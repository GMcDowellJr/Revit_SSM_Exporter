"""The roll-up's 1.1.0 columns (A8): class totals, the legacy shapes, crop A.

Same harness as tests/test_stage_a_grid_rollup.py: every view made by the
REAL producers (registration, grid_view, then stage_a_kinds.derive_view), and
every assertion reads the CSV and summary the tool WROTE. Each negative case
has a derived, gridded view beside it in the same run (the control).
"""
import json

from tests.test_stage_a_grid_rollup import _capture, _main, _read, _run
from tools import stage_a_grid as grid
from tools import stage_a_grid_rollup as rollup
from tools import stage_a_kinds as kinds

# Pinned: 1.0.0's columns, which 1.1.0 must keep in place and meaning.
COLUMNS_1_0_0 = [
    # identity
    "run_id", "view_stem", "view_id",
    "view_name", "view_type", "scale", "is_on_sheet", "views_core_state",
    # status
    "row_status", "reason", "run_meta_capture_status", "run_meta_failure_reason",
    "registration_state", "registration_reason",
    # grid
    "cells_w", "cells_h", "cell_ft", "view_scale", "px_per_cell_min",
    "uv_basis_chosen", "uncertainty_px", "uncertainty_cells", "flags",
    "frame_predicted_px", "frame_actual_px", "frame_delta_px",
    # faults
    "capture_faults_model_n", "capture_faults_annotation_n", "capture_faults_detail",
    # occupancy
    "inside_crop_a_empty", "inside_crop_a_model_only", "inside_crop_a_anno_only",
    "inside_crop_a_overlap", "outside_crop_a_anno_only",
    # ink (image_totals)
    "model_host_ink", "model_dwg_ink", "model_link_ink",
    "anno_element_ink", "anno_residual", "anno_filled_region",
    # black pixels
    "black_total", "black_assigned", "black_unassigned", "black_ambiguous",
    # filled regions
    "filled_region_class_recorded", "filled_region_elements", "filled_region_px",
    # overlap
    "model_ink_px", "under_annotation_px", "under_filled_region_px",
    "fraction_under_annotation",
]


def _derive(run, n, class_map=None):
    info = kinds.load_class_map(class_map)
    return kinds.derive_view(run / "analysis_grid" / "V_{0}.grid.json".format(n), info)


def _int(v):
    return None if v == "" else int(float(v))


def test_the_1_0_0_columns_are_unchanged_and_first():
    assert rollup.COLUMNS[:len(COLUMNS_1_0_0)] == COLUMNS_1_0_0
    assert len(set(rollup.COLUMNS)) == len(rollup.COLUMNS)
    assert rollup.TOOL_VERSION == "1.1.0"


def test_a_derived_view_carries_its_class_totals_and_legacy_shapes(tmp_path):
    run = _run(tmp_path)
    _capture(run, 1)
    _capture(run, 2)                 # gridded, never derived: the control
    kin = _derive(run, 1)
    assert kin["status"] == "value"
    _main(run)
    by_stem, _rows, summary = _read(run / "analysis_grid")
    r, c = by_stem["V_1"], by_stem["V_2"]
    assert r["kinds_state"] == "value" and r["kinds_reason"] == ""
    assert r["class_map_sha256"] == grid.sha256_file(kinds.DEFAULT_CLASS_MAP)
    for family, prefix in (("annotation_class", "anno_class_"), ("model_class", "model_class_")):
        fam = kin["families"][family]
        for cls, totals in fam["totals"].items():
            assert _int(r[prefix + cls + "_px"]) == totals["px"], cls
            assert _int(r[prefix + cls + "_ink_px"]) == totals["ink_px"], cls
            assert _int(r[prefix + cls + "_cells_with_ink"]) == \
                kin["derived"]["cells_with_ink"][family][cls]["all_cells"], cls
    # Class px sum to the grid's own image totals.
    g = json.loads((run / "analysis_grid" / "V_1.grid.json").read_text())
    anno_px = sum(_int(r[col]) for col in rollup.ANNO_CLASS_COLUMNS if col.endswith("_px")
                  and not col.endswith("_ink_px"))
    assert anno_px == g["image_totals"]["anno_element"] > 0
    part = kin["derived"]["legacy_anno_partition"]["inside_crop_a"]
    assert _int(r["legacy_AnnoPresentFinal"]) == part["AnnoPresentFinal"] == \
        g["occupancy"]["inside_crop_a"]["anno_only"] + g["occupancy"]["inside_crop_a"]["overlap"]
    buckets = [col for col in rollup.LEGACY_PARTITION_COLUMNS
               if col.startswith("legacy_AnnoFinalCells_")]
    assert sum(_int(r[col]) for col in buckets) == _int(r["legacy_AnnoPresentFinal"])
    shape = kin["channel_shape_legacy"]["inside_crop_a"]
    for col in rollup.LEGACY_SHAPE_COLUMNS:
        assert _int(r[col]) == shape[col[len("legacy_"):]], col
    assert _int(r["legacy_TotalCells"]) == sum(_int(r["legacy_" + n])
                                               for n in kinds.LEGACY_PARTITION_8)
    # Crop A and the accounting come from the grid record, derived or not.
    for row in (r, c):
        assert _int(row["crop_a_px"]) == g["crop_a_coverage"]["px"] > 0
        assert _int(row["ec_rows"]) == g["ec_rows"] > 0
        assert _int(row["ec_keys"]) == len(g["ec_keys"])
    assert float(r["crop_a_cells_partial_fraction"]) == (
        g["crop_a_coverage"]["cells_partial"] / float(g["crop_a_coverage"]["cells_with_px"]))
    # The control: not derived -> said so, and its class columns are EMPTY.
    assert c["kinds_state"] == "absent" and "derive" in c["kinds_reason"]
    for col in rollup.ANNO_CLASS_COLUMNS + rollup.MODEL_CLASS_COLUMNS \
            + rollup.LEGACY_PARTITION_COLUMNS + rollup.LEGACY_SHAPE_COLUMNS:
        assert c[col] == "", col
    assert summary["kinds_state"]["counts"]["value"] == 1
    assert summary["kinds_state"]["counts"]["absent"] == 1
    assert summary["kinds_state"]["denominator"] == 2
    assert summary["class_map"]["sha256"] == r["class_map_sha256"]


def test_the_summary_lists_unmapped_kinds_and_null_categories(tmp_path):
    """The shared fixture records no bbox map and no near_face_w_map: both
    annotation elements are a kind of nulls (unmapped), and the model element
    has no category. Counted with their denominators, never dropped."""
    run = _run(tmp_path)
    _capture(run, 1)
    _capture(run, 2, filled_region_ids=(50,))      # categories recorded: the control
    for n in (1, 2):
        _derive(run, n)
    _main(run)
    _by, _rows, summary = _read(run / "analysis_grid")
    un = summary["unmapped_kinds"]
    assert un["denominator"] == 2 and un["count"] == 1
    kind = un["kinds"][0]
    assert (kind["layer"], kind["category"], kind["element_class"]) == ("annotation", None, None)
    assert kind["views"] == 1 and kind["keys"] == 2 and kind["px"] > 0
    nulls = summary["keys_with_null_category"]
    assert nulls["annotation"]["keys"] == 2 and nulls["annotation"]["denominator"] == 4
    assert nulls["model"]["keys"] == 2 and nulls["model"]["denominator"] == 2
    assert summary["accounting_not_read"]["count"] == 0


def test_a_derivation_from_another_map_or_an_older_grid_leaves_classes_empty(tmp_path):
    run = _run(tmp_path)
    for n in (1, 2, 3):
        _capture(run, n)
    _derive(run, 1)                                         # the control
    other = json.loads(kinds.DEFAULT_CLASS_MAP.read_text())
    other["version"] = "1-test"
    alt = tmp_path / "other_map.json"
    alt.write_text(json.dumps(other))
    _derive(run, 2, class_map=alt)
    _derive(run, 3)
    gpath = run / "analysis_grid" / "V_3.grid.json"
    rec = json.loads(gpath.read_text())
    rec["regridded_note"] = "the grid record changed after the derivation"
    gpath.write_text(json.dumps(rec))
    _main(run)
    by_stem, _rows, summary = _read(run / "analysis_grid")
    assert by_stem["V_1"]["kinds_state"] == "value"
    assert by_stem["V_2"]["kinds_state"] == "class_map_differs"
    assert by_stem["V_3"]["kinds_state"] == "stale"
    assert "grid_json_sha256" in by_stem["V_3"]["kinds_reason"]
    for stem in ("V_2", "V_3"):
        assert all(by_stem[stem][col] == "" for col in rollup.ANNO_CLASS_COLUMNS), stem
    assert by_stem["V_1"]["anno_class_unmapped_px"] != ""
    assert summary["kinds_state"]["counts"] == {"value": 1, "absent": 0, "refused": 0,
                                                "stale": 1, "class_map_differs": 1,
                                                "unreadable": 0}


def test_a_model_only_view_has_model_classes_and_no_annotation_classes(tmp_path):
    run = _run(tmp_path)
    _capture(run, 1, register=False)
    kin = _derive(run, 1)
    assert kin["families"]["annotation_class"]["state"] == "not_applicable"
    _main(run)
    by_stem, _rows, _s = _read(run / "analysis_grid")
    r = by_stem["V_1"]
    assert r["kinds_state"] == "value"
    assert all(r[col] == "" for col in rollup.ANNO_CLASS_COLUMNS)
    assert _int(r["model_class_OTHER_px"]) > 0       # the fixture's element: no category
    assert _int(r["model_class_WALL_px"]) == 0       # applicable and measured: 0, not empty


def test_views_refused_by_the_accounting_are_listed(tmp_path, monkeypatch):
    run = _run(tmp_path)
    _capture(run, 1)                                         # the control
    real = grid.element_cell_accounting

    def lossy(spec, model, anno=None):
        ec, keys, info = real(spec, model, anno)
        ec["ec_ink_px"][0] += 1
        return ec, keys, info
    with monkeypatch.context() as m:
        m.setattr(grid, "element_cell_accounting", lossy)
        _capture(run, 2)
    _main(run)
    by_stem, _rows, summary = _read(run / "analysis_grid")
    assert by_stem["V_1"]["row_status"] == "gridded"
    assert by_stem["V_2"]["row_status"] == "grid_refused"
    refused = summary["ec_invariant_refusals"]
    assert refused["count"] == 1 and refused["denominator"] == 2
    assert refused["views"][0]["view_stem"] == "V_2"
    assert "cells differ" in refused["views"][0]["reason"]


# --- the manifest v2 --------------------------------------------------------------------

def test_the_manifest_names_the_class_map_it_describes_and_every_v1_column():
    m = json.loads((kinds.DEFAULT_CLASS_MAP.parent / "stage_a_metrics_manifest.v2.json").read_text())
    v1 = json.loads(open("vop_interwoven/metrics/manifest/metrics_manifest.v1.json").read())
    assert m["class_map"]["sha256"] == grid.sha256_file(kinds.DEFAULT_CLASS_MAP)
    assert m["lattice"]["anchor"] == grid.LATTICE_ANCHOR
    assert m["lattice"]["origin_rule"] == grid.ORIGIN_RULE
    assert m["lattice"]["grid_schema"] == grid.SCHEMA
    assert set(m["legacy_mapping"]) == set(v1["outputs"]["csv_columns"])
    for col, entry in m["legacy_mapping"].items():
        assert entry["relation"] in ("same", "different_semantics", "not_available"), col
    for fam in ("annotation_class", "model_class", "legacy_bucket"):
        cmap = json.loads(kinds.DEFAULT_CLASS_MAP.read_text())
        assert m["families"][fam]["classes"] == cmap[fam]["classes"], fam
    assert m["families"]["element_cell"]["fields"] == list(grid.EC_FIELDS)
