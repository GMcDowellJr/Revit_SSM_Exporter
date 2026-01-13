# VOP Interwoven — Trace Map

## Streaming Execution Trace (Current)

1. **Streaming entry**
   - `run_vop_pipeline_streaming()`

2. **Per-view dispatch**
   - `pipeline.process_document_views()`

3. **Signature computation**
   - `_view_signature(view, cfg)`

4. **Root cache check**
   - `RootStyleCache.get_view(view_id, signature)`

5. **Decision: HIT / MISS**

### HIT path
- Build result from cached entry:
  - identity fields
  - metrics
  - timings
  - row_payload
- Mark `from_cache=True`
- Skip raster/model generation
- Return to streaming layer

### MISS path
- Initialize raster
- Collect model + annotation geometry
- Compute metrics
- Build CSV row payload
- Persist to root cache via `RootStyleCache.set_view`

6. **Streaming output**
- CORE CSV row written
- VOP CSV row written
- PERF CSV row written
- PNG written **only on MISS**

## Notes
- Root cache HIT short-circuits expensive work
- Disk per-view cache is not consulted

