# Diagnostics Contract

The `Diagnostics` object is always enabled.

## Required Fields
Each recorded error or warning must include:
- phase (collection, bounds, geometry, raster, export)
- view id (if applicable)
- element id (if applicable)
- source type (HOST | LINK | DWG)
- exception type
- message

## Diagnostics Is Not Logging
- Do not `print()` for error reporting.
- Do not swallow errors after recording them unless recovery is safe.

## Diagnostics and Outputs
- Exporters must declare which raster layers they rely on.
- Bounds resolution must record:
  - source (crop, extents, fallback)
  - whether capping occurred
  - confidence level
