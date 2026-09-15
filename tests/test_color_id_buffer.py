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
    _reserved_corner_count,
    BACKGROUND_SENTINEL_RGB,
    NEAR_BLACK_RESERVED_THRESHOLD,
    NEAR_WHITE_RESERVED_THRESHOLD,
    UNCOLORABLE_SENTINEL_RGB,
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


def _hue_degrees(rgb):
    """Hue of an RGB triple in degrees, without importing colorsys.

    Kept local and explicit so this assertion does not depend on the same
    conversion helper build_palette() itself uses -- a bug shared by both
    would otherwise cancel out and the hue-spread test would pass on a
    palette that is not actually spread.
    """
    r, g, b = [c / 255.0 for c in rgb]
    hi, lo = max(r, g, b), min(r, g, b)
    span = hi - lo
    if span == 0:
        return None  # achromatic: no hue
    if hi == r:
        hue = 60.0 * (((g - b) / span) % 6.0)
    elif hi == g:
        hue = 60.0 * (((b - r) / span) + 2.0)
    else:
        hue = 60.0 * (((r - g) / span) + 4.0)
    return hue % 360.0


class TestPaletteHueSpread(unittest.TestCase):
    """The hue-primary traversal's reason for existing.

    The previous RGB-nested-loop implementation (r outer, g middle, b inner,
    returning as soon as element_count was reached) meant a real capture only
    ever sampled the low-R/low-G corner: every element got a shade of blue.
    These assert the spread statistically rather than by eye.
    """

    def _occupied_bins(self, palette, bin_count=12):
        bin_width = 360.0 / bin_count
        occupied = set()
        for rgb in palette:
            hue = _hue_degrees(rgb)
            if hue is not None:
                occupied.add(min(bin_count - 1, int(hue / bin_width)))
        return occupied

    def test_typical_view_element_count_spans_the_whole_hue_circle(self):
        # ~6500 elements is a real production view, far below the 32640
        # capacity at step 8 -- exactly the regime the old implementation
        # never escaped the blue corner in.
        occupied = self._occupied_bins(build_palette(6489, step=8))
        self.assertEqual(
            len(occupied), 12,
            "a typical view's palette must reach every 30-degree hue bin; "
            "occupied bins were {0}".format(sorted(occupied)),
        )

    def test_small_view_still_gets_distinct_hues(self):
        # The stride within a band matters here: without it the first ten
        # colors walk a single edge of the RGB cube and are all near-identical
        # reds, which is the same failure as the blue bias at a smaller scale.
        occupied = self._occupied_bins(build_palette(10, step=8))
        self.assertGreaterEqual(
            len(occupied), 8,
            "ten elements must get ten clearly distinct hues; occupied bins "
            "were {0}".format(sorted(occupied)),
        )

    def test_no_hue_bin_dominates_a_mid_size_palette(self):
        palette = build_palette(500, step=8)
        bin_counts = {}
        for rgb in palette:
            hue = _hue_degrees(rgb)
            if hue is not None:
                idx = min(11, int(hue / 30.0))
                bin_counts[idx] = bin_counts.get(idx, 0) + 1
        self.assertEqual(len(bin_counts), 12)
        # Perfectly even would be ~8.3%; the old implementation put ~100% in
        # one bin. Anything under a quarter in a single bin is comfortably
        # "spread" without over-fitting to the exact traversal.
        worst = max(bin_counts.values()) / float(len(palette))
        self.assertLess(worst, 0.25, "hue bin counts: {0}".format(sorted(bin_counts.items())))


class TestPaletteSentinels(unittest.TestCase):
    """Neither sentinel may ever be handed out as an element ID color."""

    def test_sentinels_are_inside_the_near_white_reservation(self):
        for name, rgb in (("uncolorable", UNCOLORABLE_SENTINEL_RGB),
                          ("background", BACKGROUND_SENTINEL_RGB)):
            for channel in rgb:
                self.assertGreaterEqual(
                    channel, NEAR_WHITE_RESERVED_THRESHOLD,
                    "{0} sentinel {1} must sit inside the near-white reservation, "
                    "or build_palette could assign it".format(name, rgb),
                )

    def test_background_sentinel_is_clearly_distinguishable_from_pure_white(self):
        # 240 vs 255 is 15 units per channel -- an order of magnitude above
        # the couple of RGB units of export noise measured with AA and shadows
        # suppressed, so the canvas never reads as a force-whited element.
        for channel, white_channel in zip(BACKGROUND_SENTINEL_RGB, UNCOLORABLE_SENTINEL_RGB):
            self.assertGreaterEqual(abs(white_channel - channel), 8)
        self.assertNotEqual(tuple(BACKGROUND_SENTINEL_RGB), tuple(UNCOLORABLE_SENTINEL_RGB))

    def test_neither_sentinel_is_producible_at_full_capacity(self):
        # Exhausting the palette is the only way to prove "for any
        # element_count up to capacity": a shorter request is a prefix of it.
        step = 8
        levels = (255 // step) + 1
        capacity = (levels ** 3) - _reserved_corner_count(step)
        palette = set(build_palette(capacity, step=step))
        self.assertNotIn(tuple(UNCOLORABLE_SENTINEL_RGB), palette)
        self.assertNotIn(tuple(BACKGROUND_SENTINEL_RGB), palette)


class TestPaletteCapacityUnderHuePrimaryTraversal(unittest.TestCase):
    """choose_step()'s capacity promise must survive the reimplementation.

    build_palette() now generates in HSV, snaps onto the step lattice, and
    only then falls back to a lattice-order completion pass. The claim that
    capacity is unchanged rests on that completion pass reaching every valid
    lattice point, so it is asserted directly here rather than argued.
    """

    def _capacity(self, step):
        levels = (255 // step) + 1
        return (levels ** 3) - _reserved_corner_count(step)

    def test_full_capacity_is_deliverable_at_every_practical_step(self):
        # Steps 3, 2 and 1 are omitted only because exhausting a 2M-plus
        # lattice is slow, not because they differ in kind; choose_step never
        # reaches them below ~261k elements.
        for step in (8, 6, 5, 4):
            capacity = self._capacity(step)
            palette = build_palette(capacity, step=step)
            self.assertEqual(
                len(palette), capacity,
                "step {0} promised {1} colors but build_palette produced "
                "{2}".format(step, capacity, len(palette)),
            )
            self.assertEqual(len(set(palette)), capacity, "step {0} palette has duplicates".format(step))

    def test_choose_step_guarantee_holds_across_lattice_boundaries(self):
        capacity_at_8 = self._capacity(8)
        edge_cases = [
            1, 2, 3,
            capacity_at_8 - 1, capacity_at_8, capacity_at_8 + 1,  # the step 8 -> 6 boundary
            self._capacity(6),
        ]
        for n in edge_cases + [10, 500, 6489, 20000]:
            step = choose_step(n)
            palette = build_palette(n, step=step)
            self.assertEqual(
                len(palette), n,
                "choose_step({0}) picked step={1} but build_palette produced "
                "{2}".format(n, step, len(palette)),
            )
            self.assertEqual(len(set(palette)), n, "duplicate colors for n={0}".format(n))

    def test_every_palette_color_lies_on_the_step_lattice(self):
        # The snap-to-lattice step is what preserves the previous
        # implementation's guarantee that two palette colors differ by at
        # least `step` in some channel -- without it, neighboring HSV samples
        # at low value convert to RGB values a unit or two apart, inside
        # export noise and indistinguishable to a decoder.
        for rgb in build_palette(6489, step=8):
            for channel in rgb:
                self.assertEqual(channel % 8, 0, "{0} is off the step-8 lattice".format(rgb))

    def test_shorter_request_is_a_prefix_of_a_longer_one(self):
        long_palette = build_palette(5000, step=8)
        self.assertEqual(build_palette(500, step=8), long_palette[:500])

    def test_zero_element_count_yields_an_empty_palette(self):
        self.assertEqual(build_palette(0, step=8), [])


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
