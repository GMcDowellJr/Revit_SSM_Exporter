# vop_interwoven trace map (approximate call tree)

_Regenerated from uploaded source snapshot (vop_interwoven.zip)._

## Trace: run_pipeline_from_dynamo_input (vop_interwoven/dynamo_helpers.py)

- `run_pipeline_from_dynamo_input()` — vop_interwoven/dynamo_helpers.py
  - `get_current_document()` — vop_interwoven/entry_dynamo.py
  - `get_views_from_input_or_current()` — vop_interwoven/dynamo_helpers.py
    - `get_current_view()` — vop_interwoven/entry_dynamo.py
  - `Config()` — vop_interwoven/config.py
  - `run_vop_pipeline_with_csv()` — vop_interwoven/entry_dynamo.py
    - `Config()` — vop_interwoven/config.py
    - `run_vop_pipeline()` — vop_interwoven/entry_dynamo.py
    - `export_pipeline_results_to_pngs()` — vop_interwoven/png_export.py
    - `export_pipeline_to_csv()` — vop_interwoven/csv_export.py
  - `run_vop_pipeline_with_png()` — vop_interwoven/entry_dynamo.py
    - `run_vop_pipeline()` — vop_interwoven/entry_dynamo.py
    - `_pipeline_result_for_json()` — vop_interwoven/entry_dynamo.py
    - `export_pipeline_results_to_pngs()` — vop_interwoven/png_export.py
  - `filter_supported_views()` — vop_interwoven/dynamo_helpers.py
    - `get_current_document()` — vop_interwoven/entry_dynamo.py
    - `get_views_from_input_or_current()` — vop_interwoven/dynamo_helpers.py

## Trace: run_vop_pipeline (vop_interwoven/entry_dynamo.py)

- `run_vop_pipeline()` — vop_interwoven/entry_dynamo.py
  - `Config()` — vop_interwoven/config.py
  - `_normalize_view_ids()` — vop_interwoven/entry_dynamo.py
  - `process_document_views()` — vop_interwoven/pipeline.py
    - `LRUCache()` — vop_interwoven/core/cache.py
    - `ElementCache()` — vop_interwoven/core/element_cache.py
    - `Diagnostics()` — vop_interwoven/core/diagnostics.py
    - `_perf_now()` — vop_interwoven/pipeline.py
    - `_perf_ms()` — vop_interwoven/pipeline.py
    - `resolve_view_mode()` — vop_interwoven/revit/view_basis.py
    - `_view_signature()` — vop_interwoven/pipeline.py
    - `init_view_raster()` — vop_interwoven/pipeline.py

## Trace: run_vop_pipeline_with_png (vop_interwoven/entry_dynamo.py)

- `run_vop_pipeline_with_png()` — vop_interwoven/entry_dynamo.py
  - `run_vop_pipeline()` — vop_interwoven/entry_dynamo.py
    - `Config()` — vop_interwoven/config.py
    - `_normalize_view_ids()` — vop_interwoven/entry_dynamo.py
    - `process_document_views()` — vop_interwoven/pipeline.py
  - `_pipeline_result_for_json()` — vop_interwoven/entry_dynamo.py
    - `_prune_view_raster_for_json()` — vop_interwoven/entry_dynamo.py
  - `export_pipeline_results_to_pngs()` — vop_interwoven/png_export.py
    - `export_raster_to_png()` — vop_interwoven/png_export.py

## Trace: process_document_views (vop_interwoven/pipeline.py)

- `process_document_views()` — vop_interwoven/pipeline.py
  - `LRUCache()` — vop_interwoven/core/cache.py
  - `ElementCache()` — vop_interwoven/core/element_cache.py
  - `Diagnostics()` — vop_interwoven/core/diagnostics.py
  - `_perf_now()` — vop_interwoven/pipeline.py
  - `_perf_ms()` — vop_interwoven/pipeline.py
  - `resolve_view_mode()` — vop_interwoven/revit/view_basis.py
    - `_view_type_name()` — vop_interwoven/revit/view_basis.py
    - `supports_model_geometry()` — vop_interwoven/revit/view_basis.py
    - `supports_crop_bounds()` — vop_interwoven/revit/view_basis.py
    - `supports_depth()` — vop_interwoven/revit/view_basis.py
  - `_view_signature()` — vop_interwoven/pipeline.py
    - `_safe_int()` — vop_interwoven/pipeline.py
    - `_cropbox_fingerprint()` — vop_interwoven/pipeline.py
    - `_cfg_hash()` — vop_interwoven/pipeline.py
  - `init_view_raster()` — vop_interwoven/pipeline.py
    - `make_view_basis()` — vop_interwoven/revit/view_basis.py
    - `resolve_view_mode()` — vop_interwoven/revit/view_basis.py
    - `resolve_annotation_only_bounds()` — vop_interwoven/revit/view_basis.py
    - `Bounds2D()` — vop_interwoven/core/math_utils.py
    - `resolve_view_bounds()` — vop_interwoven/revit/view_basis.py
    - `ViewRaster()` — vop_interwoven/core/raster.py
  - `collect_view_elements()` — vop_interwoven/revit/collection.py
    - `included_bic_names_for_source()` — vop_interwoven/revit/collection_policy.py
    - `PolicyStats()` — vop_interwoven/revit/collection_policy.py
    - `safe_call()` — vop_interwoven/revit/safe_api.py
    - `should_include_element()` — vop_interwoven/revit/collection_policy.py
    - `resolve_element_bbox()` — vop_interwoven/revit/collection.py
  - `render_model_front_to_back()` — vop_interwoven/pipeline.py
    - `make_view_basis()` — vop_interwoven/revit/view_basis.py
    - `expand_host_link_import_model_elements()` — vop_interwoven/revit/collection.py
    - `sort_front_to_back()` — vop_interwoven/revit/collection.py
    - `estimate_depth_range_from_bbox()` — vop_interwoven/revit/collection.py
    - `_project_element_bbox_to_cell_rect()` — vop_interwoven/revit/collection.py
    - `get_element_silhouette()` — vop_interwoven/core/silhouette.py
    - `estimate_depth_from_loops_or_bbox()` — vop_interwoven/revit/collection.py
    - `estimate_nearest_depth_from_bbox()` — vop_interwoven/revit/collection.py
  - `rasterize_annotations()` — vop_interwoven/revit/annotation.py
    - `collect_2d_annotations()` — vop_interwoven/revit/annotation.py
    - `make_view_basis()` — vop_interwoven/revit/view_basis.py
    - `get_annotation_bbox()` — vop_interwoven/revit/annotation.py
    - `_project_element_bbox_to_cell_rect_for_anno()` — vop_interwoven/revit/annotation.py
    - `_uv_to_cell()` — vop_interwoven/revit/annotation.py
    - `_stamp_line_cells()` — vop_interwoven/revit/annotation.py
    - `_stamp_rect_outline()` — vop_interwoven/revit/annotation.py
  - `export_view_raster()` — vop_interwoven/pipeline.py
  - `extract_metrics_from_view_result()` — vop_interwoven/root_cache.py
    - `Bounds2D()` — vop_interwoven/core/math_utils.py
    - `ViewRaster()` — vop_interwoven/core/raster.py
    - `compute_cell_metrics()` — vop_interwoven/csv_export.py
    - `compute_external_cell_metrics()` — vop_interwoven/csv_export.py
    - `compute_annotation_type_metrics()` — vop_interwoven/csv_export.py
  - `_extract_view_summary()` — vop_interwoven/pipeline.py

## Trace: get_element_silhouette (vop_interwoven/core/silhouette.py)

- `get_element_silhouette()` — vop_interwoven/core/silhouette.py
  - `_unwrap_elem()` — vop_interwoven/core/silhouette.py
  - `_determine_uv_mode()` — vop_interwoven/core/silhouette.py
    - `_uv_obb_rect_from_bbox()` — vop_interwoven/core/silhouette.py
  - `_uv_obb_rect_silhouette()` — vop_interwoven/core/silhouette.py
    - `_uv_obb_rect_from_bbox()` — vop_interwoven/core/silhouette.py
  - `_bbox_silhouette()` — vop_interwoven/core/silhouette.py
    - `_to_host_point()` — vop_interwoven/core/silhouette.py
    - `world_to_view()` — vop_interwoven/revit/view_basis.py
  - `_obb_silhouette()` — vop_interwoven/core/silhouette.py
    - `_to_host_point()` — vop_interwoven/core/silhouette.py
    - `world_to_view()` — vop_interwoven/revit/view_basis.py
    - `_convex_hull_2d()` — vop_interwoven/core/silhouette.py
  - `_silhouette_edges()` — vop_interwoven/core/silhouette.py
    - `_iter_solids()` — vop_interwoven/core/silhouette.py
    - `_to_host_point()` — vop_interwoven/core/silhouette.py
    - `world_to_view()` — vop_interwoven/revit/view_basis.py
    - `_order_points_by_connectivity()` — vop_interwoven/core/silhouette.py
  - `_front_face_loops_silhouette()` — vop_interwoven/core/silhouette.py
    - `_iter_solids()` — vop_interwoven/core/silhouette.py
    - `_to_host_point()` — vop_interwoven/core/silhouette.py
    - `world_to_view()` — vop_interwoven/revit/view_basis.py
  - `_cad_curves_silhouette()` — vop_interwoven/core/silhouette.py
    - `_unwrap_elem()` — vop_interwoven/core/silhouette.py
    - `_to_host_point()` — vop_interwoven/core/silhouette.py
  - `_symbolic_curves_silhouette()` — vop_interwoven/core/silhouette.py
    - `_unwrap_elem()` — vop_interwoven/core/silhouette.py
    - `_iter_curve_primitives()` — vop_interwoven/core/silhouette.py
    - `_to_host_point()` — vop_interwoven/core/silhouette.py
    - `_family_region_outlines_cached()` — vop_interwoven/core/silhouette.py
    - `_apply_transform_xyz_tuple()` — vop_interwoven/core/silhouette.py

## Trace: make_view_basis (vop_interwoven/revit/view_basis.py)

- `make_view_basis()` — vop_interwoven/revit/view_basis.py
  - `ViewBasis()` — vop_interwoven/revit/view_basis.py

## Trace: run_vop_pipeline_with_csv (vop_interwoven/entry_dynamo.py)

- `run_vop_pipeline_with_csv()` — vop_interwoven/entry_dynamo.py
  - `Config()` — vop_interwoven/config.py
  - `run_vop_pipeline()` — vop_interwoven/entry_dynamo.py
    - `Config()` — vop_interwoven/config.py
    - `_normalize_view_ids()` — vop_interwoven/entry_dynamo.py
    - `process_document_views()` — vop_interwoven/pipeline.py
  - `export_pipeline_results_to_pngs()` — vop_interwoven/png_export.py
    - `export_raster_to_png()` — vop_interwoven/png_export.py
  - `export_pipeline_to_csv()` — vop_interwoven/csv_export.py
    - `get_current_document()` — vop_interwoven/entry_dynamo.py
    - `Bounds2D()` — vop_interwoven/core/math_utils.py
    - `ViewRaster()` — vop_interwoven/core/raster.py
    - `_is_from_cache()` — vop_interwoven/csv_export.py
    - `compute_cell_metrics()` — vop_interwoven/csv_export.py
    - `compute_annotation_type_metrics()` — vop_interwoven/csv_export.py
    - `compute_external_cell_metrics()` — vop_interwoven/csv_export.py
    - `extract_view_metadata()` — vop_interwoven/csv_export.py

## Trace: run_vop_pipeline_streaming (vop_interwoven/streaming.py)

- `run_vop_pipeline_streaming()` — vop_interwoven/streaming.py
  - `Config()` — vop_interwoven/config.py
  - `RootStyleCache()` — vop_interwoven/root_cache.py
  - `StreamingExporter()` — vop_interwoven/streaming.py
  - `process_document_views_streaming()` — vop_interwoven/streaming.py
    - `process_document_views()` — vop_interwoven/pipeline.py

## Trace: thinrunner (vop_interwoven/thinrunner.py)

- _Not present in this source snapshot (no vop_interwoven/thinrunner.py found)._

## Silhouette / family-region diagnostics flow

- `get_element_silhouette()` — vop_interwoven/core/silhouette.py
  - `_unwrap_elem()` — vop_interwoven/core/silhouette.py
  - `_determine_uv_mode()` — vop_interwoven/core/silhouette.py
    - `_uv_obb_rect_from_bbox()` — vop_interwoven/core/silhouette.py
    - `_bbox_corners_world()` — vop_interwoven/core/silhouette.py
    - `_to_host_point()` — vop_interwoven/core/silhouette.py
  - `_uv_obb_rect_silhouette()` — vop_interwoven/core/silhouette.py
    - `_uv_obb_rect_from_bbox()` — vop_interwoven/core/silhouette.py
  - `_bbox_silhouette()` — vop_interwoven/core/silhouette.py
    - `_to_host_point()` — vop_interwoven/core/silhouette.py
    - `world_to_view()` — vop_interwoven/revit/view_basis.py
  - `_obb_silhouette()` — vop_interwoven/core/silhouette.py
    - `_to_host_point()` — vop_interwoven/core/silhouette.py
    - `world_to_view()` — vop_interwoven/revit/view_basis.py
    - `_convex_hull_2d()` — vop_interwoven/core/silhouette.py
  - `_silhouette_edges()` — vop_interwoven/core/silhouette.py
    - `_iter_solids()` — vop_interwoven/core/silhouette.py
    - `_iter_solids()` — vop_interwoven/core/silhouette.py
    - `_to_host_point()` — vop_interwoven/core/silhouette.py
    - `world_to_view()` — vop_interwoven/revit/view_basis.py
    - `_order_points_by_connectivity()` — vop_interwoven/core/silhouette.py
  - `_front_face_loops_silhouette()` — vop_interwoven/core/silhouette.py
    - `_iter_solids()` — vop_interwoven/core/silhouette.py
    - `_to_host_point()` — vop_interwoven/core/silhouette.py
    - `world_to_view()` — vop_interwoven/revit/view_basis.py
  - `_cad_curves_silhouette()` — vop_interwoven/core/silhouette.py
    - `_unwrap_elem()` — vop_interwoven/core/silhouette.py
    - `_to_host_point()` — vop_interwoven/core/silhouette.py
  - `_symbolic_curves_silhouette()` — vop_interwoven/core/silhouette.py
    - `_unwrap_elem()` — vop_interwoven/core/silhouette.py
    - `_iter_curve_primitives()` — vop_interwoven/core/silhouette.py
    - `_iter_curve_primitives()` — vop_interwoven/core/silhouette.py
    - `_to_host_point()` — vop_interwoven/core/silhouette.py
    - `_family_region_outlines_cached()` — vop_interwoven/core/silhouette.py
    - `_safe_int_id()` — vop_interwoven/core/silhouette.py
    - `_maybe_resize_lru()` — vop_interwoven/core/silhouette.py
    - `_cache_get()` — vop_interwoven/core/silhouette.py
