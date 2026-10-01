# Design note: the analysis grid (analysis layer, item 2)

Status: **decided and implemented** in `tools/stage_a_grid.py`: the grid
(item 2) on 2026-09-30, occupancy (item 3) on 2026-10-01.
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
mapping (the registration tool's model fit). Both are scored the same way,
against the tick centre lines the fit MEASURED in the image (not against
each other): the closer one is used. Both residuals are recorded, together
with the two mappings' largest disagreement anywhere in the image. (Review,
PR #223: an earlier version scored the crop by its disagreement with the fit,
which can understate its error by up to the fit's own residual.) The registered
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

**G-8. Checking the result.** No geometry-path run of these views existed
when this was written; one has since been made (see item 3 below).
Each view gets a `<view>.grid.png` in vop_raster's colours, for checking by
eye as the geometry version was checked:
- green: model only;
- cornflower blue: annotation only;
- orange: overlap;
- grey outline: crop A.

The picture draws the occupancy classes defined in item 3 (ink). The
geometry comparison below maps each geometry cell's centre into this grid. A
reusable comparison tool (aggregating onto a geometry grid, including
sheet-capped views) is not built yet.

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
| Elevation_CropActive | 294 × 98 | -9…284 / -15…82 | 18.74 | tick fit | 0.50 | 0.40 | 0.26 | 0.014 | — |
| ModelCallout_CropActive | 58 × 25 | -13…44 / -5…19 | 18.70 | tick fit | 0.50 | 0.69 | 0.40 | 0.021 | — |
| Plan_CropActive | 297 × 127 | -19…277 / -18…108 | 18.75 | tick fit | 1.03 | 1.29 | 0.26 | 0.014 | — |
| Plan_CropInActive | 1035 × 2413 | -1…1033 / -1…2411 | 4.15 | tick fit | 0.35 | 2.43 | 0.26 | 0.063 | capped |
| Plan_DWG | 259 × 87 | -1…257 / 0…86 | 18.74 | tick fit | 0.49 | 0.57 | 0.13 | 0.007 | — |
| Plan_RVTLink | 258 × 87 | -1…256 / 0…86 | 18.75 | tick fit | 1.10 | 1.23 | 0.26 | 0.014 | — |
| RCP_CropActive | 299 × 127 | -8…290 / -13…113 | 18.74 | tick fit | 0.49 | 0.39 | 0.26 | 0.014 | — |
| Section_CropActive | 58 × 183 | 0…57 / -8…174 | 18.75 | tick fit | 0.50 | 0.40 | 0.39 | 0.021 | — |

- **The tick fit is closer on all 8 views.** Measured against the ticks'
  observed centre lines, the recorded crop misses them by 0.35–1.10 px, and
  by up to 2.43 px at the image corners on the capped view; the fit by
  0.13–0.40 px. On an 18.75 px cell that is at most
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

## Item 3: what "occupied" means (decided 2026-10-01)

Greg's decisions, after comparing against a geometry-path run of the same
eight views (`geometry`, run 20260930T142940_bbc6c66):

- **Occupancy is ink, not filled area.** The point is ink as a proxy for work;
  filled area would count building mass. An element pixel is **ink** when a
  4-neighbour has a different colour: its own outline, or a line a pixel from
  white. The image border is not an edge, because the drawing continues past
  it. A cell is occupied by a layer with at least `OCCUPANCY_MIN_INK_PX = 1` of
  that layer's ink pixels. It is then one of empty / model only / annotation
  only / overlap.
- **The same rule applies to annotation, with one exception: filled regions
  count by AREA.** A filled region masks the model, and the mask is what is
  relevant. It is identified by the capture's new `element_class`
  (`"FilledRegion"`): filled and masking regions share the Detail Items
  category with detail components, so category cannot tell them apart. A run
  captured before `element_class` existed (1453 included) records that
  filled regions could not be identified and counts them by ink.
- **Annotation is every view-specific element**: dimensions, tags and text,
  and also datums (Grids, Levels), view markers (section, elevation and
  callout heads) and revision clouds. The geometry path left the latter out, a
  known gap in the previous tooling. Checked on 1453: every view-owned member
  is painted (members = painted colours on all 8 views). The only unpainted
  ones are the 71 on Plan_DWG and Plan_RVTLink in categories the view itself
  hides (M1).
- **Black pixels belong to the annotation that contains them.** View-symbol
  text and similar are drawn in black, not in the element's colour. Each black
  pixel on the annotation canvas is assigned to the smallest annotation
  bounding box containing its centre (padded `BLACK_BBOX_PAD_PX = 1`) and
  counts toward that layer. Pixels in no box stay unassigned and are
  reported.
- **Overlap is measured, not only binary.** The two captures share one
  lattice, so for every model ink pixel we know whether annotation is drawn
  over it. Per cell, `model_ink_under_anno` and
  `model_ink_under_filled_region` count those pixels; divided by the cell's
  model ink they give the masked fraction. The four-way class (empty / model /
  annotation / overlap) is kept alongside for parity.
- **Lines and edges are better than bounding boxes.** The geometry path's
  TINY/LINEAR proxies fill element bounding boxes. Where they mark cells the
  colour capture's ink does not, the difference is accepted as an improvement,
  not treated as a miss.

Outputs:
- `<view>.grid.npz` gains the ink channels (`model_host_ink`,
  `model_dwg_ink`, `model_link_ink`, `anno_element_ink`), `anno_filled_region`,
  `anno_black_assigned`, `model_ink_under_anno`,
  `model_ink_under_filled_region` and `occupancy` (uint8, codes in the
  record). Annotation occupies a cell with ink + filled-region area +
  assigned black ≥ 1 px.
- `<view>.grid.json` gains:
  - `occupancy`: cell counts by class over all cells, inside crop A and
    outside it, plus the rule;
  - `black`: total, assigned, ambiguous and unassigned, and pixels by element;
  - `filled_region`: whether the class was recorded, how many, and their
    pixels;
  - `model_ink_under_annotation`: the totals and the fraction.
- The PNG draws the classes.

On 1453:

| View | Black px: total / assigned / ambiguous / unassigned | Model ink px | Under annotation | Fraction |
|---|---|---|---|---|
| Elevation_CropActive | 408 / 0 / 0 / 408 | 776573 | 0 | 0.000 |
| ModelCallout_CropActive | 0 / 0 / 0 / 0 | 7186 | 114 | 0.016 |
| Plan_CropActive | 380 / 189 / 185 / 191 | 543545 | 8146 | 0.015 |
| Plan_CropInActive | 0 / 0 / 0 / 0 | 104326 | 3543 | 0.034 |
| Plan_DWG | 5795 / 5795 / 0 / 0 | 372897 | 2721 | 0.007 |
| Plan_RVTLink | 0 / 0 / 0 / 0 | 580744 | 4 | 0.000 |
| RCP_CropActive | 785 / 212 / 0 / 573 | 465911 | 3096 | 0.007 |
| Section_CropActive | 0 / 0 / 0 / 0 | 75886 | 16695 | 0.220 |

- **Plan_DWG's 5,795 black pixels are all its view-owned DWG's** (element
  19296946).
- **Unassigned black.** Elevation's 408 black pixels lie in no annotation
  box: its only annotation members are the ticks and one element with no
  bounding box. So that black comes from something drawn outside the view's
  annotation membership. RCP (573) and Plan_CropActive (191) also have some.
  These are reported, not guessed.
- **Section has 22 % of its model ink under annotation,** where its Detail
  Items sit over the section cut. With `element_class` recorded, the next run
  will say which of them are filled regions.

### Results on pipeline_0930_1453, and the geometry comparison

"Ours" counts all cells of the union grid. The geometry path's grid is crop A,
annotation-expanded on Plan_CropActive, and sheet-capped on Plan_CropInActive.
The agreement columns compare the two on the geometry grid's cells, with each
geometry cell mapped by its centre.

| View | Ours: empty / model / anno / overlap | Geometry: empty / model / anno / overlap | Model agreement | Anno agreement |
|---|---|---|---|---|
| Elevation_CropActive | 18946 / 9866 / 0 / 0 | 4234 / 14534 / 0 / 0 | 0.757 | 1.000 |
| ModelCallout_CropActive | 1063 / 128 / 212 / 47 | 224 / 442 / 30 / 24 | 0.615 | 0.701 |
| Plan_CropActive | 22114 / 5755 / 7914 / 1936 | 16708 / 7613 / 5244 / 2583 | 0.881 | 0.681 |
| Plan_CropInActive | 2475078 / 12559 / 7968 / 1850 | 110462 / 130 / 0 / 0 | (window only) | (window only) |
| Plan_DWG | 10030 / 5127 / 5341 / 2035 | 12099 / 10257 / 0 / 3 | 0.851 | 0.671 |
| Plan_RVTLink | 14040 / 8403 / 0 / 3 | 11534 / 10822 / 0 / 3 | 0.855 | 1.000 |
| RCP_CropActive | 26544 / 7019 / 3309 / 1101 | 8569 / 16704 / 13 / 658 | 0.944 | 0.865 |
| Section_CropActive | 7869 / 975 / 1228 / 542 | 3879 / 3311 / 67 / 567 | 0.741 | 0.832 |

What the disagreements are, checked view by view:

- **Model, cells only the geometry path marks.** About 90 % are its
  light-grey projection cells: bounding-box proxies. Unshifted, our grid covers
  97–99 % of the geometry model cells on Plan_CropActive and RCP (fill); the
  grids are registered.
- **Annotation, cells only ours marks.** These are Grids, Levels, view markers
  and Revision Clouds (pixels by category, RCP / Plan_CropActive / Section), as
  decided above. On Plan_DWG they are the view-owned DWG linework, which the
  geometry path no longer draws at all (G1).
- **Annotation the geometry path marks.** Our capture covers 89–100 % of it
  to within one cell (exact-cell 41–76 %): the geometry path spreads its
  annotation into neighbouring cells. On Plan_CropActive its grid is also
  offset 0.92 cell in u and 0.27 in v against ours (annotation-expanded
  origin).
- **Plan_CropInActive.** The geometry path covered only a 384 × 288-cell
  sheet window and found 130 model cells. The colour grid covers the whole
  view, with 24,502 model cells outside that window.

## Out of scope here

- Attributing the residual (anti-aliased text edge) pixels, and black pixels
  outside every annotation bbox.
- Link-model element identity: item 6, needs a capture change.
- Change detection: item 7.
- Moving `colorid_to_occupancy.py` onto this grid.
- A geometry-comparison tool: not to be built (Greg, 2026-10-01). The
  comparison above was a one-off.
