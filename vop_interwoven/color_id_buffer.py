"""Stage A color ID-buffer extraction for VOP Interwoven.

This module replaces the in-process occlusion/silhouette model extraction path
when ``Config.enable_color_id_buffer_stage_a`` is enabled.  It assigns model
elements unique flat colors, suppresses graphic effects that can distort those
colors, exports a TIFF immediately, writes a JSON sidecar, and restores view
state before returning to the next view.
"""

import json
import os
import time

NEUTRAL_PHASE_FILTER_NAME = "VOP_NeutralPhaseFilter"
STATE_FILE_NAME = "vop_color_id_buffer_state.json"
ANNOTATION_HIDE_BIC_NAMES = (
    "OST_Grids",
    "OST_Levels",
    "OST_TextNotes",
    "OST_Dimensions",
    "OST_GenericAnnotation",
    "OST_SectionLine",
    "OST_ElevationMarks",
    "OST_CalloutHeads",
    "OST_DetailComponents",
)


def choose_step(element_count):
    """Choose an RGB lattice step with capacity for ``element_count`` IDs.

    Capacity excludes black and white by reserving the all-zero color for
    background.  The default Stage-A global threshold is conservative enough to
    use a global 8-step palette (32^3 - 1 = 32,767 usable colors) for current
    model sizes, while permitting denser per-view palettes when needed.
    """
    n = max(1, int(element_count or 1))
    for step in (8, 6, 5, 4, 3, 2, 1):
        levels = (255 // step) + 1
        if (levels ** 3) - 1 >= n:
            return step
    return 1


def build_palette(element_count, step=None):
    """Build deterministic non-background RGB colors on the chosen lattice."""
    step = int(step if step is not None else choose_step(element_count))
    colors = []
    for r in range(0, 256, step):
        for g in range(0, 256, step):
            for b in range(0, 256, step):
                if r == 0 and g == 0 and b == 0:
                    continue
                colors.append((min(r, 255), min(g, 255), min(b, 255)))
                if len(colors) >= int(element_count or 0):
                    return colors
    return colors


def resolve_all(doc, top_elements):
    resolved_ids = []
    visited = set()

    def resolve(elem):
        if elem is None:
            return
        eid = elem.Id
        if eid.IntegerValue in visited:
            return
        visited.add(eid.IntegerValue)

        from Autodesk.Revit.DB import Group, FamilyInstance
        if isinstance(elem, Group):
            for mid in elem.GetMemberIds():
                resolve(doc.GetElement(mid))
            resolved_ids.append(eid)
            return

        if isinstance(elem, FamilyInstance):
            try:
                sub_ids = elem.GetSubComponentIds()
            except Exception:
                sub_ids = []
            if sub_ids and len(sub_ids) > 0:
                for sid in sub_ids:
                    resolve(doc.GetElement(sid))

        resolved_ids.append(eid)

    for e in top_elements:
        resolve(e)
    return resolved_ids


def _state_file_path():
    return os.path.join(os.environ.get("TEMP", "/tmp"), STATE_FILE_NAME)


def get_or_create_neutral_phase_filter(doc):
    from Autodesk.Revit.DB import (
        FilteredElementCollector, PhaseFilter, ElementOnPhaseStatus,
        PhaseStatusPresentation,
    )
    for pf in FilteredElementCollector(doc).OfClass(PhaseFilter):
        if pf.Name == NEUTRAL_PHASE_FILTER_NAME:
            return pf, False
    pf = PhaseFilter.Create(doc, NEUTRAL_PHASE_FILTER_NAME)
    for status in [ElementOnPhaseStatus.New, ElementOnPhaseStatus.Existing,
                   ElementOnPhaseStatus.Demolished, ElementOnPhaseStatus.Temporary]:
        pf.SetPhaseStatusPresentation(status, PhaseStatusPresentation.ShowByCategory)
    return pf, True


def _get_solid_pattern_id(doc):
    from Autodesk.Revit.DB import FilteredElementCollector, FillPatternElement, FillPatternTarget
    for fp in FilteredElementCollector(doc).OfClass(FillPatternElement):
        pat = fp.GetFillPattern()
        if pat.IsSolidFill and pat.Target == FillPatternTarget.Drafting:
            return fp.Id
    return None


def _hidden_category_state(doc, view):
    from Autodesk.Revit.DB import BuiltInCategory
    state = {}
    for bic_name in ANNOTATION_HIDE_BIC_NAMES:
        bic = getattr(BuiltInCategory, bic_name, None)
        if bic is None:
            continue
        try:
            cat = doc.Settings.Categories.get_Item(bic)
            if cat is not None and view.CanCategoryBeHidden(cat.Id):
                state[cat.Id.IntegerValue] = {
                    "bic_name": bic_name,
                    "was_hidden": bool(view.GetCategoryHidden(cat.Id)),
                }
        except Exception:
            continue
    return state


def _export_tiff(doc, view, output_path, pixel_size):
    from Autodesk.Revit.DB import (
        ImageExportOptions, ExportRange, ZoomFitType, FitDirectionType, ElementId,
    )
    import System.Collections.Generic as SCG
    out_dir = os.path.dirname(output_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir)
    before = set(os.listdir(out_dir)) if os.path.isdir(out_dir) else set()
    ids = SCG.List[ElementId]()
    ids.Add(view.Id)
    opts = ImageExportOptions()
    opts.ExportRange = ExportRange.SetOfViews
    opts.SetViewsAndSheets(ids)
    opts.ZoomType = ZoomFitType.FitToPage
    opts.FitDirection = FitDirectionType.Horizontal
    opts.PixelSize = int(pixel_size)
    opts.FilePath = os.path.join(out_dir, "_vop_color_id_tmp")
    try:
        from Autodesk.Revit.DB import ImageFileType
        tiff_type = getattr(ImageFileType, "TIFF", getattr(ImageFileType, "TIF", None))
        if tiff_type is None:
            raise RuntimeError("Revit ImageFileType does not expose TIFF/TIF")
        opts.HLRandWFViewsFileType = tiff_type
        opts.ShadowViewsFileType = tiff_type
    except Exception:
        pass
    doc.ExportImage(opts)
    after = set(os.listdir(out_dir))
    candidates = [f for f in (after - before) if f.lower().endswith((".tif", ".tiff"))]
    if not candidates:
        candidates = [f for f in (after - before) if f.lower().endswith((".png", ".jpg", ".jpeg", ".bmp"))]
    if not candidates:
        raise RuntimeError("ExportImage produced no raster in '{}'".format(out_dir))
    created = os.path.join(out_dir, candidates[0])
    if os.path.exists(output_path):
        os.remove(output_path)
    os.rename(created, output_path)
    return output_path


def export_color_id_buffer_view(doc, view, elements, cfg, diag=None):
    """Export one view as a streamed Stage-A color ID buffer and sidecar."""
    from Autodesk.Revit.DB import Transaction, OverrideGraphicSettings, Color, ElementId, BuiltInParameter

    t0 = time.time()
    base_output_dir = getattr(cfg, "output_dir", None) or getattr(
        cfg, "debug_dump_path", "C:\\temp\\vop_output"
    )
    out_dir = os.path.join(base_output_dir, "color_id_buffer")
    view_id = view.Id.IntegerValue
    safe_name = "".join(
        c if c.isalnum() or c in (" ", "-", "_") else "_"
        for c in getattr(view, "Name", "view")
    )
    tiff_path = os.path.join(out_dir, "{0}_{1}.tiff".format(safe_name, view_id))
    json_path = os.path.join(out_dir, "{0}_{1}.json".format(safe_name, view_id))

    resolved_ids = resolve_all(doc, elements)
    count = len(resolved_ids)
    global_threshold = int(getattr(cfg, "color_id_buffer_global_assignment_threshold", 32767))
    step = choose_step(global_threshold if count <= global_threshold else count)
    # TODO(Stage B+): add bbox pre-filter / multi-pass color batching if one view exceeds palette capacity.
    palette = build_palette(count, step=step)
    color_map = {resolved_ids[i].IntegerValue: palette[i] for i in range(count)}

    scale = float(getattr(view, "Scale", 1) or 1)
    threshold_mm = float(getattr(cfg, "color_id_buffer_threshold_source_mm", 0.7))
    min_px = int(getattr(cfg, "color_id_buffer_min_pixels_across_threshold", 2))
    pixel_size = max(64, int(round((25.4 / max(threshold_mm / scale, 1.0e-6)) * min_px)))

    filter_state = {}
    for fid in view.GetFilters():
        filter_state[fid.IntegerValue] = {
            "was_enabled": view.GetIsFilterEnabled(fid),
            "was_visible": view.GetFilterVisibility(fid),
        }
    pf_param = view.get_Parameter(BuiltInParameter.VIEW_PHASE_FILTER)
    orig_phase_filter_id = pf_param.AsElementId().IntegerValue if pf_param is not None else None
    phase_filter_state = {"orig_phase_filter_id": orig_phase_filter_id, "neutral_phase_filter_id": None}
    category_halftone_state = {}
    element_override_state = {}
    category_hidden_state = _hidden_category_state(doc, view)
    solid_pattern_id = _get_solid_pattern_id(doc)
    if solid_pattern_id is None:
        raise RuntimeError("No solid drafting fill pattern found in project")

    state_out = None
    suppress_tx = Transaction(doc, "VOP Stage A SUPPRESS color ID buffer")
    suppress_tx.Start()
    try:
        for fid_int, fstate in filter_state.items():
            if fstate["was_enabled"] and fstate["was_visible"]:
                view.SetIsFilterEnabled(ElementId(int(fid_int)), False)
        neutral_pf, _created = get_or_create_neutral_phase_filter(doc)
        phase_filter_state["neutral_phase_filter_id"] = neutral_pf.Id.IntegerValue
        pf_param.Set(neutral_pf.Id)
        for cat_id_int, hstate in category_hidden_state.items():
            view.SetCategoryHidden(ElementId(int(cat_id_int)), True)
        categories_touched = set()
        for eid in resolved_ids:
            elem = doc.GetElement(eid)
            cat = elem.Category if elem is not None else None
            if cat is not None:
                categories_touched.add(cat.Id.IntegerValue)
        for cat_id_int in categories_touched:
            try:
                cat_id = ElementId(int(cat_id_int))
                cat_ogs = view.GetCategoryOverrides(cat_id)
                category_halftone_state[cat_id_int] = cat_ogs.Halftone
                cat_ogs.SetHalftone(False)
                view.SetCategoryOverrides(cat_id, cat_ogs)
            except Exception as ex:
                if diag is not None:
                    diag.warn(
                        phase="color_id_buffer",
                        callsite="category_halftone",
                        message=str(ex),
                        view_id=view_id,
                    )
        for eid in resolved_ids:
            element_override_state[eid.IntegerValue] = view.GetElementOverrides(eid)
            rgb = color_map[eid.IntegerValue]
            color = Color(int(rgb[0]), int(rgb[1]), int(rgb[2]))
            ogs = OverrideGraphicSettings()
            ogs.SetSurfaceForegroundPatternId(solid_pattern_id)
            ogs.SetSurfaceForegroundPatternColor(color)
            ogs.SetSurfaceForegroundPatternVisible(True)
            ogs.SetCutForegroundPatternId(solid_pattern_id)
            ogs.SetCutForegroundPatternColor(color)
            ogs.SetCutForegroundPatternVisible(True)
            ogs.SetProjectionLineColor(color)
            ogs.SetCutLineColor(color)
            ogs.SetSurfaceTransparency(0)
            ogs.SetHalftone(False)
            view.SetElementOverrides(eid, ogs)
        suppress_tx.Commit()
    except Exception:
        suppress_tx.RollBack()
        raise

    try:
        _export_tiff(doc, view, tiff_path, pixel_size)
    finally:
        restore_tx = Transaction(doc, "VOP Stage A RESTORE color ID buffer")
        restore_tx.Start()
        try:
            for fid_int, fstate in filter_state.items():
                view.SetIsFilterEnabled(ElementId(int(fid_int)), fstate["was_enabled"])
            if orig_phase_filter_id is not None:
                pf_param.Set(ElementId(int(orig_phase_filter_id)))
            for cat_id_int, was_halftone in category_halftone_state.items():
                try:
                    cat_id = ElementId(int(cat_id_int))
                    cat_ogs = view.GetCategoryOverrides(cat_id)
                    cat_ogs.SetHalftone(was_halftone)
                    view.SetCategoryOverrides(cat_id, cat_ogs)
                except Exception as ex:
                    if diag is not None:
                        diag.warn(
                            phase="color_id_buffer",
                            callsite="restore_category_halftone",
                            message=str(ex),
                            view_id=view_id,
                        )
            for cat_id_int, hstate in category_hidden_state.items():
                view.SetCategoryHidden(ElementId(int(cat_id_int)), bool(hstate["was_hidden"]))
            for eid in resolved_ids:
                eid_int = int(eid.IntegerValue)
                prior_ogs = element_override_state.get(eid_int)
                if prior_ogs is None:
                    prior_ogs = OverrideGraphicSettings()
                view.SetElementOverrides(ElementId(eid_int), prior_ogs)
            restore_tx.Commit()
        except Exception:
            restore_tx.RollBack()
            raise

    state_out = {
        "view_id": view_id,
        "resolution": {"pixel_size": pixel_size, "min_pixels_across_threshold": min_px, "view_scale": scale},
        "color_assignment_map": {str(k): list(v) for k, v in color_map.items()},
        "threshold_source_mm": threshold_mm,
        "categories_hidden": category_hidden_state,
        "filter_state": filter_state,
        "phase_filter_state": phase_filter_state,
        "category_halftone_state": category_halftone_state,
        "palette_step": step,
        "tiff_path": tiff_path,
    }
    if not os.path.exists(out_dir):
        os.makedirs(out_dir)
    with open(json_path, "w") as f:
        json.dump(state_out, f, indent=2, sort_keys=True)
    try:
        with open(_state_file_path(), "w") as f:
            json.dump(state_out, f)
    except Exception:
        pass

    return {
        "view_id": view_id,
        "view_name": getattr(view, "Name", None),
        "success": True,
        "stage": "color_id_buffer_stage_a",
        "tiff_path": tiff_path,
        "sidecar_path": json_path,
        "resolution": state_out["resolution"],
        "color_assignment_count": count,
        "timings": {"color_id_buffer_ms": round((time.time() - t0) * 1000.0, 3)},
        "metadata": state_out,
    }
