"""Batch capture across documents (vop_interwoven/batch_capture.py,
tools/plan_capture_batch.py): the plan, the resume, the view selection and
the loop, with everything that touches Revit injected.

What is asserted is the FILES the loop leaves (the durable batch log after
every job, ``opened_from`` in each run's run_meta.json), with controls on
what it must not touch.
"""
import csv
import json
import os
import types
from pathlib import Path

import pytest

from vop_interwoven import batch_capture as bc
from vop_interwoven.batch_capture import (
    BatchPlanError, archive_date, build_plan, index_runs, job_done, load_manifest,
    load_plan, model_key, pending_jobs, run_batch, select_views, title_matches,
)
from vop_interwoven.recapture import RecaptureRefused, recapture_plan
from tools import plan_capture_batch as cli

MANIFEST_HEADER = ["include", "relative_path", "full_path", "project_folder", "filename"]


def _manifest(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_HEADER)
        writer.writeheader()
        writer.writerows(rows)
    return str(path)


def _row(stem, include="1", project="KSRF"):
    return {"include": include, "relative_path": "{0}/{1}.rvt".format(project, stem),
            "full_path": "C:\\ACC\\{0}\\{1}.rvt".format(project, stem),
            "project_folder": project, "filename": stem + ".rvt"}


def _run_dir(root, title, run_id="20261008T093539", date="2025-06-21", finalized=True,
             recaptures=None, requested=(1, 2, 3)):
    run_dir = Path(root) / "{0}__{1}".format(date, run_id)
    run_dir.mkdir(parents=True)
    meta = {"schema": "vop.run_meta.v1", "run_id": run_id, "date": date, "finalized": finalized,
            "doc_title": {"state": "value", "value": title},
            "views_requested": list(requested), "views": [], "exporter_version": "1.1.0+src.x"}
    if recaptures is not None:
        meta["recaptures"] = recaptures
    (run_dir / "run_meta.json").write_text(json.dumps(meta))
    return str(run_dir)


# --- manifest, titles, dates ---------------------------------------------------------------

def test_the_manifest_keeps_included_rows_in_order_with_their_stems(tmp_path):
    path = _manifest(tmp_path / "m.csv", [_row("A_2025-06-21"), _row("B", include="0"),
                                          _row("C_2025-06-13", project="")])
    entries = load_manifest(path)
    assert [e["stem"] for e in entries] == ["A_2025-06-21", "C_2025-06-13"]
    assert entries[0]["display_name"] == "KSRF/A_2025-06-21.rvt"
    assert entries[1]["display_name"] == "C_2025-06-13.rvt" and entries[1]["project_folder"] == ""
    assert load_manifest(path, limit=1) == entries[:1]


def test_a_manifest_without_the_columns_is_refused(tmp_path):
    (tmp_path / "bad.csv").write_text("name,path\na,b\n")
    with pytest.raises(BatchPlanError, match="lacks manifest column"):
        load_manifest(str(tmp_path / "bad.csv"))


@pytest.mark.parametrize("stem, date", [
    ("KSRF_Hosp_Interior_AR_V28_2.5 GB_2025-06-21", "2025-06-21"),
    ("Model 2025-06-13", "2025-06-13"), ("Model-2025-06-13", "2025-06-13"),
    ("Model_V28", None), ("2025-06-21_Model", None), ("Model_2025-06-21_rev2", None),
])
def test_the_archive_date_is_a_trailing_iso_date_only(stem, date):
    assert archive_date(stem) == date


def test_a_title_matches_itself_or_its_detached_form_only():
    assert title_matches("KSRF_2025-06-21", "KSRF_2025-06-21")
    assert title_matches("KSRF_2025-06-21", "KSRF_2025-06-21_detached")
    assert not title_matches("KSRF_2025-06-21", "KSRF_2025-06-21 (2)")
    assert not title_matches("KSRF_2025-06-21", "ksrf_2025-06-21")
    assert not title_matches(None, "x") and not title_matches("x", None)


def test_the_recapture_plan_accepts_a_detached_title_and_still_refuses_another():
    meta = {"finalized": True, "run_id": "r", "date": "2025-06-21",
            "doc_title": {"state": "value", "value": "KSRF_2025-06-21"},
            "views_requested": [1]}
    assert recapture_plan(meta, [1], "KSRF_2025-06-21_detached")["run_id"] == "r"
    with pytest.raises(RecaptureRefused, match="captured from"):
        recapture_plan(meta, [1], "KSRF_2025-06-13_detached")


def test_the_model_key_drops_the_date_and_unsafe_characters():
    assert model_key({"stem": "KSRF_Hosp_Interior_AR_V28_2.5 GB_2025-06-21",
                      "project_folder": "KSRF"}) == "KSRF/KSRF_Hosp_Interior_AR_V28_2.5 GB"
    assert model_key({"stem": "Tower", "project_folder": "A\\B:C"}) == "A/B_C/Tower"
    assert model_key({"stem": "Tower", "project_folder": ""}) == "Tower"


# --- the run index -------------------------------------------------------------------------

def test_the_index_lists_every_run_meta_and_names_an_unreadable_one(tmp_path):
    a = _run_dir(tmp_path / "KSRF" / "M", "A_2025-06-21")
    b = _run_dir(tmp_path / "KSRF" / "M", "A_2025-06-13", run_id="20261001T084840",
                 date="2025-06-13", finalized=False)
    bad = tmp_path / "KSRF" / "N" / "2025-01-01__x"
    bad.mkdir(parents=True)
    (bad / "run_meta.json").write_text("{not json")
    (tmp_path / "KSRF" / "M" / "2025-06-21__20261008T093539" / "nested").mkdir()
    runs = index_runs(str(tmp_path))
    assert [r["run_dir"] for r in runs] == sorted([a, b, str(bad)])
    by = dict((r["run_dir"], r) for r in runs)
    assert by[a]["doc_title"] == "A_2025-06-21" and by[a]["finalized"] is True
    assert by[b]["finalized"] is False
    assert "error" in by[str(bad)] and "Error" in by[str(bad)]["error"]
    assert index_runs(str(tmp_path / "absent")) == []


# --- the plan ------------------------------------------------------------------------------

def _entries(*stems):
    return [dict(relative_path="KSRF/{0}.rvt".format(s), full_path="C:\\ACC\\{0}.rvt".format(s),
                 filename=s + ".rvt", project_folder="KSRF", stem=s,
                 display_name="KSRF/{0}.rvt".format(s)) for s in stems]


def test_the_plan_gives_recapture_jobs_by_recorded_title_and_capture_jobs_to_the_rest():
    runs = [{"run_dir": "E/KSRF/M/2025-06-21__r1", "run_id": "r1", "recapture": [3939979, 7424270]},
            {"run_dir": "E/KSRF/M/2025-06-13__r0", "run_id": "r0", "recapture": []},
            {"run_dir": "E/KSRF/M/2025-05-01__r9", "run_id": "r9", "recapture": [5]}]
    index = [{"run_dir": "E/KSRF/M/2025-06-21__r1", "doc_title": "A_2025-06-21", "finalized": True},
             {"run_dir": "E/KSRF/M/2025-06-13__r0", "doc_title": "A_2025-06-13", "finalized": True},
             {"run_dir": "E/KSRF/M/2025-05-01__r9", "doc_title": "Z_2025-05-01", "finalized": True}]
    plan = build_plan(_entries("A_2025-06-21", "A_2025-06-13", "B_2025-07-04"), "E",
                      recapture_runs=runs, run_index=index)
    kinds = dict((j["stem"], j["kind"]) for j in plan["jobs"])
    assert kinds == {"A_2025-06-21": "recapture", "B_2025-07-04": "capture"}
    rec = plan["jobs"][0]
    assert rec["recapture"] == [{"run_dir": "E/KSRF/M/2025-06-21__r1", "run_id": "r1",
                                 "doc_title": "A_2025-06-21", "view_ids": [3939979, 7424270]}]
    cap = plan["jobs"][1]
    assert cap["output_dir"] == os.path.join("E", "KSRF", "B")
    assert cap["date_override"] == "2025-07-04" and cap["date_source"] == "archive_name"
    assert cap["view_types"] == list(bc.DEFAULT_VIEW_TYPES)
    # A_2025-06-13: captured already (finalized run, nothing to recapture).
    assert plan["skipped"] == [{"key": "KSRF/A_2025-06-13.rvt",
                                "reason": "already captured: E/KSRF/M/2025-06-13__r0"}]
    # r9 names views but no manifest file is its archive.
    assert plan["unmatched_runs"] == [{"run_dir": "E/KSRF/M/2025-05-01__r9", "run_id": "r9",
                                       "view_ids": [5], "doc_title": "Z_2025-05-01"}]
    assert plan["schema"] == bc.PLAN_SCHEMA and plan["mode"] == "both"


def test_plan_modes_and_the_skip_captured_switch():
    index = [{"run_dir": "E/r", "doc_title": "A", "finalized": True}]
    runs = [{"run_dir": "E/r", "recapture": [1]}]
    only = build_plan(_entries("A", "B"), "E", recapture_runs=runs, run_index=index,
                      mode="recapture")
    assert [j["kind"] for j in only["jobs"]] == ["recapture"]
    assert only["skipped"] == [{"key": "KSRF/B.rvt", "reason": "no run to recapture"}]
    cap = build_plan(_entries("A", "B"), "E", recapture_runs=runs, run_index=index,
                     mode="capture", skip_captured=False)
    assert [j["kind"] for j in cap["jobs"]] == ["capture", "capture"]
    assert cap["jobs"][0]["date_override"] is None
    assert cap["jobs"][0]["date_source"] == "capture_day"
    with pytest.raises(BatchPlanError):
        build_plan([], "E", mode="later")


def test_a_run_in_the_lists_but_not_in_the_index_matches_nothing():
    plan = build_plan(_entries("A"), "E", recapture_runs=[{"run_dir": "E/r", "recapture": [1]}],
                      run_index=[])
    assert [j["kind"] for j in plan["jobs"]] == ["capture"]
    assert plan["unmatched_runs"][0]["doc_title"] is None


def test_a_saved_plan_loads_and_a_foreign_file_is_refused(tmp_path):
    plan = build_plan(_entries("A"), str(tmp_path))
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    assert load_plan(str(tmp_path / "plan.json"))["jobs"][0]["kind"] == "capture"
    (tmp_path / "other.json").write_text(json.dumps({"runs": []}))
    with pytest.raises(BatchPlanError, match="not a"):
        load_plan(str(tmp_path / "other.json"))
    plan["jobs"][0]["kind"] = "audit"
    (tmp_path / "plan2.json").write_text(json.dumps(plan))
    with pytest.raises(BatchPlanError, match="kind"):
        load_plan(str(tmp_path / "plan2.json"))


# --- resume --------------------------------------------------------------------------------

def test_a_capture_job_is_done_when_a_finalized_run_under_its_output_dir_was_captured_from_it(tmp_path):
    job = {"kind": "capture", "stem": "A_2025-06-21", "output_dir": str(tmp_path / "KSRF" / "A")}
    assert job_done(job) == (False, None)
    _run_dir(tmp_path / "KSRF" / "A", "A_2025-06-21", finalized=False)
    assert job_done(job) == (False, None)                       # unfinalized: not done
    _run_dir(tmp_path / "KSRF" / "A", "A_2025-06-21_detached", run_id="20261009T000000")
    done, detail = job_done(job)
    assert done and detail.endswith("20261009T000000")


def test_a_recapture_job_is_done_when_every_run_lists_a_covering_recapture(tmp_path):
    a = _run_dir(tmp_path, "A", run_id="r1", recaptures=[{"view_ids": [1, 2, 3]}])
    b = _run_dir(tmp_path, "A", run_id="r2", date="2025-06-13", recaptures=[{"view_ids": [1]}])
    job = {"kind": "recapture", "stem": "A",
           "recapture": [{"run_dir": a, "view_ids": [1, 2]}, {"run_dir": b, "view_ids": [1, 2]}]}
    assert job_done(job) == (False, None)                       # r2 covers 1 only
    job["recapture"][1]["view_ids"] = [1]
    assert job_done(job) == (True, "recaptured already")
    job["recapture"].append({"run_dir": str(tmp_path / "gone"), "view_ids": [1]})
    assert job_done(job) == (False, None)


def test_pending_jobs_splits_and_limits(tmp_path):
    a = _run_dir(tmp_path, "A", recaptures=[{"view_ids": [1]}])
    plan = {"jobs": [{"kind": "recapture", "key": "a", "stem": "A",
                      "recapture": [{"run_dir": a, "view_ids": [1]}]},
                     {"kind": "capture", "key": "b", "stem": "B", "output_dir": str(tmp_path / "B")},
                     {"kind": "capture", "key": "c", "stem": "C", "output_dir": str(tmp_path / "C")}]}
    pending, done = pending_jobs(plan)
    assert [j["key"] for j in pending] == ["b", "c"] and [j["key"] for j in done] == ["a"]
    assert done[0]["done_detail"] == "recaptured already"
    assert [j["key"] for j in pending_jobs(plan, limit=1)[0]] == ["b"]


# --- views ---------------------------------------------------------------------------------

def _view(vt, template=False, vid=1):
    return types.SimpleNamespace(IsTemplate=template, ViewType=types.SimpleNamespace(
        ToString=lambda: vt), Id=types.SimpleNamespace(Value=vid, IntegerValue=vid))


def test_select_views_keeps_the_types_asked_for_and_records_the_rest():
    views = [_view("FloorPlan"), _view("FloorPlan", template=True), _view("ThreeD"),
             _view("Section"), _view("Schedule"), _view("Legend")]
    kept, record = select_views(views, ("FloorPlan", "Section"))
    assert [v.ViewType.ToString() for v in kept] == ["FloorPlan", "Section"]
    assert record == {"rule": "every non-template view whose ViewType is in view_types",
                      "view_types": ["FloorPlan", "Section"], "kept": {"FloorPlan": 1, "Section": 1},
                      "excluded": {"ThreeD": 1, "Schedule": 1, "Legend": 1},
                      "templates": 1, "unreadable": 0, "count": 2}


def test_the_default_types_are_the_06_21_population_without_the_rejected_3d():
    assert "ThreeD" not in bc.DEFAULT_VIEW_TYPES
    for vt in ("FloorPlan", "CeilingPlan", "AreaPlan", "Section", "Elevation",
               "DraftingView", "Legend"):
        assert vt in bc.DEFAULT_VIEW_TYPES


def test_a_view_whose_type_cannot_be_read_is_counted_not_dropped_silently():
    class _Bad:
        IsTemplate = False

        @property
        def ViewType(self):
            raise RuntimeError("no type")
    kept, record = select_views([_Bad(), _view("FloorPlan")])
    assert len(kept) == 1 and record["unreadable"] == 1


# --- the loop -------------------------------------------------------------------------------

def _doc(title):
    return types.SimpleNamespace(Title=title, PathName="C:\\ACC\\" + title + ".rvt")


def _harness(tmp_path, fail_on=None, no_doc_for=None):
    """open/close/capture/recapture fakes that write what the real ones leave
    on disk: a run directory with a run_meta.json per capture, and a
    recaptures entry per recapture."""
    events = []

    def open_doc(job):
        if job["stem"] == no_doc_for:
            return None, {}
        events.append(("open", job["stem"]))
        return _doc(job["stem"] + "_detached"), {"detach": "DetachAndPreserveWorksets",
                                                 "worksets": "all", "audit": False}

    def close_doc(doc):
        events.append(("close", doc.Title))

    def capture(doc, job):
        events.append(("capture", job["stem"]))
        if job["stem"] == fail_on:
            raise RuntimeError("capture blew up")
        run_dir = _run_dir(job["output_dir"], doc.Title, run_id="2026" + str(len(events)).zfill(11))
        return {"run_dirs": [run_dir], "views_processed": 3, "view_summaries": [{"big": 1}]}

    def recapture(doc, job):
        events.append(("recapture", job["stem"]))
        dirs = []
        for target in job["recapture"]:
            meta = json.loads(Path(target["run_dir"], "run_meta.json").read_text())
            meta.setdefault("recaptures", []).append({"view_ids": target["view_ids"]})
            Path(target["run_dir"], "run_meta.json").write_text(json.dumps(meta))
            dirs.append(target["run_dir"])
        return {"run_dirs": dirs, "refused": [], "views_processed": 1}
    return events, open_doc, close_doc, capture, recapture


def test_the_loop_runs_pending_jobs_closes_the_previous_document_and_leaves_a_log(tmp_path):
    root = tmp_path / "Exports"
    old = _run_dir(root / "KSRF" / "A", "A_2025-06-21")
    plan = {"schema": bc.PLAN_SCHEMA, "mode": "both", "output_root": str(root), "jobs": [
        {"kind": "recapture", "key": "KSRF/A_2025-06-21.rvt", "stem": "A_2025-06-21",
         "full_path": "C:\\ACC\\A_2025-06-21.rvt", "display_name": "KSRF/A_2025-06-21.rvt",
         "recapture": [{"run_dir": old, "run_id": "20261008T093539", "view_ids": [1, 2]}]},
        {"kind": "capture", "key": "KSRF/B_2025-07-04.rvt", "stem": "B_2025-07-04",
         "full_path": "C:\\ACC\\B_2025-07-04.rvt", "display_name": "KSRF/B_2025-07-04.rvt",
         "output_dir": str(root / "KSRF" / "B"), "date_override": "2025-07-04"}]}
    events, open_doc, close_doc, capture, recapture = _harness(tmp_path)
    logged = []
    from datetime import datetime
    record = run_batch(plan, open_doc, close_doc, capture, recapture, log=logged.append,
                       now=datetime(2026, 10, 12, 16, 0, 0))

    assert events == [("open", "A_2025-06-21"), ("recapture", "A_2025-06-21"),
                      ("open", "B_2025-07-04"), ("close", "A_2025-06-21_detached"),
                      ("capture", "B_2025-07-04"), ("close", "B_2025-07-04_detached")]
    assert record["ok"] == 2 and record["failed"] == 0 and record["finished"] is True
    assert record["last_document_closed"] is True
    log_path = root / "batch_capture_20261012T160000.json"
    assert record["log_path"] == str(log_path)
    on_disk = json.loads(log_path.read_text())
    assert [j["status"] for j in on_disk["jobs"]] == ["ok", "ok"]
    assert on_disk["jobs"][0]["opened"]["title"] == "A_2025-06-21_detached"
    assert on_disk["jobs"][0]["opened"]["worksets"] == "all"
    assert "view_summaries" not in on_disk["jobs"][1]["result"]
    # Each run the jobs wrote says how its document was opened.
    old_meta = json.loads(Path(old, "run_meta.json").read_text())
    assert old_meta["opened_from"]["manifest_key"] == "KSRF/A_2025-06-21.rvt"
    assert old_meta["opened_from"]["detach"] == "DetachAndPreserveWorksets"
    (new_dir,) = on_disk["jobs"][1]["run_dirs"]
    assert json.loads(Path(new_dir, "run_meta.json").read_text())["opened_from"]["title"] == (
        "B_2025-07-04_detached")
    assert logged[0].startswith("[1/2] KSRF/A_2025-06-21.rvt (recapture)")
    # Running again: both jobs are done, nothing opens, a second log says so.
    events[:] = []
    again = run_batch(plan, open_doc, close_doc, capture, recapture, log=logged.append,
                      now=datetime(2026, 10, 12, 16, 5, 0))
    assert events == [] and again["ok"] == 0
    assert [d["key"] for d in again["already_done"]] == ["KSRF/A_2025-06-21.rvt",
                                                         "KSRF/B_2025-07-04.rvt"]


def test_a_failed_job_is_logged_with_its_traceback_and_the_batch_goes_on(tmp_path):
    root = tmp_path / "Exports"
    plan = {"schema": bc.PLAN_SCHEMA, "mode": "capture", "output_root": str(root), "jobs": [
        {"kind": "capture", "key": k, "stem": s, "full_path": "C:\\" + s, "display_name": s,
         "output_dir": str(root / s), "date_override": None}
        for k, s in (("a", "A"), ("b", "B"), ("c", "C"))]}
    events, open_doc, close_doc, capture, recapture = _harness(tmp_path, fail_on="B",
                                                               no_doc_for="C")
    record = run_batch(plan, open_doc, close_doc, capture, recapture, log=lambda s: None,
                       write_log=False)
    assert [j["status"] for j in record["jobs"]] == ["ok", "failed", "failed"]
    assert "capture blew up" in record["jobs"][1]["error"]
    assert "Traceback" in record["jobs"][1]["traceback"]
    assert record["jobs"][2]["error"].startswith("RuntimeError: open_doc returned no document")
    assert record["ok"] == 1 and record["failed"] == 2 and record["log_path"] is None
    # The failed job's document stays the "previous" one and is closed by the next open.
    assert ("close", "A_detached") in events and ("close", "B_detached") in events
    assert not list(root.glob("batch_capture_*.json"))


def test_the_log_is_on_disk_after_the_first_job_not_only_at_the_end(tmp_path):
    """An interrupted batch must leave what it did (CLAUDE.md, defect class 4)."""
    root = tmp_path / "Exports"
    plan = {"schema": bc.PLAN_SCHEMA, "mode": "capture", "output_root": str(root), "jobs": [
        {"kind": "capture", "key": k, "stem": k, "full_path": "C:\\" + k, "display_name": k,
         "output_dir": str(root / k), "date_override": None} for k in ("A", "B")]}
    events, open_doc, close_doc, capture, recapture = _harness(tmp_path)
    seen = {}

    def capture_then_die(doc, job):
        if job["stem"] == "B":
            logs = list(root.glob("batch_capture_*.json"))
            seen["after_first"] = json.loads(logs[0].read_text()) if logs else None
            raise KeyboardInterrupt()
        return capture(doc, job)
    with pytest.raises(KeyboardInterrupt):
        run_batch(plan, open_doc, close_doc, capture_then_die, recapture, log=lambda s: None)
    assert seen["after_first"]["finished"] is False
    assert [j["status"] for j in seen["after_first"]["jobs"]] == ["ok"]


def test_a_limit_runs_that_many_pending_jobs(tmp_path):
    root = tmp_path / "Exports"
    plan = {"schema": bc.PLAN_SCHEMA, "mode": "capture", "output_root": str(root), "jobs": [
        {"kind": "capture", "key": k, "stem": k, "full_path": "C:\\" + k, "display_name": k,
         "output_dir": str(root / k), "date_override": None} for k in ("A", "B", "C")]}
    events, open_doc, close_doc, capture, recapture = _harness(tmp_path)
    record = run_batch(plan, open_doc, close_doc, capture, recapture, limit=2,
                       log=lambda s: None, write_log=False)
    assert [j["key"] for j in record["jobs"]] == ["A", "B"]
    assert [e for e in events if e[0] == "capture"] == [("capture", "A"), ("capture", "B")]


# --- the CLI ---------------------------------------------------------------------------------

def test_the_cli_writes_a_plan_from_a_manifest_lists_and_the_runs_on_disk(tmp_path, capsys):
    root = tmp_path / "Exports"
    old = _run_dir(root / "KSRF" / "A", "A_2025-06-21")
    elsewhere = _run_dir(tmp_path / "Other", "Z_2025-01-01", run_id="20260101T000000",
                         date="2025-01-01")
    manifest = _manifest(tmp_path / "m.csv", [_row("A_2025-06-21"), _row("B_2025-07-04"),
                                              _row("A_2025-06-13")])
    lists = tmp_path / "recapture_lists.json"
    lists.write_text(json.dumps({"runs": [
        {"run_dir": old, "run_id": "20261008T093539", "recapture": [3939979]},
        {"run_dir": elsewhere, "run_id": "20260101T000000", "recapture": [7]}]}))
    _run_dir(root / "KSRF" / "A", "A_2025-06-13", run_id="20261001T084840", date="2025-06-13")
    out = tmp_path / "capture_plan.json"
    rc = cli.main([manifest, "--output-root", str(root), "--recapture-lists", str(lists),
                   "--out", str(out)])
    assert rc == 0
    plan = json.loads(out.read_text())
    assert plan["schema"] == bc.PLAN_SCHEMA
    assert [(j["stem"], j["kind"]) for j in plan["jobs"]] == [("A_2025-06-21", "recapture"),
                                                              ("B_2025-07-04", "capture")]
    assert plan["jobs"][0]["recapture"][0]["view_ids"] == [3939979]
    assert plan["skipped"][0]["key"] == "KSRF/A_2025-06-13.rvt"
    # The run outside the output root was indexed by its own directory.
    assert plan["unmatched_runs"] == [{"run_dir": elsewhere, "run_id": "20260101T000000",
                                       "view_ids": [7], "doc_title": "Z_2025-01-01"}]
    assert plan["manifest_entries"] == 3 and plan["runs_indexed"] == 3
    text = capsys.readouterr().out
    assert "UNMATCHED run 20260101T000000" in text
    assert load_plan(str(out))["mode"] == "both"


def test_the_cli_refuses_a_bad_manifest_and_writes_nothing(tmp_path, capsys):
    (tmp_path / "bad.csv").write_text("a,b\n1,2\n")
    out = tmp_path / "plan.json"
    assert cli.main([str(tmp_path / "bad.csv"), "--output-root", str(tmp_path),
                     "--out", str(out)]) == 2
    assert not out.exists() and "REFUSED" in capsys.readouterr().out


def test_the_pyrevit_button_is_thin_and_opens_detached_with_worksets_open():
    """The script body cannot run outside Revit; its contract is pinned on the
    source: it opens detached and preserving worksets, never closes them all
    (closed worksets would hide model elements from the capture), and hands
    the loop to run_batch rather than carrying one."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pyrevit",
                        "VOP.extension", "VOP.tab", "StageA.panel", "BatchCapture.pushbutton",
                        "script.py")
    src = open(path, encoding="utf-8").read()
    assert src.startswith("#! python3")
    assert "DetachAndPreserveWorksets" in src and "DetachAndDiscardWorksets" not in src
    assert "OpenAllWorksets" in src and "CloseAllWorksets" not in src
    assert "run_batch(plan, open_doc, close_doc, capture, recapture" in src
    assert "run_capture(" in src and "run_recapture(" in src
    assert "for job in" not in src
    import ast
    ast.parse(src)
