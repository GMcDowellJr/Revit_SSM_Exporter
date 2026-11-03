"""E2: the run's view-element map is appended per view and written ONCE.

Gates (PR body): 1 the final file is the one the per-view merge wrote; 2 the
per-view cost no longer grows with the views already written; 3 a crash
mid-run keeps every finished view and leaves no half-written file.

The OLD path is still production code -- a caller with no StreamingExporter
journal takes it -- so gate 1 compares the two production paths, not a copy.
"""
import json
import os
import re
import statistics

import pytest

from tests import stage_a_loop_fixture as fx
from vop_interwoven.core import element_cache as ec
from vop_interwoven.core.element_cache import (
    ElementCache, ViewElementMapJournal, compact_view_element_map, journal_path, read_journal)


def _views(n, ids_per_view=12, shared=True):
    """``n`` synthetic FloorPlan views. ``shared``: every view lists ids from
    one pool, so the ElementCache stays small and a long run stays fast."""
    out = []
    for i in range(n):
        base = 1000 if shared else 1000 + i * ids_per_view
        members = tuple((base + (i + k) % (ids_per_view * 3), fx.WALLS)
                        for k in range(ids_per_view))
        out.append((30000000 + i, "V{0:04d}".format(i), "FloorPlan", members))
    return tuple(out)


def _map_file(run_dir):
    (name,) = [n for n in os.listdir(run_dir)
               if n.startswith("vop_view_element_map_") and n.endswith(".json")]
    return os.path.join(run_dir, name)


def _without_clock(raw):
    return re.sub(rb'\n  "generated_utc": [^\n]*\n', b"\n", raw)


def _old_path(monkeypatch):
    """Run as a caller with no journal: the per-view merge, as before E2."""
    monkeypatch.setattr(ec, "ViewElementMapJournal", lambda run_id: None)


# ---- gate 1: the final file ------------------------------------------------------

@pytest.mark.parametrize("batch_size", [None, 64])
def test_the_final_map_is_the_per_view_merges_map(monkeypatch, tmp_path, batch_size):
    """200 views: well past any batch size used in a run and enough views
    that an ordering or dedupe difference between the paths would show."""
    monkeypatch.setattr(fx, "VIEWS", _views(200, shared=False))
    cfg_kw = dict(export_view_diagnostics=False)
    _r, new_dir = fx.run(monkeypatch, tmp_path / "new", cfg=fx.make_cfg(**cfg_kw),
                         batch_size=batch_size)
    with monkeypatch.context() as m:
        _old_path(m)
        _r, old_dir = fx.run(m, tmp_path / "old", cfg=fx.make_cfg(**cfg_kw),
                             batch_size=batch_size)
    # Batched runs write each batch's map into its own _batch_tmp_<n>/, as
    # before E2: compare every map the run wrote, wherever it is.
    def maps(root):
        found = {}
        for dirpath, _d, files in os.walk(root):
            for n in files:
                if n.startswith("vop_view_element_map_"):
                    found[os.path.relpath(os.path.join(dirpath, n), root)] = os.path.join(dirpath, n)
        return found
    new_maps, old_maps = maps(new_dir), maps(old_dir)
    assert sorted(new_maps) == sorted(old_maps) and new_maps, (new_maps, old_maps)
    run_id = lambda d: os.path.basename(d).split("__", 1)[1]
    for rel in new_maps:
        a = json.load(open(old_maps[rel]))
        b = json.load(open(new_maps[rel]))
        assert a.pop("run_id") == run_id(old_dir) and b.pop("run_id") == run_id(new_dir)
        a.pop("generated_utc"), b.pop("generated_utc")
        assert a == b
        raw_a = _without_clock(open(old_maps[rel], "rb").read()).replace(
            run_id(old_dir).encode(), b"<RUN>")
        raw_b = _without_clock(open(new_maps[rel], "rb").read()).replace(
            run_id(new_dir).encode(), b"<RUN>")
        assert raw_a == raw_b
    total = sum(len(json.load(open(p))["views"]) for p in new_maps.values())
    assert total == 200
    assert not [n for d, _s, fs in os.walk(new_dir) for n in fs if n.endswith(".jsonl")]


def test_a_recapture_of_three_views_merges_into_the_runs_map_as_before(tmp_path):
    """The recapture path: an existing run's map, stamped with its id, gets
    three views appended by the same run id -- the exporter's journal
    against three per-view merge calls, from the same starting file."""
    run_id = "20261103T1200"
    base = {"schema": "vop.view_element_map.v1", "generated_utc": 1.0, "run_id": run_id,
            "views": [{"view_id": v, "element_ids": [v * 10, v * 10 + 1]}
                      for v in range(1, 11)]}
    recaptured = {3: [(31, "HOST"), (99, "HOST")], 7: [], 11: [(5, "HOST"), (5, "HOST")]}
    paths = {}
    for which in ("old", "new"):
        d = tmp_path / which
        d.mkdir()
        p = str(d / "vop_view_element_map_2026-11-03.json")
        with open(p, "w") as f:
            json.dump(base, f, indent=2)
        paths[which] = p
    cache = ElementCache(max_elements=10)
    for vid, ids in recaptured.items():
        assert cache.export_view_element_map_json(paths["old"], view_elements={vid: ids},
                                                  merge_existing=True, run_id=run_id)
    journal = ViewElementMapJournal(run_id)
    for vid, ids in recaptured.items():
        journal.append(paths["new"], {vid: ids})
    assert journal.finalize()[paths["new"]]["ok"] is True
    a, b = json.load(open(paths["old"])), json.load(open(paths["new"]))
    a.pop("generated_utc"), b.pop("generated_utc")
    assert a == b
    assert _without_clock(open(paths["old"], "rb").read()) == \
        _without_clock(open(paths["new"], "rb").read())
    views = {v["view_id"]: v["element_ids"] for v in b["views"]}
    assert views[3] == [30, 31, 99]    # union with the run's earlier entry
    assert views[7] == [70, 71] and views[11] == [5]


def test_control_another_runs_map_is_still_replaced(tmp_path):
    p = str(tmp_path / "vop_view_element_map_2026-11-03.json")
    with open(p, "w") as f:
        json.dump({"schema": "vop.view_element_map.v1", "run_id": "OLD", "generated_utc": 0,
                   "views": [{"view_id": 1, "element_ids": [9]}]}, f)
    journal = ViewElementMapJournal("NEW")
    journal.append(p, {2: [(20, "HOST")]})
    journal.finalize()
    payload = json.load(open(p))
    assert payload["run_id"] == "NEW"
    assert [(v["view_id"], v["element_ids"]) for v in payload["views"]] == [(2, [20])]


def test_finalize_detaches_the_journal_from_the_config(monkeypatch, tmp_path):
    from vop_interwoven.config import Config
    from vop_interwoven.streaming import StreamingExporter
    cfg = Config()
    exp = StreamingExporter(str(tmp_path), cfg, doc=None, export_png=False,
                            export_csv=False, run_id="RUN_X")
    assert cfg._view_element_map_journal.run_id == "RUN_X"
    out = exp.finalize()
    assert cfg._view_element_map_journal is None
    assert out["element_map"]["views_appended"] == 0


# ---- gate 2: cost no longer grows -----------------------------------------------

def test_the_per_view_map_write_does_not_grow_with_the_run(monkeypatch, tmp_path):
    """1,610 views through the real loop; the per-view map write (C2's
    pipeline.steps_ms.element_map_write, timed around the call site) at
    views 1,591-1,610 against views 41-60, medians of 20 for noise. 2x is
    loose on purpose (filesystem noise); the per-view rewrite grows ~30x
    over the same span here. 1,610: the 03-16 run had 1,650 views."""
    monkeypatch.setattr(fx, "VIEWS", _views(1610, ids_per_view=100))
    _r, run_dir = fx.run(monkeypatch, tmp_path, cfg=fx.make_cfg(export_view_diagnostics=False))
    from tests.layout_helpers import run_meta_file
    views = json.loads(run_meta_file(run_dir).read_text())["views"]
    ms = [v["loop_timing"]["pipeline"]["steps_ms"]["element_map_write"] for v in views]
    early, late = statistics.median(ms[40:60]), statistics.median(ms[1590:1610])
    assert late <= 2.0 * max(early, 0.05), (early, late)
    assert len(json.load(open(_map_file(run_dir)))["views"]) == 1610


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
        fx.run(monkeypatch, tmp_path, cfg=fx.make_cfg(export_view_diagnostics=False))
    (run_dir,) = [os.path.join(str(tmp_path), d) for d in os.listdir(str(tmp_path))
                  if "__" in d]
    files = os.listdir(run_dir)
    # (b) nothing half-written: no temp file, no map yet, every journal line whole.
    assert not [n for n in files if n.endswith(".tmp")]
    assert not [n for n in files if n.startswith("vop_view_element_map_") and n.endswith(".json")]
    (jname,) = [n for n in files if n.endswith(".journal.jsonl")]
    run_id = os.path.basename(run_dir).split("__", 1)[1]
    elements, report = read_journal(os.path.join(run_dir, jname), run_id)
    assert report["torn_tail"] == 0
    # (a) every view captured before the crash, and only those.
    assert list(elements) == seen
    # and the journal compacts into the map those k views would have had.
    map_path = os.path.join(run_dir, jname.replace(".journal.jsonl", ".json"))
    ok, report = compact_view_element_map(map_path, run_id)
    assert ok is True and report["views"] == k
    assert [v["view_id"] for v in json.load(open(map_path))["views"]] == sorted(seen)
    assert not os.path.exists(os.path.join(run_dir, jname))


def test_a_torn_last_line_is_dropped_and_a_torn_middle_line_refused(tmp_path):
    p = str(tmp_path / "vop_view_element_map_d.json")
    journal = ViewElementMapJournal("R")
    journal.append(p, {1: [(10, "HOST")]})
    journal.append(p, {2: [(20, "HOST")]})
    with open(journal_path(p), "a") as f:
        f.write('{"schema":"vop.view_element_map_journal.v1","run_id":"R","view_id":3,"elem')
    elements, report = read_journal(journal_path(p), "R")
    assert list(elements) == [1, 2] and report["torn_tail"] == 1
    with open(journal_path(p), "a") as f:
        f.write("\n" + json.dumps({"run_id": "R", "view_id": 4, "element_ids": [40]}) + "\n")
    with pytest.raises(ValueError):
        read_journal(journal_path(p), "R")
    ok_report = journal.finalize()[p]
    assert ok_report["ok"] is False and os.path.exists(journal_path(p))  # kept, never guessed


def test_a_failed_final_write_keeps_the_previous_map_whole(monkeypatch, tmp_path):
    p = str(tmp_path / "vop_view_element_map_d.json")
    before = {"schema": "vop.view_element_map.v1", "run_id": "R", "generated_utc": 0,
              "views": [{"view_id": 1, "element_ids": [1]}]}
    with open(p, "w") as f:
        json.dump(before, f, indent=2)
    journal = ViewElementMapJournal("R")
    journal.append(p, {2: [(2, "HOST")]})

    def _dies(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(json, "dump", _dies)  # export_view_element_map_json's `import json`
    assert journal.finalize()[p]["ok"] is False
    monkeypatch.undo()
    assert json.load(open(p)) == before
    assert os.path.exists(journal_path(p))


def test_lines_of_another_run_in_a_journal_are_not_merged(tmp_path):
    p = str(tmp_path / "vop_view_element_map_d.json")
    ViewElementMapJournal("OTHER").append(p, {1: [(10, "HOST")]})
    ViewElementMapJournal("R").append(p, {2: [(20, "HOST")]})
    elements, report = read_journal(journal_path(p), "R")
    assert list(elements) == [2] and report["other_run"] == 1
