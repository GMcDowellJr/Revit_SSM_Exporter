"""
Deterministic planar front-face selection utilities (capability only; no pipeline wiring).

Goals:
- Purely geometric selection (no category/view hacks).
- Deterministic grouping + ordering independent of input face iteration order.
- Designed to operate on Revit PlanarFace objects, but testable with stubs.

Key operations:
1) front-facing planar filter (dot(view_forward, face_normal) < -eps)
2) group faces by plane (normal + offset tolerance, sign-canonical)
3) select dominant face per plane by projected UV outer-loop area
4) select top N plane-groups by area with stable ordering + tie-breakers

NOTE: This module intentionally does not import pipeline modules.
"""

from __future__ import annotations


# ----------------------------
# Small math helpers (no numpy)
# ----------------------------

def _to_xyz_tuple(v):
    """Accept Revit XYZ-like or tuple/list; return (x,y,z) floats."""
    try:
        return (float(v.X), float(v.Y), float(v.Z))
    except Exception:
        return (float(v[0]), float(v[1]), float(v[2]))


def _dot(a, b):
    return float(a[0] * b[0] + a[1] * b[1] + a[2] * b[2])


def _norm(a):
    return float((_dot(a, a)) ** 0.5)


def _normalize(a):
    n = _norm(a)
    if n <= 0.0:
        return (0.0, 0.0, 0.0)
    return (a[0] / n, a[1] / n, a[2] / n)


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _canonicalize_plane(n_unit, d):
    """
    Canonicalize plane representation so that (n, d) and (-n, -d) map to one key.
    We pick a stable sign by requiring the first non-zero component of n to be positive.
    """
    nx, ny, nz = n_unit

    # Determine sign based on first significant component
    s = 1.0
    if abs(nx) > 1e-12:
        s = 1.0 if nx > 0.0 else -1.0
    elif abs(ny) > 1e-12:
        s = 1.0 if ny > 0.0 else -1.0
    elif abs(nz) > 1e-12:
        s = 1.0 if nz > 0.0 else -1.0
    else:
        s = 1.0

    if s < 0.0:
        return (-nx, -ny, -nz, -d)

    return (nx, ny, nz, d)


def _plane_from_planar_face(face):
    """
    Best-effort plane extraction for Revit PlanarFace:
    - normal: face.FaceNormal
    - point on plane: face.Origin
    Plane equation: n·x + d = 0, where d = -n·p0
    Returns: (n_unit_tuple, d_float) or (None, None) if not planar.
    """
    try:
        n = _to_xyz_tuple(getattr(face, "FaceNormal"))
        p0 = _to_xyz_tuple(getattr(face, "Origin"))
    except Exception:
        return (None, None)

    n_unit = _normalize(n)
    if _norm(n_unit) <= 0.0:
        return (None, None)

    d = -_dot(n_unit, p0)
    return (n_unit, float(d))


def _plane_eq_close(n1, d1, n2, d2, normal_eps, offset_eps):
    """
    Compare planes with tolerance.
    - normal_eps: angular tolerance expressed as (1 - |dot(n1,n2)|) <= normal_eps
    - offset_eps: |d1 - d2| <= offset_eps (in model units, feet)
    """
    dd = abs(float(d1) - float(d2))
    if dd > float(offset_eps):
        return False

    # Both normals are expected unit length
    c = abs(_dot(n1, n2))
    if (1.0 - c) > float(normal_eps):
        return False

    return True


def signed_polygon_area_2d(poly_uv):
    """
    Shoelace formula; positive for CCW.
    poly_uv: list of (u,v) points (may be closed or open).
    """
    if not poly_uv or len(poly_uv) < 3:
        return 0.0

    pts = list(poly_uv)
    if pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 3:
        return 0.0

    a = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = float(pts[i][0]), float(pts[i][1])
        x2, y2 = float(pts[(i + 1) % n][0]), float(pts[(i + 1) % n][1])
        a += (x1 * y2 - x2 * y1)
    return 0.5 * a


def polygon_area_2d(poly_uv):
    return abs(signed_polygon_area_2d(poly_uv))


# ------------------------------------
# Public API: selection + plane grouping
# ------------------------------------

def iter_front_facing_planar_faces(
    faces,
    view_forward,
    eps=1e-6,
    diag=None,
    *,
    view_id=None,
    elem_id=None,
    callsite="face_selection.iter_front_facing_planar_faces",
):
    """
    Yield planar faces that are front-facing relative to view_forward:
        dot(face_normal, view_forward) < -eps

    - Non-planar faces are ignored, and a single deduped diagnostic can be emitted.
    - Determinism: output is sorted by plane key (n,d) before yielding.

    Args:
        faces: iterable of face objects (Revit faces or stubs)
        view_forward: tuple/XYZ compatible with ViewBasis.forward
        eps: float
        diag: Diagnostics (optional)
    """
    vf = _normalize(_to_xyz_tuple(view_forward))
    nonplanar = 0

    candidates = []
    for f in (faces or []):
        n_unit, d = _plane_from_planar_face(f)
        if n_unit is None:
            nonplanar += 1
            continue

        # front-facing: dot(n, vf) < -eps
        if _dot(n_unit, vf) < -float(eps):
            # stable key based on canonical plane params (rounded for total order only)
            nx, ny, nz, dc = _canonicalize_plane(n_unit, float(d))
            sort_key = (round(nx, 12), round(ny, 12), round(nz, 12), round(dc, 8))
            candidates.append((sort_key, f))

    if nonplanar and diag is not None:
        try:
            dedupe_key = "face_selection.nonplanar|{}|{}".format(view_id, elem_id)
            diag.debug_dedupe(
                dedupe_key=dedupe_key,
                phase="face_selection",
                callsite=callsite + ".nonplanar",
                message="Non-planar faces encountered; ignored for planar front-face selection",
                view_id=view_id,
                elem_id=elem_id,
                extra={"nonplanar_count": int(nonplanar)},
            )
        except Exception:
            pass

    candidates.sort(key=lambda t: t[0])
    for _k, f in candidates:
        yield f


def group_faces_by_plane(
    faces,
    normal_eps=1e-6,
    offset_eps=1e-4,
):
    """
    Group planar faces by plane within tolerance.

    Determinism:
    - Faces are first reduced to plane params and sorted by canonical plane key.
    - Grouping proceeds in sorted order; group representative is first face in group.

    Returns:
        list of groups:
            {
              "plane": (nx, ny, nz, d),   # canonicalized
              "faces": [face, ...],
              "rep": face,               # representative face
            }
    """
    entries = []
    for f in (faces or []):
        n_unit, d = _plane_from_planar_face(f)
        if n_unit is None:
            continue
        nx, ny, nz, dc = _canonicalize_plane(n_unit, float(d))
        sort_key = (round(nx, 12), round(ny, 12), round(nz, 12), round(dc, 8))
        entries.append((sort_key, (nx, ny, nz), float(dc), f))

    entries.sort(key=lambda t: t[0])

    groups = []
    for _sk, n, d, f in entries:
        placed = False
        for g in groups:
            gn = (g["plane"][0], g["plane"][1], g["plane"][2])
            gd = g["plane"][3]
            if _plane_eq_close(gn, gd, n, d, normal_eps=normal_eps, offset_eps=offset_eps):
                g["faces"].append(f)
                placed = True
                break
        if not placed:
            groups.append(
                {
                    "plane": (float(n[0]), float(n[1]), float(n[2]), float(d)),
                    "faces": [f],
                    "rep": f,
                }
            )

    return groups


def projected_outer_loop_area_uv(
    face,
    view_basis,
    *,
    loop_extractor=None,
):
    """
    Compute projected UV area of the *outer* loop for a planar face.

    loop_extractor(face) -> list of loops, where each loop is list of model points (XYZ/tuple).
        - The first loop is treated as "outer" by convention for this capability.
        - If loop_extractor is None, tries to extract from Revit-like face.EdgeLoops.

    Returns:
        float area (>= 0)
    """
    loops = None

    if loop_extractor is not None:
        loops = loop_extractor(face)
    else:
        # Best-effort Revit PlanarFace path: face.EdgeLoops -> CurveLoop(s) -> tessellated points
        try:
            loops = []
            edge_loops = getattr(face, "EdgeLoops", None)
            if edge_loops is not None:
                for cl in edge_loops:
                    pts = []
                    for crv in cl:
                        try:
                            tess = crv.Tessellate()
                            for p in tess:
                                pts.append(_to_xyz_tuple(p))
                        except Exception:
                            continue
                    if pts:
                        loops.append(pts)
        except Exception:
            loops = None

    if not loops:
        return 0.0

    outer = loops[0]
    if not outer or len(outer) < 3:
        return 0.0

    # Project to UV
    uv = []
    for p in outer:
        u, v = view_basis.transform_to_view_uv(p)
        uv.append((float(u), float(v)))

    return float(polygon_area_2d(uv))


def select_dominant_face_per_plane_group(
    plane_groups,
    view_basis,
    *,
    loop_extractor=None,
):
    """
    For each plane-group, select the dominant face by projected UV outer-loop area.

    Returns:
        list of selections:
            {
              "plane": (nx, ny, nz, d),
              "area_uv": float,
              "face": face,
              "faces": [...],  # original faces in group
            }
    """
    out = []
    for g in (plane_groups or []):
        best = None
        best_area = -1.0

        for f in g.get("faces") or []:
            a = projected_outer_loop_area_uv(f, view_basis, loop_extractor=loop_extractor)
            if a > best_area:
                best_area = a
                best = f

        out.append(
            {
                "plane": g.get("plane"),
                "area_uv": float(max(0.0, best_area)),
                "face": best,
                "faces": list(g.get("faces") or []),
            }
        )
    return out


def select_top_plane_groups(
    selections,
    top_n,
):
    """
    Select top N plane-groups by area_uv with stable ordering and tie-breakers.

    Ordering:
      1) area_uv DESC
      2) plane.d ASC
      3) plane.normal (nx,ny,nz) lexicographic ASC
      4) fallback to id(face) ASC (stable within process)

    Returns:
        list of selections (length <= top_n)
    """
    N = int(top_n) if top_n is not None else 0
    if N <= 0:
        return []

    def _key(sel):
        plane = sel.get("plane") or (0.0, 0.0, 0.0, 0.0)
        nx, ny, nz, d = float(plane[0]), float(plane[1]), float(plane[2]), float(plane[3])
        a = float(sel.get("area_uv") or 0.0)
        f = sel.get("face")
        f_id = id(f) if f is not None else 0
        return (-a, d, nx, ny, nz, f_id)

    ordered = sorted(list(selections or []), key=_key)
    return ordered[:N]
