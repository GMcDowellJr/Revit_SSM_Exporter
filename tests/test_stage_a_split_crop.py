"""Split crops (probe Q7, run 20261005T084311): un-split for the capture,
the shown bands recorded.

HIGH ROOF PLAN (3300684) is split along its width: region 0 the left 36 % of
the crop, region 1 the right 8.6 % moved 115.9 ft left. Its export is neither
crop A nor a band, so it could not register. Removing a region of a
two-region split un-splits the view with the crop box unchanged, so the
registered capture removes the split inside its group (the rollback puts it
back), captures the ordinary view, and records the bands the view SHOWS for
the grid to keep.

Mutations: no removal -> the split FILE test red (export of a split view);
the bands from the wrong edge -> the arithmetic test red; no read-back of
the removal -> the "ignored" test red; split_crop dropped from view_state ->
the restore test red.
"""

import pytest

from vop_interwoven.stage_a_registered_capture import split_bands_uv

from tests import test_probe_anno_pass_run_variant as world
from tests.test_stage_a_registered_capture import _run, _sidecar

# Q7's own numbers.
Q7_CROP_UV = (-99.54686364019143, 2257.623066316009, 118.863631169041, 2336.7065348908263)
Q7_STATE = {"count": 2, "split": True, "horizontal": True, "vertical": False,
            "regions": [{"min": 0.0, "max": 0.36013550963313384, "offset": [0, 0, 0]},
                        {"min": 0.9137149467519557, "max": 1.0,
                         "offset": [-115.88538901042803, 0, 0]}]}


def test_the_bands_are_the_regions_fractions_of_the_crop_width_from_its_left_edge():
    bands, reason = split_bands_uv(Q7_CROP_UV, Q7_STATE)
    assert reason is None
    w = Q7_CROP_UV[2] - Q7_CROP_UV[0]
    assert bands[0] == pytest.approx([Q7_CROP_UV[0], Q7_CROP_UV[1],
                                      Q7_CROP_UV[0] + 0.36013550963313384 * w, Q7_CROP_UV[3]])
    assert bands[1] == pytest.approx([Q7_CROP_UV[0] + 0.9137149467519557 * w, Q7_CROP_UV[1],
                                      Q7_CROP_UV[2], Q7_CROP_UV[3]])
    # Region 0 holds grids 100-103 at the LEFT of the unsplit export.
    assert bands[0][0] == pytest.approx(-99.5469) and bands[0][2] == pytest.approx(-20.8895, abs=1e-3)


def test_a_vertical_split_or_an_unread_crop_is_refused_not_guessed():
    vertical = dict(Q7_STATE, horizontal=False, vertical=True)
    assert split_bands_uv(Q7_CROP_UV, vertical)[0] is None
    assert "vertical" in split_bands_uv(Q7_CROP_UV, vertical)[1]
    assert split_bands_uv(None, Q7_STATE) == (
        None, "the crop could not be read, so the bands have no extent")


# --- through the registered capture, asserted in the FILES -------------------

SPLIT = [(0.0, 0.36, 0.0), (0.91, 1.0, -30.0)]


def _split(view):
    object.__setattr__(view, "split_regions", list(SPLIT))


def _faults(path):
    return [f["fault"] for f in _sidecar(path)["capture_integrity"]["capture_faults"]]


def test_a_split_view_is_captured_unsplit_and_its_bands_are_in_the_FILES(
        tmp_path, monkeypatch):
    # The split-region count the view reports at each ExportImage: both
    # captures must be of the UNSPLIT view.
    at_export, holder = [], []

    def _setup(view):
        _split(view)
        holder.append(view)
    from tests import test_stage_a_annotation_pass_probe_switches as switches
    real_export = switches._SizedDoc.ExportImage

    def _export(self, opts):
        at_export.append(holder[0].GetCropRegionShapeManager().NumberOfSplitRegions)
        return real_export(self, opts)
    monkeypatch.setattr(switches._SizedDoc, "ExportImage", _export)
    out, view, _doc, exports, _diag = _run(tmp_path, view_setup=_setup)
    assert at_export == [1, 1]
    for path in (out["sidecar_path"], out["annotation_sidecar_path"]):
        rec = _sidecar(path)["registration_marks"]["split_crop"]
        assert rec["state"] == "removed", path
        assert rec["before"]["count"] == 2 and rec["read_back"]["value"]["count"] == 1
        crop = rec["crop_uv"]
        w = crop[2] - crop[0]
        want = [[crop[0], crop[1], crop[0] + 0.36 * w, crop[3]],
                [crop[0] + 0.91 * w, crop[1], crop[2], crop[3]]]
        assert len(rec["bands_uv"]) == 2
        for got, band in zip(rec["bands_uv"], want):
            assert got == pytest.approx(band)
        assert _faults(path) == [], path
    assert out["registration_success"] is True
    # The rollback put the split back, and the read-back says so.
    assert view.split_regions == SPLIT
    assert out["registration"]["restore"]["view_state"]["split_crop"]["status"] == "restored"


def test_control_an_unsplit_view_is_not_touched(tmp_path):
    out, view, _doc, _e, _diag = _run(tmp_path)
    rec = _sidecar(out["sidecar_path"])["registration_marks"]["split_crop"]
    assert rec == {"state": "not_split"}
    assert out["registration_success"] is True


@pytest.mark.parametrize("mode", ["raises", "ignored"])
def test_a_split_that_will_not_come_off_is_a_fault_in_the_FILE(tmp_path, monkeypatch, mode):
    def _remove(self):
        self.remove_split_calls += 1
        if mode == "raises":
            raise RuntimeError("RemoveSplit refused (fake)")
    monkeypatch.setattr(world._Manager, "RemoveSplit", _remove)
    out, _view, _doc, _e, _diag = _run(tmp_path, view_setup=_split)
    for path in (out["sidecar_path"], out["annotation_sidecar_path"]):
        assert "split_crop_not_removed" in _faults(path), path
        assert _sidecar(path)["registration_marks"]["split_crop"]["state"] == "unavailable"
    assert out["registration_success"] is False


def test_a_split_state_that_will_not_read_is_unverified_in_the_FILE(tmp_path, monkeypatch):
    def _count(self):
        raise RuntimeError("NumberOfSplitRegions refused (fake)")
    monkeypatch.setattr(world._Manager, "NumberOfSplitRegions", property(_count))
    out, _view, _doc, _e, _diag = _run(tmp_path)
    assert "split_crop_unverified" in _faults(out["sidecar_path"])
    assert out["registration_success"] is False


def test_a_split_the_rollback_did_not_restore_is_a_fault(tmp_path):
    """The rollback restores nothing on the view: the split stays removed,
    and the read-back after the rollback must say so."""
    out, view, _doc, _e, _diag = _run(tmp_path, view_setup=_split,
                                      rollback_restores=("doc",))
    verdict = out["registration"]["restore"]["view_state"]["split_crop"]
    assert verdict["status"] == "not_restored"
    assert "view_state_not_restored" in [f["fault"] for f in out["registration"]["faults"]]
