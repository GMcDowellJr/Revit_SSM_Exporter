"""Ticks move clear of annotation (Greg, 2026-09-30).

pipeline_0930_0739 lost two 32 px ticks in the annotation capture, each drawn
under an annotation element (a tag on ModelCallout, a dimension on
Plan_CropActive). A covered tick now moves to the nearest clear position that
keeps it classifiable by tools/registration_marks.py.

The decisive test COMPOSES the relocation with the real locator: the covering
annotation is painted ON TOP of the ticks, and the fit must still find 12/12
-- with a control showing the same scene without relocation loses them.
"""
import numpy as np
import pytest

from tools import registration_marks as rm
from vop_interwoven import stage_a_registration as reg
from vop_interwoven.stage_a_registration import (
    MARK_COLOUR, registration_mark_segments, relocate_marks_clear_of)

FPP = 1.0 / 12.0                     # 12 px/ft
REF = (2.0, 3.0, 32.0, 24.0)         # 360 x 252 px
W, H = 480, 360


def to_x(u):
    return 12.0 * u + 30.0


def to_y(v):
    return -12.0 * v + 330.0


def _layout():
    layout = registration_mark_segments(REF, FPP)
    assert layout["state"] == "value"
    return layout


def _seg(layout, key):
    return [s for s in layout["segments"] if s["key"] == key][0]


def _cover(seg, grow=0.4):
    (ua, va), (ub, vb) = seg["uv0"], seg["uv1"]
    return [min(ua, ub) - grow, min(va, vb) - grow, max(ua, ub) + grow, max(va, vb) + grow]


def _render(segments, covers, shared):
    """Ticks first, then the covering annotation painted OVER them, as Revit
    drew the tag/dimension over the tick on pipeline_0930_0739."""
    img = np.full((H, W, 3), 255, dtype=np.uint8)
    colours = {}
    for i, seg in enumerate(segments):
        colour = MARK_COLOUR if shared else (200, 8 * (i + 1), 16)
        colours[9000 + i] = colour
        if seg["orientation"] == "horizontal":
            y = int(np.floor(to_y(seg["level_uv"])))
            xs = sorted(to_x(u) for u in seg["span_uv"])
            img[y:y + 2, int(round(xs[0])):int(round(xs[1]))] = colour
        else:
            x = int(np.floor(to_x(seg["level_uv"])))
            ys = sorted(to_y(v) for v in seg["span_uv"])
            img[int(round(ys[0])):int(round(ys[1])), x:x + 2] = colour
    for u0, v0, u1, v1 in covers:
        img[int(round(to_y(v1))):int(round(to_y(v0))),
            int(round(to_x(u0))):int(round(to_x(u1)))] = (40, 120, 60)
    marks = [dict(s, id=9000 + i) for i, s in enumerate(segments)]
    return img, marks, colours


def _fit(segments, covers, shared):
    img, marks, colours = _render(segments, covers, shared)
    if shared:
        return rm.fit_marks_in_pixels(img, marks, shared_colour=MARK_COLOUR)
    return rm.fit_marks_in_pixels(img, marks, colour_by_id=colours)


@pytest.mark.parametrize("shared", [False, True], ids=["annotation", "model"])
def test_covered_ticks_move_and_the_real_locator_still_finds_all_twelve(shared):
    layout = _layout()
    covers = [_cover(_seg(layout, "right_bottom_h")), _cover(_seg(layout, "mid_bottom_v"))]
    avoid = [(101, covers[0]), (102, covers[1])]

    # CONTROL: the same scene without relocation loses both ticks.
    lost = _fit(layout["segments"], covers, shared)
    assert lost.get("found_count", 0) < 12

    moved = relocate_marks_clear_of(layout, avoid)
    assert moved["relocation"] == {"avoid_rects": 2, "moved": 2, "blocked": 0}
    for key, eid in (("right_bottom_h", 101), ("mid_bottom_v", 102)):
        seg = _seg(moved, key)
        assert seg["placement"] == "moved" and seg["was_covered_by"] == [eid]
    fit = _fit(moved["segments"], covers, shared)
    assert fit["status"] == "value", fit.get("reason")
    assert fit["found_count"] == 12, fit.get("missing")
    assert max(fit["residual_max_px"].values()) <= 2.0


def test_moved_ticks_stay_in_their_windows_and_apart():
    layout = _layout()
    avoid = [(i, _cover(s)) for i, s in enumerate(layout["segments"][:6])]
    moved = relocate_marks_clear_of(layout, avoid)
    inset = layout["inset_px"] * FPP
    for seg in moved["segments"]:
        if seg["placement"] != "moved":
            continue
        (a_lo, a_hi), (c_lo, c_hi) = reg._tick_window(seg, REF, inset)
        assert a_lo - 1e-9 <= seg["span_uv"][0] and seg["span_uv"][1] <= a_hi + 1e-9
        assert c_lo - 1e-9 <= seg["level_uv"] <= c_hi + 1e-9
        assert not any(reg._rects_overlap(reg._segment_rect(seg, 2 * FPP), r)
                       for _i, r in avoid)
    segs = moved["segments"]
    gap = layout["gap_px"] * FPP
    for i, a in enumerate(segs):
        for b in segs[i + 1:]:
            assert not reg._rects_overlap(reg._segment_rect(a, gap / 2.0),
                                          reg._segment_rect(b, gap / 2.0)), (a["key"], b["key"])


def test_a_tick_with_no_clear_position_is_left_and_named():
    layout = _layout()
    everything = [(7, [REF[0] - 1, REF[1] - 1, REF[2] + 1, REF[3] + 1])]
    out = relocate_marks_clear_of(layout, everything)
    assert out["relocation"]["blocked"] == 12 and out["relocation"]["moved"] == 0
    for before, after in zip(layout["segments"], out["segments"]):
        assert after["placement"] == "blocked" and after["covered_by"] == [7]
        assert after["uv0"] == before["uv0"]


def test_no_avoid_rects_changes_nothing():
    layout = _layout()
    assert relocate_marks_clear_of(layout, []) is layout
