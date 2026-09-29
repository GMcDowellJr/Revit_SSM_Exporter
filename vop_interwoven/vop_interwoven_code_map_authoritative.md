# vop_interwoven — code map (authoritative)

## Scope
- Generated from the `vop_interwoven/` folder this script was run from.
- Deterministic listing of per-file imports and definitions (functions/classes/methods).

## Files

### `bootstrap.py`

**Imports**
- `subprocess`
- `sys`

**Definitions**
- `_is_importable` (function, L24)
- `_install` (function, L32)
- `run` (function, L48)

### `color_id_buffer.py`

**Imports**
- `.config:ANNO_CROP_MODES,ANNO_MODEL_SUPPRESSION_MODES`
- `.resolution_contract:DEFAULT_COLOR_ID_EXPORT_DPI,MAX_STAGE_A_AXIS_PX,cap_axes,frame_export_geometry`
- `copy`
- `json`
- `os`
- `struct`
- `time`

**Definitions**
- `_reserved_corner_count` (function, L122)
- `choose_step` (function, L136)
- `_hsv_to_rgb255` (function, L161)
- `_snap_to_lattice` (function, L193)
- `_is_reserved_corner` (function, L203)
- `build_palette` (function, L213)
- `resolve_all` (function, L308)
- `_split_expanded_elements` (function, L343)
- `_is_import_instance` (function, L399)
- `_partition_view_specific_imports` (function, L423)
- `_scan_view_specific_imports` (function, L465)
- `_unsuppressed_view_specific_imports` (function, L519)
- `_hide_view_specific_imports` (function, L530)
- `_unhide_view_specific_imports` (function, L601)
- `_read_back_view_specific_imports` (function, L614)
- `get_or_create_neutral_phase_filter` (function, L657)
- `_category_override_refused` (function, L686)
- `_category_is_overridable` (function, L699)
- `_get_solid_pattern_id` (function, L726)
- `_build_flat_color_ogs` (function, L735)
- `_category_id_int` (function, L751)
- `_resolve_colorable_category_predicate` (function, L786)
- `_dedupe_link_instances_by_document` (function, L872)
- `_model_categories_in_linked_doc` (function, L906)
- `_find_existing_parameter_filter` (function, L990)
- `_reused_filter_matches_category` (function, L998)
- `_collect_link_category_filters` (function, L1025)
- `_apply_link_category_filters` (function, L1076)
- `_finite_or_none` (function, L1202)
- `_collect_view_scoped_link_proxies` (function, L1217)
- `_near_face_w_category_name` (function, L1276)
- `_drop_not_visible_in_view` (function, L1310)
- `_capture_integrity` (function, L1368)
- `_write_sidecar` (function, L1393)
- `_round_ft` (function, L1415)
- `_round_geometry` (function, L1423)
- `_uv_rect_or_state` (function, L1438)
- `_bbox_transform_or_state` (function, L1450)
- `_link_bbox_transform_or_state` (function, L1466)
- `_plain_or_state` (function, L1488)
- `_host_source_state` (function, L1497)
- `_collect_near_face_w_data` (function, L1527)
- `_collect_annotation_bbox_data` (function, L1809)
- `_try_color_link_element_detailed` (function, L2017)
- `_hidden_category_state` (function, L2044)
- `_GsNotApplicable` (class, L2110)
- `_GsNotApplicable.__init__` (method, L2117)
- `_gs_value` (function, L2121)
- `_gs_not_applicable` (function, L2125)
- `_gs_unavailable` (function, L2129)
- `_gs_capture` (function, L2133)
- `_gs_element_name` (function, L2151)
- `_gs_element_ref` (function, L2162)
- `_capture_view_graphics_state` (function, L2173)
- `_element_id_ints` (function, L2508)
- `_build_phase_swap_audit` (function, L2521)
- `read_image_dimensions` (function, L2617)
- `normalize_applied_smooth_edges` (function, L2719)
- `_set_pixel_size_with_backoff` (function, L2732)
- `_fit_direction` (function, L2766)
- `_export_one_tiff` (function, L2784)
- `_export_tiff` (function, L2834)
- `compute_model_crop` (function, L3005)
- `export_color_id_buffer_view` (function, L3085)
- `_model_category_hidden_state` (function, L4833)
- `_override_is_cleared` (function, L4912)
- `_verify_annotation_overrides_restored` (function, L4980)
- `export_annotation_color_id_buffer_view` (function, L5034)

### `config.py`

**Imports**
- `.resolution_contract:DEFAULT_COLOR_ID_EXPORT_DPI`
- `math`
- `os`

**Definitions**
- `Config` (class, L21)
- `Config.__init__` (method, L67)
- `Config.compute_adaptive_tile_size` (method, L485)
- `Config.max_grid_cells_width` (method, L535)
- `Config.max_grid_cells_height` (method, L544)
- `Config.bounds_buffer_ft` (method, L553)
- `Config.silhouette_tiny_thresh_ft` (method, L562)
- `Config.silhouette_large_thresh_ft` (method, L571)
- `Config.coarse_tess_max_verts` (method, L580)
- `Config.get_silhouette_strategies` (method, L588)
- `Config.__repr__` (method, L622)
- `Config.to_dict` (method, L642)
- `Config.from_dict` (method, L725)

### `core/areal_extraction.py`

**Definitions**
- `_safe_elem_id` (function, L16)
- `_safe_category` (function, L34)
- `_get_aabb_loops_from_bbox` (function, L53)
- `extract_areal_geometry` (function, L140)

### `core/cache.py`

**Imports**
- `collections:OrderedDict`

**Definitions**
- `LRUCache` (class, L16)
- `LRUCache.__init__` (method, L24)
- `LRUCache.__len__` (method, L34)
- `LRUCache.get` (method, L37)
- `LRUCache.set` (method, L54)
- `LRUCache.clear` (method, L77)
- `LRUCache.stats` (method, L83)

### `core/diagnostics.py`

**Definitions**
- `_exc_to_str` (function, L3)
- `Diagnostics` (class, L10)
- `Diagnostics.__init__` (method, L20)
- `Diagnostics._count_key` (method, L34)
- `Diagnostics._record` (method, L38)
- `Diagnostics.debug` (method, L55)
- `Diagnostics.info` (method, L82)
- `Diagnostics.debug_dedupe` (method, L109)
- `Diagnostics.warn` (method, L168)
- `Diagnostics.error` (method, L194)
- `Diagnostics.to_dict` (method, L221)

### `core/element_cache.py`

**Imports**
- `collections:OrderedDict`
- `time`

**Definitions**
- `_normalize_source_type` (function, L14)
- `ElementFingerprint` (class, L26)
- `ElementFingerprint.__init__` (method, L46)
- `ElementFingerprint.to_signature_string` (method, L87)
- `ElementFingerprint.to_dict` (method, L119)
- `ElementFingerprint.from_dict` (method, L135)
- `ElementCache` (class, L154)
- `ElementCache.__init__` (method, L177)
- `ElementCache.get_or_create_fingerprint` (method, L189)
- `ElementCache.stats` (method, L262)
- `ElementCache.save_to_json` (method, L301)
- `ElementCache.load_from_json` (method, L352)
- `ElementCache.export_analysis_csv` (method, L411)
- `ElementCache.export_view_element_map_json` (method, L499)
- `ElementCache.detect_changes` (method, L576)

### `core/face_selection.py`

**Imports**
- `__future__:annotations`

**Definitions**
- `_to_xyz_tuple` (function, L25)
- `_dot` (function, L33)
- `_norm` (function, L37)
- `_normalize` (function, L41)
- `_sub` (function, L48)
- `_canonicalize_plane` (function, L52)
- `_plane_from_planar_face` (function, L76)
- `_plane_eq_close` (function, L98)
- `signed_polygon_area_2d` (function, L116)
- `polygon_area_2d` (function, L139)
- `iter_front_facing_planar_faces` (function, L147)
- `group_faces_by_plane` (function, L212)
- `projected_outer_loop_area_uv` (function, L265)
- `select_dominant_face_per_plane_group` (function, L321)
- `select_top_plane_groups` (function, L361)

### `core/footprint.py`

**Definitions**
- `CellRectFootprint` (class, L3)
- `CellRectFootprint.__init__` (method, L5)
- `CellRectFootprint.tiles` (method, L8)
- `CellRectFootprint.cells` (method, L12)
- `HullFootprint` (class, L15)
- `HullFootprint.__init__` (method, L20)
- `HullFootprint.tiles` (method, L31)
- `HullFootprint.cells` (method, L37)

### `core/geometry.py`

**Imports**
- `enum:Enum`

**Definitions**
- `Mode` (class, L11)
- `_mesh_vertex_count` (function, L23)
- `tier_a_is_ambiguous` (function, L52)
- `classify_by_uv_pca` (function, L67)
- `classify_by_uv` (function, L79)
- `UV_AABB` (class, L117)
- `UV_AABB.__init__` (method, L134)
- `UV_AABB.width` (method, L141)
- `UV_AABB.height` (method, L145)
- `UV_AABB.center` (method, L149)
- `UV_AABB.edges` (method, L153)
- `UV_AABB.__repr__` (method, L168)
- `OBB` (class, L172)
- `OBB.__init__` (method, L195)
- `OBB.long_axis_length` (method, L201)
- `OBB.short_axis_length` (method, L205)
- `OBB.corners` (method, L209)
- `OBB.edges` (method, L225)
- `OBB.__repr__` (method, L237)
- `make_uv_aabb` (function, L241)
- `make_obb_or_skinny_aabb` (function, L267)

### `core/geometry_cache.py`

**Imports**
- `json`
- `os`
- `time`

**Definitions**
- `_encode_key` (function, L14)
- `_decode_key` (function, L19)
- `_entry_bbox_fingerprints` (function, L35)
- `GeometryCache` (class, L68)
- `GeometryCache.__init__` (method, L84)
- `GeometryCache.hits` (method, L101)
- `GeometryCache.misses` (method, L105)
- `GeometryCache.disk_hits` (method, L109)
- `GeometryCache.get` (method, L112)
- `GeometryCache.set` (method, L123)
- `GeometryCache.load` (method, L134)
- `GeometryCache.save` (method, L198)
- `GeometryCache.stats` (method, L236)

### `core/hull.py`

**Definitions**
- `convex_hull_uv` (function, L1)

### `core/math_utils.py`

**Definitions**
- `Bounds2D` (class, L8)
- `Bounds2D.__init__` (method, L22)
- `Bounds2D.width` (method, L28)
- `Bounds2D.height` (method, L32)
- `Bounds2D.area` (method, L36)
- `Bounds2D.contains_point` (method, L40)
- `Bounds2D.intersects` (method, L44)
- `Bounds2D.expand` (method, L52)
- `Bounds2D.__repr__` (method, L58)
- `CellRect` (class, L62)
- `CellRect.__init__` (method, L79)
- `CellRect.cells` (method, L90)
- `CellRect.cell_count` (method, L96)
- `CellRect.width` (method, L102)
- `CellRect.height` (method, L105)
- `CellRect.center_cell` (method, L108)
- `CellRect.__repr__` (method, L114)
- `cellrect_dims` (function, L117)
- `rect_intersects_bounds` (function, L161)
- `clamp` (function, L185)
- `point_in_rect` (function, L190)

### `core/pca2d.py`

**Imports**
- `math`

**Definitions**
- `pca_oriented_extents_uv` (function, L3)

### `core/raster.py`

**Definitions**
- `_extract_source_type` (function, L8)
- `_cell_in_model_clip` (function, L26)
- `TileMap` (class, L45)
- `TileMap.__init__` (method, L64)
- `TileMap.get_tile_index` (method, L82)
- `TileMap.get_tiles_for_rect` (method, L96)
- `TileMap.is_tile_full` (method, L117)
- `TileMap.update_filled_count` (method, L129)
- `TileMap.update_w_min` (method, L140)
- `_fix_loop_points_uv` (function, L154)
- `_polygon_mask_np` (function, L187)
- `decompose_to_rects` (function, L253)
- `_commit_polygon_mask` (function, L306)
- `ViewRaster` (class, L352)
- `ViewRaster._cell_in_model_clip` (method, L409)
- `ViewRaster.rasterize_open_polylines` (method, L430)
- `ViewRaster.__init__` (method, L536)
- `ViewRaster._is_valid_cell` (method, L607)
- `ViewRaster.width` (method, L620)
- `ViewRaster.height` (method, L625)
- `ViewRaster.cell_size` (method, L630)
- `ViewRaster.bounds` (method, L635)
- `ViewRaster.get_cell_index` (method, L639)
- `ViewRaster.model_occ_mask` (method, L655)
- `ViewRaster.model_occ_mask` (method, L660)
- `ViewRaster.model_proxy_presence` (method, L664)
- `ViewRaster.model_proxy_presence` (method, L669)
- `ViewRaster.has_model_occ` (method, L672)
- `ViewRaster.has_model_edge` (method, L676)
- `ViewRaster.has_model_proxy` (method, L680)
- `ViewRaster.has_model_present` (method, L688)
- `ViewRaster.try_write_cell` (method, L719)
- `ViewRaster.get_or_create_element_meta_index` (method, L804)
- `ViewRaster.get_or_create_anno_meta_index` (method, L844)
- `ViewRaster.finalize_anno_over_model` (method, L862)
- `ViewRaster.stamp_model_edge_idx` (method, L903)
- `ViewRaster.stamp_proxy_edge_idx` (method, L941)
- `ViewRaster.rasterize_proxy_loops` (method, L972)
- `ViewRaster.rasterize_polygon_to_proxy` (method, L1059)
- `ViewRaster.rasterize_polygon_to_anno` (method, L1234)
- `ViewRaster.rasterize_closed_loops_to_proxy_edges` (method, L1335)
- `ViewRaster.rasterize_open_polylines_to_proxy_edges` (method, L1423)
- `ViewRaster._model_clip_mask_np` (method, L1478)
- `ViewRaster.rasterize_silhouette_loops` (method, L1507)
- `ViewRaster._scanline_cells` (method, L1690)
- `ViewRaster._scanline_fill` (method, L1800)
- `ViewRaster.dump_occlusion_debug` (method, L1896)
- `ViewRaster.to_dict` (method, L2001)
- `ViewRaster.from_dict` (method, L2053)
- `ViewRaster.to_debug_dict` (method, L2145)
- `_clip_poly_to_rect_uv` (function, L2207)
- `_bresenham_line` (function, L2271)

### `core/silhouette.py`

**Imports**
- `math`

**Definitions**
- `reset_family_region_caches` (function, L53)
- `_compose_transform` (function, L70)
- `_collect_regions_recursive` (function, L84)
- `_safe_int_id` (function, L419)
- `_xyz_tuple` (function, L429)
- `_apply_transform_xyz_tuple` (function, L435)
- `_cache_get` (function, L447)
- `_cache_set` (function, L455)
- `_maybe_resize_lru` (function, L464)
- `_family_region_outlines_cached` (function, L477)
- `_bbox_corners_world` (function, L642)
- `_pca_obb_uv` (function, L682)
- `_uv_obb_rect_from_bbox` (function, L748)
- `_determine_uv_mode` (function, L777)
- `_location_curve_obb_silhouette` (function, L821)
- `_detail_line_band_silhouette` (function, L873)
- `_symbolic_curves_silhouette` (function, L1034)
- `_iter_curve_primitives` (function, L1353)
- `_merge_paths_by_endpoints` (function, L1473)
- `_cad_curves_silhouette` (function, L1546)
- `_to_host_point` (function, L1889)
- `_unwrap_elem` (function, L1911)
- `_is_dwg_import_element` (function, L1923)
- `get_element_silhouette` (function, L1972)
- `_uv_obb_rect_silhouette` (function, L2383)
- `_bbox_silhouette` (function, L2395)
- `_obb_silhouette` (function, L2447)
- `_front_face_loops_silhouette` (function, L2508)
- `_silhouette_edges` (function, L2679)
- `_planar_face_loops_silhouette` (function, L2858)
- `_order_points_by_connectivity` (function, L3079)
- `_iter_solids` (function, L3135)
- `_convex_hull_2d` (function, L3175)

### `core/source_identity.py`

**Definitions**
- `make_source_identity` (function, L23)

### `csv_export.py`

**Imports**
- `csv`
- `datetime:datetime`
- `hashlib`
- `os`

**Definitions**
- `_safe_seq` (function, L13)
- `_round6` (function, L19)
- `_normalize_locked_metrics_for_legacy_csv` (function, L27)
- `_has_legacy_partition_metrics` (function, L86)
- `_has_locked_partition_metrics` (function, L92)
- `_raster_to_dict_like` (function, L95)
- `_get_metrics_triplet_from_view_result` (function, L137)
- `_is_from_cache` (function, L180)
- `compute_external_cell_metrics` (function, L201)
- `compute_cell_metrics` (function, L284)
- `_compute_cell_metrics_np` (function, L299)
- `_compute_cell_metrics_py` (function, L371)
- `compute_annotation_type_metrics` (function, L451)
- `_coerce_view_id_int` (function, L506)
- `_viewtype_name_from_value` (function, L547)
- `_extract_view_unique_id` (function, L602)
- `extract_view_metadata` (function, L634)
- `get_stage_a_core_csv_header` (function, L968)
- `stage_a_capture_status` (function, L972)
- `stage_a_view_result_to_core_row` (function, L991)
- `compute_config_hash` (function, L1033)
- `compute_view_frame_hash` (function, L1074)
- `build_core_csv_row` (function, L1096)
- `build_vop_csv_row` (function, L1155)
- `build_occlusion_row` (function, L1380)
- `export_occlusion_diagnostics_csv` (function, L1407)
- `_get_manifest_csv_columns` (function, L1446)
- `_build_manifest_vop_row_from_metrics` (function, L1454)
- `export_pipeline_to_csv` (function, L1468)
- `get_core_csv_header` (function, L1819)
- `get_vop_csv_header` (function, L1830)
- `get_occlusion_csv_header` (function, L1845)
- `view_result_to_occlusion_row` (function, L1858)
- `get_perf_csv_header` (function, L1919)
- `view_result_to_core_row` (function, L1938)
- `view_result_to_vop_row` (function, L2083)
- `view_result_to_perf_row` (function, L2431)

### `diagnostics/occlusion_tracker.py`

**Definitions**
- `OcclusionTracker` (class, L4)
- `OcclusionTracker.__init__` (method, L7)
- `OcclusionTracker.record_element` (method, L33)
- `OcclusionTracker.record_bbox_rejection` (method, L36)
- `OcclusionTracker.record_partial_occlusion` (method, L39)
- `OcclusionTracker.record_occlusion_test_ms` (method, L51)
- `OcclusionTracker.check_saturation` (method, L57)
- `OcclusionTracker.finalize` (method, L73)
- `OcclusionTracker.as_dict` (method, L115)

### `diagnostics/strategy_tracker.py`

**Imports**
- `collections:defaultdict`

**Definitions**
- `StrategyDiagnostics` (class, L26)
- `StrategyDiagnostics.__init__` (method, L34)
- `StrategyDiagnostics.record_element_classification` (method, L87)
- `StrategyDiagnostics.record_areal_strategy` (method, L118)
- `StrategyDiagnostics.record_geometry_extraction` (method, L160)
- `StrategyDiagnostics.record_confidence` (method, L194)
- `StrategyDiagnostics.record_method_attempt` (method, L227)
- `StrategyDiagnostics.record_extraction_method` (method, L245)
- `StrategyDiagnostics._safe_rate` (method, L315)
- `StrategyDiagnostics.get_summary` (method, L328)
- `StrategyDiagnostics.print_summary` (method, L506)
- `StrategyDiagnostics.export_to_csv` (method, L664)
- `StrategyDiagnostics.export_category_summary_csv` (method, L717)

### `dynamo_helpers.py`

**Imports**
- `vop_interwoven.config:Config`
- `vop_interwoven.entry_dynamo:run_vop_pipeline_with_png,get_current_document,get_current_view`

**Definitions**
- `get_views_from_input_or_current` (function, L15)
- `get_all_views_in_model` (function, L72)
- `get_all_floor_plans` (function, L101)
- `get_all_sections` (function, L129)
- `filter_supported_views` (function, L154)
- `run_pipeline_from_dynamo_input` (function, L227)

### `entry_dynamo.py`

**Imports**
- `copy`
- `csv`
- `datetime:datetime`
- `json`
- `time`

**Definitions**
- `_prune_view_raster_for_json` (function, L57)
- `_pipeline_result_for_json` (function, L91)
- `get_current_document` (function, L180)
- `get_current_view` (function, L216)
- `_normalize_view_ids` (function, L252)
- `run_vop_pipeline` (function, L306)
- `run_vop_pipeline_with_png` (function, L354)
- `run_vop_pipeline_with_csv` (function, L451)
- `run_vop_pipeline_json` (function, L604)
- `get_test_config_tiny` (function, L638)
- `get_test_config_linear` (function, L654)
- `get_test_config_areal_heavy` (function, L670)
- `quick_test_current_view` (function, L687)

### `export/csv.py`

**Imports**
- `csv`
- `os`

**Definitions**
- `_ensure_dir` (function, L8)
- `_append_csv_rows` (function, L20)

### `memory_telemetry.py`

**Imports**
- `csv`
- `datetime:datetime`
- `time`

**Definitions**
- `_get_memory_mb` (function, L8)
- `_force_clr_gc` (function, L21)
- `MemoryTracker` (class, L39)
- `MemoryTracker.__init__` (method, L40)
- `MemoryTracker.mark` (method, L45)
- `MemoryTracker.mark_and_gc` (method, L69)
- `MemoryTracker.delta` (method, L77)
- `MemoryTracker.report` (method, L97)
- `MemoryTracker.export_csv` (method, L121)
- `MemoryTracker.to_dict` (method, L133)

### `metrics/final_state_scanner.py`

**Imports**
- `__future__:annotations`
- `typing:Any,Callable`

**Definitions**
- `_safe_seq` (function, L12)
- `_get_source_type` (function, L18)
- `_get_element_meta` (function, L39)
- `_normalize_anno_type` (function, L58)
- `_default_model_class_resolver` (function, L69)
- `scan_final_state_totals` (function, L88)

### `metrics/manifest_evaluator.py`

**Imports**
- `__future__:annotations`
- `typing:Any`

**Definitions**
- `MetricsValidationError` (class, L8)
- `_family_sum` (function, L50)
- `_eval_expr` (function, L55)
- `evaluate_metrics_manifest` (function, L118)

### `metrics_manifest.py`

**Imports**
- `__future__:annotations`
- `dataclasses:dataclass`
- `hashlib`
- `json`
- `pathlib:Path`
- `typing:Any`

**Definitions**
- `ManifestValidationError` (class, L21)
- `MetricsManifest` (class, L26)
- `ManifestLoader` (class, L35)
- `ManifestLoader.load` (method, L39)
- `_ensure_object` (function, L43)
- `_ensure_string` (function, L49)
- `_ensure_string_list` (function, L57)
- `validate_manifest_schema_version` (function, L65)
- `validate_manifest_top_level_keys` (function, L71)
- `validate_requires_block` (function, L80)
- `validate_families_block` (function, L90)
- `validate_invariants_block` (function, L103)
- `validate_outputs_block` (function, L127)
- `canonicalize_manifest_json` (function, L138)
- `compute_manifest_sha256` (function, L144)
- `load_manifest_json` (function, L148)

### `np_backend.py`

**Definitions**
- `ensure_numpy` (function, L30)
- `ensure_pillow` (function, L60)

### `perf_constants.py`

### `pipeline.py`

**Imports**
- `.config:Config`
- `.core.areal_extraction:extract_areal_geometry`
- `.core.geometry:Mode,classify_by_uv,make_uv_aabb,make_obb_or_skinny_aabb`
- `.core.math_utils:Bounds2D,CellRect`
- `.core.raster:ViewRaster,TileMap,decompose_to_rects`
- `.core.silhouette:get_element_silhouette,reset_family_region_caches`
- `.diagnostics:OcclusionTracker`
- `.memory_telemetry:MemoryTracker`
- `.perf_constants:TIMING_KEYS,METRIC_KEYS`
- `.revit.annotation:rasterize_annotations`
- `.revit.collection:collect_view_elements,expand_host_link_import_model_elements,sort_front_to_back,is_element_visible_in_view,estimate_nearest_depth_from_bbox`
- `.revit.safe_api:safe_call`
- `.revit.view_basis:make_view_basis,resolve_view_bounds`
- `datetime:datetime`
- `math`
- `time`

**Definitions**
- `_diagnose_link_geometry_transform` (function, L114)
- `_stage_a_view_diagnostics` (function, L197)
- `_perf_now` (function, L223)
- `_perf_ms` (function, L228)
- `_safe_int` (function, L231)
- `_safe_bool` (function, L238)
- `_cropbox_fingerprint` (function, L245)
- `_cfg_hash` (function, L274)
- `_view_signature` (function, L290)
- `_extract_view_identity_for_csv` (function, L436)
- `_compute_manifest_metrics_payload` (function, L550)
- `process_document_views` (function, L593)
- `init_view_raster` (function, L1709)
- `_extract_view_summary` (function, L1877)
- `rasterize_areal_loops` (function, L1915)
- `_make_areal_geom_cache_key` (function, L2071)
- `_reconstruct_areal_low_conf_loops` (function, L2085)
- `_quantize_view_dir` (function, L2181)
- `_make_areal_high_conf_cache_key` (function, L2203)
- `_reconstruct_areal_high_conf_loops` (function, L2226)
- `render_model_front_to_back` (function, L2269)
- `_is_supported_2d_view` (function, L3846)
- `_intersects_crop_volume` (function, L3899)
- `_should_skip_outside_view_volume` (function, L3925)
- `_tiles_fully_covered_and_nearer` (function, L3964)
- `_bin_elements_to_tiles` (function, L4002)
- `_tile_has_depth_conflict` (function, L4034)
- `_get_ambiguous_tiles` (function, L4068)
- `_render_proxy_element` (function, L4098)
- `_stamp_proxy_edges` (function, L4123)
- `_mark_rect_center_cell` (function, L4140)
- `_mark_thin_band_along_long_axis` (function, L4148)
- `export_view_raster` (function, L4166)

### `png_export.py`

**Imports**
- `os`
- `time`

**Definitions**
- `_safe_seq` (function, L10)
- `_export_png_dotnet` (function, L16)
- `_export_png_pillow` (function, L341)
- `export_raster_to_png` (function, L435)
- `export_pipeline_results_to_pngs` (function, L457)

### `resolution_contract.py`

**Imports**
- `math`

**Definitions**
- `round_half_up_positive` (function, L34)
- `_positive` (function, L42)
- `effective_export_dpi` (function, L48)
- `cap_axes` (function, L111)
- `feet_per_pixel_at_dpi` (function, L241)
- `_extent` (function, L260)
- `_crop_fit_extent` (function, L282)
- `_lattice_ceil` (function, L288)
- `frame_export_geometry` (function, L300)

### `revit/annotation.py`

**Definitions**
- `_is_excluded_from_extent_expansion` (function, L12)
- `is_extent_driver_annotation` (function, L47)
- `compute_annotation_extents` (function, L123)
- `collect_2d_annotations` (function, L452)
- `classify_annotation` (function, L740)
- `classify_keynote` (function, L820)
- `get_annotation_bbox` (function, L872)
- `rasterize_annotations` (function, L903)
- `_stamp_detail_line_band` (function, L1414)
- `_point_in_quad` (function, L1486)
- `_uv_to_cell` (function, L1516)
- `_stamp_cell` (function, L1525)
- `_stamp_rect_outline` (function, L1532)
- `_stamp_line_cells` (function, L1552)
- `_rasterize_filled_region_shape` (function, L1573)
- `_project_element_bbox_to_cell_rect_for_anno` (function, L1745)
- `stage_a_datum_category_ids` (function, L1947)
- `_element_category_id_int` (function, L1988)
- `_invalid_element_id_int` (function, L1996)
- `stage_a_pass_membership` (function, L2005)
- `split_stage_a_pass_membership` (function, L2092)
- `stage_a_pass_membership_summary` (function, L2189)
- `_stage_a_element_id_int` (function, L2220)

### `revit/collection.py`

**Imports**
- `math`

**Definitions**
- `resolve_element_bbox` (function, L10)
- `collect_view_elements` (function, L64)
- `is_element_visible_in_view` (function, L306)
- `expand_host_link_import_model_elements` (function, L330)
- `sort_front_to_back` (function, L501)
- `estimate_nearest_depth_from_bbox` (function, L519)
- `estimate_depth_from_loops_or_bbox` (function, L595)
- `estimate_depth_range_from_bbox` (function, L623)
- `_bbox_world_corners` (function, L752)
- `project_bbox_corners_uv` (function, L843)
- `project_bbox_uv_and_near_face_w` (function, L902)
- `bbox_world_aabb` (function, L965)
- `_transform_parts` (function, L1034)
- `_compose_parts` (function, L1046)
- `_basis_is_axis_aligned` (function, L1058)
- `bbox_oriented_transform` (function, L1065)
- `_project_element_bbox_to_cell_rect` (function, L1105)
- `_get_element_category_name` (function, L1277)
- `_diagnose_coordinate_spaces` (function, L1296)
- `_extract_geometry_footprint_uv` (function, L1523)
- `get_element_obb_loops` (function, L1798)
- `_pca_obb_uv` (function, L2062)

### `revit/collection_policy.py`

**Imports**
- `typing:Dict,Iterable,Optional,Set,Tuple`

**Definitions**
- `PolicyStats` (class, L32)
- `PolicyStats.__init__` (method, L35)
- `PolicyStats.mark_excluded` (method, L42)
- `PolicyStats.mark_included` (method, L49)
- `view_specific_import_state` (function, L212)
- `annotation_included_bic_names` (function, L245)
- `excluded_bic_names_global` (function, L255)
- `_try_import_bic` (function, L259)
- `_try_get_category_id` (function, L265)
- `resolve_category_ids` (function, L280)
- `_try_get_category_type_model` (function, L304)
- `should_include_element` (function, L313)

### `revit/linked_documents.py`

**Definitions**
- `_log` (function, L27)
- `LinkedElementProxy` (class, L32)
- `LinkedElementProxy.__init__` (method, L51)
- `LinkedElementProxy.element` (method, L76)
- `LinkedElementProxy.get_BoundingBox` (method, L80)
- `LinkedElementProxy.get_Geometry` (method, L84)
- `LinkCollectionStatus` (class, L102)
- `LinkCollectionStatus.__init__` (method, L146)
- `LinkCollectionStatus.mark_incomplete` (method, L151)
- `LinkCollectionStatus.record_presence` (method, L155)
- `collect_all_linked_elements` (function, L169)
- `_has_revit_2024_link_collector` (function, L256)
- `_collect_visible_link_elements_2024_plus` (function, L299)
- `_collect_from_revit_links` (function, L650)
- `_collect_from_dwg_imports` (function, L819)
- `_collect_link_elements_with_clipping` (function, L976)
- `_build_clip_volume` (function, L1194)
- `_get_plan_view_vertical_range` (function, L1315)
- `_build_crop_prism_corners` (function, L1374)
- `_get_host_visible_model_categories` (function, L1423)
- `_get_excluded_3d_category_ids` (function, L1467)
- `_transform_bbox_to_host` (function, L1476)

### `revit/safe_api.py`

**Imports**
- `sys`
- `typing:Any,Callable,Dict,Optional,TypeVar`

**Definitions**
- `_diag_fallback_stderr` (function, L9)
- `record_error` (function, L58)
- `record_warning` (function, L92)
- `safe_call` (function, L121)
- `element_id_value` (function, L163)

### `revit/tierb_proxy.py`

**Imports**
- `Autodesk.Revit.DB:Options,Solid,GeometryInstance`

**Definitions**
- `sample_element_uvw_points` (function, L4)
- `_sample_geom_object` (function, L35)

### `revit/view_basis.py`

**Definitions**
- `ViewBasis` (class, L9)
- `ViewBasis.__init__` (method, L30)
- `ViewBasis.is_plan_like` (method, L36)
- `ViewBasis.is_elevation_like` (method, L44)
- `ViewBasis.transform_to_view_uv` (method, L52)
- `ViewBasis.transform_to_view_uvw` (method, L77)
- `ViewBasis.world_to_view_local` (method, L101)
- `ViewBasis.__repr__` (method, L111)
- `world_to_view` (function, L115)
- `make_view_basis` (function, L133)
- `resolve_view_w_volume` (function, L245)
- `xy_bounds_from_crop_box_all_corners` (function, L341)
- `crop_box_from_uv_bounds` (function, L409)
- `xy_bounds_effective` (function, L459)
- `synthetic_bounds_from_visible_extents` (function, L505)
- `_bounds_to_tuple` (function, L787)
- `resolve_view_bounds` (function, L794)
- `_view_type_name` (function, L1252)
- `supports_model_geometry` (function, L1363)
- `supports_crop_bounds` (function, L1407)
- `supports_depth` (function, L1433)
- `resolve_view_mode` (function, L1447)
- `resolve_annotation_only_bounds` (function, L1500)

### `root_cache.py`

**Imports**
- `hashlib`
- `json`
- `os`
- `tempfile`
- `time`

**Definitions**
- `_safe_seq` (function, L14)
- `_round6` (function, L19)
- `RootStyleCache` (class, L25)
- `RootStyleCache.__init__` (method, L28)
- `RootStyleCache.load` (method, L60)
- `RootStyleCache.get_view` (method, L103)
- `RootStyleCache.get_view_any` (method, L134)
- `RootStyleCache.set_view` (method, L146)
- `RootStyleCache.save` (method, L233)
- `RootStyleCache.stats` (method, L275)
- `RootStyleCache._empty_cache` (method, L289)
- `compute_config_hash` (function, L300)
- `extract_metrics_from_view_result` (function, L324)

### `run_meta.py`

**Imports**
- `json`
- `os`

**Definitions**
- `_value` (function, L27)
- `_unavailable` (function, L31)
- `_read` (function, L35)
- `git_commit` (function, L42)
- `revit_version` (function, L78)
- `build_run_meta` (function, L90)
- `_view_id_int` (function, L113)
- `finalize_run_meta` (function, L123)
- `merge_run_metas` (function, L139)
- `write_run_meta` (function, L162)

### `stage_a_registered_capture.py`

**Imports**
- `.revit.safe_api:element_id_value`
- `copy`
- `time`

**Definitions**
- `_read` (function, L57)
- `_xyz` (function, L66)
- `view_state` (function, L71)
- `view_state_verdict` (function, L85)
- `_non_blank_override_ids` (function, L103)
- `mark_reference_rectangle` (function, L117)
- `nominal_fpp_ft` (function, L148)
- `_registration_payload` (function, L155)
- `export_registered_stage_a_view` (function, L182)

### `stage_a_registration.py`

**Imports**
- `.revit.safe_api:element_id_value`
- `json`
- `time`

**Definitions**
- `_value` (function, L30)
- `_unavailable` (function, L34)
- `_xyz_tuple` (function, L38)
- `registration_mark_segments` (function, L88)
- `missing_override_setters` (function, L177)
- `white_override_capability_record` (function, L189)
- `white_override_capability` (function, L220)
- `flat_colour_override` (function, L258)
- `white_membership_suppression` (function, L308)
- `detail_line_ids` (function, L437)
- `hide_in_view` (function, L468)
- `show_in_view` (function, L499)
- `still_hidden` (function, L515)
- `_lines_category_hidden` (function, L528)
- `_thinnest_line_style` (function, L541)
- `create_registration_marks` (function, L584)
- `marks_still_in_project` (function, L695)
- `annotate_sidecar` (function, L709)
- `complete_capture_integrity` (function, L732)
- `discover_link_categories` (function, L766)

### `streaming.py`

**Imports**
- `datetime:datetime`
- `gc`
- `json`
- `os`
- `time`
- `vop_interwoven.memory_telemetry:MemoryTracker`

**Definitions**
- `process_with_streaming` (function, L25)
- `_stage_a_annotation_summary` (function, L115)
- `_stage_a_registration_summary` (function, L143)
- `StreamingExporter` (class, L162)
- `StreamingExporter.__init__` (method, L165)
- `StreamingExporter._init_csv_writers` (method, L292)
- `StreamingExporter.on_view_complete` (method, L371)
- `StreamingExporter._write_png` (method, L514)
- `StreamingExporter._write_view_raster` (method, L542)
- `StreamingExporter._write_stage_a_core_row` (method, L612)
- `StreamingExporter._write_csv_rows` (method, L626)
- `StreamingExporter._extract_summary` (method, L765)
- `StreamingExporter.finalize` (method, L778)
- `process_document_views_streaming` (function, L848)
- `run_vop_pipeline_streaming` (function, L1139)

### `thinrunner_streaming.py`

**Imports**
- `ctypes`
- `json`
- `os`
- `shutil`
- `sys`
- `vop_interwoven.entry_dynamo:get_current_document,get_current_view`

**Definitions**
- `_to_sequence` (function, L51)
- `_resolve_view_object` (function, L86)
- `_build_views_from_input` (function, L136)
- `_coerce_view_id` (function, L152)
- `_safe_level_elevation` (function, L195)
- `sort_views_by_level` (function, L226)
- `_chunk_list` (function, L301)
- `_append_csv` (function, L307)
- `_run_gc_between_chunks` (function, L328)
- `_describe_suppression` (function, L342)
- `_summarize_stage_a_sidecar` (function, L362)
- `_relocate_batch_stage_a_outputs` (function, L414)

### `view_raster_export.py`

**Imports**
- `os`
- `time`

**Definitions**
- `_snapshot` (function, L32)
- `_canonical_name` (function, L40)
- `_is_annotation_only_view` (function, L46)
- `_dot` (function, L66)
- `_crop_uv_frame` (function, L70)
- `_grid_origin_uv` (function, L112)
- `_prepare_view_for_export` (function, L142)
- `export_view_image` (function, L405)
- `_resize_to_exact` (function, L541)
- `export_pipeline_views_to_pngs` (function, L616)
