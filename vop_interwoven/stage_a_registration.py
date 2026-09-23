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
MARK_ARM_PX = 96.0
MARK_MIN_ARM_PX = 32.0
MARK_CORNERS = (("left_bottom", 1.0, 1.0), ("right_bottom", -1.0, 1.0),
                ("left_top", 1.0, -1.0), ("right_top", -1.0, -1.0))
# Mid-edge ticks: (name, orientation, which edge the tick starts from, sign).
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
    mid_u, mid_v = (u0 + u1) / 2.0, (v0 + v1) / 2.0
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
