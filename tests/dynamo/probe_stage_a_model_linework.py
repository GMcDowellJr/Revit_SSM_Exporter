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
import importlib.util
import json
import os
import re
import sys
import time
import traceback

PROBE_NAME = "stage_a_model_linework"
PROBE_VERSION = "2026-07-24.1"
REQUESTED_PIXEL_SIZE = 1600
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


def _export_tiff(doc, view, output_path):
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
    accepted = _set_pixel_size(opts, REQUESTED_PIXEL_SIZE)
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


def _connected_components(mask, w, h):
    seen = set()
    sizes = []
    for y in range(h):
        for x in range(w):
            if not mask[y][x] or (x, y) in seen:
                continue
            stack = [(x, y)]
            seen.add((x, y))
            count = 0
            while stack:
                px, py = stack.pop()
                count += 1
                for nx, ny in ((px + 1, py), (px - 1, py), (px, py + 1), (px, py - 1)):
                    if 0 <= nx < w and 0 <= ny < h and mask[ny][nx] and (nx, ny) not in seen:
                        seen.add((nx, ny))
                        stack.append((nx, ny))
            sizes.append(count)
    sizes.sort(reverse=True)
    return {"count": len(sizes), "largest": sizes[:20], "total_dark_component_pixels": sum(sizes)}


def _analyze_image(path, reference_dims=None):
    result = {"path": path, "file_size_bytes": None, "sha256": None, "dimensions": None, "pillow_available": False}
    if os.path.exists(path):
        result["file_size_bytes"] = int(os.path.getsize(path))
        result["sha256"] = _image_sha(path)
    if importlib.util.find_spec("PIL") is None:
        result["diagnostic"] = "Pillow unavailable; pixel analysis skipped"
        return result
    from PIL import Image
    img = Image.open(path).convert("RGB")
    w, h = img.size
    result["pillow_available"] = True
    result["dimensions"] = [int(w), int(h)]
    pixels = list(img.getdata())
    dark = gray = unexpected = bg = 0
    minx = miny = None
    maxx = maxy = None
    mask = [[False for _ in range(w)] for _ in range(h)] if w * h <= 4000000 else None
    for y in range(h):
        for x in range(w):
            r, g, b = pixels[y * w + x]
            is_bg = r >= WHITE_THRESHOLD and g >= WHITE_THRESHOLD and b >= WHITE_THRESHOLD
            is_dark = r <= DARK_THRESHOLD and g <= DARK_THRESHOLD and b <= DARK_THRESHOLD
            is_gray = abs(r - g) <= 2 and abs(g - b) <= 2
            if is_bg:
                bg += 1
                continue
            minx = x if minx is None else min(minx, x)
            miny = y if miny is None else min(miny, y)
            maxx = x if maxx is None else max(maxx, x)
            maxy = y if maxy is None else max(maxy, y)
            if is_dark:
                dark += 1
                if mask is not None:
                    mask[y][x] = True
            if is_gray:
                gray += 1
            else:
                unexpected += 1
    foreground = max(0, len(pixels) - bg)
    non_dark_gray = max(0, int(gray) - int(dark))
    result.update({
        "dark_line_pixel_count": int(dark),
        "grayscale_non_background_pixel_count": int(gray),
        "non_dark_gray_foreground_pixel_count": int(non_dark_gray),
        "foreground_pixel_count": int(foreground),
        "unexpected_color_pixel_count": int(unexpected),
        "background_pixel_count": int(bg),
        "non_background_content_rect": ([minx, miny, maxx, maxy] if minx is not None else None),
        "connected_components": (_connected_components(mask, w, h) if mask is not None else {"skipped": True, "reason": "image exceeds inexpensive component threshold"}),
    })
    if reference_dims:
        result["reference_dimension_agreement"] = {"reference_dimensions": reference_dims, "matches": list(reference_dims) == [int(w), int(h)]}
    return result


def _write_diff(path_a, path_b, out_path):
    if importlib.util.find_spec("PIL") is None:
        return {"created": False, "reason": "Pillow unavailable"}
    from PIL import Image, ImageChops
    a = Image.open(path_a).convert("RGB")
    b = Image.open(path_b).convert("RGB")
    if a.size != b.size:
        return {"created": False, "reason": "dimension mismatch", "a_size": list(a.size), "b_size": list(b.size)}
    diff = ImageChops.difference(a, b)
    diff.save(out_path)
    return {"created": True, "path": out_path, "sha256": _image_sha(out_path)}


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


def _run_reference_element_id(doc, view, out_dir, base):
    from Autodesk.Revit.DB import Transaction, TransactionGroup, TransactionStatus
    result = {"transaction_group": {}, "state": {}, "settings_applied": {}, "images": [], "exceptions": [], "conclusion": "INCONCLUSIVE"}
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
            path = os.path.join(out_dir, "{0}.element_id_reference_{1}.tiff".format(base, seq))
            exported, accepted = _export_tiff(doc, view, path)
            result["images"].append({"path": exported, "accepted_pixel_size": accepted, "analysis": _analyze_image(exported)})
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
        elif result.get("images") and result["images"][0]["analysis"].get("dimensions"):
            result["conclusion"] = "PASS"
        else:
            result["conclusion"] = "INCONCLUSIVE"
    return result


def _classify_mode(mode_result, reference_dims):
    ia = mode_result.get("images", [{}])[0].get("analysis", {}) if mode_result.get("images") else {}
    dims_match = bool(ia.get("dimensions") and reference_dims and ia.get("dimensions") == reference_dims)
    dark = int(ia.get("dark_line_pixel_count") or 0)
    unexpected = int(ia.get("unexpected_color_pixel_count") or 0)
    non_dark_gray = int(ia.get("non_dark_gray_foreground_pixel_count") or 0)
    foreground = int(ia.get("foreground_pixel_count") or 0)
    gray_tolerance = max(10, int(round(0.001 * max(1, foreground))))
    fill_ok = "acceptable" if unexpected == 0 and non_dark_gray <= gray_tolerance else "unacceptable"
    if mode_result.get("exceptions"):
        candidate = "rejected"
    elif dark <= 0:
        candidate = "rejected"
    elif fill_ok == "acceptable" and dims_match:
        candidate = "recommended"
    else:
        candidate = "inconclusive"
    return {
        "internal_edges": "uncertain",
        "hidden_back_edges": "uncertain",
        "fill_contamination": fill_ok,
        "fill_contamination_evidence": {"unexpected_color_pixel_count": unexpected, "non_dark_gray_foreground_pixel_count": non_dark_gray, "non_dark_gray_tolerance": gray_tolerance},
        "link_behavior": "requires_manual_review",
        "dwg_behavior": "requires_manual_review",
        "dimension_alignment": "pass" if dims_match else "fail",
        "candidate_status": candidate,
        "manual_review_required": True,
    }


def _run_mode(doc, view, out_dir, base, mode, focused_elements, reference_dims):
    from Autodesk.Revit.DB import Transaction, TransactionGroup, TransactionStatus
    result = {"mode": mode, "focused_elements": _focused_metadata(focused_elements), "transaction_group": {}, "state": {}, "settings_applied": {}, "images": [], "exceptions": [], "conclusion": "INCONCLUSIVE"}
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
            path = os.path.join(out_dir, "{0}.{1}.linework_{2}.tiff".format(base, mode, seq))
            t0 = time.time()
            exported, accepted = _export_tiff(doc, view, path)
            analysis = _analyze_image(exported, reference_dims=reference_dims)
            result["images"].append({"path": exported, "accepted_pixel_size": accepted, "timing_ms": round((time.time() - t0) * 1000.0, 3), "analysis": analysis})
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
        result["classification"] = _classify_mode(result, reference_dims)
        if result["exceptions"] or not result["transaction_group"].get("rollback_succeeded") or result["state"].get("differences_after_rollback"):
            result["conclusion"] = "FAIL"
        elif result.get("images") and result["images"][0]["analysis"].get("pillow_available"):
            result["conclusion"] = "PASS"
        else:
            result["conclusion"] = "INCONCLUSIVE"
    return result


def _select_modes(selection):
    text = str(selection or "all").strip()
    if not text or text.lower() == "all":
        return list(MODES)
    requested = [p.strip() for p in text.split(",") if p.strip()]
    bad = [m for m in requested if m not in MODES]
    if bad:
        raise ValueError("Unsupported rendering mode(s): {0}. Supported: {1}".format(bad, MODES))
    return requested


def _rank(results):
    rows = []
    for r in results:
        ia = r.get("images", [{}])[0].get("analysis", {}) if r.get("images") else {}
        rows.append({"mode": r.get("mode"), "candidate_status": r.get("classification", {}).get("candidate_status"), "dark_line_pixel_count": ia.get("dark_line_pixel_count"), "unexpected_color_pixel_count": ia.get("unexpected_color_pixel_count"), "non_dark_gray_foreground_pixel_count": ia.get("non_dark_gray_foreground_pixel_count"), "dimension_alignment": r.get("classification", {}).get("dimension_alignment")})
    return sorted(rows, key=lambda x: (x.get("candidate_status") != "recommended", -(x.get("dark_line_pixel_count") or 0), (x.get("unexpected_color_pixel_count") or 0) + (x.get("non_dark_gray_foreground_pixel_count") or 0)))


def run(raw_view, output_dir, raw_focused, selection):
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
    report = {"probe": {"name": PROBE_NAME, "version": PROBE_VERSION, "target": "Revit 2025 / Dynamo 3.3 CPython3", "production_linework_flag_default": "disabled"}, "inputs": {"view_id": _safe_int_id(view.Id), "view_name": getattr(view, "Name", None), "output_directory": out_dir, "rendering_modes": modes, "focused_elements": _focused_metadata(focused)}, "reference_element_id_tiff": None, "modes": [], "difference_images": [], "ranked_modes": [], "conclusion": "INCONCLUSIVE"}
    reference = _run_reference_element_id(doc, view, probe_dir, base)
    reference_dims = reference.get("images", [{}])[0].get("analysis", {}).get("dimensions") if reference.get("images") else None
    report["reference_element_id_tiff"] = reference
    for mode in modes:
        report["modes"].append(_run_mode(doc, view, probe_dir, base, mode, focused, reference_dims))
    if len(report["modes"]) > 1 and importlib.util.find_spec("PIL") is not None:
        ref_path = report["modes"][0].get("images", [{}])[0].get("path")
        for result in report["modes"][1:]:
            path = result.get("images", [{}])[0].get("path")
            if ref_path and path:
                diff_path = os.path.join(probe_dir, "{0}.{1}_minus_{2}.diff.tiff".format(base, result["mode"], report["modes"][0]["mode"]))
                report["difference_images"].append({"mode": result["mode"], "against": report["modes"][0]["mode"], "diff": _write_diff(ref_path, path, diff_path)})
    report["ranked_modes"] = _rank(report["modes"])
    reference_failed = reference.get("conclusion") == "FAIL"
    reference_missing_dimensions = not bool(reference_dims)
    report["reference_status"] = {"failed": bool(reference_failed), "missing_dimensions": bool(reference_missing_dimensions)}
    if reference_failed or any(r.get("conclusion") == "FAIL" for r in report["modes"]):
        report["conclusion"] = "FAIL"
    elif reference_missing_dimensions:
        report["conclusion"] = "INCONCLUSIVE"
    elif all(r.get("conclusion") == "PASS" for r in report["modes"]):
        report["conclusion"] = "PASS"
    json_path = os.path.join(probe_dir, base + ".json")
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)
    report["paths"] = {"combined_json": json_path, "tiffs": [img.get("path") for r in ([report.get("reference_element_id_tiff", {})] + report["modes"]) for img in r.get("images", [])], "diffs": [d.get("diff", {}).get("path") for d in report["difference_images"] if d.get("diff", {}).get("path")]}
    return report


try:
    raw_view = IN[0] if "IN" in globals() and len(IN) > 0 else None
    output_dir = IN[1] if "IN" in globals() and len(IN) > 1 else None
    raw_focused = IN[2] if "IN" in globals() and len(IN) > 2 else None
    selection = IN[3] if "IN" in globals() and len(IN) > 3 else "all"
    OUT = run(raw_view, output_dir, raw_focused, selection)
except Exception as ex:
    OUT = {"conclusion": "FAIL", "error": str(ex), "error_type": type(ex).__name__, "traceback": traceback.format_exc()}
