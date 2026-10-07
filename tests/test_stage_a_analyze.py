"""tools/stage_a_analyze.py: the five analysis tools, in order, in one call.

Views are captured by the roll-up harness but NOT registered or gridded, so
every product below was made by the one call. Assertions read the files the
stages wrote.
"""
import json

from tests.test_stage_a_grid_rollup import _capture, _read
from tests.test_stage_a_grid_rollup import _run as _rollup_run
from tools import stage_a_analyze as analyze
from tools import stage_a_kinds as kinds


def _run(tmp_path, name="run_a", run_id="RUN_A"):
    """The roll-up's run, plus the views_core_<date>.csv a Stage A run
    writes, so the timing stage has this run's rows to report."""
    run = _rollup_run(tmp_path, name, run_id)
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


def test_several_runs_need_an_out_and_share_one_rollup(tmp_path):
    a, b = _run(tmp_path, "run_a", "RUN_A"), _run(tmp_path, "run_b", "RUN_B")
    _capture(a, 1, register=False, gridded=False)
    _capture(b, 1, register=False, gridded=False)
    try:
        analyze.analyze([a, b])
        raise AssertionError("expected a refusal without --out")
    except ValueError as ex:
        assert "--out" in str(ex)
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
    assert [r["exit"] for r in results if r["stage"] != "timing"] == [0] * 5
    assert not list(run.glob("views_timing*"))
    by_stem, _rows, _s = _read(run / "analysis_grid")
    assert by_stem["V_1"]["row_status"] == "gridded"
