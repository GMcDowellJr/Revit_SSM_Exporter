"""
Unit tests for deterministic planar front-face selection utilities.

These tests are Revit-free and use stubs that mimic the minimal PlanarFace surface:
- FaceNormal (XYZ-like)
- Origin (XYZ-like)
"""

import unittest
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from vop_interwoven.revit.view_basis import ViewBasis
from vop_interwoven.core.face_selection import (
    iter_front_facing_planar_faces,
    group_faces_by_plane,
    select_dominant_face_per_plane_group,
    select_top_plane_groups,
)


class _XYZ:
    def __init__(self, x, y, z):
        self.X = float(x)
        self.Y = float(y)
        self.Z = float(z)


class _FaceStub:
    def __init__(self, normal, origin, name=None):
        self.FaceNormal = _XYZ(*normal)
        self.Origin = _XYZ(*origin)
        self._name = name or "F"

    def __repr__(self):
        return "<FaceStub {}>".format(self._name)


def _square_loop(center_u, center_v, size):
    """Return a square polygon in *model* coords (plan view basis => model XY == UV)."""
    s = float(size) * 0.5
    return [
        (center_u - s, center_v - s, 0.0),
        (center_u + s, center_v - s, 0.0),
        (center_u + s, center_v + s, 0.0),
        (center_u - s, center_v + s, 0.0),
        (center_u - s, center_v - s, 0.0),
    ]


class TestFaceSelection(unittest.TestCase):
    def setUp(self):
        # Plan-like basis: UV = XY, forward into screen = -Z
        self.vb = ViewBasis(origin=(0, 0, 0), right=(1, 0, 0), up=(0, 1, 0), forward=(0, 0, -1))

    def test_front_facing_filter(self):
        # view_forward = (0,0,-1); face normal must point +Z to be front-facing (dot=-1)
        f_front = _FaceStub(normal=(0, 0, 1), origin=(0, 0, 0), name="front")
        f_back = _FaceStub(normal=(0, 0, -1), origin=(0, 0, 0), name="back")

        out = list(iter_front_facing_planar_faces([f_back, f_front], self.vb.forward, eps=1e-6))
        self.assertEqual(out, [f_front])

    def test_group_by_plane_canonicalizes_opposite_normals(self):
        # Same plane: z=0, opposite normals should collapse to same canonical plane
        f1 = _FaceStub(normal=(0, 0, 1), origin=(0, 0, 0), name="n+")
        f2 = _FaceStub(normal=(0, 0, -1), origin=(0, 0, 0), name="n-")

        groups = group_faces_by_plane([f1, f2], normal_eps=1e-9, offset_eps=1e-9)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["faces"]), 2)

    def test_dominant_face_by_projected_uv_area(self):
        # Two faces in the same plane group; choose larger projected outer loop.
        f_big = _FaceStub(normal=(0, 0, 1), origin=(0, 0, 0), name="big")
        f_small = _FaceStub(normal=(0, 0, 1), origin=(0, 0, 0), name="small")

        groups = group_faces_by_plane([f_small, f_big], normal_eps=1e-9, offset_eps=1e-9)

        loops = {
            f_small: [_square_loop(0.0, 0.0, 2.0)],  # area 4
            f_big: [_square_loop(0.0, 0.0, 4.0)],    # area 16
        }

        def loop_extractor(face):
            return loops.get(face) or []

        sels = select_dominant_face_per_plane_group(groups, self.vb, loop_extractor=loop_extractor)
        self.assertEqual(len(sels), 1)
        self.assertEqual(sels[0]["face"], f_big)
        self.assertAlmostEqual(sels[0]["area_uv"], 16.0, places=6)

    def test_select_top_n_stable_tie_breakers(self):
        # Two plane groups with equal area -> tie-break by plane.d (ASC), then normal lexicographic.
        f1 = _FaceStub(normal=(0, 0, 1), origin=(0, 0, 0), name="p0")
        f2 = _FaceStub(normal=(0, 0, 1), origin=(0, 0, 1), name="p1")  # parallel plane

        g = group_faces_by_plane([f1, f2], normal_eps=1e-9, offset_eps=1e-9)
        # g will produce two groups; craft selections with equal area
        selections = [
            {"plane": g[0]["plane"], "area_uv": 10.0, "face": g[0]["rep"], "faces": g[0]["faces"]},
            {"plane": g[1]["plane"], "area_uv": 10.0, "face": g[1]["rep"], "faces": g[1]["faces"]},
        ]

        top = select_top_plane_groups(selections, top_n=2)

        # With canonical plane equation n·x + d = 0, for z=0 plane: d=0; for z=1: d=-1.
        # Tie-break rule uses d ASC => -1 comes before 0.
        self.assertEqual(len(top), 2)
        self.assertLessEqual(top[0]["plane"][3], top[1]["plane"][3])

        # Deterministic: calling again yields same order
        top2 = select_top_plane_groups(selections, top_n=2)
        self.assertEqual([t["plane"] for t in top], [t["plane"] for t in top2])


if __name__ == "__main__":
    unittest.main()
