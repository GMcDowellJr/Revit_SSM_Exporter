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
