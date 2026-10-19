"""The element census split (tools/stage_a_element_census.py 2.0.0): per-run
outputs in each run's grid folder, the pair step, the incremental driver.

Fixtures are the census's own (tests/test_stage_a_element_census.py): views
made by the real producers, registered and gridded. Runs live in one model
folder, ``<tmp>/KSRF/Model/<date>__<run_id>``, as the export tree holds them.

Composition (CLAUDE.md, defect class 1): the per-run element rows are the
rows the multi-run ``--out`` census writes for the same run, and the pair
step's classes and counts are ``classify`` / ``transitions`` run on
``census_run`` in memory -- the pair step reads them back from FILES.

Assertions read the files written (defect class 4), each beside a control.
"""
import csv
import json
from pathlib import Path

import pytest

from tools import register_stage_a_annotation as reg
from tools import stage_a_element_census as census
from tools import stage_a_grid as grid
from tools import stage_a_runs_index as idx
from vop_interwoven import output_layout as ol

from tests.test_stage_a_element_census import EID, _dated_run, _link_entry, _view
from tests.test_stage_a_grid_rollup import _capture_dir, _grid_dir


def _rows(path):
    with open(str(path), encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _model(tmp_path):
    folder = tmp_path / "KSRF" / "Model"
    folder.mkdir(parents=True)
    return folder


def _files(run):
    return census.RunFiles(run)


def _census(run):
    f = _files(run)
    return (json.loads(f.census_path("summary").read_text()),
            dict((r["element_key"], r) for r in _rows(f.census_path("csv")))
            if f.census_path("csv").exists() else None)


def _pair(run):
    f = _files(run)
    summary = json.loads(f.pair_path("summary").read_text())
    rows = (dict((r["element_key"], r) for r in _rows(f.pair_path("csv")))
            if f.pair_path("csv").exists() else None)
    return summary, rows


def _computed(actions):
    return sorted((a["step"], a["run"]) for a in actions if a["action"] == "computed")


def _three(model):
    """A (01-01): views 1, 2 hold 7. B (02-01): view 1 holds 8, view 2 holds
    8 but failed, view 3 holds 9. C (03-01): view 1 holds 8 again."""
    a = _dated_run(model, "2025-01-01", "RUN_A")
    _view(a, 1)
    _view(a, 2, link={"11:500": _link_entry(11, 500)})
    b = _dated_run(model, "2025-02-01", "RUN_B")
    _view(b, 1, eid=8)
    _view(b, 2, eid=8, status="annotation_failed")
    _view(b, 3, eid=9)
    c = _dated_run(model, "2025-03-01", "RUN_C")
    _view(c, 1, eid=8)
    return a, b, c


# --- 1. composition with the series census ------------------------------------------

def test_per_run_element_rows_are_the_series_censuss_rows(tmp_path):
    a, b, _c = _three(_model(tmp_path))
    assert census.main([str(a), str(b)]) == 1            # B has an unmeasured view
    out = tmp_path / "out"
    census.main([str(a), str(b), "--out", str(out)])
    runs = dict((r["run_id"], r) for r in _rows(out / census.RUNS_CSV))
    for run, run_id, date in ((a, "RUN_A", "2025-01-01"), (b, "RUN_B", "2025-02-01")):
        summary, els = _census(run)
        series_rows = _rows(out / "{0}__{1}{2}".format(date, run_id, census.ELEMENTS_SUFFIX))
        assert sorted(els.values(), key=lambda r: r["element_key"]) == series_rows
        assert series_rows and all(r["view_ids"] for r in series_rows)
        for col in census.RUN_FIGURE_COLUMNS:
            if col == "run_dir":
                continue
            assert str(census._cell(summary["figures"][col])) == runs[run_id][col], col
    _s, els_a = _census(a)
    assert els_a["host:{0}".format(EID)]["view_ids"] == "1;2"
    assert els_a["link:11:500"]["view_ids"] == "2"


def test_the_pair_steps_classes_are_transitions_on_the_same_two_runs(tmp_path):
    """From the files: classify / transitions / host_id_overlap on the
    in-memory census of the same two runs, and the --out runs CSV's row."""
    a, b, _c = _three(_model(tmp_path))
    census.drive(a.parent)
    summary, rows = _pair(b)
    ra, rb = census.census_run(a), census.census_run(b)
    _e, _l, counts = census.transitions(ra, rb)
    assert summary["counts"] == counts
    assert dict((k, r["class"]) for k, r in rows.items()) == dict(
        (k, rec["word"]) for k, rec in census.classify(ra, rb).items())
    overlap = census.host_id_overlap(ra, rb)
    assert (summary["common_measured_views"], summary["host_id_jaccard_common_views"]) == (
        overlap[0], overlap[1])
    # The cases the order of the checks decides, named.
    assert rows["host:{0}".format(EID)]["class"] == "unmeasured_out"    # view 2 failed
    assert rows["host:{0}".format(EID)]["deciding_view_ids"] == "2"
    assert rows["host:8"]["class"] == "created"                        # view 1 measured
    assert rows["host:9"]["class"] == "new_view"
    assert rows["link:11:500"]["class"] == "unmeasured_out"
    assert (rows["host:8"]["in_prev"], rows["host:8"]["in_cur"]) == ("false", "true")
    out = tmp_path / "out"
    census.main([str(a), str(b), "--out", str(out)])
    rb_row = [r for r in _rows(out / census.RUNS_CSV) if r["run_id"] == "RUN_B"][0]
    for k, v in counts.items():
        assert rb_row[k] == str(v), k
    assert summary["predecessor"]["run_id"] == "RUN_A"
    assert set(summary["census"]) == {"cur", "prev"}
    assert summary["census"]["prev"]["summary_sha256"] == grid.sha256_file(
        _files(a).census_path("summary"))


def test_the_first_run_says_it_has_no_predecessor_and_writes_no_csv(tmp_path):
    a, _b, _c = _three(_model(tmp_path))
    census.drive(a.parent)
    summary, rows = _pair(a)
    assert (summary["state"], summary["predecessor"], summary["counts"]) == (
        "no_predecessor", None, None)
    assert rows is None and not _files(a).pair_path("csv").exists()
    control, crows = _pair(a.parent / "2025-02-01__RUN_B")
    assert control["state"] == "value" and crows


# --- 2. incremental ------------------------------------------------------------------

def test_a_new_run_computes_one_census_and_one_pair(tmp_path):
    model = _model(tmp_path)
    a, b, c = _three(model)
    assert len(_computed(census.drive(model))) == 6
    d = _dated_run(model, "2025-04-01", "RUN_D")
    _view(d, 1, eid=8)
    assert _computed(census.drive(model)) == [("census", d.name), ("pair", d.name)]
    assert _pair(d)[0]["predecessor"]["run_id"] == "RUN_C"


def test_a_clean_tree_recomputes_nothing(tmp_path):
    model = _model(tmp_path)
    _three(model)
    census.drive(model)
    before = dict((p, p.read_bytes()) for p in model.rglob("element_*"))
    actions = census.drive(model)
    assert _computed(actions) == [] and len(actions) == 6
    assert dict((p, p.read_bytes()) for p in model.rglob("element_*")) == before


def _recapture(run, n=1):
    """A view's capture rewritten and the run re-gridded, as a recapture and
    stage_a_analyze leave it."""
    model_path = _capture_dir(run) / "V_{0}.json".format(n)
    side = json.loads(model_path.read_text())
    side["recapture"] = {"captured_at": "later"}
    model_path.write_text(json.dumps(side))
    reg.register(model_path.with_name("V_{0}_anno.json".format(n)))
    grid.grid_view(model_path, _grid_dir(run))


def test_recapturing_a_middle_run_recomputes_its_census_and_the_two_pairs_around_it(tmp_path):
    model = _model(tmp_path)
    a, b, c = _three(model)
    census.drive(model)
    _recapture(b)
    actions = census.drive(model)
    assert _computed(actions) == [("census", b.name), ("pair", b.name), ("pair", c.name)]
    why = [x["why"] for x in actions if x["step"] == "census" and x["run"] == b.name][0]
    assert "changed" in why and "V_1.json" in why
    assert _census(b)[0]["figures"]["views_measured"] == 2       # still gridded


def test_backfilling_a_date_recomputes_the_new_run_and_the_pair_after_it(tmp_path):
    model = _model(tmp_path)
    a = _dated_run(model, "2025-01-01", "RUN_A")
    _view(a, 1)
    c = _dated_run(model, "2025-03-01", "RUN_C")
    _view(c, 1, eid=8)
    census.drive(model)
    assert _pair(c)[0]["predecessor"]["run_id"] == "RUN_A"
    b = _dated_run(model, "2025-02-01", "RUN_B")
    _view(b, 1)
    actions = census.drive(model)
    assert _computed(actions) == [("census", b.name), ("pair", b.name), ("pair", c.name)]
    why = [x["why"] for x in actions if x["step"] == "pair" and x["run"] == c.name][0]
    assert "predecessor is now {0}".format(b.name) in why
    summary, rows = _pair(c)
    assert summary["predecessor"]["run_id"] == "RUN_B"
    assert rows["host:{0}".format(EID)]["class"] == "gone"


def test_the_predecessor_is_the_previous_as_of_date_not_run_id_or_folder_order(tmp_path):
    """run_ids sort the other way from the dates, and two folder names'
    date prefixes disagree with run_meta, so folder order differs too."""
    model = _model(tmp_path)
    z1 = _dated_run(model, "2025-01-01", "Z1")
    a3 = _dated_run(model, "2025-03-01", "A3", name="2025-01-20__A3")
    m2 = _dated_run(model, "2025-02-01", "M2", name="2025-03-05__M2")
    for run, eid in ((z1, EID), (a3, 9), (m2, 8)):
        _view(run, 1, eid=eid)
    census.drive(model)
    assert [p.name for p in sorted((z1, a3, m2))] == ["2025-01-01__Z1", "2025-01-20__A3",
                                                      "2025-03-05__M2"]
    assert _pair(z1)[0]["state"] == "no_predecessor"
    assert _pair(m2)[0]["predecessor"]["run_id"] == "Z1"
    assert _pair(a3)[0]["predecessor"]["run_id"] == "M2"
    assert _pair(a3)[1]["host:8"]["class"] == "gone"


def test_two_runs_on_one_as_of_date_refuse_their_pairs_by_name(tmp_path):
    model = _model(tmp_path)
    a = _dated_run(model, "2025-01-01", "RUN_A")
    _view(a, 1)
    b1 = _dated_run(model, "2025-02-01", "RUN_B1")
    _view(b1, 1)
    b2 = _dated_run(model, "2025-02-01", "RUN_B2")
    _view(b2, 1)
    c = _dated_run(model, "2025-03-01", "RUN_C")
    _view(c, 1)
    assert census.main(["--root", str(model)]) == 1
    for run in (b1, b2):
        summary, rows = _pair(run)
        assert summary["state"] == "refused" and rows is None
        assert b1.name in summary["reason"] and b2.name in summary["reason"]
        assert "two runs on as-of date 2025-02-01" in summary["reason"]
    summary, rows = _pair(c)
    assert summary["state"] == "refused" and "previous as-of date 2025-02-01" in summary["reason"]
    assert _pair(a)[0]["state"] == "no_predecessor"                    # control
    assert _census(b1)[0]["state"] == "value"                          # the census is not


def test_a_refused_census_refuses_the_pairs_beside_it_and_stays_fresh(tmp_path):
    model = _model(tmp_path)
    a, b, c = _three(model)
    path = _grid_dir(b) / "V_1.grid.json"
    rec = json.loads(path.read_text())
    rec.pop("ec_keys")
    path.write_text(json.dumps(rec))
    census.drive(model)
    summary, rows = _census(b)
    assert summary["state"] == "refused" and rows is None
    assert summary["refused"].startswith("grid_predates_ec_keys")
    for run in (b, c):
        ps, prow = _pair(run)
        assert ps["state"] == "refused" and "the census of {0} is refused".format(
            b.name) in ps["reason"] and prow is None
    assert _computed(census.drive(model)) == []                       # not recomputed forever


# --- 3. the summary is the write-last completion mark ---------------------------------

@pytest.mark.parametrize("which", ["census", "pair"])
def test_a_failed_csv_write_leaves_no_summary_of_the_previous_one(tmp_path, monkeypatch,
                                                                  which):
    model = _model(tmp_path)
    _a, b, _c = _three(model)
    census.drive(model)
    f = _files(b)
    summary = f.census_path("summary") if which == "census" else f.pair_path("summary")
    target = f.census_csv if which == "census" else f.pair_csv
    assert summary.exists()                                           # control
    real = census._write_csv

    def _fail(path, columns, rows):
        if Path(path).name == target and Path(path).parent == f.grid_dir:
            raise OSError("disk full")
        return real(path, columns, rows)
    monkeypatch.setattr(census, "_write_csv", _fail)
    _recapture(b)
    with pytest.raises(OSError):
        census.drive(model)
    assert not summary.exists()


def test_the_summaries_name_the_csvs_written(tmp_path):
    model = _model(tmp_path)
    _a, b, _c = _three(model)
    census.drive(model)
    f = _files(b)
    for rec_path, csv_path in ((f.census_path("summary"), f.census_path("csv")),
                               (f.pair_path("summary"), f.pair_path("csv"))):
        rec = json.loads(rec_path.read_text())
        assert rec["csv"] == csv_path.name
        assert rec["csv_sha256"] == grid.sha256_file(csv_path)
        assert rec["tool_version"] == census.TOOL_VERSION
    rec = json.loads(f.census_path("summary").read_text())
    for rel, sha in rec["inputs"].items():
        assert grid.sha256_file(f.run_dir / rel) == sha, rel
    names = sorted(rec["inputs"])
    assert "run_meta.json" in names and "color_id_buffer/V_1.json" in names
    assert "analysis_grid/V_1.grid.json" in names and "analysis_grid/V_1.grid.npz" in names
    assert "color_id_buffer/V_1_anno.registered.json" in names


def test_a_refused_census_records_why_in_the_file_and_a_clean_one_records_none(tmp_path):
    model = _model(tmp_path)
    good = _dated_run(model, "2025-01-01", "RUN_A")
    _view(good, 1)
    bad = _dated_run(model, "2025-02-01", "RUN_B")
    _view(bad, 1)
    meta_path = bad / "run_meta.json"
    meta = json.loads(meta_path.read_text())
    meta["finalized"] = False
    meta_path.write_text(json.dumps(meta))
    census.drive(model)
    rec_bad, _r = _census(bad)
    rec_good, _r = _census(good)
    assert rec_bad["state"] == "refused" and "finalized" in rec_bad["refused"]
    assert rec_bad["csv"] is None and not _files(bad).census_path("csv").exists()
    assert "refused" in rec_good and rec_good["refused"] is None      # present, not absent
    assert rec_good["state"] == "value"


# --- 4. layouts ------------------------------------------------------------------------

def test_a_v1_and_a_v2_run_with_one_content_give_one_row_set(tmp_path):
    v1 = _dated_run(tmp_path / "m1", "2025-01-01", "RUN_V1")
    v2 = _dated_run(tmp_path / "m2", "2025-01-01", "RUN_V2", layout="v2")
    for run in (v1, v2):
        _view(run, 1)
        _view(run, 2, eid=8, link={"11:500": _link_entry(11, 500)})
        assert census.main([str(run)]) == 0
    assert (v2 / "grid" / "element_census_2025-01-01.csv").is_file()
    assert (v2 / "grid" / "element_census_2025-01-01.summary.json").is_file()
    assert (v1 / "analysis_grid" / "element_census.csv").is_file()
    s1, r1 = _census(v1)
    s2, r2 = _census(v2)

    def _strip(rows):
        return dict((k, dict((c, v) for c, v in r.items() if c != "run_id"))
                    for k, r in rows.items())
    assert len(r1) == 3 and _strip(r1) == _strip(r2)
    f1 = dict((k, v) for k, v in s1["figures"].items() if k not in ("run_id", "run_dir"))
    f2 = dict((k, v) for k, v in s2["figures"].items() if k not in ("run_id", "run_dir"))
    assert f1 == f2 and (s1["layout"], s2["layout"]) == ("v1", "v2")


def test_a_mixed_v1_v2_model_folder_pairs_by_date(tmp_path):
    model = _model(tmp_path)
    a = _dated_run(model, "2025-01-01", "RUN_A")
    _view(a, 1)
    b = _dated_run(model, "2025-02-01", "RUN_B", layout="v2")
    _view(b, 1, eid=8)
    census.drive(model)
    assert (b / "grid" / "element_transitions_2025-02-01.csv").is_file()
    assert (a / "analysis_grid" / "element_transitions.summary.json").is_file()
    summary, rows = _pair(b)
    assert summary["predecessor"]["run_id"] == "RUN_A"
    assert (rows["host:{0}".format(EID)]["class"], rows["host:8"]["class"]) == (
        "gone", "created")


def test_the_names_are_the_layouts(tmp_path):
    assert ol.census_names(ol.V1, "x") == (
        "element_census.csv", "element_census.summary.json")
    assert ol.transitions_names(ol.V2, "2025-07-02") == (
        "element_transitions_2025-07-02.csv", "element_transitions_2025-07-02.summary.json")


# --- 5. the runs index -----------------------------------------------------------------

def test_the_runs_index_names_each_runs_census_and_transitions(tmp_path):
    root = tmp_path / "Exports"
    model = root / "KSRF" / "Model"
    model.mkdir(parents=True)
    a = _dated_run(model, "2025-01-01", "RUN_A")
    _view(a, 1)
    b = _dated_run(model, "2025-02-01", "RUN_B", layout="v2")
    _view(b, 1)
    c = _dated_run(model, "2025-03-01", "RUN_C")
    _view(c, 1)
    census.drive(model)
    (c / "analysis_grid" / "element_census.csv").write_text("tampered\n")
    rows, _j, _s = idx.build_index(root)
    by = dict((r["run_id"], r) for r in rows)
    ra, rb, rc = by["RUN_A"], by["RUN_B"], by["RUN_C"]
    assert (ra["census_state"], ra["census_csv_path_rel"]) == (
        "value", "KSRF/Model/2025-01-01__RUN_A/analysis_grid/element_census.csv")
    assert (ra["transitions_state"], ra["transitions_csv_path_rel"]) == ("no_predecessor", "")
    assert ra["transitions_summary_path_rel"].endswith(
        "analysis_grid/element_transitions.summary.json")
    assert (rb["census_state"], rb["transitions_state"]) == ("value", "value")
    assert rb["census_csv_path_rel"].endswith("grid/element_census_2025-02-01.csv")
    assert rb["transitions_csv_path_rel"].endswith("grid/element_transitions_2025-02-01.csv")
    assert rc["census_state"] == "csv_mismatch" and "sha256" in rc["census_reason"]
    assert rc["census_csv_path_rel"] == ""
    assert idx.COLUMNS[-8:] == [
        "census_state", "census_reason", "census_csv_path_rel", "census_summary_path_rel",
        "transitions_state", "transitions_reason", "transitions_csv_path_rel",
        "transitions_summary_path_rel"]


# --- 6. the series, as BI builds it from the per-run and pair files ---------------------

def test_first_last_seen_and_enter_leave_from_the_files_are_the_series_csv(tmp_path):
    """The rules tools/notes/ELEMENT_CENSUS_2025-02-03_to_2025-11-01.md §8
    gives the BI (MIN / MAX of as_of_date over the element tables; enter /
    leave = the class in the first / next pair), run in Python over the
    FILES, equal the series CSV the --out census writes."""
    model = _model(tmp_path)
    a, b, c = _three(model)
    d = _dated_run(model, "2025-04-01", "RUN_D")
    _view(d, 1, eid=8)
    _view(d, 2)
    runs = (a, b, c, d)
    census.drive(model)
    out = tmp_path / "out"
    census.main([str(r) for r in runs] + ["--out", str(out)])
    series = dict((r["element_key"], r) for r in _rows(out / census.SERIES_CSV))
    seen, pair_class = {}, {}
    for run in runs:
        _s, els = _census(run)
        date = _s["as_of_date"]
        for key in els:
            seen.setdefault(key, []).append(date)
        _ps, prow = _pair(run)
        for key, r in (prow or {}).items():
            pair_class[(key, date)] = r["class"]
    dates = sorted(_census(r)[0]["as_of_date"] for r in runs)
    enter_of = dict(census.ENTER_OF)
    leave_of = dict(census.LEAVE_OF)
    assert set(seen) == set(series)
    for key, ds in seen.items():
        first, last = min(ds), max(ds)
        enter = ("series_start" if first == dates[0]
                 else enter_of[pair_class[(key, first)]])
        nxt = [x for x in dates if x > last]
        leave = "series_end" if not nxt else leave_of[pair_class[(key, nxt[0])]]
        s = series[key]
        assert (s["first_as_of_date"], s["last_as_of_date"], s["runs_seen"]) == (
            first, last, str(len(ds))), key
        assert (s["first_how"], s["last_how"]) == (enter, leave), key
    hows = set(s["first_how"] for s in series.values())
    assert hows >= {"series_start", "created", "new_view"}
    assert "unmeasured" in set(s["last_how"] for s in series.values())
