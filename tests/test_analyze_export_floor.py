"""tools/analyze_export_floor.py: the timeline and the two clocks.

The timeline is built from the same views_timing columns the report writes,
so the fixture writes a CSV through tools/stage_a_timing_report's own column
naming (``phase_<key>_ms``) rather than inventing names: a renamed phase
would leave the tool reading zeros.
"""
import csv

from tools import analyze_export_floor as aef


def _row(view_id, pf, gap=0.0, total=40000.0, model_export=500.0, anno_export=500.0,
         **phases):
    row = {"view_id": str(view_id), "reason": "", "mem_view_start_page_faults": pf,
           "loop_gap_from_previous_ms": gap, "loop_total_ms": total,
           "loop_view_start_mark_ms": 0.0, "phase_model_export_ms": model_export,
           "phase_annotation_export_ms": anno_export,
           "phase_model_pass_ms": 10000.0 + model_export,
           "phase_annotation_pass_ms": 2000.0 + anno_export}
    for k in aef.PRE_CAPTURE:
        row["pipeline_{0}_ms".format(k)] = 100.0
    for k in aef.MODEL_BEFORE_EXPORT:
        row["phase_model_{0}_ms".format(k)] = 1000.0
    row.update(phases)
    return row


def _write(path, rows):
    keys = sorted(set().union(*[r.keys() for r in rows]))
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def test_rows_are_put_in_capture_order_and_carried_rows_dropped(tmp_path):
    rows = [_row(2, pf=200), _row(1, pf=100), dict(_row(9, pf=""), reason="carried")]
    _write(tmp_path / "t.csv", rows)
    got = aef.captured_rows(str(tmp_path / "t.csv"))
    assert [r["view_id"] for r in got] == ["1", "2"]


def test_the_model_export_starts_after_the_laps_before_it(tmp_path):
    _write(tmp_path / "t.csv", [_row(1, pf=1, gap=500.0), _row(2, pf=2)])
    events = aef.export_events(aef.captured_rows(str(tmp_path / "t.csv")))
    model = [e for e in events if e["pass"] == "model"]
    pre = 100.0 * len(aef.PRE_CAPTURE) + 1000.0 * len(aef.MODEL_BEFORE_EXPORT)
    assert model[0]["t"] == 500.0 + pre
    # The second view starts where the first one's loop ended.
    assert model[1]["t"] == 500.0 + 40000.0 + pre
    anno = [e for e in events if e["pass"] == "annotation"]
    # Placed halfway through the pass's non-export time, after model_pass.
    assert anno[0]["t"] == 500.0 + 100.0 * len(aef.PRE_CAPTURE) + 10500.0 + 1000.0


def test_a_clock_driven_stall_is_found_on_the_clock_and_a_view_driven_one_is_not():
    # Clock: a stall whenever 30 s have passed since the last one ended.
    events, last_end = [], -1e9
    for i in range(400):
        t = i * 7000.0
        ms = 21000.0 if t - last_end > 30000.0 else 500.0
        if ms > aef.SLOW_MS:
            last_end = t + ms
        events.append({"t": t, "ms": ms, "pass": "model", "view_id": str(i), "parts": {}})
    aef.annotate(events)
    th, acc = aef.best_threshold(events, "since_end_s")
    assert acc == 1.0 and 23.0 <= th < 30.0
    # View: every third view stalls whatever the clock says.
    view = [{"t": i * 7000.0, "ms": 21000.0 if i % 3 == 0 else 500.0,
             "pass": "model", "view_id": str(i), "parts": {}} for i in range(400)]
    aef.annotate(view)
    _th, acc_view = aef.best_threshold(view, "since_end_s")
    assert acc_view < 0.9


def test_hazard_counts_each_event_once():
    events = aef.annotate([{"t": i * 30000.0, "ms": 20000.0 if i % 4 == 0 else 1.0,
                            "pass": "model", "view_id": str(i), "parts": {}}
                           for i in range(40)])
    rows = aef.hazard(events, "since_end_s")
    assert sum(n for _lo, _hi, n, _k in rows) == len(events) - 1


def test_main_reports_the_split_parts_when_present(tmp_path, capsys):
    rows = []
    for i in range(10):
        slow = i % 2 == 0
        rows.append(_row(i, pf=i, model_export=21500.0 if slow else 400.0, **{
            "phase_model_export.export_image_ms": 21000.0 if slow else 300.0,
            "phase_model_export.move_ms": 10.0}))
    _write(tmp_path / "t.csv", rows)
    assert aef.main([str(tmp_path / "t.csv")]) == 0
    out = capsys.readouterr().out
    assert "export_image: slow [21000.0] fast [300.0]" in out


def test_no_captured_row_is_refused(tmp_path):
    _write(tmp_path / "t.csv", [dict(_row(1, pf=1), reason="carried")])
    assert aef.main([str(tmp_path / "t.csv")]) == 2
