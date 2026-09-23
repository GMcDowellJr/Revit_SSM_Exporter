"""Production's REGISTERED Stage A capture, DRIVEN end to end on the fake DB.

``export_registered_stage_a_view`` sequences the real model and annotation
passes, the registration marks and the membership white suppression inside one
TransactionGroup, and uses the ROLLBACK as the restore. What is asserted is
what each export SAW when it happened (the fake doc's ExportImage hook) and
what the FILES say afterwards -- not the return value alone, because a record
written before the read-back would carry no restore verdict at all
(CLAUDE.md, defect class 4).

The fake world is the annotation-pass variant probe's (its TransactionGroup
genuinely rolls view and document state back), so the probe and production
are held to the same harness.
"""
import json
import types

import vop_interwoven.stage_a_registration as registration
from vop_interwoven.config import Config
from vop_interwoven.stage_a_registered_capture import (
    export_registered_stage_a_view, view_state_verdict,
)

from tests import test_probe_anno_pass_run_variant as world
from tests.stage_a_capture_fakes import FakeDiag, FakeElement, install_fake_revit_db
from tests.test_stage_a_annotation_pass_probe_switches import (
    ANNO_CAT, MODEL_CAT, OTHER_MODEL_CAT, VIEW_ID, _BBox, _SizedDoc, _elements,
    _model_pass_elements, _raster,
)

WHITE = (255, 255, 255)


def _run(tmp_path, rollback_restores=("view", "doc"), break_model_pass=False,
         monkeypatch=None, leave_view_changed=False):
    del world._LOG[:]
    elements = _elements()
    elements[0].bbox = _BBox((25, 18, 0), (26, 19, 0))
    elements.append(FakeElement(1003, MODEL_CAT))
    view = world._ProbeView(VIEW_ID)
    view.Scale = 96
    doc = _SizedDoc(elements=elements, link_instances=[],
                    categories=[MODEL_CAT, OTHER_MODEL_CAT, ANNO_CAT,
                                world.LINES_CAT])
    doc.IsModifiable = False
    doc.Create = world._Create(doc)
    exports = []

    def _at_export(opts):
        exports.append({
            "path": opts.FilePath,
            "overrides": dict((eid, world._colour_of(ogs))
                              for eid, ogs in view.element_overrides.items()),
            "lines_hidden": view.category_hidden.get(
                world.LINES_CAT.Id.IntegerValue, False),
            "marks_in_doc": sorted(i for i in doc._by_id if i > world.MARK_ID_BASE),
        })

    doc.on_export_image = _at_export
    cfg = Config(enable_color_id_buffer_stage_a=True,
                 color_id_buffer_registered_capture=True,
                 color_id_buffer_export_dpi=150.0)
    cfg.include_linked_rvt = False
    cfg.debug_dump_path = str(tmp_path)
    diag = FakeDiag()
    with install_fake_revit_db() as fake_db:
        fake_db.Transaction = world._Tx
        fake_db.TransactionGroup = world._Group
        fake_db.TransactionStatus = world._STATUS
        fake_db.Line = world._Line
        fake_db.FilteredElementCollector = world._ViewCollector
        world._ROLLBACK_TARGETS[:] = [obj for name, obj in (("view", view),
                                                            ("doc", doc))
                                      if name in rollback_restores]
        if break_model_pass:
            monkeypatch.setattr(
                "vop_interwoven.color_id_buffer.export_color_id_buffer_view",
                lambda *a, **k: {"success": False, "failure_reason": "boom",
                                 "tiff_path": None, "sidecar_path": None})
        if leave_view_changed:
            from vop_interwoven import color_id_buffer as _cib
            real_anno = _cib.export_annotation_color_id_buffer_view

            def _leaky(*a, **k):
                result = real_anno(*a, **k)
                object.__setattr__(view, "CropBoxVisible", True)
                return result
            monkeypatch.setattr(
                "vop_interwoven.color_id_buffer."
                "export_annotation_color_id_buffer_view", _leaky)
        out = export_registered_stage_a_view(
            doc, view, _model_pass_elements(elements), cfg, diag=diag,
            raster=_raster())
    return out, view, doc, exports, diag


def _sidecar(path):
    with open(path) as handle:
        return json.load(handle)


def test_both_passes_run_registered_and_the_view_comes_back(tmp_path):
    out, view, doc, exports, diag = _run(tmp_path)
    reg = out["registration"]
    assert reg["faults"] == [], reg["faults"]
    assert out["success"] is True and out["annotation_pass_success"] is True
    assert out["registration_success"] is True
    assert reg["marks"]["created_count"] == 12
    model_export, anno_export = exports
    # MODEL export: the marks exist, OST_Lines is visible, the marks are in
    # the reserved colour, and nothing is white yet.
    assert len(model_export["marks_in_doc"]) == 12
    assert model_export["lines_hidden"] is False
    mark_ids = [m["id"] for m in reg["marks"]["created"]]
    assert all(model_export["overrides"][i] == registration.MARK_COLOUR
               for i in mark_ids)
    assert WHITE not in model_export["overrides"].values()
    # ANNOTATION export: every model member white, the marks in the pass's
    # own palette colours.
    for eid in (1001, 1002, 1003):
        assert anno_export["overrides"][eid] == WHITE, eid
    assert all(anno_export["overrides"][i] not in (WHITE, registration.MARK_COLOUR)
               for i in mark_ids)
    # The rollback put everything back, and the read-back says so.
    assert reg["restore"]["rolled_back"] is True
    assert all(v["status"] == "restored" for v in reg["restore"]["view_state"].values())
    assert reg["restore"]["marks_still_in_project"] == []
    assert reg["restore"]["element_overrides"]["left_behind"] == []
    assert [i for i in doc._by_id if i > world.MARK_ID_BASE] == []


def test_the_annotation_pass_never_writes_the_crop(tmp_path):
    """Mutation: drop crop_mode "untouched" from the annotation cfg."""
    _run(tmp_path)
    assert world._anno_crop_writes() == []


def test_the_FILES_carry_the_registration_record_with_its_restore_verdict(tmp_path):
    """Assert the FILE, not the return value (defect class 4). A clean capture
    persists an EMPTY fault list -- present and empty is a different fact from
    absent, which is what the control half of this test pins."""
    out, view, doc, exports, diag = _run(tmp_path)
    model = _sidecar(out["sidecar_path"])["registration_marks"]
    anno = _sidecar(out["annotation_sidecar_path"])["registration_marks"]
    for record, pass_name in ((model, "model"), (anno, "annotation")):
        assert record["pass"] == pass_name
        assert record["faults"] == []
        assert record["restore"]["rolled_back"] is True
        assert record["annotation_crop_mode"] == "untouched"
        assert len(record["marks"]) == 12
    assert {tuple(m["rgb"]) for m in model["marks"]} == {registration.MARK_COLOUR}
    colours = _sidecar(out["annotation_sidecar_path"])["color_assignment_map"]
    assert all(m["rgb"] == colours[str(m["id"])] for m in anno["marks"])


def test_a_rollback_that_undoes_nothing_is_a_fault_in_the_FILE(tmp_path):
    """The marks and the white overrides are removed ONLY by the rollback, so
    a rollback that undoes nothing leaves both, and both are faults. (The view
    properties read back restored even then: each pass restores its own
    explicitly -- which is why they need the separate test below.)
    Mutation: read back before the rollback, or skip either check."""
    out, view, doc, exports, diag = _run(tmp_path, rollback_restores=())
    faults = [f["fault"] for f in out["registration"]["faults"]]
    assert faults == ["registration_marks_left_in_project",
                      "element_overrides_left_behind"]
    assert out["registration_success"] is False
    persisted = _sidecar(out["annotation_sidecar_path"])["registration_marks"]["faults"]
    assert [f["fault"] for f in persisted] == faults
    assert diag.errors


def test_marks_left_behind_alone_are_a_fault(tmp_path):
    """View restored, document not: only the marks' own check can see it."""
    out, view, doc, exports, diag = _run(tmp_path, rollback_restores=("view",))
    faults = [f["fault"] for f in out["registration"]["faults"]]
    assert faults == ["registration_marks_left_in_project"]


def test_a_failed_model_pass_skips_the_annotation_pass_and_still_rolls_back(
        tmp_path, monkeypatch):
    out, view, doc, exports, diag = _run(tmp_path, break_model_pass=True,
                                         monkeypatch=monkeypatch)
    faults = [f["fault"] for f in out["registration"]["faults"]]
    assert faults == ["registered_capture_raised"]
    assert "annotation_pass" not in out
    assert out["registration"]["restore"]["rolled_back"] is True
    assert out["registration"]["restore"]["marks_still_in_project"] == []
    assert out["registration_success"] is False


def test_view_state_verdict_is_three_valued():
    value = {"state": "value", "value": 1}
    assert view_state_verdict({"a": value}, {"a": value})["a"]["status"] == "restored"
    assert view_state_verdict({"a": value}, {"a": {"state": "value", "value": 2}})[
        "a"]["status"] == "not_restored"
    assert view_state_verdict({"a": value}, {"a": {"state": "unavailable",
                                                   "reason": "x"}})[
        "a"]["status"] == "unverified"


def test_a_view_property_left_changed_is_a_fault_when_nothing_undoes_it(
        tmp_path, monkeypatch):
    """A pass that leaves CropBoxVisible changed, and a rollback that undoes
    nothing to the view: only the view-state read-back can see it.
    Mutation: drop crop_box_visible from view_state, or skip the verdict."""
    out, view, doc, exports, diag = _run(tmp_path, rollback_restores=("doc",),
                                         monkeypatch=monkeypatch,
                                         leave_view_changed=True)
    faults = [f["fault"] for f in out["registration"]["faults"]]
    assert "view_state_not_restored" in faults
    assert out["registration"]["restore"]["view_state"]["crop_box_visible"][
        "status"] == "not_restored"


def test_the_same_leak_is_undone_by_a_real_rollback(tmp_path, monkeypatch):
    """The CONTROL for the test above."""
    out, view, doc, exports, diag = _run(tmp_path, monkeypatch=monkeypatch,
                                         leave_view_changed=True)
    assert out["registration"]["faults"] == []
