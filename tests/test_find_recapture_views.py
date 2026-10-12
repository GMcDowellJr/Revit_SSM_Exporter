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
    assert rows["2"]["verdict"] == "recapture" and "0 / 0 ticks found of 12" in rows["2"]["reason"]
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
