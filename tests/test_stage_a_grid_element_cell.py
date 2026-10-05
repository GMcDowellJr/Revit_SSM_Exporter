"""W2 / A1: the element x cell accounting in the grid npz (``ec_*``).

Composed through the real ``grid_view`` on the registration fixture
(tests/test_register_stage_a_annotation.py: model at 18 px/ft over
MODEL_BOUNDS, so with 1 ft cells from origin (-1, -2) cell i holds
u in [i - 1, i) and every rectangle below is whole cells). The fixture adds,
before registration:

  * model: the host wall 7 (u 8..12, v 6..9), a DWG element 8 (u 25..28,
    v 15..17) and a linked category's colour (u 30..33, v 20..22);
  * annotation: element 78 crossing the wall's right outline (u 11.5..13,
    v 7..8); element 52 painted but absent from the bbox map; element 61 in
    the bbox map but never painted, whose box holds a black block over the
    wall's left outline (u 7.5..8.5, v 5.5..9.5).

Expected values are arithmetic on those constants or independent counts of
the FILES (a colour counted in the registered TIFF), never tool output.
"""
import json

import numpy as np
import pytest
from PIL import Image

from tests import test_register_stage_a_annotation as fixture
from tests.test_stage_a_grid import _bbox_entry, _run_meta, _view, _write_pair
from tools import register_stage_a_annotation as reg
from tools import stage_a_grid as grid

M = fixture.M
B = fixture.MODEL_BOUNDS
WALL = (8.0, 6.0, 12.0, 9.0)
DWG_ID, DWG_RGB, DWG_RECT = 8, (9, 9, 200), (25.0, 15.0, 28.0, 17.0)
LINK_RGB, LINK_RECT = (6, 246, 42), (30.0, 20.0, 33.0, 22.0)
CROSS = {78: ((140, 8, 16), (11.5, 7.0, 13.0, 8.0))}
NO_BBOX = {52: ((90, 8, 16), (3.0, 20.0, 5.0, 22.0))}
BLACK_BOX_ID, BLACK_BOX = 61, (7.0, 5.0, 9.0, 10.0)
BLACK = (7.5, 5.5, 8.5, 9.5)


def _merge(*maps):
    out = {}
    for m in maps:
        out.update(m)
    return out


def _model_px(rect):
    u0, v0, u1, v1 = rect
    return (int(round(fixture.my(v1))), int(round(fixture.my(v0))),
            int(round(fixture.mx(u0))), int(round(fixture.mx(u1))))


def _cells(rect):
    """Whole-cell rectangle -> its cells (origin (-1, -2), 1 ft cells)."""
    u0, v0, u1, v1 = rect
    return set((i, j) for i in range(int(u0 - B[0]), int(u1 - B[0]))
               for j in range(int(v0 - B[1]), int(v1 - B[1])))


def _rich_view(tmp_path, link=True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    anno_path, model_path, _c = _write_pair(tmp_path, extra_anno=_merge(CROSS, NO_BBOX))
    # Model: a DWG element and a linked category, painted before registration.
    tiff = tmp_path / "V_1.tiff"
    img = np.asarray(Image.open(str(tiff)).convert("RGB")).copy()
    y0, y1, x0, x1 = _model_px(DWG_RECT)
    img[y0:y1, x0:x1] = DWG_RGB
    if link:
        y0, y1, x0, x1 = _model_px(LINK_RECT)
        img[y0:y1, x0:x1] = LINK_RGB
    Image.fromarray(img).save(str(tiff), format="TIFF")
    side = json.loads(model_path.read_text())
    side["color_assignment_map"][str(DWG_ID)] = list(DWG_RGB)
    side["near_face_w_map"] = {"host": {
        "7": {"source": "HOST", "category": "Walls"},
        str(DWG_ID): {"source": "DWG", "category": "site.dwg"}}}
    if link:
        side["link_category_color_map"] = {"Doors": list(LINK_RGB)}
    model_path.write_text(json.dumps(side))
    # Annotation: a bbox map without 52, with the unpainted 61; black over
    # the wall's left outline, inside 61's box only.
    side = json.loads(anno_path.read_text())
    bbox = dict((str(e), _bbox_entry(r, "TextNote"))
                for e, (_c, r) in _merge(fixture.ELEMENTS, CROSS).items())
    bbox[str(BLACK_BOX_ID)] = _bbox_entry(BLACK_BOX, "DetailLine")
    side["annotation_bbox_map"] = bbox
    anno_path.write_text(json.dumps(side))
    atiff = tmp_path / "V_1_anno.tiff"
    img = np.asarray(Image.open(str(atiff)).convert("RGB")).copy()
    u0, v0, u1, v1 = BLACK
    img[int(round(fixture.ay(v1))):int(round(fixture.ay(v0))),
        int(round(fixture.ax(u0))):int(round(fixture.ax(u1)))] = 0
    Image.fromarray(img).save(str(atiff), format="TIFF")
    reg.register(anno_path)
    _run_meta(tmp_path)
    returned = grid.grid_view(model_path, tmp_path / "out")
    rec = json.loads((tmp_path / "out" / "V_1.grid.json").read_text())
    assert rec == json.loads(json.dumps(returned))
    arr = dict(np.load(str(tmp_path / "out" / "V_1.grid.npz"))) \
        if rec["status"] == "value" else None
    return rec, arr


@pytest.fixture(scope="module")
def rich(tmp_path_factory):
    return _rich_view(tmp_path_factory.mktemp("rich"))


def _key(rec, **match):
    hits = [n for n, k in enumerate(rec["ec_keys"])
            if all(k.get(f) == v for f, v in match.items())]
    assert len(hits) == 1, (match, rec["ec_keys"])
    return hits[0]


def _rows(arr, key):
    return arr["ec_key"] == key


def _by_cell(arr, key, field):
    sel = _rows(arr, key)
    return dict(((int(i), int(j)), int(v)) for i, j, v in
                zip(arr["ec_i"][sel], arr["ec_j"][sel], arr[field][sel]) if v)


def _dense(rec, arr, rows, field):
    g = rec["grid"]
    out = np.zeros((g["cells_h"], g["cells_w"]), dtype=np.int64)
    np.add.at(out, (arr["ec_j"][rows] - g["j_range"][0], arr["ec_i"][rows] - g["i_range"][0]),
              arr[field][rows])
    return out


# --- the keys ---------------------------------------------------------------------

def test_the_keys_name_what_the_capture_recorded(rich):
    rec, _arr = rich
    assert rec["status"] == "value", rec.get("reason")
    keys = rec["ec_keys"]
    assert {"layer": "model", "element_id": 7, "link_category": None, "source": "HOST",
            "category": "Walls", "element_class": None} in keys
    assert {"layer": "model", "element_id": DWG_ID, "link_category": None,
            "source": "DWG", "category": "site.dwg", "element_class": None} in keys
    assert {"layer": "model", "element_id": None, "link_category": "Doors",
            "source": "LINK", "category": "Doors", "element_class": None} in keys
    anno = dict((k["element_id"], k) for k in keys if k["layer"] == "annotation")
    assert set(anno) == {50, 51, 78, 52, BLACK_BOX_ID}
    assert anno[78]["element_class"] == "TextNote"
    # In the colour map but not the bbox map: kept, with nulls, and counted.
    assert anno[52]["category"] is None and anno[52]["element_class"] is None
    assert rec["element_cell"]["keys_absent_from_bbox_map"] == 1
    # In the bbox map, never painted, but holding black: a key of its own.
    assert anno[BLACK_BOX_ID]["element_class"] == "DetailLine"
    assert rec["element_cell"]["keys_black_only"] == 1
    # The ticks are not keys.
    assert not any(k["element_id"] and k["element_id"] >= 9000 for k in keys)


# --- counts where the fixture puts them ---------------------------------------------

def test_model_keys_land_in_their_cells_with_their_pixels(rich):
    rec, arr = rich
    wall = _by_cell(arr, _key(rec, element_id=7), "ec_px")
    assert wall == dict((c, 18 * 18) for c in _cells(WALL))
    dwg = _by_cell(arr, _key(rec, element_id=DWG_ID), "ec_px")
    assert dwg == dict((c, 18 * 18) for c in _cells(DWG_RECT))
    link = _by_cell(arr, _key(rec, link_category="Doors"), "ec_px")
    assert link == dict((c, 18 * 18) for c in _cells(LINK_RECT))
    # Ink is the outline ring: a 72 x 54 px rectangle has 2*72 + 2*54 - 4.
    assert sum(_by_cell(arr, _key(rec, element_id=7), "ec_ink_px").values()) == 248
    assert not _by_cell(arr, _key(rec, element_id=7), "ec_black_px")


def test_annotation_keys_count_the_registered_pixels(rich, tmp_path_factory):
    rec, arr = rich
    reg_tiff = next((tmp_path_factory.getbasetemp()).glob("rich*/V_1_anno.registered.tiff"))
    pixels = np.asarray(Image.open(str(reg_tiff)).convert("RGB"))
    for eid, (colour, _rect) in _merge(fixture.ELEMENTS, CROSS, NO_BBOX).items():
        expect = int(np.all(pixels == np.asarray(colour, dtype=np.uint8), axis=2).sum())
        got = int(arr["ec_px"][_rows(arr, _key(rec, layer="annotation", element_id=eid))].sum())
        assert got == expect > 0, eid
    black = _rows(arr, _key(rec, element_id=BLACK_BOX_ID))
    assert int(arr["ec_px"][black].sum()) == 0
    assert int(arr["ec_black_px"][black].sum()) == rec["black"]["by_element"][str(BLACK_BOX_ID)] > 0


# --- the invariants, read from the FILE ---------------------------------------------

def test_layer_sums_equal_the_dense_channels_per_cell(rich):
    rec, arr = rich
    layer = np.array([k["layer"] for k in rec["ec_keys"]])[arr["ec_key"]]
    source = np.array([str(k["source"]) for k in rec["ec_keys"]])[arr["ec_key"]]
    model, anno = layer == "model", layer == "annotation"
    link, dwg = model & (source == "LINK"), model & (source == "DWG")
    elem = model & ~link
    pairs = [
        (elem, "ec_px", arr["model_host"] + arr["model_dwg"]),
        (dwg, "ec_px", arr["model_dwg"]),
        (link, "ec_px", arr["model_link"]),
        (elem, "ec_ink_px", arr["model_host_ink"] + arr["model_dwg_ink"]),
        (link, "ec_ink_px", arr["model_link_ink"]),
        (model, "ec_model_ink_under_px", arr["model_ink_under_anno"]),
        (anno, "ec_px", arr["anno_element"]),
        (anno, "ec_ink_px", arr["anno_element_ink"]),
        (anno, "ec_black_px", arr["anno_black_assigned"]),
        (anno, "ec_model_ink_under_px", arr["model_ink_under_anno"]),
    ]
    for rows, field, dense in pairs:
        assert dense.sum() > 0, field          # not vacuous on this fixture
        assert (_dense(rec, arr, rows, field) == dense).all(), field
    t = rec["image_totals"]
    assert int(arr["ec_px"][anno].sum()) == t["anno_element"]
    assert int(arr["ec_ink_px"][anno].sum()) == t["anno_element_ink"]
    assert int(arr["ec_black_px"][anno].sum()) == t["anno_black_assigned"]
    assert int(arr["ec_px"][elem].sum()) == t["model_host"] + t["model_dwg"]
    assert int(arr["ec_px"][link].sum()) == t["model_link"]
    assert int(arr["ec_black_px"][model].sum()) == 0
    # Every row holds something; no (key, cell) appears twice.
    fields = np.stack([arr[f] for f in grid.EC_FIELDS])
    assert (fields.sum(axis=0) > 0).all()
    pairs_seen = set(zip(arr["ec_key"].tolist(), arr["ec_i"].tolist(), arr["ec_j"].tolist()))
    assert len(pairs_seen) == rec["ec_rows"] == len(arr["ec_key"])


def test_model_ink_under_annotation_is_exactly_equal_from_both_layers(rich):
    """One owner per covered pixel (an element pixel, or a black pixel's
    assigned element), so the annotation side is EQUAL to the dense count,
    not merely at least it. The black block over the wall's outline makes the
    black share nonzero, so an owner rule that forgot black would differ."""
    rec, arr = rich
    layer = np.array([k["layer"] for k in rec["ec_keys"]])[arr["ec_key"]]
    total = rec["image_totals"]["model_ink_under_anno"]
    assert total > 0
    assert int(arr["ec_model_ink_under_px"][layer == "annotation"].sum()) == total
    assert int(arr["ec_model_ink_under_px"][layer == "model"].sum()) == total
    by_black = _rows(arr, _key(rec, element_id=BLACK_BOX_ID))
    by_cross = _rows(arr, _key(rec, element_id=78))
    assert int(arr["ec_model_ink_under_px"][by_black].sum()) > 0
    assert int(arr["ec_model_ink_under_px"][by_cross].sum()) > 0
    checks = dict((c["check"], c) for c in rec["element_cell"]["invariants"])
    assert checks["annotation model ink under == model_ink_under_anno"]["ec_total"] == total


def test_the_record_carries_size_and_the_npz_hash(rich):
    rec, arr = rich
    assert rec["ec_rows"] == len(arr["ec_key"]) > 0
    assert rec["npz_bytes"] > 0
    for f in ("ec_key", "ec_i", "ec_j") + grid.EC_FIELDS:
        assert arr[f].dtype == np.int32, f


# --- refusals -------------------------------------------------------------------------

def test_an_accounting_that_misses_a_pixel_refuses_the_view(tmp_path, monkeypatch):
    """The tool checks its own rows against the dense channels before writing:
    drop one pixel from one row and the view is refused with the numbers.
    The control is the same fixture unmutated."""
    _r, control, _m = _view(tmp_path / "control")
    assert control["status"] == "value"
    real = grid.element_cell_accounting

    def lossy(spec, model, anno=None):
        ec, keys, info = real(spec, model, anno)
        ec["ec_px"][0] -= 1
        return ec, keys, info
    monkeypatch.setattr(grid, "element_cell_accounting", lossy)
    _r, rec, _m = _view(tmp_path / "lossy")
    assert rec["status"] == "refused"
    assert "element x cell accounting" in rec["reason"]
    assert "cells differ" in rec["reason"]
    assert not (tmp_path / "lossy" / "out" / "V_1.grid.npz").exists()


def test_linked_categories_sharing_a_colour_are_refused():
    side = {"link_category_color_map": {"Doors": [1, 2, 3], "Walls": [1, 2, 3]}}
    with pytest.raises(grid.GridRefusal, match="share the colour"):
        grid.model_keys(side, {})


def test_a_model_element_with_no_host_entry_gets_null_source_and_category():
    side = {"near_face_w_map": {"host": {"5": {"source": {"state": "unavailable"},
                                               "category": "Walls"}}}}
    keys, ids, _c = grid.model_keys(side, {"5": [1, 1, 1], "6": [2, 2, 2]})
    assert ids.tolist() == [5, 6]
    assert keys[0]["source"] is None and keys[0]["category"] == "Walls"
    assert keys[1]["source"] is None and keys[1]["category"] is None


# --- assign_black's ids -----------------------------------------------------------------

def test_assign_black_ids_match_its_record_and_leave_mask_and_record_unchanged():
    spec = {"mapping": {"a_u": 1.0, "b_u": 0.0, "a_v": 1.0, "b_v": 0.0}}
    black = np.zeros((20, 20), dtype=bool)
    black[5, 5] = black[15, 15] = black[1, 18] = black[19, 0] = True
    bbox_map = {"4": _bbox_entry((0, 0, 12, 12)), "2": _bbox_entry((3, 3, 8, 8)),
                "1": _bbox_entry((0, 0, 19, 19))}
    mask, record = grid.assign_black(black, bbox_map, [], spec, (0, 0), pad=0.0)
    mask2, record2, ids = grid.assign_black(black, bbox_map, [], spec, (0, 0), pad=0.0,
                                            return_ids=True)
    assert (mask == mask2).all() and record == record2
    assert ids[5, 5] == 2 and ids[15, 15] == 1 and ids[1, 18] == 1
    assert ids[19, 0] == 0 and not mask[19, 0]       # (0.5, 19.5) is in no box
    got = dict((str(i), int((ids == i).sum())) for i in np.unique(ids[ids > 0]))
    assert got == record["by_element"]
    assert ((ids > 0) == mask).all()
