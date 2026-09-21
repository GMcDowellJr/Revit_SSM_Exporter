"""Stage A step 1: a DWG import the collector DROPS is recorded, not silent.

A dropped import paints no pixels, so it is correctly absent from the TIFF --
that is the locked "a DWG outside the crop is not in the TIFF and not
analyzed" decision. What must not happen is that "this drawing has no DWG"
and "the collector dropped one" look identical to whoever reads the sidecar
(Greg, 2026-09-21).

The distinction the record has to carry, and that these tests pin:

  []                      every import found was collected
  [{element_id: 2002}]    that import was found and dropped, with a reason
  [{element_id: None}]    nothing was examined at all -- the scan failed

An empty list is therefore a real answer, never a stand-in for "not measured";
that case arrives as an entry whose element_id is None.

These exercise revit/linked_documents._collect_from_dwg_imports directly. The
Revit API surface it needs is small enough to fake honestly: a collector that
yields the imports, and ImportInstance objects that answer get_BoundingBox.
"""
import sys
import types

import pytest


class _FakeElementId:
    def __init__(self, v):
        self.IntegerValue = int(v)

    def __str__(self):
        return str(self.IntegerValue)


class _P:
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = x, y, z


class _FakeBBox:
    def __init__(self, mn, mx):
        self.Min = _P(*mn) if mn is not None else None
        self.Max = _P(*mx) if mx is not None else None


class _FakeImportType:
    def __init__(self, name):
        self.Name = name


class _FakeImportInstance:
    def __init__(self, elem_id, view_bbox, view_specific=False, raises=None):
        self.Id = _FakeElementId(elem_id)
        self._view_bbox = view_bbox
        self.ViewSpecific = view_specific
        self._raises = raises

    def get_BoundingBox(self, view):
        if self._raises is not None:
            raise self._raises
        return self._view_bbox

    def GetTypeId(self):
        return _FakeElementId(9999)


class _FakeDoc:
    def GetElement(self, _eid):
        return _FakeImportType("site-plan.dwg")


class _FakeView:
    Id = _FakeElementId(42)


class _FakeCollector:
    """Stands in for FilteredElementCollector(doc, view.Id).OfClass(...)."""
    _payload = []
    _raises = None

    def __init__(self, _doc, _view_id):
        pass

    def OfClass(self, _cls):
        return self

    def ToElements(self):
        if _FakeCollector._raises is not None:
            raise _FakeCollector._raises
        return list(_FakeCollector._payload)


class _FakeDiag:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, **kwargs):
        self.errors.append(kwargs)

    def warn(self, **kwargs):
        self.warnings.append(kwargs)


@pytest.fixture
def fake_db(monkeypatch):
    fake = types.ModuleType("Autodesk.Revit.DB")
    fake.FilteredElementCollector = _FakeCollector
    fake.ImportInstance = _FakeImportInstance
    fake.XYZ = object
    fake.Transform = types.SimpleNamespace(Identity=object())
    monkeypatch.setitem(sys.modules, "Autodesk.Revit.DB", fake)
    return fake


def _collect(imports, omitted_out):
    from vop_interwoven.revit import linked_documents
    _FakeCollector._payload = imports
    try:
        return linked_documents._collect_from_dwg_imports(
            _FakeDoc(), _FakeView(), cfg=object(), omitted_out=omitted_out,
        )
    finally:
        _FakeCollector._payload = []


def test_collected_import_produces_no_omission(fake_db):
    """The control. Without it, every scenario below would also pass against a
    collector that dropped everything."""
    good = _FakeImportInstance(2002, _FakeBBox((0, 0, 0), (5, 5, 1)))
    omitted = []

    proxies = _collect([good], omitted)

    assert len(proxies) == 1
    assert proxies[0].source_type == "DWG"
    assert omitted == []


def test_import_with_no_view_bbox_is_omitted_with_a_reason(fake_db):
    bad = _FakeImportInstance(2002, None)
    omitted = []

    proxies = _collect([bad], omitted)

    assert proxies == []
    assert len(omitted) == 1
    assert omitted[0]["element_id"] == 2002
    assert omitted[0]["state"] == "unavailable"
    assert omitted[0]["reason"]


def test_import_with_a_none_bbox_corner_is_omitted(fake_db):
    """A bbox object that exists but has a None corner is just as unusable."""
    bad = _FakeImportInstance(2002, _FakeBBox(None, (5, 5, 1)))
    omitted = []

    assert _collect([bad], omitted) == []
    assert [o["element_id"] for o in omitted] == [2002]


def test_a_raising_import_is_omitted_with_its_exception(fake_db):
    boom = _FakeImportInstance(2002, None, raises=RuntimeError("geometry gone"))
    omitted = []

    assert _collect([boom], omitted) == []
    assert omitted[0]["element_id"] == 2002
    assert "RuntimeError" in omitted[0]["reason"]
    assert "geometry gone" in omitted[0]["reason"]


def test_one_bad_import_does_not_cost_the_good_ones(fake_db):
    good = _FakeImportInstance(2002, _FakeBBox((0, 0, 0), (5, 5, 1)))
    bad = _FakeImportInstance(3003, None)
    omitted = []

    proxies = _collect([bad, good], omitted)

    assert [p.Id.IntegerValue for p in proxies] == [2002]
    assert [o["element_id"] for o in omitted] == [3003]


def test_view_specific_import_is_collected_not_omitted(fake_db):
    """It used to be dropped here silently, and collection_policy excludes
    every ImportInstance from the HOST pass, so it reached no pass at all.
    It is now painted like any other import and tagged downstream."""
    vs = _FakeImportInstance(2002, _FakeBBox((0, 0, 0), (5, 5, 1)), view_specific=True)
    omitted = []

    proxies = _collect([vs], omitted)

    assert [p.Id.IntegerValue for p in proxies] == [2002]
    assert omitted == []


def test_omitted_out_is_optional(fake_db):
    """Every pre-existing call site passes nothing and must keep working."""
    from vop_interwoven.revit import linked_documents
    _FakeCollector._payload = [_FakeImportInstance(2002, None)]
    try:
        assert linked_documents._collect_from_dwg_imports(
            _FakeDoc(), _FakeView(), cfg=object(),
        ) == []
    finally:
        _FakeCollector._payload = []


def test_a_failed_scan_is_distinguishable_from_nothing_dropped(fake_db):
    """collect_all_linked_elements records an element_id-None entry when the
    DWG scan itself fails, so an empty list keeps meaning "nothing dropped"
    rather than collapsing with "nothing looked at"."""
    from vop_interwoven.revit import linked_documents

    def _boom(*_a, **_k):
        raise RuntimeError("collector unavailable")

    cfg = types.SimpleNamespace(include_linked_rvt=False, include_dwg_imports=True)
    omitted = []
    original = linked_documents._collect_from_dwg_imports
    linked_documents._collect_from_dwg_imports = _boom
    try:
        elements = linked_documents.collect_all_linked_elements(
            _FakeDoc(), _FakeView(), cfg, dwg_omitted_out=omitted,
        )
    finally:
        linked_documents._collect_from_dwg_imports = original

    assert elements == []
    assert len(omitted) == 1
    assert omitted[0]["element_id"] is None
    assert "RuntimeError" in omitted[0]["reason"]


# --- the enumeration failure that used to vanish ----------------------------
#
# When FilteredElementCollector construction or ToElements() raised,
# _collect_from_dwg_imports converted it to an empty result and returned. The
# caller's element_id-None sentinel is appended from ITS except block, which
# never fired because nothing propagated -- so the sidecar recorded
# `dwg_imports_omitted: []`, documented at the top of this file as "every
# import found was collected", over a scan that examined nothing at all. The
# failure also only ever reached _log, never Diagnostics (Refactor Rule #1).

def test_an_enumeration_failure_propagates_rather_than_reporting_empty(fake_db):
    """The inner handler must not convert the failure into [] -- that is
    indistinguishable from a view with no imports."""
    from vop_interwoven.revit import linked_documents
    _FakeCollector._raises = RuntimeError("collector exploded")
    try:
        with pytest.raises(RuntimeError):
            linked_documents._collect_from_dwg_imports(
                _FakeDoc(), _FakeView(), cfg=object(), omitted_out=[],
            )
    finally:
        _FakeCollector._raises = None


def test_an_enumeration_failure_reaches_the_sentinel_and_diagnostics(fake_db):
    """End-to-end through the real caller: a raising COLLECTOR (not a
    monkeypatched function) must produce the element_id-None sentinel AND a
    Diagnostics error, not an empty list and a print."""
    from vop_interwoven.revit import linked_documents
    cfg = types.SimpleNamespace(include_linked_rvt=False, include_dwg_imports=True)
    omitted, diag = [], _FakeDiag()

    _FakeCollector._raises = RuntimeError("collector exploded")
    try:
        elements = linked_documents.collect_all_linked_elements(
            _FakeDoc(), _FakeView(), cfg, diag=diag, dwg_omitted_out=omitted,
        )
    finally:
        _FakeCollector._raises = None

    assert elements == []
    assert len(omitted) == 1, (
        "an enumeration failure examined NO imports; an empty omission list "
        "would read as a successful scan"
    )
    assert omitted[0]["element_id"] is None
    assert "RuntimeError" in omitted[0]["reason"]
    assert "collector exploded" in omitted[0]["reason"]

    assert len(diag.errors) == 1, "Refactor Rule #1: the failure must be recorded"
    assert diag.errors[0]["callsite"] == "collect_all_linked_elements.dwg_imports"


def test_a_view_with_no_imports_is_still_an_empty_omission_list(fake_db):
    """The discrimination control. "No imports in this view" is a successful
    scan and must stay [] -- otherwise the fix above would just relabel every
    empty view as a failure."""
    from vop_interwoven.revit import linked_documents
    cfg = types.SimpleNamespace(include_linked_rvt=False, include_dwg_imports=True)
    omitted, diag = [], _FakeDiag()

    _FakeCollector._payload = []
    elements = linked_documents.collect_all_linked_elements(
        _FakeDoc(), _FakeView(), cfg, diag=diag, dwg_omitted_out=omitted,
    )

    assert elements == []
    assert omitted == []
    assert diag.errors == []
