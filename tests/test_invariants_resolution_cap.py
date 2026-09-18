"""Property tests for cap_axes' STATED invariants.

`tests/test_stage_a_export_dimension_contract.py` pins these at eight fixed
pairs, chosen because they were the pairs that had gone wrong. The claims
themselves are universal, and are written as such in the code:

  - ":91  Flooring can only ever land at or below the cap."
  - ":112 Flooring also never OVER-predicts: the error against the true
     derived value is in [0, 1) instead of half-up's (-0.5, 0.5]."
  - ":68  cap_applied is True only when the cap actually moved the request."

TWO OF THOSE CLAIMS TURNED OUT TO BE OVERSTATED, and these tests are how that
was found -- on their first run, before they were ever committed. Both :91 and
:112 are written as universals and both fail below the one-pixel floor, in two
DIFFERENT corners. Neither is a code defect: an image cannot have zero pixels,
so the floor is a limit of the problem. What was wrong was the prose. The
exceptions are asserted explicitly below rather than excluded quietly, so the
boundary is recorded where the next reader will find it.

A universal claim checked at eight points is a claim about eight points. The
D5 change turned on exactly the second of these, and the fixed-case test that
covered it had encoded half-up's +/-0.5 band as its tolerance -- so it had to
be retargeted by hand rather than simply re-run.
"""
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vop_interwoven.resolution_contract import (  # noqa: E402
    MAX_STAGE_A_AXIS_PX,
    cap_axes,
)

REQ = st.integers(min_value=1, max_value=40000)
ASPECT = st.floats(min_value=0.002, max_value=500.0,
                   allow_nan=False, allow_infinity=False)
CAP = st.sampled_from([64, 1000, 4096, MAX_STAGE_A_AXIS_PX, 15000])


@settings(max_examples=500, deadline=None)
@given(requested=REQ, aspect=ASPECT, cap=CAP)
def test_neither_axis_exceeds_the_cap(requested, aspect, cap):
    """":91 Flooring can only ever land at or below the cap." Both axes --
    capping only the axis PixelSize sets is what the two-axis cap exists to
    stop, and a fitted axis under the ceiling with a derived axis over it is
    the exact shape the 2026-09-16 runs found."""
    derived = requested * aspect
    out = cap_axes(requested, derived, cap)
    assert out["accepted_px"] <= cap

    # THE ONE-PIXEL FLOOR IS A REAL EXCEPTION, FOUND BY THIS TEST.
    # The claim at :91 is stated universally but cannot hold below one pixel:
    # the backoff loop is bounded by `while accepted_px > 1`, so on an aspect
    # steeper than the cap itself (cap=64 at aspect 500) the derived axis is
    # over the ceiling at accepted_px == 1 and there is nowhere left to go.
    # An image cannot have zero pixels, so this is a limit of the problem, not
    # of the implementation -- asserted rather than glossed, so the boundary is
    # recorded where the next reader will find it.
    #
    # Unreachable on the Stage A path: ExportImage clamps aspect at 10:1 and
    # the cap is 10000, so escaping it would need a 10000:1 view. cap_axes is
    # a general function and the probe contract passes caps of 1000 and 4000,
    # which is why the corner is pinned rather than assumed away.
    if out["accepted_px"] > 1:
        assert out["accepted_derived_px"] <= cap
        # And what the request REALLY derives, not merely what was predicted.
        assert out["accepted_px"] * aspect <= cap + 1e-6


@settings(max_examples=500, deadline=None)
@given(requested=REQ, aspect=ASPECT, cap=CAP)
def test_the_derived_prediction_never_over_predicts(requested, aspect, cap):
    """":112 the error against the true derived value is in [0, 1)."

    This is D5's whole claim, and the direction matters more than the width:
    an OVER-prediction is what drives the backoff to halve the resolution to
    save one pixel. Under-predicting by up to a pixel is the cost of never
    doing that."""
    derived = requested * aspect
    out = cap_axes(requested, derived, cap)
    real_derived = out["accepted_px"] * aspect
    predicted = out["accepted_derived_px"]

    # A SECOND EXCEPTION, ALSO FOUND BY THIS TEST, AND A DIFFERENT ONE.
    # The [0, 1) claim holds only while the true derived axis is at least one
    # pixel. Below that the `max(1, ...)` floor lifts the prediction to 1 and
    # it necessarily OVER-predicts -- e.g. 64 px at aspect 0.005 really derives
    # 0.32 px and is reported as 1. Note this needs no extreme accepted_px:
    # it is the DERIVED axis that goes sub-pixel, so it is not the same corner
    # as the cap exception above.
    #
    # Reporting 1 is the only honest answer -- a zero-pixel axis is not an
    # image -- so the invariant is stated as it actually is rather than
    # asserted where it is false.
    if real_derived >= 1.0:
        err = real_derived - predicted
        assert -1e-6 <= err < 1.0 + 1e-6, (
            "prediction {0} vs real {1} (err {2}) for {3} px at aspect "
            "{4}".format(predicted, real_derived, err, requested, aspect))
    else:
        assert predicted == 1
        assert predicted >= real_derived


@settings(max_examples=500, deadline=None)
@given(requested=REQ, aspect=ASPECT, cap=CAP)
def test_cap_applied_iff_the_cap_moved_the_request(requested, aspect, cap):
    """":68 cap_applied is True ONLY when the cap actually moved the request."

    The failure this guards is recorded at :85 -- cap_axes(501, 10001) once
    returned 501 unchanged while reporting itself capped, so the post-export
    check caught the over-ceiling render and the backoff halved the request.
    A cap that "fires" and changes nothing is worse than one that does not
    fire, because it silences the thing that would have noticed.
    """
    derived = requested * aspect
    out = cap_axes(requested, derived, cap)
    needed = (requested > cap) or (derived > cap)
    if out["cap_applied"]:
        assert out["accepted_px"] < out["pre_cap_px"] or out["pre_cap_px"] == 1
    if not needed:
        assert not out["cap_applied"]


@settings(max_examples=300, deadline=None)
@given(requested=REQ, aspect=ASPECT)
def test_no_cap_returns_the_request_unchanged(requested, aspect):
    """max_axis_px=None means "apply no cap at all"; anything else would make
    the override silently lossy."""
    out = cap_axes(requested, requested * aspect, None)
    assert out["cap_applied"] is False
    assert out["accepted_px"] == out["pre_cap_px"]
    assert out["max_axis_px"] is None


@settings(max_examples=200, deadline=None)
@given(bad=st.sampled_from([0, -1]), other=REQ)
def test_non_positive_axes_are_rejected(bad, other):
    """Both are divisors. A zero here yields inf rather than an error unless
    it is refused up front."""
    with pytest.raises(ValueError):
        cap_axes(bad, other)
    with pytest.raises(ValueError):
        cap_axes(other, bad)
