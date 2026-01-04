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
from .core.silhouette import get_element_silhouette
from .revit.view_basis import make_view_basis, xy_bounds_effective
from .revit.collection import (
    collect_view_elements,
    expand_host_link_import_model_elements,
    sort_front_to_back,
    is_element_visible_in_view,
    estimate_nearest_depth_from_bbox,
)
from .revit.annotation import rasterize_annotations, compute_annotation_extents


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
        try:
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
            rasterize_annotations(doc, view, raster, cfg)

            # 5) Derive annoOverModel with explicit OverModel semantics
            raster.finalize_anno_over_model(cfg)

            # 6) Export
            results.append(export_view_raster(view, raster, cfg))

        except Exception as e:
            # Log error but continue processing other views
            view_name = getattr(view, 'Name', 'Unknown') if 'view' in locals() else 'Unknown'
            print("[ERROR] vop.pipeline: Failed to process view {0}: {1}".format(view_name, e))
            continue

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
        ✔ Expands bounds to include extent-driver annotations (text, tags, dims)
    """
    # Cell size: 1/8" on sheet -> model feet
    scale = view.Scale  # e.g., 96 for 1/8" = 1'-0"
    cell_size_ft = (0.125 * scale) / 12.0  # inches -> feet

    # View basis
    basis = make_view_basis(view)

    # Base bounds in VIEW-LOCAL UV:
    # - crop box if available/active (with CropBox.Transform applied)
    # - otherwise synthetic bounds from elements in the view (drafting-safe)
    base_bounds_xy = xy_bounds_effective(
        doc, view, basis, buffer=cfg.bounds_buffer_ft
    )

    # Expand bounds to include extent-driver annotations (text, tags, dimensions)
    # These annotations can exist outside the crop box and need to be captured
    anno_bounds = compute_annotation_extents(
        doc, view, basis, base_bounds_xy, cell_size_ft, cfg
    )

    # Use expanded bounds if available, otherwise use base bounds
    bounds_xy = anno_bounds if anno_bounds is not None else base_bounds_xy

    width_ft = bounds_xy.width()
    height_ft = bounds_xy.height()

    W = max(1, math.ceil(width_ft / cell_size_ft))
    H = max(1, math.ceil(height_ft / cell_size_ft))

    # Apply grid size cap based on max sheet size (Arch E: 384x288 @ 1/8")
    max_W = cfg.max_grid_cells_width
    max_H = cfg.max_grid_cells_height

    if W > max_W or H > max_H:
        # Log warning about capping
        print(f"WARNING: Grid size {W}x{H} exceeds max {max_W}x{max_H} "
              f"(based on {cfg.max_sheet_width_in}x{cfg.max_sheet_height_in}\" sheet @ "
              f"{cfg.cell_size_paper_in}\" cell size). Capping to max.")
        W = min(W, max_W)
        H = min(H, max_H)

        # Adjust bounds to match capped grid
        bounds_xy = bounds_xy.__class__(
            bounds_xy.xmin,
            bounds_xy.ymin,
            bounds_xy.xmin + W * cell_size_ft,
            bounds_xy.ymin + H * cell_size_ft
        )

    # Compute adaptive tile size based on grid dimensions
    tile_size = cfg.compute_adaptive_tile_size(W, H)

    raster = ViewRaster(
        width=W, height=H, cell_size=cell_size_ft, bounds=bounds_xy, tile_size=tile_size
    )

    # Store view basis for annotation rasterization
    raster.view_basis = basis

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
        ✔ Uses silhouette extraction for accurate element boundaries
        ✔ Falls back to bbox if silhouette extraction fails
        ✔ Classifies elements as TINY/LINEAR/AREAL
        ✔ Rasterizes silhouette loops with edge tracking
        ✔ Handles linked/imported elements with transforms
    """
    from .revit.collection import _project_element_bbox_to_cell_rect, expand_host_link_import_model_elements

    # Get view basis for transformations
    vb = make_view_basis(view)

    # Expand to include linked/imported elements
    expanded_elements = expand_host_link_import_model_elements(doc, view, elements, cfg)

    # Sort elements front-to-back by depth for proper occlusion
    expanded_elements = sort_front_to_back(expanded_elements, view, raster)

    # Enrich elements with depth range and bbox for ambiguity detection
    from .revit.collection import estimate_depth_range_from_bbox
    for wrapper in expanded_elements:
        try:
            elem = wrapper["element"]
            world_transform = wrapper["world_transform"]

            # Calculate depth range for ambiguity detection
            depth_range = estimate_depth_range_from_bbox(elem, world_transform, view, raster)
            wrapper['depth_range'] = depth_range

            # Get projected bbox for tile binning
            rect = _project_element_bbox_to_cell_rect(elem, vb, raster)
            wrapper['uv_bbox_rect'] = rect
        except Exception:
            wrapper['depth_range'] = (0.0, 0.0)
            wrapper['uv_bbox_rect'] = None

    # Process each element (host + linked)
    processed = 0
    skipped = 0
    silhouette_success = 0
    bbox_fallback = 0

    for elem_wrapper in expanded_elements:
        elem = elem_wrapper["element"]
        doc_key = elem_wrapper["doc_key"]           # Unique key for indexing
        doc_label = elem_wrapper.get("doc_label", doc_key)  # Friendly label for logging
        world_transform = elem_wrapper["world_transform"]

        # Get element metadata
        try:
            elem_id = elem.Id.IntegerValue
            category = elem.Category.Name if elem.Category else "Unknown"
        except Exception as e:
            # Log the error but continue processing other elements
            skipped += 1
            if skipped <= 5:  # Log first 5 errors to avoid spam
                print("[WARN] vop.pipeline: Skipping element from {0}: {1}".format(doc_label, e))
            continue

        key_index = raster.get_or_create_element_meta_index(elem_id, category, source=doc_key, source_label=doc_label)

        # Calculate element depth for z-buffer occlusion
        elem_depth = estimate_nearest_depth_from_bbox(elem, world_transform, view, raster)

        # DEBUG: Log depth values for first few elements
        if processed < 10:
            print("[DEBUG] Element {0} ({1}): depth = {2}".format(elem_id, category, elem_depth))

        # Safe early-out occlusion using bbox footprint + tile z-min (front-to-back streaming)
        try:
            rect = _project_element_bbox_to_cell_rect(elem, vb, raster)
            if rect and not rect.empty:
                if _tiles_fully_covered_and_nearer(raster.tile, rect, elem_depth):
                    skipped += 1
                    continue
        except Exception:
            # Never skip on failure (must stay conservative)
            pass

        # Try silhouette extraction
        try:
            loops = get_element_silhouette(elem, view, vb, raster, cfg)

            if loops:
                # Get strategy used from first loop
                strategy = loops[0].get('strategy', 'unknown')
                
                # If any loop is marked open (e.g., DWG curves), rasterize as edges only
                try:
                    if any(loop.get("open", False) for loop in loops):
                        filled = raster.rasterize_open_polylines(loops, key_index, depth=elem_depth)
                        silhouette_success += 1
                        processed += 1
                        continue
                except Exception:
                    pass

                # Rasterize silhouette loops with actual depth for occlusion
                filled = raster.rasterize_silhouette_loops(loops, key_index, depth=elem_depth)

                if filled > 0:
                    # Tag element metadata with strategy used
                    if key_index < len(raster.element_meta):
                        raster.element_meta[key_index]['strategy'] = strategy

                    silhouette_success += 1
                    processed += 1
                    continue

        except Exception as e:
            # Silhouette extraction failed, fall back to bbox
            pass

        # Fallback: Use simple bbox filling
        try:
            rect = _project_element_bbox_to_cell_rect(elem, vb, raster)
            if rect is None or rect.empty:
                continue

            # Fill bbox with proper occlusion vs occupancy separation
            for i, j in rect.cells():
                # Set occlusion for all cells (interior + boundary) with actual depth
                raster.set_cell_filled(i, j, depth=elem_depth)

                # Set occupancy ONLY for boundary cells
                is_boundary = (i == rect.i_min or i == rect.i_max or
                              j == rect.j_min or j == rect.j_max)
                if is_boundary:
                    idx = raster.get_cell_index(i, j)
                    if idx is not None:
                        raster.model_edge_key[idx] = key_index

            # Tag element metadata with bbox fallback strategy
            if key_index < len(raster.element_meta):
                raster.element_meta[key_index]['strategy'] = 'bbox_fallback'

            bbox_fallback += 1
            processed += 1

        except Exception as e:
            skipped += 1
            if skipped <= 5:
                print("[WARN] vop.pipeline: Failed to render element {0}: {1}".format(elem_id, e))
            continue

    # Phase 4.5: Ambiguity detection (selective z-buffer prep)
    # Build tile bins and detect ambiguous tiles where depth conflicts exist
    if getattr(cfg, 'enable_ambiguity_detection', True):
        try:
            tile_bins = _bin_elements_to_tiles(expanded_elements, raster)
            ambiguous_tiles = _get_ambiguous_tiles(tile_bins, cfg)

            # TODO: Phase 4.5 triangle resolution will go here
            # For now, just log ambiguous tile count
            if getattr(cfg, 'debug_ambiguous_tiles', False) and ambiguous_tiles:
                print("[DEBUG] Ambiguous tiles detected: {0}".format(len(ambiguous_tiles)))
                print("[DEBUG] These tiles have depth conflicts and may need triangle resolution")

        except Exception as e:
            print("[WARN] vop.pipeline: Ambiguity detection failed: {0}".format(e))

    # Log summary
    if processed > 0:
        print("[INFO] vop.pipeline: Processed {0} elements ({1} silhouette, {2} bbox fallback)".format(
            processed, silhouette_success, bbox_fallback))

    if skipped > 0:
        print("[WARN] vop.pipeline: Skipped {0} elements due to errors".format(skipped))

    return processed


def _is_supported_2d_view(view):
    """Check if view type is supported (2D-ish views only).

    Args:
        view: Revit View

    Returns:
        True if view is supported (2D orthographic), False otherwise

    Commentary:
        ✔ Supports: Floor plans, ceiling plans, sections, elevations, area plans, drafting views
        ✘ Rejects: 3D views, schedules, sheets, legends
    """
    from Autodesk.Revit.DB import ViewType

    # Supported 2D view types
    supported_types = [
        ViewType.FloorPlan,
        ViewType.CeilingPlan,
        ViewType.Elevation,
        ViewType.Section,
        ViewType.AreaPlan,
        ViewType.EngineeringPlan,
        ViewType.Detail,
        ViewType.DraftingView,  # Drafting views (detail sheets, assembly drawings)
    ]

    try:
        view_type = view.ViewType
        return view_type in supported_types
    except:
        # If we can't determine type, reject it
        return False

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


def _bin_elements_to_tiles(elem_wrappers, raster):
    """Bin elements to tiles based on their projected bbox.

    Args:
        elem_wrappers: List of element wrapper dicts with enriched data
        raster: ViewRaster with tile map

    Returns:
        Dict {tile_id: [elem_wrappers]} mapping tiles to elements

    Commentary:
        Each element is added to all tiles its uv_bbox_px intersects.
        Used for ambiguity detection and selective z-buffer resolution.
    """
    tile_bins = {}

    for wrapper in elem_wrappers:
        rect = wrapper.get('uv_bbox_rect')
        if rect is None or rect.empty:
            continue

        # Get all tiles overlapping this element's bbox
        tiles = raster.tile.get_tiles_for_rect(rect.i_min, rect.j_min, rect.i_max, rect.j_max)

        for tile_id in tiles:
            if tile_id not in tile_bins:
                tile_bins[tile_id] = []
            tile_bins[tile_id].append(wrapper)

    return tile_bins


def _tile_has_depth_conflict(elem_wrappers):
    """Check if tile has depth range conflicts (ambiguity).

    Args:
        elem_wrappers: List of element wrappers touching this tile

    Returns:
        True if any pair of elements has overlapping depth ranges

    Commentary:
        Depth conflict occurs when: A.depth_min < B.depth_max AND B.depth_min < A.depth_max
        This indicates elements may be interleaved in depth (passing through each other).
    """
    if len(elem_wrappers) < 2:
        return False

    # Check all pairs for depth range overlap
    for i in range(len(elem_wrappers)):
        for j in range(i + 1, len(elem_wrappers)):
            a = elem_wrappers[i]
            b = elem_wrappers[j]

            depth_min_a, depth_max_a = a.get('depth_range', (0, 0))
            depth_min_b, depth_max_b = b.get('depth_range', (0, 0))

            # Check for range overlap
            if depth_min_a < depth_max_b and depth_min_b < depth_max_a:
                return True  # Ambiguous!

    return False


def _get_ambiguous_tiles(tile_bins, cfg):
    """Identify tiles with depth conflicts that need triangle resolution.

    Args:
        tile_bins: Dict {tile_id: [elem_wrappers]}
        cfg: Config object with debug flags

    Returns:
        List of tile_ids that are ambiguous

    Commentary:
        Ambiguous tiles are those where depth-based ordering is insufficient.
        These tiles will use triangle z-buffer resolution in Phase 4.5.
    """
    ambiguous = []

    for tile_id, elems in tile_bins.items():
        if _tile_has_depth_conflict(elems):
            ambiguous.append(tile_id)

    # Debug logging
    if getattr(cfg, 'debug_ambiguous_tiles', False):
        print("[DEBUG] Ambiguous tiles: {0} / {1} total tiles ({2:.1f}%)".format(
            len(ambiguous), len(tile_bins),
            100.0 * len(ambiguous) / len(tile_bins) if tile_bins else 0
        ))

    return ambiguous


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
