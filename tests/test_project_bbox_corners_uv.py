"""Unit tests for vop_interwoven/revit/collection.py's project_bbox_corners_uv
and project_bbox_uv_and_near_face_w (Phase 1b: near-face-W collection
prerequisites).

Pure-Python: uses the same plan-view ViewBasis convention as
tests/test_color_id_buffer_link_category_filters.py's _PLAN_BASIS, so world
(x, y) maps directly to view (u, v) and expected numbers stay simple, and
the same lightweight bbox stub tests/test_bbox_policy.py already uses (no
Transform attribute), so no Autodesk.Revit.DB import is ever reached.
"""
from vop_interwoven.revit.collection import project_bbox_corners_uv, project_bbox_uv_and_near_face_w
from vop_interwoven.revit.view_basis import ViewBasis, world_to_view


class _P:
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = x, y, z


class _BBox:
    def __init__(self, mn, mx):
        self.Min = _P(*mn)
        self.Max = _P(*mx)


# Plan view looking down +Z, basis aligned to world XY -- world (x, y) maps
# directly to view (u, v).
_PLAN_BASIS = ViewBasis(origin=(0, 0, 0), right=(1, 0, 0), up=(0, 1, 0), forward=(0, 0, -1))


class _FakeTransform:
    """Minimal Transform stand-in: OfPoint offsets a plain (x, y, z) tuple by
    a fixed vector and returns a tuple -- the test-stub-friendly fast path
    project_bbox_corners_uv tries before falling back to a real
    Autodesk.Revit.DB.XYZ round-trip (which real Revit's Transform.OfPoint
    always requires, since it rejects a raw tuple argument)."""
    def __init__(self, dx, dy, dz):
        self._d = (dx, dy, dz)

    def OfPoint(self, pt):
        x, y, z = pt
        dx, dy, dz = self._d
        return (x + dx, y + dy, z + dz)


def test_project_bbox_corners_uv_host_space_aabb():
    bbox = _BBox((0, 0, 0), (10, 4, 3))
    corners = project_bbox_corners_uv(bbox, _PLAN_BASIS)
    assert corners == [[0, 0], [10, 0], [10, 4], [0, 4]]


def test_project_bbox_corners_uv_returns_none_without_bbox_or_basis():
    bbox = _BBox((0, 0, 0), (1, 1, 1))
    assert project_bbox_corners_uv(None, _PLAN_BASIS) is None
    assert project_bbox_corners_uv(bbox, None) is None


def test_project_bbox_corners_uv_returns_none_for_link_space_without_transform():
    bbox = _BBox((0, 0, 0), (1, 1, 1))
    assert project_bbox_corners_uv(bbox, _PLAN_BASIS, bbox_is_link_space=True, transform=None) is None


def test_project_bbox_corners_uv_applies_link_transform_before_projection():
    bbox = _BBox((0, 0, 0), (10, 4, 3))
    trf = _FakeTransform(dx=100, dy=200, dz=0)
    corners = project_bbox_corners_uv(bbox, _PLAN_BASIS, bbox_is_link_space=True, transform=trf)
    assert corners == [[100, 200], [110, 200], [110, 204], [100, 204]]


def test_project_bbox_corners_uv_uses_bbox_transform_when_present():
    class _BBoxWithTransform(_BBox):
        def __init__(self, mn, mx, transform):
            super().__init__(mn, mx)
            self.Transform = transform

    bbox = _BBoxWithTransform((0, 0, 0), (10, 4, 3), _FakeTransform(dx=5, dy=5, dz=0))
    corners = project_bbox_corners_uv(bbox, _PLAN_BASIS)
    assert corners == [[5, 5], [15, 5], [15, 9], [5, 9]]


def test_project_bbox_corners_uv_returns_none_when_bbox_transform_fails():
    """A bbox.Transform that raises on every attempt must never fall through
    to projecting the still-untransformed, bbox-local corners as if they
    were host-space -- that would silently produce a plausible but wrong
    UV rectangle. Must honor the documented None-on-failure contract."""
    class _RaisingTransform:
        def OfPoint(self, _pt):
            raise RuntimeError("boom")

    class _BBoxWithTransform(_BBox):
        def __init__(self, mn, mx, transform):
            super().__init__(mn, mx)
            self.Transform = transform

    bbox = _BBoxWithTransform((0, 0, 0), (10, 4, 3), _RaisingTransform())

    class _Diag:
        def __init__(self):
            self.errors = []

        def error(self, **kwargs):
            self.errors.append(kwargs)

    diag = _Diag()
    assert project_bbox_corners_uv(bbox, _PLAN_BASIS, diag=diag) is None
    assert diag.errors


# --- project_bbox_uv_and_near_face_w: UV footprint and depth from the SAME
# --- transformed corners -----------------------------------------------------

def test_project_bbox_uv_and_near_face_w_agrees_with_project_bbox_corners_uv():
    bbox = _BBox((0, 0, 0), (10, 4, 3))
    rect, near_face_w = project_bbox_uv_and_near_face_w(bbox, _PLAN_BASIS)
    assert rect == project_bbox_corners_uv(bbox, _PLAN_BASIS)
    # forward=(0,0,-1), origin=(0,0,0) -> w = -z; nearest (min w) corner is
    # the one with the largest z (3).
    assert near_face_w == world_to_view((0, 0, 3), _PLAN_BASIS)[2]


def test_project_bbox_uv_and_near_face_w_uses_bbox_transform_for_both_values():
    """A HOST bbox with a non-identity Transform must have its UV footprint
    AND its near_face_w computed from the SAME transformed corners -- not a
    footprint from transformed corners paired with a depth from the
    original, un-transformed ones (which estimate_nearest_depth_from_bbox()
    would silently do, since it never applies bbox.Transform at all)."""
    class _BBoxWithTransform(_BBox):
        def __init__(self, mn, mx, transform):
            super().__init__(mn, mx)
            self.Transform = transform

    # Bbox-local Z is [0, 2]; the transform shifts everything by +100 in Z,
    # simulating a family instance placed high above its type's local origin.
    bbox = _BBoxWithTransform((0, 0, 0), (2, 2, 2), _FakeTransform(dx=0, dy=0, dz=100))
    rect, near_face_w = project_bbox_uv_and_near_face_w(bbox, _PLAN_BASIS)

    assert rect == [[0, 0], [2, 0], [2, 2], [0, 2]]
    # Transformed Z range is [100, 102] -> w range is [-100, -102]; nearest
    # (min w) is -102, from the transformed corner at z=102, NOT from the
    # untransformed local z=2 (which would incorrectly give w=-2).
    assert near_face_w == world_to_view((0, 0, 102), _PLAN_BASIS)[2]
    assert near_face_w != world_to_view((0, 0, 2), _PLAN_BASIS)[2]


def test_project_bbox_uv_and_near_face_w_returns_none_tuple_without_bbox_or_basis():
    bbox = _BBox((0, 0, 0), (1, 1, 1))
    assert project_bbox_uv_and_near_face_w(None, _PLAN_BASIS) == (None, None)
    assert project_bbox_uv_and_near_face_w(bbox, None) == (None, None)
