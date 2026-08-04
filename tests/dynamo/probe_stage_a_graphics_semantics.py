"""
Standalone Stage A graphics-semantics probe for Revit 2025 / Dynamo 3.3 CPython3.

Dynamo inputs:
    IN[0] = target model-capable view
    IN[1] = output directory
    IN[2] = maximum elements to color, null/0 means all eligible elements
    IN[3] = variant selection, "all" by default

The probe intentionally does not modify production Stage A code.  Every variant
runs in an independent TransactionGroup, commits child transactions, exports
with no child transaction open, and rolls back the group in finally.
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

PROBE_NAME = "stage_a_graphics_semantics"
PROBE_VERSION = "2026-07-24.1"
REQUESTED_PIXEL_SIZE = 1600
VARIANTS = [
    "element_overrides_only",
    "element_overrides_smooth_edges_off",
    "element_overrides_flat_colors",
    "template_detached_flat_colors_smooth_edges_off",
    "visible_filters_disabled",
    "category_halftone_neutralized",
    "neutral_phase_filter",
    "current_stage_a_full_suppression",
    "visibility_off_filters_disabled_diagnostic",
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


def _candidate_repo_roots(output_dir):
    roots = []
    for mod_name in ("vop_interwoven",):
        mod = sys.modules.get(mod_name)
        path = getattr(mod, "__file__", None)
        if path:
            roots.append(os.path.dirname(os.path.dirname(os.path.abspath(path))))
    for env in ("REVIT_SSM_EXPORTER_ROOT", "VOP_REPO_ROOT"):
        if os.environ.get(env):
            roots.append(os.environ[env])
    for seed in (output_dir, os.getcwd()):
        if not seed:
            continue
        cur = os.path.abspath(seed)
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
    except Exception:
        pass


def _unwrap(value):
    try:
        import clr
        clr.AddReference("RevitNodes")
        import Revit
        clr.ImportExtensions(Revit.Elements)
        return value.InternalElement if hasattr(value, "InternalElement") else value
    except Exception:
        return value


def _doc():
    import clr
    clr.AddReference("RevitServices")
    from RevitServices.Persistence import DocumentManager
    return DocumentManager.Instance.CurrentDBDocument


def _force_close_dynamo_transaction():
    import clr
    clr.AddReference("RevitServices")
    from RevitServices.Transactions import TransactionManager
    TransactionManager.Instance.ForceCloseTransaction()


def _validate_view(view):
    _ensure_revit_api_reference()
    from Autodesk.Revit.DB import ViewType
    if view is None:
        raise ValueError("IN[0] target view is required")
    if bool(getattr(view, "IsTemplate", False)):
        raise ValueError("View templates are unsupported; provide a model-capable view instance")
    if getattr(view, "ViewType", None) == ViewType.ThreeD and bool(getattr(view, "IsPerspective", False)):
        raise ValueError("Perspective 3D views are unsupported by this Stage A probe")
    vt = getattr(view, "ViewType", None)
    unsupported = (ViewType.Schedule, ViewType.DrawingSheet, ViewType.Legend, ViewType.ProjectBrowser, ViewType.SystemBrowser, ViewType.Internal)
    if vt in unsupported:
        raise ValueError("Unsupported/annotation-only view type for Stage A graphics probe: {0}".format(vt))


def _snapshot(doc, view):
    state = {"doc_is_modified": bool(getattr(doc, "IsModified", False)), "view_template_id": _safe_int_id(getattr(view, "ViewTemplateId", None)), "display_style": _safe_enum(getattr(view, "DisplayStyle", None)), "filters": {}, "phase_filter_id": None, "smooth_edges": None, "crop_active": None}
    try:
        state["crop_active"] = bool(view.CropBoxActive)
    except Exception as ex:
        state["crop_active"] = "unavailable: {0}".format(ex)
    try:
        dm = view.GetViewDisplayModel()
        try:
            state["smooth_edges"] = bool(dm.SmoothEdges)
        finally:
            try: dm.Dispose()
            except Exception: pass
    except Exception as ex:
        state["smooth_edges"] = "unavailable: {0}".format(ex)
    try:
        from Autodesk.Revit.DB import BuiltInParameter
        p = view.get_Parameter(BuiltInParameter.VIEW_PHASE_FILTER)
        state["phase_filter_id"] = _safe_int_id(p.AsElementId()) if p is not None else None
    except Exception as ex:
        state["phase_filter_id"] = "unavailable: {0}".format(ex)
    try:
        for fid in view.GetFilters():
            state["filters"][str(_safe_int_id(fid))] = {"enabled": bool(view.GetIsFilterEnabled(fid)), "visible": bool(view.GetFilterVisibility(fid))}
    except Exception as ex:
        state["filters_error"] = str(ex)
    return state


def _diff(a, b, path=""):
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            p = (path + "." + str(k)) if path else str(k)
            if k not in a or k not in b:
                out.append({"path": p, "before": a.get(k), "after": b.get(k)})
            else:
                out.extend(_diff(a[k], b[k], p))
    elif a != b:
        out.append({"path": path, "before": a, "after": b})
    return out


def _collect_elements(doc, view, max_count):
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
    host, links = _split_expanded_elements(expanded)
    resolved_ids = resolve_all(doc, host)
    identities = []
    for eid in resolved_ids:
        elem = doc.GetElement(eid)
        cat = getattr(elem, "Category", None) if elem is not None else None
        int_id = _safe_int_id(eid)
        identities.append({"key": str(int_id), "element_id": int_id, "source_type": "HOST", "category": getattr(cat, "Name", None), "unique_id": getattr(elem, "UniqueId", None), "api_id": eid})
    for li, le, proxy in links:
        cat = getattr(proxy, "Category", None)
        link_instance_id = _safe_int_id(li)
        linked_element_id = _safe_int_id(le)
        identities.append({"key": "{0}:{1}".format(link_instance_id, linked_element_id), "link_instance_id": link_instance_id, "linked_element_id": linked_element_id, "source_type": "LINK", "category": getattr(cat, "Name", None), "api_id": None, "link_tuple": (li, le)})
    if max_count and int(max_count) > 0:
        identities = identities[:int(max_count)]
    return cfg, diag, raster, identities, len(top)


def _get_solid_pattern_id(doc):
    from vop_interwoven.color_id_buffer import _get_solid_pattern_id
    return _get_solid_pattern_id(doc)


def _ogs_for(doc, rgb):
    from Autodesk.Revit.DB import Color
    from vop_interwoven.color_id_buffer import _build_flat_color_ogs
    return _build_flat_color_ogs(_get_solid_pattern_id(doc), Color(int(rgb[0]), int(rgb[1]), int(rgb[2])))


def _palette(n):
    from vop_interwoven.color_id_buffer import build_palette, choose_step
    step = choose_step(max(n, 1))
    return build_palette(n, step=step), step


def _set_smooth_edges(view, value, diagnostics):
    try:
        dm = view.GetViewDisplayModel()
        try:
            dm.SmoothEdges = bool(value)
            view.SetViewDisplayModel(dm)
        finally:
            try: dm.Dispose()
            except Exception: pass
        return True
    except Exception as ex:
        diagnostics.append({"setting": "smooth_edges", "message": str(ex)})
        return False


def _set_flat_colors(view, diagnostics):
    try:
        from Autodesk.Revit.DB import DisplayStyle
        flat = getattr(DisplayStyle, "FlatColors", None)
        if flat is None:
            diagnostics.append({"setting": "display_style", "message": "DisplayStyle.FlatColors unavailable"})
            return False
        view.DisplayStyle = flat
        return True
    except Exception as ex:
        diagnostics.append({"setting": "display_style", "message": str(ex)})
        return False


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


def _disable_filters(view, disable_visibility_off, diagnostics):
    from Autodesk.Revit.DB import ElementId
    changed = []
    try:
        for fid in view.GetFilters():
            enabled = bool(view.GetIsFilterEnabled(fid))
            visible = bool(view.GetFilterVisibility(fid))
            if enabled and (visible or disable_visibility_off):
                view.SetIsFilterEnabled(ElementId(int(fid.IntegerValue)), False)
                changed.append({"filter_id": fid.IntegerValue, "was_visible": visible})
    except Exception as ex:
        diagnostics.append({"setting": "filters", "message": str(ex)})
    return changed


def _neutral_phase(view, diagnostics):
    try:
        from Autodesk.Revit.DB import BuiltInParameter
        from vop_interwoven.color_id_buffer import get_or_create_neutral_phase_filter
        p = view.get_Parameter(BuiltInParameter.VIEW_PHASE_FILTER)
        if p is None or p.IsReadOnly:
            diagnostics.append({"setting": "phase_filter", "message": "VIEW_PHASE_FILTER missing or read-only"})
            return None
        pf, created = get_or_create_neutral_phase_filter(view.Document)
        p.Set(pf.Id)
        return {"neutral_phase_filter_id": pf.Id.IntegerValue, "created": bool(created)}
    except Exception as ex:
        diagnostics.append({"setting": "phase_filter", "message": str(ex)})
        return None


def _neutralize_category_halftone(doc, view, identities, diagnostics):
    from Autodesk.Revit.DB import ElementId
    touched = []
    cats = set()
    for item in identities:
        if item.get("source_type") == "HOST":
            elem = doc.GetElement(item.get("api_id"))
            cat = getattr(elem, "Category", None) if elem is not None else None
        else:
            cat = None
        if cat is not None and getattr(cat, "Id", None) is not None:
            cats.add(cat.Id.IntegerValue)
    for cid in sorted(cats):
        try:
            cid_obj = ElementId(int(cid))
            ogs = view.GetCategoryOverrides(cid_obj)
            was = bool(ogs.Halftone)
            ogs.SetHalftone(False)
            view.SetCategoryOverrides(cid_obj, ogs)
            touched.append({"category_id": cid, "was_halftone": was})
        except Exception as ex:
            diagnostics.append({"setting": "category_halftone", "category_id": cid, "message": str(ex)})
    return touched


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
    from Autodesk.Revit.DB import CategoryType, BuiltInCategory
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


def _paint(doc, view, identities, diagnostics):
    from vop_interwoven.color_id_buffer import _try_color_link_element
    palette, step = _palette(len(identities))
    assigned, failures = {}, []
    for i, item in enumerate(identities):
        rgb = palette[i]
        assigned[item["key"]] = {"rgb": list(rgb), "identity": {k: v for k, v in item.items() if k not in ("api_id", "link_tuple")}}
        try:
            ogs = _ogs_for(doc, rgb)
            if item.get("source_type") == "LINK" and item.get("link_tuple"):
                ok = _try_color_link_element(view, item["link_tuple"][0], item["link_tuple"][1], ogs)
                if not ok:
                    failures.append({"key": item["key"], "reason": "LinkElementId override unsupported"})
            else:
                view.SetElementOverrides(item["api_id"], ogs)
        except Exception as ex:
            failures.append({"key": item["key"], "type": type(ex).__name__, "message": str(ex)})
    return assigned, failures, step


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


def _export_tiff(doc, view, path):
    from Autodesk.Revit.DB import ImageExportOptions, ExportRange, ZoomFitType, FitDirectionType, ElementId, ImageFileType
    import System.Collections.Generic as SCG
    out_dir = os.path.dirname(path)
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    before = set(os.listdir(out_dir))
    ids = SCG.List[ElementId](); ids.Add(view.Id)
    opts = ImageExportOptions(); opts.ExportRange = ExportRange.SetOfViews; opts.SetViewsAndSheets(ids)
    opts.ZoomType = ZoomFitType.FitToPage; opts.FitDirection = FitDirectionType.Horizontal
    accepted = _set_pixel_size(opts, REQUESTED_PIXEL_SIZE)
    opts.FilePath = os.path.join(out_dir, "_vop_graphics_semantics_tmp")
    tiff = getattr(ImageFileType, "TIFF", getattr(ImageFileType, "TIF", None))
    if tiff is None:
        raise RuntimeError("Revit ImageFileType does not expose TIFF/TIF")
    opts.HLRandWFViewsFileType = tiff; opts.ShadowViewsFileType = tiff
    doc.ExportImage(opts)
    candidates = [f for f in (set(os.listdir(out_dir)) - before) if f.lower().endswith((".tif", ".tiff"))]
    if not candidates:
        raise RuntimeError("ExportImage produced no new TIFF in {0}".format(out_dir))
    candidates.sort(key=lambda n: os.path.getmtime(os.path.join(out_dir, n)), reverse=True)
    created = os.path.join(out_dir, candidates[0])
    if os.path.exists(path): os.remove(path)
    os.rename(created, path)
    return path, accepted


def _analyze_image(path, assigned):
    result = {"path": path, "actual_dimensions": None, "file_size_bytes": None, "sha256": None, "pillow_available": False}
    if os.path.exists(path):
        result["file_size_bytes"] = int(os.path.getsize(path))
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        result["sha256"] = h.hexdigest()
    if importlib.util.find_spec("PIL") is None:
        result["diagnostic"] = "Pillow unavailable; pixel analysis skipped"
        return result
    from PIL import Image
    img = Image.open(path).convert("RGB")
    result["pillow_available"] = True
    w, h = img.size
    result["actual_dimensions"] = [int(w), int(h)]
    pixels = list(img.getdata())
    expected = {tuple(v["rgb"]): k for k, v in assigned.items()}
    expected_counts = dict((k, 0) for k in assigned.keys())
    bg = off = near_white_bad = black_bad = 0
    unexpected = {}
    for p in pixels:
        if p in expected:
            expected_counts[expected[p]] += 1
        elif p == (255, 255, 255):
            bg += 1
        elif p[0] >= 224 and p[1] >= 224 and p[2] >= 224:
            near_white_bad += 1
            off += 1
            unexpected[str(p)] = unexpected.get(str(p), 0) + 1
        else:
            off += 1
            if p == (0, 0, 0): black_bad += 1
            unexpected[str(p)] = unexpected.get(str(p), 0) + 1
    foreground = max(1, len(pixels) - bg)
    result.update({
        "expected_palette_pixel_count": int(sum(expected_counts.values())),
        "expected_color_pixel_counts": expected_counts,
        "off_palette_foreground_pixel_count": int(off),
        "off_palette_foreground_percent": round(100.0 * off / foreground, 6),
        "background_pixel_count": int(bg),
        "black_violation_pixel_count": int(black_bad),
        "near_white_violation_pixel_count": int(near_white_bad),
        "expected_colors_detected": int(sum(1 for c in expected_counts.values() if c > 0)),
        "missing_assigned_colors": sorted([k for k, c in expected_counts.items() if c <= 0]),
        "unexpected_colors_top_50": sorted(unexpected.items(), key=lambda kv: kv[1], reverse=True)[:50],
        "visible_pixel_area_by_element": expected_counts,
    })
    return result


def _variant_steps(name):
    steps = ["paint"]
    if name in ("element_overrides_smooth_edges_off", "template_detached_flat_colors_smooth_edges_off", "current_stage_a_full_suppression"):
        steps.append("smooth_edges_off")
    if name in ("element_overrides_flat_colors", "template_detached_flat_colors_smooth_edges_off", "current_stage_a_full_suppression"):
        steps.append("flat_colors")
    if name in ("template_detached_flat_colors_smooth_edges_off", "current_stage_a_full_suppression"):
        steps.insert(0, "detach_template")
    if name in ("visible_filters_disabled", "current_stage_a_full_suppression"):
        steps.append("disable_visible_filters")
    if name == "visibility_off_filters_disabled_diagnostic":
        steps.append("disable_all_filters")
    if name in ("neutral_phase_filter", "current_stage_a_full_suppression"):
        steps.append("neutral_phase")
    if name == "current_stage_a_full_suppression":
        steps.append("hide_annotation_categories")
    if name in ("category_halftone_neutralized", "current_stage_a_full_suppression"):
        steps.append("category_halftone")
    return steps


def _run_variant(doc, view, out_dir, base, name, max_count):
    from Autodesk.Revit.DB import Transaction, TransactionGroup, TransactionStatus
    result = {"variant": name, "definition_steps": _variant_steps(name), "transaction_group": {}, "state": {}, "element_counts": {}, "assigned_elements": {}, "paint_failures": [], "mutations": {}, "image_analysis": {}, "exceptions": [], "conclusion": "INCONCLUSIVE"}
    tiff_path = os.path.join(out_dir, base + "." + name + ".tiff")
    group = None
    started = False
    try:
        _force_close_dynamo_transaction()
        result["state"]["before"] = _snapshot(doc, view)
        group = TransactionGroup(doc, "VOP Stage A graphics semantics: " + name)
        st = group.Start(); started = st == TransactionStatus.Started
        result["transaction_group"]["start_status"] = _safe_enum(st)
        if not started:
            raise RuntimeError("TransactionGroup.Start returned {0}".format(st))
        cfg, diag, raster, identities, top_count = _collect_elements(doc, view, max_count)
        result["element_counts"]["initial_collect_view_elements"] = int(top_count)
        result["element_counts"]["initial_expanded_or_resolved"] = int(len(identities))
        diagnostics = []
        # Detach the view template in its own committed transaction, same as
        # color_id_buffer._detach... / detach_tx: CanCategoryBeHidden() and
        # GetCategoryHidden() report against committed document state, so a
        # detach that is still pending inside the same open transaction as the
        # hide_annotation_categories step is not guaranteed to unlock template-
        # controlled categories before they're checked below.
        if "detach_template" in result["definition_steps"]:
            detach_tx = Transaction(doc, "VOP Stage A graphics semantics DETACH template: " + name)
            detach_tx.Start()
            try:
                result["mutations"]["detach_template"] = _detach_template(view, diagnostics)
                detach_commit = detach_tx.Commit()
                result["transaction_group"]["detach_commit_status"] = _safe_enum(detach_commit)
                if detach_commit != TransactionStatus.Committed:
                    raise RuntimeError("Detach Transaction.Commit returned {0}".format(detach_commit))
            except Exception:
                try: detach_tx.RollBack()
                except Exception as ex: result["exceptions"].append(_exception_record("detach_rollback", ex))
                raise
        tx = Transaction(doc, "VOP Stage A graphics semantics mutate: " + name)
        tx.Start()
        try:
            for step in result["definition_steps"]:
                if step == "detach_template": continue  # already committed above
                elif step == "smooth_edges_off": result["mutations"][step] = _set_smooth_edges(view, False, diagnostics)
                elif step == "flat_colors": result["mutations"][step] = _set_flat_colors(view, diagnostics)
                elif step == "disable_visible_filters": result["mutations"][step] = _disable_filters(view, False, diagnostics)
                elif step == "disable_all_filters": result["mutations"][step] = _disable_filters(view, True, diagnostics)
                elif step == "neutral_phase": result["mutations"][step] = _neutral_phase(view, diagnostics)
                elif step == "category_halftone": result["mutations"][step] = _neutralize_category_halftone(doc, view, identities, diagnostics)
                elif step == "hide_annotation_categories":
                    hidden, trace = _hide_annotation_categories(doc, view, diagnostics)
                    result["mutations"][step] = hidden
                    result["category_hide_trace"] = trace
            recollect_reasons = [step for step in ("disable_visible_filters", "disable_all_filters", "neutral_phase") if step in result["definition_steps"]]
            if recollect_reasons:
                _cfg2, _diag2, _raster2, identities, top_count2 = _collect_elements(doc, view, max_count)
                result["element_counts"]["after_visibility_mutation_collect_view_elements"] = int(top_count2)
                result["element_counts"]["after_visibility_mutation_expanded_or_resolved"] = int(len(identities))
                result["element_counts"]["recollection_reasons"] = recollect_reasons
            assigned, failures, step = _paint(doc, view, identities, diagnostics)
            result["assigned_elements"] = assigned
            result["paint_failures"] = failures
            result["palette_step"] = step
            if diagnostics: result["mutation_diagnostics"] = diagnostics
            commit = tx.Commit(); result["transaction_group"]["child_commit_status"] = _safe_enum(commit)
            if commit != TransactionStatus.Committed:
                raise RuntimeError("Child Transaction.Commit returned {0}".format(commit))
        except Exception:
            try: tx.RollBack()
            except Exception as ex: result["exceptions"].append(_exception_record("child_rollback", ex))
            raise
        result["transaction_group"]["no_child_transaction_open_at_export"] = not bool(doc.IsModifiable)
        start = time.time(); path, accepted = _export_tiff(doc, view, tiff_path); elapsed = (time.time() - start) * 1000.0
        result["export"] = {"path": path, "requested_pixel_size": REQUESTED_PIXEL_SIZE, "accepted_pixel_size": accepted, "timing_ms": round(elapsed, 3)}
        result["image_analysis"] = _analyze_image(path, result["assigned_elements"])
    except Exception as ex:
        result["exceptions"].append(_exception_record("variant", ex))
    finally:
        if started and group is not None:
            result["transaction_group"]["rollback_attempted"] = True
            try:
                rb = group.RollBack(); result["transaction_group"]["rollback_status"] = _safe_enum(rb); result["transaction_group"]["rollback_succeeded"] = rb == TransactionStatus.RolledBack
            except Exception as ex:
                result["exceptions"].append(_exception_record("group_rollback", ex)); result["transaction_group"]["rollback_succeeded"] = False
        result["state"]["after"] = _snapshot(doc, view)
        result["state"]["differences_after_rollback"] = _diff(result["state"].get("before", {}), result["state"].get("after", {}))
        if result["exceptions"] or not result["transaction_group"].get("rollback_succeeded") or result["state"].get("differences_after_rollback"):
            result["conclusion"] = "FAIL"
        elif result.get("image_analysis", {}).get("pillow_available"):
            result["conclusion"] = "PASS"
        else:
            result["conclusion"] = "INCONCLUSIVE"
    return result


def _recommend(results):
    rec = {"minimum_settings_required_for_exact_color_fidelity": [], "settings_that_only_change_visibility_semantics": [], "settings_unnecessary_in_tested_view": [], "settings_blocked_by_view_template": [], "questions_still_requiring_more_view_samples": []}
    metrics = {}
    for r in results:
        ia = r.get("image_analysis", {})
        if ia.get("pillow_available"):
            metrics[r["variant"]] = ia.get("off_palette_foreground_percent")
    if metrics:
        best = min(metrics, key=lambda k: metrics[k])
        rec["minimum_settings_required_for_exact_color_fidelity"].append("Lowest off-palette percentage in this run: {0} ({1}%). Compare cumulatively, not as a production recommendation.".format(best, metrics[best]))
    for name in ("visible_filters_disabled", "visibility_off_filters_disabled_diagnostic", "neutral_phase_filter", "current_stage_a_full_suppression"):
        if any(r["variant"] == name for r in results):
            rec["settings_that_only_change_visibility_semantics"].append(name)
    for r in results:
        for d in r.get("mutation_diagnostics", []):
            if "read-only" in d.get("message", "").lower() or "template" in d.get("setting", ""):
                rec["settings_blocked_by_view_template"].append({"variant": r["variant"], "diagnostic": d})
    rec["questions_still_requiring_more_view_samples"].append("Repeat the documented matrix; do not generalize from one view because filters, templates, phase filters, and halftone differ by view.")
    return rec


def run(raw_view, output_dir, max_elements, selection):
    out_dir = os.path.abspath(str(output_dir or os.getcwd()))
    _ensure_repo_import_path(out_dir)
    _ensure_revit_api_reference()
    doc = _doc(); view = _unwrap(raw_view); _validate_view(view)
    selected = str(selection or "all")
    variants = list(VARIANTS) if selected == "all" else [selected]
    bad = [v for v in variants if v not in VARIANTS]
    if bad: raise ValueError("Unsupported variant(s): {0}. Supported: {1}".format(bad, VARIANTS))
    probe_dir = os.path.join(out_dir, "graphics_semantics_probe")
    if not os.path.isdir(probe_dir): os.makedirs(probe_dir)
    base = "{0}_{1}.graphics_semantics".format(_safe_name(view.Name), _safe_int_id(view.Id))
    report = {"probe": {"name": PROBE_NAME, "version": PROBE_VERSION, "target": "Revit 2025 / Dynamo 3.3 CPython3"}, "inputs": {"view_id": _safe_int_id(view.Id), "view_name": getattr(view, "Name", None), "output_directory": out_dir, "maximum_elements_to_color": max_elements, "variant_selection": selected}, "variants": [], "recommendation": {}, "conclusion": "INCONCLUSIVE"}
    for variant in variants:
        report["variants"].append(_run_variant(doc, view, probe_dir, base, variant, max_elements))
    report["recommendation"] = _recommend(report["variants"])
    if any(v.get("conclusion") == "FAIL" for v in report["variants"]): report["conclusion"] = "FAIL"
    elif all(v.get("conclusion") == "PASS" for v in report["variants"]): report["conclusion"] = "PASS"
    json_path = os.path.join(probe_dir, base + ".json")
    with open(json_path, "w") as f: json.dump(report, f, indent=2, sort_keys=True)
    report["paths"] = {"combined_json": json_path, "tiffs": [v.get("export", {}).get("path") for v in report["variants"] if v.get("export", {}).get("path")]}
    return report


try:
    raw_view = IN[0] if "IN" in globals() and len(IN) > 0 else None
    output_dir = IN[1] if "IN" in globals() and len(IN) > 1 else None
    max_elements = IN[2] if "IN" in globals() and len(IN) > 2 else None
    selection = IN[3] if "IN" in globals() and len(IN) > 3 else "all"
    OUT = run(raw_view, output_dir, max_elements, selection)
except Exception as ex:
    OUT = {"conclusion": "FAIL", "error": str(ex), "error_type": type(ex).__name__, "traceback": traceback.format_exc()}
