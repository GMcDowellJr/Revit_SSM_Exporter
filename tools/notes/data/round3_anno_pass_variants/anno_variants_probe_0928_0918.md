# Annotation-pass variant report

Values only. No pass/fail, no score, no recommendation -- the probe brief puts the judgement with Greg, and a tool that rated the variants would replace that read.

## View __Elevation_CropActive (id 19293413, 3, scale 1:96)

Probe `stage_a_anno_pass_variants` version 2026-09-29.1; combined report `C:\Users\gmcdowell\Documents\VOP\Exports\FVoT\probe_0928_0918\anno_pass_variants_probe\__Elevation_CropActive_19293413.anno_pass_variants.json`.

Model pass: success=True, failure_reason=None, achieved dpi=149.99, frame_px=[5164, 1267].
Model re-export after the variants: **unchanged** -- the control held and the post-variant hash matches

**The crop.** Authored crop active: yes; UV [-104.01772534812247, -4.810097715203943, 171.41561007680002, 62.7311886414472].
- V0 widens it to frame B by (ft): left +0.00 / right -0.00 / top +0.04 / bottom +0.00
- the MODEL pass sets it to crop A, which differs from the authored crop by (ft): left +0.00 / right -0.00 / top +0.04 / bottom +0.00
- crop shape: 1 loop(s), 4 edge(s), rectangle: yes, oblique edges: 0
- **white-suppression cost**: 28873 ms for 6535 element override(s), against 25383 ms for the whole model pass (ratio 1.14; a LOWER bound on suppression-vs-paint, since production does not time its paint alone)
  - by mechanism (ms): category_override_writes 28700, element_overrides 88, category_override_reads 15, category_collect 13, build_override 2, subcategory_walk 2, link_category_filters 0
  - API calls: category_override_reads 395, category_override_writes 391, element_override_writes 6535, subcategory_lists_read 40
  - `v9_registration_marks` suppression: 28873 ms (category_override_writes 28700, element_overrides 88, category_override_reads 15, category_collect 13, build_override 2, subcategory_walk 2)
  - `v9_registration_marks`: pre-state commit 1852 ms; restore = group rollback, 1236 ms; post-rollback element-override read-back 3063 ms (restored)
- F2 fiducials: none chosen -- v8_no_crop_fiducials was not requested for this run

### 0. What the capture reports it actually did

| variant | suppression mode | applied_smooth_edges | display style | probe conclusion | measured its candidate | production success | capture faults | faults read from |
|---|---|---|---|---|---|---|---|---|
| v9_registration_marks | external | no | FlatColors | DID_NOT_MEASURE | NO | yes | none | combined_record |

`capture faults` come from the probe's combined record (`annotation_pass.capture_faults`) in preference to the annotation sidecar, and the last column says which was used. The sidecar is the fallback because production wrote it before computing its own faults until `dcb4e65`, so an older capture's file carries none however faulted it was. `UNKNOWN (not recorded)` means neither source had the field -- which is not the same fact as `none`.

`applied_smooth_edges` is four-valued: `not_attempted` (the pass was not asked), `read_failed`, `unchanged (failed)`, or `False` (**confirmed off**). A V2/V3 row that is anything but `False` did NOT have anti-aliasing disabled and therefore measures the same behaviour as the variant above it -- the probe reports that as `DID_NOT_MEASURE` rather than `RAN`.

- `v9_registration_marks` DID NOT MEASURE: requested 2 fiducials painted (F2), production reported `0`. without both fiducials F2 has no pair to fit, and on a crop-less view nothing else registers the capture

### 1. Image size vs `frame_px`, both axes

| variant | image | frame_px | dw | dh | equal | sidecar dim_check |
|---|---|---|---|---|---|---|
| v9_registration_marks | 5164x1708 | 5164x1267 | 0 | 441 | no | pass |

`dw`/`dh` are image minus `frame_px`. `dim_check` is the sidecar's own verdict, which inspects the requested axis only (finding F4) -- so a nonzero `dh` beside `dim_check=pass` is exactly the case F4 names.

### 2. Registration fit (measured from the pixels)

| variant | samples | px/ft u | px/ft v | frame px/ft | offset at (u0,v1) px | residual med/max px | v axis |
|---|---|---|---|---|---|---|---|
| v9_registration_marks | 10 pairs | 17.635 | 17.631 | 18.749 | (142.3, 258.3) | 0.12 / 0.59 | negative (expected) |

- `v9_registration_marks` frame: the AUTHORED crop (crop left as found)

#### 2b. Fitted per-side margin (how much bigger the render is than the frame)

| variant | left | right | top | bottom |  |
|---|---|---|---|---|---|
| v9_registration_marks | 8.07 ft / 1.009 in | 9.32 ft / 1.165 in | 14.65 ft / 1.832 in | 14.68 ft / 1.835 in |  |

#### 2c. The fit's own premise: does the ink sit where the bbox says?

The fit matches an element's exact-colour centroid against the centre of its recorded `bbox_uv`. **Those coincide only when the ink fills the box**, which real text, a tag with a leader or a dimension need not do. A displacement that is the SAME for every element is absorbed into the fitted intercept, so it leaves the residuals in 2 clean while shifting every margin in 2b by exactly that amount; one that varies with size or position corrupts the scale instead and does inflate the residuals. Both are surfaced here. No tolerance is applied -- what is close enough for a margin figure is your call.

| variant | ink-bbox px/ft u | ink-bbox px/ft v | px/ft u delta | px/ft v delta | margin delta ft (l/r/t/b) |  |
|---|---|---|---|---|---|---|
| v9_registration_marks | 17.635 | 17.631 | 0.0000 | 0.0000 | 0.000 / 0.000 / 0.000 / 0.000 |  |

The second anchor is the centre of the ink's own bounding box rather than its centroid. The two are identical when the ink fills its bbox and diverge when it does not, so a row of ~0 deltas says the anchor choice does not matter on this capture and the margins above do not rest on the premise.

A UNIFORM displacement -- every element's ink sitting the same distance off its box centre -- is NOT recoverable from a capture: nothing distinguishes it from the whole render sitting that far over, which is why it is the dangerous case. What IS recoverable is whether such a displacement is POSSIBLE, and by how much: ink that spans its whole box cannot be off-centre in it. The table below bounds it.

- `v9_registration_marks` samples: matched 10, no bbox 1, colour absent 2, clipped at an image edge 0.

### 3. How far recorded annotation bboxes reach past the rendered frame

| variant | left | right | top | bottom | bboxes outside |  |
|---|---|---|---|---|---|---|
| v9_registration_marks | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0 of 12 |  |

Read this table against 2b. Where they agree, the render is behaving as if it had been fitted to the crop unioned with the annotations drawn beyond it.

### 4. Coverage per category

#### `v9_registration_marks`

Ink test threshold >2% non-white, placed through the fitted mapping from measurement (2).

| category | assigned | painted | colour present | bbox | ink tested | ink present | bbox off image | ink untested |
|---|---|---|---|---|---|---|---|---|
| <no category> | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| Lines | 12 | 12 | 10 | 12 | 12 | 10 | 0 | 0 |

### 5. Non-white, non-palette pixels

| variant | total | white | palette | off-palette | off-palette frac | blend | grey | other | distinct | tally capped |
|---|---|---|---|---|---|---|---|---|---|---|
| v9_registration_marks | 8820112 | 8817871 | 901 | 1340 | 0.00015 | 0 | 1340 | 0 | 23 | no |

`blend` is within 3 RGB units of the segment between some palette colour and white. `grey` is all three channels within 3 of each other. The two OVERLAP -- a grey pixel is also collinear with white -- and blend is tested first; the `blend also grey` count below states what that ordering costs.

- `v9_registration_marks` blend also grey: 0 px.

#### `v9_registration_marks` top 10 off-palette colours

| rgb | pixels | class | also grey |
|---|---|---|---|
| [0, 0, 0] | 358 | grey | yes |
| [71, 71, 71] | 180 | grey | yes |
| [195, 195, 195] | 126 | grey | yes |
| [137, 137, 137] | 108 | grey | yes |
| [17, 17, 17] | 78 | grey | yes |
| [221, 221, 221] | 67 | grey | yes |
| [236, 236, 236] | 60 | grey | yes |
| [208, 208, 208] | 48 | grey | yes |
| [105, 105, 105] | 39 | grey | yes |
| [35, 35, 35] | 38 | grey | yes |

### 6. Model TIFF hash vs V0

| variant | model sha256 (16) | V0 sha256 (16) | equal |
|---|---|---|---|
| v9_registration_marks | 330bcaf4ec45e592 | 330bcaf4ec45e592 | yes |

The model pass runs ONCE per view, so this column detects a variant CLOBBERING the model artifact -- a path collision, a stray write. Whether a variant changed how the model pass RENDERS is the probe's own `model_reexport_after_variants` verdict, quoted at the top of this view's section, which has its own repeatability control.

### 7. F1 -- the crop boundary, recovered from the pixels

Q1: does the exported image contain the crop boundary, and does its recovered position match `view.CropBox`? A boundary is a band of rows (or columns) each holding a straight run of non-white, non-palette, non-fiducial pixels of >= 25% of the image, closing into one rectangle (or matching the crop shape's levels). `NOT FOUND` on a V8 row is the answer that ImageExportOptions does not draw it.

| variant | probe turned it on | boundary | row/col bands | px/ft u | px/ft v | lattice px/ft | u/v isotropy | boundary px rects (subtract these) |  |
|---|---|---|---|---|---|---|---|---|---|
| v9_registration_marks | yes | NOT_FOUND | 0 / 0 | -- | -- | 18.7486 | -- | -- | no row or column holds a straight non-palette run of >= 25% of the image: the crop boundary is NOT in this capture |

`isotropy` of 1 means the boundary's pixel rectangle has the crop's own aspect -- the recovered position is the crop's shape at one uniform scale. `lattice px/ft` is the model capture's (1 / achieved fpp): an untouched capture that rendered exactly the authored crop at the requested count would match it.

- `v9_registration_marks` MODEL capture (boundary drawn at crop A [-104.01772534812247, -4.810097715203943, 171.4156100768, 62.76814318010526]): NOT_FOUND -- no row or column holds a straight non-palette run of >= 25% of the image: the crop boundary is NOT in this capture

### 8. F2 -- the fiducial pair

| variant | element | colour | found | pixels | ink px bbox | clipped | recorded rect_uv |
|---|---|---|---|---|---|---|---|
| -- | -- | -- | -- | -- | -- | -- | -- |

| fit | px/ft u | px/ft v | u/v isotropy | residual max px u / v |  |
|---|---|---|---|---|---|
| -- | -- | -- | -- | -- | -- |


F2 fits each fiducial's INK EDGES against its recorded extent: four points per axis for the pair, so it has a residual. The recorded extent is a projected 3-D bbox, which can be looser than the element; the model-anchored row replaces it with the element's drawn extent in the model capture, at the cost of assuming the model capture registers.

### 9. F1 vs F2 vs F3 vs the bbox fit

Q2: with the crop untouched, is the rendered rectangle stable, and do F1 and F2 agree? The disagreement is the number. Corners are the authored crop's, pushed through both maps.

| variant | pair | px/ft u delta | px/ft v delta | worst corner px |  |
|---|---|---|---|---|---|
| v9_registration_marks | F1_vs_F2 | -- | -- | -- | one of the two mappings is not available |
| v9_registration_marks | F1_vs_bbox_fit | -- | -- | -- | one of the two mappings is not available |
| v9_registration_marks | F2_vs_bbox_fit | -- | -- | -- | one of the two mappings is not available |
| v9_registration_marks | F3_vs_F2 | -- | -- | -- | one of the two mappings is not available |
| v9_registration_marks | F3_vs_bbox_fit | -0.0006 | -0.0103 | 0.60 |  |

### 10. Datum extents: ink vs the recorded (authored-crop) bbox

Q3: do datum extents in the capture match the drawing? Positive means the ink reaches FURTHER than the bbox recorded before the capture touched the view. Under V0 the crop was widened to B, which lengthens datums; under V7/V8 it was not.

| variant | datum | category | map | length delta ft | paper in | per side ft l/r/t/b |  |
|---|---|---|---|---|---|---|---|
| -- | -- | -- | -- | -- | -- | -- | -- |

Premise: `get_BoundingBox(view)` of a datum is its drawn extent in the view (UNCONFIRMED).

### 11. Model-ink residue

Q4: does membership suppression with subcategories leave any model ink? Off-palette pixels outside the fiducials and outside any recovered boundary band. Counted, not attributed: an annotation the pass could not paint lands here too.

| variant | suppression | category layer | off-palette | fiducial px | boundary bands excluded | off-palette outside boundary |
|---|---|---|---|---|---|---|
| v9_registration_marks | external | yes | 1340 | 0 | 0 | 1340 |

`category layer` is mechanism 3 (category and subcategory white overrides). V10 runs without it: the V7 vs V10 difference in this table is what that layer removes, and the cost section above is what it costs.

### 12. F3 -- registration marks, in BOTH captures

Detail-line ticks the probe drew at KNOWN view UV, inset inside the crop, then removed by rolling back. Horizontal ticks' centre rows give v, vertical ticks' centre columns give u. Twelve ticks give six points per axis at three levels, so the residual measures disagreement between levels (a round-3 capture had eight ticks at two levels, whose residual is 0 by construction). The model row is checked against the model capture's RECORDED lattice -- the one place this method meets a known answer. The endpoint fit uses tick ends (caps, anti-aliasing) and is shown beside the centre-line fit, never in its place.

| variant | capture | ticks | px/ft u | px/ft v | u/v isotropy | residual max px u / v | endpoint fit px/ft u / v | vs model lattice worst px |  |
|---|---|---|---|---|---|---|---|---|---|
| v9_registration_marks | annotation | 10/12 | 17.6346 | 17.6205 | 1.00080 | 0.00 / 0.00 | 17.6358 / 17.6388 | -- |  |
| v9_registration_marks | model | 10/12 | 18.7486 | 18.7439 | 1.00025 | 0.00 / 0.00 | 18.7486 / 18.7435 | 0.51 |  |

- `v9_registration_marks` model lattice: 18.7486 px/ft
- `v9_registration_marks` annotation -> model pixels via_model_lattice: x' = 1.063175 x -151.96, y' = 1.064024 y -274.83
- `v9_registration_marks` annotation -> model pixels via_model_marks: x' = 1.063175 x -152.46, y' = 1.063755 y -274.95
- `v9_registration_marks` annotation: tick left_mid_h not found -- its colour [0, 156, 252] drew no pixels
- `v9_registration_marks` annotation: tick right_mid_h not found -- its colour [228, 252, 0] drew no pixels
  - where left_mid_h should be (px [170, 850, 266, 856]): 679 of 679 px white; other colours: none
  - where right_mid_h should be (px [4876, 850, 4972, 856]): 679 of 679 px white; other colours: none
- `v9_registration_marks` annotation: 901 mark pixels in 10 rect(s) to subtract in post
- `v9_registration_marks` model: tick mid_bottom_v not found -- no component of the shared colour in that corner and orientation
- `v9_registration_marks` model: tick mid_top_v not found -- no component of the shared colour in that corner and orientation
  - where mid_bottom_v should be (px [2578, 1135, 2584, 1237]): 42 of 721 px white; other colours: [0, 234, 186] x238 (element 5686560), [78, 246, 6] x154 (element 12008414), [132, 210, 6] x133 (element 18861828), [6, 126, 216] x126 (element 14208019), [6, 60, 216] x28 (element 14203081)
  - where mid_top_v should be (px [2578, 29, 2584, 131]): 721 of 721 px white; other colours: none
- `v9_registration_marks` model: 960 mark pixels in 10 rect(s) to subtract in post

#### `v9_registration_marks` -- why a tick did or did not draw (Revit's view of each mark, just before each export)

| tick | id | drew (anno) | drew (model) | in view collector | IsHidden | line style | length ft | filters passing | suspects before model | suspects before annotation |
|---|---|---|---|---|---|---|---|---|---|---|
| left_bottom_h | 19298392 | yes | yes | yes | no | <Thin Lines> | 5.120 | -- | none | none |
| left_bottom_v | 19298393 | yes | yes | yes | no | <Thin Lines> | 5.120 | -- | none | none |
| right_bottom_h | 19298394 | yes | yes | yes | no | <Thin Lines> | 5.120 | -- | none | none |
| right_bottom_v | 19298395 | yes | yes | yes | no | <Thin Lines> | 5.120 | -- | none | none |
| left_top_h | 19298396 | yes | yes | yes | no | <Thin Lines> | 5.120 | -- | none | none |
| left_top_v | 19298397 | yes | yes | yes | no | <Thin Lines> | 5.120 | -- | none | none |
| right_top_h | 19298398 | yes | yes | yes | no | <Thin Lines> | 5.120 | -- | none | none |
| right_top_v | 19298399 | yes | yes | yes | no | <Thin Lines> | 5.120 | -- | none | none |
| left_mid_h | 19298400 | no | yes | yes | no | <Thin Lines> | 5.120 | -- | none | none |
| right_mid_h | 19298401 | no | yes | yes | no | <Thin Lines> | 5.120 | -- | none | none |
| mid_bottom_v | 19298402 | yes | no | yes | no | <Thin Lines> | 5.120 | -- | none | none |
| mid_top_v | 19298403 | yes | no | yes | no | <Thin Lines> | 5.120 | -- | none | none |

A tick that did NOT draw with `none` in both suspect columns is one Revit considers visible: the reason is not in the element's visibility state, and that is the finding. The model export runs AFTER the first audit and changes the view itself (it hides annotation categories and handles filters inside its own transaction).

### Overlays

The gate is measurement 1 only: image size == `frame_px`. IT IS A SIZE GATE AND NOTHING MORE. A capture can be exactly `frame_px` pixels and still have its CONTENT drawn at a different scale or origin -- which is finding F1 -- and `capture_overlay.py` maps UV through the SIDECAR's numbers, so on such a capture its boxes will not land on the ink. Measurement 2's fitted px/ft is echoed beside each line so that is visible here rather than only three sections up.

- `v9_registration_marks`: OVERLAY WITHHELD (fitted 17.635 / 17.631 px/ft against the frame's 18.749). measurement 1 shows image 5164x1708 against frame_px 5164x1267; the overlay is not run on a capture whose rendered region is not the frame its sidecar describes
