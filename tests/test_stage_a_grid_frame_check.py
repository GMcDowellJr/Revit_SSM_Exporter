"""The grid's frame check (tools/stage_a_grid.py ``frame_check``).

Run 20261001T084840_1d0b0c1: 31 views found no ticks, gridded on the nominal
crop mapping, and exported a different size on the axis Revit did NOT fit to
(MOHAVE 11999340: 639 px against the frame's 729). dim_check passed them: it
measures the fit axis only. Greg, 2026-10-01: report and flag, never refuse.

Composed with the real producers (CLAUDE.md, defect class 1): the shared
registration fixture, re-encoded to the current ``frame`` shape, registered
by ``register_stage_a_annotation.register``, gridded by
``stage_a_grid.grid_view`` and rolled up by ``stage_a_grid_rollup.main``.
Assertions read the grid.json and the CSV those tools WROTE (class 4), and
each flagged case has an unflagged control beside it.
"""
import csv
import json

from tools import register_stage_a_annotation as reg
from tools import stage_a_grid as grid
from tools import stage_a_grid_rollup as rollup

from tests.test_register_stage_a_annotation import MH, MODEL_BOUNDS, MW
from tests.test_stage_a_grid import _run_meta
from tests.test_stage_a_grid import _write_pair
from tests.test_stage_a_grid_rollup import _capture, _run


def _frame(**overrides):
    """A current-shape model frame for the fixture's model image, which was
    drawn MW x MH over MODEL_BOUNDS, fit horizontally. ``None`` drops a key."""
    frame = {"status": "value", "sizing_frame": "crop_a", "view_scale": 96.0,
             "crop_uv": list(MODEL_BOUNDS), "fit_direction": "horizontal",
             "pixel_size": MW, "crop_px": [MW, MH], "actual_w": MW, "actual_h": MH,
             "predicted_derived_px": MH, "dim_check": "pass"}
    for key, value in overrides.items():
        if value is None:
            frame.pop(key, None)
        else:
            frame[key] = value
    return frame


def _reencode(model_path, frame):
    """Give the model sidecar ``frame`` in place of its pre-C5 keys, so
    frame_record() reads exactly ``frame``."""
    side = json.loads(model_path.read_text())
    side.pop("resolution", None)
    side.pop("bounds_xy", None)
    side["frame"] = frame
    model_path.write_text(json.dumps(side))


def _gridded(tmp_path, frame):
    tmp_path.mkdir(parents=True, exist_ok=True)
    anno_path, model_path, _c = _write_pair(tmp_path)
    _reencode(model_path, frame)
    reg.register(anno_path)
    _run_meta(tmp_path)
    grid.grid_view(model_path, tmp_path / "out")
    return json.loads((tmp_path / "out" / "V_1.grid.json").read_text())


# --- the grid record ---------------------------------------------------------

def test_a_matching_non_fit_axis_is_not_flagged_and_is_recorded(tmp_path):
    rec = _gridded(tmp_path, _frame())
    assert rec["status"] == "value"
    check = rec["grid"]["frame_check"]
    assert check == {"fit_direction": "horizontal", "axis": "height",
                     "predicted_px": MH, "actual_px": MH, "delta_px": 0,
                     "state": "value"}
    assert "frame_mismatch" not in rec["grid"]["flags"]
    assert "frame_unmeasured" not in rec["grid"]["flags"]


def test_one_pixel_of_difference_is_within_tolerance(tmp_path):
    rec = _gridded(tmp_path, _frame(actual_h=MH + 1))
    assert rec["grid"]["frame_check"]["delta_px"] == 1
    assert "frame_mismatch" not in rec["grid"]["flags"]


def test_two_pixels_of_difference_is_a_mismatch(tmp_path):
    """The boundary: |delta| > 1 flags, so 2 does and 1 does not."""
    rec = _gridded(tmp_path, _frame(actual_h=MH - 2))
    assert rec["grid"]["frame_check"]["delta_px"] == -2
    assert "frame_mismatch" in rec["grid"]["flags"]


def test_a_90_px_delta_is_flagged_and_the_view_is_still_gridded(tmp_path):
    """MOHAVE's shape: the frame predicts crop_px[1] = 729 on the non-fit
    axis, the export came back 639. Flagged, NOT refused: the grid is
    still written and its counts are still there."""
    rec = _gridded(tmp_path, _frame(crop_px=[MW, 729], actual_h=639))
    assert rec["status"] == "value"
    check = rec["grid"]["frame_check"]
    assert (check["predicted_px"], check["actual_px"], check["delta_px"]) == (729, 639, -90)
    assert check["state"] == "value"
    assert "frame_mismatch" in rec["grid"]["flags"]
    assert rec["image_totals"]["model_host"] > 0


def test_a_vertical_fit_compares_the_width(tmp_path):
    """Fit vertical: Revit sets the height, so the WIDTH is the derived axis.
    The height is wildly off here and must not matter."""
    rec = _gridded(tmp_path, _frame(fit_direction="vertical",
                                    crop_px=[MW + 90, MH], actual_h=MH + 500))
    check = rec["grid"]["frame_check"]
    assert check["axis"] == "width"
    assert (check["predicted_px"], check["actual_px"], check["delta_px"]) == (MW + 90, MW, -90)
    assert "frame_mismatch" in rec["grid"]["flags"]


def test_a_horizontal_fit_ignores_the_fit_axis(tmp_path):
    """Control for the axis choice: a width mismatch on a horizontal fit is
    dim_check's business, not this check's."""
    rec = _gridded(tmp_path, _frame(crop_px=[MW + 90, MH]))
    assert rec["grid"]["frame_check"]["delta_px"] == 0
    assert "frame_mismatch" not in rec["grid"]["flags"]


def test_a_frame_missing_its_fields_is_unmeasured_never_a_match(tmp_path):
    for missing in ("crop_px", "actual_h", "fit_direction"):
        rec = _gridded(tmp_path / missing, _frame(**{missing: None}))
        assert rec["status"] == "value", missing
        check = rec["grid"]["frame_check"]
        assert check["state"] == "unavailable", missing
        assert check["reason"], missing
        assert check["delta_px"] is None, missing
        assert "frame_unmeasured" in rec["grid"]["flags"], missing
        assert "frame_mismatch" not in rec["grid"]["flags"], missing


def test_a_frame_b_capture_is_unmeasured_not_compared(tmp_path):
    """crop_px is crop A within frame B there: it does not predict the image."""
    rec = _gridded(tmp_path, _frame(sizing_frame=None))
    assert rec["grid"]["frame_check"]["state"] == "unavailable"
    assert "frame_unmeasured" in rec["grid"]["flags"]


def test_a_pre_c5_frame_is_unmeasured(tmp_path):
    """The shared fixture's own old shape records no fit_direction or crop_px."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    anno_path, model_path, _c = _write_pair(tmp_path)
    reg.register(anno_path)
    _run_meta(tmp_path)
    rec = grid.grid_view(model_path, tmp_path / "out")
    on_disk = json.loads((tmp_path / "out" / "V_1.grid.json").read_text())
    assert rec["status"] == on_disk["status"] == "value"
    assert on_disk["grid"]["frame_check"]["state"] == "unavailable"
    assert "frame_unmeasured" in on_disk["grid"]["flags"]


def test_a_non_numeric_actual_is_unmeasured():
    for bad in ("639", True, float("nan")):
        check = grid.frame_check(_frame(actual_h=bad))
        assert check["state"] == "unavailable", bad
        assert grid.frame_check_flag(check) == "frame_unmeasured", bad


# --- the roll-up -------------------------------------------------------------

def _rollup_view(run, n, frame):
    model_path, anno_path = _capture(run, n, register=False, gridded=False)
    _reencode(model_path, frame)
    reg.register(anno_path)
    grid.grid_view(model_path, run / "analysis_grid")


def test_the_rollup_carries_the_frame_columns_and_counts_the_flags(tmp_path):
    run = _run(tmp_path)
    _rollup_view(run, 1, _frame())
    _rollup_view(run, 2, _frame(crop_px=[MW, 729], actual_h=639))
    _rollup_view(run, 3, _frame(crop_px=None))
    assert rollup.main([str(run)]) == 0
    out = run / "analysis_grid"
    with open(str(out / rollup.CSV_NAME), encoding="utf-8", newline="") as handle:
        rows = dict((r["view_stem"], r) for r in csv.DictReader(handle))
    summary = json.loads((out / rollup.SUMMARY_NAME).read_text())
    clean, bad, unmeasured = rows["V_1"], rows["V_2"], rows["V_3"]
    assert all(r["row_status"] == "gridded" for r in (clean, bad, unmeasured))
    assert (clean["frame_predicted_px"], clean["frame_actual_px"],
            clean["frame_delta_px"]) == (str(MH), str(MH), "0")
    assert "frame_mismatch" not in clean["flags"].split("|")
    assert (bad["frame_predicted_px"], bad["frame_actual_px"],
            bad["frame_delta_px"]) == ("729", "639", "-90")
    assert "frame_mismatch" in bad["flags"].split("|")
    # Unmeasured is blank, never zero (an empty cell means "not applicable").
    assert unmeasured["frame_delta_px"] == "" and unmeasured["frame_predicted_px"] == ""
    assert "frame_unmeasured" in unmeasured["flags"].split("|")
    counts = summary["flag_counts"]["counts"]
    assert counts.get("frame_mismatch") == 1
    assert counts.get("frame_unmeasured") == 1
