"""Stage A step 1: a painted DWG ImportInstance is IDENTIFIABLE in the capture
record.

DWG imports were already painted and already recorded -- expand_host_link_
import_model_elements() wraps each ImportInstance in a LinkedElementProxy
carrying source_type="DWG", _split_expanded_elements() hands it back in the
HOST bucket (it is a real element of `doc` and overrides the ordinary way),
and _collect_near_face_w_data()'s host loop gives it a category and a bbox
like any other host element. What was missing is the one bit that says which
it is: source_type was read at the partition and dropped there, so nothing
downstream could tell a DWG import from a true HOST element.

These tests pin the carried-through source, and they pin the thing that makes
the addition safe: HOST and LINK records keep every pre-existing key with its
pre-existing value.

The fakes mirror tests/test_near_face_w_collection.py's -- same minimal
Autodesk.Revit.DB stand-in, same plan-view basis -- so the two files exercise
the same collection function through the same door.
"""
import contextlib
import sys
import types

import vop_interwoven.color_id_buffer as color_id_buffer
from vop_interwoven.revit.view_basis import ViewBasis


class _FakeElementId:
    def __init__(self, v):
        self.IntegerValue = int(v)

    def __eq__(self, other):
        return isinstance(other, _FakeElementId) and other.IntegerValue == self.IntegerValue

    def __hash__(self):
        return hash(self.IntegerValue)


class _FakeCategory:
    def __init__(self, name, cat_id):
        self.Name = name
        self.Id = _FakeElementId(cat_id)


class _P:
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = x, y, z


class _FakeBBox:
    def __init__(self, mn, mx):
        self.Min = _P(*mn)
        self.Max = _P(*mx)


class _FakeElement:
    """A true HOST element."""
    def __init__(self, elem_id, category, bbox):
        self.Id = _FakeElementId(elem_id)
        self.Category = category
        self._bbox = bbox

    def get_BoundingBox(self, _view):
        return self._bbox


class ImportInstance(_FakeElement):
    """A DWG/DXF import as it lives in the HOST document.

    The class NAME is the load-bearing part: both revit/collection_policy.py
    and color_id_buffer._is_import_instance() identify an import by type name
    rather than by isinstance, precisely so the check works outside Revit and
    survives category-name localization. test_import_instance_detection_
    agrees_with_collection_policy below composes the two against this object.
    """


class _FakeLinkedElementProxy:
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
    original = sys.modules.get("Autodesk.Revit.DB")
    sys.modules["Autodesk.Revit.DB"] = fake_db
    try:
        yield fake_db
    finally:
        if original is None:
            sys.modules.pop("Autodesk.Revit.DB", None)
        else:
            sys.modules["Autodesk.Revit.DB"] = original


def _patch_link_proxies(monkeypatch, proxies):
    monkeypatch.setattr(
        "vop_interwoven.revit.linked_documents.collect_all_linked_elements",
        lambda *args, **kwargs: list(proxies),
    )


_PLAN_BASIS = ViewBasis(origin=(0, 0, 0), right=(1, 0, 0), up=(0, 1, 0), forward=(0, 0, -1))


def _raster():
    return types.SimpleNamespace(view_basis=_PLAN_BASIS)


def _collect(doc, resolved_ids, host_source_types, diag=None, link_map=None):
    with _install_fake_revit_db():
        return color_id_buffer._collect_near_face_w_data(
            doc, view=object(), raster=_raster(), cfg=object(),
            resolved_ids=resolved_ids,
            link_category_color_map=link_map if link_map is not None else {},
            diag=diag, view_id=7, host_source_types=host_source_types,
        )


# --- the source_type survives the partition ---------------------------------

def test_split_preserves_dwg_source_type_for_non_link_entries():
    """_split_expanded_elements drops source_type for everything it returns
    unless the caller asks for it. With source_out it is preserved, keyed by
    the same ids that end up in host_elements."""
    cat = _FakeCategory("Walls", 10)
    host_elem = _FakeElement(1001, cat, _FakeBBox((0, 0, 0), (2, 2, 2)))
    dwg_proxy = _FakeLinkedElementProxy(
        2002, 2002, _FakeCategory("site-plan.dwg", 99),
        _FakeBBox((5, 5, 0), (9, 9, 1)), source_type="DWG",
    )
    link_proxy = _FakeLinkedElementProxy(3003, 9001, cat, _FakeBBox((0, 0, 0), (1, 1, 1)))

    expanded = [
        {"element": host_elem, "source_type": "HOST"},
        {"element": dwg_proxy, "source_type": "DWG"},
        {"element": link_proxy, "source_type": "LINK"},
    ]

    source_out = {}
    host_elements, link_entries = color_id_buffer._split_expanded_elements(
        expanded, source_out=source_out,
    )

    assert [e.Id.IntegerValue for e in host_elements] == [1001, 2002]
    assert source_out == {1001: "HOST", 2002: "DWG"}
    # LINK is routed to link_entries and deliberately never enters the map.
    assert len(link_entries) == 1
    assert 3003 not in source_out


def test_split_without_source_out_is_unchanged():
    """The out-parameter is opt-in: every existing 2-tuple call site keeps
    working and keeps getting exactly what it got before."""
    cat = _FakeCategory("Walls", 10)
    host_elem = _FakeElement(1001, cat, None)
    expanded = [{"element": host_elem, "source_type": "DWG"}]

    host_elements, link_entries = color_id_buffer._split_expanded_elements(expanded)
    assert [e.Id.IntegerValue for e in host_elements] == [1001]
    assert link_entries == []


def test_split_dedup_and_source_map_agree():
    """The map is filled on the SAME walk that does the dedup, so a repeated
    element appears once in both -- there is no second walk to disagree."""
    cat = _FakeCategory("Walls", 10)
    elem = _FakeElement(1001, cat, None)
    expanded = [
        {"element": elem, "source_type": "DWG"},
        {"element": elem, "source_type": "DWG"},
    ]
    source_out = {}
    host_elements, _ = color_id_buffer._split_expanded_elements(expanded, source_out=source_out)
    assert len(host_elements) == 1
    assert source_out == {1001: "DWG"}


# --- the record says source=DWG, with a category and a bbox -----------------

def test_dwg_entry_has_source_dwg_category_and_bbox():
    """The gate: a painted DWG is locatable on the TIFF and labelled as a DWG."""
    dwg_cat = _FakeCategory("site-plan.dwg", 99)
    dwg_elem = ImportInstance(2002, dwg_cat, _FakeBBox((5, 5, 0), (9, 9, 1)))
    doc = _FakeDoc([dwg_elem])
    diag = _FakeDiag()

    result = _collect(doc, [dwg_elem.Id], {2002: "DWG"}, diag=diag)

    entry = result["host"]["2002"]
    assert entry["source"] == {"state": "value", "value": "DWG"}
    assert entry["category_state"] == {"state": "value", "value": "site-plan.dwg"}
    assert entry["category"] == "site-plan.dwg"
    # Same bbox shape as a host record, from the same projection call.
    assert entry["bbox_corners_uv"] == [[5, 5], [9, 5], [9, 9], [5, 9]]
    assert entry["near_face_w"] is not None
    assert not diag.errors


def test_host_entry_alongside_dwg_is_labelled_host():
    cat = _FakeCategory("Walls", 10)
    host_elem = _FakeElement(1001, cat, _FakeBBox((0, 0, 0), (2, 2, 2)))
    dwg_elem = ImportInstance(2002, _FakeCategory("site-plan.dwg", 99),
                              _FakeBBox((5, 5, 0), (9, 9, 1)))
    doc = _FakeDoc([host_elem, dwg_elem])

    result = _collect(doc, [host_elem.Id, dwg_elem.Id], {1001: "HOST", 2002: "DWG"})

    assert result["host"]["1001"]["source"] == {"state": "value", "value": "HOST"}
    assert result["host"]["2002"]["source"] == {"state": "value", "value": "DWG"}


def test_source_is_unavailable_with_a_reason_not_a_default():
    """No expansion record -> "unavailable" plus the reason. Never "HOST" as a
    stand-in, which is the whole point of the three-valued shape."""
    cat = _FakeCategory("Walls", 10)
    host_elem = _FakeElement(1001, cat, _FakeBBox((0, 0, 0), (2, 2, 2)))
    doc = _FakeDoc([host_elem])

    result = _collect(doc, [host_elem.Id], None)

    source = result["host"]["1001"]["source"]
    assert source["state"] == "unavailable"
    assert source["reason"]
    assert "value" not in source


def test_category_state_is_unavailable_when_the_name_read_raises():
    """A raising Category.Name is recorded, not swallowed and not coerced --
    getattr(cat, "Name", None) does not catch it, so it used to escape the
    whole collection."""
    class _ExplodingCategory:
        @property
        def Name(self):
            raise RuntimeError("localization table unavailable")

    elem = _FakeElement(1001, _ExplodingCategory(), _FakeBBox((0, 0, 0), (1, 1, 1)))
    doc = _FakeDoc([elem])
    diag = _FakeDiag()

    result = _collect(doc, [elem.Id], {1001: "HOST"}, diag=diag)

    entry = result["host"]["1001"]
    assert entry["category_state"]["state"] == "unavailable"
    assert "RuntimeError" in entry["category_state"]["reason"]
    # The legacy key keeps its documented meaning -- a name or None.
    assert entry["category"] is None
    assert any(w["callsite"] == "near_face_w.host.category" for w in diag.warnings)


def test_category_state_records_a_missing_category_as_a_value_not_a_failure():
    """"This element has no Category" is an ANSWER, not a failed read."""
    elem = _FakeElement(1001, None, _FakeBBox((0, 0, 0), (1, 1, 1)))
    doc = _FakeDoc([elem])

    result = _collect(doc, [elem.Id], {1001: "HOST"})
    entry = result["host"]["1001"]
    assert entry["category_state"] == {"state": "value", "value": None}
    assert entry["category"] is None


# --- a DWG that resolve_all reached by expansion, not by the partition ------

def test_grouped_dwg_import_is_still_labelled_dwg():
    """resolve_all() expands a Group's members into ids _split_expanded_
    elements never saw, so those ids are absent from the source map. Treating
    "absent" as HOST would mislabel a DWG import placed inside a Group --
    exactly the element this step exists to make visible. The element itself
    decides.
    """
    group_elem = _FakeElement(1001, _FakeCategory("Walls", 10), _FakeBBox((0, 0, 0), (2, 2, 2)))
    grouped_dwg = ImportInstance(2002, _FakeCategory("site-plan.dwg", 99),
                                 _FakeBBox((5, 5, 0), (9, 9, 1)))
    doc = _FakeDoc([group_elem, grouped_dwg])

    # Only the group's own id reached the partition; 2002 came out of
    # resolve_all's Group expansion.
    result = _collect(doc, [group_elem.Id, grouped_dwg.Id], {1001: "HOST"})

    assert result["host"]["1001"]["source"] == {"state": "value", "value": "HOST"}
    assert result["host"]["2002"]["source"] == {"state": "value", "value": "DWG"}


def test_expanded_subcomponent_of_a_host_element_is_labelled_host():
    """Control for the case above: an ordinary sub-component absent from the
    map must NOT come back as DWG."""
    parent = _FakeElement(1001, _FakeCategory("Doors", 11), _FakeBBox((0, 0, 0), (2, 2, 2)))
    sub = _FakeElement(1002, _FakeCategory("Doors", 11), _FakeBBox((0, 0, 0), (1, 1, 1)))
    doc = _FakeDoc([parent, sub])

    result = _collect(doc, [parent.Id, sub.Id], {1001: "HOST"})

    assert result["host"]["1002"]["source"] == {"state": "value", "value": "HOST"}


# --- compose the two ImportInstance detectors -------------------------------

def test_import_instance_detection_agrees_with_collection_policy():
    """_is_import_instance() and revit/collection_policy.py's exclusion are
    the same idiom in two places. Testing each against its own copy would
    prove nothing about their agreeing, so this runs BOTH over the same
    objects and asserts they answer identically.
    """
    import inspect
    from vop_interwoven.revit import collection_policy

    source = inspect.getsource(collection_policy)
    assert 'type(elem).__name__ == "ImportInstance"' in source, (
        "collection_policy no longer identifies an ImportInstance by type name; "
        "_is_import_instance() must be re-derived from whatever replaced it"
    )

    dwg = ImportInstance(2002, _FakeCategory("site-plan.dwg", 99), None)
    host = _FakeElement(1001, _FakeCategory("Walls", 10), None)

    policy_says_import = (lambda e: type(e).__name__ == "ImportInstance")
    for elem in (dwg, host):
        assert color_id_buffer._is_import_instance(elem) is policy_says_import(elem)


# --- HOST/LINK records are untouched ----------------------------------------

_PRE_CHANGE_HOST_KEYS = ("bbox_corners_uv", "near_face_w", "category")
_PRE_CHANGE_LINK_KEYS = (
    "bbox_corners_uv", "near_face_w", "category", "link_inst_id", "link_elem_id",
)


def test_fixture_without_dwg_is_unchanged_under_the_pre_change_keys(monkeypatch):
    """A capture containing no DWG at all produces, for every pre-existing
    key of every host and link record, exactly what it produced before this
    step -- byte-identical once the new keys are set aside.

    Pinned by VALUE rather than by re-running an old build: the expected
    values below are the ones tests/test_near_face_w_collection.py already
    asserts for this same fixture at ea05a8e.
    """
    cat_walls = _FakeCategory("Walls", 10)
    host_elem = _FakeElement(1001, cat_walls, _FakeBBox((0, 0, 0), (2, 2, 2)))
    doc = _FakeDoc([host_elem])
    link_a = _FakeLinkedElementProxy(501, 9001, cat_walls, _FakeBBox((10, 10, 0), (12, 12, 2)))
    _patch_link_proxies(monkeypatch, [link_a])

    result = _collect(doc, [host_elem.Id], {1001: "HOST"},
                      link_map={"Walls": [10, 20, 30]})

    host_entry = result["host"]["1001"]
    assert {k: host_entry[k] for k in _PRE_CHANGE_HOST_KEYS} == {
        "bbox_corners_uv": [[0, 0], [2, 0], [2, 2], [0, 2]],
        "near_face_w": host_entry["near_face_w"],
        "category": "Walls",
    }
    assert host_entry["near_face_w"] is not None

    link_entry = result["link"]["9001:501"]
    # The LINK record gains NOTHING at all -- key set frozen, not just values.
    assert set(link_entry.keys()) == set(_PRE_CHANGE_LINK_KEYS)
    assert link_entry["category"] == "Walls"
    assert link_entry["link_inst_id"] == 9001
    assert link_entry["link_elem_id"] == 501
    assert link_entry["bbox_corners_uv"] == [[10, 10], [12, 10], [12, 12], [10, 12]]


def test_host_record_gains_exactly_the_two_new_keys():
    """Names the addition, so a third key cannot arrive unreviewed."""
    host_elem = _FakeElement(1001, _FakeCategory("Walls", 10), _FakeBBox((0, 0, 0), (2, 2, 2)))
    doc = _FakeDoc([host_elem])

    entry = _collect(doc, [host_elem.Id], {1001: "HOST"})["host"]["1001"]
    assert set(entry.keys()) == set(_PRE_CHANGE_HOST_KEYS) | {"source", "category_state"}
