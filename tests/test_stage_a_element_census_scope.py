"""The census driver's scope guard and content-keyed pair freshness
(tools/stage_a_element_census.py 2.1.1, 2026-10-21).

Gates (the task's numbering):
  1. an unfinalized run is skipped: no folder, no file changed, a record;
  2. a finalized run with no grid outputs is skipped the same way;
  3. a pair is stale only when what it reads of a census changed -- a re-grid,
     a re-registration, or a census rewritten onto the same CSV leave it fresh;
     one changed element row does not (control);
  4. the identity change is versioned: existing pairs are recomputed once,
     every census summary on disk byte-unchanged, then everything fresh;
  5. the counts recomputed from the transitions FILE are the summary's.
Part C: the freshness check's TIFF locators and threaded hashing give the
inputs a full parse gives (composed, CLAUDE.md defect class 1).

Fixtures are the census's own (real producers: registered and gridded).
Assertions read the files written (defect class 4), each beside a control.
"""
import json
import os
import shutil
import time
import zipfile
from pathlib import Path

import pytest

from tools import register_stage_a_annotation as reg
from tools import stage_a_element_census as census
from tools import stage_a_grid as grid

from tests.test_stage_a_element_census import _dated_run, _view
from tests.test_stage_a_element_census_incremental import (
    _census, _computed, _files, _model, _pair, _recapture, _three)
from tests.test_stage_a_element_census_replace import _tie
from tests.test_stage_a_grid_rollup import _capture_dir, _grid_dir


def _snapshot(folder):
    """``{path: (mtime_ns, sha256)}`` of every file under ``folder``."""
    return dict((p, (p.stat().st_mtime_ns, grid.sha256_file(p)))
                for p in sorted(Path(folder).rglob("*")) if p.is_file())


def _skipped(actions):
    return dict((a["run"], a) for a in actions if a["action"] == "skipped")


def _series_with_middle(model, finalized=True, gridded=True):
    """A (01-01) and C (03-01) in scope; X (02-01) with capture files, its
    grid folder removed when ``gridded`` is False, and run_meta ``finalized``
    as given."""
    a = _dated_run(model, "2025-01-01", "RUN_A")
    _view(a, 1)
    x = _dated_run(model, "2025-02-01", "RUN_X")
    _view(x, 1, eid=8)
    if not gridded:
        shutil.rmtree(str(_grid_dir(x)))
    if finalized is not True:
        meta_path = x / "run_meta.json"
        meta = json.loads(meta_path.read_text())
        meta["finalized"] = finalized
        meta_path.write_text(json.dumps(meta))
    c = _dated_run(model, "2025-03-01", "RUN_C")
    _view(c, 1, eid=8)
    return a, x, c


# --- gate 1: an unfinalized run --------------------------------------------------------

def test_gate1_an_unfinalized_run_is_skipped_and_nothing_in_it_changes(tmp_path):
    model = _model(tmp_path)
    a, x, c = _series_with_middle(model, finalized=False, gridded=False)
    before = _snapshot(x)
    assert any(p.suffix == ".tiff" for p in before)            # capture files present
    actions = census.drive(model)
    assert not _grid_dir(x).exists()
    assert _snapshot(x) == before
    rec = _skipped(actions)[x.name]
    assert rec["step"] == "census" and "finalized is False" in rec["why"]
    assert rec["left_in_place"] == []
    assert not [a_ for a_ in actions if a_["run"] == x.name and a_["action"] != "skipped"]
    # Not in the series: C pairs across it, with A, and says so.
    assert _pair(c)[0]["predecessor"]["run_id"] == "RUN_A"
    assert _pair(c)[1]["host:8"]["class"] == "created"
    assert census.main(["--root", str(model)]) == 0           # a skip is not a refusal


def test_gate1_files_a_skipped_run_already_holds_are_left_and_reported(tmp_path):
    """11-22 on 2026-10-21: refused summaries written into a run mid-capture.
    They stay; the skip names them."""
    model = _model(tmp_path)
    a, x, c = _series_with_middle(model, finalized=False)
    census.write_run_census(_files(x))                       # what the old driver wrote
    census.write_pair(_files(x), ("value", _files(a), None))
    assert _census(x)[0]["state"] == "refused"               # control: they are there
    before = _snapshot(x)
    actions = census.drive(model)
    assert _snapshot(x) == before
    f = _files(x)
    assert _skipped(actions)[x.name]["left_in_place"] == sorted(
        [f.census_summary, f.pair_summary])
    assert _pair(c)[0]["predecessor"]["run_id"] == "RUN_A"


def test_gate1_control_a_finalized_gridded_middle_run_is_in_the_series(tmp_path):
    model = _model(tmp_path)
    a, x, c = _series_with_middle(model)
    actions = census.drive(model)
    assert _skipped(actions) == {}
    assert _pair(c)[0]["predecessor"]["run_id"] == "RUN_X"
    assert _computed(actions) == sorted(
        [("census", r.name) for r in (a, x, c)] + [("pair", r.name) for r in (a, x, c)])


# --- gate 2: a finalized run with no grid outputs --------------------------------------------

def test_gate2_an_ungridded_run_is_skipped_and_nothing_in_it_changes(tmp_path):
    model = _model(tmp_path)
    a, x, c = _series_with_middle(model, gridded=False)
    before = _snapshot(x)
    actions = census.drive(model)
    assert not _grid_dir(x).exists()
    assert _snapshot(x) == before
    why = _skipped(actions)[x.name]["why"]
    assert why.startswith("not gridded: 0 of 1 model sidecar(s)")
    assert str(_grid_dir(x)) in why and "does not exist" in why
    assert _pair(c)[0]["predecessor"]["run_id"] == "RUN_A"


def test_gate2_an_empty_grid_folder_is_still_ungridded_and_left_empty(tmp_path):
    model = _model(tmp_path)
    a, x, c = _series_with_middle(model, gridded=False)
    _grid_dir(x).mkdir()
    actions = census.drive(model)
    assert list(_grid_dir(x).iterdir()) == []
    assert "does not exist" not in _skipped(actions)[x.name]["why"]


def test_every_run_skipped_exits_0_and_writes_nothing(tmp_path):
    model = _model(tmp_path)
    x = _dated_run(model, "2025-02-01", "RUN_X")
    _view(x, 1)
    shutil.rmtree(str(_grid_dir(x)))
    before = _snapshot(model)
    assert census.main(["--root", str(model)]) == 0
    assert _snapshot(model) == before


# --- gate 3: a pair is stale only when what it reads changed ------------------------------------

def _clock(monkeypatch, offset_s):
    """Move the wall clock every writer reads by ``offset_s``."""
    real_time, real_local = time.time, time.localtime
    monkeypatch.setattr(time, "time", lambda: real_time() + offset_s)
    monkeypatch.setattr(time, "localtime",
                        lambda t=None: real_local((real_time() if t is None else t) + offset_s))


def test_gate3_a_regrid_of_unchanged_captures_leaves_census_and_pairs_fresh(
        tmp_path, monkeypatch):
    """Re-gridded under a clock moved by a year: the npz and grid record come
    out byte-identical (zipfile dates a member written through
    ``ZipFile.open(name, "w")`` 1980-01-01, not now), so nothing is stale."""
    model = _model(tmp_path)
    a, b, c = _three(model)
    census.drive(model)
    npz = dict((p, p.read_bytes()) for p in _grid_dir(b).glob("*.grid.npz"))
    gjson = dict((p, p.read_bytes()) for p in _grid_dir(b).glob("*.grid.json"))
    assert npz                                                     # control: there are some
    _clock(monkeypatch, 366 * 86400)
    for side in sorted(_capture_dir(b).glob("V_?.json")):
        grid.grid_view(side, _grid_dir(b))
    assert dict((p, p.read_bytes()) for p in npz) == npz
    assert dict((p, p.read_bytes()) for p in gjson) == gjson
    actions = census.drive(model)
    assert _computed(actions) == []
    assert set(a_["action"] for a_ in actions) == {"fresh"}


def test_gate3_control_the_moved_clock_moves_a_writer_that_stamps_it(tmp_path, monkeypatch):
    """The clock patch above is not a no-op: a zip member dated by the clock
    comes out different under it."""
    def _stamped(path):
        with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(zipfile.ZipInfo("a.npy", date_time=time.localtime()[:6]), b"x")
        return Path(path).read_bytes()
    first = _stamped(tmp_path / "1.zip")
    _clock(monkeypatch, 366 * 86400)
    assert _stamped(tmp_path / "2.zip") != first


def test_gate3_a_reregistration_of_unchanged_captures_leaves_everything_fresh(tmp_path):
    """The re-analysis of 10-25 (E3). Until register 1.0.1 every record was
    rewritten with a new ``elapsed_ms`` (wall time), which changed its sha256
    -- a census input, and recorded in the grid json -- so the census was
    recomputed. Now the record, the registered TIFF and the grid come out
    byte-identical, and nothing is stale."""
    model = _model(tmp_path)
    a, b, c = _three(model)
    census.drive(model)
    rec_path = _capture_dir(b) / "V_1_anno.registered.json"
    watched = [rec_path, _capture_dir(b) / "V_1_anno.registered.tiff",
               _grid_dir(b) / "V_1.grid.json", _grid_dir(b) / "V_1.grid.npz"]
    before = dict((p, p.read_bytes()) for p in watched)
    time.sleep(0.01)
    reg.register(_capture_dir(b) / "V_1_anno.json")
    grid.grid_view(_capture_dir(b) / "V_1.json", _grid_dir(b))
    assert dict((p, p.read_bytes()) for p in watched) == before
    assert "elapsed_ms" not in json.loads(rec_path.read_text())
    actions = census.drive(model)
    assert _computed(actions) == []


def test_gate3_a_census_rewritten_onto_the_same_csv_leaves_the_pairs_fresh(tmp_path):
    """stage_a_analyze step 6 rewrites the named run's census every time;
    with a TIFF's modified time moved, its summary's bytes change
    (``input_stats``) and its CSV does not. Under 2.1.0 both pairs beside it
    were recomputed ("the run's own census changed", then the predecessor's)."""
    model = _model(tmp_path)
    a, b, c = _three(model)
    census.drive(model)
    f = _files(b)
    summary_before = grid.sha256_file(f.census_path("summary"))
    tiff = _capture_dir(b) / "V_1.tiff"
    st = tiff.stat()
    os.utime(str(tiff), ns=(st.st_atime_ns, st.st_mtime_ns + 10 ** 9))
    assert census.main([str(b)]) == 1                     # B has an unmeasured view
    assert grid.sha256_file(f.census_path("summary")) != summary_before
    actions = census.drive(model)
    assert _computed(actions) == []


def test_gate3_control_one_changed_element_row_recomputes_the_pairs(tmp_path):
    model = _model(tmp_path)
    a, b, c = _three(model)
    census.drive(model)
    _recapture(b, category="Doors")
    actions = census.drive(model)
    assert _computed(actions) == [("census", b.name), ("pair", b.name), ("pair", c.name)]
    whys = dict((x["run"], x["why"]) for x in actions if x["step"] == "pair")
    assert whys[b.name] == "the run's own census changed"
    assert whys[c.name] == "the predecessor's census changed"


def test_gate3_control_a_changed_view_id_list_recomputes_the_pair(tmp_path):
    """A summary field the pair reads, with the CSV unchanged, makes it
    stale; a field it does not read (``input_stats``) does not."""
    model = _model(tmp_path)
    a, b, c = _three(model)
    census.drive(model)
    f = _files(c)
    rec = json.loads(f.census_path("summary").read_text())
    choice = census.predecessors([_files(r) for r in (a, b, c)])[str(f.run_dir)]
    for changed, want in ((dict(rec, unmeasured_view_ids=["99"]),
                           ["the run's own census changed"]),
                          (dict(rec, input_stats={"x": [1, 2]}), [])):
        contents = {str(f.run_dir): census.census_content(f, changed)}
        assert census.pair_staleness(f, choice, contents) == want


# --- gate 4: the identity change is versioned ------------------------------------------------------

def _as_2_1_0(files):
    """A pair summary as 2.1.0 wrote it: its version, and its identity keyed
    on the sha256 of each whole census summary."""
    path = files.pair_path("summary")
    rec = json.loads(path.read_text())
    ident = rec["identity"]
    prev = ident["predecessor"]
    prev_files = (census.RunFiles(files.run_dir.parent / prev["run_dir_name"])
                  if prev else None)
    rec["identity"] = {
        "state": ident["state"], "reason": ident["reason"], "predecessor": prev,
        "census_summary_sha256": {
            "cur": grid.sha256_file(files.census_path("summary")),
            "prev": (grid.sha256_file(prev_files.census_path("summary"))
                     if prev_files else None)}}
    for side in rec["census"].values():
        side["summary_sha256"] = "as 2.1.0 recorded it"
    rec.pop("identity_rule")
    rec.update(tool_version="2.1.0", pair_version="2.1.0")
    path.write_text(json.dumps(rec, indent=2, sort_keys=True))


def test_gate4_old_pairs_are_recomputed_once_and_no_census_summary_changes(tmp_path):
    model = _model(tmp_path)
    runs = _three(model)
    census.drive(model)
    for r in runs:
        _as_2_1_0(_files(r))
    before = dict((r.name, grid.sha256_file(_files(r).census_path("summary"))) for r in runs)
    actions = census.drive(model)
    assert _computed(actions) == sorted(("pair", r.name) for r in runs)
    assert all("pair_version 2.1.0" in x["why"] for x in actions if x["step"] == "pair")
    assert set(x["action"] for x in actions if x["step"] == "census") == {"fresh"}
    assert dict((r.name, grid.sha256_file(_files(r).census_path("summary")))
                for r in runs) == before
    assert _pair(runs[1])[0]["pair_version"] == census.PAIR_VERSION
    again = census.drive(model)
    assert _computed(again) == [] and set(x["action"] for x in again) == {"fresh"}


# --- gate 5: counts from the transitions FILE are the summary's ------------------------------------

def _counts_from_file(rows):
    out = dict(("{0}{1}".format(t, s), 0) for t in census.TRANSITIONS
               + census.REPLACE_COUNTS for s in ("", "_LINK"))
    for r in rows.values():
        sfx = "_LINK" if r["source"] == "LINK" else ""
        out[r["class"] + sfx] += 1
        if r["class"] == "created":
            out[("created_replaced" if r["replaced"] == "true" else "created_new") + sfx] += 1
        elif r["class"] == "gone":
            out[("gone_replaced" if r["replaced"] == "true" else "gone_removed") + sfx] += 1
    return out


def test_gate5_counts_from_the_transitions_file_are_the_summarys(tmp_path):
    checked = 0
    for name, build in (("three", _three), ("tie", _tie)):
        model = _model(tmp_path / name)
        runs = build(model)
        census.drive(model)
        for r in runs[1:]:
            summary, rows = _pair(r)
            assert summary["state"] == "value" and rows
            assert _counts_from_file(rows) == summary["counts"], (name, r.name)
            checked += sum(v for v in summary["counts"].values())
    assert checked > 0                                     # control: not all zeros
    assert any(v for k, v in _pair(runs[1])[0]["counts"].items() if "replaced" in k)


# --- part C: locators and threads give the inputs a full parse gives ---------------------------------

def test_locator_inputs_are_the_inputs_of_a_full_parse(tmp_path):
    model = _model(tmp_path)
    runs = _three(model)
    census.drive(model)
    for r in runs:
        f = _files(r)
        rec = json.loads(f.census_path("summary").read_text())
        assert rec["tiff_locators"]                                   # recorded
        located = census.census_inputs(f, rec)
        assert located == census.census_inputs(f), r.name             # no previous: parsed


def test_a_fresh_check_does_not_parse_a_sidecar_whose_sha256_is_recorded(tmp_path,
                                                                         monkeypatch):
    model = _model(tmp_path)
    a, b, c = _three(model)
    census.drive(model)
    parsed = []
    real = census._load_json

    def _count(path):
        name = Path(path).name
        if name.startswith("V_") and name.endswith(".json"):
            parsed.append(name)
        return real(path)
    monkeypatch.setattr(census, "_load_json", _count)
    assert _computed(census.drive(model)) == []
    assert [p for p in parsed if "grid" not in p] == []
    # Control: a sidecar rewritten is parsed again (and the census reruns).
    side = _capture_dir(b) / "V_1.json"
    rec = json.loads(side.read_text())
    rec["recapture"] = {"captured_at": "later"}
    side.write_text(json.dumps(rec))
    del parsed[:]
    census.drive(model)
    assert "V_1.json" in parsed


def test_a_changed_locator_is_followed(tmp_path):
    """A sidecar that now names another TIFF: its sha256 differs from the
    locator's, so it is parsed, and the census hashes the TIFF it names."""
    model = _model(tmp_path)
    a, b, c = _three(model)
    census.drive(model)
    side = _capture_dir(b) / "V_1.json"
    moved = _capture_dir(b) / "V_1_moved.tiff"
    shutil.copyfile(str(_capture_dir(b) / "V_1.tiff"), str(moved))
    rec = json.loads(side.read_text())
    rec["tiff_path"] = str(moved)
    side.write_text(json.dumps(rec))
    previous = json.loads(_files(b).census_path("summary").read_text())
    inputs, stats = census.census_inputs(_files(b), previous)
    assert "color_id_buffer/V_1_moved.tiff" in stats
    assert "color_id_buffer/V_1.tiff" not in stats


def test_threaded_hashing_gives_the_single_thread_result(tmp_path, monkeypatch):
    model = _model(tmp_path)
    a, b, c = _three(model)
    threaded = census.census_inputs(_files(b))
    monkeypatch.setattr(census, "HASH_WORKERS", 1)
    assert census.census_inputs(_files(b)) == threaded


# --- the locator back-fill: a fresh census written before 2.1.1 gains them once -----------------

def _as_2_1_0_census(files):
    """A census summary as 2.1.0 wrote it: no ``tiff_locators``."""
    path = files.census_path("summary")
    rec = json.loads(path.read_text())
    rec.pop("tiff_locators")
    census._write_json(path, rec)
    return rec


def _no_recompute(monkeypatch):
    def _no(*_a, **_k):
        raise AssertionError("census recomputed")
    monkeypatch.setattr(census, "census_run", _no)


def test_a_fresh_census_without_locators_gains_them_and_nothing_else_changes(
        tmp_path, monkeypatch):
    model = _model(tmp_path)
    runs = _three(model)
    census.drive(model)
    old = dict((r.name, _as_2_1_0_census(_files(r))) for r in runs)
    csvs = dict((r.name, grid.sha256_file(_files(r).census_path("csv"))) for r in runs)
    pairs = dict((r.name, grid.sha256_file(_files(r).pair_path("summary"))) for r in runs)
    _no_recompute(monkeypatch)
    actions = census.drive(model)
    assert _computed(actions) == []                           # no census, no pair
    assert [x["locators_added"] for x in actions if x["step"] == "census"] == [True] * 3
    for r in runs:
        f = _files(r)
        new = json.loads(f.census_path("summary").read_text())
        assert new["tiff_locators"] and new["tiff_locators_added"]["records"] == len(
            new["tiff_locators"])
        rest = dict((k, v) for k, v in new.items()
                    if k not in ("tiff_locators", "tiff_locators_added"))
        assert rest == old[r.name]                            # every other field as it was
        assert grid.sha256_file(f.census_path("csv")) == csvs[r.name]
        assert grid.sha256_file(f.pair_path("summary")) == pairs[r.name]
        assert census.census_content(f, new) == census.census_content(f, old[r.name])
        assert not list(f.grid_dir.glob("*.tmp"))
    # Then stable: nothing written, no record parsed.
    before = dict((p, p.read_bytes()) for p in model.rglob("element_*"))
    again = census.drive(model)
    assert not [x for x in again if x.get("locators_added")]
    assert dict((p, p.read_bytes()) for p in model.rglob("element_*")) == before


def test_control_a_census_that_has_its_locators_is_not_rewritten(tmp_path):
    model = _model(tmp_path)
    runs = _three(model)
    census.drive(model)
    before = dict((r.name, grid.sha256_file(_files(r).census_path("summary"))) for r in runs)
    actions = census.drive(model)
    assert not [x for x in actions if x.get("locators_added")]
    assert dict((r.name, grid.sha256_file(_files(r).census_path("summary")))
                for r in runs) == before


def test_after_the_backfill_a_fresh_check_parses_no_record(tmp_path, monkeypatch):
    model = _model(tmp_path)
    runs = _three(model)
    census.drive(model)
    for r in runs:
        _as_2_1_0_census(_files(r))
    census.drive(model)                                       # the back-fill
    parsed = []
    real = census._load_json

    def _count(path):
        name = Path(path).name
        if name.startswith("V_") and name.endswith(".json") and ".grid." not in name:
            parsed.append(name)
        return real(path)
    monkeypatch.setattr(census, "_load_json", _count)
    census.drive(model)
    assert parsed == []


def test_the_backfill_and_the_pair_version_change_together(tmp_path, monkeypatch):
    """Greg's tree before 2.1.1: censuses without locators and 2.1.0 pairs.
    One drive: locators added, every pair recomputed once, no census
    recomputed, every census CSV byte-unchanged; the next drive writes nothing."""
    model = _model(tmp_path)
    runs = _three(model)
    census.drive(model)
    for r in runs:
        _as_2_1_0_census(_files(r))
        _as_2_1_0(_files(r))
    csvs = dict((r.name, grid.sha256_file(_files(r).census_path("csv"))) for r in runs)
    _no_recompute(monkeypatch)
    actions = census.drive(model)
    assert _computed(actions) == sorted(("pair", r.name) for r in runs)
    assert sum(1 for x in actions if x.get("locators_added")) == 3
    assert dict((r.name, grid.sha256_file(_files(r).census_path("csv"))) for r in runs) == csvs
    before = dict((p, p.read_bytes()) for p in model.rglob("element_*"))
    assert _computed(census.drive(model)) == []
    assert dict((p, p.read_bytes()) for p in model.rglob("element_*")) == before


def test_an_interrupted_backfill_leaves_the_old_summary(tmp_path, monkeypatch):
    model = _model(tmp_path)
    a, b, c = _three(model)
    census.drive(model)
    f = _files(b)
    _as_2_1_0_census(f)
    old = f.census_path("summary").read_bytes()

    def _fail(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(census.os, "replace", _fail)
    with pytest.raises(OSError):
        census.drive(model)
    assert f.census_path("summary").read_bytes() == old
    assert not list(f.grid_dir.glob("*.tmp"))


# --- review, PR #245 ---------------------------------------------------------------------------

def test_a_pair_kept_fresh_names_no_hash_the_census_on_disk_no_longer_has(tmp_path):
    """Codex: a pair left fresh across a census rewritten onto the same
    content recorded the old summary's sha256. 2.1.2 records none; every
    hash the pair summary does carry matches the files on disk."""
    model = _model(tmp_path)
    a, b, c = _three(model)
    census.drive(model)
    f = _files(b)
    summary_before = grid.sha256_file(f.census_path("summary"))
    tiff = _capture_dir(b) / "V_1.tiff"                        # stage_a_analyze step 6:
    st = tiff.stat()                                           # the census rewritten onto
    os.utime(str(tiff), ns=(st.st_atime_ns, st.st_mtime_ns + 10 ** 9))   # the same CSV
    census.main([str(b)])
    assert grid.sha256_file(f.census_path("summary")) != summary_before
    assert _computed(census.drive(model)) == []
    for run in (b, c):
        summary, _rows = _pair(run)
        for side, files in (("cur", _files(run)),
                            ("prev", _files(model / summary["predecessor"]["run_dir_name"]))):
            rec = summary["census"][side]
            assert "summary_sha256" not in rec
            assert rec["csv_sha256"] == grid.sha256_file(files.census_path("csv"))
            assert summary["identity"]["census_content"][side] == census.census_content(files)


def test_a_2_1_1_pair_is_recomputed_once_and_loses_the_summary_hash(tmp_path):
    model = _model(tmp_path)
    runs = _three(model)
    census.drive(model)
    for r in runs:
        path = _files(r).pair_path("summary")
        rec = json.loads(path.read_text())
        rec.update(tool_version="2.1.1", pair_version="2.1.1")
        for side in rec["census"].values():
            side["summary_sha256"] = "as 2.1.1 recorded it"
        path.write_text(json.dumps(rec, indent=2, sort_keys=True))
    actions = census.drive(model)
    assert _computed(actions) == sorted(("pair", r.name) for r in runs)
    for r in runs[1:]:
        assert not [s for s in _pair(r)[0]["census"].values() if "summary_sha256" in s]
    assert _computed(census.drive(model)) == []


def _unindexable(model, census_from=None):
    """A run folder whose run_meta cannot be read, holding the census files
    ``census_from``'s grid folder holds (as an earlier driver left them)."""
    x = model / "2025-02-15__RUN_BROKEN"
    (x / "analysis_grid").mkdir(parents=True)
    (x / "run_meta.json").write_text("{ not json")
    if census_from is not None:
        for p in _grid_dir(census_from).glob("element_*"):
            shutil.copyfile(str(p), str(x / "analysis_grid" / p.name))
    return x


def test_a_run_folder_with_unreadable_run_meta_is_a_skipped_action(tmp_path):
    """Codex: discover() leaves such a folder out of the runs, so it had no
    action record and its files were not named."""
    model = _model(tmp_path)
    a, b, c = _three(model)
    census.drive(model)
    x = _unindexable(model, census_from=b)
    before = _snapshot(x)
    actions = census.drive(model)
    rec = _skipped(actions)[x.name]
    assert rec["why"].startswith("run_meta unreadable")
    assert rec["left_in_place"] == sorted(p.name for p in _grid_dir(b).glob("element_*"))
    assert rec["left_in_place"]                                     # control: not empty
    assert _snapshot(x) == before
    assert _computed(actions) == []                                 # the series is unchanged


def test_a_root_holding_only_an_unreadable_run_exits_0_and_writes_nothing(tmp_path):
    model = _model(tmp_path)
    x = _unindexable(model)
    before = _snapshot(model)
    assert census.main(["--root", str(model)]) == 0
    assert _snapshot(model) == before
    assert [a_["run"] for a_ in census.drive(model)] == [x.name]
