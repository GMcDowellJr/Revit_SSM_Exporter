"""tools/colorid_to_occupancy.py: the crop is not the grid.

A Stage A capture records TWO rectangles. `bounds_xy` is the rectangle the
view was actually cropped to and the TIFF spans (color_id_buffer.py:2694).
The shared cell grid that W/H/cell are defined against is raster.bounds_xy,
which can be WIDER when the view was annotation-expanded; the two are related
by model_crop_offset_uv, defined as raster.bounds_xy -> crop
(color_id_buffer.py:2697-2707), so recovering the grid SUBTRACTS it.

Using the crop where the grid is meant shifts every cell index and, in
assumed mode, understates the cell size as well. Both are silent: the arrays
come out the right shape and the numbers are simply wrong.

Synthetic throughout. No Revit, no capture.
"""
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import colorid_to_occupancy as c2o  # noqa: E402


# raster.bounds_xy (the shared grid): 100 x 50 ft at 1.0 ft cells -> 100 x 50.
GRID = (0.0, 0.0, 100.0, 50.0)
# The render crop, narrowed on every side.
CROP = (10.0, 5.0, 90.0, 40.0)
# color_id_buffer's convention: raster.bounds_xy + offset == crop.
OFFSET = [CROP[0] - GRID[0], CROP[1] - GRID[1], CROP[2] - GRID[2], CROP[3] - GRID[3]]


def _sidecar(**over):
    sc = {
        "view_id": 7,
        "resolution": {"requested_axis": "width", "backoff_floor_px": 100},
        "model_crop_offset_uv": OFFSET,
        "bounds_xy": list(CROP),
        "color_assignment_map": {"501": [10, 20, 30]},
    }
    sc.update(over)
    return sc


def test_grid_bounds_subtracts_the_offset():
    assert c2o.grid_bounds_from_capture(_sidecar(), CROP) == pytest.approx(GRID)


def test_grid_bounds_is_the_crop_when_no_narrowing():
    sc = _sidecar(model_crop_offset_uv=[0.0, 0.0, 0.0, 0.0])
    assert c2o.grid_bounds_from_capture(sc, CROP) == pytest.approx(CROP)
    # ...and when the field is absent entirely (older sidecar).
    sc = _sidecar()
    del sc["model_crop_offset_uv"]
    assert c2o.grid_bounds_from_capture(sc, CROP) == pytest.approx(CROP)


def test_assumed_grid_uses_the_grid_extent_not_the_crop():
    """backoff_floor_px counts cells across the FULL grid, so dividing the
    narrowed crop by it understates the cell and skews the other dimension.

    Grid 100x50 ft over 100 cells -> cell 1.0 ft, H 50.
    Crop 80x35 ft over the same 100 -> cell 0.8 ft, H round(35/0.8) = 44.
    """
    grid = c2o.grid_assumed(_sidecar(), GRID)
    assert grid["cell"] == pytest.approx(1.0)
    assert (grid["W"], grid["H"]) == (100, 50)

    # What the pre-fix code computed, for the record.
    crop_u, crop_v = CROP[2] - CROP[0], CROP[3] - CROP[1]
    stale_cell = crop_u / 100
    assert stale_cell == pytest.approx(0.8)
    assert max(1, int(round(crop_v / stale_cell))) == 44


def test_coverage_bins_against_the_grid_origin(tmp_path):
    """A blob painted at the very top-left of the TIFF sits at the CROP's
    origin, which is 10 ft east and 10 ft north of the GRID's origin. With
    1.0 ft cells and the grid's bottom-left origin, it must land at column 10,
    not column 0."""
    fpp = 0.5
    img_w = int(round((CROP[2] - CROP[0]) / fpp))   # 160 px
    img_h = int(round((CROP[3] - CROP[1]) / fpp))   # 70 px
    arr = np.full((img_h, img_w, 3), 255, dtype=np.uint8)
    arr[0:2, 0:2] = (10, 20, 30)                    # top-left 2x2 px
    tiff = tmp_path / "v.tiff"
    Image.fromarray(arr, mode="RGB").save(tiff)

    sc = _sidecar()
    grid = c2o.grid_assumed(sc, GRID)
    covered, _ink, _total = c2o.colorid_cell_coverage(
        tiff, sc, grid, fpp, 0.0, 0.0, CROP, GRID)

    rows, cols = np.nonzero(covered)
    assert cols.tolist() == [10], "column must be measured from the grid origin"
    # v: the crop's top is 40 ft, the grid's bottom is 0 -> row 39.
    assert rows.tolist() == [39]


def test_coverage_origin_is_unchanged_when_the_offset_is_zero(tmp_path):
    """The common case -- every view of byColor 20260917T104744 records a zero
    offset -- must be bit-for-bit what it was before this fix."""
    fpp = 0.5
    crop = (0.0, 0.0, 80.0, 35.0)
    img_w, img_h = int(80 / fpp), int(35 / fpp)
    arr = np.full((img_h, img_w, 3), 255, dtype=np.uint8)
    arr[0:2, 0:2] = (10, 20, 30)
    tiff = tmp_path / "z.tiff"
    Image.fromarray(arr, mode="RGB").save(tiff)

    sc = _sidecar(model_crop_offset_uv=[0.0, 0.0, 0.0, 0.0], bounds_xy=list(crop))
    gb = c2o.grid_bounds_from_capture(sc, crop)
    assert gb == pytest.approx(crop)
    grid = c2o.grid_assumed(sc, gb)
    covered, _ink, _total = c2o.colorid_cell_coverage(
        tiff, sc, grid, fpp, 0.0, 0.0, crop, gb)
    rows, cols = np.nonzero(covered)
    # The blob may straddle two cells (cell is 0.8 ft here, the blob 1.0 ft);
    # what matters is that indexing starts at the shared origin, column 0.
    assert set(cols.tolist()) == {0}
