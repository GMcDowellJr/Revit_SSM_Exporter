"""C3: the model record keeps the AABB always, and ALSO the oriented box --
only when the bbox's transform rotates it.

Confirmed in code before writing (main @ 8e6a34f): ``bbox_3d`` and the UV
footprint are both AABBs of ``_bbox_world_corners()``'s Transform-applied
corners. No OBB path feeds the Stage A record. So for a rotating transform the
AABB is an upper bound and the orientation was lost; C3 persists
``bbox_transform`` (origin, three basis vectors, and the bbox-local Min/Max)
exactly when that loss happens.

The bbox-local Min/Max are part of the record because the transform plus the
AABB do NOT determine the box -- pinned below by a counterexample rather than
argued.
"""
import contextlib
import math
import sys
import types

import pytest
from hypothesis import given, settings, strategies as st

from vop_interwoven import color_id_buffer
from vop_interwoven.revit.collection import (
    BBOX_AXIS_ALIGNED_TOL, bbox_oriented_transform, bbox_world_aabb,
)
from vop_interwoven.revit.view_basis import ViewBasis
from tools import capture_overlay as co
from tools.link_identity_resolver import _uv_rect_to_pixel_bbox
from tools.stage_a_sidecar_shapes import (
    model_entry_bbox_3d, model_entry_uv, model_entry_uv_corners, rect_to_corners,
)


class _P:
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = float(x), float(y), float(z)


class _Trf:
    """A rigid Revit-like Transform: the attributes C3 reads, and OfPoint on
    plain tuples (the stub path _bbox_world_corners documents)."""
    def __init__(self, origin, bx, by, bz):
        self._m = (_P(*origin), _P(*bx), _P(*by), _P(*bz))
        self.Origin, self.BasisX, self.BasisY, self.BasisZ = self._m

    def OfPoint(self, p):
        o, x, y, z = self._m
        return (o.X + p[0] * x.X + p[1] * y.X + p[2] * z.X,
                o.Y + p[0] * x.Y + p[1] * y.Y + p[2] * z.Y,
                o.Z + p[0] * x.Z + p[1] * y.Z + p[2] * z.Z)


def _rot_z(deg, origin=(0.0, 0.0, 0.0)):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return _Trf(origin, (c, s, 0.0), (-s, c, 0.0), (0.0, 0.0, 1.0))


class _BBox:
    def __init__(self, mn, mx, transform=None):
        self.Min, self.Max = _P(*mn), _P(*mx)
        if transform is not None:
            self.Transform = transform


class _Id:
    def __init__(self, v):
        self.IntegerValue = v


class _Cat:
    def __init__(self, name):
        self.Name = name
        self.Id = _Id(10)


class _Elem:
    def __init__(self, eid, bbox):
        self.Id = _Id(eid)
        self.Category = _Cat("Walls")
        self._bbox = bbox

    def get_BoundingBox(self, _view):
        return self._bbox


class _Doc:
    def __init__(self, elems):
        self._by = {e.Id.IntegerValue: e for e in elems}

    def GetElement(self, eid):
        return self._by.get(eid.IntegerValue)


class _Proxy:
    """A LinkedElementProxy's surface: a host-space AABB, the linked element,
    and the link transform."""
    source_type = "LINK"

    def __init__(self, elem_id, element, link_trf, host_bbox):
        self.Id = _Id(elem_id)
        self.LinkInstanceId = _Id(900)
        self.Category = _Cat("Walls")
        self.element = element
        self.transform = link_trf
        self._bb = host_bbox

    def get_BoundingBox(self, _view):
        return self._bb


class _Diag:
    def __init__(self):
        self.warnings, self.errors = [], []

    def warn(self, **k):
        self.warnings.append(k)

    def error(self, **k):
        self.errors.append(k)

    def info(self, **k):
        pass


@contextlib.contextmanager
def _fake_db():
    fake = types.ModuleType("Autodesk.Revit.DB")
    original = sys.modules.get("Autodesk.Revit.DB")
    sys.modules["Autodesk.Revit.DB"] = fake
    try:
        yield
    finally:
        if original is None:
            sys.modules.pop("Autodesk.Revit.DB", None)
        else:
            sys.modules["Autodesk.Revit.DB"] = original


_RASTER = types.SimpleNamespace(
    view_basis=ViewBasis(origin=(0, 0, 0), right=(1, 0, 0), up=(0, 1, 0),
                         forward=(0, 0, -1)),
    bounds_xy=None)


def _collect(doc, ids, proxies=(), link_map=None, diag=None, monkeypatch=None):
    monkeypatch.setattr(
        "vop_interwoven.revit.linked_documents.collect_all_linked_elements",
        lambda *a, **k: list(proxies))
    with _fake_db():
        return color_id_buffer._collect_near_face_w_data(
            doc, view=object(), raster=_RASTER, cfg=object(), resolved_ids=ids,
            link_category_color_map=link_map or {}, diag=diag, view_id=7,
            host_source_types={i.IntegerValue: "HOST" for i in ids})


def _obb_corners(rec):
    """The eight world corners of the recorded oriented box."""
    trf = _Trf(rec["origin"], rec["basis_x"], rec["basis_y"], rec["basis_z"])
    (x0, y0, z0), (x1, y1, z1) = rec["min"], rec["max"]
    return [trf.OfPoint((x, y, z)) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]


def _aabb_of(points):
    return ([min(p[k] for p in points) for k in range(3)],
            [max(p[k] for p in points) for k in range(3)])


# --- when nothing extra is stored -------------------------------------------

@pytest.mark.parametrize("bbox", [
    _BBox((0, 0, 0), (2, 1, 3)),                                      # no Transform
    _BBox((0, 0, 0), (2, 1, 3), _Trf((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1))),
    _BBox((0, 0, 0), (2, 1, 3), _Trf((5, -7, 2), (1, 0, 0), (0, 1, 0), (0, 0, 1))),
    _BBox((0, 0, 0), (2, 1, 3), _rot_z(90)),                          # quarter turn
    _BBox((0, 0, 0), (2, 1, 3), _Trf((0, 0, 0), (-1, 0, 0), (0, -1, 0), (0, 0, 1))),
    # Within the 1e-9 tolerance: still axis-aligned.
    _BBox((0, 0, 0), (2, 1, 3), _Trf((0, 0, 0), (1, 5e-10, 0), (-5e-10, 1, 0), (0, 0, 1))),
], ids=["none", "identity", "translation", "rot90", "flip", "within_tol"])
def test_axis_aligned_transform_stores_nothing(bbox):
    assert bbox_oriented_transform(bbox) is None


def test_just_past_the_tolerance_is_rotated():
    tilt = 2 * BBOX_AXIS_ALIGNED_TOL
    bbox = _BBox((0, 0, 0), (1, 1, 1), _Trf((0, 0, 0), (1, tilt, 0), (-tilt, 1, 0), (0, 0, 1)))
    assert bbox_oriented_transform(bbox) is not None


# --- when it is stored, it recovers the box and composes with the AABB -------

def test_rotated_record_carries_transform_and_local_box():
    bbox = _BBox((-1, -0.5, 0), (1, 0.5, 3), _rot_z(30, origin=(10, 20, 0)))
    rec = bbox_oriented_transform(bbox)
    assert rec["origin"] == pytest.approx([10, 20, 0])
    assert rec["basis_x"] == pytest.approx([math.cos(math.radians(30)), 0.5, 0])
    assert rec["min"] == [-1, -0.5, 0] and rec["max"] == [1, 0.5, 3]


@settings(max_examples=150, deadline=None)
@given(
    yaw=st.floats(-179.0, 179.0), pitch=st.floats(-80.0, 80.0),
    ox=st.floats(-1e3, 1e3), oy=st.floats(-1e3, 1e3), oz=st.floats(-50, 50),
    dx=st.floats(0.01, 50), dy=st.floats(0.01, 50), dz=st.floats(0.01, 50),
)
def test_recorded_oriented_box_reproduces_the_recorded_aabb(yaw, pitch, ox, oy, oz, dx, dy, dz):
    """Composed, not asserted apart: the OBB C3 writes, pushed back through
    its own transform, spans exactly the AABB bbox_world_aabb() writes."""
    cy, sy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    cp, sp = math.cos(math.radians(pitch)), math.sin(math.radians(pitch))
    # yaw about Z, then pitch about the new X: a rigid rotation.
    bx = (cy, sy, 0.0)
    by = (-sy * cp, cy * cp, sp)
    bz = (sy * sp, -cy * sp, cp)
    bbox = _BBox((0, 0, 0), (dx, dy, dz), _Trf((ox, oy, oz), bx, by, bz))
    aabb = bbox_world_aabb(bbox)
    rec = bbox_oriented_transform(bbox)
    if rec is None:  # only possible when the draw landed axis-aligned
        return
    mn, mx = _aabb_of(_obb_corners(rec))
    assert mn == pytest.approx(aabb["min"], abs=1e-9)
    assert mx == pytest.approx(aabb["max"], abs=1e-9)


def test_transform_and_aabb_alone_do_not_determine_the_box():
    """Why the record carries the local Min/Max: at 45 degrees about Z, a
    2 x 0 footprint and a 1 x 1 one (both about the origin) have the SAME
    AABB. Persisting only the transform would not let a reader tell them
    apart."""
    t = _rot_z(45)
    long_thin = _BBox((-1.0, -1e-12, 0), (1.0, 1e-12, 1), t)
    square = _BBox((-0.5, -0.5, 0), (0.5, 0.5, 1), t)
    a, b = bbox_world_aabb(long_thin), bbox_world_aabb(square)
    assert a["min"] == pytest.approx(b["min"], abs=1e-9)
    assert a["max"] == pytest.approx(b["max"], abs=1e-9)
    assert bbox_oriented_transform(long_thin)["max"] != bbox_oriented_transform(square)["max"]


def test_link_orientation_is_the_link_transform_after_the_element_transform():
    elem_bbox = _BBox((0, 0, 0), (4, 1, 1), _rot_z(20))
    link = _rot_z(25, origin=(100, 0, 0))
    rec = bbox_oriented_transform(elem_bbox, outer_transform=link)
    expected = _rot_z(45, origin=(100, 0, 0))
    assert rec["origin"] == pytest.approx([100, 0, 0])
    assert rec["basis_x"] == pytest.approx([expected.BasisX.X, expected.BasisX.Y, 0], abs=1e-12)
    # And the pair cancels to axis-aligned when it should.
    assert bbox_oriented_transform(_BBox((0, 0, 0), (1, 1, 1), _rot_z(30)),
                                   outer_transform=_rot_z(-30)) is None


def test_unreadable_transform_raises_rather_than_reading_as_unrotated():
    class _Broken:
        @property
        def Origin(self):
            raise RuntimeError("transform read failed")
    with pytest.raises(RuntimeError):
        bbox_oriented_transform(_BBox((0, 0, 0), (1, 1, 1), _Broken()))


# --- through the writer ------------------------------------------------------

def test_writer_emits_bbox_transform_only_for_the_rotated_host_element(monkeypatch):
    rotated = _Elem(1, _BBox((0, 0, 0), (4, 1, 1), _rot_z(30, origin=(5, 5, 0))))
    plain = _Elem(2, _BBox((0, 0, 0), (4, 1, 1), _rot_z(90, origin=(5, 5, 0))))
    out = _collect(_Doc([rotated, plain]), [rotated.Id, plain.Id],
                   monkeypatch=monkeypatch)["host"]
    assert "bbox_transform" in out["1"]
    assert "bbox_transform" not in out["2"]
    # The AABB is kept whatever the orientation (C3: "keep AABB always").
    assert set(out["1"]["bbox_3d"]) == {"min", "max"}
    assert len(out["1"]["uv_rect"]) == 4
    # Fixture count, reported by the commit: 1 of 2 host elements rotated.
    assert sum("bbox_transform" in e for e in out.values()) == 1


def test_writer_records_a_failed_transform_read_as_unavailable(monkeypatch):
    class _HalfBroken(_Trf):
        @property
        def BasisY(self):
            raise RuntimeError("BasisY read failed")

        @BasisY.setter
        def BasisY(self, _v):
            pass

    elem = _Elem(1, _BBox((0, 0, 0), (1, 1, 1),
                          _HalfBroken((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1))))
    diag = _Diag()
    entry = _collect(_Doc([elem]), [elem.Id], diag=diag, monkeypatch=monkeypatch)["host"]["1"]
    assert entry["bbox_transform"]["state"] == "unavailable"
    assert "BasisY read failed" in entry["bbox_transform"]["reason"]
    # The AABB, which does not need the basis, survives.
    assert entry["uv_rect"] == [0, 0, 1, 1]
    assert any(w["callsite"] == "near_face_w.bbox_transform" for w in diag.warnings)


def test_writer_link_entry_under_a_rotated_link(monkeypatch):
    linked_elem = _Elem(501, _BBox((0, 0, 0), (4, 1, 1)))
    link = _rot_z(30, origin=(50, 0, 0))
    host_corners = [link.OfPoint((x, y, z)) for x in (0, 4) for y in (0, 1) for z in (0, 1)]
    mn, mx = _aabb_of(host_corners)
    proxy = _Proxy(501, linked_elem, link, _BBox(mn, mx))
    out = _collect(_Doc([]), [], proxies=[proxy], link_map={"Walls": [1, 2, 3]},
                   monkeypatch=monkeypatch)["link"]["900:501"]
    rec = out["bbox_transform"]
    got_mn, got_mx = _aabb_of(_obb_corners(rec))
    assert got_mn == pytest.approx(out["bbox_3d"]["min"], abs=1e-9)
    assert got_mx == pytest.approx(out["bbox_3d"]["max"], abs=1e-9)


def test_writer_link_entry_whose_element_cannot_be_reread(monkeypatch):
    proxy = _Proxy(501, None, _rot_z(30), _BBox((0, 0, 0), (1, 1, 1)))
    out = _collect(_Doc([]), [], proxies=[proxy], link_map={"Walls": [1, 2, 3]},
                   diag=_Diag(), monkeypatch=monkeypatch)["link"]["900:501"]
    assert out["bbox_transform"]["state"] == "unavailable"


# --- readers accept both shapes ---------------------------------------------

def test_readers_give_the_same_corners_for_new_and_archive_shapes(monkeypatch):
    elem = _Elem(1, _BBox((2, 3, 0), (6, 9, 1)))
    new = _collect(_Doc([elem]), [elem.Id], monkeypatch=monkeypatch)["host"]["1"]
    old = dict(new)
    del old["uv_rect"]
    old["bbox_corners_uv"] = rect_to_corners(new["uv_rect"])
    old["bbox_3d"] = {"state": "value", "value": new["bbox_3d"]}

    assert model_entry_uv(new) == model_entry_uv(old) == ("value", [[2, 3], [6, 3], [6, 9], [2, 9]])
    assert model_entry_bbox_3d(new) == model_entry_bbox_3d(old)

    bounds = (0.0, 0.0, 10.0, 10.0)
    assert (_uv_rect_to_pixel_bbox(model_entry_uv_corners(new), bounds, 100, 100)
            == _uv_rect_to_pixel_bbox(model_entry_uv_corners(old), bounds, 100, 100))

    for entry in (new, old):
        sidecar = {"near_face_w_map": {"host": {"1": entry}, "link": {}}}
        _kind, records = co.read_records(sidecar)
        assert records[0]["bbox_uv"] == [[2, 3], [6, 3], [6, 9], [2, 9]]


def test_reader_keeps_a_failed_projection_reason(monkeypatch):
    elem = _Elem(1, None)
    new = _collect(_Doc([elem]), [elem.Id], monkeypatch=monkeypatch)["host"]["1"]
    state, reason = model_entry_uv(new)
    assert state == "unavailable" and "no bbox" in reason
    assert model_entry_uv({"bbox_corners_uv": None})[0] == "unavailable"
