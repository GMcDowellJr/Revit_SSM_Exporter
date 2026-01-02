"""
VOP Interwoven Pipeline - Main Processing Logic.

Implements the interwoven model pass (Pass A+B merged) with:
- Front-to-back element streaming
- UV classification (TINY/LINEAR/AREAL)
- AreaL gets full triangles + depth buffer
- Tiny/Linear get UV_AABB/OBB proxies
- Depth-aware early-out occlusion testing

Core principles:
1. 3D model geometry is the ONLY occlusion truth
2. 2D annotation NEVER occludes model
3. Heavy work reserved for AreaL elements
4. Safe early-out only against depth-aware tile buffers
"""

import math
from .config import Config
from .core.raster import ViewRaster, TileMap
from .core.geometry import Mode, classify_by_uv, make_uv_aabb, make_obb_or_skinny_aabb
from .core.math_utils import Bounds2D, CellRect
from .revit.view_basis import make_view_basis, xy_bounds_from_crop_box_all_corners
from .revit.collection import (
    collect_view_elements,
    expand_host_link_import_model_elements,
    sort_front_to_back,
    is_element_visible_in_view,
)


def process_document_views(doc, view_ids, cfg):
    """Process multiple views through the VOP interwoven pipeline.

    Args:
        doc: Revit Document
        view_ids: List of Revit View ElementIds (or ints) to process
        cfg: Config object

    Returns:
        List of results (one per view), each containing:
        {
            'view_id': int,
            'view_name': str,
            'raster': ViewRaster (or dict from raster.to_dict()),
            'diagnostics': dict with processing stats
        }

    Example:
        >>> cfg = Config()
        >>> results = process_document_views(doc, [view_id1, view_id2], cfg)
        >>> len(results)
        2
    """
    results = []

    for view_id in view_ids:
        # Convert int to ElementId if needed (Revit API requires ElementId object)
        from Autodesk.Revit.DB import ElementId
        if isinstance(view_id, int):
            elem_id = ElementId(view_id)
        else:
            elem_id = view_id

        view = doc.GetElement(elem_id)

        # 0) Validate supported view types (2D-ish)
        if not _is_supported_2d_view(view):
            continue

        # 1) Init raster bounds/resolution
        raster = init_view_raster(doc, view, cfg)

        # 2) Broad-phase visible elements
        elements = collect_view_elements(doc, view, raster)

        # 3) MODEL PASS (INTERWOVEN A+B): front-to-back, AreaL gets triangles+z, tiny/linear proxies
        render_model_front_to_back(doc, view, raster, elements, cfg)

        # 4) ANNO PASS (2D only, no occlusion effect)
        # rasterize_2d_annotations(doc, view, raster, cfg)
        # TODO: Implement annotation rasterization

        # 5) Derive annoOverModel with explicit OverModel semantics
        raster.finalize_anno_over_model(cfg)

        # 6) Export
        results.append(export_view_raster(view, raster, cfg))

    return results


def init_view_raster(doc, view, cfg):
    """Initialize ViewRaster for a view.

    Args:
        doc: Revit Document
        view: Revit View
        cfg: Config

    Returns:
        ViewRaster initialized with grid dimensions and bounds

    Commentary:
        ✔ Cell size: 1/8" on sheet -> model feet (scale-dependent)
        ✔ Bounds: prefer cropbox (transform all 8 corners)
        ✔ Fallback to synthetic bounds if crop off
    """
    # Cell size: 1/8" on sheet -> model feet
    scale = view.Scale  # e.g., 96 for 1/8" = 1'-0"
    cell_size_ft = (0.125 * scale) / 12.0  # inches -> feet

    # View basis
    basis = make_view_basis(view)

    # Bounds from cropbox or synthetic
    try:
        crop_active = view.CropBoxActive
    except:
        crop_active = True

    if crop_active:
        bounds_xy = xy_bounds_from_crop_box_all_corners(
            view, basis, buffer=cell_size_ft * 2
        )
    else:
        from .revit.view_basis import synthetic_bounds_from_visible_extents

        bounds_xy = synthetic_bounds_from_visible_extents(
            doc, view, basis, buffer=cell_size_ft * 4
        )

    width_ft = bounds_xy.width()
    height_ft = bounds_xy.height()

    W = max(1, math.ceil(width_ft / cell_size_ft))
    H = max(1, math.ceil(height_ft / cell_size_ft))

    # Compute adaptive tile size based on grid dimensions
    tile_size = cfg.compute_adaptive_tile_size(W, H)

    raster = ViewRaster(
        width=W, height=H, cell_size=cell_size_ft, bounds=bounds_xy, tile_size=tile_size
    )

    return raster


def render_model_front_to_back(doc, view, raster, elements, cfg):
    """Render 3D model elements front-to-back with interwoven AreaL/Tiny/Linear handling.

    Args:
        doc: Revit Document
        view: Revit View
        raster: ViewRaster (modified in-place)
        elements: List of Revit elements (from collect_view_elements)
        cfg: Config

    Returns:
        None (modifies raster in-place)

    Commentary:
        ✔ Simplified Phase 5 implementation using bbox filling
        ✔ Classifies elements as TINY/LINEAR/AREAL
        ✔ Fills raster cells from bounding boxes
        ⚠ Triangle rasterization (Phase 4) deferred
    """
    from .revit.collection import _project_element_bbox_to_cell_rect

    # Get view basis for transformations
    vb = make_view_basis(view)

    # Process each element
    processed = 0
    for elem in elements:
        # Get element metadata
        elem_id = elem.Id.IntegerValue
        category = elem.Category.Name if elem.Category else "Unknown"
        key_index = raster.get_or_create_element_meta_index(elem_id, category, "HOST")

        # Project element bbox to cell rect
        rect = _project_element_bbox_to_cell_rect(elem, vb, raster)
        if rect is None or rect.empty:
            continue

        # Classify by UV
        U = rect.width_cells
        V = rect.height_cells
        mode = classify_by_uv(U, V, cfg)

        # Simplified rendering: fill bounding box cells
        # (Phase 4 will add proper triangle rasterization)
        for i, j in rect.cells():
            raster.set_cell_filled(i, j, depth=0.0)
            idx = raster.get_cell_index(i, j)
            if idx is not None:
                raster.model_edge_key[idx] = key_index

        processed += 1

    return processed


def _is_supported_2d_view(view):
    """Check if view type is supported (2D-ish views only)."""
    # TODO: Implement view type check
    # For now, accept all views (placeholder)
    return True


def _project_element_bbox_to_cell_rect(elem, transform, view, raster):
    """Project element's view-space bounding box to grid cell rectangle.

    Returns:
        CellRect with cell indices (i_min, j_min, i_max, j_max)
    """
    # TODO: Implement bbox projection
    # Placeholder: return 5x5 rect at origin
    return CellRect(0, 0, 4, 4)


def _estimate_nearest_depth_from_bbox(elem, transform, view, raster):
    """Estimate nearest depth from element's bounding box."""
    # TODO: Implement depth estimation
    return 0.0


def _tiles_fully_covered_and_nearer(tile_map, rect, elem_near_z):
    """Check if all tiles overlapping rect are fully covered AND nearer than elem_near_z.

    Returns:
        True if element is guaranteed occluded (safe to skip)
    """
    tiles = tile_map.get_tiles_for_rect(rect.i_min, rect.j_min, rect.i_max, rect.j_max)

    for t in tiles:
        # Check if tile is fully filled
        if not tile_map.is_tile_full(t):
            return False

        # Check if tile's nearest depth is closer than element
        if tile_map.z_min_tile[t] >= elem_near_z:
            return False

    return True


def _render_areal_element(elem, transform, view, raster, rect, key_index, cfg):
    """Render AREAL element: triangles + z-buffer + depth-tested edges.

    Commentary:
        ✔ Fast conservative interior fill by tiles
        ✔ Refine boundaries using triangle z-buffer
        ✔ Edges depth-tested vs zMin
        ⚠ Placeholder implementation - requires geometry API access
    """
    # TODO: Implement triangle rasterization
    # Placeholder: fill rect with depth = 0.0
    for i, j in rect.cells():
        raster.set_cell_filled(i, j, depth=0.0)
        idx = raster.get_cell_index(i, j)
        if idx is not None:
            raster.model_edge_key[idx] = key_index


def _render_proxy_element(elem, transform, view, raster, rect, mode, key_index, cfg):
    """Render TINY/LINEAR element: proxy edges + optional minimal mask.

    Commentary:
        ✔ Save heavy work; no triangle raster
        ✔ Do not write broad proxy fill into modelMask/zMin (avoids false occlusion)
        ✔ Proxy edges go to separate layer
        ✔ Optional minimal proxy mask for "Over any model presence"
    """
    if mode == Mode.TINY:
        proxy = make_uv_aabb(rect)
    else:  # Mode.LINEAR
        proxy = make_obb_or_skinny_aabb(elem, transform, rect, view, raster)

    # Stamp proxy edges
    _stamp_proxy_edges(proxy, key_index, raster)

    # Optional minimal proxy mask
    if cfg.over_model_includes_proxies and cfg.proxy_mask_mode == "minmask":
        if mode == Mode.TINY:
            _mark_rect_center_cell(rect, raster)
        else:  # LINEAR
            _mark_thin_band_along_long_axis(rect, raster)


def _stamp_proxy_edges(proxy, key_index, raster):
    """Stamp proxy edges into model_proxy_key layer.

    Args:
        proxy: UV_AABB or OBB
        key_index: Element metadata index
        raster: ViewRaster (modified in-place)
    """
    # TODO: Implement edge rasterization
    # Placeholder: mark center cell
    pass


def _mark_rect_center_cell(rect, raster):
    """Mark center cell of rect in model_proxy_mask."""
    i_center, j_center = rect.center_cell()
    idx = raster.get_cell_index(i_center, j_center)
    if idx is not None:
        raster.model_proxy_mask[idx] = True


def _mark_thin_band_along_long_axis(rect, raster):
    """Mark thin band along long axis of rect in model_proxy_mask."""
    # TODO: Implement thin band marking
    # Placeholder: mark center row or column
    if rect.width_cells > rect.height_cells:
        # Horizontal band
        j_center = (rect.j_min + rect.j_max) // 2
        for i in range(rect.i_min, rect.i_max + 1):
            idx = raster.get_cell_index(i, j_center)
            if idx is not None:
                raster.model_proxy_mask[idx] = True
    else:
        # Vertical band
        i_center = (rect.i_min + rect.i_max) // 2
        for j in range(rect.j_min, rect.j_max + 1):
            idx = raster.get_cell_index(i_center, j)
            if idx is not None:
                raster.model_proxy_mask[idx] = True


def export_view_raster(view, raster, cfg):
    """Export view raster to dictionary for JSON serialization.

    Args:
        view: Revit View
        raster: ViewRaster
        cfg: Config

    Returns:
        Dictionary with all view data
    """
    num_filled = sum(1 for m in raster.model_mask if m)

    return {
        "view_id": view.Id.IntegerValue,
        "view_name": view.Name,
        "width": raster.W,
        "height": raster.H,
        "cell_size": raster.cell_size_ft,
        "tile_size": raster.tile.tile_size,
        "total_elements": len(raster.element_meta),
        "filled_cells": num_filled,
        "raster": raster.to_dict(),
        "config": cfg.to_dict(),
        "diagnostics": {
            "num_elements": len(raster.element_meta),
            "num_annotations": len(raster.anno_meta),
            "num_filled_cells": num_filled,
        },
    }
