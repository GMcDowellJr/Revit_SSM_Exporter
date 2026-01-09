# vop_interwoven symbol index (defs + callsites)

## `process_document_views`

## `_classify_uv_rect`

**Definitions**
- vop_interwoven/pipeline.py L687 (function)

**Callsites (approx)**
- vop_interwoven/pipeline.py::render_model_front_to_back (used to choose UV rect strategy)

**Definitions**
- vop_interwoven/pipeline.py L110 (function)

**Callsites (approx)**
- vop_interwoven/entry_dynamo.py::run_vop_pipeline (calls at/near L176)

## `init_view_raster`

**Definitions**
- vop_interwoven/pipeline.py L290 (function)

**Callsites (approx)**
- vop_interwoven/pipeline.py::process_document_views (calls at/near L110)

## `render_model_front_to_back`

**Definitions**
- vop_interwoven/pipeline.py L374 (function)

**Callsites (approx)**
- vop_interwoven/pipeline.py::process_document_views (calls at/near L110)

## `export_view_raster`

**Definitions**
- vop_interwoven/pipeline.py L1196 (function)

**Callsites (approx)**
- vop_interwoven/pipeline.py::process_document_views (calls at/near L110)

## `get_element_silhouette`

**Definitions**
- vop_interwoven/core/silhouette.py L503 (function)

**Callsites (approx)**
- vop_interwoven/pipeline.py::render_model_front_to_back (calls at/near L374)

## `_cad_curves_silhouette`

**Definitions**
- vop_interwoven/core/silhouette.py L369 (function)

**Callsites (approx)**
- vop_interwoven/core/silhouette.py::get_element_silhouette (calls at/near L464)

## `rasterize_open_polylines`

**Definitions**
- vop_interwoven/core/raster.py L243 (method `ViewRaster.rasterize_open_polylines`)

**Callsites (approx)**
- vop_interwoven/pipeline.py::render_model_front_to_back (calls within model rasterization)

## `_symbolic_curves_silhouette`

**Definitions**
- vop_interwoven/core/silhouette.py L239 (function)

**Callsites (approx)**
- vop_interwoven/core/silhouette.py::get_element_silhouette (calls at/near L464)

## `_front_face_loops_silhouette`

**Definitions**
- vop_interwoven/core/silhouette.py L700 (function)

**Callsites (approx)**
- vop_interwoven/core/silhouette.py::get_element_silhouette (calls at/near L464)

## `make_view_basis`

**Definitions**
- vop_interwoven/revit/view_basis.py L133 (function)

**Callsites (approx)**
- vop_interwoven/pipeline.py::init_view_raster (calls at/near L290)
- vop_interwoven/pipeline.py::render_model_front_to_back (calls at/near L374)
- vop_interwoven/revit/annotation.py::rasterize_annotations (calls at/near L577)

## `resolve_view_bounds`

**Definitions**
- vop_interwoven/revit/view_basis.py L561 (function)

**Callsites (approx)**
- vop_interwoven/pipeline.py::init_view_raster (calls at/near L290)

## `resolve_view_mode`

**Definitions**
- vop_interwoven/revit/view_basis.py L995 (function)

**Callsites (approx)**
- vop_interwoven/pipeline.py::init_view_raster (calls at/near L290)
- vop_interwoven/pipeline.py::process_document_views (calls at/near L110)

## Session-added / updated symbols

### vop_interwoven/core/silhouette.py
- get_element_silhouette(..., diag=None)  # signature extended to accept per-view Diagnostics
- _symbolic_curves_silhouette(..., diag=None)  # signature extended to accept diag
- _family_region_outlines_cached(..., diag=None)  # new helper (family-doc extraction)
- _collect_regions_recursive(..., diag=None)  # new helper (nested-family recursion)
- _FAMILY_REGION_OUTLINE_CACHE  # new module-level cache
- _FAMILY_FAMDOC_REGION_CACHE   # new module-level cache
- _compose_transform            # new helper (transform composition)
- family_region diagnostics callsites:
  - family_region.collect
  - family_region.recurse
  - family_region.emit

### vop_interwoven/pipeline.py
- callsite: get_element_silhouette(..., diag=diag)  # pipeline threads per-view Diagnostics into silhouette

---

# Session delta notes (added 2026-01-08)

This navigation artifact was updated to reflect changes discussed/applied during the "silhouette family masking region + diagnostics wiring" session:

- `pipeline.py`: passes per-view `Diagnostics` (`diag`) into silhouette extraction entrypoint.
- `core/silhouette.py`:
  - signatures updated to accept optional `diag` and propagate it
  - added family-document outline extraction helpers (FilledRegion + masking regions via `FilledRegion.IsMasking`)
  - added nested-family recursion helpers with depth + budget guards
  - added structured diagnostic events:
    - `silhouette|family_region.collect`
    - `silhouette|family_region.recurse`
    - `silhouette|family_region.emit`

Line numbers and callsite offsets remain approximate until the repo-wide extractor is re-run.
