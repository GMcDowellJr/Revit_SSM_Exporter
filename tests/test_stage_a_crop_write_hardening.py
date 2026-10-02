"""Codex review of PR #226 on D (no crop write / read every write back).

* CropBoxActive is part of the write: a box that reads back right with the
  crop left INACTIVE exports the whole view, so it is crop_write_not_applied
  (False) or crop_write_unverified (unreadable) -- in both passes.
* A crop write that raises half-way (CropBox taken, CropBoxActive refused) is
  still restored: restore keys off the attempt, not the finished record.
* A failed crop read, and a CropBoxActive that will not read, reach
  Diagnostics.
* The re-encoder rebuilds crop_write for a frame-shaped (post-C5, pre-D)
  sidecar too, not only for a pre-C5 one.

Mutations: crop_write_fault ignoring crop_box_active_read_back -> the first
test red; restoring on ``(crop_write or {}).get("written")`` -> the partial-
write test red; dropping either diag.warn -> its test red; the re-encoder's
early return without the rebuild -> the last test red.
"""
import types

from vop_interwoven.color_id_buffer import (
    crop_write_fault, read_crop_uv, resolve_crop_a,
)
from vop_interwoven.core.math_utils import Bounds2D

from tests.stage_a_capture_fakes import FakeBoundingBoxXYZ, FakeDiag, FakeViewPlan, FakeXYZ
from tests.test_stage_a_annotation_pass import VIEW_ID, _run_both_passes

MATCH = {"matches_request": True, "read_back_uv": [0, 0, 1, 1]}


def test_a_crop_left_inactive_is_not_applied_and_an_unread_one_unverified():
    assert crop_write_fault({"written": True, "read_back": MATCH,
                             "crop_box_active_read_back": False}) == "crop_write_not_applied"
    assert crop_write_fault({"written": True, "read_back": MATCH,
                             "crop_box_active_read_back": None}) == "crop_write_unverified"
    # Control: active and matching is clean; a record from before the
    # read-back existed is judged on the box alone.
    assert crop_write_fault({"written": True, "read_back": MATCH,
                             "crop_box_active_read_back": True}) is None
    assert crop_write_fault({"written": True, "read_back": MATCH}) is None


class _RefusesActivation(FakeViewPlan):
    """CropBox writes take; setting CropBoxActive True raises."""

    def __init__(self, view_id):
        self._ready = False
        FakeViewPlan.__init__(self, view_id)
        self._active = True
        box = FakeBoundingBoxXYZ()
        box.Min, box.Max = FakeXYZ(5, 5, 0), FakeXYZ(50, 40, 0)
        self.CropBox = box
        self._ready = True

    @property
    def CropBoxActive(self):
        return self._active

    @CropBoxActive.setter
    def CropBoxActive(self, value):
        if getattr(self, "_ready", False) and value:
            raise RuntimeError("CropBoxActive refused (fake)")
        self._active = bool(value)


def test_a_half_finished_crop_write_is_still_restored(tmp_path):
    view = _RefusesActivation(VIEW_ID)
    before = (view.CropBox.Min.X, view.CropBox.Min.Y, view.CropBox.Max.X, view.CropBox.Max.Y)
    _run_both_passes(tmp_path, view=view)
    after = (view.CropBox.Min.X, view.CropBox.Min.Y, view.CropBox.Max.X, view.CropBox.Max.Y)
    assert after == before


def test_both_passes_read_crop_box_active_back(tmp_path):
    view = FakeViewPlan(VIEW_ID)
    model, anno, _g, _d, _v, _diag = _run_both_passes(tmp_path, view=view)
    assert model["metadata"]["frame"]["crop_write"]["crop_box_active_read_back"] is True
    assert anno["metadata"]["registration"]["crop_write"]["crop_box_active_read_back"] is True


def test_a_failed_crop_read_is_in_diagnostics():
    diag = FakeDiag()
    view = types.SimpleNamespace(CropBox=None)
    basis = types.SimpleNamespace(transform_to_view_uv=lambda p: (p[0], p[1]))
    uv, error = read_crop_uv(view, basis, diag=diag, view_id=7)
    assert uv is None and error
    assert [w["callsite"] for w in diag.warnings] == ["read_crop_uv"]


def test_an_unreadable_crop_box_active_is_in_diagnostics():
    class _V(object):
        @property
        def CropBoxActive(self):
            raise RuntimeError("no")
    diag = FakeDiag()
    raster = types.SimpleNamespace(bounds_xy=Bounds2D(0, 0, 10, 10),
                                   model_clip_bounds=None, view_basis=None)
    _uv, record = resolve_crop_a(_V(), raster, diag=diag, view_id=7)
    assert record["source"] == "model_crop_a" and "authored_crop_active_error" in record
    assert [w["callsite"] for w in diag.warnings] == ["resolve_crop_a"]


def test_a_frame_shaped_pre_d_sidecar_gets_its_crop_write_rebuilt():
    from tools.reencode_stage_a_sidecar import reencode
    out = reencode({"frame": {"status": "value", "crop_uv": [0, 0, 1, 1]}})
    assert out["frame"]["crop_write"]["status"] == "rebuilt_from_pre_d_frame"
    # Control: a current-shape frame is left exactly as written.
    current = {"frame": {"crop_uv": [0, 0, 1, 1], "crop_write": {"written": False}}}
    assert reencode(current)["frame"]["crop_write"] == {"written": False}
