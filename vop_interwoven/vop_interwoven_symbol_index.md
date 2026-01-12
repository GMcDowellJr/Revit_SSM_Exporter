# vop_interwoven symbol index (defs + callsites)

This index lists selected high-signal symbols (definitions + approximate callsites) used for navigation-first debugging.
Line numbers reflect the current source set as present in vop_interwoven.zip.

## `run_vop_pipeline` / `run_vop_pipeline_with_png` / `run_vop_pipeline_with_csv`

**Definitions**
- vop_interwoven/entry_dynamo.py
  - `run_vop_pipeline` (L272)
  - `run_vop_pipeline_with_png` (L320)
  - `run_vop_pipeline_with_csv` (L383)

**Callsites (approx)**
- Dynamo entrypoints / thinrunner: vop_interwoven/thinrunner.py

## `run_vop_pipeline_streaming` / `process_document_views_streaming`

**Definitions**
- vop_interwoven/streaming.py
  - `run_vop_pipeline_streaming` (L563)
  - `process_document_views_streaming` (L496)
  - `StreamingExporter` (L168)

**Callsites (approx)**
- vop_interwoven/thinrunner_streaming.py (imports + calls `run_vop_pipeline_streaming`)
- External/Dynamo could call `vop_interwoven.streaming.run_vop_pipeline_streaming` directly

## `RootStyleCache`

**Definitions**
- vop_interwoven/root_cache.py
  - `RootStyleCache` (L34)
  - `compute_config_hash` (L191)
  - `extract_metrics_from_view_result` (L241)

**Callsites (approx)**
- vop_interwoven/streaming.py (persistent “root style” caching of per-view summaries/metrics)

## `ElementCache` / `ElementFingerprint`

**Definitions**
- vop_interwoven/core/element_cache.py
  - `ElementFingerprint` (L50)
  - `ElementCache` (L182)

**Callsites (approx)**
- vop_interwoven/pipeline.py: view signature / element tracking (see note below)

## `process_document_views`

**Definitions**
- vop_interwoven/pipeline.py
  - `process_document_views` (L126)

**Key internal decision boundary**
- Per-view orchestration boundary: resolves view mode, initializes raster, collects elements, renders model + annotations, exports raster, and aggregates per-view diagnostics.

## `_view_signature` (⚠️ structural mismatch)

**Observed in source**
- In vop_interwoven/pipeline.py, `_view_signature(...)` is defined at **L263** but it is **indented** (nested inside another function), so it is **not a module-level symbol**.

**Observed import**
- vop_interwoven/streaming.py imports it:
  - `from vop_interwoven.pipeline import _view_signature  # Need to expose this`

**Implication**
- As the code exists in the zip, that import would raise `ImportError` at runtime. Either:
  - `_view_signature` should be de-indented to module scope, or
  - streaming should not import it (and should call whatever public API is intended).
