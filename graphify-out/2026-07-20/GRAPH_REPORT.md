# Graph Report - .  (2026-07-20)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 1593 nodes · 2535 edges · 101 communities (90 shown, 11 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 37 edges (avg confidence: 0.64)
- Token cost: 7,174 input · 948 output

## Graph Freshness
- Built from commit: `84eac00a`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Geometry Cache Tests
- Depth Buffer Management
- Fake Element Testing
- Mock Object Tests
- Occlusion Contract Tests
- VOP Pipeline Implementation
- Final State Scanner Tests
- View Unique ID Tests
- Manifest Loader Tests
- Core Module Files
- VOP Code Mapping
- Configuration Tests
- ViewRaster Tests
- Collection Diagnostics Tests
- AREAL Geometry Extraction
- Phase Validation Tests
- Diagnostic Tracking Tests
- Streaming Pipeline Processing
- Import Check Tests
- AST Utilities
- VOP Pipeline Documentation
- Pipeline Diagnostics Tests
- CSV Metrics Documentation
- Color ID Buffer Extraction
- LRU Element Cache
- Phase Test Scripts
- View Capabilities Tests
- Strategy Diagnostics Tests
- Geometry Classification Tests
- View Processing Infrastructure
- Thin Runner for Streaming
- View Raster Export
- Coding Principles
- UV Classification Tests
- Exception Handling Fixes
- Dynamo Test Scripts
- Transform Binning Tests
- Depth Convention Tests
- Exclusion Filter Tests
- Face Selection Strategy Tests
- Link Collector Tests
- Memory Telemetry Utilities
- AI Assistant Documentation
- Operational Review Documentation
- Bounding Box Policy Tests
- Face Selection Tests
- UV Proxy Generation Tests
- AREAL Extraction Tests
- View Coordinate System
- Proxy Visibility Contract
- CSV Export Diagnostics Tests
- Manifest Generation
- Golden Baseline Testing
- Occlusion Tracking
- Occupancy Metrics
- Diagnostics Contract
- Repository Scanning
- Graph Query Instructions
- Annotation Classification
- CSV Model Presence Tests
- Element Cache Tests
- Raster Semantics Tests
- Annotation Bounds Computation
- Metrics Relationships
- Proxy Element Rendering
- Depth Ordering Pipeline
- Signature Generation Utilities
- Metrics Manifest Tests
- Raster Decomposition Tests
- Cache Signature Verification
- NumPy and Pillow Setup
- Rasterization Strategy Development
- Occupancy Channel Tests
- VOP Bootstrap
- Element Processing Modes
- Workflow Documentation
- Testing Overview
- Copilot Instructions
- Test Collection Prevention
- Tile Depth Conflict Checks
- AREAL Cache Key Generation
- Development Status
- Core Architecture Principles
- Performance Constants
- View Identity Extraction
- AREAL Geometry Cache Key
- Enumeration Utilities
- Knowledge Graph Rebuild
- Repository README
- Extraction Subagent Prompt
- Domain Patterns
- Metrics Manifest Utilities
- Manifest Validation and Provenance

## God Nodes (most connected - your core abstractions)
1. `Config` - 72 edges
2. `Files` - 40 edges
3. `load_manifest()` - 29 edges
4. `_FakeElem` - 26 edges
5. `_make_raster()` - 24 edges
6. `scan_final_state_totals()` - 22 edges
7. `VOP Interwoven Pipeline - Progressive Implementation Plan` - 22 edges
8. `VOP Interwoven Pipeline README` - 22 edges
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
- **VOP Interwoven Pipeline Components** — vop_interwoven_config, vop_interwoven_pipeline, vop_interwoven_entry_dynamo, vop_interwoven_csv_export, vop_interwoven_png_export, vop_interwoven_streaming, vop_interwoven_core_raster, vop_interwoven_core_geometry, vop_interwoven_core_silhouette, vop_interwoven_core_areal_extraction, vop_interwoven_core_face_selection, vop_interwoven_core_element_cache, vop_interwoven_core_cache, vop_interwoven_core_diagnostics, vop_interwoven_core_math_utils, vop_interwoven_core_footprint, vop_interwoven_core_hull, vop_interwoven_core_pca2d, vop_interwoven_core_source_identity, vop_interwoven_revit_view_basis, vop_interwoven_revit_collection, vop_interwoven_revit_annotation, vop_interwoven_revit_linked_documents, vop_interwoven_revit_collection_policy, vop_interwoven_revit_safe_api, vop_interwoven_revit_tierb_proxy, vop_interwoven_diagnostics_strategy_tracker, vop_interwoven_export_csv [EXTRACTED 0.75]
- **Graphify Workflow** — agents_md, copilot_skills_graphify_skill, graphify_add_url, graphify_update, graphify_query, graphify_export, graphify_transcribe, graphify_hooks, graphify_github_merge [EXTRACTED 0.75]

## Communities (101 total, 11 thin omitted)

### Community 0 - "Geometry Cache Tests"
Cohesion: 0.07
Nodes (51): _AlwaysMatch, _AlwaysMismatch, _AlwaysNone, _make_value(), Tests for GeometryCache (persistent AREAL geometry cache).  Covers: - Basic get/, Entry for element not in elem_cache is retained., Entries that have no bbox_fingerprint field are always kept., Record-shaped AREAL entries validate nested HIGH fingerprints. (+43 more)

### Community 1 - "Depth Buffer Management"
Cohesion: 0.07
Nodes (51): float, build_2d_regions(), classify_tier_by_rect(), compute_per_cell_depth_for_cells(), Coverage, depth_sort_key(), DepthBuffer, Envelope (+43 more)

### Community 2 - "Fake Element Testing"
Cohesion: 0.06
Nodes (26): Categories, _FakeCategory, _FakeDoc, _FakeElem, _FakeId, _include(), Walls were in the old allowlist -- verify they still work., An explicitly excluded name must be rejected even if CategoryType=Model. (+18 more)

### Community 3 - "Mock Object Tests"
Cohesion: 0.08
Nodes (39): object, MockBBox, MockCategory, MockConfig, MockElement, MockId, MockRaster, MockView (+31 more)

### Community 4 - "Occlusion Contract Tests"
Cohesion: 0.06
Nodes (50): _make_raster(), Occlusion contract tests.  Policy (from pipeline.py / README):   - Only AREAL el, rasterize_polygon_to_proxy must write normally when w_occ is empty., rasterize_polygon_to_proxy must write when this element is closer than w_occ., rasterize_polygon_to_proxy must write model_proxy_key for interior cells., rasterize_polygon_to_proxy must not touch model_edge_key., AREAL MEDIUM/LOW boundary ink (rasterize_closed_loops_to_proxy_edges) must not w, AREAL LOW open polylines (rasterize_open_polylines_to_proxy_edges) must not writ (+42 more)

### Community 5 - "VOP Pipeline Implementation"
Cohesion: 0.07
Nodes (49): CSV Output Files, Current Status: ✅ Feature Complete, Dependency Graph, Documentation, Example Iteration, For Each Phase, Future Enhancements, Git Workflow Per Phase (+41 more)

### Community 6 - "Final State Scanner Tests"
Cohesion: 0.09
Nodes (44): _manifest(), _raster_fixture(), ExtFinalCells_Only must be zero when every ext cell also has host content., ExtFinalCells_Only must NOT count a cell just because occ_host was beaten., occ_host must be True even when HOST loses the depth test to a closer LINK eleme, test_ext_cells_only_excludes_cells_with_host_and_link_overlap(), test_ext_cells_only_host_loses_depth_still_records_spatial_presence(), test_ext_cells_only_resets_do_not_falsely_elevate_count() (+36 more)

### Community 7 - "View Unique ID Tests"
Cohesion: 0.07
Nodes (30): _Cfg, test_core_row_uses_view_unique_id_without_view_object(), test_root_cache_metadata_carries_view_unique_id(), test_root_cache_row_payload_stores_uid_from_pascal_metadata(), test_vop_cache_row_uses_view_result_uid_when_cached_payload_missing_uid(), test_vop_row_uses_view_unique_id_without_view_object(), test_extract_metrics_from_view_result_prefers_precomputed_metrics_without_raster_recompute(), _Cfg (+22 more)

### Community 8 - "Manifest Loader Tests"
Cohesion: 0.11
Nodes (38): bytes, test_manifest_loader_accepts_locked_v1_and_hash_is_stable(), test_manifest_loader_rejects_bad_required_types(), test_manifest_loader_rejects_unknown_top_level_keys(), test_manifest_loader_rejects_wrong_schema_version(), color(), Colors, compare_csv_files() (+30 more)

### Community 9 - "Core Module Files"
Cohesion: 0.05
Nodes (39): `bootstrap.py`, `config.py`, `core/areal_extraction.py`, `core/cache.py`, `core/diagnostics.py`, `core/element_cache.py`, `core/face_selection.py`, `core/footprint.py` (+31 more)

### Community 10 - "VOP Code Mapping"
Cohesion: 0.05
Nodes (36): VOP Interwoven Code Map (Authoritative), VOP Interwoven Symbol Index, VOP Interwoven Trace Map (Approximate Call Tree), Scope, All top-level definitions, `collect_view_elements`, `get_element_silhouette`, High-signal callsite details (approx) (+28 more)

### Community 11 - "Configuration Tests"
Cohesion: 0.07
Nodes (20): test_csv_config_hash_matches_root_cache_config_hash_for_real_config(), test_export_pipeline_to_csv_manifest_header_exact_order_when_compat_off(), test_get_vop_csv_header_uses_manifest_columns_when_compat_off(), test_core_row_contains_cellsize_resolution_values(), Config, Configuration for VOP interwoven pipeline.      Attributes:         tile_size (i, Initialize VOP configuration.          Args:             tile_size: Base tile si, Compute optimal tile size based on grid dimensions.          Args:             g (+12 more)

### Community 12 - "ViewRaster Tests"
Cohesion: 0.06
Nodes (22): Test ViewRaster data structure., Create test view raster., Test view raster initialization., Test cell index calculation., Test cell filling with depth (deprecated method)., Test centralized cell write with depth testing., Test TileMap spatial acceleration structure., occ_host/link/dwg must accumulate — a later winner must not erase prior True fla (+14 more)

### Community 13 - "Collection Diagnostics Tests"
Cohesion: 0.13
Nodes (23): MockBBox, MockCategory, MockElement, MockGeometry, MockId, MockRaster, MockViewBasis, MockXYZ (+15 more)

### Community 14 - "AREAL Geometry Extraction"
Cohesion: 0.07
Nodes (30): AREAL element geometry extraction, General caching utilities, Front-facing face selection, Footprint computation, UV classification, proxy generation, Convex hull utilities, Core data structures and algorithms for VOP interwoven pipeline.  Modules: - ras, Bounds, rectangle operations (+22 more)

### Community 15 - "Phase Validation Tests"
Cohesion: 0.09
Nodes (19): All Phases Test: Quick validation of all implemented phases  Copy this code into, Test Linked Documents Collection - Debug Script  Paste this into a Dynamo Python, Minimal Single View Test - Step-by-step diagnostic  This will show exactly where, Phase 1 Test: View Basis & Coordinate System  Copy this code into a Dynamo Pytho, Phase 2 Test: Element Collection & Bounding Boxes  Copy this code into a Dynamo, Phase 3 Test: UV Classification & Proxy Generation  Copy this code into a Dynamo, View Type Check - See why view is being skipped  This will show the exact view t, Zero Occupancy Debug Script  Comprehensive diagnostic for linked RVT/DWG zero oc (+11 more)

### Community 16 - "Diagnostic Tracking Tests"
Cohesion: 0.14
Nodes (23): _as_tuple(), _StubView, test_bounds_budget_absent_keeps_med_confidence_for_extents(), test_bounds_budget_trigger_downgrades_confidence_to_low_and_reports_budget(), _as_tuple(), _StubView, test_resolve_bounds_cap_triggers_with_before_after_reporting(), test_resolve_bounds_crop_off_extents_failure_falls_back_low_confidence() (+15 more)

### Community 17 - "Streaming Pipeline Processing"
Cohesion: 0.09
Nodes (20): test_streaming_vop_header_uses_cfg_and_includes_metadata_for_manifest_mode(), process_document_views_streaming(), process_with_streaming(), Streaming pipeline processing for VOP Interwoven.  Enables incremental processin, Manages incremental export of pipeline results., Initialize streaming exporter.          Args:             output_dir: Base outpu, Initialize CSV writers for incremental writing., Process views with per-view callback and cache support. (+12 more)

### Community 18 - "Import Check Tests"
Cohesion: 0.09
Nodes (25): Import Check - Verify all modules load correctly  This tests if the linked docum, _snapshot_occ(), test_early_out_is_pure_optimization_no_output_difference(), _bin_elements_to_tiles(), _diagnose_link_geometry_transform(), export_view_raster(), _extract_view_summary(), _perf_ms() (+17 more)

### Community 19 - "AST Utilities"
Cohesion: 0.21
Nodes (25): AST, Module, build_index(), build_trace_tree(), _call_name(), DefInfo, _format_def(), _is_excluded_dir() (+17 more)

### Community 20 - "VOP Pipeline Documentation"
Cohesion: 0.09
Nodes (24): Annotation Non-Occlusion Layering Principle, 3D Model Geometry as Sole Occlusion Authority, over_model_includes_proxies Config Flag, Classification Examples, Commentary Annotations, Configuration, Contributors, Core Principles (+16 more)

### Community 21 - "Pipeline Diagnostics Tests"
Cohesion: 0.08
Nodes (12): Test that CSV export works., Test that config can round-trip with export_strategy_diagnostics., Test that pipeline logic for creating diagnostics works., Test that pipeline can skip diagnostics when disabled., Test that export_strategy_diagnostics defaults to False., Test that diagnostic tracking failures don't crash., Test that export_strategy_diagnostics can be disabled., Test that export_strategy_diagnostics is included in to_dict(). (+4 more)

### Community 22 - "CSV Metrics Documentation"
Cohesion: 0.08
Nodes (24): 1. Core Metrics (`views_core_YYYY-MM-DD.csv`), 2. VOP Extended Metrics (`views_vop_YYYY-MM-DD.csv`), Core CSV Headers (18 columns), CSV Headers Reference, CSV Validation Invariant, Current Output Format (VOP Interwoven), Current Outputs Generated, Dependencies (+16 more)

### Community 23 - "Color ID Buffer Extraction"
Cohesion: 0.14
Nodes (21): _build_flat_color_ogs(), build_palette(), choose_step(), export_color_id_buffer_view(), _export_tiff(), get_or_create_neutral_phase_filter(), _get_solid_pattern_id(), _hidden_category_state() (+13 more)

### Community 24 - "LRU Element Cache"
Cohesion: 0.10
Nodes (21): LRU Element Cache with Fingerprint Keying, RootStyleCache (View-level Metrics Cache), Streaming Pipeline (Memory-Aware Per-View Export), View Signature (Cache Match Token), A) Purpose & Non-Goals, B) Public entry points / API surface, C) Pipeline stages and ownership boundaries, D) Geometry extraction authority model (+13 more)

### Community 25 - "Phase Test Scripts"
Cohesion: 0.12
Nodes (18): `test_all_phases.py` - Quick Validation, Test Scripts, `thinrunner.py` - Quick Iteration Runner, Phase 7: CSV Export Test  Test CSV export functionality with invariant validatio, Phase 8a: Annotation Collection & Rasterization Test  Test annotation collection, filter_supported_views(), get_all_floor_plans(), get_all_sections() (+10 more)

### Community 26 - "View Capabilities Tests"
Cohesion: 0.22
Nodes (17): FakeView, test_crop_bounds_capability_requires_model_geometry(), test_drafting_is_annotation_only(), test_floorplan_is_model_and_annotation(), test_legend_view_is_annotation_only(), test_numeric_viewtype_floorplan_is_model_capable(), test_template_view_is_rejected(), Best-effort, Revit-free view type name extraction for gating + tests.     Return (+9 more)

### Community 27 - "Strategy Diagnostics Tests"
Cohesion: 0.11
Nodes (9): Diagnostic script to test silhouette strategy selection and occlusion vs occupan, Test that StrategyDiagnostics can be created., Test summary statistics calculation., Test CSV export format and content., Create fresh diagnostics instance for each test., Test print_summary executes without errors., Test basic element classification tracking., Test AREAL strategy success/failure tracking. (+1 more)

### Community 28 - "Geometry Classification Tests"
Cohesion: 0.12
Nodes (11): run_tests.sh script, Unit tests for VOP interwoven geometry classification and proxy generation.  Tes, Test complete classification workflows (realistic scenarios)., Test typical door classification (TINY)., Test typical window classification (TINY or LINEAR)., Test typical wall classification (LINEAR)., Test typical floor classification (AREAL)., Run all tests and print results. (+3 more)

### Community 29 - "View Processing Infrastructure"
Cohesion: 0.12
Nodes (17): 1) View capability split (model vs annotation-only), 2) View basis + bounds/crop primitives, 3) Candidate collection infrastructure, 4) Front-to-back ordering and depth hints, 5) Existing cache building blocks, 6) Annotation/model non-occlusion layering intent, A) Strict non-interwoven stage boundaries with explicit artifacts, B) Tier-1/Tier-2 cache contract separation (+9 more)

### Community 30 - "Thin Runner for Streaming"
Cohesion: 0.14
Nodes (13): _build_views_from_input(), _coerce_view_id(), VOP Interwoven Pipeline - Thin Runner for Dynamo (STREAMING VERSION)  Quick test, Normalize IN[0] into a list of view-like objects., Best-effort coercion to an integer view id for pipeline compatibility., Best-effort elevation lookup for a view., Sort views by level elevation (ascending) with graceful fallback., Convert Dynamo/.NET collections to a flat Python list without exploding strings. (+5 more)

### Community 31 - "View Raster Export"
Cohesion: 0.17
Nodes (16): _canonical_name(), _dot(), export_pipeline_views_to_pngs(), export_view_image(), _is_annotation_only_view(), _prepare_view_for_export(), View raster export: renders Revit views as-is to PNG for side-by-side comparison, Export a single Revit view to PNG at the specified pixel dimensions.      Tempor (+8 more)

### Community 32 - "Coding Principles"
Cohesion: 0.14
Nodes (16): 1. No Silent Failure, 2. Explicit Semantics, 3. Single Source of Truth, 4. Worst-Case First, 5. Small, Reviewable Changes, No Silent Failure Rule, Single Source of Truth Rule, Source Identity Normalization (HOST | LINK | DWG) (+8 more)

### Community 33 - "UV Classification Tests"
Cohesion: 0.12
Nodes (9): Test UV-based element classification., Create default config for tests., Test TINY classification (both dimensions <= tiny_max)., Test LINEAR classification (one dimension thin, one long)., Test AREAL classification (both dimensions large)., Test classification at threshold boundaries., Test classification with custom thresholds., Test classification with edge case dimensions. (+1 more)

### Community 34 - "Exception Handling Fixes"
Cohesion: 0.18
Nodes (15): apply_fixes(), find_except_blocks(), generate_fix(), get_function_name(), get_phase_from_context(), has_diag_in_scope(), main(), process_file() (+7 more)

### Community 35 - "Dynamo Test Scripts"
Cohesion: 0.13
Nodes (15): "0 views processed", AttributeError: 'Bounds2D' object has no attribute 'min_x', "AttributeError: 'View3D' object has no attribute...", Dynamo Test Scripts, Example Dynamo Python Node Setup, "ImportError: No module named vop_interwoven", Next Steps, "No elements found" (+7 more)

### Community 36 - "Transform Binning Tests"
Cohesion: 0.26
Nodes (10): _BBox, _P, _Raster, Minimal transform stub with Revit-like OfPoint semantics., test_rotated_link_binning_uses_tight_uv_aabb_not_host_aabb(), _TransformZ, crop_box_from_uv_bounds(), View basis extraction for VOP interwoven pipeline.  Provides view coordinate sys (+2 more)

### Community 37 - "Depth Convention Tests"
Cohesion: 0.20
Nodes (13): Depth convention contract: w = dot(element - origin, forward) must increase with, For RCP (looking up), elements below the cut plane are closest to the viewer., Return only the depth component w., For floor plan view, element at cut plane (high Z) must have smaller w     than, Simulated depth test: floor slab (w_occ) must block Level-1 wall (w_depth)., For a section looking in +Y, element at Y=5 is closer than Y=20.      Revit View, test_floor_plan_closer_element_has_smaller_w(), test_floor_plan_depth_test_floor_occludes_wall() (+5 more)

### Community 38 - "Exclusion Filter Tests"
Cohesion: 0.14
Nodes (8): Test exclusion filter functionality in collection.  Phase 2B: Verifies that the, Test that the exclusion filter parameter was added correctly., Test that collect_view_elements has exclude_ids parameter., Test that exclude_ids defaults to None for backward compatibility., Test that original parameters are preserved in correct order., Test that exclude_ids won't break existing positional callers., Test that the collection module imports without errors after changes., TestExclusionFilterSignature

### Community 39 - "Face Selection Strategy Tests"
Cohesion: 0.23
Nodes (12): _Id, _import_config_and_silhouette(), int, Import using the real package first (pytest runs with repo root on sys.path),, Acceptance boundary:       AREAL strategies must try front-facing planar face lo, Verifies dispatch order + passthrough semantics without requiring Revit geometry, _StubElem, _StubRaster (+4 more)

### Community 40 - "Link Collector Tests"
Cohesion: 0.29
Nodes (9): _FakeBBox, _FakeCategory, _FakeCollector, _FakeElem, _FakeId, _FakeLinkDoc, _FakeLinkInst, _FakeView (+1 more)

### Community 41 - "Memory Telemetry Utilities"
Cohesion: 0.19
Nodes (6): _force_clr_gc(), _get_memory_mb(), MemoryTracker, Memory telemetry helpers for Dynamo/Python.NET runtime., Run CLR GC triple-call pattern and report private-memory delta., Return current (working_set_mb, private_bytes_mb) or (None, None).

### Community 42 - "AI Assistant Documentation"
Cohesion: 0.15
Nodes (12): CLAUDE.md - AI Assistant Guide for Revit SSM Exporter, Code Quality Checks, Commentary Markers in Code, Directory Structure, Documentation Hierarchy, Environment Notes, graphify, Key Files to Understand First (+4 more)

### Community 43 - "Operational Review Documentation"
Cohesion: 0.15
Nodes (12): Aligned, Divergent, Executive Assessment, Highest-Leverage Next Moves, HISTORICAL OPERATIONAL REVIEW / SUPERSEDED, Implementation vs Intent, Indeterminate, Observed Architecture (+4 more)

### Community 44 - "Bounding Box Policy Tests"
Cohesion: 0.28
Nodes (10): _BBox, ElemModelOnly, ElemThrows, ElemViewWins, FakeView, _Id, _P, test_resolve_element_bbox_falls_back_to_model() (+2 more)

### Community 45 - "Face Selection Tests"
Cohesion: 0.26
Nodes (5): _FaceStub, Unit tests for deterministic planar front-face selection utilities.  These tests, Return a square polygon in *model* coords (plan view basis => model XY == UV)., _square_loop(), _XYZ

### Community 46 - "UV Proxy Generation Tests"
Cohesion: 0.17
Nodes (7): Test UV_AABB proxy creation from CellRect., Test UV_AABB width/height calculations., Test UV_AABB center calculation., Test UV_AABB edge generation for stamping., Test complete workflow: classify as TINY -> generate UV_AABB., Test proxy generation (UV_AABB creation)., TestProxyGeneration

### Community 47 - "AREAL Extraction Tests"
Cohesion: 0.47
Nodes (11): _call_name(), _calls_named(), _find_element_loop(), _find_function(), _is_elem_class_areal_if(), Static regression tests for the AREAL HIGH single-extraction contract., test_areal_path_does_not_probe_directional_high_or_low_keys_before_extraction(), test_areal_path_extracts_geometry_once_before_raster_decompose() (+3 more)

### Community 48 - "View Coordinate System"
Cohesion: 0.18
Nodes (6): View coordinate system with origin and basis vectors.      Attributes:         o, Back-compat helper: accept XYZ or tuple, return view-local (u, v, w)., Check if view is plan-like (looking down Z axis).          Returns:, Check if view is elevation-like (horizontal view direction).          Returns:, Transform model-space point to view-local UV coordinates.          Args:, ViewBasis

### Community 49 - "Proxy Visibility Contract"
Cohesion: 0.18
Nodes (11): 1) Gate or implement proxy `edges` mode, 2) Contract contradiction requiring code decision: AREAL fallback occlusion semantics, 3) Complete diagnostics wiring debt in exception handlers, 4) Remove or finalize dormant per-view disk cache path, 5) Implement explicit visibility-gating pass or narrow the contract, 6) Streaming signature-first TODO cleanup, 7) Align historical documentation footprint with current architecture doc, P0 (+3 more)

### Community 50 - "CSV Export Diagnostics Tests"
Cohesion: 0.18
Nodes (6): Test that percentage columns are in valid 0-100 range., Test that build_vop_csv_row works without strategy_diag (backward compat)., Test that strategy counts sum to expected totals., Test that CSV export doesn't crash if diagnostic extraction fails., Test that build_vop_csv_row correctly extracts strategy statistics., Test strategy diagnostics integration in pipeline.

### Community 51 - "Manifest Generation"
Cohesion: 0.20
Nodes (10): main(), normalize_csv_for_hashing(), Write manifest entries to file.      Format:         CSV  filename.csv  <hash>, Normalize CSV content for hashing by excluding volatile columns.      Args:, Compute SHA256 hash of a file., Compute SHA256 hash of content bytes., Generate manifest entries from output directory.      Args:         output_dir:, sha256_content() (+2 more)

### Community 52 - "Golden Baseline Testing"
Cohesion: 0.18
Nodes (11): `compare_golden.py` - Golden Baseline Comparison, Establishing Initial Golden Baseline, `generate_manifest.py` - Generate Golden Baseline, Integration with CI/CD, Notes, Scripts, See Also, SSM/VOP Exporter Tools (+3 more)

### Community 54 - "Occupancy Metrics"
Cohesion: 0.22
Nodes (9): 8-State Occupancy Cell Partition Invariant, External Cell Metrics (ExtFinalCells_DWG / _RVT Set Relationships), Golden Baseline Regression Testing, Multihot Model Class Cells (ModelClassCells_*), Legacy 4-Way Occupancy Partition (views_vop CSV), SSM-Compatible CSV Export Format, Metrics Totals Relationships Cheat Sheet, Phase 7 CSV Export Plan (Historical/Superseded) (+1 more)

### Community 55 - "Diagnostics Contract"
Cohesion: 0.22
Nodes (10): Diagnostics Always-On Contract, Diagnostics Wiring Debt (TODO Placeholders in Exception Handlers), Dormant Per-View Disk Cache (view_cache_enabled=False), Visibility Gating Stub (is_element_visible_in_view), Repository Operational Review (Historical/Superseded), ROADMAP - Prioritized Remaining Work, Diagnostics and Outputs, Diagnostics Is Not Logging (+2 more)

### Community 56 - "Repository Scanning"
Cohesion: 0.44
Nodes (8): Hit, _iter_py_files(), _load_whitelist(), main(), int, str, _repo_rel(), scan()

### Community 57 - "Graph Query Instructions"
Cohesion: 0.22
Nodes (8): graphify, add, export, github-and-merge, hooks, query, transcribe, update

### Community 58 - "Annotation Classification"
Cohesion: 0.22
Nodes (9): Annotation Type Classification (7 Types: TEXT/TAG/DIM/DETAIL/LINES/REGION/OTHER), AREAL Fallback Occlusion Semantics Contradiction (P0 Debt), Confidence-Based Occlusion Semantics, Dynamo Python Node Entry Point, Multi-Strategy Silhouette Extraction with Fallbacks, Proxy Edge Mask Mode Stub (_stamp_proxy_edges), Proxy Ink Semantics (model_proxy_key), UV Classification System (TINY / LINEAR / AREAL) (+1 more)

### Community 59 - "CSV Model Presence Tests"
Cohesion: 0.36
Nodes (8): _mk_raster(), A cell with only model_proxy_key set (mask=False) must not be empty under ink mo, ViewRaster.has_model_proxy must return True when only proxy_key is set., test_csv_metrics_edge_counts_edges(), test_csv_metrics_ink_counts_occupancy_fill(), test_csv_metrics_ink_counts_proxy_key_only(), test_csv_metrics_occ_ignores_edges(), test_has_model_proxy_proxy_key_only()

### Community 60 - "Element Cache Tests"
Cohesion: 0.33
Nodes (5): test_element_cache_export_analysis_csv_includes_source_type(), test_element_cache_hit_upgrades_unknown_source_type(), test_export_view_element_map_json_merges_existing_file(), test_export_view_element_map_json_writes_view_index(), LRU element caching

### Community 61 - "Raster Semantics Tests"
Cohesion: 0.22
Nodes (6): A cell with model_proxy_key set but model_proxy_mask=False must not be empty., Proxy-key-only cells must not have w_occ written (no occlusion authority)., stamp_proxy_edge_idx must set model_proxy_mask so has_model_proxy returns True., test_proxy_key_only_no_mask_counts_as_model_present(), test_proxy_key_only_no_occlusion_written(), test_stamp_proxy_edge_sets_proxy_mask()

### Community 62 - "Annotation Bounds Computation"
Cohesion: 0.25
Nodes (8): Produce bounds from annotation extents ONLY (no union with model/crop).     This, Compute XY bounds from view crop box (all 8 corners method).      Notes on API c, Compute EFFECTIVE view bounds in view-local UV.      Behaves as if the view had, Compute synthetic bounds from element extents in a view (crop-off / no-crop view, resolve_annotation_only_bounds(), synthetic_bounds_from_visible_extents(), xy_bounds_effective(), xy_bounds_from_crop_box_all_corners()

### Community 63 - "Metrics Relationships"
Cohesion: 0.25
Nodes (8): 1) Exact equalities (must always hold), 2) Subset/superset inequalities (must always hold), 3) Useful derived groups from the 8-state partition, 4) Legacy `views_vop` 4-way presence buckets, 5) What is allowed to exceed what, 6) Quick triage sequence when numbers look wrong, Metrics Totals Relationships (Set/Superset/Subset Cheat Sheet), Model classes are multihot (not disjoint)

### Community 64 - "Proxy Element Rendering"
Cohesion: 0.25
Nodes (8): _mark_rect_center_cell(), _mark_thin_band_along_long_axis(), Render TINY/LINEAR element: proxy edges + optional minimal mask.      Commentary, Stamp proxy edges into model_proxy_key layer.      NOTE: Edge rasterization is n, Mark center cell of rect in model_proxy_mask., Mark thin band along long axis of rect in model_proxy_mask., _render_proxy_element(), _stamp_proxy_edges()

### Community 65 - "Depth Ordering Pipeline"
Cohesion: 0.29
Nodes (7): Front-to-Back Depth Ordering for Model Processing, Interwoven Render Pipeline Architecture, TileMap Spatial Acceleration and Early-Out Occlusion, Two-Tier Primitive Cache Contract (Proposed, Not Implemented), View Mode Resolution (MODEL_AND_ANNOTATION / ANNOTATION_ONLY / REJECTED), Pipeline Approach Comparison (Historical/Superseded), HISTORICAL ANALYSIS / SUPERSEDED

### Community 66 - "Signature Generation Utilities"
Cohesion: 0.29
Nodes (7): _cfg_hash(), _cropbox_fingerprint(), Returns a small, stable fingerprint of crop settings and extents.     We avoid r, Enhanced signature with element fingerprints for position/size tracking.      Mu, _safe_bool(), _safe_int(), _view_signature()

### Community 67 - "Metrics Manifest Tests"
Cohesion: 0.60
Nodes (5): _fake_raster(), test_compute_manifest_metrics_payload_attaches_provenance_and_validation(), test_compute_manifest_metrics_payload_strict_fails_on_missing_required_primitive(), _compute_manifest_metrics_payload(), Compute manifest-scanned metrics totals and validation payload.

### Community 68 - "Raster Decomposition Tests"
Cohesion: 0.47
Nodes (3): _cells_from_rect(), test_decompose_to_rects_covers_l_shape_without_overlap(), test_decompose_to_rects_has_no_silent_24_rect_cap()

### Community 69 - "Cache Signature Verification"
Cohesion: 0.33
Nodes (5): Test script to verify VOP cache signature enhancement (Phase 1).  This script va, Test the element ID collection and signature generation.      Run this in Revit, Inspect actual cache files to verify schema v2 format.      Args:         cache_, test_cache_file_inspection(), test_signature_collection()

### Community 70 - "NumPy and Pillow Setup"
Cohesion: 0.33
Nodes (5): ensure_numpy(), ensure_pillow(), vop_interwoven/np_backend.py  NumPy and Pillow availability detection with optio, Return True if NumPy is importable.      If auto_install=True and NumPy is absen, Return True if Pillow is importable.      If auto_install=True and Pillow is abs

### Community 71 - "Rasterization Strategy Development"
Cohesion: 0.40
Nodes (5): Adding a New Rasterization Strategy, Adding CSV Export Columns, Common Development Tasks, Debugging Geometry Issues, Modifying Element Collection

### Community 72 - "Occupancy Channel Tests"
Cohesion: 0.70
Nodes (4): _make_raster(), test_proxy_edges_enabled_perimeter_only_to_proxy_channel(), test_proxy_fill_affects_occlusion_not_model_ink_by_default(), test_real_silhouette_writes_model_ink_edges()

### Community 74 - "VOP Bootstrap"
Cohesion: 0.60
Nodes (4): _install(), _is_importable(), VOP NumPy + Pillow Bootstrap ============================ Run once per machine (, run()

### Community 75 - "Element Processing Modes"
Cohesion: 0.40
Nodes (5): AREAL Elements (Heavy Processing), Confidence-Based Occlusion Authority (AREAL elements only), LINEAR Elements (Medium Processing), Processing Modes, TINY Elements (Lightweight Processing)

### Community 76 - "Workflow Documentation"
Cohesion: 0.50
Nodes (4): Automated Workflows, Branch Naming, Commit Message Convention, Git Workflow

### Community 77 - "Testing Overview"
Cohesion: 0.50
Nodes (4): Golden Baseline Testing, Running Tests, Test Coverage Areas, Testing

### Community 78 - "Copilot Instructions"
Cohesion: 0.50
Nodes (3): Core architecture constraints, GitHub Copilot Instructions — Revit SSM Exporter, graphify — Query the knowledge graph before browsing source

### Community 79 - "Test Collection Prevention"
Cohesion: 0.50
Nodes (3): Path, pytest_ignore_collect(), Prevent collection of Dynamo/Revit integration tests unless explicitly enabled.

### Community 82 - "Tile Depth Conflict Checks"
Cohesion: 0.50
Nodes (4): _get_ambiguous_tiles(), Check if tile has depth range conflicts (ambiguity) using a sweep (O(k log k))., Identify tiles with depth conflicts that need triangle resolution.      Args:, _tile_has_depth_conflict()

### Community 83 - "AREAL Cache Key Generation"
Cohesion: 0.50
Nodes (4): _make_areal_high_conf_cache_key(), _quantize_view_dir(), Map vb.forward to one of six axis-aligned string tokens.      Returns one of: 'x, Build a view-independent cache key for AREAL HIGH-confidence geometry.      The

### Community 84 - "Development Status"
Cohesion: 0.50
Nodes (4): ✅ Complete (Core Pipeline), ✅ Complete (External Sources), Development Status, 🔮 Future Enhancements

### Community 85 - "Core Architecture Principles"
Cohesion: 0.67
Nodes (3): Core Architecture Principles, Element Classification System, Key Configuration Parameters

## Knowledge Gaps
- **292 isolated node(s):** `graphify — Query the knowledge graph before browsing source`, `Core architecture constraints`, `Path`, `bool`, `int` (+287 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **11 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Config` connect `Configuration Tests` to `UV Classification Tests`, `Metrics Manifest Tests`, `Final State Scanner Tests`, `Face Selection Strategy Tests`, `View Unique ID Tests`, `ViewRaster Tests`, `UV Proxy Generation Tests`, `Phase Validation Tests`, `AREAL Geometry Extraction`, `Streaming Pipeline Processing`, `Import Check Tests`, `CSV Export Diagnostics Tests`, `Pipeline Diagnostics Tests`, `Phase Test Scripts`, `Geometry Classification Tests`, `Thin Runner for Streaming`?**
  _High betweenness centrality (0.179) - this node is a cross-community bridge._
- **Why does `Test Scripts` connect `Phase Test Scripts` to `Dynamo Test Scripts`, `Phase Validation Tests`?**
  _High betweenness centrality (0.170) - this node is a cross-community bridge._
- **Why does `Dynamo Test Scripts` connect `Dynamo Test Scripts` to `Phase Test Scripts`, `Occupancy Metrics`?**
  _High betweenness centrality (0.170) - this node is a cross-community bridge._
- **Are the 9 inferred relationships involving `Config` (e.g. with `test_csv_export_diagnostics.py` and `test_config_areal_prefers_front_face_loops()`) actually correct?**
  _`Config` has 9 INFERRED edges - model-reasoned connections that need verification._
- **What connects `graphify — Query the knowledge graph before browsing source`, `Core architecture constraints`, `Path` to the rest of the system?**
  _292 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Geometry Cache Tests` be split into smaller, more focused modules?**
  _Cohesion score 0.0701484895033282 - nodes in this community are weakly interconnected._
- **Should `Depth Buffer Management` be split into smaller, more focused modules?**
  _Cohesion score 0.06779661016949153 - nodes in this community are weakly interconnected._