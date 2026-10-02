"""Stage A registration marks and membership white suppression.

PROMOTED from ``tests/dynamo/probe_stage_a_anno_pass_variants.py``, where each
piece was measured before it moved here (tools/notes/ROUND2_ and
ROUND3_ANNO_PASS_VARIANTS_FINDINGS.md). The probe now imports these rather
than keeping its own copies: one implementation, so the probe keeps measuring
what production runs.

  * REGISTRATION MARKS -- detail-line ticks drawn at KNOWN view UV inside the
    crop, in both passes, so post-processing can register the annotation
    capture onto the model lattice. The crop boundary Revit draws cannot do
    this: it lands on the image border and is clipped (round 2).
  * MEMBERSHIP WHITE SUPPRESSION -- every MODEL member gets a white element
    override and linked RVT categories a white view filter, so the annotation
    pass can leave model content VISIBLE (dependent annotation -- tags,
    dimensions -- disappears when its host's category is hidden). The
    category/subcategory layer the probe also measured is NOT here: round 3
    found its images byte-identical without it, at 23-86 s per view.

Nothing here runs on its own; ``export_registered_stage_a_view`` (in
``stage_a_registered_capture.py``) sequences it, behind a Config flag.
"""

import json
import time

from .revit.safe_api import element_id_value as _element_id_int


def _value(value):
    return {"state": "value", "value": value}


def _unavailable(reason):
    return {"state": "unavailable", "reason": reason}


def _xyz_tuple(point):
    return (float(point.X), float(point.Y), float(point.Z))


# REGISTRATION MARKS. Detail lines drawn at KNOWN view UV, just
# inside the crop, and removed by rolling the capture's TransactionGroup back.
# Round 2 showed the crop boundary as Revit draws it is not a ruler: the
# elevation's horizontal edges fall on the image border and are clipped. Marks
# drawn INSIDE the crop cannot be clipped that way, and being drawn by the
# capture their UV is known exactly rather than inferred from a bbox.
#
# Twelve ticks, NOT touching: two per corner (one horizontal, one vertical) and
# one at the middle of each edge. Each is its own connected component, so the
# model capture (where all twelve share MARK_COLOUR) can be split into ticks
# without knowing the mapping. Horizontal ticks give v (their centre row),
# vertical ones u (their centre column): SIX points per axis at THREE levels.
#
# Why three levels. Round 3 had two per axis, and both ticks at one level sat
# on the same pixel row -- four points collapsed to two, every fit came back
# with residual 0.00, and the residual said nothing about the scale. A third
# level is the smallest change that gives the fit something to disagree with.
#
# Every tick is kept inside the outer THIRD of its axis (corner ticks) or on the
# centre line (mid ticks), so the analyzer can assign model-capture components
# by image thirds. Sizes are in MODEL-LATTICE pixels, via achieved_fpp_ft.
MARK_COLOUR = (139, 251, 11)
MARK_INSET_PX = 24.0
MARK_GAP_PX = 8.0
# T1 (2026-09-29): ticks at their minimum size. 96 px arms were probe-era
# generosity; 32 px is the smallest arm registration_marks is proven to
# locate, including on a capture drawn at 0.63x (ModelCallout's annotation
# scale) -- tests/test_register_stage_a_annotation.py pins that.
MARK_ARM_PX = 32.0
MARK_MIN_ARM_PX = 32.0
MARK_CORNERS = (("left_bottom", 1.0, 1.0), ("right_bottom", -1.0, 1.0),
                ("left_top", 1.0, -1.0), ("right_top", -1.0, -1.0))
# Mid-edge ticks: (name, orientation, which edge the tick starts from, sign).
# WHERE the middle level sits, as a fraction of the reference rectangle from
# its min corner. NOT 0.5: every mid-edge tick ever lost (elevation, two runs;
# plan, one) lay within half a pixel of its image's exact centre line, while
# Revit's own audit found each one in the view, unhidden and unfiltered
# (probe_0928_0918). 0.4 keeps the level in the middle THIRD -- which the
# model-capture component split needs -- and off both centre lines.
MARK_MID_FRACTION = 0.4
MARK_MIDS = (("left_mid", "horizontal", "u0", 1.0),
             ("right_mid", "horizontal", "u1", -1.0),
             ("mid_bottom", "vertical", "v0", 1.0),
             ("mid_top", "vertical", "v1", -1.0))


def registration_mark_segments(reference_uv, fpp_ft, inset_px=MARK_INSET_PX,
                               gap_px=MARK_GAP_PX, arm_px=MARK_ARM_PX,
                               min_arm_px=MARK_MIN_ARM_PX):
    """The twelve registration ticks for a reference rectangle, in view UV. PURE.

    Two ticks per corner, set in ``inset_px`` from both crop edges, each
    starting ``gap_px`` from the corner point so the pair never touches: a
    horizontal tick (constant v, the ruler for v) and a vertical one (constant
    u, the ruler for u). Plus one tick at the middle of each edge -- horizontal
    on the left and right edges at the centre v, vertical on the bottom and top
    edges at the centre u -- the third level per axis. The arm shrinks so every
    corner tick stays inside the outer third of each axis, never below
    ``min_arm_px``; a crop too small for that is REFUSED rather than drawn with
    ticks the analyzer could not separate.

    Returns ``{"state": "value", "segments": [...], ...}`` or
    ``{"state": "unavailable", "reason": ...}``.
    """
    if not reference_uv or len(reference_uv) != 4 or not fpp_ft or fpp_ft <= 0:
        return _unavailable("no reference rectangle or lattice to place the marks "
                            "on (reference={0!r}, fpp_ft={1!r})".format(
                                reference_uv, fpp_ft))
    u0, v0, u1, v1 = (float(v) for v in reference_uv)
    if u1 <= u0 or v1 <= v0:
        return _unavailable("the reference rectangle is empty: {0!r}".format(
            reference_uv))
    fpp = float(fpp_ft)
    extent_px = min((u1 - u0) / fpp, (v1 - v0) / fpp)
    # A corner tick must end inside the outer third of its axis: the model
    # capture's ticks are told apart by image thirds (top/mid/bottom rows for
    # horizontal ticks, left/mid/right columns for vertical ones).
    room = extent_px / 3.0 - inset_px - gap_px
    arm = min(float(arm_px), room)
    if arm < float(min_arm_px):
        return _unavailable(
            "the reference rectangle is {0:.0f} px on its short side, too small for "
            "{1:.0f} px ticks inset {2:.0f} px".format(extent_px, min_arm_px,
                                                         inset_px))
    inset, gap, arm_ft = inset_px * fpp, gap_px * fpp, arm * fpp
    segments = []
    for corner, su, sv in MARK_CORNERS:
        cu = (u0 + inset) if su > 0 else (u1 - inset)
        cv = (v0 + inset) if sv > 0 else (v1 - inset)
        h_span = sorted((cu + su * gap, cu + su * (gap + arm_ft)))
        v_span = sorted((cv + sv * gap, cv + sv * (gap + arm_ft)))
        segments.append({"key": corner + "_h", "corner": corner,
                         "orientation": "horizontal", "level_uv": cv,
                         "span_uv": h_span,
                         "uv0": [h_span[0], cv], "uv1": [h_span[1], cv]})
        segments.append({"key": corner + "_v", "corner": corner,
                         "orientation": "vertical", "level_uv": cu,
                         "span_uv": v_span,
                         "uv0": [cu, v_span[0]], "uv1": [cu, v_span[1]]})
    edges = {"u0": u0 + inset, "u1": u1 - inset, "v0": v0 + inset, "v1": v1 - inset}
    mid_u = u0 + MARK_MID_FRACTION * (u1 - u0)
    mid_v = v0 + MARK_MID_FRACTION * (v1 - v0)
    for name, orientation, edge, sign in MARK_MIDS:
        start = edges[edge]
        span = sorted((start + sign * gap, start + sign * (gap + arm_ft)))
        if orientation == "horizontal":
            segments.append({"key": name + "_h", "corner": name,
                             "orientation": "horizontal", "level_uv": mid_v,
                             "span_uv": span,
                             "uv0": [span[0], mid_v], "uv1": [span[1], mid_v]})
        else:
            segments.append({"key": name + "_v", "corner": name,
                             "orientation": "vertical", "level_uv": mid_u,
                             "span_uv": span,
                             "uv0": [mid_u, span[0]], "uv1": [mid_u, span[1]]})
    return {"state": "value", "segments": segments,
            "reference_uv": [u0, v0, u1, v1], "fpp_ft": fpp,
            "inset_px": float(inset_px), "gap_px": float(gap_px),
            "arm_px": arm, "colour": list(MARK_COLOUR)}


# RELOCATION (Greg, 2026-09-30): a tick drawn under an annotation element is
# invisible in the annotation capture -- pipeline_0930_0739 lost ModelCallout's
# right_bottom_h under a tag and Plan_CropActive's mid_bottom_v under a
# dimension. Nothing requires a tick to sit on the crop edge; what the fit and
# the locator need is SPREAD: three levels per axis, each tick its own
# component, and each tick classifiable by tools/registration_marks.py's
# _locate -- horizontal ticks by left/right HALF and top/mid/bottom THIRD,
# vertical ones by left/mid/right third and top/bottom half. So a covered
# tick moves to the nearest clear position inside a window that keeps it in
# its half and well inside its third. Visible in a sub-optimal place beats
# invisible in the ideal one.
#
# Corner ticks stay in the outer SIXTH across their axis, mid ticks in the
# central QUARTER band: a margin inside each third, because the locator
# measures thirds on the ticks' OWN pixel extent, which moving a tick changes.
_CORNER_BAND = 1.0 / 6.0
_MID_BAND = (0.375, 0.625)
_RELOCATION_PAD_PX = 2.0


def _rects_overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _segment_rect(seg, pad_ft):
    (ua, va), (ub, vb) = seg["uv0"], seg["uv1"]
    return (min(ua, ub) - pad_ft, min(va, vb) - pad_ft,
            max(ua, ub) + pad_ft, max(va, vb) + pad_ft)


def _tick_window(seg, reference_uv, inset_ft):
    """``((along_lo, along_hi), (across_lo, across_hi))`` in UV: where the
    tick's SEGMENT may lie along its own axis, and where its LEVEL may lie
    across it. See the relocation note above."""
    u0, v0, u1, v1 = reference_uv
    w, h = u1 - u0, v1 - v0
    name = seg["corner"]
    if seg["orientation"] == "horizontal":
        um = u0 + w / 2.0
        along = (u0 + inset_ft, um) if name.startswith("left") else (um, u1 - inset_ft)
        if name.endswith("bottom"):
            across = (v0 + inset_ft, v0 + _CORNER_BAND * h)
        elif name.endswith("top"):
            across = (v1 - _CORNER_BAND * h, v1 - inset_ft)
        else:
            across = (v0 + _MID_BAND[0] * h, v0 + _MID_BAND[1] * h)
    else:
        vm = v0 + h / 2.0
        along = (v0 + inset_ft, vm) if name.endswith("bottom") else (vm, v1 - inset_ft)
        if name.startswith("left"):
            across = (u0 + inset_ft, u0 + _CORNER_BAND * w)
        elif name.startswith("right"):
            across = (u1 - _CORNER_BAND * w, u1 - inset_ft)
        else:
            across = (u0 + _MID_BAND[0] * w, u0 + _MID_BAND[1] * w)
    return along, across


def _placed(seg, along_lo, level):
    """``seg`` moved so its span starts at ``along_lo`` and sits at ``level``."""
    length = seg["span_uv"][1] - seg["span_uv"][0]
    out = dict(seg)
    out["span_uv"] = [along_lo, along_lo + length]
    out["level_uv"] = level
    if seg["orientation"] == "horizontal":
        out["uv0"], out["uv1"] = [along_lo, level], [along_lo + length, level]
    else:
        out["uv0"], out["uv1"] = [level, along_lo], [level, along_lo + length]
    return out


def relocate_marks_clear_of(layout, avoid):
    """Move every tick that overlaps an ``avoid`` rectangle to the nearest
    clear position in its window. PURE.

    ``avoid`` is ``[(element_id, [umin, vmin, umax, vmax]), ...]`` in view UV
    (the annotation elements' view bboxes). Each segment gains ``placement``:
    ``"original"`` (already clear), ``"moved"`` (with ``moved_px``) or
    ``"blocked"`` (no clear position; left where it was, with
    ``covered_by``). A layout that is not a value is returned unchanged.
    """
    if (layout or {}).get("state") != "value" or not avoid:
        return layout
    fpp = float(layout["fpp_ft"])
    ref = layout["reference_uv"]
    inset_ft = float(layout["inset_px"]) * fpp
    gap_ft = float(layout["gap_px"]) * fpp
    pad_ft = _RELOCATION_PAD_PX * fpp
    step = max(float(layout["arm_px"]) / 4.0, 2.0) * fpp
    rects = [(eid, tuple(r)) for eid, r in avoid if r and len(r) == 4]

    def _covering(seg):
        r = _segment_rect(seg, pad_ft)
        return [eid for eid, box in rects if _rects_overlap(r, box)]

    out = dict(layout)
    placed = []
    moved = blocked = 0
    for seg in layout["segments"]:
        covered = _covering(seg)
        if not covered:
            placed.append(dict(seg, placement="original"))
            continue
        (a_lo, a_hi), (c_lo, c_hi) = _tick_window(seg, ref, inset_ft)
        length = seg["span_uv"][1] - seg["span_uv"][0]
        start0, level0 = seg["span_uv"][0], seg["level_uv"]
        n_along = int((a_hi - a_lo) / step) + 1
        n_across = int((c_hi - c_lo) / step) + 1
        candidates = []
        for i in range(-n_along, n_along + 1):
            for j in range(-n_across, n_across + 1):
                start, level = start0 + i * step, level0 + j * step
                if start < a_lo or start + length > a_hi or not c_lo <= level <= c_hi:
                    continue
                candidates.append((i * i + j * j, i, j, start, level))
        candidates.sort()
        choice = None
        others = [p for p in placed] + [s for s in layout["segments"][len(placed) + 1:]]
        for _d, i, j, start, level in candidates:
            trial = _placed(seg, start, level)
            if _covering(trial):
                continue
            box = _segment_rect(trial, gap_ft)
            if any(_rects_overlap(box, _segment_rect(o, 0.0)) for o in others):
                continue
            choice = (trial, i, j)
            break
        if choice is None:
            blocked += 1
            placed.append(dict(seg, placement="blocked", covered_by=covered[:20]))
        else:
            moved += 1
            trial, i, j = choice
            placed.append(dict(trial, placement="moved", moved_from_uv=[seg["uv0"], seg["uv1"]],
                               moved_px=[round((trial["uv0"][0] - seg["uv0"][0]) / fpp, 3),
                                         round((trial["uv0"][1] - seg["uv0"][1]) / fpp, 3)],
                               was_covered_by=covered[:20]))
    out["segments"] = placed
    out["relocation"] = {"avoid_rects": len(rects), "moved": moved, "blocked": blocked}
    return out


WHITE_OVERRIDE_SETTERS = (
    "SetProjectionLineColor", "SetCutLineColor",
    "SetSurfaceForegroundPatternId", "SetSurfaceForegroundPatternColor",
    "SetSurfaceForegroundPatternVisible",
    "SetSurfaceBackgroundPatternId", "SetSurfaceBackgroundPatternColor",
    "SetSurfaceBackgroundPatternVisible",
    "SetCutForegroundPatternId", "SetCutForegroundPatternColor",
    "SetCutForegroundPatternVisible",
    "SetCutBackgroundPatternId", "SetCutBackgroundPatternColor",
    "SetCutBackgroundPatternVisible",
    "SetSurfaceTransparency", "SetHalftone",
)


def missing_override_setters(ogs_like, setters=None):
    """PURE. Which of ``setters`` ``ogs_like`` does not expose.

    Split out of the preflight below so the DECISION is testable without a
    Revit host -- the preflight's own value is that it decides correctly, and a
    function that can only be exercised inside Revit is a function whose
    decision is never checked.
    """
    return [name for name in (setters or WHITE_OVERRIDE_SETTERS)
            if getattr(ogs_like, name, None) is None]


def white_override_capability_record(
        ogs_like, solid_pattern_id, solid_pattern_error=None):
    """PURE. The capability record, from an OGS-like object and a pattern id.

    ``state`` is "value" only when every setter resolves AND a solid pattern id
    was supplied. Anything else LISTS what is missing, because "suppression was
    skipped" is not actionable and "SetCutBackgroundPatternId is absent on this
    host" is.
    """
    missing = missing_override_setters(ogs_like)
    reason_parts = []
    if missing:
        reason_parts.append(
            "OverrideGraphicSettings is missing: " + ", ".join(missing))
    if solid_pattern_error:
        reason_parts.append(str(solid_pattern_error))
    elif solid_pattern_id is None:
        reason_parts.append(
            "no solid DRAFTING fill pattern exists in this project, so a white "
            "surface/cut pattern override cannot be built")
    if reason_parts:
        return {"state": "unavailable", "reason": "; ".join(reason_parts),
                "missing_setters": missing,
                "solid_pattern_reason": (
                    str(solid_pattern_error) if solid_pattern_error
                    else (None if solid_pattern_id is not None
                          else reason_parts[-1]))}
    return {"state": "value", "missing_setters": [],
            "solid_pattern_id": solid_pattern_id}


def white_override_capability(doc):
    """PREFLIGHT: can a white override actually be built on this host?

    Opens no transaction and touches no view -- an ``OverrideGraphicSettings``
    is a plain API object -- so this runs before any capture and decides
    whether membership white suppression can run at all.

    A missing pattern setter leaves that pattern UNCHANGED rather than raising
    at the point of use, so suppression built without it would render model
    fills in their authored colour while the record said white was applied: a
    capture that suppressed nothing and reported success.
    """
    from Autodesk.Revit.DB import OverrideGraphicSettings
    from .color_id_buffer import _get_solid_pattern_id

    try:
        probe_ogs = OverrideGraphicSettings()
    except Exception as ex:
        return {"state": "unavailable",
                "reason": "OverrideGraphicSettings() raised {0}: {1}".format(
                    type(ex).__name__, ex),
                "missing_setters": list(WHITE_OVERRIDE_SETTERS)}
    solid_id = None
    solid_error = None
    try:
        solid_id = _get_solid_pattern_id(doc)
    except Exception as ex:
        solid_error = "_get_solid_pattern_id raised {0}: {1}".format(
            type(ex).__name__, ex)
    return white_override_capability_record(
        probe_ogs,
        None if solid_id is None else _element_id_int(solid_id),
        solid_pattern_error=solid_error)


WHITE = (255, 255, 255)


def flat_colour_override(doc, colour=WHITE):
    """A solid ``OverrideGraphicSettings`` in ``colour`` (white by default):
    projection AND cut lines, and all four patterns.

    What suppresses model members to white, and what paints the registration
    marks (and the probe's F2 fiducials) their reserved colour. CUT graphics
    too, unlike ``color_id_buffer._build_flat_color_ogs``: without them a cut
    element (a wall in plan) keeps any white category cut override and shows
    only its projection sliver -- round 2 measured 463 px of a 10.6 x 1 ft
    wall that way.

    Assumes ``white_override_capability(doc)`` already said "value"; it raises
    rather than degrading if that is not so, because a partially-applied white
    override is the failure mode the preflight exists to prevent.
    """
    from Autodesk.Revit.DB import Color, OverrideGraphicSettings
    from .color_id_buffer import _get_solid_pattern_id
    capability = white_override_capability(doc)
    if capability.get("state") != "value":
        raise RuntimeError(
            "the white override cannot be built on this host: {0}".format(
                capability.get("reason")))
    solid_id = _get_solid_pattern_id(doc)
    white = Color(int(colour[0]), int(colour[1]), int(colour[2]))
    ogs = OverrideGraphicSettings()
    ogs.SetProjectionLineColor(white)
    ogs.SetCutLineColor(white)
    # Written out, not looped through getattr: a computed callee is one
    # tools/check_stage_a_no_geometry.py cannot resolve, and it REFUSES rather
    # than certify a path it cannot walk.
    ogs.SetSurfaceForegroundPatternId(solid_id)
    ogs.SetSurfaceForegroundPatternColor(white)
    ogs.SetSurfaceForegroundPatternVisible(True)
    ogs.SetSurfaceBackgroundPatternId(solid_id)
    ogs.SetSurfaceBackgroundPatternColor(white)
    ogs.SetSurfaceBackgroundPatternVisible(True)
    ogs.SetCutForegroundPatternId(solid_id)
    ogs.SetCutForegroundPatternColor(white)
    ogs.SetCutForegroundPatternVisible(True)
    ogs.SetCutBackgroundPatternId(solid_id)
    ogs.SetCutBackgroundPatternColor(white)
    ogs.SetCutBackgroundPatternVisible(True)
    ogs.SetSurfaceTransparency(0)
    ogs.SetHalftone(False)
    return ogs





def white_membership_suppression(doc, view, view_id, model_elements,
                                 link_categories=None, diag=None,
                                 exclude_ids=None, ogs=None):
    """Suppress the MODEL membership set to white: element overrides, and a
    white view filter per linked RVT category. Inside an open Transaction.

    ``model_elements`` is the model half of ``split_stage_a_pass_membership``
    -- the same split the annotation pass paints by, which is the point:
    membership, not category, decides what is suppressed, so a drafting line
    and a model line in the one OST_Lines category are told apart.

    ``link_categories``: linked RVT categories (``discover_link_categories``),
    or None to skip the link mechanism. It is production's own
    ``_apply_link_category_filters``, called with white instead of a palette
    colour -- not a second link mechanism.

    ``exclude_ids``: model members deliberately left untouched, and named.

    Returns a record: what each mechanism reached, and ``unreached`` -- every
    element and link category nothing reached, with the reason. NEVER AN
    ABSENCE: an empty list is a claim, a populated one a bound on it.
    """
    from Autodesk.Revit.DB import ElementId

    timings = {"build_override_ms": 0.0, "element_overrides_ms": 0.0,
               "link_category_filters_ms": 0.0}
    _t = time.time()
    if ogs is None:
        ogs = flat_colour_override(doc)
    timings["build_override_ms"] = (time.time() - _t) * 1000.0
    excluded = set(int(v) for v in (exclude_ids or ()))
    model_elements = [
        elem for elem in (model_elements or [])
        if _element_id_int(getattr(elem, "Id", None)) not in excluded]
    record = {
        "excluded_element_ids": sorted(excluded),
        "element_overrides": {"applied": 0, "attempted": 0, "failed": []},
        "dwg_import_instance_ids": [],
        "link_category_filters": {
            "state": "not_attempted", "categories": [], "created_filter_ids": [],
            "reused_filter_ids": [], "failed_categories": []},
        "classification_errors": [],
        "unreached": [],
    }

    # ---- element-level white override on every MODEL member -----------
    _t = time.time()
    dwg_ids = set()
    for elem in model_elements:
        elem_id = _element_id_int(getattr(elem, "Id", None))
        if elem_id is None:
            record["unreached"].append({
                "kind": "element", "id": None,
                "reason": "element id would not read as an int, so no override "
                          "could be applied"})
            continue
        record["element_overrides"]["attempted"] += 1
        try:
            view.SetElementOverrides(ElementId(int(elem_id)), ogs)
            record["element_overrides"]["applied"] += 1
        except Exception as ex:
            failure = {"id": elem_id,
                       "error": "{0}: {1}".format(type(ex).__name__, ex)}
            record["element_overrides"]["failed"].append(failure)
            record["unreached"].append({
                "kind": "element", "id": elem_id,
                "reason": "SetElementOverrides raised: {0}".format(failure["error"])})
        # DWG ImportInstances are ordinary host elements and take the same
        # element override; recorded separately so "which mechanism reached
        # what" has an answer for them.
        try:
            if type(elem).__name__ == "ImportInstance" or (
                    getattr(elem, "Category", None) is not None
                    and "import" in str(getattr(elem.Category, "Name", "")).lower()):
                dwg_ids.add(elem_id)
        except Exception as ex:
            # Classification only; a miss costs a record line, not the
            # override that already happened above.
            record["classification_errors"].append(
                {"kind": "dwg_classification", "id": elem_id,
                 "error": "{0}: {1}".format(type(ex).__name__, ex)})
    record["dwg_import_instance_ids"] = sorted(dwg_ids)
    timings["element_overrides_ms"] = (time.time() - _t) * 1000.0

    # ---- linked RVT content, via production's own link mechanism ------
    _t = time.time()
    if link_categories:
        from .color_id_buffer import (
            _apply_link_category_filters, _get_solid_pattern_id,
        )
        try:
            colour_map, created, reused, failed = _apply_link_category_filters(
                doc, view, view_id,
                [(cat, WHITE) for cat in link_categories],
                _get_solid_pattern_id(doc), diag=diag)
            record["link_category_filters"] = {
                "state": "value",
                "categories": sorted(colour_map.keys()),
                "created_filter_ids": [int(v) for v in created],
                "reused_filter_ids": [int(v) for v in reused],
                "failed_categories": [str(getattr(c, "Name", c)) for c in failed],
                "note": "production's _apply_link_category_filters, called with "
                        "white instead of a palette colour",
            }
            for cat in failed:
                record["unreached"].append({
                    "kind": "link_category",
                    "id": str(getattr(cat, "Name", cat)),
                    "reason": "the per-category white filter could not be created "
                              "or applied; this category's LINKED elements render "
                              "in their native colour"})
        except Exception as ex:
            record["link_category_filters"] = {
                "state": "unavailable",
                "reason": "{0}: {1}".format(type(ex).__name__, ex)}
            record["unreached"].append({
                "kind": "link_mechanism", "id": None,
                "reason": "the link category filter mechanism raised, so NO linked "
                          "content was suppressed: {0}: {1}".format(
                              type(ex).__name__, ex)})
    timings["link_category_filters_ms"] = (time.time() - _t) * 1000.0

    record["timings_ms"] = dict((k, round(v, 3)) for k, v in timings.items())
    record["api_call_counts"] = {
        "element_override_writes": record["element_overrides"]["attempted"]}
    record["element_override_count"] = record["element_overrides"]["applied"]
    return record


def detail_line_ids(annotation_members):
    """The view's DETAIL LINES: annotation members (view-owned, so claimed by
    OwnerViewId) in OST_Lines. Model lines are not view-owned and are not
    here -- they are model members and the model pass paints them.

    The registered capture leaves OST_Lines VISIBLE in its model pass so the
    registration marks draw, and every other detail line would then draw into
    the model image unpainted: annotation in the model capture, the same
    double-count as a view-specific DWG. These are the lines to hide there.
    The marks are drawn after membership is read, so they are never in this
    set. Returns ``(ids, None)`` or ``(None, reason)``.
    """
    try:
        from Autodesk.Revit.DB import BuiltInCategory
        bic = getattr(BuiltInCategory, "OST_Lines", None)
        if bic is None:
            return None, "BuiltInCategory.OST_Lines did not resolve"
        lines_id = int(bic)
    except Exception as ex:
        return None, "{0}: {1}".format(type(ex).__name__, ex)
    ids = []
    for elem in annotation_members:
        category = getattr(elem, "Category", None)
        cat_id = getattr(getattr(category, "Id", None), "IntegerValue", None)
        if cat_id is not None and int(cat_id) == lines_id:
            elem_id = _element_id_int(getattr(elem, "Id", None))
            if elem_id is not None:
                ids.append(elem_id)
    return sorted(ids), None


def hide_in_view(doc, view, element_ids):
    """Hide ``element_ids`` in ``view``, only those that read as VISIBLE, so
    the caller can show exactly what this hid and nothing the author hid.
    Call inside an open Transaction. Never raises: returns the record, whose
    ``error`` is set when HideElements itself refused."""
    from Autodesk.Revit.DB import ElementId
    import System.Collections.Generic as SCG
    record = {"hidden": [], "already_hidden": [], "unreadable": [], "error": None}
    for elem_id in element_ids:
        try:
            elem = doc.GetElement(ElementId(int(elem_id)))
            if bool(elem.IsHidden(view)):
                record["already_hidden"].append(elem_id)
            else:
                record["hidden"].append(elem_id)
        except Exception as ex:
            record["unreadable"].append(
                {"id": elem_id, "error": "{0}: {1}".format(type(ex).__name__, ex)})
    if record["hidden"]:
        ids = SCG.List[ElementId]()
        for elem_id in record["hidden"]:
            ids.Add(ElementId(int(elem_id)))
        try:
            view.HideElements(ids)
        except Exception as ex:
            record["error"] = "HideElements raised {0}: {1}".format(
                type(ex).__name__, ex)
            record["hidden"] = []
    return record


def show_in_view(view, element_ids):
    """UnhideElements for exactly ``element_ids``; the reason on failure."""
    if not element_ids:
        return None
    from Autodesk.Revit.DB import ElementId
    import System.Collections.Generic as SCG
    ids = SCG.List[ElementId]()
    for elem_id in element_ids:
        ids.Add(ElementId(int(elem_id)))
    try:
        view.UnhideElements(ids)
        return None
    except Exception as ex:
        return "UnhideElements raised {0}: {1}".format(type(ex).__name__, ex)


def still_hidden(doc, view, element_ids):
    """``(hidden ids, unreadable ids)``: read from the view, never assumed."""
    from Autodesk.Revit.DB import ElementId
    hidden, unreadable = [], []
    for elem_id in element_ids:
        try:
            if bool(doc.GetElement(ElementId(int(elem_id))).IsHidden(view)):
                hidden.append(elem_id)
        except Exception:
            unreadable.append(elem_id)
    return hidden, unreadable


def line_ids_in_view(doc, view):
    """Sorted ids of the OST_Lines elements FilteredElementCollector(doc,
    view.Id) returns -- the lines the view DRAWS (a hidden category's
    elements are not returned). Raises when it cannot be read."""
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector
    lines_id = int(BuiltInCategory.OST_Lines)
    ids = []
    for elem in FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType():
        cat_id = getattr(getattr(getattr(elem, "Category", None), "Id", None),
                         "IntegerValue", None)
        if cat_id is not None and int(cat_id) == lines_id:
            elem_id = _element_id_int(getattr(elem, "Id", None))
            if elem_id is not None:
                ids.append(elem_id)
    return sorted(ids)


def set_lines_category_hidden(view, hidden):
    """view.SetCategoryHidden(OST_Lines, hidden). Inside an open Transaction."""
    from Autodesk.Revit.DB import BuiltInCategory, ElementId
    view.SetCategoryHidden(ElementId(int(BuiltInCategory.OST_Lines)), bool(hidden))


def _lines_category_hidden(view):
    """Whether the view hides OST_Lines, three-valued. The marks are detail
    lines: a view that hides Lines draws none of them, in either pass."""
    try:
        from Autodesk.Revit.DB import BuiltInCategory, ElementId
        bic = getattr(BuiltInCategory, "OST_Lines", None)
        if bic is None:
            return None, "BuiltInCategory.OST_Lines did not resolve"
        return bool(view.GetCategoryHidden(ElementId(int(bic)))), None
    except Exception as ex:
        return None, "{0}: {1}".format(type(ex).__name__, ex)


def _thinnest_line_style(doc, curve, view=None):
    """``(style, record)``: the thinnest line style this detail curve may take
    THAT THE VIEW DRAWS.

    No override in either pass sets a line weight (both paint colour and
    patterns only), so a tick draws at its LINE STYLE's projection weight --
    which was never chosen, only inherited from the document's default detail
    line style. T1 picks the minimum over ``curve.GetLineStyleIds()``, keeping
    the current style on a tie. A style whose weight will not read is skipped
    and named; ``style`` is None when nothing could be chosen.

    VISIBILITY (capture-state probe Q6, 2026-10-01). A line style is a
    SUBCATEGORY of OST_Lines, and a view can hide it while the parent stays
    visible: in 5823803 and 11999340 the view template hides "<Thin Lines>",
    the weight-1 style T1 always picked, so no tick drew in 31 views and
    nothing said so -- the only check was the parent category. With ``view``,
    a style whose own subcategory the view hides is NOT a candidate, and one
    whose hidden state will not read is not either (it is named under
    ``hidden_unreadable``); ``hidden`` lists the ones refused. ``view=None``
    is T1's unconditional choice, kept for callers with no view.
    """
    from Autodesk.Revit.DB import GraphicsStyleType
    record = {"state": "value", "unreadable": [], "hidden": [],
              "hidden_unreadable": []}
    current = curve.LineStyle
    current_id = _element_id_int(getattr(current, "Id", None))
    candidates = []
    for style_id in curve.GetLineStyleIds():
        style = doc.GetElement(style_id)
        try:
            weight = int(style.GraphicsStyleCategory.GetLineWeight(
                GraphicsStyleType.Projection))
        except Exception as ex:
            record["unreadable"].append({
                "id": _element_id_int(style_id),
                "error": "{0}: {1}".format(type(ex).__name__, ex)})
            continue
        style_int = _element_id_int(getattr(style, "Id", None))
        if style_int == current_id:
            record["default_style"] = {"id": style_int,
                                       "name": getattr(style, "Name", None),
                                       "projection_line_weight": weight}
        if view is not None:
            # SOLID ONLY (Q6 run 20261001T183711): on 5823803 A picked
            # <Overhead>, a dashed style, and its ticks changed 92 px where the
            # solid temporary subcategory changed 141. A dashed tick is several
            # components, each read as a tick by the fit.
            solid, solid_error = _style_pattern_is_solid(style)
            if solid is not True:
                entry = {"id": style_int, "name": getattr(style, "Name", None),
                         "projection_line_weight": weight}
                if solid_error is not None:
                    entry["error"] = solid_error
                record.setdefault("not_solid", []).append(entry)
                continue
            hidden, error = _style_subcategory_hidden(view, style)
            if hidden is not False:
                entry = {"id": style_int, "name": getattr(style, "Name", None),
                         "projection_line_weight": weight}
                if error is not None:
                    entry["error"] = error
                    record["hidden_unreadable"].append(entry)
                else:
                    record["hidden"].append(entry)
                continue
        candidates.append((weight, 0 if style_int == current_id else 1,
                           style_int, style))
    if not candidates:
        record["state"] = "unavailable"
        record["reason"] = (
            "no line style of the detail curve had a readable weight"
            if not (record["hidden"] or record["hidden_unreadable"]
                    or record.get("not_solid")) else
            "every line style with a readable weight is hidden in this view, is "
            "not solid, or its hidden state or pattern would not read")
        return None, record
    weight, _pref, style_int, style = min(candidates, key=lambda c: c[:3])
    record.update({"id": style_int, "name": getattr(style, "Name", None),
                   "projection_line_weight": weight,
                   "candidates": len(candidates)})
    return style, record


def _solid_line_pattern_id():
    """The id of Revit's built-in Solid line pattern, as an int."""
    from Autodesk.Revit.DB import LinePatternElement
    return _element_id_int(LinePatternElement.GetSolidPatternId())


def _style_pattern_is_solid(style):
    """``(solid, error)``: whether the line style's projection pattern is
    Revit's Solid pattern, three-valued."""
    try:
        from Autodesk.Revit.DB import GraphicsStyleType
        pattern = _element_id_int(style.GraphicsStyleCategory.GetLinePatternId(
            GraphicsStyleType.Projection))
        return pattern == _solid_line_pattern_id(), None
    except Exception as ex:
        return None, "{0}: {1}".format(type(ex).__name__, ex)


def _style_subcategory_hidden(view, style):
    """``(hidden, error)``: whether ``view`` hides the line style's OWN
    subcategory (``style.GraphicsStyleCategory``), three-valued."""
    try:
        return bool(view.GetCategoryHidden(style.GraphicsStyleCategory.Id)), None
    except Exception as ex:
        return None, "{0}: {1}".format(type(ex).__name__, ex)


# A Lines subcategory created for the ticks when the view hides every line
# style the curve could take. Created inside the marks' Transaction, so the
# capture's TransactionGroup rollback removes it with the ticks.
TEMPORARY_TICK_SUBCATEGORY = "VOP Stage A registration ticks"


def _temporary_tick_style(doc, view):
    """``(style, record)``: a weight-1 OST_Lines subcategory made for the
    ticks, and its projection GraphicsStyle. Inside an open Transaction.

    Whether the view DRAWS it is read back, never assumed: a template that
    controls V/G may hide a subcategory it has never seen (capture-state probe
    Q6 asks; until a Revit run answers, the read is the answer). ``style`` is
    None when it could not be made; the record says why.
    """
    from Autodesk.Revit.DB import BuiltInCategory, GraphicsStyleType
    record = {"state": "value", "subcategory_name": TEMPORARY_TICK_SUBCATEGORY}
    try:
        categories = doc.Settings.Categories
        lines = categories.get_Item(BuiltInCategory.OST_Lines)
        existing = None
        for sub in lines.SubCategories:
            if str(getattr(sub, "Name", "")) == TEMPORARY_TICK_SUBCATEGORY:
                existing = sub
                break
        # One left behind by a capture whose rollback failed is REUSED and
        # said so, rather than refused: NewSubcategory would raise on the name.
        record["created"] = existing is None
        sub = existing if existing is not None else categories.NewSubcategory(
            lines, TEMPORARY_TICK_SUBCATEGORY)
        sub.SetLineWeight(1, GraphicsStyleType.Projection)
        from Autodesk.Revit.DB import LinePatternElement
        sub.SetLinePatternId(LinePatternElement.GetSolidPatternId(),
                             GraphicsStyleType.Projection)
        style = sub.GetGraphicsStyle(GraphicsStyleType.Projection)
        record.update({
            "id": _element_id_int(getattr(style, "Id", None)),
            "name": getattr(style, "Name", None),
            "subcategory_id": _element_id_int(getattr(sub, "Id", None)),
            "projection_line_weight": int(sub.GetLineWeight(
                GraphicsStyleType.Projection)),
            "projection_pattern_solid": _style_pattern_is_solid(style)[0],
        })
    except Exception as ex:
        return None, {"state": "unavailable",
                      "subcategory_name": TEMPORARY_TICK_SUBCATEGORY,
                      "reason": "the temporary Lines subcategory could not be "
                                "made: {0}: {1}".format(type(ex).__name__, ex)}
    return style, record


def tick_line_style(doc, view, curve):
    """``(style, record)``: the style every tick is drawn in, and WHICH PATH
    chose it -- ``record["path"]``:

    * ``"visible_existing"`` -- the thinnest of the curve's own SOLID styles
      whose subcategory the view does not hide;
    * ``"temporary"`` -- none is, so a weight-1 solid subcategory made for
      the capture (rolled back with it);
    * ``"none"`` -- neither; ``style`` is None and the ticks keep the default.

    ``subcategory_hidden_in_view`` is the chosen style's own subcategory,
    READ (three-valued): the capture faults ``registration_marks_may_not_draw``
    on anything but False. ``existing`` keeps the existing-style search's
    record whichever path won.
    """
    style, existing = _thinnest_line_style(doc, curve, view=view)
    if style is not None:
        record = dict(existing, path="visible_existing")
    else:
        style, temporary = _temporary_tick_style(doc, view)
        if style is None:
            return None, {"state": "unavailable", "path": "none",
                          "subcategory_hidden_in_view": None,
                          "reason": "{0}; {1}".format(existing.get("reason"),
                                                      temporary.get("reason")),
                          "existing": existing, "temporary": temporary}
        record = dict(temporary, path="temporary", existing=existing)
    hidden, error = _style_subcategory_hidden(view, style)
    record["subcategory_hidden_in_view"] = hidden
    if error is not None:
        record["subcategory_hidden_error"] = error
    return style, record


def create_registration_marks(doc, view, view_basis, layout):
    """Draw the registration ticks as DETAIL LINES and paint them MARK_COLOUR.

    Inside an open Transaction, inside the capture's TransactionGroup: the
    marks are removed by the group's rollback, never by a delete this module
    has to get right. BEFORE the model pass, so both captures carry them.

    UV -> world goes through the view's own Origin/RightDirection/UpDirection,
    offset by the Origin's UV under ``view_basis`` -- so a mark lands on the
    view plane (NewDetailCurve requires that) whatever depth the basis origin
    sits at (a plan's basis origin is its CUT plane). Each curve's endpoints
    are READ BACK and projected through ``view_basis``, and the record carries
    the largest deviation: the recorded UV is the element's, not the request.

    In the annotation pass production paints the marks with its OWN palette
    (they are view-owned, so annotation members by OwnerViewId), and its
    sidecar's color_assignment_map names each one's colour. MARK_COLOUR is
    what the MODEL pass draws them in.
    """
    from Autodesk.Revit.DB import ElementId, Line, XYZ
    record = {"state": "value", "expected_count": 0, "created_count": 0,
              "created": [], "failed": [], "colour": list(MARK_COLOUR),
              "lines_category_hidden_in_view": None}
    if (layout or {}).get("state") != "value":
        record["state"] = "unavailable"
        record["reason"] = (layout or {}).get("reason")
        return record
    hidden, hidden_error = _lines_category_hidden(view)
    record["lines_category_hidden_in_view"] = hidden
    if hidden_error:
        record["lines_category_hidden_error"] = hidden_error
    segments = layout["segments"]
    record["expected_count"] = len(segments)
    record["layout"] = dict((k, v) for k, v in layout.items() if k != "segments")
    try:
        origin = view.Origin
        right = view.RightDirection
        up = view.UpDirection
        o_u, o_v = view_basis.transform_to_view_uv(
            (float(origin.X), float(origin.Y), float(origin.Z)))
    except Exception as ex:
        record["state"] = "unavailable"
        record["reason"] = "the view's origin/directions could not be read: " \
                           "{0}: {1}".format(type(ex).__name__, ex)
        return record

    def _world(u, v):
        du, dv = float(u) - o_u, float(v) - o_v
        return XYZ(float(origin.X) + du * float(right.X) + dv * float(up.X),
                   float(origin.Y) + du * float(right.Y) + dv * float(up.Y),
                   float(origin.Z) + du * float(right.Z) + dv * float(up.Z))

    paint = flat_colour_override(doc, colour=MARK_COLOUR)
    thinnest = None
    for segment in segments:
        entry = dict(segment)
        try:
            curve = doc.Create.NewDetailCurve(
                view, Line.CreateBound(_world(*segment["uv0"]),
                                       _world(*segment["uv1"])))
            entry["id"] = _element_id_int(curve.Id)
        except Exception as ex:
            entry["error"] = "NewDetailCurve raised {0}: {1}".format(
                type(ex).__name__, ex)
            record["failed"].append(entry)
            continue
        # T1: the thinnest line style THE VIEW DRAWS, chosen once and applied
        # to every tick (tick_line_style: an existing visible style, else a
        # temporary subcategory). The style is the element's own, so it holds
        # in BOTH passes; the record says which path, style and weight.
        try:
            if "line_style" not in record:
                thinnest, record["line_style"] = tick_line_style(doc, view, curve)
            if thinnest is not None:
                curve.LineStyle = thinnest
            entry["line_style_applied"] = thinnest is not None
        except Exception as ex:
            entry["line_style_applied"] = False
            entry["line_style_error"] = "{0}: {1}".format(type(ex).__name__, ex)
            record.setdefault("line_style", {
                "state": "unavailable",
                "reason": "the line style could not be read or set: " + entry[
                    "line_style_error"]})
        try:
            geometry_curve = curve.GeometryCurve
            ends = [tuple(view_basis.transform_to_view_uv(
                        _xyz_tuple(geometry_curve.GetEndPoint(i)))) for i in (0, 1)]
            entry["readback_uv"] = [list(e) for e in ends]
            wanted = (segment["uv0"], segment["uv1"])
            entry["readback_max_deviation_ft"] = max(
                max(abs(a - b) for a, b in zip(end, want))
                for end, want in zip(sorted(ends), sorted(tuple(w) for w in wanted)))
        except Exception as ex:
            entry["readback_error"] = "{0}: {1}".format(type(ex).__name__, ex)
        try:
            view.SetElementOverrides(ElementId(int(entry["id"])), paint)
            entry["painted"] = True
        except Exception as ex:
            entry["painted"] = False
            entry["paint_error"] = "{0}: {1}".format(type(ex).__name__, ex)
        record["created"].append(entry)
    record["created_count"] = len(record["created"])
    deviations = [e["readback_max_deviation_ft"] for e in record["created"]
                  if e.get("readback_max_deviation_ft") is not None]
    record["readback_max_deviation_ft"] = max(deviations) if deviations else None
    if record["created_count"] != record["expected_count"]:
        record["reason"] = "{0} of {1} ticks could not be created".format(
            record["expected_count"] - record["created_count"],
            record["expected_count"])
    return record


def marks_still_in_project(doc, mark_ids):
    """``{id: bool}`` for each mark after the rollback, or ``None`` for a
    lookup that raised. A mark still present means the rollback did not remove
    what was drawn -- a document change, which the caller must report."""
    from Autodesk.Revit.DB import ElementId
    out = {}
    for mark_id in mark_ids or []:
        try:
            out[int(mark_id)] = doc.GetElement(ElementId(int(mark_id))) is not None
        except Exception:
            out[int(mark_id)] = None
    return out


def annotate_sidecar(path, key, payload):
    """Add ``key`` to a production sidecar, REFUSING to overwrite one.

    Registration facts go into the sidecar a consumer reads rather than
    only in its own combined report -- above all that the crop boundary is
    PRESENT and must be subtracted, which a consumer reading the capture alone
    would otherwise never learn. Refusing an existing key keeps production's
    own fields authoritative. Returns ``None`` on success, else the reason.
    """
    try:
        with open(path) as handle:
            sidecar = json.load(handle)
        if key in sidecar:
            return "the sidecar already carries {0!r}; not overwritten".format(key)
        sidecar[key] = payload
        with open(path, "w") as handle:
            json.dump(sidecar, handle, indent=2, sort_keys=True, default=str)
        return None
    except Exception as ex:
        return "{0}: {1}".format(type(ex).__name__, ex)



def complete_capture_integrity(path, record):
    """P1: finish a pass's ``capture_integrity`` with what only the registered
    capture knows, AFTER its rollback and read-back.

    Sets ``rolled_back`` (the verdict), ``marks_still_in_project`` (a COUNT),
    appends the registered capture's own faults to ``capture_faults`` and
    itself to ``completed_by`` -- idempotently, so completing it twice gives
    the second record's faults once. A sidecar with no integrity record to complete
    is refused, not given a fresh one: the pass that should have written it
    did not, and that is the fact. Returns ``None`` on success, else the reason.
    """
    try:
        with open(path) as handle:
            sidecar = json.load(handle)
        integrity = sidecar.get("capture_integrity")
        if not isinstance(integrity, dict):
            return "the sidecar carries no capture_integrity record to complete"
        restore = record.get("restore") or {}
        marks_left = restore.get("marks_still_in_project")
        integrity["rolled_back"] = bool(restore.get("rolled_back"))
        integrity["marks_still_in_project"] = (
            len(marks_left) if isinstance(marks_left, (list, dict)) else
            {"state": "unavailable",
             "reason": "the mark read-back did not complete: {0!r}".format(marks_left)})
        # Idempotent: a re-completion REPLACES this capture's faults rather
        # than appending them twice, so a peer completed before a later write
        # failed can be completed again with the final fault list.
        integrity["capture_faults"] = [
            f for f in integrity.get("capture_faults") or []
            if not (isinstance(f, dict) and f.get("source") == "registered_capture")
        ] + [dict(f, source="registered_capture") for f in record.get("faults") or []]
        integrity["completed_by"] = [
            c for c in integrity.get("completed_by") or [] if c != "registered_capture"
        ] + ["registered_capture"]
        with open(path, "w") as handle:
            json.dump(sidecar, handle, indent=2, sort_keys=True, default=str)
        return None
    except Exception as ex:
        return "{0}: {1}".format(type(ex).__name__, ex)


def discover_link_categories(doc, view, diag=None, view_id=None):
    """LINKED RVT categories for mechanism 2, through production's own policy.

    DRIVEN by a test (tests/test_probe_stage_a_anno_pass_variants.py): the
    annotation-pass variant probe once called
    ``_resolve_colorable_category_predicate(doc)`` -- the document where the
    predicate expects ``diag`` -- and every view reported
    ``'Document' object has no attribute 'warn'``, so the link mechanism never
    ran and nothing said so except a field nobody read. Returns
    ``(link_categories, link_report)``.
    """
    from Autodesk.Revit.DB import FilteredElementCollector as _FEC
    link_categories = []
    link_report = {"state": "not_attempted", "instances": 0}
    try:
        from Autodesk.Revit.DB import RevitLinkInstance
        from .color_id_buffer import (
            _model_categories_in_linked_doc, _resolve_colorable_category_predicate,
        )
        instances = list(_FEC(doc, view.Id).OfClass(RevitLinkInstance))
        # (diag=, view_id=), NOT (doc): round 2's run passed the document as
        # the diagnostics object and died on doc.warn, so mechanism 2 never ran.
        is_colorable, colorable_error = _resolve_colorable_category_predicate(
            diag=diag, view_id=view_id)
        seen = {}
        uncolorable_names = []
        for instance in instances:
            linked_doc = None
            try:
                linked_doc = instance.GetLinkDocument()
            except Exception as ex:
                link_report.setdefault("instance_errors", []).append(
                    "{0}: {1}".format(type(ex).__name__, ex))
            if linked_doc is None:
                continue
            colorable, uncolorable = _model_categories_in_linked_doc(
                linked_doc, is_colorable)
            for cat in colorable:
                cid = _element_id_int(getattr(cat, "Id", None))
                if cid is not None and cid not in seen:
                    seen[cid] = cat
            uncolorable_names.extend(
                str(getattr(cat, "Name", cat)) for cat in uncolorable)
        link_categories = [seen[k] for k in sorted(seen)]
        link_report = {
            "state": "value",
            "instances": len(instances),
            "colorable_category_count": len(link_categories),
            "colorable_category_names": [str(getattr(c, "Name", c))
                                         for c in link_categories],
            # REPORTING ONLY, and an accepted gap: nothing capture-side
            # suppresses a category Stage A cannot colour. Named so a
            # non-white pixel in a linked area has somewhere to point.
            "uncolorable_category_names": sorted(set(uncolorable_names)),
            "colorable_predicate_error": colorable_error,
        }
        if not instances:
            link_report["note"] = (
                "this view has NO linked RVT instances, so mechanism 2 is built "
                "but UNEXERCISED here. A clean run on this view is not evidence "
                "that the link path works.")
    except Exception as ex:
        link_report = {"state": "unavailable",
                       "reason": "{0}: {1}".format(type(ex).__name__, ex)}
    return (link_categories, link_report)
