"""A4: the VOP grid origin the model-view crop box is anchored to.

_prepare_view_for_export() needs Revit to run, so what is pinned here is the
part that decides WHETHER the model-view crop box gets re-anchored at all:
_grid_origin_uv(). Returning None from it silently selects the old
expand-only path, which cannot correct a translation -- so the failure mode
of this helper is an image that is the right size and in the wrong place,
which no dimension check catches.

Why the origin is not the crop box's own minimum, in three ways that all
reach a model view (vop_interwoven/revit/view_basis.py):
  :914        base_bounds is the crop box inflated by buffer_ft
  :1032-1036  annotation expansion moves the minimum outward
  :1056-1068  the annotation-clip recentring recomputes new_xmin/new_ymin
"""

from vop_interwoven.view_raster_export import _grid_origin_uv


class _Bounds(object):
    def __init__(self, xmin, ymin, xmax, ymax):
        self.xmin, self.ymin, self.xmax, self.ymax = xmin, ymin, xmax, ymax


class _Raster(object):
    def __init__(self, bounds):
        self.bounds_xy = bounds


def test_origin_from_a_live_raster_object():
    """Streaming mode puts the ViewRaster itself in the result dict."""
    assert _grid_origin_uv(
        {"raster": _Raster(_Bounds(-12.5, 7.25, 90.0, 60.0))}) == (-12.5, 7.25)


def test_origin_from_a_serialized_raster_dict():
    """Non-streaming mode puts raster.to_dict() there instead, whose
    bounds_xy is a plain dict (core/raster.py:2018-2023)."""
    assert _grid_origin_uv({
        "raster": {"bounds_xy": {"xmin": 3.0, "ymin": -4.0,
                                 "xmax": 103.0, "ymax": 46.0}},
    }) == (3.0, -4.0)


def test_origin_is_not_the_crop_minimum_when_the_grid_was_expanded():
    """The case the old code assumed away: a grid whose minimum sits outside
    the crop box's. The helper must report the GRID's, and the two must be
    distinguishable -- if this returned the crop's, every element in the
    exported image would be displaced by the difference."""
    grid_origin = _grid_origin_uv({"raster": _Raster(_Bounds(-5.0, -5.0, 95.0, 45.0))})
    crop_origin = (0.0, 0.0)
    assert grid_origin == (-5.0, -5.0)
    assert grid_origin != crop_origin


def test_missing_or_unusable_bounds_report_none_rather_than_a_guess():
    """None selects the expand-only path, which announces that it did not
    re-anchor. A fabricated origin would move the whole image instead."""
    assert _grid_origin_uv({}) is None
    assert _grid_origin_uv({"raster": None}) is None
    assert _grid_origin_uv({"raster": _Raster(None)}) is None
    assert _grid_origin_uv({"raster": {}}) is None
    assert _grid_origin_uv({"raster": {"bounds_xy": {"xmax": 1.0}}}) is None


def test_origin_is_coerced_to_float():
    """The values are handed straight into XYZ arithmetic; an int or a string
    from a round-tripped JSON result must not reach it untyped."""
    got = _grid_origin_uv({"raster": {"bounds_xy": {"xmin": 2, "ymin": "3.5",
                                                    "xmax": 9, "ymax": 9}}})
    assert got == (2.0, 3.5)
    assert all(isinstance(v, float) for v in got)


# --------------------------------------------------------------------------
# The crop box's own rectangle, in the frame raster.bounds_xy is expressed in.
# --------------------------------------------------------------------------

from vop_interwoven.view_raster_export import _crop_uv_frame


class _XYZ(object):
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = float(x), float(y), float(z)


class _Box(object):
    def __init__(self, mn, mx):
        self.Min, self.Max = mn, mx


def _frame(cb, origin, T=None, u=(1.0, 0.0, 0.0), v=(0.0, 1.0, 0.0)):
    return _crop_uv_frame(cb, T, _XYZ(*u), _XYZ(*v), origin, _XYZ)


_CB = _Box(_XYZ(10.0, 20.0, 0.0), _XYZ(110.0, 70.0, 0.0))


def test_origin_at_zero_projects_straight_through():
    umin, vmin, du, dv = _frame(_CB, _XYZ(0.0, 0.0, 0.0))
    assert (umin, vmin) == (10.0, 20.0)
    assert (du, dv) == (100.0, 50.0)


def test_minimum_is_relative_to_the_view_origin():
    """The regression. raster.bounds_xy is produced through
    ViewBasis.transform_to_view_uvw(), which projects (point - basis.origin)
    with basis.origin = view.Origin (view_basis.py:91-97, :151). A bare
    dot(world_pt, right) sits in a different frame, offset by
    dot(view.Origin, right) -- and an anchoring shift computed across the two
    carries that offset straight into the crop box.

    A section at (500, 300) is an ordinary case, not a contrived one, and the
    error here would be 500 ft on u.
    """
    umin, vmin, du, dv = _frame(_CB, _XYZ(500.0, 300.0, 0.0))
    assert (umin, vmin) == (10.0 - 500.0, 20.0 - 300.0)
    # The EXTENT is unaffected -- both terms are projections in the same
    # frame, so the origin cancels. That is exactly why this went unnoticed:
    # every size check passed.
    assert (du, dv) == (100.0, 50.0)


def test_extent_is_frame_independent():
    a = _frame(_CB, _XYZ(0.0, 0.0, 0.0))
    b = _frame(_CB, _XYZ(-7777.0, 31.5, 12.0))
    assert a[2:] == b[2:]
    assert a[:2] != b[:2]


def test_rotated_basis_projects_onto_the_view_axes():
    """A section whose RightDirection is not world +X. The origin's own
    projection has to be taken along the SAME axis, not along world X."""
    u, v = (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0)
    umin, vmin, du, dv = _frame(_CB, _XYZ(40.0, 60.0, 0.0), u=u, v=v)
    # u-axis is world +Y: crop spans 20..70, origin projects to 60.
    assert umin == 20.0 - 60.0
    assert du == 50.0
    # v-axis is world -X: crop spans -110..-10, origin projects to -40.
    assert vmin == -110.0 - (-40.0)
    assert dv == 100.0


def test_missing_view_origin_does_not_crash_and_does_not_re_anchor_wrongly():
    """A view object without .Origin yields None. Treated as a zero offset,
    which is the pre-A4 frame -- wrong to anchor from, but the caller's
    grid_origin_uv is what decides whether anchoring happens at all."""
    umin, vmin, du, dv = _frame(_CB, None)
    assert (umin, vmin) == (10.0, 20.0)
    assert (du, dv) == (100.0, 50.0)


def test_transform_is_applied_before_projection():
    """CropBox.Min/Max are in the box's LOCAL frame; a non-identity Transform
    has to reach the points before they are projected."""
    class _T(object):
        @staticmethod
        def OfPoint(p):
            return _XYZ(p.X + 1000.0, p.Y - 5.0, p.Z)

    umin, vmin, du, dv = _frame(_CB, _XYZ(0.0, 0.0, 0.0), T=_T)
    assert (umin, vmin) == (1010.0, 15.0)
    assert (du, dv) == (100.0, 50.0)
