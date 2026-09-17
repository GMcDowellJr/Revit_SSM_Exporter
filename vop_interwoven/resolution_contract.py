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
    pre_cap_derived_px = max(1, round_half_up_positive(derived))
    if factor < 1.0:
        accepted_px = max(1, round_half_up_positive(requested * factor))
        accepted_derived_px = max(1, round_half_up_positive(derived * factor))
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
