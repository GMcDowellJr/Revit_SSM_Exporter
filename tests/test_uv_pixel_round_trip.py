"""uv -> pixel -> uv round-trip across the forward and inverse mappings.

Nothing in this repo composes tools/link_identity_resolver.py's
_uv_rect_to_pixel_bbox() (the uv->pixel direction) with
tools/decode_stage_a_color_id.py's _pixel_corner_to_uv() (the pixel->uv
direction), even though the former's docstring declares itself the inverse
of the latter. That absence is what let the two drift apart: D1 taught the
decode side to model Revit's ExportImage aspect-clamp padding, and the
resolver side was not changed with it.

In production the two meet at link_identity_resolver.py:365, where
resolve_category() maps a candidate's Revit-side bbox_corners_uv into the
same TIFF's pixel space that decode reads back out. If the two mappings
disagree, a LINK candidate's footprint is scored against the wrong pixels.

TOLERANCE
---------
One pixel, expressed in feet (1 * feet_per_pixel), per case.

Justified from the fixture geometry alone: _uv_rect_to_pixel_bbox returns
INCLUSIVE INTEGER pixel bounds, quantized at link_identity_resolver.py:
233-236 as x0 = floor(min_px) and x1 = ceil(max_px) - 1. Rounding is
outward on both sides and strictly under one pixel on each, so an exact
inverse still cannot reproduce the original uv rectangle more closely than
one pixel's worth of feet. That is the quantization floor of the public
function's own return type, not an empirical residual: it is derived here
from the mapping's rounding rule and the case's own fpp, and it is
deliberately NOT anchored to any observed excursion figure.

Everything below is synthetic. No Revit, no capture, no link data.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import link_identity_resolver as lir  # noqa: E402
from tools import decode_stage_a_color_id as dsc  # noqa: E402


# (label, crop rect, image px, uv rect to round-trip)
#
# The crop/image pairings are chosen so the PIXEL aspect does not match the
# CROP aspect, which is what makes the clamp pad non-zero. No aspect ratio
# is asserted or hardcoded anywhere: each case's pad is derived by the same
# rule decode uses -- fpp = max(crop_u/w, crop_v/h), pad follows -- and the
# expectations below are on the SIGN of each pad, not its ratio.
CASES = [
    # (a) pad on u. crop is relatively wider-per-pixel on v, so v is the
    #     unpadded axis and the u axis carries the pad.
    ("pad_on_u", (0.0, 0.0, 40.0, 200.0), (400, 1000), (8.0, 40.0, 30.0, 160.0)),
    # (b) pad on v -- Section 1's measured shape (9960 x 996 px over a
    #     531.197 x 43.3333 ft crop), whose recovered pad_y is ~91.75 px.
    ("pad_on_v", (0.0, 0.0, 531.197, 43.3333), (9960, 996), (20.0, 4.0, 300.0, 38.0)),
    # (c) pad zero: crop aspect and pixel aspect agree, so the buggy and the
    #     corrected forward mappings are identical. Without this case the
    #     test would not show that it discriminates rather than simply
    #     failing everywhere.
    ("pad_zero", (0.0, 0.0, 80.0, 60.0), (400, 300), (10.0, 10.0, 70.0, 50.0)),
]


def _corners(umin, vmin, umax, vmax):
    """Same 4-corner order project_bbox_corners_uv() persists
    (revit/collection.py:891-895): min-min, max-min, max-max, min-max."""
    return [[umin, vmin], [umax, vmin], [umax, vmax], [umin, vmax]]


def _round_trip(uv_rect, crop, image):
    """uv rect -> pixel bbox -> uv rect, through the two production mappings."""
    umin, vmin, umax, vmax = uv_rect
    image_w, image_h = image

    px = lir._uv_rect_to_pixel_bbox(_corners(umin, vmin, umax, vmax), crop, image_w, image_h)
    assert px is not None, "fixture places the rect off-image; it must overlap"
    x0, y0, x1, y1 = px

    # _uv_rect_to_pixel_bbox returns INCLUSIVE pixel indices, so the pixel
    # CORNER span the rect covers is x0 .. x1+1 (and y0 .. y1+1). Reading
    # back at x1/y1 instead would lose one pixel by convention, not by
    # mapping error.
    back_umin, back_vmax = dsc._pixel_corner_to_uv(x0, y0, crop, image_w, image_h)
    back_umax, back_vmin = dsc._pixel_corner_to_uv(x1 + 1, y1 + 1, crop, image_w, image_h)
    return (back_umin, back_vmin, back_umax, back_vmax)


@pytest.mark.parametrize("label,crop,image,uv_rect", CASES, ids=[c[0] for c in CASES])
def test_fixture_pads_have_the_shape_each_case_claims(label, crop, image, uv_rect):
    """Self-check: each case must actually exercise the pad it is named for,
    or the round-trip assertion below proves nothing."""
    fpp, pad_x, pad_y = dsc._clamp_pad_geometry(crop, image[0], image[1])
    if label == "pad_on_u":
        assert pad_x > 1.0 and pad_y == pytest.approx(0.0, abs=1e-9)
    elif label == "pad_on_v":
        assert pad_y > 1.0 and pad_x == pytest.approx(0.0, abs=1e-9)
        assert pad_y == pytest.approx(91.75, abs=0.5)  # Section 1's shape
    else:
        assert pad_x == pytest.approx(0.0, abs=1e-9)
        assert pad_y == pytest.approx(0.0, abs=1e-9)
    assert fpp > 0.0


@pytest.mark.parametrize("label,crop,image,uv_rect", CASES, ids=[c[0] for c in CASES])
def test_uv_pixel_uv_round_trip_returns_the_original_rect(label, crop, image, uv_rect):
    fpp, _pad_x, _pad_y = dsc._clamp_pad_geometry(crop, image[0], image[1])
    tol = 1.0 * fpp  # one pixel, in feet -- see module docstring

    got = _round_trip(uv_rect, crop, image)
    worst = max(abs(g - e) for g, e in zip(got, uv_rect))
    assert worst <= tol, (
        "{0}: uv -> pixel -> uv diverged by {1:.4f} ft (tolerance {2:.4f} ft = 1 px)\n"
        "  sent back : {3}\n"
        "  original  : {4}".format(label, worst, tol, got, uv_rect))
