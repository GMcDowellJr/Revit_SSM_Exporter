"""C2: process memory per view, in the per-view timing record.

Batch 2026-10-21: our own per-view Python work grew with the batch's
position while Revit's export did not. The samples here are what tells
session state (a heap or private bytes that climb across views and files)
from a per-view pass over a growing document (flat memory). Asserted on the
sidecar FILES (CLAUDE.md, defect class 4) and composed with the report that
reads them.
"""
import csv
import json
import time

import pytest

from vop_interwoven import memory_telemetry as mt
from tests.test_stage_a_registered_capture import _run

FIELDS = ("working_set_bytes", "private_bytes", "page_faults", "managed_heap_bytes",
          "gc_collections", "read_ms")


@pytest.fixture
def fake_process(monkeypatch):
    """Readers that answer as a Windows/.NET host would, counting their calls."""
    state = {"calls": 0, "sleep": 0.0}

    def _os():
        state["calls"] += 1
        time.sleep(state["sleep"])
        return 900 * 2 ** 20 + state["calls"], 1200 * 2 ** 20, 40 + state["calls"], "psapi"

    def _managed():
        return 300 * 2 ** 20, [10 + state["calls"], 4, 1]
    monkeypatch.setattr(mt, "_psapi_counters", _os)
    monkeypatch.setattr(mt, "_managed_counters", _managed)
    return state


def _file(path):
    with open(path) as handle:
        return json.load(handle)


def test_a_sample_reads_every_field_and_its_own_cost(fake_process):
    sample = mt.process_memory_sample()
    assert set(FIELDS) <= set(sample)
    assert sample["schema"] == mt.PROCESS_MEMORY_SCHEMA
    assert sample["os_source"] == "psapi" and "errors" not in sample
    assert sample["gc_collections"] == [11, 4, 1]
    assert isinstance(sample["read_ms"], float) and sample["read_ms"] >= 0


def test_the_dotnet_reader_is_the_fallback_and_the_reason_is_kept(monkeypatch):
    def _no_psapi():
        raise AttributeError("no windll")
    monkeypatch.setattr(mt, "_psapi_counters", _no_psapi)
    monkeypatch.setattr(mt, "_dotnet_process_counters",
                        lambda: (1, 2, None, "System.Diagnostics.Process"))
    monkeypatch.setattr(mt, "_managed_counters", lambda: (3, [0, 0, 0]))
    sample = mt.process_memory_sample()
    assert sample["os_source"] == "System.Diagnostics.Process"
    assert (sample["working_set_bytes"], sample["private_bytes"]) == (1, 2)
    assert "no windll" in sample["errors"]["psapi_counters"]


def test_nothing_readable_is_None_with_the_reasons_and_never_raises():
    """This container: no windll, no System. Every value None, every reason kept."""
    sample = mt.process_memory_sample()
    assert sample["working_set_bytes"] is None and sample["managed_heap_bytes"] is None
    assert set(sample["errors"]) == {"psapi_counters", "dotnet_process_counters",
                                     "managed_counters"}


def test_both_sidecar_FILES_carry_the_capture_samples(tmp_path, fake_process):
    out, _v, _d, _e, _diag = _run(tmp_path)
    for path in (out["sidecar_path"], out["annotation_sidecar_path"]):
        memory = _file(path)["registration_marks"]["process_memory"]
        # _run calls the capture directly: no pipeline, so no view-start sample.
        assert memory["view_start"] is None
        assert memory["capture_start"]["working_set_bytes"] < (
            memory["capture_end"]["working_set_bytes"])
        assert memory["capture_end"]["gc_collections"][0] > (
            memory["capture_start"]["gc_collections"][0])


def test_the_samples_are_outside_the_capture_total(tmp_path, fake_process):
    """Each sample costs 30 ms here; the fake capture's total is ~5 ms. A
    sample moved inside t_total would show in total."""
    fake_process["sleep"] = 0.03
    out, _v, _d, _e, _diag = _run(tmp_path)
    marks = _file(out["sidecar_path"])["registration_marks"]
    assert marks["timings_ms"]["total"] < 30.0, marks["timings_ms"]["total"]
    assert marks["process_memory"]["capture_start"]["read_ms"] >= 30.0


def test_the_pipeline_hands_its_view_start_sample_to_the_registered_capture(
        monkeypatch, tmp_path, fake_process):
    from tests.test_stage_a_annotation_pass_wiring import _run_one_view
    _m, _a, _r, registered_calls = _run_one_view(
        monkeypatch, tmp_path, annotation_pass=False, registered=True)
    [kwargs] = registered_calls
    sample = kwargs["view_start_memory"]
    assert sample["schema"] == mt.PROCESS_MEMORY_SCHEMA
    assert sample["os_source"] == "psapi"


def test_the_report_puts_the_samples_beside_the_phase_times(tmp_path, fake_process):
    """Composes the report with a sidecar the capture really wrote."""
    from tools import stage_a_timing_report as report
    from tests.test_stage_a_registered_timings import _report_run

    out, run, _vid = _report_run(tmp_path, {})
    assert report.main([str(run)]) == 0
    [csv_path] = list(run.glob("views_timing_*.csv"))
    with open(str(csv_path), newline="") as handle:
        header = next(csv.reader(handle))
        handle.seek(0)
        [row] = list(csv.DictReader(handle))
    assert header.index("mem_view_start_ws_mb") > header.index("reason")
    assert header.index("mem_read_ms") < min(i for i, h in enumerate(header)
                                             if h.startswith("phase_"))
    persisted = _file(out["sidecar_path"])["registration_marks"]["process_memory"]
    start = persisted["capture_start"]
    assert float(row["mem_capture_start_ws_mb"]) == round(
        start["working_set_bytes"] / 2.0 ** 20, 3)
    assert int(row["mem_capture_end_gc0"]) == persisted["capture_end"]["gc_collections"][0]
    assert row["mem_view_start_ws_mb"] == ""          # no sample: blank, not 0
    written = json.loads(next(run.glob("views_timing_*.json")).read_text())
    assert written["views"][0]["process_memory"] == persisted


def test_a_sidecar_before_C2_gives_blank_memory_cells():
    from tools.stage_a_timing_report import MEMORY_COLUMNS, memory_cells
    assert memory_cells(None) == dict((c, None) for c in MEMORY_COLUMNS)
