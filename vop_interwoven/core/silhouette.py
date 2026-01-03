"""
Silhouette extraction for VOP interwoven pipeline.

Provides simplified silhouette extraction with adaptive strategies:
- bbox: Axis-aligned bounding box (baseline)
- obb: Oriented bounding box (projected 3D corners)
- coarse_tess: Coarse tessellation with convex hull

Falls back gracefully to bbox when strategies fail.
Compatible with IronPython (no logging module, no f-strings).
"""


def get_element_silhouette(elem, view, view_basis, raster, cfg=None):
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
            'strategy': 'bbox'|'obb'|'coarse_tess'|'silhouette_edges'  # Which strategy was used
        }

    Commentary:
        - Returns empty list if element has no geometry
        - Tries strategies in order based on element UV mode (TINY/LINEAR/AREAL)
        - bbox is the ultimate fallback (always succeeds if element has bbox)
    """
    if cfg is None:
        # Default: try silhouette_edges first for accuracy, then bbox
        strategies = ['silhouette_edges', 'bbox']
    else:
        # Get UV mode (shape-based) strategy list from config
        uv_mode = _determine_uv_mode(elem, view, view_basis, raster, cfg)
        strategies = cfg.get_silhouette_strategies(uv_mode)

    # Try each strategy in order
    for strategy_name in strategies:
        try:
            if strategy_name == 'bbox':
                loops = _bbox_silhouette(elem, view, view_basis)
            elif strategy_name == 'obb':
                loops = _obb_silhouette(elem, view, view_basis)
            elif strategy_name == 'coarse_tess':
                loops = _coarse_tess_silhouette(elem, view, view_basis, cfg)
            elif strategy_name == 'silhouette_edges':
                loops = _silhouette_edges(elem, view, view_basis, cfg)
            else:
                continue

            if loops:
                # Tag loops with strategy used
                for loop in loops:
                    loop['strategy'] = strategy_name
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


def _determine_uv_mode(elem, view, view_basis, raster, cfg):
    """Classify element by UV mode (shape): TINY, LINEAR, or AREAL.

    Args:
        elem: Revit Element
        view: Revit View
        view_basis: ViewBasis for UV projection
        raster: ViewRaster (provides bounds and cell size)
        cfg: Config object

    Returns:
        'TINY', 'LINEAR', or 'AREAL'

    Commentary:
        Uses same classification logic as classify_by_uv from geometry.py:
        - TINY: U <= tiny_max AND V <= tiny_max (small in both dimensions)
        - LINEAR: min(U,V) <= thin_max AND max(U,V) > tiny_max (thin but long)
        - AREAL: Large area elements (everything else)
    """
    try:
        bbox = elem.get_BoundingBox(view)
        if not bbox or not bbox.Min or not bbox.Max:
            return 'AREAL'  # Default to AREAL (most conservative)

        # Project bbox to view UV and get cell dimensions
        min_uv = view_basis.transform_to_view_uv((bbox.Min.X, bbox.Min.Y, bbox.Min.Z))
        max_uv = view_basis.transform_to_view_uv((bbox.Max.X, bbox.Max.Y, bbox.Max.Z))

        # Get UV extents in feet
        u_extent = abs(max_uv[0] - min_uv[0])
        v_extent = abs(max_uv[1] - min_uv[1])

        # Convert to cells
        U = int(u_extent / raster.cell_size_ft)
        V = int(v_extent / raster.cell_size_ft)

        # Get thresholds from config
        tiny_max = cfg.tiny_max
        thin_max = cfg.thin_max

        # Classify using same logic as classify_by_uv
        if U <= tiny_max and V <= tiny_max:
            return 'TINY'
        elif min(U, V) <= thin_max:
            return 'LINEAR'
        else:
            return 'AREAL'

    except Exception:
        return 'AREAL'  # Default to AREAL (most conservative)


def _bbox_silhouette(elem, view, view_basis):
    """Extract axis-aligned bounding box silhouette.

    Args:
        elem: Revit Element
        view: Revit View
        view_basis: ViewBasis

    Returns:
        List with one loop (rectangle in view UV coordinates)
    """
    try:
        bbox = elem.get_BoundingBox(view)
        if not bbox or not bbox.Min or not bbox.Max:
            return []

        # Get min/max corners
        min_pt = (bbox.Min.X, bbox.Min.Y, bbox.Min.Z)
        max_pt = (bbox.Max.X, bbox.Max.Y, bbox.Max.Z)

        # Project to view UV
        min_uv = view_basis.transform_to_view_uv(min_pt)
        max_uv = view_basis.transform_to_view_uv(max_pt)

        # Build rectangle
        u_min = min(min_uv[0], max_uv[0])
        u_max = max(min_uv[0], max_uv[0])
        v_min = min(min_uv[1], max_uv[1])
        v_max = max(min_uv[1], max_uv[1])

        if u_min >= u_max or v_min >= v_max:
            return []

        points = [
            (u_min, v_min),
            (u_max, v_min),
            (u_max, v_max),
            (u_min, v_max),
            (u_min, v_min)  # Close the loop
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
        List with one loop (convex hull of projected bbox corners)
    """
    try:
        from Autodesk.Revit.DB import XYZ

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

        # Project to view UV
        corners_uv = []
        for corner in corners_3d:
            uv = view_basis.transform_to_view_uv((corner.X, corner.Y, corner.Z))
            corners_uv.append(uv)

        if len(corners_uv) < 3:
            return []

        # Compute convex hull
        hull = _convex_hull_2d(corners_uv)

        if len(hull) < 3:
            return []

        return [{'points': hull, 'is_hole': False}]

    except Exception:
        return []


def _coarse_tess_silhouette(elem, view, view_basis, cfg):
    """Extract silhouette from coarse tessellation.

    Args:
        elem: Revit Element
        view: Revit View
        view_basis: ViewBasis
        cfg: Config object

    Returns:
        List with one loop (convex hull of tessellated points)
    """
    try:
        from Autodesk.Revit.DB import Options, ViewDetailLevel

        if not hasattr(elem, 'get_Geometry'):
            return []

        # Coarse geometry options
        opts = Options()
        opts.ComputeReferences = False
        opts.IncludeNonVisibleObjects = False
        opts.DetailLevel = ViewDetailLevel.Coarse

        try:
            opts.View = view
        except Exception:
            pass

        geom = elem.get_Geometry(opts)
        if geom is None:
            return []

        # Collect tessellated points
        points_uv = []
        max_verts = getattr(cfg, 'coarse_tess_max_verts', 20) if cfg else 20

        for solid in _iter_solids(geom):
            try:
                faces = solid.Faces
            except Exception:
                continue

            for face in faces:
                try:
                    mesh = face.Triangulate(0.5)  # Coarse tessellation
                except Exception:
                    continue

                if mesh is None:
                    continue

                try:
                    vcount = int(mesh.NumVertices)
                except Exception:
                    continue

                # Sample vertices (skip some if too many)
                step = max(1, vcount // max_verts)

                for i in range(0, vcount, step):
                    try:
                        p = mesh.get_Vertex(i)
                        uv = view_basis.transform_to_view_uv((p.X, p.Y, p.Z))
                        points_uv.append(uv)
                    except Exception:
                        continue

        if len(points_uv) < 3:
            return []

        # Compute convex hull
        hull = _convex_hull_2d(points_uv)

        if len(hull) < 3:
            return []

        return [{'points': hull, 'is_hole': False}]

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

        try:
            opts.View = view
        except Exception:
            pass

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

                    # Project to view UV
                    for pt in points_3d:
                        uv = view_basis.transform_to_view_uv((pt.X, pt.Y, pt.Z))
                        silhouette_points.append(uv)

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
        points: List of (u, v) points

    Returns:
        Ordered list of points forming a closed loop
    """
    if len(points) < 3:
        return []

    # Remove duplicates
    unique_points = []
    seen = set()
    for p in points:
        key = (round(p[0], 6), round(p[1], 6))
        if key not in seen:
            seen.add(key)
            unique_points.append(p)

    if len(unique_points) < 3:
        return []

    # Greedy nearest-neighbor ordering
    ordered = [unique_points[0]]
    remaining = set(range(1, len(unique_points)))

    while remaining:
        current = ordered[-1]
        best_idx = None
        best_dist = float('inf')

        for idx in remaining:
            pt = unique_points[idx]
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
