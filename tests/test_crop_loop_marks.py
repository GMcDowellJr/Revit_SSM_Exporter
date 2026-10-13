"""The crop LOOP and the registration marks (stage_a_registration, 2026-10-12).

Run 2025-06-21__20261008T093539, view 3939979: crop box 1385.57 x 1238.48 ft,
the loop a 1192.91 x 997.10 ft rectangle rotated 12.91 deg inside it, the
twelve ticks 24 px inside the box's edges, 0 of 12 found. Every number here
is that view's (tools/notes/RUN_2025-06-21_20261008T093539_ROTATION.md).
"""
import math

import pytest

import vop_interwoven.stage_a_registration as registration

from vop_interwoven.stage_a_registration import (
    classify_crop_loop, largest_axis_aligned_rectangle, mark_reference_from_loop,
    point_in_loop, registration_mark_segments, ticks_outside_loop,
)

CROP_UV = [-514.6273287558727, -504.50802712061466, 870.9419218318508, 733.9671000622054]
FPP_FT = 0.33330989910698183          # 3939979's achieved feet per pixel
TOL_FT = 0.05 * FPP_FT                # the crop read-back tolerance at that lattice


def rotated_loop(crop_uv, w, h, theta_deg):
    """A w x h rectangle rotated theta, centred in crop_uv: four vertices."""
    u0, v0, u1, v1 = crop_uv
    cu, cv = (u0 + u1) / 2.0, (v0 + v1) / 2.0
    c, s = math.cos(math.radians(theta_deg)), math.sin(math.radians(theta_deg))
    return [[cu + du * c - dv * s, cv + du * s + dv * c]
            for du, dv in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]


LOOP_3939979 = rotated_loop(CROP_UV, 1192.906, 997.101, 12.91)
BOX_LOOP = [[CROP_UV[0], CROP_UV[1]], [CROP_UV[2], CROP_UV[1]],
            [CROP_UV[2], CROP_UV[3]], [CROP_UV[0], CROP_UV[3]]]


def test_the_rotated_loops_bounding_box_is_the_crop_box():
    c = classify_crop_loop(LOOP_3939979, CROP_UV, TOL_FT)
    assert c["rectangular"] is True and c["axis_aligned"] is False
    assert abs(c["rotation_deg"] - 12.91) < 1e-6
    # The loop's box is the crop to 0.1 ft (as measured on the run), not to
    # the 0.017 ft read-back tolerance: the fixture's w, h are rounded.
    assert c["bbox_matches_crop"] is False
    assert all(abs(a - b) < 0.2 for a, b in zip(c["bbox_uv"], CROP_UV))
    assert classify_crop_loop(LOOP_3939979, CROP_UV, 0.2)["bbox_matches_crop"] is True
    assert c["vertex_count"] == 4


def test_the_box_loop_is_axis_aligned_and_keeps_the_crop_as_reference():
    c = classify_crop_loop(BOX_LOOP, CROP_UV, TOL_FT)
    assert (c["rectangular"], c["axis_aligned"], c["rotation_deg"]) == (True, True, 0.0)
    ref, record = mark_reference_from_loop(BOX_LOOP, CROP_UV, TOL_FT)
    assert ref == [float(x) for x in CROP_UV] and record["reference"] == "crop_box"


def test_a_six_sided_loop_is_not_rectangular():
    loop = [[0, 0], [10, 0], [10, 4], [6, 4], [6, 8], [0, 8]]     # an L
    c = classify_crop_loop(loop, [0, 0, 10, 8], 0.01)
    assert c["rectangular"] is False and c["axis_aligned"] is False
    assert c["vertex_count"] == 6


def test_the_nominal_ticks_are_outside_the_rotated_loop_and_inside_the_box():
    """The shipped placement, on 3939979's numbers: 12 of 12 outside (0 found)."""
    layout = registration_mark_segments(CROP_UV, FPP_FT)
    assert layout["state"] == "value"
    assert len(ticks_outside_loop(layout, LOOP_3939979)) == 12
    assert ticks_outside_loop(layout, BOX_LOOP) == []


def test_ticks_placed_on_the_inscribed_rectangle_are_inside_the_loop():
    ref, record = mark_reference_from_loop(LOOP_3939979, CROP_UV, TOL_FT)
    assert record["reference"] == "crop_loop_inscribed"
    u0, v0, u1, v1 = ref
    # Inside the loop, and a large share of it (3132 x 2348 px of 3579 x 2991
    # measured on the pixels: 69 % of the loop's area).
    assert all(point_in_loop(LOOP_3939979, u, v) for u, v in ((u0, v0), (u1, v0), (u1, v1), (u0, v1)))
    assert record["inscribed_area_fraction_of_bbox"] > 0.4
    assert (u1 - u0) / FPP_FT > 2800 and (v1 - v0) / FPP_FT > 2100
    layout = registration_mark_segments(ref, FPP_FT)
    assert layout["state"] == "value" and len(layout["segments"]) == 12
    assert ticks_outside_loop(layout, LOOP_3939979) == []


def test_the_largest_rectangle_in_an_L_respects_the_notch():
    loop = [[0, 0], [10, 0], [10, 4], [6, 4], [6, 8], [0, 8]]
    rect = largest_axis_aligned_rectangle(loop)
    u0, v0, u1, v1 = rect
    # Both candidates, 6 x 8 = 48 and 10 x 4 = 40: the larger one, and no
    # corner in the notch.
    assert (round(u1 - u0), round(v1 - v0)) == (6, 8)
    assert not point_in_loop([[6, 4], [10, 4], [10, 8], [6, 8]], (u0 + u1) / 2, (v0 + v1) / 2)


def test_no_rectangle_fits_a_degenerate_loop():
    assert largest_axis_aligned_rectangle([[0, 0], [1, 0]]) is None
    ref, record = mark_reference_from_loop([[0, 0], [1, 0]], [0, 0, 1, 0], 0.01)
    assert ref is None and record["reference"] == "none"


@pytest.mark.parametrize("theta", [0.0, 4.0, 10.5, 12.91, 21.5])
def test_at_every_measured_angle_the_inscribed_ticks_render(theta):
    """The angles the 06-21 run's images and loops showed."""
    loop = rotated_loop(CROP_UV, 1192.906, 997.101, theta) if theta else BOX_LOOP
    ref, _record = mark_reference_from_loop(loop, CROP_UV, TOL_FT)
    layout = registration_mark_segments(ref, FPP_FT)
    assert layout["state"] == "value"
    assert ticks_outside_loop(layout, loop) == []


# --- Codex, PR #236: concave loops and large loops -------------------------------------

def test_a_tick_spanning_the_arms_of_a_u_shaped_loop_is_outside():
    """Both endpoints inside, the boundary between them: Revit clips the
    middle. The endpoint test alone called it inside."""
    u_loop = [(0, 0), (30, 0), (30, 20), (20, 20), (20, 5), (10, 5), (10, 20), (0, 20)]
    layout = {"state": "value", "segments": [
        {"key": "spans_the_gap", "uv0": (5, 10), "uv1": (25, 10)},
        {"key": "in_left_arm", "uv0": (2, 10), "uv1": (8, 10)},
        {"key": "along_the_base", "uv0": (5, 2), "uv1": (25, 2)},
    ]}
    assert registration.ticks_outside_loop(layout, u_loop) == ["spans_the_gap"]
    assert registration.segment_crosses_loop(u_loop, (5, 10), (25, 10))
    assert not registration.segment_crosses_loop(u_loop, (5, 2), (25, 2))
    # Touching an edge at its endpoint is not a crossing.
    assert not registration.segment_crosses_loop(u_loop, (5, 2), (10, 5))


def test_a_many_vertex_loop_is_searched_in_bounded_time():
    import math
    import time
    loop = [(50 + 40 * math.cos(2 * math.pi * i / 100), 40 + 30 * math.sin(2 * math.pi * i / 100))
            for i in range(100)]
    t0 = time.time()
    rect = registration.largest_axis_aligned_rectangle(loop)
    elapsed = time.time() - t0
    assert rect is not None and elapsed < 2.0, elapsed
    u0, v0, u1, v1 = rect
    assert 10 < u0 < u1 < 90 and 10 < v0 < v1 < 70
    for u, v in ((u0, v0), (u1, v0), (u1, v1), (u0, v1)):
        assert registration.point_in_loop(loop, u, v)
    # Control: a small loop still offers its own vertex coordinates.
    assert len(loop) > registration.LOOP_VERTEX_CANDIDATE_MAX
