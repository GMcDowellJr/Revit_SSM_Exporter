"""
Unit tests for VOP Stage A LINK category-filter coloring
(vop_interwoven/color_id_buffer.py's _collect_link_category_filters and
_apply_link_category_filters), ported from the tested standalone
filter-creation script per PHASE 1a.

These exercise the Revit-API-touching helpers directly by installing a fake
Autodesk.Revit.DB module into sys.modules -- the same technique
tests/dynamo/test_probe_stage_a_external_sources.py uses for Revit-adjacent
code that has no other way to run outside Revit (the real functions do
local ``from Autodesk.Revit.DB import ...`` inside their bodies, so nothing
short of a fake module in sys.modules can intercept those imports).

They lock in:
  - RevitLinkInstance placements are deduped by underlying document
    identity (PathName) before category scanning -- one scan per UNIQUE
    document, not one per placement.
  - Model, filterable categories across unique linked documents are
    unioned into one ParameterFilterElement per category, colored via
    SetFilterOverrides from the caller-supplied palette slice (no
    independent RNG).
  - An existing same-named filter is reused, not recreated, but is always
    force-re-enabled and re-colored (the initial suppress step disables
    every pre-existing view filter unconditionally, including one Stage A
    itself left applied on an earlier run).
  - LINK elements get no individual element-level override (only the
    filter); a HOST element in the same view still gets one via
    SetElementOverrides. That is the structural precondition for Revit's
    own documented override precedence (Instance/Element > Filter >
    Category) to make a HOST override win over a LINK's filter color --
    this suite cannot invoke Revit's renderer itself, so it locks in the
    code shape that precedence rule depends on, not the rendered pixels.
"""
import contextlib
import sys
import types

import vop_interwoven.color_id_buffer as color_id_buffer


# --- Fake Autodesk.Revit.DB surface -----------------------------------------

class _FakeElementId(object):
    def __init__(self, v):
        self.IntegerValue = int(v)

    def __eq__(self, other):
        return isinstance(other, _FakeElementId) and other.IntegerValue == self.IntegerValue

    def __hash__(self):
        return hash(self.IntegerValue)

    def __repr__(self):
        return "ElementId({0})".format(self.IntegerValue)


class _FakeCategoryType(object):
    Model = "Model"
    Annotation = "Annotation"


class _FakeCategory(object):
    def __init__(self, name, cat_id, cat_type=_FakeCategoryType.Model):
        self.Name = name
        self.Id = _FakeElementId(cat_id)
        self.CategoryType = cat_type


class _FakeElement(object):
    def __init__(self, category):
        self.Category = category


class _FakeLinkedDoc(object):
    def __init__(self, path_name, elements):
        self.PathName = path_name
        self.elements = elements
        self.scan_count = 0


class _FakeLinkInstance(object):
    def __init__(self, name, linked_doc):
        self.Name = name
        self._linked_doc = linked_doc

    def GetLinkDocument(self):
        return self._linked_doc


class _FakeOGS(object):
    """Records every SetXxx(...) call generically so tests can assert on
    whichever setter they care about without re-declaring all ten methods
    _build_flat_color_ogs() calls."""
    def __init__(self):
        self.calls = {}

    def __getattr__(self, name):
        def _record(*args):
            self.calls[name] = args
        return _record


class _FakeColor(object):
    def __init__(self, r, g, b):
        self.r, self.g, self.b = r, g, b

    def __eq__(self, other):
        return (self.r, self.g, self.b) == (other.r, other.g, other.b)


class _FakeParameterFilterElement(object):
    _next_id = 900

    def __init__(self, doc, name, cat_id_list):
        self.Name = name
        self.Id = _FakeElementId(_FakeParameterFilterElement._next_id)
        _FakeParameterFilterElement._next_id += 1
        self.category_ids = list(cat_id_list)
        doc.parameter_filters.append(self)

    @classmethod
    def Create(cls, doc, name, cat_id_list):
        return cls(doc, name, cat_id_list)


class _FakeGenericList(list):
    def Add(self, item):
        self.append(item)


class _FakeListFactory(object):
    def __getitem__(self, _item_type):
        return _FakeGenericList


class _FakeCollector(object):
    """Stands in for FilteredElementCollector(...); dispatches by the
    "source" object's own attributes so the same fake class covers all
    three real call shapes:
      FilteredElementCollector(doc, view.Id).OfClass(RevitLinkInstance)
      FilteredElementCollector(linked_doc).WhereElementIsNotElementType()
      FilteredElementCollector(doc).OfClass(ParameterFilterElement)
    """
    def __init__(self, source, _view_id=None):
        self._source = source

    def OfClass(self, cls):
        if cls is _FakeLinkInstanceMarker:
            return list(self._source.link_instances)
        if cls is _FakeParameterFilterElement:
            return list(self._source.parameter_filters)
        raise AssertionError("unexpected OfClass(%r)" % (cls,))

    def WhereElementIsNotElementType(self):
        self._source.scan_count += 1
        return list(self._source.elements)


class _FakeLinkInstanceMarker(object):
    """Sentinel standing in for the real RevitLinkInstance class -- only
    ever used as an OfClass() argument/identity check, never instantiated."""
    pass


class _FakeParameterFilterUtilities(object):
    def __init__(self, filterable_ids):
        self._filterable_ids = filterable_ids

    def GetAllFilterableCategories(self):
        return [_FakeElementId(v) for v in self._filterable_ids]


class _FakeView(object):
    def __init__(self, applied_ids=()):
        self._applied = set(applied_ids)
        self.add_filter_calls = []
        self.enable_calls = []
        self.filter_override_calls = []
        self.element_override_calls = []

    def IsFilterApplied(self, filter_id):
        return filter_id.IntegerValue in self._applied

    def AddFilter(self, filter_id):
        self._applied.add(filter_id.IntegerValue)
        self.add_filter_calls.append(filter_id)

    def SetIsFilterEnabled(self, filter_id, enabled):
        self.enable_calls.append((filter_id, enabled))

    def SetFilterOverrides(self, filter_id, ogs):
        self.filter_override_calls.append((filter_id, ogs))

    def SetElementOverrides(self, elem_id, ogs):
        self.element_override_calls.append((elem_id, ogs))


class _FakeDiag(object):
    def __init__(self):
        self.warnings = []

    def warn(self, **kwargs):
        self.warnings.append(kwargs)


@contextlib.contextmanager
def _install_fake_revit_db(filterable_category_ids):
    fake_db = types.ModuleType("Autodesk.Revit.DB")
    fake_db.FilteredElementCollector = _FakeCollector
    fake_db.RevitLinkInstance = _FakeLinkInstanceMarker
    fake_db.ParameterFilterUtilities = _FakeParameterFilterUtilities(filterable_category_ids)
    fake_db.CategoryType = _FakeCategoryType
    fake_db.ParameterFilterElement = _FakeParameterFilterElement
    fake_db.ElementId = _FakeElementId
    fake_db.Color = _FakeColor
    fake_db.OverrideGraphicSettings = _FakeOGS

    fake_system = types.ModuleType("System")
    fake_system_collections = types.ModuleType("System.Collections")
    fake_scg = types.ModuleType("System.Collections.Generic")
    fake_scg.List = _FakeListFactory()

    module_names = (
        "Autodesk.Revit.DB", "System", "System.Collections", "System.Collections.Generic",
    )
    fakes = (fake_db, fake_system, fake_system_collections, fake_scg)
    originals = {name: sys.modules.get(name) for name in module_names}
    for name, fake in zip(module_names, fakes):
        sys.modules[name] = fake
    try:
        yield fake_db
    finally:
        for name in module_names:
            orig = originals[name]
            if orig is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = orig


# --- _collect_link_category_filters: dedup + category union ----------------

def test_collect_link_category_filters_dedupes_placements_by_document_identity():
    cat_walls = _FakeCategory("Walls", 10)
    cat_floors = _FakeCategory("Floors", 11)
    cat_anno = _FakeCategory("Tags", 12, cat_type=_FakeCategoryType.Annotation)
    doc_a = _FakeLinkedDoc("Z:\\typical_exam_room.rvt", [
        _FakeElement(cat_walls),
        _FakeElement(cat_floors),
        _FakeElement(cat_anno),   # excluded: not CategoryType.Model
        _FakeElement(None),       # excluded: no category
    ])
    cat_doors = _FakeCategory("Doors", 13)
    doc_b = _FakeLinkedDoc("Z:\\core_shell.rvt", [_FakeElement(cat_doors)])

    inst_a1 = _FakeLinkInstance("exam room 1", doc_a)
    inst_a2 = _FakeLinkInstance("exam room 2", doc_a)  # same underlying document
    inst_b = _FakeLinkInstance("core shell", doc_b)
    inst_unresolved = _FakeLinkInstance("unloaded link", None)

    view_doc = types.SimpleNamespace(
        link_instances=[inst_a1, inst_a2, inst_b, inst_unresolved],
        parameter_filters=[],
    )
    fake_view = types.SimpleNamespace(Id=_FakeElementId(1))
    diag = _FakeDiag()

    with _install_fake_revit_db({10, 11, 12, 13}):
        categories = color_id_buffer._collect_link_category_filters(
            view_doc, fake_view, diag=diag, view_id=1
        )

    assert doc_a.scan_count == 1, (
        "doc_a was placed twice in the view but must be scanned once -- "
        "dedup by PathName is the whole point of this port"
    )
    assert doc_b.scan_count == 1

    names = [cat.Name for cat in categories]
    assert names == ["Doors", "Floors", "Walls"], "expected Model categories only, sorted by name"

    assert any(w["callsite"] == "link_category_filter_discovery" for w in diag.warnings)
    assert "unloaded link" in diag.warnings[0]["message"]


def test_collect_link_category_filters_returns_empty_when_no_links_in_view():
    view_doc = types.SimpleNamespace(link_instances=[], parameter_filters=[])
    fake_view = types.SimpleNamespace(Id=_FakeElementId(1))
    with _install_fake_revit_db({10}):
        categories = color_id_buffer._collect_link_category_filters(view_doc, fake_view)
    assert categories == []


# --- _apply_link_category_filters: create/reuse + color assignment ---------

def test_apply_link_category_filters_creates_filter_and_sets_override_color():
    cat_walls = _FakeCategory("Walls", 10)
    doc = types.SimpleNamespace(parameter_filters=[])
    view = _FakeView()

    with _install_fake_revit_db({10}):
        link_category_color_map, newly_applied = color_id_buffer._apply_link_category_filters(
            doc, view, [(cat_walls, (10, 20, 30))], solid_pattern_id=object()
        )

    assert link_category_color_map == {"Walls": [10, 20, 30]}
    assert len(doc.parameter_filters) == 1
    pfe = doc.parameter_filters[0]
    assert pfe.Name == "VOP_Color_Walls"
    assert newly_applied == [pfe.Id.IntegerValue]
    assert view.add_filter_calls == [pfe.Id]
    assert (pfe.Id, True) in view.enable_calls
    assert len(view.filter_override_calls) == 1
    _fid, ogs = view.filter_override_calls[0]
    assert ogs.calls["SetProjectionLineColor"][0] == _FakeColor(10, 20, 30)


def test_apply_link_category_filters_reuses_existing_filter_but_force_reenables_it():
    cat_walls = _FakeCategory("Walls", 10)
    doc = types.SimpleNamespace(parameter_filters=[])
    with _install_fake_revit_db({10}):
        existing = _FakeParameterFilterElement(doc, "VOP_Color_Walls", [cat_walls.Id])
        # Already applied to the view (e.g. left over from an earlier Stage A
        # run) but disabled by this run's unconditional pre-suppress loop --
        # mirrors export_color_id_buffer_view's filter_state disable step.
        view = _FakeView(applied_ids=[existing.Id.IntegerValue])

        link_category_color_map, newly_applied = color_id_buffer._apply_link_category_filters(
            doc, view, [(cat_walls, (40, 50, 60))], solid_pattern_id=object()
        )

    assert len(doc.parameter_filters) == 1, "must reuse, not recreate, the same-named filter"
    assert newly_applied == [], "already-applied filter is not newly applied"
    assert view.add_filter_calls == []
    assert (existing.Id, True) in view.enable_calls, (
        "a reused filter must still be force-enabled: the suppress step disables "
        "every pre-existing filter unconditionally, reused or not"
    )
    assert link_category_color_map == {"Walls": [40, 50, 60]}


# --- Structural precedence: HOST gets a per-element override, LINK relies --
# --- solely on the category filter -----------------------------------------

def test_host_element_override_and_link_category_filter_are_independent_calls():
    """Mirrors export_color_id_buffer_view's actual call sequence: the
    category filter is applied first, then the HOST per-element paint loop
    runs on the same view. Revit's own documented override precedence
    (Instance/Element > Filter > Category) is what makes the HOST element's
    SetElementOverrides win over the category's SetFilterOverrides color --
    that resolution happens inside Revit's renderer and can't be invoked
    here, so this test locks in the necessary code shape instead: a LINK
    element never receives an individual override (only the shared filter
    color), while a HOST element in the very same category always does.
    """
    cat_walls = _FakeCategory("Walls", 10)
    doc = types.SimpleNamespace(parameter_filters=[])
    view = _FakeView()

    with _install_fake_revit_db({10}):
        link_category_color_map, _newly_applied = color_id_buffer._apply_link_category_filters(
            doc, view, [(cat_walls, (10, 20, 30))], solid_pattern_id=object()
        )

        host_eid = _FakeElementId(555)
        host_color = _FakeColor(70, 80, 90)
        host_ogs = color_id_buffer._build_flat_color_ogs(object(), host_color)
        view.SetElementOverrides(host_eid, host_ogs)

    assert link_category_color_map == {"Walls": [10, 20, 30]}
    assert len(view.filter_override_calls) == 1, "LINK color comes only from the category filter"
    assert len(view.element_override_calls) == 1, "HOST element still gets its own override"
    assert view.element_override_calls[0][0] is host_eid
    assert view.element_override_calls[0][1].calls["SetProjectionLineColor"][0] == host_color
