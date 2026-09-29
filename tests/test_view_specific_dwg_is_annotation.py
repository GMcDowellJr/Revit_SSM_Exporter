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


def _run(tmp_path, monkeypatch, doc, view, elements, imports, diag=None):
    def _expand(doc_, view_, elems, cfg, diag=None, elem_cache=None,
                dwg_omitted_out=None):
        return ([{"element": e, "source_type": "HOST"} for e in elems]
                + [{"element": i, "source_type": "DWG"} for i in imports])
    monkeypatch.setattr(revit_collection, "expand_host_link_import_model_elements",
                        _expand)
    hidden_at_export = []
    doc.on_export_image = lambda _opts: hidden_at_export.append(
        set(view.hidden_elements))
    result = _export(doc, view, elements, _cfg(tmp_path), diag or FakeDiag(), _raster())
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


def test_a_failed_unhide_is_read_back_as_not_restored(tmp_path, monkeypatch):
    doc, view, elements, imports = _world()

    def _refuse(_ids):
        raise RuntimeError("UnhideElements refused")
    view.UnhideElements = _refuse
    sidecar, _hidden = _run(tmp_path, monkeypatch, doc, view, elements, imports)
    assert _record(sidecar, VIEW_DWG)["restore"] == "not_restored"


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
    doc, view, elements, _imports = _world()
    sidecar, _hidden = _run(tmp_path, monkeypatch, doc, view, elements, [])
    assert sidecar["view_specific_imports"] == []
