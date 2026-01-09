# vop_interwoven code map (authoritative zip)

## vop_interwoven/__init__.py

**Imports**

- from .config import Config

_No top-level defs._

## vop_interwoven/config.py

**Imports**

- import: math

**Top-level definitions**

- class `Config` (L11) — Configuration for VOP interwoven pipeline.
  - method `__init__()` (L56) — Initialize VOP configuration.
    - persistent view-cache controls: `view_cache_enabled`, `view_cache_dir`, `view_cache_require_doc_unmodified`
  - method `compute_adaptive_tile_size()` (L209) — Compute optimal tile size based on grid dimensions.
  - method `max_grid_cells_width()` (L259) — Maximum grid width in cells based on max sheet width and cell size.
  - method `max_grid_cells_height()` (L268) — Maximum grid height in cells based on max sheet height and cell size.
  - method `bounds_buffer_ft()` (L277) — Bounds buffer in feet (converted from inches).
  - method `silhouette_tiny_thresh_ft()` (L286) — Threshold for tiny elements in feet (default: 3.0 ft).
  - method `silhouette_large_thresh_ft()` (L295) — Threshold for large elements in feet (default: 20.0 ft).
  - method `coarse_tess_max_verts()` (L304) — Maximum vertices per face for coarse tessellation (default: 20).
  - method `get_silhouette_strategies()` (L312) — Get silhouette extraction strategies for a given UV mode (shape).
  - method `__repr__()` (L344)
  - method `to_dict()` (L364) — Export configuration as dictionary for JSON serialization.
  - method `from_dict()` (L393) — Create Config from dictionary (e.g., from JSON).

## vop_interwoven/core/__init__.py

**Imports**

- from geometry import Mode, classify_by_uv, make_obb_or_skinny_aabb, make_uv_aabb
- from raster import TileMap, ViewRaster

_No top-level defs._

## vop_interwoven/core/cache.py

**Imports**

- from collections import OrderedDict

**Top-level definitions**

- class `LRUCache` (L16) — A simple, bounded LRU cache keyed by hashable keys.
  - method `__init__()` (L24)
  - method `__len__()` (L34)
  - method `get()` (L37)
  - method `set()` (L54)
  - method `clear()` (L76)
  - method `stats()` (L82)

## vop_interwoven/core/diagnostics.py

**Top-level definitions**

- function `_exc_to_str()` (L3)
- class `Diagnostics` (L10) — Structured diagnostics recorder (Dynamo-safe minimal stdlib).
  - method `__init__()` (L20)
  - method `_count_key()` (L34)
  - method `_record()` (L38)
  - method `debug()` (L55)
  - method `info()` (L82)
  - method `debug_dedupe()` (L109) — Record at most one DEBUG event per dedupe_key, with a suppressed_count.
  - method `warn()` (L162)
  - method `error()` (L188)
  - method `to_dict()` (L215)

## vop_interwoven/core/footprint.py

**Top-level definitions**

- class `CellRectFootprint` (L3) — Footprint wrapper for a CellRect, future-proofing for hull footprints.
  - method `__init__()` (L5)
  - method `tiles()` (L8)
  - method `cells()` (L12)
- class `HullFootprint` (L15) — Footprint defined by a convex hull in UV space.
  - method `__init__()` (L20)
  - method `tiles()` (L31)
  - method `cells()` (L37)

## vop_interwoven/core/geometry.py

**Imports**

- from enum import Enum

**Top-level definitions**

- class `Mode` (L11) — Element classification based on UV footprint size.
- function `_mesh_vertex_count()` (L23) — Best-effort vertex count for Revit Mesh-like objects.
- function `tier_a_is_ambiguous()` (L52)
- function `classify_by_uv_pca()` (L67)
- function `classify_by_uv()` (L79) — Classify element mode based on UV cell dimensions.
- class `UV_AABB` (L117) — Axis-aligned bounding box proxy in UV (view XY) space.
  - method `__init__()` (L134)
  - method `width()` (L141) — Width of AABB.
  - method `height()` (L145) — Height of AABB.
  - method `center()` (L149) — Center point (u, v).
  - method `edges()` (L153) — Return 4 edge segments [(u0,v0), (u1,v1)] for stamping.
  - method `__repr__()` (L168)
- class `OBB` (L172) — Oriented bounding box proxy for LINEAR elements.
  - method `__init__()` (L195)
  - method `long_axis_length()` (L201) — Length along the long axis (2 * max extent).
  - method `short_axis_length()` (L205) — Length along the short axis (2 * min extent).
  - method `corners()` (L209) — Return 4 corner points for stamping edges.
  - method `edges()` (L225) — Return 4 edge segments [(u0,v0), (u1,v1)] for stamping.
  - method `__repr__()` (L237)
- function `make_uv_aabb()` (L241) — Create UV_AABB proxy from CellRect.
- function `make_obb_or_skinny_aabb()` (L267) — Create OBB or skinny AABB proxy for LINEAR elements.
- function `mark_rect_center_cell()` (L297) — Mark center cell of rectangle in boolean mask (for TINY proxy presence).
- function `mark_thin_band_along_long_axis()` (L320) — Mark thin band along long axis of rectangle (for LINEAR proxy presence).

## vop_interwoven/core/hull.py

**Top-level definitions**

- function `convex_hull_uv()` (L1) — Compute convex hull of 2D points using monotonic chain.

## vop_interwoven/core/math_utils.py

**Top-level definitions**

- class `Bounds2D` (L8) — 2D axis-aligned bounding box in view XY space.
  - method `__init__()` (L22)
  - method `width()` (L28) — Width of bounds.
  - method `height()` (L32) — Height of bounds.
  - method `area()` (L36) — Area of bounds.
  - method `contains_point()` (L40) — Check if point (x, y) is inside bounds (inclusive).
  - method `intersects()` (L44) — Check if this bounds intersects another Bounds2D.
  - method `expand()` (L52) — Return new Bounds2D expanded by margin on all sides.
  - method `__repr__()` (L58)
- class `CellRect` (L62) — Rectangle in grid cell coordinates.
  - method `__init__()` (L79)
  - method `cells()` (L90) — Generator yielding all (i, j) cell indices in this rectangle.
  - method `cell_count()` (L96) — Total number of cells in rectangle.
  - method `width()` (L102)
  - method `height()` (L105)
  - method `center_cell()` (L108) — Return (i, j) of center cell.
  - method `__repr__()` (L114)
- function `cellrect_dims()` (L117) — Return (width_cells, height_cells) for any supported CellRect-like object.
- function `rect_intersects_bounds()` (L161) — Check if rectangle [xmin, ymin, xmax, ymax] intersects Bounds2D.
- function `clamp()` (L185) — Clamp value to [min_val, max_val].
- function `point_in_rect()` (L190) — Check if point (x, y) is inside rectangle [xmin, ymin, xmax, ymax].

## vop_interwoven/core/pca2d.py

**Imports**

- import: math

**Top-level definitions**

- function `pca_oriented_extents_uv()` (L3) — Return (major_extent, minor_extent) in UV units using 2D PCA.

## vop_interwoven/core/raster.py

**Top-level definitions**

- function `_extract_source_type()` (L9) — Extract simple source type from doc_key.
- class `TileMap` (L28) — Tile-based spatial acceleration structure for early-out occlusion testing.
  - method `__init__()` (L47) — Initialize tile map.
  - method `get_tile_index()` (L65) — Get tile index for cell (i, j).
  - method `get_tiles_for_rect()` (L79) — Get list of tile indices overlapping rectangle.
  - method `is_tile_full()` (L100) — Check if tile is completely filled.
  - method `update_filled_count()` (L112) — Update filled count for tile containing cell.
  - method `update_w_min()` (L123) — Update minimum W-depth for tile containing cell.
- class `ViewRaster` (L138) — Raster representation of a single view for VOP interwoven pipeline.
  - T3) — Rasterize OPEN polyline paths as edges only (no interior fill).
  - method `__init__()` (L256) — Initialize view raster.
  - method `width()` (L311) — Raster width in cells (alias for W).
  - method `height()` (L316) — Raster height in cells (alias for H).
  - method `cell_size()` (L321) — Cell size in model units (alias for cell_size_ft).
  - method `bounds()` (L326) — Bounds in view-local XY (alias for bounds_xy).
  - method `get_cell_index()` (L330) — Get linear index for cell (i, j).
  - method `model_occ_mask()` (L346) — Depth-tested interior occupancy ('truth'). Backed by legacy model_mask.
  - method `model_occ_mask()` (L351)
  - method `model_proxy_presence()` (L355) — Heuristic presence mask (Tiny/Linear proxies). Backed by legacy model_proxy_mask.
  - method `model_proxy_presence()` (L360)
  - method `has_model_occ()` (L363) — True if depth-tested interior occupancy is present at idx.
  - method `has_model_edge()` (L367) — True if a visible model edge label is present at idx.
  - method `has_model_proxy()` (L371) — True if proxy presence is present at idx.
  - method `has_model_present()` (L375) — Unified "model present" predicate with explicit mode.
  - method `try_write_cell()` (L406) — Centralized cell write with depth testing (MANDATORY contract).
  - method `get_or_create_element_meta_index()` (L459) — Get or create metadata index for element.
  - method `get_or_create_anno_meta_index()` (L499) — Get or create metadata index for annotation.
  - method `finalize_anno_over_model()` (L517) — Derive anno_over_model layer from anno_key and model presence.
  - method `stamp_model_edge_idx()` (L540) — Stamp a model ink edge cell (edge-only occupancy), with depth visibility check.
  - method `stamp_proxy_edge_idx()` (L557) — Stamp a proxy perimeter edge cell into proxy channel (config-gated in pipeline).
  - method `rasterize_proxy_loops()` (L574) — Rasterize proxy footprint loops: occlusion fill ALWAYS; proxy edges optionally.
  - method `rasterize_silhouette_loops()` (L632) — Rasterize element silhouette loops into model layers with depth testing.
  - method `_scanline_fill()` (L698) — Fill polygon interior using scanline algorithm with depth testing.
  - method `dump_occlusion_debug()` (L762) — Dump w_occ and occupancy layers for debugging.
  - method `to_dict()` (L900) — Export raster to dictionary for JSON serialization.
  - method `to_debug_dict()` (L938) — Export pruned raster payload for debug JSON (summary/medium/full).
- function `_clip_poly_to_rect_uv()` (L903) — Clip a polygon (list[(u,v)]) to an axis-aligned rect in UV using Sutherland–Hodgman.
- function `_bresenham_line()` (L967) — Generate cell coordinates along a line using Bresenham's algorithm.

## vop_interwoven/core/silhouette.py

**Imports**

- import: math

**Top-level definitions**

- function `_bbox_corners_world()` (L16) — Return 8 bbox corners in world coords, honoring bbox.Transform when present.
- function `_pca_obb_uv()` (L56) — Compute an oriented rectangle in UV using PCA (good for “linear-ish” detection).
- function `_uv_obb_rect_from_bbox()` (L122) — Build a UV OBB rectangle loop from bbox corners (using bbox.Transform if present).
- function `_determine_uv_mode()` (L151) — Classify element by UV mode (shape): TINY, LINEAR, or AREAL
- function `_location_curve_obb_silhouette()` (L195) — Build a thin oriented quad around the projected LocationCurve.
- function `_symbolic_curves_silhouette()` (L239) — For FamilyInstance (and similar): extract curve primitives visible in the view.
- function `_iter_curve_primitives()` (L306) — Yield Curve / PolyLine-like primitives from GeometryElement recursively.
- function `_cad_curves_silhouette()` (L360) — Extract curve primitives from DWG/DXF ImportInstance geometry and return OPEN polylines.
- function `_to_host_point()` (L439) — If elem is a LinkedElementProxy (or anything with .transform),
- function `_unwrap_elem()` (L452) — Many parts of the pipeline may pass proxy objects (e.g. linked proxies)
- function `get_element_silhouette()` (L464) — Extract element silhouette as 2D loops.
- function `_uv_obb_rect_silhouette()` (L566) — Return a single closed loop representing the UV OBB rectangle from bbox corners.
- function `_bbox_silhouette()` (L578) — Extract axis-aligned bounding box silhouette.
- function `_obb_silhouette()` (L639) — Extract oriented bounding box silhouette (all 8 bbox corners).
- function `_front_face_loops_silhouette()` (L700) — Extract loops from the most relevant front-facing planar face(s).
- function `_silhouette_edges()` (L860) — Extract true silhouette edges based on view direction.
- function `_order_points_by_connectivity()` (L1039) — Order points by spatial connectivity (simple greedy approach).
- function `_iter_solids()` (L1095) — Iterate all solids in geometry (recursively).
- function `_convex_hull_2d()` (L1135) — Compute 2D convex hull using Andrew's monotone chain algorithm.

## vop_interwoven/core/source_identity.py

**Top-level definitions**

- function `make_source_identity()` (L23) — Return a normalized source identity dict.

## vop_interwoven/csv_export.py

**Imports**

- import: hashlib, os
- from datetime import datetime

**Top-level definitions**

- function `compute_cell_metrics()` (L12) — Compute occupancy metrics from raster arrays.
- function `compute_annotation_type_metrics()` (L117) — Count annotation cells by type.
- function `extract_view_metadata()` (L162) — Extract view metadata for CSV export.
- function `compute_config_hash()` (L303) — Compute stable hash of config for reproducibility tracking.
- function `compute_view_frame_hash()` (L329) — Compute hash of view frame properties.
- function `build_core_csv_row()` (L351) — Build row for core CSV.
- function `build_vop_csv_row()` (L404) — Build row for VOP extended CSV.
- function `export_pipeline_to_csv()` (L470) — Export VOP pipeline results to CSV files.

## vop_interwoven/dynamo_helpers.py

**Imports**

- from vop_interwoven.config import Config
- from vop_interwoven.entry_dynamo import get_current_document, get_current_view, run_vop_pipeline_with_png

**Top-level definitions**

- function `get_views_from_input_or_current()` (L15) — Get views from Dynamo IN[0] or current view if None.
- function `get_all_views_in_model()` (L72) — Get all views in the model (all types).
- function `get_all_floor_plans()` (L101) — Get all floor plan views in the model.
- function `get_all_sections()` (L129) — Get all section views in the model.
- function `filter_supported_views()` (L154) — Filter views to only supported types and provide feedback.
- function `run_pipeline_from_dynamo_input()` (L223) — Run VOP pipeline with Dynamo-friendly inputs.

## vop_interwoven/entry_dynamo.py

**Imports**

- import: json, copy
- from .config import Config
- from .pipeline import process_document_views

**Top-level definitions**

- function `_prune_view_raster_for_json()` (L53) — Prune per-view raster payload for debug JSON (summary/medium/full).
- function `_pipeline_result_for_json()` (L87) — Build JSON-safe pipeline_result copy with pruned raster payload (no deepcopy).
- function `get_current_document()` (L147) — Get current Revit document (works in both IronPython and CPython3).
- function `get_current_view()` (L183) — Get current active view (works in both IronPython and CPython3).
- function `_normalize_view_ids()` (L219) — Normalize Dynamo/Revit view inputs into a list of Revit ElementIds/ints.
- function `run_vop_pipeline()` (L272) — Run VOP interwoven pipeline on specified views.
- function `run_vop_pipeline_with_png()` (L320) — Run pipeline + export PNGs (also sets default persistent view-cache dir under output_dir when unset). — Run VOP pipeline and export JSON + PNG files (JSON is pruned via helpers).
- function `run_vop_pipeline_with_csv()` (L383) — Run pipeline + export CSV (also sets default persistent view-cache dir under output_dir when unset). — Run VOP pipeline and export JSON + PNG + CSV files (JSON is pruned via helpers).
- function `run_vop_pipeline_json()` (L473) — Run VOP pipeline and export results to JSON file.
- function `get_test_config_tiny()` (L507) — Get config optimized for testing with TINY elements (doors, windows).
- function `get_test_config_linear()` (L523) — Get config optimized for testing with LINEAR elements (walls).
- function `get_test_config_areal_heavy()` (L539) — Get config optimized for testing with AREAL elements (floors, roofs).
- function `quick_test_current_view()` (L556) — Quick test on current active view (CPython3-compatible).

## vop_interwoven/pipeline.py

**Imports**

- import: math
- from .config import Config
- from .core.geometry import Mode, classify_by_uv, make_obb_or_skinny_aabb, make_uv_aabb
- from .core.math_utils import Bounds2D, CellRect
- from .core.raster import TileMap, ViewRaster
- from .core.silhouette import get_element_silhouette
- from .revit.annotation import rasterize_annotations
- from .revit.collection import collect_view_elements, estimate_nearest_depth_from_bbox, expand_host_link_import_model_elements, is_element_visible_in_view, sort_front_to_back
- from .revit.safe_api import safe_call
- from .revit.view_basis import make_view_basis, resolve_view_bounds

**Top-level definitions**

- function `process_document_views()` (L110) — Process multiple views through the VOP interwoven pipeline.
  - includes persistent disk-backed view-cache signature/load/save + early-exit skip boundary (cache HIT)
- function `init_view_raster()` (L290) — Initialize ViewRaster for a view.
- function `render_model_front_to_back()` (L374) — Render 3D model elements front-to-back with interwoven AreaL/Tiny/Linear handling.
- function `_is_supported_2d_view()` (L959) — Check if view type is supported (2D-ish views only).
- function `_tiles_fully_covered_and_nearer()` (L1006) — Early-out: detect tiles already fully covered by nearer ink.
- function `_bin_elements_to_tiles()` (L1037) — Bin elements into tiles for localized rendering.
- function `_tile_has_depth_conflict()` (L1069) — Determine whether a tile has depth ordering conflicts.
- function `_get_ambiguous_tiles()` (L1103) — Identify tiles needing conflict-safe processing.
- function `_render_areal_element()` (L1133) — Rasterize a single AREAL element.
- function `_render_proxy_element()` (L1148) — Rasterize a single PROXY (linear/tiny) element.
- function `_stamp_proxy_edges()` (L1173) — Stamp proxy edges into raster (shared helper).
- function `_mark_rect_center_cell()` (L1186) — Mark center cell for tiny rect proxy.
- function `_mark_thin_band_along_long_axis()` (L1194) — Mark a thin band for linear proxy.
- function `export_view_raster()` (L1214) — Export view raster + diagnostics payload.

## vop_interwoven/png_export.py

**Imports**

- import: os

**Top-level definitions**

- function `export_raster_to_png()` (L10) — Export VOP raster to PNG image with color-coded occupancy.
- function `export_pipeline_results_to_pngs()` (L285) — Export all views from pipeline result to PNG files.

## vop_interwoven/revit/__init__.py

**Imports**

- from collection import collect_view_elements, is_element_visible_in_view
- from view_basis import ViewBasis, make_view_basis

_No top-level defs._

## vop_interwoven/revit/annotation.py

**Top-level definitions**

- function `is_extent_driver_annotation()` (L12) — Check if annotation is an extent driver (can exist outside crop).
- function `compute_annotation_extents()` (L57) — Compute annotation extents for grid bounds expansion.
- function `collect_2d_annotations()` (L257) — Collect USER-ADDED 2D annotation elements by whitelist.
- function `classify_annotation()` (L421) — Classify annotation element into type.
- function `classify_keynote()` (L494) — Classify keynote element as TAG or TEXT based on keynote type.
- function `get_annotation_bbox()` (L546) — Get annotation bounding box in view coordinates.
- function `rasterize_annotations()` (L577) — Rasterize 2D annotations to anno_key layer.
- function `_project_element_bbox_to_cell_rect_for_anno()` (L663) — Project element bounding box to cell rectangle (annotation-specific).

## vop_interwoven/revit/collection.py

**Top-level definitions**

- function `resolve_element_bbox()` (L8) — Resolve an element bounding box with explicit source semantics.
- function `collect_view_elements()` (L62) — Collect all potentially visible elements in view (broad-phase).
- function `is_element_visible_in_view()` (L264) — Check if element is visible in view (respects view settings).
- function `expand_host_link_import_model_elements()` (L292) — Expand element list to include linked/imported model elements.
- function `sort_front_to_back()` (L416) — Sort elements front-to-back by approximate depth.
- function `estimate_nearest_depth_from_bbox()` (L434) — Estimate nearest depth of element from its bounding box.
- function `estimate_depth_from_loops_or_bbox()` (L502) — Get element depth from silhouette geometry or bbox fallback.
- function `estimate_depth_range_from_bbox()` (L530) — Estimate depth range (min, max) of element from its bounding box.
- function `_project_element_bbox_to_cell_rect()` (L604) — Project element bounding box to cell rectangle using OBB (oriented bounds).
- function `_extract_geometry_footprint_uv()` (L717) — Extract actual geometry footprint vertices in UV space.
- function `get_element_obb_loops()` (L826) — Get element OBB as polygon loops for accurate rasterization.
- function `_pca_obb_uv()` (L981) — Compute oriented bounding box in UV using PCA.

## vop_interwoven/revit/collection_policy.py

**Imports**

- from typing import Dict, Iterable, Optional, Set, Tuple

**Top-level definitions**

- class `PolicyStats` (L23) — Aggregated counters for policy filtering (runtime-safe).
  - method `__init__()` (L26)
  - method `mark_excluded()` (L33)
  - method `mark_included()` (L40)
- function `included_bic_names_for_source()` (L136) — Return allowlist BuiltInCategory *names* for a given source.
- function `excluded_bic_names_global()` (L144)
- function `_try_import_bic()` (L147) — Import BuiltInCategory lazily (Revit-only).
- function `_try_get_category_id()` (L152) — Resolve a BuiltInCategory name to a Category integer id for a given doc.
- function `resolve_category_ids()` (L166) — Resolve BuiltInCategory names to integer category ids for this doc (cached).
- function `should_include_element()` (L189) — Apply category policy to an element.

## vop_interwoven/revit/linked_documents.py

**Imports**

- from Autodesk.Revit.DB import CategoryType, FilteredElementCollector, ImportInstance, RevitLinkInstance

**Top-level definitions**

- function `_log()` (L27) — Simple logging function compatible with IronPython.
- class `LinkedElementProxy` (L32) — Lightweight proxy for linked/imported elements.
  - method `__init__()` (L51) — Initialize proxy with host-space bbox and link transform.
  - method `get_BoundingBox()` (L75) — Return host-space bounding box (view parameter ignored).
  - method `get_Geometry()` (L79) — Return element geometry in link-space coordinates.
- function `collect_all_linked_elements()` (L97) — Collect all elements from linked RVT files and DWG imports.
- function `_has_revit_2024_link_collector()` (L143) — Detect if Revit 2024+ FilteredElementCollector(doc, viewId, linkId) is available.
- function `_collect_visible_link_elements_2024_plus()` (L186) — Collect visible elements from link using Revit 2024+ collector.
- function `_collect_from_revit_links()` (L393) — Collect elements from linked Revit files.
- function `_collect_from_dwg_imports()` (L513) — Collect elements from DWG/DXF imports.
- function `_collect_link_elements_with_clipping()` (L610) — Collect elements from a link document with spatial clipping.
- function `_build_clip_volume()` (L754) — Build clip volume for spatial filtering from view crop box.
- function `_get_plan_view_vertical_range()` (L875) — Get vertical Z range for plan/ceiling/area views.
- function `_build_crop_prism_corners()` (L934) — Build 8 prism corners from view CropBox XY and Z range.
- function `_get_host_visible_model_categories()` (L983) — Get set of model category IDs visible in host view.
- function `_get_excluded_3d_category_ids()` (L1027) — Compatibility wrapper for legacy code; delegates to collection_policy.
- function `_transform_bbox_to_host()` (L1036) — Transform link-space bounding box to host-space AABB.

## vop_interwoven/revit/safe_api.py

**Imports**

- from typing import Any, Callable, Dict, Optional, TypeVar

**Top-level definitions**

- function `safe_call()` (L8) — Execute fn() and handle exceptions in a controlled, observable way.

## vop_interwoven/revit/tierb_proxy.py

**Imports**

- from Autodesk.Revit.DB import GeometryInstance, Options, Solid

**Top-level definitions**

- function `sample_element_uvw_points()` (L4) — Tier-B proxy: sample geometry and return list of (u, v, w) points
- function `_sample_geom_object()` (L35)

## vop_interwoven/revit/view_basis.py

**Top-level definitions**

- class `ViewBasis` (L9) — View coordinate system with origin and basis vectors.
  - method `__init__()` (L30)
  - method `is_plan_like()` (L36) — Check if view is plan-like (looking down Z axis).
  - method `is_elevation_like()` (L44) — Check if view is elevation-like (horizontal view direction).
  - method `transform_to_view_uv()` (L52) — Transform model-space point to view-local UV coordinates.
  - method `transform_to_view_uvw()` (L77) — Transform model-space point to view-local UVW coordinates.
  - method `world_to_view_local()` (L101) — Back-compat helper: accept XYZ or tuple, return view-local (u, v, w).
  - method `__repr__()` (L111)
- function `world_to_view()` (L115) — Transform world point to view coordinates (standalone helper).
- function `make_view_basis()` (L133) — Extract view basis from Revit View.
- function `xy_bounds_from_crop_box_all_corners()` (L212) — Compute XY bounds from view crop box (all 8 corners method).
- function `xy_bounds_effective()` (L279) — Compute EFFECTIVE view bounds in view-local UV.
- function `synthetic_bounds_from_visible_extents()` (L312) — Compute synthetic bounds from element extents in a view (crop-off / no-crop views).
- function `_bounds_to_tuple()` (L554)
- function `resolve_view_bounds()` (L561) — Resolve view bounds in view-local UV and return auditable metadata.
- function `_view_type_name()` (L850) — Best-effort, Revit-free view type name extraction for gating + tests.
- function `supports_model_geometry()` (L925) — Capability: view can reasonably be expected to host model geometry in this pipeline.
- function `supports_crop_bounds()` (L962) — Capability: view supports crop-based bounds (not whether crop is active).
- function `supports_depth()` (L981) — Capability: pipeline depth semantics are meaningful.
- function `resolve_view_mode()` (L995) — Decide how this view should be processed, and WHY.
- function `resolve_annotation_only_bounds()` (L1037) — Produce bounds from annotation extents ONLY (no union with model/crop).

## vop_interwoven/thinrunner.py

**Imports**

- import: os, sys
- from vop_interwoven.dynamo_helpers import run_pipeline_from_dynamo_input
- from vop_interwoven.entry_dynamo import get_current_document

_No top-level defs._

## vop_interwoven/core/silhouette.py (session updates)

**Material changes**
- Signature change: get_element_silhouette(..., diag=None)
- Signature change: _symbolic_curves_silhouette(..., diag=None)
- New helpers for family-document outline extraction and nested recursion:
  - _family_region_outlines_cached(..., diag=None)
  - _collect_regions_recursive(..., diag=None)
  - _compose_transform(...)
  - _FAMILY_REGION_OUTLINE_CACHE / _FAMILY_FAMDOC_REGION_CACHE

**Diagnostics**
- Adds structured diag events under phase "silhouette":
  - family_region.collect
  - family_region.recurse
  - family_region.emit

## vop_interwoven/pipeline.py (session updates)

**Material changes**
- Callsite threads per-view Diagnostics into silhouette extraction:
  - get_element_silhouette(..., diag=diag)

---

# Session delta notes (added 2026-01-08)

This navigation artifact was updated to reflect changes discussed/applied during the "silhouette family masking region + diagnostics wiring" session:

- `pipeline.py`: passes per-view `Diagnostics` (`diag`) into silhouette extraction entrypoint.
- `core/silhouette.py`:
  - signatures updated to accept optional `diag` and propagate it
  - added family-document outline extraction helpers (FilledRegion + masking regions via `FilledRegion.IsMasking`)
  - added nested-family recursion helpers with depth + budget guards
  - added structured diagnostic events:
    - `silhouette|family_region.collect`
    - `silhouette|family_region.recurse`
    - `silhouette|family_region.emit`

Line numbers and callsite offsets remain approximate until the repo-wide extractor is re-run.
