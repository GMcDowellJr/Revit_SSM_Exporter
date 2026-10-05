"""The reserved tick colour (Greg, 2026-10-02).

Both captures paint every registration tick MARK_COLOUR, and neither palette
hands out a colour whose anti-aliased fringe toward white the tick locator
could take for a tick's. Run 1001-1950 refused four views on look-alikes of
the ticks' own palette colours.

The exclusion lives in production (stage_a_registration.fringe_reads_as_mark);
the acceptance test lives in tools/registration_marks.py (blend_coverage).
Two copies of one rule are CLAUDE.md defect class 1, so the test COMPOSES
them: every colour production lets through has every fringe run through the
TOOL's own test, rounded to whole levels as an export writes them.
"""
import numpy as np
import pytest

from tools import registration_marks as rm
from vop_interwoven.color_id_buffer import build_palette, stage_a_palette
from vop_interwoven.stage_a_registration import MARK_COLOUR, fringe_reads_as_mark

STEP = 8


def _lattice(step=STEP):
    vals = np.arange(0, 256, step)
    r, g, b = np.meshgrid(vals, vals, vals, indexing="ij")
    return np.stack([r.ravel(), g.ravel(), b.ravel()], axis=1)


def _fringe_keys(colours, samples=128):
    """Every rounded fringe toward white of each colour, as packed keys:
    ``(keys [n_colours, samples])``."""
    t = np.arange(1, samples + 1, dtype=float) / samples
    depth = 255.0 - colours.astype(float)
    pix = np.rint(255.0 - t[None, :, None] * depth[:, None, :]).astype(np.int64)
    return (pix[..., 0] << 16) | (pix[..., 1] << 8) | pix[..., 2]


def test_every_colour_let_through_has_no_fringe_the_locator_takes_for_a_tick():
    lattice = _lattice()
    allowed = np.array([not fringe_reads_as_mark(tuple(int(v) for v in c))
                        for c in lattice])
    keys = _fringe_keys(lattice[allowed])
    accepted, _cover = rm.blend_coverage(np.unique(keys), MARK_COLOUR)
    hit = np.isin(keys, accepted).any(axis=1)
    offenders = lattice[allowed][hit]
    assert len(offenders) == 0, offenders[:10].tolist()


def test_the_exclusion_is_not_vacuous():
    """Control: without it, the same composed check finds look-alikes -- the
    colour of review PR #219's link category among them."""
    lattice = _lattice()
    keys = _fringe_keys(lattice)
    accepted, _cover = rm.blend_coverage(np.unique(keys), MARK_COLOUR)
    assert np.isin(keys, accepted).any(axis=1).sum() > 100
    assert fringe_reads_as_mark(MARK_COLOUR)
    assert fringe_reads_as_mark((138, 252, 12))
    assert not fringe_reads_as_mark((0, 0, 255))


def test_stage_a_palette_without_the_reservation_is_build_palette():
    palette, step, record = stage_a_palette(500, STEP)
    assert palette == build_palette(500, step=STEP) and step == STEP
    assert record["reserved_tick_colour"] is None


def test_stage_a_palette_with_it_withholds_and_says_so():
    palette, step, record = stage_a_palette(500, STEP, reserve_tick_colour=True)
    assert len(palette) == 500 and step == STEP
    assert not any(fringe_reads_as_mark(c) for c in palette)
    assert record["reserved_tick_colour"] == list(MARK_COLOUR)
    assert record["excluded_count"] > 0


def test_a_palette_the_exclusion_leaves_short_takes_a_finer_step():
    """Step 8 has 32768 lattice points less the reserved corners; with the
    look-alikes withheld it cannot hold 32000. The step is lowered and the
    record says so, rather than the palette coming back short."""
    palette, step, record = stage_a_palette(32000, STEP, reserve_tick_colour=True)
    assert len(palette) == 32000
    assert step < STEP and record["step_requested"] == STEP and record["step"] == step


# --- the reader accepts both shapes ------------------------------------------

def _marks(rgbs):
    return [{"key": "k{0}".format(i), "id": 100 + i, "rgb": list(c)}
            for i, c in enumerate(rgbs)]


def test_an_annotation_record_without_colour_mode_is_the_older_per_tick_shape():
    payload = {"pass": "annotation", "marks": _marks([(10, 20, 30), (40, 50, 60)])}
    by_id, shared, refusal = rm.mark_colours(payload)
    assert refusal is None and shared is None
    assert by_id == {100: (10, 20, 30), 101: (40, 50, 60)}


def test_a_shared_annotation_record_is_located_by_the_one_colour():
    payload = {"pass": "annotation", "colour_mode": "shared",
               "marks": _marks([MARK_COLOUR, MARK_COLOUR])}
    assert rm.mark_colours(payload) == (None, tuple(MARK_COLOUR), None)


def test_a_shared_record_naming_two_colours_is_refused():
    payload = {"pass": "annotation", "colour_mode": "shared",
               "marks": _marks([MARK_COLOUR, (1, 2, 3)])}
    _by, _shared, refusal = rm.mark_colours(payload)
    assert refusal and "annotation" in refusal
