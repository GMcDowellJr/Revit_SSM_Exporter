"""Stage A step 4 -- bbox records: annotation (absolute view UV) + model 3D.

Three things are under test, and they fail for different reasons:

1. ANNOTATION bboxes are recorded in ABSOLUTE view UV, so a cap re-centre of
   frame B cannot change them. The gate. See
   test_annotation_bbox_is_invariant_under_b_translation, which carries a
   CONTROL proving the fixture would notice if production had baked a frame
   in -- without it the assertion passes against any implementation that
   simply ignores B, including one that ignores it by accident.

2. MODEL/DWG entries carry a 3D AABB (decision B, 2026-09-21) built from the
   SAME transform ladder as the UV projection. The discriminating case is a
   ROTATING bbox.Transform: raw Min/Max and transformed-corner AABB agree for
   an identity transform, so an identity-only fixture would pass against the
   exact defect revit/annotation.py already carries (D2 -- raw Min/Max with
   Transform discarded). The rotated case is the one that tells them apart.

3. bbox_3d is NOT uniform across the annotation pass. Grids and levels reach
   it by category rather than by view-specificity and do have model-space
   extents, so they carry a real AABB where a text note carries
   "not_applicable". The fixtures put both kinds side by side, because a
   datum-only fixture cannot tell "datums get an AABB" from "everything
   does" -- which was the original bug.
"""
import contextlib
import sys
import types

import pytest

import vop_interwoven.color_id_buffer as color_id_buffer
from vop_interwoven.revit.collection import bbox_world_aabb, _bbox_world_corners
from vop_interwoven.revit.view_basis import ViewBasis


# --- fakes (same shape as tests/test_near_face_w_collection.py) ------------

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
        self.X, self.Y, self.Z = float(x), float(y), float(z)


class _FakeBBox:
    def __init__(self, mn, mx, transform=None):
        self.Min = _P(*mn)
        self.Max = _P(*mx)
        self.Transform = transform


class _RotateZ90:
    """(x, y, z) -> (-y, x, z).

    Accepts and returns plain tuples, which is the stub path
    _bbox_world_corners() documents for running outside Revit.
    """
    def OfPoint(self, p):
        return (-p[1], p[0], p[2])


class _FakeElement:
    def __init__(self, elem_id, category, bbox, model_bbox=None):
        self.Id = _FakeElementId(elem_id)
        self.Category = category
        self._bbox = bbox
        self._model_bbox = model_bbox

    def get_BoundingBox(self, view):
        return self._bbox if view is not None else self._model_bbox


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

    def info(self, **kwargs):
        pass


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


_PLAN_BASIS = ViewBasis(origin=(0, 0, 0), right=(1, 0, 0), up=(0, 1, 0), forward=(0, 0, -1))


def _raster(bounds_xy=None):
    """A raster carrying frame B in bounds_xy, as production's does."""
    return types.SimpleNamespace(view_basis=_PLAN_BASIS, bounds_xy=bounds_xy)


def _collect_anno(doc, ids, raster, diag=None, membership="owner_view"):
    """membership: a basis string applied to every id, or an explicit
    {id: basis} map, or None to supply no basis at all."""
    if membership is None:
        basis_map = None
    elif isinstance(membership, dict):
        basis_map = membership
    else:
        basis_map = dict((eid.IntegerValue, membership) for eid in ids)
    with _install_fake_revit_db():
        return color_id_buffer._collect_annotation_bbox_data(
            doc, view=object(), resolved_ids=ids, raster=raster,
            diag=diag, view_id=42, membership_basis_by_id=basis_map,
        )


# --- 1. THE GATE: absolute view UV, invariant under a B translation --------

def test_annotation_bbox_is_invariant_under_b_translation():
    """The step 4 gate.

    view_basis.py's cap envelope re-centres frame B on the pre-annotation
    model crop when the grid cap fires. A record in B-relative coordinates
    would therefore change for the same annotation in the same view. This
    asserts it does not -- and the CONTROL below proves the fixture can tell
    the difference, so a green here is a measurement rather than a tautology.
    """
    cat = _FakeCategory("Text Notes", 11)
    elem = _FakeElement(7001, cat, _FakeBBox((10, 20, 0), (14, 23, 0)))
    doc = _FakeDoc([elem])

    # Two DIFFERENT frames B, exactly as a cap re-centre would produce.
    b_before = (0.0, 0.0, 100.0, 80.0)
    b_after = (-37.5, 12.25, 62.5, 92.25)

    rec_before, _, _ = _collect_anno(doc, [elem.Id], _raster(b_before))
    rec_after, _, _ = _collect_anno(doc, [elem.Id], _raster(b_after))

    uv_before = rec_before["7001"]["bbox_uv"]
    uv_after = rec_after["7001"]["bbox_uv"]

    assert uv_before["state"] == "value"
    assert uv_before == uv_after, (
        "annotation bbox changed when frame B moved, so it is not in "
        "absolute view UV")
    # And it is the element's own view-local extent, not something relative.
    assert uv_before["value"] == [[10, 20], [14, 20], [14, 23], [10, 23]]

    # --- CONTROL ---------------------------------------------------------
    # Had the record been stored relative to B.min -- the obvious
    # alternative, and what LOCKED item 6's "origin at B.min" framing invites
    # -- these two frames WOULD give different numbers. Without this, the
    # assertion above would also pass for an implementation that happened to
    # produce constant output for an unrelated reason.
    rel_before = [[u - b_before[0], v - b_before[1]] for u, v in uv_before["value"]]
    rel_after = [[u - b_after[0], v - b_after[1]] for u, v in uv_after["value"]]
    assert rel_before != rel_after, (
        "fixture does not discriminate: the two frames B must differ enough "
        "that a frame-relative record would change")


def test_annotation_bbox_3d_is_not_applicable_never_unavailable():
    """An annotation has no model-space extent. That is a fact about the
    element, not a failed read, and the two must not be written the same."""
    cat = _FakeCategory("Dimensions", 12)
    elem = _FakeElement(7002, cat, _FakeBBox((0, 0, 0), (5, 1, 0)))
    doc = _FakeDoc([elem])

    rec, _, _ = _collect_anno(doc, [elem.Id], _raster())

    bbox_3d = rec["7002"]["bbox_3d"]
    assert bbox_3d["state"] == "not_applicable"
    assert "reason" in bbox_3d and bbox_3d["reason"]
    assert "value" not in bbox_3d


def test_annotation_without_a_bbox_is_unavailable_with_a_reason_not_omitted():
    """Absent from the map must never be able to mean "had no bbox"."""
    cat = _FakeCategory("Text Notes", 11)
    elem = _FakeElement(7003, cat, None, model_bbox=None)
    doc = _FakeDoc([elem])
    diag = _FakeDiag()

    rec, sources, _ = _collect_anno(doc, [elem.Id], _raster(), diag=diag)

    assert "7003" in rec, "element with no bbox was dropped from the map"
    assert rec["7003"]["bbox_uv"]["state"] == "unavailable"
    assert rec["7003"]["bbox_uv"]["reason"]
    assert rec["7003"]["bbox_source"] == "none"
    assert sources["none"] == 1


def test_annotation_bbox_records_which_get_boundingbox_rung_answered():
    """The 10-20x get_BoundingBox(view) cost claim is UNCONFIRMED. The
    record carries which rung answered so a real capture can measure it
    rather than the question being settled by argument."""
    cat = _FakeCategory("Text Notes", 11)
    from_view = _FakeElement(8001, cat, _FakeBBox((0, 0, 0), (1, 1, 0)))
    from_model = _FakeElement(8002, cat, None, model_bbox=_FakeBBox((2, 2, 0), (3, 3, 0)))
    doc = _FakeDoc([from_view, from_model])

    rec, sources, _ = _collect_anno(doc, [from_view.Id, from_model.Id], _raster())

    assert rec["8001"]["bbox_source"] == "view"
    assert rec["8002"]["bbox_source"] == "model"
    assert sources["view"] == 1 and sources["model"] == 1
    # Both still produced a usable UV rectangle -- the rung is provenance,
    # not a quality grade.
    assert rec["8001"]["bbox_uv"]["state"] == "value"
    assert rec["8002"]["bbox_uv"]["state"] == "value"


def test_annotation_bbox_unavailable_when_capture_has_no_view_basis():
    """No basis means no projection. That is a failed read -- "unavailable"
    with a reason -- and must not be confused with the 3D case's
    "not_applicable"."""
    cat = _FakeCategory("Text Notes", 11)
    elem = _FakeElement(7004, cat, _FakeBBox((0, 0, 0), (1, 1, 0)))
    doc = _FakeDoc([elem])

    rec, _, _ = _collect_anno(doc, [elem.Id], types.SimpleNamespace(view_basis=None))

    assert rec["7004"]["bbox_uv"]["state"] == "unavailable"
    assert "view basis" in rec["7004"]["bbox_uv"]["reason"]


# --- datums are NOT view-specific annotations (Codex P2, verified) --------

def test_a_datum_in_the_annotation_pass_gets_a_real_3d_aabb():
    """Grids and levels reach the annotation pass by CATEGORY, not by
    view-specificity (split_stage_a_pass_membership's "datum_category"
    basis), and they have model-space extents. Writing "not_applicable" for
    them would be a false claim about the element and would stop a consumer
    ever recovering the 3D record for that subset.
    """
    grid = _FakeElement(9101, _FakeCategory("Grids", 40),
                        _FakeBBox((0, 0, 0), (100, 0, 12)))
    text = _FakeElement(9102, _FakeCategory("Text Notes", 11),
                        _FakeBBox((5, 5, 0), (9, 6, 0)))
    doc = _FakeDoc([grid, text])

    rec, _, bases = _collect_anno(
        doc, [grid.Id, text.Id], _raster(),
        membership={9101: "datum_category", 9102: "owner_view"},
    )

    # The datum carries a real extent...
    grid_3d = rec["9101"]["bbox_3d"]
    assert grid_3d["state"] == "value"
    assert grid_3d["value"]["min"] == pytest.approx([0.0, 0.0, 0.0])
    assert grid_3d["value"]["max"] == pytest.approx([100.0, 0.0, 12.0])
    assert rec["9101"]["membership_basis"] == "datum_category"

    # ...while the true annotation beside it in the SAME pass does not. Both
    # in one fixture on purpose: a datum-only fixture could not tell "datums
    # get an AABB" from "everything gets an AABB".
    assert rec["9102"]["bbox_3d"]["state"] == "not_applicable"
    assert rec["9102"]["membership_basis"] == "owner_view"

    assert bases == {"owner_view": 1, "datum_category": 1, "unknown": 0}


def test_an_unknown_membership_basis_is_unavailable_not_guessed():
    """Without the basis neither answer is honest, so the record says so
    rather than defaulting to not_applicable and asserting something false
    about a possible datum."""
    elem = _FakeElement(9103, _FakeCategory("Grids", 40),
                        _FakeBBox((0, 0, 0), (10, 0, 3)))
    doc = _FakeDoc([elem])

    rec, _, bases = _collect_anno(doc, [elem.Id], _raster(), membership=None)

    bbox_3d = rec["9103"]["bbox_3d"]
    assert bbox_3d["state"] == "unavailable"
    assert "membership basis" in bbox_3d["reason"]
    assert rec["9103"]["membership_basis"] is None
    assert bases["unknown"] == 1
    # The UV half is unaffected -- only the 3D claim was undecidable.
    assert rec["9103"]["bbox_uv"]["state"] == "value"


def test_a_datum_whose_bbox_is_missing_is_unavailable_not_not_applicable():
    """A datum genuinely has an extent, so failing to read it is a failed
    read -- the one case where "unavailable" is the honest answer for an
    element in the annotation pass."""
    grid = _FakeElement(9104, _FakeCategory("Grids", 40), None, model_bbox=None)
    doc = _FakeDoc([grid])

    rec, _, _ = _collect_anno(
        doc, [grid.Id], _raster(), membership={9104: "datum_category"})

    bbox_3d = rec["9104"]["bbox_3d"]
    assert bbox_3d["state"] == "unavailable"
    assert "datum" in bbox_3d["reason"]


# --- 2. MODEL side: the 3D AABB, and the D2 defect it must not reproduce ---

def test_bbox_world_aabb_applies_transform_not_raw_min_max():
    """THE discriminating case for decision B's implementation.

    revit/annotation.py stores raw bbox.Min/bbox.Max and discards
    bbox.Transform (design-branch defect D2). For an identity transform that
    is indistinguishable from the correct answer, so only a ROTATING
    transform separates them.
    """
    bbox = _FakeBBox((0, 0, 0), (2, 4, 2), transform=_RotateZ90())

    aabb = bbox_world_aabb(bbox)

    # Rotating by +90 deg about Z sends x in [0,2], y in [0,4] to
    # x in [-4,0], y in [0,2].
    assert aabb["min"] == pytest.approx([-4.0, 0.0, 0.0])
    assert aabb["max"] == pytest.approx([0.0, 2.0, 2.0])

    # And it is NOT the raw Min/Max the defect would have produced.
    assert aabb["min"] != pytest.approx([0.0, 0.0, 0.0])
    assert aabb["max"] != pytest.approx([2.0, 4.0, 2.0])


def test_bbox_world_aabb_composes_with_the_shared_corner_set():
    """Bind the AABB to _bbox_world_corners rather than to a second copy of
    the transform ladder. Testing it against a reimplementation here would
    prove only that this file agrees with itself (CLAUDE.md, "a test that
    reimplements the thing it checks proves nothing")."""
    bbox = _FakeBBox((-3, 1, -7), (5, 9, 2), transform=_RotateZ90())

    corners = _bbox_world_corners(bbox)
    aabb = bbox_world_aabb(bbox)

    for axis in (0, 1, 2):
        assert aabb["min"][axis] == pytest.approx(min(c[axis] for c in corners))
        assert aabb["max"][axis] == pytest.approx(max(c[axis] for c in corners))


def test_bbox_world_aabb_returns_none_rather_than_a_zero_box():
    assert bbox_world_aabb(None) is None


def test_host_entry_carries_three_valued_bbox_3d_from_the_same_bbox(monkeypatch):
    monkeypatch.setattr(
        "vop_interwoven.revit.linked_documents.collect_all_linked_elements",
        lambda *a, **k: [],
    )
    cat = _FakeCategory("Walls", 10)
    elem = _FakeElement(1001, cat, None, model_bbox=_FakeBBox((0, 0, 0), (2, 4, 6)))
    doc = _FakeDoc([elem])
    diag = _FakeDiag()

    with _install_fake_revit_db():
        result = color_id_buffer._collect_near_face_w_data(
            doc, view=object(), raster=_raster(), cfg=object(),
            resolved_ids=[elem.Id], link_category_color_map={},
            diag=diag, view_id=42,
        )

    entry = result["host"]["1001"]
    assert entry["bbox_3d"]["state"] == "value"
    assert entry["bbox_3d"]["value"]["min"] == pytest.approx([0.0, 0.0, 0.0])
    assert entry["bbox_3d"]["value"]["max"] == pytest.approx([2.0, 4.0, 6.0])

    # The pre-existing keys keep their exact meaning -- near_face_w in
    # particular, which tools/link_identity_resolver.py tie-breaks on.
    assert entry["bbox_corners_uv"] == [[0, 0], [2, 0], [2, 4], [0, 4]]
    assert entry["near_face_w"] is not None
    assert not diag.errors


def test_host_entry_without_a_bbox_records_bbox_3d_unavailable(monkeypatch):
    """A missing 3D extent is "unavailable", never an origin-sized box."""
    monkeypatch.setattr(
        "vop_interwoven.revit.linked_documents.collect_all_linked_elements",
        lambda *a, **k: [],
    )
    cat = _FakeCategory("Walls", 10)
    elem = _FakeElement(1002, cat, None, model_bbox=None)
    doc = _FakeDoc([elem])

    with _install_fake_revit_db():
        result = color_id_buffer._collect_near_face_w_data(
            doc, view=object(), raster=_raster(), cfg=object(),
            resolved_ids=[elem.Id], link_category_color_map={},
            diag=_FakeDiag(), view_id=42,
        )

    bbox_3d = result["host"]["1002"]["bbox_3d"]
    assert bbox_3d["state"] == "unavailable"
    assert bbox_3d["reason"]
    assert "value" not in bbox_3d


def test_near_face_w_map_pre_step4_keys_are_unchanged(monkeypatch):
    """Freeze the pre-change key set: step 4 is additive, so every key that
    existed before it must still be there, spelled the same."""
    monkeypatch.setattr(
        "vop_interwoven.revit.linked_documents.collect_all_linked_elements",
        lambda *a, **k: [],
    )
    cat = _FakeCategory("Walls", 10)
    elem = _FakeElement(1003, cat, None, model_bbox=_FakeBBox((0, 0, 0), (1, 1, 1)))
    doc = _FakeDoc([elem])

    with _install_fake_revit_db():
        result = color_id_buffer._collect_near_face_w_data(
            doc, view=object(), raster=_raster(), cfg=object(),
            resolved_ids=[elem.Id], link_category_color_map={},
            diag=_FakeDiag(), view_id=42,
        )

    pre_step4 = {
        "bbox_corners_uv", "near_face_w", "category", "source",
        "category_state", "import_symbol_state", "view_specific_state",
    }
    assert pre_step4.issubset(set(result["host"]["1003"].keys()))
