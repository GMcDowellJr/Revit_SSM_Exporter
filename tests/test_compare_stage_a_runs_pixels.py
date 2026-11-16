"""tools/compare_stage_a_runs.py's opt-ins for runs made by different builds
(R1 gate 1, 2026-11-11): ``--pixels`` and ``--allow``.

Each is checked against its CONTROL: the same pair without the opt-in still
reports the difference, so a pass is the opt-in's doing and not an
always-green comparison.
"""
import json

import pytest
from PIL import Image   # requirements-test.txt: a missing Pillow fails, never skips

from tools import compare_stage_a_runs as cmp


def _run(root, name, run_id, tiff_kwargs, pixels, sidecar):
    run = root / name
    (run / "color_id").mkdir(parents=True)
    (run / "run_meta_2026-11-11.json").write_text(json.dumps(
        {"run_id": run_id, "exporter_version": "4.4.1+src." + run_id.lower()}))
    image = Image.new("RGB", (8, 6), (255, 255, 255))
    for (x, y), rgb in pixels.items():
        image.putpixel((x, y), rgb)
    image.save(str(run / "color_id" / "View_1.tiff"), **tiff_kwargs)
    (run / "color_id" / "View_1.json").write_text(json.dumps(sidecar))
    return run


BASE_SIDECAR = {"frame": {"pixel_size": 8}}
R1_SIDECAR = dict(BASE_SIDECAR, model_restore={"state": "deferred_to_group_rollback"})
INK = {(1, 1): (10, 20, 30)}


def _pair(tmp_path, pixels_b=INK, sidecar_b=R1_SIDECAR):
    a = _run(tmp_path, "baseline_run_dir", "RUN_A", {"compression": "raw"}, INK, BASE_SIDECAR)
    b = _run(tmp_path, "candidate_run_dir", "RUN_B", {"compression": "tiff_lzw"}, pixels_b, sidecar_b)
    assert (a / "color_id" / "View_1.tiff").read_bytes() != \
        (b / "color_id" / "View_1.tiff").read_bytes()
    return a, b


ALL = ("build", "restore_bookkeeping")


def test_equal_pixels_under_different_bytes_pass_with_pixels(tmp_path):
    a, b = _pair(tmp_path)
    report = cmp.compare(str(a), str(b), pixels=True, allow=ALL)
    assert report["unexpected"] == {}
    assert "tiff_pixels_equal" in report["allowed"]["color_id/View_1.tiff"]
    assert "restore_bookkeeping" in report["allowed"]["color_id/View_1.json"]
    assert "build" in report["allowed"]["run_meta_2026-11-11.json"]


def test_control_without_pixels_the_bytes_still_differ(tmp_path):
    a, b = _pair(tmp_path)
    report = cmp.compare(str(a), str(b), allow=ALL)
    assert report["unexpected"]["color_id/View_1.tiff"] == ["bytes differ"]


def test_control_without_allow_the_bookkeeping_and_build_are_unexpected(tmp_path):
    a, b = _pair(tmp_path)
    report = cmp.compare(str(a), str(b), pixels=True)
    assert "/model_restore/state" in report["unexpected"]["color_id/View_1.json"]
    assert "/exporter_version" in report["unexpected"]["run_meta_2026-11-11.json"]


def test_one_different_pixel_is_unexpected(tmp_path):
    a, b = _pair(tmp_path, pixels_b={(1, 1): (10, 20, 31)})
    report = cmp.compare(str(a), str(b), pixels=True, allow=ALL)
    assert report["unexpected"]["color_id/View_1.tiff"] == ["decoded pixels differ"]


def test_an_unknown_allow_rule_is_refused(tmp_path):
    a, b = _pair(tmp_path)
    with pytest.raises(ValueError):
        cmp.compare(str(a), str(b), allow=("everything",))


def test_the_bookkeeping_rule_does_not_reach_other_fields(tmp_path):
    a, b = _pair(tmp_path, sidecar_b=dict(R1_SIDECAR, frame={"pixel_size": 9}))
    report = cmp.compare(str(a), str(b), pixels=True, allow=ALL)
    assert report["unexpected"]["color_id/View_1.json"] == ["/frame/pixel_size"]


# --- two runs tagged differently (gate R, 2026-11-13: baseline_R vs R1) -----

def _tagged(root, tag, run_id, view_name="Plan_R1_19290402"):
    run = root / ("2026-11-13__" + run_id)
    (run / "color_id").mkdir(parents=True)
    (run / "run_meta_{0}.json".format(tag)).write_text(json.dumps({
        "run_id": run_id, "run_tag": tag, "date": "2026-11-13",
        "view_fingerprints": {"file": "view_fingerprints_{0}.json".format(tag)}}))
    (run / "views_core_{0}.csv".format(tag)).write_text("RunId,ViewId\n{0},1\n".format(run_id))
    # A VIEW's file whose name happens to carry the other run's tag: never
    # renamed, it is the view's name.
    (run / "color_id" / "{0}.json".format(view_name)).write_text("{}")
    return run


def test_dated_files_of_differently_tagged_runs_are_compared_file_to_file(tmp_path):
    a = _tagged(tmp_path, "baseline_R", "20261113T1034_baseline_R")
    b = _tagged(tmp_path, "R1", "20261113T1056_R1")
    report = cmp.compare(str(a), str(b))
    assert report["unexpected"] == {}, report["unexpected"]
    assert "views_core_<D>.csv" in report["identical"]
    assert report["allowed"]["run_meta_<D>.json"] == {"run_label": ["/run_tag"]}
    assert "color_id/Plan_R1_19290402.json" in report["identical"]


def test_control_a_view_file_is_not_renamed_by_the_tag(tmp_path):
    a = _tagged(tmp_path, "baseline_R", "20261113T1034_baseline_R")
    b = _tagged(tmp_path, "R1", "20261113T1056_R1", view_name="Plan_19290402")
    report = cmp.compare(str(a), str(b))
    assert set(report["unexpected"]) == {"color_id/Plan_R1_19290402.json",
                                         "color_id/Plan_19290402.json"}


def test_control_without_a_tag_the_names_are_left_alone():
    assert cmp._mask_file_date("run_meta_R1.json", None) == "run_meta_R1.json"
    assert cmp._mask_file_date("run_meta_R1.json", "R1") == "run_meta_<D>.json"
    assert cmp._mask_file_date("color_id/x_R1.json", "R1") == "color_id/x_R1.json"


# --- gate 4 (2026-11-16): what a seeded run legitimately changes -------------

def _sidecar_pair(tmp_path, side_a, side_b, tag_b="R1"):
    a = _tagged(tmp_path, "S_run1", "20261116T0944_S_run1")
    b = _tagged(tmp_path, tag_b, "20261116T1329_S_run2")
    (a / "color_id" / "V_7.json").write_text(json.dumps(side_a))
    (b / "color_id" / "V_7.json").write_text(json.dumps(side_b))
    return a, b


def _ticks(first_id, anno=False):
    ids = list(range(first_id, first_id + 3))
    side = {"registration_marks": {"marks": [{"id": i, "span_uv": [0, 1]} for i in ids]},
            "palette_reservation": {"mark_ids_painted": ids}}
    if anno:
        side["annotation_bbox_map"] = dict((str(i), {"category": "Lines"}) for i in ids)
    return side


def test_tick_ids_minted_by_the_capture_are_relabelled(tmp_path):
    """Run 2 skipped five first attempts, so every later view's ticks were
    minted with lower ids (S_run1 / S_run2, 2026-11-16)."""
    a, b = _sidecar_pair(tmp_path, _ticks(23902685, anno=True), _ticks(23901000, anno=True))
    report = cmp.compare(str(a), str(b))
    assert "color_id/V_7.json" not in report["unexpected"]
    assert "minted_id" in report["allowed"]["color_id/V_7.json"]


def test_control_a_real_difference_beside_relabelled_ticks_is_unexpected(tmp_path):
    side_b = _ticks(23901000, anno=True)
    side_b["annotation_bbox_map"][str(23901001)]["category"] = "Text Notes"
    a, b = _sidecar_pair(tmp_path, _ticks(23902685, anno=True), side_b)
    report = cmp.compare(str(a), str(b))
    assert report["unexpected"]["color_id/V_7.json"] == [
        "/annotation_bbox_map/<minted 1>/category"]


def test_control_the_same_ids_are_not_relabelled(tmp_path):
    a, b = _sidecar_pair(tmp_path, _ticks(5), _ticks(5))
    assert "color_id/V_7.json" in cmp.compare(str(a), str(b))["identical"]


def test_avoidance_calls_over_threshold_is_timing(tmp_path):
    side_a = {"registration_marks": {"mark_avoidance": {"calls_over_threshold": 3}}}
    side_b = {"registration_marks": {"mark_avoidance": {"calls_over_threshold": 1}}}
    a, b = _sidecar_pair(tmp_path, side_a, side_b)
    report = cmp.compare(str(a), str(b))
    assert report["allowed"]["color_id/V_7.json"] == {
        "timing": ["/registration_marks/mark_avoidance/calls_over_threshold"]}


def test_the_first_attempts_diagnostics_are_allowed_only_under_co_size_seed(tmp_path):
    a = _tagged(tmp_path, "S_run1", "20261116T0944_S_run1")
    b = _tagged(tmp_path, "S_run2", "20261116T1329_S_run2")
    for run, events in ((a, [{"callsite": "export_dim_check"}]), (b, [])):
        name = [p.name for p in run.iterdir() if p.name.startswith("run_meta_")][0]
        (run / name.replace("run_meta_", "views_diagnostics_")).write_text(json.dumps(
            {"views": {"7": {"diagnostics": {"events": events}}}}))
    assert cmp.compare(str(a), str(b))["unexpected"]
    assert cmp.compare(str(a), str(b), allow=("co_size_seed",))["unexpected"] == {}


def _tiff_pair(tmp_path, grid=True):
    a = _tagged(tmp_path, "S_run1", "20261116T0944_S_run1")
    b = _tagged(tmp_path, "S_run2", "20261116T1329_S_run2")
    for run, rgb in ((a, (10, 20, 30)), (b, (10, 20, 31))):
        image = Image.new("RGB", (4, 4), (255, 255, 255))
        image.putpixel((1, 1), rgb)
        image.save(str(run / "color_id" / "Plan_19290402_anno.tiff"))
        if grid:
            (run / "grid").mkdir()
    return a, b


@pytest.mark.parametrize("differs,record,kind", [
    (False, {"boundary_only": False}, "grid_same"),
    (False, {"boundary_only": True}, "grid_boundary"),
])
def test_grid_rule_allows_pixels_whose_grids_agree(tmp_path, monkeypatch, differs, record,
                                                   kind):
    from tools import validate_change_skip as vcs
    seen = []
    full = dict(record, rows_added=0, rows_removed=2, cells_differ=1, one_sided_max_px=2)
    monkeypatch.setattr(vcs, "compare_grid",
                        lambda vid, ga, gb: seen.append(vid) or (differs, full))
    a, b = _tiff_pair(tmp_path)
    report = cmp.compare(str(a), str(b), pixels=True, grid=True)
    assert report["unexpected"] == {} and seen == [19290402]
    assert kind in report["allowed"]["color_id/Plan_19290402_anno.tiff"]


def test_grid_rule_control_a_differing_grid_or_no_grid_is_unexpected(tmp_path, monkeypatch):
    from tools import validate_change_skip as vcs
    monkeypatch.setattr(vcs, "compare_grid", lambda vid, ga, gb: (True, {
        "rows_added": 9, "rows_removed": 0, "cells_differ": 4, "one_sided_max_px": 40}))
    a, b = _tiff_pair(tmp_path / "x")
    (reason,) = cmp.compare(str(a), str(b), pixels=True, grid=True)["unexpected"][
        "color_id/Plan_19290402_anno.tiff"]
    assert "GRID DIFFERS" in reason
    a, b = _tiff_pair(tmp_path / "y", grid=False)
    (reason,) = cmp.compare(str(a), str(b), pixels=True, grid=True)["unexpected"][
        "color_id/Plan_19290402_anno.tiff"]
    assert "no grid in run A" in reason
    with pytest.raises(ValueError):
        cmp.compare(str(a), str(b), grid=True)


def test_derived_files_are_not_read(tmp_path, monkeypatch):
    a, b = _tiff_pair(tmp_path)
    (a / "grid" / "x.grid.npz").write_bytes(b"A")
    (b / "grid" / "x.grid.npz").write_bytes(b"B")
    snap = cmp.snapshot(str(a))
    assert snap["grid/x.grid.npz"] is None
    assert cmp.compare(str(a), str(b))["skipped"]["grid/x.grid.npz"] == "grid"
