# vop_interwoven symbol index (defs + callsites)

This index lists selected high-signal symbols (definitions + approximate callsites) used for navigation-first debugging.
Line numbers reflect the current source set as updated in this session.


## `Config.geometry_cache_max_items` / `Config.extents_scan_*`

**Definitions**
- vop_interwoven/config.py L56 (`Config.__init__` params/attrs)
  - `extents_scan_max_elements` (param L95, attr L141)
  - `extents_scan_time_budget_s` (param L96, attr L142)
  - `geometry_cache_max_items` (param L99, attr L145)
- vop_interwoven/config.py L364 (`Config.to_dict`) — exports these fields
- vop_interwoven/config.py L393 (`Config.from_dict`) — restores these fields

**Callsites (approx)**
- vop_interwoven/pipeline.py::process_document_views (constructs per-run `LRUCache(max_items=cfg.geometry_cache_max_items)`)
## `process_document_views`

**Definitions**
- vop_interwoven/pipeline.py L110 (function)

**Key internal decision boundary**
- Per-view orchestration boundary: resolves view mode, initializes raster, collects elements, renders model + annotations, exports raster, and aggregates per-view diagnostics.

**Callsites (approx)**
- vop_interwoven/entry_dynamo.py::run_vop_pipeline (primary entry)


## `init_view_raster`

**Definitions**
- vop_interwoven/pipeline.py L290 (function)

**Callsites (approx)**
- vop_interwoven/pipeline.py::process_document_views (per-view raster init)


## `export_view_raster`

**Definitions**
- vop_interwoven/pipeline.py L1214 (function)

**Callsites (approx)**
- vop_interwoven/pipeline.py::process_document_views (per-view export)


## `LRUCache`

**Definitions**
- vop_interwoven/core/cache.py L16 (class)
  - `get()` L37
  - `set()` L54
  - `stats()` L82

**Callsites (approx)**
- vop_interwoven/pipeline.py::process_document_views (constructs per-run geometry_cache)
- vop_interwoven/pipeline.py::init_view_raster (uses cache via `get()`)


## `run_vop_pipeline_with_png`

**Definitions**
- vop_interwoven/entry_dynamo.py L320 (function)

**Callsites (approx)**
- External/Dynamo entrypoint (sets output_dir defaults and writes debug json)


## `run_vop_pipeline_with_csv`

**Definitions**
- vop_interwoven/entry_dynamo.py L383 (function)

**Callsites (approx)**
- External/Dynamo entrypoint (sets output_dir defaults and writes debug json)
