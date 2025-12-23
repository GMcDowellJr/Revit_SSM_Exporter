# -*- coding: utf-8 -*-
"""
Silhouette extraction for SSM/VOP exporter.
Hybrid silhouette extraction with pluggable strategies.
"""

import math

# ------------------------------------------------------------
# Revit API context (set by main file)
# ------------------------------------------------------------

View = None
ViewType = None
XYZ = None


def set_revit_context(view_cls, view_type_cls, xyz_cls):
    """Set the Revit API context for this module."""
    global View, ViewType, XYZ

    View = view_cls
    ViewType = view_type_cls
    XYZ = xyz_cls


class SilhouetteExtractor(object):
    """
    Hybrid silhouette extraction with pluggable strategies.
    Inline version for Dynamo (no external imports needed).
    """
    def __init__(self, view, grid_data, config, logger, adaptive_thresholds=None):
        self.view = view
        self.grid_data = grid_data
        self.config = config or {}
        self.logger = logger
        
        # Extract silhouette config
        proj_cfg = self.config.get("projection", {})
        self.sil_cfg = proj_cfg.get("silhouette", {})
        
        # Grid cell size for tier determination
        try:
            self.cell_size = float(grid_data.get("cell_size_model", 1.0))
        except Exception:
            self.cell_size = 1.0
        
        # === ADAPTIVE THRESHOLDS ===
        self.adaptive_thresholds = adaptive_thresholds  # Store adaptive thresholds
    
        # Strategy usage tracking
        self.strategy_stats = {}
        
        # View direction for silhouette edge detection
        try:
            self.view_direction = view.ViewDirection
        except Exception:
            self.view_direction = None
        
        # Transform function (set externally)
        self._transform_fn = None
    
    def extract_silhouette(self, elem, link_trf=None):
        """Main entry point: select and apply best strategy for element."""
        if not self.sil_cfg.get("enabled", True):
            return {"loops": self._bbox_fallback(elem), "strategy": "bbox_disabled"}
        
        # Get element metadata
        cat_name = self._get_category_name(elem)
        elem_id = self._get_elem_id(elem)
        
        # Estimate element size in cells
        size_tier = self._determine_size_tier(elem)
        
        # Select strategy list based on size tier
        strategies = self.sil_cfg.get("tier_{0}".format(size_tier), ["bbox"])
        
        # Category-first override
        if self.sil_cfg.get("category_first", True) and cat_name:
            if self._is_simple_category(cat_name):
                if "category_api_shortcuts" not in strategies:
                    strategies = ["category_api_shortcuts"] + list(strategies)
        
        # Try strategies in order
        for strategy_name in strategies:
            if not self._is_strategy_enabled(strategy_name):
                continue
            
            try:
                loops = self._apply_strategy(strategy_name, elem, link_trf, cat_name)
                if loops:
                    self._track_strategy_usage(strategy_name, size_tier)
                    return {
                        "loops": loops,
                        "strategy": strategy_name,
                        "size_tier": size_tier,
                        "elem_id": elem_id,
                        "category": cat_name
                    }
            except Exception as ex:
                self.logger.info(
                    "Silhouette: strategy '{0}' failed for elem {1}: {2}".format(
                        strategy_name, elem_id, ex
                    )
                )
                continue
        
        # Ultimate fallback: BBox
        loops = self._bbox_fallback(elem)
        self._track_strategy_usage("bbox_fallback", size_tier)
        return {
            "loops": loops,
            "strategy": "bbox_fallback",
            "size_tier": size_tier,
            "elem_id": elem_id,
            "category": cat_name
        }
    
    def _determine_size_tier(self, elem):
        """
        Classify element by size: tiny_linear, medium, large, very_large
        
        Supports three modes:
        1. Absolute thresholds (feet) - scale-independent
        2. Scale-adjusted thresholds - auto-adjusts cell thresholds by scale
        3. Cell-based thresholds (original) - varies by scale
        """
        try:
            bb = elem.get_BoundingBox(self.view)
            if not bb or not bb.Min or not bb.Max:
                return "medium"
            
            p0 = self._to_local_xy(bb.Min)
            p1 = self._to_local_xy(bb.Max)
            
            if not p0 or not p1:
                return "medium"
            
            width_ft = abs(p1[0] - p0[0])
            height_ft = abs(p1[1] - p0[1])
            max_dimension_ft = max(width_ft, height_ft)
            
            # ============================================================
            # MODE 1: ABSOLUTE THRESHOLDS (Scale-Independent)
            # ============================================================
            if self.sil_cfg.get("use_absolute_thresholds", False):
                tiny_thresh_ft = self.sil_cfg.get("tiny_linear_threshold_ft", 3.0)
                medium_thresh_ft = self.sil_cfg.get("medium_threshold_ft", 20.0)
                large_thresh_ft = self.sil_cfg.get("large_threshold_ft", 100.0)
                
                if max_dimension_ft <= tiny_thresh_ft:
                    return "tiny_linear"
                elif max_dimension_ft <= medium_thresh_ft:
                    return "medium"
                elif max_dimension_ft <= large_thresh_ft:
                    return "large"
                else:
                    return "very_large"
            
            # ============================================================
            # MODE 2: SCALE-ADJUSTED THRESHOLDS (Auto-Detect)
            # ============================================================
            if self.sil_cfg.get("auto_adjust_for_scale", False):
                # Get view scale
                try:
                    view_scale = int(getattr(self.view, "Scale", 96))
                except Exception:
                    view_scale = 96  # Default to 1/8" = 1'-0"
                
                # Auto-detect scale category and adjust thresholds
                tiny_thresh, medium_thresh, large_thresh = self._get_scale_adjusted_thresholds(view_scale)
                
            else:
                # ============================================================
                # MODE 3: CELL-BASED THRESHOLDS (Original Behavior)
                # ============================================================
                # Use adaptive thresholds if available, otherwise use fixed
                if self.adaptive_thresholds:
                    tiny_thresh = self.adaptive_thresholds.get("tiny", 2)
                    medium_thresh = self.adaptive_thresholds.get("medium", 10)
                    large_thresh = self.adaptive_thresholds.get("large", 50)
                else:
                    tiny_thresh = self.sil_cfg.get("tiny_linear_threshold_cells", 2)
                    medium_thresh = self.sil_cfg.get("medium_threshold_cells", 10)
                    large_thresh = self.sil_cfg.get("large_threshold_cells", 50)
            
            # Apply thresholds (common logic for modes 2 and 3)
            width_cells = int(math.ceil(width_ft / self.cell_size))
            height_cells = int(math.ceil(height_ft / self.cell_size))
            max_cells = max(width_cells, height_cells)
            
            if max_cells <= tiny_thresh:
                return "tiny_linear"
            elif max_cells <= medium_thresh:
                return "medium"
            elif max_cells <= large_thresh:
                return "large"
            else:
                return "very_large"
                
        except Exception:
            return "medium"

    def _get_scale_adjusted_thresholds(self, view_scale):
        """
        Auto-detect appropriate thresholds based on view scale.
        Handles any scale value with intelligent interpolation.
        
        Returns: (tiny_threshold, medium_threshold, large_threshold) in cells
        """
        # Define scale breakpoints and corresponding thresholds
        # Format: (max_scale, tiny, medium, large)
        scale_tiers = [
            # Large scales (more detail)
            (48,   1,   5,  25),   # 1/4" = 1'-0" or larger (detail views)
            (96,   2,  10,  50),   # 1/8" = 1'-0" (typical floor plans)
            (192,  4,  20, 100),   # 1/16" = 1'-0" (smaller scale plans)
            (384,  8,  40, 200),   # 1/32" = 1'-0" (site plans)
            (768, 16,  80, 400),   # 1/64" = 1'-0" (large site plans)
        ]
        
        # Find appropriate tier
        for max_scale, tiny, medium, large in scale_tiers:
            if view_scale <= max_scale:
                return (tiny, medium, large)
        
        # For scales beyond our largest tier (very small scale drawings)
        # Extrapolate linearly from largest tier
        largest_scale, largest_tiny, largest_medium, largest_large = scale_tiers[-1]
        
        if view_scale > largest_scale:
            # Scale factor for extrapolation
            scale_factor = float(view_scale) / largest_scale
            
            # Apply factor with ceiling to ensure we get integers
            extrapolated_tiny = int(math.ceil(largest_tiny * scale_factor))
            extrapolated_medium = int(math.ceil(largest_medium * scale_factor))
            extrapolated_large = int(math.ceil(largest_large * scale_factor))
            
            return (extrapolated_tiny, extrapolated_medium, extrapolated_large)
        
        # Fallback (should never reach here, but just in case)
        return (2, 10, 50)    
    
    def _is_simple_category(self, cat_name):
        simple_cats = self.sil_cfg.get("simple_categories", [])
        return cat_name in simple_cats
    
    def _is_strategy_enabled(self, strategy_name):
        strategy_flags = {
            "bbox": True,
            "bbox_fallback": True,
            "obb": self.sil_cfg.get("enable_obb", True),
            # "silhouette_edges": self.sil_cfg.get("enable_silhouette_edges", True),
            "coarse_tessellation": self.sil_cfg.get("enable_coarse_tessellation", True),
            "category_api_shortcuts": self.sil_cfg.get("enable_category_api_shortcuts", True),
        }
        return strategy_flags.get(strategy_name, False)
    
    def _apply_strategy(self, strategy_name, elem, link_trf, cat_name):
        if strategy_name == "bbox" or strategy_name == "bbox_fallback":
            return self._bbox_fallback(elem)
        elif strategy_name == "obb":
            return self._oriented_bbox(elem, link_trf)
        elif strategy_name == "coarse_tessellation":
            return self._coarse_tessellation(elem, link_trf)
        elif strategy_name == "category_api_shortcuts":
            return self._category_api_shortcuts(elem, link_trf)
        else:
            return None
    
    def _track_strategy_usage(self, strategy_name, size_tier):
        if not self.sil_cfg.get("track_strategy_usage", True):
            return
        key = "{0}_{1}".format(size_tier, strategy_name)
        self.strategy_stats[key] = self.strategy_stats.get(key, 0) + 1
    
    # ============================================================
    # STRATEGY: BBox (baseline)
    # ============================================================
    
    def _bbox_fallback(self, elem):
        """Axis-aligned bounding box rectangle"""
        try:
            bb = elem.get_BoundingBox(self.view)
            if not bb or not bb.Min or not bb.Max:
                return []
            
            p0 = self._to_local_xy(bb.Min)
            p1 = self._to_local_xy(bb.Max)
            
            if not p0 or not p1:
                return []
            
            x0, y0 = p0
            x1, y1 = p1
            
            min_x = min(x0, x1)
            max_x = max(x0, x1)
            min_y = min(y0, y1)
            max_y = max(y0, y1)
            
            if min_x >= max_x or min_y >= max_y:
                return []
            
            points = [
                (min_x, min_y),
                (max_x, min_y),
                (max_x, max_y),
                (min_x, max_y),
                (min_x, min_y)
            ]
            
            return [{"points": points, "is_hole": False}]
            
        except Exception:
            return []
    
    # ============================================================
    # STRATEGY: Oriented Bounding Box (OBB)
    # ============================================================
    
    def _oriented_bbox(self, elem, link_trf):
        """Compute oriented bounding rectangle from projected BBox corners"""
        try:
            bb = elem.get_BoundingBox(self.view)
            if not bb or not bb.Min or not bb.Max:
                return []
            
            # Get all 8 corners of 3D bbox
            corners_3d = self._get_bbox_corners(bb)
            
            # Project to 2D
            corners_2d = []
            for corner in corners_3d:
                xy = self._to_local_xy(corner)
                if xy:
                    corners_2d.append(xy)
            
            if len(corners_2d) < 3:
                return []
            
            # Compute 2D convex hull
            hull = self._convex_hull_2d(corners_2d)
            
            if len(hull) < 3:
                return []
            
            return [{"points": hull, "is_hole": False}]
                
        except Exception:
            return []
    
    # ============================================================
    # STRATEGY: Coarse Tessellation
    # ============================================================
    
    def _coarse_tessellation(self, elem, link_trf):
        """Tessellate with coarse settings - faster than full detail"""
        try:
            from Autodesk.Revit.DB import Options, Solid, GeometryInstance, ViewDetailLevel
        except Exception:
            return []
        
        if not hasattr(elem, "get_Geometry"):
            return []
        
        try:
            opts = Options()
            opts.ComputeReferences = False
            opts.IncludeNonVisibleObjects = False
            opts.DetailLevel = ViewDetailLevel.Coarse
            
            try:
                opts.View = self.view
            except Exception:
                pass
            
            geom = elem.get_Geometry(opts)
        except Exception:
            return []
        
        if geom is None:
            return []
        
        pts_xy = []
        max_verts = self.sil_cfg.get("coarse_tess_max_verts_per_face", 20)
        tess_param = self.sil_cfg.get("coarse_tess_triangulate_param", 0.5)
        
        for solid in self._iter_solids(geom):
            try:
                faces = solid.Faces
            except Exception:
                continue
            
            for face in faces:
                try:
                    mesh = face.Triangulate(tess_param)
                except Exception:
                    continue
                
                if mesh is None:
                    continue
                
                try:
                    vcount = int(mesh.NumVertices)
                except Exception:
                    continue
                
                step = max(1, vcount // max_verts)
                
                for i in range(0, vcount, step):
                    try:
                        p = mesh.get_Vertex(i)
                        if link_trf:
                            p = link_trf.OfPoint(p)
                        xy = self._to_local_xy(p)
                        if xy:
                            pts_xy.append(xy)
                    except Exception:
                        continue
        
        if len(pts_xy) < 3:
            return []
        
        hull = self._convex_hull_2d(pts_xy)
        
        if len(hull) < 3:
            return []
        
        return [{"points": hull, "is_hole": False}]
        
    # ============================================================
    # STRATEGY: Category API Shortcuts
    # ============================================================
    
    def _category_api_shortcuts(self, elem, link_trf):
        """
        Use category-specific Revit API methods for faster extraction.
        
        For Walls, Floors, Roofs, Ceilings:
        - Extract location curve or boundary loops directly from API
        - Much faster than tessellating geometry
        - Returns accurate 2D footprint
        
        Limitations:
        - Only works in plan views (returns None for elevations/sections)
        - Doesn't capture 3D form (just footprint)
        - May miss complex edge cases
        """
        # Only use for plan views
        try:
            view_type = getattr(self.view, "ViewType", None)
            from Autodesk.Revit.DB import ViewType
            
            is_plan = view_type in (
                ViewType.FloorPlan,
                ViewType.CeilingPlan,
                getattr(ViewType, "EngineeringPlan", None),
                getattr(ViewType, "AreaPlan", None),
            )
            
            if not is_plan:
                return None  # Not a plan view, skip shortcuts
                
        except Exception:
            pass  # If can't determine, try anyway
            
        try:
            from Autodesk.Revit.DB import Wall, Floor, RoofBase, Ceiling, CurveLoop
        except Exception:
            return None
        
        cat_name = self._get_category_name(elem)
        
        # === WALLS ===
        if cat_name == "Walls":
            try:
                # Check if it's actually a Wall
                if not isinstance(elem, Wall):
                    return None
                
                # Get location curve
                loc = elem.Location
                if not hasattr(loc, "Curve"):
                    return None
                
                curve = loc.Curve
                
                # Get wall thickness
                wall_type = elem.WallType
                width = getattr(wall_type, "Width", 0.0)
                
                # Tessellate the curve into segments
                try:
                    tess = curve.Tessellate()
                    points_3d = list(tess)
                except Exception:
                    # Fallback: use start/end points
                    points_3d = [curve.GetEndPoint(0), curve.GetEndPoint(1)]
                
                if len(points_3d) < 2:
                    return None
                
                # Project to 2D
                points_2d = []
                for pt in points_3d:
                    if link_trf:
                        pt = link_trf.OfPoint(pt)
                    xy = self._to_local_xy(pt)
                    if xy:
                        points_2d.append(xy)
                
                if len(points_2d) < 2:
                    return None
                
                # Create a thin band for the wall centerline
                # (This is approximate - doesn't capture exact wall geometry)
                hull = self._convex_hull_2d(points_2d)
                
                if len(hull) >= 3:
                    return [{"points": hull, "is_hole": False}]
                else:
                    return None
                    
            except Exception:
                return None
        
        # === FLOORS / ROOFS / CEILINGS ===
        elif cat_name in ["Floors", "Roofs", "Ceilings"]:
            try:
                # Try to get boundary curves
                # This works for Floor, RoofBase, Ceiling
                
                # Method 1: Try GetBoundarySegments (Floor)
                boundary_loops = None
                if hasattr(elem, "GetBoundarySegments"):
                    try:
                        boundary_segments = elem.GetBoundarySegments()
                        if boundary_segments:
                            boundary_loops = boundary_segments
                    except Exception:
                        pass
                
                # Method 2: Try Sketch.Profile (some roofs)
                if not boundary_loops and hasattr(elem, "Sketch"):
                    try:
                        sketch = elem.Sketch
                        if sketch and hasattr(sketch, "Profile"):
                            profile = sketch.Profile
                            if profile:
                                boundary_loops = [profile]
                    except Exception:
                        pass
                
                if not boundary_loops:
                    return None
                
                # Process boundary loops
                all_points = []
                
                for loop in boundary_loops:
                    # loop might be a CurveArray or CurveLoop
                    curves = []
                    
                    if hasattr(loop, "GetEnumerator"):
                        # It's a collection
                        try:
                            for item in loop:
                                if hasattr(item, "Curve"):
                                    curves.append(item.Curve)
                                elif hasattr(item, "GetCurve"):
                                    curves.append(item.GetCurve())
                                else:
                                    curves.append(item)
                        except Exception:
                            pass
                    
                    # Extract points from curves
                    for curve in curves:
                        try:
                            tess = curve.Tessellate()
                            for pt in tess:
                                if link_trf:
                                    pt = link_trf.OfPoint(pt)
                                xy = self._to_local_xy(pt)
                                if xy:
                                    all_points.append(xy)
                        except Exception:
                            pass
                
                if len(all_points) < 3:
                    return None
                
                hull = self._convex_hull_2d(all_points)
                
                if len(hull) >= 3:
                    return [{"points": hull, "is_hole": False}]
                else:
                    return None
                    
            except Exception:
                return None
        
        # Category not supported by shortcuts
        return None   
        
    # ============================================================
    # HELPER METHODS
    # ============================================================
    
    def _get_category_name(self, elem):
        try:
            cat = getattr(elem, "Category", None)
            return getattr(cat, "Name", None) if cat else None
        except Exception:
            return None
    
    def _get_elem_id(self, elem):
        try:
            return getattr(getattr(elem, "Id", None), "IntegerValue", None)
        except Exception:
            return None
    
    def _to_local_xy(self, point):
        """Project 3D point to view local XY coords (set externally)"""
        if self._transform_fn:
            return self._transform_fn(point)
        # Fallback (shouldn't be used if wired correctly)
        try:
            return (float(point.X), float(point.Y))
        except Exception:
            return None
    
    def _get_bbox_corners(self, bb):
        """Get 8 corners of bounding box"""
        try:
            from Autodesk.Revit.DB import XYZ
            mins = bb.Min
            maxs = bb.Max
            return [
                XYZ(mins.X, mins.Y, mins.Z),
                XYZ(maxs.X, mins.Y, mins.Z),
                XYZ(mins.X, maxs.Y, mins.Z),
                XYZ(maxs.X, maxs.Y, mins.Z),
                XYZ(mins.X, mins.Y, maxs.Z),
                XYZ(maxs.X, mins.Y, maxs.Z),
                XYZ(mins.X, maxs.Y, maxs.Z),
                XYZ(maxs.X, maxs.Y, maxs.Z),
            ]
        except Exception:
            return []
    
    def _iter_solids(self, geom):
        """Iterate all solids in geometry (recursively)"""
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
                        for s in self._iter_solids(inst):
                            yield s
                        continue
                except Exception:
                    pass
                
                try:
                    if isinstance(obj, Solid) and getattr(obj, "Volume", 0) > 1e-9:
                        yield obj
                except Exception:
                    continue
        except Exception:
            return
    
    def _convex_hull_2d(self, points):
        """Compute 2D convex hull using Andrew's monotone chain algorithm"""
        if len(points) < 3:
            return []
        
        try:
            # Remove duplicates and sort
            pts = sorted(set((float(p[0]), float(p[1])) for p in points))
            
            if len(pts) < 3:
                return []
            
            def cross(o, a, b):
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
                if hull[0] != hull[-1]:
                    hull.append(hull[0])
                return hull
            else:
                return []
                
        except Exception:
            return []
    
    def get_statistics(self):
        """Return strategy usage statistics"""
        return dict(self.strategy_stats)

