"""
Unit tests for VOP interwoven Stage A color-ID buffer palette generation.

Covers choose_step()/build_palette() only -- the rest of color_id_buffer.py
calls into the Revit API and is exercised via the Dynamo probe scripts
instead. These two functions are pure Python and must stay in sync with
each other: choose_step()'s capacity estimate has to match exactly what
build_palette() actually excludes, or a step choice can silently produce a
palette shorter than the requested element_count.
"""

import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from vop_interwoven.color_id_buffer import (
    choose_step,
    build_palette,
    compute_model_crop,
    NEAR_BLACK_RESERVED_THRESHOLD,
    NEAR_WHITE_RESERVED_THRESHOLD,
)
from vop_interwoven.core.math_utils import Bounds2D


def _is_near_black(rgb):
    return all(c < NEAR_BLACK_RESERVED_THRESHOLD for c in rgb)


def _is_near_white(rgb):
    return all(c >= NEAR_WHITE_RESERVED_THRESHOLD for c in rgb)


class TestPaletteReservations(unittest.TestCase):
    """build_palette() must never hand out a color near either reserved corner."""

    def test_no_near_black_colors(self):
        palette = build_palette(6489, step=8)
        offenders = [c for c in palette if _is_near_black(c)]
        self.assertEqual(offenders, [], "palette must not contain near-black colors")

    def test_no_near_white_colors(self):
        palette = build_palette(6489, step=8)
        offenders = [c for c in palette if _is_near_white(c)]
        self.assertEqual(offenders, [], "palette must not contain near-white colors")

    def test_first_colors_are_not_near_black(self):
        """Regression guard for the graphics_semantics_probe finding: the
        lattice used to start at (0,0,8), one RGB unit from the (0,0,0)
        background sentinel."""
        palette = build_palette(10, step=8)
        for rgb in palette:
            self.assertFalse(_is_near_black(rgb), "{0} is too close to the background sentinel".format(rgb))

    def test_pure_black_and_white_excluded(self):
        palette = build_palette(30000, step=8)
        self.assertNotIn((0, 0, 0), palette)
        self.assertNotIn((255, 255, 255), palette)


class TestPaletteCapacityConsistency(unittest.TestCase):
    """choose_step()'s capacity estimate must match what build_palette() can deliver."""

    def test_typical_model_size_still_uses_step_8(self):
        # Regression guard: the new reservation must not change behavior for
        # realistic model sizes (existing production views run ~6500 elements).
        self.assertEqual(choose_step(6489), 8)

    def test_build_palette_delivers_requested_count_at_chosen_step(self):
        for n in (1, 10, 500, 6489, 20000, 32000):
            step = choose_step(n)
            palette = build_palette(n, step=step)
            self.assertEqual(
                len(palette), n,
                "choose_step({0}) picked step={1} but build_palette only produced {2} colors".format(
                    n, step, len(palette)
                ),
            )

    def test_palette_colors_are_unique(self):
        palette = build_palette(6489, step=8)
        self.assertEqual(len(palette), len(set(palette)))

    def test_deterministic_ordering(self):
        self.assertEqual(build_palette(500, step=8), build_palette(500, step=8))


class TestComputeModelCrop(unittest.TestCase):
    """compute_model_crop() -- the crop-forcing logic's rectangle math,
    factored out as pure Python (no Revit API) so it's directly unit-
    testable, unlike the rest of export_color_id_buffer_view()."""

    def test_no_model_clip_bounds_falls_back_to_bounds_xy_unchanged(self):
        bounds_xy = Bounds2D(0.0, 0.0, 40.0, 30.0)
        render_bounds, offset = compute_model_crop(None, bounds_xy)
        self.assertIs(render_bounds, bounds_xy)
        self.assertEqual(offset, (0.0, 0.0, 0.0, 0.0))

    def test_strict_subset_model_clip_bounds_narrows_and_offsets_correctly(self):
        # anno-expanded bounds_xy vs. the narrower pre-expansion model crop
        # (view_basis.py's model_bounds_uv/model_clip_bounds) -- the normal,
        # non-cap_triggered case.
        bounds_xy = Bounds2D(0.0, 0.0, 40.0, 30.0)
        model_clip = Bounds2D(5.0, 2.0, 35.0, 28.0)
        render_bounds, offset = compute_model_crop(model_clip, bounds_xy)
        self.assertEqual((render_bounds.xmin, render_bounds.ymin, render_bounds.xmax, render_bounds.ymax),
                          (5.0, 2.0, 35.0, 28.0))
        self.assertEqual(offset, (5.0, 2.0, -5.0, -2.0))
        # Reconstruction identity: bounds_xy + offset == render_bounds, on
        # every corner -- the exact contract compute_model_crop()'s
        # docstring, and the sidecar's model_crop_offset_uv field, promise.
        self.assertEqual(bounds_xy.xmin + offset[0], render_bounds.xmin)
        self.assertEqual(bounds_xy.ymin + offset[1], render_bounds.ymin)
        self.assertEqual(bounds_xy.xmax + offset[2], render_bounds.xmax)
        self.assertEqual(bounds_xy.ymax + offset[3], render_bounds.ymax)

    def test_min_corner_offset_is_never_negative_in_the_normal_case(self):
        bounds_xy = Bounds2D(-10.0, -10.0, 50.0, 40.0)
        model_clip = Bounds2D(0.0, 0.0, 40.0, 30.0)
        _render_bounds, offset = compute_model_crop(model_clip, bounds_xy)
        dxmin, dymin, dxmax, dymax = offset
        self.assertGreaterEqual(dxmin, 0.0)
        self.assertGreaterEqual(dymin, 0.0)
        self.assertLessEqual(dxmax, 0.0)
        self.assertLessEqual(dymax, 0.0)

    def test_cap_triggered_style_bounds_xy_shrunk_below_model_clip_is_clamped(self):
        # Simulates view_basis.py's post-annotation cap-envelope
        # (view_basis.py:1038-1068), which can shrink bounds_xy back down
        # below model_clip_bounds on one or more sides, breaking the normal
        # "model_clip_bounds is a strict subset of bounds_xy" guarantee.
        # model_clip_bounds here extends past bounds_xy on the xmax side.
        bounds_xy = Bounds2D(0.0, 0.0, 20.0, 30.0)
        model_clip = Bounds2D(5.0, 2.0, 35.0, 28.0)  # xmax=35 > bounds_xy.xmax=20
        render_bounds, offset = compute_model_crop(model_clip, bounds_xy)
        # The render crop must never extend past bounds_xy despite
        # model_clip_bounds's raw xmax being wider.
        self.assertLessEqual(render_bounds.xmax, bounds_xy.xmax)
        self.assertGreaterEqual(render_bounds.xmin, bounds_xy.xmin)
        self.assertLessEqual(render_bounds.ymax, bounds_xy.ymax)
        self.assertGreaterEqual(render_bounds.ymin, bounds_xy.ymin)
        # The requested "clamp per-side offset to max(0, ...)" (and its
        # mirror, min(0, ...), for the max-corner sides) falls out of the
        # intersection: dxmax would be +15 from raw subtraction (35-20) but
        # must clamp to 0 (bounds_xy.xmax used unchanged, not widened).
        dxmin, dymin, dxmax, dymax = offset
        self.assertEqual(dxmax, 0.0)
        self.assertGreaterEqual(dxmin, 0.0)
        self.assertLessEqual(dymax, 0.0)
        self.assertGreaterEqual(dymin, 0.0)

    def test_non_overlapping_model_clip_bounds_falls_back_to_bounds_xy(self):
        bounds_xy = Bounds2D(0.0, 0.0, 10.0, 10.0)
        model_clip = Bounds2D(50.0, 50.0, 60.0, 60.0)  # disjoint from bounds_xy
        render_bounds, offset = compute_model_crop(model_clip, bounds_xy)
        self.assertEqual(
            (render_bounds.xmin, render_bounds.ymin, render_bounds.xmax, render_bounds.ymax),
            (0.0, 0.0, 10.0, 10.0),
        )
        self.assertEqual(offset, (0.0, 0.0, 0.0, 0.0))

    def test_model_clip_equal_to_bounds_xy_is_a_zero_offset_no_op(self):
        bounds_xy = Bounds2D(1.0, 2.0, 3.0, 4.0)
        model_clip = Bounds2D(1.0, 2.0, 3.0, 4.0)
        render_bounds, offset = compute_model_crop(model_clip, bounds_xy)
        self.assertEqual(
            (render_bounds.xmin, render_bounds.ymin, render_bounds.xmax, render_bounds.ymax),
            (1.0, 2.0, 3.0, 4.0),
        )
        self.assertEqual(offset, (0.0, 0.0, 0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
