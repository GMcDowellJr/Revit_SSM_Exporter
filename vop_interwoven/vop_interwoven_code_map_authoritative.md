# vop_interwoven code map (authoritative zip)

## vop_interwoven/__init__.py

**Imports**

- from .config import Config

**Top-level definitions**

_No top-level defs._

## vop_interwoven/config.py

**Imports**

- import math

**Top-level definitions**

- class `Config` (L11)

## vop_interwoven/pipeline.py

**Imports**

- import math
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

- def `_perf_now` (L111)
- def `_perf_ms` (L116)
- def `process_document_views` (L126)
- def `init_view_raster` (L413)
- def `render_model_front_to_back` (L534)
- def `_is_supported_2d_view` (L1139)
- def `_tiles_fully_covered_and_nearer` (L1186)
- def `_bin_elements_to_tiles` (L1217)
- def `_tile_has_depth_conflict` (L1249)
- def `_get_ambiguous_tiles` (L1283)
- def `_render_areal_element` (L1313)
- def `_render_proxy_element` (L1371)
- def `_stamp_proxy_edges` (L1414)
- def `_mark_rect_center_cell` (L1438)
- def `_mark_thin_band_along_long_axis` (L1453)
- def `export_view_raster` (L1485)

## vop_interwoven/streaming.py

**Imports**

- import os
- import time
- import json
- from datetime import datetime
- import csv
- from vop_interwoven.config import Config
- from vop_interwoven.pipeline import process_document_views
- from vop_interwoven.pipeline import _view_signature  # Need to expose this
- from vop_interwoven.root_cache import RootStyleCache, compute_config_hash
- from vop_interwoven.csv_export import (
- from vop_interwoven.png_export import export_raster_to_png
- from vop_interwoven.entry_dynamo import _pipeline_result_for_json

**Top-level definitions**

- def `process_with_streaming` (L29)
- class `StreamingExporter` (L168)
- def `process_document_views_streaming` (L496)
- def `run_vop_pipeline_streaming` (L563)

## vop_interwoven/entry_dynamo.py

**Imports**

- import json
- import copy
- from .config import Config
- from .pipeline import process_document_views

**Top-level definitions**

- def `_prune_view_raster_for_json` (L53)
- def `_pipeline_result_for_json` (L87)
- def `get_current_document` (L147)
- def `get_current_view` (L183)
- def `_normalize_view_ids` (L219)
- def `run_vop_pipeline` (L272)
- def `run_vop_pipeline_with_png` (L320)
- def `run_vop_pipeline_with_csv` (L383)
- def `run_vop_pipeline_json` (L473)
- def `get_test_config_tiny` (L507)
- def `get_test_config_linear` (L523)
- def `get_test_config_areal_heavy` (L539)
- def `quick_test_current_view` (L556)

## vop_interwoven/thinrunner.py

**Imports**

- import os
- import sys
- from vop_interwoven.dynamo_helpers import run_pipeline_from_dynamo_input
- from vop_interwoven.entry_dynamo import get_current_document

**Top-level definitions**

_No top-level defs._

## vop_interwoven/thinrunner_streaming.py

**Imports**

- import os
- import sys

**Top-level definitions**

_No top-level defs._

## vop_interwoven/dynamo_helpers.py

**Imports**

- from vop_interwoven.config import Config
- from vop_interwoven.entry_dynamo import get_current_document, get_current_view, run_vop_pipeline_with_png

**Top-level definitions**

- def `get_views_from_input_or_current` (L15)
- def `get_all_views_in_model` (L72)
- def `get_all_floor_plans` (L101)
- def `get_all_sections` (L129)
- def `filter_supported_views` (L154)
- def `run_pipeline_from_dynamo_input` (L223)

## vop_interwoven/csv_export.py

**Imports**

- import hashlib
- import os
- from datetime import datetime

**Top-level definitions**

- def `compute_cell_metrics` (L12)
- def `compute_annotation_type_metrics` (L117)
- def `extract_view_metadata` (L162)
- def `compute_config_hash` (L303)
- def `compute_view_frame_hash` (L329)
- def `build_core_csv_row` (L351)
- def `build_vop_csv_row` (L404)
- def `export_pipeline_to_csv` (L470)

## vop_interwoven/png_export.py

**Imports**

- import os

**Top-level definitions**

- def `export_raster_to_png` (L10)
- def `export_pipeline_results_to_pngs` (L285)

## vop_interwoven/root_cache.py

**Imports**

- import os
- import json
- import hashlib
- from datetime import datetime

**Top-level definitions**

- class `RootStyleCache` (L34)
- def `compute_config_hash` (L191)
- def `extract_metrics_from_view_result` (L241)

## vop_interwoven/core/__init__.py

**Imports**

- from geometry import Mode, classify_by_uv, make_obb_or_skinny_aabb, make_uv_aabb
- from raster import TileMap, ViewRaster

**Top-level definitions**

_No top-level defs._

## vop_interwoven/core/cache.py

**Imports**

- from collections import OrderedDict

**Top-level definitions**

- class `LRUCache` (L16)

## vop_interwoven/core/diagnostics.py

**Top-level definitions**

- def `_exc_to_str` (L3)
- class `Diagnostics` (L10)

## vop_interwoven/core/element_cache.py

**Imports**

- import hashlib
- import time

**Top-level definitions**

- class `ElementFingerprint` (L50)
- class `ElementCache` (L182)

## vop_interwoven/core/footprint.py

**Top-level definitions**

- class `CellRectFootprint` (L3)
- class `HullFootprint` (L15)

## vop_interwoven/core/geometry.py

**Imports**

- from enum import Enum

**Top-level definitions**

- class `Mode` (L11)
- def `_mesh_vertex_count` (L23)
- def `tier_a_is_ambiguous` (L52)
- def `classify_by_uv_pca` (L67)
- def `classify_by_uv` (L79)
- class `UV_AABB` (L117)
- class `OBB` (L172)
- def `make_uv_aabb` (L241)
- def `make_obb_or_skinny_aabb` (L267)
- def `mark_rect_center_cell` (L297)
- def `mark_thin_band_along_long_axis` (L320)

## vop_interwoven/core/hull.py

**Top-level definitions**

- def `convex_hull_uv` (L1)

## vop_interwoven/core/math_utils.py

**Top-level definitions**

- class `Bounds2D` (L8)
- class `CellRect` (L62)
- def `cellrect_dims` (L117)
- def `rect_intersects_bounds` (L161)
- def `clamp` (L185)
- def `point_in_rect` (L190)

## vop_interwoven/core/pca2d.py

**Imports**

- import math

**Top-level definitions**

- def `pca_oriented_extents_uv` (L3)

## vop_interwoven/core/raster.py

**Top-level definitions**

- def `_extract_source_type` (L9)
- class `TileMap` (L28)
- class `ViewRaster` (L138)
- def `_clip_poly_to_rect_uv` (L903)
- def `_bresenham_line` (L967)

## vop_interwoven/core/silhouette.py

**Imports**

- import math

**Top-level definitions**

- def `_bbox_corners_world` (L16)
- def `_pca_obb_uv` (L56)
- def `_uv_obb_rect_from_bbox` (L122)
- def `_determine_uv_mode` (L151)
- def `_location_curve_obb_silhouette` (L195)
- def `_symbolic_curves_silhouette` (L239)
- def `_iter_curve_primitives` (L306)
- def `_cad_curves_silhouette` (L360)
- def `_to_host_point` (L439)
- def `_unwrap_elem` (L452)
- def `get_element_silhouette` (L464)
- def `_uv_obb_rect_silhouette` (L566)
- def `_bbox_silhouette` (L578)
- def `_obb_silhouette` (L639)
- def `_front_face_loops_silhouette` (L700)
- def `_silhouette_edges` (L860)
- def `_order_points_by_connectivity` (L1039)
- def `_iter_solids` (L1095)
- def `_convex_hull_2d` (L1135)

## vop_interwoven/core/source_identity.py

**Top-level definitions**

- def `make_source_identity` (L23)

## vop_interwoven/revit/__init__.py

**Imports**

- from collection import collect_view_elements, is_element_visible_in_view
- from view_basis import ViewBasis, make_view_basis

**Top-level definitions**

_No top-level defs._

## vop_interwoven/revit/annotation.py

**Top-level definitions**

- def `is_extent_driver_annotation` (L12)
- def `compute_annotation_extents` (L57)
- def `collect_2d_annotations` (L257)
- def `classify_annotation` (L421)
- def `classify_keynote` (L494)
- def `get_annotation_bbox` (L546)
- def `rasterize_annotations` (L577)
- def `_project_element_bbox_to_cell_rect_for_anno` (L663)

## vop_interwoven/revit/collection.py

**Top-level definitions**

- def `resolve_element_bbox` (L8)
- def `collect_view_elements` (L62)
- def `is_element_visible_in_view` (L264)
- def `expand_host_link_import_model_elements` (L292)
- def `sort_front_to_back` (L416)
- def `estimate_nearest_depth_from_bbox` (L434)
- def `estimate_depth_from_loops_or_bbox` (L502)
- def `estimate_depth_range_from_bbox` (L530)
- def `_project_element_bbox_to_cell_rect` (L604)
- def `_extract_geometry_footprint_uv` (L717)
- def `get_element_obb_loops` (L826)
- def `_pca_obb_uv` (L981)

## vop_interwoven/revit/collection_policy.py

**Imports**

- from typing import Dict, Iterable, Optional, Set, Tuple

**Top-level definitions**

- class `PolicyStats` (L23)
- def `included_bic_names_for_source` (L136)
- def `excluded_bic_names_global` (L144)
- def `_try_import_bic` (L147)
- def `_try_get_category_id` (L152)
- def `resolve_category_ids` (L166)
- def `should_include_element` (L189)

## vop_interwoven/revit/linked_documents.py

**Imports**

- from Autodesk.Revit.DB import CategoryType, FilteredElementCollector, ImportInstance, RevitLinkInstance

**Top-level definitions**

- def `_log` (L27)
- class `LinkedElementProxy` (L32)
- def `collect_all_linked_elements` (L97)
- def `_has_revit_2024_link_collector` (L143)
- def `_collect_visible_link_elements_2024_plus` (L186)
- def `_collect_from_revit_links` (L393)
- def `_collect_from_dwg_imports` (L513)
- def `_collect_link_elements_with_clipping` (L610)
- def `_build_clip_volume` (L754)
- def `_get_plan_view_vertical_range` (L875)
- def `_build_crop_prism_corners` (L934)
- def `_get_host_visible_model_categories` (L983)
- def `_get_excluded_3d_category_ids` (L1027)
- def `_transform_bbox_to_host` (L1036)

## vop_interwoven/revit/safe_api.py

**Imports**

- from typing import Any, Callable, Dict, Optional, TypeVar

**Top-level definitions**

- def `safe_call` (L8)

## vop_interwoven/revit/tierb_proxy.py

**Imports**

- from Autodesk.Revit.DB import GeometryInstance, Options, Solid

**Top-level definitions**

- def `sample_element_uvw_points` (L4)
- def `_sample_geom_object` (L35)

## vop_interwoven/revit/view_basis.py

**Top-level definitions**

- class `ViewBasis` (L9)
- def `world_to_view` (L115)
- def `make_view_basis` (L133)
- def `xy_bounds_from_crop_box_all_corners` (L212)
- def `xy_bounds_effective` (L279)
- def `synthetic_bounds_from_visible_extents` (L312)
- def `_bounds_to_tuple` (L554)
- def `resolve_view_bounds` (L561)
- def `_view_type_name` (L850)
- def `supports_model_geometry` (L925)
- def `supports_crop_bounds` (L962)
- def `supports_depth` (L981)
- def `resolve_view_mode` (L995)
- def `resolve_annotation_only_bounds` (L1037)
