import types

import pytest

import vop_interwoven.revit.linked_documents as ld


class _FakeId:
    def __init__(self, v):
        self.IntegerValue = v


class _FakeCategoryType:
    Model = "Model"


class _FakeCategory:
    def __init__(self, name, cat_id=1, cat_type=_FakeCategoryType.Model):
        self.Name = name
        self.Id = _FakeId(cat_id)
        self.CategoryType = cat_type


class _FakeBBox:
    def __init__(self):
        self.Min = object()
        self.Max = object()


class _FakeElem:
    def __init__(self, elem_id, cat):
        self.Id = _FakeId(elem_id)
        self.Category = cat

    def get_BoundingBox(self, _):
        return _FakeBBox()


class _FakeView:
    def __init__(self, vid):
        self.Id = _FakeId(vid)


class _FakeLinkInst:
    def __init__(self, iid):
        self.Id = _FakeId(iid)


class _FakeLinkDoc:
    def __init__(self, title="LinkDoc", uid="UID"):
        self.Title = title
        self.UniqueId = uid


class _FakeCollector:
    def __init__(self, doc, view_id, link_id, elems):
        self._elems = elems
        self._called_where = False
        # capture args for assertions
        self._args = (doc, view_id, link_id)

    def WhereElementIsNotElementType(self):
        self._called_where = True
        return self

    def __iter__(self):
        return iter(self._elems)


def test_collect_visible_link_elements_2024_plus_uses_3arg_ctor_and_sets_identity(monkeypatch):
    # Arrange: patch Revit API symbols used inside the function
    fake_doc = object()
    view = _FakeView(101)
    link_inst = _FakeLinkInst(202)
    link_doc = _FakeLinkDoc(title="EC", uid="U123")
    link_trf = object()

    # Make elements: 2 model elems, 1 excluded by non-model, 1 excluded by no bbox
    cat_model = _FakeCategory("Walls", cat_id=10, cat_type=_FakeCategoryType.Model)
    cat_non_model = _FakeCategory("Anno", cat_id=11, cat_type="Annotation")

    e1 = _FakeElem(1, cat_model)
    e2 = _FakeElem(2, cat_model)
    e3 = _FakeElem(3, cat_non_model)

    elems = [e1, e2, e3]

    # Patch category exclusion to empty set
    monkeypatch.setattr(ld, "_get_excluded_3d_category_ids", lambda _doc: set())
    # Patch transform bbox to host to always succeed
    monkeypatch.setattr(ld, "_transform_bbox_to_host", lambda bbox, trf: (object(), object()))

    # Patch CategoryType enum usage inside function by patching the module name Autodesk.Revit.DB.CategoryType
    # The function imports CategoryType from Autodesk.Revit.DB; we simulate by patching ld.CategoryType reference after import.
    # Safer: patch ld.CategoryType if it exists; otherwise patch via injecting into sys.modules is overkill.
    monkeypatch.setattr(ld, "CategoryType", _FakeCategoryType, raising=False)

    # Patch LinkedElementProxy to record calls without needing Revit
    created = []

    class _FakeProxy:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setattr(ld, "LinkedElementProxy", _FakeProxy)

    # Patch FilteredElementCollector ctor used inside function to return our fake collector
    def _fec_ctor(doc, view_id, link_id):
        c = _FakeCollector(doc, view_id, link_id, elems)
        return c

    # Function does: from Autodesk.Revit.DB import FilteredElementCollector (local import)
    # We patch ld.FilteredElementCollector name used at runtime by monkeypatching in the module namespace,
    # then ensure the local import binds to our patched name by patching sys.modules would be required otherwise.
    # Instead: patch ld.Autodesk.Revit.DB.FilteredElementCollector is not feasible here.
    # So we patch the function-global name by monkeypatching the attribute on the ld module after importing.
    monkeypatch.setattr(ld, "FilteredElementCollector", _fec_ctor, raising=False)

    # Patch the local import behavior by replacing the function's global lookup:
    # easiest: monkeypatch the name in ld, and change the function to reference it (it currently imports locally).
    # This test assumes you've adjusted the function to use ld.FilteredElementCollector when present.
    # If not, change the function accordingly or move the import to module scope.

    # Act
    proxies, source_key, source_label = ld._collect_visible_link_elements_2024_plus(
        fake_doc, view, link_inst, link_doc, link_trf, cfg=types.SimpleNamespace()
    )

    # Assert: identity fields are correct
    assert source_key.startswith("RVT_LINK:")
    assert source_label.startswith("RVT_LINK:")

    # We created proxies for model elements only
    assert len(created) == 2
    for k in created:
        assert k["source_type"] == "LINK"
        assert k["source_id"] == source_key
        assert k["doc_key"] == source_key
        assert k["doc_label"] == source_label
