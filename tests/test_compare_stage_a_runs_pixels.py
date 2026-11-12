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
