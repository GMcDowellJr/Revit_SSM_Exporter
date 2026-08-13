"""
Standalone Stage A external-source probe for Revit 2025 / Dynamo 3.3 CPython3.

Inputs:
    IN[0] = target view
    IN[1] = output directory
    IN[2] = optional link instance(s)
    IN[3] = optional DWG import instance(s)

Tests HOST, LINK, and DWG Stage A color-assignment behavior without modifying
production Stage A code.  Each variant runs in its own TransactionGroup, commits
temporary child transactions, exports with no child transaction open, and rolls
back in finally.
"""

from __future__ import print_function

import hashlib
import importlib.util
import json
import math
import os
import re
import sys
import time
import traceback

from tests.dynamo.stage_a_probe_contract import execution_envelope, select_named, utc_now_iso, view_identity

PROBE_NAME = "stage_a_external_sources"
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

VARIANTS = [
    "host_reference_coloring",
    "linked_per_element_linkelementid_coloring",
    "forced_linked_override_failure_hide_instance_fallback",
    "dwg_importinstance_coloring",
    "mixed_host_link_dwg_export",
]


def select_variants(selection="all"):
    return select_named(selection, VARIANTS, "external-source variant(s)")




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
            return "Unsupported/annotation-only view type: {0}".format(getattr(view, "ViewType", None))
        from vop_interwoven.revit.view_basis import resolve_view_mode, VIEW_MODE_MODEL_AND_ANNOTATION
        mode, reason = resolve_view_mode(view)
        if mode != VIEW_MODE_MODEL_AND_ANNOTATION:
            return "Annotation-only or rejected view unsupported for external-source probe: {0}".format(reason.get("why"))
    except Exception as ex:
        return "Could not validate view capability: {0}".format(ex)
    return None


def _snapshot_ogs(view, key):
    try:
        from Autodesk.Revit.DB import LinkElementId
        if isinstance(key, tuple):
            target = LinkElementId(key[0], key[1])
        else:
            target = key
        ogs = view.GetElementOverrides(target)
        return {"halftone": bool(ogs.Halftone), "transparency": int(getattr(ogs, "Transparency", getattr(ogs, "SurfaceTransparency", -1)))}
    except Exception as ex:
        return {"unavailable": str(ex)}


def _snapshot(doc, view, assignments):
    state = {"doc_is_modified": bool(getattr(doc, "IsModified", False)), "view_template_id": _safe_int_id(getattr(view, "ViewTemplateId", None)), "display_style": _safe_enum(getattr(view, "DisplayStyle", None)), "items": {}}
    for item in assignments or []:
        key = item.get("assignment_key")
        entry = {"source_type": item.get("source_type"), "hidden_state": None, "override": None}
        try:
            host_id = item.get("host_element_id") or item.get("link_instance_id") or item.get("import_instance_id")
            elem = doc.GetElement(item.get("host_api_id")) if item.get("host_api_id") is not None else (doc.GetElement(item.get("link_instance_api_id")) if item.get("link_instance_api_id") is not None else None)
            if elem is None and host_id is not None:
                from Autodesk.Revit.DB import ElementId
                elem = doc.GetElement(ElementId(int(host_id)))
            entry["hidden_state"] = bool(elem.IsHidden(view)) if elem is not None else None
        except Exception as ex:
            entry["hidden_state"] = "unavailable: {0}".format(ex)
        if item.get("source_type") == "LINK" and item.get("link_instance_api_id") is not None and item.get("linked_element_api_id") is not None:
            entry["linked_override"] = _snapshot_ogs(view, (item["link_instance_api_id"], item["linked_element_api_id"]))
        elif item.get("host_api_id") is not None:
            entry["override"] = _snapshot_ogs(view, item["host_api_id"])
        state["items"][str(key)] = entry
    return state


def _diff(before, after, path=""):
    out = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            child = (path + "." + str(key)) if path else str(key)
            if key not in before or key not in after:
                out.append({"path": child, "before": before.get(key), "after": after.get(key)})
            else:
                out.extend(_diff(before[key], after[key], child))
    elif before != after:
        out.append({"path": path, "before": before, "after": after})
    return out


def _collect_expanded(doc, view):
    from vop_interwoven.config import Config
    from vop_interwoven.core.diagnostics import Diagnostics
    from vop_interwoven.pipeline import init_view_raster
    from vop_interwoven.revit.collection import collect_view_elements, expand_host_link_import_model_elements
    cfg = Config(debug_dump_path="", enable_color_id_buffer_stage_a=True, include_dwg_imports=True)
    diag = Diagnostics()
    raster = init_view_raster(doc, view, cfg, diag=diag)
    host_top = collect_view_elements(doc, view, raster, diag=diag, cfg=cfg)
    expanded = expand_host_link_import_model_elements(doc, view, host_top, cfg, diag=diag, elem_cache=None)
    return expanded, {"host_top_count": len(host_top), "expanded_count": len(expanded)}


def _element_id_set(elements):
    ids = set()
    for elem in elements or []:
        eid = _safe_int_id(getattr(elem, "Id", None))
        if eid is not None:
            ids.add(eid)
    return ids


def _identity_from_entry(doc, entry, index):
    elem = entry.get("element")
    source_type = entry.get("source_type", "HOST")
    cat = getattr(elem, "Category", None)
    api_id = getattr(elem, "Id", None)
    link_inst_id = getattr(elem, "LinkInstanceId", None)
    item = {
        "assignment_key": "{0}:{1}:{2}".format(source_type, _safe_int_id(link_inst_id), _safe_int_id(api_id)),
        "source_type": source_type,
        "source_id": entry.get("source_id"),
        "source_label": entry.get("source_label"),
        "host_element_id": _safe_int_id(api_id) if source_type == "HOST" else None,
        "link_instance_id": _safe_int_id(link_inst_id) if source_type == "LINK" else None,
        "linked_element_id": _safe_int_id(api_id) if source_type == "LINK" else None,
        "import_instance_id": _safe_int_id(api_id) if source_type == "DWG" else None,
        "category": getattr(cat, "Name", None),
        "host_api_id": api_id if source_type in ("HOST", "DWG") else None,
        "link_instance_api_id": link_inst_id if source_type == "LINK" else None,
        "linked_element_api_id": api_id if source_type == "LINK" else None,
        "label": "{0} {1}".format(source_type, index),
    }
    if source_type == "DWG":
        item["host_element_id"] = None
    return item


def _discover_assignments(doc, view, link_inputs, dwg_inputs):
    expanded, stats = _collect_expanded(doc, view)
    link_filter = _element_id_set(link_inputs)
    dwg_filter = _element_id_set(dwg_inputs)
    discovered = {"mode": "view_discovery", "link_inputs_supplied": bool(link_filter), "dwg_inputs_supplied": bool(dwg_filter), "collection_stats": stats, "counts_by_source": {"HOST": 0, "LINK": 0, "DWG": 0}}
    by_source = {"HOST": [], "LINK": [], "DWG": []}
    for entry in expanded:
        source_type = entry.get("source_type", "HOST")
        if source_type not in by_source:
            continue
        item = _identity_from_entry(doc, entry, len(by_source[source_type]))
        if source_type == "LINK" and link_filter and item.get("link_instance_id") not in link_filter:
            continue
        if source_type == "DWG" and dwg_filter and item.get("import_instance_id") not in dwg_filter:
            continue
        by_source[source_type].append(item)
    for key in by_source:
        discovered["counts_by_source"][key] = len(by_source[key])
    discovered["note"] = "Candidates were discovered from the target view; supplied IN[2]/IN[3] only filter matching link/import instances and do not substitute unrelated elements."
    return by_source, discovered


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


def _ogs(doc, rgb):
    from Autodesk.Revit.DB import Color
    from vop_interwoven.color_id_buffer import _build_flat_color_ogs
    solid = _solid_pattern_id(doc)
    if solid is None:
        raise RuntimeError("No solid drafting fill pattern found")
    return _build_flat_color_ogs(solid, Color(int(rgb[0]), int(rgb[1]), int(rgb[2])))


def _palette(n):
    from vop_interwoven.color_id_buffer import build_palette, choose_step
    step = choose_step(max(1, n))
    return build_palette(n, step=step), step


def _hide_element_ids(doc, view, element_ids):
    from Autodesk.Revit.DB import ElementId
    import System.Collections.Generic as SCG
    ids = SCG.List[ElementId]()
    for eid in sorted(set(int(x) for x in element_ids if x is not None)):
        elem = doc.GetElement(ElementId(eid))
        if elem is None:
            continue
        try:
            if not bool(elem.IsHidden(view)):
                ids.Add(ElementId(eid))
        except Exception:
            ids.Add(ElementId(eid))
    if ids.Count > 0:
        view.HideElements(ids)
    return [ids[i].IntegerValue for i in range(ids.Count)]


def _apply_assignments(doc, view, variant, items):
    from vop_interwoven.color_id_buffer import _try_color_link_element
    diagnostics = []
    palette, step = _palette(len(items))
    assignments = []
    hidden_link_fallback = []
    for i, item in enumerate(items):
        rgb = palette[i]
        record = dict((k, v) for k, v in item.items() if not k.endswith("api_id"))
        record["rgb"] = list(rgb)
        record["paint_success"] = False
        record["paint_failure"] = None
        try:
            if variant == "forced_linked_override_failure_hide_instance_fallback" and item.get("source_type") == "LINK":
                record["paint_failure"] = "forced diagnostic failure before LinkElementId override"
                hidden = _hide_element_ids(doc, view, [item.get("link_instance_id")])
                record["hidden_link_fallback"] = hidden
                hidden_link_fallback.extend(hidden)
            elif item.get("source_type") == "LINK":
                ok = _try_color_link_element(view, item["link_instance_api_id"], item["linked_element_api_id"], _ogs(doc, rgb))
                record["paint_success"] = bool(ok)
                if not ok:
                    record["paint_failure"] = "LinkElementId override returned False"
                    hidden = _hide_element_ids(doc, view, [item.get("link_instance_id")])
                    record["hidden_link_fallback"] = hidden
                    hidden_link_fallback.extend(hidden)
            elif item.get("source_type") == "DWG":
                view.SetElementOverrides(item["host_api_id"], _ogs(doc, rgb))
                record["paint_success"] = True
            else:
                view.SetElementOverrides(item["host_api_id"], _ogs(doc, rgb))
                record["paint_success"] = True
        except Exception as ex:
            record["paint_failure"] = "{0}: {1}".format(type(ex).__name__, ex)
            diagnostics.append({"assignment_key": item.get("assignment_key"), "message": str(ex), "type": type(ex).__name__})
        assignments.append(record)
    return {"palette_step": step, "assignments": assignments, "hidden_link_fallback_ids": sorted(set(hidden_link_fallback)), "diagnostics": diagnostics}


def _items_for_variant(variant, by_source):
    if variant == "host_reference_coloring":
        return by_source["HOST"][:25]
    if variant == "linked_per_element_linkelementid_coloring":
        return by_source["LINK"][:25]
    if variant == "forced_linked_override_failure_hide_instance_fallback":
        return by_source["LINK"][:25]
    if variant == "dwg_importinstance_coloring":
        return by_source["DWG"][:25]
    if variant == "mixed_host_link_dwg_export":
        return by_source["HOST"][:10] + by_source["LINK"][:10] + by_source["DWG"][:10]
    raise ValueError("Unsupported variant: {0}".format(variant))


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


def _export_tiff(doc, view, path, requested_pixel_size):
    from Autodesk.Revit.DB import ImageExportOptions, ExportRange, ZoomFitType, FitDirectionType, ElementId, ImageFileType
    import System.Collections.Generic as SCG
    out_dir = os.path.dirname(path)
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
    opts.FilePath = os.path.join(out_dir, "_vop_external_sources_tmp")
    tiff = getattr(ImageFileType, "TIFF", getattr(ImageFileType, "TIF", None))
    if tiff is None:
        raise RuntimeError("Revit ImageFileType does not expose TIFF/TIF")
    opts.HLRandWFViewsFileType = tiff
    opts.ShadowViewsFileType = tiff
    doc.ExportImage(opts)
    candidates = [f for f in (set(os.listdir(out_dir)) - before) if f.lower().endswith((".tif", ".tiff"))]
    if not candidates:
        raise RuntimeError("ExportImage produced no new TIFF in {0}".format(out_dir))
    candidates.sort(key=lambda n: os.path.getmtime(os.path.join(out_dir, n)), reverse=True)
    created = os.path.join(out_dir, candidates[0])
    if os.path.exists(path):
        os.remove(path)
    os.rename(created, path)
    return path, accepted


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _analyze(path, assignments):
    result = {"path": path, "file_size_bytes": None, "sha256": None, "dimensions": None, "pillow_available": False, "expected_color_pixel_counts": {}, "missing_colors": []}
    if os.path.exists(path):
        result["file_size_bytes"] = int(os.path.getsize(path))
        result["sha256"] = _sha(path)
    if importlib.util.find_spec("PIL") is None:
        result["diagnostic"] = "Pillow unavailable; exact-color analysis skipped"
        return result
    from PIL import Image
    img = Image.open(path).convert("RGB")
    result["pillow_available"] = True
    result["dimensions"] = [int(img.size[0]), int(img.size[1])]
    pixels = list(img.getdata())
    for a in assignments:
        key = str(a.get("assignment_key"))
        rgb = tuple(int(v) for v in a.get("rgb", []))
        count = pixels.count(rgb)
        result["expected_color_pixel_counts"][key] = int(count)
        if count <= 0:
            result["missing_colors"].append(key)
    return result


def _classify_variant(variant, applied, analysis):
    assignments = applied.get("assignments", [])
    by_source = {"HOST": [], "LINK": [], "DWG": []}
    for a in assignments:
        by_source.get(a.get("source_type"), []).append(a)
    return {
        "per_source_success": {src: {"assigned": len(vals), "paint_success": sum(1 for v in vals if v.get("paint_success")), "colors_detected": sum(1 for v in vals if analysis.get("expected_color_pixel_counts", {}).get(str(v.get("assignment_key")), 0) > 0)} for src, vals in by_source.items()},
        "linkelementid_supported": "yes" if any(v.get("paint_success") for v in by_source["LINK"]) else ("no_or_unresolved" if by_source["LINK"] else "not_tested"),
        "hidden_link_fallback_exercised": bool(applied.get("hidden_link_fallback_ids")),
        "recommended_linked_fallback": "hide owning link instance prevents uncolored linked contamination in this diagnostic, but keep as a measured fallback rather than final production policy",
        "required_sidecar_schema_changes": ["preserve source_type HOST/LINK/DWG", "record link_instance_id and linked_element_id separately", "record import_instance_id separately from host_element_id", "record paint_success and hidden_link_fallback per assignment"],
        "remaining_limitations": ["Exact-color pixels can prove rendered assignment color, but not geometric attribution finer than Revit exposes", "DWG attribution is ImportInstance-level unless repository proxy/geometry extraction exposes finer identities downstream"],
    }



def _source_evidence_status(variant, assignments, analysis, hidden_link_fallback):
    if not assignments:
        return {"has_required_evidence": False, "reason": "no assignments"}
    if not analysis.get("pillow_available"):
        return {"has_required_evidence": False, "reason": "Pillow unavailable; exact source-color evidence missing"}
    counts = analysis.get("expected_color_pixel_counts", {}) or {}
    by_source = {"HOST": [], "LINK": [], "DWG": []}
    for assignment in assignments:
        by_source.get(assignment.get("source_type"), []).append(assignment)

    def rendered_count(items):
        return sum(1 for item in items if counts.get(str(item.get("assignment_key")), 0) > 0)

    if variant == "forced_linked_override_failure_hide_instance_fallback":
        return {
            "has_required_evidence": bool(by_source["LINK"] and hidden_link_fallback),
            "reason": "hidden owning link fallback exercised" if hidden_link_fallback else "forced linked failure did not hide any owning link instance",
            "rendered_counts_by_source": {src: rendered_count(vals) for src, vals in by_source.items()},
        }

    required_sources = [src for src, vals in by_source.items() if vals]
    rendered = {src: rendered_count(by_source[src]) for src in required_sources}
    missing_sources = [src for src in required_sources if rendered.get(src, 0) <= 0]
    paint_success = {src: sum(1 for item in by_source[src] if item.get("paint_success")) for src in required_sources}
    no_success_sources = [src for src in required_sources if paint_success.get(src, 0) <= 0]
    ok = bool(required_sources) and not missing_sources and not no_success_sources
    reason = "source colors rendered" if ok else "missing rendered source-color evidence for: {0}; no paint success for: {1}".format(missing_sources, no_success_sources)
    return {
        "has_required_evidence": ok,
        "reason": reason,
        "required_sources": required_sources,
        "paint_success_by_source": paint_success,
        "rendered_counts_by_source": rendered,
        "missing_sources": missing_sources,
    }

def _run_variant(doc, view, out_dir, base, variant, items, resolution_report, resolution_suffix):
    from Autodesk.Revit.DB import Transaction, TransactionGroup, TransactionStatus
    report = {"variant": variant, "transaction_group": {}, "state": {}, "assignments": [], "paint_diagnostics": [], "hidden_link_fallback": [], "export": {}, "image_analysis": {}, "classification": {}, "exceptions": [], "conclusion": "INCONCLUSIVE"}
    group = None
    started = False
    try:
        _force_close_dynamo_transaction()
        report["state"]["before"] = _snapshot(doc, view, items)
        group = TransactionGroup(doc, "VOP Stage A external sources: " + variant)
        st = group.Start(); started = st == TransactionStatus.Started
        report["transaction_group"]["start_status"] = _safe_enum(st)
        if not started:
            raise RuntimeError("TransactionGroup.Start returned {0}".format(st))
        tx = Transaction(doc, "VOP Stage A external source paint: " + variant)
        tx.Start()
        try:
            applied = _apply_assignments(doc, view, variant, items)
            report["assignments"] = applied["assignments"]
            report["paint_diagnostics"] = applied.get("diagnostics", [])
            report["hidden_link_fallback"] = applied.get("hidden_link_fallback_ids", [])
            report["palette_step"] = applied.get("palette_step")
            commit = tx.Commit()
            report["transaction_group"]["child_commit_status"] = _safe_enum(commit)
            if commit != TransactionStatus.Committed:
                raise RuntimeError("Child Transaction.Commit returned {0}".format(commit))
        except Exception:
            try:
                tx.RollBack()
            except Exception as ex:
                report["exceptions"].append(_exception_record("child_rollback", ex))
            raise
        report["transaction_group"]["no_child_transaction_open_at_export"] = not bool(doc.IsModifiable)
        path = os.path.join(out_dir, "{0}.{1}.{2}.external_sources.tiff".format(base, resolution_suffix, variant))
        t0 = time.time()
        exported, accepted = _export_tiff(doc, view, path, resolution_report["accepted_width_px"])
        actual_w, actual_h = _actual_tiff_dimensions(exported)
        resolution = _finalize_resolution_report(_resolution_report_for_accepted_width(resolution_report, accepted), actual_w, actual_h)
        report["export"] = {"path": exported, "accepted_pixel_size": accepted, "requested_pixel_size": resolution_report["requested_width_px"], "timing_ms": round((time.time() - t0) * 1000.0, 3), "resolution": resolution}
        report["image_analysis"] = _analyze(exported, report["assignments"])
        report["classification"] = _classify_variant(variant, {"assignments": report["assignments"], "hidden_link_fallback_ids": report["hidden_link_fallback"]}, report["image_analysis"])
    except Exception as ex:
        report["exceptions"].append(_exception_record("variant", ex))
    finally:
        if started and group is not None:
            report["transaction_group"]["rollback_attempted"] = True
            try:
                rb = group.RollBack()
                report["transaction_group"]["rollback_status"] = _safe_enum(rb)
                report["transaction_group"]["rollback_succeeded"] = rb == TransactionStatus.RolledBack
            except Exception as ex:
                report["exceptions"].append(_exception_record("group_rollback", ex))
                report["transaction_group"]["rollback_succeeded"] = False
        report["state"]["after"] = _snapshot(doc, view, items)
        report["state"]["differences_after_rollback"] = _diff(report["state"].get("before", {}), report["state"].get("after", {}))
        report["evidence_status"] = _source_evidence_status(variant, report.get("assignments", []), report.get("image_analysis", {}), report.get("hidden_link_fallback", []))
        if report["exceptions"] or not report["transaction_group"].get("rollback_succeeded") or report["state"].get("differences_after_rollback"):
            report["conclusion"] = "FAIL"
        elif report["evidence_status"].get("has_required_evidence"):
            report["conclusion"] = "PASS"
        else:
            report["conclusion"] = "INCONCLUSIVE"
    return report


def _run_native(raw_view, output_dir, raw_links, raw_dwgs, selection="all", resolution_policy=DEFAULT_RESOLUTION_POLICY, target_dpi=DEFAULT_TARGET_DPI, fixed_pixel_width=DEFAULT_FIXED_PIXEL_WIDTH, max_pixel_dimension=DEFAULT_MAX_PIXEL_DIMENSION):
    out_dir = os.path.abspath(str(output_dir or os.getcwd()))
    _ensure_repo_import_path(out_dir)
    _ensure_revit_api_reference()
    doc = _doc()
    view = _unwrap(raw_view)
    reject = _reject_reason(view)
    if reject:
        return {"conclusion": "FAIL", "reason": reject}
    links = _unwrap_many(raw_links)
    dwgs = _unwrap_many(raw_dwgs)
    by_source, discovery = _discover_assignments(doc, view, links, dwgs)
    probe_dir = os.path.join(out_dir, "external_sources_probe")
    if not os.path.isdir(probe_dir):
        os.makedirs(probe_dir)
    base = "{0}_{1}".format(_safe_name(getattr(view, "Name", "view")), _safe_int_id(view.Id))
    selected_variants = select_variants(selection)
    report = {"probe": {"name": PROBE_NAME, "version": PROBE_VERSION, "target": "Revit 2025 / Dynamo 3.3 CPython3"}, "inputs": {"view_id": _safe_int_id(view.Id), "view_name": getattr(view, "Name", None), "output_directory": out_dir, "link_instance_input_ids": sorted(_element_id_set(links)), "dwg_import_input_ids": sorted(_element_id_set(dwgs)), "resolution_policy": resolution_policy, "target_dpi": target_dpi, "fixed_pixel_width": fixed_pixel_width, "max_pixel_dimension": max_pixel_dimension}, "discovery": discovery, "variants": [], "conclusion": "INCONCLUSIVE"}
    for run_cfg in _resolution_runs(resolution_policy, target_dpi, fixed_pixel_width, max_pixel_dimension):
        try:
            resolution_report = _resolution_from_view(view, run_cfg)
        except Exception as ex:
            report.setdefault("resolution_diagnostics", []).append({"policy": run_cfg.get("policy"), "target_dpi": run_cfg.get("target_dpi"), "conclusion": "INCONCLUSIVE", "reason": str(ex)})
            continue
        suffix = _resolution_suffix(run_cfg)
        for variant in selected_variants:
            items = _items_for_variant(variant, by_source)
            if not items:
                report["variants"].append({"variant": variant, "resolution": resolution_report, "skipped": True, "reason": "No discovered candidates for required source type(s)", "conclusion": "INCONCLUSIVE", "assignments": []})
                continue
            report["variants"].append(_run_variant(doc, view, probe_dir, base, variant, items, resolution_report, suffix))
    report["recommended_linked_fallback"] = "When LinkElementId painting fails, hiding the owning link instance prevents unassigned linked contamination for the diagnostic export; do not treat this as final production policy without more samples."
    report["required_sidecar_schema_changes"] = ["source_type", "source_id", "source_label", "host_element_id", "link_instance_id", "linked_element_id", "import_instance_id", "paint_success", "hidden_link_fallback"]
    report["remaining_limitations"] = ["Dynamo/Revit runtime required for API behavior", "Pillow required for exact-color counts", "DWG fine attribution is limited to what repository import proxies expose; ImportInstance coloring is instance-level"]
    required_family_variants = {
        "HOST": ["host_reference_coloring"],
        "LINK": ["linked_per_element_linkelementid_coloring", "forced_linked_override_failure_hide_instance_fallback"],
        "DWG": ["dwg_importinstance_coloring"],
    }
    family_status = {}
    for family, names in required_family_variants.items():
        matching = [v for v in report["variants"] if v.get("variant") in names]
        family_status[family] = {
            "required_variants": names,
            "ran": bool(matching) and all(not v.get("skipped") for v in matching),
            "passed": bool(matching) and all(v.get("conclusion") == "PASS" for v in matching),
        }
    report["required_source_family_status"] = family_status
    if any(v.get("conclusion") == "FAIL" for v in report["variants"]):
        report["conclusion"] = "FAIL"
    elif all(status["ran"] and status["passed"] for status in family_status.values()):
        report["conclusion"] = "PASS"
    else:
        report["conclusion"] = "INCONCLUSIVE"
    json_path = os.path.join(probe_dir, base + ".external_sources.json")
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)
    report["paths"] = {"combined_json": json_path, "tiffs": [v.get("export", {}).get("path") for v in report["variants"] if v.get("export", {}).get("path")]}
    return report


def run_probe(raw_view, output_dir, raw_links=None, raw_dwgs=None, selection="all",
              resolution_policy=DEFAULT_RESOLUTION_POLICY, target_dpi=DEFAULT_TARGET_DPI,
              fixed_pixel_width=DEFAULT_FIXED_PIXEL_WIDTH,
              max_pixel_dimension=DEFAULT_MAX_PIXEL_DIMENSION):
    started_at = utc_now_iso()
    native = _run_native(raw_view, output_dir, raw_links, raw_dwgs, selection,
                         resolution_policy, target_dpi, fixed_pixel_width, max_pixel_dimension)
    variants = native.get("variants", [])
    rollback_ok = bool(variants) and all(item.get("transaction_group", {}).get("rollback_succeeded") for item in variants)
    restored = bool(variants) and all(not item.get("state", {}).get("differences_after_rollback") for item in variants)
    errors = [error for item in variants for error in item.get("exceptions", [])]
    paths = native.get("paths", {})
    artifacts = ([paths.get("combined_json")] if paths.get("combined_json") else []) + list(paths.get("tiffs", []))
    return execution_envelope(
        PROBE_NAME, {"selection": [item.get("variant") for item in variants],
                     "resolution_policy": resolution_policy, "target_dpi": target_dpi,
                     "fixed_pixel_width": fixed_pixel_width, "max_pixel_dimension": max_pixel_dimension},
        view_identity(raw_view), native, artifacts, "succeeded" if rollback_ok else "failed",
        "restored" if restored else "not_restored", started_at,
        execution_status="failed" if errors or not rollback_ok or not restored else "completed", errors=errors)


def dynamo_main(inputs):
    # IN[0:8] retain their historical meaning; IN[8] optionally selects variants.
    return run_probe(inputs[0] if len(inputs) > 0 else None,
                     inputs[1] if len(inputs) > 1 else None,
                     inputs[2] if len(inputs) > 2 else None,
                     inputs[3] if len(inputs) > 3 else None,
                     inputs[8] if len(inputs) > 8 else "all",
                     inputs[4] if len(inputs) > 4 else DEFAULT_RESOLUTION_POLICY,
                     inputs[5] if len(inputs) > 5 else DEFAULT_TARGET_DPI,
                     inputs[6] if len(inputs) > 6 else DEFAULT_FIXED_PIXEL_WIDTH,
                     inputs[7] if len(inputs) > 7 else DEFAULT_MAX_PIXEL_DIMENSION)


if "IN" in globals():
    try:
        OUT = dynamo_main(IN)
    except Exception as ex:
        OUT = {"conclusion": "FAIL", "error": str(ex), "error_type": type(ex).__name__, "traceback": traceback.format_exc()}
