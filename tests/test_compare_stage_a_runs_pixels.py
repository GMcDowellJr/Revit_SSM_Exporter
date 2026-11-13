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
