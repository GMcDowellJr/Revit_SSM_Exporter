# Stage A Image Alignment Probe

`probe_stage_a_image_alignment.py` is a standalone Revit 2025 / Dynamo 3.3 CPython3 probe for Stage A image geometry and pixel-to-view alignment. It does **not** modify production Stage A code.

## Purpose

Run this probe to answer these research questions:

1. How Revit maps an existing view crop or a supplied UV crop into a TIFF exported with `ZoomFitType.FitToPage`.
2. Whether the raw TIFF includes padding or margins.
3. The pixel orientation relative to view U/V axes.
4. Whether `ImageExportOptions.PixelSize` equals the actual TIFF width.
5. Whether two sequential exports with identical geometry settings have identical dimensions and transforms.
6. Whether an original model crop can be placed accurately in a larger annotation-expanded VOP canvas without expanding the Revit model crop.
7. What model-capable views with inactive crops do when exported without forcing a crop.

## Code inspected and reused

The probe follows the transaction-group safety pattern proven by `probe_stage_a_transaction_group_export.py`: temporary work is committed in child transactions, exports occur with no child transaction open, and the enclosing `TransactionGroup` is rolled back in `finally`.

The probe mirrors the current Stage A and VOP geometry calculation without changing it:

- `make_view_basis()` supplies view origin/right/up/forward.
- `resolve_view_bounds()` centralizes crop, synthetic extents, annotation expansion, cap reporting, grid dimensions, and requested/effective cell sizes.
- `xy_bounds_from_crop_box_all_corners()` is used for complete transformed crop-box UV bounds.
- `crop_box_from_uv_bounds()` is used only for temporary diagnostic crop variants.
- `compute_annotation_extents()` is used to record annotation-expanded canvas bounds separately from model bounds.
- `pipeline.py::init_view_raster()` establishes the VOP grid dimensions and records `model_clip_bounds` only best-effort; this probe does not assume that field exists for inactive crops.
- `color_id_buffer.py::_export_tiff()` and `view_raster_export.py` informed the exact `ExportImage` settings: `ZoomFitType.FitToPage`, horizontal `PixelSize`, and lossless TIFF.

## Repo import path in Dynamo

Dynamo CPython pasted-node execution may not define `__file__`. The probe therefore does not require `__file__` to find the repository. It first tries existing imports, then checks `REVIT_SSM_EXPORTER_ROOT` / `VOP_REPO_ROOT`, the output-directory ancestors, the current working directory ancestors, and common checkout locations. If imports still fail, set `REVIT_SSM_EXPORTER_ROOT` to the local `Revit_SSM_Exporter` checkout before running Dynamo.

## Dynamo wiring

Create a Dynamo Python node set to CPython3 and wire:

```text
IN[0] = target Revit view
IN[1] = output directory string
IN[2] = export mode string:
        "original"
        "model_bounds"
        "canvas_bounds"
        "all"
IN[3] = create temporary calibration markers: bool, default True
```

Recommended first run:

```text
IN[2] = "all"
IN[3] = true
```

The node returns a dictionary through `OUT` with paths, rollback result, actual image dimensions, detected orientation, content rectangle, marker residuals, sequential equality, model-to-canvas placement calculation, and a `PASS` / `FAIL` / `INCONCLUSIVE` conclusion.

## Output artifacts

For each view and requested mode, the probe writes:

```text
<view>_<id>.<mode>.alignment_1.tiff
<view>_<id>.<mode>.alignment_2.tiff
<view>_<id>.<mode>.alignment.json
```

Return all TIFFs and JSON files for analysis.

## Export modes

- `original`: exports with the view's existing crop behavior and does not force new bounds.
- `model_bounds`: temporarily forces the crop to verified model-only bounds when available. For inactive crops, the model bounds may come from synthetic visible extents instead of an existing crop.
- `canvas_bounds`: temporarily forces the crop to the annotation-expanded canvas. This is diagnostic only; it intentionally shows whether expanding the Revit crop would admit additional model geometry and must not be treated as the proposed production solution.
- `all`: runs all three modes.

Each mode exports two sequential TIFFs with identical settings and compares dimensions plus SHA-256 hashes.

## Calibration markers

When `IN[3]` is `true`, the probe creates temporary view-specific detail-curve cross markers near:

- lower-left
- lower-right
- upper-left
- upper-right
- center

Each marker uses a distinct exact RGB color and records expected UV coordinates, RGB, and created element IDs. If the active model view cannot create detail curves, the JSON reports that failure and falls back to weaker content-rectangle measurements instead of claiming exact marker alignment.

All marker creation occurs inside the enclosing transaction group and must disappear after rollback.

## Image analysis

When Pillow is available in the Dynamo CPython environment, each TIFF records:

- actual width and height
- file size and SHA-256
- number of distinct colors
- dominant background color
- non-background content rectangle
- exact or nearest detected centroid for each calibration color
- missing markers
- sequential-export equality
- X/Y orientation observations
- padding rectangle
- marker residuals against the tested mapping

The tested mapping is:

```text
u = u_min + (x + 0.5 - content_x0) * UV_width / content_width
v = v_max - (y + 0.5 - content_y0) * UV_height / content_height
```

The probe reports observations and residuals; it does not assume the equation is correct.

## Model-to-canvas placement calculation

The JSON computes the proposed placement of the model-crop image inside the larger annotation-expanded canvas:

- observed pixels per model unit
- canvas pixel dimensions at that density
- model-image pixel offset
- whether offsets are integral
- maximum rounding error in pixels and model units
- whether lossless padding is sufficient or resampling would be required

## State safety pass criteria

A pass requires:

- the enclosing `TransactionGroup` rolled back successfully
- crop state and crop transform match before/after capture
- display-related captured state matches before/after capture
- calibration elements no longer exist
- no captured state differences are reported

The JSON also captures document `IsModified` before and after. Revit may already have a modified document before the probe; evaluate this field in context.

## Required runtime matrix

Run the same Dynamo graph separately on:

1. Floor plan with active crop
2. Floor plan with inactive crop
3. Reflected ceiling plan (RCP)
4. Section
5. Elevation
6. Detail view

For each run, return:

- all `*.alignment_1.tiff` files
- all `*.alignment_2.tiff` files
- all `*.alignment.json` files
- the Dynamo `OUT` value copied as text if possible
- view name, view id, view type, and whether the crop was active before running

## Interpreting conclusions

- `PASS`: rollback/state safety passed and markers were detected consistently.
- `INCONCLUSIVE`: rollback/state safety passed, but marker detection or Pillow analysis was incomplete.
- `FAIL`: rollback/state safety failed, unsupported inputs were supplied, or the probe observed persistent mutated state.

Unsupported, perspective, template, and annotation-only views are rejected clearly because this probe is specifically for Stage A model-geometry image alignment.
