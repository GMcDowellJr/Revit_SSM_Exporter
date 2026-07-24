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
    NEAR_BLACK_RESERVED_THRESHOLD,
    NEAR_WHITE_RESERVED_THRESHOLD,
)


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


if __name__ == "__main__":
    unittest.main()
