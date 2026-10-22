"""tools/stage_a_analyze.py: the analysis tools, in order, in one call.

Views are captured by the roll-up harness but NOT registered or gridded, so
every product below was made by the one call. Assertions read the files the
stages wrote.
"""
import json

from tests.test_stage_a_grid_rollup import _capture, _read
from tests.test_stage_a_grid_rollup import _run as _rollup_run
from tools import stage_a_analyze as analyze
from tools import stage_a_kinds as kinds


def _run(tmp_path, name="2026-10-07__RUN_A", run_id="RUN_A", date="2026-10-07"):
    """The roll-up's run, plus the views_core_<date>.csv a Stage A run
    writes, so the timing stage has this run's rows to report, and the as-of
    date the census needs."""
    run = _rollup_run(tmp_path, name, run_id, date=date)
    (run / "views_core_2026-10-07.csv").write_text(
        "RunId,ViewId,ViewName,ElapsedSec\n"
        "{0},1,V 1,3.0\n{0},2,V 2,4.0\n".format(run_id))
    return run


def test_one_call_registers_grids_derives_inventories_and_rolls_up(tmp_path):
    run = _run(tmp_path)
    for n in (1, 2):
        _capture(run, n, register=False, gridded=False)
    assert analyze.main([str(run)]) == 0
    cap, out = run / "color_id_buffer", run / "analysis_grid"
    for n in (1, 2):
        assert json.loads((cap / "V_{0}_anno.registered.json".format(n)).read_text())[
            "status"] == "registered"
        assert json.loads((out / "V_{0}.grid.json".format(n)).read_text())["status"] == "value"
        assert json.loads((out / "V_{0}.kinds.json".format(n)).read_text())["status"] == "value"
    assert json.loads((out / kinds.INVENTORY_JSON).read_text())["grid_records"]["used"] == 2
    by_stem, _rows, summary = _read(out)
    assert all(r["row_status"] == "gridded" and r["registration_state"] == "registered"
               and r["kinds_state"] == "value" for r in by_stem.values())
    assert summary["kinds_state"]["counts"]["value"] == 2
    timing = json.loads((run / "views_timing_2026-10-07.json").read_text())
    assert timing["run_id"] == "RUN_A"
    assert sorted(v["view_name"] for v in timing["views"]) == ["V 1", "V 2"]
    assert (run / "views_timing_2026-10-07.csv").is_file()
    census_rec = json.loads((out / "element_census_2026-10-07.summary.json").read_text())
    assert (census_rec["state"], census_rec["figures"]["views_measured"]) == ("value", 2)
    assert census_rec["csv_sha256"] == kinds.sha256_file(out / "element_census_2026-10-07.csv")


def test_a_refused_view_does_not_stop_the_others(tmp_path):
    """No ticks in V_2's annotation capture: registration refuses it, the
    grid grids it model-only, and the roll-up reports it. V_1 is the control."""
    run = _run(tmp_path)
    _capture(run, 1, register=False, gridded=False)
    _capture(run, 2, register=False, gridded=False, drop_anno_marks=True)
    code, results = analyze.analyze([run])
    assert code == 1
    assert [r["stage"] for r in results] == list(analyze.STAGES)
    by_stem, _rows, _s = _read(run / "analysis_grid")
    assert by_stem["V_1"]["registration_state"] == "registered"
    assert by_stem["V_2"]["registration_state"] == "refused"
    assert by_stem["V_2"]["row_status"] == "gridded"          # model-only, reported


def test_a_stage_that_cannot_run_stops_what_follows(tmp_path):
    empty = tmp_path / "empty_run"
    (empty / "color_id_buffer").mkdir(parents=True)
    code, results = analyze.analyze([empty])
    assert code == 2
    assert [r["stage"] for r in results] == ["register"]
    assert not (empty / "analysis_grid").exists()


def test_several_runs_without_out_each_get_their_own_and_with_out_share_one(tmp_path):
    """Greg, 2026-10-19: without --out each run is analysed as if alone."""
    a = _run(tmp_path, "2026-10-07__RUN_A", "RUN_A")
    b = _run(tmp_path, "2026-10-14__RUN_B", "RUN_B", date="2026-10-14")
    _capture(a, 1, register=False, gridded=False)
    _capture(b, 1, register=False, gridded=False)
    code, _results = analyze.analyze([a, b])
    assert code == 0
    for run, run_id in ((a, "RUN_A"), (b, "RUN_B")):
        _by, rows, _s = _read(run / "analysis_grid")
        assert [r["run_id"] for r in rows] == [run_id]
        assert (run / "analysis_grid" / kinds.INVENTORY_JSON).exists()
    out = tmp_path / "combined"
    assert analyze.main([str(a), str(b), "--out", str(out)]) == 0
    _by, rows, summary = _read(out)
    assert sorted(r["run_id"] for r in rows) == ["RUN_A", "RUN_B"]
    assert json.loads((out / kinds.INVENTORY_JSON).read_text())["grid_records"]["used"] == 2


def test_a_run_the_timing_report_cannot_read_costs_nothing_else(tmp_path):
    """No views_core: timing exits 2, but it runs last, so every other stage
    has already run and written its products. The run is the control's twin."""
    run = _run(tmp_path)
    _capture(run, 1, register=False, gridded=False)
    (run / "views_core_2026-10-07.csv").unlink()
    code, results = analyze.analyze([run])
    assert code == 2
    assert [r["stage"] for r in results] == list(analyze.STAGES)
    assert [r["exit"] for r in results if r["stage"] != "timing"] == [0] * 7
    assert not list(run.glob("views_timing*"))
    by_stem, _rows, _s = _read(run / "analysis_grid")
    assert by_stem["V_1"]["row_status"] == "gridded"


def test_a_new_run_gets_its_pair_against_the_run_analysed_before_it(tmp_path):
    """As used: one run analysed after capture, then the next. The pairs
    stage writes the new run's transitions against its predecessor, and the
    first run's no_predecessor summary (files, not return values)."""
    model = tmp_path / "Exports" / "KSRF" / "Model"
    model.mkdir(parents=True)
    a = _run(model, "2026-10-07__RUN_A", "RUN_A")
    _capture(a, 1, register=False, gridded=False)
    assert analyze.main([str(a)]) == 0
    first = json.loads((a / "analysis_grid" / "element_transitions_2026-10-07.summary.json").read_text())
    assert (first["state"], first["predecessor"]) == ("no_predecessor", None)
    b = _run(model, "2026-10-14__RUN_B", "RUN_B", date="2026-10-14")
    _capture(b, 1, register=False, gridded=False)
    code, results = analyze.analyze([b])
    assert code == 0
    assert [r["target"] for r in results if r["stage"] == "pairs"] == [str(model.resolve())]
    pair = json.loads((b / "analysis_grid" / "element_transitions_2026-10-14.summary.json").read_text())
    assert (pair["state"], pair["predecessor"]["run_id"]) == ("value", "RUN_A")
    assert pair["counts"]["persisted"] == 1
    assert pair["csv_sha256"] == kinds.sha256_file(b / "analysis_grid" / pair["csv"])


def test_index_root_rebuilds_the_runs_index_with_the_census(tmp_path):
    root = tmp_path / "Exports"
    model = root / "KSRF" / "Model"
    model.mkdir(parents=True)
    run = _run(model, "2026-10-07__RUN_A", "RUN_A")
    _capture(run, 1, register=False, gridded=False)
    code, results = analyze.analyze([run], index_root=root)
    assert code == 0 and results[-1]["stage"] == "index"
    import csv
    with open(str(root / "runs_index.csv"), encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [(r["run_id"], r["census_state"], r["transitions_state"]) for r in rows] == [
        ("RUN_A", "value", "no_predecessor")]
    code, results = analyze.analyze([run])                           # control: not asked
    assert "index" not in [r["stage"] for r in results]


def _products(root):
    """sha256 of every file under ``root``; a census summary with its
    input_stats mtimes zeroed (census freshness: a rebuilt fixture's files
    are new, so their mtimes differ while nothing else does)."""
    import hashlib
    out = {}
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        data = f.read_bytes()
        if f.name.startswith("element_census_") and f.name.endswith(".summary.json"):
            doc = json.loads(data)
            doc["input_stats"] = dict((k, [v[0], 0]) for k, v in
                                      (doc.get("input_stats") or {}).items())
            data = json.dumps(doc, sort_keys=True).encode()
        out[str(f.relative_to(root))] = hashlib.sha256(data).hexdigest()
    return out


def test_workers_give_the_serial_runs_files_exit_code_and_summary(tmp_path, capsys):
    """The same two-run fixture, rebuilt at the same path, analysed with
    --workers 1 and --workers 2: every file byte-identical, the same exit
    code, the same summary lines apart from their timings. A refused view
    (no annotation ticks) is in it, so a refusal travels through the pool."""
    import re
    import shutil

    def _fixture():
        shutil.rmtree(str(tmp_path / "Model"), ignore_errors=True)
        model = tmp_path / "Model"
        model.mkdir()
        a = _run(model, "2026-10-07__RUN_A", "RUN_A")
        for n in (1, 2):
            _capture(a, n, register=False, gridded=False, drop_anno_marks=(n == 2))
        b = _run(model, "2026-10-14__RUN_B", "RUN_B", date="2026-10-14")
        _capture(b, 1, register=False, gridded=False)
        # The capture harness stages each pair beside the runs; not products.
        for stage in model.glob("stage_*"):
            shutil.rmtree(str(stage))
        return a, b

    def _summary(text):
        lines = text.split("== summary", 1)[1].splitlines()
        return [re.sub(r"\s+\(\d+\.\d s\)$", "", line) for line in lines
                if not line.strip().startswith("total")]

    got = {}
    for workers in (1, 2):
        a, b = _fixture()
        code = analyze.main([str(a), str(b), "--workers", str(workers)])
        got[workers] = (code, _products(tmp_path / "Model"),
                        _summary(capsys.readouterr().out))
    assert got[1][0] == got[2][0] == 1
    assert got[1][1] == got[2][1] and len(got[1][1]) > 20
    assert got[1][2] == got[2][2]


def test_the_ec_readers_decompress_only_the_ec_members(tmp_path, monkeypatch):
    """Inventory, the roll-up's row_keys and the census per-view reader read
    only the element x cell rows of a grid npz, so only ``ec_*`` members are
    decompressed (np.load reads a member when indexed). The control: derive,
    under the same watch, reads the dense arrays too -- the watch sees them."""
    import numpy as np
    from tools import stage_a_element_census as census
    from tools import stage_a_grid_rollup as rollup
    run = _run(tmp_path)
    _capture(run, 1, register=False, gridded=False)
    assert analyze.analyze([run], workers=1)[0] == 0
    out = run / "analysis_grid"
    grid_json = out / "V_1.grid.json"
    read = []
    real = np.lib.npyio.NpzFile.__getitem__

    def watched(self, key):
        read.append(key)
        return real(self, key)
    monkeypatch.setattr(np.lib.npyio.NpzFile, "__getitem__", watched)
    kinds.inventory([out], kinds.load_class_map()[0])
    assert isinstance(rollup.row_keys(grid_json), list)
    census.view_census(run / "color_id_buffer" / "V_1.json", grid_json)
    assert read and all(k.startswith("ec_") for k in read), sorted(set(read))
    del read[:]
    kinds.derive_view(grid_json, kinds.load_class_map())          # control
    assert any(not k.startswith("ec_") for k in read)
