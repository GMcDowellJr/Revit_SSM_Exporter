"""Pure export-resolution arithmetic for Stage A color-ID captures.

This is the ONE place the export pixel cap is implemented. It was extracted
from tests/dynamo/resolution_contract.py so that production
(color_id_buffer.py) and the probe/test contract cannot drift apart: a cap
that lives in the test tree can only ever describe what production should
do, never enforce it, and the 2026-09-16 runs found production's own ceiling
sitting 5000 px ABOVE the measured clean limit for exactly that reason.

No Revit API, no I/O, no third-party imports -- importable from CPython 3,
IronPython 2, a Dynamo node, or a bare pytest run.
"""

import math

# The longer axis of a Stage A export must not exceed this. Measured, not
# chosen for headroom: across the two 2026-09-16 drift-onset runs, 27 of 28
# captures bracket the line exactly -- 10000x9642 is hard-edged and 8695x10028
# is not -- and it is the LONGER axis that decides, not the width, not the
# height, and not the pixel count (149 Mpx clean, 87 Mpx drifted). See
# tests/dynamo/PROBE_STAGE_A_COLOR_ID_ANOMALIES.md (hypotheses D-H2/D-H4) for
# the evidence and for the single unexplained exception.
MAX_STAGE_A_AXIS_PX = 10000

# The export DPI Stage A uses when a Config does not say otherwise. Unlike
# MAX_STAGE_A_AXIS_PX this is a convention, not a measurement -- no run in
# this repo derives it -- so it lives here as ONE name rather than as the
# four literals it used to be (config.py's signature and from_dict default,
# color_id_buffer's getattr fallback, and the decode tool's own fallback for
# a sidecar that never recorded which DPI it used).
DEFAULT_COLOR_ID_EXPORT_DPI = 150.0


def round_half_up_positive(value):
    """Round a nonnegative value half-up (Python's round() is half-to-even)."""
    value = float(value)
    if value < 0:
        raise ValueError("round_half_up_positive requires a nonnegative value")
    return int(math.floor(value + 0.5))


def _positive(value, name):
    if value is None or float(value) <= 0:
        raise ValueError("{0} must be positive".format(name))
    return float(value)


def effective_export_dpi(crop_bounds_xy, actual_w_px, actual_h_px, view_scale):
    """The dpi an exported capture ACTUALLY carries, or None if unknowable.

    BOTH AXES, AND THE SMALLER ANSWER WINS. The fitted axis must NOT be used
    to select here. ExportImage's aspect clamp pads the SHORT axis, and the
    fitted axis can BE the short one: an 80 x 4 ft crop under vertical fit
    comes back 400 x 40 px, where the 40 px height is both the axis PixelSize
    set AND the axis carrying 10 px of pad on each side. The post-export
    dimension check passes -- the padded height is exactly the requested 40 --
    so it cannot be used to argue the fitted axis is unpadded. Dividing by the
    fitted axis there counts pad as rendered crop and reports 80 dpi where the
    truth is 40.

    The padded axis carries more pixels than its extent warrants, so it always
    reports the LARGER dpi; the unpadded axis is the truth. Taking the minimum
    of the two per-axis figures selects the unpadded one without needing the
    aspect limit, the pad, or which axis was fitted. It is the same selection
    tools/clamp_pad_geometry.py makes as max(crop_u/w, crop_v/h), stated in dpi
    rather than feet-per-pixel, since min(a/x, b/y) is the reciprocal of
    max(x/a, y/b).

    THE DENOMINATOR IS THE RENDERED CROP, not the grid. raster.W * cell_size_ft
    is ceil(extent / cell) * cell -- the extent rounded UP to whole cells -- and
    overstates what the TIFF spans by up to one cell even with no narrowing.

    Args:
        crop_bounds_xy: (xmin, ymin, xmax, ymax) in view-local feet -- the
            rectangle the view was actually cropped to, i.e. what the sidecar
            records as "bounds_xy" and what a decoder derives feet_per_pixel
            from. None when no crop could be applied.
        actual_w_px, actual_h_px: the exported file's MEASURED dimensions.
        view_scale: the view's scale denominator.

    Returns:
        float dpi, or None when any term is missing or degenerate. None is the
        honest answer for a capture whose rendered rectangle is unknown
        (FitToPage's auto extent); substituting the REQUESTED dpi there is the
        exact confusion the separate requested/effective fields exist to end.

    Lives here, and is called rather than replicated, because a test that
    reimplements this formula binds itself to its own copy: production could
    regress to the fitted-axis or grid-extent form and the test would still
    pass. This module is pure arithmetic with no Revit API and no I/O, so both
    production and tests can call the one implementation.
    """
    if crop_bounds_xy is None or not actual_w_px or not actual_h_px:
        return None
    try:
        scale = float(view_scale)
        crop_u_ft = float(crop_bounds_xy[2]) - float(crop_bounds_xy[0])
        crop_v_ft = float(crop_bounds_xy[3]) - float(crop_bounds_xy[1])
        aw = float(actual_w_px)
        ah = float(actual_h_px)
    except (TypeError, ValueError, IndexError):
        return None
    if scale <= 0.0 or crop_u_ft <= 0.0 or crop_v_ft <= 0.0 or aw <= 0.0 or ah <= 0.0:
        return None
    # paper inches along an axis = model feet * 12 / view scale
    dpi_u = aw / (crop_u_ft * 12.0 / scale)
    dpi_v = ah / (crop_v_ft * 12.0 / scale)
    return min(dpi_u, dpi_v)


def cap_axes(requested_px, derived_px, max_axis_px=MAX_STAGE_A_AXIS_PX):
    """Scale a requested fitted-axis pixel count so BOTH axes fit the cap.

    Revit's ImageExportOptions.PixelSize sets ONE axis -- the one FitDirection
    names -- and derives the other from the view's extents, bounded by
    nothing. Capping only the axis PixelSize sets therefore proves nothing:
    a view fitted horizontally at 8695 px can still come back 10028 px tall,
    which is over the line and drifts. So the request is scaled by the
    tightest of the two ratios, which is what makes the DERIVED axis land
    under the cap too.

    Args:
        requested_px: the fitted axis's uncapped pixel count (> 0).
        derived_px: what the other axis would be at that request, i.e.
            ``requested_px * derived_extent / fit_extent`` for the rectangle
            the export is actually fitted to (> 0).
        max_axis_px: the per-axis ceiling, or None to apply no cap at all.

    Returns a dict; ``accepted_px`` is the value to hand Revit and
    ``accepted_derived_px`` is what the other axis is then predicted to be.
    Both are >= 1. ``cap_applied`` is True only when the cap actually moved
    the request.
    """
    requested = _positive(requested_px, "requested_px")
    derived = _positive(derived_px, "derived_px")
    cap = None if max_axis_px is None else int(_positive(max_axis_px, "max_axis_px"))

    if cap is None:
        factor = 1.0
    else:
        factor = min(1.0, float(cap) / requested, float(cap) / derived)

    pre_cap_px = max(1, round_half_up_positive(requested))
    # FLOOR on the DERIVED axis (D5), half-up on the fitted one. The two
    # axes are not the same kind of quantity and must not share a rule:
    #
    #   fitted   Revit is HANDED this as PixelSize and renders exactly it.
    #            The number is a REQUEST, and half-up is the right way to
    #            turn a real-valued want into one. (Its only production
    #            caller hands cap_axes an int anyway -- color_id_buffer.py
    #            :1682 -- so the rule there is inert in practice.)
    #   derived  Revit COMPUTES this from the view's extents. The number is
    #            a PREDICTION of what Revit will do, and four captures came
    #            back one pixel under the half-up prediction.
    #
    # CAVEAT, and it is the whole caveat: the mechanism is INFERRED, NOT
    # CONFIRMED. "Revit truncates" and "Revit computes from a slightly
    # different crop" BOTH produce -1 at n=4, and nothing in this repo
    # distinguishes them. If it is the second, flooring is right for the
    # wrong reason and will be wrong the other way on a capture whose crop
    # differs in the opposite direction. n=4 is four captures, not a law.
    #
    # WHAT THIS DOES AND DOES NOT MOVE. It changes a reported PREDICTION --
    # accepted_derived_px, pre_cap_derived_px, and the sidecar's
    # predicted_derived_px -- and NOT the pixel count handed to Revit.
    # accepted_px is taken from floor(requested * factor) below, which does
    # not read either of these; the only path back into it is the `while`
    # backstop, which is a float-last-bit guard that fires in 0 of 300000
    # random cases and 0 of 400000 constructed near-boundary ones, and
    # accepted_px is identical under both rules across all of them. So a
    # cap decision cannot flip through this while that loop stays what its
    # own comment says it is. If the loop is ever given real work to do,
    # this becomes a decision-affecting change and this note is wrong.
    #
    # Flooring also never OVER-predicts -- BUT ONLY WHILE THE TRUE DERIVED
    # AXIS IS AT LEAST ONE PIXEL. There the error against the true derived
    # value is in [0, 1) instead of half-up's (-0.5, 0.5].
    #
    # BOUNDARY. Below one pixel the max(1, ...) floor lifts the prediction to
    # 1, so it necessarily over-predicts: 64 px at aspect 0.005 really derives
    # 0.32 px and is reported as 1. This needs no extreme accepted_px -- it is
    # the DERIVED axis that goes sub-pixel -- so it is a different corner from
    # the cap boundary noted further down. Reporting 1 is the only honest
    # answer, a zero-pixel axis not being an image, so this is a limit of the
    # problem and not a defect in the rule. Asserted rather than glossed, in
    # tests/test_invariants_resolution_cap.py, by
    # test_the_derived_prediction_never_over_predicts.
    pre_cap_derived_px = max(1, int(math.floor(derived)))
    if factor < 1.0:
        # FLOOR, not round-half-up. Rounding the fitted axis up can hand
        # back the request unchanged while the derived axis is still
        # reported as capped: cap_axes(501, 10001) returned 501 with a
        # predicted 10000, and 501 px at that aspect really does derive
        # 10001. The cap then "fired" and changed nothing, the post-export
        # check caught the over-ceiling render, and the backoff halved
        # 501 to 250 -- half the resolution spent to save one pixel.
        # Flooring can only ever land at or below the cap: unconditionally
        # for accepted_px, and for the DERIVED axis WHILE accepted_px > 1.
        #
        # BOUNDARY. The backoff loop below is bounded by `while accepted_px >
        # 1`, so on an aspect steeper than the cap itself (cap=64 at aspect
        # 500) the derived axis is over the ceiling at accepted_px == 1 and
        # there is nowhere left to go. An image cannot have zero pixels, so
        # that corner is a limit of the problem and not a defect in the rule.
        # Unreachable on the Stage A path -- ExportImage clamps aspect at 10:1
        # against a cap of 10000, so escaping it would need a 10000:1 view --
        # but cap_axes is general and the probe contract passes caps of 1000
        # and 4000, which is why it is pinned in
        # tests/test_invariants_resolution_cap.py, by
        # test_neither_axis_exceeds_the_cap, rather than assumed away.
        accepted_px = max(1, int(math.floor(requested * factor)))
        # And the derived prediction is recomputed from the integer that
        # will actually be requested, not from the unrounded scale. A
        # prediction derived from a pixel count nobody asks for is what
        # made the two disagree in the first place.
        aspect = derived / requested
        accepted_derived_px = max(1, int(math.floor(accepted_px * aspect)))
        # Floating-point only: floor(requested * cap/derived) * aspect is
        # <= cap algebraically, so this can trip at most on the last bit.
        # Bounded by accepted_px, which strictly decreases.
        while accepted_px > 1 and accepted_derived_px > cap:
            accepted_px -= 1
            accepted_derived_px = max(1, int(math.floor(accepted_px * aspect)))
        cap_applied = True
    else:
        accepted_px = pre_cap_px
        accepted_derived_px = pre_cap_derived_px
        cap_applied = False

    return {
        "max_axis_px": cap,
        "pre_cap_px": pre_cap_px,
        "pre_cap_derived_px": pre_cap_derived_px,
        "accepted_px": accepted_px,
        "accepted_derived_px": accepted_derived_px,
        "cap_applied": cap_applied,
        "scale_factor": factor,
    }


def feet_per_pixel_at_dpi(view_scale, export_dpi):
    """View-local feet spanned by one exported pixel at a requested dpi.

    One pixel is 1/dpi paper inches and one paper inch is view_scale/12 model
    feet, so fpp = view_scale / (12 * dpi). Nothing about the analysis grid
    enters: cell_size_ft is not a term.

    This is the FORWARD mapping. effective_export_dpi() above is its inverse
    (dpi = scale / (12 * fpp) once fpp is read back off a rendered rectangle),
    and the two are composed back to back in
    tests/test_frame_export_geometry.py rather than each being checked against
    its own copy -- the defect class CLAUDE.md records as "a quantity computed
    in two places, never composed".
    """
    scale = _positive(view_scale, "view_scale")
    dpi = _positive(export_dpi, "export_dpi")
    return scale / (12.0 * dpi)


def _extent(rect_uv, name):
    """(u_ft, v_ft) of a (xmin, ymin, xmax, ymax) rectangle, both > 0."""
    try:
        u = float(rect_uv[2]) - float(rect_uv[0])
        v = float(rect_uv[3]) - float(rect_uv[1])
    except (TypeError, ValueError, IndexError):
        raise ValueError("{0} must be a 4-tuple (xmin, ymin, xmax, ymax)".format(name))
    if u <= 0.0 or v <= 0.0:
        raise ValueError("{0} must have positive extent on both axes".format(name))
    return u, v


# Relative slack used when snapping a rectangle onto a pixel lattice. A crop
# edge that lands exactly on a lattice line is a float computation away from
# landing a last-bit above it, and a bare ceil() would then spend a whole
# extra pixel. The tolerance is relative to the pixel index, so it stays
# meaningful at index 10 and at index 9999. It only ever suppresses a
# sub-milli-pixel overshoot; a real fraction of a pixel still snaps outward,
# because snapping INWARD would clip rendered content off the crop.
_LATTICE_EPS = 1.0e-6


def _lattice_ceil(value):
    """Pixels needed to CONTAIN an extent, with the lattice tolerance applied.

    ceil, not round: a lattice that must contain the frame cannot round its
    last fraction of a pixel away. A half-up rule here clipped a frame
    spanning 47.25 px to 47 and dropped the top quarter pixel -- caught by
    tests/test_invariants_frame_export_geometry.py's snap property before it
    reached a commit, not by reasoning about it.
    """
    return max(1, int(math.ceil(float(value) - _LATTICE_EPS)))


def frame_export_geometry(frame_uv, crop_uv, view_scale, export_dpi,
                          fit_direction="horizontal",
                          max_axis_px=MAX_STAGE_A_AXIS_PX,
                          min_axis_px=64):
    """Everything a Stage A export needs, derived from frame B and dpi ONLY.

    THE ANALYSIS GRID IS NOT A TERM. The caller passes rectangles in view-local
    feet and a dpi; no cell count and no cell size reach this function. That is
    the whole point of Stage A step 2: before it, the export's pixel size came
    from ``raster.W * raster.cell_size_ft``, which is ceil(extent/cell)*cell --
    the frame rounded UP to whole cells -- so changing the analysis resolution
    silently changed the captured image. effective_export_dpi()'s docstring
    already said the grid was the wrong denominator for the INVERSE mapping;
    this makes the forward one agree.

    THE CAP OPERATES ON B. When B's pixels exceed the ceiling, B is not
    clipped and not re-centred: feet-per-pixel grows until B fits, so the
    frame stays whole and the RESOLUTION is what drops. ``cap_applied`` and
    the requested/achieved fpp and dpi record the drop.

    REGISTRATION (decision A, 2026-09-21): A is snapped to B's pixel lattice
    at the shared achieved fpp. A's corners move OUTWARD to the nearest
    lattice lines, so A's offset within B is an exact whole number of pixels
    (``crop_offset_px``) and the two frames can be registered by integer
    translation with no resampling. Snapping outward can only ever add crop,
    never clip it.

    B.min remains the single origin: the lattice is anchored there, A's offset
    is expressed against it once, and ``frame_snapped_uv`` grows away from it
    rather than around it. Nothing here applies the offset a second time and
    nothing re-centres B.

    Args:
        frame_uv: B -- (xmin, ymin, xmax, ymax) view-local feet, the frame the
            capture is sized for (the annotation-expanded bounds).
        crop_uv: A -- the narrower model-only rectangle actually rendered, or
            None when no narrowing applies, in which case A is B. Must lie
            inside B; it is clamped to B if it does not.
        view_scale: the view's scale denominator.
        export_dpi: the REQUESTED dpi.
        fit_direction: "horizontal" or "vertical" -- which axis Revit's
            PixelSize sets.
        max_axis_px: per-axis ceiling, or None for no cap.
        min_axis_px: floor on B's fitted axis, mirroring the shipped
            ``max(64, ...)``. A floor RAISES resolution, so it can push a
            tiny frame above the requested dpi; achieved_export_dpi records
            that as faithfully as it records a cap.

    Returns a dict. ``requested_px`` is what to hand Revit's PixelSize and
    ``predicted_derived_px`` is what the other axis must come back as -- and
    unlike the shipped aspect-ratio prediction that figure is EXACT, because
    both are whole pixel counts of the same snapped rectangle rather than one
    inferred from the other through a float ratio.
    """
    vertical = str(fit_direction or "horizontal").strip().lower() == "vertical"
    frame_u_ft, frame_v_ft = _extent(frame_uv, "frame_uv")
    scale = _positive(view_scale, "view_scale")
    floor_px = max(1, int(min_axis_px or 1))
    cap = None if max_axis_px is None else int(_positive(max_axis_px, "max_axis_px"))

    requested_fpp = feet_per_pixel_at_dpi(scale, export_dpi)
    frame_fit_ft = frame_v_ft if vertical else frame_u_ft
    frame_der_ft = frame_u_ft if vertical else frame_v_ft

    pre_cap_fit_px = max(floor_px, round_half_up_positive(frame_fit_ft / requested_fpp))
    pre_cap_der_px = max(1, round_half_up_positive(frame_der_ft / requested_fpp))

    # The cap itself is cap_axes, unchanged and uncopied: it is the pinned
    # implementation of the two-axis ceiling and carries its own invariant
    # tests. Step 2 changes only WHAT it is handed -- B's two axes, where it
    # used to be handed the grid's fitted axis and an aspect ratio taken from
    # the render crop A.
    cap_result = cap_axes(pre_cap_fit_px, pre_cap_der_px, cap)
    accepted_fit_px = max(floor_px, cap_result["accepted_px"])

    # B stays whole and the resolution absorbs the cap: fpp is re-derived from
    # B's own unclipped extent over the pixels it is allowed to have.
    accepted_fpp = frame_fit_ft / float(accepted_fit_px)

    # B's lattice size on both axes. The fitted axis is accepted_fit_px by
    # construction; the other is measured off the same fpp.
    #
    # THIS LOOP DOES REAL WORK -- it is NOT a float-last-bit backstop, and an
    # earlier draft of this comment said it was. Measured over 200,000 random
    # (extent, scale, dpi, fit) cases: it fires 1,378 times, up to 63
    # iterations. The reason is a deliberate rounding-rule mismatch. cap_axes
    # FLOORS its derived-axis prediction (its D5 note explains why: that axis
    # is a prediction of what Revit computes, and flooring never
    # over-predicts). B's lattice, by contrast, has to CONTAIN B, so it is
    # CEILED off the achieved fpp. Ceiling can land a pixel above cap_axes'
    # floored figure, and at the ceiling that one pixel is the difference
    # between honouring the cap and breaching it. On the FITTED axis the two
    # agree exactly and cost nothing: accepted_fpp is frame_fit_ft divided by
    # accepted_fit_px, so ceiling that quotient returns accepted_fit_px.
    #
    # BOUNDARY, and it is a real one rather than a hedge. The loop is bounded
    # by accepted_fit_px > 1, so it CANNOT establish the ceiling on a frame
    # whose aspect is steeper than the cap itself: at 1 px on the fitted axis
    # the derived axis is still over, and there is nowhere left to go. This is
    # the same corner cap_axes documents for its own backoff, inherited rather
    # than newly introduced, and an image cannot have zero pixels, so it is a
    # limit of the problem. In the same 200,000-case sweep it is reached 3
    # times, all at aspects between 10,496:1 and 53,174:1. Over a second
    # 200,000-case sweep confined to aspects within 20:1 -- the band the
    # bounded claim is made over -- it is reached 0 times.
    #
    # UNCONFIRMED (no Revit run in this session): that corner is believed
    # unreachable on the Stage A path because ExportImage clamps aspect at
    # 10:1, which against a 10,000 px cap would need a 10,000:1 view. The
    # 10:1 constant itself rests on a single observed view and is recorded
    # elsewhere in this project as unconfirmed, so this is a reason to think
    # the corner is unreachable -- not a proof that it is.
    #
    # So the claim asserted in tests/test_invariants_frame_export_geometry.py
    # is the BOUNDED one -- neither axis of B exceeds the ceiling WHILE the
    # frame's aspect is shallower than the cap -- together with a case that
    # pins what happens beyond it, rather than a universal that is false.
    frame_px_u = _lattice_ceil(frame_u_ft / accepted_fpp)
    frame_px_v = _lattice_ceil(frame_v_ft / accepted_fpp)
    lattice_corrections = 0
    while cap is not None and max(frame_px_u, frame_px_v) > cap and accepted_fit_px > 1:
        accepted_fit_px -= 1
        accepted_fpp = frame_fit_ft / float(accepted_fit_px)
        frame_px_u = _lattice_ceil(frame_u_ft / accepted_fpp)
        frame_px_v = _lattice_ceil(frame_v_ft / accepted_fpp)
        lattice_corrections += 1

    # A, clamped into B. compute_model_crop() already intersects, so a crop
    # outside B should be impossible; clamping here means this function is
    # still total if it ever is, rather than emitting a negative pixel offset.
    if crop_uv is None:
        crop_rect = (float(frame_uv[0]), float(frame_uv[1]),
                     float(frame_uv[2]), float(frame_uv[3]))
        crop_is_frame = True
    else:
        _extent(crop_uv, "crop_uv")
        crop_rect = (
            max(float(crop_uv[0]), float(frame_uv[0])),
            max(float(crop_uv[1]), float(frame_uv[1])),
            min(float(crop_uv[2]), float(frame_uv[2])),
            min(float(crop_uv[3]), float(frame_uv[3])),
        )
        crop_is_frame = False
        if crop_rect[2] <= crop_rect[0] or crop_rect[3] <= crop_rect[1]:
            # A degenerate intersection is not a rectangle to render. Fall
            # back to B rather than inventing one.
            crop_rect = (float(frame_uv[0]), float(frame_uv[1]),
                         float(frame_uv[2]), float(frame_uv[3]))
            crop_is_frame = True

    fx0, fy0 = float(frame_uv[0]), float(frame_uv[1])
    i0 = int(math.floor((crop_rect[0] - fx0) / accepted_fpp + _LATTICE_EPS))
    j0 = int(math.floor((crop_rect[1] - fy0) / accepted_fpp + _LATTICE_EPS))
    i1 = int(math.ceil((crop_rect[2] - fx0) / accepted_fpp - _LATTICE_EPS))
    j1 = int(math.ceil((crop_rect[3] - fy0) / accepted_fpp - _LATTICE_EPS))
    i0 = max(0, min(i0, frame_px_u - 1))
    j0 = max(0, min(j0, frame_px_v - 1))
    i1 = min(frame_px_u, max(i1, i0 + 1))
    j1 = min(frame_px_v, max(j1, j0 + 1))

    # The frame the capture actually REALISES. An image is a whole number of
    # pixels, so when B's extent is not one -- 47.25 px, say -- the rendered
    # rectangle cannot be B exactly. It is B rounded OUT to the lattice: the
    # 48-pixel rectangle that contains B and exceeds it by under one pixel per
    # axis.
    #
    # This is the honest resolution of a tension that cannot be wished away,
    # and both halves of it were falsified as universals before landing here.
    # "The lattice contains B" and "the snapped crop stays inside B" are not
    # simultaneously satisfiable off-lattice: rounding the lattice down clips
    # the frame's far edge, rounding it up overshoots. Containment wins,
    # because clipping loses rendered content while overshoot adds at most a
    # sub-pixel margin of background.
    #
    # B.min IS STILL THE ORIGIN. The rectangle grows outward from (fx0, fy0)
    # only; the minimum corner does not move, so nothing here re-centres B and
    # the frame reconciliation invariant is untouched.
    frame_snapped_uv = (
        fx0,
        fy0,
        fx0 + frame_px_u * accepted_fpp,
        fy0 + frame_px_v * accepted_fpp,
    )
    crop_snapped_uv = (
        fx0 + i0 * accepted_fpp,
        fy0 + j0 * accepted_fpp,
        fx0 + i1 * accepted_fpp,
        fy0 + j1 * accepted_fpp,
    )
    crop_px_u = i1 - i0
    crop_px_v = j1 - j0

    requested_px = crop_px_v if vertical else crop_px_u
    predicted_derived_px = crop_px_u if vertical else crop_px_v

    return {
        # --- what to hand Revit -------------------------------------------
        "requested_px": int(requested_px),
        "predicted_derived_px": int(predicted_derived_px),
        "requested_axis": "height" if vertical else "width",
        "crop_snapped_uv": crop_snapped_uv,
        # A's whole-pixel position inside B, measured from B.min. This is the
        # registration decision A asks for: an integer translation, no resample.
        "crop_offset_px": (int(i0), int(j0)),
        "crop_px": (int(crop_px_u), int(crop_px_v)),
        "crop_is_frame": bool(crop_is_frame),
        # --- frame B -------------------------------------------------------
        "frame_px": (int(frame_px_u), int(frame_px_v)),
        # B as passed in, unclipped and un-re-centred. This is the value the
        # "B stays whole" claim is about.
        "frame_extent_ft": (frame_u_ft, frame_v_ft),
        # B rounded OUT to the pixel lattice -- what the capture realises, and
        # what a decoder must use as the frame rectangle. Contains
        # frame_uv; exceeds it by less than one pixel on each axis, always at
        # the max corner, never at the min.
        "frame_snapped_uv": frame_snapped_uv,
        # --- requested vs achieved, for every value step 2 moves ----------
        "requested_export_dpi": float(export_dpi),
        "achieved_export_dpi": scale / (12.0 * accepted_fpp),
        "requested_fpp_ft": requested_fpp,
        "achieved_fpp_ft": accepted_fpp,
        "pre_cap_px": int(cap_result["pre_cap_px"]),
        "pre_cap_derived_px": int(cap_result["pre_cap_derived_px"]),
        "frame_fit_px": int(accepted_fit_px),
        "cap_applied": bool(cap_result["cap_applied"]),
        "max_axis_px": cap_result["max_axis_px"],
        "min_axis_px": int(floor_px),
        "lattice_corrections": int(lattice_corrections),
    }
