# -*- coding: utf-8 -*-
"""
SSM/VOP occupancy exporter for Revit 2D orthographic views.
Behavior is governed by the project regression contract.
"""

import os
import json
import export_csv
import exporter_types
from debug import Logger
from config import CONFIG
import transforms
import grid


def _json_default(o):
    """JSON serializer for Revit API types used in debug payloads."""
    try:
        # ElementId
        iv = getattr(o, "IntegerValue", None)
        if isinstance(iv, int):
            return iv
        # XYZ
        if hasattr(o, "X") and hasattr(o, "Y") and hasattr(o, "Z"):
            return [float(o.X), float(o.Y), float(o.Z)]
        # UV
        if hasattr(o, "U") and hasattr(o, "V"):
            return [float(o.U), float(o.V)]
        # Anything with Id
        oid = getattr(getattr(o, "Id", None), "IntegerValue", None)
        if isinstance(oid, int):
            return oid
    except Exception:
        pass
    return str(o)

import sys
import datetime
import math
import csv
import hashlib

# Exporter version tag for downstream tools
# Base id comes from this script's filename; the run id is appended at runtime.

def _compute_exporter_base_id():
    """
    Example:
        'VOP v47 — Exporter Scaffold_ 251205_A1.py'
        -> 'VOP_v47___Exporter_Scaffold__251205_A1'
    """
    try:
        fname = os.path.basename(__file__)
    except Exception:
        fname = None

    if not fname:
        return "Unified Exporter_v4_A1"

    stem, _ = os.path.splitext(fname)
    clean = []
    for ch in stem:
        if ch.isalnum():
            clean.append(ch)
        elif ch in ("_", "-"):
            clean.append(ch)
        else:
            clean.append("_")
    return "".join(clean)

EXPORTER_BASE_ID = _compute_exporter_base_id()

# Kept for backward compatibility; per-run signature is derived from EXPORTER_BASE_ID + run_id.
EXPORTER_VERSION = EXPORTER_BASE_ID

# ------------------------------------------------------------
# Dynamo / Revit boilerplate
# ------------------------------------------------------------

DOC = None
View = object
ViewType = None
ViewDiscipline = None
CategoryType = None
ImportInstance = None
FilteredElementCollector = None
BuiltInCategory = None
BuiltInParameter = None
RevitLinkInstance = None
VisibleInViewFilter = None
Dimension = None
LinearDimension = None
XYZ = None
PointCloudInstance = None

try:
    import clr
    clr.AddReference("RevitAPI")
    clr.AddReference("RevitServices")

    from Autodesk.Revit.DB import (
        CategoryType as _CategoryType,
        ImportInstance as _ImportInstance,
        View as _View,
        ViewType as _ViewType,
        ViewDiscipline as _ViewDiscipline,
        FilteredElementCollector as _FilteredElementCollector,
        BuiltInCategory as _BuiltInCategory,
        BuiltInParameter as _BuiltInParameter,
        RevitLinkInstance as _RevitLinkInstance,
        VisibleInViewFilter as _VisibleInViewFilter,
        Dimension as _Dimension,
        LinearDimension as _LinearDimension,
        TextNote as _TextNote,
        IndependentTag as _IndependentTag,
        FilledRegion as _FilledRegion,
        DetailCurve as _DetailCurve,
        CurveElement as _CurveElement,
        FamilyInstance as _FamilyInstance,
        XYZ as _XYZ,
        PointCloudInstance as _PointCloudInstance,
        Outline as _Outline,
        BoundingBoxIntersectsFilter as _BoundingBoxIntersectsFilter,
    )
    
    Outline = _Outline
    BoundingBoxIntersectsFilter = _BoundingBoxIntersectsFilter

    from RevitServices.Persistence import DocumentManager

    # RoomTag lives under Autodesk.Revit.DB.Architecture in some Revit versions
    try:
        from Autodesk.Revit.DB.Architecture import RoomTag as _RoomTag
    except Exception:
        _RoomTag = None

    DOC = DocumentManager.Instance.CurrentDBDocument
    View = _View
    ViewType = _ViewType
    ViewDiscipline = _ViewDiscipline
    CategoryType = _CategoryType
    ImportInstance = _ImportInstance
    FilteredElementCollector = _FilteredElementCollector
    BuiltInCategory = _BuiltInCategory
    BuiltInParameter = _BuiltInParameter
    RevitLinkInstance = _RevitLinkInstance
    VisibleInViewFilter = _VisibleInViewFilter
    Dimension = _Dimension
    LinearDimension = _LinearDimension
    TextNote = _TextNote
    IndependentTag = _IndependentTag
    RoomTag = _RoomTag
    FilledRegion = _FilledRegion
    DetailCurve = _DetailCurve
    CurveElement = _CurveElement
    FamilyInstance = _FamilyInstance
    XYZ = _XYZ
    PointCloudInstance = _PointCloudInstance
    RoomTag = _RoomTag if _RoomTag is not None else None

    # 2D whitelist class handles (avoid NameError fallthrough in collectors)
    TextNote_cls = TextNote
    Dimension_cls = Dimension
    IndependentTag_cls = IndependentTag
    RoomTag_cls = RoomTag
    FilledRegion_cls = FilledRegion
    DetailCurve_cls = DetailCurve
    CurveElement_cls = CurveElement
    FamilyInstance_cls = FamilyInstance

    # Try to get Dynamo's Revit wrapper (for UnwrapElement)
    try:
        clr.AddReference("RevitNodes")
        import Revit
        clr.ImportExtensions(Revit.Elements)
    except Exception:
        pass

except Exception:
    pass

# Dynamo geometry (for optional crop/grid preview only)

try:
    import clr
    clr.AddReference("ProtoGeometry")
    from Autodesk.DesignScript.Geometry import Point as DSPoint, PolyCurve as DSPolyCurve
except Exception:
    DSPoint = None
    DSPolyCurve = None

try:
    import System
    import System.IO
    import System.Drawing as Drawing
    from System.Drawing import Bitmap
    from System.Drawing.Imaging import ImageFormat
except Exception:
    System = None
    Drawing = None
    Bitmap = None
    ImageFormat = None

# Initialize grid module with Revit API context
grid.set_revit_context(
    DOC, View, ViewType, CategoryType, ImportInstance,
    FilteredElementCollector, BuiltInCategory, BuiltInParameter,
    XYZ, DSPoint, DSPolyCurve
)

# ============================================================
# ADAPTIVE THRESHOLD COMPUTATION
# ============================================================

def _compute_adaptive_thresholds(elements, view, sil_cfg, logger):
    """
    Compute adaptive thresholds based on element size distribution in the view.
    """
    
    # Check if adaptive mode is enabled
    if not sil_cfg.get("use_adaptive_thresholds", False):
        return None
    
    # Get crop box transform for this view
    try:
        crop_box = view.CropBox
        if not crop_box:
            return None
        trf = crop_box.Transform
        inv_trf = trf.Inverse
    except Exception:
        return None
    
    def _to_local_xy_quick(point):
        if point is None:
            return None
        try:
            local_pt = inv_trf.OfPoint(point)
            return (float(local_pt.X), float(local_pt.Y))
        except Exception:
            return None
    
    # Get cell size
    try:
        cell_size = float(sil_cfg.get("cell_size_for_adaptive", 1.0))
        if cell_size <= 0:
            cell_size = 1.0
    except Exception:
        cell_size = 1.0
    
    # Collect element sizes (max dimension in cells)
    sizes = []
    
    for elem in elements:
        try:
            bb = elem.get_BoundingBox(view)
            if not bb or not bb.Min or not bb.Max:
                continue
            
            p0 = _to_local_xy_quick(bb.Min)
            p1 = _to_local_xy_quick(bb.Max)
            
            if not p0 or not p1:
                continue
            
            width_ft = abs(p1[0] - p0[0])
            height_ft = abs(p1[1] - p0[1])
            
            width_cells = width_ft / cell_size
            height_cells = height_ft / cell_size
            
            max_cells = max(width_cells, height_cells)
            
            if max_cells > 0:
                sizes.append(max_cells)
                
        except Exception:
            continue
    
    # Check if we have enough elements
    min_elements = sil_cfg.get("adaptive_min_elements", 50)
    if len(sizes) < min_elements:
        logger.info(
            "Adaptive thresholds: too few elements ({0} < {1}), using fixed thresholds".format(
                len(sizes), min_elements
            )
        )
        return None
    
    # Sort sizes
    sizes.sort()
    
    # Winsorize (remove outliers)
    if sil_cfg.get("adaptive_winsorize", True):
        lower_pct = float(sil_cfg.get("adaptive_winsorize_lower", 5))
        upper_pct = float(sil_cfg.get("adaptive_winsorize_upper", 95))
        
        n = len(sizes)
        lower_idx = int(n * lower_pct / 100.0)
        upper_idx = int(n * upper_pct / 100.0)
        
        # Keep elements between lower and upper percentile
        if upper_idx > lower_idx:
            sizes_winsorized = sizes[lower_idx:upper_idx]
            logger.info(
                "Adaptive thresholds: winsorized {0} -> {1} elements (removed {2}%)".format(
                    n, len(sizes_winsorized), lower_pct + (100 - upper_pct)
                )
            )
            sizes = sizes_winsorized
    
    # Compute percentiles
    def _percentile(data, pct):
        """Compute percentile of sorted data"""
        if not data:
            return None
        n = len(data)
        k = (n - 1) * pct / 100.0
        f = int(k)
        c = int(k) + 1
        if c >= n:
            return data[-1]
        if f == c:
            return data[int(k)]
        # Linear interpolation
        d0 = data[f] * (c - k)
        d1 = data[c] * (k - f)
        return d0 + d1
    
    tiny_pct = float(sil_cfg.get("adaptive_percentile_tiny", 25))
    medium_pct = float(sil_cfg.get("adaptive_percentile_medium", 50))
    large_pct = float(sil_cfg.get("adaptive_percentile_large", 75))
    
    tiny_threshold = _percentile(sizes, tiny_pct)
    medium_threshold = _percentile(sizes, medium_pct)
    large_threshold = _percentile(sizes, large_pct)
    
    # Apply min/max bounds
    min_tiny = float(sil_cfg.get("adaptive_min_tiny", 1))
    min_medium = float(sil_cfg.get("adaptive_min_medium", 3))
    min_large = float(sil_cfg.get("adaptive_min_large", 10))
    
    max_tiny = float(sil_cfg.get("adaptive_max_tiny", 5))
    max_medium = float(sil_cfg.get("adaptive_max_medium", 20))
    max_large = float(sil_cfg.get("adaptive_max_large", 100))
    
    tiny_threshold = max(min_tiny, min(max_tiny, tiny_threshold))
    medium_threshold = max(min_medium, min(max_medium, medium_threshold))
    large_threshold = max(min_large, min(max_large, large_threshold))
    
    # Ensure monotonic ordering
    if medium_threshold <= tiny_threshold:
        medium_threshold = tiny_threshold + 1
    if large_threshold <= medium_threshold:
        large_threshold = medium_threshold + 1
    
    logger.info(
        "Adaptive thresholds computed: tiny={0:.1f}, medium={1:.1f}, large={2:.1f} (from {3} elements)".format(
            tiny_threshold, medium_threshold, large_threshold, len(sizes)
        )
    )
    
    # Log distribution stats
    try:
        min_size = sizes[0]
        max_size = sizes[-1]
        median_size = _percentile(sizes, 50)
        logger.info(
            "  Size distribution: min={0:.1f}, median={1:.1f}, max={2:.1f}".format(
                min_size, median_size, max_size
            )
        )
    except Exception:
        pass
    
    return {
        "tiny": tiny_threshold,
        "medium": medium_threshold,
        "large": large_threshold,
    }
    
# ============================================================
# HYBRID SILHOUETTE EXTRACTION (Inline for Dynamo)
# ============================================================


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

# End of SilhouetteExtractor class
# ============================================================

# ------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------

# CONFIG dictionary imported from config.py

# ------------------------------------------------------------
# LOGGER
# ------------------------------------------------------------

# Logger class imported from debug.py
LOGGER = Logger()

# ------------------------------------------------------------
# Small helpers
# ------------------------------------------------------------

def _get_view_crop_fingerprint(view):
    """
    Return a simple crop fingerprint for the view as a 4-tuple
    (minX, minY, maxX, maxY) in model coordinates. Falls back to
    (0,0,0,0) if crop is inactive or unavailable.
    """
    try:
        if not bool(getattr(view, "CropBoxActive", True)):
            return (0.0, 0.0, 0.0, 0.0)
        bbox = getattr(view, "CropBox", None)
        if bbox is None or bbox.Min is None or bbox.Max is None:
            return (0.0, 0.0, 0.0, 0.0)
        return (
            float(bbox.Min.X),
            float(bbox.Min.Y),
            float(bbox.Max.X),
            float(bbox.Max.Y),
        )
    except Exception:
        return (0.0, 0.0, 0.0, 0.0)

def _stable_hex_digest(payload, length=8):
    """
    Compute a deterministic hex digest for the given payload.

    Uses SHA1 to avoid Python's per-process hash randomization so that
    cache keys remain stable across Dynamo/Revit sessions.
    """
    if payload is None:
        payload = ""
    try:
        data = payload.encode("utf-8")
    except Exception:
        data = str(payload).encode("utf-8", errors="ignore")
    digest = hashlib.sha1(data).hexdigest()
    if length and length > 0:
        return digest[:length]
    return digest

def _compute_view_signature(view, elem_ids=None):
    """
    Content-aware per-view signature similar to unified exporter v2:

      - ViewType, Scale, DetailLevel, TemplateId, Discipline, Phase
      - Crop fingerprint
      - Sorted element Ids visible in the view

    Used for cache reuse: if this signature matches, we reuse cached metrics.
    """
    if view is None:
        return "NoView"

    # View type + scale + detail level
    vt_name = _get_view_type_name(view)
    try:
        scale = int(getattr(view, "Scale", 0) or 0)
    except Exception:
        scale = 0

    try:
        detail = getattr(view, "DetailLevel", None)
        detail_str = detail.ToString() if detail is not None else ""
    except Exception:
        detail_str = ""

    # Template id
    tpl_id = -1
    try:
        vt_id = getattr(view, "ViewTemplateId", None)
        if vt_id is not None:
            tpl_id = getattr(vt_id, "IntegerValue", -1)
    except Exception:
        tpl_id = -1

    # Discipline + phase
    disc = _get_view_discipline_name(view)
    phase = _get_view_phase_name(view)

    # Crop fingerprint
    crop_fp = _get_view_crop_fingerprint(view)
    try:
        crop_str = "{0:.2f},{1:.2f},{2:.2f},{3:.2f}".format(
            float(crop_fp[0]),
            float(crop_fp[1]),
            float(crop_fp[2]),
            float(crop_fp[3]),
        )
    except Exception:
        crop_str = "0.00,0.00,0.00,0.00"

    # Element Ids
    if elem_ids is None:
        elem_ids = []
    try:
        elem_ids = sorted(set(int(x) for x in elem_ids if x is not None))
    except Exception:
        elem_ids = list(elem_ids) if elem_ids is not None else []

    elem_ids_str = ",".join(str(i) for i in elem_ids)

    sig_parts = [
        vt_name,
        str(scale),
        detail_str,
        str(tpl_id),
        disc,
        phase,
        crop_str,
        elem_ids_str,
    ]
    sig_str = "|".join(sig_parts)

    return _stable_hex_digest(sig_str, length=8)

def _compute_config_hash(config):
    try:
        payload = json.dumps(config, sort_keys=True)
        return _stable_hex_digest(payload, length=8)
    except Exception:
        return ""

def _json_sanitize_keys(obj):
    """Recursively make JSON-safe structures, especially dict keys (tuple -> string)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            # JSON keys must be basic scalars; convert tuples (e.g. (i,j)) and any non-scalar keys.
            if isinstance(k, tuple):
                k2 = ",".join(str(x) for x in k)  # (i,j) -> "i,j"
            elif isinstance(k, (str, int, float, bool)) or k is None:
                k2 = k
            else:
                k2 = str(k)
            out[k2] = _json_sanitize_keys(v)
        return out

    # Convert iterables that aren't JSON-native
    if isinstance(obj, (list, tuple, set)):
        return [_json_sanitize_keys(x) for x in obj]

    return obj


def _collect_element_ids_for_signature(view, logger):
    """
    Collect a per-view set of element Ids for the cache signature.

    Uses a view-scoped FilteredElementCollector (no geometry),
    so it sees both model and annotation that are visible in the view.
    """
    ids = []
    if view is None or DOC is None or _FilteredElementCollector is None:
        return ids

    try:
        col = _FilteredElementCollector(DOC, view.Id).WhereElementIsNotElementType()
        for e in col:
            try:
                eid = getattr(getattr(e, "Id", None), "IntegerValue", None)
                if eid is not None:
                    ids.append(int(eid))
            except Exception:
                continue
    except Exception as ex:
        vid = getattr(getattr(view, "Id", None), "IntegerValue", None)
        logger.warn(
            "Cache: failed to collect element ids for view Id={0}: {1}".format(vid, ex)
        )
        return []

    if not ids:
        return []

    ids = sorted(set(ids))
    return ids

def _enum_to_name(val):
    """
    Generic enum/string helper.

    - For real .NET enums: ToString()
    - For anything else: str(val)
    """
    if val is None:
        return ""
    try:
        to_str = getattr(val, "ToString", None)
        if callable(to_str) and not isinstance(val, (int, float, str)):
            s = to_str()
            if s:
                return s
    except Exception:
        pass
    try:
        return str(val)
    except Exception:
        return ""

def _enum_name_from_int(enum_type, raw, fallback_map=None):
    """
    Robust name lookup when Pythonnet has already converted enums to ints.

    Tries, in order:
    - System.Enum.GetName(enum_type, raw)
    - enum_type(raw).ToString()
    - fallback_map[int(raw)] if provided
    - str(raw) as last resort
    """
    if raw is None or enum_type is None:
        return ""

    # Avoid double-wrapping a real enum
    try:
        # If this is already an enum instance, just ToString it
        if hasattr(raw, "__class__") and raw.__class__.__name__ == enum_type.__name__:
            return _enum_to_name(raw)
    except Exception:
        pass

    # Try System.Enum.GetName
    if System is not None:
        try:
            name = System.Enum.GetName(enum_type, raw)
            if name:
                return name
        except Exception:
            pass

    # Try constructing enum_type(raw)
    try:
        ev = enum_type(raw)
        return _enum_to_name(ev)
    except Exception:
        pass

    # Fallback map for known numeric values
    if fallback_map is not None:
        try:
            key = int(raw)
            name = fallback_map.get(key)
            if name:
                return name
        except Exception:
            pass

    # Last resort: string
    return str(raw)

# Known mapping for ViewType (from Revit API docs)
# https://www.revitapidocs.com/2026/bf04dabc-05a3-baf0-3564-f96c0bde3400.htm

_VIEWTYPE_FALLBACK = {
    0: "Undefined",
    1: "FloorPlan",
    2: "CeilingPlan",
    3: "Elevation",
    4: "ThreeD",
    5: "Schedule",
    6: "DrawingSheet",
    7: "ProjectBrowser",
    8: "Report",
    10: "DraftingView",
    11: "Legend",
    115: "EngineeringPlan",
    116: "AreaPlan",
    117: "Section",
    118: "Detail",
    119: "CostReport",
    120: "LoadsReport",
    121: "PressureLossReport",
    122: "ColumnSchedule",
    123: "PanelSchedule",
    124: "Walkthrough",
    125: "Rendering",
    126: "SystemsAnalysisReport",
    214: "Internal",
}

# Known mapping for ViewDiscipline (from Revit API docs)
# https://www.revitapidocs.com/2025/94363df8-8e46-3d70-8273-dfa0abaf2c46.htm

_VIEWDISC_FALLBACK = {
    1: "Architectural",
    2: "Structural",
    4: "Mechanical",
    8: "Electrical",
    16: "Plumbing",
    4095: "Coordination",
}

def _get_view_type_name(view):
    """Return a stable string for the view's ViewType."""
    if view is None:
        return ""
    try:
        val = getattr(view, "ViewType", None)
    except Exception:
        val = None

    if val is None:
        return ""

    # If we got an enum instance, ToString should be enough
    if ViewType is not None and not isinstance(val, (int, float, str)):
        return _enum_to_name(val)

    # If we got an int or numeric string, map it
    try:
        raw_int = int(val)
    except Exception:
        # Non-numeric but not an enum? Just ToString.
        return _enum_to_name(val)

    return _enum_name_from_int(ViewType, raw_int, _VIEWTYPE_FALLBACK)

def _get_view_discipline_name(view):
    """Return a stable string for the view's Discipline."""
    if view is None:
        return ""
    try:
        val = getattr(view, "Discipline", None)
    except Exception:
        val = None

    if val is None:
        return ""

    # If we got an enum instance, ToString should be enough
    if ViewDiscipline is not None and not isinstance(val, (int, float, str)):
        return _enum_to_name(val)

    # If we got an int or numeric string, map it
    try:
        raw_int = int(val)
    except Exception:
        return _enum_to_name(val)

    return _enum_name_from_int(ViewDiscipline, raw_int, _VIEWDISC_FALLBACK)

def _get_view_phase_name(view):
    """Best-effort phase name for the view.

    Tries:
    1) view.Phase.Name
    2) VIEW_PHASE built-in parameter → Phase element.Name
    3) VIEW_PHASE text/value as last resort
    """
    # 1) Strongly-typed Phase property
    try:
        phase = getattr(view, "Phase", None)
        if phase is not None:
            name = getattr(phase, "Name", "") or ""
            if name:
                return name
    except Exception:
        pass

    # 2) VIEW_PHASE parameter → Phase element
    if BuiltInParameter is None or DOC is None:
        return ""

    param = None
    try:
        param = view.get_Parameter(BuiltInParameter.VIEW_PHASE)
    except Exception:
        param = None

    if param is None:
        return ""

    # Try AsElementId → Phase element
    elem_id = None
    try:
        as_elem_id = getattr(param, "AsElementId", None)
        if callable(as_elem_id):
            elem_id = as_elem_id()
    except Exception:
        elem_id = None

    if elem_id is not None:
        try:
            phase_elem = DOC.GetElement(elem_id)
            if phase_elem is not None:
                name = getattr(phase_elem, "Name", "") or ""
                if name:
                    return name
        except Exception:
            pass

    # 3) Fallback to string representations
    try:
        as_string = param.AsString()
        if as_string:
            return as_string
    except Exception:
        pass

    try:
        as_val_string = param.AsValueString()
        if as_val_string:
            return as_val_string
    except Exception:
        pass

    return ""

def _get_project_guid(doc):
    """Return a stable identifier for the current Revit project."""
    if doc is None:
        return "UnknownProject"
    try:
        pi = getattr(doc, "ProjectInformation", None)
        guid = getattr(pi, "UniqueId", None)
        if guid:
            return str(guid)
    except Exception:
        pass

    # Fallbacks if GUID isn't available
    try:
        path = getattr(doc, "PathName", "") or ""
        if path:
            return "Path_" + re.sub(r"[^A-Za-z0-9_-]", "_", path)
    except Exception:
        pass
    try:
        title = getattr(doc, "Title", "") or ""
        if title:
            return "Title_" + re.sub(r"[^A-Za-z0-9_-]", "_", title)
    except Exception:
        pass

    return "UnknownProject"

def _get_cache_file_path(config, view_or_doc):
    """
    Compute the cache file path inside the export output folder, using
    the cache base name defined in CONFIG and appending the project GUID.
    """
    if not isinstance(config, dict):
        config = CONFIG

    export_cfg = config.get("export", {}) or {}
    cache_cfg = config.get("cache", {}) or {}

    # The output folder is always the parent directory for the cache file
    root = export_cfg.get("output_dir") or os.path.join(
        os.path.expanduser("~"), "Documents", "_metrics"
    )

    # Accept either a View or a Document
    doc = None
    if view_or_doc is not None:
        try:
            from Autodesk.Revit.DB import Document as RevitDocument
        except Exception:
            RevitDocument = None
        if RevitDocument is not None and isinstance(view_or_doc, RevitDocument):
            doc = view_or_doc
        else:
            doc = getattr(view_or_doc, "Document", None)
    if doc is None:
        doc = DOC

    proj_guid = _get_project_guid(doc)

    # Base name comes strictly from CONFIG; no hardcoded value here
    base_name = cache_cfg.get("file_name")
    if not base_name:
        # Defensive fallback if the config is malformed
        base_name = "grid_cache.json"

    stem, ext = os.path.splitext(base_name)
    if not ext:
        ext = ".json"

    final_name = "{0}_{1}{2}".format(stem, proj_guid, ext)
    return os.path.join(root, final_name)

def _load_view_cache(cache_path, exporter_version, config_hash, project_guid, logger):
    """
    Load cache from disk, validating exporter version + config hash + project.
    Returns a dict with at least: { "views": { ... } }.
    """
    empty = {
        "exporter_version": exporter_version,
        "config_hash": config_hash,
        "project_guid": project_guid,
        "views": {},
    }
    if not cache_path:
        return empty

    if not os.path.isfile(cache_path):
        return empty

    try:
        with open(cache_path, "r") as f:
            data = json.load(f)
    except Exception as ex:
        logger.warn("Cache: could not read '{0}': {1}".format(cache_path, ex))
        return empty

    if not isinstance(data, dict):
        return empty

    if data.get("exporter_version") != exporter_version:
        logger.info("Cache: exporter_version mismatch; ignoring existing cache.")
        return empty
    if data.get("config_hash") != config_hash:
        logger.info("Cache: config_hash mismatch; ignoring existing cache.")
        return empty
    if project_guid and data.get("project_guid") not in (None, project_guid):
        logger.info("Cache: project_guid mismatch; ignoring existing cache.")
        return empty

    views = data.get("views") or {}
    if not isinstance(views, dict):
        views = {}

    logger.info("Cache: loaded {0} cached view(s) from '{1}'".format(len(views), cache_path))
    return {
        "exporter_version": exporter_version,
        "config_hash": config_hash,
        "project_guid": project_guid,
        "views": views,
    }

def _save_view_cache(cache_path, cache_data, logger):
    """
    Persist cache to disk. For each view we store:
        - view_signature
        - row (metrics)
        - elapsed_sec
    """
    if not cache_path:
        return

    try:
        # Ensure parent directory exists
        parent = os.path.dirname(cache_path)
        if not _ensure_dir(parent, logger):
            return

        views = cache_data.get("views") or {}
        safe_views = {}
        for k, v in views.items():
            if not isinstance(v, dict):
                continue
            row = v.get("row") or {}
            elapsed_sec = float(v.get("elapsed_sec") or 0.0)
            view_sig = v.get("view_signature") or ""
            safe_views[str(k)] = {
                "view_signature": view_sig,
                "row": row,
                "elapsed_sec": elapsed_sec,
            }

        payload = {
            "exporter_version": cache_data.get("exporter_version"),
            "config_hash": cache_data.get("config_hash"),
            "project_guid": cache_data.get("project_guid"),
            "views": safe_views,
        }

        with open(cache_path, "w") as f:
            json.dump(payload, f, indent=2, default=_json_default)

        logger.info(
            "Cache: wrote {0} cached view(s) to '{1}'".format(len(safe_views), cache_path)
        )
    except Exception as ex:
        logger.warn("Cache: could not write '{0}': {1}".format(cache_path, ex))

# ------------------------------------------------------------
# RESET / CACHE FLAGS
# ------------------------------------------------------------

def _get_reset_and_cache_flags():
    """
    IN[1] – UseCache (bool)
    IN[2] – ForceRecompute (bool)

    Semantics:
        - UseCache=False      => no cache read or write.
        - ForceRecompute=True => ignore existing cache this run (but still write if UseCache=True).

    Returns:
        (force_recompute, cache_enabled)
    """
    force_recompute = False
    cache_enabled = True  # default: use cache

    if "IN" in globals():
        # IN[1]: UseCache
        if len(IN) > 1 and IN[1] is not None:
            cache_enabled = bool(IN[1])

        # IN[2]: ForceRecompute
        if len(IN) > 2 and IN[2] is not None:
            force_recompute = bool(IN[2])

    # If cache_enabled is False, we ignore cache entirely.
    # If cache_enabled is True but force_recompute is True,
    # we skip reading cache but will still write a fresh one at the end.
    return force_recompute, cache_enabled

def _apply_runtime_inputs_to_config(config, logger=None):
    """
    Apply Dynamo inputs that override CONFIG:

        IN[3] – OutputFolderOverride
        IN[4] – RenderPNG
    """
    export_cfg = config.setdefault("export", {})
    png_cfg = config.setdefault("occupancy_png", {})

    if "IN" not in globals():
        return

    # IN[3]: output folder override
    if len(IN) > 3 and IN[3] is not None:
        out_dir = str(IN[3]).strip()
        if out_dir:
            export_cfg["output_dir"] = out_dir
            if logger:
                logger.info("Export: output_dir overridden to '{0}' via IN[3]".format(out_dir))

    # IN[4]: render PNG override
    if len(IN) > 4 and IN[4] is not None:
        png_enabled = bool(IN[4])
        png_cfg["enabled"] = png_enabled
        if logger:
            logger.info("Export: occupancy_png.enabled overridden to {0} via IN[4]".format(png_enabled))

def _build_navigation_noise_cat_ids():
    """
    Navigation / symbolic / view-mechanic / modifier categories that must NOT
    contribute 3D occupancy. These do not own meaningful geometry regions:
    grids, section heads, cameras, reveals, etc.
    Applies to both host and linked models.
    """
    ids = set()
    if BuiltInCategory is None:
        return ids

    names = [
        # Navigation & annotation mechanics
        "OST_Grids",
        "OST_GridHeads",
        "OST_Levels",
        "OST_LevelHeads",
        "OST_SectionHeads",
        "OST_SectionMarks",
        "OST_ElevationMarks",
        "OST_CalloutHeads",
        "OST_ReferenceViewer",
        "OST_Viewers",
        "OST_Cameras",

        # Scene/view symbolic controls
        "OST_SunPath",
        "OST_SectionBox",
        "OST_AdaptivePoints",

        # Geometry modifiers that do *not* own AREAL area and whose effects
        # are already baked into host solids (e.g., wall cuts)
        "OST_Reveals",
    ]

    for name in names:
        try:
            bic = getattr(BuiltInCategory, name, None)
            if bic is not None:
                ids.add(int(bic))
        except Exception:
            continue

    return ids

def _build_model_suppression_cat_ids():
    """
    Build a set of category Ids to suppress from the 3D pass
    (host + linked). These are categories that should never
    contribute to 3D occupancy in VOP v47.

    Note: 2D pass (host-only) is unaffected.
    """
    ids = set()

    if BuiltInCategory is None or DOC is None:
        return ids

    # Names must match Autodesk.Revit.DB.BuiltInCategory enum names
    names = [
        # Volumetric / analytical “non-geometry” we never want in 3D
        "OST_Rooms",
        "OST_Areas",
        "OST_MEPSpaces",

        "OST_DetailComponents",  # Detail Items
        "OST_Lines",             # Model Lines
        "OST_PointClouds",       # Point cloud instances
    ]

    for name in names:
        try:
            bic = getattr(BuiltInCategory, name, None)
            if bic is None:
                continue
            cat = DOC.Settings.Categories.get_Item(bic)
            if cat is not None:
                ids.add(cat.Id.IntegerValue)
        except Exception:
            # Defensive: bad categories shouldn't break exporter
            continue

    return ids

def _get_excluded_3d_cat_ids():
    """
    Union of navigation noise + model suppression categories.
    This is the authoritative exclusion set for 3D occupancy
    (host AND linked).
    """
    out = set()
    out.update(_build_navigation_noise_cat_ids())
    out.update(_build_model_suppression_cat_ids())
    return out

# ------------------------------------------------------------
# GRID: MODEL GEOMETRY EXTENTS 
# ------------------------------------------------------------

def _compute_model_geom_extents(view, logger):
    view_id_val = getattr(getattr(view, "Id", None), "IntegerValue", "Unknown")

    if DOC is None or FilteredElementCollector is None or CategoryType is None:
        return None

    boxes = []

    try:
        col = FilteredElementCollector(DOC, view.Id).WhereElementIsNotElementType()
        for e in col:
            if ImportInstance is not None and isinstance(e, ImportInstance):
                continue

            cat = getattr(e, "Category", None)
            if cat is None:
                continue

            if getattr(cat, "CategoryType", None) != CategoryType.Model:
                continue

            try:
                bic = cat.Id.IntegerValue
            except Exception:
                bic = None

            if BuiltInCategory is not None and bic == int(BuiltInCategory.OST_Cameras):
                continue

            try:
                bb = e.get_BoundingBox(view)
            except Exception:
                bb = None
            if bb is None or bb.Min is None or bb.Max is None:
                continue

            min_x = bb.Min.X
            min_y = bb.Min.Y
            max_x = bb.Max.X
            max_y = bb.Max.Y
            cx = 0.5 * (min_x + max_x)
            cy = 0.5 * (min_y + max_y)

            try:
                eid = getattr(getattr(e, "Id", None), "IntegerValue", None)
            except Exception:
                eid = None
            try:
                cat_name = getattr(cat, "Name", "") or "<NoCat>"
            except Exception:
                cat_name = "<NoCat>"

            boxes.append((cx, cy, min_x, min_y, max_x, max_y, eid, cat_name))

    except Exception as ex:
        logger.warn(
            "Grid: view Id={0} error computing model geometry extents; "
            "falling back to other methods. {1}".format(view_id_val, ex)
        )
        return None

    if not boxes:
        logger.info(
            "Grid: view Id={0} found no model geometry extents".format(view_id_val)
        )
        return None

    n = len(boxes)

    if n <= 10:
        min_x = min(b[2] for b in boxes)
        min_y = min(b[3] for b in boxes)
        max_x = max(b[4] for b in boxes)
        max_y = max(b[5] for b in boxes)
        logger.info(
            "Grid: view Id={0} using model geometry extents (n={1}, no clustering)".format(
                view_id_val, n
            )
        )
        return (min_x, min_y, max_x, max_y)

    sum_cx = sum(b[0] for b in boxes)
    sum_cy = sum(b[1] for b in boxes)
    mean_cx = sum_cx / float(n)
    mean_cy = sum_cy / float(n)

    dist2_list = []
    for idx, (cx, cy, _, _, _, _, _, _) in enumerate(boxes):
        dx = cx - mean_cx
        dy = cy - mean_cy
        d2 = dx * dx + dy * dy
        dist2_list.append((d2, idx))

    dist2_list.sort(key=lambda t: t[0])

    k = max(1, int(0.999 * n))
    keep_indices = set(idx for (_, idx) in dist2_list[:k])

    min_x = float("inf")
    min_y = float("inf")
    max_x = float("-inf")
    max_y = float("-inf")
    kept = 0
    drivers = {"min_x": None, "min_y": None, "max_x": None, "max_y": None}

    for idx, (cx, cy, bx_min_x, bx_min_y, bx_max_x, bx_max_y, eid, cat_name) in enumerate(boxes):
        if idx not in keep_indices:
            continue
        kept += 1

        if bx_min_x < min_x:
            min_x = bx_min_x
            drivers["min_x"] = (eid, cat_name)
        if bx_min_y < min_y:
            min_y = bx_min_y
            drivers["min_y"] = (eid, cat_name)
        if bx_max_x > max_x:
            max_x = bx_max_x
            drivers["max_x"] = (eid, cat_name)
        if bx_max_y > max_y:
            max_y = bx_max_y
            drivers["max_y"] = (eid, cat_name)

    if kept == 0 or min_x == float("inf"):
        min_x = min(b[2] for b in boxes)
        min_y = min(b[3] for b in boxes)
        max_x = max(b[4] for b in boxes)
        max_y = max(b[5] for b in boxes)
        logger.info(
            "Grid: view Id={0} model geometry clustering removed all candidates; "
            "falling back to raw extents".format(view_id_val)
        )
        return (min_x, min_y, max_x, max_y)

    logger.info(
        "Grid: view Id={0} using clustered model geometry extents "
        "(kept {1}/{2} elements)".format(view_id_val, kept, n)
    )

    return (min_x, min_y, max_x, max_y)

def _compute_drafting_geom_extents(view, crop_box, logger):
    view_id_val = getattr(getattr(view, "Id", None), "IntegerValue", "Unknown")

    if DOC is None or FilteredElementCollector is None:
        bb_min = crop_box.Min
        bb_max = crop_box.Max
        return (bb_min.X, bb_min.Y, bb_max.X, bb_max.Y)

    min_x = float("inf")
    min_y = float("inf")
    max_x = float("-inf")
    max_y = float("-inf")
    found_any = False

    try:
        col = FilteredElementCollector(DOC, view.Id).WhereElementIsNotElementType()
        for e in col:
            try:
                bb = e.get_BoundingBox(view)
            except Exception:
                bb = None
            if bb is None or bb.Min is None or bb.Max is None:
                continue

            found_any = True

            if bb.Min.X < min_x:
                min_x = bb.Min.X
            if bb.Min.Y < min_y:
                min_y = bb.Min.Y
            if bb.Max.X > max_x:
                max_x = bb.Max.X
            if bb.Max.Y > max_y:
                max_y = bb.Max.Y

    except Exception as ex:
        logger.warn(
            "Grid: view Id={0} error computing drafting/legend geometry extents; "
            "falling back to crop. {1}".format(view_id_val, ex)
        )
        found_any = False

    if not found_any or min_x == float("inf"):
        bb_min = crop_box.Min
        bb_max = crop_box.Max
        logger.info(
            "Grid: view Id={0} (drafting/legend) found no geometry; using crop extents".format(
                view_id_val
            )
        )
        return (bb_min.X, bb_min.Y, bb_max.X, bb_max.Y)

    logger.info(
        "Grid: view Id={0} (drafting/legend) using view-scoped geometry extents".format(
            view_id_val
        )
    )
    return (min_x, min_y, max_x, max_y)

def _compute_effective_xy_extents(view, crop_box, logger):
    if crop_box is None:
        logger.warn(
            "Grid: view Id={0} has no crop box; using degenerate extents".format(
                getattr(getattr(view, "Id", None), "IntegerValue", "Unknown")
            )
        )
        return (0.0, 0.0, 0.0, 0.0)

    if DOC is None or FilteredElementCollector is None:
        bb_min = crop_box.Min
        bb_max = crop_box.Max
        return (bb_min.X, bb_min.Y, bb_max.X, bb_max.Y)

    vtype = getattr(view, "ViewType", None)
    view_id_val = getattr(getattr(view, "Id", None), "IntegerValue", "Unknown")

    is_drafting = (vtype == getattr(ViewType, "DraftingView", None))
    is_legend = hasattr(ViewType, "Legend") and (vtype == getattr(ViewType, "Legend"))
    if is_drafting or is_legend:
        return _compute_drafting_geom_extents(view, crop_box, logger)

    try:
        crop_active = bool(getattr(view, "CropBoxActive", True))
    except Exception:
        crop_active = True

    if crop_active:
        bb_min = crop_box.Min
        bb_max = crop_box.Max
        logger.info(
            "Grid: view Id={0} (model view, crop ON) using crop extents for base domain".format(
                view_id_val
            )
        )
        return (bb_min.X, bb_min.Y, bb_max.X, bb_max.Y)

    geom_ext = _compute_model_geom_extents(view, logger)
    if geom_ext is not None:
        min_x, min_y, max_x, max_y = geom_ext
        logger.info(
            "Grid: view Id={0} (model view, crop OFF) using model geometry extents".format(
                view_id_val
            )
        )
        return (min_x, min_y, max_x, max_y)

    bb_min = crop_box.Min
    bb_max = crop_box.Max
    logger.warn(
        "Grid: view Id={0} (model view, crop OFF) could not compute model geometry extents; "
        "falling back to crop".format(view_id_val)
    )
    return (bb_min.X, bb_min.Y, bb_max.X, bb_max.Y)

# ------------------------------------------------------------
# 2D EXTENT DRIVERS (for grid expansion)
# ------------------------------------------------------------

def _is_extent_driver_2d(e):
    cat = getattr(e, "Category", None)
    name = (getattr(cat, "Name", None) or "").lower()

    if "tag" in name:
        return True
    if "dimension" in name:
        return True
    if "text" in name:
        return True

    return False

def _compute_2d_annotation_extents(view, elems2d, logger):
    debug_cfg = CONFIG.get("debug", {})
    enable_debug = bool(debug_cfg.get("enable_driver2d_debug", True))
    log_once = bool(debug_cfg.get("driver2d_log_once_per_signature", False))

    # Get crop box (for both signature + transform)
    try:
        crop_box = getattr(view, "CropBox", None)
    except Exception:
        crop_box = None

    # Build a coarse signature: ViewType + Scale + crop width/height
    sig = None
    if crop_box is not None:
        try:
            bb_min = crop_box.Min
            bb_max = crop_box.Max
            w_x = bb_max.X - bb_min.X
            w_y = bb_max.Y - bb_min.Y
            vtype_str = str(getattr(view, "ViewType", None))
            scale_val = getattr(view, "Scale", None)
            sig = (
                vtype_str,
                int(scale_val) if isinstance(scale_val, int) else None,
                round(w_x, 3),
                round(w_y, 3),
            )
        except Exception:
            sig = None

    do_log = enable_debug
    if enable_debug and log_once and sig is not None:
        global DRIVER2D_DEBUG_SIGS
        if sig in DRIVER2D_DEBUG_SIGS:
            do_log = False
        else:
            DRIVER2D_DEBUG_SIGS.add(sig)

    # ---------------------------------------------------------------
    # Inverse transform: world → crop-local XY
    # ---------------------------------------------------------------
    inv_trf = None
    if crop_box is not None:
        try:
            inv_trf = crop_box.Transform.Inverse
        except Exception:
            inv_trf = None

    def _to_local_xy(pt):
        """Project model-space XYZ → crop-local (x, y) using crop_box."""
        if pt is None:
            return None
        if inv_trf is not None:
            try:
                lp = inv_trf.OfPoint(pt)
                return (lp.X, lp.Y)
            except Exception:
                pass
        # Fallback (rare): treat model XY as local XY
        try:
            return (pt.X, pt.Y)
        except Exception:
            return None

    # ---------------------------------------------------------------
    # Annotation crop + hard cap handling (overlay 2D only)
    # ---------------------------------------------------------------
    # Base crop-local extents (model crop)
    base_min_x = base_min_y = base_max_x = base_max_y = None
    if crop_box is not None:
        try:
            bb_min = crop_box.Min
            bb_max = crop_box.Max
            base_min_x, base_min_y = float(bb_min.X), float(bb_min.Y)
            base_max_x, base_max_y = float(bb_max.X), float(bb_max.Y)
        except Exception:
            base_min_x = base_min_y = base_max_x = base_max_y = None

    # Detect annotation crop active (best-effort)
    ann_crop_active = False
    try:
        ann_crop_active = bool(getattr(view, "AnnotationCropActive", False))
    except Exception:
        ann_crop_active = False
    if not ann_crop_active:
        try:
            p = view.get_Parameter(BuiltInParameter.VIEWER_ANNOTATION_CROP_ACTIVE)
            if p is not None:
                ann_crop_active = bool(p.AsInteger() == 1)
        except Exception:
            pass

    # Hard cap for overlay-driven expansion (in cells)
    cap_cells = None
    try:
        cap_cells = int((CONFIG.get("grid", {}) or {}).get("overlay_expand_cap_cells", 2000))
    except Exception:
        cap_cells = 2000
    if cap_cells < 0:
        cap_cells = 0

    # Cell size in model units (ft) for cap conversion
    cell_size_model = None
    try:
        scale_val = getattr(view, "Scale", None)
        if isinstance(scale_val, int) and scale_val > 0:
            paper_in = float((CONFIG.get("grid", {}) or {}).get("cell_size_paper_in", 0.25))
            cell_size_model = (paper_in / 12.0) * float(scale_val)
    except Exception:
        cell_size_model = None

    # Annotation crop geometry is not reliably accessible in the API across view types / Revit versions.
    # - We DO read AnnotationCropActive reliably (boolean).
    # - When annotation crop is ACTIVE, we approximate its extents as:
    #       model-crop bounds expanded by a fixed printed margin (configurable).
    #   This prevents far-away overlay annotations (hidden by annotation crop) from exploding the grid.
    # - When annotation crop is NOT active, overlay expansion is allowed up to the hard cap envelope.
    ann_min_x = ann_min_y = ann_max_x = ann_max_y = None
    ann_margin_ft = None
    if ann_crop_active and base_min_x is not None:
        try:
            margin_in = float((CONFIG.get("grid", {}) or {}).get("overlay_anno_crop_margin_in", 2.0))
        except Exception:
            margin_in = 2.0
        if margin_in < 0.0:
            margin_in = 0.0
        try:
            scale_val = getattr(view, "Scale", None)
            if isinstance(scale_val, int) and scale_val > 0:
                ann_margin_ft = (margin_in / 12.0) * float(scale_val)
        except Exception:
            ann_margin_ft = None
        if ann_margin_ft is not None:
            ann_min_x = base_min_x - ann_margin_ft
            ann_min_y = base_min_y - ann_margin_ft
            ann_max_x = base_max_x + ann_margin_ft
            ann_max_y = base_max_y + ann_margin_ft

    cap_ft = None
    if cell_size_model is not None:
        try:
            cap_ft = float(cap_cells) * float(cell_size_model)
        except Exception:
            cap_ft = None

    # Allowed expansion envelope around the model crop (crop-local XY):
    # - If annotation crop is active: limit overlay-driven expansion to the proxy annotation-crop bounds.
    # - Else: allow overlay-driven expansion up to the hard cap envelope.
    allow_min_x = allow_min_y = allow_max_x = allow_max_y = None
    if ann_crop_active and ann_min_x is not None:
        allow_min_x, allow_min_y, allow_max_x, allow_max_y = ann_min_x, ann_min_y, ann_max_x, ann_max_y
    elif base_min_x is not None and cap_ft is not None:
        allow_min_x = base_min_x - cap_ft
        allow_min_y = base_min_y - cap_ft
        allow_max_x = base_max_x + cap_ft
        allow_max_y = base_max_y + cap_ft

    # Overlay drivers only: Text / Dimensions / Tags (no crop-clipped 2D)
    driver_elems = [e for e in elems2d if _is_extent_driver_2d(e)]

    if not driver_elems:
        if do_log:
            logger.info(
                "Grid: no driver 2D elements found for view Id={0}".format(
                    getattr(getattr(view, "Id", None), "IntegerValue", "Unknown")
                )
            )
        return None

    samples = []
    view_id_val = getattr(getattr(view, "Id", None), "IntegerValue", "Unknown")

    try:
        from Autodesk.Revit.DB import Dimension as _Dim
    except Exception:
        _Dim = None

    for e in driver_elems:
        try:
            eid = getattr(getattr(e, "Id", None), "IntegerValue", None)
        except Exception:
            eid = None
        try:
            cat_name = getattr(getattr(e, "Category", None), "Name", None) or "<NoCat>"
        except Exception:
            cat_name = "<NoCat>"
        try:
            type_name = e.GetType().Name
        except Exception:
            type_name = "Element"

        bb_min_x = bb_min_y = bb_max_x = bb_max_y = None

        # Dimensions: use endpoints + text position
        if _Dim is not None and isinstance(e, _Dim):
            pts_local = []
            try:
                curve = getattr(e, "Curve", None)
                if curve is not None:
                    try:
                        p0 = curve.GetEndPoint(0)
                        p1 = curve.GetEndPoint(1)
                        xy0 = _to_local_xy(p0)
                        xy1 = _to_local_xy(p1)
                        if xy0:
                            pts_local.append(xy0)
                        if xy1:
                            pts_local.append(xy1)
                    except Exception:
                        pass
                try:
                    tp = getattr(e, "TextPosition", None)
                    if tp is not None:
                        xy_tp = _to_local_xy(tp)
                        if xy_tp:
                            pts_local.append(xy_tp)
                except Exception:
                    pass
            except Exception:
                pts_local = []

            if pts_local:
                xs = [pt[0] for pt in pts_local]
                ys = [pt[1] for pt in pts_local]
                bb_min_x = min(xs)
                bb_min_y = min(ys)
                bb_max_x = max(xs)
                bb_max_y = max(ys)

        # Fallback: view-specific bounding box
        if bb_min_x is None:
            try:
                bb = e.get_BoundingBox(view)
            except Exception:
                bb = None

            if bb and bb.Min and bb.Max:
                xy_min = _to_local_xy(bb.Min)
                xy_max = _to_local_xy(bb.Max)
                if xy_min and xy_max:
                    bb_min_x, bb_min_y = xy_min
                    bb_max_x, bb_max_y = xy_max

        if bb_min_x is None:
            continue

        if do_log:
            logger.info(
                "Grid: driver2D view Id={0} elemId={1} cat='{2}' type='{3}' "
                "bbX=[{4:.3f},{5:.3f}] bbY=[{6:.3f},{7:.3f}] (crop-local)".format(
                    view_id_val,
                    eid,
                    cat_name,
                    type_name,
                    bb_min_x,
                    bb_max_x,
                    bb_min_y,
                    bb_max_y,
                )
            )

        cx = 0.5 * (bb_min_x + bb_max_x)
        cy = 0.5 * (bb_min_y + bb_max_y)

        samples.append((cx, cy, bb_min_x, bb_min_y, bb_max_x, bb_max_y))

    if not samples:
        return None

    n = len(samples)

    # Simple extents (no clustering needed for small n)
    min_x = min(s[2] for s in samples)
    min_y = min(s[3] for s in samples)
    max_x = max(s[4] for s in samples)
    max_y = max(s[5] for s in samples)

    # Clamp overlay-driven extents to the allowed expansion envelope (hard cap).
    if allow_min_x is not None:
        try:
            min_x = max(min_x, allow_min_x)
            min_y = max(min_y, allow_min_y)
            max_x = min(max_x, allow_max_x)
            max_y = min(max_y, allow_max_y)
        except Exception:
            pass

    # If annotation crop bounds were computed, clamp to them as the final authority for overlay.
    if ann_crop_active and ann_min_x is not None:
        try:
            min_x = max(min_x, ann_min_x)
            min_y = max(min_y, ann_min_y)
            max_x = min(max_x, ann_max_x)
            max_y = min(max_y, ann_max_y)
        except Exception:
            pass

    # If annotation crop is active but we couldn't evaluate its bounds, we set cap_cells=0 above.
    # In that case, treat degenerate extents as "no expansion".
    if base_min_x is not None and (max_x <= min_x or max_y <= min_y):
        return None

    if do_log:
        logger.info(
            "Grid: annotation extents (drivers, n={0}) "
            "X=[{1:.3f},{2:.3f}] Y=[{3:.3f},{4:.3f}] (crop-local)".format(
                n, min_x, max_x, min_y, max_y
            )
        )

    return (min_x, min_y, max_x, max_y)

def _make_rect_polycurve(view, crop_box, min_x, min_y, max_x, max_y):
    if DSPoint is None or DSPolyCurve is None:
        return None

    try:
        T = crop_box.Transform
    except Exception:
        T = None

    pts = []
    corners = [
        (min_x, min_y),
        (max_x, min_y),
        (max_x, max_y),
        (min_x, max_y),
        (min_x, min_y),
    ]

    if T is not None and XYZ is not None:
        for (x, y) in corners:
            try:
                local = XYZ(x, y, 0.0)
                world = T.OfPoint(local)
                pts.append(DSPoint.ByCoordinates(world.X, world.Y, world.Z))
            except Exception:
                pts.append(DSPoint.ByCoordinates(x, y, 0.0))
    else:
        for (x, y) in corners:
            pts.append(DSPoint.ByCoordinates(x, y, 0.0))

    try:
        return DSPolyCurve.ByPoints(pts)
    except Exception:
        return None

def _make_cell_rect_polycurve(view, grid_data, i, j):
    """Build a PolyCurve rectangle for a single grid cell (i,j) in model XY.

    Uses the same crop-box transform as _make_rect_polycurve so the
    preview geometry lands correctly in Dynamo world coordinates.
    """
    if DSPoint is None or DSPolyCurve is None:
        return None

    if not isinstance(grid_data, dict):
        return None

    origin = grid_data.get("origin_model_xy")
    cell_size = grid_data.get("cell_size_model")
    crop_box = grid_data.get("crop_box_model")

    if origin is None or cell_size is None:
        return None

    try:
        ox, oy = origin
        s = float(cell_size)
    except Exception:
        return None

    try:
        i_val = int(i)
        j_val = int(j)
    except Exception:
        return None

    half = 0.5 * s
    cx = ox + i_val * s
    cy = oy + j_val * s

    min_x = cx - half
    max_x = cx + half
    min_y = cy - half
    max_y = cy + half

    try:
        return _make_rect_polycurve(view, crop_box, min_x, min_y, max_x, max_y)
    except Exception:
        return None

def _build_occupancy_preview_rects(view, grid_data, occupancy, config, logger):
    """Build PolyCurve rectangles for occupancy cells, grouped by layer.

    Returns (rects_3d_only, rects_2d_only, rects_2d_over_3d).

    Controlled by:
        debug.enable_region_previews
        debug.max_region_areal_cells
    """
    debug_cfg = config.get("debug", {}) if isinstance(config, dict) else {}
    enable_region_previews = bool(debug_cfg.get("enable_region_previews", False))
    if not enable_region_previews:
        return [], [], []

    if DSPoint is None or DSPolyCurve is None:
        return [], [], []

    if not isinstance(occupancy, dict):
        return [], [], []

    occ_map = occupancy.get("occupancy_map") or {}
    if not occ_map:
        return [], [], []

    # Basic grid info
    if not isinstance(grid_data, dict):
        return [], [], []
    if grid_data.get("origin_model_xy") is None or grid_data.get("cell_size_model") is None:
        return [], [], []

    max_cells = debug_cfg.get("max_region_areal_cells", 512)
    if max_cells is None or max_cells <= 0:
        max_cells = 512

    occ_cfg = config.get("occupancy", {}) if isinstance(config, dict) else {}
    code_3d = occ_cfg.get("code_3d_only", 0)
    code_2d = occ_cfg.get("code_2d_only", 1)
    code_2d_over_3d = occ_cfg.get("code_2d_over_3d", 2)

    rects_3d = []
    rects_2d = []
    rects_2d_over_3d = []

    count_total = 0
    for cell, code in occ_map.items():
        if count_total >= max_cells:
            break
        try:
            i, j = cell
        except Exception:
            continue

        pc = grid._make_cell_rect_polycurve(view, grid_data, i, j)
        if pc is None:
            continue

        if code == code_3d:
            rects_3d.append(pc)
        elif code == code_2d:
            rects_2d.append(pc)
        elif code == code_2d_over_3d:
            rects_2d_over_3d.append(pc)
        else:
            # Unknown code; ignore for preview
            continue

        count_total += 1

    logger.info(
        "Debug: built {0} occupancy preview cell(s) (3D={1}, 2D={2}, 2D-over-3D={3})".format(
            count_total, len(rects_3d), len(rects_2d), len(rects_2d_over_3d)
        )
    )

    return rects_3d, rects_2d, rects_2d_over_3d

def _build_occupancy_png(view, grid_data, occupancy_map, config, logger):
    """
    Render a PNG from occupancy_map.

    occupancy_map: dict[(i,j)] -> occupancy_code
    codes come from config["occupancy"]:
        code_empty, code_3d_only, code_2d_only, code_2d_over_3d

    Uses config["occupancy_png"] for:
        - enabled (bool, default True)
        - pixels_per_cell (int, default 4)

    Returns full file path as string, or None on failure/disabled.
    """
    # Guard on drawing support
    if Bitmap is None or Drawing is None or ImageFormat is None:
        logger.warn("Occupancy: System.Drawing not available; PNG export skipped.")
        return None

    # Basic grid dims
    try:
        n_i = int(grid_data.get("grid_n_i") or 0)
        n_j = int(grid_data.get("grid_n_j") or 0)
    except Exception:
        return None

    if n_i <= 0 or n_j <= 0:
        return None
    if not occupancy_map:
        return None

    # Config / debug flags
    debug_cfg = {}
    occ_cfg = {}
    export_cfg = {}
    occ_png_cfg = {}

    if isinstance(config, dict):
        debug_cfg   = config.get("debug", {}) or {}
        occ_cfg     = config.get("occupancy", {}) or {}
        export_cfg  = config.get("export", {}) or {}
        occ_png_cfg = config.get("occupancy_png", {}) or {}

    # Optional flags (both must allow PNG)
    enable_png_debug = bool(debug_cfg.get("enable_occupancy_png", True))
    enable_png_cfg   = bool(occ_png_cfg.get("enabled", True))
    if not (enable_png_debug and enable_png_cfg):
        return None

    # pixels_per_cell from occupancy_png config
    try:
        pixels_per_cell = int(occ_png_cfg.get("pixels_per_cell", 4))
    except Exception:
        pixels_per_cell = 4

    if pixels_per_cell < 1:
        pixels_per_cell = 1
    if pixels_per_cell > 50:
        pixels_per_cell = 50  # sanity cap

    # bitmap size in pixels
    width_px  = n_i * pixels_per_cell
    height_px = n_j * pixels_per_cell

    # Occupancy codes (fall back to the defaults if missing)
    def _int_or(d, key, default):
        try:
            return int(d.get(key, default))
        except Exception:
            return default

    #code_empty = _int_or(occ_cfg, "code_empty", 0)
    code_3d    = _int_or(occ_cfg, "code_3d_only", 0)
    code_2d    = _int_or(occ_cfg, "code_2d_only", 1)
    code_2d3d  = _int_or(occ_cfg, "code_2d_over_3d", 2)

    # Colors: 3D-only, 2D-only, 2D-over-3D (3 colors)
    col_empty = Drawing.Color.White
    col_3d    = Drawing.Color.FromArgb(192, 192, 192)  # light gray
    col_2d    = Drawing.Color.FromArgb(0, 0, 255)      # blue
    col_2d3d  = Drawing.Color.FromArgb(255, 0, 255)    # magenta

    # Create bitmap (scaled by pixels_per_cell)
    bmp = Bitmap(width_px, height_px)

    # Initialize background
    for x in range(width_px):
        for y in range(height_px):
            bmp.SetPixel(x, y, col_empty)

    # Fill cells; flip j so origin is bottom-left visually
    for i in range(n_i):
        for j in range(n_j):
            code = occupancy_map.get((i, j))
            if code is None:
                continue

            if code == code_3d:
                col = col_3d
            elif code == code_2d:
                col = col_2d
            elif code == code_2d3d:
                col = col_2d3d
            else:
                # empty / unknown -> already background
                continue

            # map cell (i, j) to pixel block
            x0 = i * pixels_per_cell
            # flip vertically so j=0 is bottom row
            y0 = (n_j - 1 - j) * pixels_per_cell
            x1 = x0 + pixels_per_cell
            y1 = y0 + pixels_per_cell

            # fill the block
            for px in range(x0, min(x1, width_px)):
                for py in range(y0, min(y1, height_px)):
                    bmp.SetPixel(px, py, col)

    # Resolve output directory
    out_dir = export_cfg.get("output_dir")
    if not out_dir:
        # fallback to %TEMP%
        try:
            temp_root = System.IO.Path.GetTempPath() if System is not None else None
        except Exception:
            temp_root = None
        out_dir = temp_root or r"C:\Temp"

    # Optionally tuck into an "occupancy" subfolder
    if System is not None:
        occ_dir = System.IO.Path.Combine(out_dir, "occupancy")
    else:
        occ_dir = os.path.join(out_dir, "occupancy")

    try:
        if not os.path.isdir(occ_dir):
            os.makedirs(occ_dir)
    except Exception:
        # if we can't create the subfolder, fall back to out_dir
        occ_dir = out_dir
        try:
            if not os.path.isdir(occ_dir):
                os.makedirs(occ_dir)
        except Exception as ex:
            logger.warn(
                "Occupancy: could not create PNG output dir '{0}': {1}"
                .format(occ_dir, ex)
            )
            return None

    # Build a safe filename
    try:
        view_id_val = int(view.Id.IntegerValue)
    except Exception:
        view_id_val = 0

    try:
        view_name = view.Name or ""
    except Exception:
        view_name = ""

    try:
        import re
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", view_name)
    except Exception:
        safe_name = "view"

    file_name = "VOP_occ_{0}_{1}.png".format(view_id_val, safe_name)
    full_path = os.path.join(occ_dir, file_name)

    # Save PNG
    try:
        bmp.Save(full_path, ImageFormat.Png)
    except Exception as ex:
        logger.warn("Occupancy: failed to save PNG '{0}': {1}".format(full_path, ex))
        return None

    logger.info("Occupancy: PNG saved to '{0}'".format(full_path))
    return full_path

# ------------------------------------------------------------
# CLIP VOLUME
# ------------------------------------------------------------

def build_clip_volume_for_view(view, config, logger):
    """
    v47 Stage-2 clip volume authority.

    Returns:
      {
        "is_valid": bool,
        "kind": "plan"|"vertical"|"drafting",
        "corners_host": [XYZ]*8 (host model coords) or None,
        "obb_host": {center, axes, extents} (dict) or None,
        "depth_mode": "model_z"|"view_dir"|"none",
        "z_min": float|None,
        "z_max": float|None,
        "far": float|None
      }
    """
    view_id_val = getattr(getattr(view, "Id", None), "IntegerValue", "Unknown")
    logger.info("ClipVolume: building clip volume for view Id={0}".format(view_id_val))

    clip = {
        "is_valid": False,
        "kind": "drafting",
        "corners_host": None,
        "obb_host": None,
        "depth_mode": "none",
        "z_min": None,
        "z_max": None,
        "far": None,
        "z0_local": None,
        "z1_local": None,
    }

    # Drafting views: XY only
    try:
        if hasattr(ViewType, "DraftingView") and getattr(view, "ViewType", None) == ViewType.DraftingView:
            clip["is_valid"] = True
            clip["kind"] = "drafting"
            clip["depth_mode"] = "none"
            return clip
    except Exception:
        pass

    # Need CropBox for any model view clip volume
    try:
        crop_bb = getattr(view, "CropBox", None)
    except Exception:
        crop_bb = None
    if crop_bb is None or getattr(crop_bb, "Min", None) is None or getattr(crop_bb, "Max", None) is None:
        return clip

    # Plans/RCP: depth is model Z slab from ViewRange
    z_min, z_max = _get_plan_view_vertical_range(view, config, logger)
    if z_min is not None and z_max is not None:
        corners_host = transforms.host_crop_prism_corners_model(view, z_min, z_max, XYZ)
        if corners_host:
            clip["is_valid"] = True
            clip["kind"] = "plan"
            clip["depth_mode"] = "model_z"
            clip["corners_host"] = corners_host
            clip["z_min"] = z_min
            clip["z_max"] = z_max
            try:
                clip["obb_host"] = transforms.obb_from_ordered_corners(corners_host)
            except Exception:
                clip["obb_host"] = None
        return clip

    # Vertical views (sections/elevations): depth is along view direction (CropBox local Z).
    # We use CropBox local min/max for XY, and depth from far clip when available.
    # If far clip is unavailable/off, we fall back to CropBox local Z span.
    try:
        trf = crop_bb.Transform
    except Exception:
        trf = None
    if trf is None:
        return clip

    # Local crop extents
    try:
        minL = crop_bb.Min
        maxL = crop_bb.Max
        min_x, max_x = float(minL.X), float(maxL.X)
        min_y, max_y = float(minL.Y), float(maxL.Y)
        near_z = float(minL.Z)
        far_z_default = float(maxL.Z)
    except Exception:
        return clip

    # Try to get far clip distance (preferred)
    far_dist = None
    try:
        # Many vertical views expose this parameter
        p_far = view.get_Parameter(BuiltInParameter.VIEWER_BOUND_OFFSET_FAR)
        if p_far is not None:
            far_dist = float(p_far.AsDouble())
    except Exception:
        far_dist = None

    # Determine local Z span
    if far_dist is not None and far_dist > 0:
        z0 = near_z
        z1 = near_z + far_dist
        clip["far"] = far_dist
    else:
        z0 = near_z
        z1 = far_z_default
        clip["far"] = None

    clip["z0_local"] = z0
    clip["z1_local"] = z1

    # Build 8 corners in *local crop coords*, then transform to host model coords
    try:
        local_corners = [
            XYZ(min_x, min_y, z0),
            XYZ(min_x, min_y, z1),
            XYZ(min_x, max_y, z0),
            XYZ(min_x, max_y, z1),
            XYZ(max_x, min_y, z0),
            XYZ(max_x, min_y, z1),
            XYZ(max_x, max_y, z0),
            XYZ(max_x, max_y, z1),
        ]
        corners_host = [trf.OfPoint(p) for p in local_corners]
    except Exception:
        return clip

    clip["is_valid"] = True
    clip["kind"] = "vertical"
    clip["depth_mode"] = "view_dir"
    clip["corners_host"] = corners_host
    try:
        clip["obb_host"] = _obb_from_ordered_corners(corners_host)
    except Exception:
        clip["obb_host"] = None
    return clip

def _get_plan_view_vertical_range(host_view, config, logger):
    """
    Return (z_min, z_max) in model coords representing the host view's
    effective vertical clip range for plan/ceiling/area views, based on
    ViewRange. Returns (None, None) if not available.

    Optional refinement (flagged):
      - Plans/Area: CUT_DOWN => [min(bottom, depth) .. cut]
      - RCP:        CUT_UP   => [cut .. max(top, cut)]
    """
    if ViewType is None:
        return (None, None)

    vtype = getattr(host_view, "ViewType", None)
    if vtype not in (
        getattr(ViewType, "FloorPlan", None),
        getattr(ViewType, "CeilingPlan", None),
        getattr(ViewType, "AreaPlan", None),
    ):
        return (None, None)

    if DOC is None:
        return (None, None)

    try:
        import Autodesk.Revit.DB as RevitDB
    except Exception:
        RevitDB = None
    if RevitDB is None:
        return (None, None)

    try:
        vr = host_view.GetViewRange()
    except Exception:
        vr = None
    if vr is None:
        return (None, None)

    PlanViewPlane = getattr(RevitDB, "PlanViewPlane", None)
    if PlanViewPlane is None:
        return (None, None)

    def _plane_z(plane):
        try:
            lvl_id = vr.GetLevelId(plane)
            if lvl_id is None or lvl_id == RevitDB.ElementId.InvalidElementId:
                return None
            lvl = DOC.GetElement(lvl_id)
            base_z = getattr(lvl, "Elevation", None)
            off = vr.GetOffset(plane)
            if base_z is None or off is None:
                return None
            return base_z + off
        except Exception:
            return None

    top_z    = _plane_z(PlanViewPlane.TopClipPlane)
    cut_z    = _plane_z(PlanViewPlane.CutPlane)
    bottom_z = _plane_z(PlanViewPlane.BottomClipPlane)
    depth_z  = _plane_z(PlanViewPlane.ViewDepthPlane)

    zs = [z for z in (top_z, cut_z, bottom_z, depth_z) if z is not None]
    if not zs:
        return (None, None)

    # FULL conservative slab
    clip_cfg = config.get("clip_volume", {}) if isinstance(config, dict) else {}
    use_cut_slab = bool(clip_cfg.get("use_cut_slab", False))

    slab_mode = "FULL"
    if use_cut_slab:
        try:
            if getattr(host_view, "ViewType", None) == getattr(ViewType, "CeilingPlan", None):
                slab_mode = "CUT_UP"    # RCP
            else:
                slab_mode = "CUT_DOWN"  # plans/area
        except Exception:
            slab_mode = "FULL"

    low_candidates  = [z for z in (bottom_z, depth_z) if z is not None]
    high_candidates = [z for z in (top_z, cut_z) if z is not None]

    if slab_mode == "CUT_DOWN" and cut_z is not None and low_candidates:
        z_min = min(low_candidates)
        z_max = cut_z
    elif slab_mode == "CUT_UP" and cut_z is not None and high_candidates:
        z_min = cut_z
        z_max = max(high_candidates)
    else:
        if not low_candidates or not high_candidates:
            z_min = min(zs)
            z_max = max(zs)
        else:
            z_min = min(low_candidates)
            z_max = max(high_candidates)

    if z_min > z_max:
        z_min, z_max = z_max, z_min

    try:
        logger.info(
            "Projection: plan view Id={0} vertical clip mode={1} z_min={2:.3f}, z_max={3:.3f}".format(
                getattr(getattr(host_view, "Id", None), "IntegerValue", "Unknown"),
                slab_mode,
                z_min,
                z_max,
            )
        )
    except Exception:
        pass

    return (z_min, z_max)

# ------------------------------------------------------------
# PROJECTION: COLLECTION HELPERS
# ------------------------------------------------------------

def _summarize_elements_by_type(elems):
    counts = {}
    for e in elems:
        try:
            tname = e.GetType().Name
        except Exception:
            tname = "Element"
        counts[tname] = counts.get(tname, 0) + 1
    return counts

def _summarize_elements_by_category(elems):
    counts = {}
    for e in elems:
        cat = getattr(e, "Category", None)
        name = getattr(cat, "Name", None) if cat is not None else "<No Category>"
        counts[name] = counts.get(name, 0) + 1
    return counts

def _get_host_visible_model_cat_ids(host_view, logger):
    """
    Return a set of IntegerValue ids for *model* categories that are
    visible in the given host view, based on VG (view.GetCategoryHidden).

    If anything goes wrong, returns None and we skip host-VG filtering.
    """
    doc = getattr(host_view, "Document", None)
    if doc is None or CategoryType is None:
        logger.warn("Link3D: host view has no Document/CategoryType; skipping host VG filter.")
        return None

    settings = getattr(doc, "Settings", None)
    categories = getattr(settings, "Categories", None) if settings is not None else None
    if categories is None:
        logger.warn("Link3D: cannot read Settings.Categories; skipping host VG filter.")
        return None

    get_hidden = getattr(host_view, "GetCategoryHidden", None)
    visible_ids = set()

    for cat in categories:
        if cat is None:
            continue
        try:
            if getattr(cat, "CategoryType", None) != CategoryType.Model:
                continue
        except Exception:
            continue

        cat_id = getattr(cat, "Id", None)
        try:
            cat_id_val = getattr(cat_id, "IntegerValue", None)
        except Exception:
            cat_id_val = None

        if cat_id_val is None:
            continue

        # If GetCategoryHidden isn't callable, or throws, fail-open = visible.
        is_hidden = False
        try:
            if callable(get_hidden):
                is_hidden = bool(host_view.GetCategoryHidden(cat_id))
        except Exception:
            is_hidden = False

        if not is_hidden:
            visible_ids.add(cat_id_val)

    view_id_val = getattr(getattr(host_view, "Id", None), "IntegerValue", "Unknown")
    logger.info(
        "Link3D: host view Id={0} has {1} visible model categories (VG).".format(
            view_id_val, len(visible_ids)
        )
    )

    return visible_ids if visible_ids else None

class _LinkedElementProxy(object):
    """
    Lightweight proxy that exposes the minimal surface of a Revit element:
    - Id          -> link element's ElementId
    - Category    -> link element's Category
    - LinkInstanceId -> owning RevitLinkInstance Id
    - get_BoundingBox(view) -> host-space BoundingBox (view is ignored)
    - get_Geometry(options) -> underlying link element geometry (link space)
    """
    __slots__ = ("_bb", "_elem", "_link_trf", "Id", "Category", "LinkInstanceId")

    def __init__(self, element, link_inst, host_min, host_max, link_trf):
        class _BB(object):
            __slots__ = ("Min", "Max")
            def __init__(self, mn, mx):
                self.Min = mn
                self.Max = mx

        self._bb = _BB(host_min, host_max)
        self._elem = element                 # underlying element in link doc
        self._link_trf = link_trf            # Transform link → host
        self.Id = getattr(element, "Id", None)
        self.Category = getattr(element, "Category", None)
        self.LinkInstanceId = getattr(link_inst, "Id", None)

    def get_BoundingBox(self, view):
        # view ignored – bounding box is already in host coords
        return self._bb

    def get_Geometry(self, options):
        """
        Delegate to the underlying link element. Geometry comes back in
        *link* coordinates; we apply the link transform later when
        converting points to XY.
        """
        elem = self._elem
        if elem is None:
            return None
        try:
            return elem.get_Geometry(options)
        except Exception:
            return None

# ------------------------------------------------------------
# GEOMETRIC TRANSFORMS
# ------------------------------------------------------------
# Geometric transform and vector math functions imported from transforms.py:
# - transform_bbox_to_host, obb_from_ordered_corners, aabb_center_extents_from_bboxxyz
# - obb_intersects_aabb, host_crop_prism_corners_model
# - Vector math: v3, v_add, v_sub, v_mul, v_dot, v_cross, v_len, v_norm, xyz_to_v

def _build_link_proxies_from_collector(
    link_inst,
    collector,
    host_view,
    logger,
    host_visible_model_cat_ids=None,
    obb_link=None,
):
    """
    Given a FilteredElementCollector in the link document, build per-element
    host-space BBox proxies for model elements.

    If host_visible_model_cat_ids is not None, we treat this link as
    'By Host View' and drop any categories that are hidden in the host view.
    """
    proxies = []

    if collector is None or CategoryType is None:
        return proxies

    try:
        trf = link_inst.GetTransform()
    except Exception:
        trf = None

    if trf is None:
        return proxies

    excluded_ids = _get_excluded_3d_cat_ids()

    cats_included = {}
    cats_excl_host_hidden = {}
    cats_excl_global = {}

    for e in collector:
        # Skip nested links and imports for now
        if RevitLinkInstance is not None and isinstance(e, RevitLinkInstance):
            continue
        if ImportInstance is not None and isinstance(e, ImportInstance):
            continue

        cat = getattr(e, "Category", None)
        cat_type = getattr(cat, "CategoryType", None) if cat is not None else None
        cat_name = getattr(cat, "Name", None) if cat is not None else "<No Category>"

        # Get category id value (if any)
        cat_id_val = None
        if cat is not None:
            try:
                cat_id_val = getattr(getattr(cat, "Id", None), "IntegerValue", None)
            except Exception:
                cat_id_val = None

        # Global 3D exclusion (rooms, grids, levels, etc.)
        if cat_id_val in excluded_ids:
            cats_excl_global[cat_name] = cats_excl_global.get(cat_name, 0) + 1
            continue

        # Keep only model categories
        if cat_type != CategoryType.Model:
            continue

        # Host-VG filter for By Host View / Custom modes
        if host_visible_model_cat_ids is not None:
            if cat_id_val is None or cat_id_val not in host_visible_model_cat_ids:
                cats_excl_host_hidden[cat_name] = cats_excl_host_hidden.get(cat_name, 0) + 1
                continue

        try:
            bb_link = e.get_BoundingBox(None)
        except Exception:
            bb_link = None

        if bb_link is None or bb_link.Min is None or bb_link.Max is None:
            continue

        # Narrow-phase: enforce true transformed host crop volume (OBB in link coords)
        if obb_link is not None:
            try:
                aabb_c, aabb_e = transforms.aabb_center_extents_from_bboxxyz(bb_link)
                if not transforms.obb_intersects_aabb(obb_link, aabb_c, aabb_e):
                    continue
            except Exception:
                # If the test fails unexpectedly, keep the element (underfilter preferred)
                pass

        host_min, host_max = transforms.transform_bbox_to_host(bb_link, trf, XYZ)
        if host_min is None or host_max is None:
            continue

        proxies.append(_LinkedElementProxy(e, link_inst, host_min, host_max, trf))
        cats_included[cat_name] = cats_included.get(cat_name, 0) + 1

    # ----------------------------------------------------------------------
    # Diagnostics per link instance
    # ----------------------------------------------------------------------
    view_id_val = getattr(getattr(host_view, "Id", None), "IntegerValue", "Unknown")
    link_inst_id_val = getattr(getattr(link_inst, "Id", None), "IntegerValue", "Unknown")

    link_doc = None
    try:
        link_doc = link_inst.GetLinkDocument()
    except Exception:
        link_doc = None

    link_title = getattr(link_doc, "Title", "<link>") if link_doc is not None else "<link>"

    logger.info(
        "Link3D: view Id={0}, link '{1}' (InstId={2}): collected {3} 3D proxy element(s).".format(
            view_id_val, link_title, link_inst_id_val, len(proxies)
        )
    )

    if cats_included:
        parts = ["{0} ({1})".format(name, count) for name, count in sorted(cats_included.items())]
        logger.info("Link3D: included link categories: {0}".format(", ".join(parts)))

    if cats_excl_host_hidden:
        parts = ["{0} ({1})".format(name, count) for name, count in sorted(cats_excl_host_hidden.items())]
        logger.info(
            "Link3D: excluded link categories by host VG (hidden): {0}".format(", ".join(parts))
        )

    if cats_excl_global:
        parts = ["{0} ({1})".format(name, count) for name, count in sorted(cats_excl_global.items())]
        logger.info(
            "Link3D: excluded link categories by global 3D exclusion: {0}".format(", ".join(parts))
        )

    return proxies

def _collect_link_proxies_by_linked_view(host_view, link_inst, logger):
    """
    Option 1: if the host view displays the link 'By Linked View', use that
    linked view as the visibility filter.

    This path is cheap when available and respects the link's own view settings.
    """
    if FilteredElementCollector is None:
        return []

    link_doc = None
    try:
        link_doc = link_inst.GetLinkDocument()
    except Exception:
        link_doc = None

    if link_doc is None:
        return []

    # Try to read the linked view from link overrides on the host view
    linked_view_id = None
    try:
        overrides = host_view.GetLinkOverrides(link_inst.Id)
    except Exception:
        overrides = None

    if overrides is not None:
        try:
            linked_view_id = overrides.LinkedViewId
        except Exception:
            linked_view_id = None

    # If there's no linked view selected, this path is a no-op
    if linked_view_id is None:
        return []

    try:
        linked_view = link_doc.GetElement(linked_view_id)
    except Exception:
        linked_view = None

    if linked_view is None:
        return []

    try:
        col = (
            FilteredElementCollector(link_doc, linked_view.Id)
            .WhereElementIsNotElementType()
        )
    except Exception as ex:
        logger.warn(
            "Projection: linked-view collector failed for host view {0}, link {1}: {2}".format(
                getattr(getattr(host_view, "Id", None), "IntegerValue", "Unknown"),
                getattr(getattr(link_inst, "Id", None), "IntegerValue", "Unknown"),
                ex,
            )
        )
        return []

    # --- Intersect linked-view contents with host crop volume (crop box) ---
    # Only if we have the geometry types available and a valid CropBox.
    if BoundingBoxIntersectsFilter is not None and XYZ is not None:
        try:
            crop_bb = getattr(host_view, "CropBox", None)
        except Exception:
            crop_bb = None

        if crop_bb is not None and crop_bb.Min is not None and crop_bb.Max is not None:
            try:
                trf = link_inst.GetTransform()
                inv = trf.Inverse
            except Exception:
                inv = None

            if inv is not None:
                # Transform host CropBox corners into link-doc coordinates
                xs = [crop_bb.Min.X, crop_bb.Max.X]
                ys = [crop_bb.Min.Y, crop_bb.Max.Y]
                zs = [crop_bb.Min.Z, crop_bb.Max.Z]
                min_x = min_y = min_z = float("inf")
                max_x = max_y = max_z = float("-inf")

                for x in xs:
                    for y in ys:
                        for z in zs:
                            pt = inv.OfPoint(XYZ(x, y, z))
                            if pt.X < min_x: min_x = pt.X
                            if pt.Y < min_y: min_y = pt.Y
                            if pt.Z < min_z: min_z = pt.Z
                            if pt.X > max_x: max_x = pt.X
                            if pt.Y > max_y: max_y = pt.Y
                            if pt.Z > max_z: max_z = pt.Z

                if min_x <= max_x and min_y <= max_y and min_z <= max_z:
                    try:
                        outline = Outline(XYZ(min_x, min_y, min_z), XYZ(max_x, max_y, max_z))
                        bb_filter = BoundingBoxIntersectsFilter(outline)
                        col = col.WherePasses(bb_filter)
                        logger.info(
                            "Projection: ByLinkedView link {0} clipped to host CropBox for view Id={1}".format(
                                getattr(getattr(link_inst, "Id", None), "IntegerValue", "Unknown"),
                                getattr(getattr(host_view, "Id", None), "IntegerValue", "Unknown"),
                            )
                        )
                    except Exception as ex_clip:
                        logger.warn(
                            "Projection: failed to clip ByLinkedView link {0} to host CropBox: {1}".format(
                                getattr(getattr(link_inst, "Id", None), "IntegerValue", "Unknown"),
                                ex_clip,
                            )
                        )

    # NOTE: host_visible_model_cat_ids=None here → no host VG filtering in ByLinkedView mode.
    return _build_link_proxies_from_collector(
        link_inst,
        col,
        host_view,
        logger,
        host_visible_model_cat_ids=None,
    )

def _collect_link_proxies_by_instance_bbox(host_view, link_inst, host_visible_model_cat_ids, logger):
    """
    Host-clip driven spatial clipping for link 3D:
      - Build host clip prism (plans: ViewRange slab; vertical: far/depth).
      - Transform prism corners into link coords => true OBB.
      - Broad-phase: OBB -> enclosing AABB (full XYZ) for collector filter.
      - Narrow-phase: candidate link AABB vs true OBB (in _build_link_proxies_from_collector).
    """
    if FilteredElementCollector is None or XYZ is None:
        return []

    # Resolve link doc
    try:
        link_doc = link_inst.GetLinkDocument()
    except Exception:
        link_doc = None
    if link_doc is None:
        return []

    # Build host clip volume once
    clip = build_clip_volume_for_view(host_view, None, logger)
    if not clip.get("is_valid", False):
        return []
    corners_host = clip.get("corners_host", None)
    if not corners_host:
        return []

    # Transform host corners into link space
    try:
        trf = link_inst.GetTransform()
        inv = trf.Inverse
    except Exception:
        inv = None
    if inv is None:
        return []

    try:
        corners_link = [inv.OfPoint(p) for p in corners_host]
    except Exception:
        return []

    # True OBB in link coords (for narrow phase)
    obb_link = None
    try:
        obb_link = transforms.obb_from_ordered_corners(corners_link)
    except Exception:
        obb_link = None

    # Broad-phase AABB in link coords (min/max of all 8 corners)
    try:
        xs = [p.X for p in corners_link]
        ys = [p.Y for p in corners_link]
        zs = [p.Z for p in corners_link]
        min_link = XYZ(min(xs), min(ys), min(zs))
        max_link = XYZ(max(xs), max(ys), max(zs))
    except Exception:
        return []
        
    if Outline is None or BoundingBoxIntersectsFilter is None:
        logger.warn("Outline/BoundingBoxIntersectsFilter unavailable")
        return []
    outline = Outline(min_link, max_link)
    
    try:
        outline = Outline(min_link, max_link)
        bb_filter = BoundingBoxIntersectsFilter(outline)
        col = (
            FilteredElementCollector(link_doc)
            .WhereElementIsNotElementType()
            .WherePasses(bb_filter)
        )
    except Exception as ex:
        logger.warn(
            "Projection: linked-BBox collector failed for link {0}: {1}".format(
                getattr(getattr(link_inst, "Id", None), "IntegerValue", "Unknown"),
                ex,
            )
        )
        return []

    return _build_link_proxies_from_collector(
        link_inst,
        col,
        host_view,
        logger,
        host_visible_model_cat_ids=host_visible_model_cat_ids,
        obb_link=obb_link,
    )

def collect_link_3d_proxies(view, link_instance, config, logger):
    """
    v47 link 3D collection (CURRENT behavior):

    - All links (ByHostView, Custom, ByLinkedView) use the SAME host-driven spatial logic:
        * Stage-2 host clip volume -> transformed into link space
        * Broad-phase collector by enclosing AABB
        * Narrow-phase OBB vs element AABB (SAT)
        * Host view VG category visibility (model categories) is respected

    - Custom and ByLinkedView are kept as FUTURE stubs (logging only) until the API
      exposes reliable per-link custom/linked-view visibility data.
    - Linked 2D is not collected in v47.
    """
    view_id_val = getattr(getattr(view, "Id", None), "IntegerValue", "Unknown")
    link_id_val = getattr(getattr(link_instance, "Id", None), "IntegerValue", "Unknown")

    # Read LinkVisibilityType (diagnostics only)
    link_vis_type = None
    rlg_settings = None
    RvtLinkVisibility = None
    try:
        from Autodesk.Revit.DB import LinkVisibility as _RvtLinkVisibility
        RvtLinkVisibility = _RvtLinkVisibility
    except Exception:
        RvtLinkVisibility = None

    if RvtLinkVisibility is not None:
        try:
            rlg_settings = view.GetLinkOverrides(link_instance.Id)
        except Exception:
            rlg_settings = None

    if rlg_settings is not None and RvtLinkVisibility is not None:
        try:
            link_vis_type = rlg_settings.LinkVisibilityType
        except Exception:
            link_vis_type = None

    # LinkVisibilityType is logged for diagnostics only (future API work).
    try:
        if link_vis_type is None:
            logger.info(
                "Link3D: view {0}, link {1}: LinkVisibilityType=None (unavailable); treating as ByHostView (v47 placeholder)".format(
                    view_id_val, link_id_val
                )
            )
        else:
            logger.info(
                "Link3D: view {0}, link {1}: LinkVisibilityType={2} (diagnostics only); treating as ByHostView (v47 placeholder)".format(
                    view_id_val, link_id_val, link_vis_type
                )
            )
    except Exception:
        pass


    # FUTURE stubs (no behavior change today)
    if RvtLinkVisibility is not None and link_vis_type == RvtLinkVisibility.Custom:
        try:
            logger.info(
                "Link3D: link {0} is Custom in view {1} (stubbed); using host-driven link logic.".format(
                    link_id_val, view_id_val
                )
            )
        except Exception:
            pass

    if RvtLinkVisibility is not None and link_vis_type == RvtLinkVisibility.ByLinkView:
        try:
            logger.info(
                "Link3D: link {0} is ByLinkedView in view {1} (stubbed); using host-driven link logic.".format(
                    link_id_val, view_id_val
                )
            )
        except Exception:
            pass

    # Host view VG model-category visibility is the only view-visibility signal
    host_visible_model_ids = _get_host_visible_model_cat_ids(view, logger)

    return _collect_link_proxies_by_instance_bbox(
        view,
        link_instance,
        host_visible_model_cat_ids=host_visible_model_ids,
        logger=logger,
    )

def collect_3d_elements_for_view(view, config, logger):
    view_id_val = getattr(getattr(view, "Id", None), "IntegerValue", "Unknown")

    if DOC is None or FilteredElementCollector is None or CategoryType is None:
        logger.info(
            "Projection: DOC/collector unavailable; 3D empty for view Id={0}".format(
                view_id_val
            )
        )
        return []

    vtype = getattr(view, "ViewType", None)
    is_drafting = (vtype == getattr(ViewType, "DraftingView", None))
    is_legend = hasattr(ViewType, "Legend") and (vtype == getattr(ViewType, "Legend"))
    if is_drafting or is_legend:
        logger.info(
            "Projection: view Id={0} is Drafting/Legend; skipping 3D".format(
                view_id_val
            )
        )
        return []

    proj_cfg = (config or {}).get("projection") or {}
    include_link_3d = bool(proj_cfg.get("include_link_3d", True))

    elems3d = []
    link_insts = []

    # Canonical exclusion set for navigation + non-physical model cats
    excluded_ids = _get_excluded_3d_cat_ids()

    # ----------------------------------------------------------------------
    # Host 3D elements visible in view
    # No VisibleInViewFilter (it is active-view sensitive in Dynamo).
    # ----------------------------------------------------------------------
    try:
        try:
            col = (
                FilteredElementCollector(DOC, view.Id)
                .WhereElementIsNotElementType()
            )
        except Exception as ex:
            logger.warn(
                "Projection: 3D view-scoped collector failed for view Id={0}: {1} "
                "-- returning empty (no doc-wide fallback)".format(view_id_val, ex)
            )
            return []

        for e in col:
            cat = getattr(e, "Category", None)
            cat_id_obj = getattr(cat, "Id", None) if cat is not None else None
            try:
                cat_id_val = getattr(cat_id_obj, "IntegerValue", None)
            except Exception:
                cat_id_val = None
            cat_type = getattr(cat, "CategoryType", None) if cat is not None else None
            is_view_specific = bool(getattr(e, "ViewSpecific", False))

            # Skip navigation / non-occupancy cats (rooms, areas, grids, cameras, etc.)
            if cat_id_val is not None and cat_id_val in excluded_ids:
                continue

            # Linked RVT: record the instance, but don't treat its big BBox as geometry
            if RevitLinkInstance is not None and isinstance(e, RevitLinkInstance):
                if include_link_3d:
                    link_insts.append(e)
                continue

            # Imports: only model-level (non view-specific)
            if ImportInstance is not None and isinstance(e, ImportInstance):
                if not is_view_specific:
                    elems3d.append(e)
                continue

            # Regular model categories, non view-specific
            if cat_type == CategoryType.Model and not is_view_specific:
                elems3d.append(e)

    except Exception as ex:
        logger.warn(
            "Projection: error collecting 3D elements for view Id={0}: {1}".format(
                view_id_val, ex
            )
        )
        elems3d = []
        link_insts = []


    # ----------------------------------------------------------------------
    # Linked RVT expansion (ByLinkedView → ByHostView with VG filtering)
    # ----------------------------------------------------------------------
    link_proxies = []
    if include_link_3d and link_insts:
        try:
            for link_inst in link_insts:
                link_proxies.extend(
                    collect_link_3d_proxies(view, link_inst, config, logger)
                )
        except Exception as ex:
            logger.warn(
                "Projection: error collecting linked 3D elements for view Id={0}: {1}".format(
                    view_id_val, ex
                )
            )
            link_proxies = []


    all_elems = list(elems3d) + list(link_proxies)

    logger.info(
        "Projection: collected {0} 3D candidate element(s) for view Id={1}".format(
            len(all_elems), view_id_val
        )
    )
    return all_elems

def collect_2d_elements_for_view(view, config, logger):
    view_id_val = getattr(getattr(view, "Id", None), "IntegerValue", "Unknown")

    if DOC is None or FilteredElementCollector is None:
        logger.info("Projection: DOC/collector unavailable; 2D empty for view Id={0}".format(view_id_val))
        return []

    # Strict whitelist
    # Text, Dimensions, Tags (IndependentTag + RoomTag), Filled Regions, Detail Components, Detail Lines/Curves
    elems2d = []

    try:
        collector = FilteredElementCollector(DOC, view.Id).WhereElementIsNotElementType()
    except Exception as ex:
        logger.warn("Projection: 2D view-scoped collector failed for view Id={0}: {1}".format(view_id_val, ex))
        return []

    # Resolve types if available
    TextNote_cls = globals().get("TextNote", None)
    Dimension_cls = globals().get("Dimension", None)
    IndependentTag_cls = globals().get("IndependentTag", None)
    RoomTag_cls = globals().get("RoomTag", None)
    FilledRegion_cls = globals().get("FilledRegion", None)
    FamilyInstance_cls = globals().get("FamilyInstance", None)
    DetailCurve_cls = globals().get("DetailCurve", None)
    CurveElement_cls = globals().get("CurveElement", None)

    for e in collector:
        try:
            # Only view-specific 2D items should count
            if not bool(getattr(e, "ViewSpecific", False)):
                continue

            # Class-based whitelist where possible
            if TextNote_cls is not None and isinstance(e, TextNote_cls):
                elems2d.append(e); continue
            if Dimension_cls is not None and isinstance(e, Dimension_cls):
                elems2d.append(e); continue
            if IndependentTag_cls is not None and isinstance(e, IndependentTag_cls):
                elems2d.append(e); continue
            if RoomTag_cls is not None and isinstance(e, RoomTag_cls):
                elems2d.append(e); continue
            if FilledRegion_cls is not None and isinstance(e, FilledRegion_cls):
                elems2d.append(e); continue
            if DetailCurve_cls is not None and isinstance(e, DetailCurve_cls):
                elems2d.append(e); continue
            if CurveElement_cls is not None and isinstance(e, CurveElement_cls):
                # detail lines/curves are CurveElements that are view-specific
                # (model lines are not view-specific)
                elems2d.append(e); continue

            # Detail components are FamilyInstance + view-specific (covers many detail items)
            if FamilyInstance_cls is not None and isinstance(e, FamilyInstance_cls):
                elems2d.append(e); continue

            # No other 2D categories contribute (per whitelist).
            continue

        except Exception:
            continue

    logger.info(
        "Projection: collected {0} 2D candidate element(s) for view Id={1}".format(
            len(elems2d), view_id_val
        )
    )
    return elems2d

# ------------------------------------------------------------
# PROJECTION: GEOMETRY
# ------------------------------------------------------------

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

def build_regions_from_projected(projected, grid_data, config, logger):
    logger.info("Regions: building regions from projected geometry")

    tiny_regions = []
    linear_regions = []
    areal_regions = []

    diagnostics = {}

    # Debug config (no view dependency)
    debug_cfg = (config or {}).get("debug", {})
    debug_filled_loops = bool(debug_cfg.get("filled_region_loops", False))
    debug_filled_loops_max = int(debug_cfg.get("filled_region_loops_max", 10))
    debug_filled_loops_count = 0

    def _point_in_poly(px, py, pts):
        n = len(pts)
        if n < 3:
            return False

        inside = False
        x0, y0 = pts[0]
        for i in range(1, n + 1):
            x1, y1 = pts[i % n]

            dx = x1 - x0
            dy = y1 - y0
            if abs(dx) < 1e-9 and abs(dy) < 1e-9:
                x0, y0 = x1, y1
                continue

            # Edge test: treat points on edge as inside
            t = ((px - x0) * dy - (py - y0) * dx)
            if abs(t) < 1e-9:
                dot = (px - x0) * (px - x1) + (py - y0) * (py - y1)
                if dot <= 1e-9:
                    return True

            # Even–odd parity
            if ((y0 <= py < y1) or (y1 <= py < y0)):
                try:
                    x_int = x0 + (py - y0) * (x1 - x0) / (y1 - y0)
                except ZeroDivisionError:
                    x_int = x0
                if x_int >= px:
                    inside = not inside

            x0, y0 = x1, y1

        return inside

    if not isinstance(projected, dict):
        logger.warn("Regions: projected input not dict; stub regions")
        return {
            "tiny_regions": tiny_regions,
            "linear_regions": linear_regions,
            "areal_regions": areal_regions,
            "diagnostics": diagnostics,
        }

    proj3d = projected.get("projected_3d") or []
    proj2d = projected.get("projected_2d") or []
    proj_diag = projected.get("diagnostics") or {}

    diagnostics.update(proj_diag)
    diagnostics["num_projected_3d_elems"] = len(proj3d)
    diagnostics["num_projected_2d_elems"] = len(proj2d)
    diagnostics["num_projected_3d_loops"] = sum(len(ep.get("loops") or []) for ep in proj3d)
    diagnostics["num_projected_2d_loops"] = sum(len(ep.get("loops") or []) for ep in proj2d)

    try:
        cell_size = grid_data.get("cell_size_model")
        origin_xy = grid_data.get("origin_model_xy")
        n_i = int(grid_data.get("grid_n_i") or 0)
        n_j = int(grid_data.get("grid_n_j") or 0)
        valid_cells_list = grid_data.get("valid_cells") or []
    except Exception:
        cell_size = None
        origin_xy = None
        n_i = n_j = 0
        valid_cells_list = []

    if not cell_size or not origin_xy or n_i <= 0 or n_j <= 0:
        logger.warn("Regions: grid_data incomplete; stub regions")
        return {
            "tiny_regions": tiny_regions,
            "linear_regions": linear_regions,
            "areal_regions": areal_regions,
            "diagnostics": diagnostics,
        }
        
    # Debug thresholds for "large" regions in grid space
    debug_cfg = (config or {}).get("debug", {})
    large_reg_enable = bool(debug_cfg.get("log_large_3d_regions", False))
    large_reg_frac = float(debug_cfg.get("large_region_fraction", 0.8))  # 80% by default

    # 3D floor/roof loop debug
    floor_debug_enable = bool(debug_cfg.get("floor_loops", False))
    floor_debug_max = int(debug_cfg.get("floor_loops_max", 5))
    floor_debug_count = 0

    origin_x, origin_y = origin_xy
    valid_cells = set((int(i), int(j)) for (i, j) in valid_cells_list)

    reg_cfg = config.get("regions", {}) if config else {}
    tiny_max_w = int(reg_cfg.get("tiny_max_w", 2))
    tiny_max_h = int(reg_cfg.get("tiny_max_h", 2))
    linear_band_thickness = int(reg_cfg.get("linear_band_thickness_cells", 2))

    hole_min_w = float(reg_cfg.get("min_hole_size_w_cells", 1.0))
    hole_min_h = float(reg_cfg.get("min_hole_size_h_cells", 1.0))

    # floor/roof/ceiling suppression flag
    suppress_floor_like_3d = bool(reg_cfg.get("suppress_floor_roof_ceiling_3d", False))

    s = float(cell_size)
    eps = 1e-9

    def _cells_from_loops_boundary_only(loops, debug_label=None, debug_enabled=False):
        """
        Conservative boundary-only rasterization (no interior fill).
        Used for 3D model elements so plans/sections/elevations don't come back
        as solid AREAL blobs. We only mark cells whose area is intersected by
        the polygon edges.
        """
        if not loops:
            return set()

        elem_cells = set()
        loop_count = 0

        for loop in loops:
            pts = loop.get("points") or []
            if not pts or len(pts) < 2:
                continue

            loop_count += 1

            # Use existing SAT-based boundary raster
            b_cells = _get_conservative_boundary_cells(
                pts, origin_x, origin_y, s, n_i, n_j
            )
            elem_cells.update(b_cells)

        if debug_enabled and debug_label:
            try:
                diagnostics.setdefault("loop_debug", []).append(
                    {
                        "label": debug_label,
                        "mode": "boundary_only",
                        "num_loops": loop_count,
                        "num_cells_boundary": len(elem_cells),
                    }
                )
            except Exception:
                pass

        return elem_cells

    def _cells_from_loops_parity(loops, debug_label=None, debug_enabled=False):
        """
        v47 Refined: Conservative Boundary + Parity Interior.

        1. Traces edges to capture thin elements (Conservative Rasterization).
        2. Uses parity check for bulk interior.

        If debug_enabled is True, we also push a loop summary into
        diagnostics["loop_debug"] for this element.
        """
        if not loops:
            return set()

        usable = []

        # --- 1. Precompute geometry + signed area for all loops ---
        loop_infos = []  # (idx, loop, pts, min_x, max_x, min_y, max_y, area, sign)

        # Deduplicate loops coming from opposite faces etc.
        # Use bbox + point-count as a cheap key; good enough for our per-element use.
        seen_keys = set()

        for idx, loop in enumerate(loops):
            pts = loop.get("points") or []
            if len(pts) < 3:
                continue

            xs = [float(p[0]) for p in pts]
            ys = [float(p[1]) for p in pts]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            if max_x < min_x or max_y < min_y:
                continue

            # Cheap geometric key (bbox + vertex count, rounded to tame noise)
            key = (
                round(min_x, 6),
                round(max_x, 6),
                round(min_y, 6),
                round(max_y, 6),
                len(pts),
            )
            if key in seen_keys:
                # Duplicate loop: likely from the opposite face; ignore.
                continue
            seen_keys.add(key)

            # Signed area: orientation + magnitude
            area = 0.0
            n_pts = len(pts)
            for k in range(n_pts):
                x1, y1 = pts[k]
                x2, y2 = pts[(k + 1) % n_pts]
                area += (x1 * y2 - x2 * y1)
            area *= 0.5

            if abs(area) < eps:
                # Degenerate, ignore
                continue

            sign = 1 if area > 0.0 else -1
            loop_infos.append(
                (idx, loop, pts, min_x, max_x, min_y, max_y, area, sign)
            )


        if not loop_infos:
            return set()

        # --- 1a. Determine "solid" orientation from largest-area loop ---
        # The loop with largest |area| is the main outer; its sign is used
        # as the "solid" sign. Any loop with opposite sign is a hole ring.
        outer_idx = None
        outer_sign = None
        max_abs_area = 0.0
        for (idx, loop, pts, min_x, max_x, min_y, max_y, area, sign) in loop_infos:
            a = abs(area)
            if a > max_abs_area:
                max_abs_area = a
                outer_idx = idx
                outer_sign = sign

        if outer_sign is None:
            # Fallback: treat all loops as solid; no hole filtering
            for (idx, loop, pts, min_x, max_x, min_y, max_y, area, sign) in loop_infos:
                usable.append((pts, min_x, max_x, min_y, max_y))
        else:
            # --- 1b. Apply size filter only to hole rings (opposite sign) ---
            for (idx, loop, pts, min_x, max_x, min_y, max_y, area, sign) in loop_infos:
                # Convert bbox size to cell units
                w_cells = (max_x - min_x) / s if s > 0.0 else 0.0
                h_cells = (max_y - min_y) / s if s > 0.0 else 0.0

                is_hole_ring = (sign != outer_sign)

                #  only tiny *hole* rings are dropped; all solid
                # (outer + islands) are retained regardless of size.
                if is_hole_ring and w_cells <= hole_min_w and h_cells <= hole_min_h:
                    continue

                usable.append((pts, min_x, max_x, min_y, max_y))

        if not usable:
            return set()


        # 2. Phase 1: Boundary Trace (Conservative)
        final_cells = set()
        for (pts, _, _, _, _) in usable:
            b_cells = _get_conservative_boundary_cells(
                pts, origin_x, origin_y, s, n_i, n_j
            )
            final_cells.update(b_cells)

        # 3. Phase 2: Interior Fill (Parity)
        all_min_x = min(u[1] for u in usable)
        all_max_x = max(u[2] for u in usable)
        all_min_y = min(u[3] for u in usable)
        all_max_y = max(u[4] for u in usable)

        raw_i_min = int(math.floor((all_min_x - origin_x) / s - eps))
        raw_i_max = int(math.ceil((all_max_x - origin_x) / s + eps))
        raw_j_min = int(math.floor((all_min_y - origin_y) / s - eps))
        raw_j_max = int(math.ceil((all_max_y - origin_y) / s + eps))

        i_min = max(0, raw_i_min)
        i_max = min(n_i - 1, raw_i_max)
        j_min = max(0, raw_j_min)
        j_max = min(n_j - 1, raw_j_max)

        for i in range(i_min, i_max + 1):
            cx = origin_x + i * s
            for j in range(j_min, j_max + 1):
                # If edge trace already caught it, we are good
                if (i, j) in final_cells:
                    continue

                cy = origin_y + j * s

                # Quick BBox reject
                if cx < all_min_x - eps or cx > all_max_x + eps:
                    continue
                if cy < all_min_y - eps or cy > all_max_y + eps:
                    continue

                # Parity check
                inside = False
                for (pts, min_x, max_x, min_y, max_y) in usable:
                    if cx < min_x - eps or cx > max_x + eps:
                        continue
                    if cy < min_y - eps or cy > max_y + eps:
                        continue
                    if _point_in_poly(cx, cy, pts):
                        inside = not inside

                if inside:
                    final_cells.add((i, j))

        return final_cells

    def _segment_and_classify(elem_cells, has_3d, has_2d, elem_meta):
        if not elem_cells:
            return

        neighbors = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        visited = set()

        for seed in elem_cells:
            if seed in visited:
                continue

            stack = [seed]
            region_cells = set()

            while stack:
                ci, cj = stack.pop()
                if (ci, cj) in visited:
                    continue
                if (ci, cj) not in elem_cells:
                    continue

                visited.add((ci, cj))
                region_cells.add((ci, cj))

                for di, dj in neighbors:
                    nbr = (ci + di, cj + dj)
                    if nbr in elem_cells and nbr not in visited:
                        stack.append(nbr)

            if not region_cells:
                continue

            is_ = [c[0] for c in region_cells]
            js_ = [c[1] for c in region_cells]
            min_i = min(is_)
            max_i = max(is_)
            min_j = min(js_)
            max_j = max(js_)

            w = max_i - min_i + 1
            h = max_j - min_j + 1

            region_info = {
                "cells": sorted(region_cells),
                "bbox_ij": (min_i, min_j, max_i, max_j),
                "w": w,
                "h": h,
                "has_3d": has_3d,
                "has_2d": has_2d,
                "elem_id": elem_meta.get("elem_id"),
                "category": elem_meta.get("category", "<Unknown>"),
                "is_2d_element": elem_meta.get("is_2d_element", False),
                "is_filled_region": elem_meta.get("is_filled_region", False),
                "source": elem_meta.get("source", "HOST"),
            }

            
            # Debug: log very large 3D regions that span most of the grid
            if large_reg_enable and has_3d:
                try:
                    if (w >= large_reg_frac * n_i) or (h >= large_reg_frac * n_j):
                        logger.info(
                            "Regions-debug: large 3D region elem_id={0}, cat='{1}', w={2}, h={3}, grid={4}x{5}".format(
                                elem_meta.get("elem_id"),
                                elem_meta.get("category", "<Unknown>"),
                                w, h, n_i, n_j
                            )
                        )
                except Exception:
                    pass

            if w <= tiny_max_w and h <= tiny_max_h:
                tiny_regions.append(region_info)
            elif min(w, h) <= linear_band_thickness:
                linear_regions.append(region_info)
            else:
                areal_regions.append(region_info)

    # ------------------------------------------------------------
    # DEPTH-BASED OCCLUSION (v4 behavior change; 3D-only)
    # ------------------------------------------------------------
    occ_cfg = (config or {}).get("occlusion", {}) or {}
    occlusion_enable = bool(occ_cfg.get("enable", True))

    valid_cells_set = set()
    try:
        valid_cells_set = set(tuple(c) for c in (valid_cells_list or []))
    except Exception:
        valid_cells_set = set()

    occlusion_mask = set()  # set((i,j)) of occluded cells
    diagnostics.setdefault("num_3d_culled_by_occlusion", 0)
    # Per-cell nearest-depth buffer for occlusion (visible-geometry mode).
    # Keys are (i,j) cell indices; values are nearest depth (lower = closer).
    INF = 1.0e30
    w_nearest = {}  # dict((i,j) -> float)

    diagnostics.setdefault("num_3d_culled_by_occlusion", 0)
    diagnostics.setdefault("num_3d_tested_for_occlusion", 0)


    def _compute_uv_aabb_from_loops(_loops):
        mnx = mny = mxx = mxy = None
        try:
            for _lp in (_loops or []):
                _pts = _lp.get("points") or []
                for (_x, _y) in _pts:
                    if mnx is None:
                        mnx = mxx = float(_x)
                        mny = mxy = float(_y)
                    else:
                        if _x < mnx: mnx = float(_x)
                        if _x > mxx: mxx = float(_x)
                        if _y < mny: mny = float(_y)
                        if _y > mxy: mxy = float(_y)
        except Exception:
            return None
        if mnx is None or mny is None or mxx is None or mxy is None:
            return None
        return (mnx, mny, mxx, mxy)

    def _uv_aabb_to_cell_rect(uv_aabb):
        """Convert UV AABB to inclusive (i0,i1,j0,j1) rect in grid index space."""
        if uv_aabb is None:
            return None
        try:
            mnx, mny, mxx, mxy = uv_aabb
            if mnx is None or mny is None or mxx is None or mxy is None:
                return None
            ox, oy = origin_xy
            s = float(cell_size)
            if s <= 0.0:
                return None
            i0 = int(math.floor((float(mnx) - ox) / s))
            i1 = int(math.floor((float(mxx) - ox) / s))
            j0 = int(math.floor((float(mny) - oy) / s))
            j1 = int(math.floor((float(mxy) - oy) / s))
            if i0 > i1:
                i0, i1 = i1, i0
            if j0 > j1:
                j0, j1 = j1, j0
            # clamp to grid
            if i1 < 0 or j1 < 0 or i0 >= n_i or j0 >= n_j:
                return None
            i0 = 0 if i0 < 0 else i0
            j0 = 0 if j0 < 0 else j0
            i1 = (n_i - 1) if i1 >= n_i else i1
            j1 = (n_j - 1) if j1 >= n_j else j1
            return (i0, i1, j0, j1)
        except Exception:
            return None

    def _rect_fully_occluded(rect, elem_wmin):
        """True iff every valid cell in rect already has nearer-or-equal depth than elem_wmin."""
        if rect is None or elem_wmin is None:
            return False  # fail-open
        i0, i1, j0, j1 = rect
        any_valid = False
        for jj in range(j0, j1 + 1):
            for ii in range(i0, i1 + 1):
                c = (ii, jj)
                if valid_cells_set and (c not in valid_cells_set):
                    continue
                any_valid = True
                if w_nearest.get(c, INF) > float(elem_wmin):
                    return False
        # Fail-open: if we can't conclusively evaluate any valid cell, INCLUDE
        if not any_valid:
            return False
        return True

    # Front-to-back ordering (view-space AABB ordering)
    def _depth_sort_key(ep):
        d = ep.get("depth_min", None)
        if d is None:
            return (1, 0.0)
        try:
            return (0, float(d))
        except Exception:
            return (1, 0.0)

    proj3d_ordered = sorted(proj3d, key=_depth_sort_key)

    # ADD LOGGING: Show first 5 and last 5 elements in sort order
    if proj3d_ordered and logger:
        logger.info("Occlusion: Sorted {0} elements, first 5:".format(len(proj3d_ordered)))
        for i, ep in enumerate(proj3d_ordered[:5]):
            cat = ep.get("category", "?")
            d_min = ep.get("depth_min", None)
            d_max = ep.get("depth_max", None)
            logger.info("  [{0}] {1}: depth_min={2:.3f}, depth_max={3:.3f}".format(
                i, cat, d_min if d_min is not None else float('nan'), 
                d_max if d_max is not None else float('nan')
            ))
        
        if len(proj3d_ordered) > 5:
            logger.info("Occlusion: Last 5:")
            for i, ep in enumerate(proj3d_ordered[-5:]):
                cat = ep.get("category", "?")
                d_min = ep.get("depth_min", None)
                d_max = ep.get("depth_max", None)
                idx = len(proj3d_ordered) - 5 + i
                logger.info("  [{0}] {1}: depth_min={2:.3f}, depth_max={3:.3f}".format(
                    idx, cat, d_min if d_min is not None else float('nan'),
                    d_max if d_max is not None else float('nan')
                ))

    total_cells_3d = 0
    for ep in proj3d_ordered:
        elem_id = ep.get("elem_id")
        category = ep.get("category", "<No Category>")
        loops = ep.get("loops") or []
        
        # Early-out occlusion test (3D-only). Fail-open if we can't evaluate.
        # Policy: host + RVT link elements can occlude; externals (DWG/SKP/etc.) do not occlude.
        source = ep.get("source", "HOST")
        can_occlude = (source in ("HOST", "RVT_LINK"))

        if occlusion_enable and can_occlude:
            diagnostics["num_3d_tested_for_occlusion"] += 1
            uv_aabb = ep.get("uv_aabb") or _compute_uv_aabb_from_loops(loops)
            rect = _uv_aabb_to_cell_rect(uv_aabb)
            wmin = ep.get("depth_min", None)
            if _rect_fully_occluded(rect, wmin):
                diagnostics["num_3d_culled_by_occlusion"] += 1
                continue

        # optionally exclude floor-like 3D elements entirely
        if suppress_floor_like_3d and category in (
            "Floors",
            "Roofs",
            "Ceilings",
            "Structural Foundations",
        ):
            # Still counted in projection diagnostics, but contributes
            # no 3D regions / occupancy.
            continue
            
        debug_label = None
        debug_enabled = False

        # Optional: log floor-like elements’ loop sizes
        if (
            floor_debug_enable
            and floor_debug_count < floor_debug_max
            and category in ("Floors", "Structural Foundations", "Roofs")
        ):
            debug_label = "3D elem={0}, cat={1}".format(elem_id, category)
            debug_enabled = True
            floor_debug_count += 1

        elem_cells = _cells_from_loops_boundary_only(loops, debug_label, debug_enabled)

        # Determine whether this element's boundary footprint is AREAL in grid space
        is_areal_3d = False
        try:
            if elem_cells:
                _min_i = min(c[0] for c in elem_cells)
                _max_i = max(c[0] for c in elem_cells)
                _min_j = min(c[1] for c in elem_cells)
                _max_j = max(c[1] for c in elem_cells)
                _w = (_max_i - _min_i + 1)
                _h = (_max_j - _min_j + 1)
                if _w > 2 and _h > 2:
                    is_areal_3d = True
        except Exception:
            is_areal_3d = False

        # total_cells_3d updated after visibility (depth) filtering

        elem_meta = {
            "elem_id": elem_id,
            "category": category,
            "is_2d_element": False,
            "is_filled_region": False,
            "source": ep.get("source", "HOST"),
        }

        # Per-cell visibility test against depth buffer:
        # - All 3D contributors can be hidden by nearer occluders.
        # - Only HOST + RVT_LINK write to depth buffer (occlude).
        w_hit = ep.get("depth_min", None)

        # Filter to valid cells (grid domain)
        if valid_cells_set:
            elem_cells = [c for c in elem_cells if c in valid_cells_set]

        visible_cells = elem_cells
        if occlusion_enable and (w_hit is not None):
            try:
                w_hit_f = float(w_hit)
                vis = []
                for c in elem_cells:
                    if w_hit_f < w_nearest.get(c, INF):
                        vis.append(c)
                        if can_occlude:
                            w_nearest[c] = w_hit_f
                visible_cells = vis
            except Exception:
                # fail-open
                visible_cells = elem_cells

        total_cells_3d += len(visible_cells)
        _segment_and_classify(visible_cells, has_3d=True, has_2d=False, elem_meta=elem_meta)

    total_cells_2d = 0
    for ep in proj2d:
        elem_id = ep.get("elem_id")
        category = ep.get("category", "<No Category>")
        loops = ep.get("loops") or []
        is_filled_region = bool(ep.get("is_filled_region", False))

        # Debug only for filled regions, capped by config
        debug_label = None
        debug_enabled = False
        if (
            is_filled_region
            and debug_filled_loops
            and debug_filled_loops_count < debug_filled_loops_max
        ):
            debug_label = "elem={0}, cat={1}".format(elem_id, category)
            debug_enabled = True
            debug_filled_loops_count += 1

        elem_cells = _cells_from_loops_parity(loops, debug_label, debug_enabled)
        
        if valid_cells_set:
            elem_cells = [c for c in elem_cells if c in valid_cells_set]

        total_cells_2d += len(elem_cells)
        elem_meta = {
            "elem_id": elem_id,
            "category": category,
            "is_2d_element": True,
            "is_filled_region": is_filled_region,
        }
        _segment_and_classify(elem_cells, has_3d=False, has_2d=True, elem_meta=elem_meta)

    diagnostics["num_region_cells_3d"] = total_cells_3d
    diagnostics["num_region_cells_2d"] = total_cells_2d
    diagnostics["num_region_cells_total"] = total_cells_3d + total_cells_2d
    diagnostics["num_tiny_regions"] = len(tiny_regions)
    diagnostics["num_linear_regions"] = len(linear_regions)
    diagnostics["num_areal_regions"] = len(areal_regions)

    return {
        "tiny_regions": tiny_regions,
        "linear_regions": linear_regions,
        "areal_regions": areal_regions,
        "diagnostics": diagnostics,
    }

def _get_conservative_boundary_cells(pts, origin_x, origin_y, cell_size, grid_n_i, grid_n_j):
    """
    Identify every grid cell intersected by the polygon edges (segments).
    Ensures thin elements (walls/lines) are captured even if they miss cell centers.
    """
    boundary_cells = set()
    n = len(pts)
    if n < 2:
        return boundary_cells

    s = float(cell_size)
    eps = 1e-9
    
    # Helper: Separating Axis Theorem for Segment vs Cell AABB
    def _segment_hits_cell(p0, p1, ci, cj):
        # Cell bounds
        cx_min = origin_x + ci * s
        cx_max = cx_min + s
        cy_min = origin_y + cj * s
        cy_max = cy_min + s
        
        # 1. AABB Reject (Min/Max check)
        seg_min_x, seg_max_x = (p0[0], p1[0]) if p0[0] < p1[0] else (p1[0], p0[0])
        seg_min_y, seg_max_y = (p0[1], p1[1]) if p0[1] < p1[1] else (p1[1], p0[1])
        
        if seg_max_x < cx_min or seg_min_x > cx_max: return False
        if seg_max_y < cy_min or seg_min_y > cy_max: return False
        
        # 2. SAT Cross Product (Line Distance) check
        # Form line direction vector (dx, dy)
        dx = p1[0] - p0[0]
        dy = p1[1] - p0[1]
        
        # Test the 4 corners of the box against the line equation
        # A corner is "outside" if the cross product has a consistent sign relative to the line.
        # However, for rasterization, a simplified check is often enough:
        # Check if the line intersects the diagonals or if any corner is within distance.
        # Strict SAT: We project the box onto the normal of the line.
        
        # Normal to line = (-dy, dx)
        # Dot product with corners relative to p0
        # corners: (min, min), (max, min), (max, max), (min, max)
        corners = [
            (cx_min, cy_min), (cx_max, cy_min), 
            (cx_max, cy_max), (cx_min, cy_max)
        ]
        
        # Project corners onto normal
        projections = []
        for (vx, vy) in corners:
            # dot((vx-p0x, vy-p0y), (-dy, dx))
            val = (vx - p0[0]) * (-dy) + (vy - p0[1]) * (dx)
            projections.append(val)
            
        # If all projections have the same sign (and are not 0), the box is on one side of the line
        if all(p > eps for p in projections): return False
        if all(p < -eps for p in projections): return False
        
        return True

    # Iterate edges
    for k in range(n):
        p0 = pts[k]
        p1 = pts[(k + 1) % n]
        
        # Optimization: Only check cells in the bounding box of the segment
        seg_min_x = min(p0[0], p1[0])
        seg_max_x = max(p0[0], p1[0])
        seg_min_y = min(p0[1], p1[1])
        seg_max_y = max(p0[1], p1[1])
        
        # Convert to grid indices
        i_start = int(math.floor((seg_min_x - origin_x) / s - eps))
        i_end   = int(math.floor((seg_max_x - origin_x) / s + eps))
        j_start = int(math.floor((seg_min_y - origin_y) / s - eps))
        j_end   = int(math.floor((seg_max_y - origin_y) / s + eps))
        
        # Clamp to grid
        i_start = max(0, i_start)
        i_end   = min(grid_n_i - 1, i_end)
        j_start = max(0, j_start)
        j_end   = min(grid_n_j - 1, j_end)
        
        for i in range(i_start, i_end + 1):
            for j in range(j_start, j_end + 1):
                if (i, j) in boundary_cells:
                    continue
                if _segment_hits_cell(p0, p1, i, j):
                    boundary_cells.add((i, j))
                    
    return boundary_cells

# ------------------------------------------------------------
# RASTERIZATION
# ------------------------------------------------------------

def rasterize_regions_to_cells(regions, grid_data, config, logger):
    """
    Convert regions (tiny / linear / areal) into 3D + 2D raster layers.

    This version adds crop-clamping for *model-like* 2D so that:
        - Filled regions / detail components / detail lines & arcs
          are restricted to the model crop in the 2D layer.
        - Pure annotation (text / tags / dimensions) can still
          occupy cells anywhere in the annotation band.

    Inputs
    ------
    regions : dict
        { "tiny_regions": [...], "linear_regions": [...], "areal_regions": [...] }
        Each region_info is a dict with at least:
            - "cells": [(i,j), ...]
            - "has_3d": bool
            - "has_2d": bool
            - "category": str
            - "is_2d_element": bool
            - "is_filled_region": bool
    grid_data : dict
        Must include:
            - "crop_xy_min": (x_min, y_min)  # model crop extents in view XY
            - "crop_xy_max": (x_max, y_max)
            - "origin_model_xy": (origin_x, origin_y)
            - "cell_size_model": float
    """

    logger.info("Raster: rasterizing regions to cells")

    if not isinstance(regions, dict):
        logger.warn("Raster: regions input not dict; empty maps")
        return {"cells_3d": {}, "cells_2d": {}, "diagnostics": {}}

    tiny_regions = regions.get("tiny_regions") or []
    linear_regions = regions.get("linear_regions") or []
    areal_regions = regions.get("areal_regions") or []

    cells_3d = {}
    cells_2d = {}

    # --- crop clamp helpers -------------------------------------------------
    crop_min = grid_data.get("crop_xy_min")
    crop_max = grid_data.get("crop_xy_max")
    origin_xy = grid_data.get("origin_model_xy")
    cell_size = grid_data.get("cell_size_model")

    _crop_clamp_active = (
        isinstance(crop_min, (list, tuple)) and
        isinstance(crop_max, (list, tuple)) and
        isinstance(origin_xy, (list, tuple)) and
        cell_size not in (None, 0)
    )

    if _crop_clamp_active:
        crop_min_x, crop_min_y = crop_min
        crop_max_x, crop_max_y = crop_max
        origin_x, origin_y = origin_xy
        s = float(cell_size)
        eps = 1e-9

        def _cell_inside_crop(i, j):
            """
            Test cell center against model crop in view XY.
            """
            cx = origin_x + int(i) * s
            cy = origin_y + int(j) * s
            if cx < crop_min_x - eps or cx > crop_max_x + eps:
                return False
            if cy < crop_min_y - eps or cy > crop_max_y + eps:
                return False
            return True
    else:
        # No crop info; do not clamp anything.
        def _cell_inside_crop(i, j):
            return True

    # ------------------------------------------------------------------------

    def _accumulate_region_list(region_list):
        for reg in region_list:
            if not isinstance(reg, dict):
                continue

            has_3d = bool(reg.get("has_3d"))
            has_2d = bool(reg.get("has_2d"))
            cell_list = reg.get("cells") or []

            if not (has_3d or has_2d) or not cell_list:
                continue

            # Decide if this region's 2D layer should be clamped to the crop.
            cat_name = (reg.get("category") or "").lower()
            is_2d_elem = bool(reg.get("is_2d_element", False))
            is_filled_region = bool(reg.get("is_filled_region", False))

            # Treat filled regions + "detail-ish" 2D as model-like.
            is_detailish = (
                "detail" in cat_name or
                cat_name == "detail items" or
                cat_name == "lines"
            )

            clamp_2d_to_crop = (
                _crop_clamp_active and
                is_2d_elem and
                (is_filled_region or is_detailish)
            )

            for cell in cell_list:
                try:
                    i, j = cell
                    i = int(i)
                    j = int(j)
                except Exception:
                    continue

                key = (i, j)

                # 3D contribution is never crop-clamped here.
                if has_3d:
                    cells_3d[key] = cells_3d.get(key, 0) + 1

                if has_2d:
                    if (not clamp_2d_to_crop) or _cell_inside_crop(i, j):
                        cells_2d[key] = cells_2d.get(key, 0) + 1

    # Accumulate from all region tiers
    _accumulate_region_list(tiny_regions)
    _accumulate_region_list(linear_regions)
    _accumulate_region_list(areal_regions)

    num_cells_3d = len(cells_3d)
    num_cells_2d = len(cells_2d)
    num_cells_union = len(set(cells_3d.keys()) | set(cells_2d.keys()))

    diagnostics = {
        "num_cells_3d_layer": num_cells_3d,
        "num_cells_2d_layer": num_cells_2d,
        "num_cells_union": num_cells_union,
    }

    logger.info(
        "Raster: {0} unique cells ({1} in 3D layer, {2} in 2D layer)".format(
            num_cells_union, num_cells_3d, num_cells_2d
        )
    )

    return {
        "cells_3d": cells_3d,
        "cells_2d": cells_2d,
        "diagnostics": diagnostics,
    }

# ------------------------------------------------------------
# 2D annotation classification (TEXT / TAG / DIM / DETAIL / REGION / OTHER)
# ------------------------------------------------------------

def _classify_2d_annotation(elem):
    """
    Classify a 2D element into one of:
        TEXT, TAG, DIM, DETAIL, REGION, OTHER

    This drives the AnnoCells_* buckets. We lean on Category.Name first,
    then BuiltInCategory as a fallback.

    Key behavior for your case:
    - Category.Name == "Lines"       -> DETAIL
    - Category.Name == "Detail Items"-> DETAIL
    - Detail components / detail lines / OST_Lines also -> DETAIL
    """
    if elem is None:
        return "OTHER"

    cat = getattr(elem, "Category", None)
    cat_name = ""
    cat_id_int = None

    if cat is not None:
        try:
            cat_name = getattr(cat, "Name", "") or ""
        except Exception:
            cat_name = ""
        try:
            cat_id_int = cat.Id.IntegerValue
        except Exception:
            cat_id_int = None

    name_l = cat_name.lower().strip()

    # --- Category-name driven classification ---------------------------------

    # Regions
    if "region" in name_l:
        return "REGION"

    # Text
    if "text" in name_l:
        return "TEXT"

    # Dimensions
    if "dimension" in name_l:
        return "DIM"

    # Detail ITEMS (Detail Components)
    if name_l in ("detail items", "detail item"):
        return "DETAIL"

    # Drafting / Detail Lines → NEW bucket "LINES"
    # Revit exposes drafting lines simply as Category.Name == "Lines"
    if name_l == "lines":
        return "LINES"

    # Explicit detail-line-like curves (DetailLine, DetailArc, etc.)
    if "detail" in name_l and "line" in name_l:
        return "LINES"
    if "detail" in name_l and "arc" in name_l:
        return "LINES"

    # Generic Annotations → NOT TAG
    if name_l == "generic annotations":
        return "OTHER"

    # True tags only
    if "tag" in name_l:
        return "TAG"


    # --- BuiltInCategory driven classification (fallback) ---------------------

    if BuiltInCategory is not None and cat_id_int is not None:
        # Detail components / detail lines / lines → DETAIL
        try:
            detail_cats = []

            try:
                detail_cats.append(int(BuiltInCategory.OST_DetailComponents))
            except Exception:
                pass
            try:
                # Some Revit versions expose OST_DetailLines; some don't.
                if hasattr(BuiltInCategory, "OST_DetailLines"):
                    detail_cats.append(int(BuiltInCategory.OST_DetailLines))
            except Exception:
                pass
            try:
                # Drafting lines category
                if hasattr(BuiltInCategory, "OST_Lines"):
                    detail_cats.append(int(BuiltInCategory.OST_Lines))
            except Exception:
                pass

            if detail_cats and cat_id_int in detail_cats:
                return "DETAIL"
        except Exception:
            pass

        # Text
        try:
            if cat_id_int == int(BuiltInCategory.OST_TextNotes):
                return "TEXT"
        except Exception:
            pass

        # Dimensions
        try:
            if cat_id_int == int(BuiltInCategory.OST_Dimensions):
                return "DIM"
        except Exception:
            pass

        # Tags / annotations (generic + specific tag types)
        try:
            tag_cats = []
            try:
                tag_cats.append(int(BuiltInCategory.OST_GenericAnnotation))
            except Exception:
                pass
            try:
                tag_cats.append(int(BuiltInCategory.OST_Tags))
            except Exception:
                pass
            try:
                tag_cats.append(int(BuiltInCategory.OST_WallTags))
            except Exception:
                pass

            if tag_cats and cat_id_int in tag_cats:
                return "TAG"
        except Exception:
            pass

        # Regions (catch any remaining region-like built-in cats if needed)
        # Left minimal to avoid overreach.

    # Fallback
    return "OTHER"

# ------------------------------------------------------------
# VIEW RESOLUTION
# ------------------------------------------------------------

def _unwrap_to_views(candidate):
    flat_items = []

    def _flatten(x):
        if isinstance(x, (list, tuple)):
            for sub in x:
                _flatten(sub)
        else:
            flat_items.append(x)

    if candidate is not None:
        _flatten(candidate)

    views = []

    for item in flat_items:
        v = item
        try:
            if "UnwrapElement" in globals():
                v = UnwrapElement(v)
        except Exception:
            pass

        if not isinstance(v, View):
            try:
                v_int = getattr(v, "InternalElement", None)
                if isinstance(v_int, View):
                    v = v_int
            except Exception:
                pass

        if isinstance(v, View):
            views.append(v)

    return views

def _resolve_views_from_input():
    views = []

    if "IN" in globals() and len(IN) > 0 and IN[0]:
        candidate = IN[0]
        views = _unwrap_to_views(candidate)

        if views:
            LOGGER.info(
                "View resolution: using {0} view(s) from IN[0]".format(
                    len(views)
                )
            )
        else:
            LOGGER.warn(
                "View resolution: IN[0] provided but yielded no DB Views; "
                "falling back to document views"
            )

    if not views and DOC is not None and FilteredElementCollector is not None:
        try:
            views = list(
                FilteredElementCollector(DOC)
                .OfClass(View)
                .ToElements()
            )
        except Exception as ex:
            LOGGER.warn("Failed to collect views from document: {0}".format(ex))
            views = []

    all_views_count = len(views)

    non_template_views = []
    template_count = 0
    for v in views:
        is_template = False
        try:
            is_template = bool(getattr(v, "IsTemplate", False))
        except Exception:
            is_template = False
        if is_template:
            template_count += 1
        else:
            non_template_views.append(v)

    filtered_views = [v for v in non_template_views if grid._is_supported_2d_view(v)]

    LOGGER.info(
        "Filtered to {0} supported 2D non-template view(s) from {1} total View elements "
        "(excluded {2} templates)".format(
            len(filtered_views), all_views_count, template_count
        )
    )

    max_views = CONFIG["run"]["max_views"]
    if max_views is not None and len(filtered_views) > max_views:
        filtered_views = filtered_views[:max_views]

    return filtered_views

# ------------------------------------------------------------
# PIPELINE FOR A SINGLE VIEW
# ------------------------------------------------------------

def process_view(view, config, logger, grid_cache, cache_invalidate):
    t0 = datetime.datetime.now()

    view_id_val = getattr(getattr(view, "Id", None), "IntegerValue", "Unknown")
    view_name = getattr(view, "Name", "<no name>")
    logger.info("=== Processing view: Id={0}, Name='{1}' ===".format(
        view_id_val, view_name))

    proj_cfg = config.get("projection", {}) if isinstance(config, dict) else {}
    include_3d = bool(proj_cfg.get("include_3d", True))
    include_2d = bool(proj_cfg.get("include_2d", True))

    elems3d = collect_3d_elements_for_view(view, config, logger) if include_3d else []
    elems2d = collect_2d_elements_for_view(view, config, logger) if include_2d else []

    driver_elems2d = [e for e in elems2d if grid._is_extent_driver_2d(e)]
    logger.info(
        "Projection: view Id={0} has {1} driver 2D element(s) for grid extents".format(
            view_id_val, len(driver_elems2d)
        )
    )

    # Build Stage-2 clip volume ONCE per view; pass downstream.
    clip_data = build_clip_volume_for_view(view, config, logger)

    t_grid0 = datetime.datetime.now()
    grid_data = grid.build_grid_for_view(view, config, logger, driver_elems2d, clip_data=clip_data, build_clip_volume_for_view_fn=build_clip_volume_for_view)
    t_grid1 = datetime.datetime.now()


    type_counts_3d = _summarize_elements_by_type(elems3d)
    type_counts_2d = _summarize_elements_by_type(elems2d)
    cat_counts_3d = _summarize_elements_by_category(elems3d)
    cat_counts_2d = _summarize_elements_by_category(elems2d)

    logger.info("Debug: view Id={0} 3D types: {1}".format(
        view_id_val,
        ", ".join("{0}={1}".format(k, v) for k, v in sorted(type_counts_3d.items()))
    ))
    logger.info("Debug: view Id={0} 2D types: {1}".format(
        view_id_val,
        ", ".join("{0}={1}".format(k, v) for k, v in sorted(type_counts_2d.items()))
    ))
    logger.info("Debug: view Id={0} 3D cats: {1}".format(
        view_id_val,
        ", ".join("{0}={1}".format(k, v) for k, v in sorted(cat_counts_3d.items()))
    ))
    logger.info("Debug: view Id={0} 2D cats: {1}".format(
        view_id_val,
        ", ".join("{0}={1}".format(k, v) for k, v in sorted(cat_counts_2d.items()))
    ))

    t_proj0 = datetime.datetime.now()
    projected = project_elements_to_view_xy(
        view,
        grid_data,
        clip_data,
        elems3d,
        elems2d,
        config,
        logger
    )
    t_proj1 = datetime.datetime.now()

    t_regions0 = datetime.datetime.now()
    regions = build_regions_from_projected(projected, grid_data, config, logger)
    t_regions1 = datetime.datetime.now()

    t_raster0 = datetime.datetime.now()
    raster = rasterize_regions_to_cells(regions, grid_data, config, logger)
    t_raster1 = datetime.datetime.now()

    t_occ0 = datetime.datetime.now()
    occupancy = grid.compute_occupancy(grid_data, raster, config, logger)
    t_occ1 = datetime.datetime.now()

    timings = {
        "grid_clip_sec": (t_grid1 - t_grid0).total_seconds(),
        "projection_sec": (t_proj1 - t_proj0).total_seconds(),
        "regions_sec": (t_regions1 - t_regions0).total_seconds(),
        "raster_sec": (t_raster1 - t_raster0).total_seconds(),
        "occupancy_sec": (t_occ1 - t_occ0).total_seconds(),
    }

    logger.info(
        "Timings: grid+clip={0:.3f}s, proj={1:.3f}s, regions={2:.3f}s, raster={3:.3f}s, occupancy={4:.3f}s".format(
            timings["grid_clip_sec"],
            timings["projection_sec"],
            timings["regions_sec"],
            timings["raster_sec"],
            timings["occupancy_sec"],
        )
    )


    anno_cells = {
        "TEXT": set(),
        "TAG": set(),
        "DIM": set(),
        "DETAIL": set(),
        "LINES": set(),
        "REGION": set(),
        "OTHER": set(),
    }
    ext_cells_any = set()
    ext_cells_dwg = set()
    ext_cells_rvt = set()
    native_cells_any = set()

    tiny_regions = []
    linear_regions = []
    areal_regions = []
    if isinstance(regions, dict):
        tiny_regions = regions.get("tiny_regions") or []
        linear_regions = regions.get("linear_regions") or []
        areal_regions = regions.get("areal_regions") or []
    all_regions = tiny_regions + linear_regions + areal_regions

    ElementId_cls = None
    RevitLinkInstance_cls = None
    ImportInstance_cls = None
    try:
        from Autodesk.Revit.DB import ElementId, RevitLinkInstance, ImportInstance
        ElementId_cls = ElementId
        RevitLinkInstance_cls = RevitLinkInstance
        ImportInstance_cls = ImportInstance
    except Exception:
        ElementId_cls = None
        RevitLinkInstance_cls = None
        ImportInstance_cls = None

    for reg in all_regions:
        if not isinstance(reg, dict):
            continue

        cells = reg.get("cells") or []
        if not cells:
            continue

        cell_set = set()
        for c in cells:
            try:
                i, j = c
                cell_set.add((int(i), int(j)))
            except Exception:
                continue

        if not cell_set:
            continue

        elem_id_val = reg.get("elem_id", None)

        # Always resolve host element for annotation classification only.
        # (For 3D link proxies this will usually be None, which is fine.)
        e_obj = None
        if elem_id_val is not None and DOC is not None and ElementId_cls is not None:
            try:
                e_obj = DOC.GetElement(ElementId_cls(elem_id_val))
            except Exception:
                e_obj = None

        # Ext-cells classification must use region "source" (linked element ids are not host ids)
        src = reg.get("source", None)

        is_rvt_link = (src == "RVT_LINK")
        is_import = (src == "DWG_IMPORT")

        if is_rvt_link or is_import:
            ext_cells_any |= cell_set
            if is_rvt_link:
                ext_cells_rvt |= cell_set
            elif is_import:
                ext_cells_dwg |= cell_set
        else:
            native_cells_any |= cell_set

        if not reg.get("is_2d_element", False):
            continue

        if reg.get("is_filled_region", False):
            ann_bucket = "REGION"
        else:
            try:
                ann_bucket = _classify_2d_annotation(e_obj)
            except Exception:
                ann_bucket = "OTHER"

        if ann_bucket not in anno_cells:
            ann_bucket = "OTHER"

        anno_cells[ann_bucket].update(cell_set)

    ext_cells_only = ext_cells_any - native_cells_any

    if isinstance(occupancy, dict):
        occ_diag = occupancy.get("diagnostics", {}) or {}
        occ_map = occupancy.get("occupancy_map", {}) or {}
    else:
        occ_diag = {}
        occ_map = {}

    # Build optional occupancy preview rectangles for Dynamo
    try:
        occ_rects_3d, occ_rects_2d, occ_rects_2d_over_3d = _build_occupancy_preview_rects(
            view, grid_data, occupancy, config, logger
        )
    except Exception as ex:
        logger.warn(
            "Debug: failed to build occupancy preview rects for view Id={0}: {1}".format(
                view_id_val, ex
            )
        )
        occ_rects_3d, occ_rects_2d, occ_rects_2d_over_3d = [], [], []


    total_grid_cells = int(len(grid_data.get("valid_cells") or []))
    num_3d_only = int(occ_diag.get("num_cells_3d_only", 0))
    num_2d_only = int(occ_diag.get("num_cells_2d_only", 0))
    num_2d_over_3d = int(occ_diag.get("num_cells_2d_over_3d", 0))
    num_occ = num_3d_only + num_2d_only + num_2d_over_3d
    empty_cells = max(0, total_grid_cells - num_occ)

    # --- Derive cell size in feet -----------------------------------------
    try:
        cell_size_ft = float(grid_data.get("cell_size_model") or 0.0)
    except Exception:
        cell_size_ft = 0.0

    # --- Optional debug PNG of occupancy ----------------------------------
    occupancy_png_path = None
    try:
        occupancy_png_path = _build_occupancy_png(
            view,
            grid_data,
            occ_map,   # full occupancy_map from compute_occupancy(...)
            config,
            logger
        )
    except Exception as ex:
        logger.warn(
            "Occupancy: could not build PNG for view Id={0}: {1}"
            .format(view.Id, ex)
        )

    view_type_str = _get_view_type_name(view)

    row = {
        "ViewId": view_id_val,
        "ViewName": view_name,
        "ViewType": view_type_str,
        "TotalCells": total_grid_cells,
        "Empty": empty_cells,
        "ModelOnly": num_3d_only,
        "AnnoOnly": num_2d_only,
        "Overlap": num_2d_over_3d,
        "Ext_Cells_Any": len(ext_cells_any),
        "Ext_Cells_Only": len(ext_cells_only),
        "Ext_Cells_DWG": len(ext_cells_dwg),
        "Ext_Cells_RVT": len(ext_cells_rvt),
        "AnnoCells_TEXT": len(anno_cells.get("TEXT", set())),
        "AnnoCells_TAG": len(anno_cells.get("TAG", set())),
        "AnnoCells_DIM": len(anno_cells.get("DIM", set())),
        "AnnoCells_DETAIL": len(anno_cells.get("DETAIL", set())),
        "AnnoCells_LINES":  len(anno_cells.get("LINES", set())),
        "AnnoCells_REGION": len(anno_cells.get("REGION", set())),
        "AnnoCells_OTHER": len(anno_cells.get("OTHER", set())),
        "CellSize_ft": cell_size_ft,
    }

    debug_cfg = config.get("debug", {}) if isinstance(config, dict) else {}
    enable_preview_polys = bool(debug_cfg.get("enable_preview_polys", False))

    debug = {
        "view": {
            "id": view_id_val,
            "name": view_name,
            "type": view_type_str,
        },
        "grid": grid_data,
        "clip": clip_data,
        "projection": projected,
        "regions": regions,
        "raster": raster,
        "occupancy": occupancy,
        # Preview rectangles for occupancy cells by layer (optional)
        "occupancy_rects_3d_only": occ_rects_3d,
        "occupancy_rects_2d_only": occ_rects_2d,
        "occupancy_rects_2d_over_3d": occ_rects_2d_over_3d,
        # PNG debug path
        "occupancy_png_path": occupancy_png_path,
        "elem3d_type_counts": type_counts_3d,
        "elem2d_type_counts": type_counts_2d,
        "elem3d_cat_counts": cat_counts_3d,
        "elem2d_cat_counts": cat_counts_2d,
        "driver_2d_count": len(driver_elems2d),
        "num_tiny_regions": len(tiny_regions),
        "num_linear_regions": len(linear_regions),
        "num_areal_regions": len(areal_regions),
        "num_occupancy_cells": len(occ_map),
        "occupancy_png_path": occupancy_png_path,
    }

    if not enable_preview_polys:
        debug["grid"]["crop_rect_geom"] = None
        debug["grid"]["grid_rect_geom"] = None
        proj_preview = projected if isinstance(projected, dict) else {}
        proj_preview["preview_2d_rects"] = []
        proj_preview["preview_3d_rects"] = []
        debug["projection"] = proj_preview

    # Strip Revit/geometry objects from debug so Dynamo Watch can display OUT
    grid_dbg = debug.get("grid", {})
    if isinstance(grid_dbg, dict) and "crop_box_model" in grid_dbg:
        grid_dbg["crop_box_model"] = None
        
    logger.info(
        "AnnoCells: TEXT={0}, TAG={1}, DIM={2}, DETAIL={3}, LINES={4}, REGION={5}, OTHER={6}"
        .format(
            len(anno_cells["TEXT"]),
            len(anno_cells["TAG"]),
            len(anno_cells["DIM"]),
            len(anno_cells["DETAIL"]),
            len(anno_cells["LINES"]),
            len(anno_cells["REGION"]),
            len(anno_cells["OTHER"]),
        )
    )

    logger.info("=== Finished view Id={0} ===".format(view_id_val))

    t1 = datetime.datetime.now()
    elapsed = (t1 - t0).total_seconds()
    
    # Per-view summary debug
    logger.info(
        "=== View done: Id={0}, Name='{1}', TotalCells={2}, occ_cells={3} ===".format(
            view_id_val,
            view_name,
            total_grid_cells,
            occ_diag.get("num_cells_total", len(occ_map))
        )
    )

    return {
        "row": row,
        "debug": debug,
        "elapsed_sec": elapsed,
        "timings": timings,
    }

# ------------------------------------------------------------
# CSV EXPORT
# ------------------------------------------------------------

def _export_debug_json(results, config, logger):
    debug_cfg = config.get("debug", {}) or {}
    if not (debug_cfg.get("enable", True) and debug_cfg.get("write_debug_json", True)):
        logger.info("Debug JSON export disabled by config.")
        return

    selected_ids = _select_debug_view_ids(results, config, logger)

    debug_views = []
    for res in results:
        row = res.get("row") or {}
        view_id = row.get("ViewId") or row.get("ViewUniqueId")
        if view_id is None:
            continue
        if view_id not in selected_ids:
            res.pop("debug", None)
            continue

        dbg = res.get("debug") or {}
        debug_views.append(
            {
                "view_id": view_id,
                "row": row,
                "debug": dbg,
            }
        )

    payload = {
        "exporter_version": config.get("exporter_version", ""),
        "view_count": len(debug_views),
        "views": debug_views,
    }

    payload = _json_sanitize_keys(payload)

    path = config.get("paths", {}).get("debug_json", "debug.json")
    try:
        with open(path, "w") as f:
            json.dump(payload, f, indent=2, default=_json_default)
        logger.info("Wrote debug JSON: {0}".format(path))
    except Exception as ex:
        logger.error("Failed to write debug JSON: {0}".format(ex))

def _export_timings_csv(results, config, logger):
    paths = config.get("paths", {}) or {}
    csv_path = paths.get("csv_timings")
    if not csv_path:
        logger.info("No csv_timings path configured; skipping timings export.")
        return
        
    run_date_str = paths.get("run_date_str", "")
    run_id = paths.get("run_id", "")
    
    rows = []

    for res in results:
        row = res.get("row") or {}
        timings = res.get("timings") or {}

        view_id = row.get("ViewId") or row.get("ViewUniqueId")
        view_name = row.get("ViewName") or row.get("ViewTitle") or ""
        view_type = row.get("ViewType") or ""

        elapsed_total = float(res.get("elapsed_sec") or 0.0)

        rows.append(
            {
                "Date": run_date_str,
                "RunId": run_id,
                "ViewId": view_id,
                "ViewName": view_name,
                "ViewType": view_type,
                "GridClipSec": timings.get("grid_clip_sec", 0.0),
                "ProjectionSec": timings.get("projection_sec", 0.0),
                "RegionsSec": timings.get("regions_sec", 0.0),
                "RasterSec": timings.get("raster_sec", 0.0),
                "OccupancySec": timings.get("occupancy_sec", 0.0),
                "TotalElapsedSec": elapsed_total,
            }
        )

    if not rows:
        logger.info("No timing rows to export.")
        return

    # Define a stable column order
    headers = [
        "Date",
        "RunId",
        "ViewId",
        "ViewName",
        "ViewType",
        "GridClipSec",
        "ProjectionSec",
        "RegionsSec",
        "RasterSec",
        "OccupancySec",
        "TotalElapsedSec",
    ]

    matrix_rows = []
    for r in rows:
        matrix_rows.append([r.get(h, "") for h in headers])

    try:
        export_csv._append_csv_rows(csv_path, headers, matrix_rows, logger)
        logger.info("Appended {0} timing rows to {1}".format(len(rows), csv_path))
    except Exception as ex:
        logger.error("Failed to write timings CSV: {0}".format(ex))

def _select_debug_view_ids(results, config, logger):
    """
    Decide which views get full debug payloads.

    Strategy:
    - Always include any ids in debug_view_ids.
    - Then include up to max_debug_views more, chosen from:
        - elapsed_sec >= min_elapsed_for_debug_sec
        - optionally excluding cached views
      sorted by elapsed_sec descending.
    """
    debug_cfg = config.get("debug", {}) or {}
    max_debug = debug_cfg.get("max_debug_views", 20)
    min_elapsed = debug_cfg.get("min_elapsed_for_debug_sec", 0.0)
    explicit_ids = set(debug_cfg.get("debug_view_ids", []) or [])
    include_cached = bool(debug_cfg.get("include_cached_views", False))

    # First pass: collect candidates
    candidates = []
    for res in results:
        row = res.get("row") or {}
        view_id = row.get("ViewId") or row.get("ViewUniqueId") or None
        if view_id is None:
            continue

        elapsed = float(res.get("elapsed_sec") or 0.0)
        from_cache = bool(res.get("from_cache", False))

        # Always keep explicit ids
        if view_id in explicit_ids:
            candidates.append((view_id, elapsed, from_cache, True))
            continue

        # Filter by elapsed + cache flag
        if elapsed < min_elapsed:
            continue
        if (not include_cached) and from_cache:
            continue

        candidates.append((view_id, elapsed, from_cache, False))

    # Build final selection
    selected = set(explicit_ids)

    # Add non-explicit candidates sorted by elapsed descending
    non_explicit = [c for c in candidates if not c[3]]
    non_explicit.sort(key=lambda x: x[1], reverse=True)

    for view_id, elapsed, from_cache, _ in non_explicit:
        if len(selected) >= max_debug:
            break
        selected.add(view_id)

    logger.info(
        "Debug selection: {0} view(s) selected for debug JSON (max {1})".format(
            len(selected), max_debug
        )
    )
    return selected

def _export_view_level_csvs(views, results, run_start, config, logger, exporter_version=None):
    """
    CSV export:

    - views_core_YYYY-MM-DD.csv  (per-view core metadata)
    - views_vop_YYYY-MM-DD.csv   (per-view VOP / occupancy metrics)

    Header structure matches the previous CSVs with the addition of:
        - FromCache (boolean) in both core and vop files.
    """
    if not views or not results:
        return

    export_cfg = (config or {}).get("export") or {}
    out_dir = export_cfg.get("output_dir") or ""
    enable_rows = bool(export_cfg.get("enable_rows_csv", False))

    if not enable_rows:
        return
    if not export_csv._ensure_dir(out_dir, logger):
        return

    # Date string: either override from IN[5] or run_start date.
    run_date_str = None
    try:
        if "IN" in globals() and len(IN) > 5:
            override = IN[5]
            if override is not None:
                override_s = str(override).strip()
                if override_s and override_s.lower() not in ("bydate", "auto", "date"):
                    safe = []
                    for ch in override_s:
                        if ch.isalnum() or ch in ("-", "_"):
                            safe.append(ch)
                    run_date_str = "".join(safe) or None
    except Exception:
        run_date_str = None

    if not run_date_str:
        run_date_str = run_start.strftime("%Y-%m-%d")


    run_id = run_start.strftime("%Y%m%dT%H%M%S")
    config_hash = _compute_config_hash(config)

    paths_cfg = config.setdefault("paths", {})
    paths_cfg["run_date_str"] = run_date_str
    paths_cfg["run_id"] = run_id

    if not exporter_version:
        exporter_version = EXPORTER_BASE_ID

    # ------------------------------------------------------------------
    # Filenames
    # ------------------------------------------------------------------
    core_base = export_cfg.get("core_filename") or "views_core.csv"
    vop_base = export_cfg.get("vop_filename") or "views_vop.csv"

    core_prefix = os.path.splitext(core_base)[0]
    vop_prefix = os.path.splitext(vop_base)[0]

    core_filename = "{0}_{1}.csv".format(core_prefix, run_date_str)
    vop_filename = "{0}_{1}.csv".format(vop_prefix, run_date_str)

    core_path = os.path.join(out_dir, core_filename)
    vop_path = os.path.join(out_dir, vop_filename)
    
    # ------------------------------------------------------------------
    # Save per-run paths for downstream exports (timings, debug JSON)
    # ------------------------------------------------------------------
    paths_cfg = config.setdefault("paths", {})
    paths_cfg["csv_core"] = core_path
    paths_cfg["csv_vop"] = vop_path

    # Timings CSV: same date suffix as core/vop files
    timings_base = export_cfg.get("csv_timings") or "timings.csv"
    timings_prefix = os.path.splitext(timings_base)[0]
    timings_filename = "{0}_{1}.csv".format(timings_prefix, run_date_str)
    timings_path = os.path.join(out_dir, timings_filename)
    paths_cfg["csv_timings"] = timings_path

    # Debug JSON: place in the same output folder
    debug_filename = "debug_{0}.json".format(run_date_str)
    debug_path = os.path.join(out_dir, debug_filename)
    paths_cfg["debug_json"] = debug_path

    # ------------------------------------------------------------------
    # Headers (previous + FromCache)
    # ------------------------------------------------------------------
    core_headers = [
        "Date",
        "RunId",
        "ViewId",
        "ViewUniqueId",
        "ViewName",
        "ViewType",
        "SheetNumber",
        "IsOnSheet",
        "Scale",
        "Discipline",
        "Phase",
        "ViewTemplate_Name",
        "IsTemplate",
        "ExporterVersion",
        "ConfigHash",
        "ViewFrameHash",
        "FromCache",
        "ElapsedSec",
    ]

    vop_headers = [
        "Date",
        "RunId",
        "ViewId",
        "ViewName",
        "ViewType",
        "TotalCells",
        "Empty",
        "ModelOnly",
        "AnnoOnly",
        "Overlap",
        "Ext_Cells_Any",
        "Ext_Cells_Only",
        "Ext_Cells_DWG",
        "Ext_Cells_RVT",
        "AnnoCells_TEXT",
        "AnnoCells_TAG",
        "AnnoCells_DIM",
        "AnnoCells_DETAIL",
        "AnnoCells_LINES",
        "AnnoCells_REGION",
        "AnnoCells_OTHER",
        "CellSize_ft",
        "RowSource",
        "ExporterVersion",
        "ConfigHash",
        "FromCache",
        "ElapsedSec",
    ]


    # ------------------------------------------------------------------
    # Precompute sheet placement map once (ViewId -> (SheetNumber, IsOnSheet))
    # ------------------------------------------------------------------
    sheet_map = {}
    try:
        from Autodesk.Revit.DB import Viewport
        if DOC is not None and FilteredElementCollector is not None:
            vp_col = FilteredElementCollector(DOC).OfClass(Viewport)
            for vp in vp_col:
                try:
                    v_id = vp.ViewId
                    s_id = vp.SheetId
                    view_id_int = v_id.IntegerValue
                    sheet = DOC.GetElement(s_id)
                    sheet_num = getattr(sheet, "SheetNumber", "") if sheet is not None else ""
                    sheet_map[view_id_int] = (sheet_num, True)
                except Exception:
                    continue
    except Exception:
        # If anything goes wrong, we just leave sheet_map empty
        sheet_map = {}

    # ------------------------------------------------------------------
    # Build row lists
    # ------------------------------------------------------------------
    core_rows = []
    vop_rows = []

    for v, res in zip(views, results):
        if not res:
            continue
        row = res.get("row") or {}
        if not isinstance(row, dict):
            row = {}

        # FromCache flag – default to False if not present
        from_cache = bool(row.get("FromCache", False))
        
        # Per-view processing time (seconds)
        try:
            elapsed_sec = float(res.get("elapsed_sec") or 0.0)
        except Exception:
            elapsed_sec = 0.0

        # IDs and names (prefer row values; fall back to view where needed)
        try:
            view_id_val = int(row.get("ViewId", 0) or getattr(getattr(v, "Id", None), "IntegerValue", 0))
        except Exception:
            view_id_val = 0

        view_unique_id = ""
        try:
            view_unique_id = getattr(v, "UniqueId", "") or ""
        except Exception:
            pass

        view_name = row.get("ViewName") or getattr(v, "Name", "<no name>")
        view_type_str = row.get("ViewType") or _get_view_type_name(v)

        # Sheet info
        sheet_number = ""
        is_on_sheet = False
        if view_id_val in sheet_map:
            sheet_number, is_on_sheet = sheet_map[view_id_val]
        else:
            sheet_number = ""
            is_on_sheet = False

        # Scale
        try:
            scale_val = getattr(v, "Scale", None)
            scale = int(scale_val) if isinstance(scale_val, int) else ""
        except Exception:
            scale = ""

        # Discipline (string name)
        discipline = _get_view_discipline_name(v)

        # Phase
        phase_name = _get_view_phase_name(v)

        # View template name
        vt_name = ""
        try:
            vt_id = getattr(v, "ViewTemplateId", None)
            if vt_id is not None and DOC is not None:
                vt_elem = DOC.GetElement(vt_id)
                vt_name = getattr(vt_elem, "Name", "") or ""
        except Exception:
            vt_name = ""

        # IsTemplate
        try:
            is_template = bool(getattr(v, "IsTemplate", False))
        except Exception:
            is_template = False

        # ViewFrameHash – best-effort hash of a few stable properties.
        try:
            frame_payload = "{0}|{1}|{2}|{3}".format(
                view_type_str,
                scale,
                sheet_number,
                discipline,
            )
            view_frame_hash = _stable_hex_digest(frame_payload, length=8)
        except Exception:
            view_frame_hash = ""


        # --------------------
        # Core CSV row
        # --------------------
        core_row = [
            run_date_str,
            run_id,
            view_id_val,
            view_unique_id,
            view_name,
            view_type_str,
            sheet_number,
            is_on_sheet,
            scale,
            discipline,
            phase_name,
            vt_name,
            is_template,
            exporter_version,
            config_hash,
            view_frame_hash,
            from_cache,
            elapsed_sec,
        ]

        core_rows.append(core_row)

        # --------------------
        # VOP CSV row
        # --------------------
        total_cells = row.get("TotalCells", "")
        empty_cells = row.get("Empty", "")
        model_only = row.get("ModelOnly", "")
        anno_only = row.get("AnnoOnly", "")
        overlap = row.get("Overlap", "")
        ext_any = row.get("Ext_Cells_Any", "")
        ext_only = row.get("Ext_Cells_Only", "")
        ext_dwg = row.get("Ext_Cells_DWG", "")
        ext_rvt = row.get("Ext_Cells_RVT", "")
        ann_text = row.get("AnnoCells_TEXT", "")
        ann_tag = row.get("AnnoCells_TAG", "")
        ann_dim = row.get("AnnoCells_DIM", "")
        ann_detail = row.get("AnnoCells_DETAIL", "")
        ann_lines = row.get("AnnoCells_LINES", "")
        ann_region = row.get("AnnoCells_REGION", "")
        ann_other = row.get("AnnoCells_OTHER", "")
        cell_size_ft = row.get("CellSize_ft", "")
        row_source = "VOP_v47"  # Row source marker

        vop_row = [
            run_date_str,
            run_id,
            view_id_val,
            view_name,
            view_type_str,
            total_cells,
            empty_cells,
            model_only,
            anno_only,
            overlap,
            ext_any,
            ext_only,
            ext_dwg,
            ext_rvt,
            ann_text,
            ann_tag,
            ann_dim,
            ann_detail,
            ann_lines,
            ann_region,
            ann_other,
            cell_size_ft,
            row_source,
            exporter_version,
            config_hash,
            from_cache,
            elapsed_sec,
        ]

        vop_rows.append(vop_row)

    # ------------------------------------------------------------------
    # Append to CSVs
    # ------------------------------------------------------------------
    if core_rows:
        export_csv._append_csv_rows(core_path, core_headers, core_rows, logger)
    if vop_rows:
        export_csv._append_csv_rows(vop_path, vop_headers, vop_rows, logger)

def _build_views_out_for_dynamo(results):
    """
    Build a compact views-out structure for Dynamo.

    We don't re-group metrics into pretty nested blocks; we just send out
    the same 'row' dict used for CSV export (plus elapsed_sec), one per view.
    """
    views_out = []
    if not results:
        return views_out

    for res in results:
        if not isinstance(res, dict):
            continue
        row = res.get("row") or {}
        if not isinstance(row, dict):
            row = {}

        elapsed_sec = float(res.get("elapsed_sec") or 0.0)

        # Compact per-view payload: row metrics + elapsed_sec
        view_out = dict(row)  # shallow copy so we don't mutate
        view_out["elapsed_sec"] = elapsed_sec

        views_out.append(view_out)

    return views_out

# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------

def main():
    run_start = datetime.datetime.now()
    
    # === CLEANUP FROM PREVIOUS RUNS ===
    
    # 1. Clear extractor cache (REQUIRED)
    if hasattr(project_elements_to_view_xy, '_extractor_cache'):
        cleared_count = len(project_elements_to_view_xy._extractor_cache)
        project_elements_to_view_xy._extractor_cache.clear()
        if cleared_count > 0:
            LOGGER.info("Cleared {0} cached extractor(s) from previous run".format(cleared_count))
    
    # 2. Clear logger message buffer (OPTIONAL - if logger accumulates)
    if hasattr(LOGGER, 'messages') and isinstance(LOGGER.messages, list):
        old_count = len(LOGGER.messages)
        LOGGER.messages = []
        # Don't log about clearing messages (creates a circular issue!)
    
    # 3. Health check (OPTIONAL - for debugging only)
    # LOGGER.info("=== HEALTH CHECK ===")
    # LOGGER.info("Extractor cache cleared: {0} items".format(cleared_count))
    # LOGGER.info("=== END HEALTH CHECK ===")
    
    # === END CLEANUP ===
    
    LOGGER.info("Exporter start: {0}".format(run_start))
    force_recompute, cache_enabled = _get_reset_and_cache_flags()

    # Apply any Dynamo-driven overrides to CONFIG (output_dir, cache, PNG)
    _apply_runtime_inputs_to_config(CONFIG, LOGGER)

    # Exporter version remains stable across runs
    exporter_version = EXPORTER_BASE_ID

    LOGGER.info("ForceRecompute (IN[2]) = {0}".format(force_recompute))
    LOGGER.info("UseCache (IN[1]) = {0}".format(cache_enabled))

    # Cache controls
    cache_cfg = CONFIG.get("cache", {}) if isinstance(CONFIG, dict) else {}
    cache_enabled = bool(cache_cfg.get("enabled", False))

    views = _resolve_views_from_input()
    LOGGER.info("Found {0} view(s) to process".format(len(views)))
    # === SORT VIEWS BY (SCALE, GRID_SIZE) ===
    def get_cache_key(v):
        try:
            scale = int(getattr(v, "Scale", 96))
            # Estimate grid size (will be computed later, but approximate is fine)
            return (scale, scale * 0.0104167)  # ~1/8" in feet per scale unit
        except Exception:
            return (999, 999)  # Unknowns at end
    
    views_sorted = sorted(views, key=get_cache_key)
    
    LOGGER.info("Sorted {0} views by scale/grid for better cache performance".format(len(views_sorted)))
    # === END SORTING ===
    

        
    

    # Determine project + cache file path (if any)
    cache_path = None
    project_guid = None
    config_hash = _compute_config_hash(CONFIG)
    view_cache = {
        "exporter_version": exporter_version,
        "config_hash": config_hash,
        "project_guid": None,
        "views": {},
    }

    if cache_enabled and views:
        first_view = views[0]
        doc = getattr(first_view, "Document", DOC)
        project_guid = _get_project_guid(doc)
        cache_path = _get_cache_file_path(CONFIG, doc)
        LOGGER.info("Cache: path = '{0}'".format(cache_path))

        if cache_enabled and not force_recompute:
            view_cache = _load_view_cache(
                cache_path,
                exporter_version,
                config_hash,
                project_guid,
                LOGGER,
            )
        else:
            LOGGER.info("Cache: ForceRecompute=True or cache disabled; ignoring existing cache.")
            view_cache = {
                "exporter_version": exporter_version,
                "config_hash": config_hash,
                "project_guid": project_guid,
                "views": {},
            }

    grid_cache = {}  # reserved for future in-memory cache (per run)
    results = []

    cached_views = view_cache.get("views") if (cache_enabled and isinstance(view_cache, dict)) else {}
    if cached_views is None:
        cached_views = {}






    for view in views_sorted:  # Use sorted list
        if view is None:
            continue

        v_id = getattr(getattr(view, "Id", None), "IntegerValue", None)
        v_key = str(v_id)

        elem_ids = _collect_element_ids_for_signature(view, LOGGER)
        current_sig = _compute_view_signature(view, elem_ids)

        res = None
        from_cache = False

        if cache_enabled and not force_recompute:
            cached = cached_views.get(v_key)
            if isinstance(cached, dict) and "row" in cached:
                cached_sig = cached.get("view_signature")
                if cached_sig == current_sig:
                    row = cached.get("row") or {}
                    row["FromCache"] = True
                    res = {
                        "row": row,
                        "debug": {},
                        "elapsed_sec": 0.0,  # cached = free this run
                        "from_cache": True,
                    }
                    from_cache = True
                    LOGGER.info(
                        "Cache: using cached metrics for view Id={0} (signature match)".format(v_id)
                    )

        if res is None:
            # Compute fresh
            res = process_view(view, CONFIG, LOGGER, grid_cache, False)
            if res is None:
                continue

            row = res.get("row") or {}
            if not isinstance(row, dict):
                row = {}
            row["FromCache"] = False
            
            # === PERIODIC GC (CPython3) ===
            view_index = len(results)
            if view_index > 0 and view_index % 10 == 0:
                try:
                    import gc
                    collected = gc.collect()
                    LOGGER.info("GC after view {0}: collected {1} objects".format(
                        view_index, collected
                    ))
                except Exception as ex:
                    LOGGER.info("GC failed: {0}".format(ex))
            # === END PERIODIC GC ===
            
            res["row"] = row
            res["from_cache"] = False

            if cache_enabled:
                elapsed_sec = float(res.get("elapsed_sec") or 0.0)
                res["elapsed_sec"] = elapsed_sec
                cached_views[v_key] = {
                    "view_signature": current_sig,
                    "row": row,
                    "elapsed_sec": elapsed_sec,
                }

        # Safety: guarantee FromCache
        row = res.get("row") or {}
        if "FromCache" not in row:
            row["FromCache"] = from_cache
            res["row"] = row
        res["from_cache"] = bool(row.get("FromCache"))

        results.append(res)

    # CSV export (view-level metrics)
    try:
        _export_view_level_csvs(views, results, run_start, CONFIG, LOGGER, exporter_version)
    except Exception as ex:
        LOGGER.warn("Export: exception during CSV export: {0}".format(ex))

    # Timings CSV export (new)
    try:
        _export_timings_csv(results, CONFIG, LOGGER)
    except Exception as ex:
        LOGGER.warn("Export: exception during timings CSV export: {0}".format(ex))

    # Debug JSON export (new)
    try:
        _export_debug_json(results, CONFIG, LOGGER)
    except Exception as ex:
        LOGGER.warn("Export: exception during debug JSON export: {0}".format(ex))


    run_end = datetime.datetime.now()
    total_elapsed = (run_end - run_start).total_seconds()

    LOGGER.info("Exporter finished: {0}".format(run_end))
    LOGGER.info("Total elapsed seconds: {0}".format(total_elapsed))
    LOGGER.info("Processed {0} view(s)".format(len(results)))

    # Persist cache (if enabled)
    if cache_enabled and cache_path and not force_recompute:
        view_cache["exporter_version"] = exporter_version
        view_cache["config_hash"] = config_hash
        view_cache["project_guid"] = project_guid
        view_cache["views"] = cached_views
        _save_view_cache(cache_path, view_cache, LOGGER)


    # Build compact views_out for Dynamo
    views_out = _build_views_out_for_dynamo(results)

    include_run_log = bool(CONFIG.get("debug", {}).get("include_run_log_in_out", False))
    run_log = LOGGER.lines if include_run_log else None

    out_dict = {
        "signature": exporter_version,
        "reset_flag": force_recompute,
        "run_start": run_start.strftime("%Y-%m-%d %H:%M:%S"),
        "run_end": run_end.strftime("%Y-%m-%d %H:%M:%S"),
        "total_elapsed_sec": float(total_elapsed),
        "view_count": len(results),
        "views": views_out,
    }

    if include_run_log and run_log is not None:
        out_dict["log"] = run_log
        
    # === FINAL CLEANUP (CPython3) ===
    try:
        import gc
        
        if hasattr(project_elements_to_view_xy, '_extractor_cache'):
            cache_size = len(project_elements_to_view_xy._extractor_cache)
            project_elements_to_view_xy._extractor_cache.clear()
            LOGGER.info("Final cleanup: cleared {0} extractors".format(cache_size))
        
        collected = gc.collect()
        LOGGER.info("Final GC: collected {0} objects".format(collected))
        
    except Exception as ex:
        LOGGER.info("Final cleanup warning: {0}".format(ex))
    # === END FINAL CLEANUP ===

    return [out_dict]

# ------------------------------------------------------------
# DYNAMO OUT (A3 diagnostic)
# ------------------------------------------------------------

def _safe_main():
    try:
        return main()
    except Exception as ex:
        # If we get here, main() threw – surface it as a dict, never leave OUT empty
        return {
            "error": "Exception in main()",
            "message": str(ex),
            "trace_hint": "Check Revit API imports, view collection, or process_view()"
        }

# Only auto-run when executed as the primary Dynamo Python node script.
# When imported from a thin loader, do not execute on import.
if "IN" in globals():
    OUT = _safe_main()
