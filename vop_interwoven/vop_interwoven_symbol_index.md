# vop_interwoven symbol index (defs + callsites)

This index lists selected high-signal symbols (definitions + approximate callsites) used for navigation-first debugging.

Line numbers reflect the current source set in vop_interwoven.zip.

## Entrypoints: Dynamo / thinrunner

**Definitions**
- vop_interwoven/entry_dynamo.py
  - `run_vop_pipeline` (L333)
- vop_interwoven/entry_dynamo.py
  - `run_vop_pipeline_with_png` (L381)
- vop_interwoven/entry_dynamo.py
  - `run_vop_pipeline_with_csv` (L458)
- vop_interwoven/dynamo_helpers.py
  - `run_pipeline_from_dynamo_input` (L226)
- vop_interwoven/thinrunner_streaming.py
  - `thinrunner` (L?)

**Callsites (approx)**
- `run_vop_pipeline`: vop_interwoven/csv_export.py, vop_interwoven/dynamo_helpers.py, vop_interwoven/entry_dynamo.py, vop_interwoven/png_export.py, vop_interwoven/streaming.py, vop_interwoven/thinrunner_streaming.py
- `run_pipeline_from_dynamo_input`: vop_interwoven/dynamo_helpers.py

## Pipeline orchestration + cache keying

**Definitions**
- vop_interwoven/pipeline.py
  - `process_document_views` (L269)
- vop_interwoven/pipeline.py
  - `init_view_raster` (L837)
- vop_interwoven/pipeline.py
  - `_view_signature` (L177)

**Callsites (approx)**
- `process_document_views`: vop_interwoven/entry_dynamo.py, vop_interwoven/pipeline.py, vop_interwoven/streaming.py
- `init_view_raster`: vop_interwoven/pipeline.py
- `_view_signature`: vop_interwoven/pipeline.py, vop_interwoven/streaming.py
- `model_clip_bounds`: vop_interwoven/pipeline.py, vop_interwoven/core/raster.py

## View bounds resolution (crop / annotation expansion)

**Definitions**
- vop_interwoven/revit/view_basis.py
  - `resolve_view_bounds` (L561)
- vop_interwoven/revit/view_basis.py
  - `resolve_annotation_only_bounds` (L1130)

**Callsites (approx)**
- `resolve_view_bounds`: vop_interwoven/pipeline.py, vop_interwoven/revit/view_basis.py
- `resolve_annotation_only_bounds`: vop_interwoven/pipeline.py, vop_interwoven/revit/view_basis.py
- `model_bounds_uv`: vop_interwoven/pipeline.py, vop_interwoven/revit/view_basis.py

## Annotation extents + stamping

**Definitions**
- vop_interwoven/revit/annotation.py
  - `compute_annotation_extents` (L83)
- vop_interwoven/revit/annotation.py
  - `rasterize_annotations` (L762)
- vop_interwoven/revit/annotation.py
  - `get_annotation_bbox` (L731)

**Callsites (approx)**
- `compute_annotation_extents`: vop_interwoven/revit/annotation.py, vop_interwoven/revit/view_basis.py
- `rasterize_annotations`: vop_interwoven/pipeline.py, vop_interwoven/revit/annotation.py

## Raster write boundaries (model clip enforcement)

**Definitions**
- vop_interwoven/core/raster.py
  - `ViewRaster` (L155)
- vop_interwoven/core/raster.py
  - `ViewRaster._cell_in_model_clip` (L212)
- vop_interwoven/core/raster.py
  - `ViewRaster.try_write_cell` (L490)
- vop_interwoven/core/raster.py
  - `ViewRaster.rasterize_open_polylines` (L233)
- vop_interwoven/core/raster.py
  - `ViewRaster.stamp_proxy_edge_idx` (L674)

**Callsites (approx)**
- `_cell_in_model_clip`: vop_interwoven/core/raster.py
- `rasterize_open_polylines`: vop_interwoven/pipeline.py, vop_interwoven/core/raster.py, vop_interwoven/core/silhouette.py
- `stamp_proxy_edge_idx`: vop_interwoven/pipeline.py, vop_interwoven/core/raster.py
- `try_write_cell`: vop_interwoven/pipeline.py, vop_interwoven/core/raster.py

## Bbox projection helpers

**Definitions**
- vop_interwoven/revit/collection.py
  - `_project_element_bbox_to_cell_rect` (L624)

**Callsites (approx)**
- `_project_element_bbox_to_cell_rect`: vop_interwoven/pipeline.py, vop_interwoven/revit/annotation.py, vop_interwoven/revit/collection.py

## Streaming pipeline

**Definitions**
- vop_interwoven/streaming.py
  - `run_vop_pipeline_streaming` (L563)

**Callsites (approx)**
- `run_vop_pipeline_streaming`: vop_interwoven/entry_dynamo.py, vop_interwoven/streaming.py, vop_interwoven/thinrunner_streaming.py
- `_view_signature`: vop_interwoven/pipeline.py, vop_interwoven/streaming.py

## Trace map consistency notes

- Trace map references `_normalize_views_input` in `vop_interwoven/dynamo_helpers.py`, but no such symbol exists in the current source set. (Likely renamed/removed.)

- Prior `_view_signature` import mismatch is resolved: `_view_signature` is a module-level def in `vop_interwoven/pipeline.py` and is imported by `vop_interwoven/streaming.py`.
