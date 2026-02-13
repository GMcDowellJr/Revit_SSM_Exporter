# CURRENT_ARCHITECTURE

This document is the repository source of truth for what is implemented now.

## A) Purpose & Non-Goals

### Purpose
- The pipeline converts Revit views into rasterized model/annotation occupancy data with depth-aware model occlusion and exports to JSON/PNG/CSV surfaces. Evidence: `run_vop_pipeline*` entry points call `process_document_views` and then JSON/PNG/CSV exporters in `entry_dynamo.py`. (`run_vop_pipeline`, `run_vop_pipeline_with_png`, `run_vop_pipeline_with_csv`)【F:vop_interwoven/entry_dynamo.py†L306-L513】
- The model pass is explicitly interwoven (collection, depth ordering, extraction, rasterization in one pass) and treats model geometry as occlusion authority. Evidence: pipeline module header and `render_model_front_to_back`.【F:vop_interwoven/pipeline.py†L1-L14】【F:vop_interwoven/pipeline.py†L1709-L1769】

### Non-goals (as implemented)
- Proxy edge mode (`proxy_mask_mode="edges"`) is not implemented; `_stamp_proxy_edges` is a stub. This mode should not be considered production-complete. Evidence: `_stamp_proxy_edges` docstring and `pass`.【F:vop_interwoven/pipeline.py†L3333-L3347】
- A separate explicit post-projection visibility gate is not implemented; `is_element_visible_in_view` is permissive and always returns `True`. Evidence: function body and docstring.【F:vop_interwoven/revit/collection.py†L324-L345】

## B) Public entry points / API surface

### Primary callable surface
- `vop_interwoven.entry_dynamo.run_vop_pipeline(doc, view_ids, cfg=None)` is the base API and returns `{success, views, config, errors, summary}`.【F:vop_interwoven/entry_dynamo.py†L306-L352】
- `run_vop_pipeline_with_png(...)` wraps base execution and writes PNG (and optional JSON).【F:vop_interwoven/entry_dynamo.py†L354-L438】
- `run_vop_pipeline_with_csv(...)` wraps base execution and writes CSV (plus optional PNG/JSON/perf CSV).【F:vop_interwoven/entry_dynamo.py†L441-L560】
- `vop_interwoven.streaming.run_vop_pipeline_streaming(...)` is the memory-aware path that streams per-view export callbacks and persists root cache.【F:vop_interwoven/streaming.py†L627-L739】

### Stability notes
- Export row contract is centralized in `csv_export.export_pipeline_to_csv`, with fixed header lists and invariant checks for occupancy partitions. (`export_pipeline_to_csv`, `compute_cell_metrics`)【F:vop_interwoven/csv_export.py†L108-L216】【F:vop_interwoven/csv_export.py†L1111-L1256】

## C) Pipeline stages and ownership boundaries

Within `process_document_views` the stage ownership is:
- **View mode + identity + signature**: `resolve_view_mode`, `_extract_view_identity_for_csv`, `_view_signature`.【F:vop_interwoven/pipeline.py†L786-L893】
- **Cache check path**: root cache lookup (`root_cache.get_view`) and cache-hit result assembly.。【F:vop_interwoven/pipeline.py†L893-L967】
- **Raster initialization / bounds resolution**: `init_view_raster` (which delegates to view-basis bounds resolvers).【F:vop_interwoven/pipeline.py†L969-L973】【F:vop_interwoven/pipeline.py†L1404-L1467】
- **Model processing** (when mode allows): collection, linked expansion, depth sort, interwoven render loop via `render_model_front_to_back`.【F:vop_interwoven/pipeline.py†L995-L1031】【F:vop_interwoven/pipeline.py†L1709-L1769】
- **Annotation processing**: `rasterize_annotations`.【F:vop_interwoven/pipeline.py†L1035-L1040】
- **Cross-layer derivation**: `ViewRaster.finalize_anno_over_model`.【F:vop_interwoven/pipeline.py†L1042-L1046】【F:vop_interwoven/core/raster.py†L680-L721】
- **View export payload**: `export_view_raster` then optional root-cache write-through via `extract_metrics_from_view_result` + `root_cache.set_view`.【F:vop_interwoven/pipeline.py†L1048-L1103】

## D) Geometry extraction authority model

- Classification (`TINY`, `LINEAR`, `AREAL`) controls occlusion authority; strategy controls precision channel. This is codified in pipeline header semantics and render logic.。【F:vop_interwoven/pipeline.py†L17-L88】【F:vop_interwoven/pipeline.py†L1709-L1769】
- AREAL path attempts higher-fidelity geometry extraction and can contribute authoritative depth occupancy (`model_mask`/`z_min` path inside render stage).【F:vop_interwoven/pipeline.py†L1909-L2129】
- TINY/LINEAR write proxy representation and do not provide the same occlusion truth semantics as AREAL model geometry. `_render_proxy_element` uses proxy stamping/minmask helpers.。【F:vop_interwoven/pipeline.py†L3307-L3330】
- Fallback ownership lives in extraction and silhouette modules (`extract_areal_geometry`, `get_element_silhouette`) invoked by render stage.。【F:vop_interwoven/pipeline.py†L105-L106】【F:vop_interwoven/pipeline.py†L1971-L2050】

## E) Caching architecture

### Implemented caches
- **RootStyleCache (authoritative for streaming and metrics reuse):** single JSON file (`vop_view_cache.json`) keyed by view id + signature, storing metadata/metrics/row payload, not full rasters. (`RootStyleCache`, `get_view`, `set_view`)【F:vop_interwoven/root_cache.py†L20-L41】【F:vop_interwoven/root_cache.py†L98-L127】【F:vop_interwoven/root_cache.py†L141-L224】
- **ElementCache (LRU):** in-memory/per-run fingerprint cache keyed by `(elem_id, source_id)`, with optional persistence/export hooks in pipeline. (`ElementCache.get_or_create_fingerprint`)【F:vop_interwoven/core/element_cache.py†L420-L520】【F:vop_interwoven/pipeline.py†L680-L756】
- **Geometry cache config hooks:** bounded geometry cache size is configured (`geometry_cache_max_items`) and threaded into render call.。【F:vop_interwoven/config.py†L104-L107】【F:vop_interwoven/pipeline.py†L1027-L1031】

### Keying / guarantees
- View-signature includes view/config/doc state inputs and is used as cache match token. (`_view_signature`)【F:vop_interwoven/pipeline.py†L270-L387】
- Root cache invalidates on exporter version, config hash, and project guid mismatch during load.。【F:vop_interwoven/root_cache.py†L69-L87】

### Explicitly not cached
- Root cache does not store full raster arrays by design.。【F:vop_interwoven/root_cache.py†L2-L6】【F:vop_interwoven/root_cache.py†L141-L146】
- Per-view disk cache code exists in `pipeline.py` but is forcibly disabled (`view_cache_enabled = False`), so this mechanism is dormant.。【F:vop_interwoven/pipeline.py†L571-L594】

## F) Diagnostics & export contracts

- Diagnostics object is intended as always-on structured sink (`docs/diagnostics_contract.md`).【F:vop_interwoven/docs/diagnostics_contract.md†L1-L18】
- Raster export contract includes model/annotation layers, metadata arrays, and summary diagnostics in `export_view_raster`.【F:vop_interwoven/pipeline.py†L3376-L3502】
- CSV export contract emits core and VOP CSVs, plus occlusion CSV file scaffold, and skips failed views. (`export_pipeline_to_csv`)【F:vop_interwoven/csv_export.py†L1081-L1108】【F:vop_interwoven/csv_export.py†L1111-L1260】
- Occupancy partition invariant is enforced: `TotalCells = Empty + ModelOnly + AnnoOnly + Overlap`. (`compute_cell_metrics`)【F:vop_interwoven/csv_export.py†L205-L216】

## G) Tests & regression discipline

- Test suite is pytest-based with focused coverage for pipeline diagnostics, caching, geometry extraction, raster semantics, CSV outputs, and Dynamo-focused tests under `tests/dynamo/`.【F:tests/test_pipeline_diagnostics.py†L1-L40】【F:tests/test_csv_export_diagnostics.py†L1-L40】【F:tests/test_geometry.py†L1-L40】【F:tests/dynamo/README.md†L1-L70】
- Practical “green” for code changes in this repo is: targeted unit tests for touched contracts pass, with no regression in cache/CSV invariants and diagnostics behavior for modified paths.

## H) Known gaps / explicitly unsupported modes

1. **Proxy edge mask mode stub:** `_stamp_proxy_edges` is unimplemented (`pass`).【F:vop_interwoven/pipeline.py†L3333-L3347】
2. **Visibility-gating stub behavior:** `is_element_visible_in_view` always returns `True`; no explicit category/template/manual-hide gate beyond collector behavior.。【F:vop_interwoven/revit/collection.py†L324-L345】
3. **Streaming signature TODO path:** legacy TODO remains in `process_document_views_streaming` scaffolding around signature-first cache flow.。【F:vop_interwoven/streaming.py†L49-L58】
4. **Diagnostics instrumentation debt:** many exception handlers still carry “no diag in scope / add diagnostics” placeholders in several modules, so diagnostics coverage is incomplete. Example locations: `entry_dynamo.py`, `csv_export.py`, `root_cache.py`.【F:vop_interwoven/entry_dynamo.py†L399-L402】【F:vop_interwoven/csv_export.py†L34-L37】【F:vop_interwoven/root_cache.py†L186-L214】
