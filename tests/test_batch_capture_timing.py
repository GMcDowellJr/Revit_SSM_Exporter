"""C4: the batch log says where the time between runs went.

Batch 2026-10-21: 23-31 min between consecutive runs that no file recorded,
and dialog ids written once at the end with no time or job. Every clock is
injected here, so each figure is exact; asserted on the log FILE
(CLAUDE.md, defect class 4).
"""
import json
import types
from datetime import datetime, timedelta
from pathlib import Path

from vop_interwoven import batch_capture as bc
from tests.test_batch_capture import _run_dir


class _Clocks(object):
    """A monotonic clock the fakes advance by known amounts, a wall clock one
    minute further on at every read, and a memory sampler counting calls."""

    def __init__(self):
        self.t = 100.0
        self.wall_t = datetime(2026, 10, 22, 7, 0, 0)
        self.samples = 0

    def clock(self):
        return self.t

    def wall(self):
        now = self.wall_t
        self.wall_t = now + timedelta(minutes=1)
        return now

    def memory(self):
        self.samples += 1
        return {"schema": "fake", "private_bytes": self.samples}


def _plan(root, stems):
    return {"schema": bc.PLAN_SCHEMA, "mode": "capture", "output_root": str(root),
            "jobs": [{"kind": "capture", "key": s, "stem": s, "full_path": "C:\\" + s,
                      "display_name": s, "output_dir": str(root / s), "date_override": None}
                     for s in stems]}


def _fakes(c, dialog_log=None, fail_open_on=None):
    def open_doc(job):
        c.t += 2.0
        if dialog_log is not None:
            dialog_log.record("Dialog_Revit_DocWarnDialog", 1002)
        if job["stem"] == fail_open_on:
            raise IOError("not synced")
        return types.SimpleNamespace(Title=job["stem"] + "_detached",
                                     PathName=job["full_path"]), {"detach": "x"}

    def close_doc(doc):
        c.t += 0.5

    def capture(doc, job):
        c.t += 30.0
        return {"run_dirs": [_run_dir(job["output_dir"], doc.Title,
                                      run_id="20261022T07" + job["stem"])]}
    return open_doc, close_doc, capture


def _batch(tmp_path, stems=("00", "01"), **kw):
    c = _Clocks()
    dialog_log = bc.DialogLog(wall=c.wall)
    open_doc, close_doc, capture = _fakes(c, dialog_log, kw.pop("fail_open_on", None))
    record = bc.run_batch(_plan(tmp_path / "Exports", stems), open_doc, close_doc, capture,
                          None, log=lambda s: None, now=datetime(2026, 10, 22, 7, 0, 0),
                          clock=c.clock, wall=c.wall, memory_sample=c.memory,
                          dialog_log=dialog_log)
    return json.loads(Path(record["log_path"]).read_text()), dialog_log


def test_every_job_records_its_boundaries_and_their_costs_in_the_FILE(tmp_path):
    """Mutation (c): a batch entry without open_ms turns this red."""
    log, _d = _batch(tmp_path)
    first, second = log["jobs"]
    assert first["timings_ms"] == {"open_ms": 2000.0, "capture_ms": 30000.0, "stamp_ms": 0.0}
    # The first document is closed once the second is open.
    assert second["timings_ms"] == {"open_ms": 2000.0, "close_previous_ms": 500.0,
                                    "capture_ms": 30000.0, "stamp_ms": 0.0}
    assert sorted(first["memory"]) == ["after_capture", "after_open", "job_start"]
    assert sorted(second["memory"]) == ["after_capture", "after_close_previous",
                                        "after_open", "job_start"]
    assert second["memory"]["after_close_previous"]["private_bytes"] > (
        second["memory"]["after_open"]["private_bytes"])
    # Wall clock: batch start, then each job's start and end, then the finish.
    assert log["started_at"] == "2026-10-22T07:00:00"
    assert (first["started_at"], first["ended_at"]) == ("2026-10-22T07:01:00",
                                                        "2026-10-22T07:03:00")
    assert second["started_at"] == "2026-10-22T07:04:00"
    assert log["finished_at"] > second["ended_at"]
    assert log["last_close_ms"] == 500.0 and log["plan_scan_ms"] == 0.0


def test_each_dialog_carries_its_time_and_job_and_is_persisted(tmp_path):
    log, dialog_log = _batch(tmp_path)
    assert [(e["job_key"], e["dialog_id"], e["override_result"]) for e in log["dialogs"]] == [
        ("00", "Dialog_Revit_DocWarnDialog", 1002), ("01", "Dialog_Revit_DocWarnDialog", 1002)]
    assert all(e["at"].startswith("2026-10-22T07:") for e in log["dialogs"])
    assert [j["dialog_count"] for j in log["jobs"]] == [1, 1]
    assert dialog_log.job_key is None          # no job runs after the batch
    assert dialog_log.lines()[0] == "\t".join(
        [log["dialogs"][0]["at"], "00", "Dialog_Revit_DocWarnDialog", "1002"])


def test_a_dialog_between_jobs_has_no_job_key():
    dialog_log = bc.DialogLog(wall=lambda: datetime(2026, 10, 22, 7, 0, 0))
    dialog_log.record("Dialog_X")
    assert dialog_log.events == [{"at": "2026-10-22T07:00:00", "job_key": None,
                                  "dialog_id": "Dialog_X", "override_result": None}]


def test_a_failed_open_still_records_how_long_it_took(tmp_path):
    log, _d = _batch(tmp_path, stems=("00",), fail_open_on="00")
    [entry] = log["jobs"]
    assert entry["status"] == "failed"
    assert entry["timings_ms"] == {"open_ms": 2000.0}
    assert sorted(entry["memory"]) == ["after_open", "job_start"]
    assert entry["ended_at"] > entry["started_at"]


def test_the_default_memory_sampler_is_process_memory_sample(tmp_path, monkeypatch):
    from vop_interwoven import memory_telemetry
    monkeypatch.setattr(memory_telemetry, "process_memory_sample",
                        lambda: {"schema": "patched"})
    c = _Clocks()
    open_doc, close_doc, capture = _fakes(c)
    record = bc.run_batch(_plan(tmp_path, ("00",)), open_doc, close_doc, capture, None,
                          log=lambda s: None, write_log=False, clock=c.clock)
    assert record["jobs"][0]["memory"]["job_start"] == {"schema": "patched"}


# --- the close: each archive closed as soon as its job ends -------------------------

def _close_harness(tmp_path, refuse=(), fail_capture=()):
    """Fakes that log the order of events; ``refuse`` titles raise as Revit
    does for the active document."""
    events = []

    def open_doc(job):
        events.append(("open", job["stem"]))
        return types.SimpleNamespace(Title=job["stem"], PathName=job["full_path"]), {}

    def close_doc(doc):
        if doc.Title in refuse:
            events.append(("close_refused", doc.Title))
            raise RuntimeError("The active document may not be closed from the API.")
        events.append(("close", doc.Title))

    def capture(doc, job):
        events.append(("capture", job["stem"]))
        if job["stem"] in fail_capture:
            raise RuntimeError("capture blew up")
        return {"run_dirs": [_run_dir(job["output_dir"], doc.Title,
                                      run_id="20261023T07" + job["stem"])]}
    return events, open_doc, close_doc, capture


def _run_close(tmp_path, stems, **kw):
    events, open_doc, close_doc, capture = _close_harness(
        tmp_path, refuse=kw.pop("refuse", ()), fail_capture=kw.pop("fail_capture", ()))
    c = _Clocks()
    record = bc.run_batch(_plan(tmp_path / "Exports", stems), open_doc, close_doc, capture, None,
                          log=lambda s: None, clock=c.clock, wall=c.wall,
                          memory_sample=c.memory, **kw)
    return events, record, json.loads(Path(record["log_path"]).read_text())


def test_after_capture_each_archive_is_closed_before_the_next_opens(tmp_path):
    events, _r, log = _run_close(tmp_path, ("00", "01"), close_after_capture=True,
                                 session={"active_document": "Blank"})
    assert events == [("open", "00"), ("capture", "00"), ("close", "00"),
                      ("open", "01"), ("capture", "01"), ("close", "01")]
    assert [j["closed"] for j in log["jobs"]] == [True, True]
    assert all("close_ms" in j["timings_ms"] and "after_close" in j["memory"]
               for j in log["jobs"])
    assert "close_previous_ms" not in log["jobs"][1]["timings_ms"]
    assert log["last_document_closed"] is True
    assert log["close_mode"] == "after_capture"
    assert log["session"] == {"active_document": "Blank"}
    assert log["documents_left_open"] == []


def test_a_refused_close_falls_back_to_closing_once_the_next_is_open(tmp_path):
    """The 2026-10-22 failure, with the fallback: the refusal is recorded and
    the document is tried again after the next open, never silently kept."""
    events, _r, log = _run_close(tmp_path, ("00", "01"), close_after_capture=True,
                                 refuse=("00",))
    assert events[:4] == [("open", "00"), ("capture", "00"), ("close_refused", "00"),
                          ("open", "01")]
    assert events.count(("close_refused", "00")) == 2
    assert "may not be closed" in log["jobs"][0]["closed"]
    assert "may not be closed" in log["jobs"][1]["close_previous_failed"]
    assert log["jobs"][1]["closed"] is True
    # The last archive closed, but the refused one is still open, and the
    # log says so rather than letting last_document_closed stand for both.
    assert log["last_document_closed"] is True
    assert log["documents_left_open"] == ["00"]


def test_a_failed_capture_still_closes_its_archive(tmp_path):
    events, _r, log = _run_close(tmp_path, ("00",), close_after_capture=True,
                                 fail_capture=("00",))
    assert events == [("open", "00"), ("capture", "00"), ("close", "00")]
    assert log["jobs"][0]["status"] == "failed" and log["jobs"][0]["closed"] is True


def test_the_default_order_is_unchanged(tmp_path):
    events, _r, log = _run_close(tmp_path, ("00", "01"))
    assert events == [("open", "00"), ("capture", "00"), ("open", "01"), ("close", "00"),
                      ("capture", "01"), ("close", "01")]
    assert log["close_mode"] == "after_next_open"
    assert "closed" not in log["jobs"][0]


def test_the_batch_refuses_to_start_with_no_document_or_an_archive_active():
    jobs = [{"key": "KSRF/V50.rvt", "stem": "V50", "full_path": "C:\\ACC\\V50.rvt"}]
    assert "no document is open" in bc.active_document_refusal(jobs, None, None)
    # The archive itself, by its file or by its (detached) title.
    assert "is an archive of this plan" in bc.active_document_refusal(
        jobs, "anything", "c:/acc/v50.rvt")
    assert "is an archive of this plan" in bc.active_document_refusal(
        jobs, "V50_detached", "C:\\Temp\\V50_detached.rvt")
    # Any other file: the batch starts.
    assert bc.active_document_refusal(jobs, "Blank", "C:\\Temp\\Blank.rvt") is None
    assert bc.active_document_refusal([], "Blank", "") is None


def test_the_button_keeps_the_open_document_active_and_closes_after_capture():
    """Pinned on the source (the script cannot run outside Revit)."""
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pyrevit",
                        "VOP.extension", "VOP.tab", "StageA.panel", "BatchCapture.pushbutton",
                        "script.py")
    src = open(path, encoding="utf-8").read()
    main = src.split("def main():")[1]
    refusal = main.index("active_document_refusal(pending, active_title, active_path)")
    assert refusal < main.index("_confirm.Show()") < main.index("run_batch(")
    assert "close_after_capture=True, session=session" in main
    assert "parking" not in src.lower()
