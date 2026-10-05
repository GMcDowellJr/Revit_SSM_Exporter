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
    ELEMENTS, MODEL_BOUNDS, MODEL_ELEMENT)
from tests.test_register_stage_a_annotation import _write_pair as _write_bare_pair

CELL_IN = 0.125      # 1/8" at 1:96 is exactly 1 ft
CELL_FT = 1.0


def _write_pair(tmp_path, **kw):
    """The registration fixture, plus the two records every current capture
    writes and the grid requires: a clean capture_integrity on both passes and
    a "value" annotation_bbox_status. (The shared fixture stays in the older
    shape, which the sidecar re-encoder's tests rely on.)"""
    anno_path, model_path, colours = _write_bare_pair(tmp_path, **kw)
    for path in (model_path, anno_path):
        side = json.loads(path.read_text())
        side["capture_integrity"] = {"status": "value", "capture_faults": [],
                                     "rolled_back": True, "restore_failures": 0,
                                     "marks_still_in_project": 0,
                                     "paint_failures": 0}
        if path == anno_path:
            side["annotation_bbox_status"] = {"status": "value"}
        path.write_text(json.dumps(side))
    return anno_path, model_path, colours


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


# --- item 3: occupancy is ink ---------------------------------------------------

def test_a_filled_element_occupies_its_outline_cells_not_its_interior(tmp_path):
    """Greg (2026-10-01): ink as a proxy for work; filled area would count
    building mass. The fixture's model element fills u 8..12, v 6..9: cells
    i 9..12, j 8..10. Its interior cells (10, 9) and (11, 9) hold fill and no
    ink, so they are empty; the ring around them is model."""
    _rec, rec, _m = _view(tmp_path)
    arr = _arrays(tmp_path)
    spec = rec["grid"]
    fill = _cells_with(arr["model_host"], spec)
    ink = _cells_with(arr["model_host_ink"], spec)
    ring = set((i, j) for i in range(9, 13) for j in range(8, 11)) - {(10, 9), (11, 9)}
    assert fill == ring | {(10, 9), (11, 9)}
    assert ink == ring
    model_cells = _cells_with(arr["occupancy"] == grid.OCCUPANCY_CODES["model_only"], spec)
    assert ring <= model_cells and not ({(10, 9), (11, 9)} & model_cells)


def test_the_image_border_is_not_an_edge():
    flat = np.full((6, 8), 7, dtype=np.int64)
    assert not grid.ink_mask(flat).any()
    flat[:, :3] = 9          # a fill touching the left border
    edge = grid.ink_mask(flat)
    assert edge[:, 2].all() and edge[:, 3].all()
    assert not edge[:, 0].any() and not edge[:, 1].any()


def test_occupancy_classes_follow_the_ink_counts():
    z = np.zeros((1, 4), dtype=np.int64)
    arrays = {"model_host_ink": np.array([[0, 2, 0, 1]]), "model_dwg_ink": z,
              "model_link_ink": np.array([[0, 0, 0, 1]]),
              "anno_element_ink": np.array([[0, 0, 3, 5]])}
    assert grid.occupancy(arrays).tolist() == [[0, 1, 2, 3]]
    # Filled pixels alone never occupy a cell: only *_ink is read.
    arrays["model_host"] = np.array([[9, 9, 9, 9]])
    assert grid.occupancy(arrays).tolist() == [[0, 1, 2, 3]]
    no_anno = dict((k, v) for k, v in arrays.items() if not k.startswith("anno"))
    assert grid.occupancy(no_anno).tolist() == [[0, 1, 0, 1]]


def test_the_FILE_carries_occupancy_consistent_with_its_ink_arrays(tmp_path):
    _rec, rec, _m = _view(tmp_path)
    arr = _arrays(tmp_path)
    assert (arr["occupancy"] == grid.occupancy(arr)).all()
    summary = rec["occupancy"]
    cells = rec["grid"]["cells_w"] * rec["grid"]["cells_h"]
    assert sum(summary["all_cells"].values()) == cells
    for name, code in grid.OCCUPANCY_CODES.items():
        assert summary["all_cells"][name] == int((arr["occupancy"] == code).sum())
        assert summary["all_cells"][name] == (summary["inside_crop_a"][name]
                                              + summary["outside_crop_a"][name])
    assert summary["all_cells"]["model_only"] > 0 and summary["all_cells"]["anno_only"] > 0


def test_annotation_ink_over_model_ink_is_overlap(tmp_path):
    """An annotation rectangle whose outline crosses the model element's: the
    shared cells are overlap; the control is that there is none without it."""
    _r, base, _m = _view(tmp_path / "a")
    assert base["occupancy"]["all_cells"]["overlap"] == 0
    _r, rec, _m = _view(tmp_path / "b", extra_anno={
        78: ((140, 8, 16), (8.3, 6.3, 11.7, 8.7))})
    assert rec["occupancy"]["all_cells"]["overlap"] > 0


# --- black pixels, filled regions, and overlap as a measure --------------------

def _bbox_entry(rect, element_class="TextNote"):
    u0, v0, u1, v1 = rect
    entry = {"bbox_uv": {"state": "value",
                         "value": [[u0, v0], [u1, v0], [u1, v1], [u0, v1]]},
             "category": "Generic Annotations", "membership_basis": "owner_view"}
    if element_class is not None:
        entry["element_class"] = element_class
    return entry


def _view_with_annotation(tmp_path, extra=None, classes=None, black=(), record_class=True):
    """The fixture pair, with an annotation_bbox_map (and classes) in the
    annotation sidecar and black pixels painted into the annotation TIFF
    (anno lattice: x = 12u + 30, y = -12v + 330) BEFORE registration."""
    from PIL import Image
    tmp_path.mkdir(parents=True, exist_ok=True)
    elements = dict(ELEMENTS)
    elements.update(extra or {})
    anno_path, model_path, _c = _write_pair(tmp_path, extra_anno=extra)
    side = json.loads(anno_path.read_text())
    side["annotation_bbox_map"] = dict(
        (str(eid), _bbox_entry(rect, (classes or {}).get(eid, "TextNote")
                               if record_class else None))
        for eid, (_colour, rect) in elements.items())
    anno_path.write_text(json.dumps(side))
    tiff = tmp_path / "V_1_anno.tiff"
    img = np.asarray(Image.open(str(tiff)).convert("RGB")).copy()
    for u0, v0, u1, v1 in black:
        img[int(-12 * v1 + 330):int(-12 * v0 + 330), int(12 * u0 + 30):int(12 * u1 + 30)] = 0
    Image.fromarray(img).save(str(tiff), format="TIFF")
    reg.register(anno_path)
    _run_meta(tmp_path)
    rec = grid.grid_view(model_path, tmp_path / "out")
    return rec, dict(np.load(str(tmp_path / "out" / rec["npz"])))


def test_black_pixels_go_to_the_annotation_bbox_that_contains_them(tmp_path):
    """Black text inside element 50's bbox (u 10..14, v 10..12) is assigned
    to 50; a black block in no bbox stays unassigned."""
    rec, arr = _view_with_annotation(
        tmp_path, black=[(11.0, 10.5, 12.0, 11.5), (25.0, 5.0, 26.0, 6.0)])
    black = rec["black"]
    assert black["total"] == black["assigned"] + black["unassigned"]
    assert black["assigned"] > 0 and black["unassigned"] > 0
    assert set(black["by_element"]) == {"50"}
    assert int(arr["anno_black_assigned"].sum()) == black["assigned"]
    # The unassigned block is in no annotation cell's occupancy.
    spec = rec["grid"]
    stray = (int(25.5 - MODEL_BOUNDS[0]), int(5.5 - MODEL_BOUNDS[1]))
    occupied = _cells_with(np.isin(arr["occupancy"], (2, 3)), spec)
    assert stray not in occupied


def test_a_filled_region_occupies_its_area_not_only_its_outline(tmp_path):
    """Greg: a filled region masks the model, and the mask is what counts.
    A 6 x 6 ft region (u 20..26, v 2..8): cells i 21..26, j 4..9. As a
    FilledRegion its interior cells are annotation; as anything else only
    its outline is (the control)."""
    region = {79: ((150, 8, 16), (20.0, 2.0, 26.0, 8.0))}
    interior = set((i, j) for i in range(22, 26) for j in range(5, 9))
    rec, arr = _view_with_annotation(tmp_path / "fr", extra=region,
                                     classes={79: "FilledRegion"})
    anno = _cells_with(np.isin(arr["occupancy"], (2, 3)), rec["grid"])
    assert interior <= anno
    assert rec["filled_region"]["elements"] == 1 and rec["filled_region"]["px"] > 0
    rec2, arr2 = _view_with_annotation(tmp_path / "text", extra=region)
    anno2 = _cells_with(np.isin(arr2["occupancy"], (2, 3)), rec2["grid"])
    assert not (interior & anno2)


def test_model_ink_under_a_filled_region_is_measured_not_binary(tmp_path):
    """A filled region over the model element (u 8..12, v 6..9) masks its
    whole outline: every model ink pixel is under it. The control, with no
    region, has none under annotation."""
    rec0, arr0 = _view_with_annotation(tmp_path / "none")
    assert rec0["model_ink_under_annotation"]["under_annotation_px"] == 0
    mask = {80: ((160, 8, 16), (7.0, 5.0, 13.0, 10.0))}
    rec, arr = _view_with_annotation(tmp_path / "mask", extra=mask,
                                     classes={80: "FilledRegion"})
    over = rec["model_ink_under_annotation"]
    model_ink = int((arr["model_host_ink"] + arr["model_dwg_ink"]
                     + arr["model_link_ink"]).sum())
    assert over["model_ink_px"] == model_ink
    assert over["under_filled_region_px"] == model_ink
    assert over["fraction_under_annotation"] == pytest.approx(1.0)
    per_cell = arr["model_ink_under_filled_region"]
    assert (per_cell <= arr["model_host_ink"] + arr["model_dwg_ink"]
            + arr["model_link_ink"]).all()


def test_without_element_class_filled_regions_are_not_guessed(tmp_path):
    rec, _arr = _view_with_annotation(tmp_path, record_class=False)
    assert rec["filled_region"]["element_class_recorded"] is False
    assert rec["filled_region"]["elements"] == 0 and "reason" in rec["filled_region"]


def test_assign_black_takes_the_smallest_containing_box():
    spec = {"mapping": {"a_u": 1.0, "b_u": 0.0, "a_v": 1.0, "b_v": 0.0}}
    black = np.zeros((20, 20), dtype=bool)
    black[5, 5] = black[15, 15] = black[1, 18] = True
    # (5, 5) lies in boxes 4, 2 and 1, listed in that order: "first match
    # wins" gives it to 4, "last match wins" to 1; only "smallest" gives 2.
    bbox_map = {"4": _bbox_entry((0, 0, 12, 12)), "2": _bbox_entry((3, 3, 8, 8)),
                "1": _bbox_entry((0, 0, 20, 20)), "3": _bbox_entry((0, 0, 2, 2))}
    mask, rec = grid.assign_black(black, bbox_map, excluded_ids=[3], spec=spec,
                                  canvas_origin=(0, 0), pad=0.0)
    assert rec["by_element"] == {"2": 1, "1": 2}
    assert rec["ambiguous"] == 1          # (5, 5) is in boxes 1 and 2
    assert rec["unassigned"] == 0 and mask.sum() == 3


# --- review, PR #223 ------------------------------------------------------------

def test_a_relative_run_directory_resolves_the_recorded_paths(tmp_path, monkeypatch):
    """Codex P1: registration run from the parent folder records
    "run/color_id_buffer/V_1_anno.registered.tiff"; joining that to the
    record's folder doubled it and the grid raised FileNotFoundError."""
    run = tmp_path / "run" / "color_id_buffer"
    run.mkdir(parents=True)
    _write_pair(run)
    _run_meta(run.parent)
    monkeypatch.chdir(tmp_path)
    from pathlib import Path
    reg.register(Path("run/color_id_buffer/V_1_anno.json"))
    recorded = json.loads(Path("run/color_id_buffer/V_1_anno.registered.json")
                          .read_text())["registered_tiff"]
    assert not Path(recorded).is_absolute()
    rec = grid.grid_view(Path("run/color_id_buffer/V_1.json"), Path("run/analysis_grid"))
    assert rec["status"] == "value"
    assert "anno_element" in np.load("run/analysis_grid/" + rec["npz"])


def test_both_mappings_are_scored_against_the_measured_ticks(tmp_path):
    """Codex P2: the crop mapping's residual is its distance to the ticks the
    fit MEASURED, not its disagreement with the fitted mapping. Composed with
    the fitter: its own residual_max_px is the same quantity for its mapping."""
    from tools import registration_marks as rm
    from PIL import Image
    _anno, model_path, _c = _write_pair(tmp_path)
    side = json.loads(model_path.read_text())
    side["bounds_xy"] = [v + 0.7 / 18.0 for v in side["bounds_xy"]]
    model_path.write_text(json.dumps(side))
    # Move ONE horizontal tick down a pixel, so the measured ticks no longer
    # sit on the fitted line: "distance to the fit" and "distance to the
    # measured ticks" then differ, and only the second is right.
    tiff = tmp_path / "V_1.tiff"
    img = np.asarray(Image.open(str(tiff)).convert("RGB")).copy()
    first = rm.fit_recorded_marks(
        img, dsc.legacy_view(side)["registration_marks"],
        reserved_colours=rm.palette_colours(side))
    tick = [t for t in first["ticks"] if t["orientation"] == "horizontal"][0]
    x0, y0, x1, y1 = tick["pixel_bbox"]
    block = img[y0:y1 + 1, x0:x1 + 1].copy()
    img[y0:y1 + 1, x0:x1 + 1] = 255
    img[y0 + 1:y1 + 2, x0:x1 + 1] = block
    Image.fromarray(img).save(str(tiff), format="TIFF")
    _run_meta(tmp_path)
    rec = grid.grid_view(model_path, tmp_path / "out")
    basis = rec["grid"]["uv_basis"]
    pixels = rm.load_rgb(tmp_path / "V_1.tiff")
    payload = dsc.legacy_view(json.loads(model_path.read_text()))["registration_marks"]
    fit = rm.fit_recorded_marks(pixels, payload, reserved_colours=rm.palette_colours(side))
    measured = [(t["orientation"], float(t["level_uv"]), float(t["centre_px"]))
                for t in fit["ticks"]]

    def worst(m):
        """Distance to the measured centre lines: a horizontal tick pins a
        row (v), a vertical one a column (u)."""
        def predicted(orientation, level):
            if orientation == "horizontal":
                return m["a_v"] * level + m["b_v"]
            return m["a_u"] * level + m["b_u"]
        return max(abs(predicted(o, lvl) - c) for o, lvl, c in measured)
    assert basis["tick_fit"]["residual_px"] == pytest.approx(
        max(fit["residual_max_px"].values()), abs=1e-9)
    assert basis["nominal_crop"]["residual_px"] == pytest.approx(
        worst(basis["nominal_crop"]["mapping"]), abs=1e-9)
    chosen = basis["chosen"]
    other = "tick_fit" if chosen == "nominal_crop" else "nominal_crop"
    assert basis[chosen]["residual_px"] <= basis[other]["residual_px"]
    assert basis["uncertainty_px"] == basis[chosen]["residual_px"]


def test_crop_A_cells_come_from_the_crop_not_the_padded_image(tmp_path, monkeypatch):
    """Codex P2: an aspect-clamped capture pads the image outside crop A, so
    the image's end pixels are not inside the crop. Composed with production's
    own mapping, shifted by the pad a clamp would add; the control is the same
    view unpadded, where crop and image agree."""
    _r, control, _m = _view(tmp_path / "control")
    expected = control["grid"]["crop_a_cells"]
    pad = 40.0                       # > 2 cells of 18 px each side
    _write_pair(tmp_path)
    sidecar = json.loads((tmp_path / "V_1.json").read_text())
    rgb = dsc._load_rgb_array(tmp_path / "V_1.tiff")
    real = grid.choose_mapping

    def padded(sidecar, _padded_rgb, crop_uv):
        # Production's mapping of the unpadded capture, moved by the pad.
        mapping, basis = real(sidecar, rgb, crop_uv)
        mapping = dict(mapping, b_u=mapping["b_u"] + pad,
                       b_v=mapping["b_v"] + pad)
        return mapping, basis
    monkeypatch.setattr(grid, "choose_mapping", padded)
    big = np.full((rgb.shape[0] + 80, rgb.shape[1] + 80, 3), 255, dtype=np.uint8)
    spec = grid.build_spec(sidecar, big, {"cell_size_paper_in": CELL_IN})
    image_i = grid.cells_of_columns(spec, [0, big.shape[1] - 1])
    assert image_i.min() < expected["i_range"][0]       # the pad reaches outside
    assert spec["crop_a_cells"] == expected


def test_a_stale_registration_is_refused_not_consumed(tmp_path):
    """Codex P1: a view recaptured without re-registering must not pair the
    new capture with the old lattice. Each recorded source hash is checked;
    the control is the same view untouched."""
    _r, control, _m = _view(tmp_path / "control")
    assert control["status"] == "value"
    for victim in ("V_1.json", "V_1.tiff", "V_1_anno.json", "V_1_anno.tiff"):
        folder = tmp_path / victim.replace(".", "_")
        folder.mkdir()
        anno_path, model_path, _c = _write_pair(folder)
        reg.register(anno_path)
        _run_meta(folder)
        target = folder / victim
        if victim.endswith(".json"):
            side = json.loads(target.read_text())
            side["recaptured"] = True
            target.write_text(json.dumps(side))
        else:
            from PIL import Image
            img = np.asarray(Image.open(str(target)).convert("RGB")).copy()
            img[0, 0] = (1, 2, 3)
            Image.fromarray(img).save(str(target), format="TIFF")
        rec = grid.grid_view(model_path, folder / "out")
        on_disk = json.loads((folder / "out" / "V_1.grid.json").read_text())
        assert on_disk["status"] == "refused", victim
        assert "stale" in on_disk["reason"], (victim, on_disk["reason"])
        assert rec == on_disk


def _faulted_view(tmp_path, model_faults=(), anno_faults=(), drop_integrity=False,
                  bbox_status=None):
    """The fixture pair with faults written into the sidecars BEFORE
    registration (so the registration's source hashes still verify)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    anno_path, model_path, _c = _write_pair(tmp_path)
    for path, faults in ((model_path, model_faults), (anno_path, anno_faults)):
        side = json.loads(path.read_text())
        side["capture_integrity"]["capture_faults"] = [
            {"fault": f, "detail": "test"} for f in faults]
        if drop_integrity and path == anno_path:
            del side["capture_integrity"]
        if bbox_status is not None and path == anno_path:
            side["annotation_bbox_status"] = bbox_status
        path.write_text(json.dumps(side))
    reg.register(anno_path)
    _run_meta(tmp_path)
    grid.grid_view(model_path, tmp_path / "out")
    return json.loads((tmp_path / "out" / "V_1.grid.json").read_text())


def test_a_capture_fault_that_invalidates_pixels_refuses_the_view(tmp_path):
    """Codex P1: registration can succeed on a capture whose pixels are not
    trustworthy. Each pass's own faults are read through capture_integrity();
    a restore-only fault is recorded and does not refuse (the control)."""
    clean = _faulted_view(tmp_path / "clean")
    assert clean["status"] == "value"
    assert clean["capture_faults"] == {"model": [], "annotation": []}
    restore = _faulted_view(tmp_path / "restore", anno_faults=["view_membership_changed"])
    assert restore["status"] == "value"
    assert restore["capture_faults"]["annotation"] == ["view_membership_changed"]
    for name, kw in (("anno", {"anno_faults": ["annotation_collection_failed"]}),
                     ("model", {"model_faults": ["view_specific_import_not_suppressed"]}),
                     ("unknown", {"anno_faults": ["a_fault_added_later"]}),
                     ("absent", {"drop_integrity": True})):
        on_disk = _faulted_view(tmp_path / name, **kw)
        assert on_disk["status"] == "refused", name
        assert "capture" in on_disk["reason"], (name, on_disk["reason"])


def test_an_unavailable_bbox_map_refuses_rather_than_reads_as_empty(tmp_path):
    """Codex P2: the producer writes an empty map with status "unavailable"
    when bbox collection fails; that is not a view with no boxes."""
    on_disk = _faulted_view(tmp_path, bbox_status={"status": "unavailable",
                                                   "reason": "RuntimeError: x"})
    assert on_disk["status"] == "refused"
    assert "bbox map is unavailable" in on_disk["reason"]
    assert "RuntimeError: x" in on_disk["reason"]


def test_bboxes_are_placed_with_the_registrations_mapping(tmp_path, monkeypatch):
    """Codex P2: the registered annotation pixels sit where the registration's
    model mapping put them. When the grid chooses another mapping for model
    pixels (here one 20 px off in v), black text must still
    land in its own box. Control: the unshifted run assigns the same pixels."""
    control, _a = _view_with_annotation(tmp_path / "control",
                                        black=[(11.0, 10.5, 12.0, 11.5)])
    real = grid.choose_mapping

    def shifted(sidecar, rgb, crop_uv):
        mapping, basis = real(sidecar, rgb, crop_uv)
        # 20 px in v: more than the black text's 0.5 ft (9 px) margin to its
        # box edges plus the 1 px pad, so a box placed by THIS mapping misses.
        return dict(mapping, b_v=mapping["b_v"] + 20.0), basis
    monkeypatch.setattr(grid, "choose_mapping", shifted)
    rec, _a = _view_with_annotation(tmp_path / "shifted",
                                    black=[(11.0, 10.5, 12.0, 11.5)])
    assert control["black"]["assigned"] == control["black"]["total"] > 0
    assert rec["black"] == control["black"]


# --- split crops (probe Q7): only the bands the view shows --------------------
#
# The capture takes a split crop un-split and records the bands it shows; the
# grid keeps only those. With bands u -1..5 and u 20..end, the model element
# (u 8..12) is captured but not in the view. Mutations: ignoring the bands ->
# the split test red; gridding a split whose bands are missing -> the refusal
# tests red.

BANDS = [[MODEL_BOUNDS[0], MODEL_BOUNDS[1], 5.0, MODEL_BOUNDS[3]],
         [20.0, MODEL_BOUNDS[1], MODEL_BOUNDS[2], MODEL_BOUNDS[3]]]


def _split_view(tmp_path, split_crop):
    tmp_path.mkdir(parents=True, exist_ok=True)
    anno_path, model_path, _c = _write_pair(tmp_path)
    side = json.loads(model_path.read_text())
    side["registration_marks"]["split_crop"] = split_crop
    model_path.write_text(json.dumps(side))
    reg.register(anno_path)
    _run_meta(tmp_path)
    grid.grid_view(model_path, tmp_path / "out")
    return json.loads((tmp_path / "out" / "V_1.grid.json").read_text())


def _shown_cells(tmp_path, spec):
    return _cells_with(_arrays(tmp_path)["inside_crop_a"], spec)


def test_a_split_view_shows_only_its_bands_in_the_FILE(tmp_path):
    rec = _split_view(tmp_path, {"state": "removed", "bands_uv": BANDS})
    assert rec["status"] == "value", rec.get("reason")
    spec = rec["grid"]
    assert "split_crop" in spec["flags"] and spec["split_bands_uv"] == BANDS
    shown = _shown_cells(tmp_path, spec)
    # Cell i holds u in [i - 1, i); its centre is i - 0.5 (origin u = -1).
    assert all(c - 0.5 <= 5.0 or c - 0.5 >= 20.0 for c, _j in shown)
    assert any(c - 0.5 < 5.0 for c, _j in shown) and any(c - 0.5 > 20.0 for c, _j in shown)
    # The model element lies in the hidden middle: occupied, but not shown.
    occ = rec["occupancy"]
    assert occ["inside_crop_a"]["model_only"] + occ["inside_crop_a"]["overlap"] == 0
    assert occ["outside_crop_a"]["model_only"] + occ["outside_crop_a"]["overlap"] > 0


def test_control_an_unsplit_view_shows_all_of_crop_A(tmp_path):
    rec = _split_view(tmp_path, {"state": "not_split"})
    spec = rec["grid"]
    assert "split_crop" not in spec["flags"] and spec["split_bands_uv"] is None
    occ = rec["occupancy"]
    assert occ["inside_crop_a"]["model_only"] + occ["inside_crop_a"]["overlap"] > 0


@pytest.mark.parametrize("split_crop", [
    {"state": "removed", "bands_uv": None, "bands_reason": "a vertical split"},
    # As production writes a failed removal: the bands were computed BEFORE
    # it, so they are present -- only the state says the view was not
    # captured un-split.
    {"state": "unavailable", "reason": "the split could not be removed",
     "bands_uv": BANDS}])
def test_a_split_without_usable_bands_is_refused_not_gridded_whole(tmp_path, split_crop):
    rec = _split_view(tmp_path, split_crop)
    assert rec["status"] == "refused" and "split" in rec["reason"]
