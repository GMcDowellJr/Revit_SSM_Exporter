# transforms.py
# Phase 2, Sprint 1: MOVE ONLY (geometric transforms and utilities)
#
# Extracted coordinate transformation and geometric utility functions.

"""
Geometric transformations and utilities for SSM Exporter.

This module contains:
- 3D vector math operations (add, sub, mul, dot, cross, normalize)
- Bounding box transformations
- OBB (Oriented Bounding Box) construction and intersection tests
- AABB (Axis-Aligned Bounding Box) utilities
- Coordinate space conversions
"""

import math


# ============================================================
# 3D VECTOR MATH (tuple-based for performance)
# ============================================================

def v3(x, y, z):
    """Create a 3D vector as a tuple."""
    return (float(x), float(y), float(z))


def v_add(a, b):
    """Add two 3D vectors."""
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def v_sub(a, b):
    """Subtract vector b from vector a."""
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def v_mul(a, s):
    """Multiply vector by scalar."""
    return (a[0] * s, a[1] * s, a[2] * s)


def v_dot(a, b):
    """Dot product of two vectors."""
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def v_cross(a, b):
    """Cross product of two vectors."""
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def v_len(a):
    """Length (magnitude) of a vector."""
    return math.sqrt(v_dot(a, a))


def v_norm(a):
    """Normalize a vector to unit length."""
    l = v_len(a)
    if l <= 1e-12:
        return (0.0, 0.0, 0.0)
    return (a[0] / l, a[1] / l, a[2] / l)


def xyz_to_v(p):
    """Convert Revit XYZ point to tuple vector."""
    return (float(p.X), float(p.Y), float(p.Z))


# ============================================================
# BOUNDING BOX TRANSFORMATIONS
# ============================================================

def transform_bbox_to_host(bb, trf, XYZ):
    """
    Transform a link-space BoundingBoxXYZ into a host-space axis-aligned box
    by transforming its 8 corners with the given Transform.

    Args:
        bb: Revit BoundingBoxXYZ in link coordinates
        trf: Revit Transform from link to host
        XYZ: Revit XYZ class

    Returns:
        (min_xyz, max_xyz) tuple or (None, None) on error
    """
    if XYZ is None or bb is None or trf is None:
        return None, None

    try:
        bb_min = bb.Min
        bb_max = bb.Max
    except Exception:
        return None, None

    try:
        corners = [
            XYZ(bb_min.X, bb_min.Y, bb_min.Z),
            XYZ(bb_min.X, bb_min.Y, bb_max.Z),
            XYZ(bb_min.X, bb_max.Y, bb_min.Z),
            XYZ(bb_min.X, bb_max.Y, bb_max.Z),
            XYZ(bb_max.X, bb_min.Y, bb_min.Z),
            XYZ(bb_max.X, bb_min.Y, bb_max.Z),
            XYZ(bb_max.X, bb_max.Y, bb_min.Z),
            XYZ(bb_max.X, bb_max.Y, bb_max.Z),
        ]
    except Exception:
        return None, None

    host_pts = []
    for p in corners:
        try:
            host_pts.append(trf.OfPoint(p))
        except Exception:
            return None, None

    xs = [p.X for p in host_pts]
    ys = [p.Y for p in host_pts]
    zs = [p.Z for p in host_pts]
    return XYZ(min(xs), min(ys), min(zs)), XYZ(max(xs), max(ys), max(zs))


# ============================================================
# OBB (ORIENTED BOUNDING BOX) UTILITIES
# ============================================================

def obb_from_ordered_corners(corners):
    """
    Construct an OBB from 8 ordered corners.

    Args:
        corners: 8 points in the following order (local box coords):
            0: (minX, minY, z0)
            1: (minX, minY, z1)
            2: (minX, maxY, z0)
            3: (minX, maxY, z1)
            4: (maxX, minY, z0)
            5: (maxX, minY, z1)
            6: (maxX, maxY, z0)
            7: (maxX, maxY, z1)

    Returns:
        dict: {center, axes[3], extents[3]} in same coordinate space as input
    """
    c0 = xyz_to_v(corners[0])
    cx = xyz_to_v(corners[4])
    cy = xyz_to_v(corners[2])
    cz = xyz_to_v(corners[1])

    ax = v_norm(v_sub(cx, c0))
    ay = v_norm(v_sub(cy, c0))
    az = v_norm(v_sub(cz, c0))

    ex = 0.5 * v_len(v_sub(cx, c0))
    ey = 0.5 * v_len(v_sub(cy, c0))
    ez = 0.5 * v_len(v_sub(cz, c0))

    # Center = c0 + ax*ex + ay*ey + az*ez
    center = v_add(c0, v_add(v_mul(ax, ex), v_add(v_mul(ay, ey), v_mul(az, ez))))

    return {"center": center, "axes": (ax, ay, az), "extents": (ex, ey, ez)}


def aabb_center_extents_from_bboxxyz(bb):
    """
    Extract center and extents from a Revit BoundingBoxXYZ.

    Returns:
        (center, extents) as tuples
    """
    mn = bb.Min
    mx = bb.Max
    c = ((mn.X + mx.X) * 0.5, (mn.Y + mx.Y) * 0.5, (mn.Z + mx.Z) * 0.5)
    e = ((mx.X - mn.X) * 0.5, (mx.Y - mn.Y) * 0.5, (mx.Z - mn.Z) * 0.5)
    return c, e


def obb_intersects_aabb(obb, aabb_center, aabb_extents):
    """
    Test OBB vs AABB intersection using Separating Axis Theorem (SAT).

    Args:
        obb: dict with {center, axes, extents}
        aabb_center: tuple (x, y, z)
        aabb_extents: tuple (ex, ey, ez) - half-widths

    Returns:
        True if boxes overlap, False otherwise

    Note: Both boxes must be in the SAME coordinate space.
    Uses Christer Ericson-style SAT with numerical epsilon.
    """
    C0 = obb["center"]
    A0, A1, A2 = obb["axes"]
    E0, E1, E2 = obb["extents"]

    C1 = aabb_center
    F0, F1, F2 = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    G0, G1, G2 = aabb_extents

    # Rotation matrix R[i][j] = dot(Ai, Fj)
    R00 = v_dot(A0, F0); R01 = v_dot(A0, F1); R02 = v_dot(A0, F2)
    R10 = v_dot(A1, F0); R11 = v_dot(A1, F1); R12 = v_dot(A1, F2)
    R20 = v_dot(A2, F0); R21 = v_dot(A2, F1); R22 = v_dot(A2, F2)

    # Abs rotation matrix with epsilon
    eps = 1e-9
    AR00 = abs(R00) + eps; AR01 = abs(R01) + eps; AR02 = abs(R02) + eps
    AR10 = abs(R10) + eps; AR11 = abs(R11) + eps; AR12 = abs(R12) + eps
    AR20 = abs(R20) + eps; AR21 = abs(R21) + eps; AR22 = abs(R22) + eps

    # Translation in A's frame: t = (C1 - C0) expressed in A basis
    t = v_sub(C1, C0)
    t0 = v_dot(t, A0)
    t1 = v_dot(t, A1)
    t2 = v_dot(t, A2)

    # Test axes A0, A1, A2
    ra = E0
    rb = G0 * AR00 + G1 * AR01 + G2 * AR02
    if abs(t0) > ra + rb: return False

    ra = E1
    rb = G0 * AR10 + G1 * AR11 + G2 * AR12
    if abs(t1) > ra + rb: return False

    ra = E2
    rb = G0 * AR20 + G1 * AR21 + G2 * AR22
    if abs(t2) > ra + rb: return False

    # Test axes F0, F1, F2 (AABB axes)
    tx = t[0]; ty = t[1]; tz = t[2]

    ra = E0 * AR00 + E1 * AR10 + E2 * AR20
    rb = G0
    if abs(tx) > ra + rb: return False

    ra = E0 * AR01 + E1 * AR11 + E2 * AR21
    rb = G1
    if abs(ty) > ra + rb: return False

    ra = E0 * AR02 + E1 * AR12 + E2 * AR22
    rb = G2
    if abs(tz) > ra + rb: return False

    # Test cross products Ai x Fj (9 tests)
    # A0 x F0
    ra = E1 * AR20 + E2 * AR10
    rb = G1 * AR02 + G2 * AR01
    if abs(t2 * R10 - t1 * R20) > ra + rb: return False

    # A0 x F1
    ra = E1 * AR21 + E2 * AR11
    rb = G0 * AR02 + G2 * AR00
    if abs(t2 * R11 - t1 * R21) > ra + rb: return False

    # A0 x F2
    ra = E1 * AR22 + E2 * AR12
    rb = G0 * AR01 + G1 * AR00
    if abs(t2 * R12 - t1 * R22) > ra + rb: return False

    # A1 x F0
    ra = E0 * AR20 + E2 * AR00
    rb = G1 * AR12 + G2 * AR11
    if abs(t0 * R20 - t2 * R00) > ra + rb: return False

    # A1 x F1
    ra = E0 * AR21 + E2 * AR01
    rb = G0 * AR12 + G2 * AR10
    if abs(t0 * R21 - t2 * R01) > ra + rb: return False

    # A1 x F2
    ra = E0 * AR22 + E2 * AR02
    rb = G0 * AR11 + G1 * AR10
    if abs(t0 * R22 - t2 * R02) > ra + rb: return False

    # A2 x F0
    ra = E0 * AR10 + E1 * AR00
    rb = G1 * AR22 + G2 * AR21
    if abs(t1 * R00 - t0 * R10) > ra + rb: return False

    # A2 x F1
    ra = E0 * AR11 + E1 * AR01
    rb = G0 * AR22 + G2 * AR20
    if abs(t1 * R01 - t0 * R11) > ra + rb: return False

    # A2 x F2
    ra = E0 * AR12 + E1 * AR02
    rb = G0 * AR21 + G1 * AR20
    if abs(t1 * R02 - t0 * R12) > ra + rb: return False

    return True


def host_crop_prism_corners_model(host_view, z0, z1, XYZ):
    """
    Build 8 prism corners in host MODEL coordinates.

    Driven by host_view.CropBox XY, with Z overridden to [z0,z1] (host model Z).
    Works for typical plan-view crop rotation about Z.

    Args:
        host_view: Revit View object
        z0: Bottom Z coordinate in model space
        z1: Top Z coordinate in model space
        XYZ: Revit XYZ class

    Returns:
        List of 8 corners ordered for _obb_from_ordered_corners, or None on error
    """
    try:
        crop_bb = getattr(host_view, "CropBox", None)
    except Exception:
        crop_bb = None

    if crop_bb is None or crop_bb.Min is None or crop_bb.Max is None:
        return None

    tr = None
    try:
        tr = getattr(crop_bb, "Transform", None)
    except Exception:
        tr = None

    # local box mins/maxs (in crop box local coords)
    mn = crop_bb.Min
    mx = crop_bb.Max
    xs = (mn.X, mx.X)
    ys = (mn.Y, mx.Y)

    if tr is None:
        # Assume already in model coords
        p00 = XYZ(xs[0], ys[0], 0.0)
        p01 = XYZ(xs[0], ys[1], 0.0)
        p10 = XYZ(xs[1], ys[0], 0.0)
        p11 = XYZ(xs[1], ys[1], 0.0)
    else:
        # Transform local XY corners to model coords (z ignored here, then overwritten)
        p00 = tr.OfPoint(XYZ(xs[0], ys[0], 0.0))
        p01 = tr.OfPoint(XYZ(xs[0], ys[1], 0.0))
        p10 = tr.OfPoint(XYZ(xs[1], ys[0], 0.0))
        p11 = tr.OfPoint(XYZ(xs[1], ys[1], 0.0))

    # Overwrite Z with host slab bounds (model Z)
    b00 = XYZ(p00.X, p00.Y, z0)
    b01 = XYZ(p01.X, p01.Y, z0)
    b10 = XYZ(p10.X, p10.Y, z0)
    b11 = XYZ(p11.X, p11.Y, z0)
    t00 = XYZ(p00.X, p00.Y, z1)
    t01 = XYZ(p01.X, p01.Y, z1)
    t10 = XYZ(p10.X, p10.Y, z1)
    t11 = XYZ(p11.X, p11.Y, z1)

    # Ordered for box-local axes: X then Y then Z
    return [b00, t00, b01, t01, b10, t10, b11, t11]
