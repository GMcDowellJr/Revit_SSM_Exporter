# vop_interwoven symbol index (defs + callsites)

This index lists definitions and approximate callsites (by file) for navigation-first debugging.
Line numbers are from AST parsing of the current source.

## High-signal symbols

**Definitions**
- entry_dynamo.py
  - `run_vop_pipeline` (L306)
- entry_dynamo.py
  - `run_vop_pipeline_with_png` (L354)
- entry_dynamo.py
  - `run_vop_pipeline_with_csv` (L441)
- streaming.py
  - `run_vop_pipeline_streaming` (L677)
- dynamo_helpers.py
  - `run_pipeline_from_dynamo_input` (L227)
- pipeline.py
  - `process_document_views` (L539)
- streaming.py
  - `process_document_views_streaming` (L545)
- pipeline.py
  - `render_model_front_to_back` (L1779)
- pipeline.py
  - `init_view_raster` (L1474)
- pipeline.py
  - `_view_signature` (L270)
- revit/view_basis.py
  - `resolve_view_bounds` (L733)
- revit/view_basis.py
  - `resolve_annotation_only_bounds` (L1411)
- revit/annotation.py
  - `rasterize_annotations` (L903)
- revit/collection.py
  - `collect_view_elements` (L64)
- core/silhouette.py
  - `get_element_silhouette` (L1920)

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
- `Config` — config.py (L12)
- `Diagnostics` — core/diagnostics.py (L10)
- `ElementCache` — core/element_cache.py (L139)
- `ElementFingerprint` — core/element_cache.py (L14)
- `HullFootprint` — core/footprint.py (L15)
- `LRUCache` — core/cache.py (L16)
- `LinkedElementProxy` — revit/linked_documents.py (L32)
- `ManifestLoader` — metrics_manifest.py (L35)
- `ManifestValidationError` — metrics_manifest.py (L21)
- `MetricsManifest` — metrics_manifest.py (L26)
- `MetricsValidationError` — metrics/manifest_evaluator.py (L8)
- `Mode` — core/geometry.py (L11)
- `OBB` — core/geometry.py (L172)
- `OcclusionTracker` — diagnostics/occlusion_tracker.py (L4)
- `PolicyStats` — revit/collection_policy.py (L23)
- `RootStyleCache` — root_cache.py (L20)
- `StrategyDiagnostics` — diagnostics/strategy_tracker.py (L26)
- `StreamingExporter` — streaming.py (L100)
- `TileMap` — core/raster.py (L45)
- `UV_AABB` — core/geometry.py (L117)
- `ViewBasis` — revit/view_basis.py (L9)
- `ViewRaster` — core/raster.py (L296)
- `_append_csv_rows` — export/csv.py (L20)
- `_apply_transform_xyz_tuple` — core/silhouette.py (L435)
- `_bbox_corners_world` — core/silhouette.py (L642)
- `_bbox_silhouette` — core/silhouette.py (L2363)
- `_bin_elements_to_tiles` — pipeline.py (L3282)
- `_bounds_to_tuple` — revit/view_basis.py (L726)
- `_bresenham_line` — core/raster.py (L2143)
- `_build_clip_volume` — revit/linked_documents.py (L860)
- `_build_crop_prism_corners` — revit/linked_documents.py (L1040)
- `_build_manifest_vop_row_from_metrics` — csv_export.py (L1260)
- `_cache_get` — core/silhouette.py (L447)
- `_cache_set` — core/silhouette.py (L455)
- `_cad_curves_silhouette` — core/silhouette.py (L1546)
- `_canonicalize_plane` — core/face_selection.py (L52)
- `_cell_in_model_clip` — core/raster.py (L26)
- `_cfg_hash` — pipeline.py (L254)
- `_clip_poly_to_rect_uv` — core/raster.py (L2079)
- `_coerce_view_id_int` — csv_export.py (L404)
- `_collect_from_dwg_imports` — revit/linked_documents.py (L619)
- `_collect_from_revit_links` — revit/linked_documents.py (L499)
- `_collect_link_elements_with_clipping` — revit/linked_documents.py (L716)
- `_collect_regions_recursive` — core/silhouette.py (L84)
- `_collect_visible_link_elements_2024_plus` — revit/linked_documents.py (L186)
- `_commit_polygon_mask` — core/raster.py (L253)
- `_compose_transform` — core/silhouette.py (L70)
- `_compute_cell_metrics_np` — csv_export.py (L160)
- `_compute_cell_metrics_py` — csv_export.py (L228)
- `_compute_manifest_metrics_payload` — pipeline.py (L496)
- `_convex_hull_2d` — core/silhouette.py (L3122)
- `_cropbox_fingerprint` — pipeline.py (L225)
- `_default_model_class_resolver` — metrics/final_state_scanner.py (L63)
- `_detail_line_band_silhouette` — core/silhouette.py (L873)
- `_determine_uv_mode` — core/silhouette.py (L777)
- `_diag_fallback_stderr` — revit/safe_api.py (L9)
- `_diagnose_coordinate_spaces` — revit/collection.py (L910)
- `_diagnose_link_geometry_transform` — pipeline.py (L120)
- `_dot` — core/face_selection.py (L33)
- `_ensure_dir` — export/csv.py (L8)
- `_ensure_object` — metrics_manifest.py (L43)
- `_ensure_string` — metrics_manifest.py (L49)
- `_ensure_string_list` — metrics_manifest.py (L57)
- `_eval_expr` — metrics/manifest_evaluator.py (L55)
- `_exc_to_str` — core/diagnostics.py (L3)
- `_export_png_dotnet` — png_export.py (L10)
- `_export_png_pillow` — png_export.py (L334)
- `_extract_geometry_footprint_uv` — revit/collection.py (L1137)
- `_extract_source_type` — core/raster.py (L8)
- `_extract_view_identity_for_csv` — pipeline.py (L382)
- `_extract_view_summary` — pipeline.py (L1626)
- `_extract_view_unique_id` — csv_export.py (L500)
- `_family_region_outlines_cached` — core/silhouette.py (L477)
- `_family_sum` — metrics/manifest_evaluator.py (L50)
- `_fix_loop_points_uv` — core/raster.py (L154)
- `_front_face_loops_silhouette` — core/silhouette.py (L2476)
- `_get_aabb_loops_from_bbox` — core/areal_extraction.py (L53)
- `_get_ambiguous_tiles` — pipeline.py (L3348)
- `_get_element_category_name` — revit/collection.py (L891)
- `_get_element_meta` — metrics/final_state_scanner.py (L33)
- `_get_excluded_3d_category_ids` — revit/linked_documents.py (L1133)
- `_get_host_visible_model_categories` — revit/linked_documents.py (L1089)
- `_get_manifest_csv_columns` — csv_export.py (L1252)
- `_get_metrics_triplet_from_view_result` — csv_export.py (L340)
- `_get_plan_view_vertical_range` — revit/linked_documents.py (L981)
- `_get_source_type` — metrics/final_state_scanner.py (L12)
- `_has_revit_2024_link_collector` — revit/linked_documents.py (L143)
- `_install` — bootstrap.py (L32)
- `_intersects_crop_volume` — pipeline.py (L3179)
- `_is_excluded_from_extent_expansion` — revit/annotation.py (L12)
- `_is_from_cache` — csv_export.py (L55)
- `_is_importable` — bootstrap.py (L24)
- `_is_supported_2d_view` — pipeline.py (L3126)
- `_iter_curve_primitives` — core/silhouette.py (L1353)
- `_iter_solids` — core/silhouette.py (L3082)
- `_location_curve_obb_silhouette` — core/silhouette.py (L821)
- `_log` — revit/linked_documents.py (L27)
- `_mark_rect_center_cell` — pipeline.py (L3420)
- `_mark_thin_band_along_long_axis` — pipeline.py (L3428)
- `_maybe_resize_lru` — core/silhouette.py (L464)
- `_merge_paths_by_endpoints` — core/silhouette.py (L1473)
- `_mesh_vertex_count` — core/geometry.py (L23)
- `_norm` — core/face_selection.py (L37)
- `_normalize` — core/face_selection.py (L41)
- `_normalize_anno_type` — metrics/final_state_scanner.py (L52)
- `_normalize_locked_metrics_for_legacy_csv` — csv_export.py (L302)
- `_normalize_view_ids` — entry_dynamo.py (L252)
- `_obb_silhouette` — core/silhouette.py (L2415)
- `_order_points_by_connectivity` — core/silhouette.py (L3026)
- `_pca_obb_uv` — revit/collection.py (L1676)
- `_perf_ms` — pipeline.py (L208)
- `_perf_now` — pipeline.py (L203)
- `_pipeline_result_for_json` — entry_dynamo.py (L91)
- `_planar_face_loops_silhouette` — core/silhouette.py (L2815)
- `_plane_eq_close` — core/face_selection.py (L98)
- `_plane_from_planar_face` — core/face_selection.py (L76)
- `_point_in_quad` — revit/annotation.py (L1466)
- `_polygon_mask_np` — core/raster.py (L187)
- `_project_element_bbox_to_cell_rect` — revit/collection.py (L719)
- `_project_element_bbox_to_cell_rect_for_anno` — revit/annotation.py (L1725)
- `_prune_view_raster_for_json` — entry_dynamo.py (L57)
- `_raster_to_dict_like` — csv_export.py (L18)
- `_rasterize_filled_region_shape` — revit/annotation.py (L1553)
- `_render_proxy_element` — pipeline.py (L3378)
- `_round6` — root_cache.py (L14)
- `_safe_bool` — pipeline.py (L218)
- `_safe_category` — core/areal_extraction.py (L34)
- `_safe_elem_id` — core/areal_extraction.py (L16)
- `_safe_int` — pipeline.py (L211)
- `_safe_int_id` — core/silhouette.py (L419)
- `_sample_geom_object` — revit/tierb_proxy.py (L35)
- `_should_skip_outside_view_volume` — pipeline.py (L3205)
- `_silhouette_edges` — core/silhouette.py (L2636)
- `_stamp_cell` — revit/annotation.py (L1505)
- `_stamp_detail_line_band` — revit/annotation.py (L1394)
- `_stamp_line_cells` — revit/annotation.py (L1532)
- `_stamp_proxy_edges` — pipeline.py (L3403)
- `_stamp_rect_outline` — revit/annotation.py (L1512)
- `_sub` — core/face_selection.py (L48)
- `_symbolic_curves_silhouette` — core/silhouette.py (L1034)
- `_tile_has_depth_conflict` — pipeline.py (L3314)
- `_tiles_fully_covered_and_nearer` — pipeline.py (L3244)
- `_to_host_point` — core/silhouette.py (L1886)
- `_to_xyz_tuple` — core/face_selection.py (L25)
- `_transform_bbox_to_host` — revit/linked_documents.py (L1142)
- `_try_get_category_id` — revit/collection_policy.py (L152)
- `_try_import_bic` — revit/collection_policy.py (L147)
- `_unwrap_elem` — core/silhouette.py (L1908)
- `_uv_obb_rect_from_bbox` — core/silhouette.py (L748)
- `_uv_obb_rect_silhouette` — core/silhouette.py (L2351)
- `_uv_to_cell` — revit/annotation.py (L1496)
- `_view_signature` — pipeline.py (L270)
- `_view_type_name` — revit/view_basis.py (L1163)
- `_viewtype_name_from_value` — csv_export.py (L445)
- `_xyz_tuple` — core/silhouette.py (L429)
- `build_core_csv_row` — csv_export.py (L903)
- `build_occlusion_row` — csv_export.py (L1186)
- `build_vop_csv_row` — csv_export.py (L961)
- `canonicalize_manifest_json` — metrics_manifest.py (L138)
- `cellrect_dims` — core/math_utils.py (L117)
- `clamp` — core/math_utils.py (L185)
- `classify_annotation` — revit/annotation.py (L740)
- `classify_by_uv` — core/geometry.py (L79)
- `classify_by_uv_pca` — core/geometry.py (L67)
- `classify_keynote` — revit/annotation.py (L820)
- `collect_2d_annotations` — revit/annotation.py (L452)
- `collect_all_linked_elements` — revit/linked_documents.py (L97)
- `collect_view_elements` — revit/collection.py (L64)
- `compute_annotation_extents` — revit/annotation.py (L123)
- `compute_annotation_type_metrics` — csv_export.py (L349)
- `compute_cell_metrics` — csv_export.py (L145)
- `compute_config_hash` — root_cache.py (L295)
- `compute_external_cell_metrics` — csv_export.py (L76)
- `compute_manifest_sha256` — metrics_manifest.py (L144)
- `compute_view_frame_hash` — csv_export.py (L881)
- `convex_hull_uv` — core/hull.py (L1)
- `ensure_numpy` — np_backend.py (L30)
- `ensure_pillow` — np_backend.py (L60)
- `estimate_depth_from_loops_or_bbox` — revit/collection.py (L596)
- `estimate_depth_range_from_bbox` — revit/collection.py (L624)
- `estimate_nearest_depth_from_bbox` — revit/collection.py (L520)
- `evaluate_metrics_manifest` — metrics/manifest_evaluator.py (L118)
- `excluded_bic_names_global` — revit/collection_policy.py (L144)
- `expand_host_link_import_model_elements` — revit/collection.py (L348)
- `export_occlusion_diagnostics_csv` — csv_export.py (L1213)
- `export_pipeline_results_to_pngs` — png_export.py (L430)
- `export_pipeline_to_csv` — csv_export.py (L1274)
- `export_raster_to_png` — png_export.py (L407)
- `export_view_raster` — pipeline.py (L3446)
- `extract_areal_geometry` — core/areal_extraction.py (L140)
- `extract_metrics_from_view_result` — root_cache.py (L319)
- `extract_view_metadata` — csv_export.py (L532)
- `filter_supported_views` — dynamo_helpers.py (L154)
- `get_all_floor_plans` — dynamo_helpers.py (L101)
- `get_all_sections` — dynamo_helpers.py (L129)
- `get_all_views_in_model` — dynamo_helpers.py (L72)
- `get_annotation_bbox` — revit/annotation.py (L872)
- `get_core_csv_header` — csv_export.py (L1621)
- `get_current_document` — entry_dynamo.py (L180)
- `get_current_view` — entry_dynamo.py (L216)
- `get_element_obb_loops` — revit/collection.py (L1412)
- `get_element_silhouette` — core/silhouette.py (L1920)
- `get_occlusion_csv_header` — csv_export.py (L1647)
- `get_perf_csv_header` — csv_export.py (L1721)
- `get_test_config_areal_heavy` — entry_dynamo.py (L650)
- `get_test_config_linear` — entry_dynamo.py (L634)
- `get_test_config_tiny` — entry_dynamo.py (L618)
- `get_views_from_input_or_current` — dynamo_helpers.py (L15)
- `get_vop_csv_header` — csv_export.py (L1632)
- `group_faces_by_plane` — core/face_selection.py (L212)
- `included_bic_names_for_source` — revit/collection_policy.py (L136)
- `init_view_raster` — pipeline.py (L1474)
- `is_element_visible_in_view` — revit/collection.py (L324)
- `is_extent_driver_annotation` — revit/annotation.py (L47)
- `iter_front_facing_planar_faces` — core/face_selection.py (L147)
- `load_manifest_json` — metrics_manifest.py (L148)
- `make_obb_or_skinny_aabb` — core/geometry.py (L267)
- `make_source_identity` — core/source_identity.py (L23)
- `make_uv_aabb` — core/geometry.py (L241)
- `make_view_basis` — revit/view_basis.py (L133)
- `pca_oriented_extents_uv` — core/pca2d.py (L3)
- `point_in_rect` — core/math_utils.py (L190)
- `polygon_area_2d` — core/face_selection.py (L139)
- `process_document_views` — pipeline.py (L539)
- `process_document_views_streaming` — streaming.py (L545)
- `process_with_streaming` — streaming.py (L24)
- `projected_outer_loop_area_uv` — core/face_selection.py (L265)
- `quick_test_current_view` — entry_dynamo.py (L667)
- `rasterize_annotations` — revit/annotation.py (L903)
- `rasterize_areal_loops` — pipeline.py (L1664)
- `record_error` — revit/safe_api.py (L58)
- `record_warning` — revit/safe_api.py (L92)
- `rect_intersects_bounds` — core/math_utils.py (L161)
- `render_model_front_to_back` — pipeline.py (L1779)
- `reset_family_region_caches` — core/silhouette.py (L53)
- `resolve_annotation_only_bounds` — revit/view_basis.py (L1411)
- `resolve_category_ids` — revit/collection_policy.py (L166)
- `resolve_element_bbox` — revit/collection.py (L10)
- `resolve_view_bounds` — revit/view_basis.py (L733)
- `resolve_view_mode` — revit/view_basis.py (L1358)
- `resolve_view_w_volume` — revit/view_basis.py (L235)
- `run` — bootstrap.py (L48)
- `run_pipeline_from_dynamo_input` — dynamo_helpers.py (L227)
- `run_vop_pipeline` — entry_dynamo.py (L306)
- `run_vop_pipeline_json` — entry_dynamo.py (L584)
- `run_vop_pipeline_streaming` — streaming.py (L677)
- `run_vop_pipeline_with_csv` — entry_dynamo.py (L441)
- `run_vop_pipeline_with_png` — entry_dynamo.py (L354)
- `safe_call` — revit/safe_api.py (L121)
- `sample_element_uvw_points` — revit/tierb_proxy.py (L4)
- `scan_final_state_totals` — metrics/final_state_scanner.py (L82)
- `select_dominant_face_per_plane_group` — core/face_selection.py (L321)
- `select_top_plane_groups` — core/face_selection.py (L361)
- `should_include_element` — revit/collection_policy.py (L190)
- `signed_polygon_area_2d` — core/face_selection.py (L116)
- `sort_front_to_back` — revit/collection.py (L502)
- `supports_crop_bounds` — revit/view_basis.py (L1318)
- `supports_depth` — revit/view_basis.py (L1344)
- `supports_model_geometry` — revit/view_basis.py (L1274)
- `synthetic_bounds_from_visible_extents` — revit/view_basis.py (L444)
- `tier_a_is_ambiguous` — core/geometry.py (L52)
- `validate_families_block` — metrics_manifest.py (L90)
- `validate_invariants_block` — metrics_manifest.py (L103)
- `validate_manifest_schema_version` — metrics_manifest.py (L65)
- `validate_manifest_top_level_keys` — metrics_manifest.py (L71)
- `validate_outputs_block` — metrics_manifest.py (L127)
- `validate_requires_block` — metrics_manifest.py (L80)
- `view_result_to_core_row` — csv_export.py (L1738)
- `view_result_to_occlusion_row` — csv_export.py (L1660)
- `view_result_to_perf_row` — csv_export.py (L2169)
- `view_result_to_vop_row` — csv_export.py (L1872)
- `world_to_view` — revit/view_basis.py (L115)
- `xy_bounds_effective` — revit/view_basis.py (L398)
- `xy_bounds_from_crop_box_all_corners` — revit/view_basis.py (L331)

## High-signal callsite details (approx)

### `run_vop_pipeline`
- entry_dynamo.py:L403
- entry_dynamo.py:L512
- entry_dynamo.py:L604
- entry_dynamo.py:L684

### `run_vop_pipeline_with_png`
- dynamo_helpers.py:L303

### `run_vop_pipeline_with_csv`
- dynamo_helpers.py:L290

### `run_vop_pipeline_streaming`
- thinrunner_streaming.py:L79

### `process_document_views`
- entry_dynamo.py:L338
- streaming.py:L73
- streaming.py:L564
- streaming.py:L586

### `process_document_views_streaming`
- streaming.py:L772

### `render_model_front_to_back`
- pipeline.py:L1060

### `init_view_raster`
- pipeline.py:L1017

### `_view_signature`
- pipeline.py:L929

### `resolve_view_bounds`
- pipeline.py:L1522

### `resolve_annotation_only_bounds`
- pipeline.py:L1497

### `rasterize_annotations`
- pipeline.py:L1084

### `collect_view_elements`
- pipeline.py:L1054

### `get_element_silhouette`
- pipeline.py:L2216
