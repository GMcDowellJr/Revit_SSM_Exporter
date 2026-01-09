# vop_interwoven symbol index (defs + callsites)

This index lists selected high-signal symbols (definitions + approximate callsites) used for navigation-first debugging.
It is intentionally scoped (not exhaustive) and focuses on Dynamo entrypoints + export side-effects relevant to recent changes.

## `thinrunner` (Dynamo Python node entry)

**Definitions**
- vop_interwoven/thinrunner.py
  - Reads Dynamo `IN[]` contract:
    - `IN[0]` view ids / view elements
    - `IN[1]` tag/date override (opaque string)
    - `IN[2]` output_dir (optional)
  - Constructs `Config()` and calls `run_pipeline_from_dynamo_input(...)`

**Callsites (approx)**
- Dynamo graph Python node (thin runner)

## `run_pipeline_from_dynamo_input`

**Definitions**
- vop_interwoven/dynamo_helpers.py
  - `run_pipeline_from_dynamo_input(views_input, output_dir, pixels_per_cell, config, verbose, export_csv, export_json, export_perf_csv, export_png, ...)`

**Callsites (approx)**
- vop_interwoven/thinrunner.py

## `run_vop_pipeline_with_png`

**Definitions**
- vop_interwoven/entry_dynamo.py
  - `run_vop_pipeline_with_png(doc, view_ids, cfg=None, output_dir=None, pixels_per_cell=4, export_json=True, ...)`

**Callsites (approx)**
- vop_interwoven/dynamo_helpers.py when `export_csv=False`

## `run_vop_pipeline_with_csv`

**Definitions**
- vop_interwoven/entry_dynamo.py
  - `run_vop_pipeline_with_csv(doc, view_ids, cfg=None, output_dir=None, pixels_per_cell=4, export_json=False, export_png=True, export_perf_csv=True, date_override=None, ...)`

**Callsites (approx)**
- vop_interwoven/dynamo_helpers.py when `export_csv=True`

## `export_pipeline_to_csv`

**Definitions**
- vop_interwoven/csv_export.py
  - `export_pipeline_to_csv(pipeline_result, output_dir, config, doc=None, diag=None, date_override=None)`
  - `date_override` semantics:
    - If parseable as date/datetime → drives Date column + filename date
    - Else treated as opaque tag → incorporated into RunId and filenames; Date defaults to today

**Callsites (approx)**
- vop_interwoven/entry_dynamo.py (`run_vop_pipeline_with_csv`)
- (optional legacy) vop_interwoven/thinrunner.py manual export branch

## `export_pipeline_results_to_pngs`

**Definitions**
- vop_interwoven/png_export.py
  - `export_pipeline_results_to_pngs(pipeline_result, output_dir, pixels_per_cell, cut_vs_projection=True, ...)`

**Callsites (approx)**
- vop_interwoven/entry_dynamo.py (`run_vop_pipeline_with_png`, `run_vop_pipeline_with_csv` when export_png=True)

## `Config` export-related knobs

**Definitions**
- vop_interwoven/config.py
  - `Config` (export- and raster-related fields referenced by entry + export layers)
  - Note: dynamic attributes (e.g. `cfg.debug_json_detail`) may exist at runtime but are only meaningful if consumed downstream.

**Callsites (approx)**
- vop_interwoven/entry_dynamo.py (default config creation / to_dict for JSON snapshot)
- vop_interwoven/thinrunner.py (constructs + overrides)
