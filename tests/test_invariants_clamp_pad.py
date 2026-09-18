"""Property tests for the clamp-pad geometry's STATED invariants.

WHY THESE EXIST
---------------
Every defect found by review on PR #201 was a violation of an invariant that
was already WRITTEN DOWN in a comment and never asserted. The two dpi defects
both violated `effective_export_dpi == view_scale / (12 * feet_per_pixel)`, a
claim that sat in color_id_buffer.py's comment block through two wrong
implementations.

So the rule these files encode is narrow and mechanical: **if an identity is
claimed in prose, assert it over generated inputs.** Each test below names the
comment it is holding to account.

A fixed-case test can only check the shapes someone thought of. The second dpi
defect needed a capture whose FITTED axis was the padded one -- a shape absent
from every fixture and from all six views of the reference run. Generated
inputs found an equivalent in a few hundred examples, and the minimal
counterexample was a 1x1 ft crop into 64x65 px: no contrived aspect ratio, just
a one-pixel asymmetry.

BOUNDS ARE DELIBERATE, NOT DECORATION
-------------------------------------
Extents and pixel counts are bounded to physically plausible captures. Letting
hypothesis reach 1e308 would exercise float behaviour rather than this model,
and a property test that fails on unreachable inputs gets muted, which is worse
than not having one.
"""
import math
import sys
from pathlib import Path

import pytest
from hypothesis import assume, given, settings, strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from tools.clamp_pad_geometry import clamp_pad_geometry  # noqa: E402
from tools import decode_stage_a_color_id as dsc  # noqa: E402
from vop_interwoven.resolution_contract import (  # noqa: E402
    effective_export_dpi as producer_effective_dpi,
)
import link_identity_resolver as lir  # noqa: E402

# A Stage A capture: feet of model, pixels of image. Both ends are generous
# against the reference run (531 ft / 9960 px was its widest).
FT = st.floats(min_value=0.1, max_value=5000.0, allow_nan=False, allow_infinity=False)
PX = st.integers(min_value=16, max_value=20000)
SCALE = st.sampled_from([1.0, 12.0, 48.0, 96.0, 192.0, 384.0])

CAPTURE = st.tuples(FT, FT, PX, PX)


def _geom(cu, cv, w, h):
    return clamp_pad_geometry((0.0, 0.0, cu, cv), w, h, measured_w=w, measured_h=h)


@settings(max_examples=400, deadline=None)
@given(cap=CAPTURE)
def test_content_plus_pads_exactly_fills_the_image(cap):
    """clamp_pad_geometry.py: "the pad on the other axis follows from it,
    split evenly". Split evenly means the rendered content plus two equal pads
    is the image -- on BOTH axes, or the model does not describe the file."""
    cu, cv, w, h = cap
    fpp, pad_x, pad_y = _geom(cu, cv, w, h)
    assert math.isclose(cu / fpp + 2.0 * pad_x, w, rel_tol=1e-9, abs_tol=1e-9)
    assert math.isclose(cv / fpp + 2.0 * pad_y, h, rel_tol=1e-9, abs_tol=1e-9)


@settings(max_examples=400, deadline=None)
@given(cap=CAPTURE)
def test_exactly_one_axis_is_unpadded(cap):
    """"take the LARGER of the two ratios" means the axis that supplied
    feet_per_pixel has no pad: its extent already accounts for every pixel.
    If both axes came back padded, the ratio was taken from neither."""
    cu, cv, w, h = cap
    fpp, pad_x, pad_y = _geom(cu, cv, w, h)
    assert min(pad_x, pad_y) == pytest.approx(0.0, abs=1e-6), (
        "neither axis is unpadded: fpp={0} pads=({1}, {2})".format(fpp, pad_x, pad_y))


@settings(max_examples=400, deadline=None)
@given(cap=CAPTURE)
def test_pads_are_non_negative_when_the_dimensions_agree(cap):
    """decode_stage_a_color_id.py:798 -- "pads are >= 0 whenever fpp and the
    pads share their dimensions". That is what makes a NEGATIVE pad a usable
    signal for Guard 3 rather than ordinary noise, so it has to be true."""
    cu, cv, w, h = cap
    _fpp, pad_x, pad_y = _geom(cu, cv, w, h)
    assert pad_x >= -1e-6 and pad_y >= -1e-6


@settings(max_examples=300, deadline=None)
@given(cap=CAPTURE, scale=SCALE)
def test_effective_dpi_identity(cap, scale):
    """color_id_buffer.py's effective_export_dpi: "Identically
    view_scale / (12 * feet_per_pixel) for the feet_per_pixel a decoder
    derives from this same sidecar's bounds_xy".

    THE ONE THAT WAS WRONG TWICE. First against the grid extent instead of the
    rendered crop, then against the fitted axis instead of the unpadded one.
    Both times the claim above was sitting in the comment directly over the
    code that violated it.

    The producer's rule is replicated here rather than imported, because
    vop_interwoven is deployed by copying the package into Revit/Dynamo and
    must not depend on tools/ -- so the two arithmetics are genuinely separate
    and this is what binds them.
    """
    cu, cv, w, h = cap
    fpp, _px, _py = _geom(cu, cv, w, h)
    # THE PRODUCTION FUNCTION, not a replica of it. An earlier version of this
    # test reimplemented the formula here, which bound the decoder to the
    # TEST's idea of the producer: production could regress to the fitted-axis
    # or grid-extent form and this property would still pass. That was a review
    # finding on PR #202, and it is the reason
    # resolution_contract.effective_export_dpi() exists as a callable function
    # rather than inline inside a 1,500-line Revit-bound one.
    producer = producer_effective_dpi((0.0, 0.0, cu, cv), w, h, scale)
    assert producer is not None
    assert math.isclose(producer, scale / (12.0 * fpp), rel_tol=1e-9)


@settings(max_examples=300, deadline=None)
@given(cap=CAPTURE, x=st.integers(0, 20000), y=st.integers(0, 20000))
def test_pixel_to_uv_is_monotonic_in_x_and_antitonic_in_y(cap, x, y):
    """`u = xmin + (x - pad_x) * fpp` / `v = ymax - (y - pad_y) * fpp`.
    Rightward pixels are larger u; downward pixels are SMALLER v. A sign slip
    here inverts a whole capture while every extent stays correct."""
    cu, cv, w, h = cap
    assume(x < w and y < h)
    bounds = (0.0, 0.0, cu, cv)
    u0, v0 = dsc._pixel_corner_to_uv(x, y, bounds, w, h)
    u1, _ = dsc._pixel_corner_to_uv(x + 1, y, bounds, w, h)
    _, v1 = dsc._pixel_corner_to_uv(x, y + 1, bounds, w, h)
    assert u1 > u0
    assert v1 < v0


@settings(max_examples=250, deadline=None)
@given(cap=CAPTURE, fu=st.floats(0.0, 0.45), fv=st.floats(0.0, 0.45),
       du=st.floats(0.1, 0.5), dv=st.floats(0.1, 0.5))
def test_uv_pixel_uv_round_trip_within_one_pixel(cap, fu, fv, du, dv):
    """Generalizes tests/test_uv_pixel_round_trip.py from three fixed cases.

    That file's tolerance is derived from the inverse's own rounding rule --
    inclusive integer bounds, floor/ceil at link_identity_resolver.py:233-236 --
    so an exact inverse still cannot beat one pixel. The three fixed cases pin
    the shapes someone thought of; this pins the rule.
    """
    cu, cv, w, h = cap
    fpp, _px, _py = _geom(cu, cv, w, h)
    umin, umax = fu * cu, (fu + du) * cu
    vmin, vmax = fv * cv, (fv + dv) * cv
    # A rect thinner than a pixel round-trips to nothing measurable; that is
    # quantization, not a mapping error, and the fixed-case file says so too.
    assume(umax - umin > 2.0 * fpp and vmax - vmin > 2.0 * fpp)

    crop = (0.0, 0.0, cu, cv)
    corners = [[umin, vmin], [umax, vmin], [umax, vmax], [umin, vmax]]
    px = lir._uv_rect_to_pixel_bbox(corners, crop, w, h)
    assume(px is not None)
    x0, y0, x1, y1 = px

    back_umin, back_vmax = dsc._pixel_corner_to_uv(x0, y0, crop, w, h)
    back_umax, back_vmin = dsc._pixel_corner_to_uv(x1 + 1, y1 + 1, crop, w, h)
    tol = 1.0 * fpp + 1e-9
    assert abs(back_umin - umin) <= tol
    assert abs(back_umax - umax) <= tol
    assert abs(back_vmin - vmin) <= tol
    assert abs(back_vmax - vmax) <= tol


@settings(max_examples=200, deadline=None)
@given(cu=FT, cv=FT, w=PX, h=PX)
def test_degenerate_geometry_raises_rather_than_returning_a_number(cu, cv, w, h):
    """The helper's contract: "there is no number to return and guessing one
    would be fabricating geometry". A zero extent must not quietly yield inf."""
    for bad in ((0.0, 0.0, 0.0, cv), (0.0, 0.0, cu, 0.0)):
        with pytest.raises(ValueError):
            clamp_pad_geometry(bad, w, h)
