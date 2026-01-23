# vop_interwoven symbol index (defs + callsites)

This index lists definitions and approximate callsites (by file) for navigation-first debugging.
Line numbers are from AST parsing of the current source.

## High-signal symbols

**Definitions**
- entry_dynamo.py
  - `run_vop_pipeline` (L276)
- entry_dynamo.py
  - `run_vop_pipeline_with_png` (L324)
- entry_dynamo.py
  - `run_vop_pipeline_with_csv` (L401)
- streaming.py
  - `run_vop_pipeline_streaming` (L576)
- dynamo_helpers.py
  - `run_pipeline_from_dynamo_input` (L226)
- pipeline.py
  - `process_document_views` (L450)
- streaming.py
  - `process_document_views_streaming` (L488)
- pipeline.py
  - `render_model_front_to_back` (L1369)
- pipeline.py
  - `init_view_raster` (L1050)
- pipeline.py
  - `_view_signature` (L262)
- revit/view_basis.py
  - `resolve_view_bounds` (L637)
- revit/view_basis.py
  - `resolve_annotation_only_bounds` (L1220)
- revit/annotation.py
  - `rasterize_annotations` (L762)
- revit/collection.py
  - `collect_view_elements` (L64)
- core/silhouette.py
  - `get_element_silhouette` (L1593)

**Callsites (approx)**
- `run_vop_pipeline`: entry_dynamo.py
- `run_vop_pipeline_with_png`: dynamo_helpers.py
- `run_vop_pipeline_with_csv`: dynamo_helpers.py
- `run_vop_pipeline_streaming`: thinrunner_streaming.py
- `process_document_views`: entry_dynamo.py, streaming.py
- `process_document_views_streaming`: streaming.py
- `render_model_front_to_back`: pipeline.py
- `init_view_raster`: pipeline.py
- `_view_signature`: pipeline.py
- `resolve_view_bounds`: pipeline.py
- `resolve_annotation_only_bounds`: pipeline.py
- `rasterize_annotations`: pipeline.py
- `collect_view_elements`: pipeline.py
- `get_element_silhouette`: pipeline.py

## All top-level definitions

- `Bounds2D` — core/math_utils.py (L8)
- `CellRect` — core/math_utils.py (L62)
- `CellRectFootprint` — core/footprint.py (L3)
- `Config` — config.py (L11)
- `Diagnostics` — core/diagnostics.py (L10)
- `ElementCache` — core/element_cache.py (L139)
- `ElementFingerprint` — core/element_cache.py (L14)
- `HullFootprint` — core/footprint.py (L15)
- `LRUCache` — core/cache.py (L16)
- `LinkedElementProxy` — revit/linked_documents.py (L32)
- `Mode` — core/geometry.py (L11)
- `OBB` — core/geometry.py (L172)
- `PolicyStats` — revit/collection_policy.py (L23)
- `RootStyleCache` — root_cache.py (L20)
- `StrategyDiagnostics` — diagnostics/strategy_tracker.py (L26)
- `StreamingExporter` — streaming.py (L91)
- `TileMap` — core/raster.py (L45)
- `UV_AABB` — core/geometry.py (L117)
- `ViewBasis` — revit/view_basis.py (L9)
- `ViewRaster` — core/raster.py (L187)
- `_append_csv_rows` — export/csv.py (L20)
- `_apply_transform_xyz_tuple` — core/silhouette.py (L326)
- `_bbox_corners_world` — core/silhouette.py (L485)
- `_bbox_silhouette` — core/silhouette.py (L1926)
- `_bin_elements_to_tiles` — pipeline.py (L2469)
- `_bounds_to_tuple` — revit/view_basis.py (L630)
- `_bresenham_line` — core/raster.py (L1699)
- `_build_clip_volume` — revit/linked_documents.py (L803)
- `_build_crop_prism_corners` — revit/linked_documents.py (L983)
- `_cache_get` — core/silhouette.py (L338)
- `_cache_set` — core/silhouette.py (L346)
- `_cad_curves_silhouette` — core/silhouette.py (L1245)
- `_canonicalize_plane` — core/face_selection.py (L52)
- `_cell_in_model_clip` — core/raster.py (L26)
- `_cfg_hash` — pipeline.py (L246)
- `_clip_poly_to_rect_uv` — core/raster.py (L1635)
- `_coerce_view_id_int` — csv_export.py (L271)
- `_collect_from_dwg_imports` — revit/linked_documents.py (L562)
- `_collect_from_revit_links` — revit/linked_documents.py (L442)
- `_collect_link_elements_with_clipping` — revit/linked_documents.py (L659)
- `_collect_regions_recursive` — core/silhouette.py (L67)
- `_collect_visible_link_elements_2024_plus` — revit/linked_documents.py (L186)
- `_compose_transform` — core/silhouette.py (L54)
- `_convex_hull_2d` — core/silhouette.py (L2634)
- `_cropbox_fingerprint` — pipeline.py (L218)
- `_detail_line_band_silhouette` — core/silhouette.py (L709)
- `_determine_uv_mode` — core/silhouette.py (L620)
- `_diagnose_link_geometry_transform` — pipeline.py (L113)
- `_dot` — core/face_selection.py (L33)
- `_ensure_dir` — export/csv.py (L8)
- `_exc_to_str` — core/diagnostics.py (L3)
- `_extract_geometry_footprint_uv` — revit/collection.py (L789)
- `_extract_source_type` — core/raster.py (L8)
- `_extract_view_identity_for_csv` — pipeline.py (L352)
- `_extract_view_summary` — pipeline.py (L1192)
- `_family_region_outlines_cached` — core/silhouette.py (L368)
- `_fix_loop_points_uv` — core/raster.py (L154)
- `_front_face_loops_silhouette` — core/silhouette.py (L2039)
- `_get_aabb_loops_from_bbox` — core/areal_extraction.py (L53)
- `_get_ambiguous_tiles` — pipeline.py (L2535)
- `_get_element_category_name` — revit/collection.py (L770)
- `_get_excluded_3d_category_ids` — revit/linked_documents.py (L1076)
- `_get_host_visible_model_categories` — revit/linked_documents.py (L1032)
- `_get_plan_view_vertical_range` — revit/linked_documents.py (L924)
- `_has_revit_2024_link_collector` — revit/linked_documents.py (L143)
- `_is_from_cache` — csv_export.py (L17)
- `_is_supported_2d_view` — pipeline.py (L2355)
- `_iter_curve_primitives` — core/silhouette.py (L1058)
- `_iter_solids` — core/silhouette.py (L2594)
- `_location_curve_obb_silhouette` — core/silhouette.py (L664)
- `_log` — revit/linked_documents.py (L27)
- `_mark_rect_center_cell` — pipeline.py (L2618)
- `_mark_thin_band_along_long_axis` — pipeline.py (L2626)
- `_maybe_resize_lru` — core/silhouette.py (L355)
- `_merge_paths_by_endpoints` — core/silhouette.py (L1172)
- `_mesh_vertex_count` — core/geometry.py (L23)
- `_norm` — core/face_selection.py (L37)
- `_normalize` — core/face_selection.py (L41)
- `_normalize_view_ids` — entry_dynamo.py (L223)
- `_obb_silhouette` — core/silhouette.py (L1978)
- `_order_points_by_connectivity` — core/silhouette.py (L2538)
- `_pca_obb_uv` — revit/collection.py (L1196)
- `_perf_ms` — pipeline.py (L201)
- `_perf_now` — pipeline.py (L196)
- `_pipeline_result_for_json` — entry_dynamo.py (L91)
- `_planar_face_loops_silhouette` — core/silhouette.py (L2377)
- `_plane_eq_close` — core/face_selection.py (L98)
- `_plane_from_planar_face` — core/face_selection.py (L76)
- `_point_in_quad` — revit/annotation.py (L1181)
- `_project_element_bbox_to_cell_rect` — revit/collection.py (L626)
- `_project_element_bbox_to_cell_rect_for_anno` — revit/annotation.py (L1268)
- `_prune_view_raster_for_json` — entry_dynamo.py (L57)
- `_render_areal_element` — pipeline.py (L2565)
- `_render_proxy_element` — pipeline.py (L2580)
- `_round6` — root_cache.py (L14)
- `_safe_bool` — pipeline.py (L211)
- `_safe_category` — core/areal_extraction.py (L34)
- `_safe_elem_id` — core/areal_extraction.py (L16)
- `_safe_int` — pipeline.py (L204)
- `_safe_int_id` — core/silhouette.py (L311)
- `_sample_geom_object` — revit/tierb_proxy.py (L35)
- `_should_skip_outside_view_volume` — pipeline.py (L2402)
- `_silhouette_edges` — core/silhouette.py (L2199)
- `_stamp_cell` — revit/annotation.py (L1220)
- `_stamp_detail_line_band` — revit/annotation.py (L1109)
- `_stamp_line_cells` — revit/annotation.py (L1247)
- `_stamp_proxy_edges` — pipeline.py (L2605)
- `_stamp_rect_outline` — revit/annotation.py (L1227)
- `_sub` — core/face_selection.py (L48)
- `_symbolic_curves_silhouette` — core/silhouette.py (L853)
- `_tile_has_depth_conflict` — pipeline.py (L2501)
- `_tiles_fully_covered_and_nearer` — pipeline.py (L2438)
- `_to_host_point` — core/silhouette.py (L1559)
- `_to_xyz_tuple` — core/face_selection.py (L25)
- `_transform_bbox_to_host` — revit/linked_documents.py (L1085)
- `_try_get_category_id` — revit/collection_policy.py (L152)
- `_try_import_bic` — revit/collection_policy.py (L147)
- `_unwrap_elem` — core/silhouette.py (L1581)
- `_uv_obb_rect_from_bbox` — core/silhouette.py (L591)
- `_uv_obb_rect_silhouette` — core/silhouette.py (L1914)
- `_uv_to_cell` — revit/annotation.py (L1211)
- `_view_signature` — pipeline.py (L262)
- `_view_type_name` — revit/view_basis.py (L1000)
- `_viewtype_name_from_value` — csv_export.py (L312)
- `_xyz_tuple` — core/silhouette.py (L320)
- `build_core_csv_row` — csv_export.py (L619)
- `build_vop_csv_row` — csv_export.py (L672)
- `cellrect_dims` — core/math_utils.py (L117)
- `clamp` — core/math_utils.py (L185)
- `classify_annotation` — revit/annotation.py (L599)
- `classify_by_uv` — core/geometry.py (L79)
- `classify_by_uv_pca` — core/geometry.py (L67)
- `classify_keynote` — revit/annotation.py (L679)
- `collect_2d_annotations` — revit/annotation.py (L362)
- `collect_all_linked_elements` — revit/linked_documents.py (L97)
- `collect_view_elements` — revit/collection.py (L64)
- `compute_annotation_extents` — revit/annotation.py (L83)
- `compute_annotation_type_metrics` — csv_export.py (L216)
- `compute_cell_metrics` — csv_export.py (L106)
- `compute_config_hash` — root_cache.py (L286)
- `compute_external_cell_metrics` — csv_export.py (L37)
- `compute_view_frame_hash` — csv_export.py (L597)
- `convex_hull_uv` — core/hull.py (L1)
- `estimate_depth_from_loops_or_bbox` — revit/collection.py (L524)
- `estimate_depth_range_from_bbox` — revit/collection.py (L552)
- `estimate_nearest_depth_from_bbox` — revit/collection.py (L456)
- `excluded_bic_names_global` — revit/collection_policy.py (L144)
- `expand_host_link_import_model_elements` — revit/collection.py (L294)
- `export_pipeline_results_to_pngs` — png_export.py (L297)
- `export_pipeline_to_csv` — csv_export.py (L884)
- `export_raster_to_png` — png_export.py (L10)
- `export_view_raster` — pipeline.py (L2646)
- `extract_areal_geometry` — core/areal_extraction.py (L140)
- `extract_metrics_from_view_result` — root_cache.py (L310)
- `extract_view_metadata` — csv_export.py (L367)
- `filter_supported_views` — dynamo_helpers.py (L154)
- `get_all_floor_plans` — dynamo_helpers.py (L101)
- `get_all_sections` — dynamo_helpers.py (L129)
- `get_all_views_in_model` — dynamo_helpers.py (L72)
- `get_annotation_bbox` — revit/annotation.py (L731)
- `get_core_csv_header` — csv_export.py (L1206)
- `get_current_document` — entry_dynamo.py (L151)
- `get_current_view` — entry_dynamo.py (L187)
- `get_element_obb_loops` — revit/collection.py (L974)
- `get_element_silhouette` — core/silhouette.py (L1593)
- `get_perf_csv_header` — csv_export.py (L1228)
- `get_test_config_areal_heavy` — entry_dynamo.py (L619)
- `get_test_config_linear` — entry_dynamo.py (L603)
- `get_test_config_tiny` — entry_dynamo.py (L587)
- `get_views_from_input_or_current` — dynamo_helpers.py (L15)
- `get_vop_csv_header` — csv_export.py (L1216)
- `group_faces_by_plane` — core/face_selection.py (L207)
- `included_bic_names_for_source` — revit/collection_policy.py (L136)
- `init_view_raster` — pipeline.py (L1050)
- `is_element_visible_in_view` — revit/collection.py (L266)
- `is_extent_driver_annotation` — revit/annotation.py (L12)
- `iter_front_facing_planar_faces` — core/face_selection.py (L147)
- `make_obb_or_skinny_aabb` — core/geometry.py (L267)
- `make_source_identity` — core/source_identity.py (L23)
- `make_uv_aabb` — core/geometry.py (L241)
- `make_view_basis` — revit/view_basis.py (L133)
- `mark_rect_center_cell` — core/geometry.py (L333)
- `mark_thin_band_along_long_axis` — core/geometry.py (L356)
- `pca_oriented_extents_uv` — core/pca2d.py (L3)
- `point_in_rect` — core/math_utils.py (L190)
- `polygon_area_2d` — core/face_selection.py (L139)
- `process_document_views` — pipeline.py (L450)
- `process_document_views_streaming` — streaming.py (L488)
- `process_with_streaming` — streaming.py (L21)
- `projected_outer_loop_area_uv` — core/face_selection.py (L260)
- `quick_test_current_view` — entry_dynamo.py (L636)
- `rasterize_annotations` — revit/annotation.py (L762)
- `rasterize_areal_loops` — pipeline.py (L1238)
- `rect_intersects_bounds` — core/math_utils.py (L161)
- `render_model_front_to_back` — pipeline.py (L1369)
- `resolve_annotation_only_bounds` — revit/view_basis.py (L1220)
- `resolve_category_ids` — revit/collection_policy.py (L166)
- `resolve_element_bbox` — revit/collection.py (L10)
- `resolve_view_bounds` — revit/view_basis.py (L637)
- `resolve_view_mode` — revit/view_basis.py (L1174)
- `resolve_view_w_volume` — revit/view_basis.py (L211)
- `run_pipeline_from_dynamo_input` — dynamo_helpers.py (L226)
- `run_vop_pipeline` — entry_dynamo.py (L276)
- `run_vop_pipeline_json` — entry_dynamo.py (L553)
- `run_vop_pipeline_streaming` — streaming.py (L576)
- `run_vop_pipeline_with_csv` — entry_dynamo.py (L401)
- `run_vop_pipeline_with_png` — entry_dynamo.py (L324)
- `safe_call` — revit/safe_api.py (L8)
- `sample_element_uvw_points` — revit/tierb_proxy.py (L4)
- `select_dominant_face_per_plane_group` — core/face_selection.py (L316)
- `select_top_plane_groups` — core/face_selection.py (L356)
- `should_include_element` — revit/collection_policy.py (L189)
- `signed_polygon_area_2d` — core/face_selection.py (L116)
- `sort_front_to_back` — revit/collection.py (L438)
- `supports_crop_bounds` — revit/view_basis.py (L1141)
- `supports_depth` — revit/view_basis.py (L1160)
- `supports_model_geometry` — revit/view_basis.py (L1104)
- `synthetic_bounds_from_visible_extents` — revit/view_basis.py (L388)
- `tier_a_is_ambiguous` — core/geometry.py (L52)
- `view_result_to_core_row` — csv_export.py (L1238)
- `view_result_to_perf_row` — csv_export.py (L1639)
- `view_result_to_vop_row` — csv_export.py (L1367)
- `world_to_view` — revit/view_basis.py (L115)
- `xy_bounds_effective` — revit/view_basis.py (L355)
- `xy_bounds_from_crop_box_all_corners` — revit/view_basis.py (L288)

## High-signal callsite details (approx)

### `run_vop_pipeline`
- entry_dynamo.py:L363
- entry_dynamo.py:L461
- entry_dynamo.py:L573
- entry_dynamo.py:L653

### `run_vop_pipeline_with_png`
- dynamo_helpers.py:L302

### `run_vop_pipeline_with_csv`
- dynamo_helpers.py:L289

### `run_vop_pipeline_streaming`
- thinrunner_streaming.py:L76

### `process_document_views`
- entry_dynamo.py:L308
- streaming.py:L64
- streaming.py:L507
- streaming.py:L521

### `process_document_views_streaming`
- streaming.py:L660

### `render_model_front_to_back`
- pipeline.py:L809

### `init_view_raster`
- pipeline.py:L779

### `_view_signature`
- pipeline.py:L712

### `resolve_view_bounds`
- pipeline.py:L1098

### `resolve_annotation_only_bounds`
- pipeline.py:L1073

### `rasterize_annotations`
- pipeline.py:L826

### `collect_view_elements`
- pipeline.py:L803

### `get_element_silhouette`
- pipeline.py:L1678
