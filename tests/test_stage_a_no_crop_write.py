"""D: an ACTIVE authored crop is never written; every crop write is READ BACK.

Capture-state probe (runs 20261001T145758 / 153207): writing back the
IDENTICAL CropBox on Plaza 6112047 (a split elevation line the API does not
report) added 80 model elements and removed 3 rooms -- 8.8 % of the pixels.
Under a scope box every CropBox write commits and reads back unchanged. The
model pass wrote its snapped crop A on every view, never read it back, and
recorded the request as "the rectangle actually set".

Driven end to end through the registered capture on the fake DB; asserted on
the write log at the moment of each export and on the sidecar FILES.

Mutations this is wired to:
  * resolve_crop_a returning write=True for an active crop -> the first test
    goes red (a CropBox write under the model pass);
  * dropping the read-back (crop_read_back_record never called / matches
    forced True) -> the ignored-write test goes red;
  * mark_fpp_ft sizing from compute_model_crop instead of resolve_crop_a ->
    the composition test goes red (its authored crop differs from the model
    clip, so the two resolutions give different lattices under the cap).
"""
import json

import pytest

from vop_interwoven.color_id_buffer import crop_read_back_record, crop_write_fault

from tests import test_probe_anno_pass_run_variant as world
from tests.stage_a_capture_fakes import FakeBoundingBoxXYZ, FakeXYZ
from tests.test_stage_a_registered_capture import _run


def _sidecar(path):
    with open(path) as handle:
        return json.load(handle)


def _crop_writes():
    return [e for e in world._LOG if e[0] == "write" and e[1] in ("CropBox", "CropBoxActive")]


def _crop(view, rect):
    box = FakeBoundingBoxXYZ()
    box.Min, box.Max = FakeXYZ(rect[0], rect[1], 0), FakeXYZ(rect[2], rect[3], 0)
    object.__setattr__(view, "CropBox", box)


def test_an_active_authored_crop_is_never_written_and_is_recorded_as_read(tmp_path):
    out, view, _doc, exports, _diag = _run(tmp_path)
    assert _crop_writes() == [], _crop_writes()
    frame = _sidecar(out["sidecar_path"])["frame"]
    write = frame["crop_write"]
    assert write["written"] is False and write["source"] == "authored_read"
    assert write["read_back"]["read_back_uv"] == [20.0, 15.0, 80.0, 60.0]
    assert write["read_back"]["matches_request"] is True
    assert frame["crop_uv"] == [20.0, 15.0, 80.0, 60.0]
    # And the export saw the authored crop, untouched.
    assert exports[0]["crop_box"] == (20.0, 15.0, 80.0, 60.0)
    assert out["registration"]["success"] is True


def test_control_a_crop_inactive_view_is_written_and_read_back(tmp_path):
    out, view, _doc, exports, _diag = _run(tmp_path, crop_active=False)
    assert any(e[1] == "CropBox" for e in _crop_writes())
    frame = _sidecar(out["sidecar_path"])["frame"]
    write = frame["crop_write"]
    assert write["written"] is True and write["source"] == "model_crop_a"
    assert write["read_back"]["matches_request"] is True
    assert write["crop_box_active_read_back"] is True
    anno_write = _sidecar(out["annotation_sidecar_path"])["registration"]["crop_write"]
    assert anno_write["written"] is True and anno_write["read_back"]["matches_request"] is True
    assert out["registration"]["success"] is True


def _ignore_crop_writes_in(monkeypatch, tx_marker):
    """A scope box: a CropBox write under a transaction whose name contains
    ``tx_marker`` commits and the crop does not move."""
    real = world._ProbeView.__setattr__

    def _ignore_crop(self, name, value):
        tx = world._open_tx() if getattr(self, "_logging", False) else None
        if name == "CropBox" and tx is not None and tx_marker in tx:
            world._LOG.append(("write", name, value, tx))
            return
        real(self, name, value)
    monkeypatch.setattr(world._ProbeView, "__setattr__", _ignore_crop)


def _faults(side):
    return [f["fault"] for f in side["capture_integrity"]["capture_faults"]]


def test_a_model_crop_write_the_view_ignores_fails_the_capture_in_the_FILE(
        tmp_path, monkeypatch):
    _ignore_crop_writes_in(monkeypatch, "VOP Stage A SUPPRESS")
    out, view, _doc, _exports, _diag = _run(tmp_path, crop_active=False)
    model = _sidecar(out["sidecar_path"])
    assert model["frame"]["crop_write"]["read_back"]["matches_request"] is False
    assert model["failure_reason"] == "crop_write_not_applied"
    assert "crop_write_not_applied" in _faults(model)
    # A failed model pass stops the registered capture before the annotation
    # pass, as for any other model failure.
    assert out["success"] is False and out["registration"]["success"] is False


def test_an_annotation_crop_write_the_view_ignores_is_a_fault_in_its_FILE(
        tmp_path, monkeypatch):
    _ignore_crop_writes_in(monkeypatch, "ANNO")
    out, view, _doc, _exports, _diag = _run(tmp_path, crop_active=False)
    assert _sidecar(out["sidecar_path"])["frame"]["crop_write"]["read_back"][
        "matches_request"] is True
    anno = _sidecar(out["annotation_sidecar_path"])
    assert anno["registration"]["crop_write"]["read_back"]["matches_request"] is False
    assert "crop_write_not_applied" in _faults(anno)
    assert out["registration"]["success"] is False


def test_the_crop_write_reader_takes_the_old_shape_and_the_new():
    from tools.stage_a_sidecar_shapes import crop_write
    new = {"crop_uv": [0, 0, 1, 1], "crop_write": {"written": False}}
    assert crop_write(new) == {"written": False}
    old = crop_write({"crop_uv": [0, 0, 1, 1]})
    assert old["status"] == "rebuilt_from_pre_d_frame" and old["written"] is True
    assert old["read_back"]["state"] == "unavailable"
    assert crop_write({"crop_uv": None}) is None


def test_ticks_and_the_model_lattice_resolve_the_same_crop_A(tmp_path):
    """The authored crop (10..90 x 10..70) is NOT the raster's model clip
    (20..80 x 15..60). Under the cap the two give different lattices, so
    the ticks are on the model's only if both sized from resolve_crop_a."""
    def _cap(cfg):
        cfg.color_id_buffer_cap_axis_px = 400
    out, _v, _d, _e, _diag = _run(
        tmp_path, cfg_setup=_cap, view_setup=lambda v: _crop(v, (10, 10, 90, 70)))
    frame = _sidecar(out["sidecar_path"])["frame"]
    assert frame["cap_applied"] is True
    assert frame["crop_uv"] == [10.0, 10.0, 90.0, 70.0]
    assert out["registration"]["mark_fpp_basis"] == "model_lattice"
    assert out["registration"]["marks"]["layout"]["fpp_ft"] == pytest.approx(
        frame["achieved_fpp_ft"])
    # Discriminating: the model clip's own lattice is a different fpp.
    assert frame["achieved_fpp_ft"] != pytest.approx(60.0 / 400.0)


# --- the pure pieces ---------------------------------------------------------

def test_a_read_back_is_matched_within_a_twentieth_of_a_pixel_only():
    rec = crop_read_back_record((0, 0, 10, 10), (0, 0, 10.004, 10), None, 0.1)
    assert rec["matches_request"] is True
    rec = crop_read_back_record((0, 0, 10, 10), (0, 0, 10.006, 10), None, 0.1)
    assert rec["matches_request"] is False


def test_an_unread_crop_is_unmeasured_never_matched():
    rec = crop_read_back_record((0, 0, 1, 1), None, "AttributeError: x", 0.1)
    assert rec["matches_request"] is None and rec["read_error"] == "AttributeError: x"
    assert crop_write_fault({"written": True, "read_back": rec}) == "crop_write_unverified"
    assert crop_write_fault({"written": False, "source": "authored_read",
                             "read_back": rec}) == "authored_crop_unreadable"
    assert crop_write_fault(None) is None
