"""The capture floor (vop_interwoven/capture_floor.py): a view too small
for the twelve registration ticks is REFUSED before anything is exported
and recorded ``unmeasurable``, which the status chain (csv_export ->
run_meta -> views_core) carries as such rather than as a failed capture.

Legends 4622 and 142022 of the Hops_Interior_AR series failed this way in
all 44 runs (2025-02-03..11-01) as ``registration_failed`` with
``registration_marks_incomplete;registration_marks_may_not_draw``, and the
series showed them as a measure lost. The registered capture is driven END
TO END on the fake DB (tests/test_stage_a_registered_capture._run) with a
crop under the floor; what is asserted is that NO ExportImage ran, that the
outcome names the rule with the measured size, and that the ordinary
fixture beside it still exports both passes (the control).

Mutations that must turn this red: dropping the ``raise CaptureBelowFloor``
(the tiny view exports twice and comes back ``registration_failed``);
dropping the ``below_floor`` record from registration_mark_segments (the
capture cannot tell size from any other unavailable layout); routing floor
reasons to ``failed`` in stage_a_capture_status.
"""
import json

from vop_interwoven import capture_floor as floor
from vop_interwoven import stage_a_registration as registration
from vop_interwoven.csv_export import stage_a_capture_status, stage_a_view_result_to_core_row
from vop_interwoven.run_meta import view_outcomes
from vop_interwoven.stage_a_registered_capture import CaptureBelowFloor

from tests.stage_a_capture_fakes import FakeXYZ
from tests.test_stage_a_registered_capture import _run, _sidecar


# --- the rule, as registration_mark_segments records it ----------------------

def test_the_floor_is_three_outer_thirds_of_inset_gap_and_minimum_arm():
    assert registration.mark_floor_px() == 192.0
    assert registration.mark_floor_px(24, 8, 32) == 3.0 * (24 + 8 + 32)


def test_a_rectangle_under_the_floor_carries_the_measured_size():
    # 48 x 190 px at 0.1 ft/px: the short side is the one measured.
    below = registration.registration_mark_segments((0.0, 0.0, 19.0, 4.8), 0.1)
    assert below["state"] == "unavailable" and "too small for" in below["reason"]
    rec = below["below_floor"]
    assert rec["short_side_px"] == 48.0 and rec["floor_px"] == 192.0
    assert rec["extent_px"] == [190.0, 48.0]
    assert rec["reference_uv"] == [0.0, 0.0, 19.0, 4.8] and rec["fpp_ft"] == 0.1
    assert floor.registration_record_below_floor(below) is True


def test_other_unavailable_layouts_carry_no_floor_record():
    """An empty or missing rectangle is not a size refusal: the capture must
    not call it unmeasurable."""
    for bad in (registration.registration_mark_segments(None, 0.1),
                registration.registration_mark_segments((5.0, 5.0, 5.0, 9.0), 0.1),
                registration.registration_mark_segments((0.0, 0.0, 30.0, 30.0), None)):
        assert bad["state"] == "unavailable" and "below_floor" not in bad
        assert floor.registration_record_below_floor(bad) is False
    ok = registration.registration_mark_segments((0.0, 0.0, 20.0, 20.0), 0.1)   # 200 px
    assert ok["state"] == "value" and "below_floor" not in ok


# --- the classifier ------------------------------------------------------------

def test_reason_faults_split_joined_names_and_drop_detail():
    assert floor.reason_faults("a;b") == ["a", "b"]
    assert floor.reason_faults("below_capture_floor: the reference rectangle is 48 px") == [
        "below_capture_floor"]
    assert floor.reason_faults("") == [] and floor.reason_faults(None) == []


def test_floor_fault_is_the_first_fault_only():
    assert floor.floor_fault("annotation_export_degenerate") == "annotation_export_degenerate"
    assert floor.floor_fault("below_capture_floor: 48 px") == "below_capture_floor"
    # A degenerate export behind an earlier fault is that fault's failure.
    assert floor.floor_fault("annotation_collection_failed;annotation_export_degenerate") is None
    assert floor.floor_fault("export_dim_mismatch") is None


def test_floor_rule_new_shape_old_shape_and_candidates():
    assert floor.floor_rule("unmeasurable", "below_capture_floor: 48 px") == {
        "rule": "below_capture_floor", "evidence": "run_meta"}
    assert floor.floor_rule("unmeasurable", "annotation_export_degenerate") == {
        "rule": "annotation_export_degenerate", "evidence": "run_meta"}
    # A build that recorded the rule under a failed status.
    assert floor.floor_rule("annotation_failed", "annotation_export_degenerate") == {
        "rule": "annotation_export_degenerate", "evidence": "run_meta"}
    assert floor.floor_rule("failed", "below_capture_floor: 48 px") == {
        "rule": "below_capture_floor", "evidence": "run_meta"}
    # The old shape is a candidate; the sidecar's marks record decides it.
    old = "registration_marks_incomplete;registration_marks_may_not_draw"
    assert floor.old_shape_candidate("registration_failed", old) is True
    assert floor.old_shape_candidate("registration_failed",
                                     "registration_marks_may_not_draw") is False
    assert floor.old_shape_candidate("annotation_failed", old) is False
    assert floor.floor_rule("registration_failed", old) is None
    # The persisted shape: the sidecar's registration_marks carries the
    # refusal only in the incomplete fault's message (marks [], layout None).
    too_small = {"marks": [], "layout": None, "faults": [
        {"fault": "registration_marks_incomplete",
         "message": "0 of 0 marks drawn: the reference rectangle is 48 px on its short "
                    "side, too small for 32 px ticks inset 24 px"},
        {"fault": "registration_marks_may_not_draw", "message": "... (None)"}]}
    assert floor.floor_rule("registration_failed", old, too_small) == {
        "rule": "below_capture_floor", "evidence": "sidecar"}
    drawn = {"marks": [{"id": 1}], "faults": [
        {"fault": "registration_marks_incomplete", "message": "11 of 12 marks drawn: None"},
        {"fault": "registration_marks_may_not_draw", "message": "the view hides OST_Lines"}]}
    assert floor.floor_rule("registration_failed", old, drawn) is None
    assert floor.floor_rule("registration_failed", old, {"faults": []}) is None
    assert floor.floor_rule("registration_failed", old, None) is None
    # Failed captures stay failed.
    assert floor.floor_rule("failed", "export_dim_mismatch") is None
    assert floor.floor_rule("annotation_failed", "annotation_lattice_mismatch") is None
    assert floor.floor_rule("success", "") is None


# --- the status chain ----------------------------------------------------------

def test_capture_status_is_unmeasurable_for_a_floor_reason_and_failed_otherwise():
    reason = "below_capture_floor: the reference rectangle is 48 px on its short side"
    assert stage_a_capture_status({"success": False, "failure_reason": reason}) == (
        "unmeasurable", reason)
    assert stage_a_capture_status({"success": True, "annotation_pass_success": False,
                                   "annotation_pass_failure_reason":
                                       "annotation_export_degenerate"}) == (
        "unmeasurable", "annotation_export_degenerate")
    # Controls: every other failure keeps its status.
    assert stage_a_capture_status({"success": False, "failure_reason": "boom"}) == (
        "failed", "boom")
    assert stage_a_capture_status({"success": True, "annotation_pass_success": False,
                                   "annotation_pass_failure_reason":
                                       "annotation_lattice_mismatch"}) == (
        "annotation_failed", "annotation_lattice_mismatch")
    summary = {"view_id": 4622, "view_name": "Drawing Index Submission Matrix Legend",
               "success": False, "failure_reason": reason}
    assert view_outcomes([summary]) == [{"view_id": 4622, "view_name": summary["view_name"],
                                         "capture_status": "unmeasurable",
                                         "capture_failure_reason": reason}]
    row = stage_a_view_result_to_core_row(summary, None, None, "RUN_X", config_hash="c0ffee")
    assert row["CaptureStatus"] == "unmeasurable" and row["CaptureFailureReason"] == reason


# --- the capture, end to end ---------------------------------------------------

def _tiny_crop(view):
    # 3 x 2.25 ft at 1/96 and 150 dpi: 56 x 42 px requested; the 64 px floor
    # on the fitted axis re-derives the lattice to 3/64 ft/px, so the short
    # side is 48 px -- under the 192 px the ticks need.
    view.CropBox.Min, view.CropBox.Max = FakeXYZ(20, 15, 0), FakeXYZ(23, 17.25, 0)


def test_a_view_under_the_floor_is_refused_before_any_export(tmp_path):
    out, view, doc, exports, diag = _run(tmp_path, view_setup=_tiny_crop)
    assert exports == [], "nothing may be exported for a view below the floor"
    assert out["success"] is False
    assert out["failure_reason"].startswith("below_capture_floor: the reference rectangle "
                                            "is 48 px on its short side, too small for "
                                            "32 px ticks inset 24 px")
    assert "floor 192 px" in out["failure_reason"]
    assert out["tiff_path"] is None and out["sidecar_path"] is None
    assert "annotation_pass" not in out
    reg = out["registration"]
    assert [f["fault"] for f in reg["faults"]] == ["below_capture_floor"]
    rec = reg["below_capture_floor"]
    assert rec["short_side_px"] == 48.0 and rec["floor_px"] == 192.0
    assert rec["extent_px"] == [64.0, 48.0]
    assert rec["reference_source"] == "authored_crop"
    assert rec["mark_fpp_basis"] == "model_lattice"
    # The group still rolled back and read the view back.
    assert reg["restore"]["rolled_back"] is True
    assert all(v["status"] == "restored" for v in reg["restore"]["view_state"].values())
    assert [i for i in doc._by_id if i > 10 ** 6] == []
    assert stage_a_capture_status(out) == ("unmeasurable", out["failure_reason"])
    assert floor.floor_rule(*stage_a_capture_status(out)) == {
        "rule": "below_capture_floor", "evidence": "run_meta"}
    assert any("below_capture_floor" in str(e.get("message")) for e in diag.errors)


def test_control_the_ordinary_view_still_exports_both_passes_and_predicts_its_extent(tmp_path):
    out, _view, _doc, exports, _diag = _run(tmp_path)
    assert len(exports) == 2 and out["success"] is True
    assert "below_capture_floor" not in out["registration"]
    assert stage_a_capture_status(out) == ("success", "")
    for path in (out["sidecar_path"], out["annotation_sidecar_path"]):
        pred = _sidecar(path)["registration_marks"]["annotation_extent_prediction"]
        assert pred["state"] == "value", path
        # The model crop is 60 x 45 ft at 96 / 1800 ft/px; both annotation
        # bboxes lie inside it, so the union is the crop.
        assert pred["crop_a_px"] == [1125.0, 843.8] and pred["union_px"] == pred["crop_a_px"]
        assert pred["growth"] == [1.0, 1.0] and pred["avoid_rects_used"] == 2
        assert pred["max_axis_px"] == 10000.0 and pred["exceeds_cap"] is False


def test_extent_prediction_is_the_bbox_union_and_says_when_that_exceeds_the_cap():
    """The union, not the export's extent: run 2025-09-06 exported at crop
    A's size with a union 1.49x crop A (the function's docstring)."""
    crop = (0.0, 0.0, 300.0, 200.0)                       # 10000 x 6667 px at 0.03
    pred = registration.annotation_extent_prediction(
        crop, [(1, [0.0, -60.0, 300.0, 260.0])], 0.03, 10000)
    assert pred["crop_a_px"] == [10000.0, 6666.7] and pred["union_px"] == [10000.0, 10666.7]
    assert pred["growth"] == [1.0, 1.6] and pred["exceeds_cap"] is True
    inside = registration.annotation_extent_prediction(crop, [(1, [10, 10, 20, 20])], 0.03, 10000)
    assert inside["exceeds_cap"] is False and inside["union_px"] == inside["crop_a_px"]
    assert registration.annotation_extent_prediction(None, [], 0.03, 10000)["state"] == "unavailable"
    assert registration.annotation_extent_prediction(crop, [], 0.03, None)["exceeds_cap"] is None


def test_the_refusal_is_a_value_error_like_the_recapture_refusal():
    assert issubclass(CaptureBelowFloor, ValueError)
    assert json.dumps(floor.FLOOR_RULES)      # the rule table is serialisable
