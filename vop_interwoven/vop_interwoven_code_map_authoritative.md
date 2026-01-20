# vop_interwoven — code map (authoritative)

## Scope
- Generated from the `vop_interwoven/` folder this script was run from.
- Deterministic listing of per-file imports and definitions (functions/classes/methods).

## Files

### `config.py`

**Imports**
- `math`

**Definitions**
- `Config` (class, L11)
- `Config.__init__` (method, L57)
- `Config.compute_adaptive_tile_size` (method, L287)
- `Config.max_grid_cells_width` (method, L337)
- `Config.max_grid_cells_height` (method, L346)
- `Config.bounds_buffer_ft` (method, L355)
- `Config.silhouette_tiny_thresh_ft` (method, L364)
- `Config.silhouette_large_thresh_ft` (method, L373)
- `Config.coarse_tess_max_verts` (method, L382)
- `Config.get_silhouette_strategies` (method, L390)
- `Config.__repr__` (method, L424)
- `Config.to_dict` (method, L444)
- `Config.from_dict` (method, L491)

### `core/cache.py`

**Imports**
- `collections:OrderedDict`

**Definitions**
- `LRUCache` (class, L16)
- `LRUCache.__init__` (method, L24)
- `LRUCache.__len__` (method, L34)
- `LRUCache.get` (method, L37)
- `LRUCache.set` (method, L54)
- `LRUCache.clear` (method, L76)
- `LRUCache.stats` (method, L82)

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
- `Diagnostics.warn` (method, L162)
- `Diagnostics.error` (method, L188)
- `Diagnostics.to_dict` (method, L215)

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
- `ElementCache.detect_changes` (method, L470)

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
- `group_faces_by_plane` (function, L207)
- `projected_outer_loop_area_uv` (function, L260)
- `select_dominant_face_per_plane_group` (function, L316)
- `select_top_plane_groups` (function, L356)

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
- `mark_rect_center_cell` (function, L333)
- `mark_thin_band_along_long_axis` (function, L356)

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
- `ViewRaster.stamp_model_edge_idx` (method, L716)
- `ViewRaster.stamp_proxy_edge_idx` (method, L748)
- `ViewRaster.rasterize_proxy_loops` (method, L772)
- `ViewRaster.rasterize_silhouette_loops` (method, L855)
- `ViewRaster._scanline_cells` (method, L1090)
- `ViewRaster._scanline_fill` (method, L1163)
- `ViewRaster.dump_occlusion_debug` (method, L1243)
- `ViewRaster.to_dict` (method, L1348)
- `ViewRaster.from_dict` (method, L1390)
- `ViewRaster.to_debug_dict` (method, L1446)
- `_clip_poly_to_rect_uv` (function, L1501)
- `_bresenham_line` (function, L1565)

### `core/silhouette.py`

**Imports**
- `math`

**Definitions**
- `_compose_transform` (function, L54)
- `_collect_regions_recursive` (function, L67)
- `_safe_int_id` (function, L311)
- `_xyz_tuple` (function, L320)
- `_apply_transform_xyz_tuple` (function, L326)
- `_cache_get` (function, L338)
- `_cache_set` (function, L346)
- `_maybe_resize_lru` (function, L355)
- `_family_region_outlines_cached` (function, L368)
- `_bbox_corners_world` (function, L485)
- `_pca_obb_uv` (function, L525)
- `_uv_obb_rect_from_bbox` (function, L591)
- `_determine_uv_mode` (function, L620)
- `_location_curve_obb_silhouette` (function, L664)
- `_detail_line_band_silhouette` (function, L709)
- `_symbolic_curves_silhouette` (function, L853)
- `_iter_curve_primitives` (function, L1058)
- `_merge_paths_by_endpoints` (function, L1172)
- `_cad_curves_silhouette` (function, L1245)
- `_to_host_point` (function, L1559)
- `_unwrap_elem` (function, L1581)
- `get_element_silhouette` (function, L1593)
- `_uv_obb_rect_silhouette` (function, L1914)
- `_bbox_silhouette` (function, L1926)
- `_obb_silhouette` (function, L1978)
- `_front_face_loops_silhouette` (function, L2039)
- `_silhouette_edges` (function, L2199)
- `_planar_face_loops_silhouette` (function, L2377)
- `_order_points_by_connectivity` (function, L2538)
- `_iter_solids` (function, L2594)
- `_convex_hull_2d` (function, L2634)

### `core/source_identity.py`

**Definitions**
- `make_source_identity` (function, L23)

### `csv_export.py`

**Imports**
- `datetime:datetime`
- `hashlib`
- `os`

**Definitions**
- `_round6` (function, L11)
- `_is_from_cache` (function, L17)
- `compute_external_cell_metrics` (function, L37)
- `compute_cell_metrics` (function, L106)
- `compute_annotation_type_metrics` (function, L216)
- `_coerce_view_id_int` (function, L271)
- `_viewtype_name_from_value` (function, L312)
- `extract_view_metadata` (function, L367)
- `compute_config_hash` (function, L565)
- `compute_view_frame_hash` (function, L597)
- `build_core_csv_row` (function, L619)
- `build_vop_csv_row` (function, L672)
- `export_pipeline_to_csv` (function, L747)
- `get_core_csv_header` (function, L1035)
- `get_vop_csv_header` (function, L1045)
- `get_perf_csv_header` (function, L1057)
- `view_result_to_core_row` (function, L1067)
- `view_result_to_vop_row` (function, L1196)
- `view_result_to_perf_row` (function, L1468)

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
- `run_pipeline_from_dynamo_input` (function, L226)

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
- `get_current_document` (function, L151)
- `get_current_view` (function, L187)
- `_normalize_view_ids` (function, L223)
- `run_vop_pipeline` (function, L276)
- `run_vop_pipeline_with_png` (function, L324)
- `run_vop_pipeline_with_csv` (function, L401)
- `run_vop_pipeline_json` (function, L553)
- `get_test_config_tiny` (function, L587)
- `get_test_config_linear` (function, L603)
- `get_test_config_areal_heavy` (function, L619)
- `quick_test_current_view` (function, L636)

### `export/csv.py`

**Imports**
- `csv`
- `os`

**Definitions**
- `_ensure_dir` (function, L8)
- `_append_csv_rows` (function, L20)

### `pipeline.py`

**Imports**
- `.config:Config`
- `.core.geometry:Mode,classify_by_uv,make_uv_aabb,make_obb_or_skinny_aabb`
- `.core.math_utils:Bounds2D,CellRect`
- `.core.raster:ViewRaster,TileMap`
- `.core.silhouette:get_element_silhouette`
- `.revit.annotation:rasterize_annotations`
- `.revit.collection:collect_view_elements,expand_host_link_import_model_elements,sort_front_to_back,is_element_visible_in_view,estimate_nearest_depth_from_bbox`
- `.revit.safe_api:safe_call`
- `.revit.view_basis:make_view_basis,resolve_view_bounds`
- `math`
- `time`

**Definitions**
- `_diagnose_link_geometry_transform` (function, L112)
- `_perf_now` (function, L195)
- `_perf_ms` (function, L200)
- `_safe_int` (function, L203)
- `_safe_bool` (function, L210)
- `_cropbox_fingerprint` (function, L217)
- `_cfg_hash` (function, L245)
- `_view_signature` (function, L261)
- `_extract_view_identity_for_csv` (function, L351)
- `process_document_views` (function, L449)
- `init_view_raster` (function, L1039)
- `_extract_view_summary` (function, L1181)
- `render_model_front_to_back` (function, L1226)
- `_is_supported_2d_view` (function, L2089)
- `_should_skip_outside_view_volume` (function, L2136)
- `_tiles_fully_covered_and_nearer` (function, L2172)
- `_bin_elements_to_tiles` (function, L2203)
- `_tile_has_depth_conflict` (function, L2235)
- `_get_ambiguous_tiles` (function, L2269)
- `_render_areal_element` (function, L2299)
- `_render_proxy_element` (function, L2314)
- `_stamp_proxy_edges` (function, L2339)
- `_mark_rect_center_cell` (function, L2352)
- `_mark_thin_band_along_long_axis` (function, L2360)
- `export_view_raster` (function, L2380)

### `png_export.py`

**Imports**
- `os`
- `time`

**Definitions**
- `export_raster_to_png` (function, L10)
- `export_pipeline_results_to_pngs` (function, L285)

### `revit/annotation.py`

**Definitions**
- `is_extent_driver_annotation` (function, L12)
- `compute_annotation_extents` (function, L83)
- `collect_2d_annotations` (function, L362)
- `classify_annotation` (function, L599)
- `classify_keynote` (function, L679)
- `get_annotation_bbox` (function, L731)
- `rasterize_annotations` (function, L762)
- `_stamp_detail_line_band` (function, L1109)
- `_point_in_quad` (function, L1181)
- `_uv_to_cell` (function, L1211)
- `_stamp_cell` (function, L1220)
- `_stamp_rect_outline` (function, L1227)
- `_stamp_line_cells` (function, L1247)
- `_project_element_bbox_to_cell_rect_for_anno` (function, L1268)

### `revit/collection.py`

**Imports**
- `math`

**Definitions**
- `resolve_element_bbox` (function, L10)
- `collect_view_elements` (function, L64)
- `is_element_visible_in_view` (function, L266)
- `expand_host_link_import_model_elements` (function, L294)
- `sort_front_to_back` (function, L438)
- `estimate_nearest_depth_from_bbox` (function, L456)
- `estimate_depth_from_loops_or_bbox` (function, L524)
- `estimate_depth_range_from_bbox` (function, L552)
- `_project_element_bbox_to_cell_rect` (function, L626)
- `_extract_geometry_footprint_uv` (function, L770)
- `get_element_obb_loops` (function, L886)
- `_pca_obb_uv` (function, L1059)

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
- `should_include_element` (function, L189)

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
- `_collect_from_revit_links` (function, L442)
- `_collect_from_dwg_imports` (function, L562)
- `_collect_link_elements_with_clipping` (function, L659)
- `_build_clip_volume` (function, L803)
- `_get_plan_view_vertical_range` (function, L924)
- `_build_crop_prism_corners` (function, L983)
- `_get_host_visible_model_categories` (function, L1032)
- `_get_excluded_3d_category_ids` (function, L1076)
- `_transform_bbox_to_host` (function, L1085)

### `revit/safe_api.py`

**Imports**
- `typing:Any,Callable,Dict,Optional,TypeVar`

**Definitions**
- `safe_call` (function, L8)

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
- `resolve_view_w_volume` (function, L211)
- `xy_bounds_from_crop_box_all_corners` (function, L288)
- `xy_bounds_effective` (function, L355)
- `synthetic_bounds_from_visible_extents` (function, L388)
- `_bounds_to_tuple` (function, L630)
- `resolve_view_bounds` (function, L637)
- `_view_type_name` (function, L1000)
- `supports_model_geometry` (function, L1104)
- `supports_crop_bounds` (function, L1141)
- `supports_depth` (function, L1160)
- `resolve_view_mode` (function, L1174)
- `resolve_annotation_only_bounds` (function, L1220)

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
- `RootStyleCache.save` (method, L219)
- `RootStyleCache.stats` (method, L261)
- `RootStyleCache._empty_cache` (method, L275)
- `compute_config_hash` (function, L286)
- `extract_metrics_from_view_result` (function, L310)

### `streaming.py`

**Imports**
- `datetime:datetime`
- `json`
- `os`
- `time`

**Definitions**
- `process_with_streaming` (function, L21)
- `StreamingExporter` (class, L91)
- `StreamingExporter.__init__` (method, L94)
- `StreamingExporter._init_csv_writers` (method, L174)
- `StreamingExporter.on_view_complete` (method, L223)
- `StreamingExporter._write_png` (method, L289)
- `StreamingExporter._write_csv_rows` (method, L317)
- `StreamingExporter._extract_summary` (method, L424)
- `StreamingExporter.finalize` (method, L437)
- `process_document_views_streaming` (function, L488)
- `run_vop_pipeline_streaming` (function, L576)

### `thinrunner_streaming.py`

**Imports**
- `os`
- `sys`
- `vop_interwoven.entry_dynamo:get_current_document,get_current_view`
