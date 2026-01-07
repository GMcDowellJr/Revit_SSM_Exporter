import math

from vop_interwoven.revit.collection import _project_element_bbox_to_cell_rect
from vop_interwoven.revit.view_basis import ViewBasis
from vop_interwoven.core.math_utils import Bounds2D


class _P(object):
    __slots__ = ("X", "Y", "Z")
    def __init__(self, x, y, z):
        self.X = float(x)
        self.Y = float(y)
        self.Z = float(z)


class _BBox(object):
    __slots__ = ("Min", "Max")
    def __init__(self, mn, mx):
        self.Min = mn
        self.Max = mx


class _TransformZ(object):
    """Minimal transform stub with Revit-like OfPoint semantics."""
    def __init__(self, angle_deg, tx=0.0, ty=0.0, tz=0.0):
        ang = math.radians(angle_deg)
        self._c = math.cos(ang)
        self._s = math.sin(ang)
        self._t = (float(tx), float(ty), float(tz))

    def OfPoint(self, p):
        # Accept either tuple or _P
        try:
            x, y, z = p.X, p.Y, p.Z
        except Exception:
            x, y, z = p[0], p[1], p[2]

        xr = x * self._c - y * self._s
        yr = x * self._s + y * self._c
        zr = z

        return (xr + self._t[0], yr + self._t[1], zr + self._t[2])


class _Raster(object):
    def __init__(self, bounds, cell_size_ft=0.1, w=500, h=500, view_basis=None):
        self.bounds = bounds
        self.cell_size_ft = float(cell_size_ft)
        # Back-compat: collection._project_element_bbox_to_cell_rect expects raster.cell_size
        self.cell_size = self.cell_size_ft
        self.w = int(w)
        self.h = int(h)
        # Back-compat: some code expects raster.W/H
        self.W = self.w
        self.H = self.h
        self.view_basis = view_basis


def test_rotated_link_binning_uses_tight_uv_aabb_not_host_aabb():
    # Link-space bbox: a long thin rectangle aligned to link axes.
    bbox_link = _BBox(_P(0, 0, 0), _P(10, 1, 0))

    # Link is rotated 45° in host space.
    trf = _TransformZ(45.0)

    # View basis is rotated -45° so the element is axis-aligned in view UV.
    # If we collapse to host AABB first, the UV AABB stays fat.
    vb = ViewBasis(
        origin=(0, 0, 0),
        right=(math.cos(math.radians(-45)), math.sin(math.radians(-45)), 0),
        up=(-math.sin(math.radians(-45)), math.cos(math.radians(-45)), 0),
        forward=(0, 0, 1),
    )
    raster = _Raster(bounds=Bounds2D(0, 0, 50, 50), cell_size_ft=0.1, view_basis=vb)

    # Simulate legacy behavior: host-space AABB computed first (min/max over transformed corners).
    corners = [
        (bbox_link.Min.X, bbox_link.Min.Y, bbox_link.Min.Z),
        (bbox_link.Min.X, bbox_link.Max.Y, bbox_link.Min.Z),
        (bbox_link.Max.X, bbox_link.Min.Y, bbox_link.Min.Z),
        (bbox_link.Max.X, bbox_link.Max.Y, bbox_link.Min.Z),
    ]
    host_pts = [trf.OfPoint(c) for c in corners]
    xs = [p[0] for p in host_pts]
    ys = [p[1] for p in host_pts]
    host_bbox = _BBox(_P(min(xs), min(ys), 0), _P(max(xs), max(ys), 0))

    rect_host_aabb = _project_element_bbox_to_cell_rect(
        elem=None,
        vb=vb,
        raster=raster,
        bbox=host_bbox,
        transform=None,
        bbox_is_link_space=False,
    )

    rect_tight_uv = _project_element_bbox_to_cell_rect(
        elem=None,
        vb=vb,
        raster=raster,
        bbox=bbox_link,
        transform=trf,
        bbox_is_link_space=True,
    )

    assert rect_host_aabb is not None
    assert rect_tight_uv is not None

    # Tight-UV projection must be strictly better (smaller) than projecting a collapsed host AABB.
    assert rect_tight_uv.cell_count() < rect_host_aabb.cell_count()
