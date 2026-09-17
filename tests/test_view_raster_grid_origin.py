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
