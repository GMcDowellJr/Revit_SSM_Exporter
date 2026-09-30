"""R1: the element's own rotation, recorded per near_face_w_map entry ONLY
when it is rotated (or the read failed), with the read's cost on the capture.

Why: C3's ``bbox_transform`` reads the bbox's Transform, which Revit returns
as identity for an element's bbox -- on pipeline_0930_0739 it fired on 0 of
~26,000 entries. The rotation lives on the element: a FamilyInstance's
GetTransform(), a straight LocationCurve's direction, or -- for an element
with neither inside a rotated link -- the link transform.
"""
import json
import math

import pytest

from tests.test_stage_a_oriented_bbox import (
    _BBox, _Diag, _Doc, _Elem, _Proxy, _Trf, _collect, _rot_z,
)
from vop_interwoven.color_id_buffer import BASIS_DECIMALS
from vop_interwoven.revit.collection import element_rotation


class _Instance(_Elem):
    """A FamilyInstance: GetTransform()."""
    def __init__(self, eid, trf):
        _Elem.__init__(self, eid, _BBox((0, 0, 0), (1, 1, 1)))
        self._trf = trf

    def GetTransform(self):
        return self._trf


class _XYZ:
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = float(x), float(y), float(z)


class Line:
    """Named like Autodesk.Revit.DB.Line: element_rotation keys off the name."""
    def __init__(self, p0, p1):
        self._p = (_XYZ(*p0), _XYZ(*p1))

    def GetEndPoint(self, i):
        return self._p[i]


class Arc(Line):
    pass


class _Located(_Elem):
    """A wall-like element: Location.Curve."""
    def __init__(self, eid, curve):
        _Elem.__init__(self, eid, _BBox((0, 0, 0), (1, 1, 1)))
        self.Location = type("LocationCurve", (), {"Curve": curve})()


# --- the reader --------------------------------------------------------------

def test_instance_rotated_30_is_recorded_and_90_or_0_is_not():
    rec = element_rotation(_Instance(1, _rot_z(30, origin=(5, 6, 0))))
    assert rec["source"] == "instance_transform"
    assert rec["origin"] == [5.0, 6.0, 0.0]
    assert rec["basis_x"] == pytest.approx([math.cos(math.radians(30)),
                                            math.sin(math.radians(30)), 0.0])
    # A quarter turn is axis-aligned: the AABB already shows it exactly.
    assert element_rotation(_Instance(2, _rot_z(90))) is None
    assert element_rotation(_Instance(3, _rot_z(0))) is None


def test_straight_location_line_gives_its_unit_direction():
    rec = element_rotation(_Located(1, Line((0, 0, 0), (3, 3, 0))))
    assert rec == {"source": "location_line",
                   "direction": pytest.approx([2 ** -0.5, 2 ** -0.5, 0.0])}
    # None is AXIS-ALIGNED, and only that (R2).
    assert element_rotation(_Located(2, Line((0, 0, 0), (0, 7, 0)))) is None
    # Zero length has no direction; an arc has no single one; a floor (no
    # Location, no GetTransform) has none: each SAYS so (R2), where it used to
    # be None and indistinguishable from axis-aligned.
    assert element_rotation(_Located(3, Line((1, 1, 0), (1, 1, 0)))) == {
        "state": "no_single_rotation", "reason": "zero_length_line"}
    assert element_rotation(_Located(4, Arc((0, 0, 0), (3, 3, 0)))) == {
        "state": "no_single_rotation", "reason": "curve_not_line"}
    assert element_rotation(_Elem(5, _BBox((0, 0, 0), (1, 1, 1)))) == {
        "state": "no_single_rotation", "reason": "no_location"}


def test_link_transform_composes_onto_both_sources_and_stands_alone():
    link = _rot_z(30, origin=(50, 0, 0))
    inst = element_rotation(_Instance(1, _rot_z(0, origin=(1, 0, 0))), outer_transform=link)
    assert inst["source"] == "instance_transform"
    assert inst["origin"] == pytest.approx([50 + math.cos(math.radians(30)),
                                            math.sin(math.radians(30)), 0.0])
    # An instance turned -30 inside a link turned +30 is not rotated in host space.
    assert element_rotation(_Instance(2, _rot_z(-30)), outer_transform=link) is None
    wall = element_rotation(_Located(3, Line((0, 0, 0), (1, 0, 0))), outer_transform=link)
    assert wall["direction"] == pytest.approx([math.cos(math.radians(30)),
                                               math.sin(math.radians(30)), 0.0])
    floor = element_rotation(_Elem(4, _BBox((0, 0, 0), (1, 1, 1))), outer_transform=link)
    assert floor["source"] == "link_transform"
    assert floor["origin"] == [50.0, 0.0, 0.0]
    # A curved element inside a rotated link keeps link_transform (R2).
    arc = element_rotation(_Located(6, Arc((0, 0, 0), (3, 3, 0))), outer_transform=link)
    assert arc["source"] == "link_transform"
    # A link that is not rotated gives an unrotated floor no rotation: it
    # has no single one (R2).
    assert element_rotation(_Elem(5, _BBox((0, 0, 0), (1, 1, 1))),
                            outer_transform=_rot_z(180, origin=(9, 9, 0))) == {
        "state": "no_single_rotation", "reason": "no_location"}


def test_an_unreadable_transform_raises_rather_than_reading_as_unrotated():
    class _Broken(_Instance):
        def GetTransform(self):
            raise RuntimeError("GetTransform failed")
    with pytest.raises(RuntimeError):
        element_rotation(_Broken(1, None))


# --- the writer --------------------------------------------------------------

def test_writer_records_rotation_only_when_rotated_and_times_the_read(monkeypatch):
    from vop_interwoven.color_id_buffer import _new_rotation_stats
    import vop_interwoven.color_id_buffer as cib
    stats = _new_rotation_stats()
    rotated = _Instance(1, _rot_z(30))
    square = _Instance(2, _rot_z(90))
    wall = _Located(3, Line((0, 0, 0), (1, 2, 0)))
    orig = cib._collect_near_face_w_data

    def _with_stats(*a, **k):
        k["rotation_stats"] = stats
        return orig(*a, **k)
    monkeypatch.setattr(cib, "_collect_near_face_w_data", _with_stats)
    out = _collect(_Doc([rotated, square, wall]), [rotated.Id, square.Id, wall.Id],
                   monkeypatch=monkeypatch)["host"]
    assert out["1"]["rotation"]["source"] == "instance_transform"
    assert "rotation" not in out["2"]
    assert out["3"]["rotation"]["source"] == "location_line"
    # Rounded: unit vectors at BASIS_DECIMALS.
    for c in out["3"]["rotation"]["direction"]:
        assert c == round(c, BASIS_DECIMALS)
    assert stats["elements_read"] == 3
    assert stats["rotated"] == 2
    assert stats["unavailable"] == 0
    assert stats["elapsed_ms"] >= 0.0


def test_writer_records_a_failed_rotation_read_as_unavailable(monkeypatch):
    class _Broken(_Instance):
        def GetTransform(self):
            raise RuntimeError("GetTransform failed")
    diag = _Diag()
    out = _collect(_Doc([_Broken(1, None)]), [_Broken(1, None).Id], diag=diag,
                   monkeypatch=monkeypatch)["host"]["1"]
    assert out["rotation"]["state"] == "unavailable"
    assert "GetTransform failed" in out["rotation"]["reason"]
    assert any(w["callsite"] == "near_face_w.rotation" for w in diag.warnings)


def test_writer_link_entry_takes_the_link_rotation(monkeypatch):
    floor = _Elem(501, _BBox((0, 0, 0), (1, 1, 1)))
    proxy = _Proxy(501, floor, _rot_z(30), _BBox((0, 0, 0), (1, 1, 1)))
    out = _collect(_Doc([]), [], proxies=[proxy], link_map={"Walls": [1, 2, 3]},
                   monkeypatch=monkeypatch)["link"]["900:501"]
    assert out["rotation"]["source"] == "link_transform"
    lost = _Proxy(502, None, _rot_z(30), _BBox((0, 0, 0), (1, 1, 1)))
    out = _collect(_Doc([]), [], proxies=[lost], link_map={"Walls": [1, 2, 3]},
                   diag=_Diag(), monkeypatch=monkeypatch)["link"]["900:502"]
    assert out["rotation"]["state"] == "unavailable"


def test_the_sidecar_file_carries_rotation_read_and_the_entry(tmp_path):
    """Defect class 4: assert the FILE. The control is the unrotated element,
    which must carry no "rotation" key at all."""
    from tests.test_color_id_buffer_view_graphics_state import (
        FakeDiag, _cfg, _export, _furnished_world, _raster,
    )
    doc, view, elements = _furnished_world()
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    elements[0].GetTransform = lambda: _Trf((1, 2, 0), (c, s, 0), (-s, c, 0), (0, 0, 1))
    result = _export(doc, view, elements, _cfg(tmp_path), FakeDiag(), _raster())
    with open(result["sidecar_path"]) as f:
        on_disk = json.load(f)
    host = on_disk["near_face_w_map"]["host"]
    assert host["1001"]["rotation"]["source"] == "instance_transform"
    # R2: 1002 has no Location and no GetTransform -- no single rotation, said
    # so in the file rather than left absent like an axis-aligned element.
    assert host["1002"]["rotation"] == {"state": "no_single_rotation",
                                        "reason": "no_location"}
    read = on_disk["rotation_read"]
    assert read["elements_read"] == 2 and read["rotated"] == 1
    assert read["unavailable"] == 0
    assert read["no_single_rotation"] == 1
    assert read["no_single_rotation_by_reason"] == {"no_location": 1}
    assert isinstance(read["elapsed_ms"], float)


def test_r2_axis_aligned_and_no_single_rotation_differ_in_the_FILE(tmp_path):
    """R2's gate: an axis-aligned instance writes NO rotation key (the
    control), an arc-located element writes the state, counted by reason."""
    from tests.test_color_id_buffer_view_graphics_state import (
        FakeDiag, _cfg, _export, _furnished_world, _raster,
    )
    doc, view, elements = _furnished_world()
    elements[0].GetTransform = lambda: _rot_z(90)
    elements[1].Location = type("LocationCurve", (), {"Curve": Arc((0, 0, 0), (3, 3, 0))})()
    result = _export(doc, view, elements, _cfg(tmp_path), FakeDiag(), _raster())
    with open(result["sidecar_path"]) as f:
        on_disk = json.load(f)
    host = on_disk["near_face_w_map"]["host"]
    assert "rotation" not in host["1001"]
    assert host["1002"]["rotation"] == {"state": "no_single_rotation",
                                        "reason": "curve_not_line"}
    read = on_disk["rotation_read"]
    assert read["rotated"] == 0 and read["no_single_rotation"] == 1
    assert read["no_single_rotation_by_reason"] == {"curve_not_line": 1}
