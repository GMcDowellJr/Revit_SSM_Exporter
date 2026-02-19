# vop_interwoven — code map (authoritative)

## Scope
- Generated from the `vop_interwoven/` folder this script was run from.
- Deterministic listing of per-file imports and definitions (functions/classes/methods).

## Files

### `config.py`

**Imports**
- `math`
- `os`

**Definitions**
- `Config` (class, L12)
- `Config.__init__` (method, L58)
- `Config.compute_adaptive_tile_size` (method, L326)
- `Config.max_grid_cells_width` (method, L376)
- `Config.max_grid_cells_height` (method, L385)
- `Config.bounds_buffer_ft` (method, L394)
- `Config.silhouette_tiny_thresh_ft` (method, L403)
- `Config.silhouette_large_thresh_ft` (method, L412)
- `Config.coarse_tess_max_verts` (method, L421)
- `Config.get_silhouette_strategies` (method, L429)
- `Config.__repr__` (method, L463)
- `Config.to_dict` (method, L483)
- `Config.from_dict` (method, L542)

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
- `ElementFingerprint` (class, L14)
- `ElementFingerprint.__init__` (method, L34)
- `ElementFingerprint.to_signature_string` (method, L74)
- `ElementFingerprint.to_dict` (method, L106)
- `ElementFingerprint.from_dict` (method, L121)
- `ElementCache` (class, L139)
- `ElementCache.__init__` (method, L162)
- `ElementCache.get_or_create_fingerprint` (method, L174)
- `ElementCache.stats` (method, L239)
- `ElementCache.save_to_json` (method, L278)
- `ElementCache.load_from_json` (method, L329)
- `ElementCache.export_analysis_csv` (method, L386)
- `ElementCache.export_view_element_map_json` (method, L472)
- `ElementCache.detect_changes` (method, L549)

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
- `ViewRaster` (class, L187)
- `ViewRaster._cell_in_model_clip` (method, L244)
- `ViewRaster.rasterize_open_polylines` (method, L265)
- `ViewRaster.__init__` (method, L369)
- `ViewRaster._is_valid_cell` (method, L429)
- `ViewRaster.width` (method, L442)
- `ViewRaster.height` (method, L447)
- `ViewRaster.cell_size` (method, L452)
- `ViewRaster.bounds` (method, L457)
- `ViewRaster.get_cell_index` (method, L461)
- `ViewRaster.model_occ_mask` (method, L477)
- `ViewRaster.model_occ_mask` (method, L482)
- `ViewRaster.model_proxy_presence` (method, L486)
- `ViewRaster.model_proxy_presence` (method, L491)
- `ViewRaster.has_model_occ` (method, L494)
- `ViewRaster.has_model_edge` (method, L498)
- `ViewRaster.has_model_proxy` (method, L502)
- `ViewRaster.has_model_present` (method, L506)
- `ViewRaster.try_write_cell` (method, L537)
- `ViewRaster.get_or_create_element_meta_index` (method, L622)
- `ViewRaster.get_or_create_anno_meta_index` (method, L662)
- `ViewRaster.finalize_anno_over_model` (method, L680)
- `ViewRaster.stamp_model_edge_idx` (method, L721)
- `ViewRaster.stamp_proxy_edge_idx` (method, L759)
- `ViewRaster.rasterize_proxy_loops` (method, L789)
- `ViewRaster.rasterize_polygon_to_proxy` (method, L872)
- `ViewRaster.rasterize_polygon_to_anno` (method, L1006)
- `ViewRaster.rasterize_closed_loops_to_proxy_edges` (method, L1107)
- `ViewRaster.rasterize_open_polylines_to_proxy_edges` (method, L1195)
- `ViewRaster.rasterize_silhouette_loops` (method, L1250)
- `ViewRaster._scanline_cells` (method, L1531)
- `ViewRaster._scanline_fill` (method, L1641)
- `ViewRaster.dump_occlusion_debug` (method, L1737)
- `ViewRaster.to_dict` (method, L1842)
- `ViewRaster.from_dict` (method, L1884)
- `ViewRaster.to_debug_dict` (method, L1945)
- `_clip_poly_to_rect_uv` (function, L2007)
- `_bresenham_line` (function, L2071)

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
- `_to_host_point` (function, L1886)
- `_unwrap_elem` (function, L1908)
- `get_element_silhouette` (function, L1920)
- `_uv_obb_rect_silhouette` (function, L2351)
- `_bbox_silhouette` (function, L2363)
- `_obb_silhouette` (function, L2415)
- `_front_face_loops_silhouette` (function, L2476)
- `_silhouette_edges` (function, L2636)
- `_planar_face_loops_silhouette` (function, L2815)
- `_order_points_by_connectivity` (function, L3026)
- `_iter_solids` (function, L3082)
- `_convex_hull_2d` (function, L3122)

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
- `_round6` (function, L12)
- `_raster_to_dict_like` (function, L18)
- `_is_from_cache` (function, L55)
- `compute_external_cell_metrics` (function, L76)
- `compute_cell_metrics` (function, L145)
- `_normalize_locked_metrics_for_legacy_csv` (function, L255)
- `_get_metrics_triplet_from_view_result` (function, L293)
- `compute_annotation_type_metrics` (function, L302)
- `_coerce_view_id_int` (function, L357)
- `_viewtype_name_from_value` (function, L398)
- `_extract_view_unique_id` (function, L453)
- `extract_view_metadata` (function, L485)
- `compute_config_hash` (function, L802)
- `compute_view_frame_hash` (function, L834)
- `build_core_csv_row` (function, L856)
- `build_vop_csv_row` (function, L914)
- `build_occlusion_row` (function, L1139)
- `export_occlusion_diagnostics_csv` (function, L1166)
- `_get_manifest_csv_columns` (function, L1205)
- `_build_manifest_vop_row_from_metrics` (function, L1213)
- `export_pipeline_to_csv` (function, L1227)
- `get_core_csv_header` (function, L1574)
- `get_vop_csv_header` (function, L1585)
- `get_occlusion_csv_header` (function, L1600)
- `view_result_to_occlusion_row` (function, L1613)
- `get_perf_csv_header` (function, L1674)
- `view_result_to_core_row` (function, L1691)
- `view_result_to_vop_row` (function, L1825)
- `view_result_to_perf_row` (function, L2122)

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
- `StrategyDiagnostics.record_method_attempt` (method, L220)
- `StrategyDiagnostics.record_extraction_method` (method, L238)
- `StrategyDiagnostics._safe_rate` (method, L308)
- `StrategyDiagnostics.get_summary` (method, L321)
- `StrategyDiagnostics.print_summary` (method, L499)
- `StrategyDiagnostics.export_to_csv` (method, L657)
- `StrategyDiagnostics.export_category_summary_csv` (method, L710)

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
- `run_vop_pipeline_with_csv` (function, L441)
- `run_vop_pipeline_json` (function, L584)
- `get_test_config_tiny` (function, L618)
- `get_test_config_linear` (function, L634)
- `get_test_config_areal_heavy` (function, L650)
- `quick_test_current_view` (function, L667)

### `export/csv.py`

**Imports**
- `csv`
- `os`

**Definitions**
- `_ensure_dir` (function, L8)
- `_append_csv_rows` (function, L20)

### `metrics/final_state_scanner.py`

**Imports**
- `__future__:annotations`
- `typing:Any,Callable`

**Definitions**
- `_get_source_type` (function, L12)
- `_get_element_meta` (function, L33)
- `_normalize_anno_type` (function, L52)
- `_default_model_class_resolver` (function, L63)
- `scan_final_state_totals` (function, L82)

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

### `pipeline.py`

**Imports**
- `.config:Config`
- `.core.areal_extraction:extract_areal_geometry`
- `.core.geometry:Mode,classify_by_uv,make_uv_aabb,make_obb_or_skinny_aabb`
- `.core.math_utils:Bounds2D,CellRect`
- `.core.raster:ViewRaster,TileMap`
- `.core.silhouette:get_element_silhouette,reset_family_region_caches`
- `.diagnostics:OcclusionTracker`
- `.revit.annotation:rasterize_annotations`
- `.revit.collection:collect_view_elements,expand_host_link_import_model_elements,sort_front_to_back,is_element_visible_in_view,estimate_nearest_depth_from_bbox`
- `.revit.safe_api:safe_call`
- `.revit.view_basis:make_view_basis,resolve_view_bounds`
- `datetime:datetime`
- `math`
- `time`

**Definitions**
- `_diagnose_link_geometry_transform` (function, L120)
- `_perf_now` (function, L203)
- `_perf_ms` (function, L208)
- `_safe_int` (function, L211)
- `_safe_bool` (function, L218)
- `_cropbox_fingerprint` (function, L225)
- `_cfg_hash` (function, L254)
- `_view_signature` (function, L270)
- `_extract_view_identity_for_csv` (function, L381)
- `_compute_manifest_metrics_payload` (function, L495)
- `process_document_views` (function, L538)
- `init_view_raster` (function, L1471)
- `_extract_view_summary` (function, L1623)
- `rasterize_areal_loops` (function, L1661)
- `render_model_front_to_back` (function, L1776)
- `_is_supported_2d_view` (function, L3123)
- `_intersects_crop_volume` (function, L3176)
- `_should_skip_outside_view_volume` (function, L3202)
- `_tiles_fully_covered_and_nearer` (function, L3241)
- `_bin_elements_to_tiles` (function, L3279)
- `_tile_has_depth_conflict` (function, L3311)
- `_get_ambiguous_tiles` (function, L3345)
- `_render_proxy_element` (function, L3375)
- `_stamp_proxy_edges` (function, L3400)
- `_mark_rect_center_cell` (function, L3417)
- `_mark_thin_band_along_long_axis` (function, L3425)
- `export_view_raster` (function, L3443)

### `png_export.py`

**Imports**
- `os`
- `time`

**Definitions**
- `export_raster_to_png` (function, L10)
- `export_pipeline_results_to_pngs` (function, L334)

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
- `_stamp_detail_line_band` (function, L1394)
- `_point_in_quad` (function, L1466)
- `_uv_to_cell` (function, L1496)
- `_stamp_cell` (function, L1505)
- `_stamp_rect_outline` (function, L1512)
- `_stamp_line_cells` (function, L1532)
- `_rasterize_filled_region_shape` (function, L1553)
- `_project_element_bbox_to_cell_rect_for_anno` (function, L1725)

### `revit/collection.py`

**Imports**
- `math`

**Definitions**
- `resolve_element_bbox` (function, L10)
- `collect_view_elements` (function, L64)
- `is_element_visible_in_view` (function, L324)
- `expand_host_link_import_model_elements` (function, L348)
- `sort_front_to_back` (function, L502)
- `estimate_nearest_depth_from_bbox` (function, L520)
- `estimate_depth_from_loops_or_bbox` (function, L596)
- `estimate_depth_range_from_bbox` (function, L624)
- `_project_element_bbox_to_cell_rect` (function, L719)
- `_get_element_category_name` (function, L891)
- `_diagnose_coordinate_spaces` (function, L910)
- `_extract_geometry_footprint_uv` (function, L1137)
- `get_element_obb_loops` (function, L1412)
- `_pca_obb_uv` (function, L1676)

### `revit/collection_policy.py`

**Imports**
- `typing:Dict,Iterable,Optional,Set,Tuple`

**Definitions**
- `PolicyStats` (class, L23)
- `PolicyStats.__init__` (method, L26)
- `PolicyStats.mark_excluded` (method, L33)
- `PolicyStats.mark_included` (method, L40)
- `included_bic_names_for_source` (function, L136)
- `excluded_bic_names_global` (function, L144)
- `_try_import_bic` (function, L147)
- `_try_get_category_id` (function, L152)
- `resolve_category_ids` (function, L166)
- `should_include_element` (function, L190)

### `revit/linked_documents.py`

**Definitions**
- `_log` (function, L27)
- `LinkedElementProxy` (class, L32)
- `LinkedElementProxy.__init__` (method, L51)
- `LinkedElementProxy.get_BoundingBox` (method, L75)
- `LinkedElementProxy.get_Geometry` (method, L79)
- `collect_all_linked_elements` (function, L97)
- `_has_revit_2024_link_collector` (function, L143)
- `_collect_visible_link_elements_2024_plus` (function, L186)
- `_collect_from_revit_links` (function, L499)
- `_collect_from_dwg_imports` (function, L619)
- `_collect_link_elements_with_clipping` (function, L716)
- `_build_clip_volume` (function, L860)
- `_get_plan_view_vertical_range` (function, L981)
- `_build_crop_prism_corners` (function, L1040)
- `_get_host_visible_model_categories` (function, L1089)
- `_get_excluded_3d_category_ids` (function, L1133)
- `_transform_bbox_to_host` (function, L1142)

### `revit/safe_api.py`

**Imports**
- `sys`
- `typing:Any,Callable,Dict,Optional,TypeVar`

**Definitions**
- `_diag_fallback_stderr` (function, L9)
- `record_error` (function, L58)
- `record_warning` (function, L92)
- `safe_call` (function, L121)

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
- `resolve_view_w_volume` (function, L235)
- `xy_bounds_from_crop_box_all_corners` (function, L331)
- `xy_bounds_effective` (function, L398)
- `synthetic_bounds_from_visible_extents` (function, L444)
- `_bounds_to_tuple` (function, L726)
- `resolve_view_bounds` (function, L733)
- `_view_type_name` (function, L1163)
- `supports_model_geometry` (function, L1274)
- `supports_crop_bounds` (function, L1318)
- `supports_depth` (function, L1344)
- `resolve_view_mode` (function, L1358)
- `resolve_annotation_only_bounds` (function, L1411)

### `root_cache.py`

**Imports**
- `hashlib`
- `json`
- `os`
- `tempfile`
- `time`

**Definitions**
- `_round6` (function, L14)
- `RootStyleCache` (class, L20)
- `RootStyleCache.__init__` (method, L23)
- `RootStyleCache.load` (method, L55)
- `RootStyleCache.get_view` (method, L98)
- `RootStyleCache.get_view_any` (method, L129)
- `RootStyleCache.set_view` (method, L141)
- `RootStyleCache.save` (method, L228)
- `RootStyleCache.stats` (method, L270)
- `RootStyleCache._empty_cache` (method, L284)
- `compute_config_hash` (function, L295)
- `extract_metrics_from_view_result` (function, L319)

### `streaming.py`

**Imports**
- `datetime:datetime`
- `gc`
- `json`
- `os`
- `time`

**Definitions**
- `process_with_streaming` (function, L24)
- `StreamingExporter` (class, L100)
- `StreamingExporter.__init__` (method, L103)
- `StreamingExporter._init_csv_writers` (method, L184)
- `StreamingExporter.on_view_complete` (method, L253)
- `StreamingExporter._write_png` (method, L326)
- `StreamingExporter._write_csv_rows` (method, L354)
- `StreamingExporter._extract_summary` (method, L474)
- `StreamingExporter.finalize` (method, L487)
- `process_document_views_streaming` (function, L541)
- `run_vop_pipeline_streaming` (function, L659)

### `thinrunner_streaming.py`

**Imports**
- `os`
- `sys`
- `vop_interwoven.entry_dynamo:get_current_document,get_current_view`
