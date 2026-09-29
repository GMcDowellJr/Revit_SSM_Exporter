# Run `pipeline_0928_0953` — first production run of the registered capture

Greg ran `thinrunner_streaming` with `Config.color_id_buffer_registered_capture`
on over nine views (the eight in the handoff plus `Section_CropActiveHidden`
19297372). This is step 2 of `HANDOFF_REGISTERED_CAPTURE.md`, and the first
time `tools/register_stage_a_annotation.py` saw real captures.

## 1. The capture itself

Every view came back with an empty `faults` list, `rolled_back: true`, every
view property read back `restored`, `marks_still_in_project: []`, and no element
override left behind. Rollbacks took 0.09–1.6 s.

`restore.status` is recorded as `"2"`: `str(TransactionStatus.RolledBack)` under
Python.NET gives the enum's number, not its name. The verdict field
(`rolled_back`) is computed by comparison and is correct. Cosmetic; not fixed here.

## 2. Three defects in the offline registration, found by this run

| view | first result | cause | fix |
|---|---|---|---|
| Elevation | REFUSED, annotation 4/12 ticks | every horizontal tick landed on a pixel **boundary** and exported as two anti-aliased rows (coverage 0.62 / 0.75), with no pixel of its exact colour | ticks are read exactly **and** as blends toward white, weighted by coverage; the capture's own palette colours are never taken for a blend |
| Plan_CropInActive | REFUSED, model 8/12, 4 merged | the marks sit on the raster frame (y 4408–5592 of 10000) inside a far larger crop A; the model locator split the IMAGE into thirds, so all six horizontals landed in "mid" | halves and thirds are taken over the marks' own pixel extent |
| Plan_CropActive | registered, but 26 % of in-grid annotation ink dropped | its model crop is narrower than its grid (`model_crop_offset_uv` 11 / 11 / −13 / −16 ft), and the output was the model image only | the output canvas is the model lattice extended to the grid, on the model's pixel phase; `model_image_origin_px` says where the model image sits in it |

**The first blend fix made four views worse**, and that is worth knowing before
touching it again. With a ~300-colour annotation palette, a faint fringe of one
hue lies within 3 levels of a neighbour's line to white: each tick colour
matched 40–370 pixels nowhere near its tick, and Plan_CropActive's residual went
from 0.24 px to 522 px. Colour alone cannot identify a fringe. A blend now
counts only inside a connected piece that is line-shaped (≥ 3:1, along the tick's
orientation), has a pixel ≥ 50 % covered, holds ≥ 25 % of the strongest piece's
coverage, and — for the annotation capture — lies on the strongest piece's
centre line. Each rule was added because a real view defeated the ones before
it (RCP: an 11 × 23 px blob; Plan_CropInActive: a 10 × 3 px sliver next to
21 px ticks).

**And a fit is now refused above 2 px residual.** Before that the tool
registered Plan_CropInActive through a 584 px residual. Every correct fit on this
run is at or under 0.39 px.

## 3. Result after the fixes — all nine register

| view | anno ticks | model ticks | worst residual a / m (px) | scale x / y | ink off canvas | canvas origin |
|---|---|---|---|---|---|---|
| Elevation 19293413 | 12/12 | 12/12 | 0.04 / 0.26 | 1.063124 / 1.062967 | 71 % (3 view labels) | 0, 0 |
| ModelCallout 19293458 | 12/12 | 12/12 | 0.25 / 0.26 | 1.587381 / 1.591730 | 32 % | 0, 0 |
| Plan_CropActive 19290402 | 12/12 | 12/12 | 0.24 / 0.26 | 1.151161 / 1.152003 | 9.8 % | 206, 300 |
| Plan_CropInActive 19291097 | 12/12 | 12/12 | 0.30 / 0.30 | 1.000000 / 1.000000 | 0 % | 0, 0 |
| Plan_DWG 19294180 | 12/12 | 12/12 | 0.26 / 0.13 | 0.999586 / 0.999417 | 0 % | 0, 0 |
| Plan_RVTLink 19293485 | 12/12 | 12/12 | 0.26 / 0.26 | 1.000000 / 1.000000 | 0 % | 0, 1 |
| RCP 19293283 | 12/12 | 12/12 | 0.37 / 0.26 | 1.079582 / 1.079914 | 30 % | 0, 0 |
| Section 19293421 | 12/12 | 12/12 | 0.23 / 0.39 | 1.213359 / 1.213955 | 14 % | 0, 0 |
| Section_CropActiveHidden 19297372 | 12/12 | 12/12 | 0.23 / 0.39 | 1.213359 / 1.213955 | 14 % | 0, 0 |

Two independent checks agree with earlier measurement: the elevation's scale is
the probe's 1.063156 (`probe_0928_0933`) to 3e-5, and Plan_CropInActive — crop
A, per round 3b — is the identity. Overlays of the model's edges under the
registered annotation (plan, RCP, sections) put tags on their walls and grid
lines through their columns. No colour was lost to the resample on any view.

"Ink off canvas" is annotation beyond the pipeline's grid. On every view but
Plan_CropActive the grid equals the model crop and that ink is beyond both; the
shipped frame-B pass does not capture it either. Whether the grid should grow to
hold it is a step-4 question, not a registration one.

The decoder now reports `registration_marks: subtracted`, 12/12, on all 18
sidecars, and excludes the 12 tick ids from every annotation decode.

## 4. Things this run shows that are not the registration's

- **Elevation annotation is nearly empty** (one non-mark element, which did not
  draw; three black view-reference labels). Round 3 already recorded that this
  elevation has no levels or grids.
- **Plan_DWG's annotation capture is mostly the DWG.** A view-specific import is
  view-owned, so membership (`OwnerViewId`) makes it annotation: 346 k px of its
  palette colour, plus ~40 off-palette greens where its layers were not
  flattened. 71 of its 88 annotation records have membership basis `unknown`
  and no bbox, and none of them drew (Plan_RVTLink: the same shape).
- **Section_CropActiveHidden's model capture holds only the marks**: two model
  elements in its palette, neither drew. Its annotation TIFF is byte-identical to
  Section_CropActive's. Why the model pass collected almost nothing for this
  view is open.
- Model-capture off-palette counts are high on Plan_RVTLink (210 k) and RCP
  (214 k) — linked content, coloured by filters outside `color_assignment_map`.
