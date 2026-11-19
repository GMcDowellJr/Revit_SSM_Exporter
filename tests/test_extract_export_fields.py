"""tools/extract_export_fields.py: one row per sidecar, this run's only.

Two kinds of test. The synthetic run folder pins the selection rules (a model
and an annotation sidecar kept, a run-level file and a malformed JSON
skipped, a carried view skipped, and a co-sized sidecar giving ONE row, not
five -- the 33 duplicated pairs of run 20261118T1002). The composition test
runs the real registered capture against the fake Revit and extracts from the
files IT wrote, so a field the producer renames turns a column empty here
rather than silently on Greg's machine (CLAUDE.md, defect class 1).
"""
import csv
import json
import os

import pytest

from tools import extract_export_fields as eef


def _frame(px, w, h, attempts=None, **extra):
    attempts = attempts or [{"requested_px": px, "accepted_px": px, "actual_w": w,
                             "actual_h": h, "dim_check": "pass", "dim_read_error": None}]
    out = {"fit_direction": "horizontal", "pixel_size": px, "actual_w": w, "actual_h": h,
           "dim_check": "pass", "dim_check_attempts": attempts,
           "backoff_stop_reason": None, "view_scale": 48.0}
    out.update(extra)
    return out


def _summary(px, w, h):
    # stage_a_registered_capture._frame_summary's keys: frame-shaped.
    return {"pixel_size": px, "actual_w": w, "actual_h": h, "dim_check": "pass",
            "cap_applied": False, "co_sized_fit_px": px,
            "dim_check_attempts": [{"requested_px": px, "actual_w": w, "actual_h": h}]}


def _co_sized_marks():
    co_sizing = {
        "state": "applies", "attempts": 2, "fit_px": 8153,
        "first_attempt": {"model_frame": _summary(9118, 9118, 11000),
                          "annotation_frame": _summary(9118, 9118, 11163)},
        "second_attempt": {"model_frame": _summary(8153, 8153, 9982),
                           "annotation_frame": _summary(8153, 8153, 9999)}}
    return {
        "co_sizing": co_sizing,
        "timings_ms": {"model_export": 22000.0, "model_export.export_image": 21000.0,
                       "model_export.list_stage": 1.0, "model_export.move": 900.0,
                       "model_export.read_dims": 2.0, "model_export.cleanup": 3.0,
                       "annotation_export": 1000.0},
        "export_telemetry": [
            {"pass": "model", "capture_attempt": 1, "export_image_ordinal": 1,
             "first_export_image_for_view": True, "file_bytes": 10, "listed_to_moved_ms": 5.0,
             "previous_export_view_id": 10, "gap_since_previous_export_ms": 900.0},
            {"pass": "annotation", "capture_attempt": 1, "export_image_ordinal": 2,
             "first_export_image_for_view": False, "file_bytes": 11, "listed_to_moved_ms": 1.0},
            {"pass": "model", "capture_attempt": 2, "export_image_ordinal": 3,
             "first_export_image_for_view": False, "file_bytes": 12, "listed_to_moved_ms": 7.0},
            {"pass": "annotation", "capture_attempt": 2, "export_image_ordinal": 4,
             "first_export_image_for_view": False, "file_bytes": 13, "listed_to_moved_ms": 2.0}]}


def _write(path, payload):
    with open(path, "w") as handle:
        if isinstance(payload, str):
            handle.write(payload)
        else:
            json.dump(payload, handle)


@pytest.fixture
def run_dir(tmp_path):
    cap = tmp_path / "color_id"
    cap.mkdir()
    _write(tmp_path / "run_meta_2026-11-18.json", {"run_id": "R", "views": [
        {"view_id": 11, "view_name": "LEVEL 1 - Plan", "capture_status": "success"},
        {"view_id": 22, "view_name": "Carried", "capture_status": "unchanged"}]})
    marks = _co_sized_marks()
    _write(cap / "LEVEL 1 - Plan_11.json", {
        "view_id": 11, "registration_marks": marks,
        "frame": _frame(8153, 8153, 9982, pre_cap_px=9118, cap_applied=False)})
    _write(cap / "LEVEL 1 - Plan_11_anno.json", {
        "view_id": 11, "pass": "annotation", "registration_marks": marks,
        "frame": _frame(8153, 8153, 9999)})
    _write(cap / "Carried_22.json", {"view_id": 22, "frame": _frame(100, 100, 50)})
    # Not sidecars: a run-level/derived file, and a malformed JSON.
    _write(cap / "LEVEL 1 - Plan_11_anno.reg.json", {"view_id": 11, "frame": _frame(1, 1, 1)})
    _write(cap / "Broken_33.json", "{\"view_id\": 33, \"frame\": ")
    return tmp_path


def test_one_row_per_sidecar_and_only_this_runs(run_dir):
    rows, report = eef.extract(str(run_dir))
    assert [(r["pass"], r["view_id"]) for r in rows] == [("model", 11), ("anno", 11)]
    assert report["skipped_status"] == 1
    assert report["skipped_not_sidecar"] == ["LEVEL 1 - Plan_11_anno.reg.json"]
    assert len(report["malformed"]) == 1 and report["malformed"][0].startswith("Broken_33.json")


def test_a_co_sized_sidecar_gives_its_own_frame_not_the_attempt_summaries(run_dir):
    rows, _ = eef.extract(str(run_dir))
    model, anno = rows
    # The top-level frame, never a co_sizing.*_attempt summary.
    assert (model["pixel_size"], model["actual_h"], model["pre_cap_px"]) == (8153, 9982, 9118)
    assert (anno["pixel_size"], anno["actual_h"]) == (8153, 9999)
    assert model["co_sizing_state"] == "applies" and model["co_sizing_attempts"] == 2
    assert model["view_name"] == "LEVEL 1 - Plan"


def test_the_export_cells_are_the_rows_own_pass(run_dir):
    model, anno = eef.extract(str(run_dir))[0]
    assert model["export_ms"] == 22000.0 and model["export_image_ms"] == 21000.0
    assert model["move_ms"] == 900.0
    assert model["n_export_images"] == 2
    assert model["first_export_image_for_view"] is True
    assert model["export_image_ordinal"] == 1
    assert model["file_bytes"] == 12            # the final attempt's file
    assert model["listed_to_moved_ms"] == 12.0  # summed, like the sub-keys
    assert model["gap_since_previous_export_ms"] == 900.0
    assert anno["export_ms"] == 1000.0 and anno["export_image_ms"] is None
    assert anno["export_image_ordinal"] == 2 and anno["file_bytes"] == 13


def test_all_statuses_keeps_the_carried_view_and_says_so(run_dir):
    rows, report = eef.extract(str(run_dir), all_statuses=True)
    carried = [r for r in rows if r["view_id"] == 22]
    assert len(carried) == 1 and carried[0]["capture_status"] == "unchanged"
    assert report["skipped_status"] == 0


def test_main_writes_the_csv_and_prints_the_counts(run_dir, tmp_path, capsys):
    out = tmp_path / "export_fields.csv"
    assert eef.main([str(run_dir), str(out)]) == 0
    with open(out, newline="") as handle:
        got = list(csv.DictReader(handle))
    assert len(got) == 2
    assert list(got[0].keys()) == list(eef.COLUMNS) + list(eef.ADDED_COLUMNS)
    printed = capsys.readouterr()
    assert printed.out.startswith("rows 2 (")
    assert "malformed: Broken_33.json" in printed.err


def test_content_cells_read_the_graphics_state_envelope():
    assert eef.content_cells({"near_face_w_map": [1, 2, 3], "view_graphics_state": {
        "link_instances": {"state": "value", "value": [{}, {}]}}}) == {
        "near_face_w_count": 3, "link_instance_count": 2}
    assert eef.content_cells({"view_graphics_state": {"link_instances": {
        "state": "not_applicable", "reason": "none"}}})["link_instance_count"] == 0
    assert eef.content_cells({"view_graphics_state": {"link_instances": {
        "state": "unavailable", "reason": "x"}}})["link_instance_count"] is None


def test_no_capture_folder_is_refused(tmp_path):
    assert eef.main([str(tmp_path), str(tmp_path / "x.csv")]) == 2


# --- composition with the real producer ----------------------------------

def test_every_field_is_read_from_what_the_producer_writes(tmp_path):
    from tests.test_stage_a_registered_capture import _run

    def _setup(cfg):
        cfg._stage_a_run_dir = "compose"
    out, _v, _d, _e, _diag = _run(tmp_path, cfg_setup=_setup)
    run_dir = os.path.dirname(os.path.dirname(out["sidecar_path"]))
    _write(os.path.join(run_dir, "run_meta.json"), {"run_id": "R", "views": [
        {"view_id": out["view_id"], "view_name": "From run_meta",
         "capture_status": "success"}]})
    rows, report = eef.extract(run_dir)
    assert report["malformed"] == [] and report["skipped_not_sidecar"] == []
    by_pass = dict((r["pass"], r) for r in rows)
    assert sorted(by_pass) == ["anno", "model"]
    with open(out["sidecar_path"]) as handle:
        frame = json.load(handle)["frame"]
    model = by_pass["model"]
    assert model["pixel_size"] == frame["pixel_size"]
    assert (model["actual_w"], model["actual_h"]) == (frame["actual_w"], frame["actual_h"])
    # Absent BY DESIGN, so allowed empty: backoff_stop_reason on a passing
    # export, and the annotation frame's pre_cap_px / cap_applied (its frame
    # records achieved values only and is sized from the model pass's).
    allowed_empty = {"model": {"backoff_stop_reason"},
                     "anno": {"backoff_stop_reason", "pre_cap_px", "cap_applied",
                              # model-sidecar content: not in the annotation's
                              "near_face_w_count", "link_instance_count"}}
    for name, row in by_pass.items():
        for column in eef.COLUMNS + eef.ADDED_COLUMNS:
            if column in allowed_empty[name]:
                continue
            if column in ("previous_export_view_id", "gap_since_previous_export_ms") \
                    and name == "model":
                continue  # None for the process's first export: a fresh ledger
            assert row[column] not in (None, ""), (name, column)
        assert row["view_name"] == "From run_meta"
        assert row["n_export_images"] == 1
        for part in eef.EXPORT_PARTS:
            assert row[part + "_ms"] is not None
    assert by_pass["model"]["near_face_w_count"] >= 1
    assert by_pass["model"]["link_instance_count"] == 0  # the fake has no links
