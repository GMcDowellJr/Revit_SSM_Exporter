"""
Silhouette extraction for VOP interwoven pipeline.

Provides simplified silhouette extraction with adaptive strategies:
- bbox: Axis-aligned bounding box (baseline)
- obb: Oriented bounding box (projected 3D corners)
- coarse_tess: Coarse tessellation with convex hull

Falls back gracefully to bbox when strategies fail.
Compatible with IronPython (no logging module, no f-strings).
"""


def get_element_silhouette(elem, view, view_basis, cfg=None):
    """Extract element silhouette as 2D loops.

    Args:
        elem: Revit Element
        view: Revit View
        view_basis: ViewBasis object for coordinate transformation
        cfg: Optional Config object (provides strategy settings)

    Returns:
        List of loop dicts, each with:
        {
            'points': [(u, v), ...],  # View-local UV coordinates
            'is_hole': False,          # Whether this loop is a hole
            'strategy': 'bbox'|'obb'|'coarse_tess'  # Which strategy was used
        }

    Commentary:
        - Returns empty list if element has no geometry
        - Tries strategies in order: obb -> coarse_tess -> bbox
        - bbox is the ultimate fallback (always succeeds if element has bbox)
    """
    if cfg is None:
        # Default: try obb first, then bbox
        strategies = ['obb', 'bbox']
    else:
        # Get size-based strategy list from config
        size_tier = _determine_size_tier(elem, view, cfg)
        strategies = cfg.get_silhouette_strategies(size_tier)

    # Try each strategy in order
    for strategy_name in strategies:
        try:
            if strategy_name == 'bbox':
                loops = _bbox_silhouette(elem, view, view_basis)
            elif strategy_name == 'obb':
                loops = _obb_silhouette(elem, view, view_basis)
            elif strategy_name == 'coarse_tess':
                loops = _coarse_tess_silhouette(elem, view, view_basis, cfg)
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


def _determine_size_tier(elem, view, cfg):
    """Classify element by size: tiny_linear, medium, large.

    Args:
        elem: Revit Element
        view: Revit View
        cfg: Config object

    Returns:
        'tiny_linear', 'medium', or 'large'
    """
    try:
        bbox = elem.get_BoundingBox(view)
        if not bbox or not bbox.Min or not bbox.Max:
            return 'medium'

        # Compute max dimension in feet
        dx = abs(bbox.Max.X - bbox.Min.X)
        dy = abs(bbox.Max.Y - bbox.Min.Y)
        dz = abs(bbox.Max.Z - bbox.Min.Z)
        max_dim_ft = max(dx, dy, dz)

        # Use absolute thresholds (scale-independent)
        tiny_thresh = getattr(cfg, 'silhouette_tiny_thresh_ft', 3.0)
        large_thresh = getattr(cfg, 'silhouette_large_thresh_ft', 20.0)

        if max_dim_ft <= tiny_thresh:
            return 'tiny_linear'
        elif max_dim_ft <= large_thresh:
            return 'medium'
        else:
            return 'large'

    except Exception:
        return 'medium'


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
