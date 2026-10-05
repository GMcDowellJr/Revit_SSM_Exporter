"""W1 / D-A9 (Greg, 2026-10-05): the cell lattice is anchored to VIEW UV.

``origin_uv = floor(crop_min_uv / cell_ft) * cell_ft`` per axis, so a crop
edit moves crop A inside the lattice instead of renumbering every cell.

Built by composition through the real ``build_spec``/``grid_view`` on the
registration fixture (tests/test_register_stage_a_annotation.py), drawn at
18 px/ft. The SHIFTED fixture draws the same world with the model crop moved
by 7 px (7/18 ft -- a whole pixel, so the drawing is exactly the same pixels
in UV, and not a whole cell, so the old crop-anchored origin renumbers the
cells). Expected values are arithmetic on the fixture's constants, never
copies of tool output. Assertions read the FILES the tool wrote.
"""
import json
import math

import numpy as np
import pytest

from tests import test_register_stage_a_annotation as fixture
from tests.test_stage_a_grid import (BANDS, CELL_IN, _run_meta, _split_view, _view,
                                     _view_with_annotation, _write_pair)
from tools import register_stage_a_annotation as reg
from tools import stage_a_grid as grid

SHIFT_PX = 7
SHIFT_FT = SHIFT_PX / fixture.M         # 0.3889 ft: not a multiple of the 1 ft cell


def _old_rule(crop_min_uv, cell_ft):
    """The pre-W1 origin, crop A's own lower-left corner (G-2 as written)."""
    return [float(crop_min_uv[0]), float(crop_min_uv[1])]


def _shifted_bounds():
    b = fixture.MODEL_BOUNDS
    return (b[0] + SHIFT_FT, b[1] + SHIFT_FT, b[2] + SHIFT_FT, b[3] + SHIFT_FT)


def _shifted_view(tmp_path, monkeypatch, **kw):
    """The fixture with the model capture's crop (and so its image) moved by
    SHIFT_PX: the fixture's drawing helpers read MODEL_BOUNDS at call time."""
    with monkeypatch.context() as m:
        m.setattr(fixture, "MODEL_BOUNDS", _shifted_bounds())
        return _view(tmp_path, **kw)


def _by_uv_cell(arr, spec):
    """{(i, j): count} for the nonzero cells of ``arr``."""
    js, is_ = np.nonzero(arr)
    return dict(((int(i) + spec["i_range"][0], int(j) + spec["j_range"][0]),
                 int(arr[j, i])) for i, j in zip(is_, js))


def _arrays(tmp_path):
    return dict(np.load(str(tmp_path / "out" / "V_1.grid.npz")))


def _is_whole(x):
    return abs(x - round(x)) <= 1e-9 * max(1.0, abs(x))


# --- the origin -------------------------------------------------------------------

@pytest.mark.parametrize("cell_in", [CELL_IN, 0.1, 0.25])
def test_origin_is_a_whole_number_of_cells_from_view_uv(tmp_path, monkeypatch, cell_in):
    for name, shifted in (("base", False), ("shifted", True)):
        folder = tmp_path / name
        if shifted:
            _r, rec, _m = _shifted_view(folder, monkeypatch, cell=cell_in)
        else:
            _r, rec, _m = _view(folder, cell=cell_in)
        spec = rec["grid"]
        cell = cell_in * 96.0 / 12.0
        assert spec["cell_ft"] == pytest.approx(cell)
        bounds = _shifted_bounds() if shifted else fixture.MODEL_BOUNDS
        for k in (0, 1):
            assert _is_whole(spec["origin_uv"][k] / spec["cell_ft"]), (name, k, spec)
            # The largest whole multiple at or below crop A's minimum.
            assert spec["origin_uv"][k] == pytest.approx(
                math.floor(bounds[k] / cell) * cell, abs=1e-12)
            offset = spec["crop_a_offset_cells"][k]
            assert offset == pytest.approx((bounds[k] - spec["origin_uv"][k]) / cell)
            assert 0.0 <= offset < 1.0
        assert spec["lattice_anchor"] == "view_uv"
        assert "floor" in spec["origin_rule"]
        # Crop A's minimum lies in cell 0. Its first pixel CENTRE can lie in
        # cell 1 when the minimum is within half a pixel of the boundary
        # (cell 0.8 ft, shifted: offset 0.986 cells, 0.2 px below it).
        assert spec["crop_a_cells"]["i_range"][0] in (0, 1)
        assert spec["crop_a_cells"]["j_range"][0] in (0, 1)


def test_a_float_artefact_does_not_move_the_origin_a_whole_cell():
    assert grid.lattice_origin((3.0 * (1 - 1e-12), -2.0), 1.0) == [3.0, -2.0]
    assert grid.lattice_origin((2.75, -0.25), 0.5) == [2.5, -0.5]
    # A genuine fraction below a boundary is floored, not snapped.
    assert grid.lattice_origin((2.999, 0.0), 1.0) == [2.0, 0.0]


# --- a crop edit no longer renumbers the cells -------------------------------------

def test_a_shifted_crop_keeps_cell_boundaries_and_element_cells(tmp_path, monkeypatch):
    """Same world, crop moved 7 px: identical cell boundaries in UV, and the
    same element ink in the same (i, j) -- model and annotation alike."""
    _r, base, _m = _view(tmp_path / "base")
    _r, moved, _m = _shifted_view(tmp_path / "moved", monkeypatch)
    sb, sm = base["grid"], moved["grid"]
    assert sb["origin_uv"] == sm["origin_uv"]
    for ij in ((0, 0), (9, 8), (-3, -2), (40, 30)):
        assert grid.cell_to_uv(sb, *ij) == grid.cell_to_uv(sm, *ij)
    ab, am = _arrays(tmp_path / "base"), _arrays(tmp_path / "moved")
    for name in ("model_host", "model_host_ink", "anno_element", "anno_element_ink"):
        assert _by_uv_cell(ab[name], sb), name
        assert _by_uv_cell(ab[name], sb) == _by_uv_cell(am[name], sm), name
    # Where the fixture says: the model element fills u 8..12, v 6..9, so its
    # ink is in cells i 8..11, j 6..8 counted from view UV (0, 0) -- i.e.
    # i = floor(u - origin_u) with origin (-1, -2).
    u0, v0, u1, v1 = fixture.MODEL_ELEMENT[2]
    origin = (math.floor(fixture.MODEL_BOUNDS[0]), math.floor(fixture.MODEL_BOUNDS[1]))
    expect = set((i, j) for i in range(int(u0 - origin[0]), int(u1 - origin[0]))
                 for j in range(int(v0 - origin[1]), int(v1 - origin[1])))
    assert set(_by_uv_cell(am["model_host"], sm)) == expect


def test_control_the_old_crop_anchored_origin_renumbers_the_shifted_crop(
        tmp_path, monkeypatch):
    """The fixture discriminates: under the pre-W1 origin the same shifted
    view puts the element's ink in different cells."""
    _r, base, _m = _view(tmp_path / "base")
    monkeypatch.setattr(grid, "lattice_origin", _old_rule)
    _r, moved, _m = _shifted_view(tmp_path / "moved", monkeypatch)
    assert moved["grid"]["origin_uv"] != base["grid"]["origin_uv"]
    ab, am = _arrays(tmp_path / "base"), _arrays(tmp_path / "moved")
    assert (_by_uv_cell(ab["model_host_ink"], base["grid"])
            != _by_uv_cell(am["model_host_ink"], moved["grid"]))


# --- W1 changes binning, never pixel counts ----------------------------------------

def _views_under(tmp_path, monkeypatch):
    out = {}
    out["base"] = _view(tmp_path / "base")[1]
    out["shifted"] = _shifted_view(tmp_path / "shifted", monkeypatch)[1]
    out["black"] = _view_with_annotation(
        tmp_path / "black", black=[(11.0, 10.5, 12.0, 11.5), (25.0, 5.0, 26.0, 6.0)])[0]
    out["filled"] = _view_with_annotation(
        tmp_path / "filled", extra={80: ((160, 8, 16), (7.0, 5.0, 13.0, 10.0))},
        classes={80: "FilledRegion"})[0]
    out["split"] = _split_view(tmp_path / "split", {"state": "removed", "bands_uv": BANDS})
    return out


def test_image_totals_are_identical_before_and_after_W1(tmp_path, monkeypatch):
    new = _views_under(tmp_path / "new", monkeypatch)
    monkeypatch.setattr(grid, "lattice_origin", _old_rule)
    old = _views_under(tmp_path / "old", monkeypatch)
    differs = 0
    for name in new:
        assert new[name]["status"] == old[name]["status"] == "value", name
        assert new[name]["image_totals"] == old[name]["image_totals"], name
        differs += new[name]["grid"]["origin_uv"] != old[name]["grid"]["origin_uv"]
    # Not vacuous: at least one fixture's origin really moved.
    assert differs >= 1


# --- crop_a_px ----------------------------------------------------------------------

def test_crop_a_px_counts_every_model_pixel_centre_inside_crop_A(tmp_path):
    """The fixture's crop IS the image (18 px/ft over MODEL_BOUNDS), so every
    model pixel centre is inside: 700 x 520."""
    _r, rec, _m = _view(tmp_path)
    arr = _arrays(tmp_path)
    assert int(arr["crop_a_px"].sum()) == fixture.MW * fixture.MH
    assert rec["image_totals"]["crop_a_px"] == fixture.MW * fixture.MH
    assert ((arr["crop_a_px"] > 0) == arr["inside_crop_a"]).all()
    assert (arr["crop_a_px"] <= arr["model_total"]).all()
    cov = rec["crop_a_coverage"]
    assert cov["px"] == fixture.MW * fixture.MH
    assert cov["cells_with_px"] == int(arr["inside_crop_a"].sum())


def test_crop_a_px_on_a_crop_smaller_than_the_image(tmp_path):
    """The recorded crop a sub-rectangle whose edges fall on pixel
    boundaries: columns 60..599 and rows 40..479 of the model image. The tick
    fit places the pixels; the count is arithmetic on those constants."""
    anno_path, model_path, _c = _write_pair(tmp_path)
    side = json.loads(model_path.read_text())
    b = fixture.MODEL_BOUNDS
    side["bounds_xy"] = [b[0] + 60 / fixture.M, b[3] - 480 / fixture.M,
                         b[0] + 600 / fixture.M, b[3] - 40 / fixture.M]
    model_path.write_text(json.dumps(side))
    reg.register(anno_path)
    _run_meta(tmp_path)
    rec = grid.grid_view(model_path, tmp_path / "out")
    on_disk = json.loads((tmp_path / "out" / "V_1.grid.json").read_text())
    assert on_disk["status"] == "value", on_disk.get("reason")
    assert on_disk["grid"]["uv_basis"]["chosen"] == "tick_fit"
    arr = _arrays(tmp_path)
    assert int(arr["crop_a_px"].sum()) == 540 * 440
    # Edge cells are partial: the crop's edges are not on cell boundaries.
    assert on_disk["crop_a_coverage"]["cells_partial"] > 0
    assert rec == on_disk


def test_crop_a_px_of_a_split_view_counts_only_its_bands(tmp_path):
    """Bands u -1..5 and u 20..end: pixel column x has centre
    u = -1 + (x + 0.5)/18, so x 0..107 and x 378..699 are shown."""
    rec = _split_view(tmp_path, {"state": "removed", "bands_uv": BANDS})
    assert rec["status"] == "value", rec.get("reason")
    cols = sum(1 for x in range(fixture.MW)
               if fixture.MODEL_BOUNDS[0] + (x + 0.5) / fixture.M <= 5.0
               or fixture.MODEL_BOUNDS[0] + (x + 0.5) / fixture.M >= 20.0)
    assert cols == 108 + 322
    assert int(_arrays(tmp_path)["crop_a_px"].sum()) == cols * fixture.MH
