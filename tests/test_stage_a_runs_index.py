"""The runs index (tools/stage_a_runs_index.py).

Each run folder is named by ``run_meta.run_directory`` and its roll-up and
kinds outputs are written by the REAL producers (``stage_a_grid_rollup.rollup``
over the run, ``stage_a_kinds.write_inventory``), so the index is bound to the
shapes those tools actually write, not to a copy of them (CLAUDE.md, defect
class 1). Assertions read the files the index WROTE (defect class 4).

Fixtures (one model_key each unless stated):
  A  complete; roll-up and kinds at the run's top level
  B  complete; roll-up and kinds in analysis_grid/ (the producers' default)
  C  finalized: false
  D  no roll-up files (and no kinds)
  E  roll-up CSV altered after its summary recorded the hash
  F  folder date differs from run_meta date
  G  a decoy run under a NON-run folder's analysis_grid/ and color_id_buffer/,
     whose only protection is the reserved-name rule; plus decoys inside A
     (analysis_grid/ and an ordinary subfolder)
  H  two runs sharing model_key + as_of_date
  I  an unparsable run_meta.json -> skipped, not a row
Rows: A, B, C, D, E, F, H1, H2 = 8.
"""
import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools import stage_a_grid_rollup as rollup
from tools import stage_a_kinds as kinds
from tools import stage_a_runs_index as idx
from vop_interwoven import run_meta

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "stage_a_runs_index.py"

ROLLUP_COUNT_COLUMNS = ("rollup_refused_runs_count", "rollup_count_invariant_difference",
                        "rollup_row_status_counts", "rollup_registration_state_counts",
                        "rollup_flag_counts", "rollup_run_ids", "rollup_csv_sha256",
                        "rollup_csv_sha256_match", "rollup_schema", "rollup_tool_version",
                        "rollup_class_map_sha256", "rollup_class_map_version")


# --- fixtures ------------------------------------------------------------------

def make_run(root, model_key, as_of, run_id, *, finalized=True, meta_date=None,
             outputs="top", views_core_rows=2, run_meta_text=None):
    """One run folder, named as the exporter names it. ``outputs`` is
    ``"top"``, ``"analysis_grid"`` or None (no roll-up, no kinds)."""
    run = Path(run_meta.run_directory(str(root / model_key),
                                      {"as_of_date": as_of, "run_id": run_id}))
    (run / "color_id_buffer").mkdir(parents=True)
    (run / "color_id_buffer" / "V_1.json").write_text("{}")
    meta = {"schema": run_meta.RUN_META_SCHEMA, "finalized": finalized, "run_id": run_id,
            "date": meta_date or as_of, "run_tag": None, "doc_title": "Doc " + model_key,
            "doc_path": "C:\\models\\" + model_key.replace("/", "\\") + ".rvt",
            "exporter_version": run_meta.EXPORTER_VERSION, "git_commit": "c0ffee",
            "revit_version": {"version_number": "2024", "version_build": "x",
                              "version_name": "Autodesk Revit 2024"},
            "config_hash": "abcd1234",
            "views_requested": [101, 102, 103],
            "views": [{"view_id": 101, "capture_status": "success"},
                      {"view_id": 102, "capture_status": "failed"},
                      {"view_id": 103, "capture_status": "failed"}]}
    (run / run_meta.RUN_META_FILENAME).write_text(
        run_meta_text if run_meta_text is not None else json.dumps(meta))
    d = as_of
    with open(run / "views_core_{0}.csv".format(d), "w", newline="") as h:
        w = csv.writer(h)
        w.writerow(["RunId", "ViewId", "ViewName"])
        for n in range(views_core_rows):
            w.writerow([run_id, 101 + n, "V, {0}".format(n)])
    (run / "views_diagnostics_{0}.json".format(d)).write_text("{}")
    (run / "vop_view_element_map_{0}.json".format(d)).write_text("{}")
    if outputs and run_meta_text is None:
        out = run if outputs == "top" else run / "analysis_grid"
        rollup.rollup([rollup.run_layout(run)], out)
        kinds.write_inventory([], [], kinds.load_class_map(), out)
    return run


@pytest.fixture
def tree(tmp_path):
    return build_tree(tmp_path)


def build_tree(tmp_path):
    """Fixtures A-I under ``<tmp_path>/Exports``; ``(root, {name: run_dir})``."""
    root = tmp_path / "Exports"
    runs = {
        "A": make_run(root, "KSRF/Model_A", "2025-04-05", "20261006T084152"),
        "B": make_run(root, "KSRF/Model_B", "2025-04-05", "20261006T090000",
                      outputs="analysis_grid"),
        "C": make_run(root, "KSRF/Model_C", "2025-04-05", "20261006T091000",
                      finalized=False),
        "D": make_run(root, "Other/Model_D", "2025-04-06", "20261006T092000", outputs=None),
        "E": make_run(root, "Other/Model_E", "2025-04-06", "20261006T093000"),
        "F": make_run(root, "Other/Model_F", "2025-04-07", "20261006T094000",
                      meta_date="2025-04-08"),
        "H1": make_run(root, "Proj/Model_H", "2025-05-01", "20261006T100000"),
        "H2": make_run(root, "Proj/Model_H", "2025-05-01", "20261006T110000_PR_221"),
        "I": make_run(root, "Proj/Model_I", "2025-05-02", "20261006T120000",
                      run_meta_text="{not json"),
    }
    # E: the CSV no longer matches the hash its summary recorded.
    with open(runs["E"] / idx.ROLLUP_CSV, "a", encoding="utf-8") as h:
        h.write("tampered\n")
    # G: decoys only the reserved-name rule keeps out (a non-run folder).
    g = root / "KSRF" / "Model_G"
    for reserved in ("analysis_grid", "color_id_buffer"):
        decoy = Path(run_meta.run_directory(str(g / reserved), {
            "as_of_date": "2025-04-05", "run_id": "DECOY_" + reserved}))
        decoy.mkdir(parents=True)
        (decoy / "run_meta.json").write_text(json.dumps(
            {"run_id": "DECOY_" + reserved, "date": "2025-04-05", "finalized": True}))
    # ...and one inside a real run's analysis_grid (stopped at the run folder).
    # ...and inside a real run, under analysis_grid/ and under an ordinary
    # subfolder (only "stop descending at a run folder" keeps the latter out).
    for sub in ("analysis_grid", "archive"):
        decoy_a = runs["A"] / sub / "2025-04-05__DECOY_A_{0}".format(sub)
        decoy_a.mkdir(parents=True)
        (decoy_a / "run_meta.json").write_text(json.dumps(
            {"run_id": "DECOY_A_" + sub, "date": "2025-04-05", "finalized": True}))
    return root, runs


def _index(root, *extra):
    code = idx.main(["--root", str(root)] + list(extra))
    out = Path(extra[extra.index("--out") + 1]) if "--out" in extra else root
    with open(out / idx.CSV_NAME, encoding="utf-8", newline="") as h:
        rows = list(csv.DictReader(h))
    record = json.loads((out / idx.JSON_NAME).read_text(encoding="utf-8"))
    return code, rows, record


def _by_run(rows):
    return dict((r["run_id"], r) for r in rows)


def _snapshot(root):
    return dict((p.relative_to(root).as_posix(),
                 (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()))
                for p in sorted(root.rglob("*")) if p.is_file())


# --- gates ---------------------------------------------------------------------

def test_gate1_eight_rows_i_skipped_g_not_a_row(tree):
    root, runs = tree
    code, rows, record = _index(root)
    assert len(rows) == 8
    assert sorted(r["run_dir_name"] for r in rows) == sorted(
        runs[k].name for k in ("A", "B", "C", "D", "E", "F", "H1", "H2"))
    assert not any("DECOY" in r["run_id"] for r in rows)
    skipped = dict((s["path_rel"], s["reason"]) for s in record["scan"]["skipped"])
    i_rel = runs["I"].relative_to(root).as_posix()
    assert skipped[i_rel].startswith("run_meta.json unreadable: JSONDecodeError")
    for reserved in ("analysis_grid", "color_id_buffer"):
        assert "reserved folder name" in skipped["KSRF/Model_G/" + reserved]
    assert record["scan"]["run_folders_found"] == 9      # 8 rows + I
    assert record["scan"]["run_folders_indexed"] == 8
    assert record["scan"]["max_depth_reached"] == 3
    assert code == 1                                      # C is not finalized


def test_gate2_rollup_location(tree):
    root, _runs = tree
    rows = _by_run(_index(root)[1])
    a, b = rows["20261006T084152"], rows["20261006T090000"]
    assert a["rollup_location"] == "top" and a["kinds_location"] == "top"
    assert b["rollup_location"] == "analysis_grid" and b["kinds_location"] == "analysis_grid"
    assert b["rollup_summary_path_rel"] == (
        "KSRF/Model_B/2025-04-05__20261006T090000/analysis_grid/" + idx.ROLLUP_SUMMARY)
    # Control: a clean run's facts are all present, so D's blanks below mean absence.
    for r in (a, b):
        assert r["rollup_state"] == "value" and r["rollup_reason"] == ""
        assert r["rollup_csv_sha256_match"] == "true"
        assert r["rollup_schema"] == rollup.SCHEMA
        assert r["rollup_tool_version"] == rollup.TOOL_VERSION
        assert r["rollup_refused_runs_count"] == "0"
        assert r["rollup_count_invariant_difference"] == "0"
        assert json.loads(r["rollup_row_status_counts"])["no_stage_a_capture"] == 3
        assert json.loads(r["rollup_run_ids"]) == [r["run_id"]]
        assert r["chk_rollup_run_id_eq_run_meta"] == "true"
        assert r["kinds_state"] == "value" and r["kinds_csv_sha256_match"] == "true"
        assert r["kinds_schema"] == kinds.SCHEMA_INVENTORY
        assert r["chk_kinds_class_map_eq_rollup"] == "true"
        assert r["kinds_grid_used"] == "0" and r["kinds_grid_denominator"] == "0"


def test_gate3_absent_rollup_has_empty_cells(tree):
    root, _runs = tree
    d = _by_run(_index(root)[1])["20261006T092000"]
    assert d["rollup_state"] == "absent" and d["rollup_reason"]
    assert d["rollup_location"] == "" and d["rollup_csv_path_rel"] == ""
    for c in ROLLUP_COUNT_COLUMNS:
        assert d[c] == "", c
    assert d["chk_rollup_run_id_eq_run_meta"] == ""
    assert d["kinds_state"] == "absent" and d["chk_kinds_class_map_eq_rollup"] == ""


def test_gate4_tampered_rollup_csv(tree):
    root, _runs = tree
    e = _by_run(_index(root)[1])["20261006T093000"]
    assert e["rollup_state"] == "value"
    assert e["rollup_csv_sha256_match"] == "false"


def test_gate5_folder_date_differs(tree):
    root, _runs = tree
    rows = _by_run(_index(root)[1])
    f = rows["20261006T094000"]
    assert f["chk_folder_date_eq_as_of"] == "false"
    assert f["as_of_date"] == "2025-04-08"                 # run_meta, not the folder
    assert f["chk_folder_run_id_eq_run_meta"] == "true"
    assert rows["20261006T084152"]["chk_folder_date_eq_as_of"] == "true"   # control


def test_gate6_same_model_as_of(tree):
    root, _runs = tree
    rows = _by_run(_index(root)[1])
    for rid in ("20261006T100000", "20261006T110000_PR_221"):
        assert rows[rid]["n_runs_same_model_as_of"] == "2"
        assert rows[rid]["model_key"] == "Proj/Model_H"
    assert rows["20261006T084152"]["n_runs_same_model_as_of"] == "1"      # control


def test_gate7_deterministic(tree):
    root, _runs = tree
    _index(root)
    first_csv = (root / idx.CSV_NAME).read_bytes()
    first_json = json.loads((root / idx.JSON_NAME).read_text())
    _index(root)
    assert (root / idx.CSV_NAME).read_bytes() == first_csv
    second_json = json.loads((root / idx.JSON_NAME).read_text())
    first_json.pop("generated_utc")
    second_json.pop("generated_utc")
    assert first_json == second_json


def test_gate8_run_folders_untouched(tree):
    root, _runs = tree
    before = _snapshot(root)
    _index(root)
    after = _snapshot(root)
    assert set(after) - set(before) == {idx.CSV_NAME, idx.JSON_NAME}   # nothing else, no temp
    for rel, fact in before.items():
        assert after[rel] == fact, rel


# --- the contract beyond the gates -----------------------------------------------

def test_rows_sorted_every_cell_a_string_and_json_written_last(tree):
    root, _runs = tree
    _code, rows, record = _index(root)
    keys = [(r["model_key"], r["as_of_date"], r["run_id"]) for r in rows]
    assert keys == sorted(keys)
    with open(root / idx.CSV_NAME, encoding="utf-8", newline="") as h:
        header = next(csv.reader(h))
    assert header == idx.COLUMNS
    assert record["schema"] == "vop.stage_a.runs_index.v1"
    assert record["csv_sha256"] == hashlib.sha256((root / idx.CSV_NAME).read_bytes()).hexdigest()
    assert len(record["rows"]) == len(rows)
    a = [r for r in record["rows"] if r["run_id"] == "20261006T084152"][0]
    assert a["capture_status_counts"] == {"failed": 2, "success": 1}   # a real object
    assert a["rollup_run_ids"] == ["20261006T084152"]
    d = [r for r in record["rows"] if r["run_id"] == "20261006T092000"][0]
    assert d["rollup_row_status_counts"] is None
    assert (root / idx.CSV_NAME).stat().st_mtime_ns <= (root / idx.JSON_NAME).stat().st_mtime_ns


def test_identity_and_dated_files(tree):
    root, runs = tree
    rows = _by_run(_index(root)[1])
    a = rows["20261006T084152"]
    assert a["model_key"] == "KSRF/Model_A"
    assert a["run_dir_rel"] == "KSRF/Model_A/2025-04-05__20261006T084152"
    assert a["finalized"] == "true" and rows["20261006T091000"]["finalized"] == "false"
    assert a["revit_version_number"] == "2024" and a["config_hash"] == "abcd1234"
    assert a["views_requested_count"] == "3" and a["views_count"] == "3"
    assert a["views_core_state"] == "value" and a["views_core_rows"] == "2"
    assert a["views_core_path_rel"].endswith("/views_core_2025-04-05.csv")
    assert a["element_map_state"] == "value" and a["diagnostics_state"] == "value"
    assert a["run_tag"] == "" and a["doc_title"] == "Doc KSRF/Model_A"   # None is empty
    h2 = rows["20261006T110000_PR_221"]
    assert h2["chk_folder_run_id_eq_run_meta"] == "true"
    assert h2["n_config_hash_in_model"] == "1"


def test_top_level_wins_over_analysis_grid(tree):
    """Both locations hold outputs: the run's top level is taken first."""
    root, runs = tree
    rollup.rollup([rollup.run_layout(runs["A"])], runs["A"] / "analysis_grid")
    a = _by_run(_index(root)[1])["20261006T084152"]
    assert a["rollup_location"] == "top"
    assert a["rollup_summary_path_rel"] == (
        "KSRF/Model_A/2025-04-05__20261006T084152/" + idx.ROLLUP_SUMMARY)


def test_ambiguous_dated_file(tree):
    root, runs = tree
    (runs["A"] / "views_core_PR_221.csv").write_text("RunId\n")
    a = _by_run(_index(root)[1])["20261006T084152"]
    assert a["views_core_state"] == "ambiguous"
    assert "views_core_2025-04-05.csv" in a["views_core_reason"]
    assert a["views_core_path_rel"] == "" and a["views_core_rows"] == ""


def test_bad_folder_name_is_skipped_with_reason(tmp_path):
    root = tmp_path / "r"
    run = make_run(root, "P/M", "2025-01-01", "X1")
    run.rename(run.parent / "not_a_run_name")
    make_run(root, "P/M", "2025-01-02", "X2")
    code, rows, record = _index(root)
    assert [r["run_id"] for r in rows] == ["X2"]
    assert any(s["path_rel"] == "P/M/not_a_run_name" and "is not" in s["reason"]
               for s in record["scan"]["skipped"])
    assert code == 0


def test_depth_cap_reports_what_it_did_not_search(tmp_path):
    root = tmp_path / "r"
    make_run(root, "a/b/c/d", "2025-01-01", "DEEP")          # run folder at depth 5
    make_run(root, "P/M", "2025-01-01", "SHALLOW")
    code, rows, record = _index(root)
    assert [r["run_id"] for r in rows] == ["SHALLOW"]
    assert any(s["path_rel"] == "a/b/c/d/2025-01-01__DEEP" and "--max-depth 4" in s["reason"]
               for s in record["scan"]["skipped"])
    code, rows, record = _index(root, "--max-depth", "5")
    assert sorted(r["run_id"] for r in rows) == ["DEEP", "SHALLOW"]


def test_reserved_name_rule_is_what_keeps_g_out(tree, monkeypatch):
    """Mutation: without the reserved-name rule G's decoys ARE found, so the
    gate-1 fixture discriminates."""
    root, _runs = tree
    monkeypatch.setattr(idx, "RESERVED_DIRS", ())
    rows = _index(root)[1]
    assert {"DECOY_analysis_grid", "DECOY_color_id_buffer"} <= set(r["run_id"] for r in rows)


def test_exit_codes(tmp_path, tree):
    root, runs = tree
    clean = tmp_path / "clean"
    make_run(clean, "P/M", "2025-01-01", "OK1")
    assert _index(clean)[0] == 0
    empty = tmp_path / "empty"
    (empty / "nothing").mkdir(parents=True)
    assert idx.main(["--root", str(empty)]) == 2
    assert idx.main(["--root", str(tmp_path / "missing")]) == 2
    assert idx.main(["--root", str(root), "--out", str(runs["A"] / "x")]) == 2
    assert not (runs["A"] / "x").exists()
    assert idx.main(["--bogus"]) == 2


def test_cli_subprocess(tree):
    root, _runs = tree
    proc = subprocess.run([sys.executable, str(TOOL), "--root", str(root)],
                          capture_output=True, text=True)
    assert proc.returncode == 1, proc.stderr
    lines = proc.stdout.splitlines()
    assert any(line.startswith("KSRF/Model_A  2025-04-05  20261006T084152  finalized=true  "
                               "rollup=value  kinds=value") for line in lines)
    assert lines[-1].startswith("wrote ")


def test_file_names_match_the_producers():
    """The names read from the producers' sources are the producers' names."""
    assert idx.ROLLUP_CSV == rollup.CSV_NAME
    assert idx.ROLLUP_SUMMARY == rollup.SUMMARY_NAME
    assert idx.KINDS_CSV == kinds.INVENTORY_CSV
    assert idx.KINDS_JSON == kinds.INVENTORY_JSON
    assert idx.RUN_META_FILENAME == run_meta.RUN_META_FILENAME


# --- review findings (PR #231) ---------------------------------------------------

def test_out_inside_a_skipped_run_folder_is_refused(tmp_path, tree):
    """The refusal is decided on --out's own path, not on the indexed rows: I
    is skipped (unreadable run_meta) and a run beyond --max-depth is never
    seen, yet both are run folders."""
    root, runs = tree
    deep = make_run(root, "a/b/c/d", "2025-01-01", "DEEP", outputs=None)
    for target in (runs["I"] / "idx", deep / "idx", runs["I"]):
        before = _snapshot(root)
        assert idx.main(["--root", str(root), "--out", str(target)]) == 2
        assert _snapshot(root) == before
        assert not (target / idx.CSV_NAME).exists()


def _edit_json(path, **changes):
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc.update(changes)
    path.write_text(json.dumps(doc), encoding="utf-8")


def test_record_csv_name_is_never_followed_outside_its_folder(tree):
    root, runs = tree
    outside = root.parent / "outside.csv"
    outside.write_text("x\n")
    a, b = runs["A"], runs["B"] / "analysis_grid"
    _edit_json(a / idx.ROLLUP_SUMMARY, csv=str(outside))                    # absolute
    _edit_json(a / idx.KINDS_JSON, csv="../../../../outside.csv")           # traversal
    _edit_json(b / idx.ROLLUP_SUMMARY, csv="..\\..\\outside.csv")          # Windows form
    code, rows, _record = _index(root)
    rows = _by_run(rows)
    for rid, prefix in (("20261006T084152", "rollup"), ("20261006T084152", "kinds"),
                        ("20261006T090000", "rollup")):
        r = rows[rid]
        assert r[prefix + "_state"] == "value"
        assert r[prefix + "_csv_path_rel"] == ""
        assert r[prefix + "_csv_sha256"] == "" and r[prefix + "_csv_sha256_match"] == ""
        assert "not a file name beside it" in r[prefix + "_reason"]
    # control: the untouched kinds record beside B's edited summary still resolves
    assert rows["20261006T090000"]["kinds_csv_sha256_match"] == "true"


def test_non_string_rollup_run_id_does_not_stop_the_index(tree):
    root, runs = tree
    summary = json.loads((runs["A"] / idx.ROLLUP_SUMMARY).read_text())
    summary["runs"][0]["run_id"] = ["not", "a", "string"]
    (runs["A"] / idx.ROLLUP_SUMMARY).write_text(json.dumps(summary))
    code, rows, _record = _index(root)
    assert len(rows) == 8
    a = _by_run(rows)["20261006T084152"]
    assert a["rollup_state"] == "value"
    assert a["chk_rollup_run_id_eq_run_meta"] == "" and a["rollup_run_ids"] == ""
    assert "not a string" in a["rollup_reason"]
    assert _by_run(rows)["20261006T090000"]["chk_rollup_run_id_eq_run_meta"] == "true"
