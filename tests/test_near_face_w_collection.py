"""Unit tests for vop_interwoven/color_id_buffer.py's _collect_near_face_w_data
(Phase 1b: near-face-W + UV bbox footprint collection for the additive
"near_face_w_map" sidecar section).

Installs a minimal fake Autodesk.Revit.DB module into sys.modules -- same
technique tests/test_color_id_buffer_link_category_filters.py uses for
Revit-adjacent code with no other way to run outside Revit. The fake module
deliberately omits CategoryType/BuiltInCategory so
revit/collection_policy.should_include_element() takes its documented
"outside Revit" fallback path (int(cat.CategoryType) == 1 for
CategoryType.Model) instead of needing a full Revit API stub.
"""
import contextlib
import sys
import types

import vop_interwoven.color_id_buffer as color_id_buffer
from vop_interwoven.revit.view_basis import ViewBasis

CATEGORY_TYPE_MODEL = 1
CATEGORY_TYPE_ANNOTATION = 2


class _FakeElementId:
    def __init__(self, v):
        self.IntegerValue = int(v)

    def __eq__(self, other):
        return isinstance(other, _FakeElementId) and other.IntegerValue == self.IntegerValue

    def __hash__(self):
        return hash(self.IntegerValue)

    def __repr__(self):
        return "ElementId({0})".format(self.IntegerValue)


class _FakeCategory:
    def __init__(self, name, cat_id, cat_type=CATEGORY_TYPE_MODEL):
        self.Name = name
        self.Id = _FakeElementId(cat_id)
        self.CategoryType = cat_type


class _P:
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = x, y, z


class _FakeBBox:
    def __init__(self, mn, mx):
        self.Min = _P(*mn)
        self.Max = _P(*mx)


class _FakeElement:
    def __init__(self, elem_id, category, bbox):
        self.Id = _FakeElementId(elem_id)
        self.Category = category
        self._bbox = bbox

    def get_BoundingBox(self, _view):
        return self._bbox


class _FakeDoc:
    def __init__(self, elements):
        self._by_id = {e.Id.IntegerValue: e for e in elements}

    def GetElement(self, eid):
        return self._by_id.get(eid.IntegerValue)


class _FakeLinkedDoc:
    def __init__(self, elements):
        self.elements = elements


class _FakeTransform:
    """OfPoint offsets a plain tuple and returns a tuple -- the fast test-
    stub path project_bbox_corners_uv/estimate_nearest_depth_from_bbox try
    before falling back to a real Autodesk.Revit.DB.XYZ round-trip."""
    def __init__(self, dx=0, dy=0, dz=0):
        self._d = (dx, dy, dz)

    def OfPoint(self, pt):
        x, y, z = pt
        dx, dy, dz = self._d
        return (x + dx, y + dy, z + dz)


class _FakeIdentityTransform:
    pass


class _FakeLinkInstance:
    def __init__(self, inst_id, linked_doc, transform=None):
        self.Id = _FakeElementId(inst_id)
        self._linked_doc = linked_doc
        self._transform = transform or _FakeTransform()

    def GetLinkDocument(self):
        return self._linked_doc

    def GetTotalTransform(self):
        return self._transform


class _FakeCollector:
    """Stands in for FilteredElementCollector(linked_doc).OfCategoryId(cat.Id)
    .WhereElementIsNotElementType()."""
    def __init__(self, source):
        self._source = source
        self._cat_id_int = None

    def OfCategoryId(self, cat_id):
        self._cat_id_int = cat_id.IntegerValue
        return self

    def WhereElementIsNotElementType(self):
        elems = self._source.elements
        if self._cat_id_int is not None:
            elems = [
                e for e in elems
                if e.Category is not None and e.Category.Id.IntegerValue == self._cat_id_int
            ]
        return list(elems)


class _FakeDiag:
    def __init__(self):
        self.warnings = []
        self.errors = []

    def warn(self, **kwargs):
        self.warnings.append(kwargs)

    def error(self, **kwargs):
        self.errors.append(kwargs)


@contextlib.contextmanager
def _install_fake_revit_db():
    fake_db = types.ModuleType("Autodesk.Revit.DB")
    fake_db.FilteredElementCollector = _FakeCollector
    fake_db.Transform = types.SimpleNamespace(Identity=_FakeIdentityTransform())
    # Deliberately no CategoryType/BuiltInCategory -- should_include_element
    # must take its documented outside-Revit int-based fallback path.

    module_names = ("Autodesk.Revit.DB",)
    originals = {name: sys.modules.get(name) for name in module_names}
    sys.modules["Autodesk.Revit.DB"] = fake_db
    try:
        yield fake_db
    finally:
        for name in module_names:
            orig = originals[name]
            if orig is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = orig


# Plan view looking down +Z, basis aligned to world XY -- world (x, y) maps
# directly to view (u, v), matching the other Phase 1b test files' convention.
_PLAN_BASIS = ViewBasis(origin=(0, 0, 0), right=(1, 0, 0), up=(0, 1, 0), forward=(0, 0, -1))


def _raster():
    return types.SimpleNamespace(view_basis=_PLAN_BASIS)


def test_collects_host_and_link_entries_with_bbox_and_near_face_w():
    cat_walls = _FakeCategory("Walls", 10)
    host_elem = _FakeElement(1001, cat_walls, _FakeBBox((0, 0, 0), (2, 2, 2)))
    doc_elements = {1001: host_elem}

    class _Doc:
        def GetElement(self, eid):
            return doc_elements.get(eid.IntegerValue)

    link_elem_a = _FakeElement(501, cat_walls, _FakeBBox((10, 10, 0), (12, 12, 2)))
    link_elem_b = _FakeElement(502, cat_walls, _FakeBBox((20, 20, 0), (22, 22, 2)))
    linked_doc = _FakeLinkedDoc([link_elem_a, link_elem_b])
    link_inst = _FakeLinkInstance(9001, linked_doc)

    resolved_ids = [host_elem.Id]
    categories_with_colors = [(cat_walls, (10, 20, 30))]
    link_category_color_map = {"Walls": [10, 20, 30]}
    link_category_to_instances = {cat_walls.Id.IntegerValue: {link_inst}}
    instances_to_hide = set()
    diag = _FakeDiag()

    with _install_fake_revit_db():
        result = color_id_buffer._collect_near_face_w_data(
            _Doc(), view=object(), raster=_raster(), resolved_ids=resolved_ids,
            categories_with_colors=categories_with_colors,
            link_category_color_map=link_category_color_map,
            link_category_to_instances=link_category_to_instances,
            instances_to_hide=instances_to_hide, diag=diag, view_id=42,
        )

    assert set(result.keys()) == {"host", "link"}

    host_entry = result["host"]["1001"]
    assert host_entry["category"] == "Walls"
    assert host_entry["near_face_w"] is not None
    assert host_entry["bbox_corners_uv"] == [[0, 0], [2, 0], [2, 2], [0, 2]]

    assert set(result["link"].keys()) == {"9001:501", "9001:502"}
    link_a = result["link"]["9001:501"]
    assert link_a["link_inst_id"] == 9001
    assert link_a["link_elem_id"] == 501
    assert link_a["category"] == "Walls"
    assert link_a["near_face_w"] is not None
    assert link_a["bbox_corners_uv"] == [[10, 10], [12, 10], [12, 12], [10, 12]]

    link_b = result["link"]["9001:502"]
    assert link_b["bbox_corners_uv"] == [[20, 20], [22, 20], [22, 22], [20, 22]]

    assert not diag.errors


def test_host_element_with_no_bbox_still_gets_an_entry_recorded_as_none():
    cat_walls = _FakeCategory("Walls", 10)
    host_elem = _FakeElement(1001, cat_walls, None)  # get_BoundingBox returns None

    class _Doc:
        def GetElement(self, eid):
            return host_elem if eid.IntegerValue == 1001 else None

    diag = _FakeDiag()
    with _install_fake_revit_db():
        result = color_id_buffer._collect_near_face_w_data(
            _Doc(), view=object(), raster=_raster(), resolved_ids=[host_elem.Id],
            categories_with_colors=[], link_category_color_map={},
            link_category_to_instances={}, instances_to_hide=set(), diag=diag, view_id=1,
        )

    entry = result["host"]["1001"]
    assert entry == {"bbox_corners_uv": None, "near_face_w": None, "category": "Walls"}
    assert any(w["callsite"] == "near_face_w.host" for w in diag.warnings), (
        "a missing bbox must be recorded, not silently dropped -- CLAUDE.md's no-silent-failure rule"
    )


def test_link_category_whose_filter_failed_is_excluded_from_link_collection():
    """A category present in categories_with_colors but NOT in
    link_category_color_map means its filter application failed (see
    _apply_link_category_filters) -- no pixels of that color exist in the
    TIFF, so near_face_w collection must not manufacture identity data for
    elements that were never actually colorable."""
    cat_doors = _FakeCategory("Doors", 11)
    linked_doc = _FakeLinkedDoc([_FakeElement(701, cat_doors, _FakeBBox((0, 0, 0), (1, 1, 1)))])
    link_inst = _FakeLinkInstance(9002, linked_doc)

    with _install_fake_revit_db():
        result = color_id_buffer._collect_near_face_w_data(
            _FakeDoc([]), view=object(), raster=_raster(), resolved_ids=[],
            categories_with_colors=[(cat_doors, (1, 2, 3))],
            link_category_color_map={},  # Doors is absent -- filter failed
            link_category_to_instances={cat_doors.Id.IntegerValue: {link_inst}},
            instances_to_hide=set(), diag=None, view_id=1,
        )

    assert result["link"] == {}


def test_hidden_link_instance_is_excluded_from_link_collection():
    cat_walls = _FakeCategory("Walls", 10)
    linked_doc = _FakeLinkedDoc([_FakeElement(801, cat_walls, _FakeBBox((0, 0, 0), (1, 1, 1)))])
    link_inst = _FakeLinkInstance(9003, linked_doc)

    with _install_fake_revit_db():
        result = color_id_buffer._collect_near_face_w_data(
            _FakeDoc([]), view=object(), raster=_raster(), resolved_ids=[],
            categories_with_colors=[(cat_walls, (1, 2, 3))],
            link_category_color_map={"Walls": [1, 2, 3]},
            link_category_to_instances={cat_walls.Id.IntegerValue: {link_inst}},
            instances_to_hide={link_inst}, diag=None, view_id=1,
        )

    assert result["link"] == {}


def test_non_model_category_link_elements_are_excluded_by_policy():
    """A LINK element whose category fails should_include_element's
    CategoryType.Model check (e.g. an Annotation-type category) must never
    be reported as a near-face-W candidate -- collection_policy.py is the
    single source of truth for category inclusion (CLAUDE.md refactor rule
    #3), not a private re-check here."""
    cat_tags = _FakeCategory("Tags", 12, cat_type=CATEGORY_TYPE_ANNOTATION)
    linked_doc = _FakeLinkedDoc([_FakeElement(901, cat_tags, _FakeBBox((0, 0, 0), (1, 1, 1)))])
    link_inst = _FakeLinkInstance(9004, linked_doc)

    with _install_fake_revit_db():
        result = color_id_buffer._collect_near_face_w_data(
            _FakeDoc([]), view=object(), raster=_raster(), resolved_ids=[],
            categories_with_colors=[(cat_tags, (1, 2, 3))],
            link_category_color_map={"Tags": [1, 2, 3]},
            link_category_to_instances={cat_tags.Id.IntegerValue: {link_inst}},
            instances_to_hide=set(), diag=None, view_id=1,
        )

    assert result["link"] == {}


def test_two_overlapping_same_category_link_elements_get_distinct_bboxes():
    """Two LINK elements of the same category with overlapping projected
    bboxes must both be collected as distinct candidates (distinct keys,
    distinct bbox_corners_uv) -- the identity resolver needs this to
    disambiguate them later."""
    cat_walls = _FakeCategory("Walls", 10)
    elem_near = _FakeElement(601, cat_walls, _FakeBBox((0, 0, 0), (10, 10, 5)))
    elem_far = _FakeElement(602, cat_walls, _FakeBBox((2, 2, 0), (8, 8, -5)))
    linked_doc = _FakeLinkedDoc([elem_near, elem_far])
    link_inst = _FakeLinkInstance(9005, linked_doc)

    with _install_fake_revit_db():
        result = color_id_buffer._collect_near_face_w_data(
            _FakeDoc([]), view=object(), raster=_raster(), resolved_ids=[],
            categories_with_colors=[(cat_walls, (1, 2, 3))],
            link_category_color_map={"Walls": [1, 2, 3]},
            link_category_to_instances={cat_walls.Id.IntegerValue: {link_inst}},
            instances_to_hide=set(), diag=None, view_id=1,
        )

    link_entries = result["link"]
    assert set(link_entries.keys()) == {"9005:601", "9005:602"}
    near_w = link_entries["9005:601"]["near_face_w"]
    far_w = link_entries["9005:602"]["near_face_w"]
    assert near_w != far_w, "distinct bboxes must not collapse to the same near_face_w"
