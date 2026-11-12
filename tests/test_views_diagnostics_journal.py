"""views_diagnostics_<d>.json is appended per view and written ONCE (2026-11-09).

Gates (PR body): 1 the final file is the one the per-call merge wrote; 2 the
per-view write cost no longer grows with the views already written; 3 a run
stopped after view k keeps views 1..k and leaves no half-written file; 4d a
view journalled twice takes its LAST entry (this file must not union, as the
element map does).

The OLD path is still production code -- a caller with no StreamingExporter
journal takes it -- so gate 1 compares the two production paths, not a copy.
"""
import json
import os
import re
import statistics

import pytest

from tests import stage_a_loop_fixture as fx
from tests.test_view_element_map_journal import _views
from vop_interwoven import pipeline
from vop_interwoven import views_diagnostics as vd
from vop_interwoven.core.element_cache import journal_path
from vop_interwoven.views_diagnostics import (
    ViewsDiagnosticsJournal, compact_views_diagnostics, compact_views_diagnostics_file,
    read_views_diagnostics_journal)


def _diag_file(run_dir):
    (name,) = [n for n in os.listdir(run_dir)
               if n.startswith("views_diagnostics_") and n.endswith(".json")]
    return os.path.join(run_dir, name)


def _run_id(run_dir):
    return os.path.basename(run_dir).split("__", 1)[1]


def _normalised(raw, run_id):
    """The file's bytes with the two things two runs cannot share: the run id
    and each view's measured ``elapsed_sec``."""
    raw = raw.replace(run_id.encode(), b"<RUN>")
    return re.sub(rb'"elapsed_sec": [0-9.eE+-]+', b'"elapsed_sec": 0', raw)


def _old_path(monkeypatch):
    """Run as a caller with no journal: the per-call merge, as before."""
    monkeypatch.setattr(vd, "ViewsDiagnosticsJournal", lambda run_id: None)


# ---- gate 1: the final file ------------------------------------------------------

@pytest.mark.parametrize("batch_size", [None, 64])
def test_the_final_file_is_the_per_call_merges_file(monkeypatch, tmp_path, batch_size):
    """200 views: past the batch size used here (64, so a batched run has
    four batches, the last one partial) and enough entries that an ordering
    or last-wins difference between the paths would show."""
    monkeypatch.setattr(fx, "VIEWS", _views(200, shared=False))
    _r, new_dir = fx.run(monkeypatch, tmp_path / "new", batch_size=batch_size)
    with monkeypatch.context() as m:
        _old_path(m)
        _r, old_dir = fx.run(m, tmp_path / "old", batch_size=batch_size)

    def files(root):
        found = {}
        for dirpath, _d, names in os.walk(root):
            for n in names:
                if n.startswith("views_diagnostics_"):
                    found[os.path.relpath(os.path.join(dirpath, n), root)] = os.path.join(dirpath, n)
        return found
    new_files, old_files = files(new_dir), files(old_dir)
    assert sorted(new_files) == sorted(old_files) and new_files, (new_files, old_files)
    total = 0
    for rel in new_files:
        a, b = json.load(open(old_files[rel])), json.load(open(new_files[rel]))
        for p in (a, b):
            for v in p["views"].values():
                v["elapsed_sec"] = 0
        assert a["metadata"].pop("run_id") == a["metadata"].pop("exporter_run_id") == _run_id(old_dir)
        assert b["metadata"].pop("run_id") == b["metadata"].pop("exporter_run_id") == _run_id(new_dir)
        assert a == b
        assert list(a["views"]) == list(b["views"])          # key order too
        assert _normalised(open(old_files[rel], "rb").read(), _run_id(old_dir)) == \
            _normalised(open(new_files[rel], "rb").read(), _run_id(new_dir))
        total += len(b["views"])
    assert total == 200
    assert not [n for d, _s, fs in os.walk(new_dir) for n in fs
                if n.endswith(".jsonl") or n.endswith(".tmp")]


def _per_view_calls(monkeypatch, out_dir, view_ids, run_id, journal):
    """process_document_views once per view, as streaming calls it, with the
    exporter's run id and (new path) its journal."""
    fx.install(monkeypatch)
    cfg = fx.make_cfg()
    cfg.output_dir = str(out_dir)
    cfg.date_override = "2026-11-09"
    cfg._view_element_map_run_id = run_id
    cfg._views_diagnostics_journal = journal
    with fx.fake_revit_db():
        doc = fx.FakeDoc()
        for vid in view_ids:
            pipeline.process_document_views(doc, [vid], cfg, reset_family_caches=False)
    if journal is not None:
        return journal.finalize()
    return None


def test_a_recapture_of_three_views_merges_into_the_runs_file_as_before(monkeypatch, tmp_path):
    """An existing run's file, stamped with its id; three views recaptured
    under the same id -- one of them already in the file -- the journal
    against three per-view merge calls, from the same starting file."""
    monkeypatch.setattr(fx, "VIEWS", _views(12, shared=False))
    ids = [v[0] for v in fx.VIEWS]
    run_id = "20261109T1200"
    base = {"metadata": {"date": "2026-11-09", "run_id": run_id, "exporter_run_id": run_id,
                         "doc_title": "Synthetic.rvt", "doc_path": "x",
                         "exporter_version": "vop_interwoven"},
            "views": {str(v): {"view_id": v, "old": True} for v in ids[:10]}}
    recaptured = [ids[3], ids[10], ids[11]]
    paths = {}
    for which in ("old", "new"):
        d = tmp_path / which
        d.mkdir()
        p = str(d / "views_diagnostics_2026-11-09.json")
        with open(p, "w") as f:
            json.dump(base, f, indent=2)
        paths[which] = p
    _per_view_calls(monkeypatch, tmp_path / "old", recaptured, run_id, None)
    report = _per_view_calls(monkeypatch, tmp_path / "new", recaptured, run_id,
                             ViewsDiagnosticsJournal(run_id))
    assert report[paths["new"]]["ok"] is True
    a, b = json.load(open(paths["old"])), json.load(open(paths["new"]))
    for p in (a, b):
        for v in p["views"].values():
            v.pop("elapsed_sec", None)
    assert a == b and list(a["views"]) == list(b["views"])
    assert _normalised(open(paths["old"], "rb").read(), run_id) == \
        _normalised(open(paths["new"], "rb").read(), run_id)
    assert list(b["views"]) == [str(v) for v in ids]     # recaptured id 3 keeps its place
    assert "old" not in b["views"][str(ids[3])]           # ... and its entry is replaced
    assert b["views"][str(ids[0])] == {"view_id": ids[0], "old": True}
    assert sorted(os.listdir(str(tmp_path / "new"))) == sorted(os.listdir(str(tmp_path / "old")))


def test_control_another_runs_file_is_still_replaced(tmp_path):
    p = str(tmp_path / "views_diagnostics_2026-11-09.json")
    with open(p, "w") as f:
        json.dump({"metadata": {"exporter_run_id": "OLD"}, "views": {"1": {"view_id": 1}}}, f)
    journal = ViewsDiagnosticsJournal("NEW")
    journal.append(p, {"exporter_run_id": "NEW"}, {"2": {"view_id": 2}})
    assert journal.finalize()[p]["ok"] is True
    assert json.load(open(p)) == {"metadata": {"exporter_run_id": "NEW"},
                                  "views": {"2": {"view_id": 2}}}


def test_a_call_with_no_entry_still_writes_the_file_as_before(tmp_path):
    p = str(tmp_path / "views_diagnostics_2026-11-09.json")
    journal = ViewsDiagnosticsJournal("R")
    journal.append(p, {"exporter_run_id": "R", "n": 1}, {})
    assert journal.views_appended == 0
    journal.finalize()
    assert json.load(open(p)) == {"metadata": {"exporter_run_id": "R", "n": 1}, "views": {}}


def test_finalize_detaches_the_journal_from_the_config(tmp_path):
    from vop_interwoven.config import Config
    from vop_interwoven.streaming import StreamingExporter
    cfg = Config()
    exp = StreamingExporter(str(tmp_path), cfg, doc=None, export_png=False,
                            export_csv=False, run_id="RUN_X")
    assert cfg._views_diagnostics_journal.run_id == "RUN_X"
    out = exp.finalize()
    assert cfg._views_diagnostics_journal is None
    assert out["views_diagnostics"]["views_appended"] == 0


# ---- gate 2: cost no longer grows -----------------------------------------------

def test_the_per_view_diagnostics_write_does_not_grow_with_the_run(monkeypatch, tmp_path):
    """1,610 views through the real loop; the per-view write (C2's
    pipeline.steps_ms.views_diagnostics_write, timed around the call site)
    at views 1,591-1,610 against views 41-60, medians of 20 for noise. 2x is
    loose on purpose (filesystem noise); the per-call rewrite grew ~25x over
    the same span here (D1). 1,610: the 03-16 run had 1,650 views."""
    monkeypatch.setattr(fx, "VIEWS", _views(1610, ids_per_view=100))
    _r, run_dir = fx.run(monkeypatch, tmp_path)
    from tests.layout_helpers import run_meta_file
    views = json.loads(run_meta_file(run_dir).read_text())["views"]
    ms = [v["loop_timing"]["pipeline"]["steps_ms"]["views_diagnostics_write"] for v in views]
    early, late = statistics.median(ms[40:60]), statistics.median(ms[1590:1610])
    assert late <= 2.0 * max(early, 0.05), (early, late)
    assert len(json.load(open(_diag_file(run_dir)))["views"]) == 1610


# ---- gate 3: crash state ---------------------------------------------------------

class _Crash(BaseException):
    """Not an Exception: the loop's per-view handler does not catch it, as a
    Revit crash or a cancelled script is not caught."""


def test_a_crash_after_view_k_keeps_views_1_to_k(monkeypatch, tmp_path):
    k = 7
    monkeypatch.setattr(fx, "VIEWS", _views(12, shared=False))
    real = fx.fake_registered_capture
    seen = []

    def _capture(doc, view, *a, **kw):
        if len(seen) == k:
            raise _Crash("Revit went away")
        seen.append(view.Id.IntegerValue)
        return real(doc, view, *a, **kw)
    monkeypatch.setattr(fx, "fake_registered_capture", _capture)
    with pytest.raises(_Crash):
        fx.run(monkeypatch, tmp_path)
    (run_dir,) = [os.path.join(str(tmp_path), d) for d in os.listdir(str(tmp_path))
                  if "__" in d]
    run_id = _run_id(run_dir)
    files = os.listdir(run_dir)
    assert not [n for n in files if n.endswith(".tmp")]
    assert not [n for n in files if n.startswith("views_diagnostics_") and n.endswith(".json")]
    (jname,) = [n for n in files if n.startswith("views_diagnostics_")
                and n.endswith(".journal.jsonl")]
    jpath = os.path.join(run_dir, jname)
    # (a) the journal holds views 1..k, and only those
    lines, report = read_views_diagnostics_journal(jpath, run_id)
    assert report["torn_tail"] == 0 and [int(v) for v, _e, _m in lines] == seen
    # (b) a torn final line (the view being appended when it stopped) is
    # dropped and counted, not raised
    with open(jpath, "a") as f:
        f.write('{"schema":"vop.views_diagnostics_journal.v1","run_id":"%s","view_id":"9' % run_id)
    lines, report = read_views_diagnostics_journal(jpath, run_id)
    assert report["torn_tail"] == 1 and len(lines) == k
    # (c) the standalone compaction, with no exporter, gives views 1..k and
    # leaves no partial file and no journal behind
    out = compact_views_diagnostics(run_dir, run_id)
    (diag_path,) = out
    assert out[diag_path]["ok"] is True and out[diag_path]["torn_tail"] == 1
    assert [int(v) for v in json.load(open(diag_path))["views"]] == seen
    assert json.load(open(diag_path))["metadata"]["exporter_run_id"] == run_id
    left = os.listdir(run_dir)
    assert not [n for n in left if n.endswith(".tmp") or n.endswith(".jsonl")
                and n.startswith("views_diagnostics_")]


def test_a_torn_middle_line_is_refused_and_the_journal_kept(tmp_path):
    p = str(tmp_path / "views_diagnostics_d.json")
    journal = ViewsDiagnosticsJournal("R")
    journal.append(p, {}, {"1": {"view_id": 1}})
    with open(journal_path(p), "a") as f:
        f.write('{"schema":"x","run_id":"R","view_id":"2","ent\n')
    journal.append(p, {}, {"3": {"view_id": 3}})
    report = journal.finalize()[p]
    assert report["ok"] is False and "line 2" in report["error"]
    assert os.path.exists(journal_path(p)) and not os.path.exists(p)


def test_an_unreadable_existing_file_is_not_replaced_by_the_compaction(tmp_path):
    p = str(tmp_path / "views_diagnostics_d.json")
    with open(p, "w") as f:
        f.write('{"metadata": {"exporter_run_id": "R"}, "views": {"1": {')
    journal = ViewsDiagnosticsJournal("R")
    journal.append(p, {}, {"2": {"view_id": 2}})
    report = journal.finalize()[p]
    assert report["ok"] is False and "existing file unreadable" in report["error"]
    assert open(p).read().endswith('"1": {') and os.path.exists(journal_path(p))


def test_a_failed_final_write_keeps_the_previous_file_whole(monkeypatch, tmp_path):
    p = str(tmp_path / "views_diagnostics_d.json")
    before = {"metadata": {"exporter_run_id": "R"}, "views": {"1": {"view_id": 1}}}
    with open(p, "w") as f:
        json.dump(before, f, indent=2)
    journal = ViewsDiagnosticsJournal("R")
    journal.append(p, {"exporter_run_id": "R"}, {"2": {"view_id": 2}})

    def _dies(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(vd.json, "dump", _dies)
    assert journal.finalize()[p]["ok"] is False
    monkeypatch.undo()
    assert json.load(open(p)) == before
    assert os.path.exists(journal_path(p)) and not os.path.exists(p + ".tmp")


def test_lines_of_another_run_in_a_journal_are_not_merged(tmp_path):
    p = str(tmp_path / "views_diagnostics_d.json")
    ViewsDiagnosticsJournal("OTHER").append(p, {}, {"1": {"view_id": 1}})
    ViewsDiagnosticsJournal("R").append(p, {}, {"2": {"view_id": 2}})
    lines, report = read_views_diagnostics_journal(journal_path(p), "R")
    assert [v for v, _e, _m in lines] == ["2"] and report["other_run"] == 1


# ---- gate 4d: a view journalled twice takes its LAST entry ----------------------

def test_a_repeated_view_takes_its_last_entry_in_its_first_place(tmp_path):
    """The per-call merge's ``merged.update(views)``: the later call wins
    outright, and the view keeps the position it first had. A union of the
    two entries (the element map's rule) or the first entry would fail."""
    p = str(tmp_path / "views_diagnostics_d.json")
    journal = ViewsDiagnosticsJournal("R")
    journal.append(p, {"call": 1}, {"5": {"view_id": 5, "a": 1}})
    journal.append(p, {"call": 2}, {"6": {"view_id": 6}})
    journal.append(p, {"call": 3}, {"5": {"view_id": 5, "b": 2}})
    ok, _report = compact_views_diagnostics_file(p, "R")
    assert ok is True
    payload = json.load(open(p))
    assert payload == {"metadata": {"call": 3},
                       "views": {"5": {"view_id": 5, "b": 2}, "6": {"view_id": 6}}}
    assert list(payload["views"]) == ["5", "6"]


# ---- Codex, PR #256: cleanup failure, and the durable record ----------------------

def _refuse_journal_removal(monkeypatch):
    """os.remove refuses the views_diagnostics journal only (Windows: another
    process holds it), and removes everything else as usual."""
    real = os.remove

    def _remove(path, *a, **k):
        if os.path.basename(str(path)).startswith("views_diagnostics_") and \
                str(path).endswith(".journal.jsonl"):
            raise PermissionError(13, "the process cannot access the file", path)
        return real(path, *a, **k)
    monkeypatch.setattr(os, "remove", _remove)


def test_a_journal_that_cannot_be_removed_keeps_the_written_file(monkeypatch, tmp_path):
    p = str(tmp_path / "views_diagnostics_d.json")
    journal = ViewsDiagnosticsJournal("R")
    journal.append(p, {"exporter_run_id": "R"}, {"1": {"view_id": 1}})
    journal.append(p, {"exporter_run_id": "R"}, {"2": {"view_id": 2}})
    with monkeypatch.context() as m:
        _refuse_journal_removal(m)
        report = journal.finalize()[p]               # does not raise
    assert report["ok"] is True and "PermissionError" in report["journal_not_removed"]
    assert os.path.exists(journal_path(p))
    first = open(p, "rb").read()
    assert sorted(json.loads(first)["views"]) == ["1", "2"]
    # Compacting the kept journal again gives the same file, and removes it.
    ok, again = compact_views_diagnostics_file(p, "R")
    assert ok is True and "journal_not_removed" not in again
    assert open(p, "rb").read() == first and not os.path.exists(journal_path(p))


def _compaction_record(run_dir):
    from tests.layout_helpers import run_meta_file
    meta = json.loads(run_meta_file(run_dir).read_text())     # the FILE
    assert meta["finalized"] is True
    return meta["views_diagnostics_compaction"]


def test_the_run_finalizes_when_the_journal_cannot_be_removed(monkeypatch, tmp_path):
    monkeypatch.setattr(fx, "VIEWS", _views(5, shared=False))
    _refuse_journal_removal(monkeypatch)
    _r, run_dir = fx.run(monkeypatch, tmp_path)
    record = _compaction_record(run_dir)
    (name,) = record["files"]
    assert record["files"][name]["ok"] is True and record["files"][name]["views"] == 5
    assert "PermissionError" in record["files"][name]["journal_not_removed"]
    assert len(json.load(open(_diag_file(run_dir)))["views"]) == 5


def test_a_failed_compaction_is_recorded_in_run_meta(monkeypatch, tmp_path):
    monkeypatch.setattr(fx, "VIEWS", _views(5, shared=False))

    def _dies(path, payload):
        raise OSError("disk full")
    monkeypatch.setattr(vd, "write_payload_atomic", _dies)
    _r, run_dir = fx.run(monkeypatch, tmp_path)
    record = _compaction_record(run_dir)
    (name,) = record["files"]
    entry = record["files"][name]
    assert entry["ok"] is False and "disk full" in entry["error"]
    assert entry["journal"] == os.path.basename(name)[:-len(".json")] + ".journal.jsonl"
    assert record["views_appended"] == 5
    assert os.path.exists(os.path.join(run_dir, entry["journal"]))
    assert not os.path.exists(os.path.join(run_dir, name))


def test_control_a_clean_compaction_is_recorded_too(monkeypatch, tmp_path):
    """The record is there on success, so a reader never takes its absence
    for a clean compaction."""
    monkeypatch.setattr(fx, "VIEWS", _views(5, shared=False))
    _r, run_dir = fx.run(monkeypatch, tmp_path)
    record = _compaction_record(run_dir)
    (name,) = record["files"]
    assert name == os.path.basename(_diag_file(run_dir))
    entry = record["files"][name]
    assert entry["ok"] is True and entry["views"] == 5 and entry["torn_tail"] == 0
    assert "error" not in entry and "journal_not_removed" not in entry
