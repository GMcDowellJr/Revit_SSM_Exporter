"""W3 (A2, A4, A5): kinds, the class map v1 and the derived class arrays.

Composed with what they must agree with, never with a copy of it (CLAUDE.md
defect class 1):
  * the map's model classes ARE final_state_scanner's
    ``_default_model_class_resolver`` on every category tried;
  * the legacy channel shape IS ``scan_final_state_totals`` on a real
    ``ViewRaster`` carrying the same per-cell flags;
  * every derived class field, summed over a family's classes, IS the grid's
    dense channel in every cell (read from both FILES).
"""
import json

import numpy as np
import pytest

from tests.test_stage_a_grid_element_cell import (BLACK_BOX_ID, DWG_RECT, LINK_RECT, WALL,
                                                  _cells, _rich_view)
from tools import stage_a_grid as grid
from tools import stage_a_kinds as kinds
from vop_interwoven.metrics import final_state_scanner as fss


@pytest.fixture(scope="module")
def cmap_info():
    return kinds.load_class_map()


@pytest.fixture(scope="module")
def rich(tmp_path_factory):
    folder = tmp_path_factory.mktemp("kinds_rich")
    rec, _arr = _rich_view(folder)
    assert rec["status"] == "value", rec.get("reason")
    return folder / "out"


def _key(**kw):
    base = {"layer": "annotation", "source": None, "category": None, "element_class": None,
            "element_id": 1, "link_category": None}
    base.update(kw)
    return base


# --- the class map -------------------------------------------------------------------

@pytest.mark.parametrize("category", [
    "Walls", "Curtain Wall Mullions", "Doors", "Stairs", "Stair Runs", "Columns",
    "Structural Columns", "Lighting Fixtures", "Lighting Devices", "Floors", "Generic Models",
    "site.dwg", "Railings", "walls", "Wall Lights", "Door Columns", "", None])
def test_model_classes_are_the_geometry_paths_resolver(cmap_info, category):
    cmap = cmap_info[0]
    got, _rule = kinds.classify(_key(layer="model", category=category), cmap, "model_class")
    assert got == fss._default_model_class_resolver({"category": category})


def test_the_maps_class_lists_are_the_geometry_paths(cmap_info):
    cmap = cmap_info[0]
    assert tuple(cmap["model_class"]["classes"]) == fss.DEFAULT_MODEL_CLASSES
    v1 = [c for c in cmap["legacy_bucket"]["classes"]
          if c not in cmap["legacy_bucket"]["not_in_v1"]]
    assert tuple(v1) == fss.ANNO_BUCKETS


@pytest.mark.parametrize("key, annotation_class, legacy", [
    (_key(element_class="TextNote", category="Text Notes"), "text", "TEXT"),
    (_key(element_class="IndependentTag", category="Door Tags"), "tag", "TAG"),
    (_key(element_class="IndependentTag", category="Furniture Tags"), "tag", "OTHER"),
    (_key(element_class="IndependentTag", category="Keynote Tags"), "keynote_unknown_kind", "TAG"),
    (_key(element_class="RoomTag", category="Room Tags"), "tag", "TAG"),
    (_key(element_class="Dimension", category="Dimensions"), "dimension", "DIM"),
    (_key(element_class="SpotDimension", category="Spot Elevations"), "dimension", "OTHER"),
    # Run 1005_1018: linear dimensions are recorded as the subclass.
    (_key(element_class="LinearDimension", category="Dimensions"), "dimension", "DIM"),
    (_key(element_class="AngularDimension", category="Dimensions"), "dimension", "DIM"),
    (_key(element_class="RadialDimension", category="Dimensions"), "dimension", "DIM"),
    (_key(element_class="DetailLine", category="Lines"), "detail_line", "LINES"),
    (_key(element_class="AnnotationSymbol", category="Generic Annotations"),
     "generic_annotation", "TAG"),
    (_key(element_class="FilledRegion", category="Detail Items"), "filled_region", "REGION"),
    (_key(element_class="FamilyInstance", category="Detail Items"), "detail_component",
     "DETAIL"),
    (_key(element_class="ImportInstance", category="plan.dwg"), "import_view_owned",
     "IMPORT_VIEW_OWNED"),
    (_key(element_class="Grid", category="Grids"), "datum", "OTHER"),
    (_key(element_class="Level", category="Levels"), "datum", "OTHER"),
    (_key(element_class="RevisionCloud", category="Revision Clouds"), "revision_cloud", "OTHER"),
    (_key(element_class="ElevationMarker", category="Elevations"), "view_reference", "OTHER"),
    (_key(element_class="Element", category="Views"), "view_reference", "OTHER"),
    # Not matched: listed as unmapped, never guessed.
    (_key(element_class="FamilyInstance", category="Generic Annotations"), "unmapped", "TAG"),
    (_key(), "unmapped", "OTHER"),
])
def test_annotation_classes_and_legacy_buckets(cmap_info, key, annotation_class, legacy):
    cmap = cmap_info[0]
    assert kinds.classify(key, cmap, "annotation_class")[0] == annotation_class
    assert kinds.classify(key, cmap, "legacy_bucket")[0] == legacy


def test_a_map_that_does_not_hold_together_is_refused(tmp_path):
    good = json.loads(kinds.DEFAULT_CLASS_MAP.read_text())
    for name, mutate in (
            ("default", lambda m: m["model_class"].update(default="NOPE")),
            ("rule class", lambda m: m["annotation_class"]["rules"][0].update({"class": "nope"})),
            ("condition", lambda m: m["legacy_bucket"]["rules"][0]["when"].update(colour=[1])),
            ("family", lambda m: m.pop("legacy_bucket"))):
        bad = json.loads(json.dumps(good))
        mutate(bad)
        path = tmp_path / (name.replace(" ", "_") + ".json")
        path.write_text(json.dumps(bad))
        with pytest.raises(kinds.KindsRefusal):
            kinds.load_class_map(path)
    kinds.load_class_map(kinds.DEFAULT_CLASS_MAP)      # the control


# --- inventory --------------------------------------------------------------------------

def test_inventory_lists_every_kind_with_its_pixels(rich, cmap_info, tmp_path):
    rows, views = kinds.inventory([rich], cmap_info[0])
    record = kinds.write_inventory(rows, views, cmap_info, tmp_path)
    on_disk = json.loads((tmp_path / kinds.INVENTORY_JSON).read_text())
    assert on_disk == json.loads(json.dumps(record, default=str))
    assert on_disk["csv_sha256"] == grid.sha256_file(tmp_path / kinds.INVENTORY_CSV)
    assert on_disk["class_map"]["sha256"] == grid.sha256_file(kinds.DEFAULT_CLASS_MAP)
    import csv
    with open(tmp_path / kinds.INVENTORY_CSV, newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    assert len(csv_rows) == on_disk["kinds"] == len(rows)
    by = dict(((r["layer"], r["source"], r["category"], r["element_class"]), r)
              for r in csv_rows)
    # 324 px per whole cell: the wall is 12 cells, the DWG and link 6 each.
    assert by[("model", "HOST", "Walls", "")]["px"] == str(12 * 324)
    assert by[("model", "HOST", "Walls", "")]["model_class"] == "WALL"
    assert by[("model", "DWG", "site.dwg", "")]["px"] == str(6 * 324)
    assert by[("model", "LINK", "Doors", "")]["model_class"] == "DOOR"
    grid_rec = json.loads((rich / "V_1.grid.json").read_text())
    anno_px = sum(int(r["px"]) for r in csv_rows if r["layer"] == "annotation")
    assert anno_px == grid_rec["image_totals"]["anno_element"]
    # The element absent from the bbox map is a kind of nulls, unmapped.
    assert by[("annotation", "", "", "")]["annotation_class"] == "unmapped"
    assert on_disk["unmapped_kinds"]["count"] == 1
    assert on_disk["keys_with_null_category"]["annotation"]["keys"] == 1


def test_a_record_without_the_accounting_is_not_used(rich, cmap_info, tmp_path):
    old = json.loads((rich / "V_1.grid.json").read_text())
    del old["ec_keys"]
    old["schema"] = "vop.stage_a.analysis_grid.v1"
    (tmp_path / "OLD_2.grid.json").write_text(json.dumps(old))
    rows, views = kinds.inventory([tmp_path], cmap_info[0])
    assert rows == [] and views[0]["used"] is False
    assert "made before A1" in views[0]["reason"]


# --- derive -----------------------------------------------------------------------------

@pytest.fixture(scope="module")
def derived(rich, cmap_info):
    rec = kinds.derive_view(rich / "V_1.grid.json", cmap_info)
    on_disk = json.loads((rich / "V_1.kinds.json").read_text())
    assert on_disk == json.loads(json.dumps(rec, default=str))
    assert on_disk["status"] == "value", on_disk.get("reason")
    arr = dict(np.load(str(rich / "V_1.kinds.npz")))
    g = dict(np.load(str(rich / "V_1.grid.npz")))
    return on_disk, arr, g, json.loads((rich / "V_1.grid.json").read_text())


def _family_sum(arr, family, field, shape):
    total = np.zeros(shape, dtype=np.int64)
    for name, a in arr.items():
        f, _cls, fld = name.split("__") if name.count("__") == 2 else (None, None, None)
        if f == family and fld == field:
            total += a
    return total


def test_every_family_sums_to_the_grids_dense_channels(derived):
    rec, arr, g, grid_rec = derived
    shape = g["model_total"].shape
    expect = {
        ("model_class", "px"): g["model_host"] + g["model_dwg"] + g["model_link"],
        ("model_class", "ink_px"): g["model_host_ink"] + g["model_dwg_ink"] + g["model_link_ink"],
        ("model_class", "model_ink_under_px"): g["model_ink_under_anno"],
    }
    for family in ("annotation_class", "legacy_bucket"):
        expect.update({
            (family, "px"): g["anno_element"], (family, "ink_px"): g["anno_element_ink"],
            (family, "black_px"): g["anno_black_assigned"],
            (family, "occupancy_px"): (g["anno_element_ink"] + g["anno_filled_region"]
                                       + g["anno_black_assigned"]),
            (family, "model_ink_under_px"): g["model_ink_under_anno"]})
    for (family, field), dense in expect.items():
        assert dense.sum() > 0, (family, field)
        assert (_family_sum(arr, family, field, shape) == dense).all(), (family, field)
    assert rec["grid_npz_sha256"] == grid_rec["npz_sha256"]
    assert rec["npz_sha256"] == grid.sha256_file(
        __import__("pathlib").Path(rec["grid_json"]).parent / rec["npz"])
    assert rec["class_map"]["sha256"] == grid.sha256_file(kinds.DEFAULT_CLASS_MAP)
    assert rec["lattice_anchor"] == "view_uv"


def test_class_arrays_land_where_the_fixture_puts_them(derived):
    rec, arr, g, grid_rec = derived
    i0, j0 = grid_rec["grid"]["i_range"][0], grid_rec["grid"]["j_range"][0]

    def cells(name):
        js, is_ = np.nonzero(arr[name])
        return set((int(i) + i0, int(j) + j0) for i, j in zip(is_, js))
    assert cells("model_class__WALL__px") == _cells(WALL)
    assert cells("model_class__DOOR__px") == _cells(LINK_RECT)       # the linked Doors
    assert cells("model_class__OTHER__px") == _cells(DWG_RECT)       # site.dwg
    assert "model_class__STAIR__px" not in arr
    fam = rec["families"]["model_class"]
    assert fam["totals"]["STAIR"]["px"] == 0 and "STAIR" not in fam["classes_with_rows"]
    # The unpainted DetailLine bbox holding black: detail_line by black only.
    assert rec["families"]["annotation_class"]["totals"]["detail_line"]["px"] == 0
    assert rec["families"]["annotation_class"]["totals"]["detail_line"]["black_px"] == \
        grid_rec["black"]["by_element"][str(BLACK_BOX_ID)]


def test_a_tampered_grid_npz_refuses_the_derivation(rich, cmap_info, tmp_path):
    import shutil
    for name in ("V_1.grid.json", "V_1.grid.npz"):
        shutil.copy(str(rich / name), str(tmp_path / name))
    data = dict(np.load(str(tmp_path / "V_1.grid.npz")))
    data["anno_element"] = data["anno_element"] + 1
    np.savez_compressed(str(tmp_path / "V_1.grid.npz"), **data)
    rec = kinds.derive_view(tmp_path / "V_1.grid.json", cmap_info)
    assert rec["status"] == "refused" and "hash" in rec["reason"]
    assert not (tmp_path / "V_1.kinds.npz").exists()
    assert json.loads((tmp_path / "V_1.kinds.json").read_text())["status"] == "refused"


def test_a_class_sum_that_misses_the_grid_is_refused(derived, rich, cmap_info, tmp_path,
                                                     monkeypatch):
    """The derivation checks itself against the grid: lose one row's field
    and it refuses with the numbers."""
    real = kinds._row_fields

    def lossy(rec, arr):
        out = real(rec, arr)
        out["ink_px"] = out["ink_px"].copy()
        out["ink_px"][int(np.argmax(out["ink_px"]))] -= 1
        return out
    monkeypatch.setattr(kinds, "_row_fields", lossy)
    import shutil
    for name in ("V_1.grid.json", "V_1.grid.npz"):
        shutil.copy(str(rich / name), str(tmp_path / name))
    rec = kinds.derive_view(tmp_path / "V_1.grid.json", cmap_info)
    assert rec["status"] == "refused"
    assert "ink_px" in rec["reason"] and "cells differ" in rec["reason"]


# --- the legacy channel shape (A4), composed with the geometry path's scanner ------------

def _raster_like(host, dwg, rvt, anno, via):
    """A real ViewRaster whose final-state cells carry the same flags. ``via``
    says how model presence is written: 'occ' arrays (occ_host / occ_dwg /
    occ_link) or element-meta-keyed EDGE labels, which is how the geometry
    path marks DWG and link geometry it rasterised."""
    from vop_interwoven.core.math_utils import Bounds2D
    from vop_interwoven.core.raster import ViewRaster
    h, w = host.shape
    r = ViewRaster(width=w, height=h, cell_size=1.0, bounds=Bounds2D(0.0, 0.0, w, h),
                   tile_size=2)
    n = w * h
    r.element_meta = [{"source_type": "HOST"}, {"source_type": "DWG"},
                      {"source_type": "LINK"}]
    r.anno_meta = [{"type": "TEXT"}]
    for idx in range(n):
        j, i = divmod(idx, w)
        r.anno_key[idx] = 0 if anno[j, i] else -1
    if via == "occ":
        r.occ_host = [bool(host[divmod(k, w)]) for k in range(n)]
        r.occ_dwg = [bool(dwg[divmod(k, w)]) for k in range(n)]
        r.occ_link = [bool(rvt[divmod(k, w)]) for k in range(n)]
    else:
        # One edge key per cell: the geometry path's last writer. A cell with
        # several sources is written through occ_ for the others.
        r.occ_host, r.occ_dwg, r.occ_link = [False] * n, [False] * n, [False] * n
        for idx in range(n):
            j, i = divmod(idx, w)
            flags = [host[j, i], dwg[j, i], rvt[j, i]]
            first = next((k for k, f in enumerate(flags) if f), None)
            if first is not None:
                r.model_edge_key[idx] = first
            for k, f in enumerate(flags):
                if f and k != first:
                    (r.occ_host, r.occ_dwg, r.occ_link)[k][idx] = True
    return r


@pytest.mark.parametrize("via", ["occ", "edge"])
def test_the_legacy_shape_is_the_geometry_paths_scanner(via):
    rng = np.random.RandomState(7)
    shape = (9, 11)
    host_ink = rng.randint(0, 3, shape) * (rng.rand(*shape) < 0.5)
    dwg_ink = rng.randint(0, 3, shape) * (rng.rand(*shape) < 0.3)
    link_ink = rng.randint(0, 3, shape) * (rng.rand(*shape) < 0.3)
    anno = rng.rand(*shape) < 0.4
    model = (host_ink + dwg_ink + link_ink) >= 1
    occ = np.zeros(shape, dtype=np.uint8)
    occ[model & ~anno] = grid.OCCUPANCY_CODES["model_only"]
    occ[anno & ~model] = grid.OCCUPANCY_CODES["anno_only"]
    occ[model & anno] = grid.OCCUPANCY_CODES["overlap"]
    arrays = {"model_host_ink": host_ink, "model_dwg_ink": dwg_ink,
              "model_link_ink": link_ink, "occupancy": occ,
              "inside_crop_a": np.ones(shape, dtype=bool)}
    _part, ours = kinds.legacy_channel_shape(arrays)
    raster = _raster_like(host_ink >= 1, dwg_ink >= 1, link_ink >= 1, anno, via)
    manifest = json.loads(open("vop_interwoven/metrics/manifest/metrics_manifest.v1.json").read())
    theirs = fss.scan_final_state_totals(raster, manifest, model_presence_mode="any")
    for name in kinds.LEGACY_PARTITION_8 + ("TotalCells", "ExtFinalCells_Any",
                                            "ExtFinalCells_Only", "ExtFinalCells_DWG",
                                            "ExtFinalCells_RVT", "ExtFinalCells_DWG_RVT"):
        assert ours["all_cells"][name] == theirs[name], name
    # VERIFIED, not assumed: with presence mode "any", Ext lies within Model.
    assert theirs["Cells_ExtOnly"] == theirs["Cells_AnnoExt"] == 0
    assert ours["all_cells"]["Cells_ModelExt"] + ours["all_cells"]["Cells_All3"] > 0
    assert all(ours["all_cells"]["invariants"].values())


def test_control_under_presence_mode_occ_ext_is_not_within_model():
    """The structural zero belongs to mode "any": an edge-only DWG cell has no
    interior occupancy, so under "occ" the scanner counts it ExtOnly. This is
    why the docs state the mode, not just the zero."""
    shape = (1, 2)
    raster = _raster_like(np.zeros(shape, bool), np.array([[True, False]]),
                          np.zeros(shape, bool), np.zeros(shape, bool), "edge")
    raster.occ_dwg = [False, False]
    manifest = json.loads(open("vop_interwoven/metrics/manifest/metrics_manifest.v1.json").read())
    assert fss.scan_final_state_totals(raster, manifest,
                                       model_presence_mode="occ")["Cells_ExtOnly"] == 1
    assert fss.scan_final_state_totals(raster, manifest,
                                       model_presence_mode="any")["Cells_ExtOnly"] == 0
