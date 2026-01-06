# tests/test_early_out_semantics.py

from vop_interwoven.core.math_utils import Bounds2D, CellRect
from vop_interwoven.core.raster import ViewRaster
from vop_interwoven.core.footprint import CellRectFootprint
from vop_interwoven.pipeline import _tiles_fully_covered_and_nearer


def _snapshot_occ(raster):
    return list(raster.w_occ), list(raster.model_mask), list(raster.occ_host)


def test_early_out_is_pure_optimization_no_output_difference():
    bounds = Bounds2D(0.0, 0.0, 8.0, 8.0)
    rect = CellRect(1, 1, 4, 4)
    fp = CellRectFootprint(rect)

    # Mode A: early-out check runs (but does not skip), then stamping
    r1 = ViewRaster(width=8, height=8, cell_size=1.0, bounds=bounds, tile_size=4)
    assert _tiles_fully_covered_and_nearer(r1.tile, fp, elem_min_w=1.0) is False
    for (i, j) in fp.cells():
        r1.try_write_cell(i, j, w_depth=1.0, source="HOST")
    snap_a = _snapshot_occ(r1)

    # Mode B: early-out disabled (direct stamping)
    r2 = ViewRaster(width=8, height=8, cell_size=1.0, bounds=bounds, tile_size=4)
    for (i, j) in fp.cells():
        r2.try_write_cell(i, j, w_depth=1.0, source="HOST")
    snap_b = _snapshot_occ(r2)

    assert snap_a == snap_b
