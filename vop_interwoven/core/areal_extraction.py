# -*- coding: utf-8 -*-
"""
Unified AREAL element geometry extraction with confidence-based fallback hierarchy.

This module implements a unified extraction strategy for AREAL elements (floors, ceilings, roofs)
with a clear fallback hierarchy:
  - Tier 1 (HIGH confidence): Planar face loops or silhouette edges
  - Tier 2 (MEDIUM/LOW confidence): Geometry extraction with OBB/AABB fallback
  - Tier 3 (LOW confidence): Pure AABB from bounding box
  - Failure: Returns None

The extraction function tracks which strategy succeeded and assigns appropriate
confidence levels for downstream quality assessment.
"""

def _safe_elem_id(elem):
    """Safely extract element ID as integer.

    Args:
        elem: Revit Element

    Returns:
        Integer element ID, or None if unavailable
    """
    try:
        elem_id = getattr(elem, 'Id', None)
        if elem_id is None:
            return None
        return getattr(elem_id, 'IntegerValue', None)
    except Exception:
        return None


def _safe_category(elem):
    """Safely extract category name from element.

    Args:
        elem: Revit Element

    Returns:
        Category name string, or 'Unknown' if unavailable
    """
    try:
        cat = getattr(elem, 'Category', None)
        if cat is None:
            return 'Unknown'
        cname = getattr(cat, 'Name', None)
        return cname if cname else 'Unknown'
    except Exception:
        return 'Unknown'


def _get_aabb_loops_from_bbox(bbox, view_basis):
    """Create axis-aligned bounding box loops from bbox.

    Args:
        bbox: Revit BoundingBoxXYZ
        view_basis: ViewBasis for coordinate transformation

    Returns:
        List of loop dicts [{'points': [...], 'is_hole': False}], or None if failed
    """
    # Simple UV transformation using view_basis directly (no import needed)
    def _transform_to_uvw(point, vb):
        """Transform 3D point to view UVW coordinates."""
        if hasattr(point, 'X'):
            px, py, pz = point.X, point.Y, point.Z
        else:
            px, py, pz = point

        # Translate to view origin
        dx = px - vb.origin[0]
        dy = py - vb.origin[1]
        dz = pz - vb.origin[2]

        # Project onto view axes
        u = dx * vb.right[0] + dy * vb.right[1] + dz * vb.right[2]
        v = dx * vb.up[0] + dy * vb.up[1] + dz * vb.up[2]
        w = dx * vb.forward[0] + dy * vb.forward[1] + dz * vb.forward[2]

        return (u, v, w)

    try:
        # Get bbox corners in model coordinates
        try:
            corners = [
                bbox.Min,
                (bbox.Max.X, bbox.Min.Y, bbox.Min.Z),
                (bbox.Max.X, bbox.Max.Y, bbox.Min.Z),
                (bbox.Min.X, bbox.Max.Y, bbox.Min.Z),
            ]
        except Exception:
            # Fallback: use Min/Max as tuples
            corners = [
                (bbox.Min.X, bbox.Min.Y, bbox.Min.Z),
                (bbox.Max.X, bbox.Min.Y, bbox.Min.Z),
                (bbox.Max.X, bbox.Max.Y, bbox.Min.Z),
                (bbox.Min.X, bbox.Max.Y, bbox.Min.Z),
            ]

        # Transform to view UVW coordinates
        uvws = []
        for corner in corners:
            try:
                uvw = _transform_to_uvw(corner, view_basis)
                uvws.append(uvw)
            except Exception:
                return None

        if len(uvws) < 4:
            return None

        # Get min depth for all corners
        w_min = min(uvw[2] for uvw in uvws)

        # Create AABB in UV space
        u_coords = [uvw[0] for uvw in uvws]
        v_coords = [uvw[1] for uvw in uvws]

        u_min = min(u_coords)
        u_max = max(u_coords)
        v_min = min(v_coords)
        v_max = max(v_coords)

        # Create rectangle loop (closed)
        points_uvw = [
            (u_min, v_min, w_min),
            (u_max, v_min, w_min),
            (u_max, v_max, w_min),
            (u_min, v_max, w_min),
            (u_min, v_min, w_min),  # Close loop
        ]

        return [{'points': points_uvw, 'is_hole': False}]

    except Exception:
        return None


def extract_areal_geometry(elem, view, view_basis, raster, cfg, diag=None, strategy_diag=None):
    """Extract AREAL element geometry with confidence-based fallback hierarchy.

    Implements a 3-tier fallback strategy:
      1. HIGH confidence: Planar face loops or silhouette edges (actual visible geometry)
      2. MEDIUM/LOW confidence: Geometry extraction with OBB/AABB fallback
      3. LOW confidence: Pure AABB from bounding box

    Args:
        elem: Revit Element
        view: Revit View
        view_basis: ViewBasis for coordinate transformation
        raster: ViewRaster with bounds and cell size
        cfg: Config object
        diag: Diagnostics instance for error tracking (optional)
        strategy_diag: StrategyDiagnostics instance for strategy tracking (optional)

    Returns:
        Tuple of (loops, confidence, strategy_name):
          - loops: List of loop dicts [{'points': [...], 'is_hole': bool}]
          - confidence: 'HIGH', 'MEDIUM', 'LOW', or None
          - strategy_name: String describing which strategy succeeded, or 'failed'

        Returns (None, None, 'failed') if all strategies fail.

    Commentary:
        This function unifies all AREAL extraction strategies into a single
        entry point with explicit confidence levels. Downstream code can use
        confidence to filter or warn about low-quality extractions.
    """
    elem_id = _safe_elem_id(elem)
    category = _safe_category(elem)

    # ========================================================================
    # TIER 1: HIGH CONFIDENCE - Planar face loops or silhouette edges
    # ========================================================================

    # Try planar face loops first (best for floors/ceilings with openings)
    # Phase 3.1: Record method attempt
    if strategy_diag is not None and elem_id is not None:
        try:
            strategy_diag.record_method_attempt(elem_id, 'planar_face')
        except Exception:
            pass

    try:
        from .silhouette import _front_face_loops_silhouette

        loops = _front_face_loops_silhouette(elem, view, view_basis, cfg=cfg)

        if loops and len(loops) > 0:
            # Success! Track with strategy_diag if available
            if strategy_diag is not None and elem_id is not None:
                try:
                    strategy_diag.record_areal_strategy(
                        elem_id=elem_id,
                        strategy='planar_face',
                        success=True,
                        category=category,
                        confidence='HIGH'
                    )
                    strategy_diag.record_geometry_extraction(
                        elem_id=elem_id,
                        outcome='success',
                        category=category,
                        details={'strategy': 'planar_face_loops', 'loop_count': len(loops)}
                    )
                    # Phase 3.1: Record successful extraction method
                    strategy_diag.record_extraction_method(
                        elem_id=elem_id,
                        category=category,
                        method='planar_face',
                        success=True,
                        confidence='HIGH'
                    )
                except Exception:
                    pass

            return (loops, 'HIGH', 'planar_face_loops')
    except Exception:
        pass

    # Try silhouette edges (preserves concave shapes like L, U, C)
    # Phase 3.1: Record method attempt
    if strategy_diag is not None and elem_id is not None:
        try:
            strategy_diag.record_method_attempt(elem_id, 'silhouette')
        except Exception:
            pass

    try:
        from .silhouette import _silhouette_edges

        loops = _silhouette_edges(elem, view, view_basis, cfg)

        if loops and len(loops) > 0:
            # Success! Track with strategy_diag if available
            if strategy_diag is not None and elem_id is not None:
                try:
                    strategy_diag.record_areal_strategy(
                        elem_id=elem_id,
                        strategy='silhouette',
                        success=True,
                        category=category,
                        confidence='HIGH'
                    )
                    strategy_diag.record_geometry_extraction(
                        elem_id=elem_id,
                        outcome='success',
                        category=category,
                        details={'strategy': 'silhouette_edges', 'loop_count': len(loops)}
                    )
                    # Phase 3.1: Record successful extraction method
                    strategy_diag.record_extraction_method(
                        elem_id=elem_id,
                        category=category,
                        method='silhouette',
                        success=True,
                        confidence='HIGH'
                    )
                except Exception:
                    pass

            return (loops, 'HIGH', 'silhouette_edges')
    except Exception:
        pass

    # Track Tier 1 failure
    if strategy_diag is not None and elem_id is not None:
        try:
            strategy_diag.record_areal_strategy(
                elem_id=elem_id,
                strategy='planar_face',
                success=False,
                category=category
            )
            strategy_diag.record_areal_strategy(
                elem_id=elem_id,
                strategy='silhouette',
                success=False,
                category=category
            )
        except Exception:
            pass

    # ========================================================================
    # TIER 2: MEDIUM/LOW CONFIDENCE - Geometry extraction with OBB/AABB
    # ========================================================================

    # Phase 3.1: Record method attempt for geometry_polygon
    if strategy_diag is not None and elem_id is not None:
        try:
            strategy_diag.record_method_attempt(elem_id, 'geometry_polygon')
        except Exception:
            pass

    try:
        from ..revit.collection import get_element_obb_loops

        loops = get_element_obb_loops(
            elem, view_basis, raster,
            bbox=None,  # Let function resolve bbox
            diag=diag,
            view=view,
            strategy_diag=strategy_diag
        )

        if loops and len(loops) > 0:
            # Check strategy used by get_element_obb_loops
            strategy_name = loops[0].get('strategy', 'unknown')

            # Map internal strategy names to Phase 3.1 method names
            if strategy_name == 'geometry_polygon':
                # Actual geometry was extracted - MEDIUM confidence
                confidence = 'MEDIUM'
                method_name = 'geometry_polygon'
            elif strategy_name in ['uv_obb', 'bbox_obb_used']:
                # Bbox corners with OBB rotation - LOW confidence
                confidence = 'LOW'
                method_name = 'bbox_obb'
            elif strategy_name in ['uv_aabb', 'aabb_used']:
                # Bbox corners with AABB - LOW confidence
                confidence = 'LOW'
                method_name = 'aabb'
            else:
                # Unknown strategy - assume LOW confidence
                confidence = 'LOW'
                method_name = strategy_name

            # Phase 3.1: Record successful extraction method
            if strategy_diag is not None and elem_id is not None:
                try:
                    strategy_diag.record_extraction_method(
                        elem_id=elem_id,
                        category=category,
                        method=method_name,
                        success=True,
                        confidence=confidence
                    )
                    strategy_diag.record_confidence(elem_id, confidence, category)
                except Exception:
                    pass

            return (loops, confidence, strategy_name)
    except Exception:
        pass

    # ========================================================================
    # TIER 3: LOW CONFIDENCE - Pure AABB from bounding box
    # ========================================================================

    # Phase 3.1: Record method attempt for aabb
    if strategy_diag is not None and elem_id is not None:
        try:
            strategy_diag.record_method_attempt(elem_id, 'aabb')
        except Exception:
            pass

    try:
        from ..revit.collection import resolve_element_bbox

        bbox, bbox_src = resolve_element_bbox(
            elem,
            view=view,
            diag=diag,
            context={'caller': 'areal_extraction_tier3'}
        )

        if bbox is not None:
            loops = _get_aabb_loops_from_bbox(bbox, view_basis)

            if loops and len(loops) > 0:
                # Track AABB strategy
                if strategy_diag is not None and elem_id is not None:
                    try:
                        strategy_diag.record_areal_strategy(
                            elem_id=elem_id,
                            strategy='aabb_used',
                            success=True,
                            category=category,
                            confidence='LOW'
                        )
                        strategy_diag.record_geometry_extraction(
                            elem_id=elem_id,
                            outcome='success',
                            category=category,
                            details={'strategy': 'aabb_fallback', 'bbox_source': bbox_src}
                        )
                        # Phase 3.1: Record successful extraction method
                        strategy_diag.record_extraction_method(
                            elem_id=elem_id,
                            category=category,
                            method='aabb',
                            success=True,
                            confidence='LOW'
                        )
                        strategy_diag.record_confidence(elem_id, 'LOW', category)
                    except Exception:
                        pass

                return (loops, 'LOW', 'aabb_fallback')
    except Exception:
        pass

    # ========================================================================
    # TOTAL FAILURE - No strategy succeeded
    # ========================================================================

    if strategy_diag is not None and elem_id is not None:
        try:
            strategy_diag.record_geometry_extraction(
                elem_id=elem_id,
                outcome='failed_all_strategies',
                category=category,
                details={'reason': 'All AREAL extraction strategies failed'}
            )
            # Phase 3.1: Record failed extraction (no method succeeded)
            # Note: We don't call record_extraction_method here because no method succeeded
            # The method_attempted_order will show all methods tried via record_method_attempt
        except Exception:
            pass

    return (None, None, 'failed')
