"""A view-specific DWG is annotation; a model-placed DWG is model.

Greg's rule (2026-09-29). On run pipeline_0928_0953 Plan_DWG's "in current
view only" import (19296946) was in BOTH sidecars' color_assignment_map and
drew in both images: the annotation pass claims it by OwnerViewId, and the
model pass painted it too. These drive the real model pass
(export_color_id_buffer_view) through the shared fake Revit and assert on the
sidecar FILE (CLAUDE.md defect class 4), with a model-placed import beside the
view-specific one as the control in every scenario.
"""
import json

import vop_interwoven.color_id_buffer as color_id_buffer
import vop_interwoven.revit.collection as revit_collection
from vop_interwoven.revit.collection_policy import view_specific_import_state

from tests.stage_a_capture_fakes import FakeCategory, FakeDiag, FakeElement
from tests.test_color_id_buffer_view_graphics_state import (
    _cfg, _export, _furnished_world, _raster)

VIEW_DWG, MODEL_DWG = 5001, 5002


class ImportInstance(FakeElement):
    """Named as the Revit type is: production matches it by type NAME."""

    def __init__(self, elem_id, view_specific, owner_view_id=None):
        FakeElement.__init__(self, elem_id, FakeCategory("site.dwg", 70),
                             owner_view_id=owner_view_id)
        self._view_specific = view_specific

    @property
    def ViewSpecific(self):
        if isinstance(self._view_specific, Exception):
            raise self._view_specific
        return self._view_specific


def _raise_is_hidden(self, view):
    raise RuntimeError("IsHidden unavailable")


# An ImportInstance whose IsHidden raises -- still NAMED ImportInstance, or
# production (which matches the type name) would not see an import at all.
_Unreadable = type("ImportInstance", (ImportInstance,), {"IsHidden": _raise_is_hidden})


def _world(view_dwg=None):
    doc, view, elements = _furnished_world()
    imports = [view_dwg or ImportInstance(VIEW_DWG, True, owner_view_id=42),
               ImportInstance(MODEL_DWG, False)]
    for imp in imports:
        doc.register(imp)
    return doc, view, elements, imports


def _run(tmp_path, monkeypatch, doc, view, elements, imports, diag=None,
         at_export=None, results=None):
    def _expand(doc_, view_, elems, cfg, diag=None, elem_cache=None,
                dwg_omitted_out=None):
        return ([{"element": e, "source_type": "HOST"} for e in elems]
                + [{"element": i, "source_type": "DWG"} for i in imports])
    monkeypatch.setattr(revit_collection, "expand_host_link_import_model_elements",
                        _expand)
    hidden_at_export = []

    def _on_export(_opts):
        hidden_at_export.append(set(view.hidden_elements))
        if at_export is not None:
            at_export()
    doc.on_export_image = _on_export
    result = _export(doc, view, elements, _cfg(tmp_path), diag or FakeDiag(), _raster())
    if results is not None:
        results.append(result)
    with open(result["sidecar_path"]) as handle:
        return json.load(handle), hidden_at_export


def _record(sidecar, elem_id):
    [record] = [r for r in sidecar["view_specific_imports"]
                if r["element_id"] == elem_id]
    return record


# ---- the one predicate ------------------------------------------------------

def test_the_predicate_is_three_valued():
    assert view_specific_import_state(ImportInstance(1, True)) == {
        "state": "value", "value": True}
    assert view_specific_import_state(ImportInstance(1, False)) == {
        "state": "value", "value": False}
    assert view_specific_import_state(FakeElement(1))["state"] == "not_applicable"
    broken = view_specific_import_state(ImportInstance(1, RuntimeError("gone")))
    assert broken["state"] == "unavailable" and "RuntimeError" in broken["reason"]


# ---- the model pass ---------------------------------------------------------

def test_a_view_specific_import_is_not_painted_by_the_model_pass(tmp_path, monkeypatch):
    doc, view, elements, imports = _world()
    sidecar, _hidden = _run(tmp_path, monkeypatch, doc, view, elements, imports)
    assert str(VIEW_DWG) not in sidecar["color_assignment_map"]
    record = _record(sidecar, VIEW_DWG)
    assert record["classification"] == "annotation"
    assert record["in_model_paint_set"] is False


def test_a_model_placed_import_is_still_painted(tmp_path, monkeypatch):
    """The CONTROL: the rule removes the view-specific import, not imports."""
    doc, view, elements, imports = _world()
    sidecar, _hidden = _run(tmp_path, monkeypatch, doc, view, elements, imports)
    assert str(MODEL_DWG) in sidecar["color_assignment_map"]
    assert _record(sidecar, MODEL_DWG)["classification"] == "model"
    assert "1001" in sidecar["color_assignment_map"]


def test_it_is_hidden_for_the_export_and_shown_again_after(tmp_path, monkeypatch):
    """Out of the palette is not enough: unpainted, it would still draw."""
    doc, view, elements, imports = _world()
    sidecar, hidden_at_export = _run(tmp_path, monkeypatch, doc, view, elements, imports)
    assert hidden_at_export and all(VIEW_DWG in h for h in hidden_at_export)
    assert all(MODEL_DWG not in h for h in hidden_at_export)
    assert VIEW_DWG not in view.hidden_elements
    record = _record(sidecar, VIEW_DWG)
    assert record["hidden_by_capture"] is True
    assert record["hidden_before_capture"] == {"state": "value", "value": False}
    assert record["hidden_after_restore"] == {"state": "value", "value": False}
    assert record["restore"] == "restored"


def test_an_import_already_hidden_stays_hidden(tmp_path, monkeypatch):
    """Restore undoes what the capture did, not what the author did."""
    doc, view, elements, imports = _world()
    view.hidden_elements.add(VIEW_DWG)
    sidecar, _hidden = _run(tmp_path, monkeypatch, doc, view, elements, imports)
    assert VIEW_DWG in view.hidden_elements
    record = _record(sidecar, VIEW_DWG)
    assert record["hidden_by_capture"] is False
    assert record["restore"] == "not_touched"


def _readback_errors(diag):
    return [e for e in diag.errors
            if e.get("callsite") == "restore_view_specific_imports_readback"]


def test_a_failed_unhide_is_read_back_as_not_restored(tmp_path, monkeypatch):
    """And reported to Diagnostics: the view is left modified."""
    diag = FakeDiag()
    doc, view, elements, imports = _world()

    def _refuse(_ids):
        raise RuntimeError("UnhideElements refused")
    view.UnhideElements = _refuse
    sidecar, _hidden = _run(tmp_path, monkeypatch, doc, view, elements, imports,
                            diag=diag)
    assert _record(sidecar, VIEW_DWG)["restore"] == "not_restored"
    [error] = _readback_errors(diag)
    assert error["elem_id"] == VIEW_DWG and error["view_id"] == 42


def test_an_unreadable_read_back_is_unverified_and_reported(tmp_path, monkeypatch):
    """The hidden state reads before the export and fails after it."""
    diag = FakeDiag()
    doc, view, elements, imports = _world()
    view_dwg = imports[0]

    def _break_is_hidden():
        def _raise(_view):
            raise RuntimeError("IsHidden gone")
        view_dwg.IsHidden = _raise
    sidecar, _hidden = _run(tmp_path, monkeypatch, doc, view, elements, imports,
                            diag=diag, at_export=_break_is_hidden)
    record = _record(sidecar, VIEW_DWG)
    assert record["restore"] == "unverified"
    assert record["hidden_after_restore"]["state"] == "unavailable"
    assert len(_readback_errors(diag)) == 1


def test_a_clean_restore_reports_nothing(tmp_path, monkeypatch):
    """The CONTROL for the two above."""
    diag = FakeDiag()
    doc, view, elements, imports = _world()
    sidecar, _hidden = _run(tmp_path, monkeypatch, doc, view, elements, imports,
                            diag=diag)
    assert _record(sidecar, VIEW_DWG)["restore"] == "restored"
    assert _readback_errors(diag) == []


def test_an_unreadable_view_specific_flag_keeps_the_import_in_the_model_pass(
        tmp_path, monkeypatch):
    """No content dropped on a failed read: painted as before, and said so."""
    diag = FakeDiag()
    doc, view, elements, imports = _world(
        view_dwg=ImportInstance(VIEW_DWG, RuntimeError("no ViewSpecific")))
    sidecar, hidden_at_export = _run(tmp_path, monkeypatch, doc, view, elements,
                                     imports, diag=diag)
    assert str(VIEW_DWG) in sidecar["color_assignment_map"]
    record = _record(sidecar, VIEW_DWG)
    assert record["classification"] == "unresolved"
    assert record["view_specific"]["state"] == "unavailable"
    assert all(VIEW_DWG not in h for h in hidden_at_export)
    assert any(w.get("callsite") == "view_specific_import_classification"
               for w in diag.warnings)


def test_an_unreadable_hidden_state_is_left_visible_not_guessed(tmp_path, monkeypatch):
    """Hiding what cannot be read back could not be undone exactly."""
    doc, view, elements, imports = _world(
        view_dwg=_Unreadable(VIEW_DWG, True, owner_view_id=42))
    sidecar, hidden_at_export = _run(tmp_path, monkeypatch, doc, view, elements, imports)
    record = _record(sidecar, VIEW_DWG)
    assert record["hidden_by_capture"] is False
    assert record["hidden_before_capture"]["state"] == "unavailable"
    assert "left_visible_reason" in record
    assert all(VIEW_DWG not in h for h in hidden_at_export)
    # Still annotation: out of the model palette whatever its visibility.
    assert str(VIEW_DWG) not in sidecar["color_assignment_map"]


def test_a_view_without_imports_records_an_empty_list(tmp_path, monkeypatch):
    doc, view, elements = _furnished_world()
    sidecar, _hidden = _run(tmp_path, monkeypatch, doc, view, elements, [])
    assert sidecar["view_specific_imports"] == []


# ---- review, PR #218: an annotation import that stays in the image fails ----

def test_an_import_that_cannot_be_hidden_fails_the_capture(tmp_path, monkeypatch):
    """Unpainted, its native pixels can cover model ids nothing can recover:
    a failed view, not a clean one with a note. The files are still written."""
    diag, results = FakeDiag(), []
    doc, view, elements, imports = _world(
        view_dwg=_Unreadable(VIEW_DWG, True, owner_view_id=42))
    sidecar, _hidden = _run(tmp_path, monkeypatch, doc, view, elements, imports,
                            diag=diag, results=results)
    [result] = results
    assert result["success"] is False
    assert result["failure_reason"] == "view_specific_import_not_suppressed"
    assert _record(sidecar, VIEW_DWG)["suppressed"] is False
    assert any(e.get("callsite") == "view_specific_import_suppression"
               for e in diag.errors)


def test_a_hide_that_raises_fails_the_capture(tmp_path, monkeypatch):
    results = []
    doc, view, elements, imports = _world()

    def _refuse(_ids):
        raise RuntimeError("HideElements refused")
    view.HideElements = _refuse
    sidecar, _hidden = _run(tmp_path, monkeypatch, doc, view, elements, imports,
                            results=results)
    assert results[0]["failure_reason"] == "view_specific_import_not_suppressed"
    assert "HideElements raised" in _record(sidecar, VIEW_DWG)["left_visible_reason"]


def test_a_suppressed_import_leaves_the_capture_successful(tmp_path, monkeypatch):
    """The CONTROL for both: hidden, or already hidden, is suppressed."""
    for already_hidden in (False, True):
        results = []
        doc, view, elements, imports = _world()
        if already_hidden:
            view.hidden_elements.add(VIEW_DWG)
        sidecar, _hidden = _run(tmp_path, monkeypatch, doc, view, elements, imports,
                                results=results)
        assert results[0]["success"] is True, already_hidden
        assert _record(sidecar, VIEW_DWG)["suppressed"] is True


def test_a_view_specific_import_the_paint_set_never_saw_is_still_hidden(
        tmp_path, monkeypatch):
    """include_dwg_imports off stops COLLECTION, not Revit drawing the import:
    the view's own imports are scanned, and the view-specific one hidden."""
    doc, view, elements, imports = _world()
    sidecar, hidden_at_export = _run(tmp_path, monkeypatch, doc, view, elements, [])
    record = _record(sidecar, VIEW_DWG)
    assert record["found_by"] == "view_scan"
    assert record["suppressed"] is True and record["restore"] == "restored"
    assert all(VIEW_DWG in h for h in hidden_at_export)
    assert VIEW_DWG not in view.hidden_elements


def test_the_scan_leaves_a_model_placed_import_to_the_dwg_setting(tmp_path, monkeypatch):
    """The CONTROL: a model-placed import the paint set did not collect is
    neither recorded nor hidden -- that is the setting's call, not this rule's."""
    doc, view, elements, imports = _world()
    sidecar, hidden_at_export = _run(tmp_path, monkeypatch, doc, view, elements, [])
    assert [r for r in sidecar["view_specific_imports"]
            if r["element_id"] == MODEL_DWG] == []
    assert all(MODEL_DWG not in h for h in hidden_at_export)


def test_a_failed_scan_fails_the_capture(tmp_path, monkeypatch):
    """Cannot enumerate the view's imports -> cannot say none is drawn."""
    import tests.stage_a_capture_fakes as fakes
    results = []
    doc, view, elements, imports = _world()
    original = fakes.FakeCollector.OfClass

    def _of_class(self, cls):
        if cls is fakes.FakeImportInstanceClass:
            raise RuntimeError("collector gone")
        return original(self, cls)
    monkeypatch.setattr(fakes.FakeCollector, "OfClass", _of_class)
    sidecar, _hidden = _run(tmp_path, monkeypatch, doc, view, elements, imports,
                            results=results)
    assert results[0]["failure_reason"] == "view_specific_import_not_suppressed"
    [sentinel] = [r for r in sidecar["view_specific_imports"] if r["element_id"] is None]
    assert sentinel["scan"]["state"] == "unavailable"


def test_an_unclassifiable_import_outside_the_paint_set_fails_the_capture(
        tmp_path, monkeypatch):
    """The scan finds an import the paint set never saw and cannot read its
    ViewSpecific: if it is view-specific it draws unpainted, so the capture
    cannot claim success (review, PR #218)."""
    results = []
    doc, view, elements, imports = _world(
        view_dwg=ImportInstance(VIEW_DWG, RuntimeError("no ViewSpecific")))
    sidecar, _hidden = _run(tmp_path, monkeypatch, doc, view, elements, [],
                            results=results)
    record = _record(sidecar, VIEW_DWG)
    assert record["classification"] == "unresolved"
    assert record["found_by"] == "view_scan"
    assert results[0]["failure_reason"] == "view_specific_import_not_suppressed"


def test_an_unclassifiable_import_in_the_paint_set_does_not_fail_it(
        tmp_path, monkeypatch):
    """The CONTROL: painted with its own palette colour, its pixels stay
    identifiable -- the behaviour before the rule, and no failure."""
    results = []
    doc, view, elements, imports = _world(
        view_dwg=ImportInstance(VIEW_DWG, RuntimeError("no ViewSpecific")))
    sidecar, _hidden = _run(tmp_path, monkeypatch, doc, view, elements, imports,
                            results=results)
    assert _record(sidecar, VIEW_DWG)["found_by"] == "paint_set"
    assert results[0]["success"] is True
