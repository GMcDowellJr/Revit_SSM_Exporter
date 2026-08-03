# Graph Report - .  (2026-08-03)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 1748 nodes · 2960 edges · 104 communities (94 shown, 10 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 37 edges (avg confidence: 0.61)
- Token cost: 7,423 input · 993 output

## Graph Freshness
- Built from commit: `dc97bb87`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Mock Objects for Testing
- Geometry Cache Testing
- 2D Region Processing
- Collection Policy Testing
- Occlusion Contract Testing
- VOP Implementation Planning
- Final State Scanner Tests
- View Metadata Consistency Tests
- Streaming Pipeline Processing
- Manifest Loader Testing
- Model Linework Analysis
- Core Utility Files
- VOP Code Mapping
- Configuration Testing
- ViewRaster Testing
- Image Alignment Probing
- Phase Validation Tests
- External Source Probing
- Graphics Semantics Probing
- Transaction Group Export
- Diagnostic Tracking
- Areal Geometry Extraction
- Abstract Syntax Tree Utilities
- Pipeline Diagnostics Testing
- CSV Metrics Overview
- Color ID Buffer Testing
- Color ID Buffer Extraction
- CSV Export Testing
- Early Out Optimization Testing
- View Capability Testing
- Strategy Diagnostics Testing
- Element Caching Architecture
- Geometry Classification Testing
- View Raster Export
- Coding Principles Overview
- UV Classification Testing
- Exception Handling Fixes
- Core Data Structures
- Transform Binning Testing
- Depth Convention Testing
- Exclusion Filter Testing
- Front Face Strategy Testing
- Link Collector Testing
- Memory Telemetry Utilities
- Project Documentation Overview
- Operational Review Insights
- Bounding Box Policy Testing
- Interconnected Domain Patterns
- Occlusion Semantics Diagnostics
- Face Selection Testing
- UV AABB Proxy Testing
- AREAL Extraction Contract Testing
- View Coordinate System
- Roadmap and Priorities
- CSV Export Diagnostics Testing
- Manifest Generation
- Golden Baseline Testing
- Occupancy Cell Metrics
- Pipeline Contract Proposals
- Repository Scanning Utilities
- Model Presence Testing
- Element Cache Testing
- Raster Semantics Testing
- Annotation Bounds Calculation
- Metrics Relationships Overview
- Proxy Edge Rendering
- Testing Workflow Overview
- View Capability Infrastructure
- Error Troubleshooting
- Configuration Hashing
- Depth Ordering Architecture
- Pipeline Metrics Testing
- Raster Decomposition Testing
- Cache Signature Verification
- NumPy and Pillow Setup
- Rasterization Strategy Development
- Element ID Override Testing
- Occupancy Channel Testing
- VOP Bootstrap Process
- Workflow Automation
- Testing Overview
- Copilot Instructions
- Test Collection Management
- Tile Depth Conflict Checking
- Cache Key Mapping
- Architecture Principles Overview
- Performance Diagnostics Constants
- Element Binning
- Geometry Transform Diagnosis
- View Identity Extraction
- World-Space Loop Projection
- View Volume Resolution
- Enumeration
- Repository Documentation
- Metrics Validation Tools
- Validated Metadata

## God Nodes (most connected - your core abstractions)
1. `Config` - 72 edges
2. `Files` - 40 edges
3. `Stage A Image Alignment Probe` - 36 edges
4. `load_manifest()` - 29 edges
5. `_FakeElem` - 26 edges
6. `_make_raster()` - 24 edges
7. `scan_final_state_totals()` - 22 edges
8. `VOP Interwoven Pipeline - Progressive Implementation Plan` - 22 edges
9. `_include()` - 21 edges
10. `Diagnostic tracking` - 20 edges

## Surprising Connections (you probably didn't know these)
- `Two-Tier Primitive Cache Contract (Proposed, Not Implemented)` --semantically_similar_to--> `RootStyleCache (View-level Metrics Cache)`  [INFERRED] [semantically similar]
  Pipeline_Approach_Comparison.md → CURRENT_ARCHITECTURE.md
- `test_config_areal_prefers_front_face_loops()` --calls--> `Config`  [INFERRED]
  tests/test_front_face_loops_strategy.py → vop_interwoven/config.py
- `TestUVClassification` --uses--> `Config`  [INFERRED]
  tests/test_geometry.py → vop_interwoven/config.py
- `TestProxyGeneration` --uses--> `Config`  [INFERRED]
  tests/test_geometry.py → vop_interwoven/config.py
- `TestClassificationWorkflows` --uses--> `Config`  [INFERRED]
  tests/test_geometry.py → vop_interwoven/config.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Graphify References** — agents_md, copilot_skills_graphify_skill, graphify_add_watch, graphify_exports, graphify_extraction_spec, graphify_github_merge, graphify_hooks, graphify_query, graphify_transcribe, graphify_update, github_workflow_graphify, tests_output_interconnected_domain_patterns [EXTRACTED 0.75]
- **Stage A Probes** — tests_dynamo_probe_stage_a_external_sources, tests_dynamo_probe_stage_a_graphics_semantics, tests_dynamo_probe_stage_a_image_alignment, tests_dynamo_probe_stage_a_model_linework, tests_dynamo_probe_stage_a_transaction_group [EXTRACTED 0.75]
- **VOP Interwoven Pipeline Components** — vop_interwoven_config, vop_interwoven_pipeline, vop_interwoven_entry_dynamo, vop_interwoven_csv_export, vop_interwoven_png_export, vop_interwoven_streaming, vop_interwoven_core_raster, vop_interwoven_core_geometry, vop_interwoven_core_silhouette, vop_interwoven_core_areal_extraction, vop_interwoven_core_face_selection, vop_interwoven_core_element_cache, vop_interwoven_core_cache, vop_interwoven_core_diagnostics, vop_interwoven_core_math_utils, vop_interwoven_core_footprint, vop_interwoven_core_hull, vop_interwoven_core_pca2d, vop_interwoven_core_source_identity, vop_interwoven_revit_view_basis, vop_interwoven_revit_collection, vop_interwoven_revit_annotation, vop_interwoven_revit_linked_documents, vop_interwoven_revit_collection_policy, vop_interwoven_revit_safe_api, vop_interwoven_revit_tierb_proxy, vop_interwoven_diagnostics_strategy_tracker, vop_interwoven_export_csv [EXTRACTED 0.75]

## Communities (104 total, 10 thin omitted)

### Community 0 - "Mock Objects for Testing"
Cohesion: 0.05
Nodes (62): object, MockBBox, MockCategory, MockConfig, MockElement, MockId, MockRaster, MockView (+54 more)

### Community 1 - "Geometry Cache Testing"
Cohesion: 0.07
Nodes (51): _AlwaysMatch, _AlwaysMismatch, _AlwaysNone, _make_value(), Tests for GeometryCache (persistent AREAL geometry cache).  Covers: - Basic get/, Entry for element not in elem_cache is retained., Entries that have no bbox_fingerprint field are always kept., Record-shaped AREAL entries validate nested HIGH fingerprints. (+43 more)

### Community 2 - "2D Region Processing"
Cohesion: 0.07
Nodes (51): float, build_2d_regions(), classify_tier_by_rect(), compute_per_cell_depth_for_cells(), Coverage, depth_sort_key(), DepthBuffer, Envelope (+43 more)

### Community 3 - "Collection Policy Testing"
Cohesion: 0.06
Nodes (26): Categories, _FakeCategory, _FakeDoc, _FakeElem, _FakeId, _include(), Walls were in the old allowlist -- verify they still work., An explicitly excluded name must be rejected even if CategoryType=Model. (+18 more)

### Community 4 - "Occlusion Contract Testing"
Cohesion: 0.06
Nodes (50): _make_raster(), Occlusion contract tests.  Policy (from pipeline.py / README):   - Only AREAL el, rasterize_polygon_to_proxy must write normally when w_occ is empty., rasterize_polygon_to_proxy must write when this element is closer than w_occ., rasterize_polygon_to_proxy must write model_proxy_key for interior cells., rasterize_polygon_to_proxy must not touch model_edge_key., AREAL MEDIUM/LOW boundary ink (rasterize_closed_loops_to_proxy_edges) must not w, AREAL LOW open polylines (rasterize_open_polylines_to_proxy_edges) must not writ (+42 more)

### Community 5 - "VOP Implementation Planning"
Cohesion: 0.07
Nodes (49): CSV Output Files, Current Status: ✅ Feature Complete, Dependency Graph, Documentation, Example Iteration, For Each Phase, Future Enhancements, Git Workflow Per Phase (+41 more)

### Community 6 - "Final State Scanner Tests"
Cohesion: 0.09
Nodes (44): _manifest(), _raster_fixture(), ExtFinalCells_Only must be zero when every ext cell also has host content., ExtFinalCells_Only must NOT count a cell just because occ_host was beaten., occ_host must be True even when HOST loses the depth test to a closer LINK eleme, test_ext_cells_only_excludes_cells_with_host_and_link_overlap(), test_ext_cells_only_host_loses_depth_still_records_spatial_presence(), test_ext_cells_only_resets_do_not_falsely_elevate_count() (+36 more)

### Community 7 - "View Metadata Consistency Tests"
Cohesion: 0.07
Nodes (30): _Cfg, test_core_row_uses_view_unique_id_without_view_object(), test_root_cache_metadata_carries_view_unique_id(), test_root_cache_row_payload_stores_uid_from_pascal_metadata(), test_vop_cache_row_uses_view_result_uid_when_cached_payload_missing_uid(), test_vop_row_uses_view_unique_id_without_view_object(), test_extract_metrics_from_view_result_prefers_precomputed_metrics_without_raster_recompute(), _Cfg (+22 more)

### Community 8 - "Streaming Pipeline Processing"
Cohesion: 0.06
Nodes (33): test_streaming_vop_header_uses_cfg_and_includes_metadata_for_manifest_mode(), process_document_views_streaming(), process_with_streaming(), Streaming pipeline processing for VOP Interwoven.  Enables incremental processin, Manages incremental export of pipeline results., Initialize streaming exporter.          Args:             output_dir: Base outpu, Initialize CSV writers for incremental writing., Process views with per-view callback and cache support. (+25 more)

### Community 9 - "Manifest Loader Testing"
Cohesion: 0.11
Nodes (38): bytes, test_manifest_loader_accepts_locked_v1_and_hash_is_stable(), test_manifest_loader_rejects_bad_required_types(), test_manifest_loader_rejects_unknown_top_level_keys(), test_manifest_loader_rejects_wrong_schema_version(), color(), Colors, compare_csv_files() (+30 more)

### Community 10 - "Model Linework Analysis"
Cohesion: 0.12
Nodes (40): _analyze_image(), _apply_category_linework(), _apply_element_id_reference(), _apply_mode(), _candidate_repo_roots(), _classify_mode(), _collect_model_category_ids(), _connected_components() (+32 more)

### Community 11 - "Core Utility Files"
Cohesion: 0.05
Nodes (39): `bootstrap.py`, `config.py`, `core/areal_extraction.py`, `core/cache.py`, `core/diagnostics.py`, `core/element_cache.py`, `core/face_selection.py`, `core/footprint.py` (+31 more)

### Community 12 - "VOP Code Mapping"
Cohesion: 0.05
Nodes (36): VOP Interwoven Code Map (Authoritative), VOP Interwoven Symbol Index, VOP Interwoven Trace Map (Approximate Call Tree), Scope, All top-level definitions, `collect_view_elements`, `get_element_silhouette`, High-signal callsite details (approx) (+28 more)

### Community 13 - "Configuration Testing"
Cohesion: 0.07
Nodes (20): test_csv_config_hash_matches_root_cache_config_hash_for_real_config(), test_export_pipeline_to_csv_manifest_header_exact_order_when_compat_off(), test_get_vop_csv_header_uses_manifest_columns_when_compat_off(), test_core_row_contains_cellsize_resolution_values(), Config, Configuration for VOP interwoven pipeline.      Attributes:         tile_size (i, Initialize VOP configuration.          Args:             tile_size: Base tile si, Compute optimal tile size based on grid dimensions.          Args:             g (+12 more)

### Community 14 - "ViewRaster Testing"
Cohesion: 0.06
Nodes (22): Test ViewRaster data structure., Create test view raster., Test view raster initialization., Test cell index calculation., Test cell filling with depth (deprecated method)., Test centralized cell write with depth testing., Test TileMap spatial acceleration structure., occ_host/link/dwg must accumulate — a later winner must not erase prior True fla (+14 more)

### Community 15 - "Image Alignment Probing"
Cohesion: 0.14
Nodes (34): Stage A Image Alignment Probe, _analyze_image(), _bounds_dict(), _bounds_tuple(), _build_marker_ogs(), _candidate_repo_roots(), _capture_crop_state(), _compute_bounds() (+26 more)

### Community 16 - "Phase Validation Tests"
Cohesion: 0.08
Nodes (23): `test_all_phases.py` - Quick Validation, Test Scripts, `thinrunner.py` - Quick Iteration Runner, All Phases Test: Quick validation of all implemented phases  Copy this code into, Import Check - Verify all modules load correctly  This tests if the linked docum, Test Linked Documents Collection - Debug Script  Paste this into a Dynamo Python, Minimal Single View Test - Step-by-step diagnostic  This will show exactly where, Phase 1 Test: View Basis & Coordinate System  Copy this code into a Dynamo Pytho (+15 more)

### Community 17 - "External Source Probing"
Cohesion: 0.14
Nodes (31): _analyze(), _candidate_repo_roots(), _classify_variant(), _collect_expanded(), _diff(), _discover_assignments(), _doc(), _element_id_set() (+23 more)

### Community 18 - "Graphics Semantics Probing"
Cohesion: 0.13
Nodes (30): _analyze_image(), _candidate_repo_roots(), _collect_elements(), _detach_template(), _diff(), _disable_filters(), _doc(), _ensure_repo_import_path() (+22 more)

### Community 19 - "Transaction Group Export"
Cohesion: 0.15
Nodes (30): _apply_temporary_changes(), _as_list(), _build_flat_color_ogs(), _diff_values(), _discover_and_rename_tiff(), _document_label(), _ensure_revit_api_reference(), _exception_record() (+22 more)

### Community 20 - "Diagnostic Tracking"
Cohesion: 0.14
Nodes (23): _as_tuple(), _StubView, test_bounds_budget_absent_keeps_med_confidence_for_extents(), test_bounds_budget_trigger_downgrades_confidence_to_low_and_reports_budget(), _as_tuple(), _StubView, test_resolve_bounds_cap_triggers_with_before_after_reporting(), test_resolve_bounds_crop_off_extents_failure_falls_back_low_confidence() (+15 more)

### Community 21 - "Areal Geometry Extraction"
Cohesion: 0.07
Nodes (26): AREAL element geometry extraction, General caching utilities, Front-facing face selection, Footprint computation, Convex hull utilities, Bounds, rectangle operations, 2D PCA for OBB fitting, Multi-strategy silhouette extraction (+18 more)

### Community 22 - "Abstract Syntax Tree Utilities"
Cohesion: 0.21
Nodes (25): AST, Module, build_index(), build_trace_tree(), _call_name(), DefInfo, _format_def(), _is_excluded_dir() (+17 more)

### Community 23 - "Pipeline Diagnostics Testing"
Cohesion: 0.08
Nodes (12): Test that CSV export works., Test that config can round-trip with export_strategy_diagnostics., Test that pipeline logic for creating diagnostics works., Test that pipeline can skip diagnostics when disabled., Test that export_strategy_diagnostics defaults to False., Test that diagnostic tracking failures don't crash., Test that export_strategy_diagnostics can be disabled., Test that export_strategy_diagnostics is included in to_dict(). (+4 more)

### Community 24 - "CSV Metrics Overview"
Cohesion: 0.08
Nodes (24): 1. Core Metrics (`views_core_YYYY-MM-DD.csv`), 2. VOP Extended Metrics (`views_vop_YYYY-MM-DD.csv`), Core CSV Headers (18 columns), CSV Headers Reference, CSV Validation Invariant, Current Output Format (VOP Interwoven), Current Outputs Generated, Dependencies (+16 more)

### Community 25 - "Color ID Buffer Testing"
Cohesion: 0.14
Nodes (14): _paint(), _palette(), _is_near_black(), _is_near_white(), Unit tests for VOP interwoven Stage A color-ID buffer palette generation.  Cover, build_palette() must never hand out a color near either reserved corner., Regression guard for the graphics_semantics_probe finding: the         lattice u, choose_step()'s capacity estimate must match what build_palette() can deliver. (+6 more)

### Community 26 - "Color ID Buffer Extraction"
Cohesion: 0.13
Nodes (21): _ogs(), _solid_pattern_id(), _get_solid_pattern_id(), _ogs_for(), _collect_reference_elements(), _build_flat_color_ogs(), export_color_id_buffer_view(), _export_tiff() (+13 more)

### Community 27 - "CSV Export Testing"
Cohesion: 0.14
Nodes (15): Phase 7: CSV Export Test  Test CSV export functionality with invariant validatio, Phase 8a: Annotation Collection & Rasterization Test  Test annotation collection, filter_supported_views(), get_all_floor_plans(), get_all_sections(), get_all_views_in_model(), get_views_from_input_or_current(), Dynamo-friendly entry points for VOP interwoven pipeline.  These functions handl (+7 more)

### Community 28 - "Early Out Optimization Testing"
Cohesion: 0.14
Nodes (18): _snapshot_occ(), test_early_out_is_pure_optimization_no_output_difference(), export_view_raster(), _extract_view_summary(), _make_areal_geom_cache_key(), _perf_ms(), _perf_now(), process_document_views() (+10 more)

### Community 29 - "View Capability Testing"
Cohesion: 0.22
Nodes (17): FakeView, test_crop_bounds_capability_requires_model_geometry(), test_drafting_is_annotation_only(), test_floorplan_is_model_and_annotation(), test_legend_view_is_annotation_only(), test_numeric_viewtype_floorplan_is_model_capable(), test_template_view_is_rejected(), Best-effort, Revit-free view type name extraction for gating + tests.     Return (+9 more)

### Community 30 - "Strategy Diagnostics Testing"
Cohesion: 0.11
Nodes (9): Diagnostic script to test silhouette strategy selection and occlusion vs occupan, Test that StrategyDiagnostics can be created., Test summary statistics calculation., Test CSV export format and content., Create fresh diagnostics instance for each test., Test print_summary executes without errors., Test basic element classification tracking., Test AREAL strategy success/failure tracking. (+1 more)

### Community 31 - "Element Caching Architecture"
Cohesion: 0.10
Nodes (21): LRU Element Cache with Fingerprint Keying, RootStyleCache (View-level Metrics Cache), Streaming Pipeline (Memory-Aware Per-View Export), View Signature (Cache Match Token), A) Purpose & Non-Goals, B) Public entry points / API surface, C) Pipeline stages and ownership boundaries, D) Geometry extraction authority model (+13 more)

### Community 32 - "Geometry Classification Testing"
Cohesion: 0.12
Nodes (11): run_tests.sh script, Unit tests for VOP interwoven geometry classification and proxy generation.  Tes, Test complete classification workflows (realistic scenarios)., Test typical door classification (TINY)., Test typical window classification (TINY or LINEAR)., Test typical wall classification (LINEAR)., Test typical floor classification (AREAL)., Run all tests and print results. (+3 more)

### Community 33 - "View Raster Export"
Cohesion: 0.17
Nodes (16): _canonical_name(), _dot(), export_pipeline_views_to_pngs(), export_view_image(), _is_annotation_only_view(), _prepare_view_for_export(), View raster export: renders Revit views as-is to PNG for side-by-side comparison, Export a single Revit view to PNG at the specified pixel dimensions.      Tempor (+8 more)

### Community 34 - "Coding Principles Overview"
Cohesion: 0.14
Nodes (16): 1. No Silent Failure, 2. Explicit Semantics, 3. Single Source of Truth, 4. Worst-Case First, 5. Small, Reviewable Changes, No Silent Failure Rule, Single Source of Truth Rule, Source Identity Normalization (HOST | LINK | DWG) (+8 more)

### Community 35 - "UV Classification Testing"
Cohesion: 0.12
Nodes (9): Test UV-based element classification., Create default config for tests., Test TINY classification (both dimensions <= tiny_max)., Test LINEAR classification (one dimension thin, one long)., Test AREAL classification (both dimensions large)., Test classification at threshold boundaries., Test classification with custom thresholds., Test classification with edge case dimensions. (+1 more)

### Community 36 - "Exception Handling Fixes"
Cohesion: 0.18
Nodes (15): apply_fixes(), find_except_blocks(), generate_fix(), get_function_name(), get_phase_from_context(), has_diag_in_scope(), main(), process_file() (+7 more)

### Community 37 - "Core Data Structures"
Cohesion: 0.12
Nodes (6): UV classification, proxy generation, Core data structures and algorithms for VOP interwoven pipeline.  Modules: - ras, ViewRaster, TileMap, Lightweight per-view occlusion diagnostics tracking., Aggregates low-cost occlusion diagnostics for a single view render., Strategy performance tracking

### Community 38 - "Transform Binning Testing"
Cohesion: 0.26
Nodes (10): _BBox, _P, _Raster, Minimal transform stub with Revit-like OfPoint semantics., test_rotated_link_binning_uses_tight_uv_aabb_not_host_aabb(), _TransformZ, crop_box_from_uv_bounds(), View basis extraction for VOP interwoven pipeline.  Provides view coordinate sys (+2 more)

### Community 39 - "Depth Convention Testing"
Cohesion: 0.20
Nodes (13): Depth convention contract: w = dot(element - origin, forward) must increase with, For RCP (looking up), elements below the cut plane are closest to the viewer., Return only the depth component w., For floor plan view, element at cut plane (high Z) must have smaller w     than, Simulated depth test: floor slab (w_occ) must block Level-1 wall (w_depth)., For a section looking in +Y, element at Y=5 is closer than Y=20.      Revit View, test_floor_plan_closer_element_has_smaller_w(), test_floor_plan_depth_test_floor_occludes_wall() (+5 more)

### Community 40 - "Exclusion Filter Testing"
Cohesion: 0.14
Nodes (8): Test exclusion filter functionality in collection.  Phase 2B: Verifies that the, Test that the exclusion filter parameter was added correctly., Test that collect_view_elements has exclude_ids parameter., Test that exclude_ids defaults to None for backward compatibility., Test that original parameters are preserved in correct order., Test that exclude_ids won't break existing positional callers., Test that the collection module imports without errors after changes., TestExclusionFilterSignature

### Community 41 - "Front Face Strategy Testing"
Cohesion: 0.23
Nodes (12): _Id, _import_config_and_silhouette(), int, Import using the real package first (pytest runs with repo root on sys.path),, Acceptance boundary:       AREAL strategies must try front-facing planar face lo, Verifies dispatch order + passthrough semantics without requiring Revit geometry, _StubElem, _StubRaster (+4 more)

### Community 42 - "Link Collector Testing"
Cohesion: 0.29
Nodes (9): _FakeBBox, _FakeCategory, _FakeCollector, _FakeElem, _FakeId, _FakeLinkDoc, _FakeLinkInst, _FakeView (+1 more)

### Community 43 - "Memory Telemetry Utilities"
Cohesion: 0.19
Nodes (6): _force_clr_gc(), _get_memory_mb(), MemoryTracker, Memory telemetry helpers for Dynamo/Python.NET runtime., Run CLR GC triple-call pattern and report private-memory delta., Return current (working_set_mb, private_bytes_mb) or (None, None).

### Community 44 - "Project Documentation Overview"
Cohesion: 0.15
Nodes (12): CLAUDE.md - AI Assistant Guide for Revit SSM Exporter, Code Quality Checks, Commentary Markers in Code, Directory Structure, Documentation Hierarchy, Environment Notes, graphify, Key Files to Understand First (+4 more)

### Community 45 - "Operational Review Insights"
Cohesion: 0.15
Nodes (12): Aligned, Divergent, Executive Assessment, Highest-Leverage Next Moves, HISTORICAL OPERATIONAL REVIEW / SUPERSEDED, Implementation vs Intent, Indeterminate, Observed Architecture (+4 more)

### Community 46 - "Bounding Box Policy Testing"
Cohesion: 0.28
Nodes (10): _BBox, ElemModelOnly, ElemThrows, ElemViewWins, FakeView, _Id, _P, test_resolve_element_bbox_falls_back_to_model() (+2 more)

### Community 47 - "Interconnected Domain Patterns"
Cohesion: 0.17
Nodes (10): graphify, add, exports, extraction-spec, github-and-merge, hooks, query, transcribe (+2 more)

### Community 48 - "Occlusion Semantics Diagnostics"
Cohesion: 0.18
Nodes (12): AREAL Fallback Occlusion Semantics Contradiction (P0 Debt), Diagnostics Always-On Contract, Diagnostics Wiring Debt (TODO Placeholders in Exception Handlers), Dormant Per-View Disk Cache (view_cache_enabled=False), Proxy Edge Mask Mode Stub (_stamp_proxy_edges), Visibility Gating Stub (is_element_visible_in_view), Repository Operational Review (Historical/Superseded), ROADMAP - Prioritized Remaining Work (+4 more)

### Community 49 - "Face Selection Testing"
Cohesion: 0.26
Nodes (5): _FaceStub, Unit tests for deterministic planar front-face selection utilities.  These tests, Return a square polygon in *model* coords (plan view basis => model XY == UV)., _square_loop(), _XYZ

### Community 50 - "UV AABB Proxy Testing"
Cohesion: 0.17
Nodes (7): Test UV_AABB proxy creation from CellRect., Test UV_AABB width/height calculations., Test UV_AABB center calculation., Test UV_AABB edge generation for stamping., Test complete workflow: classify as TINY -> generate UV_AABB., Test proxy generation (UV_AABB creation)., TestProxyGeneration

### Community 51 - "AREAL Extraction Contract Testing"
Cohesion: 0.47
Nodes (11): _call_name(), _calls_named(), _find_element_loop(), _find_function(), _is_elem_class_areal_if(), Static regression tests for the AREAL HIGH single-extraction contract., test_areal_path_does_not_probe_directional_high_or_low_keys_before_extraction(), test_areal_path_extracts_geometry_once_before_raster_decompose() (+3 more)

### Community 52 - "View Coordinate System"
Cohesion: 0.18
Nodes (6): View coordinate system with origin and basis vectors.      Attributes:         o, Back-compat helper: accept XYZ or tuple, return view-local (u, v, w)., Check if view is plan-like (looking down Z axis).          Returns:, Check if view is elevation-like (horizontal view direction).          Returns:, Transform model-space point to view-local UV coordinates.          Args:, ViewBasis

### Community 53 - "Roadmap and Priorities"
Cohesion: 0.18
Nodes (11): 1) Gate or implement proxy `edges` mode, 2) Contract contradiction requiring code decision: AREAL fallback occlusion semantics, 3) Complete diagnostics wiring debt in exception handlers, 4) Remove or finalize dormant per-view disk cache path, 5) Implement explicit visibility-gating pass or narrow the contract, 6) Streaming signature-first TODO cleanup, 7) Align historical documentation footprint with current architecture doc, P0 (+3 more)

### Community 54 - "CSV Export Diagnostics Testing"
Cohesion: 0.18
Nodes (6): Test that percentage columns are in valid 0-100 range., Test that build_vop_csv_row works without strategy_diag (backward compat)., Test that strategy counts sum to expected totals., Test that CSV export doesn't crash if diagnostic extraction fails., Test that build_vop_csv_row correctly extracts strategy statistics., Test strategy diagnostics integration in pipeline.

### Community 55 - "Manifest Generation"
Cohesion: 0.20
Nodes (10): main(), normalize_csv_for_hashing(), Write manifest entries to file.      Format:         CSV  filename.csv  <hash>, Normalize CSV content for hashing by excluding volatile columns.      Args:, Compute SHA256 hash of a file., Compute SHA256 hash of content bytes., Generate manifest entries from output directory.      Args:         output_dir:, sha256_content() (+2 more)

### Community 56 - "Golden Baseline Testing"
Cohesion: 0.18
Nodes (11): `compare_golden.py` - Golden Baseline Comparison, Establishing Initial Golden Baseline, `generate_manifest.py` - Generate Golden Baseline, Integration with CI/CD, Notes, Scripts, See Also, SSM/VOP Exporter Tools (+3 more)

### Community 57 - "Occupancy Cell Metrics"
Cohesion: 0.22
Nodes (10): 8-State Occupancy Cell Partition Invariant, Annotation Type Classification (7 Types: TEXT/TAG/DIM/DETAIL/LINES/REGION/OTHER), External Cell Metrics (ExtFinalCells_DWG / _RVT Set Relationships), Multihot Model Class Cells (ModelClassCells_*), Legacy 4-Way Occupancy Partition (views_vop CSV), SSM-Compatible CSV Export Format, Dynamo Test Scripts README, Metrics Totals Relationships Cheat Sheet (+2 more)

### Community 58 - "Pipeline Contract Proposals"
Cohesion: 0.20
Nodes (10): A) Strict non-interwoven stage boundaries with explicit artifacts, B) Tier-1/Tier-2 cache contract separation, C) Authoritative Stage-5 visibility gating pass over already-projected primitives, D) Explicit B1/B2 model-hosted-2D split with allowlisted occluder upgrade path, E) Primitive context isolation in cache policy, Exporter Pipeline Comparison: Proposed Contract-Aligned Flow vs Current VOP Interwoven Code, Needs substantial new implementation (essentially from scratch), Practical migration path (minimal-risk order) (+2 more)

### Community 59 - "Repository Scanning Utilities"
Cohesion: 0.44
Nodes (8): Hit, _iter_py_files(), _load_whitelist(), main(), int, str, _repo_rel(), scan()

### Community 60 - "Model Presence Testing"
Cohesion: 0.36
Nodes (8): _mk_raster(), A cell with only model_proxy_key set (mask=False) must not be empty under ink mo, ViewRaster.has_model_proxy must return True when only proxy_key is set., test_csv_metrics_edge_counts_edges(), test_csv_metrics_ink_counts_occupancy_fill(), test_csv_metrics_ink_counts_proxy_key_only(), test_csv_metrics_occ_ignores_edges(), test_has_model_proxy_proxy_key_only()

### Community 61 - "Element Cache Testing"
Cohesion: 0.33
Nodes (5): test_element_cache_export_analysis_csv_includes_source_type(), test_element_cache_hit_upgrades_unknown_source_type(), test_export_view_element_map_json_merges_existing_file(), test_export_view_element_map_json_writes_view_index(), LRU element caching

### Community 62 - "Raster Semantics Testing"
Cohesion: 0.22
Nodes (6): A cell with model_proxy_key set but model_proxy_mask=False must not be empty., Proxy-key-only cells must not have w_occ written (no occlusion authority)., stamp_proxy_edge_idx must set model_proxy_mask so has_model_proxy returns True., test_proxy_key_only_no_mask_counts_as_model_present(), test_proxy_key_only_no_occlusion_written(), test_stamp_proxy_edge_sets_proxy_mask()

### Community 63 - "Annotation Bounds Calculation"
Cohesion: 0.25
Nodes (8): Produce bounds from annotation extents ONLY (no union with model/crop).     This, Compute XY bounds from view crop box (all 8 corners method).      Notes on API c, Compute EFFECTIVE view bounds in view-local UV.      Behaves as if the view had, Compute synthetic bounds from element extents in a view (crop-off / no-crop view, resolve_annotation_only_bounds(), synthetic_bounds_from_visible_extents(), xy_bounds_effective(), xy_bounds_from_crop_box_all_corners()

### Community 64 - "Metrics Relationships Overview"
Cohesion: 0.25
Nodes (8): 1) Exact equalities (must always hold), 2) Subset/superset inequalities (must always hold), 3) Useful derived groups from the 8-state partition, 4) Legacy `views_vop` 4-way presence buckets, 5) What is allowed to exceed what, 6) Quick triage sequence when numbers look wrong, Metrics Totals Relationships (Set/Superset/Subset Cheat Sheet), Model classes are multihot (not disjoint)

### Community 65 - "Proxy Edge Rendering"
Cohesion: 0.25
Nodes (8): _mark_rect_center_cell(), _mark_thin_band_along_long_axis(), Render TINY/LINEAR element: proxy edges + optional minimal mask.      Commentary, Stamp proxy edges into model_proxy_key layer.      NOTE: Edge rasterization is n, Mark center cell of rect in model_proxy_mask., Mark thin band along long axis of rect in model_proxy_mask., _render_proxy_element(), _stamp_proxy_edges()

### Community 66 - "Testing Workflow Overview"
Cohesion: 0.29
Nodes (6): Golden Baseline Regression Testing, Dynamo Test Scripts, Example Dynamo Python Node Setup, Next Steps, Output Format, Recommended Testing Workflow

### Community 67 - "View Capability Infrastructure"
Cohesion: 0.29
Nodes (7): 1) View capability split (model vs annotation-only), 2) View basis + bounds/crop primitives, 3) Candidate collection infrastructure, 4) Front-to-back ordering and depth hints, 5) Existing cache building blocks, 6) Annotation/model non-occlusion layering intent, Reusable with minimal effort

### Community 68 - "Error Troubleshooting"
Cohesion: 0.29
Nodes (7): "0 views processed", AttributeError: 'Bounds2D' object has no attribute 'min_x', "AttributeError: 'View3D' object has no attribute...", "ImportError: No module named vop_interwoven", "No elements found", "RuntimeError: Not running in Revit/Dynamo context", Troubleshooting

### Community 69 - "Configuration Hashing"
Cohesion: 0.29
Nodes (7): _cfg_hash(), _cropbox_fingerprint(), Returns a small, stable fingerprint of crop settings and extents.     We avoid r, Enhanced signature with element fingerprints for position/size tracking.      Mu, _safe_bool(), _safe_int(), _view_signature()

### Community 70 - "Depth Ordering Architecture"
Cohesion: 0.33
Nodes (6): Front-to-Back Depth Ordering for Model Processing, Interwoven Render Pipeline Architecture, Two-Tier Primitive Cache Contract (Proposed, Not Implemented), View Mode Resolution (MODEL_AND_ANNOTATION / ANNOTATION_ONLY / REJECTED), Pipeline Approach Comparison (Historical/Superseded), HISTORICAL ANALYSIS / SUPERSEDED

### Community 71 - "Pipeline Metrics Testing"
Cohesion: 0.60
Nodes (5): _fake_raster(), test_compute_manifest_metrics_payload_attaches_provenance_and_validation(), test_compute_manifest_metrics_payload_strict_fails_on_missing_required_primitive(), _compute_manifest_metrics_payload(), Compute manifest-scanned metrics totals and validation payload.

### Community 72 - "Raster Decomposition Testing"
Cohesion: 0.47
Nodes (3): _cells_from_rect(), test_decompose_to_rects_covers_l_shape_without_overlap(), test_decompose_to_rects_has_no_silent_24_rect_cap()

### Community 73 - "Cache Signature Verification"
Cohesion: 0.33
Nodes (5): Test script to verify VOP cache signature enhancement (Phase 1).  This script va, Test the element ID collection and signature generation.      Run this in Revit, Inspect actual cache files to verify schema v2 format.      Args:         cache_, test_cache_file_inspection(), test_signature_collection()

### Community 74 - "NumPy and Pillow Setup"
Cohesion: 0.33
Nodes (5): ensure_numpy(), ensure_pillow(), vop_interwoven/np_backend.py  NumPy and Pillow availability detection with optio, Return True if NumPy is importable.      If auto_install=True and NumPy is absen, Return True if Pillow is importable.      If auto_install=True and Pillow is abs

### Community 75 - "Rasterization Strategy Development"
Cohesion: 0.40
Nodes (5): Adding a New Rasterization Strategy, Adding CSV Export Columns, Common Development Tasks, Debugging Geometry Issues, Modifying Element Collection

### Community 76 - "Element ID Override Testing"
Cohesion: 0.40
Nodes (5): _apply_assignments(), _hide_element_ids(), _palette(), Attempt a Revit 2022+ LinkElementId-based override for a linked element.      Re, _try_color_link_element()

### Community 77 - "Occupancy Channel Testing"
Cohesion: 0.70
Nodes (4): _make_raster(), test_proxy_edges_enabled_perimeter_only_to_proxy_channel(), test_proxy_fill_affects_occlusion_not_model_ink_by_default(), test_real_silhouette_writes_model_ink_edges()

### Community 79 - "VOP Bootstrap Process"
Cohesion: 0.60
Nodes (4): _install(), _is_importable(), VOP NumPy + Pillow Bootstrap ============================ Run once per machine (, run()

### Community 80 - "Workflow Automation"
Cohesion: 0.50
Nodes (4): Automated Workflows, Branch Naming, Commit Message Convention, Git Workflow

### Community 81 - "Testing Overview"
Cohesion: 0.50
Nodes (4): Golden Baseline Testing, Running Tests, Test Coverage Areas, Testing

### Community 83 - "Copilot Instructions"
Cohesion: 0.50
Nodes (3): Core architecture constraints, GitHub Copilot Instructions — Revit SSM Exporter, graphify — Query the knowledge graph before browsing source

### Community 84 - "Test Collection Management"
Cohesion: 0.50
Nodes (3): Path, pytest_ignore_collect(), Prevent collection of Dynamo/Revit integration tests unless explicitly enabled.

### Community 87 - "Tile Depth Conflict Checking"
Cohesion: 0.50
Nodes (4): _get_ambiguous_tiles(), Check if tile has depth range conflicts (ambiguity) using a sweep (O(k log k))., Identify tiles with depth conflicts that need triangle resolution.      Args:, _tile_has_depth_conflict()

### Community 88 - "Cache Key Mapping"
Cohesion: 0.50
Nodes (4): _make_areal_high_conf_cache_key(), _quantize_view_dir(), Map vb.forward to one of six axis-aligned string tokens.      Returns one of: 'x, Build a view-independent cache key for AREAL HIGH-confidence geometry.      The

### Community 89 - "Architecture Principles Overview"
Cohesion: 0.67
Nodes (3): Core Architecture Principles, Element Classification System, Key Configuration Parameters

## Knowledge Gaps
- **270 isolated node(s):** `graphify — Query the knowledge graph before browsing source`, `Core architecture constraints`, `Path`, `bool`, `int` (+265 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **10 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Config` connect `Configuration Testing` to `Geometry Classification Testing`, `UV Classification Testing`, `Final State Scanner Tests`, `View Metadata Consistency Tests`, `Streaming Pipeline Processing`, `Front Face Strategy Testing`, `Pipeline Metrics Testing`, `ViewRaster Testing`, `Phase Validation Tests`, `UV AABB Proxy Testing`, `Areal Geometry Extraction`, `CSV Export Diagnostics Testing`, `Pipeline Diagnostics Testing`, `CSV Export Testing`?**
  _High betweenness centrality (0.177) - this node is a cross-community bridge._
- **Why does `export_color_id_buffer_view()` connect `Color ID Buffer Extraction` to `Element ID Override Testing`, `Graphics Semantics Probing`, `Areal Geometry Extraction`, `Color ID Buffer Testing`, `Early Out Optimization Testing`?**
  _High betweenness centrality (0.142) - this node is a cross-community bridge._
- **Why does `Test Scripts` connect `Phase Validation Tests` to `Testing Workflow Overview`, `CSV Export Testing`?**
  _High betweenness centrality (0.134) - this node is a cross-community bridge._
- **Are the 9 inferred relationships involving `Config` (e.g. with `test_csv_export_diagnostics.py` and `test_config_areal_prefers_front_face_loops()`) actually correct?**
  _`Config` has 9 INFERRED edges - model-reasoned connections that need verification._
- **What connects `graphify — Query the knowledge graph before browsing source`, `Core architecture constraints`, `Path` to the rest of the system?**
  _270 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Mock Objects for Testing` be split into smaller, more focused modules?**
  _Cohesion score 0.051839464882943144 - nodes in this community are weakly interconnected._
- **Should `Geometry Cache Testing` be split into smaller, more focused modules?**
  _Cohesion score 0.0701484895033282 - nodes in this community are weakly interconnected._