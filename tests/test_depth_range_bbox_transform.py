from vop_interwoven.revit.collection import estimate_depth_range_from_bbox
from vop_interwoven.revit.view_basis import ViewBasis


class _P(object):
    __slots__ = ("X", "Y", "Z")

    def __init__(self, x, y, z):
        self.X = float(x)
        self.Y = float(y)
        self.Z = float(z)


class _BBox(object):
    __slots__ = ("Min", "Max", "Transform")

    def __init__(self, mn, mx, transform=None):
        self.Min = mn
        self.Max = mx
        self.Transform = transform


class _Translate(object):
    def __init__(self, dx=0.0, dy=0.0, dz=0.0, reject_tuple=False):
        self.dx = float(dx)
        self.dy = float(dy)
        self.dz = float(dz)
        self.reject_tuple = bool(reject_tuple)
        self.Origin = _P(0.0, 0.0, 0.0)

    def OfPoint(self, p):
        if self.reject_tuple and not hasattr(p, "X"):
            raise TypeError("expected XYZ-like point")
        try:
            x, y, z = p.X, p.Y, p.Z
        except Exception:
            x, y, z = p[0], p[1], p[2]
        return _P(x + self.dx, y + self.dy, z + self.dz)


class _Raster(object):
    def __init__(self):
        self.view_basis = ViewBasis(
            origin=(0.0, 0.0, 0.0),
            right=(1.0, 0.0, 0.0),
            up=(0.0, 1.0, 0.0),
            forward=(0.0, 0.0, 1.0),
        )


def test_depth_range_applies_bbox_transform_before_view_w():
    bbox = _BBox(_P(0, 0, 0), _P(1, 1, 1), transform=_Translate(dz=5, reject_tuple=True))

    assert estimate_depth_range_from_bbox(None, None, None, _Raster(), bbox=bbox) == (5.0, 6.0)


def test_depth_range_applies_link_transform_after_bbox_transform():
    bbox = _BBox(_P(0, 0, 0), _P(1, 1, 1), transform=_Translate(dz=5, reject_tuple=True))
    link_transform = _Translate(dz=10, reject_tuple=True)

    assert estimate_depth_range_from_bbox(
        None,
        link_transform,
        None,
        _Raster(),
        bbox=bbox,
        bbox_is_link_space=True,
    ) == (15.0, 16.0)
