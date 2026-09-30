"""The analysis grid (tools/stage_a_grid.py; docs/DESIGN_ANALYSIS_GRID.md).

Composed with the producers it must agree with, never with a copy of their
arithmetic (CLAUDE.md, defect class 1):
  * the nominal mapping IS the decoder's ``_pixel_corner_to_uv``;
  * the per-cell channel totals ARE the decoder's S1 ``pixel_stats``;
  * the tick-fit mapping reproduces the registration tool's ``canvas_origin_uv``,
    and the nominal-vs-fit disagreement its ``model_marks_vs_lattice``.
The fixture is test_register_stage_a_annotation's model + annotation pair,
drawn from known constants: the model at 18 px/ft over MODEL_BOUNDS, so at
1:96 a 1/8" cell (1 ft) is 18 px and every expected cell index is arithmetic
on those constants. Assertions read the FILES the tool wrote (class 4).
"""
import json
import math

import numpy as np
import pytest

from tools import decode_stage_a_color_id as dsc
from tools import register_stage_a_annotation as reg
from tools import stage_a_grid as grid

from tests.test_register_stage_a_annotation import (
    ELEMENTS, MODEL_BOUNDS, MODEL_ELEMENT, _write_pair)

CELL_IN = 0.125      # 1/8" at 1:96 is exactly 1 ft
CELL_FT = 1.0


def _run_meta(folder, cell=CELL_IN):
    config = {} if cell is None else {"cell_size_paper_in": cell}
    (folder / "run_meta.json").write_text(json.dumps(
        {"run_id": "RUN_T", "config": config}))


def _view(tmp_path, register=True, cell=CELL_IN, **pair):
    tmp_path.mkdir(parents=True, exist_ok=True)
    anno_path, model_path, _c = _write_pair(tmp_path, **pair)
    if register:
        reg.register(anno_path)
    _run_meta(tmp_path, cell)
    record = grid.grid_view(model_path, tmp_path / "out")
    on_disk = json.loads((tmp_path / "out" / "V_1.grid.json").read_text())
    return record, on_disk, model_path


def _arrays(tmp_path):
    return dict(np.load(str(tmp_path / "out" / "V_1.grid.npz")))


def _cells_with(arr, spec):
    """{(i, j)} of cells holding any pixel of ``arr``."""
    js, is_ = np.nonzero(arr)
    return set((int(i) + spec["i_range"][0], int(j) + spec["j_range"][0])
               for i, j in zip(is_, js))


# --- the grid itself --------------------------------------------------------

def test_cells_are_the_paper_cell_from_crop_As_lower_left_in_the_FILE(tmp_path):
    _rec, rec, _m = _view(tmp_path)
    assert rec["status"] == "value"
    spec = rec["grid"]
    assert spec["cell_ft"] == pytest.approx(CELL_FT)
    assert spec["origin_uv"] == pytest.approx([MODEL_BOUNDS[0], MODEL_BOUNDS[1]])
    assert spec["px_per_cell_u"] == pytest.approx(18.0, abs=0.05)
    # The model element spans u 8..12, v 6..9: cells i = 8-(-1) .. 12-(-1)-1,
    # j = 6-(-2) .. 9-(-2)-1 -- arithmetic on the fixture, not the tool.
    u0, v0, u1, v1 = MODEL_ELEMENT[2]
    expect = set((i, j)
                 for i in range(int(u0 - MODEL_BOUNDS[0]), int(u1 - MODEL_BOUNDS[0]))
                 for j in range(int(v0 - MODEL_BOUNDS[1]), int(v1 - MODEL_BOUNDS[1])))
    assert _cells_with(_arrays(tmp_path)["model_host"], spec) == expect


def test_annotation_left_of_and_below_crop_A_has_negative_cells(tmp_path):
    """Greg: the annotation canvas is expected to be larger, so its ink
    beyond crop A's lower-left takes -i, -j. The canvas reaches u = -2.5,
    v = -2.5 (the fixture's annotation lattice); crop A starts at (-1, -2)."""
    colour = (120, 8, 16)
    _rec, rec, _m = _view(tmp_path, extra_anno={
        77: (colour, (-2.3, -2.3, -1.6, -2.1))})
    spec = rec["grid"]
    assert spec["i_range"][0] < 0 and spec["j_range"][0] < 0
    cells = _cells_with(_arrays(tmp_path)["anno_element"], spec)
    assert (-2, -1) in cells
    assert spec["crop_a_cells"]["i_range"][0] == 0
    assert spec["crop_a_cells"]["j_range"][0] == 0
    # Both annotation elements of the fixture, where their UV says.
    for _eid, (_c, (a0, b0, a1, b1)) in ELEMENTS.items():
        i = int(math.floor((a0 + a1) / 2.0 - MODEL_BOUNDS[0]))
        j = int(math.floor((b0 + b1) / 2.0 - MODEL_BOUNDS[1]))
        assert (i, j) in cells


def test_control_without_a_registration_the_grid_is_crop_A_only(tmp_path):
    _rec, rec, _m = _view(tmp_path, register=False)
    spec = rec["grid"]
    assert "no_registered_annotation" in spec["flags"]
    assert spec["i_range"] == spec["crop_a_cells"]["i_range"]
    assert spec["j_range"] == spec["crop_a_cells"]["j_range"]
    assert "anno_element" not in _arrays(tmp_path)


# --- composition with the producers -------------------------------------------

def test_the_nominal_mapping_is_the_decoders(tmp_path):
    anno_path, model_path, _c = _write_pair(tmp_path)
    side = dsc.legacy_view(json.loads(model_path.read_text()))
    w, h = 700, 520
    mapping = grid.nominal_mapping(side["bounds_xy"], w, h)
    spec = {"mapping": mapping}
    for x, y in ((0, 0), (17, 5), (699.5, 519.25), (350, 260)):
        expect = dsc._pixel_corner_to_uv(x, y, side["bounds_xy"], w, h)
        assert grid.pixel_to_uv(spec, x, y) == pytest.approx(expect, abs=1e-9)


def test_channel_totals_are_the_decoders_pixel_stats(tmp_path):
    _rec, rec, model_path = _view(tmp_path)
    doc = json.loads(dsc.decode_one(model_path).read_text())
    ps, t = doc["pixel_stats"], rec["image_totals"]
    assert t["model_host"] + t["model_dwg"] == ps["element_px"]
    assert t["model_tick"] == ps["registration_mark_px"] > 0
    assert t["model_white"] == ps["background_white_px"]
    assert t["model_black"] == ps["black_px"]
    assert t["model_link"] == ps["link_category_px_total"]
    assert t["model_residual"] == ps["off_palette_px"]
    assert t["model_total"] == ps["total_px"] == 700 * 520


def test_the_fit_basis_reproduces_the_registration_tools_frame(tmp_path):
    anno_path, model_path, _c = _write_pair(tmp_path)
    reg.register(anno_path)
    registered = json.loads(reg.output_paths(anno_path)[1].read_text())
    _run_meta(tmp_path)
    rec = grid.grid_view(model_path, tmp_path / "out")
    spec = rec["grid"]
    assert spec["uv_basis"]["tick_fit"]["mapping"] == pytest.approx(
        registered["model_fit"]["mapping"])
    lattice = registered["lattice"]
    fit_spec = {"mapping": registered["model_fit"]["mapping"]}
    assert grid.pixel_to_uv(fit_spec, *lattice["canvas_origin_model_px"]) == \
        pytest.approx(lattice["canvas_origin_uv"], abs=1e-9)
    # The registration tool's lattice scales each axis on its own; the
    # nominal mapping here is the decoder's square-pixel clamp model. On an
    # unclamped capture they differ only by float rounding in the pad.
    assert spec["uv_basis"]["disagreement_at_image_corners_px"] == pytest.approx(
        registered["model_marks_vs_lattice"]["worst_corner_px"], abs=1e-3)


# --- the basis choice ---------------------------------------------------------

def test_the_mapping_closer_to_the_ticks_is_chosen_and_both_recorded(tmp_path):
    _rec, rec, _m = _view(tmp_path)
    basis = rec["grid"]["uv_basis"]
    fit_r = basis["tick_fit"]["residual_px"]
    nom_r = basis["nominal_crop"]["residual_px"]
    assert basis["chosen"] == ("nominal_crop" if nom_r <= fit_r else "tick_fit")
    assert basis["uncertainty_px"] == min(nom_r, fit_r)


def test_a_crop_that_misses_the_ticks_loses_to_the_fit(tmp_path):
    """Shift the RECORDED crop by 2 px' worth; the image and ticks stay put,
    so the crop now misses them and the fit must win."""
    _anno, model_path, _c = _write_pair(tmp_path)
    side = json.loads(model_path.read_text())
    side["bounds_xy"] = [v + 2.0 / 18.0 for v in side["bounds_xy"]]
    model_path.write_text(json.dumps(side))
    _run_meta(tmp_path)
    rec = grid.grid_view(model_path, tmp_path / "out")
    basis = rec["grid"]["uv_basis"]
    assert basis["chosen"] == "tick_fit"
    assert basis["nominal_crop"]["residual_px"] > 1.5


def test_no_ticks_means_the_crop_and_an_unmeasured_uncertainty(tmp_path):
    _rec, rec, _m = _view(tmp_path, register=False, with_marks=False)
    spec = rec["grid"]
    assert spec["uv_basis"]["chosen"] == "nominal_crop"
    assert spec["uv_basis"]["uncertainty_px"] is None
    assert "uncertainty_unmeasured" in spec["flags"]


# --- refusals, each with its control above ------------------------------------

def test_no_cell_size_in_run_meta_is_refused_not_guessed(tmp_path):
    _rec, rec, _m = _view(tmp_path, cell=None)
    assert rec["status"] == "refused" and "cell_size_paper_in" in rec["reason"]
    assert not (tmp_path / "out" / "V_1.grid.npz").exists()


def test_fewer_than_two_px_per_cell_is_refused(tmp_path):
    _rec, rec, _m = _view(tmp_path, cell=0.01)     # 0.08 ft cells = 1.44 px
    assert rec["status"] == "refused" and "px per cell" in rec["reason"]


def test_coarse_is_uncertainty_as_a_fraction_of_the_cell(tmp_path, monkeypatch):
    """Same view, a fixed 1 px mapping uncertainty; only the cell size
    changes. 1 px is 0.06 of an 18 px cell (fine) and 0.44 of a 2.25 px cell
    (1/64" -- coarse): the flag follows cell size against pixel count, not a
    fixed pixel figure."""
    real = grid.choose_mapping

    def _one_px(sidecar, rgb, crop_uv):
        mapping, basis = real(sidecar, rgb, crop_uv)
        return mapping, dict(basis, uncertainty_px=1.0)
    monkeypatch.setattr(grid, "choose_mapping", _one_px)
    _rec, fine, _m = _view(tmp_path / "a")
    _rec, coarse, _m = _view(tmp_path / "b", cell=0.015625)
    assert "coarse" not in fine["grid"]["flags"]
    assert fine["grid"]["uncertainty_cells"] == pytest.approx(1.0 / 18.0, rel=1e-2)
    assert "coarse" in coarse["grid"]["flags"]
    assert coarse["grid"]["uncertainty_cells"] == pytest.approx(1.0 / 2.25, rel=1e-2)


# --- the record ---------------------------------------------------------------

def test_the_record_is_written_last_and_names_the_arrays_it_describes(tmp_path):
    _rec, rec, _m = _view(tmp_path)
    npz = tmp_path / "out" / rec["npz"]
    assert rec["npz_sha256"] == grid.sha256_file(npz)
    arrays = _arrays(tmp_path)
    spec = rec["grid"]
    for name, arr in arrays.items():
        assert arr.shape == (spec["cells_h"], spec["cells_w"]), name
    assert rec["image_totals"]["model_total"] == int(arrays["model_total"].sum())
    assert (tmp_path / "out" / rec["png"]).exists()


def test_link_category_pixels_are_the_link_channel(tmp_path):
    """S1's ~210k link pixels on Plan_RVTLink must be model, not empty: paint a
    linked category's filter colour into the model image and it is counted as
    ``link``, in the cells its UV says, and equals the decoder's figure."""
    from PIL import Image
    anno_path, model_path, _c = _write_pair(tmp_path)
    side = json.loads(model_path.read_text())
    link_rgb = (6, 246, 42)
    side["link_category_color_map"] = {"Walls": list(link_rgb)}
    model_path.write_text(json.dumps(side))
    tiff = tmp_path / "V_1.tiff"
    img = np.asarray(Image.open(str(tiff)).convert("RGB")).copy()
    # u 25..27, v 18..19 on the 18 px/ft lattice over MODEL_BOUNDS.
    x0, x1 = int(18 * (25 - MODEL_BOUNDS[0])), int(18 * (27 - MODEL_BOUNDS[0]))
    y0, y1 = int(-18 * (19 - MODEL_BOUNDS[3])), int(-18 * (18 - MODEL_BOUNDS[3]))
    img[y0:y1, x0:x1] = link_rgb
    Image.fromarray(img).save(str(tiff), format="TIFF")
    reg.register(anno_path)
    _run_meta(tmp_path)
    rec = grid.grid_view(model_path, tmp_path / "out")
    assert rec["image_totals"]["model_link"] == (y1 - y0) * (x1 - x0)
    doc = json.loads(dsc.decode_one(model_path).read_text())
    assert rec["image_totals"]["model_link"] == doc["pixel_stats"]["link_category_px_total"]
    cells = _cells_with(_arrays(tmp_path)["model_link"], rec["grid"])
    assert cells == set((i, j) for i in (26, 27) for j in (20,))
