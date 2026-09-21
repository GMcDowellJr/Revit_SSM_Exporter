"""The neutral-phase-filter swap is GATED, and the gate decides which element
set gets painted.

Two things shipped together in color_id_buffer.export_color_id_buffer_view and
are tested together here because only one of them is visible from outside:

  1. ``Config.color_id_neutral_phase_swap`` (default False) decides whether the
     view is swapped onto VOP_NeutralPhaseFilter for the capture.
  2. With the swap off, the "Re-collect under the neutral phase state" call is
     skipped and the paint set is the collection the PIPELINE handed in.

A test that only asserted (1) would stay green if (2) kept re-collecting, and
the re-collected set is the one that actually reaches the palette -- so every
case below asserts on the element ids that were painted, not only on whether a
phase filter was created. ``_painted_ids`` reads them off the view, which is
where production put them.

Controls, because a fake that cannot reach the code under test proves nothing
(CLAUDE.md, "Know what the fake harness does NOT provide"):
  - ``test_fake_surface_reaches_the_crop_path`` pins that this file's fake
    Revit surface really exercises the crop branch rather than falling into
    its ImportError fallback.
  - ``test_swap_on_restores_previous_behaviour`` is the positive control: the
    gated-off assertions below would also pass against a build where the swap
    path had simply been deleted.
"""
import types

import vop_interwoven.color_id_buffer as color_id_buffer
import vop_interwoven.revit.collection as revit_collection
from vop_interwoven.config import Config
from vop_interwoven.core.math_utils import Bounds2D
from vop_interwoven.revit.view_basis import ViewBasis

from tests.stage_a_capture_fakes import (
    FakeBuiltInParameter,
    FakeCategory,
    FakeDiag,
    FakeDoc,
    FakeElement,
    FakeParameter,
    FakePhaseFilter,
    FakeView,
    install_fake_revit_db,
)

_PLAN_BASIS = ViewBasis(origin=(0, 0, 0), right=(1, 0, 0), up=(0, 1, 0), forward=(0, 0, -1))

# Two elements the pipeline collected, plus one only a re-collection under the
# neutral phase filter would reveal -- the demolished element the swap exists
# to expose. Which of the two sets reaches the palette is the whole question.
_PIPELINE_IDS = (1001, 1002)
_PHASE_REVEALED_ID = 1003


def _raster():
    return types.SimpleNamespace(
        W=64,
        H=48,
        cell_size_ft=1.0,
        bounds_xy=Bounds2D(0.0, 0.0, 64.0, 48.0),
        model_clip_bounds=None,
        view_basis=_PLAN_BASIS,
    )


def _walls():
    return FakeCategory("Walls", 10)


def _make_world(view_id=42, phase_filter_read_only=False):
    cat = _walls()
    pipeline_elements = [FakeElement(eid, cat) for eid in _PIPELINE_IDS]
    phase_revealed = FakeElement(_PHASE_REVEALED_ID, cat)
    doc = FakeDoc(elements=pipeline_elements + [phase_revealed])
    original_pf = FakePhaseFilter("Show Complete", 4100)
    doc.phase_filters.append(original_pf)
    doc.register(original_pf)

    view = FakeView(view_id)
    view.params[FakeBuiltInParameter.VIEW_PHASE_FILTER] = FakeParameter(
        original_pf.Id, read_only=phase_filter_read_only)
    return doc, view, pipeline_elements, phase_revealed, original_pf


def _cfg(tmp_path, **overrides):
    cfg = Config()
    cfg.include_linked_rvt = False
    cfg.debug_dump_path = str(tmp_path)
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def _painted_ids(view):
    """Element ids production actually painted, read off the view."""
    return sorted(view.element_overrides)


def _spy_recollection(monkeypatch, base_elements, phase_revealed=None):
    """A collector that HONOURS the view's phase filter at collection time.

    The earlier version returned whatever it was handed, unconditionally. That
    let a measurement taken without the neutral filter applied still "find" the
    phase-revealed element -- so an audit that could not possibly see
    demolished/temporary content in Revit reported that it had. Review caught
    it on PR #208; the fake was asserting the conclusion.

    Revit's view-scoped collector does not work that way: it respects the phase
    filter in force when it runs. This one does too, which is what makes the
    audit's measurement falsifiable.
    """
    calls = []

    def _fake_collect_view_elements(doc, view, raster, diag=None, cfg=None):
        param = view.params.get(FakeBuiltInParameter.VIEW_PHASE_FILTER)
        current = doc.GetElement(param.AsElementId()) if param is not None else None
        on_neutral = (
            getattr(current, "Name", None)
            == color_id_buffer.NEUTRAL_PHASE_FILTER_NAME)
        calls.append({"on_neutral_phase_filter": on_neutral})
        collected = list(base_elements)
        if on_neutral and phase_revealed is not None:
            collected.append(phase_revealed)
        return collected

    monkeypatch.setattr(
        revit_collection, "collect_view_elements", _fake_collect_view_elements)
    return calls


def _run(doc, view, elements, cfg, diag, raster):
    with install_fake_revit_db():
        return color_id_buffer.export_color_id_buffer_view(
            doc, view, elements=elements, cfg=cfg, diag=diag,
            raster=raster, elem_cache=None,
        )


# --- the gate ---------------------------------------------------------------


def test_default_config_does_not_swap_the_phase_filter(tmp_path, monkeypatch):
    doc, view, elements, revealed, original_pf = _make_world()
    calls = _spy_recollection(monkeypatch, elements, revealed)
    cfg = _cfg(tmp_path)
    assert cfg.color_id_neutral_phase_swap is False, "default must be OFF"
    diag = FakeDiag()

    result = _run(doc, view, elements, cfg, diag, _raster())

    pf_state = result["metadata"]["phase_filter_state"]
    assert pf_state["swap_requested"] is False
    assert pf_state["swapped"] is False
    assert pf_state["neutral_phase_filter_id"] is None
    assert pf_state["neutral_phase_filter_created"] is False
    assert "swap_skipped_reason" in pf_state

    # No neutral filter was ever created, and the view's own filter was never
    # written to -- not merely written and written back.
    assert view.params[FakeBuiltInParameter.VIEW_PHASE_FILTER].set_calls == []
    assert [pf.Name for pf in doc.phase_filters] == ["Show Complete"]
    assert doc.deleted_ids == []

    # And the re-collection was not run at all.
    assert calls == []


def test_gate_off_paints_the_pipelines_own_collection(tmp_path, monkeypatch):
    doc, view, elements, revealed, _pf = _make_world()
    _spy_recollection(monkeypatch, elements, revealed)
    diag = FakeDiag()

    _run(doc, view, elements, _cfg(tmp_path), diag, _raster())

    assert _painted_ids(view) == sorted(_PIPELINE_IDS)
    assert _PHASE_REVEALED_ID not in _painted_ids(view)


def test_swap_on_restores_previous_behaviour(tmp_path, monkeypatch):
    """Positive control: the flag genuinely re-enables the old path.

    Without this, every assertion above would also hold for a build that had
    deleted the swap instead of gating it -- which is not what was asked for.
    """
    doc, view, elements, revealed, original_pf = _make_world()
    calls = _spy_recollection(monkeypatch, elements, revealed)
    cfg = _cfg(tmp_path, color_id_neutral_phase_swap=True)
    diag = FakeDiag()

    result = _run(doc, view, elements, cfg, diag, _raster())

    pf_state = result["metadata"]["phase_filter_state"]
    assert pf_state["swap_requested"] is True
    assert pf_state["swapped"] is True
    assert pf_state["neutral_phase_filter_created"] is True
    assert pf_state["neutral_phase_filter_id"] is not None

    # The neutral filter was created, swapped in, and cleaned up afterwards.
    assert color_id_buffer.NEUTRAL_PHASE_FILTER_NAME in [
        pf.Name for pf in doc.phase_filters]
    assert doc.deleted_ids == [pf_state["neutral_phase_filter_id"]]

    # The re-collection ran and its result -- including the phase-revealed
    # element -- is what got painted.
    assert len(calls) == 1
    assert _painted_ids(view) == sorted(list(_PIPELINE_IDS) + [_PHASE_REVEALED_ID])


def test_read_only_phase_filter_parameter_is_still_not_swapped_when_gated_off(
    tmp_path, monkeypatch
):
    """A read-only VIEW_PHASE_FILTER used to produce a diagnostic about the
    swap failing. With the gate off there is no swap to fail, so that warning
    must not be emitted -- it would send a reader looking for a View Template
    problem that is not there."""
    doc, view, elements, revealed, _pf = _make_world(phase_filter_read_only=True)
    _spy_recollection(monkeypatch, elements, revealed)
    diag = FakeDiag()

    _run(doc, view, elements, _cfg(tmp_path), diag, _raster())

    assert not [w for w in diag.warnings if w.get("callsite") == "phase_filter_swap"]


# --- the audit --------------------------------------------------------------


def test_audit_is_not_applicable_by_default(tmp_path, monkeypatch):
    doc, view, elements, revealed, _pf = _make_world()
    calls = _spy_recollection(monkeypatch, elements, revealed)
    cfg = _cfg(tmp_path)
    assert cfg.color_id_phase_swap_audit is False

    result = _run(doc, view, elements, cfg, diag=FakeDiag(), raster=_raster())

    audit = result["metadata"]["phase_swap_element_set_audit"]
    assert audit["state"] == "not_applicable"
    assert "color_id_phase_swap_audit=False" in audit["reason"]
    # Not paid for: no re-collection ran.
    assert calls == []


def test_audit_reports_the_difference_without_changing_what_is_painted(
    tmp_path, monkeypatch
):
    doc, view, elements, revealed, _pf = _make_world()
    calls = _spy_recollection(monkeypatch, elements, revealed)
    cfg = _cfg(tmp_path, color_id_phase_swap_audit=True)
    diag = FakeDiag()

    result = _run(doc, view, elements, cfg, diag, _raster())

    # It ran the second collection purely to measure it, AND under the neutral
    # phase state -- the property the whole audit rests on. Asserting only the
    # resulting counts would pass against a measurement taken under the
    # authored filter that happened to be handed the right list.
    assert len(calls) == 1
    assert calls[0]["on_neutral_phase_filter"] is True
    audit = result["metadata"]["phase_swap_element_set_audit"]
    assert audit["state"] == "value"
    value = audit["value"]
    assert value["neutral_phase_swap_enabled"] is False
    assert value["element_set_used_for_paint"] == "pipeline_collection"
    assert value["pipeline_collection_count"] == len(_PIPELINE_IDS)
    assert value["in_transaction_recollection_count"] == len(_PIPELINE_IDS) + 1
    assert value["common_count"] == len(_PIPELINE_IDS)
    assert value["only_in_recollection_ids"] == [_PHASE_REVEALED_ID]
    assert value["only_in_pipeline_collection_ids"] == []
    assert value["only_in_recollection_ids_truncated"] is False
    assert value["measured_under"] == "neutral_phase_filter"

    # ... and discarded it: the paint set is unchanged by the audit.
    assert _painted_ids(view) == sorted(_PIPELINE_IDS)

    # The difference is reported, not only written to the sidecar.
    reported = [n for n in diag.infos
                if n.get("callsite") == "phase_swap_element_set_audit"]
    assert len(reported) == 1
    assert "only-in-re-collection=1" in reported[0]["message"]


def test_audit_reverts_the_measurement_swap_before_anything_is_painted(tmp_path, monkeypatch):
    """The measurement borrows the view's phase filter; it must give it back.

    Everything after the audit paints and exports under whatever filter is in
    force, so a measurement swap left in place would export a phase state the
    caller never asked for -- with no colour assigned to the elements it
    reveals.
    """
    doc, view, elements, revealed, original_pf = _make_world()
    _spy_recollection(monkeypatch, elements, revealed)
    cfg = _cfg(tmp_path, color_id_phase_swap_audit=True)

    captured = {}

    def _on_export_image(_opts):
        param = view.params[FakeBuiltInParameter.VIEW_PHASE_FILTER]
        captured["phase_filter_at_export"] = param.AsElementId().IntegerValue

    doc.on_export_image = _on_export_image

    result = _run(doc, view, elements, cfg, FakeDiag(), _raster())

    # Back on the view's own filter by export time ...
    assert captured["phase_filter_at_export"] == original_pf.Id.IntegerValue
    # ... and the neutral filter this run created was cleaned up.
    neutral_id = result["metadata"]["phase_filter_state"]["neutral_phase_filter_id"]
    assert doc.deleted_ids == [neutral_id]
    # The paint set is still the pipeline's -- measuring changed nothing.
    assert _painted_ids(view) == sorted(_PIPELINE_IDS)


def test_audit_refuses_to_measure_when_the_phase_filter_is_read_only(tmp_path, monkeypatch):
    """A View Template locking VIEW_PHASE_FILTER makes the measurement
    impossible. It must read as unavailable, never as a difference of zero --
    a zero here would be taken as "the swap changes nothing", which is the
    conclusion the audit exists to test."""
    doc, view, elements, revealed, _pf = _make_world(phase_filter_read_only=True)
    calls = _spy_recollection(monkeypatch, elements, revealed)
    cfg = _cfg(tmp_path, color_id_phase_swap_audit=True)
    diag = FakeDiag()

    result = _run(doc, view, elements, cfg, diag, _raster())

    audit = result["metadata"]["phase_swap_element_set_audit"]
    assert audit["state"] == "unavailable"
    assert "read-only" in audit["reason"]
    assert "value" not in audit
    # The collection may still have run, but it never saw the neutral state --
    # which is exactly why its diff is refused rather than reported.
    assert all(c["on_neutral_phase_filter"] is False for c in calls)
    assert any(w.get("callsite") == "phase_swap_audit_measurement"
               for w in diag.warnings)


def test_swap_on_but_read_only_also_refuses_the_measurement(tmp_path, monkeypatch):
    """Same refusal from the other direction: the swap was requested, the
    parameter rejected it, so no collection in the capture saw the neutral
    state."""
    doc, view, elements, revealed, _pf = _make_world(phase_filter_read_only=True)
    _spy_recollection(monkeypatch, elements, revealed)
    cfg = _cfg(tmp_path, color_id_neutral_phase_swap=True,
               color_id_phase_swap_audit=True)

    result = _run(doc, view, elements, cfg, FakeDiag(), _raster())

    assert result["metadata"]["phase_filter_state"]["swapped"] is False
    audit = result["metadata"]["phase_swap_element_set_audit"]
    assert audit["state"] == "unavailable"
    assert "value" not in audit


def test_audit_with_the_swap_on_measures_under_the_swap_already_applied(tmp_path, monkeypatch):
    """No second, redundant swap when the main one is already in force."""
    doc, view, elements, revealed, _pf = _make_world()
    calls = _spy_recollection(monkeypatch, elements, revealed)
    cfg = _cfg(tmp_path, color_id_neutral_phase_swap=True,
               color_id_phase_swap_audit=True)

    result = _run(doc, view, elements, cfg, FakeDiag(), _raster())

    assert len(calls) == 1
    assert calls[0]["on_neutral_phase_filter"] is True
    audit = result["metadata"]["phase_swap_element_set_audit"]
    assert audit["state"] == "value"
    assert audit["value"]["element_set_used_for_paint"] == "in_transaction_recollection"
    # Swap-on paints the re-collected set, phase-revealed element included.
    assert _painted_ids(view) == sorted(list(_PIPELINE_IDS) + [_PHASE_REVEALED_ID])


def test_audit_records_a_failed_recollection_as_unavailable_not_as_no_difference(
    tmp_path, monkeypatch
):
    doc, view, elements, _revealed, _pf = _make_world()

    def _boom(*args, **kwargs):
        raise RuntimeError("collector exploded")

    monkeypatch.setattr(revit_collection, "collect_view_elements", _boom)
    cfg = _cfg(tmp_path, color_id_phase_swap_audit=True)

    result = _run(doc, view, elements, cfg, diag=FakeDiag(), raster=_raster())

    audit = result["metadata"]["phase_swap_element_set_audit"]
    assert audit["state"] == "unavailable"
    assert "RuntimeError" in audit["reason"]
    # A failed measurement must never read as "the two sets agreed".
    assert "value" not in audit


def test_audit_without_a_raster_is_unavailable_not_a_zero_difference(tmp_path):
    doc, view, elements, _revealed, _pf = _make_world()
    cfg = _cfg(tmp_path, color_id_phase_swap_audit=True)

    result = _run(doc, view, elements, cfg, diag=FakeDiag(), raster=None)

    audit = result["metadata"]["phase_swap_element_set_audit"]
    assert audit["state"] == "unavailable"
    assert "raster" in audit["reason"]


# --- harness control --------------------------------------------------------


def test_fake_surface_reaches_the_crop_path(tmp_path, monkeypatch):
    """Reachability pin.

    ``crop_box_from_uv_bounds()`` needs XYZ/BoundingBoxXYZ. A fake without them
    raises ImportError, production catches it, and every test in this file
    would silently run the no-crop branch -- the exact failure CLAUDE.md
    records. A written "bounds_xy" and no crop_box_set warning is the proof
    that did not happen here.
    """
    doc, view, elements, revealed, _pf = _make_world()
    _spy_recollection(monkeypatch, elements, revealed)
    diag = FakeDiag()

    result = _run(doc, view, elements, _cfg(tmp_path), diag, _raster())

    assert result["metadata"]["bounds_xy"] == [0.0, 0.0, 64.0, 48.0]
    assert not [w for w in diag.warnings if w.get("callsite") == "crop_box_set"]
