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
``_model_categories_in_linked_doc`` also imports the REAL
vop_interwoven.revit.collection_policy module (it is designed to be
Revit-import-safe outside Revit), so these tests exercise the actual
project-wide category policy, not a stand-in for it.

They lock in:
  - RevitLinkInstance placements are deduped by underlying document
    identity (PathName) before category scanning -- one scan per UNIQUE
    document, not one per placement.
  - Category candidates go through the SAME authoritative LINK inclusion
    policy (collection_policy.should_include_element) the rest of the
    pipeline uses -- a category the policy excludes (Rooms, Areas, ...)
    never gets a filter, even if it is otherwise CategoryType.Model and
    Revit-filterable.
  - Filters are colored via SetFilterOverrides from the caller-supplied
    palette slice (no independent RNG), named per-view so a same-named
    filter can only ever be this same view's own Stage A leftover, never
    a real user filter -- reusing one still force-enables AND
    force-visibles it (both independently gate whether it renders).
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

    def __init__(self, doc, name, cat_id_list, element_filter=None):
        self.Name = name
        self.Id = _FakeElementId(_FakeParameterFilterElement._next_id)
        _FakeParameterFilterElement._next_id += 1
        self.category_ids = list(cat_id_list)
        self._element_filter = element_filter
        doc.parameter_filters.append(self)

    def GetCategories(self):
        return list(self.category_ids)

    def GetElementFilter(self):
        return self._element_filter

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
        self.visibility_calls = []
        self.filter_override_calls = []
        self.element_override_calls = []

    def IsFilterApplied(self, filter_id):
        return filter_id.IntegerValue in self._applied

    def AddFilter(self, filter_id):
        self._applied.add(filter_id.IntegerValue)
        self.add_filter_calls.append(filter_id)

    def SetIsFilterEnabled(self, filter_id, enabled):
        self.enable_calls.append((filter_id, enabled))

    def SetFilterVisibility(self, filter_id, visible):
        self.visibility_calls.append((filter_id, visible))

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
def _install_fake_revit_db(filterable_category_ids=()):
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


# --- _collect_link_category_filters: dedup + category union + policy -------
#
# Colorability is supplied by the caller as an ``is_colorable(category)``
# predicate (production resolves it once per capture via
# _resolve_colorable_category_predicate). These tests pass an explicit name
# whitelist, mirroring the frozen VETTED_COLORABLE_CATEGORY_NAMES.

def _whitelist(*names):
    allowed = set(names)
    return lambda cat: getattr(cat, "Name", None) in allowed


def test_collect_link_category_filters_dedupes_and_applies_authoritative_policy():
    cat_walls = _FakeCategory("Walls", 10)
    cat_floors = _FakeCategory("Floors", 11)
    cat_anno = _FakeCategory("Tags", 12, cat_type=_FakeCategoryType.Annotation)
    # "Rooms" is CategoryType.Model and (deliberately, in this test) on the
    # colorable whitelist, but the real collection_policy.py excludes it by
    # name -- this is the exact scenario the LINK-policy review finding was
    # about: a non-physical category must never get a filter just because it
    # is colorable and CategoryType.Model.
    cat_rooms = _FakeCategory("Rooms", 14)
    doc_a = _FakeLinkedDoc("Z:\\typical_exam_room.rvt", [
        _FakeElement(cat_walls),
        _FakeElement(cat_floors),
        _FakeElement(cat_anno),   # excluded: not CategoryType.Model
        _FakeElement(cat_rooms),  # excluded: collection_policy excludes "Rooms" by name
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

    with _install_fake_revit_db():
        colorable, uncolorable = color_id_buffer._collect_link_category_filters(
            view_doc, fake_view,
            _whitelist("Walls", "Floors", "Tags", "Doors", "Rooms"),
            diag=diag, view_id=1,
        )

    assert doc_a.scan_count == 1, (
        "doc_a was placed twice in the view but must be scanned once -- "
        "dedup by PathName is the whole point of this port"
    )
    assert doc_b.scan_count == 1

    names = [cat.Name for cat in colorable]
    assert names == ["Doors", "Floors", "Walls"], (
        "expected only policy-included, whitelisted Model categories, sorted by name -- "
        "Rooms must be excluded despite being CategoryType.Model and whitelisted"
    )
    assert uncolorable == [], (
        "Tags (Annotation) and Rooms fail the POLICY check, landing in neither list -- "
        "only a policy-included-but-unwhitelisted category becomes 'uncolorable'"
    )

    assert any(w["callsite"] == "link_category_filter_discovery" for w in diag.warnings)
    assert "unloaded link" in diag.warnings[0]["message"]


def test_collect_link_category_filters_splits_out_policy_included_but_unwhitelisted_categories():
    """A category the policy would include but the vetted whitelist does not
    cover must come back as "uncolorable", not silently dropped. Nothing
    suppresses it at capture time -- it renders with an uncontrolled native
    color, a known accepted gap -- so reporting it is the whole job: a
    whitelist that quietly lost an entry would otherwise look identical to one
    that never needed it."""
    cat_walls = _FakeCategory("Walls", 10)
    cat_odd = _FakeCategory("OddModelCategory", 15)  # Model, policy-included, NOT whitelisted
    doc_a = _FakeLinkedDoc("Z:\\typical_exam_room.rvt", [
        _FakeElement(cat_walls),
        _FakeElement(cat_odd),
    ])
    inst_a = _FakeLinkInstance("exam room", doc_a)
    view_doc = types.SimpleNamespace(link_instances=[inst_a], parameter_filters=[])
    fake_view = types.SimpleNamespace(Id=_FakeElementId(1))

    with _install_fake_revit_db():
        colorable, uncolorable = color_id_buffer._collect_link_category_filters(
            view_doc, fake_view, _whitelist("Walls"),
        )

    assert [cat.Name for cat in colorable] == ["Walls"]
    assert [cat.Name for cat in uncolorable] == ["OddModelCategory"]


def test_collect_link_category_filters_returns_empty_when_no_links_in_view():
    view_doc = types.SimpleNamespace(link_instances=[], parameter_filters=[])
    fake_view = types.SimpleNamespace(Id=_FakeElementId(1))
    with _install_fake_revit_db():
        colorable, uncolorable = color_id_buffer._collect_link_category_filters(
            view_doc, fake_view, _whitelist("Walls"),
        )
    assert colorable == []
    assert uncolorable == []


# --- _apply_link_category_filters: create/reuse + color + enable/visible ---

def test_apply_link_category_filters_creates_view_scoped_filter_and_sets_color():
    cat_walls = _FakeCategory("Walls", 10)
    doc = types.SimpleNamespace(parameter_filters=[])
    view = _FakeView()

    with _install_fake_revit_db({10}):
        link_category_color_map, created_ids, reused_ids, failed_cats = color_id_buffer._apply_link_category_filters(
            doc, view, 1234, [(cat_walls, (10, 20, 30))], solid_pattern_id=object()
        )

    assert link_category_color_map == {"Walls": [10, 20, 30]}
    assert len(doc.parameter_filters) == 1
    pfe = doc.parameter_filters[0]
    assert pfe.Name == "VOP_Color_1234_Walls", (
        "filter name must be scoped to this view's id -- a stable shared name "
        "could collide with a filter the user applied to some other view"
    )
    assert created_ids == [pfe.Id.IntegerValue], "freshly created -- safe for restore to doc.Delete"
    assert reused_ids == []
    assert failed_cats == []
    assert view.add_filter_calls == [pfe.Id]
    assert (pfe.Id, True) in view.enable_calls
    assert (pfe.Id, True) in view.visibility_calls
    assert len(view.filter_override_calls) == 1
    _fid, ogs = view.filter_override_calls[0]
    assert ogs.calls["SetProjectionLineColor"][0] == _FakeColor(10, 20, 30)


def test_apply_link_category_filters_creates_filter_records_it_even_if_addfilter_then_raises():
    """A Create() that succeeds but a later AddFilter() that raises must not
    orphan the freshly created ParameterFilterElement: it has to land in
    created_filter_ids (tracked for restore's doc.Delete()) before AddFilter
    is even attempted, not only after the whole per-category block succeeds.
    The category itself must also come back in failed_categories so the
    caller hides it -- its LINK elements are left with no color at all, and
    rendering with their uncontrolled native color risks aliasing a HOST
    element's palette id in the decoder.
    """
    cat_walls = _FakeCategory("Walls", 10)
    doc = types.SimpleNamespace(parameter_filters=[])
    view = _FakeView()

    def _raising_add_filter(_filter_id):
        raise RuntimeError("view rejected this filter")
    view.AddFilter = _raising_add_filter

    diag = _FakeDiag()
    with _install_fake_revit_db({10}):
        link_category_color_map, created_ids, reused_ids, failed_cats = color_id_buffer._apply_link_category_filters(
            doc, view, 1234, [(cat_walls, (10, 20, 30))], solid_pattern_id=object(), diag=diag
        )

    assert len(doc.parameter_filters) == 1, "Create() itself succeeded"
    pfe = doc.parameter_filters[0]
    assert created_ids == [pfe.Id.IntegerValue], (
        "must be tracked for cleanup despite AddFilter failing afterward, or "
        "restore can never doc.Delete() it and it orphans permanently"
    )
    assert reused_ids == []
    assert failed_cats == [cat_walls], "caller must hide this category -- it has no color at all now"
    assert link_category_color_map == {}, "the category never got colored since AddFilter failed"
    assert diag.warnings, "the AddFilter failure must still be recorded"


def test_apply_link_category_filters_reuses_same_view_crash_leftover_without_deleting_it():
    """A filter named "VOP_Color_<view_id>_Walls" already existing and
    already applied to THIS view is, in practice, this same view's own
    Stage A run having left it behind after a crash before reaching
    restore. Reusing it, rather than failing on the Create() name
    collision, is correct -- it must still be force-enabled and
    force-visible -- but ParameterFilterElement objects are document-global,
    so restore may only RemoveFilter it from this view, never doc.Delete
    the shared definition (some other view/template could reference it).
    """
    cat_walls = _FakeCategory("Walls", 10)
    doc = types.SimpleNamespace(parameter_filters=[])
    with _install_fake_revit_db({10}):
        existing = _FakeParameterFilterElement(doc, "VOP_Color_1234_Walls", [cat_walls.Id])
        view = _FakeView(applied_ids=[existing.Id.IntegerValue])

        link_category_color_map, created_ids, reused_ids, failed_cats = color_id_buffer._apply_link_category_filters(
            doc, view, 1234, [(cat_walls, (40, 50, 60))], solid_pattern_id=object()
        )

    assert len(doc.parameter_filters) == 1, "must reuse, not recreate, the same-named filter"
    assert view.add_filter_calls == [], "already-applied filter does not need AddFilter again"
    assert created_ids == []
    assert failed_cats == []
    assert reused_ids == [existing.Id.IntegerValue], (
        "a reused filter is tracked separately -- restore may only RemoveFilter "
        "it from this view, never doc.Delete the document-global definition"
    )
    assert (existing.Id, True) in view.enable_calls, (
        "a reused filter must still be force-enabled: the suppress step disables "
        "every pre-existing filter unconditionally, reused or not"
    )
    assert (existing.Id, True) in view.visibility_calls, (
        "a reused filter must still be force-visible: filter visibility is a "
        "separate gate from enabled, and a crash leftover could have it false"
    )
    assert link_category_color_map == {"Walls": [40, 50, 60]}


def test_apply_link_category_filters_rejects_reused_filter_with_wrong_categories():
    """A same-named filter found in the document that doesn't actually
    filter on exactly cat's category (wrong category set here; a non-None
    element-parameter rule is the other disqualifying case) must not be
    silently trusted and recolored -- that would report the category as
    successfully colored while its LINK elements keep rendering with
    whatever native color they already had, the exact HOST-palette-ID-
    aliasing risk this whole mechanism exists to prevent.
    """
    cat_walls = _FakeCategory("Walls", 10)
    cat_floors = _FakeCategory("Floors", 11)
    doc = types.SimpleNamespace(parameter_filters=[])
    diag = _FakeDiag()
    with _install_fake_revit_db({10, 11}):
        # Same name Stage A would generate for Walls in view 1234, but its
        # actual category list is Floors -- a mismatched definition.
        mismatched = _FakeParameterFilterElement(doc, "VOP_Color_1234_Walls", [cat_floors.Id])
        view = _FakeView(applied_ids=[mismatched.Id.IntegerValue])

        link_category_color_map, created_ids, reused_ids, failed_cats = color_id_buffer._apply_link_category_filters(
            doc, view, 1234, [(cat_walls, (40, 50, 60))], solid_pattern_id=object(), diag=diag
        )

    assert link_category_color_map == {}, "must not report Walls as colored"
    assert created_ids == []
    assert reused_ids == [], "a rejected filter is neither reused nor tracked for cleanup"
    assert failed_cats == [cat_walls]
    assert view.filter_override_calls == [], "must never recolor a filter that doesn't match"
    assert diag.warnings


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
        link_category_color_map, _created_ids, _reused_ids, _failed_cats = color_id_buffer._apply_link_category_filters(
            doc, view, 1234, [(cat_walls, (10, 20, 30))], solid_pattern_id=object()
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


# --- LinkCollectionStatus / _collect_view_scoped_link_proxies --------------
#
# The hide-instance mechanism these once served
# (_instances_to_hide_for_uncolorable_categories and friends) is retired, and
# nothing replaced it: Stage A hides no link instances, so nothing decides
# whether to leave a placement visible on the strength of an absence any more.
# The status itself is still exercised because near-face-W consumes the same
# scan, and because the "a short list is indistinguishable from a complete
# one" hazard is a property of the collectors rather than of that one retired
# caller.


def test_link_collection_status_starts_complete_and_records_failures():
    from vop_interwoven.revit.linked_documents import LinkCollectionStatus

    status = LinkCollectionStatus()
    assert status.rvt_complete is True
    assert status.failures == []
    assert status.link_presence == set()

    status.mark_incomplete("_collect_from_revit_links.transform", "link 'core shell'")
    assert status.rvt_complete is False
    assert status.failures == [
        ("_collect_from_revit_links.transform", "link 'core shell'")
    ], "a False must carry WHAT was incomplete -- CLAUDE.md's no-silent-failure rule"


def test_collect_view_scoped_link_proxies_returns_status_marked_by_a_partial_scan():
    """collect_all_linked_elements swallows a failed link enumeration, a
    placement with no transform, and a mid-loop element error -- each comes
    back as a quietly SHORT list, not an exception. The status is what makes
    that visible; the proxy list alone cannot."""
    import vop_interwoven.revit.linked_documents as linked_documents

    def _partial(_doc, _view, _cfg, diag=None, status=None):
        # Mirrors the real collectors: log, mark, skip -- never raise.
        if status is not None:
            status.record_presence(10, 5001)
            status.mark_incomplete(
                "_collect_from_revit_links.per_link_instance", "boom on placement 2"
            )
        return [_FakeLinkProxyStub()]

    original = linked_documents.collect_all_linked_elements
    linked_documents.collect_all_linked_elements = _partial
    diag = _FakeDiag()
    try:
        proxies, status = color_id_buffer._collect_view_scoped_link_proxies(
            object(), object(), object(), diag=diag, view_id=1
        )
    finally:
        linked_documents.collect_all_linked_elements = original

    assert len(proxies) == 1, "the partial list is still returned for near-face-W's use"
    assert status.rvt_complete is False, "but it cannot prove any category absent"
    assert any("incomplete" in w["message"] for w in diag.warnings)


class _FakeLinkProxyStub(object):
    """Near-face-W consumes proxies; the hide decision no longer does."""
    source_type = "LINK"


def test_collect_view_scoped_link_proxies_marks_status_when_the_scan_raises():
    import vop_interwoven.revit.linked_documents as linked_documents

    def _boom(*_args, **_kwargs):
        raise RuntimeError("link doc unavailable")

    original = linked_documents.collect_all_linked_elements
    linked_documents.collect_all_linked_elements = _boom
    diag = _FakeDiag()
    try:
        proxies, status = color_id_buffer._collect_view_scoped_link_proxies(
            object(), object(), object(), diag=diag, view_id=1
        )
    finally:
        linked_documents.collect_all_linked_elements = original

    assert proxies == []
    assert status.rvt_complete is False
    assert any(w["callsite"] == "view_scoped_link_collect" for w in diag.warnings)


def test_collect_view_scoped_link_proxies_reports_empty_success_as_complete():
    import vop_interwoven.revit.linked_documents as linked_documents

    original = linked_documents.collect_all_linked_elements
    linked_documents.collect_all_linked_elements = lambda *_a, **_k: []  # no failures marked
    try:
        proxies, status = color_id_buffer._collect_view_scoped_link_proxies(
            object(), object(), object()
        )
    finally:
        linked_documents.collect_all_linked_elements = original

    assert proxies == []
    assert status.rvt_complete is True
    assert status.link_presence == set()
