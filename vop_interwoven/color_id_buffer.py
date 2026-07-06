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
MAX_STAGE_A_PIXEL_SIZE = 15000

# Mirrors the annotation category set collected by revit.annotation.collect_2d_annotations
# plus view-only categories (grids/levels/section/elevation/callout marks) that are never
# occlusion truth. Kept as an explicit list (rather than importing collect_2d_annotations'
# locals) since that function does not expose its category set as a module constant.
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
    "OST_RoomTags",
    "OST_SpaceTags",
    "OST_AreaTags",
    "OST_DoorTags",
    "OST_WindowTags",
    "OST_WallTags",
    "OST_MEPSpaceTags",
    "OST_KeynoteTags",
    "OST_FilledRegion",
    "OST_Lines",
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


def _split_expanded_elements(expanded_entries):
    """Partition expand_host_link_import_model_elements() output.

    Returns (host_elements, link_entries):
    - host_elements: real Elements living in `doc` (HOST + DWG ImportInstance),
      safe to resolve/override the normal way.
    - link_entries: list of (link_instance_id, link_element_id, proxy) tuples for
      elements that live inside a linked RVT document and require the
      LinkElementId override path.
    """
    host_elements = []
    link_entries = []
    seen_host = set()
    for entry in expanded_entries:
        source_type = entry.get("source_type", "HOST")
        elem = entry.get("element")
        if elem is None:
            continue
        if source_type == "LINK":
            link_inst_id = getattr(elem, "LinkInstanceId", None)
            link_elem_id = getattr(elem, "Id", None)
            if link_inst_id is not None and link_elem_id is not None:
                link_entries.append((link_inst_id, link_elem_id, elem))
            continue
        eid = getattr(elem, "Id", None)
        if eid is None:
            continue
        if eid.IntegerValue in seen_host:
            continue
        seen_host.add(eid.IntegerValue)
        host_elements.append(elem)
    return host_elements, link_entries


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


def _build_flat_color_ogs(solid_pattern_id, color):
    from Autodesk.Revit.DB import OverrideGraphicSettings
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
    return ogs


def _try_color_link_element(view, link_inst_id, link_elem_id, ogs):
    """Attempt a Revit 2022+ LinkElementId-based override for a linked element.

    Returns (success, prior_overrides_or_None). LinkElementId/overload support
    varies by Revit version, so failure is expected on older hosts and must not
    be treated as fatal — the caller falls back to hiding the owning link
    instance so uncolored linked geometry never contaminates the ID buffer.
    """
    try:
        from Autodesk.Revit.DB import LinkElementId
        lek = LinkElementId(link_inst_id, link_elem_id)
        prior = view.GetElementOverrides(lek)
        view.SetElementOverrides(lek, ogs)
        return True, prior
    except Exception:
        return False, None


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


def _set_pixel_size_with_backoff(opts, pixel_size, diag=None, view_id=None):
    """Set ImageExportOptions.PixelSize, backing off if Revit rejects the value.

    Revit enforces an internal PixelSize ceiling that isn't documented as a
    fixed constant and can vary by version/install, so rather than guess a
    "safe" cap, halve the request on ArgumentException until Revit accepts
    it. Logs a warning when it has to back off so degraded resolution is
    visible instead of silent.
    """
    candidate = max(1, int(pixel_size))
    requested = candidate
    floor = 16
    while True:
        try:
            opts.PixelSize = candidate
            if candidate != requested and diag is not None:
                diag.warn(
                    phase="color_id_buffer",
                    callsite="pixel_size_backoff",
                    message="Requested PixelSize {0} exceeded Revit's accepted range; "
                            "used {1} instead (resolution degraded for this "
                            "view)".format(requested, candidate),
                    view_id=view_id,
                )
            return candidate
        except Exception as ex:
            if candidate <= floor:
                raise RuntimeError(
                    "Revit rejected PixelSize down to the floor of {0} "
                    "(last error: {1})".format(floor, ex)
                )
            candidate = max(floor, candidate // 2)


def _export_tiff(doc, view, output_path, pixel_size, diag=None, view_id=None):
    from Autodesk.Revit.DB import (
        ImageExportOptions, ExportRange, ZoomFitType, FitDirectionType, ElementId,
        ImageFileType,
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
    actual_pixel_size = _set_pixel_size_with_backoff(opts, pixel_size, diag=diag, view_id=view_id)
    opts.FilePath = os.path.join(out_dir, "_vop_color_id_tmp")

    tiff_type = getattr(ImageFileType, "TIFF", getattr(ImageFileType, "TIF", None))
    if tiff_type is None:
        raise RuntimeError("Revit ImageFileType does not expose TIFF/TIF; Stage A requires lossless TIFF export")
    opts.HLRandWFViewsFileType = tiff_type
    opts.ShadowViewsFileType = tiff_type

    doc.ExportImage(opts)
    after = set(os.listdir(out_dir))
    candidates = [f for f in (after - before) if f.lower().endswith((".tif", ".tiff"))]
    if not candidates:
        raise RuntimeError(
            "ExportImage did not produce a TIFF in '{0}'; Stage A refuses to mislabel "
            "a non-TIFF raster as .tiff".format(out_dir)
        )
    created = os.path.join(out_dir, candidates[0])
    if os.path.exists(output_path):
        os.remove(output_path)
    os.rename(created, output_path)
    return output_path, actual_pixel_size


def export_color_id_buffer_view(doc, view, elements, cfg, diag=None, raster=None, elem_cache=None):
    """Export one view as a streamed Stage-A color ID buffer and sidecar.

    Args:
        elements: Host-visible elements from collect_view_elements() (pre-phase-swap).
        raster: Optional ViewRaster for the view; supplies grid width for pixel-size
            scaling and enables re-collecting elements under the neutral phase filter.
        elem_cache: Optional ElementCache passed through to link/import expansion.
    """
    from Autodesk.Revit.DB import Transaction, Color, ElementId, BuiltInParameter

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

    scale = float(getattr(view, "Scale", 1) or 1)
    export_dpi = float(getattr(cfg, "color_id_buffer_export_dpi", 150))
    if raster is not None and getattr(raster, "W", 0) and getattr(raster, "cell_size_ft", 0):
        paper_width_in = (float(raster.W) * float(raster.cell_size_ft) * 12.0) / max(scale, 1.0e-6)
    else:
        paper_width_in = 1.0
        if diag is not None:
            diag.warn(
                phase="color_id_buffer",
                callsite="pixel_size",
                message="raster not provided; falling back to 1 paper-inch width for pixel "
                        "sizing (export resolution will be far below the requested DPI)",
                view_id=view_id,
            )
    pixel_size = int(round(export_dpi * paper_width_in))
    pixel_size = max(64, min(pixel_size, MAX_STAGE_A_PIXEL_SIZE))
    if pixel_size >= MAX_STAGE_A_PIXEL_SIZE and diag is not None:
        diag.warn(
            phase="color_id_buffer",
            callsite="pixel_size",
            message="Requested Stage A pixel size clamped to {0}; export DPI may "
                    "not be met for this view".format(MAX_STAGE_A_PIXEL_SIZE),
            view_id=view_id,
        )

    filter_state = {}
    for fid in view.GetFilters():
        filter_state[fid.IntegerValue] = {
            "was_enabled": view.GetIsFilterEnabled(fid),
            "was_visible": view.GetFilterVisibility(fid),
        }
    pf_param = view.get_Parameter(BuiltInParameter.VIEW_PHASE_FILTER)
    orig_phase_filter_id = pf_param.AsElementId().IntegerValue if pf_param is not None else None
    phase_filter_state = {
        "orig_phase_filter_id": orig_phase_filter_id,
        "neutral_phase_filter_id": None,
        "neutral_phase_filter_created": False,
    }
    category_halftone_state = {}
    element_override_state = {}
    link_override_state = {}
    hidden_link_instance_ids = []
    unresolved_link_instance_ids = set()
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
        neutral_pf, pf_created = get_or_create_neutral_phase_filter(doc)
        phase_filter_state["neutral_phase_filter_id"] = neutral_pf.Id.IntegerValue
        phase_filter_state["neutral_phase_filter_created"] = bool(pf_created)
        pf_param.Set(neutral_pf.Id)
        for cat_id_int, hstate in category_hidden_state.items():
            view.SetCategoryHidden(ElementId(int(cat_id_int)), True)

        # Re-collect under the neutral phase state: elements the view's original
        # phase filter hid (e.g. demolished/temporary) but the neutral filter shows
        # would otherwise be rendered by ExportImage without a color assignment.
        recollected = elements
        if raster is not None:
            try:
                from .revit.collection import collect_view_elements as _collect_view_elements
                recollected = _collect_view_elements(doc, view, raster, diag=diag, cfg=cfg)
            except Exception as ex:
                if diag is not None:
                    diag.warn(
                        phase="color_id_buffer",
                        callsite="recollect_under_neutral_phase",
                        message=str(ex),
                        view_id=view_id,
                    )
                recollected = elements
        elif diag is not None:
            diag.warn(
                phase="color_id_buffer",
                callsite="recollect_under_neutral_phase",
                message="raster not provided; using pre-phase-swap element collection "
                        "(may miss phase-revealed elements)",
                view_id=view_id,
            )

        try:
            from .revit.collection import expand_host_link_import_model_elements as _expand_elements
            expanded = _expand_elements(doc, view, recollected, cfg, diag=diag, elem_cache=elem_cache)
        except Exception as ex:
            if diag is not None:
                diag.warn(
                    phase="color_id_buffer",
                    callsite="expand_linked_elements",
                    message=str(ex),
                    view_id=view_id,
                )
            expanded = [{"element": e, "source_type": "HOST"} for e in recollected]

        host_elements, link_entries = _split_expanded_elements(expanded)
        resolved_ids = resolve_all(doc, host_elements)
        count_host = len(resolved_ids)
        count_link = len(link_entries)
        total_count = count_host + count_link

        global_threshold = int(getattr(cfg, "color_id_buffer_global_assignment_threshold", 32767))
        step = choose_step(global_threshold if total_count <= global_threshold else total_count)
        # TODO(Stage B+): add bbox pre-filter / multi-pass color batching if one view exceeds palette capacity.
        palette = build_palette(total_count, step=step)
        color_map = {resolved_ids[i].IntegerValue: palette[i] for i in range(count_host)}
        link_color_map = {
            (link_inst_id.IntegerValue, link_elem_id.IntegerValue): palette[count_host + j]
            for j, (link_inst_id, link_elem_id, _proxy) in enumerate(link_entries)
        }

        categories_touched = set()
        for eid in resolved_ids:
            elem = doc.GetElement(eid)
            cat = elem.Category if elem is not None else None
            if cat is not None:
                categories_touched.add(cat.Id.IntegerValue)
        for (_li, _le, proxy) in link_entries:
            cat = getattr(proxy, "Category", None)
            if cat is not None and getattr(cat, "Id", None) is not None:
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
            ogs = _build_flat_color_ogs(solid_pattern_id, color)
            view.SetElementOverrides(eid, ogs)

        # Linked RVT elements: attempt a per-element LinkElementId override (Revit
        # 2022+). Elements whose link instance can't be colored this way are hidden
        # for the export instead of left uncolored, so they never contaminate the
        # ID buffer with unassigned pixels.
        for (link_inst_id, link_elem_id, _proxy) in link_entries:
            rgb = link_color_map[(link_inst_id.IntegerValue, link_elem_id.IntegerValue)]
            color = Color(int(rgb[0]), int(rgb[1]), int(rgb[2]))
            ogs = _build_flat_color_ogs(solid_pattern_id, color)
            ok, prior = _try_color_link_element(view, link_inst_id, link_elem_id, ogs)
            if ok:
                link_override_state[(link_inst_id.IntegerValue, link_elem_id.IntegerValue)] = prior
            else:
                unresolved_link_instance_ids.add(link_inst_id.IntegerValue)

        if unresolved_link_instance_ids:
            import System.Collections.Generic as SCG
            ids_to_hide = SCG.List[ElementId]()
            for iid in sorted(unresolved_link_instance_ids):
                link_eid = ElementId(int(iid))
                link_elem = doc.GetElement(link_eid)
                if link_elem is None:
                    continue
                try:
                    already_hidden = bool(link_elem.IsHidden(view))
                except Exception:
                    already_hidden = False
                if not already_hidden:
                    ids_to_hide.Add(link_eid)
            if ids_to_hide.Count > 0:
                view.HideElements(ids_to_hide)
                for i in range(ids_to_hide.Count):
                    hidden_link_instance_ids.append(ids_to_hide[i].IntegerValue)
            if diag is not None:
                diag.warn(
                    phase="color_id_buffer",
                    callsite="unresolved_link_elements",
                    message="LinkElementId overrides unavailable; hid link instance(s) "
                            "instead of exporting uncolored linked geometry",
                    view_id=view_id,
                    extra={"link_instance_ids": sorted(unresolved_link_instance_ids)},
                )

        suppress_tx.Commit()
    except Exception:
        suppress_tx.RollBack()
        raise

    actual_pixel_size = pixel_size
    try:
        _tiff_path, actual_pixel_size = _export_tiff(
            doc, view, tiff_path, pixel_size, diag=diag, view_id=view_id
        )
    finally:
        restore_tx = Transaction(doc, "VOP Stage A RESTORE color ID buffer")
        restore_tx.Start()

        # Best-effort restore: every step below is independently guarded. Revit
        # transactions are all-or-nothing on RollBack, so a single failing step
        # (a stale ElementId, an UnhideElements refusal, ...) must never be able
        # to roll back every other restore step that already succeeded — that
        # would leave the document sitting in the suppressed/colored Stage A
        # state permanently instead of just missing the one failed piece.
        def _restore_step(callsite, fn):
            try:
                fn()
            except Exception as ex:
                if diag is not None:
                    diag.error(
                        phase="color_id_buffer",
                        callsite=callsite,
                        message=str(ex),
                        view_id=view_id,
                        exc=ex,
                    )

        def _restore_filters():
            for fid_int, fstate in filter_state.items():
                view.SetIsFilterEnabled(ElementId(int(fid_int)), fstate["was_enabled"])
        _restore_step("restore_filters", _restore_filters)

        def _restore_phase_filter():
            if orig_phase_filter_id is not None:
                pf_param.Set(ElementId(int(orig_phase_filter_id)))
        _restore_step("restore_phase_filter", _restore_phase_filter)

        if phase_filter_state.get("neutral_phase_filter_created") and phase_filter_state.get("neutral_phase_filter_id") is not None:
            _restore_step(
                "restore_neutral_phase_filter",
                lambda: doc.Delete(ElementId(int(phase_filter_state["neutral_phase_filter_id"]))),
            )

        for cat_id_int, was_halftone in category_halftone_state.items():
            def _restore_halftone(cat_id_int=cat_id_int, was_halftone=was_halftone):
                cat_id = ElementId(int(cat_id_int))
                cat_ogs = view.GetCategoryOverrides(cat_id)
                cat_ogs.SetHalftone(was_halftone)
                view.SetCategoryOverrides(cat_id, cat_ogs)
            _restore_step("restore_category_halftone", _restore_halftone)

        for cat_id_int, hstate in category_hidden_state.items():
            def _restore_cat_hidden(cat_id_int=cat_id_int, hstate=hstate):
                view.SetCategoryHidden(ElementId(int(cat_id_int)), bool(hstate["was_hidden"]))
            _restore_step("restore_category_hidden", _restore_cat_hidden)

        for eid in resolved_ids:
            def _restore_element_override(eid=eid):
                eid_int = int(eid.IntegerValue)
                prior_ogs = element_override_state.get(eid_int)
                if prior_ogs is None:
                    from Autodesk.Revit.DB import OverrideGraphicSettings
                    prior_ogs = OverrideGraphicSettings()
                view.SetElementOverrides(ElementId(eid_int), prior_ogs)
            _restore_step("restore_element_overrides", _restore_element_override)

        if link_override_state:
            from Autodesk.Revit.DB import LinkElementId, OverrideGraphicSettings
            for (link_inst_int, link_elem_int), prior_ogs in link_override_state.items():
                def _restore_link_override(link_inst_int=link_inst_int, link_elem_int=link_elem_int, prior_ogs=prior_ogs):
                    lek = LinkElementId(ElementId(int(link_inst_int)), ElementId(int(link_elem_int)))
                    view.SetElementOverrides(lek, prior_ogs if prior_ogs is not None else OverrideGraphicSettings())
                _restore_step("restore_link_element_overrides", _restore_link_override)

        if hidden_link_instance_ids:
            def _restore_unhide():
                import System.Collections.Generic as SCG
                unhide_list = SCG.List[ElementId]()
                for iid in hidden_link_instance_ids:
                    unhide_list.Add(ElementId(int(iid)))
                view.UnhideElements(unhide_list)
            _restore_step("restore_unhide_link_instances", _restore_unhide)

        try:
            restore_tx.Commit()
        except Exception:
            restore_tx.RollBack()
            raise

    state_out = {
        "view_id": view_id,
        "resolution": {
            "pixel_size": actual_pixel_size,
            "requested_pixel_size": pixel_size,
            "export_dpi": export_dpi,
            "view_scale": scale,
        },
        "color_assignment_map": {str(k): list(v) for k, v in color_map.items()},
        "link_color_assignment_map": {
            "{0}:{1}".format(li, le): list(rgb) for (li, le), rgb in link_color_map.items()
        },
        "unresolved_link_instance_hidden_ids": list(hidden_link_instance_ids),
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

    return {
        "view_id": view_id,
        "view_name": getattr(view, "Name", None),
        "success": True,
        "stage": "color_id_buffer_stage_a",
        "tiff_path": tiff_path,
        "sidecar_path": json_path,
        "output_dir": out_dir,
        "resolution": state_out["resolution"],
        "color_assignment_count": count_host + count_link,
        "timings": {"color_id_buffer_ms": round((time.time() - t0) * 1000.0, 3)},
        "metadata": state_out,
    }
