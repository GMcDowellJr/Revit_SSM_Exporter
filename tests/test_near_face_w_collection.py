"""Unit tests for vop_interwoven/color_id_buffer.py's _collect_near_face_w_data
(Phase 1b: near-face-W + UV bbox footprint collection for the additive
"near_face_w_map" sidecar section).

Installs a minimal fake Autodesk.Revit.DB module into sys.modules -- same
technique tests/test_color_id_buffer_link_category_filters.py uses for
Revit-adjacent code with no other way to run outside Revit. The fake module
deliberately omits CategoryType/BuiltInCategory so
revit/collection_policy.should_include_element() takes its documented
"outside Revit" fallback path where that policy is still exercised
elsewhere in this codebase (not here -- see the module note below).

LINK candidates are supplied by monkeypatching revit/linked_documents.
collect_all_linked_elements(), the same view-scoped collection function the
rest of the pipeline already relies on for LINK visibility -- exercising
this module's OWN filtering (by colored category name, by hidden instance,
by source_type) rather than re-testing collect_all_linked_elements' own
policy/visibility logic, which has its own test coverage elsewhere.
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
    """Stands in for a real HOST Element."""
    def __init__(self, elem_id, category, bbox):
        self.Id = _FakeElementId(elem_id)
        self.Category = category
        self._bbox = bbox

    def get_BoundingBox(self, _view):
        return self._bbox


class _FakeLinkedElementProxy:
    """Stands in for revit/linked_documents.LinkedElementProxy -- bbox is
    already host-space (as the real proxy's get_BoundingBox() always
    returns), so no link transform is involved on this side at all."""
    def __init__(self, elem_id, link_inst_id, category, bbox, source_type="LINK"):
        self.Id = _FakeElementId(elem_id)
        self.LinkInstanceId = _FakeElementId(link_inst_id)
        self.Category = category
        self.source_type = source_type
        self._bbox = bbox

    def get_BoundingBox(self, _view):
        return self._bbox


class _FakeDoc:
    def __init__(self, elements):
        self._by_id = {e.Id.IntegerValue: e for e in elements}

    def GetElement(self, eid):
        return self._by_id.get(eid.IntegerValue)


class _FakeIdentityTransform:
    pass


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
    fake_db.Transform = types.SimpleNamespace(Identity=_FakeIdentityTransform())
    # Deliberately no CategoryType/BuiltInCategory/FilteredElementCollector --
    # this function no longer touches the Revit API directly for LINK (it
    # delegates entirely to collect_all_linked_elements, monkeypatched per
    # test below), so only the Transform.Identity lookup needs a fake here.

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


def _patch_link_proxies(monkeypatch, proxies):
    monkeypatch.setattr(
        "vop_interwoven.revit.linked_documents.collect_all_linked_elements",
        lambda *args, **kwargs: list(proxies),
    )


# Plan view looking down +Z, basis aligned to world XY -- world (x, y) maps
# directly to view (u, v), matching the other Phase 1b test files' convention.
_PLAN_BASIS = ViewBasis(origin=(0, 0, 0), right=(1, 0, 0), up=(0, 1, 0), forward=(0, 0, -1))


def _raster():
    return types.SimpleNamespace(view_basis=_PLAN_BASIS)


def test_collects_host_and_link_entries_with_bbox_and_near_face_w(monkeypatch):
    cat_walls = _FakeCategory("Walls", 10)
    host_elem = _FakeElement(1001, cat_walls, _FakeBBox((0, 0, 0), (2, 2, 2)))
    doc = _FakeDoc([host_elem])

    link_a = _FakeLinkedElementProxy(501, 9001, cat_walls, _FakeBBox((10, 10, 0), (12, 12, 2)))
    link_b = _FakeLinkedElementProxy(502, 9001, cat_walls, _FakeBBox((20, 20, 0), (22, 22, 2)))
    _patch_link_proxies(monkeypatch, [link_a, link_b])

    diag = _FakeDiag()

    with _install_fake_revit_db():
        result = color_id_buffer._collect_near_face_w_data(
            doc, view=object(), raster=_raster(), cfg=object(), resolved_ids=[host_elem.Id],
            link_category_color_map={"Walls": [10, 20, 30]}, diag=diag, view_id=42,
        )

    assert set(result.keys()) == {"host", "link"}

    host_entry = result["host"]["1001"]
    assert host_entry["category"] == "Walls"
    assert host_entry["near_face_w"] is not None
    assert host_entry["bbox_corners_uv"] == [[0, 0], [2, 0], [2, 2], [0, 2]]

    assert set(result["link"].keys()) == {"9001:501", "9001:502"}
    link_entry_a = result["link"]["9001:501"]
    assert link_entry_a["link_inst_id"] == 9001
    assert link_entry_a["link_elem_id"] == 501
    assert link_entry_a["category"] == "Walls"
    assert link_entry_a["near_face_w"] is not None
    assert link_entry_a["bbox_corners_uv"] == [[10, 10], [12, 10], [12, 12], [10, 12]]

    link_entry_b = result["link"]["9001:502"]
    assert link_entry_b["bbox_corners_uv"] == [[20, 20], [22, 20], [22, 22], [20, 22]]

    assert not diag.errors


def test_host_element_with_no_bbox_still_gets_an_entry_recorded_as_none():
    cat_walls = _FakeCategory("Walls", 10)
    host_elem = _FakeElement(1001, cat_walls, None)  # get_BoundingBox returns None
    doc = _FakeDoc([host_elem])

    diag = _FakeDiag()
    with _install_fake_revit_db():
        result = color_id_buffer._collect_near_face_w_data(
            doc, view=object(), raster=_raster(), cfg=object(), resolved_ids=[host_elem.Id],
            link_category_color_map={}, diag=diag, view_id=1,
        )

    entry = result["host"]["1001"]
    assert entry == {"bbox_corners_uv": None, "near_face_w": None, "category": "Walls"}
    assert any(w["callsite"] == "near_face_w.host" for w in diag.warnings), (
        "a missing bbox must be recorded, not silently dropped -- CLAUDE.md's no-silent-failure rule"
    )


def test_near_face_w_is_normalized_to_none_when_non_finite():
    """estimate_nearest_depth_from_bbox() returns float("inf") when the
    raster has no usable view_basis -- that must never be serialized as-is
    (json.dump emits the non-standard "Infinity" token); it must come back
    as None, matching the documented float | None contract."""
    cat_walls = _FakeCategory("Walls", 10)
    host_elem = _FakeElement(1001, cat_walls, _FakeBBox((0, 0, 0), (2, 2, 2)))
    doc = _FakeDoc([host_elem])
    raster_without_basis = types.SimpleNamespace(view_basis=None)

    with _install_fake_revit_db():
        result = color_id_buffer._collect_near_face_w_data(
            doc, view=object(), raster=raster_without_basis, cfg=object(),
            resolved_ids=[host_elem.Id], link_category_color_map={}, diag=None, view_id=1,
        )

    assert result["host"]["1001"]["near_face_w"] is None


def test_link_collection_skipped_entirely_when_no_category_was_colored(monkeypatch):
    """When link_category_color_map is empty, no LINK category was
    successfully colored in this view -- collect_all_linked_elements must
    never even be called (paying its view-scoped scan cost for nothing)."""
    called = []
    monkeypatch.setattr(
        "vop_interwoven.revit.linked_documents.collect_all_linked_elements",
        lambda *args, **kwargs: called.append(True) or [],
    )

    with _install_fake_revit_db():
        result = color_id_buffer._collect_near_face_w_data(
            _FakeDoc([]), view=object(), raster=_raster(), cfg=object(), resolved_ids=[],
            link_category_color_map={}, diag=None, view_id=1,
        )

    assert result["link"] == {}
    assert called == []


def test_link_category_whose_filter_failed_is_excluded_from_link_collection(monkeypatch):
    """A LINK element whose category is NOT a key of link_category_color_map
    means that category's filter application failed (see
    _apply_link_category_filters) -- no pixels of that color exist in the
    TIFF, so near_face_w collection must not manufacture identity data for
    it, even though collect_all_linked_elements() itself has no reason to
    exclude it (category-filter success/failure is Stage A paint state,
    not a visibility/policy fact collect_all_linked_elements knows about)."""
    cat_doors = _FakeCategory("Doors", 11)
    cat_walls = _FakeCategory("Walls", 10)
    proxy_doors = _FakeLinkedElementProxy(701, 9002, cat_doors, _FakeBBox((0, 0, 0), (1, 1, 1)))
    proxy_walls = _FakeLinkedElementProxy(702, 9002, cat_walls, _FakeBBox((0, 0, 0), (1, 1, 1)))
    _patch_link_proxies(monkeypatch, [proxy_doors, proxy_walls])

    with _install_fake_revit_db():
        result = color_id_buffer._collect_near_face_w_data(
            _FakeDoc([]), view=object(), raster=_raster(), cfg=object(), resolved_ids=[],
            link_category_color_map={"Walls": [1, 2, 3]},  # Doors absent -- its filter failed diag=None, view_id=1,
        )

    assert set(result["link"].keys()) == {"9002:702"}


def test_dwg_import_proxies_are_excluded_from_link_collection(monkeypatch):
    """DWG occlusion/identity work is explicitly out of scope for Phase 1b
    (see this module's docstring) -- a proxy collect_all_linked_elements()
    returns for a DWG import (source_type="DWG") must never be reported as
    a LINK candidate just because its category happens to match a colored
    RVT LINK category name."""
    cat_walls = _FakeCategory("Walls", 10)
    dwg_proxy = _FakeLinkedElementProxy(
        901, 9004, cat_walls, _FakeBBox((0, 0, 0), (1, 1, 1)), source_type="DWG",
    )
    _patch_link_proxies(monkeypatch, [dwg_proxy])

    with _install_fake_revit_db():
        result = color_id_buffer._collect_near_face_w_data(
            _FakeDoc([]), view=object(), raster=_raster(), cfg=object(), resolved_ids=[],
            link_category_color_map={"Walls": [1, 2, 3]}, diag=None, view_id=1,
        )

    assert result["link"] == {}


def test_two_overlapping_same_category_link_elements_get_distinct_bboxes(monkeypatch):
    """Two LINK elements of the same category with overlapping projected
    bboxes must both be collected as distinct candidates (distinct keys,
    distinct bbox_corners_uv) -- the identity resolver needs this to
    disambiguate them later."""
    cat_walls = _FakeCategory("Walls", 10)
    elem_near = _FakeLinkedElementProxy(601, 9005, cat_walls, _FakeBBox((0, 0, 0), (10, 10, 5)))
    elem_far = _FakeLinkedElementProxy(602, 9005, cat_walls, _FakeBBox((2, 2, 0), (8, 8, -5)))
    _patch_link_proxies(monkeypatch, [elem_near, elem_far])

    with _install_fake_revit_db():
        result = color_id_buffer._collect_near_face_w_data(
            _FakeDoc([]), view=object(), raster=_raster(), cfg=object(), resolved_ids=[],
            link_category_color_map={"Walls": [1, 2, 3]}, diag=None, view_id=1,
        )

    link_entries = result["link"]
    assert set(link_entries.keys()) == {"9005:601", "9005:602"}
    near_w = link_entries["9005:601"]["near_face_w"]
    far_w = link_entries["9005:602"]["near_face_w"]
    assert near_w != far_w, "distinct bboxes must not collapse to the same near_face_w"
