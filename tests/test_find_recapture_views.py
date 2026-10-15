"""tools/find_recapture_views.py on the shared fixture: a registered view
(keep), one whose ticks drew in neither image (recapture), one never
registered (recapture: ticks drawn, no record), and one run_meta records
as failed (capture_failed: listed, in the recapture list only on
--include-failed)."""

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
    assert rows["4"]["verdict"] == "capture_failed"
    assert rows["4"]["reason"] == "capture_status failed: export raised"
    assert rows["4"]["provenance"] == "not_this_run"
    lists = json.loads((out / finder.LISTS_NAME).read_text())["runs"]
    assert len(lists) == 1
    assert lists[0]["run_id"] == "RUN_A" and lists[0]["recapture"] == [2, 3]
    assert lists[0]["review"] == [] and lists[0]["keep"] == 1
    assert lists[0]["failed"] == [{"view_id": 4, "view_name": "V 4", "capture_status": "failed",
                                  "reason": "export raised"}]
    assert lists[0]["include_failed"] is False


def test_failed_captures_are_listed_and_recaptured_only_on_the_flag(tmp_path):
    """2025-09-13 to 2025-10-25: six 1/48 plans failed capture in every
    run, each an older sidecar or none at all; the analysis never grids them
    and the series shows a measure lost. The finder names them in every
    run's entry; --include-failed puts them in the recapture list."""
    run = _run(tmp_path)
    _capture(run, 1, gridded=False, pair_kw={"shared_anno": True})      # keep
    _capture(run, 4, gridded=False, pair_kw={"shared_anno": True})      # stale sidecar
    _outcome(run, 4, status="failed", reason="export raised")
    _outcome(run, 5, status="annotation_failed", reason="annotation pass raised")  # no sidecar
    _capture(run, 6, gridded=False, pair_kw={"shared_anno": True})
    _outcome(run, 6, status="registration_failed", reason="registration_marks_outside_crop_loop")
    _outcome(run, 7, status="success")                                   # no sidecar, not failed
    out = tmp_path / "find"
    assert finder.main([str(run), "--out", str(out)]) == 0
    rows = dict((r["view_id"], r) for r in csv.DictReader(
        open(str(out / finder.CSV_NAME), encoding="utf-8", newline="")))
    assert set(rows) == {"1", "4", "5", "6"}
    assert rows["1"]["verdict"] == "keep"
    assert rows["4"]["verdict"] == "capture_failed" and rows["4"]["provenance"] == "not_this_run"
    assert rows["4"]["view_stem"] == "V_4"            # the sidecar's own columns stay
    assert rows["5"]["verdict"] == "capture_failed" and rows["5"]["provenance"] == "no_sidecar"
    assert rows["5"]["reason"] == "capture_status annotation_failed: annotation pass raised"
    assert rows["5"]["view_name"] == "V 5" and rows["5"]["run_id"] == "RUN_A"
    assert rows["5"]["as_of_date"] == rows["1"]["as_of_date"]
    assert rows["5"]["registration_state"] == "" and rows["5"]["view_stem"] == ""
    assert rows["6"]["verdict"] == "capture_failed"
    assert rows["6"]["reason"] == ("capture_status registration_failed: "
                                   "registration_marks_outside_crop_loop")
    entry = json.loads((out / finder.LISTS_NAME).read_text())["runs"][0]
    assert entry["recapture"] == [] and entry["keep"] == 1 and entry["review"] == []
    assert [f["view_id"] for f in entry["failed"]] == [4, 5, 6]
    assert entry["failed"][1] == {"view_id": 5, "view_name": "V 5",
                                  "capture_status": "annotation_failed",
                                  "reason": "annotation pass raised"}
    assert entry["include_failed"] is False
    # The flag: the same rows, the ids in the recapture list.
    out2 = tmp_path / "find2"
    assert finder.main([str(run), "--out", str(out2), "--include-failed"]) == 0
    rows2 = dict((r["view_id"], r) for r in csv.DictReader(
        open(str(out2 / finder.CSV_NAME), encoding="utf-8", newline="")))
    assert dict((k, (v["verdict"], v["reason"])) for k, v in rows2.items()) == \
        dict((k, (v["verdict"], v["reason"])) for k, v in rows.items())
    entry2 = json.loads((out2 / finder.LISTS_NAME).read_text())["runs"][0]
    assert entry2["recapture"] == [4, 5, 6] and entry2["include_failed"] is True
    assert entry2["failed"] == entry["failed"] and entry2["keep"] == 1
    # The list is what the recapture consumes: every id is one the run requested.
    from vop_interwoven import recapture as rc
    meta = rc.load_run_meta(str(run))
    meta.update(doc_title={"state": "value", "value": "DOC"}, date="2025-09-13")
    assert rc.recapture_plan(meta, entry2["recapture"], "DOC")["view_ids"] == [4, 5, 6]


def test_failed_captures_pure():
    ident = {"refused": None, "requested": [], "duplicates": {}, "outcomes": {
        "1": {"view_id": 1, "view_name": "ok", "capture_status": "success",
              "capture_failure_reason": ""},
        "9": {"view_id": 9, "view_name": "late", "capture_status": "failed",
              "capture_failure_reason": "timeout"},
        "3": None,                                        # ambiguous: not nameable
        "2": {"view_id": 2, "view_name": "anno", "capture_status": "annotation_failed",
              "capture_failure_reason": None}}}
    assert finder.failed_captures(ident) == [
        {"view_id": 2, "view_name": "anno", "capture_status": "annotation_failed", "reason": ""},
        {"view_id": 9, "view_name": "late", "capture_status": "failed", "reason": "timeout"}]
    assert finder.failed_reason(finder.failed_captures(ident)[0]) == \
        "capture_status annotation_failed: (no reason recorded)"
    assert finder.failed_captures(dict(ident, refused="no run_meta")) == []


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


def test_ticks_the_annotation_itself_covers_are_review_not_recapture():
    """2025-11-01 view 11068525 (H-G010 LOCATION MAP AND VICINITY MAP): two
    annotation symbols span the whole frame, all twelve ticks are placed
    blocked and painted over in the annotation pass, the model pass finds
    all twelve. Relocating ticks cannot fix it."""
    base = {"provenance": "this_run", "registration_state": "refused", "marks_expected": "12",
            "marks_painted": "12", "registration_reason": "annotation capture's marks cannot "
                                                          "be fitted: 0 of 12 ticks found"}
    row = dict(base, marks_blocked="12", anno_found_count="0", model_found_count="12")
    verdict, reason = finder.verdict(row)
    assert verdict == "review" and reason.startswith("every tick blocked")
    # The same signature without the blocked count (an older census row).
    row = dict(base, marks_blocked="", anno_found_count="0", model_found_count="12")
    verdict, reason = finder.verdict(row)
    assert verdict == "review" and reason.startswith("the model pass found every tick")
    # Control: the rotated-crop family, lost in BOTH passes, stays recapture.
    row = dict(base, marks_blocked="0", anno_found_count="0", model_found_count="0")
    assert finder.verdict(row)[0] == "recapture"
    row = dict(base, marks_blocked="3", anno_found_count="1", model_found_count="1")
    assert finder.verdict(row)[0] == "recapture"


OLD_FLOOR_SHAPE = "registration_marks_incomplete;registration_marks_may_not_draw"


def test_views_below_the_capture_floor_are_listed_apart_and_never_recaptured(tmp_path):
    """The policy for views under the floor: unmeasurable, not failed. The
    finder lists them under ``below_floor``, keeps them out of ``recapture``
    even on --include-failed (a recapture would refuse them the same way),
    and tells the old registration_failed shape apart by the sidecar: a
    size refusal in its marks record is the floor, anything else is a
    failed capture whose reason says it is a candidate."""
    from tests.test_stage_a_grid_rollup import TOO_SMALL_FAULTS, _set_registration_faults
    run = _run(tmp_path)
    _capture(run, 1, gridded=False, pair_kw={"shared_anno": True})            # keep
    _outcome(run, 2, status="unmeasurable",
             reason="below_capture_floor: the reference rectangle is 48 px on its short "
                    "side, too small for 32 px ticks inset 24 px (floor 192 px)")
    _capture(run, 3, gridded=False, pair_kw={"shared_anno": True})            # old shape + sidecar
    _outcome(run, 3, status="registration_failed", reason=OLD_FLOOR_SHAPE)
    _set_registration_faults(run, 3, TOO_SMALL_FAULTS)
    _outcome(run, 4, status="registration_failed", reason=OLD_FLOOR_SHAPE)    # no sidecar
    _outcome(run, 5, status="annotation_failed", reason="annotation_export_degenerate")
    _outcome(run, 6, status="failed", reason="export raised")                 # a real failure
    out = tmp_path / "find"
    assert finder.main([str(run), "--out", str(out), "--include-failed"]) == 0
    rows = dict((r["view_id"], r) for r in csv.DictReader(
        open(str(out / finder.CSV_NAME), encoding="utf-8", newline="")))
    assert set(rows) == {"1", "2", "3", "4", "5", "6"}
    assert rows["1"]["verdict"] == "keep"
    assert rows["2"]["verdict"] == "below_capture_floor" and rows["2"]["provenance"] == "no_sidecar"
    assert rows["2"]["reason"].startswith("below_capture_floor (run_meta): capture_status "
                                          "unmeasurable: below_capture_floor: the reference")
    assert rows["3"]["verdict"] == "below_capture_floor" and rows["3"]["provenance"] == "not_this_run"
    assert rows["3"]["reason"].startswith("below_capture_floor (sidecar): capture_status "
                                          "registration_failed: registration_marks_incomplete")
    assert rows["3"]["view_stem"] == "V_3"
    assert rows["4"]["verdict"] == "capture_failed"
    assert "a below-floor candidate" in rows["4"]["reason"] and "no sidecar on disk" in (
        rows["4"]["reason"])
    assert rows["5"]["verdict"] == "below_capture_floor"
    assert "(run_meta): capture_status annotation_failed: annotation_export_degenerate" in (
        rows["5"]["reason"])
    assert rows["6"]["verdict"] == "capture_failed"
    entry = json.loads((out / finder.LISTS_NAME).read_text())["runs"][0]
    assert entry["recapture"] == [4, 6] and entry["include_failed"] is True
    assert [f["view_id"] for f in entry["failed"]] == [4, 6]
    assert [(f["view_id"], f["rule"], f["evidence"]) for f in entry["below_floor"]] == [
        (2, "below_capture_floor", "run_meta"), (3, "below_capture_floor", "sidecar"),
        (5, "annotation_export_degenerate", "run_meta")]
    assert entry["below_floor"][1]["capture_status"] == "registration_failed"
    assert entry["keep"] == 1


def test_below_floor_captures_pure():
    ident = {"refused": None, "requested": [], "duplicates": {}, "outcomes": {
        "1": {"view_id": 1, "view_name": "ok", "capture_status": "success",
              "capture_failure_reason": ""},
        "2": {"view_id": 2, "view_name": "legend", "capture_status": "unmeasurable",
              "capture_failure_reason": "below_capture_floor: 48 px"},
        "3": {"view_id": 3, "view_name": "old", "capture_status": "registration_failed",
              "capture_failure_reason": OLD_FLOOR_SHAPE},
        "4": None}}
    assert finder.below_floor_captures(ident) == [
        {"view_id": 2, "view_name": "legend", "capture_status": "unmeasurable",
         "reason": "below_capture_floor: 48 px", "rule": "below_capture_floor",
         "evidence": "run_meta"}]
    # The old shape is a failed capture until a sidecar decides it.
    assert [f["view_id"] for f in finder.failed_captures(ident)] == [3]
    assert finder.below_floor_captures(dict(ident, refused="no run_meta")) == []
