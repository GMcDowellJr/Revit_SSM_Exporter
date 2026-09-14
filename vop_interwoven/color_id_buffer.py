"""Stage A color ID-buffer extraction for VOP Interwoven.

This module replaces the in-process occlusion/silhouette model extraction path
when ``Config.enable_color_id_buffer_stage_a`` is enabled.  It assigns model
elements unique flat colors, suppresses graphic effects that can distort those
colors, exports a TIFF immediately, writes a JSON sidecar, and restores view
state before returning to the next view.
"""

import copy
import json
import os
import time

NEUTRAL_PHASE_FILTER_NAME = "VOP_NeutralPhaseFilter"
MAX_STAGE_A_PIXEL_SIZE = 15000
# Near-white and near-black corners of the RGB cube are reserved as invalid so
# a decoder can draw a clean boundary against two distinct noise sources:
# (a) AA/export halos at the page background, which is white outside the
# view's geometry (validity rule: any channel >= NEAR_WHITE_RESERVED_THRESHOLD
# on every channel is invalid); (b) black (0,0,0) is the "no element painted"
# sentinel, and a palette walking the lattice from the origin outward would
# otherwise hand the very first elements colors like (0,0,8) that sit only a
# few units from that sentinel -- indistinguishable from background after a
# couple of RGB units of TIFF-export/rendering noise. Field data
# (graphics_semantics_probe, 2026-07-24) showed near-black pixels were 99% of
# the residual off-palette contamination in the fully-suppressed production
# config, concentrated exactly here.
NEAR_BLACK_RESERVED_THRESHOLD = 32
NEAR_WHITE_RESERVED_THRESHOLD = 224

# Categories that are CategoryType.Model in the Revit API but are still
# view-specific 2D drafting content with no real 3D presence -- Revit buckets
# them as "Model" for historical reasons, not because they're actual model
# geometry. Stage A's whole point is letting Revit's renderer resolve
# occlusion for real 3D model geometry only; this content is collected by the
# existing 2D annotation pipeline (revit/annotation.py) and combined with the
# color buffer's decoded edges in a later post-process step instead.
VIEW_ONLY_MODEL_BIC_NAMES = (
    "OST_DetailComponents",  # Detail Items: 2D symbols placed per-view, no 3D form
    "OST_Lines",             # Model Lines & Detail Lines share this category; neither has fill/area
)


def _reserved_corner_count(step):
    """Count lattice points excluded by the near-black and near-white corner reservations.

    Shared by choose_step() and build_palette() so capacity estimation can
    never drift out of sync with what build_palette() actually excludes --
    that mismatch would let choose_step() pick a step build_palette() can't
    fill, silently truncating the palette below element_count.
    """
    values = range(0, 256, step)
    near_black = sum(1 for v in values if v < NEAR_BLACK_RESERVED_THRESHOLD)
    near_white = sum(1 for v in values if v >= NEAR_WHITE_RESERVED_THRESHOLD)
    return (near_black ** 3) + (near_white ** 3)


def choose_step(element_count):
    """Choose an RGB lattice step with capacity for ``element_count`` IDs.

    Capacity excludes the near-black and near-white corners reserved by
    build_palette(). The default Stage-A global threshold is conservative
    enough to use a global 8-step palette for current model sizes, while
    permitting denser per-view palettes when needed.
    """
    n = max(1, int(element_count or 1))
    for step in (8, 6, 5, 4, 3, 2, 1):
        levels = (255 // step) + 1
        capacity = (levels ** 3) - _reserved_corner_count(step)
        if capacity >= n:
            return step
    return 1


def build_palette(element_count, step=None):
    """Build deterministic non-near-black, non-near-white RGB colors on the chosen lattice."""
    step = int(step if step is not None else choose_step(element_count))
    colors = []
    for r in range(0, 256, step):
        for g in range(0, 256, step):
            for b in range(0, 256, step):
                if r < NEAR_BLACK_RESERVED_THRESHOLD and g < NEAR_BLACK_RESERVED_THRESHOLD and b < NEAR_BLACK_RESERVED_THRESHOLD:
                    continue
                if (
                    r >= NEAR_WHITE_RESERVED_THRESHOLD
                    and g >= NEAR_WHITE_RESERVED_THRESHOLD
                    and b >= NEAR_WHITE_RESERVED_THRESHOLD
                ):
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


def _dedupe_link_instances_by_document(link_instances):
    """Group placed RevitLinkInstance objects by underlying document identity
    (PathName), so a link type placed many times in a view (e.g. a "typical
    exam room" link placed dozens of times) is only scanned once — ported
    from the tested standalone filter-creation script, where scanning each
    placement separately was the dominant cost (15.9s of 34s total for 152
    instances resolving to a much smaller set of unique documents).

    Returns ``(unique_docs, unresolved_names)``:
      unique_docs: path_name -> {"instance": link_inst, "linked_doc": Document}
      unresolved_names: link instance Names whose document could not be
        resolved (unloaded/missing link) — reported, never silently dropped.
    """
    unique_docs = {}
    unresolved_names = []
    for link_inst in link_instances:
        linked_doc = link_inst.GetLinkDocument()
        if linked_doc is None:
            unresolved_names.append(link_inst.Name)
            continue
        path_name = linked_doc.PathName or link_inst.Name
        if path_name not in unique_docs:
            unique_docs[path_name] = {"instance": link_inst, "linked_doc": linked_doc}
    return unique_docs, unresolved_names


def _model_categories_in_linked_doc(linked_doc, filterable_ids):
    """Distinct categories among elements in ONE linked document that the
    project's authoritative LINK category policy (revit/collection_policy.
    py's should_include_element(), "single source of truth" per CLAUDE.md)
    would include -- split by whether Revit's ParameterFilterElement API can
    actually filter on that category
    (ParameterFilterUtilities.GetAllFilterableCategories()). Both checks
    must run for every category now (unlike an earlier revision that
    treated filterable_ids as a cheap first gate and skipped the policy
    call otherwise): a policy-included-but-unfilterable category still
    needs to be identified and returned, not silently dropped, or its LINK
    elements end up neither colored nor suppressed.

    Returns ``(colorable, uncolorable)``: ``colorable`` categories are both
    policy-included AND filterable -- callable code can color their LINK
    elements via a category filter. ``uncolorable`` categories are
    policy-included but NOT filterable -- Stage A has no way to color their
    LINK elements at all, and the caller must suppress them (hide the
    category) rather than let them render with an uncontrolled native
    color a decoder could alias onto some unrelated HOST element's palette
    ID (see _apply_link_category_filters's docstring and the retired
    per-element path's "hide what can't be colored" precedent this
    restores). Policy-EXCLUDED categories (Rooms, Areas, Lines, ...) are in
    neither list -- the rest of the pipeline already never collects their
    LINK elements at all (linked_documents.py's own should_include_element
    call), so their native rendering is an existing, out-of-scope
    characteristic of the whole system, not something this function
    introduces or needs to suppress.

    Deliberately unscoped by host-view visibility: a category can be hidden
    in the host view while still visible via the link's own Custom
    category-visibility settings, and no API exposes that per-link state
    directly, so narrowing here would silently drop categories that are
    genuinely visible. Overinclusion from that alone only costs a spare
    filter/palette slot, never wrong output -- but skipping the policy call
    would not be mere overinclusion, it would recolor categories (Rooms,
    Areas, Lines, ...) the rest of the pipeline treats as non-physical/
    non-model, and since a category filter paints HOST elements of that
    category too, it would contaminate HOST content as well.
    """
    from Autodesk.Revit.DB import FilteredElementCollector
    from .revit.collection_policy import should_include_element
    seen_cat_ids = set()
    colorable = []
    uncolorable = []
    for elem in FilteredElementCollector(linked_doc).WhereElementIsNotElementType():
        cat = elem.Category
        if cat is None:
            continue
        cid_int = cat.Id.IntegerValue
        if cid_int in seen_cat_ids:
            continue
        seen_cat_ids.add(cid_int)
        include, _reason, _cat_name = should_include_element(
            elem=elem, doc=linked_doc, source_type="LINK"
        )
        if not include:
            continue
        if cid_int in filterable_ids:
            colorable.append(cat)
        else:
            uncolorable.append(cat)
    return colorable, uncolorable


def _find_existing_parameter_filter(doc, name):
    from Autodesk.Revit.DB import FilteredElementCollector, ParameterFilterElement
    for pfe in FilteredElementCollector(doc).OfClass(ParameterFilterElement):
        if pfe.Name == name:
            return pfe
    return None


def _collect_link_category_filters(doc, view, diag=None, view_id=None):
    """Discover the union of policy-included, CategoryType.Model categories
    across every UNIQUE linked document referenced in this view.

    Ported from the tested standalone filter-creation script: deduplicates
    RevitLinkInstance placements by underlying document identity (PathName)
    before scanning, then unions each unique document's model categories.

    Returns ``(colorable, uncolorable)``, each an ordered list of Category
    objects sorted by category Name for a deterministic, reproducible
    palette-slot assignment order. ``colorable`` categories get a
    ParameterFilterElement (see _apply_link_category_filters);
    ``uncolorable`` ones are policy-included but not filterable at all, so
    the caller must suppress (hide) them instead -- see
    _model_categories_in_linked_doc's docstring for why leaving them
    uncontrolled is not an option.
    """
    from Autodesk.Revit.DB import FilteredElementCollector, RevitLinkInstance, ParameterFilterUtilities
    link_instances = list(FilteredElementCollector(doc, view.Id).OfClass(RevitLinkInstance))
    if not link_instances:
        return [], []

    unique_docs, unresolved_names = _dedupe_link_instances_by_document(link_instances)
    if unresolved_names and diag is not None:
        diag.warn(
            phase="color_id_buffer",
            callsite="link_category_filter_discovery",
            message="{0} linked instance(s) could not resolve their document "
                    "(unloaded/missing link); excluded from category-filter "
                    "coloring: {1}".format(len(unresolved_names), unresolved_names),
            view_id=view_id,
        )

    filterable_ids = set(
        cid.IntegerValue for cid in ParameterFilterUtilities.GetAllFilterableCategories()
    )

    combined_colorable = {}
    combined_uncolorable = {}
    for path_name, info in unique_docs.items():
        colorable, uncolorable = _model_categories_in_linked_doc(info["linked_doc"], filterable_ids)
        for cat in colorable:
            cid_int = cat.Id.IntegerValue
            if cid_int not in combined_colorable:
                combined_colorable[cid_int] = cat
        for cat in uncolorable:
            cid_int = cat.Id.IntegerValue
            if cid_int not in combined_uncolorable:
                combined_uncolorable[cid_int] = cat

    return (
        sorted(combined_colorable.values(), key=lambda cat: cat.Name),
        sorted(combined_uncolorable.values(), key=lambda cat: cat.Name),
    )


def _apply_link_category_filters(doc, view, view_id, categories_with_colors, solid_pattern_id, diag=None):
    """Create one ParameterFilterElement per category and color it via
    SetFilterOverrides, ported from the tested standalone filter-creation
    script (random per-run colors there are replaced with this call's
    caller-supplied palette slice — see export_color_id_buffer_view).

    Same "filter colors the category, a later per-element HOST override
    wins" precedence Revit documents natively (Instance/Element > Filter >
    Category): LINK elements have no individual override, so they fall
    through to the filter's flat category color; a HOST element painted
    per-element still wins over any filter.

    Filter names are scoped to this view's id ("VOP_Color_<view_id>_
    <category>"), NOT shared across views/runs the way the ported script's
    find-or-create-by-fixed-name did. That script was a manual Dynamo tool
    a human re-ran on the same view during iterative testing, where leaving
    a stable-named filter behind was the point; this module's whole
    contract is the opposite -- suppress, capture, and restore the view to
    exactly how it was, with nothing left behind (see the module docstring
    and the extensive suppress/restore machinery below). A stable shared
    name risks colliding with a filter the *user* already has applied to
    this view for their own reasons: overwriting its color with no
    prior-state capture would permanently corrupt their view graphics, and
    capturing/restoring a live OverrideGraphicSettings object across the
    suppress_tx/export/restore_tx boundary is exactly the pattern this
    module's history warns against (see the "Deliberately NOT capturing/
    reusing prior OverrideGraphicSettings" comment above painted_link_
    entries in export_color_id_buffer_view -- the curtain-panel restore bug
    was caused by exactly that). View-scoping makes a name collision on
    THIS view mean only one thing: this same view's own Stage A run left a
    filter behind after a crash before reaching restore -- safe to reuse.

    ParameterFilterElement objects are document-global, though, not
    view-scoped: even a Stage-A-named leftover could since have been
    applied by a user (or another process) to some OTHER view or view
    template, which a name match on THIS view can't rule out. So a filter
    found via _find_existing_parameter_filter() is only ever reused, never
    deleted -- deleting a document element that some other view/template
    also references would corrupt those too. Only a filter this call
    freshly creates is guaranteed unreferenced anywhere else (nothing
    could have a reference to it before it exists), and only those are
    safe for the caller to doc.Delete() outright during restore.

    A category whose filter creation/application fails here is NOT left to
    render with its native, uncontrolled color: the decoder matches every
    TIFF pixel against color_assignment_map's deterministic HOST palette,
    and an unrelated native LINK color that happens to coincide with a HOST
    element's assigned RGB would silently corrupt that HOST element's
    decoded silhouette. The retired per-element path avoided exactly this
    by hiding a link instance whose override failed; failed_categories
    below is this function's equivalent -- the caller must hide these
    categories view-wide (see _model_categories_in_linked_doc's docstring
    for the matching "uncolorable" case discovery finds up front).

    Returns ``(link_category_color_map, created_filter_ids, reused_filter_ids,
    failed_categories)``:
      link_category_color_map: {category_name: [r, g, b]}
      created_filter_ids: ElementId ints of filters this call created fresh
        this run -- restore may RemoveFilter AND doc.Delete these.
      reused_filter_ids: ElementId ints of filters this call found already
        existing by name and recolored -- restore may only RemoveFilter
        these from THIS view, never doc.Delete the shared definition.
      failed_categories: Category objects whose filter could not be
        created/applied -- the caller must suppress (hide) these.
    """
    from Autodesk.Revit.DB import ElementId, ParameterFilterElement, Color
    import System.Collections.Generic as SCG

    link_category_color_map = {}
    created_filter_ids = []
    reused_filter_ids = []
    failed_categories = []
    for cat, rgb in categories_with_colors:
        cat_name = cat.Name
        filter_name = "VOP_Color_{0}_{1}".format(view_id, cat_name)
        try:
            pfe = _find_existing_parameter_filter(doc, filter_name)
            if pfe is None:
                cat_id_list = SCG.List[ElementId]()
                cat_id_list.Add(cat.Id)
                pfe = ParameterFilterElement.Create(doc, filter_name, cat_id_list)
                # Recorded immediately on success, before AddFilter/enable/
                # visibility/color below get any chance to raise -- a
                # Create() that succeeds but a later step that fails must
                # still leave this freshly created element tracked for
                # restore's doc.Delete(), or it orphans permanently.
                created_filter_ids.append(pfe.Id.IntegerValue)
            else:
                reused_filter_ids.append(pfe.Id.IntegerValue)

            if not view.IsFilterApplied(pfe.Id):
                view.AddFilter(pfe.Id)
            # Force both enabled AND visible: the suppress step above
            # unconditionally disables every pre-existing filter on this
            # view, and a filter's visibility (separate from its enabled
            # state -- view.GetFilterVisibility/SetFilterVisibility, used
            # the same way by export_color_id_buffer_view's own filter_
            # state capture below) could independently be false on a
            # same-view crash leftover. Either would leave this category's
            # LINK elements invisible or uncolored in the export even
            # though link_category_color_map reports them assigned.
            view.SetIsFilterEnabled(pfe.Id, True)
            view.SetFilterVisibility(pfe.Id, True)

            color = Color(int(rgb[0]), int(rgb[1]), int(rgb[2]))
            ogs = _build_flat_color_ogs(solid_pattern_id, color)
            view.SetFilterOverrides(pfe.Id, ogs)

            link_category_color_map[cat_name] = [int(rgb[0]), int(rgb[1]), int(rgb[2])]
        except Exception as ex:
            failed_categories.append(cat)
            if diag is not None:
                diag.warn(
                    phase="color_id_buffer",
                    callsite="apply_link_category_filter",
                    message="Category '{0}' filter could not be created/applied; this "
                            "category will be hidden for this view's capture instead of "
                            "rendering with an uncontrolled native color: {1}".format(cat_name, ex),
                    view_id=view_id,
                )
    return link_category_color_map, created_filter_ids, reused_filter_ids, failed_categories


def _try_color_link_element_detailed(view, link_inst_id, link_elem_id, ogs):
    """Attempt a Revit 2022+ LinkElementId-based override for a linked element.

    Returns ``(success, exception)``: ``exception`` is the caught
    ``Exception`` instance on failure, or ``None`` on success — callers that
    need to know *why* the LinkElementId override failed (as opposed to just
    that it did) must record the actual exception rather than losing it, so
    a real bug is never silently reported the same way as a version/
    environment capability gap. LinkElementId/overload support varies by
    Revit version, so failure here is expected on older hosts and must not
    be treated as fatal by callers.

    Not used by export_color_id_buffer_view's main Stage A path (see
    ``_apply_link_category_filters`` for how LINK elements are colored) —
    kept for the Stage A external-source diagnostic probes
    (tests/dynamo/probe_stage_a_external_sources.py) that still exercise
    the per-element LinkElementId override path directly.
    """
    try:
        from Autodesk.Revit.DB import LinkElementId
        lek = LinkElementId(link_inst_id, link_elem_id)
        view.SetElementOverrides(lek, ogs)
        return True, None
    except Exception as ex:
        return False, ex


def _hidden_category_state(doc, view):
    """Categories to hide before the Stage A paint/export pass.

    Hides every top-level category Revit itself classifies as
    CategoryType.Annotation (tags, text, dimensions, datums, callouts, filled
    regions, etc. -- whatever that set is on this document, not a
    hand-maintained list that can drift out of sync with it), plus the
    Model-categorytype exceptions in VIEW_ONLY_MODEL_BIC_NAMES that have no
    real 3D presence despite the category-type label.
    """
    from Autodesk.Revit.DB import BuiltInCategory, CategoryType
    view_only_model_bic_ids = set()
    for bic_name in VIEW_ONLY_MODEL_BIC_NAMES:
        bic = getattr(BuiltInCategory, bic_name, None)
        if bic is not None:
            view_only_model_bic_ids.add(int(bic))

    state = {}
    for cat in doc.Settings.Categories:
        try:
            cat_id = cat.Id
            is_annotation = cat.CategoryType == CategoryType.Annotation
            is_view_only_model = cat_id.IntegerValue in view_only_model_bic_ids
        except Exception:
            continue
        if not (is_annotation or is_view_only_model):
            continue
        try:
            if view.CanCategoryBeHidden(cat_id):
                state[cat_id.IntegerValue] = {
                    "name": getattr(cat, "Name", None),
                    "was_hidden": bool(view.GetCategoryHidden(cat_id)),
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


def compute_model_crop(model_clip_bounds, bounds_xy):
    """Resolve the rectangle this view's color-ID export should be cropped to.

    Prefers ``model_clip_bounds`` (the pre-annotation-expansion, model-only
    crop threaded onto the raster by pipeline.py -- raster.model_clip_bounds,
    sourced from view_basis.py's resolve_view_bounds()) over ``bounds_xy``
    (the possibly annotation-expanded rectangle the rest of the grid/cell
    math is sized against). Cropping the render to the wider, annotation-
    expanded bounds_xy makes HOST elements that sit only in the annotation
    margin -- never part of the view's real model extent -- get collected
    and painted by the neutral-phase re-collection call downstream, which
    disagrees with LINK/DWG's narrower, unmodified view-crop-scoped
    collection for the same view. Falls back to ``bounds_xy`` unchanged
    (zero offset) when no model-only bounds is available -- e.g. bounds_
    result's reason != "crop" (extents/fallback bounds path) or an
    annotation-only view; see resolve_view_bounds's "model_bounds_uv" for
    exactly when it's populated.

    Pure Python, no Revit API dependency -- unit-testable directly, unlike
    the rest of this module.

    Defensively intersects the candidate crop with bounds_xy on all four
    sides rather than trusting model_clip_bounds's raw corners: resolve_
    view_bounds() normally guarantees model_clip_bounds is a strict subset
    of bounds_xy, but its post-annotation cap-envelope (view_basis.py:
    1038-1068) can, in rare cases, shrink bounds_xy back down below model_
    clip_bounds, which would otherwise make this crop wider than bounds_xy
    on that side. Intersecting keeps the render crop always inside bounds_xy
    regardless -- the "clamp to non-negative" safety net requested for the
    cap_triggered case, applied unconditionally since it is a no-op whenever
    the normal subset guarantee already holds.

    Returns:
        (render_bounds, offset) where:
            render_bounds: Bounds2D actually used for the view crop (may be
                bounds_xy itself, unchanged, if no narrower crop applies).
            offset: 4-tuple (dxmin, dymin, dxmax, dymax) such that
                    render_bounds.xmin == bounds_xy.xmin + dxmin
                    render_bounds.ymin == bounds_xy.ymin + dymin
                    render_bounds.xmax == bounds_xy.xmax + dxmax
                    render_bounds.ymax == bounds_xy.ymax + dymax
                i.e. ADD offset to bounds_xy's own corners to reconstruct
                the exact rectangle this capture was actually cropped to.
                This is a rectangle-to-rectangle relationship for
                reconstructing/auditing the render crop from bounds_xy --
                it is NOT meant to be added a second time to UV points
                already decoded against the correct render rectangle (see
                tools/decode_stage_a_color_id.py's module docstring for why
                that would double-count and corrupt otherwise-correct
                coordinates). (0.0, 0.0, 0.0, 0.0) when render_bounds is
                bounds_xy itself.
    """
    if bounds_xy is None:
        return model_clip_bounds, (0.0, 0.0, 0.0, 0.0)
    if model_clip_bounds is None:
        return bounds_xy, (0.0, 0.0, 0.0, 0.0)

    from .core.math_utils import Bounds2D

    eff_xmin = max(float(model_clip_bounds.xmin), float(bounds_xy.xmin))
    eff_ymin = max(float(model_clip_bounds.ymin), float(bounds_xy.ymin))
    eff_xmax = min(float(model_clip_bounds.xmax), float(bounds_xy.xmax))
    eff_ymax = min(float(model_clip_bounds.ymax), float(bounds_xy.ymax))

    if eff_xmax <= eff_xmin or eff_ymax <= eff_ymin:
        # model_clip_bounds does not meaningfully overlap bounds_xy (a
        # degenerate/empty intersection) -- fall back to bounds_xy unchanged
        # rather than force a zero/negative-area crop onto the view.
        return bounds_xy, (0.0, 0.0, 0.0, 0.0)

    render_bounds = Bounds2D(eff_xmin, eff_ymin, eff_xmax, eff_ymax)
    offset = (
        eff_xmin - float(bounds_xy.xmin),
        eff_ymin - float(bounds_xy.ymin),
        eff_xmax - float(bounds_xy.xmax),
        eff_ymax - float(bounds_xy.ymax),
    )
    return render_bounds, offset


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

    orig_view_template_id = None
    try:
        orig_view_template_id = view.ViewTemplateId
    except Exception as ex:
        if diag is not None:
            diag.warn(
                phase="color_id_buffer",
                callsite="capture_view_template",
                message=str(ex),
                view_id=view_id,
            )

    # Detach the view template (if any) before reading any V/G-controlled state
    # below. A template that controls Phase Filter, category visibility,
    # filters, or display style locks those read-only/non-overridable on the
    # instance -- CanCategoryBeHidden() and Parameter.IsReadOnly would both
    # report "can't touch this" even though Stage A is about to suppress and
    # restore everything on this view anyway. Detaching first, and reattaching
    # as the very last restore step, means every capture below reads (and
    # every restore step writes back) the view's real instance-level state.
    view_template_detached = False
    if orig_view_template_id is not None and orig_view_template_id != ElementId.InvalidElementId:
        detach_tx = Transaction(doc, "VOP Stage A DETACH view template")
        detach_tx.Start()
        try:
            view.ViewTemplateId = ElementId.InvalidElementId
            detach_tx.Commit()
            view_template_detached = True
        except Exception as ex:
            detach_tx.RollBack()
            if diag is not None:
                diag.warn(
                    phase="color_id_buffer",
                    callsite="detach_view_template",
                    message="Could not detach view template before Stage A capture; "
                            "template-controlled settings (phase filter, category "
                            "visibility, filters, display style) may remain locked "
                            "for this view: {0}".format(ex),
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
    # Deliberately NOT capturing/reusing prior OverrideGraphicSettings objects
    # here (a "restore to what it was" behavior this module used to have).
    # Reference SUPPRESS/RESTORE testing always resets element overrides to a
    # freshly-constructed blank OverrideGraphicSettings() rather than reapplying
    # a live object captured in an earlier transaction, and never carries a
    # live API object across a Transaction.Commit()/export boundary. Observed
    # bug: curtain wall panels silently kept their paint color after restore
    # (no exception) while their parent Wall correctly cleared — consistent
    # with a captured-then-reapplied-later OverrideGraphicSettings object not
    # reliably taking full effect once reused across that boundary. Painted-id
    # bookkeeping below exists only to know what to reset, not to remember
    # what it looked like before. Host elements are always reset via
    # resolved_ids directly (the full painted set, matching the reference
    # script); LINK elements get no individual override to reset at all --
    # created_link_category_filter_ids / reused_link_category_filter_ids
    # instead track every view-id-scoped category filter this run touched,
    # split by whether restore may doc.Delete it outright (freshly created,
    # nothing else could reference it yet) or must only RemoveFilter it from
    # THIS view (found already existing by name -- ParameterFilterElement
    # objects are document-global, so some other view/template could
    # reference the same one; see _apply_link_category_filters's docstring).
    created_link_category_filter_ids = []
    reused_link_category_filter_ids = []
    category_hidden_state = _hidden_category_state(doc, view)
    solid_pattern_id = _get_solid_pattern_id(doc)
    if solid_pattern_id is None:
        raise RuntimeError("No solid drafting fill pattern found in project")
    orig_display_style = getattr(view, "DisplayStyle", None)
    # Capture only the plain bool, not the ViewDisplayModel object itself — the
    # curtain-panel restore bug earlier in this module was caused by exactly
    # this pattern (holding a live Revit API object across the suppress/export/
    # restore transaction boundary). Fetch a fresh ViewDisplayModel whenever we
    # actually need to read or write it.
    orig_smooth_edges = None
    try:
        _dm = view.GetViewDisplayModel()
        try:
            orig_smooth_edges = bool(getattr(_dm, "SmoothEdges", None))
        finally:
            try:
                _dm.Dispose()
            except Exception:
                pass
    except Exception as ex:
        if diag is not None:
            diag.warn(
                phase="color_id_buffer",
                callsite="smooth_edges_capture",
                message=str(ex),
                view_id=view_id,
            )

    # Captured only as (BoundingBoxXYZ, bool) -- same live-API-object-across-
    # transaction-boundary caution as orig_display_style/orig_smooth_edges
    # above -- rather than re-derived at restore time.
    orig_crop_box = None
    orig_crop_box_active = None
    try:
        orig_crop_box = view.CropBox
        orig_crop_box_active = bool(view.CropBoxActive)
    except Exception as ex:
        if diag is not None:
            diag.warn(
                phase="color_id_buffer",
                callsite="crop_box_capture",
                message=str(ex),
                view_id=view_id,
            )

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
        # VIEW_PHASE_FILTER is commonly locked read-only by a View Template that
        # controls Phase Filter -- Parameter.Set() raises InvalidOperationException
        # in that case. That must not abort the whole view: fall back to the
        # view's existing phase filter (phase-hidden elements like New/Demolished/
        # Temporary will be absent from this view's ID buffer, which is a
        # completeness gap, not an occlusion-truth violation).
        phase_filter_swapped = bool(pf_param is not None and not pf_param.IsReadOnly)
        if phase_filter_swapped:
            pf_param.Set(neutral_pf.Id)
        elif diag is not None:
            diag.warn(
                phase="color_id_buffer",
                callsite="phase_filter_swap",
                message="VIEW_PHASE_FILTER is read-only (likely a View Template "
                        "controlling Phase Filter); continuing with the view's "
                        "existing phase filter. Elements the original phase filter "
                        "hides (e.g. New/Demolished/Temporary) will be missing "
                        "from this view's color ID buffer.",
                view_id=view_id,
            )
        phase_filter_state["swapped"] = phase_filter_swapped
        for cat_id_int, hstate in category_hidden_state.items():
            view.SetCategoryHidden(ElementId(int(cat_id_int)), True)

        # Force the view's crop to a narrower, model-only rectangle when one
        # is available (raster.model_clip_bounds -- the pre-annotation-
        # expansion crop bounds computed by view_basis.py's resolve_view_
        # bounds() and threaded onto the raster at pipeline.py's model_clip_
        # bounds assignment) instead of raster.bounds_xy (the possibly
        # annotation-expanded rectangle the rest of the grid is sized
        # against) -- see compute_model_crop() above for why. This MUST run
        # before the re-collection call further below: that collection has
        # to see the new crop, not the view's original one, or elements
        # outside the new crop but inside the old one would still be
        # collected/painted even though they will fall outside the exported
        # image.
        #
        # raster.bounds_xy itself is left untouched everywhere else (cell-
        # grid sizing, the annotation pass, LINK/DWG collection) -- this only
        # narrows what gets rendered/re-collected for the color-ID buffer.
        # The sidecar's "bounds_xy" field keeps its existing contract
        # (records exactly the rectangle the view was actually cropped to,
        # or None if no crop could be applied); model_crop_offset_uv is a
        # new, separate field recording that rectangle's relationship to
        # raster.bounds_xy -- see compute_model_crop()'s docstring for the
        # exact reconstruction convention, and tools/decode_stage_a_color_
        # id.py for why it is provenance/reconstruction data, not something
        # applied a second time to already-decoded UV points.
        crop_bounds_xy = None
        model_crop_offset_uv = (0.0, 0.0, 0.0, 0.0)
        try:
            if raster is not None and getattr(raster, "bounds_xy", None) is not None:
                render_bounds, model_crop_offset_uv = compute_model_crop(
                    getattr(raster, "model_clip_bounds", None), raster.bounds_xy
                )
                b = render_bounds
                basis = getattr(raster, "view_basis", None)
                if basis is None:
                    from .revit.view_basis import make_view_basis as _make_view_basis
                    basis = _make_view_basis(view, diag=diag)
                from .revit.view_basis import crop_box_from_uv_bounds as _crop_box_from_uv_bounds
                new_crop_box = _crop_box_from_uv_bounds(view, basis, b.xmin, b.ymin, b.xmax, b.ymax)
                if new_crop_box is not None:
                    view.CropBox = new_crop_box
                    view.CropBoxActive = True
                    crop_bounds_xy = (float(b.xmin), float(b.ymin), float(b.xmax), float(b.ymax))
                else:
                    model_crop_offset_uv = (0.0, 0.0, 0.0, 0.0)
                    if diag is not None:
                        diag.warn(
                            phase="color_id_buffer",
                            callsite="crop_box_set",
                            message="View has no CropBox; Stage A export falls back to "
                                    "FitToPage's auto-computed extent instead of an "
                                    "explicit crop",
                            view_id=view_id,
                        )
            elif diag is not None:
                diag.warn(
                    phase="color_id_buffer",
                    callsite="crop_box_set",
                    message="raster/raster.bounds_xy not provided; Stage A export falls "
                            "back to FitToPage's auto-computed extent instead of an "
                            "explicit crop",
                    view_id=view_id,
                )
        except Exception as ex:
            crop_bounds_xy = None
            model_crop_offset_uv = (0.0, 0.0, 0.0, 0.0)
            if diag is not None:
                diag.warn(
                    phase="color_id_buffer",
                    callsite="crop_box_set",
                    message=str(ex),
                    view_id=view_id,
                )

        # Force a flat, unlit display style so painted colors export exactly as
        # set — shading/shadows/ambient occlusion would tint a single flat-color
        # surface with a lighting gradient, which is exactly the kind of
        # per-pixel color drift a decoder can't tell apart from a real boundary.
        # DisplayStyle.FlatColors (Revit 2021+) is purpose-built for this; older
        # hosts fall back to plain Shading (no realistic materials/lighting, but
        # not guaranteed shadow-free) with a diagnostic noting the gap.
        applied_display_style = "unchanged"
        if orig_display_style is not None:
            try:
                from Autodesk.Revit.DB import DisplayStyle
                flat_style = getattr(DisplayStyle, "FlatColors", None)
                if flat_style is not None:
                    view.DisplayStyle = flat_style
                    applied_display_style = "FlatColors"
                else:
                    view.DisplayStyle = DisplayStyle.Shading
                    applied_display_style = "Shading"
                    if diag is not None:
                        diag.warn(
                            phase="color_id_buffer",
                            callsite="display_style",
                            message="DisplayStyle.FlatColors not available on this Revit "
                                    "version; falling back to Shading, which does not "
                                    "guarantee shadow/lighting-free flat colors",
                            view_id=view_id,
                        )
            except Exception as ex:
                applied_display_style = "unchanged (failed)"
                if diag is not None:
                    diag.warn(
                        phase="color_id_buffer",
                        callsite="display_style",
                        message=str(ex),
                        view_id=view_id,
                    )

        # Disable "Smooth lines with anti-aliasing" (the per-view Graphic
        # Display Options checkbox, distinct from DisplayStyle above). AA blends
        # colors across an element's silhouette edge, producing off-lattice
        # pixel colors right at element boundaries that a decoder can't tell
        # apart from a genuine third color — this is the specific setting the
        # original empirical Stage A testing confirmed as "AA-off is clean".
        applied_smooth_edges = "unchanged"
        if orig_smooth_edges is not None:
            try:
                dm = view.GetViewDisplayModel()
                try:
                    dm.SmoothEdges = False
                    view.SetViewDisplayModel(dm)
                    applied_smooth_edges = False
                finally:
                    try:
                        dm.Dispose()
                    except Exception:
                        pass
            except Exception as ex:
                applied_smooth_edges = "unchanged (failed)"
                if diag is not None:
                    diag.warn(
                        phase="color_id_buffer",
                        callsite="smooth_edges",
                        message=str(ex),
                        view_id=view_id,
                    )

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
            # LINK proxies this expansion would build (via linked_documents.py's
            # collect_all_linked_elements -> _collect_from_revit_links, one scan
            # per PLACEMENT, not deduped by document) are never consumed below --
            # LINK coloring now comes entirely from _collect_link_category_filters/
            # _apply_link_category_filters, which already dedupe by unique linked
            # document. Forcing include_linked_rvt off on a local Config copy (the
            # two include_* flags are the only cfg this expansion reads, and they
            # gate independent branches) skips that redundant per-placement work
            # without touching DWG import expansion, which HOST-paints exactly as
            # before.
            host_only_cfg = copy.copy(cfg)
            host_only_cfg.include_linked_rvt = False
            expanded = _expand_elements(doc, view, recollected, host_only_cfg, diag=diag, elem_cache=elem_cache)
        except Exception as ex:
            if diag is not None:
                diag.warn(
                    phase="color_id_buffer",
                    callsite="expand_linked_elements",
                    message=str(ex),
                    view_id=view_id,
                )
            expanded = [{"element": e, "source_type": "HOST"} for e in recollected]

        host_elements, _link_entries = _split_expanded_elements(expanded)
        resolved_ids = resolve_all(doc, host_elements)
        count_host = len(resolved_ids)

        # Respect the same include_linked_rvt opt-out collect_all_linked_elements()
        # always honored for the retired per-element path: discovery scans every
        # RevitLinkInstance in the view regardless of config, so skipping it
        # entirely when the user has disabled linked-RVT processing is the only
        # way an opted-out capture reports zero LINK assignments and applies no
        # category filters, matching prior behavior.
        if getattr(cfg, "include_linked_rvt", False):
            link_categories, uncolorable_link_categories = _collect_link_category_filters(
                doc, view, diag=diag, view_id=view_id
            )
        else:
            link_categories, uncolorable_link_categories = [], []
        count_link_categories = len(link_categories)
        total_count = count_host + count_link_categories

        global_threshold = int(getattr(cfg, "color_id_buffer_global_assignment_threshold", 32767))
        step = choose_step(global_threshold if total_count <= global_threshold else total_count)
        # TODO(Stage B+): add bbox pre-filter / multi-pass color batching if one view exceeds palette capacity.
        palette = build_palette(total_count, step=step)
        color_map = {resolved_ids[i].IntegerValue: palette[i] for i in range(count_host)}
        # Shared stepped allocation: link-category filter colors are sliced from
        # the same palette as HOST element colors (no independent RNG), so no
        # color_map/link_category_color_map RGB value can collide with another.
        categories_with_colors = [
            (cat, palette[count_host + idx]) for idx, cat in enumerate(link_categories)
        ]

        categories_touched = set()
        for eid in resolved_ids:
            elem = doc.GetElement(eid)
            cat = elem.Category if elem is not None else None
            if cat is not None:
                categories_touched.add(cat.Id.IntegerValue)
        for cat in link_categories:
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

        # LINK elements are colored via one ParameterFilterElement per category
        # applied to the view (SetFilterOverrides), not per-element — Revit's
        # documented override precedence (Instance/Element > Filter > Category)
        # means a HOST element's later per-element override below still wins;
        # LINK elements, which never get an individual override, fall through
        # to the filter's flat category color. Must run before the HOST paint
        # loop so the filters are in place before ExportImage.
        (
            link_category_color_map,
            created_link_category_filter_ids,
            reused_link_category_filter_ids,
            failed_link_categories,
        ) = _apply_link_category_filters(
            doc, view, view_id, categories_with_colors, solid_pattern_id, diag=diag
        )

        # Categories that are policy-included but couldn't be colored at all
        # (not Revit-filterable -- uncolorable_link_categories) or whose
        # filter creation/application failed (failed_link_categories) must
        # not render with their uncontrolled native color: that color could
        # coincidentally match a HOST element's deterministic palette RGB
        # and the decoder would silently attribute those pixels to the
        # wrong element. Hidden instead, mirroring the retired per-element
        # path's "hide what can't be colored" precedent. Reuses
        # category_hidden_state/the existing restore_category_hidden step
        # (populated dynamically here, mid-transaction, rather than
        # up-front like _hidden_category_state()'s sweep -- the dict is a
        # plain Python object, restore just iterates whatever is in it when
        # restore_tx runs later).
        for cat in list(uncolorable_link_categories) + list(failed_link_categories):
            cat_id_int = cat.Id.IntegerValue
            if cat_id_int in category_hidden_state:
                continue
            try:
                cat_id = ElementId(int(cat_id_int))
                if view.CanCategoryBeHidden(cat_id):
                    category_hidden_state[cat_id_int] = {
                        "name": getattr(cat, "Name", None),
                        "was_hidden": bool(view.GetCategoryHidden(cat_id)),
                    }
                    view.SetCategoryHidden(cat_id, True)
                elif diag is not None:
                    diag.warn(
                        phase="color_id_buffer",
                        callsite="hide_uncolorable_link_category",
                        message="Category '{0}' cannot be colored (not filterable, or filter "
                                "application failed) and CANNOT be hidden either; its native "
                                "LINK color may alias a HOST element's palette ID in this "
                                "view's ID buffer".format(getattr(cat, "Name", cat_id_int)),
                        view_id=view_id,
                    )
            except Exception as ex:
                if diag is not None:
                    diag.warn(
                        phase="color_id_buffer",
                        callsite="hide_uncolorable_link_category",
                        message=str(ex),
                        view_id=view_id,
                    )

        # Paint per-element, but never let one element's failure (some categories/
        # nested sub-components legitimately reject graphic overrides) roll back
        # every other element already painted in this view — mirrors the reference
        # SUPPRESS script's per-element try/except-and-continue behavior.
        paint_failures = 0
        paint_failed_element_ids = []
        for eid in resolved_ids:
            try:
                rgb = color_map[eid.IntegerValue]
                color = Color(int(rgb[0]), int(rgb[1]), int(rgb[2]))
                ogs = _build_flat_color_ogs(solid_pattern_id, color)
                view.SetElementOverrides(eid, ogs)
            except Exception as ex:
                paint_failures += 1
                paint_failed_element_ids.append(eid.IntegerValue)
                if diag is not None:
                    diag.warn(
                        phase="color_id_buffer",
                        callsite="paint_element_override",
                        message=str(ex),
                        view_id=view_id,
                        elem_id=eid.IntegerValue,
                    )
        if paint_failures and diag is not None:
            diag.warn(
                phase="color_id_buffer",
                callsite="paint_element_override",
                message="{0} of {1} resolved element(s) could not be painted; those "
                        "pixels will be unassigned in the ID buffer".format(paint_failures, count_host),
                view_id=view_id,
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

        # Element-level state (halftone, hidden categories, element/link overrides)
        # is restored FIRST, while the document is still under the same neutral
        # phase filter that was active when everything was painted. Reverting the
        # phase filter appears to trigger Revit to regenerate curtain-grid-hosted
        # sub-elements (mullions/panels get new ElementIds on regen; the host Wall
        # does not), which orphans any element-level restore attempted afterward —
        # observed as the parent curtain wall correctly losing its override while
        # its mullions/panels silently keep theirs. Filters and the phase filter
        # itself are restored last, once no more element-level Set/GetOverrides
        # calls depend on the current element identities.
        if orig_display_style is not None:
            def _restore_display_style():
                view.DisplayStyle = orig_display_style
            _restore_step("restore_display_style", _restore_display_style)

        if orig_smooth_edges is not None:
            def _restore_smooth_edges():
                dm = view.GetViewDisplayModel()
                try:
                    dm.SmoothEdges = orig_smooth_edges
                    view.SetViewDisplayModel(dm)
                finally:
                    try:
                        dm.Dispose()
                    except Exception:
                        pass
            _restore_step("restore_smooth_edges", _restore_smooth_edges)

        if orig_crop_box is not None:
            def _restore_crop_box():
                view.CropBox = orig_crop_box
                view.CropBoxActive = orig_crop_box_active
            _restore_step("restore_crop_box", _restore_crop_box)

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

        # Always reset to a freshly-constructed blank OverrideGraphicSettings(),
        # matching the reference SUPPRESS/RESTORE script exactly — never reapply
        # an object captured earlier (see note above painted_ids).
        for eid in resolved_ids:
            def _restore_element_override(eid=eid):
                from Autodesk.Revit.DB import OverrideGraphicSettings
                view.SetElementOverrides(ElementId(int(eid.IntegerValue)), OverrideGraphicSettings())
            _restore_step("restore_element_overrides", _restore_element_override)

        # Filters and the phase filter are restored last (see note above) —
        # reverting the phase filter can trigger regeneration of curtain-grid
        # sub-elements, so nothing element-level should still depend on the
        # current identities of those elements by this point.
        #
        # Created filters: RemoveFilter (view's active filter list) AND
        # doc.Delete (the ParameterFilterElement itself) -- nothing else in
        # the document could reference something this call only just
        # created, so full cleanup is safe. Each half is its own isolated
        # step so a RemoveFilter failure (e.g. it was never actually applied
        # because AddFilter itself raised) never blocks the doc.Delete that
        # matters more for not orphaning a document element.
        for fid_int in created_link_category_filter_ids:
            def _restore_remove_created_filter(fid_int=fid_int):
                view.RemoveFilter(ElementId(int(fid_int)))
            _restore_step("restore_remove_created_link_category_filter", _restore_remove_created_filter)
            def _restore_delete_created_filter(fid_int=fid_int):
                doc.Delete(ElementId(int(fid_int)))
            _restore_step("restore_delete_created_link_category_filter", _restore_delete_created_filter)

        # Reused filters (found already existing by name -- see
        # _apply_link_category_filters's docstring): RemoveFilter from THIS
        # view only, never doc.Delete -- the ParameterFilterElement is
        # document-global and some other view or view template could
        # reference the very same one.
        for fid_int in reused_link_category_filter_ids:
            def _restore_remove_reused_filter(fid_int=fid_int):
                view.RemoveFilter(ElementId(int(fid_int)))
            _restore_step("restore_remove_reused_link_category_filter", _restore_remove_reused_filter)

        def _restore_filters():
            already_cleaned_up = set(created_link_category_filter_ids) | set(reused_link_category_filter_ids)
            for fid_int, fstate in filter_state.items():
                if fid_int in already_cleaned_up:
                    # A same-view Stage A crash leftover reused above and just
                    # removed (and, if freshly created, deleted) by the
                    # per-filter loops -- touching its ElementId here would
                    # raise and abort this loop before it reaches any later,
                    # unrelated pre-existing filter's own restore.
                    continue
                view.SetIsFilterEnabled(ElementId(int(fid_int)), fstate["was_enabled"])
                # was_visible is captured above alongside was_enabled but was
                # never restored here -- a latent gap that only matters once
                # something actively calls SetFilterVisibility, which
                # _apply_link_category_filters now does (a same-view crash
                # leftover could have been left non-visible); for every other,
                # untouched filter this is a no-op (nothing else in this
                # module ever changes filter visibility).
                view.SetFilterVisibility(ElementId(int(fid_int)), fstate["was_visible"])
        _restore_step("restore_filters", _restore_filters)

        def _restore_phase_filter():
            if orig_phase_filter_id is not None and phase_filter_state.get("swapped"):
                pf_param.Set(ElementId(int(orig_phase_filter_id)))
        _restore_step("restore_phase_filter", _restore_phase_filter)

        if phase_filter_state.get("neutral_phase_filter_created") and phase_filter_state.get("neutral_phase_filter_id") is not None:
            _restore_step(
                "restore_neutral_phase_filter",
                lambda: doc.Delete(ElementId(int(phase_filter_state["neutral_phase_filter_id"]))),
            )

        # Reattach the view template last of all -- every other restore step
        # above needs the template still detached to succeed (same reasoning
        # as the detach at the top of this function), and once reattached
        # Revit reasserts whatever the template dictates for the settings it
        # controls anyway.
        if view_template_detached and orig_view_template_id is not None:
            def _restore_view_template():
                view.ViewTemplateId = orig_view_template_id
            _restore_step("restore_view_template", _restore_view_template)

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
        # View-local UV rectangle (min_u, min_v, max_u, max_v) the export
        # was cropped to -- the same tuple set as view.CropBox above, not
        # recomputed here. None when the crop could not be applied (no
        # raster/bounds_xy provided, or the view has no CropBox), in which
        # case the TIFF's extent is whatever FitToPage auto-computed and a
        # decode step cannot assume this field describes it. May now be
        # narrower than the raster's own bounds_xy (see compute_model_crop()
        # above) -- model_crop_offset_uv below records that relationship.
        "bounds_xy": list(crop_bounds_xy) if crop_bounds_xy is not None else None,
        # (dxmin, dymin, dxmax, dymax) such that ADDING this to raster.
        # bounds_xy's own corners reconstructs this capture's actual crop
        # (the "bounds_xy" rectangle above): render.xmin = raster.bounds_xy.
        # xmin + dxmin, etc. Always (0.0, 0.0, 0.0, 0.0) when this capture's
        # crop IS raster.bounds_xy unchanged (no narrower model_clip_bounds
        # was available, or the crop could not be applied at all -- in which
        # case "bounds_xy" above is also None). Diagnostic/reconstruction
        # data only -- see compute_model_crop()'s docstring and tools/
        # decode_stage_a_color_id.py for why decoded UV points must not be
        # shifted by this a second time.
        "model_crop_offset_uv": [float(v) for v in model_crop_offset_uv],
        "color_assignment_map": {str(k): list(v) for k, v in color_map.items()},
        "paint_failures": paint_failures,
        "paint_failed_element_ids": list(paint_failed_element_ids),
        "link_category_color_map": dict(link_category_color_map),
        "applied_display_style": applied_display_style,
        "applied_smooth_edges": applied_smooth_edges,
        "categories_hidden": category_hidden_state,
        "filter_state": filter_state,
        "phase_filter_state": phase_filter_state,
        "category_halftone_state": category_halftone_state,
        "palette_step": step,
        "tiff_path": tiff_path,
        "view_template_detached": view_template_detached,
        "orig_view_template_id": (
            orig_view_template_id.IntegerValue if orig_view_template_id is not None else None
        ),
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
        "color_assignment_count": count_host + count_link_categories,
        "timings": {"color_id_buffer_ms": round((time.time() - t0) * 1000.0, 3)},
        "metadata": state_out,
    }
