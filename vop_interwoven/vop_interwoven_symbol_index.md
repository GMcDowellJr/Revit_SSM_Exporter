# VOP Interwoven — Symbol Index

## pipeline.py
- `process_document_views`
- `_view_signature`
- `_cfg_hash`

## root_cache.py
- `RootStyleCache`
  - `load`
  - `get_view`
  - `set_view`
  - `save`
- `compute_config_hash`
- `extract_metrics_from_view_result`

## csv_export.py
- `export_pipeline_to_csv`
- `view_result_to_core_row`
- `view_result_to_vop_row`
- `view_result_to_perf_row`

## streaming.py
- `run_vop_pipeline_streaming`
- `StreamingExporter`

## Notes
- No new public symbols introduced in this iteration
- Semantic ownership of cache-hit behavior shifted to `pipeline.py`

