"""A model-pass crop write that cannot be made is a capture FAULT.

The model pass sizes its lattice on crop A and then writes it as the view's
crop. When that write raised -- or the view had no CropBox to write -- it used
to warn and carry on: the export rendered whatever extent Revit chose, and
the capture was recorded as clean. Drafting views and legends now go through
Stage A, and Revit may refuse them a crop, so that path must fail loudly
(``crop_write_failed``). The annotation pass already faults its own case as
``annotation_frame_not_applied``.

Asserted on the PERSISTED sidecar, not only the returned dict (CLAUDE.md
defect class 4), with a control on a view whose crop write succeeds.

Mutation: dropping the ``write_error`` record in either branch of the model
pass's crop write, or the ``write_error`` check in crop_write_fault, turns
the two fault tests red and leaves the control green.
"""
import json

from vop_interwoven.color_id_buffer import crop_write_fault

from tests.stage_a_capture_fakes import FakeViewPlan
from tests.test_stage_a_annotation_pass import VIEW_ID, _run_both_passes


class _RefusesCrop(FakeViewPlan):
    """A view whose CropBox reads but cannot be written (once set up)."""

    def __init__(self, view_id):
        self._ready = False
        FakeViewPlan.__init__(self, view_id)
        self._ready = True

    def __setattr__(self, name, value):
        if name == "CropBox" and getattr(self, "_ready", False):
            raise RuntimeError("this view type does not support a crop (fake)")
        object.__setattr__(self, name, value)


def _persisted(result):
    with open(result["sidecar_path"]) as f:
        return json.load(f)


def _fault_names(sidecar):
    return [f["fault"] for f in sidecar["capture_integrity"]["capture_faults"]]


def test_a_crop_write_that_raises_fails_the_model_capture(tmp_path):
    model, _anno, _g, _d, _v, _diag = _run_both_passes(
        tmp_path, view=_RefusesCrop(VIEW_ID))
    assert model["success"] is False
    assert model["failure_reason"] == "crop_write_failed"
    persisted = _persisted(model)
    assert "crop_write_failed" in _fault_names(persisted)
    assert persisted["failure_reason"] == "crop_write_failed"
    crop_write = persisted["frame"]["crop_write"]
    assert crop_write["written"] is False
    assert crop_write["write_attempted"] is True
    assert "does not support a crop" in crop_write["write_error"]


def test_a_view_with_no_crop_box_to_write_fails_the_model_capture(tmp_path, monkeypatch):
    import vop_interwoven.revit.view_basis as view_basis
    monkeypatch.setattr(view_basis, "crop_box_from_uv_bounds", lambda *a, **k: None)
    model, _anno, _g, _d, _v, _diag = _run_both_passes(tmp_path)
    persisted = _persisted(model)
    assert model["success"] is False
    assert "crop_write_failed" in _fault_names(persisted)
    assert persisted["frame"]["crop_write"]["write_error"]


def test_control_a_crop_write_that_takes_is_not_a_fault(tmp_path):
    model, _anno, _g, _d, _v, _diag = _run_both_passes(tmp_path)
    persisted = _persisted(model)
    assert "crop_write_failed" not in _fault_names(persisted)
    assert "write_error" not in (persisted["frame"]["crop_write"] or {})
    assert persisted["frame"]["crop_write"]["written"] is True


def test_crop_write_fault_ranks_a_write_error_first():
    # A write that raised after its read-back was recorded still failed.
    assert crop_write_fault({"written": True, "write_error": "x",
                             "read_back": {"matches_request": True},
                             "crop_box_active_read_back": True}) == "crop_write_failed"
    assert crop_write_fault({"written": False, "source": "model_crop_a",
                             "write_error": "x"}) == "crop_write_failed"
