# Graph Report - .  (2026-06-06)

## Corpus Check
- 138 files · ~163,175 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1929 nodes · 3752 edges · 89 communities (83 shown, 6 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 96 edges (avg confidence: 0.56)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Dynamo Integration Tests|Dynamo Integration Tests]]
- [[_COMMUNITY_Geometry Cache Layer|Geometry Cache Layer]]
- [[_COMMUNITY_Convex Hull & View Coordinates|Convex Hull & View Coordinates]]
- [[_COMMUNITY_UV Classification Engine|UV Classification Engine]]
- [[_COMMUNITY_Metrics State Scanner|Metrics State Scanner]]
- [[_COMMUNITY_Diagnostics & Error Tracking|Diagnostics & Error Tracking]]
- [[_COMMUNITY_Architecture Docs & Concepts|Architecture Docs & Concepts]]
- [[_COMMUNITY_Occlusion Contract Tests|Occlusion Contract Tests]]
- [[_COMMUNITY_Silhouette Extraction|Silhouette Extraction]]
- [[_COMMUNITY_Test Infrastructure & Skeletons|Test Infrastructure & Skeletons]]
- [[_COMMUNITY_Math Utilities & AABB|Math Utilities & AABB]]
- [[_COMMUNITY_AREAL Extraction Tests|AREAL Extraction Tests]]
- [[_COMMUNITY_Linked Document Handling|Linked Document Handling]]
- [[_COMMUNITY_Element LRU Cache|Element LRU Cache]]
- [[_COMMUNITY_CSV Export & Tests|CSV Export & Tests]]
- [[_COMMUNITY_Collection Diagnostics Tests|Collection Diagnostics Tests]]
- [[_COMMUNITY_Export Identity Tests|Export Identity Tests]]
- [[_COMMUNITY_Revit Element Collection|Revit Element Collection]]
- [[_COMMUNITY_Raster Core Rationale|Raster Core Rationale]]
- [[_COMMUNITY_Collection Policy Tests|Collection Policy Tests]]
- [[_COMMUNITY_Metrics Manifest Tests|Metrics Manifest Tests]]
- [[_COMMUNITY_2D Bounds Geometry|2D Bounds Geometry]]
- [[_COMMUNITY_CSV Cache Normalization Tests|CSV Cache Normalization Tests]]
- [[_COMMUNITY_AREAL Geometry Extraction|AREAL Geometry Extraction]]
- [[_COMMUNITY_2D Annotation Processing|2D Annotation Processing]]
- [[_COMMUNITY_Raster Pipeline Design Notes|Raster Pipeline Design Notes]]
- [[_COMMUNITY_Strategy Performance Tracker|Strategy Performance Tracker]]
- [[_COMMUNITY_Pipeline Diagnostics Tests|Pipeline Diagnostics Tests]]
- [[_COMMUNITY_Code Map Generation Tools|Code Map Generation Tools]]
- [[_COMMUNITY_Config Hash & Cache Reset|Config Hash & Cache Reset]]
- [[_COMMUNITY_Raster Test Rationale|Raster Test Rationale]]
- [[_COMMUNITY_Front-Facing Face Selection|Front-Facing Face Selection]]
- [[_COMMUNITY_Raster Polygon Clipping|Raster Polygon Clipping]]
- [[_COMMUNITY_Link Collector Tests|Link Collector Tests]]
- [[_COMMUNITY_View Volume Metrics Tests|View Volume Metrics Tests]]
- [[_COMMUNITY_Collection Policy & Categories|Collection Policy & Categories]]
- [[_COMMUNITY_View Raster Export|View Raster Export]]
- [[_COMMUNITY_View Basis Design Notes|View Basis Design Notes]]
- [[_COMMUNITY_Face Plane Grouping|Face Plane Grouping]]
- [[_COMMUNITY_Strategy Tracker Tests|Strategy Tracker Tests]]
- [[_COMMUNITY_Thin Runner Streaming|Thin Runner Streaming]]
- [[_COMMUNITY_Raster Drawing Algorithms|Raster Drawing Algorithms]]
- [[_COMMUNITY_Policy Stats Tracking|Policy Stats Tracking]]
- [[_COMMUNITY_BBox Policy Tests|BBox Policy Tests]]
- [[_COMMUNITY_Front-Face Loops Tests|Front-Face Loops Tests]]
- [[_COMMUNITY_Golden Baseline Comparison|Golden Baseline Comparison]]
- [[_COMMUNITY_Bare Except Fixer|Bare Except Fixer]]
- [[_COMMUNITY_View Basis Coordinates|View Basis Coordinates]]
- [[_COMMUNITY_Exclusion Filter Tests|Exclusion Filter Tests]]
- [[_COMMUNITY_Streaming Manifest Tests|Streaming Manifest Tests]]
- [[_COMMUNITY_Memory Telemetry|Memory Telemetry]]
- [[_COMMUNITY_LRU Cache Core|LRU Cache Core]]
- [[_COMMUNITY_Link Transform Binning Tests|Link Transform Binning Tests]]
- [[_COMMUNITY_Collection Error Diagnostics|Collection Error Diagnostics]]
- [[_COMMUNITY_CSV Model Presence Tests|CSV Model Presence Tests]]
- [[_COMMUNITY_OBB Geometry|OBB Geometry]]
- [[_COMMUNITY_Raster Cell Operations|Raster Cell Operations]]
- [[_COMMUNITY_Occlusion Saturation Tracker|Occlusion Saturation Tracker]]
- [[_COMMUNITY_Annotation Collection Policy|Annotation Collection Policy]]
- [[_COMMUNITY_CSV Export Test Notes|CSV Export Test Notes]]
- [[_COMMUNITY_Depth Convention Tests|Depth Convention Tests]]
- [[_COMMUNITY_AREAL Extraction Contract Tests|AREAL Extraction Contract Tests]]
- [[_COMMUNITY_Manifest Generation Tool|Manifest Generation Tool]]
- [[_COMMUNITY_Cell Footprint Computation|Cell Footprint Computation]]
- [[_COMMUNITY_Cell Size Column Tests|Cell Size Column Tests]]
- [[_COMMUNITY_PNG Export|PNG Export]]
- [[_COMMUNITY_Location Curve Silhouette|Location Curve Silhouette]]
- [[_COMMUNITY_Source Identity (HOSTLINKDWG)|Source Identity (HOST/LINK/DWG)]]
- [[_COMMUNITY_Bare Except Linter|Bare Except Linter]]
- [[_COMMUNITY_Raster Decomposition|Raster Decomposition]]
- [[_COMMUNITY_Raster Internals Notes|Raster Internals Notes]]
- [[_COMMUNITY_Raster Depth Buffer Notes|Raster Depth Buffer Notes]]
- [[_COMMUNITY_Cache Signature Verification|Cache Signature Verification]]
- [[_COMMUNITY_NumPy & Pillow Backend|NumPy & Pillow Backend]]
- [[_COMMUNITY_AREAL Test Mock Objects|AREAL Test Mock Objects]]
- [[_COMMUNITY_Proxy Occupancy Channel Tests|Proxy Occupancy Channel Tests]]
- [[_COMMUNITY_Bootstrap & Dependency Check|Bootstrap & Dependency Check]]
- [[_COMMUNITY_Raster Debug Serialization|Raster Debug Serialization]]
- [[_COMMUNITY_Tier B Proxy Sampling|Tier B Proxy Sampling]]
- [[_COMMUNITY_Test Configuration|Test Configuration]]
- [[_COMMUNITY_Strategy Diagnostics (Dynamo)|Strategy Diagnostics (Dynamo)]]
- [[_COMMUNITY_Test Runner Script|Test Runner Script]]
- [[_COMMUNITY_Tests Package Init|Tests Package Init]]
- [[_COMMUNITY_Root README|Root README]]

## God Nodes (most connected - your core abstractions)
1. `Config` - 84 edges
2. `ViewRaster` - 76 edges
3. `Bounds2D` - 73 edges
4. `StrategyDiagnostics` - 54 edges
5. `GeometryCache` - 40 edges
6. `Diagnostics` - 34 edges
7. `view_result_to_vop_row()` - 32 edges
8. `load_manifest_json()` - 32 edges
9. `render_model_front_to_back()` - 32 edges
10. `PolicyStats` - 31 edges

## Surprising Connections (you probably didn't know these)
- `Two-Tier Primitive Cache Contract (Proposed, Not Implemented)` --semantically_similar_to--> `RootStyleCache (View-level Metrics Cache)`  [INFERRED] [semantically similar]
  Pipeline_Approach_Comparison.md → CURRENT_ARCHITECTURE.md
- `Settings` --uses--> `PolicyStats`  [INFERRED]
  tests/test_collection_policy.py → vop_interwoven/revit/collection_policy.py
- `_StubView` --uses--> `Bounds2D`  [INFERRED]
  tests/test_bounds_budget.py → vop_interwoven/core/math_utils.py
- `_StubView` --uses--> `Bounds2D`  [INFERRED]
  tests/test_bounds_resolution.py → vop_interwoven/core/math_utils.py
- `MockXYZ` --uses--> `StrategyDiagnostics`  [INFERRED]
  tests/test_collection_diagnostics.py → vop_interwoven/diagnostics/strategy_tracker.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Occlusion Authority: UV Classification + Confidence + Annotation Non-Occlusion** — concept_uv_classification, concept_confidence_based_occlusion, concept_annotation_non_occlusion, concept_occlusion_authority [EXTRACTED 0.95]
- **Pipeline Correctness Contract: No Silent Failure + Single Source of Truth + Diagnostics Always-On** — concept_no_silent_failure, concept_single_source_of_truth, concept_diagnostics_always_on, refactor_rules_doc, diagnostics_contract_doc [EXTRACTED 0.95]
- **Regression Safety: Golden Baseline + CSV Invariant + Diagnostics** — concept_golden_baseline_testing, concept_8state_occupancy_partition, concept_diagnostics_always_on [INFERRED 0.75]

## Communities (89 total, 6 thin omitted)

### Community 0 - "Dynamo Integration Tests"
Cohesion: 0.04
Nodes (63): All Phases Test: Quick validation of all implemented phases  Copy this code into, Import Check - Verify all modules load correctly  This tests if the linked docum, Test Linked Documents Collection - Debug Script  Paste this into a Dynamo Python, Minimal Single View Test - Step-by-step diagnostic  This will show exactly where, Phase 1 Test: View Basis & Coordinate System  Copy this code into a Dynamo Pytho, Phase 2 Test: Element Collection & Bounding Boxes  Copy this code into a Dynamo, Phase 3 Test: UV Classification & Proxy Generation  Copy this code into a Dynamo, Phase 7: CSV Export Test  Test CSV export functionality with invariant validatio (+55 more)

### Community 1 - "Geometry Cache Layer"
Cohesion: 0.06
Nodes (52): _decode_key(), _encode_key(), _entry_bbox_fingerprints(), GeometryCache, Persistent AREAL geometry cache backed by JSON file.  Replaces the in-memory LRU, Return cached value or None.  Tracks hits/misses and disk_hits., Store value in cache and mark dirty., Load entries from JSON.  Silent no-op if file is absent or unreadable. (+44 more)

### Community 2 - "Convex Hull & View Coordinates"
Cohesion: 0.05
Nodes (60): convex_hull_uv(), Compute convex hull of 2D points using monotonic chain.     Input: list of (u, v, Resolve view-space W volume [W0, Wmax] for the host view.      Unification rule:, resolve_view_w_volume(), Performance/memory diagnostics key constants., _bin_elements_to_tiles(), _cfg_hash(), _cropbox_fingerprint() (+52 more)

### Community 3 - "UV Classification Engine"
Cohesion: 0.05
Nodes (36): classify_by_uv(), classify_by_uv_pca(), make_obb_or_skinny_aabb(), _mesh_vertex_count(), Mode, Geometry classification and proxy generation for VOP interwoven pipeline.  Provi, Axis-aligned bounding box proxy in UV (view XY) space.      Used for TINY elemen, Element classification based on UV footprint size.      TINY: Both dimensions <= (+28 more)

### Community 4 - "Metrics State Scanner"
Cohesion: 0.08
Nodes (50): _default_model_class_resolver(), _get_element_meta(), _get_source_type(), _normalize_anno_type(), Final-state totals scanner for manifest-locked metrics., Scan finalized raster grids and emit locked aggregate totals., _safe_seq(), scan_final_state_totals() (+42 more)

### Community 5 - "Diagnostics & Error Tracking"
Cohesion: 0.07
Nodes (35): Diagnostics, _exc_to_str(), Structured diagnostics recorder (Dynamo-safe minimal stdlib).      - Bounded eve, Record at most one DEBUG event per dedupe_key, with a suppressed_count., _diag_fallback_stderr(), CRITICAL FALLBACK: If diagnostic recording fails, print to stderr.      This ens, Execute fn() and handle exceptions in a controlled, observable way.      policy:, Record an error with diagnostic fallback to stderr if recording fails.      Use (+27 more)

### Community 6 - "Architecture Docs & Concepts"
Cohesion: 0.06
Nodes (52): CLAUDE.md AI Assistant Project Guide, VOP Interwoven Code Map (Authoritative), 8-State Occupancy Cell Partition Invariant, Annotation Non-Occlusion Layering Principle, Annotation Type Classification (7 Types: TEXT/TAG/DIM/DETAIL/LINES/REGION/OTHER), AREAL Fallback Occlusion Semantics Contradiction (P0 Debt), Confidence-Based Occlusion Semantics, Copy/Deploy Deployment Model (No Packaging) (+44 more)

### Community 7 - "Occlusion Contract Tests"
Cohesion: 0.06
Nodes (50): _make_raster(), Occlusion contract tests.  Policy (from pipeline.py / README):   - Only AREAL el, rasterize_polygon_to_proxy must write normally when w_occ is empty., rasterize_polygon_to_proxy must write when this element is closer than w_occ., rasterize_polygon_to_proxy must write model_proxy_key for interior cells., rasterize_polygon_to_proxy must not touch model_edge_key., AREAL MEDIUM/LOW boundary ink (rasterize_closed_loops_to_proxy_edges) must not w, AREAL LOW open polylines (rasterize_open_polylines_to_proxy_edges) must not writ (+42 more)

### Community 8 - "Silhouette Extraction"
Cohesion: 0.07
Nodes (47): _apply_transform_xyz_tuple(), _bbox_corners_world(), _bbox_silhouette(), _cache_get(), _cache_set(), _cad_curves_silhouette(), _collect_regions_recursive(), _compose_transform() (+39 more)

### Community 9 - "Test Infrastructure & Skeletons"
Cohesion: 0.09
Nodes (40): float, build_2d_regions(), classify_tier_by_rect(), compute_per_cell_depth_for_cells(), compute_per_cell_depth_for_curve(), Coverage, depth_sort_key(), DepthBuffer (+32 more)

### Community 10 - "Math Utilities & AABB"
Cohesion: 0.06
Nodes (28): make_uv_aabb(), Create UV_AABB proxy from CellRect.      Args:         rect: CellRect with cell, CellRect, cellrect_dims(), clamp(), point_in_rect(), Mathematical utilities for VOP interwoven pipeline.  Provides bounds and rectang, Return (i, j) of center cell. (+20 more)

### Community 11 - "AREAL Extraction Tests"
Cohesion: 0.11
Nodes (25): MockConfig, MockElement, MockRaster, MockView, MockViewBasis, Test _safe_category handles missing category., Test fallback tier behavior and confidence assignment., Test that total failure returns (None, None, 'failed'). (+17 more)

### Community 12 - "Linked Document Handling"
Cohesion: 0.09
Nodes (35): _build_clip_volume(), _build_crop_prism_corners(), collect_all_linked_elements(), _collect_from_dwg_imports(), _collect_from_revit_links(), _collect_link_elements_with_clipping(), _collect_visible_link_elements_2024_plus(), _get_excluded_3d_category_ids() (+27 more)

### Community 13 - "Element LRU Cache"
Cohesion: 0.07
Nodes (23): ElementCache, ElementFingerprint, _normalize_source_type(), Element cache with bbox fingerprints for cross-view reuse.  Phase 2 of VOP cache, Serialize fingerprint to dict for JSON export.          Returns:             Dic, Deserialize fingerprint from dict (JSON import).          Args:             d: D, LRU cache for element fingerprints with hit/miss tracking.      Caches element b, Initialize element cache with LRU eviction.          Args:             max_eleme (+15 more)

### Community 14 - "CSV Export & Tests"
Cohesion: 0.10
Nodes (35): _append_csv_rows(), _ensure_dir(), build_core_csv_row(), _build_manifest_vop_row_from_metrics(), build_occlusion_row(), build_vop_csv_row(), _coerce_view_id_int(), _compute_cell_metrics_py() (+27 more)

### Community 15 - "Collection Diagnostics Tests"
Cohesion: 0.10
Nodes (21): MockBBox, MockElement, MockRaster, MockViewBasis, MockXYZ, Test diagnostic integration in collection.py functions., Test _get_element_category_name extracts category correctly., Test _get_element_category_name handles missing category. (+13 more)

### Community 16 - "Export Identity Tests"
Cohesion: 0.07
Nodes (28): _Cfg, test_core_row_uses_view_unique_id_without_view_object(), test_perf_header_uses_pascal_case_and_includes_view_unique_id(), test_perf_row_includes_view_unique_id_from_result_or_view_object(), test_root_cache_metadata_carries_view_unique_id(), test_root_cache_row_payload_stores_uid_from_pascal_metadata(), test_vop_cache_row_uses_view_result_uid_when_cached_payload_missing_uid(), test_vop_header_includes_view_unique_id() (+20 more)

### Community 17 - "Revit Element Collection"
Cohesion: 0.09
Nodes (33): Zero Occupancy Debug Script  Comprehensive diagnostic for linked RVT/DWG zero oc, collect_view_elements(), _diagnose_coordinate_spaces(), estimate_depth_from_loops_or_bbox(), estimate_depth_range_from_bbox(), estimate_nearest_depth_from_bbox(), expand_host_link_import_model_elements(), _extract_geometry_footprint_uv() (+25 more)

### Community 18 - "Raster Core Rationale"
Cohesion: 0.07
Nodes (22): Reconstruct a ViewRaster from a dict payload (inverse of to_dict()).          In, Raster representation of a single view for VOP interwoven pipeline.      Stores, Initialize view raster.             Args:                 width: Raster width in, Raster width in cells (alias for W)., Raster height in cells (alias for H)., Cell size in model units (alias for cell_size_ft)., Bounds in view-local XY (alias for bounds_xy)., Depth-tested interior occupancy ('truth'). Backed by legacy model_mask. (+14 more)

### Community 19 - "Collection Policy Tests"
Cohesion: 0.11
Nodes (15): _FakeElem, _include(), Walls were in the old allowlist -- verify they still work., An explicitly excluded name must be rejected even if CategoryType=Model., RevitLinkInstance (category 'RVT Links') must be excluded from the HOST pass., ImportInstance (category 'Imports') must be excluded from the HOST pass., String 'Annotation' is used in pytest string-comparison fallback path., String 'Model' is used in pytest string-comparison fallback path. (+7 more)

### Community 20 - "Metrics Manifest Tests"
Cohesion: 0.17
Nodes (30): bytes, test_manifest_loader_accepts_locked_v1_and_hash_is_stable(), test_manifest_loader_rejects_bad_required_types(), test_manifest_loader_rejects_unknown_top_level_keys(), test_manifest_loader_rejects_wrong_schema_version(), test_export_pipeline_to_csv_manifest_header_exact_order_when_compat_off(), test_get_vop_csv_header_uses_manifest_columns_when_compat_off(), canonicalize_manifest_json() (+22 more)

### Community 21 - "2D Bounds Geometry"
Cohesion: 0.09
Nodes (25): Bounds2D, Check if point (x, y) is inside bounds (inclusive)., Check if this bounds intersects another Bounds2D., Return new Bounds2D expanded by margin on all sides., 2D axis-aligned bounding box in view XY space.      Attributes:         xmin, ym, compute_annotation_extents(), is_extent_driver_annotation(), Compute annotation extents for grid bounds expansion.      Collects extent-drive (+17 more)

### Community 22 - "CSV Cache Normalization Tests"
Cohesion: 0.12
Nodes (30): test_vop_cache_hit_backfills_view_unique_id_from_metadata_when_missing_in_payload(), test_vop_cache_hit_viewtype_is_human_readable_and_blanks_preserved(), test_extract_metrics_from_view_result_prefers_precomputed_metrics_without_raster_recompute(), _Cfg, _raster(), test_external_metrics_uses_occupancy_layers_to_distinguish_only_from_rvt(), test_extract_metrics_from_view_result_ignores_incomplete_precomputed_metrics_with_live_raster(), test_extract_metrics_from_view_result_preserves_link_source_at_key_zero() (+22 more)

### Community 23 - "AREAL Geometry Extraction"
Cohesion: 0.10
Nodes (21): extract_areal_geometry(), _get_aabb_loops_from_bbox(), Extract AREAL element geometry with confidence-based fallback hierarchy.      Im, Safely extract element ID as integer.      Args:         elem: Revit Element, Safely extract category name from element.      Args:         elem: Revit Elemen, Create axis-aligned bounding box loops from bbox.      Args:         bbox: Revit, _safe_category(), _safe_elem_id() (+13 more)

### Community 24 - "2D Annotation Processing"
Cohesion: 0.10
Nodes (31): classify_annotation(), classify_keynote(), collect_2d_annotations(), get_annotation_bbox(), _is_excluded_from_extent_expansion(), _point_in_quad(), _project_element_bbox_to_cell_rect_for_anno(), rasterize_annotations() (+23 more)

### Community 25 - "Raster Pipeline Design Notes"
Cohesion: 0.08
Nodes (17): Check if tile is completely filled.          Args:             tile_idx: Tile in, Tile-based spatial acceleration structure for early-out occlusion testing., Initialize tile map.          Args:             tile_size: Size of each tile in, Get list of tile indices overlapping rectangle.          Args:             i_min, TileMap, Unit tests for VOP interwoven raster data structures.  Tests ViewRaster, TileMap, Test TileMap spatial acceleration structure., Create test tile map. (+9 more)

### Community 26 - "Strategy Performance Tracker"
Cohesion: 0.08
Nodes (14): Record AREAL strategy attempt and outcome.          Args:             elem_id: E, Record geometry extraction attempt and outcome.          Args:             elem_, Record confidence level for an element.          Args:             elem_id: Elem, Record an extraction method attempt for an element.          Args:             e, Track which extraction method was used for an element.          Args:, Tracks geometry extraction strategy diagnostics.      Provides detailed tracking, Calculate success rate safely, handling division by zero.          Args:, Get summary statistics.          Returns:             dict: Summary statistics i (+6 more)

### Community 27 - "Pipeline Diagnostics Tests"
Cohesion: 0.07
Nodes (15): Test that CSV export works., Test strategy diagnostics integration in pipeline., Test that config can round-trip with export_strategy_diagnostics., Test that pipeline logic for creating diagnostics works., Test that pipeline can skip diagnostics when disabled., Test that export_strategy_diagnostics defaults to False., Test that diagnostic tracking failures don't crash., Test that export_strategy_diagnostics can be disabled. (+7 more)

### Community 28 - "Code Map Generation Tools"
Cohesion: 0.21
Nodes (25): AST, Module, build_index(), build_trace_tree(), _call_name(), DefInfo, _format_def(), _is_excluded_dir() (+17 more)

### Community 29 - "Config Hash & Cache Reset"
Cohesion: 0.12
Nodes (21): Clear module-level family-region caches.      Revit add-ins can execute in a lon, reset_family_region_caches(), test_csv_config_hash_matches_root_cache_config_hash_for_real_config(), compute_config_hash(), Compute stable hash of config for reproducibility tracking.      Args:         c, _pipeline_result_for_json(), _prune_view_raster_for_json(), Mutate one view_result dict in-place to reduce JSON size. (+13 more)

### Community 30 - "Raster Test Rationale"
Cohesion: 0.08
Nodes (13): Test ViewRaster data structure., Create test view raster., Test view raster initialization., Test cell index calculation., Test cell filling with depth (deprecated method)., Test centralized cell write with depth testing., occ_host/link/dwg must accumulate — a later winner must not erase prior True fla, Test element metadata tracking. (+5 more)

### Community 31 - "Front-Facing Face Selection"
Cohesion: 0.15
Nodes (21): _canonicalize_plane(), _dot(), iter_front_facing_planar_faces(), _norm(), _normalize(), _plane_eq_close(), _plane_from_planar_face(), polygon_area_2d() (+13 more)

### Community 32 - "Raster Polygon Clipping"
Cohesion: 0.12
Nodes (13): _clip_poly_to_rect_uv(), _fix_loop_points_uv(), Rasterize polygon loops to proxy layer WITHOUT updating occlusion buffer., Rasterize polygon loops into the annotation channel (anno_key) using scanline fi, Stamp CLOSED loop perimeters into proxy channel only (no fill, no occlusion)., Merge consecutive points closer than tol_ft (UV space, feet).     Returns a new, Return set of interior (i,j) cells for polygon using scanline (no writes)., Fill polygon interior using scanline algorithm with depth testing.          Args (+5 more)

### Community 33 - "Link Collector Tests"
Cohesion: 0.13
Nodes (10): _FakeBBox, _FakeCategory, _FakeCategoryType, _FakeCollector, _FakeElem, _FakeId, _FakeLinkDoc, _FakeLinkInst (+2 more)

### Community 34 - "View Volume Metrics Tests"
Cohesion: 0.15
Nodes (14): int, _StubCfg, _StubId, _StubRaster, _StubTile, _StubView, test_export_defaults_skipped_outside_view_volume_to_zero_when_missing(), test_export_includes_skipped_outside_view_volume_metric() (+6 more)

### Community 35 - "Collection Policy & Categories"
Cohesion: 0.13
Nodes (9): excluded_bic_names_global(), Categories, _FakeCategory, _FakeDoc, _FakeId, Minimal fake Category.      category_type:         1 (or _CATEGORY_TYPE_MODEL_IN, Minimal fake doc.  resolve_category_ids returns empty set (triggers name fallbac, Settings (+1 more)

### Community 36 - "View Raster Export"
Cohesion: 0.15
Nodes (18): Import BuiltInCategory lazily (Revit-only)., _try_import_bic(), _canonical_name(), _dot(), export_pipeline_views_to_pngs(), export_view_image(), _is_annotation_only_view(), _prepare_view_for_export() (+10 more)

### Community 37 - "View Basis Design Notes"
Cohesion: 0.22
Nodes (17): Best-effort, Revit-free view type name extraction for gating + tests.     Return, Capability: view can reasonably be expected to host model geometry in this pipel, Capability: view supports crop-based bounds (not whether crop is active).     Dr, Capability: pipeline depth semantics are meaningful.     In this pipeline, depth, Decide how this view should be processed, and WHY.     Returns: (mode, reason_di, resolve_view_mode(), supports_crop_bounds(), supports_depth() (+9 more)

### Community 38 - "Face Plane Grouping"
Cohesion: 0.16
Nodes (9): group_faces_by_plane(), Group planar faces by plane within tolerance.      Determinism:     - Faces are, Select top N plane-groups by area_uv with stable ordering and tie-breakers., select_top_plane_groups(), _FaceStub, Unit tests for deterministic planar front-face selection utilities.  These tests, Return a square polygon in *model* coords (plan view basis => model XY == UV)., _square_loop() (+1 more)

### Community 39 - "Strategy Tracker Tests"
Cohesion: 0.12
Nodes (9): Test summary statistics calculation., Test CSV export format and content., Test StrategyDiagnostics class., Create fresh diagnostics instance for each test., Test print_summary executes without errors., Test basic element classification tracking., Test AREAL strategy success/failure tracking., Test geometry extraction outcome tracking. (+1 more)

### Community 40 - "Thin Runner Streaming"
Cohesion: 0.14
Nodes (13): _build_views_from_input(), _coerce_view_id(), VOP Interwoven Pipeline - Thin Runner for Dynamo (STREAMING VERSION)  Quick test, Normalize IN[0] into a list of view-like objects., Best-effort coercion to an integer view id for pipeline compatibility., Best-effort elevation lookup for a view., Sort views by level elevation (ascending) with graceful fallback., Convert Dynamo/.NET collections to a flat Python list without exploding strings. (+5 more)

### Community 41 - "Raster Drawing Algorithms"
Cohesion: 0.16
Nodes (10): _bresenham_line(), _polygon_mask_np(), Stamp OPEN polylines into proxy channel only (no fill, no occlusion)., Rasterize element silhouette loops into model layers with depth testing., Return boolean mask shape (H, W) filled inside closed polygon.      Uses vectori, Generate cell coordinates along a line using Bresenham's algorithm.      Args:, Model-only clip predicate.         Returns True if (i,j) lies fully inside model, Rasterize OPEN polyline paths as edges only (no interior fill).          Args: (+2 more)

### Community 42 - "Policy Stats Tracking"
Cohesion: 0.22
Nodes (8): PolicyStats, Apply category policy to an element.      Decision order:       1. Exclude eleme, Aggregated counters for policy filtering (runtime-safe)., should_include_element(), The old 'not_in_allowlist' reason must never appear., TestNoCategoryElement, TestPolicyStats, bool

### Community 43 - "BBox Policy Tests"
Cohesion: 0.20
Nodes (10): _BBox, ElemModelOnly, ElemThrows, ElemViewWins, FakeView, _Id, _P, test_resolve_element_bbox_falls_back_to_model() (+2 more)

### Community 44 - "Front-Face Loops Tests"
Cohesion: 0.21
Nodes (12): _Id, _import_config_and_silhouette(), int, Import using the real package first (pytest runs with repo root on sys.path),, Acceptance boundary:       AREAL strategies must try front-facing planar face lo, Verifies dispatch order + passthrough semantics without requiring Revit geometry, _StubElem, _StubRaster (+4 more)

### Community 45 - "Golden Baseline Comparison"
Cohesion: 0.17
Nodes (15): color(), Colors, compare_csv_files(), compare_png_files(), load_manifest(), main(), normalize_csv_for_comparison(), Compare CSV file against expected hash after normalizing.      Args:         cur (+7 more)

### Community 46 - "Bare Except Fixer"
Cohesion: 0.18
Nodes (15): apply_fixes(), find_except_blocks(), generate_fix(), get_function_name(), get_phase_from_context(), has_diag_in_scope(), main(), process_file() (+7 more)

### Community 47 - "View Basis Coordinates"
Cohesion: 0.16
Nodes (7): View coordinate system with origin and basis vectors.      Attributes:         o, Back-compat helper: accept XYZ or tuple, return view-local (u, v, w)., Check if view is plan-like (looking down Z axis).          Returns:, Check if view is elevation-like (horizontal view direction).          Returns:, Transform model-space point to view-local UVW coordinates.          Args:, ViewBasis, TestFaceSelection

### Community 48 - "Exclusion Filter Tests"
Cohesion: 0.14
Nodes (8): Test exclusion filter functionality in collection.  Phase 2B: Verifies that the, Test that the exclusion filter parameter was added correctly., Test that collect_view_elements has exclude_ids parameter., Test that exclude_ids defaults to None for backward compatibility., Test that original parameters are preserved in correct order., Test that exclude_ids won't break existing positional callers., Test that the collection module imports without errors after changes., TestExclusionFilterSignature

### Community 49 - "Streaming Manifest Tests"
Cohesion: 0.20
Nodes (8): test_streaming_vop_header_uses_cfg_and_includes_metadata_for_manifest_mode(), Manages incremental export of pipeline results., Callback when a view completes processing.                  Args:             vi, Write PNG for a single view result.                  Returns:             Path t, Export a raw Revit view image to view_raster/ for comparison.          Returns:, Write CSV rows for a single view result., Extract lightweight summary from view result (no raster arrays)., StreamingExporter

### Community 50 - "Memory Telemetry"
Cohesion: 0.19
Nodes (6): _force_clr_gc(), _get_memory_mb(), MemoryTracker, Memory telemetry helpers for Dynamo/Python.NET runtime., Run CLR GC triple-call pattern and report private-memory delta., Return current (working_set_mb, private_bytes_mb) or (None, None).

### Community 51 - "LRU Cache Core"
Cohesion: 0.18
Nodes (5): LRUCache, Bounded LRU caches.  We use these caches to avoid repeated, expensive Revit geom, A simple, bounded LRU cache keyed by hashable keys.      Notes:         - max_it, test_geometry_cache_disabled_when_max_items_zero(), test_geometry_cache_lru_eviction_order()

### Community 52 - "Link Transform Binning Tests"
Cohesion: 0.26
Nodes (7): object, _BBox, _P, _Raster, Minimal transform stub with Revit-like OfPoint semantics., test_rotated_link_binning_uses_tight_uv_aabb_not_host_aabb(), _TransformZ

### Community 53 - "Collection Error Diagnostics"
Cohesion: 0.17
Nodes (5): MockCategory, MockGeometry, MockId, # NOTE: In unit test environment without Revit API, this will track 'exception', Mock Geometry collection.

### Community 54 - "CSV Model Presence Tests"
Cohesion: 0.27
Nodes (12): _mk_raster(), A cell with only model_proxy_key set (mask=False) must not be empty under ink mo, ViewRaster.has_model_proxy must return True when only proxy_key is set., test_csv_metrics_edge_counts_edges(), test_csv_metrics_ink_counts_occupancy_fill(), test_csv_metrics_ink_counts_proxy_key_only(), test_csv_metrics_occ_ignores_edges(), test_has_model_proxy_proxy_key_only() (+4 more)

### Community 55 - "OBB Geometry"
Cohesion: 0.18
Nodes (6): OBB, Oriented bounding box proxy for LINEAR elements.      Captures orientation of lo, Length along the long axis (2 * max extent)., Length along the short axis (2 * min extent)., Return 4 corner points for stamping edges., Return 4 edge segments [(u0,v0), (u1,v1)] for stamping.

### Community 56 - "Raster Cell Operations"
Cohesion: 0.17
Nodes (10): _cell_in_model_clip(), _commit_polygon_mask(), _extract_source_type(), Raster data structures for VOP interwoven pipeline.  Provides ViewRaster and Til, # IMPORTANT: Inset model clip by half a cell so no raster cell, Return cached boolean flat array [W*H] for model_clip_bounds.          Returns N, # IMPORTANT: match _cell_in_model_clip semantics by insetting model clip by half, ffected to model writes only.     Returns True if (i,j) lies within model_clip_b (+2 more)

### Community 57 - "Occlusion Saturation Tracker"
Cohesion: 0.17
Nodes (3): OcclusionTracker, Lightweight per-view occlusion diagnostics tracking., Aggregates low-cost occlusion diagnostics for a single view render.

### Community 58 - "Annotation Collection Policy"
Cohesion: 0.23
Nodes (11): annotation_included_bic_names(), Category policy (single source of truth).  Notes ----- - Must be importable unde, Category names collected by the VOP annotation pass.      Used by view_raster to, Resolve a BuiltInCategory name to a Category integer id for a given doc., Resolve BuiltInCategory names to integer category ids for this doc (cached)., Return CategoryType.Model from the Revit API, or None if outside Revit., resolve_category_ids(), _try_get_category_id() (+3 more)

### Community 59 - "CSV Export Test Notes"
Cohesion: 0.17
Nodes (7): Test strategy diagnostics integration in CSV export., Test that percentage columns are in valid 0-100 range., Test that build_vop_csv_row works without strategy_diag (backward compat)., Test that strategy counts sum to expected totals., Test that CSV export doesn't crash if diagnostic extraction fails., Test that build_vop_csv_row correctly extracts strategy statistics., TestCSVExportDiagnostics

### Community 60 - "Depth Convention Tests"
Cohesion: 0.23
Nodes (11): Depth convention contract: w = dot(element - origin, forward) must increase with, For RCP (looking up), elements below the cut plane are closest to the viewer., Return only the depth component w., For floor plan view, element at cut plane (high Z) must have smaller w     than, Simulated depth test: floor slab (w_occ) must block Level-1 wall (w_depth)., For a section looking in +Y, element at Y=5 is closer than Y=20.      Revit View, test_floor_plan_closer_element_has_smaller_w(), test_floor_plan_depth_test_floor_occludes_wall() (+3 more)

### Community 61 - "AREAL Extraction Contract Tests"
Cohesion: 0.47
Nodes (11): _call_name(), _calls_named(), _find_element_loop(), _find_function(), _is_elem_class_areal_if(), Static regression tests for the AREAL HIGH single-extraction contract., test_areal_path_does_not_probe_directional_high_or_low_keys_before_extraction(), test_areal_path_extracts_geometry_once_before_raster_decompose() (+3 more)

### Community 62 - "Manifest Generation Tool"
Cohesion: 0.24
Nodes (11): generate_manifest(), main(), normalize_csv_for_hashing(), Write manifest entries to file.      Format:         CSV  filename.csv  <hash>, Normalize CSV content for hashing by excluding volatile columns.      Args:, Compute SHA256 hash of a file., Compute SHA256 hash of content bytes., Generate manifest entries from output directory.      Args:         output_dir: (+3 more)

### Community 63 - "Cell Footprint Computation"
Cohesion: 0.20
Nodes (4): CellRectFootprint, HullFootprint, Footprint defined by a convex hull in UV space.     Rasterized conservatively vi, Footprint wrapper for a CellRect, future-proofing for hull footprints.

### Community 64 - "Cell Size Column Tests"
Cohesion: 0.20
Nodes (8): test_core_row_contains_cellsize_resolution_values(), test_get_core_csv_header_includes_cellsize_resolution_columns(), get_core_csv_header(), get_occlusion_csv_header(), Get header for core CSV file., Get header for occlusion diagnostics CSV file., Initialize streaming exporter.          Args:             output_dir: Base outpu, Initialize CSV writers for incremental writing.

### Community 65 - "PNG Export"
Cohesion: 0.27
Nodes (10): export_pipeline_results_to_pngs(), _export_png_dotnet(), _export_png_pillow(), export_raster_to_png(), PNG export for VOP interwoven pipeline rasters.  Generates visual representation, Export VOP raster to PNG image with color-coded occupancy.      Color Legend:, PNG export via NumPy + Pillow. Called only when both are available., Export VOP raster to PNG. Uses Pillow if available, System.Drawing otherwise. (+2 more)

### Community 66 - "Location Curve Silhouette"
Cohesion: 0.20
Nodes (10): _iter_solids(), _location_curve_obb_silhouette(), _order_points_by_connectivity(), If elem is a LinkedElementProxy, map link-space XYZ -> host-space XYZ.      IMPO, Extract true silhouette edges based on view direction.      This preserves conca, Order points by spatial connectivity (simple greedy approach).      Args:, Iterate all solids in geometry (recursively).      Args:         geom: Geometry, Build a thin oriented quad around the projected LocationCurve.     Great for dia (+2 more)

### Community 67 - "Source Identity (HOST/LINK/DWG)"
Cohesion: 0.31
Nodes (6): make_source_identity(), Source identity normalization for VOP interwoven.  Purpose - Establish a single, Return a normalized source identity dict.      This is the only place that shoul, test_make_source_identity_accepts_known_types(), test_make_source_identity_rejects_bad_type(), test_make_source_identity_requires_nonempty_id()

### Community 68 - "Bare Except Linter"
Cohesion: 0.53
Nodes (8): Hit, _iter_py_files(), _load_whitelist(), main(), int, str, _repo_rel(), scan()

### Community 69 - "Raster Decomposition"
Cohesion: 0.39
Nodes (7): decompose_to_rects(), Decompose flat raster cell indices into maximal covered rectangles.      Args:, _cells_from_rect(), test_decompose_to_rects_covers_l_shape_without_overlap(), test_decompose_to_rects_has_no_silent_24_rect_cap(), test_rasterize_silhouette_loops_out_cells_includes_occluding_edges(), test_rasterize_silhouette_loops_populates_out_cells()

### Community 70 - "Raster Internals Notes"
Cohesion: 0.29
Nodes (4): Update filled count for tile containing cell.          Args:             cell_i,, Update minimum W-depth for tile containing cell.          Args:             cell, Centralized cell write with depth testing (MANDATORY contract).          Args:, Get tile index for cell (i, j).          Args:             cell_i: Cell column i

### Community 71 - "Raster Depth Buffer Notes"
Cohesion: 0.25
Nodes (4): True if depth-tested interior occupancy is present at idx., True if a visible model edge label is present at idx., True if proxy presence is present at idx (mask OR key label)., Unified "model present" predicate with explicit mode.          mode:           -

### Community 72 - "Cache Signature Verification"
Cohesion: 0.33
Nodes (5): Test script to verify VOP cache signature enhancement (Phase 1).  This script va, Test the element ID collection and signature generation.      Run this in Revit, Inspect actual cache files to verify schema v2 format.      Args:         cache_, test_cache_file_inspection(), test_signature_collection()

### Community 73 - "NumPy & Pillow Backend"
Cohesion: 0.33
Nodes (5): ensure_numpy(), ensure_pillow(), vop_interwoven/np_backend.py  NumPy and Pillow availability detection with optio, Return True if NumPy is importable.      If auto_install=True and NumPy is absen, Return True if Pillow is importable.      If auto_install=True and Pillow is abs

### Community 75 - "Proxy Occupancy Channel Tests"
Cohesion: 0.70
Nodes (4): _make_raster(), test_proxy_edges_enabled_perimeter_only_to_proxy_channel(), test_proxy_fill_affects_occlusion_not_model_ink_by_default(), test_real_silhouette_writes_model_ink_edges()

### Community 76 - "Bootstrap & Dependency Check"
Cohesion: 0.60
Nodes (4): _install(), _is_importable(), VOP NumPy + Pillow Bootstrap ============================ Run once per machine (, run()

### Community 78 - "Tier B Proxy Sampling"
Cohesion: 0.67
Nodes (3): Tier-B proxy: sample geometry and return list of (u, v, w) points  in view-space, sample_element_uvw_points(), _sample_geom_object()

### Community 79 - "Test Configuration"
Cohesion: 0.50
Nodes (3): Path, pytest_ignore_collect(), Prevent collection of Dynamo/Revit integration tests unless explicitly enabled.

## Knowledge Gaps
- **29 isolated node(s):** `Path`, `bool`, `int`, `str`, `_Id` (+24 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **6 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `StrategyDiagnostics` connect `Strategy Performance Tracker` to `Dynamo Integration Tests`, `Convex Hull & View Coordinates`, `Strategy Tracker Tests`, `Pipeline Diagnostics Tests`, `AREAL Extraction Tests`, `CSV Export & Tests`, `Collection Diagnostics Tests`, `Link Transform Binning Tests`, `Collection Error Diagnostics`, `AREAL Geometry Extraction`, `CSV Export Test Notes`?**
  _High betweenness centrality (0.110) - this node is a cross-community bridge._
- **Why does `Config` connect `Dynamo Integration Tests` to `Cell Size Column Tests`, `Convex Hull & View Coordinates`, `UV Classification Engine`, `Metrics State Scanner`, `Pipeline Diagnostics Tests`, `Thin Runner Streaming`, `Math Utilities & AABB`, `Front-Face Loops Tests`, `CSV Export & Tests`, `Revit Element Collection`, `Streaming Manifest Tests`, `Metrics Manifest Tests`, `CSV Cache Normalization Tests`, `Raster Pipeline Design Notes`, `CSV Export Test Notes`, `Config Hash & Cache Reset`, `Raster Test Rationale`?**
  _High betweenness centrality (0.105) - this node is a cross-community bridge._
- **Why does `ViewRaster` connect `Raster Core Rationale` to `Dynamo Integration Tests`, `Convex Hull & View Coordinates`, `UV Classification Engine`, `Metrics State Scanner`, `Occlusion Contract Tests`, `Math Utilities & AABB`, `CSV Export & Tests`, `Export Identity Tests`, `2D Bounds Geometry`, `CSV Cache Normalization Tests`, `Raster Pipeline Design Notes`, `Raster Test Rationale`, `Raster Polygon Clipping`, `Raster Drawing Algorithms`, `CSV Model Presence Tests`, `Raster Cell Operations`, `Raster Decomposition`, `Raster Internals Notes`, `Raster Depth Buffer Notes`, `Proxy Occupancy Channel Tests`, `Raster Debug Serialization`?**
  _High betweenness centrality (0.076) - this node is a cross-community bridge._
- **Are the 9 inferred relationships involving `Config` (e.g. with `TestCSVExportDiagnostics` and `test_config_areal_prefers_front_face_loops()`) actually correct?**
  _`Config` has 9 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `ViewRaster` (e.g. with `Bounds2D` and `TestTileMap`) actually correct?**
  _`ViewRaster` has 5 INFERRED edges - model-reasoned connections that need verification._
- **Are the 12 inferred relationships involving `Bounds2D` (e.g. with `TileMap` and `ViewRaster`) actually correct?**
  _`Bounds2D` has 12 INFERRED edges - model-reasoned connections that need verification._
- **Are the 10 inferred relationships involving `StrategyDiagnostics` (e.g. with `MockBBox` and `MockCategory`) actually correct?**
  _`StrategyDiagnostics` has 10 INFERRED edges - model-reasoned connections that need verification._