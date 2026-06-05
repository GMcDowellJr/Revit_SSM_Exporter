from vop_interwoven.core.math_utils import Bounds2D
from vop_interwoven.core.raster import ViewRaster, decompose_to_rects


def _cells_from_rect(rect, ncols):
    i_min, j_min, i_max, j_max = rect
    return {
        j * ncols + i
        for j in range(j_min, j_max + 1)
        for i in range(i_min, i_max + 1)
    }


def test_decompose_to_rects_covers_l_shape_without_overlap():
    nrows = 20
    ncols = 20

    # L-shape: a 10x20 vertical arm plus a 20x5 horizontal arm.
    cells = {
        j * ncols + i
        for j in range(20)
        for i in range(10)
    }
    cells.update(
        j * ncols + i
        for j in range(5)
        for i in range(20)
    )

    rects = decompose_to_rects(cells, nrows=nrows, ncols=ncols)

    assert 2 <= len(rects) <= 3
    assert rects == sorted(
        rects,
        key=lambda r: (r[2] - r[0] + 1) * (r[3] - r[1] + 1),
        reverse=True,
    )

    seen = set()
    for rect in rects:
        rect_cells = _cells_from_rect(rect, ncols)
        assert seen.isdisjoint(rect_cells)
        seen.update(rect_cells)

    assert seen == cells


def test_rasterize_silhouette_loops_populates_out_cells():
    bounds = Bounds2D(0.0, 0.0, 8.0, 8.0)
    raster = ViewRaster(width=8, height=8, cell_size=1.0, bounds=bounds, tile_size=4)
    loop = {
        "points": [(2.0, 2.0), (5.0, 2.0), (5.0, 5.0), (2.0, 5.0), (2.0, 2.0)],
        "is_hole": False,
    }

    out_cells = set()
    filled = raster.rasterize_silhouette_loops(
        [loop], key_index=0, depth=1.0, source="HOST", _out_cells=out_cells
    )

    expected = {
        j * raster.W + i
        for j in range(3, 6)
        for i in range(2, 6)
    }
    assert out_cells == expected
    assert filled == len(expected)
