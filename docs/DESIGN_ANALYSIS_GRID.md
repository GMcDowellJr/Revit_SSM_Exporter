# Design note: the analysis grid (analysis layer, item 2)

Status: **decided and implemented** in `tools/stage_a_grid.py`, 2026-09-30.
The decisions below were reviewed by Greg the same day. Evidence: runs
`pipeline_0930_1249` (74320d7) and `pipeline_0930_1453` (99c7c44, main after
PR #222). Their 16 captures are byte-identical.

## Why this comes first

The Stage A capture no longer knows about cells. Occupancy (empty /
model-only / annotation-only / overlap), comparison against the geometry path,
and the per-cell metrics all need one answer to: *which cell does each pixel of
each capture fall in?* Everything else in the analysis layer consumes that
answer, so it is defined once, in one function, and tested by composition
(CLAUDE.md, defect class 1).

## What the existing tool got wrong

`tools/colorid_to_occupancy.py` was the only grid builder. Run on 1453:

| Symptom | Cause |
|---|---|
| **Every model view got a 64-cell-wide grid.** Plan_CropActive: 64 × 26 cells of **4.40 ft**, where 1/8" at 1:96 is **1.00 ft**. Plan_CropInActive: 6.0 ft cells. It was labelled `basis: "assumed"`, not refused. | Without a geometry run, `grid_assumed()` read `backoff_floor_px` as "raster W". Since the registered capture, that field is the capture's own **64 px minimum** (`color_id_buffer._export_tiff`: "THE FLOOR IS NO LONGER THE CELL GRID"). **Now refused** for a frame-record sidecar, pointing at `stage_a_grid.py`. |
| **All 8 annotation sidecars failed** ("no usable bounds_xy"). | The annotation pass records no crop; its pixels only mean something after `register_stage_a_annotation`. |
| **Link-model pixels counted as empty.** | `link_category_color_map` was not read (about 210k px on Plan_RVTLink and RCP). |

Moving `colorid_to_occupancy.py` fully onto the new grid is left for later.
Until then it refuses rather than mis-grids.

## Decisions

**G-1. Cell size is the requested paper cell.**
`cell_ft = cell_size_paper_in × view_scale / 12`, with `cell_size_paper_in`
taken from the run's `run_meta.json` config and `view_scale` from the `frame`
record. If either is missing, the grid is **refused**, never guessed.

Pixels per cell follow from that. Uncapped, a cell is 1/8" of paper and the
export is 150 dpi of paper, so every uncapped view is 18.75 px per cell
whatever its scale (1453: 1:8, 1:48 and 1:96 alike). Only a Revit-capped
export differs (Plan_CropInActive, 4.15 px). The grid never assumes a whole
number: cell edges fall mid-pixel (G-5).

**G-2. The origin is crop A's lower-left corner, and indices are signed.**
Cell `(i, j)` covers `u ∈ [u0 + i·cell, u0 + (i+1)·cell)`, `v ∈ [v0 + j·cell,
v0 + (j+1)·cell)`, where `(u0, v0)` is `frame.crop_uv`'s minimum. `i` runs
along +u and `j` up +v, the geometry path's convention. Annotation ink left of
or below crop A takes **negative** `i`, `j`. That is expected and kept; the
record gives `i_range` and `j_range`. In the current configuration
(`bounds_buffer_in = 0`, `anno_expand_cap_cells = 0`), crop A's minimum is the
geometry path's `raster.bounds_xy` minimum, so the two grids share their cell
phase.

**G-3. The extent is the union of the model image and the registered
annotation canvas.** The canvas is usually larger, but not always (it equals
the model image on Plan_DWG, Plan_RVTLink and Plan_CropInActive). The record
keeps crop A's own cell range (`crop_a_cells`), and the arrays carry an
`inside_crop_a` mask, so ink outside the model crop is a measured fact. With
no registered annotation capture, the extent is the model image alone and the
view is flagged `no_registered_annotation`.

**G-4. Pixel→UV: whichever of the two mappings lands closer to the measured
ticks.** The model capture carries its crop, so a nominal mapping exists (the
decoder's, with its aspect-clamp pad). The ticks give a second, measured
mapping (the registration tool's model fit). Both are evaluated at the tick
positions, the closer one is used, and both residuals are recorded, together
with their largest disagreement anywhere in the image. The registered
annotation canvas is on the model lattice, so the same mapping serves both
images. With no usable tick fit, the crop is used and the uncertainty is
recorded as **unmeasured** (flag), never as zero.

**G-5. A pixel belongs to the cell containing its centre.** Each pixel lands
in exactly one cell, so counts are exact and no fractional weights exist.
Every channel is reported as a count alongside the cell's own pixel total, so
a threshold can be applied later as a fraction (item 3).

**G-6. Resolution is judged per view, from cell size against pixel count.**
- **Refused** below `MIN_PX_PER_CELL = 2` pixels per cell.
- **Flagged `coarse`** when the mapping uncertainty exceeds
  `COARSE_UNCERTAINTY_CELLS = 0.25` of a cell. That is the "8 px" starting
  figure (2 px of uncertainty in an 8 px cell), expressed as what it
  measures.
- **Capped exports are analysed and flagged `capped`.** The cap is Revit's.
  Such views are never on sheets and will play a different role in the final
  analysis.

Both thresholds are starting values, to be revisited with experience.

**G-7. Channels are counted separately; interpreting them is items 3 and 4.**
Per cell, as integer counts:
- model image: `host`, `dwg` (host ids whose `source` is DWG), `link` (any
  linked-category colour), `tick`, `white`, `black`, `residual`, `total`;
- registered annotation canvas: `element`, `white`, `black`, `residual`,
  `total`.

The model channels use the decoder's S1 precedence, so their image totals
equal its `pixel_stats` exactly. Per-element counts per cell are not produced
yet.

**G-8. Checking the result.** No geometry-path run of these views exists.
Each view gets a `<view>.grid.png` in vop_raster's colours, for checking by
eye as the geometry version was checked:
- green: model only;
- cornflower blue: annotation only;
- orange: overlap;
- grey outline: crop A.

The picture's rule, any pixel, is **provisional**; item 3 decides what
"occupied" means. Comparison with a geometry run (aggregating onto its grid,
including sheet-capped views) is deferred until one exists.

**G-9. One module, one record, written last.** `tools/stage_a_grid.py`
(NumPy and Pillow; the decoder's and registration tool's own functions, not
copies of them) writes per view:
- `<view>.grid.npz`: arrays `(cells_h, cells_w)`, with row 0 = the lowest `j`;
- `<view>.grid.png`;
- `<view>.grid.json`, written **last**, naming the npz's hash, the source
  files' hashes, `run_id`, the GridSpec and `uv_basis` (defect class 4).

A refusal is a record too.

```
python tools/stage_a_grid.py <run dir | color_id_buffer dir> [--out DIR]
```

It reads `<view>_anno.registered.json`, so run `register_stage_a_annotation`
first. The default output is `<run>/analysis_grid/`.

## Results on pipeline_0930_1453

| View | Cells (W × H) | Signed range i / j | px/cell | Basis | Crop at ticks (px) | Crop at corners (px) | Fit (px) | Uncertainty (cells) | Flags |
|---|---|---|---|---|---|---|---|---|---|
| Elevation_CropActive | 294 × 98 | -9…284 / -15…82 | 18.74 | tick fit | 0.40 | 0.40 | 0.26 | 0.014 | — |
| ModelCallout_CropActive | 58 × 25 | -13…44 / -5…19 | 18.70 | tick fit | 0.66 | 0.69 | 0.40 | 0.021 | — |
| Plan_CropActive | 297 × 127 | -19…277 / -18…108 | 18.75 | tick fit | 1.14 | 1.29 | 0.26 | 0.014 | — |
| Plan_CropInActive | 1035 × 2413 | -1…1033 / -1…2411 | 4.15 | tick fit | 0.41 | 2.43 | 0.26 | 0.063 | capped |
| Plan_DWG | 259 × 87 | -1…257 / 0…86 | 18.74 | tick fit | 0.56 | 0.57 | 0.13 | 0.007 | — |
| Plan_RVTLink | 258 × 87 | -1…256 / 0…86 | 18.75 | tick fit | 1.21 | 1.23 | 0.26 | 0.014 | — |
| RCP_CropActive | 299 × 127 | -8…290 / -13…113 | 18.74 | tick fit | 0.36 | 0.39 | 0.26 | 0.014 | — |
| Section_CropActive | 58 × 183 | 0…57 / -8…174 | 18.75 | tick fit | 0.40 | 0.40 | 0.39 | 0.021 | — |

- **The tick fit is closer on all 8 views.** The recorded crop misses the
  ticks by 0.36–1.21 px, and by up to 2.43 px at the image corners on the
  capped view; the fit by 0.13–0.40 px. On an 18.75 px cell that is at most
  2 % of a cell; on the capped view 6 %. No view is `coarse`.
- **The corner figures reproduce the registration tool's own
  `model_marks_vs_lattice`** (2.43 px on Plan_CropInActive, 1.29 on
  Plan_CropActive). The two tools agree from independent code.
- **Sliver cells at index −1 where the canvas equals the model image.** Under
  the measured fit, the image's first pixel column lies a fraction of a pixel
  outside the recorded crop, so a sliver cell appears at i = −1 (Plan_DWG,
  Plan_RVTLink, Plan_CropInActive). It is kept, not clipped: it is what the
  measurement says, and its `total_px` shows how thin it is.
- **The model channel totals equal the decoder's S1 `pixel_stats` on all 8
  views.**

## Tests (`tests/test_stage_a_grid.py`)

- **Composition:**
  - the nominal mapping is the decoder's `_pixel_corner_to_uv`;
  - the channel totals are the decoder's `pixel_stats`;
  - the fit mapping reproduces the registration tool's `canvas_origin_uv`, and
    the corner disagreement its `model_marks_vs_lattice`.
- **Cells:**
  - cells from crop A's lower-left, at the paper cell, where the fixture's
    constants put them;
  - negative indices for annotation ink below and left of crop A;
  - linked-category pixels in the `link` channel, in their cells.
- **Basis:**
  - the closer mapping is chosen and both are recorded;
  - a recorded crop moved 2 px loses to the fit;
  - no ticks gives the crop, with the uncertainty unmeasured.
- **Refusals:**
  - no `cell_size_paper_in`;
  - under 2 px per cell;
  - `coarse` follows cell size against pixel count (a fixed 1 px uncertainty
    is fine at 18 px per cell and coarse at 2.25).
- **The record:** written last, it names the arrays' hash and shapes.

Three mutations of production each turn the suite red:
- origin moved off crop A;
- annotation offset sign flipped;
- link channel dropped.

## Out of scope here

- Thresholds and what "occupied" means: item 3.
- Attributing black and residual pixels: item 4.
- Link-model element identity: item 6, needs a capture change.
- Change detection: item 7.
- Moving `colorid_to_occupancy.py` onto this grid, and comparing with a
  geometry run once one exists.
