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
import struct
import time

from .resolution_contract import (
    DEFAULT_COLOR_ID_EXPORT_DPI,
    MAX_STAGE_A_AXIS_PX,
    cap_axes,
    effective_export_dpi as _effective_export_dpi,
    frame_export_geometry,
)

NEUTRAL_PHASE_FILTER_NAME = "VOP_NeutralPhaseFilter"
# Kept as a name so existing readers (tools/decode_stage_a_color_id.py) keep
# importing one symbol, but it is no longer a ceiling of this module's own
# invention sitting above the measured limit: it IS the measured limit, and
# it now bounds BOTH exported axes rather than only the one FitDirection
# sets. See resolution_contract.MAX_STAGE_A_AXIS_PX for the evidence.
MAX_STAGE_A_PIXEL_SIZE = MAX_STAGE_A_AXIS_PX
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

# Vetted colorable categories -- the FROZEN capture of
# ParameterFilterUtilities.GetAllFilterableCategories(), as primary-category
# ElementId integers (no subcategories).
#
# KEYED BY ID, NOT BY NAME. Category.Name is LOCALIZED, so a whitelist keyed
# on display names and captured on an English Revit matches nothing on a
# French or German one: every category would be judged uncolorable, every
# LINK category would lose its filter, and the capture would look exactly
# like one run against a stale whitelist. collection_policy.py hit the same
# hazard first and documents it in those terms; the stable identifier is what
# it relies on. A built-in category's id is its BuiltInCategory enum value --
# a negative integer, identical across locales and across Revit versions --
# which makes it both the portable key and, unlike an OST_ name, one that
# needs no API resolution to compare against.
#
# Auditability is preserved by the capture tool writing each entry with its
# display name as a trailing comment, so the frozen block still reads as a
# list of categories in review rather than as a wall of integers.
#
# Regenerate with tools/capture_vetted_colorable_categories.py, run inside a
# live Dynamo/Revit session; it prints this exact literal block for pasting.
# Do not hand-transcribe it from a screenshot or from a description of the
# Filter dialog: the live API call is the same source the dialog itself reads
# from, and transcription of a ~150-200 entry list risks silent errors that
# would show up only as a category quietly losing its color in a capture.
#
# TRADEOFF (deliberate): freezing the list trades a small amount of
# Revit-version drift risk -- a category ADDED in a newer Revit than the
# capture will not be recognized as colorable until the list is recaptured --
# for full determinism and auditability. (Renaming is no longer a drift risk
# now that the key is the id.) Two captures of the same view now assign the
# same categories the same way regardless of what the host's live API happens
# to report, and a reviewer can see the exact set in the diff instead of
# having to run Revit to know it. Drift is also visible rather than silent:
# an unrecognized category is reported as "uncolorable" in the diagnostics
# and in the sidecar's uncolorable_link_categories field, so a stale
# whitelist shows up as a named category rather than as content that quietly
# stopped being identified.
#
# EMPTY UNTIL CAPTURED. While this set is empty,
# _resolve_colorable_category_predicate() falls back to the live
# GetAllFilterableCategories() lookup and records a loud diagnostic saying so
# on every capture -- the pre-freeze behavior, kept working but never silent.
# Populating this set retires that fallback permanently.
VETTED_COLORABLE_CATEGORY_IDS = frozenset()

# Hue samples per (saturation, value) band in build_palette()'s hue-primary
# traversal. 186 = 6 * (32 - 1): at the production step of 8 there are 32
# levels per RGB channel, and a fully saturated hue sweep traces the six
# edges of the RGB cube's outer hexagon, hitting 6 * (levels - 1) distinct
# lattice points. Fixed rather than scaled to the step so the traversal stays
# bounded for the very dense lattices choose_step() falls back to for
# pathologically large views; the deterministic completion pass in
# build_palette() picks up whatever those denser lattices leave unreached.
HUE_SAMPLES_PER_BAND = 186
# Stride applied to the hue index within a band, so CONSECUTIVELY ASSIGNED
# colors land far apart on the hue circle instead of walking one cube edge at
# a time. 115 ~= 186 / the golden ratio, and gcd(115, 186) == 1 (186 = 2*3*31,
# 115 = 5*23), so multiplying modulo 186 is a full-period permutation: every
# hue sample is still visited exactly once per band, just in
# near-golden-angle order. Without this a 10-element view would receive ten
# near-identical reds even though the band as a whole spans 0-360.
HUE_TRAVERSAL_STRIDE = 115


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
    """Choose a color-lattice step with capacity for ``element_count`` IDs.

    ``step`` is the spacing of the RGB lattice every palette color is snapped
    onto (see build_palette). Capacity excludes the near-black and near-white
    corners reserved by build_palette(). The default Stage-A global threshold
    is conservative enough to use a global 8-step palette for current model
    sizes, while permitting denser per-view palettes when needed.

    Unchanged by the move to a hue-primary traversal: build_palette() still
    emits colors drawn from exactly this lattice, and its completion pass
    guarantees it can reach every valid point on it, so the capacity this
    function promises is still the capacity build_palette() can deliver. See
    build_palette()'s docstring for the derivation, and
    tests/test_color_id_buffer.py for the enforced round-trip.
    """
    n = max(1, int(element_count or 1))
    for step in (8, 6, 5, 4, 3, 2, 1):
        levels = (255 // step) + 1
        capacity = (levels ** 3) - _reserved_corner_count(step)
        if capacity >= n:
            return step
    return 1


def _hsv_to_rgb255(hue_deg, sat, val):
    """HSV -> 0-255 RGB, implemented inline rather than via colorsys.

    Two reasons, both about this module's IronPython 2.x / CPython 3.x dual
    target: colorsys is not guaranteed present in every IronPython deployment
    this ships into, and rounding must be bit-identical across both or the
    palette stops being reproducible. ``int(x + 0.5)`` is used instead of
    ``round()`` precisely because round() is half-away-from-zero on Python 2
    and banker's rounding on Python 3 -- the same float would otherwise
    quantize to different colors on the two hosts.
    """
    h = (float(hue_deg) % 360.0) / 60.0
    sector = int(h) % 6
    f = h - int(h)
    p = val * (1.0 - sat)
    q = val * (1.0 - sat * f)
    t = val * (1.0 - sat * (1.0 - f))
    if sector == 0:
        r, g, b = val, t, p
    elif sector == 1:
        r, g, b = q, val, p
    elif sector == 2:
        r, g, b = p, val, t
    elif sector == 3:
        r, g, b = p, q, val
    elif sector == 4:
        r, g, b = t, p, val
    else:
        r, g, b = val, p, q
    return (int(r * 255.0 + 0.5), int(g * 255.0 + 0.5), int(b * 255.0 + 0.5))


def _snap_to_lattice(channel, step, max_level):
    """Snap one 0-255 channel onto the ``step`` lattice choose_step() sized."""
    q = int(float(channel) / step + 0.5)
    if q > max_level:
        q = max_level
    elif q < 0:
        q = 0
    return q * step


def _is_reserved_corner(rgb):
    """True for the near-black and near-white corners build_palette() refuses."""
    r, g, b = rgb
    if r < NEAR_BLACK_RESERVED_THRESHOLD and g < NEAR_BLACK_RESERVED_THRESHOLD and b < NEAR_BLACK_RESERVED_THRESHOLD:
        return True
    if r >= NEAR_WHITE_RESERVED_THRESHOLD and g >= NEAR_WHITE_RESERVED_THRESHOLD and b >= NEAR_WHITE_RESERVED_THRESHOLD:
        return True
    return False


def build_palette(element_count, step=None):
    """Build deterministic non-near-black, non-near-white colors, hue first.

    Generation is HSV-based with HUE as the primary, fastest-spanning
    dimension: saturation is the outer loop, value the middle one, and a full
    0-360 hue sweep is the inner one, so the first colors handed out are
    maximally separated in hue instead of maximally similar. That is the fix
    for the confirmed blue bias of the previous RGB-nested-loop version
    (r outer, g middle, b inner, returning as soon as element_count was
    reached): a real capture only ever sampled the low-R/low-G corner, so
    essentially every element in every production view got a shade of blue.
    HUE_TRAVERSAL_STRIDE spreads the sweep within each band as well, so even
    a ten-element view gets ten clearly distinct hues rather than ten reds.

    Each generated color is then SNAPPED onto the same ``step`` RGB lattice
    the previous implementation enumerated directly, and duplicates are
    dropped. This is a deliberate deviation from a pure HSV lattice: two
    neighboring HSV samples at low value/low saturation convert to RGB
    values only one or two units apart, which is inside the TIFF-export and
    rendering noise this module's reserved corners already exist to defend
    against, and a decoder could not tell those two element IDs apart.
    Snapping preserves the previous implementation's guarantee that any two
    palette colors differ by at least ``step`` in at least one channel.

    The near-black/near-white rejection is applied after conversion and
    snapping (the previous version could test it before conversion because it
    generated in RGB directly), so the reserved corners exclude exactly the
    same set of colors as before.

    CAPACITY. The hue sweep alone cannot reach every point of the lattice, so
    a completion pass walks the lattice in the old deterministic RGB order and
    appends whatever the sweep did not emit. The palette is therefore a
    permutation of exactly the valid-lattice set the old implementation drew
    from -- same set, different order -- which is why choose_step() and
    _reserved_corner_count() need no re-derivation to stay correct: capacity
    is still (levels ** 3) - _reserved_corner_count(step), and build_palette
    can still always deliver it. That equality is asserted directly at every
    practically reachable step in tests/test_color_id_buffer.py rather than
    left as a claim. The completion pass is unreachable for any realistic
    view (it only begins once the hue sweep has exhausted what it can reach,
    far past any real element count at the production step of 8).

    Reproducibility: same (element_count, step) always yields the same list,
    and a shorter request is always a prefix of a longer one at the same step.
    """
    step = int(step if step is not None else choose_step(element_count))
    target = int(element_count or 0)
    if target <= 0:
        return []

    n_levels = (255 // step) + 1
    max_level = n_levels - 1
    colors = []
    seen = set()

    # Saturation outer, value middle, hue inner. Both outer dimensions run
    # high-to-low so the first bands are the fully saturated, full-brightness
    # ones -- the hues that separate most cleanly in RGB and survive export
    # noise best go to the elements most likely to exist at all.
    for sat_idx in range(n_levels, 0, -1):
        sat = float(sat_idx) / n_levels
        for val_idx in range(n_levels, 0, -1):
            val = float(val_idx) / n_levels
            for i in range(HUE_SAMPLES_PER_BAND):
                hue_idx = (i * HUE_TRAVERSAL_STRIDE) % HUE_SAMPLES_PER_BAND
                hue = 360.0 * hue_idx / HUE_SAMPLES_PER_BAND
                r, g, b = _hsv_to_rgb255(hue, sat, val)
                rgb = (
                    _snap_to_lattice(r, step, max_level),
                    _snap_to_lattice(g, step, max_level),
                    _snap_to_lattice(b, step, max_level),
                )
                if rgb in seen or _is_reserved_corner(rgb):
                    continue
                seen.add(rgb)
                colors.append(rgb)
                if len(colors) >= target:
                    return colors

    # Completion pass -- see CAPACITY above. Same lattice order the previous
    # implementation used, so the tail of a maximally-full palette is exactly
    # the old palette's content, minus whatever the hue sweep already took.
    for r in range(0, 256, step):
        for g in range(0, 256, step):
            for b in range(0, 256, step):
                rgb = (r, g, b)
                if rgb in seen or _is_reserved_corner(rgb):
                    continue
                seen.add(rgb)
                colors.append(rgb)
                if len(colors) >= target:
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


def _split_expanded_elements(expanded_entries, source_out=None):
    """Partition expand_host_link_import_model_elements() output.

    Returns (host_elements, link_entries):
    - host_elements: real Elements living in `doc` (HOST + DWG ImportInstance),
      safe to resolve/override the normal way.
    - link_entries: list of (link_instance_id, link_element_id, proxy) tuples for
      elements that live inside a linked RVT document and require the
      LinkElementId override path.

    ``source_out``: optional dict the caller supplies to have the non-LINK
    entries' own ``source_type`` PRESERVED across this partition (Stage A
    step 1). It is filled ``{element_id_int: source_type}`` for exactly the
    elements returned in ``host_elements`` -- so a DWG ImportInstance, which
    this function deliberately hands back in the HOST bucket because it is a
    real element of ``doc`` and overrides the ordinary way, stays
    distinguishable from a true HOST element downstream. Without it the
    "HOST bucket" is one-way: source_type is read here and then dropped, and
    nothing after this point can tell the two apart.

    It is an out-parameter rather than a third return value on purpose: this
    is the ONE walk that decides which entries are host and which id wins the
    dedup, so deriving the map from a second walk elsewhere would be the
    "same quantity computed in two places" defect CLAUDE.md warns about, while
    a third return value would break every existing 2-tuple call site.

    Note the map is keyed by TOP-LEVEL element id only. resolve_all() later
    expands Groups and FamilyInstance sub-components into ids that never
    appear here; see _collect_near_face_w_data() for how those are labelled.
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
        if source_out is not None:
            source_out[eid.IntegerValue] = source_type
    return host_elements, link_entries


def _is_import_instance(elem):
    """True when ``elem`` is a DWG/DXF ImportInstance.

    Type-NAME based, deliberately: it is the same test revit/collection_
    policy.py already applies to keep ImportInstance out of the HOST
    collection pass ("Belt-and-suspenders: exclude ImportInstance by type
    name regardless of category name localization"), and it needs no Revit
    import so it runs outside Revit. tests/test_near_face_w_collection.py
    COMPOSES the two -- asserting this helper and that policy agree on the
    same object -- rather than testing each against its own copy of the
    idiom.

    Used only for ids resolve_all() produced by expansion, where
    _split_expanded_elements' per-entry source_type is not available.

    Matching on the type NAME means a SUBCLASS of ImportInstance answers
    False. That is shared with collection_policy.py, so the two stay
    consistent, and the Revit API exposes ImportInstance as a concrete type
    rather than something host code subclasses -- but it is a real edge of
    the idiom, not an oversight.
    """
    return type(elem).__name__ == "ImportInstance"


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


def _category_id_int(cat, diag=None, view_id=None, callsite="category_id"):
    """A Category's id as a plain int, or None when it cannot be read.

    Returning None is not the same fact as "this category is not in the set",
    and every caller below uses the result in a membership test where the two
    are indistinguishable by inspection. So the exception is RECORDED rather
    than swallowed (AGENTS.md: "all errors must be recorded in Diagnostics"):
    without it, a stale or broken Revit Category and a category that simply is
    not on the whitelist produce the same downstream outcome with no way to
    tell which happened.

    The category name is reported best-effort. It is read in its own guard
    because a Category whose Id access raises may well fail on Name too, and
    losing the diagnostic to a second exception would defeat the point.
    """
    try:
        return int(cat.Id.IntegerValue)
    except Exception as ex:
        if diag is not None:
            try:
                cat_name = getattr(cat, "Name", None) or "<unreadable>"
            except Exception:
                cat_name = "<unreadable>"
            diag.warn(
                phase="color_id_buffer",
                callsite=callsite,
                message="Could not read the category id for '{0}'; it is treated as not "
                        "colorable for this capture, which is NOT the same as it being "
                        "absent from the whitelist: {1}: {2}".format(
                            cat_name, type(ex).__name__, ex),
                view_id=view_id,
            )
        return None


def _resolve_colorable_category_predicate(diag=None, view_id=None):
    """Resolve the single "can Stage A give this category a real color?" test.

    Returns ``(predicate, source)`` where predicate takes a Category and
    ``source`` names where the answer came from, for the sidecar. Centralized
    rather than inlined at the one call site so there is a single place the
    answer comes from if Stage A ever grows a second consumer of it.

    Prefers the frozen VETTED_COLORABLE_CATEGORY_IDS capture. While that set
    is still empty it falls back to the live
    ParameterFilterUtilities.GetAllFilterableCategories() lookup this replaced,
    and says so loudly on every capture: the fallback keeps pre-freeze captures
    working exactly as before, but an unfrozen whitelist is a state the
    operator has to be able to see, not a silent default.
    """
    if VETTED_COLORABLE_CATEGORY_IDS:
        ids = VETTED_COLORABLE_CATEGORY_IDS

        def _by_frozen_id(cat):
            return _category_id_int(
                cat, diag=diag, view_id=view_id, callsite="colorable_category_whitelist",
            ) in ids

        return _by_frozen_id, "frozen_whitelist"

    try:
        from Autodesk.Revit.DB import ParameterFilterUtilities
        filterable_ids = set(
            cid.IntegerValue for cid in ParameterFilterUtilities.GetAllFilterableCategories()
        )
    except Exception as ex:
        # Colorability is unknown, so assume every policy-included category IS
        # colorable and let Revit adjudicate: _apply_link_category_filters
        # attempts one ParameterFilterElement per category, which succeeds for
        # the categories Revit can actually filter on and raises for the rest,
        # landing those in failed_categories. The attempt is the lookup.
        #
        # Assuming the opposite would withhold color from every LINK category,
        # including the ones a filter would have worked on, and buy nothing for
        # it: nothing downstream hides or marks what goes uncolored (see
        # _model_categories_in_linked_doc's ACCEPTED GAP note), so a category
        # denied a filter here renders exactly like one whose filter failed.
        # The conservative answer costs identity coverage without reducing
        # risk. It was the right call only while a category-level force-white
        # override covered whatever went uncolored, and that override is gone.
        #
        # Recorded at ERROR, above the live-lookup fallback's warning: this
        # capture consulted no colorability source at all, so its LINK
        # coloring is neither reproducible nor auditable, and neither cause --
        # an unfrozen VETTED_COLORABLE_CATEGORY_IDS, an unavailable
        # ParameterFilterUtilities -- is something a retry can change.
        if diag is not None:
            diag.error(
                phase="color_id_buffer",
                callsite="colorable_category_whitelist",
                message="VETTED_COLORABLE_CATEGORY_IDS is empty AND the live "
                        "ParameterFilterUtilities fallback failed; attempting a filter "
                        "for every policy-included LINK category instead. Categories "
                        "Revit cannot filter on will fail and are reported in "
                        "failed_link_categories: {0}".format(ex),
                view_id=view_id,
                exc=ex,
            )
        return (lambda cat: True), "unavailable"

    if diag is not None:
        diag.warn(
            phase="color_id_buffer",
            callsite="colorable_category_whitelist",
            message="VETTED_COLORABLE_CATEGORY_IDS is empty; falling back to the live "
                    "ParameterFilterUtilities.GetAllFilterableCategories() lookup for "
                    "this capture. Colorability is therefore host-dependent and not "
                    "reproducible from the source tree -- run "
                    "tools/capture_vetted_colorable_categories.py in Dynamo and freeze "
                    "the result to retire this fallback.",
            view_id=view_id,
        )

    def _by_live_lookup(cat):
        return _category_id_int(
            cat, diag=diag, view_id=view_id, callsite="colorable_category_whitelist",
        ) in filterable_ids

    return _by_live_lookup, "live_filterable_lookup"


def _dedupe_link_instances_by_document(link_instances):
    """Group placed RevitLinkInstance objects by underlying document identity
    (PathName), so a link type placed many times in a view (e.g. a "typical
    exam room" link placed dozens of times) is only scanned once — ported
    from the tested standalone filter-creation script, where scanning each
    placement separately was the dominant cost (15.9s of 34s total for 152
    instances resolving to a much smaller set of unique documents).

    Returns ``(unique_docs, unresolved_names)``:
      unique_docs: path_name -> {"instance": link_inst, "linked_doc": Document,
        "instances": [link_inst, ...]}. "instance" is one representative
        placement (enough to scan the document's own elements/categories);
        "instances" is EVERY placement of that document in this view --
        needed when a category discovered in the document must be
        suppressed, since every placement could carry that category's
        geometry, not just the representative one.
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
            unique_docs[path_name] = {"instance": link_inst, "linked_doc": linked_doc, "instances": [link_inst]}
        else:
            unique_docs[path_name]["instances"].append(link_inst)
    return unique_docs, unresolved_names


def _model_categories_in_linked_doc(linked_doc, is_colorable):
    """Distinct categories among elements in ONE linked document that the
    project's authoritative LINK category policy (revit/collection_policy.
    py's should_include_element(), "single source of truth" per CLAUDE.md)
    would include -- split by whether Stage A can actually color them, which
    is the frozen VETTED_COLORABLE_CATEGORY_IDS whitelist rather than a
    live ParameterFilterUtilities.GetAllFilterableCategories() lookup (see
    _resolve_colorable_category_predicate). Both checks must run for every
    category: a policy-included-but-uncolorable category still needs to be
    identified and returned, not silently dropped.

    Returns ``(colorable, uncolorable)``: ``colorable`` categories are both
    policy-included AND on the vetted whitelist -- callable code can color
    their LINK elements via a category filter. ``uncolorable`` categories are
    policy-included but not colorable; they are returned for REPORTING ONLY.

    ACCEPTED GAP -- uncontrolled native color
    -----------------------------------------
    Nothing capture-side suppresses content Stage A cannot color. That covers
    three sets, and applies to HOST as much as to LINK:

      - the ``uncolorable`` categories returned here;
      - colorable categories whose filter creation/application fails
        (``failed_categories`` from _apply_link_category_filters);
      - policy-EXCLUDED but CategoryType.Model HOST categories -- Rooms,
        Areas, MEP Spaces and Point Clouds are all in collection_policy's
        _EXCLUDED_BIC_NAMES_GLOBAL and all report CategoryType.Model, while
        _hidden_category_state() hides only CategoryType.Annotation plus the
        two VIEW_ONLY_MODEL_BIC_NAMES entries. Nothing collects them, nothing
        paints them, nothing hides them. (Confirmed by reading those two
        lists against each other, not assumed.)

    All three render with whatever native color Revit gives them. This is a
    KNOWN, ACCEPTED gap, not an oversight, and it is deliberately NOT closed
    at capture time -- an earlier hide-the-link-instance mechanism and a later
    force-white category-override mechanism were both tried and both removed,
    the first for taking down colorable categories sharing a placement, the
    second as more capture-side machinery than the risk warrants.

    What actually contains the risk is on the DECODE side:
    tools/decode_stage_a_color_id.py does exact-match lookup against
    color_assignment_map with, in its own words, "no nearest-color fallback"
    -- an uncontrolled color is only ever misread as an element if it lands
    EXACTLY on that element's assigned RGB. A planned bbox-location
    cross-check (separate follow-up, not part of this pass) narrows the
    residual accidental-collision risk further, and does so uniformly for
    non-whitelisted content and non-compliant LINK instances alike.

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
        if is_colorable(cat):
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


def _reused_filter_matches_category(pfe, cat):
    """True only if ``pfe`` is exactly what this module itself would have
    created for ``cat``: an accept-all filter on cat's single category id,
    with no additional element-parameter rule narrowing which of that
    category's elements it actually matches.

    A filter found by name (see _apply_link_category_filters's docstring
    for why a match on the view-id-scoped name should, in practice, only
    ever be this same view's own Stage A crash leftover) is still just a
    name match -- Revit enforces no reservation on that naming pattern, so
    nothing guarantees the found filter's actual definition is the one
    this function is about to assume it is. Reusing an unverified filter
    could silently leave cat's elements native-colored (if the filter
    doesn't actually catch them) while link_category_color_map still
    reports the category as successfully colored -- the same
    HOST-palette-ID-aliasing risk this whole mechanism exists to prevent,
    just from a mismatched filter definition instead of a missing one.
    """
    try:
        cat_ids = set(c.IntegerValue for c in pfe.GetCategories())
        if cat_ids != {cat.Id.IntegerValue}:
            return False
        return pfe.GetElementFilter() is None
    except Exception:
        return False


def _collect_link_category_filters(doc, view, is_colorable, diag=None, view_id=None):
    """Discover the union of policy-included, CategoryType.Model categories
    across every UNIQUE linked document referenced in this view.

    Ported from the tested standalone filter-creation script: deduplicates
    RevitLinkInstance placements by underlying document identity (PathName)
    before scanning, then unions each unique document's model categories.

    ``is_colorable`` is the shared predicate from
    _resolve_colorable_category_predicate(), passed in rather than resolved
    here so the caller resolves it once per capture.

    Returns ``(colorable, uncolorable)``: ordered lists of Category objects,
    sorted by Name for a deterministic, reproducible palette-slot assignment
    order. ``colorable`` categories get a ParameterFilterElement (see
    _apply_link_category_filters). ``uncolorable`` ones are returned for
    reporting only -- the caller has nothing to do about them beyond
    recording them; see _model_categories_in_linked_doc's "ACCEPTED GAP" note
    for why nothing suppresses them at capture time.
    """
    from Autodesk.Revit.DB import FilteredElementCollector, RevitLinkInstance
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

    combined_colorable = {}
    combined_uncolorable = {}
    for _path_name, info in unique_docs.items():
        colorable, uncolorable = _model_categories_in_linked_doc(info["linked_doc"], is_colorable)
        for cat in colorable:
            combined_colorable.setdefault(cat.Id.IntegerValue, cat)
        for cat in uncolorable:
            combined_uncolorable.setdefault(cat.Id.IntegerValue, cat)

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

    A category whose filter creation/application fails here renders with its
    native, uncontrolled color. Nothing suppresses that: it joins the same
    accepted gap as the "uncolorable" categories discovery finds up front --
    see _model_categories_in_linked_doc's ACCEPTED GAP note for what actually
    contains the risk (decode's exact-match-only lookup) and for the two
    capture-side mechanisms that were tried for this and removed.
    failed_categories is returned so the caller can report it.

    Returns ``(link_category_color_map, created_filter_ids, reused_filter_ids,
    failed_categories)``:
      link_category_color_map: {category_name: [r, g, b]}
      created_filter_ids: ElementId ints of filters this call created fresh
        this run -- restore may RemoveFilter AND doc.Delete these.
      reused_filter_ids: ElementId ints of filters this call found already
        existing by name and recolored -- restore may only RemoveFilter
        these from THIS view, never doc.Delete the shared definition.
      failed_categories: Category objects whose filter could not be
        created/applied -- reported by the caller, not suppressed.
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
                if not _reused_filter_matches_category(pfe, cat):
                    raise RuntimeError(
                        "Existing filter '{0}' does not match category '{1}' (wrong "
                        "category set or an element-parameter rule) -- refusing to "
                        "reuse it".format(filter_name, cat_name)
                    )
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
                    message="Category '{0}' filter could not be created/applied; its "
                            "LINK elements render with an uncontrolled native color in "
                            "this capture: {1}".format(cat_name, ex),
                    view_id=view_id,
                )
    return link_category_color_map, created_filter_ids, reused_filter_ids, failed_categories


def _finite_or_none(value):
    """Normalize a depth value for JSON serialization: json.dump()'s default
    allow_nan=True emits the non-standard tokens Infinity/-Infinity/NaN for
    non-finite floats, which a strict JSON consumer rejects outright --
    estimate_nearest_depth_from_bbox() returns float("inf") whenever a view
    basis or bbox corner is unavailable, and that must never reach the
    sidecar as anything but the documented float | None contract."""
    import math
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _collect_view_scoped_link_proxies(doc, view, cfg, diag=None, view_id=None):
    """View-scoped LINK/DWG element collection, done ONCE per Stage A capture
    and shared by every consumer in export_color_id_buffer_view that needs to
    know what a linked document actually contributes to THIS view.

    Delegates entirely to revit/linked_documents.collect_all_linked_elements()
    -- the same view-scoped Revit 2024+ FilteredElementCollector(doc, view.Id,
    link_inst.Id) overload (or the legacy clip-volume fallback) the rest of the
    pipeline already relies on for LINK visibility, and the same authoritative
    collection_policy inclusion rules _model_categories_in_linked_doc() applies
    during category discovery.

    Returns ``(proxies, status)``. The proxy list is what near-face-W
    consumes; the LinkCollectionStatus is what any PRESENCE question must be
    asked of, and the two deliberately do not agree.

    The proxy list cannot answer presence. collect_all_linked_elements and
    the collectors beneath it are resilient by design: a link whose document
    fails to enumerate, a placement with no resolvable transform, and an
    element that raises mid-loop are logged and skipped rather than
    propagated, and -- separately -- an element whose bbox is missing,
    malformed, or untransformable is dropped after it has already been
    resolved as visible in this view. All of those come back as a quietly
    short list, indistinguishable by inspection from a complete one.

    So the status carries the presence answer directly
    (``status.link_presence``, recorded upstream of all bbox work) and a
    completeness flag (``status.rvt_complete``) for the failures that
    happen before presence can be recorded at all. Any caller reasoning about
    an ABSENCE has to read both. An empty presence set on a complete scan is
    a real answer: this view genuinely resolves no LINK elements. An empty
    one on an incomplete scan says nothing at all.
    """
    from .revit.linked_documents import collect_all_linked_elements, LinkCollectionStatus
    status = LinkCollectionStatus()
    try:
        proxies = list(collect_all_linked_elements(doc, view, cfg, diag=diag, status=status))
    except Exception as ex:
        status.mark_incomplete("view_scoped_link_collect", str(ex))
        if diag is not None:
            diag.warn(
                phase="collection",
                callsite="view_scoped_link_collect",
                message="Failed to collect view-scoped LINK elements: {0}".format(ex),
                view_id=view_id,
            )
        return [], status

    if not status.rvt_complete and diag is not None:
        diag.warn(
            phase="collection",
            callsite="view_scoped_link_collect",
            message="View-scoped LINK collection completed but is incomplete; it cannot "
                    "prove a category absent from this view: {0}".format(status.failures),
            view_id=view_id,
        )
    return proxies, status


def _near_face_w_category_name(elem):
    """Reader (raises on failure) for a host element's category name, for
    _gs_capture. Returns None when the element genuinely has no Category.

    NOTE (Stage A step 1, UNCONFIRMED): for a DWG ImportInstance this is the
    IMPORT INSTANCE's own Revit category -- typically the "Imports in
    Families"/"<import file name>.dwg" category Revit creates per imported
    file. Whether that, or the import SYMBOL (type) name reached via
    GetTypeId(), is the stable identifier across Revit versions has NOT been
    verified in this session; no Revit API call was run. The symbol name is
    deliberately NOT recorded here pending that decision -- see the Stage A
    step 1 RAISE in the PR description.
    """
    cat = getattr(elem, "Category", None)
    if cat is None:
        return None
    return getattr(cat, "Name", None)


def _near_face_w_import_symbol_name(doc, elem):
    """Reader (raises on failure) for a DWG import's SYMBOL (type) name.

    Recorded ALONGSIDE the ImportInstance's own category rather than instead
    of it (Greg, 2026-09-21): which of the two is stable across Revit versions
    is unverified, so both are captured and post decides. This is the same
    GetTypeId() -> Name read revit/linked_documents.py already uses to build a
    DWG proxy's source_label.

    UNCONFIRMED: no Revit API call was run this session. In particular, that
    the type element resolves and exposes Name for every import flavour
    (linked CAD vs imported CAD) is assumed, not verified.
    """
    type_id = elem.GetTypeId()
    if type_id is None:
        return None
    import_type = doc.GetElement(type_id)
    if import_type is None:
        return None
    return getattr(import_type, "Name", None)


def _near_face_w_view_specific(elem):
    """Reader (raises on failure) for whether an import is placed "this view
    only".

    Both passes now record a view-specific DWG, tagged rather than dropped
    (Greg, 2026-09-21), so this is the tag that lets post attribute it and
    lets the Step 3 annotation pass recognise what the model pass already
    claimed.
    """
    return bool(elem.ViewSpecific)


def _dwg_only_state(elem, reader, diag=None, callsite=None, view_id=None):
    """Three-valued wrapper for a field that exists only for a DWG import.

    Gated on the ELEMENT, not on the resolved source: source can itself be
    "unavailable" (no expansion record), and a field's applicability must not
    inherit another field's failure.
    """
    if not _is_import_instance(elem):
        return _gs_not_applicable("not a DWG ImportInstance")
    return _gs_capture(reader, diag=diag, callsite=callsite, view_id=view_id)


def _host_source_state(elem, elem_id_int, host_source_types):
    """Three-valued source for one entry in the near_face_w_map "host" bucket.

    The "host" bucket holds true HOST elements AND DWG ImportInstances --
    both are real elements of ``doc`` (see _split_expanded_elements). This
    resolves which, without ever defaulting:

    1. ``host_source_types`` is None -> "unavailable". The caller kept no
       expansion record, and an element id alone does not carry a source.
    2. The id is IN the map -> that source_type verbatim. The map is the
       authority: it is what expand_host_link_import_model_elements() read
       off the element or its LinkedElementProxy at collection time.
    3. The id is NOT in the map -> it is an id resolve_all() produced by
       expanding a Group's members or a FamilyInstance's sub-components,
       which _split_expanded_elements never saw. Those descend from a
       top-level entry, but NOT necessarily a HOST one: a DWG import placed
       inside a Group is reached as a group member, so "everything that is
       not in the map is HOST" would mislabel exactly the case this step
       exists to make visible. Decided from the element itself instead.
    """
    if host_source_types is None:
        return _gs_unavailable(
            "no expansion source record supplied to _collect_near_face_w_data; "
            "an element id alone does not carry HOST vs DWG"
        )
    if elem_id_int in host_source_types:
        return _gs_value(host_source_types[elem_id_int])
    return _gs_value("DWG" if _is_import_instance(elem) else "HOST")


def _collect_near_face_w_data(
    doc, view, raster, cfg, resolved_ids, link_category_color_map,
    diag=None, view_id=None, link_proxies=None, host_source_types=None,
):
    """Collect near-face-W (nearest projected depth) and a UV bbox footprint
    for every HOST and LINK element resolved by export_color_id_buffer_view,
    for the additive "near_face_w_map" sidecar section (Phase 1b).

    Purely a read -- never overrides graphics, never modifies
    color_assignment_map or link_category_color_map. HOST identity mirrors
    resolved_ids exactly (the same element set color_assignment_map keys
    off of). LINK identity is scoped to exactly link_category_color_map's
    underlying element set: categories that actually got colored (present
    in link_category_color_map), restricted to elements revit/linked_
    documents.collect_all_linked_elements() itself resolves as visible in
    THIS host view (the same view-scoped Revit 2024+ FilteredElementCollector
    (doc, view.Id, link_inst.Id) overload -- or the legacy clip-volume
    fallback -- the rest of the pipeline already relies on for LINK
    visibility). A document-wide, unscoped category scan would otherwise let
    an element hidden in this view (or simply out of the crop) compete as a
    candidate even though it painted zero TIFF pixels. There is no longer a
    hidden-placement exclusion to apply on top of that: Stage A hides no link
    instances at all.

    Returns {"host": {"<elem_id>": entry}, "link": {"<link_inst_id>:<link_elem_id>": entry}}
    where entry is {"bbox_corners_uv": [[u,v],...] | None, "near_face_w": float | None,
    "category": str | None}; LINK entries additionally carry "link_inst_id"/
    "link_elem_id" ints for the identity resolver's convenience. A bbox or
    view basis that cannot be resolved records near_face_w/bbox_corners_uv
    as None rather than omitting the element entirely -- CLAUDE.md's "no
    silent failure": every element in the resolved set gets an entry.

    ``link_proxies`` lets the caller hand in an already-collected view-scoped
    proxy list (export_color_id_buffer_view collects one per capture via
    _collect_view_scoped_link_proxies). Left None, this function collects its
    own -- the standalone behavior its own tests exercise.

    ``host_source_types`` (Stage A step 1) is _split_expanded_elements'
    source_out map, {top_level_element_id_int: source_type}. It is what makes
    a painted DWG ImportInstance IDENTIFIABLE in the record: DWG imports are
    real elements of ``doc`` and so have always been painted and recorded in
    the "host" bucket, indistinguishable from true HOST elements because
    source_type was dropped at the partition. HOST and LINK entries keep
    their existing keys and values unchanged; the distinction is carried by
    the NEW three-valued "source" key on host entries only.

    "source" is {"state": "value", "value": "HOST"|"DWG"} when it is known,
    and {"state": "unavailable", "reason": ...} when it is not -- never a
    guess and never a stand-in default, per the Stage A three-valued rule.
    Left None (the standalone/legacy call), every host entry records
    "unavailable" with that as the reason: this function cannot derive a top
    element's source from an element id alone.
    """
    from .revit.collection import resolve_element_bbox, project_bbox_uv_and_near_face_w

    vb = getattr(raster, "view_basis", None) if raster is not None else None

    host_out = {}
    for eid in resolved_ids:
        elem = doc.GetElement(eid)
        if elem is None:
            continue
        elem_id_int = eid.IntegerValue
        # Three-valued FIRST, then the pre-existing bare "category" key is
        # derived FROM it, so the two cannot disagree about what was read
        # (one read, one place). The legacy key keeps its exact meaning -- a
        # category name or None -- and gains nothing but the case where the
        # Name getter RAISES, which getattr(cat, "Name", None) does not catch
        # and which previously escaped this whole collection.
        category_state = _gs_capture(
            lambda e=elem: _near_face_w_category_name(e),
            diag=diag, callsite="near_face_w.host.category", view_id=view_id,
        )
        category_name = (
            category_state.get("value") if category_state.get("state") == _GS_VALUE
            else None
        )
        source_state = _host_source_state(elem, elem_id_int, host_source_types)
        import_symbol_state = _dwg_only_state(
            elem, lambda e=elem: _near_face_w_import_symbol_name(doc, e),
            diag=diag, callsite="near_face_w.host.import_symbol", view_id=view_id,
        )
        view_specific_state = _dwg_only_state(
            elem, lambda e=elem: _near_face_w_view_specific(e),
            diag=diag, callsite="near_face_w.host.view_specific", view_id=view_id,
        )
        bbox, _src = resolve_element_bbox(
            elem, view=None, diag=diag,
            context={"view_id": view_id, "elem_id": elem_id_int, "source_type": "HOST"},
        )
        near_face_w = None
        bbox_corners_uv = None
        if bbox is not None:
            # Computed together (not via a separate estimate_nearest_depth_
            # from_bbox() call) so near_face_w and bbox_corners_uv are always
            # derived from the identical transformed corner set -- see
            # project_bbox_uv_and_near_face_w()'s docstring for why calling
            # them separately can silently disagree on coordinate space for
            # a bbox with a non-identity Transform (e.g. a rotated instance).
            bbox_corners_uv, near_face_w = project_bbox_uv_and_near_face_w(
                bbox, vb, diag=diag, view_id=view_id, elem_id=elem_id_int,
            )
            near_face_w = _finite_or_none(near_face_w)
        elif diag is not None:
            diag.warn(
                phase="collection",
                callsite="near_face_w.host",
                message="No bbox resolvable; near_face_w/bbox_corners_uv recorded as None",
                view_id=view_id,
                elem_id=elem_id_int,
            )
        host_out[str(elem_id_int)] = {
            "bbox_corners_uv": bbox_corners_uv,
            "near_face_w": near_face_w,
            "category": category_name,
            # --- Stage A step 1, additive. Pre-existing keys above are
            # untouched; a consumer that does not know these exist reads the
            # same record it always did.
            "source": source_state,
            "category_state": category_state,
            # DWG-only; "not_applicable" for a true HOST element. Both of the
            # DWG's candidate identifiers are recorded because which one is
            # stable across Revit versions is unverified -- see
            # _near_face_w_import_symbol_name.
            "import_symbol_state": import_symbol_state,
            "view_specific_state": view_specific_state,
        }

    link_out = {}
    colored_cat_names = set(link_category_color_map.keys())
    if colored_cat_names:
        # NOTE: this is the same full, per-placement RVT link scan export_
        # color_id_buffer_view's own host_only_cfg deliberately skips
        # elsewhere in this function (see the comment above its
        # _expand_elements call) because LINK painting only needs category-
        # level filters, not individual proxies. Near-face-W identity has no
        # such shortcut -- it needs each element's own host-space bbox, and
        # only a view-scoped collector (this call, same one the rest of the
        # pipeline uses for LINK visibility) can guarantee a candidate
        # actually rendered in this view. Re-paying that scan here is a
        # deliberate, view-local cost for correctness, only when this view
        # actually has a colored LINK category to identify. The caller passes
        # its single per-capture scan in via link_proxies so it is paid once.
        if link_proxies is None:
            link_proxies, _status = _collect_view_scoped_link_proxies(
                doc, view, cfg, diag=diag, view_id=view_id,
            )

        for proxy in link_proxies:
            # DWG imports are explicitly out of scope for Phase 1b (see this
            # module's docstring); only real RVT LINK elements are candidates.
            if getattr(proxy, "source_type", None) != "LINK":
                continue
            cat = getattr(proxy, "Category", None)
            cat_name = getattr(cat, "Name", None) if cat is not None else None
            if cat_name not in colored_cat_names:
                continue  # this category's filter failed; no colored pixels exist to identify
            link_inst_id = getattr(proxy, "LinkInstanceId", None)
            link_elem_id = getattr(proxy, "Id", None)
            if link_inst_id is None or link_elem_id is None:
                continue
            link_inst_id_int = link_inst_id.IntegerValue
            link_elem_id_int = link_elem_id.IntegerValue

            # LinkedElementProxy.get_BoundingBox() already returns a host-
            # space bbox (built from the link transform at collection time,
            # see linked_documents.py's _transform_bbox_to_host) -- no
            # further link-space handling needed here, unlike a raw element
            # pulled straight from the linked document.
            bbox_host, _src = resolve_element_bbox(
                proxy, view=None, diag=diag,
                context={"view_id": view_id, "elem_id": link_elem_id_int, "source_type": "LINK"},
            )
            near_face_w = None
            bbox_corners_uv = None
            if bbox_host is not None:
                bbox_corners_uv, near_face_w = project_bbox_uv_and_near_face_w(
                    bbox_host, vb, diag=diag, view_id=view_id, elem_id=link_elem_id_int,
                )
                near_face_w = _finite_or_none(near_face_w)
            elif diag is not None:
                diag.warn(
                    phase="collection",
                    callsite="near_face_w.link",
                    message="No bbox resolvable; near_face_w/bbox_corners_uv recorded as None",
                    view_id=view_id,
                    elem_id=link_elem_id_int,
                )
            key = "{0}:{1}".format(link_inst_id_int, link_elem_id_int)
            link_out[key] = {
                "bbox_corners_uv": bbox_corners_uv,
                "near_face_w": near_face_w,
                "category": cat_name,
                "link_inst_id": link_inst_id_int,
                "link_elem_id": link_elem_id_int,
            }
    return {"host": host_out, "link": link_out}


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


# --- Per-view graphics-state record (additive Stage A sidecar section) ------
#
# Every field is THREE-VALUED: a real value, "not_applicable" (the setting
# does not exist for this view -- a section view has no underlay, a view with
# no template has no template id), or "unavailable" with the reason a read
# failed. The shape is what makes that structural rather than a convention:
# _gs_capture() is the only writer, and it cannot emit a value for a reader
# that raised. A failed read coerced to False/0 -- the exact silent coercion
# that made applied_smooth_edges's "unchanged" unreadable, and that
# orig_show_shadows's _MISSING sentinel exists to refuse -- is unreachable
# here by construction, not by remembering to check.

_GS_VALUE = "value"
_GS_NOT_APPLICABLE = "not_applicable"
_GS_UNAVAILABLE = "unavailable"

# Distinguishes "the host does not expose this member at all" from "the member
# read back as None/False". getattr(obj, name, None) collapses the two.
_GS_MISSING = object()

VIEW_GRAPHICS_STATE_SCHEMA = "vop.stage_a.view_graphics_state.v1"

# The ElementOnPhaseStatus members a PhaseFilter carries a presentation for,
# named rather than enumerated off the enum so a host missing one degrades
# that ONE status to "unavailable" instead of losing the whole record.
_PHASE_STATUS_NAMES = ("New", "Existing", "Demolished", "Temporary")


class _GsNotApplicable(object):
    """Returned BY a reader to mean "this view has no such setting".

    Distinct from raising, which means the setting exists but could not be
    read. _gs_capture() maps the two onto "not_applicable" and "unavailable".
    """

    def __init__(self, reason):
        self.reason = reason


def _gs_value(value):
    return {"state": _GS_VALUE, "value": value}


def _gs_not_applicable(reason):
    return {"state": _GS_NOT_APPLICABLE, "reason": str(reason)}


def _gs_unavailable(reason):
    return {"state": _GS_UNAVAILABLE, "reason": str(reason)}


def _gs_capture(reader, diag=None, callsite=None, view_id=None):
    """Run ``reader`` and wrap its outcome in the three-valued envelope."""
    try:
        value = reader()
    except Exception as ex:
        if diag is not None and callsite is not None:
            diag.warn(
                phase="color_id_buffer",
                callsite=callsite,
                message=str(ex),
                view_id=view_id,
            )
        return _gs_unavailable("{0}: {1}".format(type(ex).__name__, ex))
    if isinstance(value, _GsNotApplicable):
        return _gs_not_applicable(value.reason)
    return _gs_value(value)


def _gs_element_name(doc, element_id):
    """Reader (raises on failure) for an element's Name, for _gs_capture."""
    el = doc.GetElement(element_id)
    if el is None:
        return _GsNotApplicable("no element with this id in the document")
    name = getattr(el, "Name", _GS_MISSING)
    if name is _GS_MISSING:
        return _GsNotApplicable("element exposes no Name property")
    return name


def _gs_element_ref(doc, element_id, diag=None, callsite=None, view_id=None):
    """``{"id": int, "name": <three-valued>}`` for an ElementId."""
    return {
        "id": int(element_id.IntegerValue),
        "name": _gs_capture(
            lambda: _gs_element_name(doc, element_id),
            diag=diag, callsite=callsite, view_id=view_id,
        ),
    }


def _capture_view_graphics_state(doc, view, elements, diag=None, view_id=None):
    """Read-only record of the graphics state that governs THIS view.

    Captured BEFORE Stage A detaches the view template and before any
    suppression runs, so it records the state that actually governed what the
    view rendered -- template-applied values included -- rather than the
    instance-level residue the rest of this function works against once the
    template is detached. That ordering is the whole point of the record: it
    is evidence about the view as authored, not about Stage A's scratch state.

    Purely additive: nothing here mutates the document, and nothing here feeds
    the paint/export path.
    """
    from Autodesk.Revit.DB import BuiltInParameter, ElementId

    def cap(callsite, reader):
        return _gs_capture(reader, diag=diag, callsite=callsite, view_id=view_id)

    def _param_element_id(bip_name):
        bip = getattr(BuiltInParameter, bip_name, _GS_MISSING)
        if bip is _GS_MISSING:
            return _GsNotApplicable(
                "this Revit host exposes no BuiltInParameter.{0}".format(bip_name))
        param = view.get_Parameter(bip)
        if param is None:
            return _GsNotApplicable(
                "view exposes no {0} parameter".format(bip_name))
        eid = param.AsElementId()
        if eid is None or eid == ElementId.InvalidElementId:
            return _GsNotApplicable("parameter is unset on this view")
        return eid

    def _read_view_phase():
        eid = _param_element_id("VIEW_PHASE")
        if isinstance(eid, _GsNotApplicable):
            return eid
        return _gs_element_ref(
            doc, eid, diag=diag, callsite="graphics_state_view_phase", view_id=view_id)

    def _read_phase_filter():
        eid = _param_element_id("VIEW_PHASE_FILTER")
        if isinstance(eid, _GsNotApplicable):
            return eid
        return _gs_element_ref(
            doc, eid, diag=diag, callsite="graphics_state_phase_filter", view_id=view_id)

    def _read_phase_status_presentation():
        from Autodesk.Revit.DB import ElementOnPhaseStatus
        eid = _param_element_id("VIEW_PHASE_FILTER")
        if isinstance(eid, _GsNotApplicable):
            return eid
        pf = doc.GetElement(eid)
        if pf is None:
            return _GsNotApplicable("phase filter id does not resolve to an element")
        out = {}
        for status_name in _PHASE_STATUS_NAMES:
            def _read_one(status_name=status_name):
                status = getattr(ElementOnPhaseStatus, status_name, _GS_MISSING)
                if status is _GS_MISSING:
                    return _GsNotApplicable(
                        "ElementOnPhaseStatus has no member {0} on this Revit "
                        "host".format(status_name))
                return str(pf.GetPhaseStatusPresentation(status))
            out[status_name] = cap(
                "graphics_state_phase_status_presentation", _read_one)
        return out

    def _read_category_overrides():
        # TWO sources, because neither alone can explain the view.
        #
        # (a) The categories of the elements the pipeline collected. That is
        #     what is on screen -- but it is blind in exactly the case this
        #     record exists to explain. collect_view_elements() is
        #     view-scoped (FilteredElementCollector(doc, view.Id)), so Revit
        #     has already dropped every element of a hidden category before
        #     this function is handed the list. Derive "categories present"
        #     from it alone and a category hidden in the authored view is
        #     simply ABSENT, with no "hidden": true anywhere to say why --
        #     the record would be silent precisely where it is needed.
        #
        # (b) Every top-level category this view reports hidden, read off the
        #     view and independent of any element. This is the source that
        #     explains an absence.
        #
        # Each entry names the source(s) that put it there, so a reader can
        # tell "present and visible" from "hidden, nothing collected". The
        # document scan costs one pass over doc.Settings.Categories per view,
        # the same order as _hidden_category_state()'s existing pass.
        present = {}
        names = {}
        unreadable = 0
        for elem in (elements or []):
            try:
                cat = elem.Category
                if cat is None:
                    continue
                cat_id_int = int(cat.Id.IntegerValue)
                present[cat_id_int] = True
                names[cat_id_int] = getattr(cat, "Name", _GS_MISSING)
            except Exception:
                # Counted, then reported below and in the record -- a stale or
                # disposed element is not a reason to lose the whole section,
                # but it is not allowed to vanish silently either.
                unreadable += 1
        if unreadable and diag is not None:
            diag.warn(
                phase="color_id_buffer",
                callsite="graphics_state_category_overrides",
                message="{0} collected element(s) would not report a Category; their "
                        "categories are absent from the graphics-state record".format(
                            unreadable),
                view_id=view_id,
            )

        hidden_ids = set()
        scan_unreadable = 0
        scan_error = None
        try:
            doc_categories = list(doc.Settings.Categories)
        except Exception as ex:
            doc_categories = []
            scan_error = "{0}: {1}".format(type(ex).__name__, ex)
        for cat in doc_categories:
            try:
                cat_id = cat.Id
                if not view.GetCategoryHidden(cat_id):
                    continue
                cat_id_int = int(cat_id.IntegerValue)
                hidden_ids.add(cat_id_int)
                if cat_id_int not in names:
                    names[cat_id_int] = getattr(cat, "Name", _GS_MISSING)
            except Exception:
                scan_unreadable += 1
        if scan_unreadable and diag is not None:
            diag.warn(
                phase="color_id_buffer",
                callsite="graphics_state_category_overrides",
                message="{0} document categor(ies) would not report a hidden state; a "
                        "category hidden in this view may be missing from the "
                        "graphics-state record".format(scan_unreadable),
                view_id=view_id,
            )

        categories = {}
        for cat_id_int in sorted(set(present) | hidden_ids):
            raw_name = names.get(cat_id_int, _GS_MISSING)
            if cat_id_int in present and cat_id_int in hidden_ids:
                source = "collected_element+hidden_in_view"
            elif cat_id_int in present:
                source = "collected_element"
            else:
                source = "hidden_in_view"
            categories[str(cat_id_int)] = {
                "source": source,
                "name": (
                    _gs_not_applicable("category exposes no Name property")
                    if raw_name is _GS_MISSING else _gs_value(raw_name)),
                "halftone": cap(
                    "graphics_state_category_halftone",
                    lambda cid=cat_id_int: bool(
                        view.GetCategoryOverrides(ElementId(int(cid))).Halftone),
                ),
                "hidden": cap(
                    "graphics_state_category_hidden",
                    lambda cid=cat_id_int: bool(
                        view.GetCategoryHidden(ElementId(int(cid)))),
                ),
            }
        return {
            "basis": "categories of the elements the pipeline collected, UNION every "
                     "top-level category this view reports hidden; the second source "
                     "is what explains an absence, since a hidden category's elements "
                     "never reach the view-scoped collection",
            "elements_with_unreadable_category": unreadable,
            "categories_unreadable_in_document_scan": scan_unreadable,
            "document_categories_scanned": (
                _gs_unavailable(scan_error) if scan_error is not None
                else _gs_value(len(doc_categories))),
            "categories": categories,
        }

    def _read_view_filters():
        fids = list(view.GetFilters())
        if not fids:
            return _GsNotApplicable("no view filters are applied to this view")
        out = []
        for fid in fids:
            entry = _gs_element_ref(
                doc, fid, diag=diag, callsite="graphics_state_view_filters",
                view_id=view_id)
            entry["enabled"] = cap(
                "graphics_state_filter_enabled",
                lambda f=fid: bool(view.GetIsFilterEnabled(f)))
            entry["visible"] = cap(
                "graphics_state_filter_visibility",
                lambda f=fid: bool(view.GetFilterVisibility(f)))
            entry["halftone"] = cap(
                "graphics_state_filter_halftone",
                lambda f=fid: bool(view.GetFilterOverrides(f).Halftone))
            out.append(entry)
        return out

    def _read_underlay():
        from Autodesk.Revit.DB import ViewPlan
        if not isinstance(view, ViewPlan):
            return _GsNotApplicable(
                "underlay is a plan-view setting; this view is a {0}".format(
                    type(view).__name__))

        def _read_level(getter_name):
            getter = getattr(view, getter_name, _GS_MISSING)
            if getter is _GS_MISSING:
                return _GsNotApplicable(
                    "this Revit host exposes no View.{0}".format(getter_name))
            eid = getter()
            if eid is None or eid == ElementId.InvalidElementId:
                return _GsNotApplicable("no underlay level is set on this view")
            return _gs_element_ref(
                doc, eid, diag=diag, callsite="graphics_state_underlay",
                view_id=view_id)

        def _read_orientation():
            getter = getattr(view, "GetUnderlayOrientation", _GS_MISSING)
            if getter is _GS_MISSING:
                return _GsNotApplicable(
                    "this Revit host exposes no View.GetUnderlayOrientation")
            return str(getter())

        return {
            "base_level": cap(
                "graphics_state_underlay_base",
                lambda: _read_level("GetUnderlayBaseLevel")),
            "top_level": cap(
                "graphics_state_underlay_top",
                lambda: _read_level("GetUnderlayTopLevel")),
            # LookingUp vs LookingDown changes what the underlay actually
            # draws while base and top can be identical, so without this two
            # materially different views produce the same underlay record --
            # which defeats the point of recording the underlay at all.
            # Confirmed present on the target install by Run 1 (Revit 2025
            # 25.4.41.14); see tests/dynamo/PROBE_STAGE_A_COLOR_ID_ANOMALIES.md,
            # "Underlay / blend", the GetUnderlayBaseLevel/GetUnderlayTopLevel/
            # GetUnderlayOrientation item.
            "orientation": cap(
                "graphics_state_underlay_orientation", _read_orientation),
        }

    def _read_view_template():
        tid = view.ViewTemplateId
        if tid is None or tid == ElementId.InvalidElementId:
            return _GsNotApplicable("view has no view template applied")
        return _gs_element_ref(
            doc, tid, diag=diag, callsite="graphics_state_view_template",
            view_id=view_id)

    def _read_enum_property(name):
        value = getattr(view, name, _GS_MISSING)
        if value is _GS_MISSING:
            return _GsNotApplicable(
                "view exposes no {0} property on this Revit host".format(name))
        return str(value)

    def _read_link_ogs_halftone(link_id):
        getter = getattr(view, "GetLinkOverrides", _GS_MISSING)
        if getter is _GS_MISSING:
            return _GsNotApplicable(
                "this Revit host exposes no View.GetLinkOverrides "
                "(RevitLinkGraphicsSettings, Revit 2018+)")
        settings = getter(link_id)
        if settings is None:
            return _GsNotApplicable(
                "no RevitLinkGraphicsSettings for this link in this view")
        halftone = getattr(settings, "Halftone", _GS_MISSING)
        if halftone is _GS_MISSING:
            return _GsNotApplicable(
                "RevitLinkGraphicsSettings exposes no Halftone")
        return bool(halftone)

    def _read_link_instances():
        from Autodesk.Revit.DB import FilteredElementCollector, RevitLinkInstance
        links = list(FilteredElementCollector(doc, view.Id).OfClass(RevitLinkInstance))
        if not links:
            return _GsNotApplicable("no RevitLinkInstance is visible in this view")
        out = []
        for link in links:
            link_id = link.Id
            entry = _gs_element_ref(
                doc, link_id, diag=diag, callsite="graphics_state_link_instances",
                view_id=view_id)
            # Two different overrides, both named "halftone" in the Revit UI and
            # reported separately because they are set in different dialogs and
            # either one alone can halftone the link.
            entry["element_override_halftone"] = cap(
                "graphics_state_link_element_halftone",
                lambda lid=link_id: bool(view.GetElementOverrides(lid).Halftone))
            entry["link_override_halftone"] = cap(
                "graphics_state_link_override_halftone",
                lambda lid=link_id: _read_link_ogs_halftone(lid))
            # Recorded as a field rather than omitted so a reader can tell
            # "Revit has no such setting" from "this capture forgot to look".
            entry["underlay"] = _gs_not_applicable(
                "Revit exposes no per-link-instance underlay setting; underlay is a "
                "host plan-view property (see the record's own 'underlay' field)")
            out.append(entry)
        return out

    return {
        "schema": VIEW_GRAPHICS_STATE_SCHEMA,
        "captured_at": "before_view_template_detach_and_suppression",
        "view_phase": cap("graphics_state_view_phase", _read_view_phase),
        "phase_filter": cap("graphics_state_phase_filter", _read_phase_filter),
        "phase_status_presentation": cap(
            "graphics_state_phase_status_presentation",
            _read_phase_status_presentation),
        "category_overrides": cap(
            "graphics_state_category_overrides", _read_category_overrides),
        "view_filters": cap("graphics_state_view_filters", _read_view_filters),
        "underlay": cap("graphics_state_underlay", _read_underlay),
        "view_template_id": cap("graphics_state_view_template", _read_view_template),
        "detail_level": cap(
            "graphics_state_detail_level", lambda: _read_enum_property("DetailLevel")),
        "display_style": cap(
            "graphics_state_display_style", lambda: _read_enum_property("DisplayStyle")),
        "link_instances": cap("graphics_state_link_instances", _read_link_instances),
    }


# --- Neutral-phase-swap element-set audit -----------------------------------

# Cap on how many differing element ids the audit writes per side. The audit
# is opt-in and meant to be read, so the ids themselves matter -- but a view
# with a six-figure element count must not turn the sidecar into a dump.
PHASE_SWAP_AUDIT_MAX_IDS = 1000


def _element_id_ints(elements):
    """``(set_of_int_ids, unreadable_count)`` for a collection of elements."""
    ids = set()
    unreadable = 0
    for elem in (elements or []):
        try:
            ids.add(int(elem.Id.IntegerValue))
        except Exception:
            # Counted and reported by the caller, never dropped silently.
            unreadable += 1
    return ids, unreadable


def _build_phase_swap_audit(
    audit_enabled, swap_enabled, pipeline_elements, recollected_elements,
    recollection_error, raster_present, neutral_phase_applied, measurement_error,
    diag=None, view_id=None,
):
    """Three-valued record comparing the two candidate element sets.

    The comparison this exists for: the set the PIPELINE collected for this
    view (handed in as ``elements``, collected before Stage A touched
    anything) against the set a re-collection sees from inside the suppress
    transaction. Gating the neutral-phase swap off means the paint pass stops
    relying on the second one, so the difference between them is the thing
    that has to be measured on a real view before the reliance is dropped for
    good -- not argued about.
    """
    if not audit_enabled:
        return _gs_not_applicable("color_id_phase_swap_audit=False")
    if not raster_present:
        return _gs_unavailable(
            "no raster was provided, so the in-transaction re-collection could "
            "not be run and there is no second set to compare against")
    if not neutral_phase_applied:
        # The measurement that matters is what the NEUTRAL phase state reveals.
        # A collection taken under the view's authored phase filter cannot see
        # the demolished/temporary elements that filter hides, so its diff is
        # not the diff this audit names -- and a zero would be read as "the
        # swap changes nothing", the very conclusion under test. Refuse it.
        return _gs_unavailable(
            "the neutral phase state was never applied, so the comparison "
            "would be blind to exactly the phase-hidden elements it exists to "
            "count: {0}".format(measurement_error or "reason not recorded"))
    if recollected_elements is None:
        return _gs_unavailable(
            "the in-transaction re-collection failed: {0}".format(
                recollection_error or "reason not recorded"))

    pipeline_ids, pipeline_unreadable = _element_id_ints(pipeline_elements)
    recollected_ids, recollected_unreadable = _element_id_ints(recollected_elements)
    only_recollected = sorted(recollected_ids - pipeline_ids)
    only_pipeline = sorted(pipeline_ids - recollected_ids)

    value = {
        "neutral_phase_swap_enabled": bool(swap_enabled),
        # Always "neutral_phase_filter" when a value exists at all -- the guard
        # above is what makes that true, rather than a hope.
        "measured_under": "neutral_phase_filter",
        "element_set_used_for_paint": (
            "in_transaction_recollection" if swap_enabled else "pipeline_collection"),
        "pipeline_collection_count": len(pipeline_ids),
        "in_transaction_recollection_count": len(recollected_ids),
        "common_count": len(pipeline_ids & recollected_ids),
        "only_in_recollection_count": len(only_recollected),
        "only_in_pipeline_collection_count": len(only_pipeline),
        "only_in_recollection_ids": only_recollected[:PHASE_SWAP_AUDIT_MAX_IDS],
        "only_in_pipeline_collection_ids": only_pipeline[:PHASE_SWAP_AUDIT_MAX_IDS],
        "only_in_recollection_ids_truncated": (
            len(only_recollected) > PHASE_SWAP_AUDIT_MAX_IDS),
        "only_in_pipeline_collection_ids_truncated": (
            len(only_pipeline) > PHASE_SWAP_AUDIT_MAX_IDS),
        "pipeline_elements_with_unreadable_id": pipeline_unreadable,
        "recollected_elements_with_unreadable_id": recollected_unreadable,
    }
    if diag is not None:
        # Reported, not just written to the sidecar: a run with the audit on is
        # a run someone is watching the log of. info() where available so a
        # zero-difference audit does not cry wolf; warn() is the fallback for
        # diagnostics shims that only implement warn/error.
        report = (
            "phase-swap element-set audit (swap={0}): pipeline={1}, "
            "re-collected={2}, common={3}, only-in-re-collection={4}, "
            "only-in-pipeline={5}".format(
                bool(swap_enabled), value["pipeline_collection_count"],
                value["in_transaction_recollection_count"], value["common_count"],
                value["only_in_recollection_count"],
                value["only_in_pipeline_collection_count"])
        )
        notify = getattr(diag, "info", None) or diag.warn
        notify(
            phase="color_id_buffer",
            callsite="phase_swap_element_set_audit",
            message=report,
            view_id=view_id,
        )
    return _gs_value(value)


# TIFF tag numbers for the two fields this module needs, and the scalar
# field types they are allowed to carry (BYTE/SHORT/LONG). Anything else is
# refused rather than guessed at -- a misread dimension is worse than an
# honest read failure, because it would silently pass or fail the export
# dimension check below.
_TIFF_TAG_IMAGE_WIDTH = 256
_TIFF_TAG_IMAGE_LENGTH = 257
_TIFF_SCALAR_FORMATS = {1: "B", 3: "H", 4: "I"}


def read_image_dimensions(path):
    """Return ``(width, height)`` of a classic TIFF from its header alone.

    Deliberately NOT Pillow. Every existing dimension reader in this repo
    (the Stage A probes) goes through ``PIL.Image.open``, and the captures
    that most need measuring are exactly the ones Pillow refuses: its
    ~179 Mpx decompression-bomb guard rejected the largest exports in the
    2026-09-16 runs (see tests/dynamo/RUN1_2026-09-16_drift_table.md) and
    the callers recorded ``dimensions_px: null``, which is how an
    over-limit export stayed invisible. Pillow is also not present
    in the Dynamo CPython3 runtime this code actually ships into.

    Parsing the header instead reads ~8 bytes plus one IFD and never touches
    the pixel data, so it costs the same on a 300 MB export as on a thumbnail
    and cannot be defeated by a size guard. Raises on anything it cannot read
    exactly -- the caller records that as ``read_failed`` rather than
    treating an unknown size as a pass.
    """
    with open(path, "rb") as fh:
        header = fh.read(8)
        if len(header) < 8:
            raise ValueError(
                "'{0}' is {1} bytes; too short to hold a TIFF header".format(
                    path, len(header)))
        order = header[0:2]
        if order == b"II":
            endian = "<"
        elif order == b"MM":
            endian = ">"
        else:
            raise ValueError(
                "'{0}' does not start with a TIFF byte-order mark (II/MM)".format(path))
        magic, ifd_offset = struct.unpack(endian + "HI", header[2:8])
        if magic == 43:
            raise ValueError(
                "'{0}' is BigTIFF (magic 43); this reader handles classic TIFF "
                "only. A Stage A export capped at {1} px per axis stays far "
                "under classic TIFF's 4 GB limit, so BigTIFF here means the "
                "export was not produced the way this module expects".format(
                    path, MAX_STAGE_A_AXIS_PX))
        if magic != 42:
            raise ValueError(
                "'{0}' has TIFF magic {1}, expected 42".format(path, magic))
        fh.seek(ifd_offset)
        raw_count = fh.read(2)
        if len(raw_count) < 2:
            raise ValueError("'{0}' has a truncated IFD entry count".format(path))
        (entry_count,) = struct.unpack(endian + "H", raw_count)
        entries = fh.read(entry_count * 12)
        if len(entries) < entry_count * 12:
            raise ValueError(
                "'{0}' declares {1} IFD entries but the file ends early".format(
                    path, entry_count))

    width = None
    height = None
    for i in range(entry_count):
        off = i * 12
        tag, field_type, field_count = struct.unpack(endian + "HHI", entries[off:off + 8])
        if tag not in (_TIFF_TAG_IMAGE_WIDTH, _TIFF_TAG_IMAGE_LENGTH):
            continue
        fmt = _TIFF_SCALAR_FORMATS.get(field_type)
        if fmt is None or field_count != 1:
            raise ValueError(
                "'{0}' stores TIFF tag {1} as type {2} count {3}; expected a single "
                "BYTE/SHORT/LONG".format(path, tag, field_type, field_count))
        size = struct.calcsize(fmt)
        # A value of 4 bytes or fewer lives inline in the value/offset field,
        # left-justified (TIFF6 sec. 2) -- so it starts at byte 0 of the
        # field under both byte orders.
        (value,) = struct.unpack(endian + fmt, entries[off + 8:off + 8 + size])
        if tag == _TIFF_TAG_IMAGE_WIDTH:
            width = int(value)
        else:
            height = int(value)

    if width is None or height is None:
        raise ValueError(
            "'{0}' first IFD carries no ImageWidth/ImageLength tag".format(path))
    if width <= 0 or height <= 0:
        raise ValueError(
            "'{0}' reports non-positive dimensions {1}x{2}".format(path, width, height))
    return width, height


# Sidecars written before "read_failed" existed record a failed AA read as
# "unchanged" -- see the writer below for why that string could never mean
# what it says. Readers must not treat those captures as clean.
LEGACY_UNKNOWN_SMOOTH_EDGES = "unchanged"

# Absolute lower bound for PixelSize backoff, used when Revit rejects a
# value outright and as the dimension-mismatch floor only when the view's
# own grid extent is unknown. It is a "this is not a pixel count" guard, not
# a useful resolution -- the grid extent is what makes the mismatch floor
# meaningful.
_PIXEL_SIZE_BACKOFF_FLOOR = 16
# Re-exports attempted after a dimension mismatch, on top of the first
# export. Three total exports of a large view is already expensive; a fourth
# halving has never turned a mismatch into a pass in any observed run.
MAX_MISMATCH_RETRIES = 2


def normalize_applied_smooth_edges(value):
    """Map a sidecar's applied_smooth_edges onto its true meaning.

    Exists so the decode tool, the thinrunner summary and the probes cannot
    each decide separately what a legacy "unchanged" meant. Only False is a
    confirmed anti-aliasing-off capture; everything else is returned as-is
    except "unchanged", which becomes "read_failed".
    """
    if value == LEGACY_UNKNOWN_SMOOTH_EDGES:
        return "read_failed"
    return value


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
    floor = _PIXEL_SIZE_BACKOFF_FLOOR
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


def _fit_direction(name):
    """Map cfg.color_id_buffer_fit_direction onto Revit's FitDirectionType.

    Unknown values are refused rather than defaulted: silently exporting along
    the other axis would change the output size of every view, and a typo in a
    config is not a reason to do that.
    """
    from Autodesk.Revit.DB import FitDirectionType
    key = str(name or "horizontal").strip().lower()
    if key == "horizontal":
        return FitDirectionType.Horizontal
    if key == "vertical":
        return FitDirectionType.Vertical
    raise ValueError(
        "color_id_buffer_fit_direction must be 'horizontal' or 'vertical', "
        "got {0!r}".format(name))


def _export_one_tiff(doc, view, out_dir, output_path, pixel_size, diag=None,
                     view_id=None, fit_direction="horizontal"):
    """Run exactly one ExportImage and move its TIFF to ``output_path``.

    Returns the PixelSize Revit actually accepted (see
    ``_set_pixel_size_with_backoff``), which may be lower than requested.
    """
    from Autodesk.Revit.DB import (
        ImageExportOptions, ExportRange, ZoomFitType, FitDirectionType, ElementId,
        ImageFileType,
    )
    import System.Collections.Generic as SCG
    before = set(os.listdir(out_dir)) if os.path.isdir(out_dir) else set()
    ids = SCG.List[ElementId]()
    ids.Add(view.Id)
    opts = ImageExportOptions()
    opts.ExportRange = ExportRange.SetOfViews
    opts.SetViewsAndSheets(ids)
    opts.ZoomType = ZoomFitType.FitToPage
    # PixelSize sets the axis fitted here; the other one is derived from the
    # view's extents and is bounded by nothing Revit will tell us about in
    # advance. Horizontal is the shipped default, so unless a caller asks
    # otherwise this is the line it has always been -- and the caller's
    # two-axis cap plus the post-export check below are what bound the
    # derived axis.
    opts.FitDirection = _fit_direction(fit_direction)
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
    return actual_pixel_size


def _export_tiff(doc, view, output_path, pixel_size, diag=None, view_id=None,
                 fit_direction="horizontal", max_axis_px=MAX_STAGE_A_AXIS_PX,
                 grid_axis_px=None, max_mismatch_retries=MAX_MISMATCH_RETRIES):
    """Export the view, then MEASURE the file and refuse to trust the request.

    Everything upstream of this function -- the DPI math, the two-axis cap,
    even Revit's own PixelSize backoff -- is a prediction about the image
    FitToPage will produce. Nothing before now has looked at the image. The
    2026-09-16 runs found views whose derived axis landed 10028 px tall from
    an 8695 px request, drifting, with the pipeline reporting success, so a
    prediction is exactly what must not be recorded as a result.

    The export passes only when the axis PixelSize set came back within 1 px
    of what was asked AND neither axis exceeds ``max_axis_px``. A mismatch is
    routed into the same halving backoff Revit's PixelSize rejection uses:
    halve, re-export, re-measure.

    That backoff is bounded on both ends, because an unbounded one is its own
    failure mode: halving a 10000 px request to a hard floor of 16 is ten
    full exports of a large view, and the last several produce an image too
    small to carry the view's content at any useful density. So at most
    ``max_mismatch_retries`` re-exports are attempted, and no request goes
    below the floor the caller supplies. Hitting either bound reports the
    view as a mismatch and the caller fails it. The export never continues
    silently and never grinds.

    THE FLOOR IS NO LONGER THE CELL GRID. It was, on the reasoning that an
    export narrower than the grid cannot resolve the grid it fills; the
    capture does not fill a grid any more, so its production caller now
    passes the capture's own 64 px minimum. The parameter keeps its name for
    the probe contract that calls this directly.

    Note ``max_axis_px`` is the VERIFICATION ceiling and stays at the
    measured limit even when a caller raises the sizing cap: a deliberately
    over-cap request is precisely the case this check has to catch.

    Args:
        grid_axis_px: the backoff floor along the fitted axis. Historically
            the raster's cell count, hence the name; production now passes
            the capture's own minimum pixel size. None or 0 falls back to
            ``_PIXEL_SIZE_BACKOFF_FLOOR`` -- an absolute lower bound, not a
            meaningful one.

    Returns ``(output_path, actual_pixel_size, dim_report)``.
    """
    out_dir = os.path.dirname(output_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir)

    requested_axis = "height" if str(fit_direction).strip().lower() == "vertical" else "width"
    candidate = max(1, int(pixel_size))
    if grid_axis_px:
        floor = max(1, int(grid_axis_px))
    else:
        floor = _PIXEL_SIZE_BACKOFF_FLOOR
    max_retries = max(0, int(max_mismatch_retries))
    attempts = []

    while True:
        actual_pixel_size = _export_one_tiff(
            doc, view, out_dir, output_path, candidate, diag=diag,
            view_id=view_id, fit_direction=fit_direction,
        )

        actual_w = None
        actual_h = None
        dim_read_error = None
        try:
            actual_w, actual_h = read_image_dimensions(output_path)
        except Exception as ex:
            dim_read_error = "{0}: {1}".format(type(ex).__name__, ex)

        if dim_read_error is not None:
            # Not a pass. The exported file may be within the limit or far
            # over it; this run simply does not know, and says so.
            dim_check = "read_failed"
        else:
            on_axis = actual_w if requested_axis == "width" else actual_h
            axis_ok = abs(int(on_axis) - int(actual_pixel_size)) <= 1
            cap_ok = max(int(actual_w), int(actual_h)) <= int(max_axis_px)
            dim_check = "pass" if (axis_ok and cap_ok) else "mismatch"

        attempts.append({
            "requested_px": int(candidate),
            "accepted_px": int(actual_pixel_size),
            "actual_w": actual_w,
            "actual_h": actual_h,
            "dim_check": dim_check,
            "dim_read_error": dim_read_error,
        })

        report = {
            "requested_axis": requested_axis,
            "actual_w": actual_w,
            "actual_h": actual_h,
            "dim_check": dim_check,
            "dim_read_error": dim_read_error,
            "dim_check_ceiling_px": int(max_axis_px),
            "attempts": attempts,
        }

        if dim_check == "read_failed":
            if diag is not None:
                diag.warn(
                    phase="color_id_buffer",
                    callsite="export_dim_check",
                    message="could not read the exported image's dimensions ({0}); "
                            "this capture is NOT confirmed to be within the {1} px "
                            "per-axis limit".format(dim_read_error, int(max_axis_px)),
                    view_id=view_id,
                )
            return output_path, actual_pixel_size, report

        if dim_check == "pass":
            return output_path, actual_pixel_size, report

        if diag is not None:
            diag.warn(
                phase="color_id_buffer",
                callsite="export_dim_check",
                message="exported {0}x{1} for a {2}-axis request of {3} px (limit {4} px "
                        "per axis); halving the request and re-exporting".format(
                            actual_w, actual_h, requested_axis, int(actual_pixel_size),
                            int(max_axis_px)),
                view_id=view_id,
            )

        # Halve what Revit ACCEPTED, not what was asked for. _set_pixel_
        # size_with_backoff may already have reduced the request -- a
        # 10000 asked for and accepted at 5000 -- and halving the original
        # would then ask for 5000 again: the identical export, one of two
        # retries spent making no progress.
        next_candidate = int(actual_pixel_size) // 2
        stop_reason = None
        if len(attempts) > max_retries:
            stop_reason = "retry_limit"
        elif next_candidate < floor:
            # Deliberately not clamped up to the floor and retried: the point
            # of the floor is that a request under the grid's own cell count
            # cannot produce a usable capture, so there is nothing below here
            # worth spending an export on.
            stop_reason = "grid_floor"
        if stop_reason is not None:
            if diag is not None:
                diag.error(
                    phase="color_id_buffer",
                    callsite="export_dim_check",
                    message="exported dimensions still disagree with the request after "
                            "{0} attempt(s) (stopped on {1}: retry limit {2}, floor {3} "
                            "px); last export was {4}x{5}".format(
                                len(attempts), stop_reason, max_retries, floor,
                                actual_w, actual_h),
                    view_id=view_id,
                )
            report["backoff_exhausted"] = True
            report["backoff_stop_reason"] = stop_reason
            return output_path, actual_pixel_size, report

        candidate = next_candidate


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
    export_dpi = float(getattr(cfg, "color_id_buffer_export_dpi", DEFAULT_COLOR_ID_EXPORT_DPI))
    # Normalized here, once, so the value handed to Revit and the value written
    # into the sidecar cannot disagree. A cfg without the field is the shipped
    # behaviour.
    fit_direction = str(
        getattr(cfg, "color_id_buffer_fit_direction", "horizontal") or "horizontal"
    ).strip().lower()
    # ------------------------------------------------------------------
    # Stage A step 2: the export is sized from FRAME B and export_dpi only.
    #
    # It used to be sized from raster.W * raster.cell_size_ft -- the analysis
    # grid's own rectangle, which is the frame rounded UP to whole cells. That
    # coupled the captured image to a setting that has nothing to do with it:
    # the same view at a different cell size produced a different export.
    # resolution_contract.effective_export_dpi() already said the grid was the
    # wrong denominator for the inverse mapping; this makes the forward one
    # agree, and the arithmetic is CALLED rather than restated here so the two
    # cannot drift (the defect class CLAUDE.md records twice over).
    #
    # WHAT MOVES, AND IT IS VISIBLE IN EVERY CAPTURE WITH A NARROWED CROP.
    # Before, the fitted axis was handed B's pixel count while the view was
    # cropped to the narrower A, so Revit fitted A into B's pixels and the
    # capture came back over-resolved by B/A. Now feet-per-pixel is fixed by B
    # and the dpi, and A is rendered AT that fpp -- so a view whose model crop
    # is half its annotation frame exports about half as wide as it did, at
    # the dpi that was actually requested. requested vs achieved dpi, px and
    # fpp are all recorded below.
    # TEST-ONLY. A caller may raise the SIZING cap to prove the post-export
    # check fires (see _export_tiff: the verification ceiling stays at the
    # measured limit regardless). None/0/absent means the shipped limit, which
    # is what every production run uses.
    #
    # Resolved and announced BEFORE the frame branch below, not inside it: the
    # override is a property of the config, not of whether this view has a
    # usable frame. It was briefly inside, where a view with no raster went
    # silently uncapped-by-request -- caught by
    # tests/test_stage_a_export_dimension_contract.py, which runs exactly that
    # case.
    cap_axis_px = getattr(cfg, "color_id_buffer_cap_axis_px", None) or MAX_STAGE_A_AXIS_PX
    if cap_axis_px > MAX_STAGE_A_AXIS_PX and diag is not None:
        diag.warn(
            phase="color_id_buffer",
            callsite="pixel_size",
            message="color_id_buffer_cap_axis_px is {0}, above the {1} px verification "
                    "ceiling: this is a test-only override and every export it lets "
                    "through will be rejected by the post-export dimension check and "
                    "driven into the mismatch backoff".format(
                        cap_axis_px, MAX_STAGE_A_AXIS_PX),
            view_id=view_id,
        )

    geom = None
    frame_uv = None
    frame_source = "unavailable"
    if raster is not None and getattr(raster, "bounds_xy", None) is not None:
        # FRAME B IS THE ANNOTATION FRAME AS COMPUTED, not as the cap envelope
        # left it. view_basis clips the annotation-expanded bounds to a sheet
        # envelope and re-centres the remainder on the MODEL bounds, so under
        # that envelope raster.bounds_xy is no longer the annotation extent --
        # it is a model-centred window. Capturing against it puts annotation
        # content outside the image on the very views that were expanded to
        # hold it.
        #
        # raster.bounds_xy is still what the analysis grid uses and what the
        # geometry path renders into; only the capture reads the uncapped
        # rectangle. Absent (None) means no annotation expansion applied, so
        # bounds_xy already IS the frame.
        _b = getattr(raster, "anno_frame_bounds", None) or raster.bounds_xy
        frame_uv = (float(_b.xmin), float(_b.ymin), float(_b.xmax), float(_b.ymax))
        frame_source = ("anno_uncapped"
                        if getattr(raster, "anno_frame_bounds", None) is not None
                        else "raster_bounds")
        try:
            # compute_model_crop is pure, and is resolved here as well as at
            # the crop-application site below for the same reason the shipped
            # code resolved it twice: it costs nothing and keeps the sizing
            # honest about what will actually be rendered. The SNAPPED result
            # computed here is what gets applied, so the two cannot disagree.
            # Intersected against the FRAME, not against raster.bounds_xy.
            # compute_model_crop clamps A into whatever rectangle it is given,
            # and handing it the capped window while B is the uncapped frame
            # would narrow A to the window -- reintroducing the re-centred
            # rectangle through the back door on exactly the capped views this
            # is meant to fix.
            from .core.math_utils import Bounds2D as _Bounds2D_frame
            _frame_bounds = _Bounds2D_frame(*frame_uv)
            _render_bounds, _unused_offset = compute_model_crop(
                getattr(raster, "model_clip_bounds", None), _frame_bounds
            )
            crop_uv = (float(_render_bounds.xmin), float(_render_bounds.ymin),
                       float(_render_bounds.xmax), float(_render_bounds.ymax))
        except Exception as ex:
            crop_uv = None
            if diag is not None:
                diag.warn(
                    phase="color_id_buffer",
                    callsite="pixel_size_crop",
                    message="could not resolve the model crop from the raster ({0}); "
                            "sizing the export from the frame alone".format(ex),
                    view_id=view_id,
                )
        try:
            geom = frame_export_geometry(
                frame_uv, crop_uv, scale, export_dpi,
                fit_direction=fit_direction, max_axis_px=cap_axis_px,
            )
        except ValueError as ex:
            # A degenerate frame cannot be sized. Refusing beats substituting a
            # rectangle that would be recorded as if it were the view's.
            geom = None
            if diag is not None:
                diag.warn(
                    phase="color_id_buffer",
                    callsite="pixel_size",
                    message="frame bounds unusable for export sizing ({0}); falling back "
                            "to the 1 paper-inch minimum (export resolution will be far "
                            "below the requested DPI)".format(ex),
                    view_id=view_id,
                )
    elif diag is not None:
        diag.warn(
            phase="color_id_buffer",
            callsite="pixel_size",
            message="raster/raster.bounds_xy not provided; falling back to 1 paper-inch "
                    "width for pixel sizing (export resolution will be far below the "
                    "requested DPI)",
            view_id=view_id,
        )

    if geom is not None:
        # NO max(64, ...) HERE. The floor is enforced inside
        # frame_export_geometry, which re-derives feet-per-pixel and rebuilds
        # the whole lattice around it. Raising the number here instead left
        # Revit rendering at one fpp while the sidecar's crop_px,
        # achieved_fpp_ft and crop_offset_px described another -- and the
        # dimension check passed, because the raised number is exactly what
        # was asked for. A 1 ft crop inside a 100 ft frame put the recorded
        # fpp out by 3.2x with every check green.
        pixel_size = int(geom["requested_px"])
        requested_axis = geom["requested_axis"]
        # Paper extents of the FRAME. These sidecar keys keep their documented
        # meaning -- "the view's real paper extent along requested_axis" -- and
        # in fact now match it: the shipped values came from the grid and so
        # overstated the frame by up to one cell.
        paper_width_in = geom["frame_extent_ft"][0] * 12.0 / max(scale, 1.0e-6)
        paper_height_in = geom["frame_extent_ft"][1] * 12.0 / max(scale, 1.0e-6)
        paper_fit_in = paper_height_in if fit_direction == "vertical" else paper_width_in
        # Shaped like cap_axes' return so the sidecar block below is unchanged.
        # predicted_derived_px is now EXACT rather than an aspect-ratio guess:
        # both axes are whole pixel counts of one snapped rectangle.
        cap = {
            "max_axis_px": geom["max_axis_px"],
            "pre_cap_px": geom["pre_cap_px"],
            "pre_cap_derived_px": geom["pre_cap_derived_px"],
            "accepted_px": geom["requested_px"],
            "accepted_derived_px": geom["predicted_derived_px"],
            "cap_applied": geom["cap_applied"],
            "scale_factor": geom["achieved_fpp_ft"] and (
                geom["requested_fpp_ft"] / geom["achieved_fpp_ft"]),
        }
        if geom["floor_applied_to_crop"] and diag is not None:
            diag.warn(
                phase="color_id_buffer",
                callsite="pixel_size",
                message="the rendered crop fell below the {0} px floor on the {1} axis, "
                        "so feet-per-pixel was re-derived from the crop and the whole "
                        "lattice rebuilt around it: this capture is ABOVE the requested "
                        "DPI at {2:.2f} (requested {3:.2f}), and the frame is {4}x{5} "
                        "px".format(geom["min_axis_px"], requested_axis,
                                    geom["achieved_export_dpi"],
                                    geom["requested_export_dpi"],
                                    geom["frame_px"][0], geom["frame_px"][1]),
                view_id=view_id,
            )
        if (not geom["floor_applied_to_crop"]
                and geom["requested_px"] < geom["min_axis_px"]
                and diag is not None):
            # The ceiling refused the floor. Said out loud rather than left
            # to be inferred from two numbers in the sidecar.
            diag.warn(
                phase="color_id_buffer",
                callsite="pixel_size",
                message="the rendered crop is {0} px on the {1} axis, below the {2} px "
                        "floor, and the floor could NOT be applied: raising resolution "
                        "far enough would push the frame past the {3} px ceiling. The "
                        "ceiling is a measured limit and wins; this capture is small by "
                        "design".format(geom["requested_px"], requested_axis,
                                        geom["min_axis_px"], geom["max_axis_px"]),
                view_id=view_id,
            )
        if geom["cap_applied"] and diag is not None:
            diag.warn(
                phase="color_id_buffer",
                callsite="pixel_size",
                message="Stage A export capped so BOTH axes of the annotation frame stay "
                        "within {0} px: the frame is UNCHANGED and the resolution drops "
                        "from {1:.2f} to {2:.2f} dpi ({3:.6g} to {4:.6g} ft/px); the frame "
                        "is {5}x{6} px and the rendered crop {7}x{8} px".format(
                            geom["max_axis_px"], geom["requested_export_dpi"],
                            geom["achieved_export_dpi"], geom["requested_fpp_ft"],
                            geom["achieved_fpp_ft"], geom["frame_px"][0],
                            geom["frame_px"][1], geom["crop_px"][0], geom["crop_px"][1]),
                view_id=view_id,
            )
    else:
        # No usable frame. Keep the shipped 1 paper-inch fallback rather than
        # inventing a frame; the diagnostics above say which branch got here.
        paper_width_in = 1.0
        paper_height_in = 1.0
        paper_fit_in = 1.0
        requested_axis = "height" if fit_direction == "vertical" else "width"
        pre_cap_px = max(64, int(round(export_dpi * paper_fit_in)))
        cap = cap_axes(pre_cap_px, pre_cap_px, cap_axis_px)
        pixel_size = max(64, cap["accepted_px"])

    # The floor on the dimension-mismatch backoff.
    #
    # NOT THE CELL GRID. It used to be the raster's cell count along the
    # fitted axis, on the reasoning that "an export narrower than the grid it
    # feeds cannot resolve that grid". That reasoning is void: the capture no
    # longer feeds the grid. The analysis grid is derived from the TIFF and
    # the view scale AFTER the fact, so a cell size measured in model units
    # has no bearing on what the capture has to be -- which is the last place
    # in this path where one still did.
    #
    # A floor is still wanted, to stop the backoff halving its way down to a
    # useless image, so it is the capture's OWN minimum -- the same 64 px
    # floor the sizing path applies -- rather than a number borrowed from the
    # analysis resolution. _PIXEL_SIZE_BACKOFF_FLOOR (16) remains the absolute
    # bound for a capture with no frame, where 64 would be asserting something
    # about a view this code could not size.
    backoff_floor_px = (
        int(geom["min_axis_px"]) if geom is not None else _PIXEL_SIZE_BACKOFF_FLOOR)

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

    # The per-view graphics-state record is read HERE, before the detach below
    # and before any suppression, because it is evidence about the view as
    # authored -- template-applied phase filter, category overrides, filters,
    # underlay and display settings included. Read it after the detach and it
    # would describe Stage A's own scratch state instead. Purely additive: it
    # feeds one sidecar key and nothing else.
    try:
        view_graphics_state = _capture_view_graphics_state(
            doc, view, elements, diag=diag, view_id=view_id)
    except Exception as ex:
        # A record that could not be built at all is itself three-valued
        # rather than absent -- a missing key would be indistinguishable from
        # a capture written before this section existed.
        view_graphics_state = _gs_unavailable(
            "{0}: {1}".format(type(ex).__name__, ex))
        view_graphics_state["schema"] = VIEW_GRAPHICS_STATE_SCHEMA
        if diag is not None:
            diag.warn(
                phase="color_id_buffer",
                callsite="capture_view_graphics_state",
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
    # The exception TYPE when the read itself fails, which is the difference
    # between "AA was already off, nothing to do" and "this capture never
    # found out whether AA was on". Both used to be recorded as
    # applied_smooth_edges = "unchanged" -- and because
    # bool(getattr(dm, "SmoothEdges", None)) can never return None, a failed
    # read was in fact the ONLY way "unchanged" was ever written. A reader
    # (tools/decode_stage_a_color_id._capture_reliability) could not tell the
    # two apart, so a view whose AA state was unknown was reported exactly
    # like one that needed no change.
    smooth_edges_read_error = None
    # No getattr default here. bool(getattr(dm, "SmoothEdges", None)) reads a
    # host that does not expose the property at all as False -- "AA is
    # already off, nothing to do" -- which is the same silent coercion the
    # ShowShadows capture below already refuses via its own sentinel. An
    # absent property means this capture does not know the view's AA state,
    # and an unknown state is not an off state.
    _MISSING_SMOOTH_EDGES = object()
    try:
        _dm = view.GetViewDisplayModel()
        try:
            _raw_smooth_edges = getattr(_dm, "SmoothEdges", _MISSING_SMOOTH_EDGES)
            if _raw_smooth_edges is _MISSING_SMOOTH_EDGES:
                # getattr without a default would have raised exactly this.
                smooth_edges_read_error = "AttributeError"
                if diag is not None:
                    diag.warn(
                        phase="color_id_buffer",
                        callsite="smooth_edges_capture",
                        message="ViewDisplayModel has no SmoothEdges attribute on this "
                                "Revit host; anti-aliasing cannot be confirmed off and "
                                "decoded edges may be blended",
                        view_id=view_id,
                    )
            else:
                orig_smooth_edges = bool(_raw_smooth_edges)
        finally:
            try:
                _dm.Dispose()
            except Exception:
                pass
    except Exception as ex:
        smooth_edges_read_error = type(ex).__name__
        if diag is not None:
            diag.warn(
                phase="color_id_buffer",
                callsite="smooth_edges_capture",
                message="could not read the view's SmoothEdges state ({0}: {1}); the "
                        "capture proceeds but anti-aliasing cannot be confirmed off "
                        "and decoded edges may be blended".format(
                            type(ex).__name__, ex),
                view_id=view_id,
            )

    # Shadows were confirmed (empirically) to shift assigned colors in the
    # exported TIFF, the same class of per-pixel color drift SmoothEdges/
    # DisplayStyle above exist to eliminate -- same live-API-object-across-
    # transaction-boundary caution as orig_smooth_edges. Unlike orig_smooth_
    # edges's bool(getattr(..., None)), a missing ShowShadows attribute
    # (unsupported on this Revit host) is kept as None rather than coerced
    # to False: coercing it would make suppression below silently skip AND
    # the sidecar report "unchanged" as if nothing needed doing, when the
    # real state is actually unknown and the export may still carry shadow
    # tinting.
    _MISSING_SHOW_SHADOWS = object()
    orig_show_shadows = None
    try:
        _dm = view.GetViewDisplayModel()
        try:
            _raw_show_shadows = getattr(_dm, "ShowShadows", _MISSING_SHOW_SHADOWS)
            if _raw_show_shadows is _MISSING_SHOW_SHADOWS:
                if diag is not None:
                    diag.warn(
                        phase="color_id_buffer",
                        callsite="show_shadows_capture",
                        message="ViewDisplayModel has no ShowShadows attribute on this "
                                "Revit host; shadow suppression will be skipped and the "
                                "exported TIFF may still carry shadow tinting",
                        view_id=view_id,
                    )
            else:
                orig_show_shadows = bool(_raw_show_shadows)
        finally:
            try:
                _dm.Dispose()
            except Exception as ex:
                if diag is not None:
                    diag.warn(
                        phase="color_id_buffer",
                        callsite="show_shadows_capture_dispose",
                        message=str(ex),
                        view_id=view_id,
                    )
    except Exception as ex:
        if diag is not None:
            diag.warn(
                phase="color_id_buffer",
                callsite="show_shadows_capture",
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
        # The neutral-phase-filter swap is GATED, not deleted. Swapping the
        # view onto VOP_NeutralPhaseFilter shows phase-hidden content
        # (Demolished/Temporary) that the view as authored does not show, so
        # the ID buffer stops describing the view and starts describing a
        # phase state that exists only during the capture. Default OFF; the
        # whole path below is intact and turning the flag back on restores the
        # previous behaviour exactly. color_id_phase_swap_audit measures what
        # the difference actually is on a given view -- see
        # _build_phase_swap_audit.
        neutral_phase_swap_enabled = bool(
            getattr(cfg, "color_id_neutral_phase_swap", False))
        phase_filter_state["swap_requested"] = neutral_phase_swap_enabled
        if neutral_phase_swap_enabled:
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
        else:
            # No neutral filter is created, so restore has none to delete and
            # none to revert -- both restore steps key off the two fields left
            # False/None here.
            phase_filter_swapped = False
            phase_filter_state["swap_skipped_reason"] = (
                "color_id_neutral_phase_swap=False; the view keeps its own phase "
                "filter and the capture reflects the view as authored")
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
                # Decision A (2026-09-21): the rectangle actually handed to
                # Revit is A SNAPPED OUT to B's pixel lattice, so A's offset
                # within B is a whole number of pixels and the two frames
                # register by integer translation with no resampling.
                #
                # The snapped rectangle comes from the sizing block above --
                # the same object the pixel count was derived from -- rather
                # than being recomputed here. Recomputing is how the fitted
                # pixel count and the rendered rectangle would come to
                # disagree, which is the whole failure mode step 2 is closing.
                #
                # model_crop_offset_uv is recomputed against the SNAPPED
                # rectangle for the same reason: it is defined as what you ADD
                # to raster.bounds_xy's corners to reconstruct the crop, so it
                # has to describe the rectangle that was actually rendered.
                # Leaving it on the unsnapped one would put every decoded UV
                # off by up to a pixel per edge.
                if geom is not None:
                    _sb = geom["crop_snapped_uv"]
                    from .core.math_utils import Bounds2D as _Bounds2D_snap
                    b = _Bounds2D_snap(float(_sb[0]), float(_sb[1]),
                                       float(_sb[2]), float(_sb[3]))
                    model_crop_offset_uv = (
                        float(_sb[0]) - float(raster.bounds_xy.xmin),
                        float(_sb[1]) - float(raster.bounds_xy.ymin),
                        float(_sb[2]) - float(raster.bounds_xy.xmax),
                        float(_sb[3]) - float(raster.bounds_xy.ymax),
                    )
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
        # "unchanged" is NOT a value this writer can produce any more. It
        # never once meant what it said: a successful read always reaches the
        # set below and ends at False or "unchanged (failed)", so the only
        # way "unchanged" was ever written was a read that failed. Starting
        # from "read_failed" removes the word rather than leaving a label
        # nothing can reach. Capture PROCEEDS either way -- an unconfirmed AA
        # state costs decode confidence (MEDIUM, not HIGH), not the export.
        applied_smooth_edges = "read_failed"
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

        # Suppress shadow tinting for the same reason AA is disabled above --
        # shadows shift a painted flat color's exported RGB, which a decoder
        # can't distinguish from a genuine palette color. Skip the mutation
        # (and the transaction write it would cost) when shadows are already
        # off: nothing to suppress, and restore would otherwise have nothing
        # meaningful to undo either.
        applied_show_shadows = "unchanged"
        if orig_show_shadows is not None and orig_show_shadows is not False:
            try:
                dm = view.GetViewDisplayModel()
                try:
                    dm.ShowShadows = False
                    view.SetViewDisplayModel(dm)
                    applied_show_shadows = False
                finally:
                    try:
                        dm.Dispose()
                    except Exception as ex:
                        if diag is not None:
                            diag.warn(
                                phase="color_id_buffer",
                                callsite="show_shadows_dispose",
                                message=str(ex),
                                view_id=view_id,
                            )
            except Exception as ex:
                applied_show_shadows = "unchanged (failed)"
                if diag is not None:
                    diag.warn(
                        phase="color_id_buffer",
                        callsite="show_shadows",
                        message=str(ex),
                        view_id=view_id,
                    )

        # NOT suppressed, deliberately: the view BACKGROUND. Stage A leaves it
        # entirely untouched -- no capture, no mutation, no restore -- so the
        # export carries whatever Revit renders by default. A prior revision
        # forced it to a reserved grey sentinel and was removed; this is the
        # API research from that attempt, recorded so it does not have to be
        # rediscovered if background control is ever revisited:
        #
        #   - View.GetBackground() / View.SetBackground(ViewDisplayBackground)
        #     exist since Revit 2014, but SetBackground is only accepted on 3D,
        #     SECTION and ELEVATION views. A plan view's canvas color is an
        #     application-level Options setting (Options > Graphics >
        #     Background) with no Revit API surface at all, so the majority of
        #     Stage A's target views cannot be controlled this way regardless.
        #   - There is no solid-color creator. ViewDisplayBackground exposes
        #     CreateGradient(sky, horizon, ground) and an image variant; a flat
        #     fill is only reachable by passing the same Color three times to
        #     CreateGradient.
        #   - An IMAGE background has no safe round trip: the class has no
        #     public constructor and a gradient capture cannot rebuild one, so
        #     any mutation of a view using one would be unrestorable.
        #
        # None of that is a problem today, because the default background is
        # white and build_palette() reserves the near-white corner by
        # construction (NEAR_WHITE_RESERVED_THRESHOLD), so no assigned element
        # color can collide with it. Decode absorbs off-palette background
        # pixels into BACKGROUND_ELEMENT_ID by exact-match lookup anyway.

        # Re-collect under the neutral phase state: elements the view's original
        # phase filter hid (e.g. demolished/temporary) but the neutral filter shows
        # would otherwise be rendered by ExportImage without a color assignment.
        #
        # GATED alongside the swap itself. With no swap applied there is no
        # phase-revealed content for this re-collection to find, so its only
        # remaining effect would be re-running collection against the narrowed
        # crop -- which is a different change, not this one. The path is kept
        # and still runs verbatim when color_id_neutral_phase_swap is on, and
        # runs for measurement only (its result discarded) when
        # color_id_phase_swap_audit is on.
        phase_swap_audit_enabled = bool(
            getattr(cfg, "color_id_phase_swap_audit", False))
        recollect_wanted = neutral_phase_swap_enabled or phase_swap_audit_enabled
        neutral_recollection = None
        neutral_recollection_error = None

        # The measurement has to be taken UNDER the neutral phase state, or it
        # measures nothing it claims to.
        #
        # An audit-only run (audit on, swap off) skips the swap branch above,
        # so without this the re-collection below would run under the view's
        # AUTHORED phase filter -- which by definition cannot surface the
        # demolished/temporary elements that filter hides. The audit would then
        # report a zero difference and be taken as evidence that the swap
        # changes nothing, which is the exact conclusion it exists to test.
        # Review caught this on PR #208.
        #
        # So: apply the neutral filter for the measurement, revert it
        # immediately afterwards, and keep discarding the measured set for
        # painting. When it cannot be applied, the audit records "unavailable"
        # rather than a difference of zero -- an unmeasurable view must never
        # read as a view where the two sets agreed.
        neutral_phase_applied = neutral_phase_swap_enabled and phase_filter_swapped
        measurement_error = None
        if neutral_phase_swap_enabled and not phase_filter_swapped:
            measurement_error = (
                "the neutral phase filter could not be applied (VIEW_PHASE_FILTER "
                "is read-only), so no collection in this capture saw the neutral "
                "phase state")
        elif phase_swap_audit_enabled and not neutral_phase_swap_enabled:
            try:
                audit_pf, audit_pf_created = get_or_create_neutral_phase_filter(doc)
                phase_filter_state["neutral_phase_filter_id"] = audit_pf.Id.IntegerValue
                phase_filter_state["neutral_phase_filter_created"] = bool(audit_pf_created)
                if pf_param is not None and not pf_param.IsReadOnly:
                    pf_param.Set(audit_pf.Id)
                    # Mirrored into phase_filter_state so the restore step can
                    # revert it too: if the inline revert below is skipped by an
                    # exception, restore is the only thing standing between this
                    # view and being left on the neutral filter.
                    phase_filter_state["audit_swap_active"] = True
                    neutral_phase_applied = True
                else:
                    measurement_error = (
                        "VIEW_PHASE_FILTER is read-only (likely a View Template "
                        "controlling Phase Filter), so the neutral phase state "
                        "could not be applied for the measurement")
            except Exception as ex:
                measurement_error = "{0}: {1}".format(type(ex).__name__, ex)
            if measurement_error is not None and diag is not None:
                diag.warn(
                    phase="color_id_buffer",
                    callsite="phase_swap_audit_measurement",
                    message="phase-swap audit requested but the neutral phase state "
                            "could not be applied; the comparison is recorded as "
                            "unavailable rather than as a zero difference: "
                            "{0}".format(measurement_error),
                    view_id=view_id,
                )
        phase_filter_state["audit_measurement_applied_neutral_phase"] = bool(
            phase_swap_audit_enabled and neutral_phase_applied)

        if recollect_wanted and raster is not None:
            try:
                from .revit.collection import collect_view_elements as _collect_view_elements
                neutral_recollection = _collect_view_elements(
                    doc, view, raster, diag=diag, cfg=cfg)
            except Exception as ex:
                neutral_recollection_error = "{0}: {1}".format(type(ex).__name__, ex)
                if diag is not None:
                    diag.warn(
                        phase="color_id_buffer",
                        callsite="recollect_under_neutral_phase",
                        message=str(ex),
                        view_id=view_id,
                    )
        elif recollect_wanted and diag is not None:
            neutral_recollection_error = "raster not provided"
            diag.warn(
                phase="color_id_buffer",
                callsite="recollect_under_neutral_phase",
                message="raster not provided; using pre-phase-swap element collection "
                        "(may miss phase-revealed elements)",
                view_id=view_id,
            )

        # Revert the measurement-only swap before anything is painted or
        # exported. A failure here is NOT recoverable and must not be absorbed:
        # everything below paints and exports under whatever phase filter is in
        # force, so continuing would produce a capture of a phase state the
        # caller never asked for, with no colour assignment for the elements it
        # reveals -- silently wrong output, which is worse than a failed view.
        # phase_filter_state["audit_swap_active"] stays True so the restore
        # step reverts it on the way out.
        if phase_filter_state.get("audit_swap_active"):
            try:
                pf_param.Set(ElementId(int(orig_phase_filter_id)))
                phase_filter_state["audit_swap_active"] = False
            except Exception as ex:
                if diag is not None:
                    diag.error(
                        phase="color_id_buffer",
                        callsite="phase_swap_audit_revert",
                        message="could not revert the measurement-only phase filter "
                                "swap; refusing to paint or export this view under a "
                                "phase state the caller did not ask for",
                        view_id=view_id,
                        exc=ex,
                    )
                raise

        # The paint set. Only the swap path replaces the pipeline's own
        # collection; an audit-only run measures and discards.
        recollected = elements
        if neutral_phase_swap_enabled and neutral_recollection is not None:
            recollected = neutral_recollection

        phase_swap_element_set_audit = _build_phase_swap_audit(
            phase_swap_audit_enabled,
            neutral_phase_swap_enabled,
            elements,
            neutral_recollection,
            neutral_recollection_error,
            raster is not None,
            neutral_phase_applied,
            measurement_error,
            diag=diag,
            view_id=view_id,
        )

        # Stage A step 1: DWG imports the collector finds but drops (an
        # unusable view bbox, a per-import failure, or a scan that failed
        # outright) land here so the sidecar can say an import was omitted
        # rather than leaving "not in the drawing" and "dropped" identical.
        dwg_imports_omitted = []
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
            expanded = _expand_elements(
                doc, view, recollected, host_only_cfg, diag=diag,
                elem_cache=elem_cache, dwg_omitted_out=dwg_imports_omitted,
            )
        except Exception as ex:
            if diag is not None:
                diag.warn(
                    phase="color_id_buffer",
                    callsite="expand_linked_elements",
                    message=str(ex),
                    view_id=view_id,
                )
            expanded = [{"element": e, "source_type": "HOST"} for e in recollected]
            dwg_imports_omitted.append({
                "element_id": None,
                "state": "unavailable",
                "reason": "element expansion failed; no DWG import was examined "
                          "({0}: {1})".format(type(ex).__name__, ex),
            })

        # Stage A step 1: keep each non-LINK entry's own source_type across
        # the partition so a painted DWG ImportInstance stays identifiable in
        # the sidecar. Nothing about which elements are collected, painted or
        # colored changes -- this is a read carried alongside the same set.
        host_source_types = {}
        host_elements, _link_entries = _split_expanded_elements(
            expanded, source_out=host_source_types,
        )
        resolved_ids = resolve_all(doc, host_elements)
        count_host = len(resolved_ids)

        # One colorability answer for this whole capture, resolved once here
        # and handed to LINK category-filter discovery below. See
        # _resolve_colorable_category_predicate.
        is_colorable, colorable_source = _resolve_colorable_category_predicate(
            diag=diag, view_id=view_id
        )

        # Respect the same include_linked_rvt opt-out collect_all_linked_elements()
        # always honored for the retired per-element path: discovery scans every
        # RevitLinkInstance in the view regardless of config, so skipping it
        # entirely when the user has disabled linked-RVT processing is the only
        # way an opted-out capture reports zero LINK assignments and applies no
        # category filters, matching prior behavior.
        if getattr(cfg, "include_linked_rvt", False):
            link_categories, uncolorable_link_categories = _collect_link_category_filters(
                doc, view, is_colorable, diag=diag, view_id=view_id
            )
        else:
            link_categories, uncolorable_link_categories = [], []
        if uncolorable_link_categories and diag is not None:
            diag.warn(
                phase="color_id_buffer",
                callsite="link_category_filter_discovery",
                message="{0} policy-included LINK categor(ies) are not on the vetted "
                        "colorable whitelist, get no assigned color, and render with an "
                        "uncontrolled native color in this capture (a known, accepted "
                        "gap -- see _model_categories_in_linked_doc): {1}".format(
                            len(uncolorable_link_categories),
                            sorted(cat.Name for cat in uncolorable_link_categories)),
                view_id=view_id,
            )
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

        # A colorable category whose filter could not be created or applied
        # renders with its native, uncontrolled color for this capture.
        # Nothing capture-side suppresses that (see
        # _model_categories_in_linked_doc's note on the accepted
        # uncontrolled-color gap) -- it is reported here and in the sidecar so
        # it is visible, not silent.
        if failed_link_categories and diag is not None:
            diag.warn(
                phase="color_id_buffer",
                callsite="apply_link_category_filter",
                message="{0} colorable LINK categor(ies) could not be given a filter and "
                        "render with an uncontrolled native color in this capture: "
                        "{1}".format(
                            len(failed_link_categories),
                            sorted(cat.Name for cat in failed_link_categories)),
                view_id=view_id,
            )

        # Phase 1b: near-face-W + UV bbox footprint collection for every HOST
        # and LINK element resolved above -- a pure read, so it runs here
        # (the identity set is finalized) rather than depending on anything
        # painted/exported below. Additive-only sidecar data; never touches
        # color_assignment_map or link_category_color_map. The view-scoped
        # LINK scan it needs is collected once here and handed in.
        link_proxies = []
        if link_category_color_map:
            link_proxies, _link_status = _collect_view_scoped_link_proxies(
                doc, view, cfg, diag=diag, view_id=view_id,
            )
        near_face_w_map = _collect_near_face_w_data(
            doc, view, raster, cfg, resolved_ids, link_category_color_map,
            diag=diag, view_id=view_id, link_proxies=link_proxies,
            host_source_types=host_source_types,
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
    dim_report = {
        "requested_axis": requested_axis,
        "actual_w": None,
        "actual_h": None,
        "dim_check": "read_failed",
        "dim_read_error": "export did not complete",
        "dim_check_ceiling_px": MAX_STAGE_A_AXIS_PX,
        "attempts": [],
    }
    try:
        _tiff_path, actual_pixel_size, dim_report = _export_tiff(
            doc, view, tiff_path, pixel_size, diag=diag, view_id=view_id,
            fit_direction=fit_direction, max_axis_px=MAX_STAGE_A_AXIS_PX,
            grid_axis_px=backoff_floor_px,
        )
    finally:
        restore_tx = Transaction(doc, "VOP Stage A RESTORE color ID buffer")
        restore_tx.Start()

        # Best-effort restore: every step below is independently guarded. Revit
        # transactions are all-or-nothing on RollBack, so a single failing step
        # (a stale ElementId, a category that refuses an override, ...) must
        # never be able to roll back every other restore step that already
        # succeeded — that would leave the document sitting in the suppressed/
        # colored Stage A state permanently instead of just missing the one
        # failed piece.
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

        if orig_show_shadows is not None:
            def _restore_show_shadows():
                dm = view.GetViewDisplayModel()
                try:
                    dm.ShowShadows = orig_show_shadows
                    view.SetViewDisplayModel(dm)
                finally:
                    try:
                        dm.Dispose()
                    except Exception as ex:
                        if diag is not None:
                            diag.warn(
                                phase="color_id_buffer",
                                callsite="restore_show_shadows_dispose",
                                message=str(ex),
                                view_id=view_id,
                            )
            _restore_step("restore_show_shadows", _restore_show_shadows)

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
            # "audit_swap_active" covers the measurement-only swap: it is
            # cleared by the inline revert, so it is still True here only when
            # that revert was skipped or failed. Without this clause a raise
            # between the two would leave the view on VOP_NeutralPhaseFilter
            # permanently.
            if orig_phase_filter_id is not None and (
                    phase_filter_state.get("swapped")
                    or phase_filter_state.get("audit_swap_active")):
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

    # The dpi this capture ACHIEVED, as opposed to the dpi that was asked
    # for. They come apart three ways, all in the sizing path above: the
    # max(64, ...) floor on pre_cap_px, the two-axis cap, and the
    # dimension-mismatch backoff.
    #
    # The arithmetic lives in resolution_contract.effective_export_dpi() and is
    # CALLED, not replicated: a test that reimplements the formula binds itself
    # to its own copy, so production could regress to the fitted-axis or
    # grid-extent form with the test still green. That is a review finding on
    # PR #202, not a hypothetical. See that function for why the denominator is
    # the rendered crop and why BOTH axes are used with the smaller winning.
    effective_export_dpi = _effective_export_dpi(
        crop_bounds_xy, dim_report.get("actual_w"), dim_report.get("actual_h"), scale)

    state_out = {
        "view_id": view_id,
        "resolution": {
            "pixel_size": actual_pixel_size,
            "requested_pixel_size": pixel_size,
            # The dpi that was REQUESTED. Named for that, because the old name
            # ("export_dpi") reads as a property of the export and has already
            # been consumed as a measurement once. Nothing here verifies that
            # Revit delivered it; effective_export_dpi below is the measurement.
            "requested_export_dpi": export_dpi,
            # The dpi the exported file actually carries on its UNPADDED axis,
            # measured against the RENDERED CROP (see above). Identically
            # view_scale / (12 * feet_per_pixel) for the feet_per_pixel a
            # decoder derives from this same sidecar's "bounds_xy", so the
            # producer's figure and the decoder's cannot drift apart. None
            # when the file's dimensions or the crop rectangle are unknown.
            "effective_export_dpi": effective_export_dpi,
            # RETAINED, not renamed away: every sidecar already written carries
            # this name, and tools/decode_stage_a_color_id.py's pixel-space
            # fallback reads it. Dropping it would orphan those captures. It is
            # the request -- the same value as requested_export_dpi -- and new
            # readers should prefer that name.
            "export_dpi": export_dpi,
            "view_scale": scale,
            # Which axis pixel_size set. Without it a reader cannot tell
            # whether the other dimension was requested or derived, and every
            # size-derived metric downstream assumes one of the two.
            "fit_direction": fit_direction,
            # The paper dimension pixel_size was derived from, so a reader can
            # reproduce the request instead of assuming it came from the width.
            # This is the view's real paper extent along requested_axis, and
            # it is the ONLY field that survives both the 64 px floor on
            # pre_cap_px and the axis cap: a reader reconstructing physical
            # scale should start here and fall back to pre_cap_px/export_dpi
            # only for sidecars written before it existed.
            "paper_fit_in": paper_fit_in,
            # Both axes, so a reader never has to infer the other one from
            # an aspect ratio it would have to derive from the pixels.
            "paper_width_in": paper_width_in,
            "paper_height_in": paper_height_in,
            # The two-axis cap. pre_cap_px is the uncapped request, which is
            # what makes a capped export reconstructable: before this field
            # existed the pre-cap value was discarded and no consumer could
            # recover the view's real paper extent from the sidecar.
            "requested_axis": requested_axis,
            "requested_px": pixel_size,
            "pre_cap_px": cap["pre_cap_px"],
            "cap_applied": bool(cap["cap_applied"]),
            "max_axis_px": cap["max_axis_px"],
            "predicted_derived_px": cap["accepted_derived_px"],
            # What the exported file MEASURED, as opposed to everything above
            # it, which is what was asked for. dim_check is "pass" only when
            # the fitted axis came back within 1 px of the request and
            # neither axis exceeds dim_check_ceiling_px; "read_failed" means
            # the dimensions could not be read at all and is NOT a pass.
            "actual_w": dim_report.get("actual_w"),
            "actual_h": dim_report.get("actual_h"),
            "dim_check": dim_report.get("dim_check"),
            "dim_check_ceiling_px": dim_report.get("dim_check_ceiling_px"),
            "dim_read_error": dim_report.get("dim_read_error"),
            "dim_check_attempts": dim_report.get("attempts"),
            "backoff_stop_reason": dim_report.get("backoff_stop_reason"),
            "backoff_floor_px": backoff_floor_px,
            "backoff_max_retries": MAX_MISMATCH_RETRIES,
        },
        # Stage A step 2. ADDITIVE: every key above keeps its meaning and its
        # name; this records what the frame-derived sizing did, which the
        # shipped keys have no place for.
        #
        # THREE-VALUED, per the standing rule. A capture that could not resolve
        # a frame carries {"status": "unavailable", "reason": ...} rather than
        # zeros or a silently absent key -- a zero here would read as "the
        # frame is degenerate" and an absent key as "written before step 2".
        #
        # requested vs achieved is spelled out for dpi, px AND fpp because
        # this project has misread that distinction four times (dpi, CellSize,
        # IsOnSheet, cap) and treats it as a class, not as three incidents.
        "export_frame": (
            {
                "status": "value",
                # B as resolved, unclipped and un-re-centred.
                "frame_uv": [float(v) for v in frame_uv],
                # Which rectangle B came from. "anno_uncapped" is the
                # annotation frame as computed; "raster_bounds" means no
                # annotation expansion applied, so the grid's own rectangle
                # already IS the frame. Recorded rather than inferable,
                # because the two are equal whenever the cap envelope did not
                # fire and a reader cannot otherwise tell which path ran.
                "frame_source": frame_source,
                # True when view_basis' cap envelope clipped and re-centred
                # the grid's bounds. On those views raster.bounds_xy is a
                # model-centred window rather than the annotation extent, and
                # the capture is deliberately NOT sized against it.
                "anno_cap_envelope_applied": bool(
                    getattr(raster, "anno_cap_envelope_applied", False)),
                # The grid's own rectangle, for the capped case where it
                # differs from the frame. model_crop_offset_uv is defined
                # against THIS rectangle, not against frame_uv.
                "raster_bounds_uv": (
                    [float(raster.bounds_xy.xmin), float(raster.bounds_xy.ymin),
                     float(raster.bounds_xy.xmax), float(raster.bounds_xy.ymax)]
                    if raster is not None and getattr(raster, "bounds_xy", None) is not None
                    else None),
                "frame_extent_ft": [float(v) for v in geom["frame_extent_ft"]],
                # B rounded OUT to the pixel lattice: what the capture
                # realises. Grows only at the max corner, so B.min -- the
                # single origin the frame-reconciliation invariant rests on --
                # does not move.
                "frame_snapped_uv": [float(v) for v in geom["frame_snapped_uv"]],
                "frame_px": [int(v) for v in geom["frame_px"]],
                # A snapped onto B's lattice, and its whole-pixel offset from
                # B.min. This is decision A's registration data: B overlays A
                # by translating this many pixels, with no resampling.
                "crop_snapped_uv": [float(v) for v in geom["crop_snapped_uv"]],
                "crop_px": [int(v) for v in geom["crop_px"]],
                "crop_offset_px": [int(v) for v in geom["crop_offset_px"]],
                # True when no narrower model crop applied, so A IS B. Kept
                # separate from crop_offset_px == [0, 0], which a crop that
                # starts at B's own corner also produces.
                "crop_is_frame": bool(geom["crop_is_frame"]),
                "requested_export_dpi": float(geom["requested_export_dpi"]),
                "achieved_export_dpi": float(geom["achieved_export_dpi"]),
                "requested_fpp_ft": float(geom["requested_fpp_ft"]),
                "achieved_fpp_ft": float(geom["achieved_fpp_ft"]),
                "requested_px": int(geom["requested_px"]),
                "pre_cap_px": int(geom["pre_cap_px"]),
                "cap_applied": bool(geom["cap_applied"]),
                "max_axis_px": geom["max_axis_px"],
                "min_axis_px": int(geom["min_axis_px"]),
                # True when the crop fell below the floor and feet-per-pixel
                # was re-derived from it, putting achieved dpi ABOVE the
                # request. False both when the floor was not needed and when
                # the ceiling refused it -- requested_px below min_axis_px
                # with this False is the refused case.
                "floor_applied_to_crop": bool(geom["floor_applied_to_crop"]),
                # Whether the ceiling moved the request through cap_axes
                # itself, as opposed to through the lattice correction. Both
                # set cap_applied; this says which.
                "cap_applied_by_cap_axes": bool(geom["cap_applied_by_cap_axes"]),
                # How many times the lattice had to step down to keep both of
                # B's axes inside the ceiling. Normally 0. Non-zero is not an
                # error, but it means the achieved figures above are a step or
                # more below what the cap alone would predict.
                "lattice_corrections": int(geom["lattice_corrections"]),
                # UNCONFIRMED (no Revit run in this session): that Revit
                # renders the snapped rectangle at exactly crop_px pixels. The
                # post-export dim_check above is what measures it; these are
                # the producer's intent, not a measurement.
                "verified_against_revit": False,
            }
            if geom is not None and frame_uv is not None else
            {
                "status": "unavailable",
                "reason": ("no usable frame bounds for this view; the export fell back "
                           "to the 1 paper-inch minimum and is not frame-derived"),
            }
        ),
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
        # Phase 1b: additive near-face-W + UV bbox footprint per HOST/LINK
        # element -- see _collect_near_face_w_data's docstring. Consumed by
        # tools/link_identity_resolver.py; never read by decode_stage_a_
        # color_id.py and never modifies color_assignment_map/
        # link_category_color_map above.
        "near_face_w_map": near_face_w_map,
        # Stage A step 1, additive: DWG ImportInstances the collector found
        # but did not paint, each with the reason. An EMPTY list means every
        # import found was collected; it never means "no imports exist" and
        # never means "not checked" -- those arrive as an entry with
        # element_id None. See revit/linked_documents._collect_from_dwg_imports.
        "dwg_imports_omitted": dwg_imports_omitted,
        "applied_display_style": applied_display_style,
        "applied_smooth_edges": applied_smooth_edges,
        # The exception type when the SmoothEdges read raised; None whenever
        # the read succeeded, whatever it found.
        "smooth_edges_read_error": smooth_edges_read_error,
        "applied_show_shadows": applied_show_shadows,
        # Where the "can Stage A color this category?" answer came from:
        # "frozen_whitelist" (VETTED_COLORABLE_CATEGORY_IDS) or
        # "live_filterable_lookup" (the pre-freeze fallback -- a capture
        # recording this is NOT reproducible from the source tree alone).
        "colorable_category_source": colorable_source,
        # LINK categories that end this capture with no assigned color, and so
        # render with a native, uncontrolled color: policy-included but not on
        # the vetted colorable whitelist ("uncolorable"), or whitelisted but
        # whose filter could not be created/applied ("failed"). Nothing
        # capture-side suppresses either -- see _model_categories_in_linked_
        # doc's note on the accepted gap -- so they are recorded for
        # auditability. A category showing up as uncolorable that should be
        # colorable means the whitelist needs recapturing.
        "uncolorable_link_categories": sorted(
            cat.Name for cat in uncolorable_link_categories
        ),
        "failed_link_categories": sorted(
            cat.Name for cat in failed_link_categories
        ),
        "categories_hidden": category_hidden_state,
        "filter_state": filter_state,
        "phase_filter_state": phase_filter_state,
        # ADDITIVE, new top-level key: the graphics state that governed this
        # view before Stage A touched it. Every field three-valued
        # (value | not_applicable | unavailable+reason) -- see
        # _capture_view_graphics_state. Nothing above or below reads it; it
        # exists so a capture can be explained after the fact.
        "view_graphics_state": view_graphics_state,
        # ADDITIVE, new top-level key: the element-set comparison behind the
        # decision to stop relying on the neutral-phase re-collection.
        # "not_applicable" unless color_id_phase_swap_audit is on -- see
        # _build_phase_swap_audit.
        "phase_swap_element_set_audit": phase_swap_element_set_audit,
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

    # A dimension mismatch that survived the halving backoff is a failed
    # view, not a successful one with a note attached. The TIFF and sidecar
    # are still written -- they are the evidence -- but the pipeline counts
    # this view as failed (streaming.py honours success=False) so the run
    # cannot report a clean Stage A pass over an export whose size Revit
    # never actually delivered.
    failure_reason = None
    if dim_report.get("dim_check") == "mismatch":
        failure_reason = "export_dim_mismatch"
        if diag is not None:
            diag.error(
                phase="color_id_buffer",
                callsite="export_dim_check",
                message="view failed: exported {0}x{1} never matched the request on the "
                        "{2} axis within the {3} px per-axis limit, through {4} "
                        "attempt(s)".format(
                            dim_report.get("actual_w"), dim_report.get("actual_h"),
                            dim_report.get("requested_axis"),
                            dim_report.get("dim_check_ceiling_px"),
                            len(dim_report.get("attempts") or [])),
                view_id=view_id,
            )

    return {
        "view_id": view_id,
        "view_name": getattr(view, "Name", None),
        "success": failure_reason is None,
        "failure_reason": failure_reason,
        "stage": "color_id_buffer_stage_a",
        "tiff_path": tiff_path,
        "sidecar_path": json_path,
        "output_dir": out_dir,
        "resolution": state_out["resolution"],
        "color_assignment_count": count_host + count_link_categories,
        "timings": {"color_id_buffer_ms": round((time.time() - t0) * 1000.0, 3)},
        "metadata": state_out,
    }
