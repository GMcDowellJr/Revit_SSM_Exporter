# Design note: the analysis grid (analysis layer, item 2)

Status: **proposal for review**, 2026-09-30. Nothing here is implemented yet.
Evidence: runs `pipeline_0930_1249` (74320d7) and `pipeline_0930_1453` (99c7c44,
main after PR #222). Their 16 captures are byte-identical, so every figure
below holds for both.

## Why this comes first

The Stage A capture no longer knows about cells. Occupancy (empty /
model-only / annotation-only / overlap), comparison against the geometry path,
and the per-cell metrics all need one answer to: *which cell does each pixel of
each capture fall in?* Everything else in the analysis layer consumes that
answer, so it has to be defined once, in one function, and tested by
composition (CLAUDE.md, defect class 1).

## What exists today, and what it gets wrong on the current sidecars

`tools/colorid_to_occupancy.py` is the only grid builder. Run on 1453:

| Symptom | Cause |
|---|---|
| **Every model view gets a 64-cell-wide grid.** Plan_CropActive: 64 × 26 cells of **4.40 ft**, where 1/8" at 1:96 is **1.00 ft**. Plan_CropInActive: 6.0 ft cells. It is labelled `basis: "assumed"` and not refused. | Without a geometry run, `grid_assumed()` reads `backoff_floor_px` as "raster W". Since the registered capture, that field is the capture's own **64 px minimum**. `color_id_buffer._export_tiff` says so ("THE FLOOR IS NO LONGER THE CELL GRID"); the tool was never updated. |
| **All 8 annotation sidecars fail** ("no usable bounds_xy"). | The annotation pass records no crop. Its pixels only mean something after `register_stage_a_annotation` puts them on the model lattice, and the tool never reads the registered output. |
| **Link-model pixels count as empty.** | "Model" means `color_assignment_map` only; `link_category_color_map` is not read. That is about 210k px on Plan_RVTLink and RCP (S1). |
| **The pixel→UV mapping is the nominal crop's.** | It uses `crop_uv` and the clamp/pad model. The next section shows this disagrees with the measured ticks by up to 59 % of a cell. |

## Measured geometry of the 8 test views

Cell = `cell_size_paper_in` (0.125, from `run_meta.json`) × view scale / 12.
"Tick vs crop" is the largest `model_marks_vs_lattice` corner deviation in the
`.registered.json` record: where the ticks were measured in the model image,
against where the nominal crop mapping predicts them.

| View | Scale | Cell (ft) | px/cell | Model image (px) | Cells over crop A | Anno canvas (px) | Cells over union | Tick vs crop (px) | % of a cell |
|---|---|---|---|---|---|---|---|---|---|
| Elevation_CropActive | 1:96 | 1.000 | 18.75 | 5164 × 1267 | 276 × 68 | 5490 × 1817 | 294 × 98 | 0.40 | 2 % |
| ModelCallout_CropActive | 1:8 | 0.083 | 18.76 | 665 × 368 | 36 × 20 | 1061 × 445 | 58 × 25 | 0.69 | 4 % |
| Plan_CropActive | 1:96 | 1.000 | 18.75 | 4818 × 1623 | 257 × 87 | 5548 × 2366 | 297 × 127 | 1.29 | 7 % |
| Plan_CropInActive | 1:96 | 1.000 | **4.15** | 4285 × 10000 | 1034 × 2412 | 4285 × 10000 | 1034 × 2412 | **2.43** | **59 %** |
| Plan_DWG | 1:96 | 1.000 | 18.75 | 4818 × 1623 | 257 × 87 | 4818 × 1623 | 257 × 87 | 1.52 | 8 % |
| Plan_RVTLink | 1:96 | 1.000 | 18.75 | 4818 × 1623 | 257 × 87 | 4818 × 1623 | 257 × 87 | 1.23 | 7 % |
| RCP_CropActive | 1:96 | 1.000 | 18.75 | 5166 × 1749 | 276 × 94 | 5577 × 2366 | 299 × 127 | 1.57 | 8 % |
| Section_CropActive | 1:48 | 0.500 | 18.75 | 884 × 3045 | 48 × 163 | 1072 × 3420 | 58 × 183 | 0.40 | 2 % |

Four facts the design has to absorb:

1. **Cell edges fall mid-pixel.** At 150 dpi a 1/8" cell is 18.75 px, so
   cells are alternately 18 and 19 px wide. On a capped view (Plan_CropInActive,
   33 dpi achieved) a cell is only 4.15 px.
2. **The nominal crop mapping is not the image.** Plan_CropActive's lattice
   predicted 1624 rows and Revit produced 1623 (`predicted_derived_px` vs
   `actual_h`; the unresolved F4 note in `_export_tiff`). Its ticks sit 1.29 px
   off the prediction at the top and 0.38 px at the bottom. On the capped view
   the vertical scale is off by about 4.8 px over 10,000 rows. The registration
   tool already maps through the **measured** tick fit, so its
   `canvas_origin_uv` disagrees with the crop mapping by the same 1–2.4 px.
   Two derivations of one quantity, disagreeing: defect class 1, before any
   code is written.
3. **Annotation ink extends beyond crop A.** On 5 of 8 views the registered
   annotation canvas is larger than the model image (Plan_CropActive: 297 × 127
   cells vs 257 × 87). This is content outside the model crop: dimensions,
   tags and grid heads.
4. **The geometry path's grid is sheet-capped.** 48" × 36" at 1/8" is 384 ×
   288 cells. Plan_CropInActive's native grid (1034 × 2412) exceeds it, so the
   geometry path widens its cells there, to about 8.4 ft (`resolve_view_bounds`,
   the "ADAPTIVE CELL SIZE" branch).

## Proposed decisions

**G-1. Cell size is the requested paper cell, never adaptive.**
`cell_ft = cell_size_paper_in × view_scale / 12`, with `cell_size_paper_in`
taken from the run's `run_meta.json` config and `view_scale` from the `frame`
record. If either is missing the tool **refuses**; it never guesses (the 64-cell
grid above is what guessing produced). The sheet cap is a geometry-path
limitation, not a property of the view. Comparison against a capped geometry
run is handled in G-8.

**G-2. The origin is crop A's lower-left corner.** Cell `(i, j)` covers
`u ∈ [xmin + i·cell, xmin + (i+1)·cell)` and `v ∈ [ymin + j·cell, ymin +
(j+1)·cell)`, where `(xmin, ymin)` is `frame.crop_uv`'s minimum. `i` runs along
+u and `j` along +v (up), the same convention as the geometry path's
`i = int((u − bounds_xy.xmin) / cell)`. In the current configuration
(`bounds_buffer_in = 0`, `anno_expand_cap_cells = 0`) crop A's minimum is the
geometry path's `raster.bounds_xy` minimum, so the two grids share their cell
phase. `test_view_raster_grid_origin.py` lists the three ways they can differ
under other configurations; those are recorded, not silently absorbed (G-8).

**G-3. The extent is the union of crop A and the registered annotation
canvas, snapped outward to whole cells.** Cells may have negative indices
before re-indexing, so the grid records `origin_cell = (i0, j0)`. Each cell
records whether it lies inside crop A. That keeps "annotation outside the model
crop" a measured fact rather than a clipped one. Where there is no registered
annotation capture, the extent is crop A alone, and the record says so.

**G-4. Pixel→UV comes from one source: the model capture's tick fit.**
`registration_marks.fit_recorded_marks` on the model TIFF gives the measured
per-axis scale and offset. The registered annotation TIFF is already on the
same lattice, so the same mapping serves both images. That is one derivation
for both captures, and the same one the registration tool uses.
- If the fit is unusable (fewer than the required ticks, refused), fall back to
  the nominal crop mapping. The record then says `uv_basis: "nominal_crop"`
  with the reason, and gives the expected error: the tick-vs-crop deviation
  class above, up to about 2.4 px.
- The grid record carries `uv_basis` and the fit residual, so every downstream
  number knows which mapping produced it.

**G-5. A pixel belongs to the cell containing its centre.** Each pixel lands
in exactly one cell, so counts are exact and no fractional weights exist.
Cells hold 18 or 19 px per axis at 150 dpi; every channel is reported as
counts plus the cell's own pixel total, so a threshold can be applied later as
a fraction (item 3).

**G-6. Refuse below a resolution floor.** A cell narrower than a small number
of pixels cannot be measured meaningfully. Proposed floor: **2 px per cell**
per axis. Below it the view's grid is refused with the figure, not produced.
Plan_CropInActive (4.15 px) passes. It is flagged `coarse` below 8 px per cell,
because a 2.4 px mapping uncertainty is then more than a quarter of a cell.

**G-7. Channels are counted separately; interpreting them is items 3 and 4.**
Per cell, as integer counts:
- `host_px` (by element id, from `color_assignment_map`);
- `link_px` (by category);
- `dwg_px` (host ids whose `source` is DWG);
- `anno_px` (by annotation element id, from the registered TIFF);
- `black_px`;
- `residual_px`;
- `white_px`;
- `tick_px` (subtracted, but counted so its loss is visible);
- `total_px`.

This mirrors the decoder's S1 `pixel_stats` partition, so the per-cell counts
must sum to the image-level counts. That gives a composition test for free.

**G-8. Comparing with the geometry path is a separate, explicit step.** The
native grid (G-1…G-3) is the product. To compare with a geometry run, a second
function maps native cells onto that run's grid, using its recorded origin,
cell size and W × H. It records the relation (`same_grid`, `aggregated k × k`,
or `incommensurate` with the reason). A sheet-capped view is therefore
compared by aggregation, never by re-gridding the capture.

**G-9. One module, one record, written last.** A new
`tools/stage_a_grid.py` (standard library and NumPy only) owns:
- `grid_for_view(model_sidecar, registered_record, run_config) -> GridSpec`;
- `pixel_to_cell(GridSpec, col, row)`;
- `cell_to_uv(GridSpec, i, j)`.

`colorid_to_occupancy.py` and the decoder's `grid_bounds_uv` both move onto
it. Its per-view output is one `<view>.grid.npz` (the counts) and one
`<view>.grid.json` (the GridSpec, `uv_basis`, source file hashes and
`run_id`). The JSON is written after the arrays and names their hash, the same
ordering rule as `register_stage_a_annotation` (defect class 4).

## Tests that make this trustworthy

- **Composition, not reimplementation:**
  - `pixel_to_cell ∘ cell_to_uv` round-trips against the decoder's
    `_pixel_corner_to_uv` under the nominal basis;
  - the tick-fit basis reproduces the registration tool's `canvas_origin_uv`
    exactly (today they disagree by construction);
  - the per-cell channel sums equal the image's S1 `pixel_stats`.
- **Discriminating fixture for G-2:** a view whose crop A minimum differs from
  a stand-in `bounds_xy` minimum, so the wrong anchor visibly shifts every cell.
- **Refusals, each with a control:** missing `cell_size_paper_in`, missing
  `view_scale`, below the resolution floor, an unusable tick fit (falls back
  and says so).
- **Golden on 1453:** the W × H figures in the table above, per view, and the
  per-channel totals.
- **Regression for today's defect:** the current `grid_assumed()` produces the
  64-cell grid. The new code must refuse, or produce 257 × 87, on the same
  sidecar.

## Open questions for Greg

1. **Grid extent.** Union with the annotation canvas (G-3), or crop A only
   with the outside ink counted per view? The union changes W × H on 5 of 8
   views.
2. **Pixel→UV basis.** Tick fit first (G-4), accepting that the model pass's
   own crop record becomes secondary? Or nominal crop first, with the fit as a
   check?
3. **Resolution floor.** 2 px per cell to refuse and 8 px per cell to flag?
   And should capped captures like Plan_CropInActive be re-exported uncapped
   for analysis, or accepted at 4.15 px per cell?
4. **Integer cells in pixels.** At 144 dpi a 1/8" cell is exactly 18 px (at
   160 dpi, 20 px), which removes the mid-pixel edges entirely. That is a
   capture-config change, so outside this item, but cheap if wanted.
5. **Geometry-run comparison.** Is a geometry-path run of these 8 views
   available, or should one be made, for G-8 and item 3's threshold?

## Out of scope here

- Thresholds and what "occupied" means: item 3.
- Attributing black and residual pixels: item 4.
- Link-model element identity: item 6, needs a capture change.
- Change detection: item 7.
