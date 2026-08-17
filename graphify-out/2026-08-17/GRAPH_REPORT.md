# Graph Report - .  (2026-08-10)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 1962 nodes · 3531 edges · 99 communities (93 shown, 6 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 89 edges (avg confidence: 0.72)
- Token cost: 6,950 input · 886 output

## Graph Freshness
- Built from commit: `eae15a29`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Mock Objects
- Test Configuration
- Module Import Testing
- Geometry Cache Testing
- Depth Calculation
- Fake Element Testing
- Linework Application
- Graphics Semantics
- Configuration Testing
- External Source Analysis
- Occlusion Contract Testing
- Image Alignment Probe
- Implementation Planning
- Final State Scanning
- Identity Consistency Testing
- Streaming Export Processing
- Color ID Buffer Testing
- Manifest Loading
- Core Utilities
- Stage Analysis
- View Raster Testing
- Phase Validation
- Transaction Group Export
- Bounds Budget Testing
- AST Utilities
- Pipeline Diagnostics
- Resolution Helpers
- Architecture Overview
- View Capabilities Testing
- View Coordinate System
- CSV Export Testing
- Strategy Diagnostics Testing
- Geometry Classification Testing
- Trace Map Documentation
- View Raster Export
- Documentation and Guidelines
- Coding Principles
- UV Classification Testing
- Exception Handling Fixes
- Transform Binning Testing
- Depth Convention Testing
- Exclusion Filter Testing
- Front Face Selection Testing
- Link Collector Testing
- Memory Telemetry
- Callsite Details
- Golden Baseline Management
- Operational Review
- Bounding Box Policy Testing
- CSV Export Overview
- Graph Querying
- Dynamo Test Scripts
- Face Selection Testing
- Proxy Generation Testing
- Single Extraction Testing
- Implementation Roadmap
- Manifest Generation
- Occlusion Diagnostics
- Annotation Classification
- Exporter Pipeline Comparison
- Repository Utilities
- Model Presence Testing
- Element Cache Testing
- Raster Semantics Testing
- Metrics Relationships
- Proxy Rendering
- View Capability Analysis
- Code Mapping
- Diagnostics Contract
- Render Pipeline Architecture
- Raster Decomposition Testing
- Cache Signature Verification
- NumPy and Pillow Setup
- Development Tasks
- Occupancy Channel Testing
- Bootstrap Utilities
- CSV Metrics Overview
- Occupancy Cell Partitioning
- Testing Overview
- Caching Architecture
- Copilot Instructions
- Purpose and Goals
- Output Format Overview
- Gap Analysis
- Performance Constants
- Enumeration
- Repository README
- Manifest Utilities
- Validated Manifest Metadata

## God Nodes (most connected - your core abstractions)
1. `Config` - 82 edges
2. `Stage A Image Alignment Probe` - 50 edges
3. `Files` - 40 edges
4. `load_manifest()` - 29 edges
5. `_run()` - 27 edges
6. `_FakeElem` - 26 edges
7. `_make_raster()` - 24 edges
8. `scan_final_state_totals()` - 22 edges
9. `VOP Interwoven Pipeline - Progressive Implementation Plan` - 22 edges
10. `_run_variant()` - 22 edges

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

## Communities (99 total, 6 thin omitted)

### Community 0 - "Mock Objects"
Cohesion: 0.05
Nodes (62): object, MockBBox, MockCategory, MockConfig, MockElement, MockId, MockRaster, MockView (+54 more)

### Community 1 - "Test Configuration"
Cohesion: 0.05
Nodes (84): Path, pytest_ignore_collect(), Prevent collection of Dynamo/Revit integration tests unless explicitly enabled., _active_crop_bounds(), _add_repo_root_to_path(), all_mutation_statuses(), analyze_tiff(), _apply_flat_colors() (+76 more)

### Community 2 - "Module Import Testing"
Cohesion: 0.04
Nodes (75): Import Check - Verify all modules load correctly  This tests if the linked docum, _snapshot_occ(), test_early_out_is_pure_optimization_no_output_difference(), AREAL element geometry extraction, General caching utilities, Front-facing face selection, Footprint computation, UV classification, proxy generation (+67 more)

### Community 3 - "Geometry Cache Testing"
Cohesion: 0.07
Nodes (51): _AlwaysMatch, _AlwaysMismatch, _AlwaysNone, _make_value(), Tests for GeometryCache (persistent AREAL geometry cache).  Covers: - Basic get/, Entry for element not in elem_cache is retained., Entries that have no bbox_fingerprint field are always kept., Record-shaped AREAL entries validate nested HIGH fingerprints. (+43 more)

### Community 4 - "Depth Calculation"
Cohesion: 0.07
Nodes (51): float, build_2d_regions(), classify_tier_by_rect(), compute_per_cell_depth_for_cells(), Coverage, depth_sort_key(), DepthBuffer, Envelope (+43 more)

### Community 5 - "Fake Element Testing"
Cohesion: 0.06
Nodes (26): Categories, _FakeCategory, _FakeDoc, _FakeElem, _FakeId, _include(), Walls were in the old allowlist -- verify they still work., An explicitly excluded name must be rejected even if CategoryType=Model. (+18 more)

### Community 6 - "Linework Application"
Cohesion: 0.09
Nodes (55): _active_crop_bounds(), _actual_tiff_dimensions(), _analyze_image(), _apply_category_linework(), _apply_element_id_reference(), _apply_mode(), apply_resolution_cap(), _bounds_tuple() (+47 more)

### Community 7 - "Graphics Semantics"
Cohesion: 0.09
Nodes (52): _active_crop_bounds(), _actual_tiff_dimensions(), _analyze_image(), apply_resolution_cap(), _bounds_tuple(), build_resolution_report(), calculate_canvas_placement(), calculate_paper_space_resolution() (+44 more)

### Community 8 - "Configuration Testing"
Cohesion: 0.05
Nodes (31): test_csv_config_hash_matches_root_cache_config_hash_for_real_config(), Test that percentage columns are in valid 0-100 range., Test that build_vop_csv_row works without strategy_diag (backward compat)., Test that strategy counts sum to expected totals., Test that CSV export doesn't crash if diagnostic extraction fails., Test that build_vop_csv_row correctly extracts strategy statistics., test_export_pipeline_to_csv_manifest_header_exact_order_when_compat_off(), test_get_vop_csv_header_uses_manifest_columns_when_compat_off() (+23 more)

### Community 9 - "External Source Analysis"
Cohesion: 0.09
Nodes (51): _active_crop_bounds(), _actual_tiff_dimensions(), _analyze(), _apply_assignments(), apply_resolution_cap(), _bounds_tuple(), build_resolution_report(), calculate_canvas_placement() (+43 more)

### Community 10 - "Occlusion Contract Testing"
Cohesion: 0.06
Nodes (50): _make_raster(), Occlusion contract tests.  Policy (from pipeline.py / README):   - Only AREAL el, rasterize_polygon_to_proxy must write normally when w_occ is empty., rasterize_polygon_to_proxy must write when this element is closer than w_occ., rasterize_polygon_to_proxy must write model_proxy_key for interior cells., rasterize_polygon_to_proxy must not touch model_edge_key., AREAL MEDIUM/LOW boundary ink (rasterize_closed_loops_to_proxy_edges) must not w, AREAL LOW open polylines (rasterize_open_polylines_to_proxy_edges) must not writ (+42 more)

### Community 11 - "Image Alignment Probe"
Cohesion: 0.10
Nodes (50): Stage A Image Alignment Probe, _actual_tiff_dimensions(), _analyze_image(), apply_resolution_cap(), _bounds_dict(), _bounds_tuple(), _build_marker_ogs(), build_resolution_report() (+42 more)

### Community 12 - "Implementation Planning"
Cohesion: 0.07
Nodes (49): CSV Output Files, Current Status: ✅ Feature Complete, Dependency Graph, Documentation, Example Iteration, For Each Phase, Future Enhancements, Git Workflow Per Phase (+41 more)

### Community 13 - "Final State Scanning"
Cohesion: 0.09
Nodes (44): _manifest(), _raster_fixture(), ExtFinalCells_Only must be zero when every ext cell also has host content., ExtFinalCells_Only must NOT count a cell just because occ_host was beaten., occ_host must be True even when HOST loses the depth test to a closer LINK eleme, test_ext_cells_only_excludes_cells_with_host_and_link_overlap(), test_ext_cells_only_host_loses_depth_still_records_spatial_presence(), test_ext_cells_only_resets_do_not_falsely_elevate_count() (+36 more)

### Community 14 - "Identity Consistency Testing"
Cohesion: 0.07
Nodes (30): _Cfg, test_core_row_uses_view_unique_id_without_view_object(), test_root_cache_metadata_carries_view_unique_id(), test_root_cache_row_payload_stores_uid_from_pascal_metadata(), test_vop_cache_row_uses_view_result_uid_when_cached_payload_missing_uid(), test_vop_row_uses_view_unique_id_without_view_object(), test_extract_metrics_from_view_result_prefers_precomputed_metrics_without_raster_recompute(), _Cfg (+22 more)

### Community 15 - "Streaming Export Processing"
Cohesion: 0.06
Nodes (33): test_streaming_vop_header_uses_cfg_and_includes_metadata_for_manifest_mode(), process_document_views_streaming(), process_with_streaming(), Streaming pipeline processing for VOP Interwoven.  Enables incremental processin, Manages incremental export of pipeline results., Initialize streaming exporter.          Args:             output_dir: Base outpu, Initialize CSV writers for incremental writing., Process views with per-view callback and cache support. (+25 more)

### Community 16 - "Color ID Buffer Testing"
Cohesion: 0.08
Nodes (35): _palette(), _palette(), _palette(), _collect_reference_elements(), _is_near_black(), _is_near_white(), Unit tests for VOP interwoven Stage A color-ID buffer palette generation.  Cover, build_palette() must never hand out a color near either reserved corner. (+27 more)

### Community 17 - "Manifest Loading"
Cohesion: 0.11
Nodes (38): bytes, test_manifest_loader_accepts_locked_v1_and_hash_is_stable(), test_manifest_loader_rejects_bad_required_types(), test_manifest_loader_rejects_unknown_top_level_keys(), test_manifest_loader_rejects_wrong_schema_version(), color(), Colors, compare_csv_files() (+30 more)

### Community 18 - "Core Utilities"
Cohesion: 0.05
Nodes (39): `bootstrap.py`, `config.py`, `core/areal_extraction.py`, `core/cache.py`, `core/diagnostics.py`, `core/element_cache.py`, `core/face_selection.py`, `core/footprint.py` (+31 more)

### Community 19 - "Stage Analysis"
Cohesion: 0.15
Nodes (35): Any, ndarray, Path, test_analyzer_does_not_recommend_without_real_metrics(), test_analyzer_handles_minimum_id_mutations_probe(), test_analyzer_marks_failed_attestation_as_non_evidence(), affine_fit(), alignment_evidence_status() (+27 more)

### Community 20 - "View Raster Testing"
Cohesion: 0.06
Nodes (22): Test ViewRaster data structure., Create test view raster., Test view raster initialization., Test cell index calculation., Test cell filling with depth (deprecated method)., Test centralized cell write with depth testing., Test TileMap spatial acceleration structure., occ_host/link/dwg must accumulate — a later winner must not erase prior True fla (+14 more)

### Community 21 - "Phase Validation"
Cohesion: 0.09
Nodes (22): `test_all_phases.py` - Quick Validation, Test Scripts, `thinrunner.py` - Quick Iteration Runner, All Phases Test: Quick validation of all implemented phases  Copy this code into, Test Linked Documents Collection - Debug Script  Paste this into a Dynamo Python, Minimal Single View Test - Step-by-step diagnostic  This will show exactly where, Phase 1 Test: View Basis & Coordinate System  Copy this code into a Dynamo Pytho, Phase 2 Test: Element Collection & Bounding Boxes  Copy this code into a Dynamo (+14 more)

### Community 22 - "Transaction Group Export"
Cohesion: 0.15
Nodes (30): _apply_temporary_changes(), _as_list(), _build_flat_color_ogs(), _diff_values(), _discover_and_rename_tiff(), _document_label(), _ensure_revit_api_reference(), _exception_record() (+22 more)

### Community 23 - "Bounds Budget Testing"
Cohesion: 0.14
Nodes (23): _as_tuple(), _StubView, test_bounds_budget_absent_keeps_med_confidence_for_extents(), test_bounds_budget_trigger_downgrades_confidence_to_low_and_reports_budget(), _as_tuple(), _StubView, test_resolve_bounds_cap_triggers_with_before_after_reporting(), test_resolve_bounds_crop_off_extents_failure_falls_back_low_confidence() (+15 more)

### Community 24 - "AST Utilities"
Cohesion: 0.21
Nodes (25): AST, Module, build_index(), build_trace_tree(), _call_name(), DefInfo, _format_def(), _is_excluded_dir() (+17 more)

### Community 25 - "Pipeline Diagnostics"
Cohesion: 0.08
Nodes (12): Test that CSV export works., Test that config can round-trip with export_strategy_diagnostics., Test that pipeline logic for creating diagnostics works., Test that pipeline can skip diagnostics when disabled., Test that export_strategy_diagnostics defaults to False., Test that diagnostic tracking failures don't crash., Test that export_strategy_diagnostics can be disabled., Test that export_strategy_diagnostics is included in to_dict(). (+4 more)

### Community 26 - "Resolution Helpers"
Cohesion: 0.23
Nodes (20): parametrize, apply_resolution_cap(), build_resolution_report(), calculate_canvas_placement(), calculate_paper_space_resolution(), choose_resolution_bounds(), _positive(), Pure Stage A probe resolution helpers for safe non-Revit tests. (+12 more)

### Community 27 - "Architecture Overview"
Cohesion: 0.11
Nodes (20): AREAL Fallback Occlusion Semantics Contradiction (P0 Debt), LRU Element Cache with Fingerprint Keying, Dormant Per-View Disk Cache (view_cache_enabled=False), Proxy Edge Mask Mode Stub (_stamp_proxy_edges), RootStyleCache (View-level Metrics Cache), Streaming Pipeline (Memory-Aware Per-View Export), View Signature (Cache Match Token), Visibility Gating Stub (is_element_visible_in_view) (+12 more)

### Community 28 - "View Capabilities Testing"
Cohesion: 0.22
Nodes (17): FakeView, test_crop_bounds_capability_requires_model_geometry(), test_drafting_is_annotation_only(), test_floorplan_is_model_and_annotation(), test_legend_view_is_annotation_only(), test_numeric_viewtype_floorplan_is_model_capable(), test_template_view_is_rejected(), Best-effort, Revit-free view type name extraction for gating + tests.     Return (+9 more)

### Community 29 - "View Coordinate System"
Cohesion: 0.12
Nodes (12): View coordinate system with origin and basis vectors.      Attributes:         o, Back-compat helper: accept XYZ or tuple, return view-local (u, v, w)., Compute XY bounds from view crop box (all 8 corners method).      Notes on API c, Check if view is plan-like (looking down Z axis).          Returns:, Check if view is elevation-like (horizontal view direction).          Returns:, Compute EFFECTIVE view bounds in view-local UV.      Behaves as if the view had, Compute synthetic bounds from element extents in a view (crop-off / no-crop view, Transform model-space point to view-local UV coordinates.          Args: (+4 more)

### Community 30 - "CSV Export Testing"
Cohesion: 0.14
Nodes (15): Phase 7: CSV Export Test  Test CSV export functionality with invariant validatio, Phase 8a: Annotation Collection & Rasterization Test  Test annotation collection, filter_supported_views(), get_all_floor_plans(), get_all_sections(), get_all_views_in_model(), get_views_from_input_or_current(), Dynamo-friendly entry points for VOP interwoven pipeline.  These functions handl (+7 more)

### Community 31 - "Strategy Diagnostics Testing"
Cohesion: 0.11
Nodes (9): Diagnostic script to test silhouette strategy selection and occlusion vs occupan, Test that StrategyDiagnostics can be created., Test summary statistics calculation., Test CSV export format and content., Create fresh diagnostics instance for each test., Test print_summary executes without errors., Test basic element classification tracking., Test AREAL strategy success/failure tracking. (+1 more)

### Community 32 - "Geometry Classification Testing"
Cohesion: 0.12
Nodes (11): run_tests.sh script, Unit tests for VOP interwoven geometry classification and proxy generation.  Tes, Test complete classification workflows (realistic scenarios)., Test typical door classification (TINY)., Test typical window classification (TINY or LINEAR)., Test typical wall classification (LINEAR)., Test typical floor classification (AREAL)., Run all tests and print results. (+3 more)

### Community 33 - "Trace Map Documentation"
Cohesion: 0.12
Nodes (16): VOP Interwoven Trace Map (Approximate Call Tree), Trace: `collect_view_elements` (revit/collection.py:L64), Trace: `get_element_silhouette` (core/silhouette.py:L1920), Trace: `init_view_raster` (pipeline.py:L1628), Trace: `process_document_views` (pipeline.py:L540), Trace: `process_document_views_streaming` (streaming.py:L549), Trace: `rasterize_annotations` (revit/annotation.py:L903), Trace: `render_model_front_to_back` (pipeline.py:L1933) (+8 more)

### Community 34 - "View Raster Export"
Cohesion: 0.17
Nodes (16): _canonical_name(), _dot(), export_pipeline_views_to_pngs(), export_view_image(), _is_annotation_only_view(), _prepare_view_for_export(), View raster export: renders Revit views as-is to PNG for side-by-side comparison, Export a single Revit view to PNG at the specified pixel dimensions.      Tempor (+8 more)

### Community 35 - "Documentation and Guidelines"
Cohesion: 0.10
Nodes (19): Automated Workflows, Branch Naming, CLAUDE.md - AI Assistant Guide for Revit SSM Exporter, Code Quality Checks, Commentary Markers in Code, Commit Message Convention, Core Architecture Principles, Directory Structure (+11 more)

### Community 36 - "Coding Principles"
Cohesion: 0.14
Nodes (16): 1. No Silent Failure, 2. Explicit Semantics, 3. Single Source of Truth, 4. Worst-Case First, 5. Small, Reviewable Changes, No Silent Failure Rule, Single Source of Truth Rule, Source Identity Normalization (HOST | LINK | DWG) (+8 more)

### Community 37 - "UV Classification Testing"
Cohesion: 0.12
Nodes (9): Test UV-based element classification., Create default config for tests., Test TINY classification (both dimensions <= tiny_max)., Test LINEAR classification (one dimension thin, one long)., Test AREAL classification (both dimensions large)., Test classification at threshold boundaries., Test classification with custom thresholds., Test classification with edge case dimensions. (+1 more)

### Community 38 - "Exception Handling Fixes"
Cohesion: 0.18
Nodes (15): apply_fixes(), find_except_blocks(), generate_fix(), get_function_name(), get_phase_from_context(), has_diag_in_scope(), main(), process_file() (+7 more)

### Community 39 - "Transform Binning Testing"
Cohesion: 0.26
Nodes (10): _BBox, _P, _Raster, Minimal transform stub with Revit-like OfPoint semantics., test_rotated_link_binning_uses_tight_uv_aabb_not_host_aabb(), _TransformZ, crop_box_from_uv_bounds(), View basis extraction for VOP interwoven pipeline.  Provides view coordinate sys (+2 more)

### Community 40 - "Depth Convention Testing"
Cohesion: 0.20
Nodes (13): Depth convention contract: w = dot(element - origin, forward) must increase with, For RCP (looking up), elements below the cut plane are closest to the viewer., Return only the depth component w., For floor plan view, element at cut plane (high Z) must have smaller w     than, Simulated depth test: floor slab (w_occ) must block Level-1 wall (w_depth)., For a section looking in +Y, element at Y=5 is closer than Y=20.      Revit View, test_floor_plan_closer_element_has_smaller_w(), test_floor_plan_depth_test_floor_occludes_wall() (+5 more)

### Community 41 - "Exclusion Filter Testing"
Cohesion: 0.14
Nodes (8): Test exclusion filter functionality in collection.  Phase 2B: Verifies that the, Test that the exclusion filter parameter was added correctly., Test that collect_view_elements has exclude_ids parameter., Test that exclude_ids defaults to None for backward compatibility., Test that original parameters are preserved in correct order., Test that exclude_ids won't break existing positional callers., Test that the collection module imports without errors after changes., TestExclusionFilterSignature

### Community 42 - "Front Face Selection Testing"
Cohesion: 0.23
Nodes (12): _Id, _import_config_and_silhouette(), int, Import using the real package first (pytest runs with repo root on sys.path),, Acceptance boundary:       AREAL strategies must try front-facing planar face lo, Verifies dispatch order + passthrough semantics without requiring Revit geometry, _StubElem, _StubRaster (+4 more)

### Community 43 - "Link Collector Testing"
Cohesion: 0.29
Nodes (9): _FakeBBox, _FakeCategory, _FakeCollector, _FakeElem, _FakeId, _FakeLinkDoc, _FakeLinkInst, _FakeView (+1 more)

### Community 44 - "Memory Telemetry"
Cohesion: 0.19
Nodes (6): _force_clr_gc(), _get_memory_mb(), MemoryTracker, Memory telemetry helpers for Dynamo/Python.NET runtime., Run CLR GC triple-call pattern and report private-memory delta., Return current (working_set_mb, private_bytes_mb) or (None, None).

### Community 45 - "Callsite Details"
Cohesion: 0.14
Nodes (14): `collect_view_elements`, `get_element_silhouette`, High-signal callsite details (approx), `init_view_raster`, `process_document_views`, `process_document_views_streaming`, `rasterize_annotations`, `render_model_front_to_back` (+6 more)

### Community 46 - "Golden Baseline Management"
Cohesion: 0.18
Nodes (11): `compare_golden.py` - Golden Baseline Comparison, Establishing Initial Golden Baseline, `generate_manifest.py` - Generate Golden Baseline, Integration with CI/CD, Notes, Scripts, See Also, SSM/VOP Exporter Tools (+3 more)

### Community 47 - "Operational Review"
Cohesion: 0.15
Nodes (12): Aligned, Divergent, Executive Assessment, Highest-Leverage Next Moves, HISTORICAL OPERATIONAL REVIEW / SUPERSEDED, Implementation vs Intent, Indeterminate, Observed Architecture (+4 more)

### Community 48 - "Bounding Box Policy Testing"
Cohesion: 0.28
Nodes (10): _BBox, ElemModelOnly, ElemThrows, ElemViewWins, FakeView, _Id, _P, test_resolve_element_bbox_falls_back_to_model() (+2 more)

### Community 49 - "CSV Export Overview"
Cohesion: 0.15
Nodes (13): Core CSV Headers (18 columns), CSV Headers Reference, Dependencies, Future Enhancements (Post-Phase 7), Integration Test (Dynamo), Notes, Overview, Phase 7: CSV Export Implementation Plan (+5 more)

### Community 50 - "Graph Querying"
Cohesion: 0.17
Nodes (10): graphify, add, exports, extraction-spec, github-and-merge, hooks, query, transcribe (+2 more)

### Community 51 - "Dynamo Test Scripts"
Cohesion: 0.17
Nodes (12): "0 views processed", AttributeError: 'Bounds2D' object has no attribute 'min_x', "AttributeError: 'View3D' object has no attribute...", Dynamo Test Scripts, Example Dynamo Python Node Setup, "ImportError: No module named vop_interwoven", Next Steps, "No elements found" (+4 more)

### Community 52 - "Face Selection Testing"
Cohesion: 0.26
Nodes (5): _FaceStub, Unit tests for deterministic planar front-face selection utilities.  These tests, Return a square polygon in *model* coords (plan view basis => model XY == UV)., _square_loop(), _XYZ

### Community 53 - "Proxy Generation Testing"
Cohesion: 0.17
Nodes (7): Test UV_AABB proxy creation from CellRect., Test UV_AABB width/height calculations., Test UV_AABB center calculation., Test UV_AABB edge generation for stamping., Test complete workflow: classify as TINY -> generate UV_AABB., Test proxy generation (UV_AABB creation)., TestProxyGeneration

### Community 54 - "Single Extraction Testing"
Cohesion: 0.47
Nodes (11): _call_name(), _calls_named(), _find_element_loop(), _find_function(), _is_elem_class_areal_if(), Static regression tests for the AREAL HIGH single-extraction contract., test_areal_path_does_not_probe_directional_high_or_low_keys_before_extraction(), test_areal_path_extracts_geometry_once_before_raster_decompose() (+3 more)

### Community 55 - "Implementation Roadmap"
Cohesion: 0.18
Nodes (11): 1) Gate or implement proxy `edges` mode, 2) Contract contradiction requiring code decision: AREAL fallback occlusion semantics, 3) Complete diagnostics wiring debt in exception handlers, 4) Remove or finalize dormant per-view disk cache path, 5) Implement explicit visibility-gating pass or narrow the contract, 6) Streaming signature-first TODO cleanup, 7) Align historical documentation footprint with current architecture doc, P0 (+3 more)

### Community 56 - "Manifest Generation"
Cohesion: 0.20
Nodes (10): main(), normalize_csv_for_hashing(), Write manifest entries to file.      Format:         CSV  filename.csv  <hash>, Normalize CSV content for hashing by excluding volatile columns.      Args:, Compute SHA256 hash of a file., Compute SHA256 hash of content bytes., Generate manifest entries from output directory.      Args:         output_dir:, sha256_content() (+2 more)

### Community 58 - "Annotation Classification"
Cohesion: 0.29
Nodes (6): Annotation Type Classification (7 Types: TEXT/TAG/DIM/DETAIL/LINES/REGION/OTHER), Golden Baseline Regression Testing, SSM-Compatible CSV Export Format, Dynamo Test Scripts README, Phase 7 CSV Export Plan (Historical/Superseded), HISTORICAL / SUPERSEDED

### Community 59 - "Exporter Pipeline Comparison"
Cohesion: 0.20
Nodes (10): A) Strict non-interwoven stage boundaries with explicit artifacts, B) Tier-1/Tier-2 cache contract separation, C) Authoritative Stage-5 visibility gating pass over already-projected primitives, D) Explicit B1/B2 model-hosted-2D split with allowlisted occluder upgrade path, E) Primitive context isolation in cache policy, Exporter Pipeline Comparison: Proposed Contract-Aligned Flow vs Current VOP Interwoven Code, Needs substantial new implementation (essentially from scratch), Practical migration path (minimal-risk order) (+2 more)

### Community 60 - "Repository Utilities"
Cohesion: 0.44
Nodes (8): Hit, _iter_py_files(), _load_whitelist(), main(), int, str, _repo_rel(), scan()

### Community 61 - "Model Presence Testing"
Cohesion: 0.36
Nodes (8): _mk_raster(), A cell with only model_proxy_key set (mask=False) must not be empty under ink mo, ViewRaster.has_model_proxy must return True when only proxy_key is set., test_csv_metrics_edge_counts_edges(), test_csv_metrics_ink_counts_occupancy_fill(), test_csv_metrics_ink_counts_proxy_key_only(), test_csv_metrics_occ_ignores_edges(), test_has_model_proxy_proxy_key_only()

### Community 62 - "Element Cache Testing"
Cohesion: 0.33
Nodes (5): test_element_cache_export_analysis_csv_includes_source_type(), test_element_cache_hit_upgrades_unknown_source_type(), test_export_view_element_map_json_merges_existing_file(), test_export_view_element_map_json_writes_view_index(), LRU element caching

### Community 63 - "Raster Semantics Testing"
Cohesion: 0.22
Nodes (6): A cell with model_proxy_key set but model_proxy_mask=False must not be empty., Proxy-key-only cells must not have w_occ written (no occlusion authority)., stamp_proxy_edge_idx must set model_proxy_mask so has_model_proxy returns True., test_proxy_key_only_no_mask_counts_as_model_present(), test_proxy_key_only_no_occlusion_written(), test_stamp_proxy_edge_sets_proxy_mask()

### Community 64 - "Metrics Relationships"
Cohesion: 0.25
Nodes (8): 1) Exact equalities (must always hold), 2) Subset/superset inequalities (must always hold), 3) Useful derived groups from the 8-state partition, 4) Legacy `views_vop` 4-way presence buckets, 5) What is allowed to exceed what, 6) Quick triage sequence when numbers look wrong, Metrics Totals Relationships (Set/Superset/Subset Cheat Sheet), Model classes are multihot (not disjoint)

### Community 65 - "Proxy Rendering"
Cohesion: 0.25
Nodes (8): _mark_rect_center_cell(), _mark_thin_band_along_long_axis(), Render TINY/LINEAR element: proxy edges + optional minimal mask.      Commentary, Stamp proxy edges into model_proxy_key layer.      NOTE: Edge rasterization is n, Mark center cell of rect in model_proxy_mask., Mark thin band along long axis of rect in model_proxy_mask., _render_proxy_element(), _stamp_proxy_edges()

### Community 66 - "View Capability Analysis"
Cohesion: 0.29
Nodes (7): 1) View capability split (model vs annotation-only), 2) View basis + bounds/crop primitives, 3) Candidate collection infrastructure, 4) Front-to-back ordering and depth hints, 5) Existing cache building blocks, 6) Annotation/model non-occlusion layering intent, Reusable with minimal effort

### Community 67 - "Code Mapping"
Cohesion: 0.33
Nodes (6): VOP Interwoven Code Map (Authoritative), VOP Interwoven Symbol Index, Scope, All top-level definitions, High-signal symbols, vop_interwoven symbol index (defs + callsites)

### Community 68 - "Diagnostics Contract"
Cohesion: 0.33
Nodes (6): Diagnostics Always-On Contract, Diagnostics Wiring Debt (TODO Placeholders in Exception Handlers), Diagnostics and Outputs, Diagnostics Is Not Logging, Diagnostics Contract, Required Fields

### Community 69 - "Render Pipeline Architecture"
Cohesion: 0.33
Nodes (6): Front-to-Back Depth Ordering for Model Processing, Interwoven Render Pipeline Architecture, Two-Tier Primitive Cache Contract (Proposed, Not Implemented), View Mode Resolution (MODEL_AND_ANNOTATION / ANNOTATION_ONLY / REJECTED), Pipeline Approach Comparison (Historical/Superseded), HISTORICAL ANALYSIS / SUPERSEDED

### Community 70 - "Raster Decomposition Testing"
Cohesion: 0.47
Nodes (3): _cells_from_rect(), test_decompose_to_rects_covers_l_shape_without_overlap(), test_decompose_to_rects_has_no_silent_24_rect_cap()

### Community 71 - "Cache Signature Verification"
Cohesion: 0.33
Nodes (5): Test script to verify VOP cache signature enhancement (Phase 1).  This script va, Test the element ID collection and signature generation.      Run this in Revit, Inspect actual cache files to verify schema v2 format.      Args:         cache_, test_cache_file_inspection(), test_signature_collection()

### Community 72 - "NumPy and Pillow Setup"
Cohesion: 0.33
Nodes (5): ensure_numpy(), ensure_pillow(), vop_interwoven/np_backend.py  NumPy and Pillow availability detection with optio, Return True if NumPy is importable.      If auto_install=True and NumPy is absen, Return True if Pillow is importable.      If auto_install=True and Pillow is abs

### Community 73 - "Development Tasks"
Cohesion: 0.40
Nodes (5): Adding a New Rasterization Strategy, Adding CSV Export Columns, Common Development Tasks, Debugging Geometry Issues, Modifying Element Collection

### Community 74 - "Occupancy Channel Testing"
Cohesion: 0.70
Nodes (4): _make_raster(), test_proxy_edges_enabled_perimeter_only_to_proxy_channel(), test_proxy_fill_affects_occlusion_not_model_ink_by_default(), test_real_silhouette_writes_model_ink_edges()

### Community 76 - "Bootstrap Utilities"
Cohesion: 0.60
Nodes (4): _install(), _is_importable(), VOP NumPy + Pillow Bootstrap ============================ Run once per machine (, run()

### Community 77 - "CSV Metrics Overview"
Cohesion: 0.40
Nodes (5): 1. Core Metrics (`views_core_YYYY-MM-DD.csv`), 2. VOP Extended Metrics (`views_vop_YYYY-MM-DD.csv`), CSV Validation Invariant, Target CSV Format (SSM Exporter), Two CSV Files

### Community 78 - "Occupancy Cell Partitioning"
Cohesion: 0.50
Nodes (5): 8-State Occupancy Cell Partition Invariant, External Cell Metrics (ExtFinalCells_DWG / _RVT Set Relationships), Multihot Model Class Cells (ModelClassCells_*), Legacy 4-Way Occupancy Partition (views_vop CSV), Metrics Totals Relationships Cheat Sheet

### Community 79 - "Testing Overview"
Cohesion: 0.50
Nodes (4): Golden Baseline Testing, Running Tests, Test Coverage Areas, Testing

### Community 80 - "Caching Architecture"
Cohesion: 0.50
Nodes (4): E) Caching architecture, Explicitly not cached, Implemented caches, Keying / guarantees

### Community 81 - "Copilot Instructions"
Cohesion: 0.50
Nodes (3): Core architecture constraints, GitHub Copilot Instructions — Revit SSM Exporter, graphify — Query the knowledge graph before browsing source

### Community 84 - "Purpose and Goals"
Cohesion: 0.67
Nodes (3): A) Purpose & Non-Goals, Non-goals (as implemented), Purpose

### Community 88 - "Output Format Overview"
Cohesion: 0.67
Nodes (3): Current Output Format (VOP Interwoven), Current Outputs Generated, JSON Structure (per view)

### Community 89 - "Gap Analysis"
Cohesion: 0.67
Nodes (3): Gap Analysis: Current vs. Target, What We Have (VOP Interwoven), What We Need to Add

## Knowledge Gaps
- **268 isolated node(s):** `graphify — Query the knowledge graph before browsing source`, `Core architecture constraints`, `bool`, `int`, `str` (+263 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **6 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Config` connect `Configuration Testing` to `Geometry Classification Testing`, `Test Configuration`, `Module Import Testing`, `UV Classification Testing`, `Linework Application`, `Graphics Semantics`, `External Source Analysis`, `Front Face Selection Testing`, `Image Alignment Probe`, `Final State Scanning`, `Identity Consistency Testing`, `Streaming Export Processing`, `Color ID Buffer Testing`, `View Raster Testing`, `Phase Validation`, `Proxy Generation Testing`, `Pipeline Diagnostics`, `CSV Export Testing`?**
  _High betweenness centrality (0.214) - this node is a cross-community bridge._
- **Why does `Test Scripts` connect `Phase Validation` to `Dynamo Test Scripts`, `CSV Export Testing`?**
  _High betweenness centrality (0.116) - this node is a cross-community bridge._
- **Why does `Dynamo Test Scripts` connect `Dynamo Test Scripts` to `Annotation Classification`, `Phase Validation`?**
  _High betweenness centrality (0.115) - this node is a cross-community bridge._
- **Are the 19 inferred relationships involving `Config` (e.g. with `_collect_expanded()` and `_current_resolution_bounds()`) actually correct?**
  _`Config` has 19 INFERRED edges - model-reasoned connections that need verification._
- **What connects `graphify — Query the knowledge graph before browsing source`, `Core architecture constraints`, `bool` to the rest of the system?**
  _268 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Mock Objects` be split into smaller, more focused modules?**
  _Cohesion score 0.051839464882943144 - nodes in this community are weakly interconnected._
- **Should `Test Configuration` be split into smaller, more focused modules?**
  _Cohesion score 0.052184769038701624 - nodes in this community are weakly interconnected._