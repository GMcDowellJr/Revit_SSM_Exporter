"""W4 (A3): presence and dominant are derivations over stored counts.

Hand-built count arrays, so every expectation is read off the arrays in the
test; then the same functions on a real derived record (composed through
grid_view and derive_view), where the legacy partition must sum to the
annotation-present cell count the grid's own occupancy gives.
"""
import json

import numpy as np
import pytest

from tests.test_stage_a_kinds import derived, rich, cmap_info  # noqa: F401  (fixtures)
from tools import stage_a_grid as grid
from tools import stage_a_kinds as kinds

ORDER = ["TEXT", "TAG", "DIM"]


def _counts(**by_class):
    """{class: {"ink_px": arr, "occupancy_px": arr}} from 1 x N lists."""
    out = {}
    for name in ORDER:
        values = np.array([by_class.get(name, [0] * 5)], dtype=np.int64)
        out[name] = {"ink_px": values, "occupancy_px": values}
    return out


# --- presence -------------------------------------------------------------------------

def test_presence_is_count_at_or_above_min_px():
    counts = _counts(TEXT=[0, 1, 2, 5, 0], TAG=[3, 0, 0, 1, 0])
    p1 = kinds.presence(counts, "ink_px", 1)
    assert p1["TEXT"].tolist() == [[False, True, True, True, False]]
    assert p1["TAG"].tolist() == [[True, False, False, True, False]]
    assert not p1["DIM"].any()
    p3 = kinds.presence(counts, "ink_px", 3)
    assert p3["TEXT"].tolist() == [[False, False, False, True, False]]
    assert p3["TAG"].tolist() == [[True, False, False, False, False]]


def test_presence_refuses_a_zero_threshold():
    with pytest.raises(ValueError):
        kinds.presence(_counts(), "ink_px", 0)


# --- dominant -------------------------------------------------------------------------

def test_dominant_is_the_largest_count_and_minus_one_when_empty():
    counts = _counts(TEXT=[0, 4, 1, 0, 0], TAG=[0, 2, 7, 0, 0], DIM=[0, 0, 0, 9, 0])
    dom = kinds.dominant(counts, "ink_px", ORDER)
    assert dom["index"].tolist() == [[-1, 0, 1, 2, -1]]
    assert dom["ties"] == 0 and not dom["tie_cells"].any()


def test_ties_go_to_the_declared_order_and_are_counted():
    """Cell 1: TEXT = TAG = 3; cell 2: TAG = DIM = 5; cell 3: all 2. The
    earliest in ORDER wins each, whatever order the dict was built in."""
    counts = _counts(TEXT=[0, 3, 0, 2, 1], TAG=[0, 3, 5, 2, 0], DIM=[0, 0, 5, 2, 0])
    shuffled = dict((k, counts[k]) for k in reversed(ORDER))
    for c in (counts, shuffled):
        dom = kinds.dominant(c, "ink_px", ORDER)
        assert dom["index"].tolist() == [[-1, 0, 1, 0, 0]]
        assert dom["ties"] == 3
        assert dom["tie_cells"].tolist() == [[False, True, True, True, False]]
    # A different declared order decides the same ties differently.
    dom = kinds.dominant(counts, "ink_px", ["DIM", "TAG", "TEXT"])
    assert dom["index"].tolist() == [[-1, 1, 0, 0, 2]]     # cell 1: TAG before TEXT


def test_a_cell_where_every_class_is_zero_is_never_a_tie():
    dom = kinds.dominant(_counts(), "ink_px", ORDER)
    assert (dom["index"] == -1).all() and dom["ties"] == 0


# --- the legacy partition -------------------------------------------------------------

def test_the_legacy_partition_puts_each_present_cell_in_one_bucket():
    counts = _counts(TEXT=[0, 3, 0, 2, 1], TAG=[0, 3, 5, 2, 0], DIM=[0, 0, 6, 2, 0])
    present = np.array([[False, True, True, True, True]])
    part = kinds.legacy_anno_partition(counts, present, ORDER)["all_cells"]
    assert part == {"AnnoFinalCells_TEXT": 3, "AnnoFinalCells_TAG": 0,
                    "AnnoFinalCells_DIM": 1, "AnnoPresentFinal": 4, "ties": 2,
                    "anno_types_sum_to_anno_present": True}


def test_a_present_cell_with_no_bucket_is_refused():
    present = np.array([[True, False, False, False, False]])
    with pytest.raises(kinds.KindsRefusal, match="hold no legacy bucket"):
        kinds.legacy_anno_partition(_counts(), present, ORDER)


def test_inside_crop_a_is_a_subset_of_the_partition():
    counts = _counts(TEXT=[1, 1, 0, 0, 0], TAG=[0, 0, 4, 4, 0])
    present = np.array([[True, True, True, True, False]])
    inside = np.array([[True, False, True, False, True]])
    part = kinds.legacy_anno_partition(counts, present, ORDER, inside)
    assert part["inside_crop_a"]["AnnoFinalCells_TEXT"] == 1
    assert part["inside_crop_a"]["AnnoFinalCells_TAG"] == 1
    assert part["inside_crop_a"]["AnnoPresentFinal"] == 2


# --- on a real derived record (composed) --------------------------------------------

def test_the_FILE_partition_sums_to_the_grids_annotation_present_cells(derived):  # noqa: F811
    rec, arr, g, grid_rec = derived
    part = rec["derived"]["legacy_anno_partition"]
    present = grid_rec["occupancy"]["all_cells"]
    assert part["all_cells"]["AnnoPresentFinal"] == present["anno_only"] + present["overlap"] > 0
    buckets = [k for k in part["all_cells"] if k.startswith("AnnoFinalCells_")]
    assert sum(part["all_cells"][k] for k in buckets) == part["all_cells"]["AnnoPresentFinal"]
    inside = grid_rec["occupancy"]["inside_crop_a"]
    assert part["inside_crop_a"]["AnnoPresentFinal"] == inside["anno_only"] + inside["overlap"]
    # Recomputed from the kinds npz with the public functions: same answer.
    shape = g["model_total"].shape
    fam = rec["families"]["legacy_bucket"]
    counts = kinds.family_counts(arr, "legacy_bucket", fam["classes"], fam["fields"], shape)
    again = kinds.legacy_anno_partition(
        counts, np.isin(g["occupancy"], (2, 3)), fam["classes"])["all_cells"]
    assert again == part["all_cells"]


def test_the_FILE_cells_with_ink_are_presence_at_one_px(derived):  # noqa: F811
    rec, arr, g, grid_rec = derived
    shape = g["model_total"].shape
    for family in ("annotation_class", "model_class", "legacy_bucket"):
        fam = rec["families"][family]
        counts = kinds.family_counts(arr, family, fam["classes"], fam["fields"], shape)
        for name, m in kinds.presence(counts, "ink_px", 1).items():
            assert rec["derived"]["cells_with_ink"][family][name]["all_cells"] == int(m.sum())
    multihot = rec["derived"]["model_class_cells_multihot"]
    assert multihot["ModelClassCells_WALL"]["all_cells"] == 10     # 12 cells, 2 interior hold no ink
    assert rec["derived"]["presence_min_px"] == 1
    assert json.loads(json.dumps(rec))  # serialisable as written
    assert grid.OCCUPANCY_MIN_INK_PX == rec["derived"]["presence_min_px"]
