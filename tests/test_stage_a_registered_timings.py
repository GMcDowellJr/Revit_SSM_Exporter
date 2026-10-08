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
    view_id = _file(out["sidecar_path"])["view_id"]
    shutil.copy(out["sidecar_path"],
                str(run / "color_id_buffer" / "v_{0}.json".format(view_id)))
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


def test_p2_derived_json_beside_the_sidecar_is_never_read_as_it(tmp_path, monkeypatch):
    """Codex, PR #222: the decoder's <x>_anno.decoded.json and the
    registration tool's <x>_anno.registered.json carry the same view_id; a
    "*.json" glob let whichever came last replace the model sidecar. Both
    listing orders are exercised, since the real order is the filesystem's."""
    import glob as _glob
    import tools.stage_a_timing_report as report

    out, run, view_id = _report_run(tmp_path, {})
    for name in ("v_{0}.decoded.json", "v_{0}_anno.decoded.json",
                 "v_{0}_anno.registered.json", "v_{0}_anno.json"):
        with open(str(run / "color_id_buffer" / name.format(view_id)), "w") as handle:
            json.dump({"view_id": view_id}, handle)
    total = _file(out["sidecar_path"])["registration_marks"]["timings_ms"]["total"]
    real_glob = _glob.glob
    for reverse in (False, True):
        monkeypatch.setattr(report.glob, "glob",
                            lambda p, _r=reverse: sorted(real_glob(p), reverse=_r))
        [row] = report.timing_rows(str(run))
        assert row["capture_total_ms"] == total, reverse


def test_p2_two_model_sidecars_for_one_view_are_not_guessed_between(tmp_path):
    import shutil
    from tools.stage_a_timing_report import timing_rows

    out, run, view_id = _report_run(tmp_path, {})
    shutil.copy(out["sidecar_path"],
                str(run / "color_id_buffer" / "w_{0}.json".format(view_id)))
    [row] = timing_rows(str(run))
    assert row["capture_total_ms"] is None
    assert "more than one model sidecar" in row["reason"]


# The model pass's own steps, in pass order (color_id_buffer.export_color_id_
# buffer_view's _lap calls). Each reaches timings_ms as model_<step>.
MODEL_STEPS = ("setup", "graphics_state", "template_detach", "suppress_setup",
               "expand", "link_filters", "link_proxies", "near_face_w", "paint",
               "suppress_commit", "restore", "restore_commit", "finish")


def _slow(monkeypatch, name, ms=25.0):
    """Make color_id_buffer.<name> take at least ``ms``: a known quantity the
    step timings must attribute to exactly one step."""
    import time
    from vop_interwoven import color_id_buffer as cib
    real = getattr(cib, name)

    def slow(*a, **k):
        time.sleep(ms / 1000.0)
        return real(*a, **k)
    monkeypatch.setattr(cib, name, slow)


def test_model_pass_steps_are_in_both_sidecar_FILES_nested_in_the_pass(tmp_path):
    out, _v, _d, _e, _diag = _run(tmp_path)
    for path in (out["sidecar_path"], out["annotation_sidecar_path"]):
        timings = _file(path)["registration_marks"]["timings_ms"]
        steps = dict((s, timings.get("model_" + s)) for s in MODEL_STEPS)
        assert all(isinstance(v, (int, float)) and v >= 0 for v in steps.values()), steps
        # Nested in model_pass, never beside it, and not in the partition.
        nested = sum(steps.values()) + timings["model_export"] + timings["sidecar_write_model"]
        assert nested <= timings["model_pass"] + 1e-2
        from vop_interwoven.stage_a_registered_capture import TIMING_PARTITION
        assert not set("model_" + s for s in MODEL_STEPS) & set(TIMING_PARTITION)


def test_model_pass_steps_account_for_the_whole_pass_without_double_counting(
        tmp_path, monkeypatch):
    """The laps are contiguous: steps + export + sidecar write == the pass's
    own clock. A slow export makes the check discriminate: a lap clock not
    restarted after the export would count those 25 ms twice."""
    _slow(monkeypatch, "_export_tiff")
    out, _v, _d, _e, _diag = _run(tmp_path)
    t = out["timings"]
    assert list(t["steps_ms"]) == list(MODEL_STEPS)
    assert t["export_ms"] >= 25.0
    total = sum(t["steps_ms"].values()) + t["export_ms"] + t["sidecar_write_ms"]
    assert abs(total - t["color_id_buffer_ms"]) < 1.0, (total, t)


def test_a_slow_step_lands_in_its_own_lap(tmp_path, monkeypatch):
    """25 ms injected into the near-face/bbox collection appears in
    model_near_face_w and in no neighbouring step."""
    _slow(monkeypatch, "_collect_near_face_w_data")
    out, _v, _d, _e, _diag = _run(tmp_path)
    steps = out["timings"]["steps_ms"]
    assert steps["near_face_w"] >= 25.0
    assert all(v < 25.0 for k, v in steps.items() if k != "near_face_w"), steps
    persisted = _file(out["sidecar_path"])["registration_marks"]["timings_ms"]
    assert persisted["model_near_face_w"] == steps["near_face_w"]


def _rename_core(run, suffix):
    (run / "views_core_x.csv").rename(run / "views_core_{0}.csv".format(suffix))


def test_p2_the_report_is_WRITTEN_beside_views_core_named_by_its_override(tmp_path):
    """main() writes views_timing_<suffix>.csv/.json into the run directory,
    <suffix> being what the thinrunner's override named views_core by -- a
    tag as given, not run_meta's as-of date. Asserts the FILES (defect class
    4), and that they carry the rows timing_rows() returns."""
    import csv
    from tools import stage_a_timing_report as report

    out, run, view_id = _report_run(tmp_path, {999: "success"},
                                    extra_rows=[["RUN_B", 999, "never_captured", "2.0"]])
    _rename_core(run, "PR_221")
    meta = json.loads((run / "run_meta.json").read_text())
    meta.update(date="2026-10-07", run_tag="PR_221")
    (run / "run_meta.json").write_text(json.dumps(meta))
    assert report.main([str(run)]) == 0
    assert sorted(p.name for p in run.glob("views_timing*")) == [
        "views_timing_PR_221.csv", "views_timing_PR_221.json"]
    written = json.loads((run / "views_timing_PR_221.json").read_text())
    assert written["run_id"] == "RUN_B" and written["file_date"] == "PR_221"
    assert written["views"] == json.loads(json.dumps(report.timing_rows(str(run))))
    with open(str(run / "views_timing_PR_221.csv"), newline="") as handle:
        rows = dict((r["view_name"], r) for r in csv.DictReader(handle))
    timings = _file(out["sidecar_path"])["registration_marks"]["timings_ms"]
    assert float(rows["v"]["capture_total_ms"]) == timings["total"]
    for phase in set(timings) - {"total", "unaccounted"}:
        assert float(rows["v"]["phase_{0}_ms".format(phase)]) == timings[phase]
    # Reported, never dropped -- blank cells, the reason kept.
    assert rows["never_captured"]["capture_total_ms"] == ""
    assert rows["never_captured"]["reason"] == "no model sidecar"


def test_p2_the_views_core_name_wins_over_run_meta(tmp_path):
    """A dated override: views_core_2026-09-01.csv names the report even
    though run_meta's date says otherwise -- the file name is ground truth."""
    from tools import stage_a_timing_report as report

    _out, run, _vid = _report_run(tmp_path, {})
    _rename_core(run, "2026-09-01")
    meta = json.loads((run / "run_meta.json").read_text())
    meta.update(date="2026-10-07")
    (run / "run_meta.json").write_text(json.dumps(meta))
    assert report.file_date_str(str(run)) == "2026-09-01"


def test_p2_run_meta_names_the_report_only_when_views_core_cannot(tmp_path):
    from tools import stage_a_timing_report as report

    _out, run, _vid = _report_run(tmp_path, {})
    (run / "views_core_x.csv").rename(run / "views_core.csv")   # names nothing
    meta = json.loads((run / "run_meta.json").read_text())
    meta.update(date="2026-10-07", run_tag="PR_221")
    (run / "run_meta.json").write_text(json.dumps(meta))
    assert report.file_date_str(str(run)) == "PR_221"           # the tag, not the date
    meta["run_tag"] = None
    (run / "run_meta.json").write_text(json.dumps(meta))
    assert report.file_date_str(str(run)) == "2026-10-07"


def test_p2_a_run_whose_name_cannot_be_told_is_refused_and_nothing_written(tmp_path):
    import shutil
    from tools import stage_a_timing_report as report

    _out, run, _vid = _report_run(tmp_path, {})
    shutil.copy(str(run / "views_core_x.csv"), str(run / "views_core_y.csv"))
    assert report.main([str(run)]) == 2                         # two names: not guessed
    (run / "views_core_y.csv").unlink()
    (run / "views_core_x.csv").rename(run / "views_core.csv")   # none, and no run_meta date
    assert report.main([str(run)]) == 2
    assert not list(run.glob("views_timing*"))
    # And without run_meta.json, main refuses rather than raising.
    (run / "run_meta.json").unlink()
    assert report.main([str(run)]) == 2
