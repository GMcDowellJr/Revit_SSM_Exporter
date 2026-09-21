"""Regressions for two defects review found in the Stage A step 2 geometry.

Both were reported by the Codex review bot on PR #210, both reproduced
exactly as described, and NEITHER turned any of the 1288 tests red. They are
the same shape: a number the capture reports and a number the capture renders
at, allowed to disagree, with every check still green.

P1 -- THE FLOOR WAS APPLIED TO THE WRONG RECTANGLE. The 64 px floor bounded
frame B's fitted axis, but PixelSize receives A's. A can sit far under the
floor while B sits over it, so the caller raised the number it handed Revit
and left the recorded lattice where it was. A 1 ft crop inside a 100 ft frame
was recorded at 20 px / 0.0533 ft-per-pixel and rendered at 64 px /
0.0156 -- fpp out by 3.2x, and every UV decoded from that capture wrong with
it. The dimension check passes, because 64 is exactly what was asked for.

P2 -- A RESOLUTION DROP THAT REPORTED ITSELF AS NO CAP. cap_axes rounds its
derived-axis prediction half-up, so a derived extent of 10000.25 px passes a
10000 px cap; the containing lattice then needs 10001 and the correction loop
steps the fitted axis down. Resolution fell from 150 to 148.5 dpi with
cap_applied False -- and that field is what the sidecar and the capped-export
diagnostic both lead with.

The fixture numbers are the reported ones, not ones chosen to be convenient.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vop_interwoven.resolution_contract import (  # noqa: E402
    MAX_STAGE_A_AXIS_PX,
    effective_export_dpi,
    feet_per_pixel_at_dpi,
    frame_export_geometry,
)

SCALE = 96.0
DPI = 150.0
FLOOR = 64


# --- P1 -------------------------------------------------------------------

TINY_FRAME = (0.0, 0.0, 100.0, 100.0)
TINY_CROP = (50.0, 50.0, 51.0, 51.0)      # 1 ft inside a 100 ft frame


def test_the_floor_is_enforced_on_the_rectangle_revit_actually_renders():
    """P1. The number handed to PixelSize is A's fitted axis, so that is what
    the floor has to bound -- and the lattice must be rebuilt around it, not
    left describing the pre-floor resolution."""
    g = frame_export_geometry(TINY_FRAME, TINY_CROP, SCALE, DPI, min_axis_px=FLOOR)
    assert g["floor_applied_to_crop"] is True
    assert g["requested_px"] >= FLOOR


def test_the_recorded_lattice_matches_what_the_floor_renders():
    """The defect itself: recorded feet-per-pixel must be the feet-per-pixel
    the exported image actually carries. Composed through the decoder rather
    than recomputed here, so a formula that drifted would still be caught."""
    g = frame_export_geometry(TINY_FRAME, TINY_CROP, SCALE, DPI, min_axis_px=FLOOR)
    s = g["crop_snapped_uv"]
    w, h = g["crop_px"]

    # the rectangle spans exactly its pixel count at the recorded fpp ...
    assert (s[2] - s[0]) == pytest.approx(w * g["achieved_fpp_ft"], rel=1e-12)
    assert (s[3] - s[1]) == pytest.approx(h * g["achieved_fpp_ft"], rel=1e-12)
    # ... and a decoder reading the sidecar recovers the same dpi.
    assert effective_export_dpi(s, w, h, SCALE) == pytest.approx(
        g["achieved_export_dpi"], rel=1e-9)
    # The pre-fix capture recorded 20 px against a 64 px render. Pin that the
    # two are now the same number.
    assert w == g["requested_px"] or h == g["requested_px"]


def test_the_floor_raises_resolution_rather_than_desynchronising_it():
    """Enforcing the floor means MORE resolution than requested, and the
    record says so. Silently keeping the requested figure is what made the
    defect invisible."""
    g = frame_export_geometry(TINY_FRAME, TINY_CROP, SCALE, DPI, min_axis_px=FLOOR)
    assert g["achieved_export_dpi"] > g["requested_export_dpi"]
    assert g["achieved_fpp_ft"] < g["requested_fpp_ft"]


def test_the_frame_and_the_crop_stay_on_one_shared_lattice():
    """Decision A's registration is what forbids the obvious shortcut of
    giving A its own feet-per-pixel. After the rebuild both are still on one
    fpp and A's offset is still whole pixels from B.min."""
    g = frame_export_geometry(TINY_FRAME, TINY_CROP, SCALE, DPI, min_axis_px=FLOOR)
    fpp = g["achieved_fpp_ft"]
    i0, j0 = g["crop_offset_px"]
    assert g["crop_snapped_uv"][0] == pytest.approx(TINY_FRAME[0] + i0 * fpp, abs=1e-9)
    assert g["crop_snapped_uv"][1] == pytest.approx(TINY_FRAME[1] + j0 * fpp, abs=1e-9)
    fs = g["frame_snapped_uv"]
    assert (fs[2] - fs[0]) == pytest.approx(g["frame_px"][0] * fpp, rel=1e-12)
    assert (fs[3] - fs[1]) == pytest.approx(g["frame_px"][1] * fpp, rel=1e-12)


def test_a_crop_already_above_the_floor_is_left_alone():
    """Control. Without it the assertions above would pass against a function
    that rebuilt the lattice unconditionally, which would over-resolve every
    ordinary capture."""
    g = frame_export_geometry((0.0, 0.0, 240.0, 160.0), (30.0, 20.0, 210.0, 140.0),
                              SCALE, DPI, min_axis_px=FLOOR)
    assert g["requested_px"] > FLOOR
    assert g["floor_applied_to_crop"] is False
    assert g["achieved_export_dpi"] == pytest.approx(DPI, rel=1e-6)


def test_the_ceiling_wins_when_the_floor_cannot_be_honoured():
    """The conflict case. Raising resolution far enough to give a minuscule
    crop 64 px can push the frame past the measured ceiling. The ceiling is a
    measurement and the floor a convention, so the ceiling wins -- and the
    record must not then claim the floor was applied."""
    # A 4 inch crop inside a 20,000 ft frame: 64 px on the crop would need a
    # frame of millions of pixels.
    g = frame_export_geometry((0.0, 0.0, 20000.0, 20000.0),
                              (0.0, 0.0, 0.333, 0.333), SCALE, DPI, min_axis_px=FLOOR)
    assert g["floor_applied_to_crop"] is False
    assert g["requested_px"] < FLOOR
    assert max(g["frame_px"]) <= MAX_STAGE_A_AXIS_PX


# --- P2 -------------------------------------------------------------------

def _lattice_correction_case():
    """The reported case: a derived extent of 10000.25 px, which rounds
    half-up to exactly the cap and so passes cap_axes untouched."""
    fpp = feet_per_pixel_at_dpi(SCALE, DPI)
    return (0.0, 0.0, 100.0 * fpp, 10000.25 * fpp)


def test_a_lattice_driven_resolution_drop_reports_as_capped():
    """P2. cap_applied is what the sidecar and the capped-export diagnostic
    lead with, so it has to be true whenever the ceiling lowered resolution
    -- by either route."""
    g = frame_export_geometry(_lattice_correction_case(), None, SCALE, DPI)
    assert g["lattice_corrections"] > 0
    assert g["cap_applied"] is True
    # and the drop is real, not just a flag
    assert g["achieved_export_dpi"] < g["requested_export_dpi"]


def test_the_two_cap_routes_stay_distinguishable():
    """Folding the lattice correction into cap_applied must not erase which
    route fired -- that is the difference between cap_axes mis-rounding and a
    genuinely oversized frame."""
    g = frame_export_geometry(_lattice_correction_case(), None, SCALE, DPI)
    assert g["cap_applied"] is True
    assert g["cap_applied_by_cap_axes"] is False

    # A genuinely oversized frame goes the other way.
    big = frame_export_geometry((0.0, 0.0, 4000.0, 2000.0), None, SCALE, DPI)
    assert big["cap_applied"] is True
    assert big["cap_applied_by_cap_axes"] is True


def test_an_uncapped_frame_reports_neither_route():
    """Control. Both flags false on an ordinary capture, or the assertions
    above hold for a function that reports capped unconditionally."""
    g = frame_export_geometry((0.0, 0.0, 240.0, 160.0), None, SCALE, DPI)
    assert g["cap_applied"] is False
    assert g["cap_applied_by_cap_axes"] is False
    assert g["lattice_corrections"] == 0
