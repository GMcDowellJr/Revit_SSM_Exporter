"""The export path and the bounds path must express UV in the SAME frame.

THE DEFECT THIS ENCODES
-----------------------
`raster.bounds_xy` is produced by `xy_bounds_from_crop_box_all_corners()`
through `ViewBasis.transform_to_view_uvw()`, which projects
``point - basis.origin`` (view_basis.py:91-97), and `make_view_basis()` sets
``basis.origin = view.Origin`` (:151).

`view_raster_export.py` anchors the model-view crop box to that rectangle, and
computed the crop's own minimum as a bare ``dot(world_pt, RightDirection)`` --
a different frame, offset by ``dot(view.Origin, right)``.

It survived because an anchoring shift is a difference ACROSS the two frames
while every size check is a difference WITHIN one, where the origin cancels.
Both images measured exactly W*cs x H*cs ft and sat in different places.

So this file binds the two producers rather than testing either alone. A test
of `_crop_uv_frame` by itself would have been written from the same wrong model
as the code -- which is precisely what happened the first time.
"""
import math
import sys
from pathlib import Path

from hypothesis import given, settings, strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vop_interwoven.revit.view_basis import ViewBasis  # noqa: E402
from vop_interwoven.view_raster_export import _crop_uv_frame  # noqa: E402


class _XYZ(object):
    """Duck-typed Autodesk.Revit.DB.XYZ. No Revit in this process."""

    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = float(x), float(y), float(z)


class _Box(object):
    def __init__(self, mn, mx):
        self.Min, self.Max = mn, mx


class _Shift(object):
    """A CropBox.Transform: Min/Max are in the box's LOCAL frame, so a
    non-identity transform has to reach the corners before they are projected.
    Translation alone is enough to distinguish "applied" from "not applied"."""

    def __init__(self, dx, dy):
        self._dx, self._dy = dx, dy

    def OfPoint(self, p):
        return _XYZ(p.X + self._dx, p.Y + self._dy, p.Z)


COORD = st.floats(min_value=-5000.0, max_value=5000.0,
                  allow_nan=False, allow_infinity=False)
EXTENT = st.floats(min_value=0.5, max_value=2000.0,
                   allow_nan=False, allow_infinity=False)
ANGLE = st.floats(min_value=0.0, max_value=2.0 * math.pi,
                  allow_nan=False, allow_infinity=False)


def _basis_vectors(theta):
    """An orthonormal in-plane right/up pair. A section's basis differs from a
    plan's by exactly this rotation, which is what makes "project the origin
    onto the SAME axis" non-trivial: world X is not the u axis."""
    c, s = math.cos(theta), math.sin(theta)
    return (c, s, 0.0), (-s, c, 0.0)


def _reference_frame(corners_world, origin, right, up):
    """What the BOUNDS path computes, via the production ViewBasis."""
    vb = ViewBasis(origin, right, up, (0.0, 0.0, -1.0))
    uvs = [vb.transform_to_view_uvw(p)[:2] for p in corners_world]
    us = [uv[0] for uv in uvs]
    vs = [uv[1] for uv in uvs]
    return min(us), min(vs), max(us) - min(us), max(vs) - min(vs)


@settings(max_examples=400, deadline=None)
@given(ox=COORD, oy=COORD, x0=COORD, y0=COORD, du=EXTENT, dv=EXTENT,
       theta=ANGLE, tx=st.floats(-500.0, 500.0), ty=st.floats(-500.0, 500.0),
       use_transform=st.booleans())
def test_export_frame_agrees_with_the_bounds_frame(ox, oy, x0, y0, du, dv,
                                                   theta, tx, ty, use_transform):
    """The binding. Both paths must place the SAME crop box at the SAME UV
    minimum -- not merely give it the same size."""
    right, up = _basis_vectors(theta)
    origin = (ox, oy, 0.0)
    cb = _Box(_XYZ(x0, y0, 0.0), _XYZ(x0 + du, y0 + dv, 0.0))
    T = _Shift(tx, ty) if use_transform else None

    got = _crop_uv_frame(cb, T, _XYZ(*right), _XYZ(*up), _XYZ(*origin), _XYZ)

    corners = []
    for lx in (cb.Min.X, cb.Max.X):
        for ly in (cb.Min.Y, cb.Max.Y):
            p = _XYZ(lx, ly, cb.Min.Z)
            p = T.OfPoint(p) if T is not None else p
            corners.append((p.X, p.Y, p.Z))
    want = _reference_frame(corners, origin, right, up)

    for label, a, b in zip(("umin", "vmin", "u_extent", "v_extent"), got, want):
        assert math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-7), (
            "{0}: export path {1!r} vs bounds path {2!r} "
            "(origin=({3}, {4}), theta={5})".format(label, a, b, ox, oy, theta))


@settings(max_examples=250, deadline=None)
@given(ox=COORD, oy=COORD, x0=COORD, y0=COORD, du=EXTENT, dv=EXTENT, theta=ANGLE)
def test_extent_is_frame_independent_but_the_minimum_is_not(ox, oy, x0, y0,
                                                            du, dv, theta):
    """Why every size check passed while the crop sat in the wrong place.

    Moving the view origin must leave the EXTENT untouched -- it is a
    difference within one frame -- and must move the MINIMUM by exactly the
    origin's projection onto that axis. A test that only ever compared extents
    could not have seen the defect, and that is the shape of every check this
    code had.
    """
    right, up = _basis_vectors(theta)
    cb = _Box(_XYZ(x0, y0, 0.0), _XYZ(x0 + du, y0 + dv, 0.0))
    at_zero = _crop_uv_frame(cb, None, _XYZ(*right), _XYZ(*up),
                             _XYZ(0.0, 0.0, 0.0), _XYZ)
    moved = _crop_uv_frame(cb, None, _XYZ(*right), _XYZ(*up),
                           _XYZ(ox, oy, 0.0), _XYZ)

    assert math.isclose(at_zero[2], moved[2], rel_tol=1e-9, abs_tol=1e-9)
    assert math.isclose(at_zero[3], moved[3], rel_tol=1e-9, abs_tol=1e-9)

    proj_u = ox * right[0] + oy * right[1]
    proj_v = ox * up[0] + oy * up[1]
    assert math.isclose(moved[0], at_zero[0] - proj_u, rel_tol=1e-9, abs_tol=1e-7)
    assert math.isclose(moved[1], at_zero[1] - proj_v, rel_tol=1e-9, abs_tol=1e-7)
