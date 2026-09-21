"""GATE: a frame-derived capture still round-trips through the DECODER.

The clamp/pad fixtures in tests/test_invariants_clamp_pad.py generate their
own crop rectangles and pixel counts. That proves the decoder is
self-consistent; it says nothing about whether the rectangles STAGE A now
produces survive it, because the producer never appears in that file.

Stage A step 2 changed what the producer emits: the rendered rectangle is now
A snapped onto B's pixel lattice, and the pixel counts are whole multiples of
one shared feet-per-pixel. So the composition worth asserting is
producer -> decoder, with the producer's own output as the decoder's input.
This is the same shape as tests/test_uv_pixel_round_trip.py and for the same
stated reason: testing each side against its own fixtures binds each to its
own copy.

NON-SQUARE AND PADDED CASES ARE PINNED HERE, not left to a generator. The
step 2 gate names a non-square padded fixture specifically, and a property
test that merely *can* generate one does not establish that it did.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from tools.clamp_pad_geometry import clamp_pad_geometry  # noqa: E402
from vop_interwoven.resolution_contract import (  # noqa: E402
    effective_export_dpi,
    frame_export_geometry,
)

SCALE = 96.0
DPI = 150.0

# (label, frame, crop) -- deliberately non-square, and the crop deliberately
# off-centre so a dropped or doubled offset cannot cancel out.
CASES = [
    ("wide frame, narrower crop", (0.0, 0.0, 240.0, 60.0), (30.0, 12.0, 150.0, 48.0)),
    ("tall frame, narrower crop", (0.0, 0.0, 60.0, 240.0), (12.0, 30.0, 48.0, 150.0)),
    ("crop is frame, non-square", (0.0, 0.0, 240.0, 60.0), None),
    ("off-lattice extents", (1.7, -3.1, 61.9, 43.3), (9.3, 2.2, 40.7, 29.9)),
]


@pytest.mark.parametrize("label,frame,crop", CASES,
                         ids=[c[0].replace(" ", "_").replace(",", "") for c in CASES])
def test_the_decoder_recovers_the_producers_feet_per_pixel(label, frame, crop):
    """The producer's achieved fpp is what clamp_pad_geometry derives from the
    rectangle and pixel counts the producer emitted. These two numbers
    disagreeing is the defect this repo found twice in the dpi figure."""
    g = frame_export_geometry(frame, crop, SCALE, DPI)
    w, h = g["crop_px"]
    fpp, pad_x, pad_y = clamp_pad_geometry(g["crop_snapped_uv"], w, h,
                                           measured_w=w, measured_h=h)
    assert fpp == pytest.approx(g["achieved_fpp_ft"], rel=1e-12)
    # An unpadded render: the image IS the crop, so both pads are zero.
    assert pad_x == pytest.approx(0.0, abs=1e-9)
    assert pad_y == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize("label,frame,crop", CASES,
                         ids=[c[0].replace(" ", "_").replace(",", "") for c in CASES])
def test_the_decoder_recovers_the_producers_dpi(label, frame, crop):
    g = frame_export_geometry(frame, crop, SCALE, DPI)
    w, h = g["crop_px"]
    assert effective_export_dpi(g["crop_snapped_uv"], w, h, SCALE) == pytest.approx(
        g["achieved_export_dpi"], rel=1e-12)


@pytest.mark.parametrize("pad_px", [1, 7, 64])
def test_a_non_square_padded_capture_still_recovers_fpp(pad_px):
    """GATE: the round-trip clamp fixtures still pass, INCLUDING a non-square
    padded fixture.

    ExportImage's aspect clamp pads the short axis, and that padding is
    Revit's and cannot be disabled. The producer does not know about it -- it
    emits the crop and the pixel counts it asked for -- so the decoder has to
    recover feet-per-pixel from a file that is LARGER than the crop on one
    axis. max(crop_u/w, crop_v/h) selects the unpadded axis, which is why the
    padded axis must not be the one that decides.

    The frame here is 4:1, so the short axis is the one that gets padded.
    """
    frame = (0.0, 0.0, 240.0, 60.0)
    crop = (30.0, 12.0, 150.0, 48.0)
    g = frame_export_geometry(frame, crop, SCALE, DPI)
    w, h = g["crop_px"]
    assert w != h, "fixture must be non-square or it does not test the gate"

    # Revit hands back a file padded on the SHORT axis.
    image_h = h + 2 * pad_px
    fpp, pad_x, pad_y = clamp_pad_geometry(g["crop_snapped_uv"], w, image_h,
                                           measured_w=w, measured_h=h)

    assert fpp == pytest.approx(g["achieved_fpp_ft"], rel=1e-12)
    assert pad_x == pytest.approx(0.0, abs=1e-9)
    assert pad_y == pytest.approx(float(pad_px), rel=1e-9)
    # The producer's rectangle is still recoverable from the padded file.
    assert effective_export_dpi(g["crop_snapped_uv"], w, h, SCALE) == pytest.approx(
        g["achieved_export_dpi"], rel=1e-12)


def test_the_pad_fixture_is_not_vacuous():
    """Control. pad_px=0 must give a zero pad, or the assertions above would
    hold for a decoder that reported zero padding unconditionally."""
    frame = (0.0, 0.0, 240.0, 60.0)
    crop = (30.0, 12.0, 150.0, 48.0)
    g = frame_export_geometry(frame, crop, SCALE, DPI)
    w, h = g["crop_px"]
    _fpp, _px, pad_y = clamp_pad_geometry(g["crop_snapped_uv"], w, h,
                                          measured_w=w, measured_h=h)
    assert pad_y == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize("label,frame,crop", CASES,
                         ids=[c[0].replace(" ", "_").replace(",", "") for c in CASES])
def test_the_snapped_rectangle_is_a_whole_number_of_pixels(label, frame, crop):
    """What makes the recovery above exact rather than approximate: after
    snapping, each axis of the rendered rectangle is its pixel count times one
    shared fpp, with no residue for the decoder to absorb."""
    g = frame_export_geometry(frame, crop, SCALE, DPI)
    s = g["crop_snapped_uv"]
    w, h = g["crop_px"]
    fpp = g["achieved_fpp_ft"]
    assert (s[2] - s[0]) == pytest.approx(w * fpp, rel=1e-12)
    assert (s[3] - s[1]) == pytest.approx(h * fpp, rel=1e-12)
