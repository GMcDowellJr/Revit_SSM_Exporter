"""
Standalone Stage A model-linework feasibility probe for Revit 2025 / Dynamo 3.3 CPython3.

Inputs:
    IN[0] = target model-capable view
    IN[1] = output directory
    IN[2] = optional test element or list of elements for focused analysis metadata
    IN[3] = rendering modes, default "all"

This diagnostic probe does not modify production Stage A code.  Each rendering
mode runs from the same original view state in its own TransactionGroup, commits
child transactions, exports with no child transaction open, and rolls back in
finally.
"""

from __future__ import print_function

import hashlib
import json
import math
import os
import re
import sys
import time
import traceback

from tests.dynamo.stage_a_probe_contract import execution_envelope, select_named, utc_now_iso, view_identity

PROBE_NAME = "stage_a_model_linework"
PROBE_VERSION = "2026-07-24.1"
DEFAULT_FIXED_PIXEL_WIDTH = 1600
DEFAULT_RESOLUTION_POLICY = "paper_space_dpi"
DEFAULT_TARGET_DPI = 150
DEFAULT_FIXED_PIXEL_WIDTH = 1600
DEFAULT_MAX_PIXEL_DIMENSION = None

def round_half_up_positive(value):
    value = float(value)
    if value < 0:
        raise ValueError("round_half_up_positive requires a nonnegative value")
    return int(math.floor(value + 0.5))


def _positive_float(value, name, allow_none=False):
    if value is None:
        if allow_none:
            return None
        raise ValueError("{0} is required".format(name))
    number = float(value)
    if number <= 0:
        raise ValueError("{0} must be positive".format(name))
    return number


def _parse_resolution_policy(value):
    text = str(value or "paper_space_dpi").strip().lower()
    if text not in ("fixed_pixel_width", "paper_space_dpi", "both"):
        raise ValueError("Unsupported resolution_policy '{0}'".format(value))
    return text


def _parse_dpi_values(value):
    if value is None or value == "":
        return [150.0]
    if isinstance(value, (list, tuple)):
        raw = value
    else:
        raw = str(value).replace(";", ",").split(",")
    values = []
    for item in raw:
        if item is None or str(item).strip() == "":
            continue
        values.append(_positive_float(item, "target_dpi"))
    return values or [150.0]


def _parse_optional_cap(value):
    if value is None or value == "":
        return None
    return int(round_half_up_positive(_positive_float(value, "max_pixel_dimension")))


def _resolution_runs(policy, dpi_value, fixed_width, max_dimension):
    policy = _parse_resolution_policy(policy)
    fixed = int(round_half_up_positive(_positive_float(fixed_width if fixed_width is not None else 1600, "fixed_pixel_width")))
    cap = _parse_optional_cap(max_dimension)
    runs = []
    if policy in ("paper_space_dpi", "both"):
        for dpi in _parse_dpi_values(dpi_value):
            runs.append({"policy": "paper_space_dpi", "target_dpi": float(dpi), "fixed_pixel_width": fixed, "max_pixel_dimension": cap})
    if policy in ("fixed_pixel_width", "both"):
        runs.append({"policy": "fixed_pixel_width", "target_dpi": None, "fixed_pixel_width": fixed, "max_pixel_dimension": cap})
    return runs


def _resolution_suffix(run):
    if run.get("policy") == "fixed_pixel_width":
        return "fixed_{0}".format(int(run.get("fixed_pixel_width")))
    dpi = run.get("target_dpi")
    return "dpi_{0}".format(int(dpi) if abs(float(dpi) - int(float(dpi))) < 1e-9 else str(dpi).replace(".", "p"))


def calculate_paper_space_resolution(model_width_ft, model_height_ft, view_scale, target_dpi, bounds_source):
    model_width_ft = _positive_float(model_width_ft, "model_width_ft")
    model_height_ft = _positive_float(model_height_ft, "model_height_ft")
    view_scale = int(round_half_up_positive(_positive_float(view_scale, "view_scale")))
    target_dpi = _positive_float(target_dpi, "target_dpi")
    paper_width_in = model_width_ft * 12.0 / float(view_scale)
    requested_width_px = round_half_up_positive(paper_width_in * target_dpi)
    pixels_per_model_foot = 12.0 * target_dpi / float(view_scale)
    predicted_height_px = round_half_up_positive(model_height_ft * pixels_per_model_foot)
    return {"policy": "paper_space_dpi", "target_dpi": float(target_dpi), "view_scale": view_scale, "bounds_source": str(bounds_source), "model_width_ft": float(model_width_ft), "model_height_ft": float(model_height_ft), "paper_width_in": paper_width_in, "requested_width_px": int(requested_width_px), "accepted_width_px": int(requested_width_px), "predicted_height_px": int(predicted_height_px), "target_pixels_per_model_foot": pixels_per_model_foot, "accepted_pixels_per_model_foot": pixels_per_model_foot, "effective_dpi": float(target_dpi), "target_model_inches_per_pixel": 12.0 / pixels_per_model_foot, "actual_model_inches_per_pixel": 12.0 / pixels_per_model_foot, "max_pixel_dimension": None, "capped": False}


def apply_resolution_cap(report, max_pixel_dimension):
    cap = _parse_optional_cap(max_pixel_dimension)
    report = dict(report)
    report["max_pixel_dimension"] = cap
    if cap is None:
        return report
    width = int(report["requested_width_px"])
    height = int(report["predicted_height_px"])
    factor = min(1.0, float(cap) / float(width), float(cap) / float(height))
    if factor < 1.0:
        accepted = max(1, round_half_up_positive(width * factor))
        report["accepted_width_px"] = int(accepted)
        report["predicted_height_px"] = max(1, round_half_up_positive(height * factor))
        report["accepted_pixels_per_model_foot"] = float(accepted) / float(report["model_width_ft"])
        report["effective_dpi"] = report["accepted_pixels_per_model_foot"] * float(report["view_scale"]) / 12.0
        report["actual_model_inches_per_pixel"] = 12.0 / report["accepted_pixels_per_model_foot"]
        report["capped"] = True
    return report


def build_resolution_report(policy, model_width_ft, model_height_ft, view_scale, target_dpi, bounds_source, fixed_pixel_width, max_pixel_dimension=None):
    if policy == "fixed_pixel_width":
        model_width_ft = _positive_float(model_width_ft, "model_width_ft")
        model_height_ft = _positive_float(model_height_ft, "model_height_ft")
        fixed = int(round_half_up_positive(_positive_float(fixed_pixel_width, "fixed_pixel_width")))
        ppf = float(fixed) / model_width_ft
        return {"policy": "fixed_pixel_width", "target_dpi": None, "view_scale": int(view_scale) if view_scale else None, "bounds_source": str(bounds_source), "model_width_ft": float(model_width_ft), "model_height_ft": float(model_height_ft), "paper_width_in": None, "requested_width_px": fixed, "accepted_width_px": fixed, "predicted_height_px": round_half_up_positive(model_height_ft * ppf), "target_pixels_per_model_foot": ppf, "accepted_pixels_per_model_foot": ppf, "effective_dpi": (ppf * float(view_scale) / 12.0) if view_scale else None, "target_model_inches_per_pixel": 12.0 / ppf, "actual_model_inches_per_pixel": 12.0 / ppf, "max_pixel_dimension": None, "capped": False}
    return apply_resolution_cap(calculate_paper_space_resolution(model_width_ft, model_height_ft, view_scale, target_dpi, bounds_source), max_pixel_dimension)


def _actual_tiff_dimensions(path):
    try:
        from PIL import Image
        with Image.open(path) as img:
            return int(img.size[0]), int(img.size[1])
    except Exception:
        return None, None


def _finalize_resolution_report(report, actual_width, actual_height):
    report = dict(report)
    report["actual_width_px"] = int(actual_width) if actual_width else None
    report["actual_height_px"] = int(actual_height) if actual_height else None
    report["dimension_discrepancy"] = {"width_px": (int(actual_width) - int(report["accepted_width_px"])) if actual_width else None, "height_px": (int(actual_height) - int(report["predicted_height_px"])) if actual_height else None}
    return report


def _resolution_report_for_accepted_width(report, accepted_width):
    report = dict(report)
    accepted_width = int(round_half_up_positive(_positive_float(accepted_width, "accepted_width_px")))
    model_width = _positive_float(report.get("model_width_ft"), "model_width_ft")
    model_height = _positive_float(report.get("model_height_ft"), "model_height_ft")
    report["accepted_width_px"] = accepted_width
    report["accepted_pixels_per_model_foot"] = float(accepted_width) / model_width
    report["predicted_height_px"] = round_half_up_positive(model_height * report["accepted_pixels_per_model_foot"])
    view_scale = report.get("view_scale")
    report["effective_dpi"] = (report["accepted_pixels_per_model_foot"] * float(view_scale) / 12.0) if view_scale else None
    report["actual_model_inches_per_pixel"] = 12.0 / report["accepted_pixels_per_model_foot"]
    if accepted_width != int(report.get("requested_width_px", accepted_width)):
        report["pixel_size_backoff"] = True
    return report


def calculate_canvas_placement(model_bounds, canvas_bounds, accepted_pixels_per_model_foot):
    if not model_bounds or not canvas_bounds:
        return {"available": False, "reason": "missing model or canvas bounds"}
    mb = _bounds_tuple(model_bounds); cb = _bounds_tuple(canvas_bounds)
    density = _positive_float(accepted_pixels_per_model_foot, "accepted_pixels_per_model_foot")
    canvas_w = (cb[2] - cb[0]) * density; canvas_h = (cb[3] - cb[1]) * density
    off_x = (mb[0] - cb[0]) * density; off_y = (cb[3] - mb[3]) * density
    vals = [canvas_w, canvas_h, off_x, off_y]
    rounded = [round_half_up_positive(v) for v in vals]
    errors = [abs(vals[i] - rounded[i]) for i in range(len(vals))]
    return {"available": True, "canvas_width_px": rounded[0], "canvas_height_px": rounded[1], "model_offset_px": [rounded[2], rounded[3]], "model_offset_float_px": [off_x, off_y], "model_offset_fractional_px": [off_x - math.floor(off_x), off_y - math.floor(off_y)], "rounding_error_px": {"canvas_width": errors[0], "canvas_height": errors[1], "offset_x": errors[2], "offset_y": errors[3], "max": max(errors)}, "rounding_error_model_units": max(errors) / density, "lossless_padding_possible": max(errors) < 1e-9, "resampling_required": max(errors) >= 1e-9}

DARK_THRESHOLD = 64
WHITE_THRESHOLD = 244
NEAR_WHITE_RESERVED_THRESHOLD = 224
MODES = [
    "hidden_line_original",
    "hidden_line_white_fill_black_lines",
    "flat_colors_white_fill_black_lines",
    "wireframe_black_lines",
    "current_view_model_only_reference",
]




def _bounds_tuple(b):
    if b is None:
        return None
    if isinstance(b, dict):
        return (float(b["min_u"]), float(b["min_v"]), float(b["max_u"]), float(b["max_v"]))
    try:
        return (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
    except Exception:
        try:
            return (float(b.xmin), float(b.ymin), float(b.xmax), float(b.ymax))
        except Exception:
            return (float(b.Min.X), float(b.Min.Y), float(b.Max.X), float(b.Max.Y))


def _active_crop_bounds(view):
    try:
        if bool(getattr(view, "CropBoxActive", False)) and getattr(view, "CropBox", None) is not None:
            b = view.CropBox
            return (float(b.Min.X), float(b.Min.Y), float(b.Max.X), float(b.Max.Y)), "active_model_crop"
    except Exception:
        pass
    return None, "INCONCLUSIVE"


def _current_resolution_bounds(view):
    bounds, source = _active_crop_bounds(view)
    if bounds is not None:
        return bounds, source
    try:
        doc = _doc()
        from vop_interwoven.config import Config
        from vop_interwoven.revit.view_basis import make_view_basis, resolve_view_bounds
        cfg = Config()
        if not hasattr(cfg, "cell_size_paper_in") or cfg.cell_size_paper_in is None:
            cfg.cell_size_paper_in = 0.125
        scale = _positive_float(getattr(view, "Scale", None), "view_scale")
        basis = make_view_basis(view)
        cell_size_ft = (float(cfg.cell_size_paper_in) * scale) / 12.0
        resolved = resolve_view_bounds(view, policy={"doc": doc, "basis": basis, "cfg": cfg, "buffer_ft": float(getattr(cfg, "bounds_buffer_ft", 0.0) or 0.0), "cell_size_ft": cell_size_ft, "max_W": getattr(cfg, "max_grid_cells_width", None), "max_H": getattr(cfg, "max_grid_cells_height", None)})
        model_bounds = resolved.get("model_bounds_uv") or resolved.get("bounds_uv")
        if model_bounds is not None:
            return _bounds_tuple(model_bounds), "resolved_model_bounds" if resolved.get("model_bounds_uv") is not None else "canvas_bounds"
    except Exception:
        pass
    return None, "INCONCLUSIVE"


def _resolution_from_view(view, run):
    bounds, source = _current_resolution_bounds(view)
    if bounds is None:
        raise ValueError("paper-space/fixed resolution requires defensible model bounds; inactive crop without resolved model-only bounds is INCONCLUSIVE")
    w = bounds[2] - bounds[0]; h = bounds[3] - bounds[1]
    return build_resolution_report(run.get("policy"), w, h, getattr(view, "Scale", None), run.get("target_dpi"), source, run.get("fixed_pixel_width"), run.get("max_pixel_dimension"))

def _safe_name(value):
    text = str(value or "view")
    return re.sub(r"[^A-Za-z0-9_. -]+", "_", text).strip().replace(" ", "_") or "view"


def _safe_int_id(eid):
    try:
        return int(eid.IntegerValue)
    except Exception:
        try:
            return int(eid.Value)
        except Exception:
            return None


def _safe_enum(value):
    try:
        return str(value)
    except Exception:
        return None


def _exception_record(stage, ex):
    return {"stage": stage, "type": type(ex).__name__, "message": str(ex), "traceback": traceback.format_exc()}


def _unwrap(value):
    if value is None:
        return None
    try:
        return value.InternalElement
    except Exception:
        return value


def _unwrap_many(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [_unwrap(v) for v in value if v is not None]
    return [_unwrap(value)]


def _candidate_repo_roots(output_dir):
    roots = []
    mod = sys.modules.get("vop_interwoven")
    path = getattr(mod, "__file__", None)
    if path:
        roots.append(os.path.dirname(os.path.dirname(os.path.abspath(path))))
    for env in ("REVIT_SSM_EXPORTER_ROOT", "VOP_REPO_ROOT"):
        if os.environ.get(env):
            roots.append(os.environ[env])
    for seed in (output_dir, os.getcwd()):
        if not seed:
            continue
        cur = os.path.abspath(str(seed))
        while cur and cur != os.path.dirname(cur):
            roots.append(cur)
            cur = os.path.dirname(cur)
        roots.append(cur)
    home = os.path.expanduser("~")
    roots.extend([
        os.path.join(home, "Documents", "Revit_SSM_Exporter"),
        os.path.join(home, "Documents", "GitHub", "Revit_SSM_Exporter"),
        os.path.join(home, "source", "repos", "Revit_SSM_Exporter"),
        os.path.join(home, "Revit_SSM_Exporter"),
        "/workspace/Revit_SSM_Exporter",
    ])
    seen = []
    for root in roots:
        if root and root not in seen:
            seen.append(root)
    return seen


def _ensure_repo_import_path(output_dir):
    try:
        import vop_interwoven  # noqa: F401
        return None
    except Exception:
        pass
    checked = []
    for root in _candidate_repo_roots(output_dir):
        checked.append(root)
        if os.path.isdir(os.path.join(root, "vop_interwoven")):
            if root not in sys.path:
                sys.path.insert(0, root)
            try:
                import vop_interwoven  # noqa: F401
                return root
            except Exception:
                continue
    raise RuntimeError("Could not locate Revit_SSM_Exporter repo root for vop_interwoven imports. Set REVIT_SSM_EXPORTER_ROOT. Checked: {0}".format(checked))


def _ensure_revit_api_reference():
    import clr
    try:
        clr.AddReference("RevitAPI")
        clr.AddReference("RevitServices")
    except Exception:
        pass


def _doc():
    _ensure_revit_api_reference()
    from RevitServices.Persistence import DocumentManager
    return DocumentManager.Instance.CurrentDBDocument


def _force_close_dynamo_transaction():
    _ensure_revit_api_reference()
    from RevitServices.Transactions import TransactionManager
    TransactionManager.Instance.ForceCloseTransaction()


def _reject_reason(view):
    try:
        from Autodesk.Revit.DB import ViewType
        if view is None:
            return "IN[0] target view is required"
        if bool(getattr(view, "IsTemplate", False)):
            return "View templates are unsupported; provide a model-capable view instance"
        if getattr(view, "ViewType", None) == ViewType.ThreeD and bool(getattr(view, "IsPerspective", False)):
            return "Perspective 3D views are unsupported"
        unsupported = (ViewType.Schedule, ViewType.DrawingSheet, ViewType.Legend, ViewType.ProjectBrowser, ViewType.SystemBrowser, ViewType.Internal)
        if getattr(view, "ViewType", None) in unsupported:
            return "Unsupported/annotation-only view type for model-linework probe: {0}".format(getattr(view, "ViewType", None))
        from vop_interwoven.revit.view_basis import resolve_view_mode, VIEW_MODE_MODEL_AND_ANNOTATION
        mode, reason = resolve_view_mode(view)
        if mode != VIEW_MODE_MODEL_AND_ANNOTATION:
            return "Annotation-only or rejected view unsupported for this model-linework probe: {0}".format(reason.get("why"))
    except Exception as ex:
        return "Could not validate view capability: {0}".format(ex)
    return None


def _snapshot(doc, view):
    state = {"doc_is_modified": bool(getattr(doc, "IsModified", False)), "view_template_id": _safe_int_id(getattr(view, "ViewTemplateId", None)), "display_style": _safe_enum(getattr(view, "DisplayStyle", None)), "detail_level": _safe_enum(getattr(view, "DetailLevel", None)), "filters": {}, "smooth_edges": None, "crop_active": None, "crop_visible": None}
    for attr in ("CropBoxActive", "CropBoxVisible"):
        try:
            state[attr[0].lower() + attr[1:]] = bool(getattr(view, attr))
        except Exception as ex:
            state[attr[0].lower() + attr[1:]] = "unavailable: {0}".format(ex)
    try:
        cb = view.CropBox
        state["crop_box"] = {"min": [cb.Min.X, cb.Min.Y, cb.Min.Z], "max": [cb.Max.X, cb.Max.Y, cb.Max.Z]}
    except Exception as ex:
        state["crop_box"] = {"error": str(ex)}
    try:
        dm = view.GetViewDisplayModel()
        try:
            state["smooth_edges"] = bool(dm.SmoothEdges)
            for attr in ("ShowShadows", "AmbientOcclusion", "SketchyLines", "DepthCueing"):
                try:
                    state[attr] = str(getattr(dm, attr))
                except Exception:
                    pass
        finally:
            try:
                dm.Dispose()
            except Exception:
                pass
    except Exception as ex:
        state["smooth_edges"] = "unavailable: {0}".format(ex)
    try:
        for fid in view.GetFilters():
            state["filters"][str(_safe_int_id(fid))] = {"enabled": bool(view.GetIsFilterEnabled(fid)), "visible": bool(view.GetFilterVisibility(fid))}
    except Exception as ex:
        state["filters_error"] = str(ex)
    return state


def _diff(before, after, path=""):
    diffs = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            child = (path + "." + str(key)) if path else str(key)
            if key not in before or key not in after:
                diffs.append({"path": child, "before": before.get(key), "after": after.get(key)})
            else:
                diffs.extend(_diff(before[key], after[key], child))
    elif before != after:
        diffs.append({"path": path, "before": before, "after": after})
    return diffs


def _solid_pattern_id(doc):
    from Autodesk.Revit.DB import FilteredElementCollector, FillPatternElement, FillPatternTarget
    for fp in FilteredElementCollector(doc).OfClass(FillPatternElement):
        try:
            pat = fp.GetFillPattern()
            if pat.IsSolidFill and pat.Target == FillPatternTarget.Drafting:
                return fp.Id
        except Exception:
            continue
    return None


def _linework_ogs(doc, line_rgb=(0, 0, 0), fill_rgb=(255, 255, 255), use_fill=True):
    from Autodesk.Revit.DB import OverrideGraphicSettings, Color
    ogs = OverrideGraphicSettings()
    line = Color(int(line_rgb[0]), int(line_rgb[1]), int(line_rgb[2]))
    fill = Color(int(fill_rgb[0]), int(fill_rgb[1]), int(fill_rgb[2]))
    diagnostics = []
    calls = [("SetProjectionLineColor", line), ("SetCutLineColor", line), ("SetHalftone", False), ("SetSurfaceTransparency", 0)]
    if use_fill:
        solid = _solid_pattern_id(doc)
        calls.extend([
            ("SetSurfaceForegroundPatternId", solid),
            ("SetSurfaceForegroundPatternColor", fill),
            ("SetSurfaceForegroundPatternVisible", True),
            ("SetCutForegroundPatternId", solid),
            ("SetCutForegroundPatternColor", fill),
            ("SetCutForegroundPatternVisible", True),
        ])
    for name, arg in calls:
        try:
            if arg is not None:
                getattr(ogs, name)(arg)
        except Exception as ex:
            diagnostics.append({"setter": name, "message": str(ex)})
    return ogs, diagnostics


def _hide_annotation_categories(doc, view, diagnostics):
    """Hide annotation + view-only-model categories, mirroring color_id_buffer._hidden_category_state().

    Every candidate category is traced (name, CategoryType, CanCategoryBeHidden,
    GetCategoryHidden-before) regardless of whether the hide is attempted or
    succeeds, so an empty ``hidden`` result is diagnosable from the JSON alone
    without a second Revit session. Callers must run this only after any view
    template detach has already been committed in its own transaction --
    CanCategoryBeHidden() reports categories as locked while a controlling view
    template is still attached, same as color_id_buffer._hidden_category_state().
    """
    from Autodesk.Revit.DB import BuiltInCategory, CategoryType
    view_only = set(int(getattr(BuiltInCategory, n)) for n in ("OST_DetailComponents", "OST_Lines") if getattr(BuiltInCategory, n, None) is not None)
    hidden = []
    trace = []
    for cat in doc.Settings.Categories:
        try:
            cat_id = cat.Id
            is_annotation = cat.CategoryType == CategoryType.Annotation
            is_view_only = cat_id.IntegerValue in view_only
            if not (is_annotation or is_view_only):
                continue
            record = {
                "category_id": cat_id.IntegerValue,
                "name": cat.Name,
                "category_type": _safe_enum(getattr(cat, "CategoryType", None)),
                "is_view_only_model_bic": bool(is_view_only),
            }
            try:
                can_hide = bool(view.CanCategoryBeHidden(cat_id))
            except Exception as ex:
                record["can_category_be_hidden_error"] = str(ex)
                can_hide = False
            record["can_category_be_hidden"] = can_hide
            try:
                was_hidden = bool(view.GetCategoryHidden(cat_id))
            except Exception as ex:
                record["get_category_hidden_error"] = str(ex)
                was_hidden = None
            record["was_hidden_before"] = was_hidden
            if can_hide:
                try:
                    view.SetCategoryHidden(cat_id, True)
                    record["hide_applied"] = True
                    hidden.append({"category_id": cat_id.IntegerValue, "name": cat.Name, "was_hidden": was_hidden})
                except Exception as ex:
                    record["hide_applied"] = False
                    record["hide_exception"] = str(ex)
                    diagnostics.append({"setting": "hide_annotation_category", "category_id": cat_id.IntegerValue, "message": str(ex)})
            else:
                record["hide_applied"] = False
            trace.append(record)
        except Exception as ex:
            diagnostics.append({"setting": "hide_annotation_category", "message": str(ex)})
    return hidden, trace


def _collect_model_category_ids(doc, view, diagnostics):
    from Autodesk.Revit.DB import FilteredElementCollector, CategoryType
    ids = set()
    try:
        collector = FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType()
        for elem in collector:
            try:
                cat = elem.Category
                if cat is not None and cat.CategoryType == CategoryType.Model and view.CanCategoryBeHidden(cat.Id):
                    ids.add(cat.Id.IntegerValue)
            except Exception:
                continue
    except Exception as ex:
        diagnostics.append({"setting": "collect_model_categories", "message": str(ex)})
    # FilteredElementCollector(doc, view.Id) only sees HOST-document elements; a view that
    # also shows linked-RVT geometry needs those categories painted too, or link-only
    # categories are left uncolored and show up as fill/line contamination in the isolation
    # modes below.
    try:
        from vop_interwoven.config import Config
        from vop_interwoven.core.diagnostics import Diagnostics
        from vop_interwoven.pipeline import init_view_raster
        from vop_interwoven.revit.collection import collect_view_elements, expand_host_link_import_model_elements
        cfg = Config(debug_dump_path="", enable_color_id_buffer_stage_a=True)
        diag = Diagnostics()
        raster = init_view_raster(doc, view, cfg, diag=diag)
        top = collect_view_elements(doc, view, raster, diag=diag, cfg=cfg)
        expanded = expand_host_link_import_model_elements(doc, view, top, cfg, diag=diag, elem_cache=None)
        for entry in expanded:
            if entry.get("source_type") != "LINK":
                continue
            proxy = entry.get("element")
            cat = getattr(proxy, "Category", None) if proxy is not None else None
            if cat is not None and cat.CategoryType == CategoryType.Model and view.CanCategoryBeHidden(cat.Id):
                ids.add(cat.Id.IntegerValue)
    except Exception as ex:
        diagnostics.append({"setting": "collect_link_model_categories", "message": str(ex)})
    return sorted(ids)


def _apply_category_linework(doc, view, use_fill, diagnostics):
    from Autodesk.Revit.DB import ElementId
    ogs, ogs_diags = _linework_ogs(doc, use_fill=use_fill)
    diagnostics.extend(ogs_diags)
    touched = []
    for cid in _collect_model_category_ids(doc, view, diagnostics):
        try:
            view.SetCategoryOverrides(ElementId(int(cid)), ogs)
            touched.append(cid)
        except Exception as ex:
            diagnostics.append({"setting": "category_linework_override", "category_id": cid, "message": str(ex)})
    return touched


def _detach_template(view, diagnostics):
    try:
        from Autodesk.Revit.DB import ElementId
        orig = view.ViewTemplateId
        if orig is not None and orig != ElementId.InvalidElementId:
            view.ViewTemplateId = ElementId.InvalidElementId
            return True
        return False
    except Exception as ex:
        diagnostics.append({"setting": "view_template_detach", "message": str(ex)})
        return False


def _set_display_style(view, style_name, diagnostics):
    try:
        from Autodesk.Revit.DB import DisplayStyle
        style = getattr(DisplayStyle, style_name, None)
        if style is None:
            diagnostics.append({"setting": "display_style", "message": "DisplayStyle.{0} unavailable".format(style_name)})
            return False
        view.DisplayStyle = style
        return True
    except Exception as ex:
        diagnostics.append({"setting": "display_style", "style": style_name, "message": str(ex)})
        return False


def _disable_effects(view, diagnostics):
    applied = {}
    try:
        dm = view.GetViewDisplayModel()
        try:
            for attr, value in (("SmoothEdges", False), ("ShowShadows", False), ("AmbientOcclusion", False), ("SketchyLines", False), ("DepthCueing", False)):
                try:
                    setattr(dm, attr, value)
                    applied[attr] = value
                except Exception as ex:
                    diagnostics.append({"setting": attr, "message": str(ex)})
            view.SetViewDisplayModel(dm)
        finally:
            try:
                dm.Dispose()
            except Exception:
                pass
    except Exception as ex:
        diagnostics.append({"setting": "view_display_model", "message": str(ex)})
    return applied


def _mode_definition(mode):
    if mode == "hidden_line_original":
        return {"display_style": "HLR", "category_linework": False, "fill": False, "disable_effects": True}
    if mode == "hidden_line_white_fill_black_lines":
        return {"display_style": "HLR", "category_linework": True, "fill": True, "disable_effects": True}
    if mode == "flat_colors_white_fill_black_lines":
        return {"display_style": "FlatColors", "category_linework": True, "fill": True, "disable_effects": True}
    if mode == "wireframe_black_lines":
        return {"display_style": "Wireframe", "category_linework": True, "fill": False, "disable_effects": True}
    if mode == "current_view_model_only_reference":
        return {"display_style": None, "category_linework": False, "fill": False, "disable_effects": True}
    raise ValueError("Unsupported mode: {0}".format(mode))


def _set_pixel_size(opts, requested):
    candidate = max(1, int(requested))
    while True:
        try:
            opts.PixelSize = candidate
            return candidate
        except Exception:
            if candidate <= 16:
                raise
            candidate = max(16, candidate // 2)


def _discover_tiff(out_dir, before, final_path):
    after = set(os.listdir(out_dir))
    candidates = [f for f in (after - before) if f.lower().endswith((".tif", ".tiff"))]
    if not candidates:
        raise RuntimeError("ExportImage produced no new TIFF in {0}".format(out_dir))
    candidates.sort(key=lambda n: os.path.getmtime(os.path.join(out_dir, n)), reverse=True)
    created = os.path.join(out_dir, candidates[0])
    if os.path.exists(final_path):
        os.remove(final_path)
    os.rename(created, final_path)
    return final_path


def _export_tiff(doc, view, output_path, requested_pixel_size):
    from Autodesk.Revit.DB import ImageExportOptions, ExportRange, ZoomFitType, FitDirectionType, ElementId, ImageFileType
    import System.Collections.Generic as SCG
    out_dir = os.path.dirname(output_path)
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    before = set(os.listdir(out_dir))
    ids = SCG.List[ElementId](); ids.Add(view.Id)
    opts = ImageExportOptions()
    opts.ExportRange = ExportRange.SetOfViews
    opts.SetViewsAndSheets(ids)
    opts.ZoomType = ZoomFitType.FitToPage
    opts.FitDirection = FitDirectionType.Horizontal
    accepted = _set_pixel_size(opts, requested_pixel_size)
    opts.FilePath = os.path.join(out_dir, "_vop_model_linework_tmp")
    tiff = getattr(ImageFileType, "TIFF", getattr(ImageFileType, "TIF", None))
    if tiff is None:
        raise RuntimeError("Revit ImageFileType does not expose TIFF/TIF")
    opts.HLRandWFViewsFileType = tiff
    opts.ShadowViewsFileType = tiff
    doc.ExportImage(opts)
    return _discover_tiff(out_dir, before, output_path), accepted


def _image_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _analyze_image(path, reference_dims=None):
    result = {"path": path, "file_size_bytes": None, "sha256": None, "dimensions": None, "status": "pending_external_analysis"}
    if os.path.exists(path):
        result["file_size_bytes"] = int(os.path.getsize(path))
        result["sha256"] = _image_sha(path)
    if reference_dims:
        result["reference_dimension_agreement"] = {"reference_dimensions": reference_dims, "matches": None, "status": "pending_external_analysis"}
    result["diagnostic"] = "Pixel analysis moved to tools/analyze_stage_a_probe.py"
    return result

def _focused_metadata(elements):
    out = []
    for elem in elements:
        cat = getattr(elem, "Category", None)
        out.append({"element_id": _safe_int_id(getattr(elem, "Id", None)), "unique_id": getattr(elem, "UniqueId", None), "category": getattr(cat, "Name", None), "note": "Recorded for focused manual review; probe does not isolate content in standard modes."})
    return out


def _apply_mode(doc, view, mode, template_detached):
    diagnostics = []
    definition = _mode_definition(mode)
    hidden, trace = _hide_annotation_categories(doc, view, diagnostics)
    applied = {
        "mode_definition": definition,
        "view_template_detached": template_detached,
        "annotation_categories_hidden": hidden,
        "annotation_category_hide_trace": trace,
    }
    if definition.get("display_style"):
        applied["display_style_set"] = _set_display_style(view, definition["display_style"], diagnostics)
    if definition.get("disable_effects"):
        applied["display_effects_disabled"] = _disable_effects(view, diagnostics)
    if definition.get("category_linework"):
        applied["category_linework_overrides"] = _apply_category_linework(doc, view, bool(definition.get("fill")), diagnostics)
    if diagnostics:
        applied["diagnostics"] = diagnostics
    return applied


def _collect_reference_elements(doc, view, diagnostics):
    try:
        from vop_interwoven.config import Config
        from vop_interwoven.core.diagnostics import Diagnostics
        from vop_interwoven.pipeline import init_view_raster
        from vop_interwoven.revit.collection import collect_view_elements, expand_host_link_import_model_elements
        from vop_interwoven.color_id_buffer import _split_expanded_elements, resolve_all
        cfg = Config(debug_dump_path="", enable_color_id_buffer_stage_a=True)
        diag = Diagnostics()
        raster = init_view_raster(doc, view, cfg, diag=diag)
        top = collect_view_elements(doc, view, raster, diag=diag, cfg=cfg)
        expanded = expand_host_link_import_model_elements(doc, view, top, cfg, diag=diag, elem_cache=None)
        host, _links = _split_expanded_elements(expanded)
        return resolve_all(doc, host), len(top)
    except Exception as ex:
        diagnostics.append({"setting": "element_id_reference_collection", "message": str(ex)})
        return [], 0


def _apply_element_id_reference(doc, view, template_detached):
    diagnostics = []
    hidden, trace = _hide_annotation_categories(doc, view, diagnostics)
    applied = {
        "annotation_categories_hidden": hidden,
        "annotation_category_hide_trace": trace,
        "view_template_detached": template_detached,
    }
    _set_display_style(view, "FlatColors", diagnostics)
    applied["display_effects_disabled"] = _disable_effects(view, diagnostics)
    ids, top_count = _collect_reference_elements(doc, view, diagnostics)
    try:
        from Autodesk.Revit.DB import Color
        from vop_interwoven.color_id_buffer import build_palette, choose_step, _build_flat_color_ogs
        solid = _solid_pattern_id(doc)
        palette = build_palette(len(ids), step=choose_step(max(1, len(ids))))
        painted = 0
        for i, eid in enumerate(ids):
            try:
                rgb = palette[i]
                view.SetElementOverrides(eid, _build_flat_color_ogs(solid, Color(int(rgb[0]), int(rgb[1]), int(rgb[2]))))
                painted += 1
            except Exception as ex:
                diagnostics.append({"setting": "element_id_reference_paint", "element_id": _safe_int_id(eid), "message": str(ex)})
        applied["element_counts"] = {"collected": int(top_count), "resolved_host": int(len(ids)), "painted": int(painted)}
    except Exception as ex:
        diagnostics.append({"setting": "element_id_reference_paint", "message": str(ex)})
    if diagnostics:
        applied["diagnostics"] = diagnostics
    return applied


def _run_reference_element_id(doc, view, out_dir, base, resolution_report, resolution_suffix):
    from Autodesk.Revit.DB import Transaction, TransactionGroup, TransactionStatus
    result = {"transaction_group": {}, "state": {}, "settings_applied": {}, "images": [], "exceptions": [], "conclusion": "INCONCLUSIVE", "requires_external_analysis": True}
    group = None
    started = False
    try:
        _force_close_dynamo_transaction()
        result["state"]["before"] = _snapshot(doc, view)
        group = TransactionGroup(doc, "VOP Stage A linework element-ID dimension reference")
        st = group.Start(); started = st == TransactionStatus.Started
        result["transaction_group"]["start_status"] = _safe_enum(st)
        if not started:
            raise RuntimeError("TransactionGroup.Start returned {0}".format(st))
        # Detach and commit before anything else in its own transaction, same as
        # color_id_buffer.py's detach_tx: CanCategoryBeHidden()/GetCategoryHidden()
        # inside _hide_annotation_categories() report against committed document
        # state, so a detach still pending in the same transaction as the hide
        # attempt is not guaranteed to unlock template-controlled categories.
        detach_diagnostics = []
        detach_tx = Transaction(doc, "VOP Stage A linework element-ID reference DETACH template")
        detach_tx.Start()
        try:
            template_detached = _detach_template(view, detach_diagnostics)
            detach_commit = detach_tx.Commit()
            result["transaction_group"]["detach_commit_status"] = _safe_enum(detach_commit)
            if detach_commit != TransactionStatus.Committed:
                raise RuntimeError("Detach Transaction.Commit returned {0}".format(detach_commit))
        except Exception:
            try:
                detach_tx.RollBack()
            except Exception as ex:
                result["exceptions"].append(_exception_record("reference_detach_rollback", ex))
            raise
        tx = Transaction(doc, "VOP Stage A linework element-ID reference settings")
        tx.Start()
        try:
            result["settings_applied"] = _apply_element_id_reference(doc, view, template_detached)
            if detach_diagnostics:
                result["settings_applied"].setdefault("diagnostics", []).extend(detach_diagnostics)
            commit = tx.Commit()
            result["transaction_group"]["child_commit_status"] = _safe_enum(commit)
            if commit != TransactionStatus.Committed:
                raise RuntimeError("Child Transaction.Commit returned {0}".format(commit))
        except Exception:
            try:
                tx.RollBack()
            except Exception as ex:
                result["exceptions"].append(_exception_record("reference_child_rollback", ex))
            raise
        result["transaction_group"]["no_child_transaction_open_at_export"] = not bool(doc.IsModifiable)
        for seq in (1, 2):
            path = os.path.join(out_dir, "{0}.{1}.element_id_reference_{2}.tiff".format(base, resolution_suffix, seq))
            exported, accepted = _export_tiff(doc, view, path, resolution_report["accepted_width_px"])
            actual_w, actual_h = _actual_tiff_dimensions(exported)
            resolution = _finalize_resolution_report(_resolution_report_for_accepted_width(resolution_report, accepted), actual_w, actual_h)
            result["images"].append({"path": exported, "accepted_pixel_size": accepted, "resolution": resolution, "actual_model_inches_per_pixel": resolution.get("actual_model_inches_per_pixel"), "actual_paper_inches_per_pixel": (1.0 / resolution.get("effective_dpi")) if resolution.get("effective_dpi") else None, "analysis": _analyze_image(exported)})
    except Exception as ex:
        result["exceptions"].append(_exception_record("element_id_reference", ex))
    finally:
        if started and group is not None:
            result["transaction_group"]["rollback_attempted"] = True
            try:
                rb = group.RollBack()
                result["transaction_group"]["rollback_status"] = _safe_enum(rb)
                result["transaction_group"]["rollback_succeeded"] = rb == TransactionStatus.RolledBack
            except Exception as ex:
                result["exceptions"].append(_exception_record("reference_group_rollback", ex))
                result["transaction_group"]["rollback_succeeded"] = False
        result["state"]["after"] = _snapshot(doc, view)
        result["state"]["differences_after_rollback"] = _diff(result["state"].get("before", {}), result["state"].get("after", {}))
        if result["exceptions"] or not result["transaction_group"].get("rollback_succeeded") or result["state"].get("differences_after_rollback"):
            result["conclusion"] = "FAIL"
        elif result.get("images"):
            result["conclusion"] = "PASS_PARTIAL"
        else:
            result["conclusion"] = "INCONCLUSIVE"
    return result


def _run_mode(doc, view, out_dir, base, mode, focused_elements, reference_dims, resolution_report, resolution_suffix):
    from Autodesk.Revit.DB import Transaction, TransactionGroup, TransactionStatus
    result = {"mode": mode, "focused_elements": _focused_metadata(focused_elements), "transaction_group": {}, "state": {}, "settings_applied": {}, "images": [], "exceptions": [], "conclusion": "INCONCLUSIVE", "requires_external_analysis": True}
    group = None
    started = False
    try:
        _force_close_dynamo_transaction()
        result["state"]["before"] = _snapshot(doc, view)
        group = TransactionGroup(doc, "VOP Stage A linework probe: " + mode)
        st = group.Start(); started = st == TransactionStatus.Started
        result["transaction_group"]["start_status"] = _safe_enum(st)
        if not started:
            raise RuntimeError("TransactionGroup.Start returned {0}".format(st))
        # Detach and commit before anything else in its own transaction, same as
        # color_id_buffer.py's detach_tx: CanCategoryBeHidden()/GetCategoryHidden()
        # inside _hide_annotation_categories() report against committed document
        # state, so a detach still pending in the same transaction as the hide
        # attempt is not guaranteed to unlock template-controlled categories.
        # Unconditional (not gated on this mode's display_style) because every
        # mode calls _hide_annotation_categories().
        detach_diagnostics = []
        detach_tx = Transaction(doc, "VOP Stage A linework DETACH template: " + mode)
        detach_tx.Start()
        try:
            template_detached = _detach_template(view, detach_diagnostics)
            detach_commit = detach_tx.Commit()
            result["transaction_group"]["detach_commit_status"] = _safe_enum(detach_commit)
            if detach_commit != TransactionStatus.Committed:
                raise RuntimeError("Detach Transaction.Commit returned {0}".format(detach_commit))
        except Exception:
            try:
                detach_tx.RollBack()
            except Exception as ex:
                result["exceptions"].append(_exception_record("detach_rollback", ex))
            raise
        tx = Transaction(doc, "VOP Stage A linework settings: " + mode)
        tx.Start()
        try:
            result["settings_applied"] = _apply_mode(doc, view, mode, template_detached)
            if detach_diagnostics:
                result["settings_applied"].setdefault("diagnostics", []).extend(detach_diagnostics)
            commit = tx.Commit()
            result["transaction_group"]["child_commit_status"] = _safe_enum(commit)
            if commit != TransactionStatus.Committed:
                raise RuntimeError("Child Transaction.Commit returned {0}".format(commit))
        except Exception:
            try:
                tx.RollBack()
            except Exception as ex:
                result["exceptions"].append(_exception_record("child_rollback", ex))
            raise
        result["transaction_group"]["no_child_transaction_open_at_export"] = not bool(doc.IsModifiable)
        for seq in (1, 2):
            path = os.path.join(out_dir, "{0}.{1}.{2}.linework_{3}.tiff".format(base, resolution_suffix, mode, seq))
            t0 = time.time()
            exported, accepted = _export_tiff(doc, view, path, resolution_report["accepted_width_px"])
            actual_w, actual_h = _actual_tiff_dimensions(exported)
            resolution = _finalize_resolution_report(_resolution_report_for_accepted_width(resolution_report, accepted), actual_w, actual_h)
            analysis = _analyze_image(exported, reference_dims=reference_dims)
            analysis["line_pixel_metrics"] = {"edge_dark_line_pixel_count": None, "edge_dark_line_percent": None, "unexpected_color_count": None, "unexpected_color_percent": None, "internal_edge_manual_review_status": "pending_external_analysis", "actual_model_inches_per_pixel": resolution.get("actual_model_inches_per_pixel"), "actual_paper_inches_per_pixel": (1.0 / resolution.get("effective_dpi")) if resolution.get("effective_dpi") else None}
            result["images"].append({"path": exported, "accepted_pixel_size": accepted, "resolution": resolution, "timing_ms": round((time.time() - t0) * 1000.0, 3), "analysis": analysis})
        a0 = result["images"][0]["analysis"]
        a1 = result["images"][1]["analysis"]
        result["sequential_export_repeatability"] = {"same_sha256": a0.get("sha256") == a1.get("sha256"), "same_dimensions": a0.get("dimensions") == a1.get("dimensions")}
    except Exception as ex:
        result["exceptions"].append(_exception_record("mode", ex))
    finally:
        if started and group is not None:
            result["transaction_group"]["rollback_attempted"] = True
            try:
                rb = group.RollBack()
                result["transaction_group"]["rollback_status"] = _safe_enum(rb)
                result["transaction_group"]["rollback_succeeded"] = rb == TransactionStatus.RolledBack
            except Exception as ex:
                result["exceptions"].append(_exception_record("group_rollback", ex))
                result["transaction_group"]["rollback_succeeded"] = False
        result["state"]["after"] = _snapshot(doc, view)
        result["state"]["differences_after_rollback"] = _diff(result["state"].get("before", {}), result["state"].get("after", {}))
        result["classification"] = {"status": "pending_external_analysis", "manual_review_required": True}
        if result["exceptions"] or not result["transaction_group"].get("rollback_succeeded") or result["state"].get("differences_after_rollback"):
            result["conclusion"] = "FAIL"
        elif result.get("images"):
            result["conclusion"] = "PASS_PARTIAL"
        else:
            result["conclusion"] = "INCONCLUSIVE"
    return result


def _select_modes(selection):
    return select_named(selection, MODES, "model-linework display/configuration case(s)")


def _run_native(raw_view, output_dir, raw_focused=None, selection="all", resolution_policy=DEFAULT_RESOLUTION_POLICY, target_dpi=DEFAULT_TARGET_DPI, fixed_pixel_width=DEFAULT_FIXED_PIXEL_WIDTH, max_pixel_dimension=DEFAULT_MAX_PIXEL_DIMENSION):
    out_dir = os.path.abspath(str(output_dir or os.getcwd()))
    _ensure_repo_import_path(out_dir)
    _ensure_revit_api_reference()
    doc = _doc()
    view = _unwrap(raw_view)
    reject = _reject_reason(view)
    if reject:
        return {"conclusion": "FAIL", "reason": reject}
    focused = _unwrap_many(raw_focused)
    probe_dir = os.path.join(out_dir, "model_linework_probe")
    if not os.path.isdir(probe_dir):
        os.makedirs(probe_dir)
    base = "{0}_{1}.model_linework".format(_safe_name(getattr(view, "Name", "view")), _safe_int_id(view.Id))
    modes = _select_modes(selection)
    report = {"probe": {"name": PROBE_NAME, "version": PROBE_VERSION, "target": "Revit 2025 / Dynamo 3.3 CPython3", "production_linework_flag_default": "disabled"}, "inputs": {"view_id": _safe_int_id(view.Id), "view_name": getattr(view, "Name", None), "output_directory": out_dir, "rendering_modes": modes, "focused_elements": _focused_metadata(focused), "resolution_policy": resolution_policy, "target_dpi": target_dpi, "fixed_pixel_width": fixed_pixel_width, "max_pixel_dimension": max_pixel_dimension}, "reference_element_id_tiff": None, "modes": [], "difference_images": [], "ranked_modes": [], "conclusion": "INCONCLUSIVE", "requires_external_analysis": True}
    reference = {"conclusion": "INCONCLUSIVE"}
    for run_cfg in _resolution_runs(resolution_policy, target_dpi, fixed_pixel_width, max_pixel_dimension):
        try:
            resolution_report = _resolution_from_view(view, run_cfg)
        except Exception as ex:
            report.setdefault("resolution_diagnostics", []).append({"policy": run_cfg.get("policy"), "target_dpi": run_cfg.get("target_dpi"), "conclusion": "INCONCLUSIVE", "reason": str(ex)})
            continue
        suffix = _resolution_suffix(run_cfg)
        reference = _run_reference_element_id(doc, view, probe_dir, base, resolution_report, suffix)
        reference_dims = None
        report["reference_element_id_tiff"] = reference if report["reference_element_id_tiff"] is None else report["reference_element_id_tiff"]
        report.setdefault("reference_runs", []).append(reference)
        for mode in modes:
            report["modes"].append(_run_mode(doc, view, probe_dir, base, mode, focused, reference_dims, resolution_report, suffix))
    report["difference_images"] = [{"status": "pending_external_analysis", "reason": "Diff images are generated by tools/analyze_stage_a_probe.py"}] if len(report["modes"]) > 1 else []
    report["ranked_modes"] = [{"status": "pending_external_analysis", "reason": "Mode ranking is computed by tools/analyze_stage_a_probe.py"}]
    reference_failed = reference.get("conclusion") == "FAIL"
    reference_missing_dimensions = False
    report["reference_status"] = {"failed": bool(reference_failed), "missing_dimensions": bool(reference_missing_dimensions)}
    if reference_failed or any(r.get("conclusion") == "FAIL" for r in report["modes"]):
        report["conclusion"] = "FAIL"
    elif reference_missing_dimensions:
        report["conclusion"] = "INCONCLUSIVE"
    elif all(r.get("conclusion") in ("PASS", "PASS_PARTIAL") for r in report["modes"]):
        report["conclusion"] = "PASS_PARTIAL"
    json_path = os.path.join(probe_dir, base + ".json")
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)
    report["paths"] = {"combined_json": json_path, "tiffs": [img.get("path") for r in ([report.get("reference_element_id_tiff", {})] + report["modes"]) for img in r.get("images", [])], "diffs": [d.get("diff", {}).get("path") for d in report["difference_images"] if d.get("diff", {}).get("path")]}
    return report


def run_probe(raw_view, output_dir, raw_focused=None, selection="all",
              resolution_policy=DEFAULT_RESOLUTION_POLICY, target_dpi=DEFAULT_TARGET_DPI,
              fixed_pixel_width=DEFAULT_FIXED_PIXEL_WIDTH,
              max_pixel_dimension=DEFAULT_MAX_PIXEL_DIMENSION):
    """Run selected display/configuration and DPI cases with probe-owned cleanup."""
    started_at = utc_now_iso()
    native = _run_native(raw_view, output_dir, raw_focused, selection, resolution_policy,
                         target_dpi, fixed_pixel_width, max_pixel_dimension)
    runs = list(native.get("reference_runs", [])) + list(native.get("modes", []))
    rollback_ok = bool(runs) and all(item.get("transaction_group", {}).get("rollback_succeeded") for item in runs)
    restored = bool(runs) and all(not item.get("state", {}).get("differences_after_rollback") for item in runs)
    errors = [error for item in runs for error in item.get("exceptions", [])]
    paths = native.get("paths", {})
    artifacts = ([paths.get("combined_json")] if paths.get("combined_json") else []) + list(paths.get("tiffs", []))
    selected_modes = _select_modes(selection)
    return execution_envelope(
        PROBE_NAME, {"display_cases": selected_modes, "resolution_policy": resolution_policy,
                     "dpi_cases": _parse_dpi_values(target_dpi), "fixed_pixel_width": fixed_pixel_width,
                     "max_pixel_dimension": max_pixel_dimension},
        view_identity(raw_view), native, artifacts, "succeeded" if rollback_ok else "failed",
        "restored" if restored else "not_restored", started_at,
        execution_status="failed" if errors or not rollback_ok or not restored else "completed", errors=errors)


def dynamo_main(inputs):
    return run_probe(inputs[0] if len(inputs) > 0 else None,
                     inputs[1] if len(inputs) > 1 else None,
                     inputs[2] if len(inputs) > 2 else None,
                     inputs[3] if len(inputs) > 3 else "all",
                     inputs[4] if len(inputs) > 4 else DEFAULT_RESOLUTION_POLICY,
                     inputs[5] if len(inputs) > 5 else DEFAULT_TARGET_DPI,
                     inputs[6] if len(inputs) > 6 else DEFAULT_FIXED_PIXEL_WIDTH,
                     inputs[7] if len(inputs) > 7 else DEFAULT_MAX_PIXEL_DIMENSION)


if "IN" in globals():
    try:
        OUT = dynamo_main(IN)
    except Exception as ex:
        OUT = {"conclusion": "FAIL", "error": str(ex), "error_type": type(ex).__name__, "traceback": traceback.format_exc()}
