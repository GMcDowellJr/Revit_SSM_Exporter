"""tools/find_recapture_views.py on the shared fixture: a registered view
(keep), one whose ticks drew in neither image (recapture), one never
registered (recapture: ticks drawn, no record), and one run_meta records
as failed (not this run's, not counted)."""

import csv
import json

from tests.test_stage_a_grid_rollup import _capture, _outcome, _run
from tools import find_recapture_views as finder


def test_verdicts_and_the_recapture_list(tmp_path):
    run = _run(tmp_path)
    _capture(run, 1, gridded=False, pair_kw={"shared_anno": True})
    every_tick = tuple(m["key"] for m in
                       json.loads((run / "color_id_buffer" / "V_1.json").read_text())
                       ["registration_marks"]["marks"])
    _capture(run, 2, gridded=False,
             pair_kw={"drop_anno": every_tick, "drop_model": every_tick, "shared_anno": True})
    _capture(run, 3, gridded=False, register=False, pair_kw={"shared_anno": True})
    _capture(run, 4, gridded=False, pair_kw={"shared_anno": True})
    _outcome(run, 4, status="failed", reason="export raised")
    out = tmp_path / "find"
    assert finder.main([str(run), "--out", str(out)]) == 0
    rows = dict((r["view_id"], r) for r in csv.DictReader(
        open(str(out / finder.CSV_NAME), encoding="utf-8", newline="")))
    assert rows["1"]["verdict"] == "keep"
    assert rows["2"]["verdict"] == "recapture"
    assert "0 / 0 ticks found of 12 painted" in rows["2"]["reason"]
    assert rows["2"]["marks_painted"] == "" and rows["2"]["marks_expected"] == "12"
    assert rows["1"]["scanned_dir"] == str(run) and rows["1"]["run_dir"] == str(run)
    assert rows["3"]["verdict"] == "recapture" and "no registration record" in rows["3"]["reason"]
    assert rows["4"]["verdict"] == "not_this_run"
    lists = json.loads((out / finder.LISTS_NAME).read_text())["runs"]
    assert len(lists) == 1
    assert lists[0]["run_id"] == "RUN_A" and lists[0]["recapture"] == [2, 3]
    assert lists[0]["review"] == [] and lists[0]["keep"] == 1


def test_a_residual_refusal_is_review_not_recapture():
    row = {"provenance": "this_run", "registration_state": "refused", "marks_expected": "12",
           "anno_found_count": "12", "model_found_count": "12",
           "registration_reason": "annotation capture: worst tick residual 127.12 px exceeds 2.0 px"}
    assert finder.verdict(row) == ("review", "refused with every tick found: annotation capture: "
                                             "worst tick residual 127.12 px exceeds 2.0 px")
    row.update(anno_found_count="1", model_found_count="1")
    assert finder.verdict(row)[0] == "recapture"


def test_found_ticks_are_compared_with_the_ticks_painted_not_planned():
    """Codex, PR #236: a tick whose override failed never drew; relocating
    the ticks would not change that."""
    base = {"provenance": "this_run", "registration_state": "refused", "marks_expected": "12",
            "registration_reason": "2 of 12 ticks found"}
    # Ten painted, ten found: nothing a recapture fixes.
    row = dict(base, marks_painted="10", anno_found_count="10", model_found_count="10")
    assert finder.verdict(row) == ("review", "10 of 12 ticks painted (overrides failed), every "
                                             "painted tick found: 2 of 12 ticks found")
    # Ten painted, two found: recapture, counted against the ten.
    row = dict(base, marks_painted="10", anno_found_count="2", model_found_count="2")
    assert finder.verdict(row) == ("recapture", "2 / 2 ticks found of 10 painted: 2 of 12 "
                                                "ticks found")
    # Painted unknown (an older record): the planned count stands.
    row = dict(base, marks_painted="", anno_found_count="2", model_found_count="2")
    assert finder.verdict(row)[0] == "recapture"
    # No record at all: planned but none painted is review, not recapture.
    absent = {"provenance": "this_run", "registration_state": "absent", "marks_expected": "12"}
    assert finder.verdict(dict(absent, marks_painted="0"))[0] == "review"
    assert finder.verdict(dict(absent, marks_painted="12"))[0] == "recapture"
    assert finder.verdict(dict(absent, marks_painted=""))[0] == "recapture"


def test_rows_join_their_own_run_even_when_two_runs_share_no_run_id(tmp_path):
    """Codex, PR #236: joined by run_id, two refused runs (both None) took
    every row of the other."""
    a = _run(tmp_path / "one")
    b = _run(tmp_path / "two")
    _capture(a, 1, gridded=False, pair_kw={"shared_anno": True})
    _capture(b, 2, gridded=False, pair_kw={"shared_anno": True})
    _capture(b, 3, gridded=False, pair_kw={"shared_anno": True})
    for run in (a, b):
        (run / "run_meta.json").unlink()
    lists, rows = finder.find([str(a), str(b)])
    assert [e["run_id"] for e in lists] == [None, None]
    by_dir = {}
    for r in rows:
        by_dir.setdefault(r["scanned_dir"], []).append(r["view_id"])
    assert by_dir == {str(a): [1], str(b): [2, 3]}
    assert len(rows) == 3


def test_run_dir_is_the_runs_recorded_directory_and_the_scan_path_is_kept(tmp_path):
    run = _run(tmp_path)
    _capture(run, 1, gridded=False, pair_kw={"shared_anno": True})
    meta = json.loads((run / "run_meta.json").read_text())
    meta["run_dir"] = "C:\\Exports\\KSRF\\2025-06-21__RUN_A"
    (run / "run_meta.json").write_text(json.dumps(meta))
    lists, rows = finder.find([str(run)])
    assert lists[0]["run_dir"] == "C:\\Exports\\KSRF\\2025-06-21__RUN_A"
    assert lists[0]["scanned_dir"] == str(run)
    assert rows[0]["run_dir"] == lists[0]["run_dir"]
