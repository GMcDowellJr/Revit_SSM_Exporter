"""P2: the registered capture's phase timings reach BOTH sidecar FILES.

``record["timings_ms"]`` was filled since the registered capture shipped and
never written (pipeline_0930_1133: absent from every sidecar). Asserted on the
files, not the returned record (CLAUDE.md, defect class 4).
"""
import json

from tests.test_stage_a_registered_capture import _run

EXPECTED = {
    "authored_override_scan", "mark_layout", "marks_create", "model_pass",
    "model_export", "sidecar_write_model", "suppression", "annotation_pass",
    "annotation_export", "sidecar_write_annotation", "rollback",
    "restore_element_overrides", "total", "unaccounted",
}


def _file(path):
    with open(path) as handle:
        return json.load(handle)


def test_p2_every_phase_timing_is_in_both_sidecar_FILES(tmp_path):
    out, _v, _d, _e, _diag = _run(tmp_path)
    for path in (out["sidecar_path"], out["annotation_sidecar_path"]):
        marks = _file(path)["registration_marks"]
        timings = marks["timings_ms"]
        assert EXPECTED <= set(timings), sorted(EXPECTED - set(timings))
        assert all(isinstance(v, (int, float)) and v >= -1e-6
                   for k, v in timings.items() if k != "unaccounted")
        from vop_interwoven.stage_a_registered_capture import TIMING_PARTITION
        assert marks["timing_partition"] == list(TIMING_PARTITION)
        # The remainder is reported, and it is exactly what the partition
        # does not cover -- not a figure that could hide a phase.
        partition = sum(timings[k] for k in TIMING_PARTITION)
        assert abs(timings["unaccounted"] - (timings["total"] - partition)) < 1e-2
        # Nested phases sit inside their pass, never beside it.
        assert timings["model_export"] <= timings["model_pass"] + 1e-3
        assert timings["annotation_export"] <= timings["annotation_pass"] + 1e-3


def test_p2_both_files_carry_the_same_timings(tmp_path):
    out, _v, _d, _e, _diag = _run(tmp_path)
    model = _file(out["sidecar_path"])["registration_marks"]["timings_ms"]
    anno = _file(out["annotation_sidecar_path"])["registration_marks"]["timings_ms"]
    assert model == anno


def test_p2_unaccounted_is_total_minus_the_partition_only():
    from vop_interwoven.stage_a_registered_capture import unaccounted_ms
    timings = {"total": 100.0, "model_pass": 40.0, "model_export": 30.0,
               "annotation_pass": 20.0, "rollback": 5.0}
    # model_export is nested in model_pass: counting it would hide 30 ms.
    assert unaccounted_ms(timings) == 35.0
    assert unaccounted_ms({"model_pass": 1.0}) is None


def _report_run(tmp_path, statuses, extra_rows=()):
    import csv
    import os
    import shutil

    out, _v, _d, _e, _diag = _run(tmp_path / "cap")
    run = tmp_path / "run"
    os.makedirs(str(run / "color_id_buffer"))
    shutil.copy(out["sidecar_path"], str(run / "color_id_buffer" / "v.json"))
    view_id = _file(out["sidecar_path"])["view_id"]
    # "v" is the captured view; any other key is a view id as written.
    views = [{"view_id": view_id, "capture_status": statuses.get("v", "success")}]
    views += [{"view_id": vid, "capture_status": st}
              for vid, st in statuses.items() if vid != "v"]
    with open(str(run / "run_meta.json"), "w") as handle:
        json.dump({"run_id": "RUN_B", "views": views}, handle)
    with open(str(run / "views_core_x.csv"), "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["RunId", "ViewId", "ViewName", "ElapsedSec"])
        writer.writerow(["RUN_B", view_id, "v", "10.0"])
        for row in extra_rows:
            writer.writerow(row)
    return out, run, view_id


def test_p2_the_report_reconciles_ElapsedSec_with_the_written_total(tmp_path):
    """Composes the offline report with a sidecar the capture really wrote."""
    from tools.stage_a_timing_report import timing_rows

    out, run, _vid = _report_run(tmp_path, {999: "success"},
                                 extra_rows=[["RUN_B", 999, "never_captured", "2.0"]])
    rows = dict((r["view_name"], r) for r in timing_rows(str(run)))
    total = _file(out["sidecar_path"])["registration_marks"]["timings_ms"]["total"]
    assert rows["v"]["capture_total_ms"] == total
    assert abs(rows["v"]["outside_capture_ms"] - (10000.0 - total)) < 1e-6
    assert rows["v"]["unaccounted_ms"] is not None
    # Reported, never dropped.
    assert rows["never_captured"]["reason"] == "no model sidecar"


def test_p2_the_report_joins_one_run_only(tmp_path):
    """Codex, PR #222: a historical views_core row for the same ViewId gets
    no timings from this run's sidecar -- it is not this run's row at all."""
    from tools.stage_a_timing_report import timing_rows

    _out, run, view_id = _report_run(
        tmp_path, {}, extra_rows=[["RUN_A", "PLACEHOLDER", "v_old", "99.0"]])
    # The old run's row carries the SAME ViewId.
    text = (run / "views_core_x.csv").read_text().replace("PLACEHOLDER", str(view_id))
    (run / "views_core_x.csv").write_text(text)
    rows = timing_rows(str(run))
    assert [r["view_name"] for r in rows] == ["v"]
    assert rows[0]["run_id"] == "RUN_B"


def test_p2_a_failed_capture_is_not_joined_to_a_sidecar_beside_it(tmp_path):
    from tools.stage_a_timing_report import timing_rows

    _out, run, _vid = _report_run(tmp_path, {"v": "failed"})
    [row] = timing_rows(str(run))
    assert row["capture_total_ms"] is None and row["outside_capture_ms"] is None
    assert "failed" in row["reason"]


def test_p2_a_run_without_run_meta_is_refused(tmp_path):
    import pytest
    from tools.stage_a_timing_report import timing_rows

    _out, run, _vid = _report_run(tmp_path, {})
    (run / "run_meta.json").unlink()
    with pytest.raises((OSError, IOError)):
        timing_rows(str(run))
