"""Stage A step 3 -- the four defects PR #211's review found, pinned.

All four were real and all four were mine. Sourced to
https://github.com/GMcDowellJr/Revit_SSM_Exporter/pull/211 (review by
chatgpt-codex-connector, 2026-09-21). Each test below reinstates the
original defect if production regresses, which is the only thing that makes
a regression test worth having.

  P1  the annotation result reached no streaming consumer
  P1  a suppression failure left the view detached from its template
  P1  a failed restore step still reported the capture successful
  P2  RAISED, not fixed -- see the module note in the annotation pass and
      the reply on that thread; it turns on a membership rule Greg set.
"""
import types

import pytest

import vop_interwoven.color_id_buffer as color_id_buffer
from vop_interwoven import streaming
from vop_interwoven.config import Config

from tests.stage_a_capture_fakes import (
    FakeDiag,
    FakeViewPlan,
    install_fake_revit_db,
)
from tests.test_stage_a_annotation_pass import (
    ANNO_CAT,
    GRID_CAT,
    LINES_CAT,
    MODEL_CAT,
    VIEW_ID,
    _SizedDoc,
    _elements,
    _raster,
)
from tests.test_stage_a_annotation_pass_wiring import GEOM


# ==========================================================================
# P1 -- the annotation result must reach the streaming consumers
# ==========================================================================

class _Recorder(streaming.StreamingExporter):
    """The real StreamingExporter, instantiated without touching disk.

    Subclassed rather than faked: the defect was that the real consumer
    never saw the data, so a stand-in for the consumer would prove nothing.
    Only __init__ is replaced, and only to skip the file handles the Stage A
    branch never reaches.
    """

    def __init__(self):
        self.view_summaries = []
        self.full_results = []
        self.views_processed = 0
        self.views_failed = 0


def _stage_a_result(annotation=None):
    entry = {
        "view_id": VIEW_ID,
        "view_name": "L1 Plan",
        "success": True,
        "stage": "color_id_buffer_stage_a",
        "tiff_path": "/out/L1_77.tiff",
        "sidecar_path": "/out/L1_77.json",
    }
    if annotation is not None:
        entry["annotation_pass"] = annotation
        entry["annotation_pass_success"] = bool(annotation.get("success"))
        entry["annotation_pass_failure_reason"] = annotation.get("failure_reason")
        entry["annotation_tiff_path"] = annotation.get("tiff_path")
        entry["annotation_sidecar_path"] = annotation.get("sidecar_path")
    return entry


def test_a_failed_annotation_capture_is_visible_in_the_view_summary():
    """The defect: it was not, and the run reported clean."""
    summary = streaming._stage_a_annotation_summary(_stage_a_result({
        "success": False,
        "failure_reason": "annotation_overrides_not_restored",
        "tiff_path": "/out/L1_77_anno.tiff",
        "sidecar_path": "/out/L1_77_anno.json",
    }))

    assert summary["annotation_pass_success"] is False
    assert summary["annotation_pass_failure_reason"] == (
        "annotation_overrides_not_restored")
    # The evidence is still on disk and the summary points at it.
    assert summary["annotation_tiff_path"] == "/out/L1_77_anno.tiff"


def test_a_pass_that_did_not_run_is_not_reported_as_a_failure():
    """THREE-VALUED. A bool cannot carry three states.

    Reporting "the flag was off" as False would inventory every ordinary
    model-only capture as a failed annotation capture.
    """
    summary = streaming._stage_a_annotation_summary(_stage_a_result(annotation=None))
    assert summary["annotation_pass_success"] == "not_applicable"
    assert "annotation_pass_failure_reason" not in summary


def test_a_successful_annotation_capture_reads_true_not_not_applicable():
    """CONTROL. Without it, an implementation that always returned
    "not_applicable" would pass the test above."""
    summary = streaming._stage_a_annotation_summary(_stage_a_result({
        "success": True, "failure_reason": None,
        "tiff_path": "/out/a.tiff", "sidecar_path": "/out/a.json",
    }))
    assert summary["annotation_pass_success"] is True


def test_the_real_exporter_carries_the_annotation_outcome_through():
    """Drives StreamingExporter itself, not just the helper.

    The defect was never in a helper -- it was that the consumer never saw
    the data. So the gate is the consumer.
    """
    exporter = _Recorder()
    exporter.on_view_complete(_stage_a_result({
        "success": False,
        "failure_reason": "annotation_view_state_not_restored",
        "tiff_path": "/out/a.tiff",
        "sidecar_path": "/out/a.json",
    }))

    assert len(exporter.view_summaries) == 1
    summary = exporter.view_summaries[0]
    assert summary["annotation_pass_success"] is False
    assert summary["annotation_pass_failure_reason"] == (
        "annotation_view_state_not_restored")
    # full_results keeps the whole nested result, so the annotation sidecar
    # is reachable from the run record and not only from the filesystem.
    assert exporter.full_results[0]["annotation_pass"]["failure_reason"] == (
        "annotation_view_state_not_restored")


# ==========================================================================
# P1 -- a suppression failure must not leave the template detached
# ==========================================================================

class _SuppressFailsView(FakeViewPlan):
    """Detach succeeds; the first category hide inside suppress_tx raises.

    That is the exact window the defect lived in: the detach is committed in
    its OWN transaction, so rolling back suppress_tx does not undo it, and
    the restore that would re-attach sits in the export's `finally`, which a
    raise from suppression never reaches.
    """

    def __init__(self, view_id, template_id):
        FakeViewPlan.__init__(self, view_id)
        self.ViewTemplateId = template_id
        self.template_writes = []

    def __setattr__(self, name, value):
        if name == "ViewTemplateId" and hasattr(self, "template_writes"):
            self.template_writes.append(value)
        object.__setattr__(self, name, value)

    def SetCategoryHidden(self, cat_id, value):
        raise RuntimeError("category visibility is locked on this view")


def _run_with_failing_suppression(tmp_path):
    from tests.stage_a_capture_fakes import FakeElementId

    template_id = FakeElementId(9001)
    view = _SuppressFailsView(VIEW_ID, template_id)
    elements = _elements()
    doc = _SizedDoc(elements=elements, link_instances=[],
                    categories=[MODEL_CAT, ANNO_CAT, LINES_CAT, GRID_CAT])
    cfg = Config()
    cfg.debug_dump_path = str(tmp_path)
    diag = FakeDiag()

    with install_fake_revit_db():
        with pytest.raises(RuntimeError):
            color_id_buffer.export_annotation_color_id_buffer_view(
                doc, view, cfg, GEOM, diag=diag, raster=_raster(),
                elements=elements,
            )
    return view, template_id, diag


def test_a_suppression_failure_reattaches_the_view_template(tmp_path):
    view, template_id, diag = _run_with_failing_suppression(tmp_path)

    # Detached, then put back. The LAST write is what the document is left
    # with, and it must be the view's own template.
    assert len(view.template_writes) >= 2
    assert view.template_writes[-1] is template_id
    assert view.ViewTemplateId is template_id


def test_the_suppression_failure_still_propagates(tmp_path):
    """CONTROL. The re-attach must not swallow the failure that caused it --
    a capture that silently half-ran is worse than one that raises."""
    view, template_id, diag = _run_with_failing_suppression(tmp_path)
    # pytest.raises inside the helper already asserts this; the control is
    # that the view was genuinely detached first, so the re-attach was real
    # work and not a no-op on a view that never had a template.
    from tests.stage_a_capture_fakes import INVALID_ELEMENT_ID
    assert INVALID_ELEMENT_ID in view.template_writes


# ==========================================================================
# P1 -- a failed restore step must fail the capture
# ==========================================================================

class _RestoreFailsView(FakeViewPlan):
    """Everything works until the crop restore, which raises."""

    def __init__(self, view_id):
        FakeViewPlan.__init__(self, view_id)
        self._exported = False

    def __setattr__(self, name, value):
        if (name == "CropBox" and getattr(self, "_exported", False)):
            raise RuntimeError("crop box is locked after export")
        object.__setattr__(self, name, value)


def test_a_failed_restore_step_fails_the_capture(tmp_path):
    elements = _elements()
    view = _RestoreFailsView(VIEW_ID)
    doc = _SizedDoc(elements=elements, link_instances=[],
                    categories=[MODEL_CAT, ANNO_CAT, LINES_CAT, GRID_CAT])

    def _mark(opts):
        object.__setattr__(view, "_exported", True)
    doc.on_export_image = _mark

    cfg = Config()
    cfg.debug_dump_path = str(tmp_path)
    diag = FakeDiag()

    with install_fake_revit_db():
        result = color_id_buffer.export_annotation_color_id_buffer_view(
            doc, view, cfg, GEOM, diag=diag, raster=_raster(), elements=elements,
        )

    # The TIFF is fine. The DOCUMENT is not as it was found, and that is a
    # failed capture -- which is precisely what the defect denied.
    assert result["success"] is False
    assert result["failure_reason"] == "annotation_view_state_not_restored"
    failures = result["metadata"]["restore_failures"]
    assert any(f["callsite"] == "annotation_restore_crop_box" for f in failures)
    assert any(e["callsite"] == "annotation_restore_crop_box" for e in diag.errors)


def test_a_clean_restore_records_no_failures(tmp_path):
    """CONTROL. Without it, an implementation that always reported a restore
    failure would pass the test above."""
    from tests.test_stage_a_annotation_pass import _run_both_passes

    _m, anno_result, _g, _d, _v, _diag = _run_both_passes(tmp_path)
    assert anno_result["metadata"]["restore_failures"] == []
    assert anno_result["success"] is True


def test_restore_continues_past_a_failing_step(tmp_path):
    """The accumulation must not turn into an early exit.

    One failing step must still never stop the steps after it -- that is why
    _restore_step swallows in the first place. Recording the failure changed
    the REPORT, and must not have changed the behaviour.
    """
    elements = _elements()
    view = _RestoreFailsView(VIEW_ID)
    doc = _SizedDoc(elements=elements, link_instances=[],
                    categories=[MODEL_CAT, ANNO_CAT, LINES_CAT, GRID_CAT])
    doc.on_export_image = lambda opts: object.__setattr__(view, "_exported", True)

    cfg = Config()
    cfg.debug_dump_path = str(tmp_path)

    with install_fake_revit_db():
        result = color_id_buffer.export_annotation_color_id_buffer_view(
            doc, view, cfg, GEOM, diag=FakeDiag(), raster=_raster(),
            elements=elements,
        )

    # The crop restore failed; the element overrides after it still ran and
    # still verified clean.
    check = result["metadata"]["override_restore_check"]
    assert check["verified_cleared_count"] == 4
    assert check["still_set_count"] == 0


# ==========================================================================
# P1 -- an authored override the paint destroys must not read as "restored"
# ==========================================================================

def _view_with_authored_override(element_id):
    """A view where one annotation already carries an authored override.

    The blank-restore is unchanged (see the module note: reapplying a
    captured OverrideGraphicSettings across a transaction boundary is the
    behaviour this module removed on evidence). What is pinned here is that
    the capture stops CLAIMING it put the view back as it found it.
    """
    class _Authored(FakeViewPlan):
        def __init__(self, view_id):
            FakeViewPlan.__init__(self, view_id)
            self._restored = set()

        def GetElementOverrides(self, eid):
            ogs = FakeViewPlan.GetElementOverrides(self, eid)
            if (int(eid.IntegerValue) == element_id
                    and element_id not in self._restored):
                ogs.ProjectionLineColor = types.SimpleNamespace(IsValid=True)
            return ogs

        def SetElementOverrides(self, eid, ogs):
            # A blank override is the restore; from then on the element reads
            # clear -- exactly as Revit would, and exactly why the read-back
            # alone cannot see that something authored was lost.
            if int(eid.IntegerValue) == element_id and not getattr(ogs, "calls", None):
                self._restored.add(element_id)
            FakeViewPlan.SetElementOverrides(self, eid, ogs)

    return _Authored(VIEW_ID)


def _capture_with(view, tmp_path):
    elements = _elements()
    doc = _SizedDoc(elements=elements, link_instances=[],
                    categories=[MODEL_CAT, ANNO_CAT, LINES_CAT, GRID_CAT])
    cfg = Config()
    cfg.debug_dump_path = str(tmp_path)
    diag = FakeDiag()
    with install_fake_revit_db():
        result = color_id_buffer.export_annotation_color_id_buffer_view(
            doc, view, cfg, GEOM, diag=diag, raster=_raster(), elements=elements,
        )
    return result, diag


def test_an_authored_override_destroyed_by_the_paint_is_recorded(tmp_path):
    result, diag = _capture_with(_view_with_authored_override(2002), tmp_path)

    authored = result["metadata"]["authored_overrides_replaced"]
    assert authored["status"] == "value"
    assert authored["checked_count"] == 4
    assert authored["replaced_count"] == 1
    assert authored["replaced_element_ids"] == [2002]
    assert any(w["callsite"] == "annotation_authored_override_replaced"
               for w in diag.warnings)


def test_the_read_back_alone_cannot_see_it(tmp_path):
    """THE POINT. This is what makes the record above load-bearing.

    After restore the element reads blank, so the override read-back reports
    a clean, fully-verified restore -- over a view whose authored override
    is gone. Two facts, and only one of them was ever recorded.
    """
    result, _diag = _capture_with(_view_with_authored_override(2002), tmp_path)

    check = result["metadata"]["override_restore_check"]
    assert check["verified_cleared_count"] == 4
    assert check["still_set_count"] == 0
    # ... and yet:
    assert result["metadata"]["authored_overrides_replaced"]["replaced_count"] == 1


def test_no_authored_overrides_records_zero_not_unavailable(tmp_path):
    """CONTROL. Without it, an implementation that reported every element as
    authored -- or none of them, always -- would pass the tests above."""
    from tests.test_stage_a_annotation_pass import _run_both_passes

    _m, anno_result, _g, _d, _v, _diag = _run_both_passes(tmp_path)
    authored = anno_result["metadata"]["authored_overrides_replaced"]
    assert authored["status"] == "value"
    assert authored["checked_count"] == 4
    assert authored["replaced_count"] == 0
    assert authored["replaced_element_ids"] == []
