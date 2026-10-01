"""The grid roll-up (tools/stage_a_grid_rollup.py).

Every fixture is made by the REAL producers: the registration fixture of
test_register_stage_a_annotation (through test_stage_a_grid's wrapper, which
adds the capture_integrity and annotation_bbox_status every current capture
writes), registered by ``register_stage_a_annotation.register`` and gridded by
``stage_a_grid.grid_view``. Each view is copied into a run's
``color_id_buffer/`` under its own stem, so one run holds several views.

Assertions read the CSV and the summary JSON the tool WROTE (CLAUDE.md,
defect class 4), and every negative case has a clean view beside it in the
same run, so a tool that marked everything bad would fail the control.
"""
import csv
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from tools import register_stage_a_annotation as reg
from tools import stage_a_grid as grid
from tools import stage_a_grid_rollup as rollup

from tests.test_register_stage_a_annotation import ELEMENTS
from tests.test_stage_a_grid import _bbox_entry
from tests.test_stage_a_grid import _write_pair as _grid_pair

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "stage_a_grid_rollup.py"


# --- fixtures ------------------------------------------------------------------

def _run(tmp_path, name="run_a", run_id="RUN_A", commit="c0ffee"):
    run = tmp_path / name
    (run / "color_id_buffer").mkdir(parents=True)
    (run / "run_meta.json").write_text(json.dumps(
        {"run_id": run_id, "git_commit": commit, "finalized": True, "views": [],
         "config": {"cell_size_paper_in": 0.125}}))
    return run


def _outcome(run, n, status="success", **meta):
    """Record view ``n``'s capture outcome in run_meta.json, as
    run_meta.finalize_run_meta writes it."""
    path = run / "run_meta.json"
    doc = json.loads(path.read_text())
    doc["views"] = [v for v in doc["views"] if v["view_id"] != n] + [
        {"view_id": n, "view_name": "V {0}".format(n), "capture_status": status,
         "capture_failure_reason": ""}]
    doc.update(meta)
    path.write_text(json.dumps(doc))


def _capture(run, n, register=True, gridded=True, model_faults=None,
             filled_region_ids=(), drop_anno_marks=False):
    """View ``V_<n>`` in ``run``: the shared pair, renamed and re-pointed,
    then (optionally) registered and gridded by the real tools."""
    stage = run.parent / "stage_{0}_{1}".format(run.name, n)
    stage.mkdir()
    anno_src, model_src, _c = _grid_pair(stage)
    cap = run / "color_id_buffer"
    model_tiff, anno_tiff = cap / "V_{0}.tiff".format(n), cap / "V_{0}_anno.tiff".format(n)
    shutil.copy(str(stage / "V_1.tiff"), str(model_tiff))
    shutil.copy(str(stage / "V_1_anno.tiff"), str(anno_tiff))
    model = json.loads(model_src.read_text())
    model.update(view_id=n, tiff_path=str(model_tiff))
    if model_faults:
        model["capture_integrity"]["capture_faults"] = list(model_faults)
    anno = json.loads(anno_src.read_text())
    anno.update(view_id=n, tiff_path=str(anno_tiff), model_pass_tiff_path=str(model_tiff))
    if filled_region_ids:
        anno["annotation_bbox_map"] = dict(
            (str(eid), _bbox_entry(rect, "FilledRegion" if eid in filled_region_ids
                                   else "TextNote"))
            for eid, (_colour, rect) in ELEMENTS.items())
    if drop_anno_marks:
        anno.pop("registration_marks")
    model_path, anno_path = cap / "V_{0}.json".format(n), cap / "V_{0}_anno.json".format(n)
    model_path.write_text(json.dumps(model))
    anno_path.write_text(json.dumps(anno))
    _outcome(run, n)
    if register:
        reg.register(anno_path)
    if gridded:
        grid.grid_view(model_path, run / "analysis_grid")
    return model_path, anno_path


def _main(*argv):
    return rollup.main([str(a) for a in argv])


def _read(out):
    with open(str(out / rollup.CSV_NAME), encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    summary = json.loads((out / rollup.SUMMARY_NAME).read_text())
    return dict((r["view_stem"], r) for r in rows), rows, summary


def _grid_json(run, n):
    return json.loads((run / "analysis_grid" / "V_{0}.grid.json".format(n)).read_text())


GRID_COLUMNS = [c for c in rollup.COLUMNS if rollup.COLUMNS.index(c) >=
                rollup.COLUMNS.index("cells_w")]


# --- 1. a clean view ----------------------------------------------------------

def test_a_gridded_registered_view_carries_the_grids_own_numbers(tmp_path):
    run = _run(tmp_path)
    _capture(run, 1)
    assert _main(run) == 0
    by_stem, rows, summary = _read(run / "analysis_grid")
    r = by_stem["V_1"]
    g = _grid_json(run, 1)
    assert g["status"] == "value"
    assert (r["row_status"], r["registration_state"]) == ("gridded", "registered")
    assert r["run_id"] == "RUN_A" and r["view_id"] == "1" and r["reason"] == ""
    spec = g["grid"]
    assert int(r["cells_w"]) == spec["cells_w"] and int(r["cells_h"]) == spec["cells_h"]
    assert float(r["px_per_cell_min"]) == min(spec["px_per_cell_u"], spec["px_per_cell_v"])
    assert r["uv_basis_chosen"] == spec["uv_basis"]["chosen"]
    assert r["flags"] == "|".join(spec["flags"])
    assert "no_registered_annotation" not in r["flags"]
    assert int(r["inside_crop_a_model_only"]) == g["occupancy"]["inside_crop_a"]["model_only"]
    assert int(r["outside_crop_a_anno_only"]) == g["occupancy"]["outside_crop_a"]["anno_only"]
    assert int(r["model_host_ink"]) == g["image_totals"]["model_host_ink"] > 0
    assert int(r["anno_element_ink"]) == g["image_totals"]["anno_element_ink"] > 0
    assert int(r["black_total"]) == g["black"]["total"]
    assert int(r["model_ink_px"]) == g["model_ink_under_annotation"]["model_ink_px"]
    assert r["capture_faults_model_n"] == "0" and r["capture_faults_annotation_n"] == "0"
    assert summary["denominator"] == 1
    assert summary["row_status"] == {"denominator": 1, "counts": dict(
        (s, int(s == "gridded")) for s in rollup.ROW_STATUSES)}
    assert summary["model_only_count"]["count"] == 0
    assert summary["not_gridded_rows"]["rows"] == []
    assert summary["runs"][0]["git_commit"] == "c0ffee"


def test_the_summary_states_states_not_a_verdict(tmp_path):
    """Tool gravity: no ok/pass/clean, and a denominator beside every count."""
    run = _run(tmp_path)
    _capture(run, 1)
    _capture(run, 2, gridded=False)
    _main(run)
    _b, _r, summary = _read(run / "analysis_grid")

    def keys(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield k
                for kk in keys(v):
                    yield kk
        elif isinstance(obj, list):
            for v in obj:
                for kk in keys(v):
                    yield kk
    names = set(k.lower() for k in keys(summary))
    assert not names & {"ok", "pass", "passed", "clean", "success"}
    for key, block in summary.items():
        if isinstance(block, dict) and ("count" in block or "counts" in block):
            assert "denominator" in block, key


# --- 2. a grid refusal --------------------------------------------------------

def test_a_grid_refusal_is_a_row_with_the_grids_reason(tmp_path):
    run = _run(tmp_path)
    _capture(run, 1)
    _capture(run, 2, model_faults=["view_specific_import_not_suppressed"])
    assert _grid_json(run, 2)["status"] == "refused"
    assert _main(run) == 1
    by_stem, _rows, summary = _read(run / "analysis_grid")
    assert by_stem["V_1"]["row_status"] == "gridded"           # control
    r = by_stem["V_2"]
    assert r["row_status"] == "grid_refused"
    assert r["reason"] == _grid_json(run, 2)["reason"]
    assert "invalidate its pixels" in r["reason"]
    assert all(r[c] == "" for c in GRID_COLUMNS), "n/a is empty, never a number"
    assert summary["row_status"]["counts"]["grid_refused"] == 1
    assert [x["view_stem"] for x in summary["not_gridded_rows"]["rows"]] == ["V_2"]


# --- 3. no grid.json ----------------------------------------------------------

def test_a_sidecar_with_no_grid_json_is_not_gridded_and_still_counted(tmp_path):
    """The denominator is the sidecars: two views, one grid.json, two rows,
    and the counts add up to the denominator."""
    run = _run(tmp_path)
    _capture(run, 1)
    _capture(run, 2, gridded=False)
    assert sorted(p.name for p in (run / "analysis_grid").glob("*.grid.json")) == [
        "V_1.grid.json"]
    assert _main(run) == 1
    by_stem, rows, summary = _read(run / "analysis_grid")
    assert len(rows) == 2 and summary["denominator"] == 2
    assert by_stem["V_1"]["row_status"] == "gridded"
    assert by_stem["V_2"]["row_status"] == "not_gridded"
    assert "V_2.grid.json" in by_stem["V_2"]["reason"]
    assert by_stem["V_2"]["registration_state"] == "registered"
    assert sum(summary["row_status"]["counts"].values()) == summary["denominator"]
    assert sum(summary["registration_state"]["counts"].values()) == summary["denominator"]


def test_an_unparseable_grid_json_is_unreadable_with_the_exception(tmp_path):
    run = _run(tmp_path)
    _capture(run, 1)
    _capture(run, 2)
    (run / "analysis_grid" / "V_2.grid.json").write_text("{ not json")
    _main(run)
    by_stem, _rows, summary = _read(run / "analysis_grid")
    assert by_stem["V_1"]["row_status"] == "gridded"
    assert by_stem["V_2"]["row_status"] == "unreadable"
    assert by_stem["V_2"]["reason"].startswith("JSONDecodeError:")
    assert summary["row_status"]["counts"]["unreadable"] == 1


# --- 4. staleness ---------------------------------------------------------------

def _touch_json(path, **extra):
    doc = json.loads(path.read_text())
    doc.update(extra)
    path.write_text(json.dumps(doc))


def test_a_sidecar_changed_after_gridding_is_stale(tmp_path):
    run = _run(tmp_path)
    _capture(run, 1)
    model, _a = _capture(run, 2)
    _touch_json(model, recaptured=True)
    _main(run)
    by_stem, _rows, _s = _read(run / "analysis_grid")
    assert by_stem["V_1"]["row_status"] == "gridded"
    assert by_stem["V_2"]["row_status"] == "grid_stale"
    assert "model_sidecar_sha256" in by_stem["V_2"]["reason"]
    assert all(by_stem["V_2"][c] == "" for c in GRID_COLUMNS)


def test_a_tiff_changed_after_gridding_is_stale(tmp_path):
    run = _run(tmp_path)
    _capture(run, 1)
    _capture(run, 2)
    tiff = run / "color_id_buffer" / "V_2.tiff"
    tiff.write_bytes(tiff.read_bytes() + b"\0")
    _main(run)
    by_stem, _rows, _s = _read(run / "analysis_grid")
    assert by_stem["V_1"]["row_status"] == "gridded"
    assert by_stem["V_2"]["row_status"] == "grid_stale"
    assert by_stem["V_2"]["reason"] == ("model_tiff_sha256 does not match the model "
                                        "TIFF on disk")


def test_a_registration_record_changed_after_gridding_is_stale(tmp_path):
    run = _run(tmp_path)
    _capture(run, 1)
    _m, anno = _capture(run, 2)
    _touch_json(rollup.registration_record_path(run / "color_id_buffer" / "V_2.json"),
                rewritten=True)
    _main(run)
    by_stem, _rows, _s = _read(run / "analysis_grid")
    assert by_stem["V_1"]["row_status"] == "gridded"
    assert by_stem["V_2"]["row_status"] == "grid_stale"
    assert "registered_annotation.record_sha256" in by_stem["V_2"]["reason"]


def test_a_registration_made_after_a_model_only_grid_is_stale(tmp_path):
    """The grid read no record because there was none; once one exists the
    grid's flags (no_registered_annotation) no longer describe the view."""
    run = _run(tmp_path)
    _capture(run, 1)
    _m, anno = _capture(run, 2, register=False)
    assert "no_registered_annotation" in _grid_json(run, 2)["grid"]["flags"]
    reg.register(anno)
    _main(run)
    by_stem, _rows, _s = _read(run / "analysis_grid")
    assert by_stem["V_1"]["row_status"] == "gridded"
    assert by_stem["V_2"]["row_status"] == "grid_stale"
    assert "made without" in by_stem["V_2"]["reason"]


def test_a_refused_registration_that_later_registers_makes_the_grid_stale(tmp_path):
    run = _run(tmp_path)
    _capture(run, 1)
    _m, anno = _capture(run, 2, drop_anno_marks=True)
    assert _grid_json(run, 2)["registered_annotation"]["status"] == "unusable"
    # Re-capture only the annotation pass (the model sidecar is untouched),
    # then register again: the record is now "registered".
    _m3, anno3 = _capture(run, 3, register=False, gridded=False)
    doc = json.loads(anno.read_text())
    doc["registration_marks"] = json.loads(anno3.read_text())["registration_marks"]
    anno.write_text(json.dumps(doc))
    assert reg.register(anno)["status"] == "registered"
    _main(run)
    by_stem, _rows, _s = _read(run / "analysis_grid")
    assert by_stem["V_1"]["row_status"] == "gridded"
    assert by_stem["V_2"]["row_status"] == "grid_stale"
    assert "now registered" in by_stem["V_2"]["reason"]


# --- 5. registration state and model-only -------------------------------------------

def test_a_refused_registration_is_refused_and_the_view_is_model_only(tmp_path):
    run = _run(tmp_path)
    _capture(run, 1)
    _capture(run, 2, drop_anno_marks=True)
    _capture(run, 3, register=False)
    assert _main(run) == 1
    by_stem, _rows, summary = _read(run / "analysis_grid")
    assert by_stem["V_1"]["registration_state"] == "registered"     # control
    r2 = by_stem["V_2"]
    assert (r2["row_status"], r2["registration_state"]) == ("gridded", "refused")
    assert "no registration_marks" in r2["registration_reason"]
    assert "no_registered_annotation" in r2["flags"].split("|")
    assert r2["anno_element_ink"] == "" and r2["black_total"] == ""
    r3 = by_stem["V_3"]
    assert (r3["row_status"], r3["registration_state"]) == ("gridded", "absent")
    assert "no_registered_annotation" in r3["flags"].split("|")
    assert summary["model_only_count"] == {
        "denominator": 3, "denominator_rule": "gridded rows", "count": 2,
        "rule": "gridded rows whose registration_state is not registered"}
    assert summary["registration_state"]["counts"] == {
        "registered": 1, "refused": 1, "absent": 1, "unusable": 0}
    assert summary["flag_counts"]["counts"]["no_registered_annotation"] == 2


def test_a_registration_the_grid_found_unusable_is_unusable(tmp_path):
    """A record whose status is "registered" but carries no lattice: the grid
    records it unusable, and so does the roll-up (not "registered")."""
    run = _run(tmp_path)
    _capture(run, 1)
    model, anno = _capture(run, 2, gridded=False)
    record = rollup.registration_record_path(model)
    doc = json.loads(record.read_text())
    doc.pop("lattice")
    record.write_text(json.dumps(doc))
    grid.grid_view(model, run / "analysis_grid")
    assert _grid_json(run, 2)["registered_annotation"]["status"] == "unusable"
    _main(run)
    by_stem, _rows, summary = _read(run / "analysis_grid")
    assert by_stem["V_1"]["registration_state"] == "registered"
    assert by_stem["V_2"]["row_status"] == "gridded"
    assert by_stem["V_2"]["registration_state"] == "unusable"
    assert summary["model_only_count"]["count"] == 1


# --- 6. filled regions ----------------------------------------------------------------

def test_a_view_with_a_filled_region_is_listed_by_name(tmp_path):
    run = _run(tmp_path)
    _capture(run, 1, filled_region_ids=())
    _capture(run, 2, filled_region_ids=(50,))
    (run / "views_core_2026-10-01.csv").write_text(
        "RunId,ViewId,ViewName,ViewType,Scale,IsOnSheet\n"
        "RUN_A,1,Plan One,FloorPlan,96,Y\nRUN_A,2,Plan Two,FloorPlan,96,N\n")
    _main(run)
    by_stem, _rows, summary = _read(run / "analysis_grid")
    assert by_stem["V_1"]["filled_region_elements"] == "0"            # control
    assert by_stem["V_2"]["filled_region_elements"] == "1"
    assert by_stem["V_2"]["filled_region_class_recorded"] == "true"
    assert int(by_stem["V_2"]["filled_region_px"]) == \
        _grid_json(run, 2)["filled_region"]["px"] > 0
    fr = summary["filled_region_views"]
    assert fr["denominator"] == 2 and fr["count"] == 1
    assert [(v["view_stem"], v["view_name"]) for v in fr["views"]] == [("V_2", "Plan Two")]


# --- views_core join ----------------------------------------------------------------

def test_views_core_is_joined_on_view_id_and_its_state_recorded(tmp_path):
    run = _run(tmp_path)
    _capture(run, 1)
    _capture(run, 2)
    core = run / "views_core_2026-10-01.csv"
    core.write_text("RunId,ViewId,ViewName,ViewType,Scale,IsOnSheet\n"
                    "RUN_A,1,Plan One,FloorPlan,96,Y\n")
    _main(run)
    by_stem, _rows, _s = _read(run / "analysis_grid")
    r1 = by_stem["V_1"]
    assert (r1["view_name"], r1["view_type"], r1["scale"], r1["is_on_sheet"],
            r1["views_core_state"]) == ("Plan One", "FloorPlan", "96", "Y", "value")
    assert by_stem["V_2"]["views_core_state"] == "absent"
    assert by_stem["V_2"]["view_name"] == ""
    (run / "views_core_2026-10-02.csv").write_text(core.read_text())
    _main(run)
    by_stem, _rows, summary = _read(run / "analysis_grid")
    assert by_stem["V_1"]["views_core_state"] == "ambiguous"
    assert by_stem["V_1"]["view_name"] == ""
    assert summary["runs"][0]["views_core_state"] == "ambiguous"
    core.unlink()
    (run / "views_core_2026-10-02.csv").unlink()
    _main(run)
    by_stem, _rows, _s = _read(run / "analysis_grid")
    assert by_stem["V_1"]["views_core_state"] == "absent"


# --- 7. several runs ------------------------------------------------------------------

def test_two_runs_roll_up_into_one_out_dir(tmp_path):
    a = _run(tmp_path, "run_a", "RUN_A", "aaa")
    b = _run(tmp_path, "run_b", "RUN_B", "bbb")
    _capture(a, 1)
    _capture(b, 1)
    _capture(b, 2, gridded=False)
    out = tmp_path / "rollup"
    assert _main(a, b / "color_id_buffer", "--out", out) == 1
    _by, rows, summary = _read(out)
    assert [(r["run_id"], r["view_stem"], r["row_status"]) for r in rows] == [
        ("RUN_A", "V_1", "gridded"), ("RUN_B", "V_1", "gridded"),
        ("RUN_B", "V_2", "not_gridded")]
    assert [(x["run_id"], x["git_commit"], x["model_sidecars"])
            for x in summary["runs"]] == [("RUN_A", "aaa", 1), ("RUN_B", "bbb", 2)]
    assert summary["denominator"] == 3
    assert not (a / "analysis_grid" / rollup.CSV_NAME).exists()


def test_two_runs_without_out_exit_2_and_write_nothing(tmp_path):
    a = _run(tmp_path, "run_a", "RUN_A")
    b = _run(tmp_path, "run_b", "RUN_B")
    _capture(a, 1)
    _capture(b, 1)
    proc = subprocess.run([sys.executable, str(TOOL), str(a), str(b)],
                          capture_output=True, text=True, cwd=str(REPO))
    assert proc.returncode == 2, proc.stderr
    assert "--out is required" in proc.stderr
    for run in (a, b):
        assert not (run / "analysis_grid" / rollup.CSV_NAME).exists()
    control = subprocess.run([sys.executable, str(TOOL), str(a)],
                             capture_output=True, text=True, cwd=str(REPO))
    assert control.returncode == 0, control.stdout + control.stderr
    assert (a / "analysis_grid" / rollup.SUMMARY_NAME).exists()


def test_a_run_with_no_model_sidecars_exits_2(tmp_path):
    empty = _run(tmp_path, "empty")
    assert _main(empty) == 2
    assert not (empty / "analysis_grid").exists()


def test_it_never_writes_into_the_capture_folder(tmp_path):
    run = _run(tmp_path)
    _capture(run, 1)
    cap = run / "color_id_buffer"
    before = dict((p.name, p.read_bytes()) for p in cap.iterdir())
    proc = subprocess.run([sys.executable, str(TOOL), str(run), "--out",
                           str(cap / "x")], capture_output=True, text=True)
    assert proc.returncode == 2 and "never writes there" in proc.stderr
    assert _main(run) == 0
    assert dict((p.name, p.read_bytes()) for p in cap.iterdir()) == before


# --- 8. the summary names the CSV it describes -------------------------------------

def test_the_summary_carries_the_written_csvs_sha256(tmp_path):
    run = _run(tmp_path)
    _capture(run, 1)
    _capture(run, 2, gridded=False)
    _main(run)
    out = run / "analysis_grid"
    summary = json.loads((out / rollup.SUMMARY_NAME).read_text())
    assert summary["csv"] == rollup.CSV_NAME
    assert summary["csv_sha256"] == hashlib.sha256(
        (out / rollup.CSV_NAME).read_bytes()).hexdigest()
    assert (out / rollup.SUMMARY_NAME).stat().st_mtime_ns >= \
        (out / rollup.CSV_NAME).stat().st_mtime_ns


# --- review (Codex, PR #224) -----------------------------------------------------------

def test_a_registered_tiff_changed_after_gridding_is_stale(tmp_path):
    """The grid's annotation numbers come from the registered TIFF; the
    registration JSON left untouched does not vouch for it."""
    run = _run(tmp_path)
    _capture(run, 1)
    _m, anno = _capture(run, 2)
    tiff = reg.output_paths(anno)[0]
    tiff.write_bytes(tiff.read_bytes() + b"\0")
    _main(run)
    by_stem, _rows, _s = _read(run / "analysis_grid")
    assert by_stem["V_1"]["row_status"] == "gridded"
    assert by_stem["V_2"]["row_status"] == "grid_stale"
    assert by_stem["V_2"]["reason"] == ("registered_annotation.tiff_sha256 does not "
                                        "match the registered annotation TIFF on disk")


def test_a_sidecar_whose_capture_failed_in_this_run_is_not_this_runs(tmp_path):
    """A failed capture leaves the previous run's sidecar and grid in place.
    They are reported as not this run's, never as a clean gridded view."""
    run = _run(tmp_path)
    _capture(run, 1)
    _capture(run, 2)
    _outcome(run, 2, status="failed")
    assert _main(run) == 1
    by_stem, _rows, summary = _read(run / "analysis_grid")
    assert (by_stem["V_1"]["row_status"], by_stem["V_1"]["run_capture_status"]) == (
        "gridded", "success")
    r = by_stem["V_2"]
    assert (r["row_status"], r["run_capture_status"]) == ("not_this_run", "failed")
    assert "cannot be tied to run 'RUN_A'" in r["reason"]
    assert all(r[c] == "" for c in GRID_COLUMNS)
    assert summary["row_status"]["counts"]["not_this_run"] == 1


def test_a_view_run_meta_does_not_list_is_not_this_runs(tmp_path):
    run = _run(tmp_path)
    _capture(run, 1)
    _capture(run, 2)
    doc = json.loads((run / "run_meta.json").read_text())
    doc["views"] = [v for v in doc["views"] if v["view_id"] != 2]
    (run / "run_meta.json").write_text(json.dumps(doc))
    _main(run)
    by_stem, _rows, _s = _read(run / "analysis_grid")
    assert by_stem["V_1"]["row_status"] == "gridded"
    assert (by_stem["V_2"]["row_status"], by_stem["V_2"]["run_capture_status"]) == (
        "not_this_run", "unrecorded")


def test_an_unfinalized_run_ties_no_sidecar(tmp_path):
    run = _run(tmp_path)
    _capture(run, 1)
    _outcome(run, 1, finalized=False)
    assert _main(run) == 1
    by_stem, _rows, summary = _read(run / "analysis_grid")
    assert by_stem["V_1"]["row_status"] == "not_this_run"
    assert "not finalized" in by_stem["V_1"]["reason"]
    assert summary["runs"][0]["run_meta_finalized"] is False


def test_a_grid_made_under_another_run_id_is_stale(tmp_path):
    """The run folder reused: run_meta now names a new run that captured the
    view successfully, but the grid on disk was made under the old one."""
    run = _run(tmp_path)
    _capture(run, 1)
    _capture(run, 2)
    for n in (1, 2):
        grid.grid_view(run / "color_id_buffer" / "V_{0}.json".format(n),
                       run / "analysis_grid")
    _outcome(run, 2, run_id="RUN_NEW")
    grid.grid_view(run / "color_id_buffer" / "V_1.json", run / "analysis_grid")
    _main(run)
    by_stem, _rows, _s = _read(run / "analysis_grid")
    assert by_stem["V_1"]["row_status"] == "gridded"                 # control
    assert by_stem["V_2"]["row_status"] == "grid_stale"
    assert "made under run_id 'RUN_A', not 'RUN_NEW'" in by_stem["V_2"]["reason"]


def test_views_core_rows_of_another_run_are_not_joined(tmp_path):
    run = _run(tmp_path)
    _capture(run, 1)
    _capture(run, 2)
    (run / "views_core_2026-10-01.csv").write_text(
        "RunId,ViewId,ViewName,ViewType,Scale,IsOnSheet\n"
        "RUN_OLD,1,Old Name,FloorPlan,48,N\n"
        "RUN_A,1,Plan One,FloorPlan,96,Y\n"
        "RUN_OLD,2,Old Two,FloorPlan,48,N\n")
    _main(run)
    by_stem, _rows, _s = _read(run / "analysis_grid")
    assert (by_stem["V_1"]["view_name"], by_stem["V_1"]["views_core_state"]) == (
        "Plan One", "value")
    assert (by_stem["V_2"]["view_name"], by_stem["V_2"]["views_core_state"]) == (
        "", "absent")
