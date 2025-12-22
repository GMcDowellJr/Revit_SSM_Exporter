# -*- coding: utf-8 -*-
"""
Projection processing for SSM/VOP exporter.
Element projection to view XY plane with silhouette extraction.
"""

import math
from geometry.silhouette import SilhouetteExtractor


def _create_silhouette_extractor(view, grid_data, config, logger, adaptive_thresholds=None):
    """
    adaptive_thresholds: Pass in pre-computed thresholds (computed per-view)
    Extractor can be reused across views with same scale/grid,
    but each view gets its own adaptive thresholds
    """
    # Get crop box transform
    try:
        crop_box = view.CropBox
        if not crop_box:
            return None
        trf = crop_box.Transform
        inv_trf = trf.Inverse
    except Exception:
        return None
    
    # Create extractor (now accepts adaptive_thresholds)
    extractor = SilhouetteExtractor(view, grid_data, config, logger, adaptive_thresholds)
    # Wire the view-local XY transform onto the extractor
    def _to_local_xy(pt):
        if pt is None:
            return None
        try:
            lp = inv_trf.OfPoint(pt)
            return (float(lp.X), float(lp.Y))
        except Exception:
            try:
                return (float(pt.X), float(pt.Y))
            except Exception:
                return None

    extractor._transform_fn = _to_local_xy
    return extractor

    
def project_elements_to_view_xy(view, grid_data, clip_data, elems3d, elems2d, config, logger):
    """
    v47 projection, A2 flavor:

    - 3D: per-element *rectangular* silhouette from view BBox.
    - 2D whitelist:
        * Text
        * Dimensions
        * Tags (IndependentTag, RoomTag, and tag-like annotation cats)
        * FilledRegion
        * DetailComponents
        * DetailLines + Lines (drafting/drawing lines)
        * GenericAnnotation
    - FilledRegion → true loops from GetBoundaries().
    - Detail/drafting lines → 2-cell-thick oriented bands along the curve.
    - Everything else → BBox rectangles.
    """
    extractor = None
    
    view_id_val = getattr(getattr(view, "Id", None), "IntegerValue", "Unknown")
    logger.info("Projection: projecting elements to view XY for view Id={0}".format(view_id_val))

    proj_cfg = (config or {}).get("projection") or {}
    suppress_link_floor_3d = False  # deprecated (was debug-only)

    grid_xy_min = grid_data.get("grid_xy_min")
    grid_xy_max = grid_data.get("grid_xy_max")

    # Grid info for debug diagnostics (large 3D BBoxes)
    try:
        origin_xy = grid_data.get("origin_model_xy") or None
        if origin_xy is not None:
            origin_x, origin_y = origin_xy
        else:
            origin_x = origin_y = None
    except Exception:
        origin_x = origin_y = None

    try:
        n_i = int(grid_data.get("grid_n_i") or 0)
        n_j = int(grid_data.get("grid_n_j") or 0)
    except Exception:
        n_i = n_j = 0

    if not (isinstance(grid_xy_min, (list, tuple)) and isinstance(grid_xy_max, (list, tuple))):
        grid_xy_min = grid_data.get("crop_xy_min")
        grid_xy_max = grid_data.get("crop_xy_max")

    if not (isinstance(grid_xy_min, (list, tuple)) and isinstance(grid_xy_max, (list, tuple))):
        logger.warn(
            "Projection: view Id={0} has no usable grid XY; skipping projection".format(
                view_id_val
            )
        )
        return {
            "projected_3d": [],
            "projected_2d": [],
            "diagnostics": {
                "skipped_reason": "no_grid_xy",
                "num_3d_input": len(elems3d),
                "num_2d_input": len(elems2d),
            },
            "preview_2d_rects": [],
            "preview_3d_rects": [],
        }

    gmin_x, gmin_y = grid_xy_min
    gmax_x, gmax_y = grid_xy_max

    try:
        crop_box = getattr(view, "CropBox", None)
    except Exception:
        crop_box = None

    inv_trf = None
    crop_near_z = 0.0  # ADD THIS - default if no crop box
    
    if crop_box is not None:
        try:
            inv_trf = crop_box.Transform.Inverse
            
            # For plan views, use cut plane as W=0 reference (not CropBox.Min.Z)
            vtype = getattr(view, "ViewType", None)
            is_plan_like = False
            is_rcp = False
            if ViewType is not None:
                is_plan_like = vtype in (
                    ViewType.FloorPlan,
                    ViewType.CeilingPlan,
                    getattr(ViewType, "EngineeringPlan", None),
                    getattr(ViewType, "AreaPlan", None),
                )
                is_rcp = (vtype == ViewType.CeilingPlan)
            
            if is_plan_like:
                # Get cut plane elevation from ViewRange
                try:
                    vr = view.GetViewRange()
                    from Autodesk.Revit.DB import PlanViewPlane
                    cut_plane_id = vr.GetLevelId(PlanViewPlane.CutPlane)
                    cut_offset = vr.GetOffset(PlanViewPlane.CutPlane)
                    
                    if cut_plane_id is not None and DOC is not None:
                        cut_level = DOC.GetElement(cut_plane_id)
                        cut_elevation = getattr(cut_level, "Elevation", 0.0)
                        cut_plane_z = cut_elevation + cut_offset
                        
                        # Transform to crop-local coords
                        cut_pt_model = XYZ(0, 0, cut_plane_z)
                        cut_pt_local = inv_trf.OfPoint(cut_pt_model)
                        crop_near_z = float(cut_pt_local.Z)
                        
                        logger.info(
                            "Projection: {0} view W=0 at cut plane (model Z={1:.3f}, crop-local Z={2:.3f}){3}".format(
                                "RCP" if is_rcp else "Plan",
                                cut_plane_z, 
                                crop_near_z,
                                " [depth will be negated for occlusion]" if is_rcp else ""
                            )
                        )
                    else:
                        crop_near_z = float(crop_box.Min.Z)
                except Exception as ex:
                    logger.warn("Projection: Could not get cut plane, using CropBox.Min.Z: {0}".format(ex))
                    crop_near_z = float(crop_box.Min.Z)
            else:
                # For sections/elevations, use CropBox.Min.Z
                crop_near_z = float(crop_box.Min.Z)
                
        except Exception:
            inv_trf = None
            crop_near_z = 0.0
        
    def _to_local_xy(pt):
        if pt is None:
            return None
        if inv_trf is not None:
            try:
                lp = inv_trf.OfPoint(pt)
                return (lp.X, lp.Y)
            except Exception:
                pass
        try:
            return (pt.X, pt.Y)
        except Exception:
            return None

    def _to_local_xyz(pt):
        """Return view-aligned (u,v,depth) with W=0 at cut plane for plans, crop plane for sections.
        
        For RCPs (Reflected Ceiling Plans), depth is negated so that elements closer to the 
        view (lower in model Z) have lower depth values for proper front-to-back occlusion sorting.
        """
        if pt is None:
            return None
        if inv_trf is not None:
            try:
                lp = inv_trf.OfPoint(pt)
                w_normalized = lp.Z - crop_near_z
                
                # For RCP views, negate depth so closer elements (lower Z) have lower depth
                if is_rcp:
                    w_normalized = -w_normalized
                    
                return (lp.X, lp.Y, w_normalized)
            except Exception:
                pass
        try:
            # Fallback: use model Z directly (not ideal but better than crashing)
            # For RCP, negate to maintain proper ordering
            z_val = pt.Z
            if is_rcp:
                z_val = -z_val
            return (pt.X, pt.Y, z_val)
        except Exception:
            return None

    def _rect_intersects_grid(min_x, min_y, max_x, max_y):
        if min_x > max_x or min_y > max_y:
            return False
        if max_x < gmin_x or max_y < gmin_y:
            return False
        if min_x > gmax_x or min_y > gmax_y:
            return False
        return True

    def _make_rect_loop(min_x, min_y, max_x, max_y):
        pts = [
            (min_x, min_y),
            (max_x, min_y),
            (max_x, max_y),
            (min_x, max_y),
            (min_x, min_y),
        ]
        return {"points": pts, "is_hole": False}

    # cell size for bands
    try:
        cell_size = float(grid_data.get("cell_size_model") or 0.0)
    except Exception:
        cell_size = 0.0
    reg_cfg = config.get("regions", {})

    # --- scale/grid for extractor cache keys (must exist even when adaptive thresholds are off)
    try:
        scale = int(getattr(view, "Scale", 0) or 0)
    except Exception:
        scale = 0

    # grid_size == cell size in model units (feet)
    grid_size = cell_size

    if not hasattr(project_elements_to_view_xy, "_extractor_cache"):
        project_elements_to_view_xy._extractor_cache = {}

    band_cells = float(reg_cfg.get("linear_band_thickness_cells", 1.0))
    if band_cells < 0.0:
        band_cells = 0.0

    band_half_width = 0.5 * band_cells * cell_size if cell_size > 0 else 0.0

    projected_3d = []
    projected_2d = []

    diagnostics = {
        "num_3d_input": len(elems3d),
        "num_2d_input": len(elems2d),
        "num_3d_projected": 0,
        "num_2d_projected": 0,
        "num_3d_outside_grid": 0,
        "num_2d_outside_grid": 0,
        "num_2d_not_whitelisted": 0,
        "num_3d_loops": 0,
        "num_2d_loops": 0,
        "num_3d_skipped_link_floor": 0,
    }
    
    # Force strategies_used to exist
    diagnostics["strategies_used"] = {}

    # === COMPUTE ADAPTIVE THRESHOLDS (NEW!) ===
    adaptive_thresholds = None
    sil_cfg = config.get("projection", {}).get("silhouette", {})
    
    if sil_cfg.get("use_adaptive_thresholds", False):
        logger.info("Computing adaptive thresholds for view...")
        
        # Store cell size in config for adaptive computation
        sil_cfg["cell_size_for_adaptive"] = grid_data.get("cell_size_model", 1.0)
        
        # Compute adaptive thresholds PER VIEW (always fresh)
        adaptive_thresholds = _compute_adaptive_thresholds(
            elems3d if elems3d else [],
            view,
            sil_cfg,
            logger
        )

        # Get/create extractor (cached by scale/grid)
        cache_key = "{0}_{1:.3f}".format(scale, grid_size)

        if cache_key not in project_elements_to_view_xy._extractor_cache:
            extractor = _create_silhouette_extractor(view, grid_data, config, logger, None)
            project_elements_to_view_xy._extractor_cache[cache_key] = extractor

        extractor = project_elements_to_view_xy._extractor_cache.get(cache_key)

        # UPDATE extractor with this view's adaptive thresholds
        if extractor is not None:
            extractor.adaptive_thresholds = adaptive_thresholds  # Fresh per view!
            extractor.view = view  # Update view reference
        
        if adaptive_thresholds:
            diagnostics["adaptive_thresholds"] = adaptive_thresholds
    # === END ADAPTIVE THRESHOLDS ===
    
    # ---- 2D whitelist -----------------------------------------------------
    allowed_2d_cat_ids = set()
    IndependentTag_cls = None
    RoomTag_cls = None
    try:
        from Autodesk.Revit.DB import IndependentTag, RoomTag
        IndependentTag_cls = IndependentTag
        RoomTag_cls = RoomTag
    except Exception:
        IndependentTag_cls = None
        RoomTag_cls = None

    if BuiltInCategory is not None:
        # whitelist: only these annotation categories
        # contribute geometry to projection + occupancy.
        bic_names = [
            "OST_TextNotes",
            "OST_Dimensions",
            "OST_FilledRegion",
            "OST_DetailComponents",
            "OST_DetailLines",
            "OST_Lines",  # drafting/drawing lines
        ]

        for nm in bic_names:
            try:
                if hasattr(BuiltInCategory, nm):
                    bic = getattr(BuiltInCategory, nm)
                    allowed_2d_cat_ids.add(int(bic))
            except Exception:
                continue

    # ---- 3D projection: floors via geometry, others via BBox -------------

    floor_cat_ids = set()
    if BuiltInCategory is not None:
        try:
            floor_bics = [
                "OST_Floors",
                "OST_StructuralFoundation",
                "OST_Roofs",
            ]
            for nm in floor_bics:
                try:
                    bic = getattr(BuiltInCategory, nm, None)
                    if bic is not None:
                        floor_cat_ids.add(int(bic))
                except Exception:
                    continue
        except Exception:
            floor_cat_ids = set()
            

    def _extract_floor_loops_xy(elem, link_trf=None):
        """
        For "floor-like" 3D elements (Floors, Structural Foundations, Roofs),
        extract horizontal faces and project them into local XY (grid) coords.

        If link_trf is provided (linked element), points are first transformed
        link → host with link_trf, then host → grid with _to_local_xy().
        """
        loops = []
        # Lazy import so the module still loads even if geometry APIs differ
        try:
            from Autodesk.Revit.DB import Options, Solid, GeometryInstance, UV
        except Exception:
            return loops

        if not hasattr(elem, "get_Geometry"):
            return loops

        def _to_xy(pt):
            """Apply link transform (if any) then map to local XY."""
            if pt is None:
                return None
            try:
                if link_trf is not None:
                    pt = link_trf.OfPoint(pt)
            except Exception:
                # Fall back to untreated point
                pass
            return _to_local_xy(pt)

        try:
            opts = Options()
            opts.ComputeReferences = False
            opts.IncludeNonVisibleObjects = False
            try:
                opts.View = view
            except Exception:
                pass
            geom = elem.get_Geometry(opts)
        except Exception:
            return loops


        def _iter_solids(geom_elem):
            if geom_elem is None:
                return
            try:
                it = getattr(geom_elem, "__iter__", None)
                if it is None:
                    return
            except Exception:
                return
            for g in geom_elem:
                try:
                    if isinstance(g, Solid):
                        try:
                            vol = getattr(g, "Volume", 0.0)
                        except Exception:
                            vol = 0.0
                        if vol and vol > 1e-6:
                            yield g
                    elif isinstance(g, GeometryInstance):
                        try:
                            inst_geom = g.GetInstanceGeometry()
                        except Exception:
                            inst_geom = None
                        if inst_geom is not None:
                            for s in _iter_solids(inst_geom):
                                yield s
                except Exception:
                    continue

        def _is_horizontal_face(face):
            try:
                uv = UV(0.5, 0.5)
                n = face.ComputeNormal(uv)
            except Exception:
                return False
            try:
                # horizontal ≈ normal mostly along Z
                return abs(getattr(n, "Z", 0.0)) >= 0.9
            except Exception:
                return False

        def _points_equal(p1, p2, tol=1e-6):
            try:
                return (
                    abs(p1.X - p2.X) <= tol
                    and abs(p1.Y - p2.Y) <= tol
                    and abs(p1.Z - p2.Z) <= tol
                )
            except Exception:
                return False

        for solid in _iter_solids(geom):
            faces = getattr(solid, "Faces", None)
            if faces is None:
                continue
            for face in faces:
                if not _is_horizontal_face(face):
                    continue
                try:
                    edge_loops = face.EdgeLoops
                except Exception:
                    edge_loops = None
                if edge_loops is None:
                    continue

                for edge_loop in edge_loops:
                    pts_xy = []
                    first = True
                    prev_end = None
                    try:
                        for edge in edge_loop:
                            try:
                                curve = edge.AsCurve()
                                p0 = curve.GetEndPoint(0)
                                p1 = curve.GetEndPoint(1)
                            except Exception:
                                continue

                            if first:
                                pts_xy.append(_to_xy(p0))
                                pts_xy.append(_to_xy(p1))
                                prev_end = p1
                                first = False
                            else:
                                if _points_equal(prev_end, p0):
                                    pts_xy.append(_to_xy(p1))
                                    prev_end = p1
                                elif _points_equal(prev_end, p1):
                                    pts_xy.append(_to_xy(p0))
                                    prev_end = p0
                                else:
                                    # Fallback: append both; loop closure will clean up
                                    pts_xy.append(_to_xy(p0))
                                    pts_xy.append(_to_xy(p1))
                                    prev_end = p1


                    except Exception:
                        pts_xy = []

                    if not pts_xy:
                        continue

                    # Ensure closed
                    if pts_xy[0] != pts_xy[-1]:
                        pts_xy.append(pts_xy[0])

                    loops.append({"points": pts_xy, "is_hole": False})

        return loops


    def _extract_geom_hull_loop_xy(elem, link_trf=None):
        """
        Generic 3D fallback (non-BBox): extract a coarse 2D silhouette loop by
        projecting tessellated solid face vertices into local XY and taking the
        convex hull. Boundary-only downstream; no interior fill is introduced.

        Returns a single closed loop dict (or [] if geometry is unavailable).
        """
        loops = []
        try:
            from Autodesk.Revit.DB import Options, Solid, GeometryInstance
        except Exception:
            return loops

        if not hasattr(elem, "get_Geometry"):
            return loops

        try:
            opts = Options()
            opts.ComputeReferences = False
            opts.IncludeNonVisibleObjects = False
            try:
                opts.View = view
            except Exception:
                pass
            geom = elem.get_Geometry(opts)
        except Exception:
            geom = None

        if geom is None:
            return loops

        pts_xy = []

        def _iter_solids(g):
            if g is None:
                return
            try:
                it = getattr(g, "__iter__", None)
                if it is None:
                    return
            except Exception:
                return
            for obj in g:
                if obj is None:
                    continue
                try:
                    if isinstance(obj, GeometryInstance):
                        try:
                            inst = obj.GetInstanceGeometry()
                        except Exception:
                            inst = None
                        for s in _iter_solids(inst):
                            yield s
                        continue
                except Exception:
                    pass
                try:
                    if isinstance(obj, Solid) and getattr(obj, "Volume", 0.0) > 1e-9:
                        yield obj
                except Exception:
                    continue

        for solid in _iter_solids(geom):
            try:
                faces = getattr(solid, "Faces", None)
            except Exception:
                faces = None
            if faces is None:
                continue
            try:
                face_it = getattr(faces, "__iter__", None)
                if face_it is None:
                    continue
            except Exception:
                continue
            for face in faces:
                if face is None:
                    continue
                try:
                    mesh = face.Triangulate()
                except Exception:
                    mesh = None
                if mesh is None:
                    continue
                try:
                    vcount = int(getattr(mesh, "NumVertices", 0))
                except Exception:
                    vcount = 0
                for vi in range(vcount):
                    try:
                        p = mesh.get_Vertex(vi)
                    except Exception:
                        continue
                    try:
                        if link_trf is not None:
                            p = link_trf.OfPoint(p)
                    except Exception:
                        pass
                    xy = _to_local_xy(p)
                    if xy is None:
                        continue
                    pts_xy.append((float(xy[0]), float(xy[1])))

        if len(pts_xy) < 3:
            return loops

        # Convex hull in XY
        try:
            hull = []
            pts = sorted(set(pts_xy))
            if len(pts) < 3:
                return loops
            def _cross(o,a,b):
                return (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])
            lower = []
            for p in pts:
                while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
                    lower.pop()
                lower.append(p)
            upper = []
            for p in reversed(pts):
                while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
                    upper.pop()
                upper.append(p)
            hull = lower[:-1] + upper[:-1]
        except Exception:
            return loops

        if len(hull) < 3:
            return loops

        # Close
        if hull[0] != hull[-1]:
            hull.append(hull[0])

        loops.append({"points": hull, "is_hole": False})
        return loops

    def _extract_import_band_loops_xy(elem, link_trf=None):
        """
        For ImportInstance-based external content (DWG/IFC/SKP), extract
        curve-like geometry and build thin rectangular bands along each
        segment in local XY.

        This is intentionally LINEAR-only (no AREAL fill, no hatch inference).
        """
        loops = []
        # Lazy import so the module still loads even if geometry APIs differ
        try:
            from Autodesk.Revit.DB import Options, GeometryInstance, PolyLine
        except Exception:
            return loops

        if not hasattr(elem, "get_Geometry"):
            return loops

        def _to_xy(pt):
            if pt is None:
                return None
            try:
                if link_trf is not None:
                    pt = link_trf.OfPoint(pt)
            except Exception:
                # Fall back to untreated point
                pass
            return _to_local_xy(pt)

        try:
            opts = Options()
            opts.ComputeReferences = False
            opts.IncludeNonVisibleObjects = False
            try:
                opts.View = view
            except Exception:
                pass
            geom = elem.get_Geometry(opts)
        except Exception:
            geom = None

        if geom is None:
            return loops

        def _iter_curve_segments(geom_elem):
            if geom_elem is None:
                return
            try:
                it = getattr(geom_elem, "__iter__", None)
                if it is None:
                    return
            except Exception:
                return
            for g in geom_elem:
                try:
                    # Nested geometry instances
                    if isinstance(g, GeometryInstance):
                        try:
                            inst_geom = g.GetInstanceGeometry()
                        except Exception:
                            inst_geom = None
                        if inst_geom is not None:
                            for seg in _iter_curve_segments(inst_geom):
                                yield seg
                    # PolyLine → explicit point chain
                    elif isinstance(g, PolyLine):
                        try:
                            pts = list(g.GetCoordinates())
                        except Exception:
                            pts = None
                        if pts and len(pts) >= 2:
                            prev = pts[0]
                            for pt in pts[1:]:
                                yield (prev, pt)
                                prev = pt
                    # Generic Revit curve-like object with endpoints
                    elif hasattr(g, "GetEndPoint"):
                        yield g
                except Exception:
                    continue

        # BAND LOGIC: same band_half_width as 2D detail/drafting lines
        if band_half_width <= 0.0:
            return loops

        for seg in _iter_curve_segments(geom):
            try:
                # seg may be a Curve or a (p0, p1) tuple
                if isinstance(seg, tuple) and len(seg) == 2:
                    p0, p1 = seg
                else:
                    p0 = seg.GetEndPoint(0)
                    p1 = seg.GetEndPoint(1)
            except Exception:
                continue

            xy0 = _to_xy(p0)
            xy1 = _to_xy(p1)
            if xy0 is None or xy1 is None:
                continue

            x0, y0 = xy0
            x1, y1 = xy1
            dx = x1 - x0
            dy = y1 - y0
            length = math.sqrt(dx * dx + dy * dy)
            if length <= 1e-9:
                continue

            ux = dx / length
            uy = dy / length
            nx = -uy
            ny = ux
            offx = nx * band_half_width
            offy = ny * band_half_width

            p0a = (x0 + offx, y0 + offy)
            p0b = (x0 - offx, y0 - offy)
            p1a = (x1 + offx, y1 + offy)
            p1b = (x1 - offx, y1 - offy)

            loops.append(
                {
                    "points": [p0a, p1a, p1b, p0b, p0a],
                    "is_hole": False,
                    "is_linear_band": True,
                }
            )

        return loops

    for e in elems3d:
        try:
            eid = getattr(getattr(e, "Id", None), "IntegerValue", None)
        except Exception:
            eid = None

        cat = getattr(e, "Category", None)
        try:
            cat_name = getattr(cat, "Name", "") or "<No Category>"
        except Exception:
            cat_name = "<No Category>"

        cat_id_val = None
        if cat is not None:
            try:
                cat_id_val = getattr(getattr(cat, "Id", None), "IntegerValue", None)
            except Exception:
                cat_id_val = None
        # Floor-like elements: try full geometry projection first.
        # For linked proxies, we also pass the link → host transform.
        loops_xy = []
        if cat_id_val is not None and cat_id_val in floor_cat_ids:
            link_trf = getattr(e, "_link_trf", None)
            try:
                loops_xy = _extract_floor_loops_xy(e, link_trf)
                
                # === TRACK STRATEGY FOR FLOOR EXTRACTION ===
                if loops_xy:
                    # Floor extraction succeeded - track it!
                    diagnostics.setdefault("strategies_used", {})
                    
                    # Determine size based on element (approximate)
                    try:
                        bb = e.get_BoundingBox(view)
                        if bb and bb.Min and bb.Max:
                            width = abs(bb.Max.X - bb.Min.X)
                            height = abs(bb.Max.Y - bb.Min.Y)
                            max_dim_ft = max(width, height)
                            
                            # Simple size classification
                            if max_dim_ft < 3.0:
                                size_tier = "tiny_linear"
                            elif max_dim_ft < 20.0:
                                size_tier = "medium"
                            elif max_dim_ft < 100.0:
                                size_tier = "large"
                            else:
                                size_tier = "very_large"
                        else:
                            size_tier = "medium"
                    except Exception:
                        size_tier = "medium"
                    
                    composite_key = "{0}_floor_extraction".format(size_tier)
                    diagnostics["strategies_used"][composite_key] = \
                        diagnostics["strategies_used"].get(composite_key, 0) + 1
                    
                    # logger.info("DEBUG: Tracked {0}".format(composite_key))
                # === END TRACKING ===
                
            except Exception:
                loops_xy = []

        if loops_xy:
            # Rasterizable loops from geometry.
            # IMPORTANT: keep all usable loops together per element so that
            # parity has access to outer + inner loops at once.
            filtered_loops = []
            any_in_grid = False

            for loop in loops_xy:
                pts = loop.get("points") or []
                if len(pts) < 4:
                    continue

                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                min_x, max_x = min(xs), max(xs)
                min_y, max_y = min(ys), max(ys)

                if not _rect_intersects_grid(min_x, min_y, max_x, max_y):
                    # Loop is entirely outside the grid; skip it.
                    diagnostics["num_3d_outside_grid"] += 1
                    continue

                filtered_loops.append(loop)
                any_in_grid = True

            if not any_in_grid or not filtered_loops:
                # No loops from this element intersect the grid → ignore.
                continue

            # Classify source for ext-cells (host vs RVT link proxies)
            is_link_proxy = getattr(e, "LinkInstanceId", None) is not None
            source = "RVT_LINK" if is_link_proxy else "HOST"

            # Occlusion support: store view-aligned UV AABB + depth AABB for ordering
            uv_min_x = uv_min_y = uv_max_x = uv_max_y = None
            try:
                for _lp in filtered_loops:
                    _pts = _lp.get("points") or []
                    for (_x, _y) in _pts:
                        if uv_min_x is None:
                            uv_min_x = uv_max_x = float(_x)
                            uv_min_y = uv_max_y = float(_y)
                        else:
                            if _x < uv_min_x: uv_min_x = float(_x)
                            if _x > uv_max_x: uv_max_x = float(_x)
                            if _y < uv_min_y: uv_min_y = float(_y)
                            if _y > uv_max_y: uv_max_y = float(_y)
            except Exception:
                uv_min_x = uv_min_y = uv_max_x = uv_max_y = None

            depth_min = depth_max = None
            try:
                if bb is not None and bb.Min is not None and bb.Max is not None:

                    # Compute depth range from ALL 8 bounding-box corners (spec: corner transforms required).
                    # This avoids incorrect ordering/culling when the bbox is not view-aligned.
                    try:
                        x0, y0, z0 = bb.Min.X, bb.Min.Y, bb.Min.Z
                        x1, y1, z1 = bb.Max.X, bb.Max.Y, bb.Max.Z
                        _corners = [
                            XYZ(x0, y0, z0), XYZ(x0, y0, z1),
                            XYZ(x0, y1, z0), XYZ(x0, y1, z1),
                            XYZ(x1, y0, z0), XYZ(x1, y0, z1),
                            XYZ(x1, y1, z0), XYZ(x1, y1, z1),
                        ]
                        _ws = []
                        for _c in _corners:
                            _lp = _to_local_xyz(_c)
                            if _lp is not None:
                                _ws.append(float(_lp[2]))
                        if _ws:
                            depth_min = min(_ws)
                            depth_max = max(_ws)
                    except Exception:
                        pass

            except Exception:
                depth_min = depth_max = None
            try:
                if bb is not None and bb.Min is not None and bb.Max is not None:
                    pmin = _to_local_xyz(bb.Min)
                    pmax = _to_local_xyz(bb.Max)
                    if pmin is not None and pmax is not None:
                        z0 = float(pmin[2]); z1 = float(pmax[2])
                        depth_min = z0 if z0 <= z1 else z1
                        depth_max = z1 if z1 >= z0 else z0
            except Exception:
                depth_min = depth_max = None

            projected_3d.append(
                {
                    "elem_id": eid,
                    "category": cat_name,
                    "is_2d": False,
                    "loops": filtered_loops,
                    "uv_aabb": (uv_min_x, uv_min_y, uv_max_x, uv_max_y),
                    "depth_min": depth_min,
                    "depth_max": depth_max,
                    "source": source,
                }
            )
            
            diagnostics["num_3d_projected"] += 1
            diagnostics["num_3d_loops"] += len(filtered_loops)
            
            # === TRACK FLOOR EXTRACTION STRATEGY ===
            diagnostics.setdefault("strategies_used", {})
            diagnostics["strategies_used"]["floor_extraction"] = \
                diagnostics["strategies_used"].get("floor_extraction", 0) + 1
            # === END TRACKING ===
            
            continue  # done with this element

     
        # ImportInstance-based external content (DWG/IFC/SKP):
        # use banded curve geometry only; never fall back to a BBox.
        if ImportInstance is not None and isinstance(e, ImportInstance):
            link_trf = getattr(e, "_link_trf", None)
            try:
                loops_xy = _extract_import_band_loops_xy(e, link_trf)
            except Exception:
                loops_xy = []
            try:
                loops_xy = _extract_import_band_loops_xy(e, link_trf)
                
                # === TRACK STRATEGY FOR IMPORT EXTRACTION ===
                if loops_xy:
                    diagnostics.setdefault("strategies_used", {})
                    composite_key = "import_band_extraction"
                    diagnostics["strategies_used"][composite_key] = \
                        diagnostics["strategies_used"].get(composite_key, 0) + 1
                # === END TRACKING ===
                
            except Exception:
                loops_xy = []            
            
            if not loops_xy:
                # Prefer underfill over a huge import BBox dominating the grid.
                diagnostics.setdefault("num_3d_import_no_loops", 0)
                diagnostics["num_3d_import_no_loops"] += 1
                
                # === TRACK IMPORT EXTRACTION STRATEGY ===
                diagnostics.setdefault("strategies_used", {})
                diagnostics["strategies_used"]["import_extraction"] = \
                    diagnostics["strategies_used"].get("import_extraction", 0) + 1
                # === END TRACKING ===
                
                logger.info(
                    "Projection-debug: ImportInstance elem_id={0}, cat='{1}' "
                    "produced no usable band loops; skipping from 3D occupancy"
                    .format(eid, cat_name)
                )
                continue

            for loop in loops_xy:
                pts = loop.get("points") or []
                if len(pts) < 4:
                    continue

                # Quick grid-crop pre-check using loop bbox
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                min_x, max_x = min(xs), max(xs)
                min_y, max_y = min(ys), max(ys)

                if not _rect_intersects_grid(min_x, min_y, max_x, max_y):
                    diagnostics.setdefault("num_3d_import_outside_grid", 0)
                    diagnostics["num_3d_import_outside_grid"] += 1
                    continue

                # Occlusion support metadata (depth unknown for import band loops)
                uv_min_x = uv_min_y = uv_max_x = uv_max_y = None
                try:
                    _pts = loop.get("points") or []
                    for (_x, _y) in _pts:
                        if uv_min_x is None:
                            uv_min_x = uv_max_x = float(_x)
                            uv_min_y = uv_max_y = float(_y)
                        else:
                            if _x < uv_min_x: uv_min_x = float(_x)
                            if _x > uv_max_x: uv_max_x = float(_x)
                            if _y < uv_min_y: uv_min_y = float(_y)
                            if _y > uv_max_y: uv_max_y = float(_y)
                except Exception:
                    uv_min_x = uv_min_y = uv_max_x = uv_max_y = None

                projected_3d.append(
                    {
                        "elem_id": eid,
                        "category": cat_name,
                        "is_2d": False,
                        "loops": [loop],
                        "uv_aabb": (uv_min_x, uv_min_y, uv_max_x, uv_max_y),
                        "depth_min": None,
                        "depth_max": None,
                        "source": "DWG_IMPORT",
                    }
                )

                diagnostics["num_3d_projected"] += 1
                diagnostics.setdefault("num_3d_import_loops", 0)
                diagnostics["num_3d_import_loops"] += 1

            # Done with this ImportInstance – do NOT fall through to BBox.
            continue
     
        # Skip point clouds entirely
        if PointCloudInstance is not None and isinstance(e, PointCloudInstance):
            diagnostics.setdefault("num_3d_skipped_pointcloud", 0)
            diagnostics["num_3d_skipped_pointcloud"] += 1
            logger.info(
                "Projection-debug: skipping PointCloudInstance elem_id={0}, "
                "cat='{1}' from 3D occupancy".format(eid, cat_name)
            )
            continue

        # ============================================================
        # HYBRID SILHOUETTE EXTRACTION
        # Use intelligent strategies instead of always tessellating
        # ============================================================
        
        # Check if hybrid mode is enabled
        use_hybrid = config.get("projection", {}).get("silhouette", {}).get("enabled", False)
        # DEBUG
        logger.info("DEBUG: Hybrid mode enabled = {0}".format(use_hybrid))
        # make hybrid/strategies_used diagnosable in debug.json
        diagnostics["silhouette_hybrid_enabled"] = bool(use_hybrid)
        diagnostics.setdefault("silhouette_extractor_is_none", False)
        diagnostics.setdefault("silhouette_attempted", 0)
        diagnostics.setdefault("silhouette_succeeded", 0)
        diagnostics.setdefault("silhouette_failed", 0)
        hull_loops = []
        strategy_used = "unknown"
        
        if use_hybrid:
            # Create extractor once per view (cache it)
            if not hasattr(project_elements_to_view_xy, '_extractor_cache'):
                project_elements_to_view_xy._extractor_cache = {}
            
            # Smart cache key: (scale, grid_size) instead of view_id
            try:
                scale = int(getattr(view, "Scale", 96))
                grid_size = float(grid_data.get("cell_size_model", 1.0))
                cache_key = "{0}_{1:.3f}".format(scale, grid_size)
            except Exception:
                # Fallback to view_id if scale/grid unavailable
                view_id_str = str(getattr(getattr(view, "Id", None), "IntegerValue", "unknown"))
                cache_key = "view_{0}".format(view_id_str)

            if cache_key not in project_elements_to_view_xy._extractor_cache:
                extractor = _create_silhouette_extractor(
                    view, grid_data, config, logger, adaptive_thresholds
                )
                if extractor:
                    project_elements_to_view_xy._extractor_cache[cache_key] = extractor
                    logger.info("Created extractor for cache_key={0}".format(cache_key))

            extractor = project_elements_to_view_xy._extractor_cache.get(cache_key)
            
            if extractor:
                logger.info("Reusing extractor for cache_key={0}".format(cache_key))
                
                # Update with THIS view's adaptive thresholds
                if adaptive_thresholds:
                    extractor.adaptive_thresholds = adaptive_thresholds
                
                extractor.view = view
                
            if extractor:
                try:
                    _lt = getattr(e, "_link_trf", None)
                except Exception:
                    _lt = None
                
                try:
                    result = extractor.extract_silhouette(e, _lt)
                    hull_loops = result.get("loops", [])
                    strategy_used = result.get("strategy", "unknown")
                    size_tier = result.get("size_tier", "unknown")

                    diagnostics.setdefault("strategies_used", {})

                    # Build composite key: "tier_strategy"
                    if size_tier and size_tier != "unknown":
                        composite_key = "{0}_{1}".format(size_tier, strategy_used)
                    else:
                        composite_key = strategy_used

                    diagnostics["strategies_used"][composite_key] = \
                        diagnostics["strategies_used"].get(composite_key, 0) + 1
                    # DEBUG
                    logger.info("DEBUG: Tracked {0}".format(composite_key))   
                except Exception as ex:
                    logger.info(
                        "Silhouette: hybrid extraction failed for elem {0}: {1}".format(eid, ex)
                    )
                    hull_loops = []
        
        # Fallback: Original A19 full tessellation (if hybrid disabled or failed)
        if not hull_loops and not use_hybrid:
            try:
                _lt = getattr(e, "_link_trf", None)
            except Exception:
                _lt = None
            try:
                hull_loops = _extract_geom_hull_loop_xy(e, _lt)
                strategy_used = "full_tessellation_a19"
            except Exception:
                hull_loops = []

        if hull_loops:
            filtered_loops = []
            any_in_grid = False
            for loop in hull_loops:
                pts = loop.get("points") or []
                if not pts:
                    continue
                try:
                    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
                    min_x = min(xs); max_x = max(xs); min_y = min(ys); max_y = max(ys)
                except Exception:
                    continue
                if not _rect_intersects_grid(min_x, min_y, max_x, max_y):
                    continue
                filtered_loops.append(loop)
                any_in_grid = True

            if any_in_grid and filtered_loops:
                # uv_aabb is just the loop AABB in local XY
                try:
                    uv_min_x = min(p[0] for lp in filtered_loops for p in (lp.get("points") or []))
                    uv_max_x = max(p[0] for lp in filtered_loops for p in (lp.get("points") or []))
                    uv_min_y = min(p[1] for lp in filtered_loops for p in (lp.get("points") or []))
                    uv_max_y = max(p[1] for lp in filtered_loops for p in (lp.get("points") or []))
                except Exception:
                    uv_min_x = uv_min_y = uv_max_x = uv_max_y = None

                # depth from view BBox only (ordering hint); geometry loop does not depend on BBox
                depth_min = depth_max = None
                try:
                    bb = e.get_BoundingBox(view)
                except Exception:
                    bb = None
                try:
                    if bb is not None and bb.Min is not None and bb.Max is not None:
                        pmin = _to_local_xyz(bb.Min)
                        pmax = _to_local_xyz(bb.Max)
                        if pmin is not None and pmax is not None:
                            z0 = float(pmin[2]); z1 = float(pmax[2])
                            depth_min = z0 if z0 <= z1 else z1
                            depth_max = z1 if z1 >= z0 else z0
                except Exception:
                    depth_min = depth_max = None

                is_link_proxy = getattr(e, "LinkInstanceId", None) is not None
                source = "RVT_LINK" if is_link_proxy else "HOST"

                projected_3d.append(
                    {
                        "elem_id": eid,
                        "category": cat_name,
                        "is_2d": False,
                        "loops": filtered_loops,
                        "uv_aabb": (uv_min_x, uv_min_y, uv_max_x, uv_max_y),
                        "depth_min": depth_min,
                        "depth_max": depth_max,
                        "source": source,
                    }
                )
                diagnostics["num_3d_projected"] += 1
                diagnostics.setdefault("num_3d_geom_hull_loops", 0)
                diagnostics["num_3d_geom_hull_loops"] += len(filtered_loops)
                continue

        # Fallback: rectangular silhouette from view BBox (existing behavior)
        try:
            bb = e.get_BoundingBox(view)
        except Exception:
            bb = None

        if bb is None or bb.Min is None or bb.Max is None:
            continue

        p0 = _to_local_xy(bb.Min)
        p1 = _to_local_xy(bb.Max)
        if p0 is None or p1 is None:
            continue

        x0, y0 = p0
        x1, y1 = p1

        min_x = x0 if x0 <= x1 else x1
        max_x = x1 if x1 >= x0 else x0
        min_y = y0 if y0 <= y1 else y1
        max_y = y1 if y1 >= y0 else y0

        if min_x >= max_x or min_y >= max_y:
            continue

        if not _rect_intersects_grid(min_x, min_y, max_x, max_y):
            diagnostics["num_3d_outside_grid"] += 1
            continue

        # --- DEBUG: log huge 3D BBoxes that span most of the grid -----------
        try:
            # Only run if we have enough grid info
            if (
                origin_x is not None
                and origin_y is not None
                and cell_size > 0.0
                and n_i > 0
                and n_j > 0
            ):
                # convert rect extents to grid indices
                i_min = int(math.floor((min_x - origin_x) / cell_size))
                i_max = int(math.ceil((max_x - origin_x) / cell_size)) - 1
                j_min = int(math.floor((min_y - origin_y) / cell_size))
                j_max = int(math.ceil((max_y - origin_y) / cell_size)) - 1

                # Spec compliance: BBox projection is only allowed for TINY/LINEAR cases.
                try:
                    w_cells = int(i_max - i_min + 1)
                    h_cells = int(j_max - j_min + 1)
                    is_tiny = (w_cells <= 2 and h_cells <= 2)
                    is_linear = (not is_tiny) and (min(w_cells, h_cells) <= 2)
                    if not (is_tiny or is_linear):
                        diagnostics.setdefault("num_3d_bbox_suppressed_areal", 0)
                        diagnostics["num_3d_bbox_suppressed_areal"] += 1
                        logger.info(
                            "Projection-debug: suppressing AREAL BBox fallback for elem_id={0}, cat='{1}' (w={2}, h={3})".format(
                                eid, cat_name, w_cells, h_cells
                            )
                        )
                        continue
                except Exception:
                    # If we can't classify, fall through (fail-open) to existing BBox behavior.
                    pass


                # clamp to grid
                i_min = max(0, min(i_min, n_i - 1))
                i_max = max(0, min(i_max, n_i - 1))
                j_min = max(0, min(j_min, n_j - 1))
                j_max = max(0, min(j_max, n_j - 1))

                w = i_max - i_min + 1
                h = j_max - j_min + 1

                # threshold: spans at least 80% of grid in X or Y
                if (w >= 0.8 * n_i) or (h >= 0.8 * n_j):
                    logger.info(
                        "Projection-debug: large 3D BBox elem_id={0}, cat='{1}', "
                        "w={2}, h={3}, grid={4}x{5}".format(
                            eid, cat_name, w, h, n_i, n_j
                        )
                    )
        except Exception:
            pass
        # --------------------------------------------------------------------


        loop = _make_rect_loop(min_x, min_y, max_x, max_y)

        # Classify source for ext-cells (host vs RVT link proxies)
        is_link_proxy = getattr(e, "LinkInstanceId", None) is not None
        source = "RVT_LINK" if is_link_proxy else "HOST"

        # Occlusion support: UV AABB from rect loop and depth from view-aligned bbox
        uv_min_x = uv_min_y = uv_max_x = uv_max_y = None
        try:
            _pts = loop.get("points") or []
            for (_x, _y) in _pts:
                if uv_min_x is None:
                    uv_min_x = uv_max_x = float(_x)
                    uv_min_y = uv_max_y = float(_y)
                else:
                    if _x < uv_min_x: uv_min_x = float(_x)
                    if _x > uv_max_x: uv_max_x = float(_x)
                    if _y < uv_min_y: uv_min_y = float(_y)
                    if _y > uv_max_y: uv_max_y = float(_y)
        except Exception:
            uv_min_x = uv_min_y = uv_max_x = uv_max_y = None

        depth_min = depth_max = None
        try:
            if bb is not None and bb.Min is not None and bb.Max is not None:
                pmin = _to_local_xyz(bb.Min)
                pmax = _to_local_xyz(bb.Max)
                if pmin is not None and pmax is not None:
                    z0 = float(pmin[2]); z1 = float(pmax[2])
                    depth_min = z0 if z0 <= z1 else z1
                    depth_max = z1 if z1 >= z0 else z0
        except Exception:
            depth_min = depth_max = None

        projected_3d.append(
            {
                "elem_id": eid,
                "category": cat_name,
                "is_2d": False,
                "loops": [loop],
                "uv_aabb": (uv_min_x, uv_min_y, uv_max_x, uv_max_y),
                "depth_min": depth_min,
                "depth_max": depth_max,
                "source": source,
            }
        )

       # preview_3d_rects.append((min_x, min_y, max_x, max_y))
        diagnostics["num_3d_projected"] += 1
        diagnostics["num_3d_loops"] += 1


    # ---- 2D projection ----------------------------------------------------
    FilledRegion_cls = None
    CategoryType_cls = CategoryType
    try:
        from Autodesk.Revit.DB import FilledRegion
        FilledRegion_cls = FilledRegion
    except Exception:
        FilledRegion_cls = None

    for e in elems2d:
        cat = getattr(e, "Category", None)
        cat_id_val = None
        if cat is not None:
            try:
                cat_id_val = getattr(getattr(cat, "Id", None), "IntegerValue", None)
            except Exception:
                cat_id_val = None

        is_taglike = False
        if IndependentTag_cls is not None and isinstance(e, IndependentTag_cls):
            is_taglike = True
        elif RoomTag_cls is not None and isinstance(e, RoomTag_cls):
            is_taglike = True
        elif CategoryType_cls is not None and cat is not None:
            try:
                if cat.CategoryType == CategoryType_cls.Annotation and "tag" in (cat.Name or "").lower():
                    is_taglike = True
            except Exception:
                pass

        is_allowed = False
        if allowed_2d_cat_ids:
            if cat_id_val is not None and cat_id_val in allowed_2d_cat_ids:
                is_allowed = True
        if is_taglike:
            is_allowed = True

        if not is_allowed:
            diagnostics["num_2d_not_whitelisted"] += 1
            continue

        try:
            eid = getattr(getattr(e, "Id", None), "IntegerValue", None)
        except Exception:
            eid = None

        try:
            cat_name = getattr(cat, "Name", "") or "<No Category>"
        except Exception:
            cat_name = "<No Category>"

        is_filled_region = False
        try:
            if FilledRegion_cls is not None and isinstance(e, FilledRegion_cls):
                is_filled_region = True
        except Exception:
            is_filled_region = False

        # detail/drafting lines identified by category *name*, not numeric IDb
        cat = e.Category
        cat_name = cat.Name if cat else ""
        is_detail_line = (cat_name in ("Lines", "Detail Lines"))

        loops_xy = []

        # Filled regions: true boundary loops
        if is_filled_region:
            try:
                bnds = e.GetBoundaries()
            except Exception:
                bnds = None

            if bnds:
                for loop_curves in bnds:
                    pts = []
                    first_xy = None
                    for crv in loop_curves:
                        try:
                            p0 = crv.GetEndPoint(0)
                            p1 = crv.GetEndPoint(1)
                        except Exception:
                            continue

                        xy0 = _to_local_xy(p0)
                        xy1 = _to_local_xy(p1)
                        if xy0 is None or xy1 is None:
                            continue

                        if not pts:
                            pts.append(xy0)
                            first_xy = xy0
                        pts.append(xy1)

                    if len(pts) >= 3:
                        if first_xy is not None and pts[-1] != first_xy:
                            pts.append(first_xy)
                        loops_xy.append({"points": pts, "is_hole": False})

        # Detail / drafting lines: 2-cell-thick oriented bands
        elif is_detail_line and band_half_width > 0.0:
            curve = None
            try:
                loc = getattr(e, "Location", None)
                curve = getattr(loc, "Curve", None)
            except Exception:
                curve = None

            if curve is not None:
                try:
                    p0 = curve.GetEndPoint(0)
                    p1 = curve.GetEndPoint(1)
                    xy0 = _to_local_xy(p0)
                    xy1 = _to_local_xy(p1)
                except Exception:
                    xy0 = xy1 = None

                if xy0 is not None and xy1 is not None:
                    x0, y0 = xy0
                    x1, y1 = xy1
                    dx = x1 - x0
                    dy = y1 - y0
                    length = math.sqrt(dx * dx + dy * dy)
                    if length > 1e-9:
                        # unit tangent + normal
                        ux = dx / length
                        uy = dy / length
                        nx = -uy
                        ny = ux
                        offx = nx * band_half_width
                        offy = ny * band_half_width

                        # four corners of the band
                        p0a = (x0 + offx, y0 + offy)
                        p0b = (x0 - offx, y0 - offy)
                        p1a = (x1 + offx, y1 + offy)
                        p1b = (x1 - offx, y1 - offy)

                        xs = [p0a[0], p0b[0], p1a[0], p1b[0]]
                        ys = [p0a[1], p0b[1], p1a[1], p1b[1]]
                        min_x = min(xs)
                        max_x = max(xs)
                        min_y = min(ys)
                        max_y = max(ys)

                        # Diagnostic only: track bands whose bbox is fully outside grid
                        if not _rect_intersects_grid(min_x, min_y, max_x, max_y):
                            diagnostics["num_2d_outside_grid"] += 1

                        # Always create the band loop; cells outside the grid
                        # will be culled later by the valid_cells mask.
                        band_loop = {
                            "points": [p0a, p1a, p1b, p0b, p0a],
                            "is_hole": False,
                            "is_linear_band": True,
                        }
                        loops_xy.append(band_loop)


        # Other 2D: BBox rectangles
        if not loops_xy and not is_filled_region:
            try:
                bb = e.get_BoundingBox(view)
            except Exception:
                bb = None

            if bb is None or bb.Min is None or bb.Max is None:
                continue

            pmin = _to_local_xy(bb.Min)
            pmax = _to_local_xy(bb.Max)
            if pmin is None or pmax is None:
                continue

            min_x = min(pmin[0], pmax[0])
            min_y = min(pmin[1], pmax[1])
            max_x = max(pmin[0], pmax[0])
            max_y = max(pmin[1], pmax[1])

            if not _rect_intersects_grid(min_x, min_y, max_x, max_y):
                diagnostics["num_2d_outside_grid"] += 1
                continue

            loops_xy.append(_make_rect_loop(min_x, min_y, max_x, max_y))

        kept_loops = []
        for lp in loops_xy:
            pts = lp.get("points") or []
            if len(pts) < 2:
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            min_x = min(xs); max_x = max(xs)
            min_y = min(ys); max_y = max(ys)
            if not _rect_intersects_grid(min_x, min_y, max_x, max_y):
                continue
            kept_loops.append(lp)

        if kept_loops:
            projected_2d.append(
                {
                    "elem_id": eid,
                    "category": cat_name,
                    "is_2d": True,
                    "is_filled_region": bool(is_filled_region),
                    "loops": kept_loops,
                }
            )
            diagnostics["num_2d_projected"] += 1

    diagnostics["num_3d_loops"] = sum(len(ep.get("loops") or []) for ep in projected_3d)
    diagnostics["num_2d_loops"] = sum(len(ep.get("loops") or []) for ep in projected_2d)

    debug_cfg = CONFIG.get("debug", {})
    enable_preview_polys = bool(debug_cfg.get("enable_preview_polys", False))
    preview_2d_rects = []
    preview_3d_rects = []

    if enable_preview_polys and crop_box is not None:
        max_preview_2d = debug_cfg.get("max_preview_projected_2d", 32)
        try:
            max_preview_2d = int(max_preview_2d)
        except Exception:
            max_preview_2d = 32

        count2d = 0
        for ep in projected_2d:
            loops = ep.get("loops") or []
            for loop in loops:
                pts = loop.get("points") or []
                if len(pts) < 2:
                    continue
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                min_x = min(xs)
                max_x = max(xs)
                min_y = min(ys)
                max_y = max(ys)
                pc = grid._make_rect_polycurve(view, crop_box, min_x, min_y, max_x, max_y)
                if pc is not None:
                    preview_2d_rects.append(pc)
                    count2d += 1
                    if count2d >= max_preview_2d:
                        break
            if count2d >= max_preview_2d:
                break

        max_preview_3d = debug_cfg.get("max_preview_projected_3d", 64)
        try:
            max_preview_3d = int(max_preview_3d)
        except Exception:
            max_preview_3d = 64

        count3d = 0
        for ep in projected_3d:
            loops = ep.get("loops") or []
            for loop in loops:
                pts = loop.get("points") or []
                if len(pts) < 2:
                    continue
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                min_x = min(xs)
                max_x = max(xs)
                min_y = min(ys)
                max_y = max(ys)
                pc3 = grid._make_rect_polycurve(view, crop_box, min_x, min_y, max_x, max_y)
                if pc3 is not None:
                    preview_3d_rects.append(pc3)
                    count3d += 1
                    if count3d >= max_preview_3d:
                        break
            if count3d >= max_preview_3d:
                break
                
    logger.info(
        "Projection-debug: view Id={0} 2D whitelist stats -> "
        "input={1}, projected={2}, not_whitelisted={3}, outside_grid={4}".format(
            view_id_val,
            diagnostics.get("num_2d_input", 0),
            diagnostics.get("num_2d_projected", 0),
            diagnostics.get("num_2d_not_whitelisted", 0),
            diagnostics.get("num_2d_outside_grid", 0),
        )
    )

    logger.info(
        "Projection: view Id={0} -> projected_3d={1} elem(s), projected_2d={2} elem(s)".format(
            view_id_val,
            diagnostics["num_3d_projected"],
            diagnostics["num_2d_projected"],
        )
    )

    # DEBUG
    logger.info("DEBUG: strategies_used = {0}".format(diagnostics.get("strategies_used", "NOT PRESENT")))
    

    return {
        "projected_3d": projected_3d,
        "projected_2d": projected_2d,
        "diagnostics": diagnostics,
        "preview_2d_rects": preview_2d_rects,
        "preview_3d_rects": preview_3d_rects,
    }

# ------------------------------------------------------------
# REGIONS
# ------------------------------------------------------------

