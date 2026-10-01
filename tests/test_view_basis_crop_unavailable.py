"""E: a crop that cannot be read is UNAVAILABLE, never a made-up rectangle.

xy_bounds_from_crop_box_all_corners used to answer an AttributeError (no
CropBox, or a corner that would not project) with a +/-100 ft rectangle --
returned as if it were the view's crop, so resolve_view_bounds recorded it with
reason "crop" and confidence "high". It now records the failure in Diagnostics
and raises; both callers fall back to extents and say so.

Mutation: restoring the +/-100 ft return turns the first two tests red; the
control (a real crop) stays green either way.
"""
import pytest

from vop_interwoven.core.diagnostics import Diagnostics
from vop_interwoven.core.math_utils import Bounds2D
from vop_interwoven.revit.view_basis import (
    resolve_view_bounds, xy_bounds_from_crop_box_all_corners,
)

from tests.stage_a_capture_fakes import (
    FakeBoundingBoxXYZ, FakeXYZ, install_fake_revit_db,
)


class _Basis(object):
    def transform_to_view_uv(self, p):
        return (float(p[0]), float(p[1]))


class _View(object):
    def __init__(self, crop_box):
        self.CropBox = crop_box
        self.CropBoxActive = True
        self.Scale = 96
        self.Name = "V"
        self.Id = type("I", (object,), {"IntegerValue": 77})()


def _errors(diag):
    return [e for e in diag.events
            if e.get("callsite") == "xy_bounds_from_crop_box_all_corners"]


def test_a_view_with_no_crop_box_raises_and_is_recorded():
    diag = Diagnostics()
    with pytest.raises(AttributeError):
        xy_bounds_from_crop_box_all_corners(_View(None), _Basis(), diag=diag, view_id=77)
    errs = _errors(diag)
    assert len(errs) == 1 and errs[0]["phase"] == "bounds" and errs[0]["view_id"] == 77


def test_resolve_view_bounds_falls_back_to_extents_not_to_an_invented_crop():
    diag = Diagnostics()
    r = resolve_view_bounds(
        _View(None), diag=diag,
        policy={"cell_size_ft": 1.0, "buffer_ft": 0.0, "max_W": 999, "max_H": 999,
                "doc": object(), "basis": _Basis(),
                "bounds_extents_fn": lambda _v, _p: Bounds2D(3, 4, 13, 24)})
    assert r["reason"] != "crop"
    assert (r["bounds_uv"].xmin, r["bounds_uv"].ymin,
            r["bounds_uv"].xmax, r["bounds_uv"].ymax) == (3, 4, 13, 24)
    assert len(_errors(diag)) == 1


def test_control_a_readable_crop_is_projected():
    box = FakeBoundingBoxXYZ()
    box.Min, box.Max = FakeXYZ(1, 2, 0), FakeXYZ(11, 22, 0)
    diag = Diagnostics()
    with install_fake_revit_db():
        b = xy_bounds_from_crop_box_all_corners(_View(box), _Basis(), diag=diag)
    assert (b.xmin, b.ymin, b.xmax, b.ymax) == (1, 2, 11, 22)
    assert _errors(diag) == []
