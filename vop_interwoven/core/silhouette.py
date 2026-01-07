"""
Silhouette extraction for VOP interwoven pipeline.

Provides simplified silhouette extraction with adaptive strategies:
- bbox: Axis-aligned bounding box (baseline)
- obb: Oriented bounding box (projected 3D corners)
- silhouette_edges: Edge silhouette (most accurate, most expensive)
- cad_curves: ImportInstance curve extraction (DWG/DXF, open polylines)

Falls back gracefully to bbox when strategies fail.
Compatible with IronPython (no logging module, no f-strings).
"""

import math

def _bbox_corners_world(bbox):
    """
    Return 8 bbox corners in world coords, honoring bbox.Transform when present.
    bbox.Min/Max are in bbox-local coords.
    """
    try:
        from Autodesk.Revit.DB import XYZ, Transform
    except Exception:
        XYZ = None

    if not bbox or not bbox.Min or not bbox.Max:
        return []

    mn = bbox.Min
    mx = bbox.Max

    # local corners
    if XYZ is None:
        return []
    local = [
        XYZ(mn.X, mn.Y, mn.Z),
        XYZ(mx.X, mn.Y, mn.Z),
        XYZ(mx.X, mx.Y, mn.Z),
        XYZ(mn.X, mx.Y, mn.Z),
        XYZ(mn.X, mn.Y, mx.Z),
        XYZ(mx.X, mn.Y, mx.Z),
        XYZ(mx.X, mx.Y, mx.Z),
        XYZ(mn.X, mx.Y, mx.Z),
    ]

    trf = getattr(bbox, "Transform", None)
    if trf is None:
        return local

    try:
        return [trf.OfPoint(p) for p in local]
    except Exception:
        return local


def _pca_obb_uv(points_uv):
    """
    Compute an oriented rectangle in UV using PCA (good for “linear-ish” detection).
    Returns: (rect_points, len_u, len_v) where rect_points is a closed loop in UV.
    """
    if not points_uv or len(points_uv) < 2:
        return ([], 0.0, 0.0)

    # mean
    sx = 0.0
    sy = 0.0
    n = float(len(points_uv))
    for (x, y) in points_uv:
        sx += float(x)
        sy += float(y)
    mx = sx / n
    my = sy / n

    # covariance
    cxx = 0.0
    cxy = 0.0
    cyy = 0.0
    for (x, y) in points_uv:
        dx = float(x) - mx
        dy = float(y) - my
        cxx += dx * dx
        cxy += dx * dy
        cyy += dy * dy

    # principal axis angle (2D PCA)
    # angle = 0.5 * atan2(2*cxy, cxx - cyy)
    ang = 0.5 * math.atan2(2.0 * cxy, (cxx - cyy))
    ux = math.cos(ang)
    uy = math.sin(ang)
    vx = -uy
    vy = ux

    # project extents
    umin = 1e100
    umax = -1e100
    vmin = 1e100
    vmax = -1e100
    for (x, y) in points_uv:
        dx = float(x) - mx
        dy = float(y) - my
        u = dx * ux + dy * uy
        v = dx * vx + dy * vy
        if u < umin: umin = u
        if u > umax: umax = u
        if v < vmin: vmin = v
        if v > vmax: vmax = v

    len_u = (umax - umin)
    len_v = (vmax - vmin)

    # rectangle corners back in UV
    # p = mean + u*U + v*V
    a = (mx + umin * ux + vmin * vx, my + umin * uy + vmin * vy)
    b = (mx + umax * ux + vmin * vx, my + umax * uy + vmin * vy)
    c = (mx + umax * ux + vmax * vx, my + umax * uy + vmax * vy)
    d = (mx + umin * ux + vmax * vx, my + umin * uy + vmax * vy)
    rect = [a, b, c, d, a]

    return (rect, abs(len_u), abs(len_v))


def _uv_obb_rect_from_bbox(elem, view, view_basis):
    """
    Build a UV OBB rectangle loop from bbox corners (using bbox.Transform if present).
    Returns: (loop_points_uv, len_u, len_v)
    """
    try:
        bbox = elem.get_BoundingBox(view)
        if not bbox or not bbox.Min or not bbox.Max:
            return ([], 0.0, 0.0)

        corners_w = _bbox_corners_world(bbox)
        if not corners_w:
            return ([], 0.0, 0.0)

        pts_uv = []
        for p in corners_w:
            # NOTE: if you already added _to_host_point earlier, bbox for proxies is host already,
            # but this is harmless for normal elems.
            p2 = _to_host_point(elem, p) if "_to_host_point" in globals() else p
            uv = view_basis.transform_to_view_uv((p2.X, p2.Y, p2.Z))
            pts_uv.append((uv[0], uv[1]))

        rect, lu, lv = _pca_obb_uv(pts_uv)
        return (rect, lu, lv)

    except Exception:
        return ([], 0.0, 0.0)


def _determine_uv_mode(elem, view, view_basis, raster, cfg):
    """
    Classify element by UV mode (shape): TINY, LINEAR, or AREAL
    using an OBB in UV derived from bbox.Transform when possible.
    Falls back to the previous AABB behavior on failure.
    """
    try:
        # thresholds in cells
        tiny_max = cfg.tiny_max
        thin_max = cfg.thin_max

        rect, lu_ft, lv_ft = _uv_obb_rect_from_bbox(elem, view, view_basis)
        if lu_ft > 0.0 and lv_ft > 0.0:
            U = int(lu_ft / raster.cell_size_ft)
            V = int(lv_ft / raster.cell_size_ft)

            if U <= tiny_max and V <= tiny_max:
                return 'TINY'
            elif min(U, V) <= thin_max:
                return 'LINEAR'
            else:
                return 'AREAL'

        # Fallback: old AABB in UV
        bbox = elem.get_BoundingBox(view)
        if not bbox or not bbox.Min or not bbox.Max:
            return 'AREAL'
        min_uv = view_basis.transform_to_view_uv((bbox.Min.X, bbox.Min.Y, bbox.Min.Z))
        max_uv = view_basis.transform_to_view_uv((bbox.Max.X, bbox.Max.Y, bbox.Max.Z))
        u_extent = abs(max_uv[0] - min_uv[0])
        v_extent = abs(max_uv[1] - min_uv[1])
        U = int(u_extent / raster.cell_size_ft)
        V = int(v_extent / raster.cell_size_ft)

        if U <= tiny_max and V <= tiny_max:
            return 'TINY'
        elif min(U, V) <= thin_max:
            return 'LINEAR'
        else:
            return 'AREAL'

    except Exception:
        return 'AREAL'

def _location_curve_obb_silhouette(elem, view, view_basis, cfg=None):
    """
    Build a thin oriented quad around the projected LocationCurve.
    Great for diagonal thin elements (beams/brace/pipe runs) so they rasterize as lines.
    """
    try:
        thin = getattr(cfg, "thin_max", 1.0) if cfg else 1.0  # in *view UV units*
        loc = getattr(elem, "Location", None)
        if loc is None or not hasattr(loc, "Curve"):
            return []

        c = loc.Curve
        p0 = _to_host_point(elem, c.GetEndPoint(0))
        p1 = _to_host_point(elem, c.GetEndPoint(1))

        u0, v0 = view_basis.transform_to_view_uv((p0.X, p0.Y, p0.Z))
        u1, v1 = view_basis.transform_to_view_uv((p1.X, p1.Y, p1.Z))

        du = (u1 - u0)
        dv = (v1 - v0)
        L = (du * du + dv * dv) ** 0.5
        if L <= 1e-9:
            return []

        # perpendicular unit
        nx = -dv / L
        ny = du / L

        # thickness = thin (half on each side)
        t = thin * 0.5

        a = (u0 + nx * t, v0 + ny * t)
        b = (u1 + nx * t, v1 + ny * t)
        c2 = (u1 - nx * t, v1 - ny * t)
        d = (u0 - nx * t, v0 - ny * t)

        return [{
            "points": [a, b, c2, d, a],
            "is_hole": False
        }]

    except Exception:
        return []
        
def _cad_curves_silhouette(elem, view, view_basis, cfg=None):
    """
    For ImportInstance (DWG/DXF): extract curve primitives and return as OPEN polylines.
    These should be rasterized as edges only (no interior fill).
    """
    try:
        from Autodesk.Revit.DB import Options
        opts = Options()

        # CRITICAL: Never set opts.View for linked elements - causes geometry extraction failure
        # Linked elements (LinkedElementProxy) have .transform attribute
        if not hasattr(elem, 'transform'):  # Host element only
            try:
                opts.View = view
            except Exception:
                pass
        # For linked elements: leave opts.View = None (extract in link coordinates)

        geom = elem.get_Geometry(opts)
        if geom is None:
            return []

        max_paths = getattr(cfg, "cad_max_paths", 500) if cfg else 500
        max_pts = getattr(cfg, "cad_max_pts_per_path", 200) if cfg else 200

        loops = []
        count = 0

        for g in _iter_curve_primitives(geom):
            if count >= max_paths:
                break

            pts_uv = []

            # PolyLine
            if g.__class__.__name__ == "PolyLine":
                try:
                    coords = g.GetCoordinates()
                    for k in range(min(len(coords), max_pts)):
                        p = _to_host_point(elem, coords[k])
                        uv = view_basis.transform_to_view_uv((p.X, p.Y, p.Z))
                        pts_uv.append((uv[0], uv[1]))
                except Exception:
                    continue

            # Curve-like (Line/Arc/NurbSpline etc.)
            elif hasattr(g, "Tessellate"):
                try:
                    tess = g.Tessellate()
                    for k in range(min(len(tess), max_pts)):
                        p = _to_host_point(elem, tess[k])
                        uv = view_basis.transform_to_view_uv((p.X, p.Y, p.Z))
                        pts_uv.append((uv[0], uv[1]))
                except Exception:
                    continue

            if len(pts_uv) >= 2:
                loops.append({"points": pts_uv, "is_hole": False, "open": True})
                count += 1

        return loops

    except Exception:
        return []

def _iter_curve_primitives(geom):
    """Yield Curve / PolyLine-like primitives from GeometryElement recursively."""
    try:
        it = geom.GetEnumerator()
    except Exception:
        it = None
    if it:
        while it.MoveNext():
            g = it.Current
            if g is None:
                continue
            # GeometryInstance: recurse into instance geometry
            if hasattr(g, "GetInstanceGeometry"):
                try:
                    ig = g.GetInstanceGeometry()
                    for x in _iter_curve_primitives(ig):
                        yield x
                except Exception:
                    pass
                continue

            # Curves / Polylines
            if hasattr(g, "GetEndPoint") or g.__class__.__name__ == "PolyLine":
                yield g
                continue

def _to_host_point(elem, xyz):
    """
    If elem is a LinkedElementProxy (or anything with .transform),
    map link-space XYZ -> host-space XYZ.
    """
    trf = getattr(elem, "transform", None)
    if trf is None:
        return xyz
    try:
        return trf.OfPoint(xyz)
    except Exception:
        return xyz

def get_element_silhouette(elem, view, view_basis, raster, cfg=None, cache=None, cache_key=None):
    """Extract element silhouette as 2D loops.

    Args:
        elem: Revit Element
        view: Revit View
        view_basis: ViewBasis object for coordinate transformation
        raster: ViewRaster (provides cell size for UV mode classification)
        cfg: Optional Config object (provides strategy settings)

    Returns:
        List of loop dicts, each with:
        {
            'points': [(u, v), ...],  # View-local UV coordinates
            'is_hole': False,          # Whether this loop is a hole
            'strategy': 'bbox'|'obb'|'uv_obb_rect'|'silhouette_edges'|'cad_curves'  # Which strategy was used
        }

   'strategy': 'bbox'|'obb'|'uv_obb_rect'|'silhouette_edges'|'cad_curves' Commentary:
        - Returns empty list if element has no geometry
        - Tries strategies in order based on element UV mode (TINY/LINEAR/AREAL)
        - bbox is the ultimate fallback (always succeeds if element has bbox)
    """

    # PR12: geometry cache (caller provides bounded LRU; this function treats it as optional).
    if cache is not None and cache_key is not None:
        try:
            cached = cache.get(cache_key, default=None)
            if cached is not None:
                return cached
        except Exception:
            pass

    if cfg is None:
        # No config: accuracy-first default
        strategies = ['silhouette_edges', 'obb', 'bbox']
    else:
        # Cheap shape classification using UV-OBB first (grid-aware)
        uv_mode = _determine_uv_mode(elem, view, view_basis, raster, cfg)

        if uv_mode == 'TINY':
            # Don’t waste time; bbox/obb is plenty at this scale
            strategies = ['bbox', 'obb']
        elif uv_mode == 'LINEAR':
            # Key point: LINEAR means “too small for curves/L to matter at this grid”
            # So avoid silhouette_edges; use uv_obb_rect to prevent diagonal fattening
            strategies = ['uv_obb_rect', 'bbox']
        else:
            # AREAL: now it’s worth paying for silhouette_edges
            strategies = ['silhouette_edges', 'obb', 'bbox']
            # or config-driven:
            # strategies = cfg.get_silhouette_strategies(uv_mode)

    # Try each strategy in order
    for strategy_name in strategies:
        try:
            if strategy_name == 'uv_obb_rect':
                loops = _uv_obb_rect_silhouette(elem, view, view_basis)
            elif strategy_name == 'bbox':
                loops = _bbox_silhouette(elem, view, view_basis)
            elif strategy_name == 'obb':
                loops = _obb_silhouette(elem, view, view_basis)
            elif strategy_name == 'silhouette_edges':
                loops = _silhouette_edges(elem, view, view_basis, cfg)
            elif strategy_name == 'cad_curves':
                loops = _cad_curves_silhouette(elem, view, view_basis, cfg)
            else:
                continue

            if loops:
                # Tag loops with strategy used
                for loop in loops:
                    loop['strategy'] = strategy_name

                if cache is not None and cache_key is not None:
                    try:
                        cache.set(cache_key, [dict(loop) for loop in loops])
                    except Exception:
                        pass

                return loops

        except Exception as e:
            # Strategy failed, try next
            pass

    # Ultimate fallback: bbox
    try:
        loops = _bbox_silhouette(elem, view, view_basis)
        for loop in loops:
            loop['strategy'] = 'bbox_fallback'
        return loops
    except Exception:
        return []

def _uv_obb_rect_silhouette(elem, view, view_basis):
    """
    Return a single closed loop representing the UV OBB rectangle from bbox corners.
    This is used for LINEAR classification so diagonals render as thin oriented boxes,
    not fat axis-aligned rectangles.
    """
    rect, lu, lv = _uv_obb_rect_from_bbox(elem, view, view_basis)
    if not rect or len(rect) < 4:
        return []
    return [{"points": rect, "is_hole": False}]


def _bbox_silhouette(elem, view, view_basis):
    """Extract axis-aligned bounding box silhouette.

    Args:
        elem: Revit Element
        view: Revit View
        view_basis: ViewBasis

    Returns:
        List with one loop (rectangle in view UVW coordinates, W for depth)
    """
    try:
        from .view_basis import world_to_view

        bbox = elem.get_BoundingBox(view)
        if not bbox or not bbox.Min or not bbox.Max:
            return []

        # Get min/max corners and transform to host space if needed
        min_pt_local = (bbox.Min.X, bbox.Min.Y, bbox.Min.Z)
        max_pt_local = (bbox.Max.X, bbox.Max.Y, bbox.Max.Z)

        # Transform to host coordinates for linked elements
        from Autodesk.Revit.DB import XYZ
        min_xyz = XYZ(min_pt_local[0], min_pt_local[1], min_pt_local[2])
        max_xyz = XYZ(max_pt_local[0], max_pt_local[1], max_pt_local[2])

        min_pt = _to_host_point(elem, min_xyz)
        max_pt = _to_host_point(elem, max_xyz)
        min_pt_tuple = (min_pt.X, min_pt.Y, min_pt.Z)
        max_pt_tuple = (max_pt.X, max_pt.Y, max_pt.Z)

        # Project to view UVW (with depth)
        min_uvw = world_to_view(min_pt_tuple, view_basis)
        max_uvw = world_to_view(max_pt_tuple, view_basis)

        # Build rectangle (use min depth for occlusion)
        u_min = min(min_uvw[0], max_uvw[0])
        u_max = max(min_uvw[0], max_uvw[0])
        v_min = min(min_uvw[1], max_uvw[1])
        v_max = max(min_uvw[1], max_uvw[1])
        w_min = min(min_uvw[2], max_uvw[2])  # Nearest depth

        if u_min >= u_max or v_min >= v_max:
            return []

        # Store (u, v, w) tuples - all points at same depth for bbox
        points = [
            (u_min, v_min, w_min),
            (u_max, v_min, w_min),
            (u_max, v_max, w_min),
            (u_min, v_max, w_min),
            (u_min, v_min, w_min)  # Close the loop
        ]

        return [{'points': points, 'is_hole': False}]

    except Exception:
        return []


def _obb_silhouette(elem, view, view_basis):
    """Extract oriented bounding box silhouette (all 8 bbox corners).

    Args:
        elem: Revit Element
        view: Revit View
        view_basis: ViewBasis

    Returns:
        List with one loop (convex hull of projected bbox corners, with depth)
    """
    try:
        from Autodesk.Revit.DB import XYZ
        from .view_basis import world_to_view

        bbox = elem.get_BoundingBox(view)
        if not bbox or not bbox.Min or not bbox.Max:
            return []

        # Get all 8 corners of 3D bbox
        mn = bbox.Min
        mx = bbox.Max
        corners_3d = [
            XYZ(mn.X, mn.Y, mn.Z),
            XYZ(mx.X, mn.Y, mn.Z),
            XYZ(mn.X, mx.Y, mn.Z),
            XYZ(mx.X, mx.Y, mn.Z),
            XYZ(mn.X, mn.Y, mx.Z),
            XYZ(mx.X, mn.Y, mx.Z),
            XYZ(mn.X, mx.Y, mx.Z),
            XYZ(mx.X, mx.Y, mx.Z),
        ]

        # Transform to host coordinates for linked elements and project to view UVW
        corners_uvw = []
        min_w = float('inf')
        for corner in corners_3d:
            corner_h = _to_host_point(elem, corner)
            uvw = world_to_view((corner_h.X, corner_h.Y, corner_h.Z), view_basis)
            corners_uvw.append(uvw)
            if uvw[2] < min_w:
                min_w = uvw[2]

        if len(corners_uvw) < 3:
            return []

        # Compute convex hull in UV (ignoring W for hull computation)
        corners_uv = [(uvw[0], uvw[1]) for uvw in corners_uvw]
        hull_uv = _convex_hull_2d(corners_uv)

        if len(hull_uv) < 3:
            return []

        # Add minimum depth to hull points
        hull_uvw = [(u, v, min_w) for (u, v) in hull_uv]

        return [{'points': hull_uvw, 'is_hole': False}]

    except Exception:
        return []


def _silhouette_edges(elem, view, view_basis, cfg):
    """Extract true silhouette edges based on view direction.

    This preserves concave shapes (L, U, C, etc.) by extracting actual
    visible edges rather than using convex hull approximation.

    Args:
        elem: Revit Element
        view: Revit View
        view_basis: ViewBasis
        cfg: Config object

    Returns:
        List with loops representing actual silhouette (preserves concavity)
    """
    try:
        from Autodesk.Revit.DB import Options, ViewDetailLevel, UV
    except Exception:
        return []

    if not hasattr(elem, 'get_Geometry'):
        return []

    # Get view direction for silhouette detection
    try:
        view_direction = view.ViewDirection
    except Exception:
        # Can't determine view direction, fall back
        return []

    # Get geometry with appropriate detail level
    try:
        opts = Options()
        opts.ComputeReferences = False
        opts.IncludeNonVisibleObjects = False
        opts.DetailLevel = ViewDetailLevel.Medium

        # CRITICAL: Never set opts.View for linked elements - causes geometry extraction failure
        # Linked elements (LinkedElementProxy) have .transform attribute
        if not hasattr(elem, 'transform'):  # Host element only
            try:
                opts.View = view
            except Exception:
                pass
        # For linked elements: leave opts.View = None (extract in link coordinates)

        geom = elem.get_Geometry(opts)
        if geom is None:
            return []
    except Exception:
        return []

    # Collect silhouette edges
    silhouette_points = []

    for solid in _iter_solids(geom):
        if not solid or getattr(solid, 'Volume', 0) <= 1e-9:
            continue

        try:
            faces = solid.Faces
        except Exception:
            continue

        if not faces:
            continue

        # Build edge -> faces mapping to identify silhouette edges
        edge_face_map = {}

        for face in faces:
            try:
                # Get face normal at center
                bbox_uv = face.GetBoundingBox()
                if not bbox_uv:
                    continue

                u_mid = (bbox_uv.Min.U + bbox_uv.Max.U) / 2.0
                v_mid = (bbox_uv.Min.V + bbox_uv.Max.V) / 2.0

                try:
                    uv = UV(u_mid, v_mid)
                    normal = face.ComputeNormal(uv)
                except Exception:
                    continue

                # Check if face is front-facing
                # View direction points INTO screen, so negative dot = facing us
                dot = normal.DotProduct(view_direction)
                is_front_facing = dot < 0

                # Get edges of this face
                try:
                    edge_loops = face.EdgeLoops
                except Exception:
                    continue

                if not edge_loops:
                    continue

                for edge_loop in edge_loops:
                    for edge in edge_loop:
                        try:
                            curve = edge.AsCurve()
                            if not curve:
                                continue

                            # Create edge key (order-independent)
                            p0 = curve.GetEndPoint(0)
                            p1 = curve.GetEndPoint(1)

                            key = tuple(sorted([
                                (round(p0.X, 6), round(p0.Y, 6), round(p0.Z, 6)),
                                (round(p1.X, 6), round(p1.Y, 6), round(p1.Z, 6))
                            ]))

                            if key not in edge_face_map:
                                edge_face_map[key] = []

                            edge_face_map[key].append((edge, is_front_facing))

                        except Exception:
                            continue

            except Exception:
                continue

        # Find silhouette edges (boundary or front/back transition)
        for edge_key, face_list in edge_face_map.items():
            is_silhouette = False

            if len(face_list) == 1:
                # Boundary edge - always silhouette
                is_silhouette = True
            elif len(face_list) == 2:
                # Check if front/back transition
                _, is_front_0 = face_list[0]
                _, is_front_1 = face_list[1]
                is_silhouette = (is_front_0 != is_front_1)

            if is_silhouette and len(face_list) > 0:
                edge_obj = face_list[0][0]
                try:
                    curve = edge_obj.AsCurve()
                    if not curve:
                        continue

                    # Tessellate edge
                    try:
                        tess = curve.Tessellate()
                        points_3d = list(tess)
                    except Exception:
                        # Fallback: use endpoints
                        points_3d = [curve.GetEndPoint(0), curve.GetEndPoint(1)]

                    # Project to view UVW (with depth)
                    from .view_basis import world_to_view
                    for pt in points_3d:
                        pt_h = _to_host_point(elem, pt)
                        uvw = world_to_view((pt_h.X, pt_h.Y, pt_h.Z), view_basis)
                        silhouette_points.append(uvw)

                except Exception:
                    continue

    if len(silhouette_points) < 3:
        return []

    # Build polygon from silhouette points
    # For now, use simplified approach: order by connectivity
    # TODO: Implement proper edge chaining for multiple loops
    loop_points = _order_points_by_connectivity(silhouette_points)

    if len(loop_points) >= 3:
        return [{'points': loop_points, 'is_hole': False}]
    else:
        return []


def _order_points_by_connectivity(points):
    """Order points by spatial connectivity (simple greedy approach).

    Args:
        points: List of (u, v) or (u, v, w) points

    Returns:
        Ordered list of points forming a closed loop
    """
    if len(points) < 3:
        return []

    # Remove duplicates (use UV for comparison, preserve W if present)
    unique_points = []
    seen = set()
    for p in points:
        key = (round(p[0], 6), round(p[1], 6))
        if key not in seen:
            seen.add(key)
            unique_points.append(p)

    if len(unique_points) < 3:
        return []

    # Greedy nearest-neighbor ordering (use UV distance only)
    ordered = [unique_points[0]]
    remaining = set(range(1, len(unique_points)))

    while remaining:
        current = ordered[-1]
        best_idx = None
        best_dist = float('inf')

        for idx in remaining:
            pt = unique_points[idx]
            # Distance in UV plane only
            dist = (pt[0] - current[0]) ** 2 + (pt[1] - current[1]) ** 2

            if dist < best_dist:
                best_dist = dist
                best_idx = idx

        if best_idx is not None:
            ordered.append(unique_points[best_idx])
            remaining.remove(best_idx)
        else:
            break

    # Close the loop
    if len(ordered) >= 3:
        if ordered[0] != ordered[-1]:
            ordered.append(ordered[0])

    return ordered


def _iter_solids(geom):
    """Iterate all solids in geometry (recursively).

    Args:
        geom: Geometry object

    Yields:
        Solid objects
    """
    try:
        from Autodesk.Revit.DB import Solid, GeometryInstance
    except Exception:
        return

    if geom is None:
        return

    try:
        for obj in geom:
            if obj is None:
                continue

            try:
                if isinstance(obj, GeometryInstance):
                    inst = obj.GetInstanceGeometry()
                    for s in _iter_solids(inst):
                        yield s
                    continue
            except Exception:
                pass

            try:
                if isinstance(obj, Solid) and getattr(obj, 'Volume', 0) > 1e-9:
                    yield obj
            except Exception:
                continue
    except Exception:
        return


def _convex_hull_2d(points):
    """Compute 2D convex hull using Andrew's monotone chain algorithm.

    Args:
        points: List of (u, v) tuples

    Returns:
        List of (u, v) points forming convex hull (closed loop)
    """
    if len(points) < 3:
        return []

    try:
        # Remove duplicates and sort
        pts = sorted(set((float(p[0]), float(p[1])) for p in points))

        if len(pts) < 3:
            return []

        def cross(o, a, b):
            """2D cross product."""
            return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

        # Build lower hull
        lower = []
        for p in pts:
            while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
                lower.pop()
            lower.append(p)

        # Build upper hull
        upper = []
        for p in reversed(pts):
            while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
                upper.pop()
            upper.append(p)

        # Concatenate and close
        hull = lower[:-1] + upper[:-1]

        if len(hull) >= 3:
            # Close the loop
            if hull[0] != hull[-1]:
                hull.append(hull[0])
            return hull
        else:
            return []

    except Exception:
        return []
