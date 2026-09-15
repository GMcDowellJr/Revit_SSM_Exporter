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

# Document-envelope categories the force-white safety net must NOT touch,
# even though both are CategoryType.Model and both are policy-excluded (they
# are in collection_policy._EXCLUDED_BIC_NAMES_GLOBAL), which would otherwise
# make them textbook force-white targets.
#
# "RVT Links" is the category of the RevitLinkInstance element itself, sitting
# above every category inside the linked document. A flat-color override there
# is at best a no-op and at worst tints the very LINK geometry the category
# filters exist to color correctly -- it would risk breaking the mechanism
# this change is built around.
#
# "Imports" is the same shape for DWG: DWG layers are SUBCATEGORIES of it, and
# Revit applies a parent category's override to a subcategory that has none of
# its own. DWG ImportInstances are painted per-element (element overrides
# outrank category ones, so painted content is unaffected either way), but
# DWG handling is explicitly out of scope for this pass and a category-level
# override reaching into every DWG layer is not "untouched".
#
# Both are exempt from the safety net, not from the architecture: whatever
# renders from them keeps today's behavior, which is the same residual
# uncontrolled-color risk this change deliberately accepts elsewhere.
FORCE_WHITE_EXEMPT_BIC_NAMES = (
    "OST_RVT_Links",
    "OST_ImportObjectStyles",
)
# Human-readable counterpart, checked alongside the BIC ids above for the same
# belt-and-suspenders reason collection_policy.py pairs
# _FALLBACK_EXCLUDED_CATEGORY_NAMES with _EXCLUDED_BIC_NAMES_GLOBAL: a BIC name
# that does not resolve on the running Revit version leaves the id check
# matching nothing, and Category.Name is localized so the name check matches
# nothing on a non-English Revit. Each covers the other's gap. Must stay in
# sync with FORCE_WHITE_EXEMPT_BIC_NAMES, one entry per BIC name.
FORCE_WHITE_EXEMPT_CATEGORY_NAMES = frozenset((
    "RVT Links",
    "Imports",
))

# Sentinel colors. Both sit inside the near-white reservation declared above
# (every channel >= NEAR_WHITE_RESERVED_THRESHOLD), so build_palette() can
# never hand either one to a real element -- the reservation logic itself is
# unchanged; these two values simply occupy space it already refused to
# assign. tests/test_color_id_buffer.py enforces that guarantee directly.
#
# UNCOLORABLE_SENTINEL_RGB is pure white and means "deliberately uncontrolled":
# it is what a category-level force-white override paints for every
# CategoryType.Model category Stage A does not hand a real assigned color --
# HOST and LINK alike (see _category_force_white_targets). A decoder must read
# it the same way it reads the (0,0,0) "no element painted" sentinel: as an
# absence of element identity, never as a palette ID.
UNCOLORABLE_SENTINEL_RGB = (255, 255, 255)
# BACKGROUND_SENTINEL_RGB marks the view canvas itself, so an empty region of
# the export stays distinguishable from a force-whited element. 240 is not an
# arbitrary "light grey": the reservation floor is 224 and the uncolorable
# sentinel is 255, so 240 is the midpoint that maximizes the margin to BOTH
# (16 units either way) while still sitting inside the reservation. Anything
# below 224 would leave the reservation and could be assigned to a real
# element; 224 or 255 themselves would sit flush against the reservation
# boundary or against the uncolorable sentinel. 16 units is comfortably clear
# of the couple of RGB units of TIFF-export/rendering noise the
# graphics_semantics_probe measured with AA and shadows suppressed.
BACKGROUND_SENTINEL_RGB = (240, 240, 240)

# Vetted colorable categories -- the FROZEN capture of
# ParameterFilterUtilities.GetAllFilterableCategories(), by primary category
# name only (no subcategories).
#
# Regenerate with tools/capture_vetted_colorable_categories.py, run inside a
# live Dynamo/Revit session; it prints this exact literal block for pasting.
# Do not hand-transcribe it from a screenshot or from a description of the
# Filter dialog: the live API call is the same source the dialog itself reads
# from, and transcription of a ~150-200 entry list risks silent errors that
# would show up only as a category quietly losing its color in a capture.
#
# TRADEOFF (deliberate): freezing the list trades a small amount of
# Revit-version drift risk -- a category added or renamed in a newer Revit
# than the capture will not be recognized as colorable until the list is
# recaptured -- for full determinism and auditability. Two captures of the
# same view on the same Revit now assign the same categories the same way
# regardless of what the host's live API happens to report, and a reviewer
# can see the exact set in the diff instead of having to run Revit to know
# it. The drift failure mode is also the safe direction: an unrecognized
# category is not colored, so it falls through to the force-white safety net
# rather than rendering an uncontrolled native color.
#
# EMPTY UNTIL CAPTURED. While this set is empty,
# _resolve_colorable_category_predicate() falls back to the live
# GetAllFilterableCategories() lookup and records a loud diagnostic saying so
# on every capture -- the pre-freeze behavior, kept working but never silent.
# Populating this set retires that fallback permanently.
VETTED_COLORABLE_CATEGORY_NAMES = frozenset()

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


def _apply_flat_color_to_ogs(ogs, solid_pattern_id, color):
    """Write the flat-color paint onto an existing OverrideGraphicSettings.

    Factored out of _build_flat_color_ogs() so the per-element paint, the
    LINK category filter, and the category-level force-white override all
    write the SAME set of fields -- and so _CATEGORY_OGS_FIELDS below has one
    definition to mirror when it snapshots what force-white is about to
    overwrite. Mutating a caller-supplied ogs (rather than always starting
    from a blank one) lets the category-level path preserve unrelated
    override state on that category, such as line weights or line patterns,
    which it never touches and therefore never has to restore.
    """
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


def _build_flat_color_ogs(solid_pattern_id, color):
    from Autodesk.Revit.DB import OverrideGraphicSettings
    return _apply_flat_color_to_ogs(OverrideGraphicSettings(), solid_pattern_id, color)


_MISSING_OGS_FIELD = object()

# Exactly the OverrideGraphicSettings fields _apply_flat_color_to_ogs() writes,
# as (property to read, setter to write, kind). The category-level force-white
# override is applied on top of a category's existing overrides, so restore has
# to put these -- and only these -- back. Captured as plain data (ints, RGB
# tuples, bools), never as live Revit objects: this module's curtain-panel
# restore bug came from holding an OverrideGraphicSettings across the
# suppress/export/restore transaction boundary and reapplying it afterward.
_CATEGORY_OGS_FIELDS = (
    ("SurfaceForegroundPatternId", "SetSurfaceForegroundPatternId", "element_id"),
    ("SurfaceForegroundPatternColor", "SetSurfaceForegroundPatternColor", "color"),
    ("IsSurfaceForegroundPatternVisible", "SetSurfaceForegroundPatternVisible", "bool"),
    ("CutForegroundPatternId", "SetCutForegroundPatternId", "element_id"),
    ("CutForegroundPatternColor", "SetCutForegroundPatternColor", "color"),
    ("IsCutForegroundPatternVisible", "SetCutForegroundPatternVisible", "bool"),
    ("ProjectionLineColor", "SetProjectionLineColor", "color"),
    ("CutLineColor", "SetCutLineColor", "color"),
    ("Transparency", "SetSurfaceTransparency", "int"),
    ("Halftone", "SetHalftone", "bool"),
)


def _color_to_rgb_or_none(color):
    """A Revit Color as a plain (r, g, b), or None for "no color set"."""
    if color is None:
        return None
    try:
        if not bool(getattr(color, "IsValid", True)):
            return None
        return (int(color.Red), int(color.Green), int(color.Blue))
    except Exception:
        return None


def _capture_category_ogs_fields(ogs):
    """Plain-data snapshot of the _CATEGORY_OGS_FIELDS on ``ogs``.

    A field whose property does not exist on this Revit host is recorded as
    _MISSING_OGS_FIELD rather than as a guessed default, so restore can skip
    it instead of writing a value that was never there. Property names have
    shifted across Revit versions (the Surface*Pattern* family gained its
    Foreground/Background split in 2019), and inventing a value for one that
    cannot be read would silently rewrite a user's category graphics.
    """
    snapshot = {}
    for prop, _setter, kind in _CATEGORY_OGS_FIELDS:
        raw = getattr(ogs, prop, _MISSING_OGS_FIELD)
        if raw is _MISSING_OGS_FIELD:
            snapshot[prop] = _MISSING_OGS_FIELD
            continue
        try:
            if kind == "color":
                snapshot[prop] = _color_to_rgb_or_none(raw)
            elif kind == "element_id":
                snapshot[prop] = int(raw.IntegerValue)
            elif kind == "bool":
                snapshot[prop] = bool(raw)
            else:
                snapshot[prop] = int(raw)
        except Exception:
            snapshot[prop] = _MISSING_OGS_FIELD
    return snapshot


def _restore_category_ogs_fields(ogs, snapshot):
    """Write a _capture_category_ogs_fields() snapshot back onto ``ogs``.

    Returns the list of property names that could not be restored (either
    unreadable at capture time or rejected on write) so the caller can report
    them -- CLAUDE.md's no-silent-failure rule applies to restore too.
    """
    from Autodesk.Revit.DB import Color, ElementId
    unrestored = []
    for prop, setter_name, kind in _CATEGORY_OGS_FIELDS:
        value = snapshot.get(prop, _MISSING_OGS_FIELD)
        if value is _MISSING_OGS_FIELD:
            unrestored.append(prop)
            continue
        setter = getattr(ogs, setter_name, None)
        if setter is None:
            unrestored.append(prop)
            continue
        try:
            if kind == "color":
                if value is None:
                    setter(Color.InvalidColorValue)
                else:
                    setter(Color(int(value[0]), int(value[1]), int(value[2])))
            elif kind == "element_id":
                setter(ElementId(int(value)))
            elif kind == "bool":
                setter(bool(value))
            else:
                setter(int(value))
        except Exception:
            unrestored.append(prop)
    return unrestored


class _CategoryOnlyProbe(object):
    """Minimal stand-in element for asking collection_policy a CATEGORY-level
    question.

    should_include_element() is element-shaped by design, but the only
    element-level facts it reads are ``Category``, ``ViewSpecific`` and the
    element's own type name (its ImportInstance belt-and-suspenders check).
    Pinning ViewSpecific to False and presenting a type name that is not
    ImportInstance reduces it to exactly the category decision -- which is
    what "is this category a policy-included model candidate?" means -- while
    still routing through the single authoritative policy rather than
    re-deriving a second, drift-prone copy of it here (CLAUDE.md's "single
    source of truth").
    """

    ViewSpecific = False

    def __init__(self, category):
        self.Category = category


def _category_id_int(cat):
    try:
        return int(cat.Id.IntegerValue)
    except Exception:
        return None


def _resolve_colorable_category_predicate(diag=None, view_id=None):
    """Resolve the single "can Stage A give this category a real color?" test.

    Returns ``(predicate, source)`` where predicate takes a Category and
    ``source`` names where the answer came from, for the sidecar. Centralized
    so LINK category-filter discovery and the HOST+LINK force-white safety net
    are never asked to agree about colorability by two separate code paths --
    a category the two disagreed about would be both colored and force-whited,
    or neither.

    Prefers the frozen VETTED_COLORABLE_CATEGORY_NAMES capture. While that set
    is still empty it falls back to the live
    ParameterFilterUtilities.GetAllFilterableCategories() lookup this replaced,
    and says so loudly on every capture: the fallback keeps pre-freeze captures
    working exactly as before instead of force-whiting every category in the
    model, but an unfrozen whitelist is a state the operator has to be able to
    see, not a silent default.
    """
    if VETTED_COLORABLE_CATEGORY_NAMES:
        names = VETTED_COLORABLE_CATEGORY_NAMES

        def _by_frozen_name(cat):
            return getattr(cat, "Name", None) in names

        return _by_frozen_name, "frozen_whitelist"

    try:
        from Autodesk.Revit.DB import ParameterFilterUtilities
        filterable_ids = set(
            cid.IntegerValue for cid in ParameterFilterUtilities.GetAllFilterableCategories()
        )
    except Exception as ex:
        # Fail CLOSED: with no colorability answer at all, treat nothing as
        # colorable. That assigns no LINK category filters and force-whites
        # every CategoryType.Model category, so the capture is degraded (LINK
        # content carries no identity) but never WRONG -- no uncontrolled
        # native color can alias an assigned palette ID. Failing open would
        # invert exactly that. Recorded as an error, not a warning: a capture
        # that hits this needs the whitelist frozen, not a retry.
        if diag is not None:
            diag.error(
                phase="color_id_buffer",
                callsite="colorable_category_whitelist",
                message="VETTED_COLORABLE_CATEGORY_NAMES is empty AND the live "
                        "ParameterFilterUtilities fallback failed; treating every "
                        "category as uncolorable for this capture. LINK content will "
                        "carry no identity and every model category renders the "
                        "uncolorable sentinel: {0}".format(ex),
                view_id=view_id,
                exc=ex,
            )
        return (lambda cat: False), "unavailable"

    if diag is not None:
        diag.warn(
            phase="color_id_buffer",
            callsite="colorable_category_whitelist",
            message="VETTED_COLORABLE_CATEGORY_NAMES is empty; falling back to the live "
                    "ParameterFilterUtilities.GetAllFilterableCategories() lookup for "
                    "this capture. Colorability is therefore host-dependent and not "
                    "reproducible from the source tree -- run "
                    "tools/capture_vetted_colorable_categories.py in Dynamo and freeze "
                    "the result to retire this fallback.",
            view_id=view_id,
        )

    def _by_live_lookup(cat):
        return _category_id_int(cat) in filterable_ids

    return _by_live_lookup, "live_filterable_lookup"


def _category_force_white_targets(doc, is_colorable, already_hidden_ids=(), diag=None, view_id=None):
    """Category ids to force-paint UNCOLORABLE_SENTINEL_RGB for this capture.

    The rule is the safety net's whole point, stated once: force-white every
    CategoryType.Model category that Stage A will NOT hand a real assigned
    color. A category gets a real color only when it is both policy-included
    (a genuine model candidate) AND colorable (on the vetted whitelist, so a
    LINK category filter can carry it). Everything else CategoryType.Model
    renders the "deliberately uncontrolled" sentinel instead of its native
    color.

    NOTE -- this is deliberately WIDER than "policy-included but not
    whitelisted". That narrower reading would not cover the HOST-side gap this
    exists to close: _hidden_category_state() hides only CategoryType.
    Annotation plus the two VIEW_ONLY_MODEL_BIC_NAMES entries, so the
    policy-EXCLUDED but CategoryType.Model categories -- Rooms, Areas, MEP
    Spaces and Point Clouds are all in _EXCLUDED_BIC_NAMES_GLOBAL and all
    report CategoryType.Model -- are collected by nothing, painted by nothing,
    and today render with whatever native color Revit gives them. They are
    excluded from policy, so a "policy-included" filter would skip exactly the
    categories that need this most.

    HOST elements of a force-whited category are unaffected when they have
    their own per-element override: Revit resolves Instance/Element > Filter >
    Category, so the category-level white sits underneath both the HOST paint
    loop's SetElementOverrides and any colored LINK category filter. Only
    content with no override above it -- which is precisely the uncontrolled
    content -- actually renders white.

    Categories already hidden for this capture (``already_hidden_ids``, from
    _hidden_category_state) are skipped: they render nothing at all, so an
    override on them would be a no-op, and VIEW_ONLY_MODEL_BIC_NAMES stays
    untouched by this pass as intended. FORCE_WHITE_EXEMPT_BIC_NAMES is
    skipped too -- see that constant for why the two document-envelope
    categories are carved out.

    Returns a sorted list of category id ints -- sorted so a capture's
    force-white set is deterministic and diffable in the sidecar. Whether a
    given category will actually accept a view-level override is not
    pre-checked here: no Revit API surface reports that for overrides the way
    View.CanCategoryBeHidden() does for visibility, so _force_white_category()
    attempts the write and reports the ones that refuse it, rather than this
    pass guessing at an availability rule.
    """
    from Autodesk.Revit.DB import CategoryType
    from .revit.collection_policy import resolve_category_ids, should_include_element

    already_hidden = set(int(cid) for cid in already_hidden_ids)
    already_hidden.update(resolve_category_ids(doc, FORCE_WHITE_EXEMPT_BIC_NAMES))
    target_ids = []
    for cat in doc.Settings.Categories:
        try:
            if cat.CategoryType != CategoryType.Model:
                continue
            cat_id_int = _category_id_int(cat)
            if cat_id_int is None or cat_id_int in already_hidden:
                continue
            if getattr(cat, "Name", None) in FORCE_WHITE_EXEMPT_CATEGORY_NAMES:
                continue
            include, _reason, _cname = should_include_element(
                elem=_CategoryOnlyProbe(cat), doc=doc, source_type="HOST"
            )
            if include and is_colorable(cat):
                continue  # gets a real assigned color; nothing to force
            target_ids.append(cat_id_int)
        except Exception as ex:
            if diag is not None:
                diag.warn(
                    phase="color_id_buffer",
                    callsite="force_white_target_scan",
                    message="Category could not be evaluated for the force-white safety "
                            "net; it may render with an uncontrolled native color in this "
                            "capture: {0}".format(ex),
                    view_id=view_id,
                )
    return sorted(target_ids)


def _force_white_category(view, cat_id_int, solid_pattern_id, snapshots, diag=None, view_id=None):
    """Paint one category UNCOLORABLE_SENTINEL_RGB, snapshotting what it replaces.

    ``snapshots`` is the shared {cat_id_int: field snapshot} dict restore reads
    back. A category already present in it keeps its FIRST snapshot: by the
    time a LINK category filter fails and lands here, the halftone
    neutralization pass may already have rewritten that category's overrides,
    and re-snapshotting would capture Stage A's own mutation as if it were the
    user's original state.

    Returns True when the override was applied.
    """
    from Autodesk.Revit.DB import Color, ElementId
    try:
        cat_id = ElementId(int(cat_id_int))
        cat_ogs = view.GetCategoryOverrides(cat_id)
        if cat_id_int not in snapshots:
            snapshots[cat_id_int] = _capture_category_ogs_fields(cat_ogs)
        _apply_flat_color_to_ogs(
            cat_ogs, solid_pattern_id,
            Color(*[int(c) for c in UNCOLORABLE_SENTINEL_RGB]),
        )
        view.SetCategoryOverrides(cat_id, cat_ogs)
        return True
    except Exception as ex:
        if diag is not None:
            diag.warn(
                phase="color_id_buffer",
                callsite="force_white_category",
                message="Category {0} could not be force-whited; its uncontrolled content "
                        "may render a native color that aliases an assigned palette "
                        "ID: {1}".format(cat_id_int, ex),
                view_id=view_id,
            )
        return False


def _force_white_after_halftone(
    view, cat_id_int, solid_pattern_id, snapshots, halftone_state,
    diag=None, view_id=None,
):
    """Force-white a category the halftone neutralization pass already touched.

    Only one category can be in this position: a colorable LINK category whose
    filter turned out to fail. It was in categories_touched, so the halftone
    pass set its Halftone False before _apply_link_category_filters revealed
    it needed the safety net after all -- and a snapshot taken now would
    record Stage A's own mutation as the user's original state.

    Resolves it by ownership rather than by restore ordering: the true
    original halftone moves OUT of ``halftone_state`` and INTO the force-white
    snapshot, so exactly one restore path writes this category back. Two paths
    that both write it would have to run in the right order to agree, which is
    the kind of implicit coupling this module's restore block has been bitten
    by before.

    Returns True when the override was applied.
    """
    pre_halftone = halftone_state.pop(cat_id_int, _MISSING_OGS_FIELD)
    applied = _force_white_category(
        view, cat_id_int, solid_pattern_id, snapshots, diag=diag, view_id=view_id
    )
    if pre_halftone is not _MISSING_OGS_FIELD:
        if cat_id_int in snapshots:
            snapshots[cat_id_int]["Halftone"] = bool(pre_halftone)
        else:
            # No snapshot was taken (reading the overrides failed outright),
            # so the halftone restore is the only one left that can put this
            # category back -- give it back rather than dropping it.
            halftone_state[cat_id_int] = pre_halftone
    return applied


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
    is now the frozen VETTED_COLORABLE_CATEGORY_NAMES whitelist rather than a
    live ParameterFilterUtilities.GetAllFilterableCategories() lookup (see
    _resolve_colorable_category_predicate, which owns that decision for both
    this scan and the force-white safety net). Both checks must run for every
    category: a policy-included-but-uncolorable category still needs to be
    identified and returned, not silently dropped.

    Returns ``(colorable, uncolorable)``: ``colorable`` categories are both
    policy-included AND on the vetted whitelist -- callable code can color
    their LINK elements via a category filter. ``uncolorable`` categories are
    policy-included but not colorable; they are returned for reporting only.
    Suppressing them is no longer this function's caller's job: the
    category-level force-white safety net
    (_category_force_white_targets/_force_white_category) already covers every
    CategoryType.Model category that does not receive a real assigned color,
    which is exactly this set plus the policy-excluded ones. That replaces the
    retired "hide the whole link instance" mechanism, and with it the
    whole-instance collateral damage it caused when one uncolorable category
    took down every colorable category sharing a placement.

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
    _resolve_colorable_category_predicate() -- passed in rather than resolved
    here so this scan and the force-white safety net can never disagree about
    a category (a category both of them claimed would be colored and whited;
    one neither claimed would render uncontrolled).

    Returns ``(colorable, uncolorable)``: ordered lists of Category objects,
    sorted by Name for a deterministic, reproducible palette-slot assignment
    order. ``colorable`` categories get a ParameterFilterElement (see
    _apply_link_category_filters). ``uncolorable`` ones are returned for
    reporting only -- the category-level force-white override already covers
    them, so unlike the retired hide-instance mechanism the caller has nothing
    further to do about them.
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
                    message="Category '{0}' filter could not be created/applied; this "
                            "category will be hidden for this view's capture instead of "
                            "rendering with an uncontrolled native color: {1}".format(cat_name, ex),
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


def _collect_near_face_w_data(
    doc, view, raster, cfg, resolved_ids, link_category_color_map,
    diag=None, view_id=None, link_proxies=None,
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
    instances at all now (see _category_force_white_targets).

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
    """
    from .revit.collection import resolve_element_bbox, project_bbox_uv_and_near_face_w

    vb = getattr(raster, "view_basis", None) if raster is not None else None

    host_out = {}
    for eid in resolved_ids:
        elem = doc.GetElement(eid)
        if elem is None:
            continue
        elem_id_int = eid.IntegerValue
        cat = getattr(elem, "Category", None)
        category_name = getattr(cat, "Name", None) if cat is not None else None
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
    # {category_id_int: _capture_category_ogs_fields() snapshot} for every
    # category the force-white safety net paints UNCOLORABLE_SENTINEL_RGB.
    # Plain data only, captured before the first mutation of that category and
    # never re-captured afterward -- same live-API-object-across-transaction
    # caution as orig_smooth_edges/orig_crop_box below.
    category_force_white_state = {}
    force_white_category_ids = []
    force_white_failures = 0
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

    # Background (Graphic Display Options > Background). Revit exposes this as
    # View.GetBackground()/SetBackground(ViewDisplayBackground) since 2014, and
    # SetBackground is only accepted on 3D, section and elevation views -- a
    # plan view's canvas color is an application-level Options setting with no
    # API surface at all, so BACKGROUND_SENTINEL_RGB simply cannot be applied
    # there and the attempt below records "unchanged (unsupported)" rather than
    # pretending otherwise.
    #
    # Captured as three plain RGB triples (sky/horizon/ground) plus ImagePath,
    # never as the live ViewDisplayBackground object -- same caution as
    # orig_smooth_edges/orig_crop_box. ViewDisplayBackground exposes no public
    # constructor, only ViewDisplayBackground.CreateGradient(sky, horizon,
    # ground) and the image variant, so an IMAGE background cannot be faithfully
    # rebuilt from a gradient capture. When one is in use the suppression is
    # skipped entirely rather than risk replacing the user's image background
    # with a flat grey permanently: an unsuppressed background costs this
    # capture a sentinel, a bad restore costs the user their view.
    orig_background_rgbs = None
    orig_background_image_path = None
    try:
        _bg = view.GetBackground()
        if _bg is not None:
            orig_background_image_path = getattr(_bg, "ImagePath", None) or None
            _sky = _color_to_rgb_or_none(getattr(_bg, "SkyColor", None))
            _horizon = _color_to_rgb_or_none(getattr(_bg, "HorizonColor", None))
            _ground = _color_to_rgb_or_none(getattr(_bg, "GroundColor", None))
            if None not in (_sky, _horizon, _ground):
                orig_background_rgbs = (_sky, _horizon, _ground)
    except Exception as ex:
        if diag is not None:
            diag.warn(
                phase="color_id_buffer",
                callsite="background_capture",
                message="Could not read this view's background; the background sentinel "
                        "will not be applied and the export keeps Revit's default "
                        "canvas: {0}".format(ex),
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

        # Force the canvas to BACKGROUND_SENTINEL_RGB, following the same
        # capture/apply/restore shape as shadows and smooth edges above.
        # A solid color is expressed as a gradient with all three stops equal
        # -- ViewDisplayBackground has no solid-color creator, and Revit
        # renders an all-equal gradient as a flat fill. Skipped outright when
        # the capture above could not produce a restorable gradient (an image
        # background, or an unreadable one), since restoring is the part that
        # must not fail.
        applied_background = "unchanged"
        if orig_background_image_path:
            applied_background = "unchanged (image background)"
        elif orig_background_rgbs is None:
            applied_background = "unchanged (not captured)"
        else:
            try:
                from Autodesk.Revit.DB import ViewDisplayBackground
                sentinel = Color(*[int(c) for c in BACKGROUND_SENTINEL_RGB])
                view.SetBackground(
                    ViewDisplayBackground.CreateGradient(sentinel, sentinel, sentinel)
                )
                applied_background = list(BACKGROUND_SENTINEL_RGB)
            except Exception as ex:
                # Expected on plan views, which reject SetBackground outright.
                applied_background = "unchanged (failed)"
                if diag is not None:
                    diag.warn(
                        phase="color_id_buffer",
                        callsite="background",
                        message="Could not apply the background sentinel (expected on view "
                                "types Revit does not allow a background on, e.g. plan "
                                "views); the export keeps Revit's default canvas "
                                "color: {0}".format(ex),
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

        # One colorability answer for this whole capture: LINK category-filter
        # discovery below and the force-white safety net further down both read
        # the SAME predicate, so no category can be claimed by both or by
        # neither. See _resolve_colorable_category_predicate.
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
                        "colorable whitelist and get no assigned color; they are covered "
                        "by the category-level force-white safety net instead of hiding "
                        "their link instances: {1}".format(
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

        # Force-white safety net: every CategoryType.Model category this
        # capture will NOT hand a real assigned color gets a category-level
        # UNCOLORABLE_SENTINEL_RGB override -- HOST and LINK alike. This
        # replaces the retired hide-instance mechanism entirely, and closes
        # the separate HOST-side gap where policy-EXCLUDED but
        # CategoryType.Model categories (Rooms, Areas, MEP Spaces, Point
        # Clouds) rendered with native, uncontrolled color because
        # _hidden_category_state only covers CategoryType.Annotation plus
        # VIEW_ONLY_MODEL_BIC_NAMES. See _category_force_white_targets.
        force_white_category_ids = _category_force_white_targets(
            doc, is_colorable, already_hidden_ids=category_hidden_state.keys(),
            diag=diag, view_id=view_id,
        )
        force_white_ids_set = set(force_white_category_ids)

        # Halftone neutralization runs only for categories that are NOT
        # force-whited: a force-whited category's override already sets
        # Halftone False as part of the flat-color paint, and its full field
        # snapshot (taken inside _force_white_category, before any mutation)
        # is what restores its halftone. Running both on one category would
        # let the halftone pass mutate it first and the force-white snapshot
        # then capture Stage A's own mutation as the user's original state.
        for cat_id_int in categories_touched:
            if cat_id_int in force_white_ids_set:
                continue
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

        for cat_id_int in force_white_category_ids:
            if not _force_white_category(
                view, cat_id_int, solid_pattern_id, category_force_white_state,
                diag=diag, view_id=view_id,
            ):
                force_white_failures += 1

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

        # A colorable category whose filter could not actually be created or
        # applied is the one gap the force-white target scan cannot see in
        # advance: it IS on the vetted whitelist, so the scan correctly
        # excluded it as "gets a real assigned color", and only
        # _apply_link_category_filters knows it did not. Force-white it now,
        # with the same override mechanism, so it falls back into the safety
        # net rather than out of it. The snapshot dict is shared, and
        # _force_white_category keeps the FIRST snapshot per category, so a
        # category the halftone pass already touched restores correctly.
        for cat in failed_link_categories:
            failed_cat_id = _category_id_int(cat)
            if failed_cat_id is None or failed_cat_id in force_white_ids_set:
                continue
            force_white_ids_set.add(failed_cat_id)
            force_white_category_ids.append(failed_cat_id)
            # The halftone pass above already touched this category (it is a
            # colorable LINK category, so it was in categories_touched) --
            # see _force_white_after_halftone for why that needs handling.
            if not _force_white_after_halftone(
                view, failed_cat_id, solid_pattern_id, category_force_white_state,
                category_halftone_state, diag=diag, view_id=view_id,
            ):
                force_white_failures += 1

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

        # Gated on the sentinel actually having been applied, not merely on a
        # capture existing: a plain view rejects SetBackground outright, and
        # attempting the restore there would log a restore ERROR on every such
        # view for a mutation that never happened.
        if isinstance(applied_background, list):
            def _restore_background():
                from Autodesk.Revit.DB import ViewDisplayBackground
                sky, horizon, ground = orig_background_rgbs
                view.SetBackground(ViewDisplayBackground.CreateGradient(
                    Color(int(sky[0]), int(sky[1]), int(sky[2])),
                    Color(int(horizon[0]), int(horizon[1]), int(horizon[2])),
                    Color(int(ground[0]), int(ground[1]), int(ground[2])),
                ))
            _restore_step("restore_background", _restore_background)

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

        # Put back exactly the category-override fields the force-white safety
        # net overwrote, from the plain-data snapshot taken before the first
        # mutation of each category. Disjoint from category_halftone_state
        # above by construction (the halftone pass skips force-white targets,
        # and _force_white_after_halftone moves the one category that can end
        # up in both), so these two loops never write the same category and
        # their relative order does not matter. Every other field on that category's
        # overrides (line weights, line patterns, ...) was never touched, so
        # nothing else needs restoring -- which is why this is a field-level
        # write-back rather than the blank-OverrideGraphicSettings() reset the
        # element-level restore above can safely use.
        for cat_id_int, snapshot in category_force_white_state.items():
            def _restore_force_white(cat_id_int=cat_id_int, snapshot=snapshot):
                cat_id = ElementId(int(cat_id_int))
                cat_ogs = view.GetCategoryOverrides(cat_id)
                unrestored = _restore_category_ogs_fields(cat_ogs, snapshot)
                view.SetCategoryOverrides(cat_id, cat_ogs)
                if unrestored:
                    raise RuntimeError(
                        "Category {0}: force-white override restored, but these fields "
                        "could not be written back and keep Stage A's values: "
                        "{1}".format(cat_id_int, unrestored)
                    )
            _restore_step("restore_category_force_white", _restore_force_white)

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
        # Phase 1b: additive near-face-W + UV bbox footprint per HOST/LINK
        # element -- see _collect_near_face_w_data's docstring. Consumed by
        # tools/link_identity_resolver.py; never read by decode_stage_a_
        # color_id.py and never modifies color_assignment_map/
        # link_category_color_map above.
        "near_face_w_map": near_face_w_map,
        "applied_display_style": applied_display_style,
        "applied_smooth_edges": applied_smooth_edges,
        "applied_show_shadows": applied_show_shadows,
        # The two reserved sentinel colors this capture used, recorded so a
        # decoder reads them from the sidecar rather than hardcoding them.
        # "uncolorable" is what every force-whited category renders;
        # "background" is the canvas, and is only meaningful when
        # applied_background below is a list (the value was actually applied).
        "sentinel_colors": {
            "uncolorable": list(UNCOLORABLE_SENTINEL_RGB),
            "background": list(BACKGROUND_SENTINEL_RGB),
        },
        "applied_background": applied_background,
        # Where the "can Stage A color this category?" answer came from:
        # "frozen_whitelist" (VETTED_COLORABLE_CATEGORY_NAMES) or
        # "live_filterable_lookup" (the pre-freeze fallback -- a capture
        # recording this is NOT reproducible from the source tree alone).
        "colorable_category_source": colorable_source,
        # Categories painted UNCOLORABLE_SENTINEL_RGB at the category level,
        # and how many of those overrides Revit refused. A non-zero failure
        # count means some uncontrolled content may still render a native
        # color in this capture.
        "force_white_category_ids": sorted(force_white_category_ids),
        "force_white_failures": force_white_failures,
        # Policy-included LINK categories that are not on the vetted colorable
        # whitelist. They receive no assigned color; the force-white net above
        # covers them. Reported for auditability -- a category showing up here
        # that should be colorable means the whitelist needs recapturing.
        "uncolorable_link_categories": sorted(
            cat.Name for cat in uncolorable_link_categories
        ),
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
