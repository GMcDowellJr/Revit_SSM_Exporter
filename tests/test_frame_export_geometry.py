"""Composition tests for Stage A step 2's frame-derived export geometry.

WHAT THIS BINDS, AND WHY IT IS COMPOSED RATHER THAN CHECKED PIECEWISE.
``frame_export_geometry`` is the FORWARD mapping (frame B + dpi -> pixels)
and ``effective_export_dpi`` is the INVERSE (rendered rectangle + pixels ->
dpi). CLAUDE.md records the defect class where two such halves each pass
their own test and disagree with each other -- the uv<->pixel mappings, the
achieved-dpi figure twice. So the central test here runs the forward mapping
and feeds its OWN output straight into the inverse, and requires the dpi to
come back. Testing either alone would bind each to its own copy.

The grid-independence test is the step's first gate. Before step 2 the
export's pixel size came from ``raster.W * raster.cell_size_ft`` -- the frame
rounded UP to whole cells -- so the same view at a different analysis
resolution produced a different image. Here the cell size is not a parameter
at all, which is what makes the gate checkable: the test varies what USED to
be the coupling and requires every exported quantity to sit still. The
call-site half of that gate, which is where the coupling actually lived, is
in tests/test_frame_export_geometry_call_site.py.
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

# A frame, a narrower model crop inside it, and a view scale. Numbers chosen
# so B is not square and A is not centred in B -- a square frame or a centred
# crop would let a transposed axis or a dropped offset pass unnoticed.
FRAME = (10.0, -4.0, 90.0, 36.0)          # 80 x 40 ft
CROP = (22.0, 3.0, 70.0, 27.0)            # 48 x 24 ft, offset (12, 7) from B.min
SCALE = 96.0
DPI = 150.0


def test_forward_and_inverse_agree_on_the_snapped_crop():
    """The achieved dpi this function reports is the dpi a decoder recovers
    from the rectangle and the pixel counts it hands out. Composed, not
    checked separately: these are the two halves that drifted before."""
    g = frame_export_geometry(FRAME, CROP, SCALE, DPI)
    recovered = effective_export_dpi(
        g["crop_snapped_uv"], g["crop_px"][0], g["crop_px"][1], SCALE)
    assert recovered == pytest.approx(g["achieved_export_dpi"], rel=1e-9)


def test_forward_and_inverse_agree_when_the_cap_fires():
    """Same composition, on the path where resolution actually drops. An
    uncapped-only check would never exercise the re-derived fpp."""
    huge = (0.0, 0.0, 4000.0, 2000.0)
    g = frame_export_geometry(huge, None, SCALE, DPI)
    assert g["cap_applied"] is True
    recovered = effective_export_dpi(
        g["crop_snapped_uv"], g["crop_px"][0], g["crop_px"][1], SCALE)
    assert recovered == pytest.approx(g["achieved_export_dpi"], rel=1e-9)


def test_achieved_equals_requested_dpi_when_nothing_caps_or_floors():
    """With no cap and no floor in play the achieved figure must BE the
    request. If this drifts, the fpp round trip has a scale error that the
    relative comparisons above would absorb."""
    g = frame_export_geometry(FRAME, CROP, SCALE, DPI)
    assert g["cap_applied"] is False
    assert g["achieved_export_dpi"] == pytest.approx(DPI, rel=1e-6)
    assert g["achieved_fpp_ft"] == pytest.approx(
        feet_per_pixel_at_dpi(SCALE, DPI), rel=1e-6)


# --- GATE 1: the export is a function of B and dpi, not of the grid --------

def test_export_pixels_and_fpp_do_not_depend_on_the_analysis_grid():
    """GATE: varying cell_size_ft leaves export px and fpp unchanged.

    Stated at the level this function operates at: a cell size cannot change
    the answer because it cannot be passed in. The test spells out the frames
    a range of cell sizes WOULD have produced under the old grid-derived
    sizing -- ceil(extent/cell)*cell -- and requires the same pixel counts and
    the same fpp from all of them, since the frame itself never moved."""
    import math

    results = []
    for cell_size_ft in (0.5, 1.0, 2.0, 3.0, 7.0, 16.0):
        # What the shipped sizing path would have handed the export: the
        # frame's extent rounded UP to a whole number of cells.
        w_cells = int(math.ceil((FRAME[2] - FRAME[0]) / cell_size_ft))
        h_cells = int(math.ceil((FRAME[3] - FRAME[1]) / cell_size_ft))
        grid_u_ft = w_cells * cell_size_ft
        grid_v_ft = h_cells * cell_size_ft
        # Confirm the fixture discriminates: at least one cell size must make
        # the grid rectangle genuinely differ from the frame, or this test
        # would pass against the defect it is meant to catch.
        results.append((grid_u_ft, grid_v_ft,
                        frame_export_geometry(FRAME, CROP, SCALE, DPI)))

    assert any(abs(u - (FRAME[2] - FRAME[0])) > 1e-9
               or abs(v - (FRAME[3] - FRAME[1])) > 1e-9
               for u, v, _ in results), (
        "fixture is not discriminating: no cell size made the grid rectangle "
        "differ from the frame, so this would pass against grid-derived sizing")

    first = results[0][2]
    for _u, _v, g in results[1:]:
        assert g["crop_px"] == first["crop_px"]
        assert g["frame_px"] == first["frame_px"]
        assert g["achieved_fpp_ft"] == pytest.approx(first["achieved_fpp_ft"], rel=1e-12)
        assert g["requested_px"] == first["requested_px"]


# --- GATE 2: an oversized B caps without moving B -------------------------

def test_oversized_frame_caps_without_moving_the_frame():
    """GATE: an oversized B yields cap_applied=True, B unchanged, and a lower
    achieved dpi recorded. "B unchanged" is the whole point of step 2 -- the
    shipped grid cap clipped and re-centred the frame instead."""
    frame = (100.0, 200.0, 4100.0, 2200.0)   # 4000 x 2000 ft
    g = frame_export_geometry(frame, None, SCALE, DPI)

    assert g["cap_applied"] is True
    # B is whole: the extent the geometry reports is the extent passed in.
    assert g["frame_extent_ft"] == pytest.approx((4000.0, 2000.0))
    # ... and the snapped crop still spans the frame, to within the one
    # lattice pixel snapping is allowed to move an edge.
    assert g["crop_snapped_uv"][0] == pytest.approx(frame[0], abs=g["achieved_fpp_ft"])
    assert g["crop_snapped_uv"][2] == pytest.approx(frame[2], abs=g["achieved_fpp_ft"])
    # The drop landed on resolution, and it is recorded.
    assert g["achieved_export_dpi"] < g["requested_export_dpi"]
    assert g["achieved_fpp_ft"] > g["requested_fpp_ft"]
    assert max(g["frame_px"]) <= MAX_STAGE_A_AXIS_PX


def test_the_uncapped_control_is_not_capped():
    """Control. Without it every scenario above would also pass against a
    function that reported cap_applied=True unconditionally."""
    g = frame_export_geometry(FRAME, CROP, SCALE, DPI)
    assert g["cap_applied"] is False
    assert g["achieved_export_dpi"] == pytest.approx(g["requested_export_dpi"], rel=1e-6)


# --- decision A: A snaps to B's lattice -----------------------------------

def test_crop_offset_is_a_whole_number_of_pixels_from_frame_min():
    """Decision A: A is snapped to B's pixel lattice at the shared fpp, so
    registering the two is an integer translation with no resampling."""
    g = frame_export_geometry(FRAME, CROP, SCALE, DPI)
    i0, j0 = g["crop_offset_px"]
    fpp = g["achieved_fpp_ft"]
    assert g["crop_snapped_uv"][0] == pytest.approx(FRAME[0] + i0 * fpp, rel=1e-9, abs=1e-9)
    assert g["crop_snapped_uv"][1] == pytest.approx(FRAME[1] + j0 * fpp, rel=1e-9, abs=1e-9)
    # And the offset is where the crop actually is, not zero by accident.
    assert i0 > 0 and j0 > 0


def test_snapping_never_clips_the_crop():
    """Snapping moves A's corners OUTWARD. Inward would silently drop
    rendered content off the edge of the capture, which no later stage could
    detect or recover."""
    g = frame_export_geometry(FRAME, CROP, SCALE, DPI)
    s = g["crop_snapped_uv"]
    assert s[0] <= CROP[0] + 1e-9
    assert s[1] <= CROP[1] + 1e-9
    assert s[2] >= CROP[2] - 1e-9
    assert s[3] >= CROP[3] - 1e-9


def test_snapped_crop_stays_inside_the_realised_frame():
    """A snapped outward must not escape the frame the capture realises, or
    the recorded pixel offset would address pixels the image does not
    contain. The bound is frame_snapped_uv -- B rounded out to the lattice --
    because an image is a whole number of pixels and an off-lattice B is not
    a rectangle any export can span."""
    g = frame_export_geometry(FRAME, CROP, SCALE, DPI)
    s = g["crop_snapped_uv"]
    fs = g["frame_snapped_uv"]
    assert s[0] >= fs[0] - 1e-9
    assert s[1] >= fs[1] - 1e-9
    assert s[2] <= fs[2] + 1e-9
    assert s[3] <= fs[3] + 1e-9
    # The realised frame contains B and grows only away from B.min, so the
    # origin the whole frame-reconciliation invariant rests on does not move.
    assert fs[0] == FRAME[0] and fs[1] == FRAME[1]
    assert fs[2] >= FRAME[2] - 1e-9 and fs[3] >= FRAME[3] - 1e-9


def test_a_crop_equal_to_the_frame_reports_a_zero_offset():
    """The no-narrowing case. Distinguished from "narrowed by nothing
    measurable" by crop_is_frame, not by the offset being zero."""
    g = frame_export_geometry(FRAME, None, SCALE, DPI)
    assert g["crop_offset_px"] == (0, 0)
    assert g["crop_is_frame"] is True
    assert g["crop_px"] == g["frame_px"]


def test_the_derived_axis_prediction_is_exact_not_an_aspect_guess():
    """Both axes are whole pixel counts of ONE snapped rectangle, so the
    predicted derived axis is the rectangle's other side rather than a float
    ratio applied to the fitted one."""
    for direction in ("horizontal", "vertical"):
        g = frame_export_geometry(FRAME, CROP, SCALE, DPI, fit_direction=direction)
        u_px, v_px = g["crop_px"]
        if direction == "vertical":
            assert g["requested_px"] == v_px and g["predicted_derived_px"] == u_px
            assert g["requested_axis"] == "height"
        else:
            assert g["requested_px"] == u_px and g["predicted_derived_px"] == v_px
            assert g["requested_axis"] == "width"


def test_fit_direction_does_not_change_the_rendered_rectangle():
    """Which axis Revit is handed is a request mechanism, not a frame
    decision. A tall view must not lose resolution merely by being fitted
    vertically -- the shipped sizing path had exactly that bug for the
    paper-inch term, and this pins that the frame-derived path cannot."""
    h = frame_export_geometry(FRAME, CROP, SCALE, DPI, fit_direction="horizontal")
    v = frame_export_geometry(FRAME, CROP, SCALE, DPI, fit_direction="vertical")
    assert h["crop_px"] == v["crop_px"]
    assert h["crop_snapped_uv"] == pytest.approx(v["crop_snapped_uv"])
    assert h["achieved_fpp_ft"] == pytest.approx(v["achieved_fpp_ft"], rel=1e-12)


def test_a_crop_outside_the_frame_is_clamped_not_negative():
    """Worst-case-first: compute_model_crop already intersects, so this
    should be unreachable -- but a negative pixel offset would be written
    into the sidecar as if it meant something."""
    g = frame_export_geometry(FRAME, (-50.0, -50.0, 500.0, 500.0), SCALE, DPI)
    assert g["crop_offset_px"] == (0, 0)
    assert g["crop_px"][0] <= g["frame_px"][0]
    assert g["crop_px"][1] <= g["frame_px"][1]


@pytest.mark.parametrize("bad", [
    (0.0, 0.0, 0.0, 10.0),      # zero width
    (0.0, 0.0, 10.0, 0.0),      # zero height
    (0.0, 0.0, -10.0, 10.0),    # inverted
])
def test_a_degenerate_frame_is_refused_not_guessed(bad):
    """A frame with no extent cannot be sized. Refusing beats substituting a
    fallback rectangle that would be recorded as if it were the view's."""
    with pytest.raises(ValueError):
        frame_export_geometry(bad, None, SCALE, DPI)
