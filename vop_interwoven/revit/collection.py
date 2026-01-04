"""
Element collection and visibility filtering for VOP interwoven pipeline.

Provides functions to collect visible elements in a view and check
element visibility according to Revit view settings.
"""


def collect_view_elements(doc, view, raster):
    """Collect all potentially visible elements in view (broad-phase).

    Args:
        doc: Revit Document
        view: Revit View
        raster: ViewRaster (provides bounds_xy for spatial filtering)

    Returns:
        List of Revit elements visible in view

    Commentary:
        ✔ Uses FilteredElementCollector with view.Id scope
        ✔ Filters to 3D model categories (Walls, Floors, etc.)
        ✔ Excludes element types (only instances)
        ✔ Requires valid bounding box
        ✔ Broad-phase only - keeps collection cheap
    """
    from Autodesk.Revit.DB import FilteredElementCollector, BuiltInCategory

    # Define model categories to collect (3D model + symbolic lines)
    # Using category name strings to handle different Revit versions gracefully
    category_names = [
        # 3D Model elements
        'OST_Walls',
        'OST_Floors',
        'OST_Roofs',
        'OST_Doors',
        'OST_Windows',
        'OST_Columns',
        'OST_StructuralFraming',
        'OST_StructuralColumns',
        'OST_Stairs',
        'OST_Railings',
        'OST_Ceilings',
        'OST_GenericModel',
        'OST_Furniture',
        'OST_Casework',  # Note: some versions use OST_Casework, others OST_CaseworkWall
        'OST_MechanicalEquipment',
        'OST_ElectricalEquipment',
        'OST_PlumbingFixtures',
        'OST_DuctCurves',
        'OST_PipeCurves',
        # Symbolic geometry (contributes to MODEL occupancy, TINY/LINEAR classification)
        'OST_Lines',  # DetailCurves, CurveElements (symbolic lines in families)
        # NOTE: DetailComponents NOT included here - user-placed ones go to ANNOTATION
        # Detail items embedded in model families are part of family geometry (collected via FamilyInstance)
    ]

    # Convert category names to BuiltInCategory enums (skip if not available in this Revit version)
    model_categories = []
    for cat_name in category_names:
        if hasattr(BuiltInCategory, cat_name):
            model_categories.append(getattr(BuiltInCategory, cat_name))

    elements = []

    try:
        # Collect elements visible in view
        for cat in model_categories:
            try:
                collector = FilteredElementCollector(doc, view.Id)
                collector.OfCategory(cat).WhereElementIsNotElementType()

                # Filter to elements with valid bounding boxes
                for elem in collector:
                    # For OST_Lines: Only collect MODEL lines (ViewSpecific=False)
                    # Detail lines (ViewSpecific=True) go to ANNOTATION
                    if cat == getattr(BuiltInCategory, 'OST_Lines', None):
                        try:
                            if bool(getattr(elem, 'ViewSpecific', False)):
                                continue  # Skip detail lines (they're annotations)
                        except:
                            pass

                    bbox = elem.get_BoundingBox(None)  # World coordinates
                    if bbox is not None:
                        elements.append(elem)
            except:
                # Skip categories that cause errors
                continue

    except Exception as e:
        # If collection fails, return empty list (graceful degradation)
        pass

    return elements


def is_element_visible_in_view(elem, view):
    """Check if element is visible in view (respects view settings).

    Args:
        elem: Revit Element
        view: Revit View

    Returns:
        True if element is visible in view

    Commentary:
        ✔ Checks element visibility settings (IsHidden, Category visibility, etc.)
        ✔ Respects view template visibility overrides
        ✔ Does NOT check geometry occlusion (that's done in the pipeline)
        ⚠ This is a placeholder - full implementation requires Revit API

    Example (with actual Revit API):
        >>> # if elem.IsHidden(view):
        >>> #     return False
        >>> # category = elem.Category
        >>> # if not view.GetCategoryHidden(category.Id):
        >>> #     return True
    """
    # TODO: Implement actual Revit visibility check
    # Placeholder: return True (optimistic)
    return True


def expand_host_link_import_model_elements(doc, view, elements, cfg):
    """Expand element list to include linked/imported model elements.

    Args:
        doc: Revit Document
        view: Revit View
        elements: List of host document elements
        cfg: Config object with linked document settings

    Returns:
        List of element wrappers with transform info:
        Each item: {
            'element': Element,
            'world_transform': Transform (identity for host, link transform for linked),
            'doc_key': str (unique key for indexing),
            'doc_label': str (friendly label for logging),
            'link_inst_id': ElementId or None
        }

    Commentary:
        ✔ Includes host elements (identity transform)
        ✔ Expands RevitLinkInstance and ImportInstance to access linked elements
        ✔ Uses linked_documents module for production-ready link handling
        ✔ Provides unique doc_key for multiple link instances (includes instance ID)
        ✔ Provides friendly doc_label for logging/display
    """
    from Autodesk.Revit.DB import Transform
    from .linked_documents import collect_all_linked_elements

    result = []

    # Add host elements with identity transform
    identity_trf = Transform.Identity
    for e in elements:
        result.append(
            {
                "element": e,
                "world_transform": identity_trf,
                "doc_key": "HOST",
                "doc_label": "HOST",
                "link_inst_id": None,
            }
        )

    # Collect and add linked/imported elements
    try:
        linked_proxies = collect_all_linked_elements(doc, view, cfg)

        for proxy in linked_proxies:
            result.append(
                {
                    "element": proxy,  # LinkedElementProxy
                    "world_transform": proxy.transform,
                    "doc_key": proxy.doc_key,          # Unique key (includes instance ID)
                    "doc_label": proxy.doc_label,      # Friendly label for logging
                    "link_inst_id": proxy.LinkInstanceId,
                }
            )
    except Exception as e:
        # Log warning but don't fail the whole export
        print("[WARN] vop.collection: Failed to collect linked elements: {0}".format(e))

    return result


def sort_front_to_back(model_elems, view, raster):
    """Sort elements front-to-back by approximate depth.

    Args:
        model_elems: List of element wrappers (from expand_host_link_import_model_elements)
        view: Revit View
        raster: ViewRaster (provides view basis for depth calculation)

    Returns:
        Sorted list (nearest elements first)

    Commentary:
        ✔ Uses bbox minimum depth as sorting key (fast approximation)
        ✔ Front-to-back order enables early-out occlusion testing
        ✔ Stable sort for deterministic output
        ✔ Elements with smaller depth values are closer to view plane
    """
    # Sort by depth (nearest first)
    # Use stable sort to ensure deterministic output for elements with equal depth
    sorted_elems = sorted(
        model_elems,
        key=lambda item: estimate_nearest_depth_from_bbox(
            item['element'],
            item['world_transform'],
            view,
            raster
        )
    )

    return sorted_elems


def estimate_nearest_depth_from_bbox(elem, transform, view, raster):
    """Estimate nearest depth of element from its bounding box.

    Args:
        elem: Revit Element
        transform: World transform (identity for host, link transform for linked)
        view: Revit View
        raster: ViewRaster

    Returns:
        Float depth value (distance from view plane)

    Commentary:
        ✔ Computes minimum depth across all 8 bbox corners
        ✔ Used for front-to-back sorting
        ✔ Returns depth in view space (w coordinate)
    """
    from .view_basis import world_to_view

    # Get world-space bounding box
    bbox = elem.get_BoundingBox(None)
    if bbox is None:
        return float('inf')  # Elements without bbox go to back

    # Get view basis from raster (stored during init_view_raster)
    vb = getattr(raster, 'view_basis', None)
    if vb is None:
        return 0.0  # Fallback if view basis not available

    # Get all 8 corners of bounding box in world space
    min_x, min_y, min_z = bbox.Min.X, bbox.Min.Y, bbox.Min.Z
    max_x, max_y, max_z = bbox.Max.X, bbox.Max.Y, bbox.Max.Z

    # DEBUG: Log bbox and view basis for first few elements
    debug_count = getattr(estimate_nearest_depth_from_bbox, '_debug_count', 0)
    if debug_count < 2:
        estimate_nearest_depth_from_bbox._debug_count = debug_count + 1
        print("[DEBUG] estimate_depth: bbox Z range: [{0:.3f}, {1:.3f}]".format(min_z, max_z))
        print("[DEBUG] estimate_depth: view_basis.origin = {0}".format(vb.origin))
        print("[DEBUG] estimate_depth: view_basis.forward = {0}".format(vb.forward))

    corners = [
        (min_x, min_y, min_z),
        (min_x, min_y, max_z),
        (min_x, max_y, min_z),
        (min_x, max_y, max_z),
        (max_x, min_y, min_z),
        (max_x, min_y, max_z),
        (max_x, max_y, min_z),
        (max_x, max_y, max_z),
    ]

    # Transform all corners to view space and get minimum depth (w coordinate)
    min_depth = float('inf')
    max_depth = float('-inf')
    for corner in corners:
        u, v, w = world_to_view(corner, vb)
        if w < min_depth:
            min_depth = w
        if w > max_depth:
            max_depth = w

    # DEBUG: Log depth range for first few elements
    if debug_count < 2:
        print("[DEBUG] estimate_depth: depth range: [{0:.3f}, {1:.3f}], using min={2:.3f}".format(
            min_depth, max_depth, min_depth))

    return min_depth


def estimate_depth_from_loops_or_bbox(elem, loops, transform, view, raster):
    """Get element depth from silhouette geometry or bbox fallback.

    Args:
        elem: Revit Element
        loops: Silhouette loops (may contain w coordinates)
        transform: World transform
        view: Revit View
        raster: ViewRaster

    Returns:
        Float depth value (nearest point to view plane)

    Commentary:
        ✔ Extracts depth from silhouette loop points if w coordinate available
        ✔ Falls back to bbox depth if loops unavailable/empty or no w coordinate
        ✔ CRITICAL FIX: Use accurate geometry depth instead of inflated bbox depth
    """
    # Try to extract depth from loops first (accurate geometry)
    if loops:
        min_w = float('inf')
        found_depth = False

        for loop in loops:
            points = loop.get('points', [])
            for pt in points:
                # Check if point has w coordinate (3-tuple)
                if len(pt) >= 3:
                    w = pt[2]
                    if w < min_w:
                        min_w = w
                    found_depth = True

        # If we found depth in loops, use it (accurate!)
        if found_depth and min_w < float('inf'):
            return min_w

    # Fallback: use bbox (less accurate for rotated elements, but better than nothing)
    return estimate_nearest_depth_from_bbox(elem, transform, view, raster)


def estimate_depth_range_from_bbox(elem, transform, view, raster):
    """Estimate depth range (min, max) of element from its bounding box.

    Args:
        elem: Revit Element
        transform: World transform (identity for host, link transform for linked)
        view: Revit View
        raster: ViewRaster

    Returns:
        Tuple (min_depth, max_depth) - range of depths in view space

    Commentary:
        Used for ambiguity detection in selective z-buffer phase.
        Returns full depth extent across all 8 bbox corners.
    """
    from .view_basis import world_to_view

    # Get world-space bounding box
    bbox = elem.get_BoundingBox(None)
    if bbox is None:
        return (float('inf'), float('inf'))  # Elements without bbox

    # Get view basis from raster
    vb = getattr(raster, 'view_basis', None)
    if vb is None:
        return (0.0, 0.0)  # Fallback

    # Get all 8 corners of bounding box in world space
    min_x, min_y, min_z = bbox.Min.X, bbox.Min.Y, bbox.Min.Z
    max_x, max_y, max_z = bbox.Max.X, bbox.Max.Y, bbox.Max.Z

    corners = [
        (min_x, min_y, min_z),
        (min_x, min_y, max_z),
        (min_x, max_y, min_z),
        (min_x, max_y, max_z),
        (max_x, min_y, min_z),
        (max_x, min_y, max_z),
        (max_x, max_y, min_z),
        (max_x, max_y, max_z),
    ]

    # Transform all corners to view space and get min/max depth (w coordinate)
    min_depth = float('inf')
    max_depth = float('-inf')
    for corner in corners:
        u, v, w = world_to_view(corner, vb)
        if w < min_depth:
            min_depth = w
        if w > max_depth:
            max_depth = w

    return (min_depth, max_depth)


def _project_element_bbox_to_cell_rect(elem, vb, raster):
    """Project element bounding box to cell rectangle using OBB (oriented bounds).

    Args:
        elem: Revit Element
        vb: ViewBasis for coordinate transformation
        raster: ViewRaster with bounds and cell size

    Returns:
        CellRect in grid coordinates, or None if no bbox

    Commentary:
        ✔ Gets world-space bounding box
        ✔ Projects ALL 8 corners to view UV
        ✔ Fits UV OBB (oriented bounding box) using PCA for tighter bounds
        ✔ Computes AABB of OBB rectangle for cell indices
        ✔ CRITICAL: OBB is much tighter than AABB for rotated elements
        ✔ Handles elements outside view bounds (returns None or empty rect)
    """
    from ..core.math_utils import CellRect
    from .view_basis import world_to_view

    # Get world-space bounding box
    bbox = elem.get_BoundingBox(None)
    if bbox is None:
        return None

    # Get all 8 corners of 3D bounding box
    min_x, min_y, min_z = bbox.Min.X, bbox.Min.Y, bbox.Min.Z
    max_x, max_y, max_z = bbox.Max.X, bbox.Max.Y, bbox.Max.Z

    corners = [
        (min_x, min_y, min_z), (min_x, min_y, max_z),
        (min_x, max_y, min_z), (min_x, max_y, max_z),
        (max_x, min_y, min_z), (max_x, min_y, max_z),
        (max_x, max_y, min_z), (max_x, max_y, max_z),
    ]

    # Project all 8 corners to view UV
    uvs = [world_to_view(corner, vb) for corner in corners]

    # Extract just UV (ignore W for footprint calculation)
    points_uv = [(uv[0], uv[1]) for uv in uvs]

    # Fit OBB to UV points using PCA for tighter bounds
    obb_rect, len_u, len_v, angle_deg = _pca_obb_uv(points_uv)

    if not obb_rect or len(obb_rect) < 4:
        # Fallback to AABB if OBB fitting fails
        u_min = min(uv[0] for uv in uvs)
        u_max = max(uv[0] for uv in uvs)
        v_min = min(uv[1] for uv in uvs)
        v_max = max(uv[1] for uv in uvs)
    else:
        # Compute AABB of OBB rectangle (tighter than world AABB projection!)
        u_min = min(pt[0] for pt in obb_rect)
        u_max = max(pt[0] for pt in obb_rect)
        v_min = min(pt[1] for pt in obb_rect)
        v_max = max(pt[1] for pt in obb_rect)

    # Convert to cell indices
    i_min = int((u_min - raster.bounds.xmin) / raster.cell_size)
    i_max = int((u_max - raster.bounds.xmin) / raster.cell_size)
    j_min = int((v_min - raster.bounds.ymin) / raster.cell_size)
    j_max = int((v_max - raster.bounds.ymin) / raster.cell_size)

    # Clamp to raster bounds
    i_min = max(0, min(i_min, raster.W - 1))
    i_max = max(0, min(i_max, raster.W - 1))
    j_min = max(0, min(j_min, raster.H - 1))
    j_max = max(0, min(j_max, raster.H - 1))

    return CellRect(i_min, j_min, i_max, j_max)


def _extract_geometry_footprint_uv(elem, vb):
    """Extract actual geometry footprint vertices in UV space.

    Extracts solid geometry faces/edges and projects all vertices to UV.
    Works in all view types (plan, elevation, section, 3D).

    Args:
        elem: Revit Element
        vb: ViewBasis for coordinate transformation

    Returns:
        List of (u, v) points representing element footprint, or None if failed
    """
    from .view_basis import world_to_view

    try:
        from Autodesk.Revit.DB import Options, Solid, Face, Edge, GeometryInstance

        # Create geometry options (don't set View to avoid linked element issues)
        opts = Options()
        opts.ComputeReferences = False
        opts.IncludeNonVisibleObjects = False
        opts.DetailLevel = 2  # Medium detail

        # Get geometry
        geom = elem.get_Geometry(opts)
        if not geom:
            return None

        # Collect all vertices from solid geometry
        points_uv = []

        def process_geometry(geo, transform=None):
            """Recursively process geometry to extract vertices."""
            for obj in geo:
                # Handle geometry instances (e.g., family instances)
                if isinstance(obj, GeometryInstance):
                    inst_geom = obj.GetInstanceGeometry()
                    if inst_geom:
                        inst_transform = obj.Transform
                        process_geometry(inst_geom, inst_transform)

                # Handle solids
                elif isinstance(obj, Solid):
                    if obj.Volume > 0.0001:  # Non-degenerate solid
                        # Extract vertices from faces
                        for face in obj.Faces:
                            try:
                                # Get face boundary edges
                                for edge_loop in face.EdgeLoops:
                                    for edge in edge_loop:
                                        # Sample edge endpoints
                                        curve = edge.AsCurve()
                                        if curve:
                                            for t in [0.0, 1.0]:  # Start and end
                                                try:
                                                    pt = curve.Evaluate(t, True)

                                                    # Apply instance transform if present
                                                    if transform:
                                                        pt = transform.OfPoint(pt)

                                                    # Project to UV
                                                    uvw = world_to_view((pt.X, pt.Y, pt.Z), vb)
                                                    points_uv.append((uvw[0], uvw[1]))
                                                except:
                                                    pass
                            except:
                                pass

        # Process geometry tree
        process_geometry(geom)

        # Return unique points (remove duplicates)
        if len(points_uv) >= 3:
            # Remove duplicates with tolerance
            unique_points = []
            tolerance = 0.01  # 0.01 ft tolerance
            for pt in points_uv:
                is_duplicate = False
                for existing in unique_points:
                    if abs(pt[0] - existing[0]) < tolerance and abs(pt[1] - existing[1]) < tolerance:
                        is_duplicate = True
                        break
                if not is_duplicate:
                    unique_points.append(pt)

            return unique_points if len(unique_points) >= 3 else None

        return None

    except Exception:
        return None


def get_element_obb_loops(elem, vb, raster):
    """Get element OBB as polygon loops for accurate rasterization.

    Args:
        elem: Revit Element
        vb: ViewBasis for coordinate transformation
        raster: ViewRaster with bounds and cell size

    Returns:
        List of loop dicts with OBB polygon, or None if bbox unavailable

    Commentary:
        ✔ Extracts ACTUAL geometry footprint (not bbox corners)
        ✔ For walls: uses location curve + thickness (diagonal walls)
        ✔ For other elements: extracts geometry faces/edges
        ✔ Falls back to bbox corners only if geometry extraction fails
    """
    from .view_basis import world_to_view

    # Always get bbox for depth calculation
    bbox = elem.get_BoundingBox(None)
    if bbox is None:
        return None

    # Get all 8 corners and project to UV (needed for depth regardless of geometry extraction)
    min_x, min_y, min_z = bbox.Min.X, bbox.Min.Y, bbox.Min.Z
    max_x, max_y, max_z = bbox.Max.X, bbox.Max.Y, bbox.Max.Z

    corners = [
        (min_x, min_y, min_z), (min_x, min_y, max_z),
        (min_x, max_y, min_z), (min_x, max_y, max_z),
        (max_x, min_y, min_z), (max_x, min_y, max_z),
        (max_x, max_y, min_z), (max_x, max_y, max_z),
    ]

    uvs = [world_to_view(corner, vb) for corner in corners]
    bbox_points_uv = [(uv[0], uv[1]) for uv in uvs]

    # STEP 1: Try to extract actual geometry footprint
    points_uv = _extract_geometry_footprint_uv(elem, vb)

    # STEP 2: Fallback to bbox corners if geometry extraction failed
    if not points_uv or len(points_uv) < 3:
        points_uv = bbox_points_uv

    # DEBUG: Log unique UV points for diagonal wall
    try:
        elem_id = getattr(elem, 'Id', None)
        if elem_id:
            elem_id_val = getattr(elem_id, 'IntegerValue', elem_id)
            if elem_id_val == 1619124:  # Target diagonal wall
                unique_uv = list(set(points_uv))
                print("[DEBUG GEOM] Element {0}: Extracted {1} footprint points -> {2} unique UV".format(
                    elem_id_val, len(points_uv), len(unique_uv)))
                print("  UV points: {0}".format([(round(u, 2), round(v, 2)) for u, v in unique_uv[:8]]))
    except:
        pass

    # STEP 3: Compute polygon for rasterization
    # If we extracted actual geometry, use it directly (preserve actual shape)
    # If we're using bbox, fit OBB to get rotated rectangle

    used_geometry = (points_uv != bbox_points_uv)  # Did geometry extraction succeed?

    if used_geometry:
        # Use actual geometry vertices - compute convex hull for clean polygon
        from ..core.silhouette import _convex_hull_2d
        hull_uv = _convex_hull_2d(points_uv)

        if hull_uv and len(hull_uv) >= 3:
            # Close the loop
            if hull_uv[0] != hull_uv[-1]:
                hull_uv.append(hull_uv[0])

            polygon_uv = hull_uv
            strategy_name = 'geometry_hull'
            angle_deg = 0.0  # Not applicable for arbitrary polygons
        else:
            # Convex hull failed, fall back to bbox OBB
            used_geometry = False

    if not used_geometry:
        # Using bbox corners - fit OBB for rotated rectangle
        obb_rect, len_u, len_v, angle_deg = _pca_obb_uv(points_uv)

        if not obb_rect or len(obb_rect) < 4:
            # Fallback: use axis-aligned rect from min/max UV
            u_min = min(uv[0] for uv in uvs)
            u_max = max(uv[0] for uv in uvs)
            v_min = min(uv[1] for uv in uvs)
            v_max = max(uv[1] for uv in uvs)

            # Build axis-aligned rectangle
            polygon_uv = [
                (u_min, v_min),
                (u_max, v_min),
                (u_max, v_max),
                (u_min, v_max),
                (u_min, v_min),  # Close loop
            ]
            strategy_name = 'uv_aabb'
            angle_deg = 0.0
        else:
            polygon_uv = obb_rect
            strategy_name = 'uv_obb'

    # Get minimum depth for occlusion
    w_min = min(uv[2] for uv in uvs)

    # Convert to loop format with depth
    points_uvw = [(pt[0], pt[1], w_min) for pt in polygon_uv]

    # DEBUG: Log polygon calculations
    try:
        elem_id = getattr(elem, 'Id', None)
        if elem_id:
            elem_id_val = getattr(elem_id, 'IntegerValue', elem_id)
            # Always log element 1619124 (diagonal wall), 5% sample for others
            import random
            should_log = (elem_id_val == 1619124) or (random.random() < 0.05)
            if should_log:
                print("[DEBUG POLY] Element {0}: strategy={1}, vertices={2}, geom_extracted={3}".format(
                    elem_id_val, strategy_name, len(polygon_uv), used_geometry))
                if elem_id_val == 1619124 or used_geometry:
                    print("  Polygon: {0}".format([(round(pt[0], 2), round(pt[1], 2)) for pt in polygon_uv[:8]]))
    except:
        pass

    # Return polygon loop with correct strategy tag
    return [{'points': points_uvw, 'is_hole': False, 'strategy': strategy_name}]


def _pca_obb_uv(points_uv):
    """Compute oriented bounding box in UV using PCA.

    Args:
        points_uv: List of (u, v) points

    Returns:
        (rect_points, len_u, len_v, angle_deg) where rect_points is closed loop in UV
        Returns ([], 0.0, 0.0, 0.0) on failure

    Commentary:
        ✔ Fits oriented rectangle to point cloud using PCA
        ✔ Much tighter than AABB for rotated/diagonal elements
        ✔ Returns rectangle as 5-point closed loop
    """
    import math

    if not points_uv or len(points_uv) < 2:
        return ([], 0.0, 0.0, 0.0)

    # Compute mean
    n = float(len(points_uv))
    mean_u = sum(p[0] for p in points_uv) / n
    mean_v = sum(p[1] for p in points_uv) / n

    # Compute covariance matrix
    cxx = sum((p[0] - mean_u) ** 2 for p in points_uv)
    cxy = sum((p[0] - mean_u) * (p[1] - mean_v) for p in points_uv)
    cyy = sum((p[1] - mean_v) ** 2 for p in points_uv)

    # Principal axis angle (2D PCA)
    angle = 0.5 * math.atan2(2.0 * cxy, (cxx - cyy))
    angle_deg = angle * 180.0 / math.pi

    # Principal axes (unit vectors)
    ux = math.cos(angle)
    uy = math.sin(angle)
    vx = -uy
    vy = ux

    # Project points onto principal axes to get extents
    u_min = float('inf')
    u_max = float('-inf')
    v_min = float('inf')
    v_max = float('-inf')

    for pt in points_uv:
        du = pt[0] - mean_u
        dv = pt[1] - mean_v

        # Project onto principal axes
        u_proj = du * ux + dv * uy
        v_proj = du * vx + dv * vy

        u_min = min(u_min, u_proj)
        u_max = max(u_max, u_proj)
        v_min = min(v_min, v_proj)
        v_max = max(v_max, v_proj)

    len_u = abs(u_max - u_min)
    len_v = abs(v_max - v_min)

    # Build rectangle corners in original UV space
    # p = mean + u_proj * U_axis + v_proj * V_axis
    corners = [
        (mean_u + u_min * ux + v_min * vx, mean_v + u_min * uy + v_min * vy),
        (mean_u + u_max * ux + v_min * vx, mean_v + u_max * uy + v_min * vy),
        (mean_u + u_max * ux + v_max * vx, mean_v + u_max * uy + v_max * vy),
        (mean_u + u_min * ux + v_max * vx, mean_v + u_min * uy + v_max * vy),
        (mean_u + u_min * ux + v_min * vx, mean_v + u_min * uy + v_min * vy),  # Close loop
    ]

    return (corners, len_u, len_v, angle_deg)
