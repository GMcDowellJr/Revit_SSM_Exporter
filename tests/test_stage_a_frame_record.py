"""C5: one frame record per sidecar, exact duplicates written once.

Pins the WRITER's new shape, and composes it with the READER that rebuilds
the pre-C5 keys (tools/stage_a_sidecar_shapes.legacy_view / frame_record), so
every dropped key is shown to be exactly derivable from what remains -- on
production output, not on a fixture that reimplements the schema.
"""
import copy
import json

import pytest

from tools.stage_a_sidecar_shapes import frame_record, legacy_view
from tests.test_frame_export_geometry_call_site import _export, _raster_at
from tests.test_capture_frame_is_not_recentred import (
    _bounds_result, _capture, _raster_from,
)
from vop_interwoven.core.math_utils import Bounds2D

_FOLDED = ("resolution", "export_frame", "bounds_xy")
_EXACT_DUPLICATES = ("export_dpi", "requested_px")
# C6: requested values and the derived dpi are not recorded.
_C6_DROPPED = ("requested_export_dpi", "requested_pixel_size", "requested_axis",
               "requested_fpp_ft", "effective_export_dpi")


def _narrowed(tmp_path):
    return _export(tmp_path, _raster_at(1.0, Bounds2D(12.0, 9.0, 40.0, 30.0)))["metadata"]


def test_one_frame_record_and_no_folded_blocks(tmp_path):
    md = _narrowed(tmp_path)
    assert isinstance(md["frame"], dict)
    assert not (set(_FOLDED) & set(md)), set(_FOLDED) & set(md)
    assert not (set(_EXACT_DUPLICATES) & set(md["frame"]))
    assert not (set(_C6_DROPPED) & set(md["frame"])), set(_C6_DROPPED) & set(md["frame"])
    # The crop was applied, so the snapped crop IS crop_uv: written once.
    assert md["frame"]["status"] == "value"
    assert md["frame"]["crop_uv"] is not None
    assert "crop_snapped_uv" not in md["frame"]
    # Uncapped: the grid rectangle IS the frame, so it is written once too.
    assert "raster_bounds_uv" not in md["frame"]


def test_the_folded_keys_rebuild_exactly_from_the_frame(tmp_path):
    md = _narrowed(tmp_path)
    f = md["frame"]
    run_config = {"config": {"color_id_buffer_export_dpi": 150.0}}
    old = legacy_view(md, run_config)
    assert old["bounds_xy"] == f["crop_uv"]
    assert old["export_frame"]["crop_snapped_uv"] == f["crop_uv"]
    assert old["export_frame"]["raster_bounds_uv"] == f["frame_uv"]
    requested_px = f["dim_check_attempts"][0]["requested_px"]
    assert old["export_frame"]["requested_px"] == requested_px
    assert old["resolution"]["requested_px"] == requested_px
    assert old["resolution"]["requested_pixel_size"] == requested_px
    assert old["resolution"]["export_dpi"] == 150.0
    assert old["resolution"]["requested_axis"] == "width"   # horizontal fit
    for key in ("pre_cap_px", "cap_applied", "max_axis_px"):
        assert old["export_frame"][key] == old["resolution"][key] == f[key]
    assert old["export_frame"]["requested_export_dpi"] == 150.0
    assert old["export_frame"]["requested_fpp_ft"] == pytest.approx(
        f["view_scale"] / (12.0 * 150.0))


def test_an_old_shape_folds_back_to_the_same_frame(tmp_path):
    """Round trip through the file, as a reader sees it: new -> pre-C5 keys
    -> frame_record() is the writer's frame, plus the two conditional keys
    the old shape always carried."""
    md = json.loads(json.dumps(_narrowed(tmp_path)))
    old_only = dict(legacy_view(md))
    del old_only["frame"]
    rebuilt = frame_record(old_only)
    f = md["frame"]
    expected = dict(f, crop_snapped_uv=f["crop_uv"], raster_bounds_uv=f["frame_uv"],
                    requested_pixel_size=f["dim_check_attempts"][0]["requested_px"],
                    requested_axis="width")
    assert rebuilt == expected


def test_a_capped_view_writes_the_grid_rectangle_because_it_differs(tmp_path):
    md = _capture(tmp_path, _raster_from(_bounds_result()))
    f = md["frame"]
    assert f["frame_source"] == "anno_uncapped"
    assert f["raster_bounds_uv"] != f["frame_uv"]
    assert legacy_view(md)["export_frame"]["raster_bounds_uv"] == f["raster_bounds_uv"]


def test_a_capture_with_no_frame_is_explicitly_unavailable(tmp_path):
    md = _export(tmp_path, None)["metadata"]
    f = md["frame"]
    assert f["status"] == "unavailable" and f["reason"]
    assert f["crop_uv"] is None
    old = legacy_view(md)
    assert old["export_frame"] == {"status": "unavailable", "reason": f["reason"]}
    assert old["bounds_xy"] is None


def test_legacy_view_leaves_an_archive_sidecar_untouched():
    archive = {"resolution": {"actual_w": 10, "actual_h": 5}, "bounds_xy": [0, 0, 1, 1],
               "export_frame": {"status": "unavailable", "reason": "r"}}
    before = copy.deepcopy(archive)
    assert legacy_view(archive) is archive
    assert archive == before
    assert frame_record(archive)["crop_uv"] == [0, 0, 1, 1]
    assert frame_record(archive)["actual_w"] == 10
