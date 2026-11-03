"""C2: per-view loop timing (vop_interwoven/loop_timing.py), through the REAL loop.

Driven by tests/stage_a_loop_fixture.py: run_vop_pipeline_streaming ->
process_document_views_streaming -> pipeline.process_document_views over a
fake document. Every assertion reads the PERSISTED run_meta (the FILE), not a
return value: the loop's last step happens after the exporter callback, so
the defect this guards against is a record written before it was finished
(CLAUDE.md, Recurring Defect Class 4).
"""
import json
import os

import pytest

from tests import stage_a_loop_fixture as fx
from tests.layout_helpers import run_meta_file
from vop_interwoven import loop_timing
from vop_interwoven.stage_a_registered_capture import TIMING_PARTITION, unaccounted_ms

ALL_LOOP_KEYS = (set(loop_timing.RECORD_KEYS) | set(loop_timing.STREAMING_STEPS)
                 | set(loop_timing.PIPELINE_STEPS))


def _views(run_dir):
    return json.loads(run_meta_file(run_dir).read_text())["views"]


def _sum_holds(record, partition):
    steps = record["steps_ms"]
    assert set(steps) <= set(partition), set(steps) - set(partition)
    total = sum(steps.values()) + record["unaccounted_ms"]
    assert record["total_ms"] == pytest.approx(total, abs=0.01)


# ---- gate 2: the capture partition is untouched -------------------------------

def test_no_loop_timing_key_is_in_the_capture_partition():
    """Turns RED if any C2 key is added to TIMING_PARTITION: those steps lie
    OUTSIDE the capture's total (pipeline 'capture' contains it), so summing
    one there would make 'unaccounted' negative and meaningless."""
    assert not set(TIMING_PARTITION) & ALL_LOOP_KEYS, set(TIMING_PARTITION) & ALL_LOOP_KEYS


def test_capture_unaccounted_ignores_keys_outside_its_partition():
    timings = {"total": 100.0, "model_pass": 40.0, "annotation_pass": 30.0}
    base = unaccounted_ms(timings)
    assert base == 30.0
    noisy = dict(timings, **dict((k, 999.0) for k in ALL_LOOP_KEYS))
    assert unaccounted_ms(noisy) == base


def test_step_timer_refuses_a_step_outside_its_partition():
    timer = loop_timing.StepTimer(loop_timing.STREAMING_STEPS)
    with pytest.raises(KeyError):
        timer.step("capture", loop_timing.now())


def test_unaccounted_is_total_minus_the_timed_steps():
    assert loop_timing.unaccounted_ms(10.0, {"a": 2.0, "b": None, "c": 9.0},
                                      ("a", "b")) == 8.0
    assert loop_timing.unaccounted_ms(None, {"a": 1.0}, ("a",)) is None


# ---- the record, persisted ---------------------------------------------------------

def test_every_view_has_a_finished_loop_record_in_the_file(monkeypatch, tmp_path):
    _result, run_dir = fx.run(monkeypatch, tmp_path)
    views = _views(run_dir)
    assert len(views) == len(fx.VIEWS)
    for v in views:
        rec = v["loop_timing"]
        assert rec["schema"] == loop_timing.SCHEMA
        _sum_holds(rec, loop_timing.STREAMING_STEPS)
        _sum_holds(rec["pipeline"], loop_timing.PIPELINE_STEPS)
        # The LAST step of the loop is in the file: finished before written.
        assert "gc_after_view" in rec["steps_ms"]
        assert "on_view_complete" in rec["steps_ms"]
        for step in ("preamble", "mode", "signature", "raster_init", "capture",
                     "diagnostics_entry", "element_map_write",
                     "views_diagnostics_write", "gc_after_run", "run_summary"):
            assert step in rec["pipeline"]["steps_ms"], step
        assert rec["loop_bottom_s"] >= rec["loop_top_s"]
        assert rec["capture_total_ms"] == 12.5
        assert "gc_ms" in rec["gc"]["after_view"]
        assert "gc_ms" in rec["pipeline"]["gc_after_run"]


def test_model_views_time_their_collection_and_drafting_views_do_not(monkeypatch, tmp_path):
    """collect_view_elements is skipped for an annotation-only Stage A view;
    its step then times only the branch, and both are recorded."""
    _result, run_dir = fx.run(monkeypatch, tmp_path)
    by_id = dict((v["view_id"], v["loop_timing"]["pipeline"]["steps_ms"])
                 for v in _views(run_dir))
    assert "collect_elements" in by_id[13521188]
    assert "collect_elements" in by_id[13521300]


def test_the_gap_between_views_is_recorded(monkeypatch, tmp_path):
    _result, run_dir = fx.run(monkeypatch, tmp_path)
    gaps = [v["loop_timing"]["gap_from_previous_ms"] for v in _views(run_dir)]
    assert gaps[0] is None
    assert all(g is not None and g >= 0.0 for g in gaps[1:])


def test_a_batched_run_carries_every_views_record_into_the_merged_run_meta(
        monkeypatch, tmp_path):
    _result, run_dir = fx.run(monkeypatch, tmp_path, batch_size=2)
    views = _views(run_dir)
    assert sorted(v["view_id"] for v in views) == sorted(v[0] for v in fx.VIEWS)
    assert all("loop_timing" in v for v in views)


def test_control_a_run_outside_stage_a_records_no_pipeline_loop_timing(monkeypatch):
    """The legacy path's results are untouched: no record is attached."""
    from vop_interwoven import pipeline
    fx.install(monkeypatch)
    cfg = fx.make_cfg(enable_color_id_buffer_stage_a=False)
    monkeypatch.setattr(pipeline, "render_model_front_to_back", lambda *a, **k: {})
    monkeypatch.setattr(pipeline, "rasterize_annotations", lambda *a, **k: {})
    monkeypatch.setattr(pipeline, "export_view_raster", lambda *a, **k: {"success": True})
    cfg.output_dir = None
    with fx.fake_revit_db():
        results = pipeline.process_document_views(
            fx.FakeDoc(), [fx.VIEWS[0][0]], cfg)
    assert results and all("loop_timing_pipeline" not in r for r in results)


def test_the_timing_report_joins_the_loop_record(monkeypatch, tmp_path):
    from tools import stage_a_timing_report as report
    _result, run_dir = fx.run(monkeypatch, tmp_path)
    rows = report.timing_rows(run_dir)
    assert len(rows) == len(fx.VIEWS)
    csv_path, _json_path = report.write_report(run_dir, rows)
    import csv
    with open(csv_path, newline="") as handle:
        out = list(csv.DictReader(handle))
    for col in report.LOOP_COLUMNS:
        assert col in out[0], col
    for row in out:
        assert row["loop_total_ms"] != ""
        assert row["pipeline_views_diagnostics_write_ms"] != ""


def test_the_timing_report_leaves_loop_columns_blank_for_an_older_run(tmp_path):
    from tools import stage_a_timing_report as report
    cells = report.loop_cells(None)
    assert set(cells) == set(report.LOOP_COLUMNS)
    assert all(v is None for v in cells.values())
