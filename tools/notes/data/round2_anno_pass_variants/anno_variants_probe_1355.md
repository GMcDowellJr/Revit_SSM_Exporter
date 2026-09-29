# Annotation-pass variant report

Values only. No pass/fail, no score, no recommendation -- the probe brief puts the judgement with Greg, and a tool that rated the variants would replace that read.

## View __Elevation_CropActive (id 19293413, 3, scale 1:96)

Probe `stage_a_anno_pass_variants` version 2026-09-22.2; combined report `C:\Users\gmcdowell\Documents\VOP\Exports\FVoT\probe_1355\anno_pass_variants_probe\__Elevation_CropActive_19293413.anno_pass_variants.json`.

Model pass: success=True, failure_reason=None, achieved dpi=149.99, frame_px=[5164, 1267].
Model re-export after the variants: **unchanged** -- the control held and the post-variant hash matches

**The crop.** Authored crop active: yes; UV [-104.01772534812247, -4.810097715203943, 171.41561007680002, 62.7311886414472].
- V0 widens it to frame B by (ft): left +0.00 / right -0.00 / top +0.04 / bottom +0.00
- the MODEL pass sets it to crop A, which differs from the authored crop by (ft): left +0.00 / right -0.00 / top +0.04 / bottom +0.00
- crop shape: 1 loop(s), 4 edge(s), rectangle: yes, oblique edges: 0
- **white-suppression cost**: 32323 ms for 6536 element override(s), against 28357 ms for the whole model pass (ratio 1.14; a LOWER bound on suppression-vs-paint, since production does not time its paint alone)
- F2 fiducials: 8852504 (Fascias), 14208030 (Walls); separated 176.8 ft on u and 60.3 ft on v (1278 of 6536 candidates kept, reference authored_crop)

### 0. What the capture reports it actually did

| variant | suppression mode | applied_smooth_edges | display style | probe conclusion | measured its candidate | production success | capture faults | faults read from |
|---|---|---|---|---|---|---|---|---|
| v0_control | hide_categories | not_attempted | FlatColors | RAN | yes | yes | none | combined_record |
| v7_no_crop | external | not_attempted | FlatColors | RAN | yes | yes | none | combined_record |
| v8_no_crop_fiducials | external | no | FlatColors | RAN | yes | yes | none | combined_record |

`capture faults` come from the probe's combined record (`annotation_pass.capture_faults`) in preference to the annotation sidecar, and the last column says which was used. The sidecar is the fallback because production wrote it before computing its own faults until `dcb4e65`, so an older capture's file carries none however faulted it was. `UNKNOWN (not recorded)` means neither source had the field -- which is not the same fact as `none`.

`applied_smooth_edges` is four-valued: `not_attempted` (the pass was not asked), `read_failed`, `unchanged (failed)`, or `False` (**confirmed off**). A V2/V3 row that is anything but `False` did NOT have anti-aliasing disabled and therefore measures the same behaviour as the variant above it -- the probe reports that as `DID_NOT_MEASURE` rather than `RAN`.

### 1. Image size vs `frame_px`, both axes

| variant | image | frame_px | dw | dh | equal | sidecar dim_check |
|---|---|---|---|---|---|---|
| v0_control | 5164x1709 | 5164x1267 | 0 | 442 | no | pass |
| v7_no_crop | 5164x1708 | 5164x1267 | 0 | 441 | no | pass |
| v8_no_crop_fiducials | 5164x1708 | 5164x1267 | 0 | 441 | no | pass |

`dw`/`dh` are image minus `frame_px`. `dim_check` is the sidecar's own verdict, which inspects the requested axis only (finding F4) -- so a nonzero `dh` beside `dim_check=pass` is exactly the case F4 names.

### 2. Registration fit (measured from the pixels)

| variant | samples | px/ft u | px/ft v | frame px/ft | offset at (u0,v1) px | residual med/max px | v axis |
|---|---|---|---|---|---|---|---|
| v0_control | NOT FITTED | -- | -- | -- | -- | -- | 0 matched sample(s); a linear fit needs at least 2 |
| v7_no_crop | NOT FITTED | -- | -- | -- | -- | -- | 0 matched sample(s); a linear fit needs at least 2 |
| v8_no_crop_fiducials | NOT FITTED | -- | -- | -- | -- | -- | 0 matched sample(s); a linear fit needs at least 2 |

- `v0_control` frame: registration.rendered_uv
- `v7_no_crop` frame: the AUTHORED crop (crop_mode untouched)
- `v8_no_crop_fiducials` frame: the AUTHORED crop (crop_mode untouched)

#### 2b. Fitted per-side margin (how much bigger the render is than the frame)

| variant | left | right | top | bottom |  |
|---|---|---|---|---|---|
| v0_control | -- | -- | -- | -- | NOT FITTED |
| v7_no_crop | -- | -- | -- | -- | NOT FITTED |
| v8_no_crop_fiducials | -- | -- | -- | -- | NOT FITTED |

#### 2c. The fit's own premise: does the ink sit where the bbox says?

The fit matches an element's exact-colour centroid against the centre of its recorded `bbox_uv`. **Those coincide only when the ink fills the box**, which real text, a tag with a leader or a dimension need not do. A displacement that is the SAME for every element is absorbed into the fitted intercept, so it leaves the residuals in 2 clean while shifting every margin in 2b by exactly that amount; one that varies with size or position corrupts the scale instead and does inflate the residuals. Both are surfaced here. No tolerance is applied -- what is close enough for a margin figure is your call.

| variant | ink-bbox px/ft u | ink-bbox px/ft v | px/ft u delta | px/ft v delta | margin delta ft (l/r/t/b) |  |
|---|---|---|---|---|---|---|
| v0_control | -- | -- | -- | -- | -- | one of the two anchors did not fit (unavailable / unavailable) |
| v7_no_crop | -- | -- | -- | -- | -- | one of the two anchors did not fit (unavailable / unavailable) |
| v8_no_crop_fiducials | -- | -- | -- | -- | -- | one of the two anchors did not fit (unavailable / unavailable) |

The second anchor is the centre of the ink's own bounding box rather than its centroid. The two are identical when the ink fills its bbox and diverge when it does not, so a row of ~0 deltas says the anchor choice does not matter on this capture and the margins above do not rest on the premise.

A UNIFORM displacement -- every element's ink sitting the same distance off its box centre -- is NOT recoverable from a capture: nothing distinguishes it from the whole render sitting that far over, which is why it is the dangerous case. What IS recoverable is whether such a displacement is POSSIBLE, and by how much: ink that spans its whole box cannot be off-centre in it. The table below bounds it.

- `v0_control` samples: matched 0, no bbox 1, colour absent 0, clipped at an image edge 0.
- `v7_no_crop` samples: matched 0, no bbox 1, colour absent 0, clipped at an image edge 0.
- `v8_no_crop_fiducials` samples: matched 0, no bbox 1, colour absent 0, clipped at an image edge 0.

### 3. How far recorded annotation bboxes reach past the rendered frame

| variant | left | right | top | bottom | bboxes outside |  |
|---|---|---|---|---|---|---|
| v0_control | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0 of 0 |  |
| v7_no_crop | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0 of 0 |  |
| v8_no_crop_fiducials | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0 of 0 |  |

Read this table against 2b. Where they agree, the render is behaving as if it had been fitted to the crop unioned with the annotations drawn beyond it.

### 4. Coverage per category

#### `v0_control`

Ink column UNMEASURED: no fitted mapping (measurement 2 is unavailable: 0 matched sample(s); a linear fit needs at least 2); the sidecar's mapping is deliberately NOT substituted

| category | assigned | painted | colour present | bbox | ink tested | ink present | bbox off image | ink untested |
|---|---|---|---|---|---|---|---|---|
| <no category> | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |

#### `v7_no_crop`

Ink column UNMEASURED: no fitted mapping (measurement 2 is unavailable: 0 matched sample(s); a linear fit needs at least 2); the sidecar's mapping is deliberately NOT substituted

| category | assigned | painted | colour present | bbox | ink tested | ink present | bbox off image | ink untested |
|---|---|---|---|---|---|---|---|---|
| <no category> | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |

#### `v8_no_crop_fiducials`

Ink column UNMEASURED: no fitted mapping (measurement 2 is unavailable: 0 matched sample(s); a linear fit needs at least 2); the sidecar's mapping is deliberately NOT substituted

| category | assigned | painted | colour present | bbox | ink tested | ink present | bbox off image | ink untested |
|---|---|---|---|---|---|---|---|---|
| <no category> | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |

### 5. Non-white, non-palette pixels

| variant | total | white | palette | off-palette | off-palette frac | blend | grey | other | distinct | tally capped |
|---|---|---|---|---|---|---|---|---|---|---|
| v0_control | 8825276 | 8801147 | 0 | 24129 | 0.00273 | 0 | 23549 | 580 | 29 | no |
| v7_no_crop | 8820112 | 8818772 | 0 | 1340 | 0.00015 | 0 | 1340 | 0 | 23 | no |
| v8_no_crop_fiducials | 8820112 | 8818251 | 0 | 1340 | 0.00015 | 0 | 1340 | 0 | 23 | no |

`blend` is within 3 RGB units of the segment between some palette colour and white. `grey` is all three channels within 3 of each other. The two OVERLAP -- a grey pixel is also collinear with white -- and blend is tested first; the `blend also grey` count below states what that ordering costs.

- `v0_control` blend also grey: 0 px.
- `v7_no_crop` blend also grey: 0 px.
- `v8_no_crop_fiducials` blend also grey: 0 px.

#### `v0_control` top 10 off-palette colours

| rgb | pixels | class | also grey |
|---|---|---|---|
| [0, 0, 0] | 22570 | grey | yes |
| [255, 207, 159] | 288 | other | no |
| [255, 128, 0] | 287 | other | no |
| [71, 71, 71] | 180 | grey | yes |
| [195, 195, 195] | 126 | grey | yes |
| [137, 137, 137] | 108 | grey | yes |
| [17, 17, 17] | 78 | grey | yes |
| [221, 221, 221] | 67 | grey | yes |
| [236, 236, 236] | 59 | grey | yes |
| [208, 208, 208] | 48 | grey | yes |

#### `v7_no_crop` top 10 off-palette colours

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

#### `v8_no_crop_fiducials` top 10 off-palette colours

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
| v0_control | 5c1378bf858dbb1d | 5c1378bf858dbb1d | yes |
| v7_no_crop | 5c1378bf858dbb1d | 5c1378bf858dbb1d | yes |
| v8_no_crop_fiducials | 5c1378bf858dbb1d | 5c1378bf858dbb1d | yes |

The model pass runs ONCE per view, so this column detects a variant CLOBBERING the model artifact -- a path collision, a stray write. Whether a variant changed how the model pass RENDERS is the probe's own `model_reexport_after_variants` verdict, quoted at the top of this view's section, which has its own repeatability control.

### 7. F1 -- the crop boundary, recovered from the pixels

Q1: does the exported image contain the crop boundary, and does its recovered position match `view.CropBox`? A boundary is a band of rows (or columns) each holding a straight run of non-white, non-palette, non-fiducial pixels of >= 25% of the image, closing into one rectangle (or matching the crop shape's levels). `NOT FOUND` on a V8 row is the answer that ImageExportOptions does not draw it.

| variant | probe turned it on | boundary | row/col bands | px/ft u | px/ft v | lattice px/ft | u/v isotropy | boundary px rects (subtract these) |  |
|---|---|---|---|---|---|---|---|---|---|
| v0_control | no | VALUE | 2 / 5 | 17.6377 | 17.6485 | 18.7486 | 0.99939 | [142, 258, 5001, 259]; [142, 1450, 5001, 1451]; [142, 258, 143, 1451]; [5000, 258, 5001, 1451] |  |
| v7_no_crop | no | NOT_FOUND | 0 / 0 | -- | -- | 18.7486 | -- | -- | no row or column holds a straight non-palette run of >= 25% of the image: the crop boundary is NOT in this capture |
| v8_no_crop_fiducials | yes | NOT_FOUND | 0 / 0 | -- | -- | 18.7486 | -- | -- | no row or column holds a straight non-palette run of >= 25% of the image: the crop boundary is NOT in this capture |

`isotropy` of 1 means the boundary's pixel rectangle has the crop's own aspect -- the recovered position is the crop's shape at one uniform scale. `lattice px/ft` is the model capture's (1 / achieved fpp): an untouched capture that rendered exactly the authored crop at the requested count would match it.

- `v8_no_crop_fiducials` MODEL capture (boundary drawn at crop A [-104.01772534812247, -4.810097715203943, 171.4156100768, 62.76814318010526]): NOT_FOUND -- 2 row band(s) and 1 column band(s) long enough to be a crop edge, but no four of them close into one rectangle

### 8. F2 -- the fiducial pair

| variant | element | colour | found | pixels | ink px bbox | clipped | recorded rect_uv |
|---|---|---|---|---|---|---|---|
| v8_no_crop_fiducials | 8852504 | [251, 11, 139] | yes | 368 | [1246.0, 341.0, 1429.0, 342.0] | no | [-41.51502225001987, 57.58333333333331, -31.015022250016784, 60.916666666666664] |
| v8_no_crop_fiducials | 14208030 | [11, 139, 251] | yes | 153 | [4452.0, 1376.0, 4459.0, 1395.0] | no | [140.37039441664712, -1.737695094585085, 140.7870610833138, -0.4166666666666676] |

| fit | px/ft u | px/ft v | u/v isotropy | residual max px u / v |  |
|---|---|---|---|---|---|
| `v8_no_crop_fiducials` F2 (recorded bbox) | 17.6312 | 17.2797 | 1.02034 | 0.58 / 28.58 |  |
| `v8_no_crop_fiducials` F2 (model-anchored) | 17.6346 | 17.6417 | 0.99960 | 0.30 / 0.53 |  |

F2 fits each fiducial's INK EDGES against its recorded extent: four points per axis for the pair, so it has a residual. The recorded extent is a projected 3-D bbox, which can be looser than the element; the model-anchored row replaces it with the element's drawn extent in the model capture, at the cost of assuming the model capture registers.

### 9. F1 vs F2 vs the bbox fit

Q2: with the crop untouched, is the rendered rectangle stable, and do F1 and F2 agree? The disagreement is the number. Corners are the authored crop's, pushed through both maps.

| variant | pair | px/ft u delta | px/ft v delta | worst corner px |  |
|---|---|---|---|---|---|
| v0_control | F1_vs_F2 | -- | -- | -- | one of the two mappings is not available |
| v0_control | F1_vs_bbox_fit | -- | -- | -- | one of the two mappings is not available |
| v0_control | F2_vs_bbox_fit | -- | -- | -- | one of the two mappings is not available |
| v7_no_crop | F1_vs_F2 | -- | -- | -- | one of the two mappings is not available |
| v7_no_crop | F1_vs_bbox_fit | -- | -- | -- | one of the two mappings is not available |
| v7_no_crop | F2_vs_bbox_fit | -- | -- | -- | one of the two mappings is not available |
| v8_no_crop_fiducials | F1_vs_F2 | -- | -- | -- | one of the two mappings is not available |
| v8_no_crop_fiducials | F1_vs_bbox_fit | -- | -- | -- | one of the two mappings is not available |
| v8_no_crop_fiducials | F2_vs_bbox_fit | -- | -- | -- | one of the two mappings is not available |

### 10. Datum extents: ink vs the recorded (authored-crop) bbox

Q3: do datum extents in the capture match the drawing? Positive means the ink reaches FURTHER than the bbox recorded before the capture touched the view. Under V0 the crop was widened to B, which lengthens datums; under V7/V8 it was not.

| variant | datum | category | map | length delta ft | paper in | per side ft l/r/t/b |  |
|---|---|---|---|---|---|---|---|
| v7_no_crop | -- | -- | -- | -- | -- | -- | no mapping from pixels to UV |

Premise: `get_BoundingBox(view)` of a datum is its drawn extent in the view (UNCONFIRMED).

### 11. Model-ink residue

Q4: does membership suppression with subcategories leave any model ink? Off-palette pixels outside the fiducials and outside any recovered boundary band. Counted, not attributed: an annotation the pass could not paint lands here too.

| variant | suppression | off-palette | fiducial px | boundary bands excluded | off-palette outside boundary |
|---|---|---|---|---|---|
| v0_control | hide_categories | 24129 | 0 | 4 | 12029 |
| v7_no_crop | external | 1340 | 0 | 0 | 1340 |
| v8_no_crop_fiducials | external | 1340 | 521 | 0 | 1340 |

### Overlays

The gate is measurement 1 only: image size == `frame_px`. IT IS A SIZE GATE AND NOTHING MORE. A capture can be exactly `frame_px` pixels and still have its CONTENT drawn at a different scale or origin -- which is finding F1 -- and `capture_overlay.py` maps UV through the SIDECAR's numbers, so on such a capture its boxes will not land on the ink. Measurement 2's fitted px/ft is echoed beside each line so that is visible here rather than only three sections up.

- `v0_control`: OVERLAY WITHHELD (measurement 2 did not fit: 0 matched sample(s); a linear fit needs at least 2). measurement 1 shows image 5164x1709 against frame_px 5164x1267; the overlay is not run on a capture whose rendered region is not the frame its sidecar describes
- `v7_no_crop`: OVERLAY WITHHELD (measurement 2 did not fit: 0 matched sample(s); a linear fit needs at least 2). measurement 1 shows image 5164x1708 against frame_px 5164x1267; the overlay is not run on a capture whose rendered region is not the frame its sidecar describes
- `v8_no_crop_fiducials`: OVERLAY WITHHELD (measurement 2 did not fit: 0 matched sample(s); a linear fit needs at least 2). measurement 1 shows image 5164x1708 against frame_px 5164x1267; the overlay is not run on a capture whose rendered region is not the frame its sidecar describes

## View __Plan_CropActive (id 19290402, 1, scale 1:96)

Probe `stage_a_anno_pass_variants` version 2026-09-22.2; combined report `C:\Users\gmcdowell\Documents\VOP\Exports\FVoT\probe_1355\anno_pass_variants_probe\__Plan_CropActive_19290402.anno_pass_variants.json`.

Model pass: success=True, failure_reason=None, achieved dpi=149.99, frame_px=[5274, 2124].
Model re-export after the variants: **unchanged** -- the control held and the post-variant hash matches

**The crop.** Authored crop active: yes; UV [-91.46926561670251, 2250.749394451395, 165.49485231475583, 2337.3310543423245].
- V0 widens it to frame B by (ft): left +11.08 / right +13.26 / top +15.97 / bottom +10.73
- the MODEL pass sets it to crop A, which differs from the authored crop by (ft): left +0.04 / right +0.03 / top +0.03 / bottom +0.01
- crop shape: 1 loop(s), 4 edge(s), rectangle: yes, oblique edges: 0
- **white-suppression cost**: 35716 ms for 4788 element override(s), against 33078 ms for the whole model pass (ratio 1.08; a LOWER bound on suppression-vs-paint, since production does not time its paint alone)
- F2 fiducials: 12261162 (Walls), 5850099 (Generic Models); separated 176.3 ft on u and 80.5 ft on v (1711 of 4788 candidates kept, reference authored_crop)

### 0. What the capture reports it actually did

| variant | suppression mode | applied_smooth_edges | display style | probe conclusion | measured its candidate | production success | capture faults | faults read from |
|---|---|---|---|---|---|---|---|---|
| v0_control | hide_categories | not_attempted | FlatColors | RAN | yes | yes | none | combined_record |
| v7_no_crop | external | not_attempted | FlatColors | RAN | yes | yes | none | combined_record |
| v8_no_crop_fiducials | external | no | FlatColors | RAN | yes | yes | none | combined_record |

`capture faults` come from the probe's combined record (`annotation_pass.capture_faults`) in preference to the annotation sidecar, and the last column says which was used. The sidecar is the fallback because production wrote it before computing its own faults until `dcb4e65`, so an older capture's file carries none however faulted it was. `UNKNOWN (not recorded)` means neither source had the field -- which is not the same fact as `none`.

`applied_smooth_edges` is four-valued: `not_attempted` (the pass was not asked), `read_failed`, `unchanged (failed)`, or `False` (**confirmed off**). A V2/V3 row that is anything but `False` did NOT have anti-aliasing disabled and therefore measures the same behaviour as the variant above it -- the probe reports that as `DID_NOT_MEASURE` rather than `RAN`.

### 1. Image size vs `frame_px`, both axes

| variant | image | frame_px | dw | dh | equal | sidecar dim_check |
|---|---|---|---|---|---|---|
| v0_control | 5274x2248 | 5274x2124 | 0 | 124 | no | pass |
| v7_no_crop | 4819x2054 | 5274x2124 | -455 | -70 | no | pass |
| v8_no_crop_fiducials | 4819x2054 | 5274x2124 | -455 | -70 | no | pass |

`dw`/`dh` are image minus `frame_px`. `dim_check` is the sidecar's own verdict, which inspects the requested axis only (finding F4) -- so a nonzero `dh` beside `dim_check=pass` is exactly the case F4 names.

### 2. Registration fit (measured from the pixels)

| variant | samples | px/ft u | px/ft v | frame px/ft | offset at (u0,v1) px | residual med/max px | v axis |
|---|---|---|---|---|---|---|---|
| v0_control | 13 pairs | 17.788 | 17.582 | 18.749 | (132.5, 119.5) | 9.89 / 29.30 | negative (expected) |
| v7_no_crop | 269 pairs | 16.337 | 16.871 | 18.754 | (290.8, 329.5) | 19.24 / 192.95 | negative (expected) |
| v8_no_crop_fiducials | 269 pairs | 16.337 | 16.871 | 18.754 | (290.8, 329.5) | 19.24 / 192.95 | negative (expected) |

- `v0_control` frame: registration.rendered_uv
- `v7_no_crop` frame: the AUTHORED crop (crop_mode untouched)
- `v8_no_crop_fiducials` frame: the AUTHORED crop (crop_mode untouched)

#### 2b. Fitted per-side margin (how much bigger the render is than the frame)

| variant | left | right | top | bottom |  |
|---|---|---|---|---|---|
| v0_control | 7.45 ft / 0.931 in | 7.74 ft / 0.967 in | 6.79 ft / 0.849 in | 7.77 ft / 0.972 in |  |
| v7_no_crop | 17.80 ft / 2.225 in | 20.21 ft / 2.526 in | 19.53 ft / 2.441 in | 15.64 ft / 1.955 in |  |
| v8_no_crop_fiducials | 17.80 ft / 2.225 in | 20.21 ft / 2.526 in | 19.53 ft / 2.441 in | 15.64 ft / 1.955 in |  |

#### 2c. The fit's own premise: does the ink sit where the bbox says?

The fit matches an element's exact-colour centroid against the centre of its recorded `bbox_uv`. **Those coincide only when the ink fills the box**, which real text, a tag with a leader or a dimension need not do. A displacement that is the SAME for every element is absorbed into the fitted intercept, so it leaves the residuals in 2 clean while shifting every margin in 2b by exactly that amount; one that varies with size or position corrupts the scale instead and does inflate the residuals. Both are surfaced here. No tolerance is applied -- what is close enough for a margin figure is your call.

| variant | ink-bbox px/ft u | ink-bbox px/ft v | px/ft u delta | px/ft v delta | margin delta ft (l/r/t/b) |  |
|---|---|---|---|---|---|---|
| v0_control | 17.812 | 17.799 | -0.0235 | -0.2167 | 0.281 / 0.110 / 0.761 / 0.796 |  |
| v7_no_crop | 16.289 | 16.319 | 0.0479 | 0.5518 | -0.345 / -0.522 / -2.307 / -1.810 |  |
| v8_no_crop_fiducials | 16.289 | 16.319 | 0.0479 | 0.5518 | -0.345 / -0.522 / -2.307 / -1.810 |  |

The second anchor is the centre of the ink's own bounding box rather than its centroid. The two are identical when the ink fills its bbox and diverge when it does not, so a row of ~0 deltas says the anchor choice does not matter on this capture and the margins above do not rest on the premise.

A UNIFORM displacement -- every element's ink sitting the same distance off its box centre -- is NOT recoverable from a capture: nothing distinguishes it from the whole render sitting that far over, which is why it is the dangerous case. What IS recoverable is whether such a displacement is POSSIBLE, and by how much: ink that spans its whole box cannot be off-centre in it. The table below bounds it.

##### `v0_control` does the ink fill its recorded bbox?

| category | n | ink span / bbox u | ink span / bbox v | solidity | possible offset u px | possible offset v px |
|---|---|---|---|---|---|---|
| Dimensions | 2 | 0.947 (0.892 .. 1.002) | 0.965 (0.916 .. 1.014) | 0.058 (0.055 .. 0.061) | 1.1 (0.0 .. 2.2) | 0.6 (0.0 .. 1.2) |
| Grids | 4 | 1.002 (1.002 .. 1.002) | 1.031 (1.024 .. 1.038) | 0.012 (0.012 .. 0.012) | 0.0 (0.0 .. 0.0) | 0.0 (0.0 .. 0.0) |
| Lines | 1 | 1.034 (1.034 .. 1.034) | 1.046 (1.046 .. 1.046) | 0.167 (0.167 .. 0.167) | 0.0 (0.0 .. 0.0) | 0.0 (0.0 .. 0.0) |
| Revision Cloud Tags | 1 | 0.938 (0.938 .. 0.938) | 0.916 (0.916 .. 0.916) | 0.094 (0.094 .. 0.094) | 1.6 (1.6 .. 1.6) | 2.2 (2.2 .. 2.2) |
| Revision Clouds | 1 | 1.020 (1.020 .. 1.020) | 1.015 (1.015 .. 1.015) | 0.066 (0.066 .. 0.066) | 0.0 (0.0 .. 0.0) | 0.0 (0.0 .. 0.0) |
| Views | 3 | 1.001 (1.001 .. 1.003) | 1.020 (1.011 .. 1.020) | 0.016 (0.015 .. 0.018) | 0.0 (0.0 .. 0.0) | 0.0 (0.0 .. 0.0) |

`ink span / bbox` of 1.0 means the ink reaches both edges of its recorded box, so it cannot be off-centre in it and the margins above are bounded. `solidity` is ink pixels over ink bbox area: below 1 the ink is not a solid rectangle -- a glyph run, an L, a leader -- so its centroid can also sit away from its own bbox centre. `possible offset` is how far, in pixels, a margin in 2b could be wrong because of this.

##### `v7_no_crop` does the ink fill its recorded bbox?

| category | n | ink span / bbox u | ink span / bbox v | solidity | possible offset u px | possible offset v px |
|---|---|---|---|---|---|---|
| Dimensions | 135 | 0.985 (0.234 .. 1.003) | 0.958 (0.279 .. 0.973) | 0.035 (0.004 .. 0.160) | 0.8 (0.0 .. 106.6) | 3.1 (0.9 .. 135.5) |
| Door Tags | 26 | 0.979 (0.808 .. 1.061) | 0.988 (0.588 .. 1.027) | 0.135 (0.073 .. 0.192) | 0.5 (0.0 .. 5.0) | 0.3 (0.0 .. 5.8) |
| Grids | 4 | 0.997 (0.997 .. 0.997) | 0.978 (0.978 .. 0.993) | 0.013 (0.012 .. 0.013) | 7.4 (7.4 .. 7.4) | 0.7 (0.2 .. 0.7) |
| Lines | 1 | 0.938 (0.938 .. 0.938) | 0.909 (0.909 .. 0.909) | 0.200 (0.200 .. 0.200) | 0.2 (0.2 .. 0.2) | 0.3 (0.3 .. 0.3) |
| Revision Cloud Tags | 1 | 0.915 (0.915 .. 0.915) | 0.873 (0.873 .. 0.873) | 0.107 (0.107 .. 0.107) | 2.0 (2.0 .. 2.0) | 3.1 (3.1 .. 3.1) |
| Revision Clouds | 1 | 1.014 (1.014 .. 1.014) | 0.965 (0.965 .. 0.965) | 0.068 (0.068 .. 0.068) | 0.0 (0.0 .. 0.0) | 3.8 (3.8 .. 3.8) |
| Room Tags | 17 | 0.993 (0.961 .. 1.010) | 0.872 (0.872 .. 0.910) | 0.201 (0.125 .. 0.219) | 0.4 (0.0 .. 2.1) | 3.5 (3.0 .. 3.7) |
| Views | 3 | 0.987 (0.981 .. 0.999) | 0.968 (0.963 .. 0.972) | 0.018 (0.016 .. 0.020) | 3.1 (0.3 .. 3.2) | 4.3 (3.6 .. 4.4) |
| Wall Tags | 53 | 1.012 (0.978 .. 1.041) | 0.984 (0.968 .. 1.002) | 0.060 (0.034 .. 0.509) | 0.0 (0.0 .. 0.9) | 0.5 (0.0 .. 1.9) |
| Window Tags | 27 | 0.971 (0.962 .. 0.989) | 0.874 (0.874 .. 0.899) | 0.215 (0.193 .. 0.235) | 0.8 (0.3 .. 1.1) | 2.5 (2.0 .. 2.5) |

`ink span / bbox` of 1.0 means the ink reaches both edges of its recorded box, so it cannot be off-centre in it and the margins above are bounded. `solidity` is ink pixels over ink bbox area: below 1 the ink is not a solid rectangle -- a glyph run, an L, a leader -- so its centroid can also sit away from its own bbox centre. `possible offset` is how far, in pixels, a margin in 2b could be wrong because of this.

##### `v8_no_crop_fiducials` does the ink fill its recorded bbox?

| category | n | ink span / bbox u | ink span / bbox v | solidity | possible offset u px | possible offset v px |
|---|---|---|---|---|---|---|
| Dimensions | 135 | 0.985 (0.234 .. 1.003) | 0.958 (0.279 .. 0.973) | 0.035 (0.004 .. 0.160) | 0.8 (0.0 .. 106.6) | 3.1 (0.9 .. 135.5) |
| Door Tags | 26 | 0.979 (0.808 .. 1.061) | 0.988 (0.588 .. 1.027) | 0.135 (0.073 .. 0.192) | 0.5 (0.0 .. 5.0) | 0.3 (0.0 .. 5.8) |
| Grids | 4 | 0.997 (0.997 .. 0.997) | 0.978 (0.978 .. 0.993) | 0.013 (0.012 .. 0.013) | 7.4 (7.4 .. 7.4) | 0.7 (0.2 .. 0.7) |
| Lines | 1 | 0.938 (0.938 .. 0.938) | 0.909 (0.909 .. 0.909) | 0.200 (0.200 .. 0.200) | 0.2 (0.2 .. 0.2) | 0.3 (0.3 .. 0.3) |
| Revision Cloud Tags | 1 | 0.915 (0.915 .. 0.915) | 0.873 (0.873 .. 0.873) | 0.107 (0.107 .. 0.107) | 2.0 (2.0 .. 2.0) | 3.1 (3.1 .. 3.1) |
| Revision Clouds | 1 | 1.014 (1.014 .. 1.014) | 0.965 (0.965 .. 0.965) | 0.068 (0.068 .. 0.068) | 0.0 (0.0 .. 0.0) | 3.8 (3.8 .. 3.8) |
| Room Tags | 17 | 0.993 (0.961 .. 1.010) | 0.872 (0.872 .. 0.910) | 0.201 (0.125 .. 0.219) | 0.4 (0.0 .. 2.1) | 3.5 (3.0 .. 3.7) |
| Views | 3 | 0.987 (0.981 .. 0.999) | 0.968 (0.963 .. 0.972) | 0.018 (0.016 .. 0.020) | 3.1 (0.3 .. 3.2) | 4.3 (3.6 .. 4.4) |
| Wall Tags | 53 | 1.012 (0.978 .. 1.041) | 0.984 (0.968 .. 1.002) | 0.060 (0.034 .. 0.509) | 0.0 (0.0 .. 0.9) | 0.5 (0.0 .. 1.9) |
| Window Tags | 27 | 0.971 (0.962 .. 0.989) | 0.874 (0.874 .. 0.899) | 0.215 (0.193 .. 0.235) | 0.8 (0.3 .. 1.1) | 2.5 (2.0 .. 2.5) |

`ink span / bbox` of 1.0 means the ink reaches both edges of its recorded box, so it cannot be off-centre in it and the margins above are bounded. `solidity` is ink pixels over ink bbox area: below 1 the ink is not a solid rectangle -- a glyph run, an L, a leader -- so its centroid can also sit away from its own bbox centre. `possible offset` is how far, in pixels, a margin in 2b could be wrong because of this.

- `v0_control` samples: matched 13, no bbox 1, colour absent 257, clipped at an image edge 12.
- `v7_no_crop` samples: matched 269, no bbox 1, colour absent 1, clipped at an image edge 12.
- `v8_no_crop_fiducials` samples: matched 269, no bbox 1, colour absent 1, clipped at an image edge 12.

### 3. How far recorded annotation bboxes reach past the rendered frame

| variant | left | right | top | bottom | bboxes outside |  |
|---|---|---|---|---|---|---|
| v0_control | 4.62 ft / 0.577 in | 4.51 ft / 0.564 in | 6.01 ft / 0.751 in | 6.86 ft / 0.858 in | 16 of 282 |  |
| v7_no_crop | 15.70 ft / 1.962 in | 17.76 ft / 2.221 in | 21.98 ft / 2.748 in | 17.60 ft / 2.200 in | 69 of 282 |  |
| v8_no_crop_fiducials | 15.70 ft / 1.962 in | 17.76 ft / 2.221 in | 21.98 ft / 2.748 in | 17.60 ft / 2.200 in | 69 of 282 |  |

Read this table against 2b. Where they agree, the render is behaving as if it had been fitted to the crop unioned with the annotations drawn beyond it.

### 4. Coverage per category

#### `v0_control`

Ink test threshold >2% non-white, placed through the fitted mapping from measurement (2).

| category | assigned | painted | colour present | bbox | ink tested | ink present | bbox off image | ink untested |
|---|---|---|---|---|---|---|---|---|
| <no category> | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| Attached Detail Groups | 1 | 1 | 0 | 1 | 1 | 0 | 0 | 0 |
| Dimensions | 135 | 135 | 2 | 135 | 135 | 12 | 0 | 0 |
| Door Tags | 26 | 26 | 0 | 26 | 26 | 1 | 0 | 0 |
| Grids | 16 | 16 | 16 | 16 | 16 | 11 | 0 | 0 |
| Lines | 2 | 2 | 2 | 2 | 2 | 2 | 0 | 0 |
| Revision Cloud Tags | 1 | 1 | 1 | 1 | 1 | 1 | 0 | 0 |
| Revision Clouds | 1 | 1 | 1 | 1 | 1 | 1 | 0 | 0 |
| Room Tags | 17 | 17 | 0 | 17 | 17 | 1 | 0 | 0 |
| Views | 3 | 3 | 3 | 3 | 3 | 3 | 0 | 0 |
| Wall Tags | 53 | 53 | 0 | 53 | 53 | 3 | 0 | 0 |
| Window Tags | 27 | 27 | 0 | 27 | 27 | 0 | 0 | 0 |

#### `v7_no_crop`

Ink test threshold >2% non-white, placed through the fitted mapping from measurement (2).

| category | assigned | painted | colour present | bbox | ink tested | ink present | bbox off image | ink untested |
|---|---|---|---|---|---|---|---|---|
| <no category> | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| Attached Detail Groups | 1 | 1 | 0 | 1 | 1 | 1 | 0 | 0 |
| Dimensions | 135 | 135 | 135 | 135 | 135 | 132 | 0 | 0 |
| Door Tags | 26 | 26 | 26 | 26 | 26 | 26 | 0 | 0 |
| Grids | 16 | 16 | 16 | 16 | 16 | 16 | 0 | 0 |
| Lines | 2 | 2 | 2 | 2 | 2 | 2 | 0 | 0 |
| Revision Cloud Tags | 1 | 1 | 1 | 1 | 1 | 1 | 0 | 0 |
| Revision Clouds | 1 | 1 | 1 | 1 | 1 | 1 | 0 | 0 |
| Room Tags | 17 | 17 | 17 | 17 | 17 | 17 | 0 | 0 |
| Views | 3 | 3 | 3 | 3 | 3 | 3 | 0 | 0 |
| Wall Tags | 53 | 53 | 53 | 53 | 53 | 53 | 0 | 0 |
| Window Tags | 27 | 27 | 27 | 27 | 27 | 27 | 0 | 0 |

#### `v8_no_crop_fiducials`

Ink test threshold >2% non-white, placed through the fitted mapping from measurement (2).

| category | assigned | painted | colour present | bbox | ink tested | ink present | bbox off image | ink untested |
|---|---|---|---|---|---|---|---|---|
| <no category> | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| Attached Detail Groups | 1 | 1 | 0 | 1 | 1 | 1 | 0 | 0 |
| Dimensions | 135 | 135 | 135 | 135 | 135 | 132 | 0 | 0 |
| Door Tags | 26 | 26 | 26 | 26 | 26 | 26 | 0 | 0 |
| Grids | 16 | 16 | 16 | 16 | 16 | 16 | 0 | 0 |
| Lines | 2 | 2 | 2 | 2 | 2 | 2 | 0 | 0 |
| Revision Cloud Tags | 1 | 1 | 1 | 1 | 1 | 1 | 0 | 0 |
| Revision Clouds | 1 | 1 | 1 | 1 | 1 | 1 | 0 | 0 |
| Room Tags | 17 | 17 | 17 | 17 | 17 | 17 | 0 | 0 |
| Views | 3 | 3 | 3 | 3 | 3 | 3 | 0 | 0 |
| Wall Tags | 53 | 53 | 53 | 53 | 53 | 53 | 0 | 0 |
| Window Tags | 27 | 27 | 27 | 27 | 27 | 27 | 0 | 0 |

### 5. Non-white, non-palette pixels

| variant | total | white | palette | off-palette | off-palette frac | blend | grey | other | distinct | tally capped |
|---|---|---|---|---|---|---|---|---|---|---|
| v0_control | 11855952 | 11753223 | 65135 | 37594 | 0.00317 | 10494 | 27094 | 6 | 320 | no |
| v7_no_crop | 9898226 | 9664786 | 198951 | 34489 | 0.00348 | 33827 | 660 | 2 | 1388 | no |
| v8_no_crop_fiducials | 9898226 | 9664306 | 198951 | 34489 | 0.00348 | 33827 | 660 | 2 | 1388 | no |

`blend` is within 3 RGB units of the segment between some palette colour and white. `grey` is all three channels within 3 of each other. The two OVERLAP -- a grey pixel is also collinear with white -- and blend is tested first; the `blend also grey` count below states what that ordering costs.

- `v0_control` blend also grey: 0 px.
- `v7_no_crop` blend also grey: 0 px.
- `v8_no_crop_fiducials` blend also grey: 0 px.

#### `v0_control` top 10 off-palette colours

| rgb | pixels | class | also grey |
|---|---|---|---|
| [0, 0, 0] | 26397 | grey | yes |
| [137, 137, 137] | 198 | grey | yes |
| [254, 172, 172] | 180 | blend | no |
| [253, 164, 56] | 158 | blend | no |
| [253, 59, 59] | 158 | blend | no |
| [254, 183, 161] | 150 | blend | no |
| [253, 55, 121] | 146 | blend | no |
| [49, 253, 223] | 140 | blend | no |
| [253, 186, 105] | 140 | blend | no |
| [108, 56, 253] | 136 | blend | no |

#### `v7_no_crop` top 10 off-palette colours

| rgb | pixels | class | also grey |
|---|---|---|---|
| [0, 0, 0] | 278 | grey | yes |
| [253, 94, 249] | 265 | blend | no |
| [253, 223, 114] | 260 | blend | no |
| [136, 112, 253] | 240 | blend | no |
| [51, 253, 157] | 156 | blend | no |
| [254, 172, 172] | 156 | blend | no |
| [182, 168, 254] | 155 | blend | no |
| [253, 47, 248] | 153 | blend | no |
| [254, 183, 161] | 150 | blend | no |
| [234, 253, 58] | 134 | blend | no |

#### `v8_no_crop_fiducials` top 10 off-palette colours

| rgb | pixels | class | also grey |
|---|---|---|---|
| [0, 0, 0] | 278 | grey | yes |
| [253, 94, 249] | 265 | blend | no |
| [253, 223, 114] | 260 | blend | no |
| [136, 112, 253] | 240 | blend | no |
| [51, 253, 157] | 156 | blend | no |
| [254, 172, 172] | 156 | blend | no |
| [182, 168, 254] | 155 | blend | no |
| [253, 47, 248] | 153 | blend | no |
| [254, 183, 161] | 150 | blend | no |
| [234, 253, 58] | 134 | blend | no |

### 6. Model TIFF hash vs V0

| variant | model sha256 (16) | V0 sha256 (16) | equal |
|---|---|---|---|
| v0_control | 11a6ae2e33f7f08b | 11a6ae2e33f7f08b | yes |
| v7_no_crop | 11a6ae2e33f7f08b | 11a6ae2e33f7f08b | yes |
| v8_no_crop_fiducials | 11a6ae2e33f7f08b | 11a6ae2e33f7f08b | yes |

The model pass runs ONCE per view, so this column detects a variant CLOBBERING the model artifact -- a path collision, a stray write. Whether a variant changed how the model pass RENDERS is the probe's own `model_reexport_after_variants` verdict, quoted at the top of this view's section, which has its own repeatability control.

### 7. F1 -- the crop boundary, recovered from the pixels

Q1: does the exported image contain the crop boundary, and does its recovered position match `view.CropBox`? A boundary is a band of rows (or columns) each holding a straight run of non-white, non-palette, non-fiducial pixels of >= 25% of the image, closing into one rectangle (or matching the crop shape's levels). `NOT FOUND` on a V8 row is the answer that ImageExportOptions does not draw it.

| variant | probe turned it on | boundary | row/col bands | px/ft u | px/ft v | lattice px/ft | u/v isotropy | boundary px rects (subtract these) |  |
|---|---|---|---|---|---|---|---|---|---|
| v0_control | no | VALUE | 3 / 4 | 8.7794 | 17.8213 | 18.7487 | 0.49264 | [324, 391, 4614, 392]; [324, 1934, 2878, 1935]; [324, 391, 325, 1935]; [2580, 295, 2581, 1948] |  |
| v7_no_crop | no | NOT_FOUND | 0 / 0 | -- | -- | 18.7487 | -- | -- | no row or column holds a straight non-palette run of >= 25% of the image: the crop boundary is NOT in this capture |
| v8_no_crop_fiducials | yes | NOT_FOUND | 0 / 0 | -- | -- | 18.7487 | -- | -- | no row or column holds a straight non-palette run of >= 25% of the image: the crop boundary is NOT in this capture |

`isotropy` of 1 means the boundary's pixel rectangle has the crop's own aspect -- the recovered position is the crop's shape at one uniform scale. `lattice px/ft` is the model capture's (1 / achieved fpp): an untouched capture that rendered exactly the authored crop at the requested count would match it.

- `v8_no_crop_fiducials` MODEL capture (boundary drawn at crop A [-91.50953986386723, 2250.738331334784, 165.52223653185607, 2337.3578799686616]): VALUE; against the model lattice: px/ft u -0.0117, v -0.0115, worst corner 1.50 px

### 8. F2 -- the fiducial pair

| variant | element | colour | found | pixels | ink px bbox | clipped | recorded rect_uv |
|---|---|---|---|---|---|---|---|
| v8_no_crop_fiducials | 12261162 | [251, 11, 139] | yes | 463 | [1186.0, 415.0, 1340.0, 417.0] | no | [-37.3402448956206, 2332.8998997006133, -26.729670423215794, 2333.8998997006133] |
| v8_no_crop_fiducials | 5850099 | [11, 139, 251] | yes | 17 | [4129.0, 1732.0, 4133.0, 1738.0] | no | [143.95663010436672, 2252.592608033942, 144.5763807605349, 2253.21235869011] |

| fit | px/ft u | px/ft v | u/v isotropy | residual max px u / v |  |
|---|---|---|---|---|---|
| `v8_no_crop_fiducials` F2 (recorded bbox) | 16.2645 | 16.3844 | 0.99268 | 9.06 / 6.74 |  |
| `v8_no_crop_fiducials` F2 (model-anchored) | 11.7179 | 18.7371 | 0.62539 | 0.00 / 0.00 |  |

F2 fits each fiducial's INK EDGES against its recorded extent: four points per axis for the pair, so it has a residual. The recorded extent is a projected 3-D bbox, which can be looser than the element; the model-anchored row replaces it with the element's drawn extent in the model capture, at the cost of assuming the model capture registers.

### 9. F1 vs F2 vs the bbox fit

Q2: with the crop untouched, is the rendered rectangle stable, and do F1 and F2 agree? The disagreement is the number. Corners are the authored crop's, pushed through both maps.

| variant | pair | px/ft u delta | px/ft v delta | worst corner px |  |
|---|---|---|---|---|---|
| v0_control | F1_vs_F2 | -- | -- | -- | one of the two mappings is not available |
| v0_control | F1_vs_bbox_fit | -9.0090 | +0.2391 | 2320.07 |  |
| v0_control | F2_vs_bbox_fit | -- | -- | -- | one of the two mappings is not available |
| v7_no_crop | F1_vs_F2 | -- | -- | -- | one of the two mappings is not available |
| v7_no_crop | F1_vs_bbox_fit | -- | -- | -- | one of the two mappings is not available |
| v7_no_crop | F2_vs_bbox_fit | -- | -- | -- | one of the two mappings is not available |
| v8_no_crop_fiducials | F1_vs_F2 | -- | -- | -- | one of the two mappings is not available |
| v8_no_crop_fiducials | F1_vs_bbox_fit | -- | -- | -- | one of the two mappings is not available |
| v8_no_crop_fiducials | F2_vs_bbox_fit | -0.0725 | -0.4868 | 22.69 |  |

### 10. Datum extents: ink vs the recorded (authored-crop) bbox

Q3: do datum extents in the capture match the drawing? Positive means the ink reaches FURTHER than the bbox recorded before the capture touched the view. Under V0 the crop was widened to B, which lengthens datums; under V7/V8 it was not.

| variant | datum | category | map | length delta ft | paper in | per side ft l/r/t/b |  |
|---|---|---|---|---|---|---|---|
| v0_control | 3693278 | Grids | F1 | +299.13 | +37.392 | +16.14 / +283.00 / +0.03 / +0.01 |  |
| v0_control | 3693539 | Grids | F1 | +299.13 | +37.392 | +16.14 / +283.00 / +0.03 / +0.01 |  |
| v0_control | 3693688 | Grids | F1 | +299.13 | +37.392 | +16.14 / +283.00 / +0.07 / +0.02 |  |
| v0_control | 3694573 | Grids | F1 | -0.02 | -0.002 | -22.64 / +26.84 / -0.01 / -0.00 | CLIPPED at image edge |
| v0_control | 3694705 | Grids | F1 | -0.02 | -0.002 | -53.46 / +57.78 / -0.01 / -0.00 | CLIPPED at image edge |
| v0_control | 3694782 | Grids | F1 | -0.02 | -0.002 | -74.48 / +78.68 / -0.01 / -0.00 | CLIPPED at image edge |
| v0_control | 3694880 | Grids | F1 | -0.02 | -0.002 | -105.30 / +109.62 / -0.01 / -0.00 | CLIPPED at image edge |
| v0_control | 3694982 | Grids | F1 | -0.02 | -0.002 | -136.24 / +140.44 / -0.01 / -0.00 | CLIPPED at image edge |
| v0_control | 3695045 | Grids | F1 | -0.02 | -0.002 | -167.06 / +171.38 / -0.01 / -0.00 | CLIPPED at image edge |
| v0_control | 3695123 | Grids | F1 | -0.02 | -0.002 | -208.98 / +213.18 / -0.01 / -0.00 | CLIPPED at image edge |
| v0_control | 3695201 | Grids | F1 | -0.02 | -0.002 | -239.91 / +244.12 / -0.01 / -0.00 | CLIPPED at image edge |
| v0_control | 3697629 | Grids | F1 | -0.02 | -0.002 | -198.00 / +202.20 / -0.01 / -0.00 | CLIPPED at image edge |
| v0_control | 3698218 | Grids | F1 | -0.02 | -0.002 | -257.04 / +261.35 / -0.01 / -0.00 | CLIPPED at image edge |
| v0_control | 3784562 | Grids | F1 | +299.13 | +37.392 | +16.14 / +283.00 / +0.07 / +0.02 |  |
| v0_control | 3923006 | Grids | F1 | -0.02 | -0.002 | -5.41 / +9.61 / -0.01 / -0.00 | CLIPPED at image edge |
| v0_control | 5136165 | Grids | F1 | -0.02 | -0.002 | -64.44 / +68.75 / -0.01 / -0.00 | CLIPPED at image edge |
| v7_no_crop | 3693278 | Grids | bbox_fit | -0.90 | -0.113 | -0.41 / -0.49 / -0.62 / +0.53 |  |
| v7_no_crop | 3693539 | Grids | bbox_fit | -0.90 | -0.113 | -0.41 / -0.49 / -1.21 / +1.13 |  |
| v7_no_crop | 3693688 | Grids | bbox_fit | -0.90 | -0.113 | -0.41 / -0.49 / +0.94 / -1.03 |  |
| v7_no_crop | 3694573 | Grids | bbox_fit | -4.41 | -0.552 | -0.28 / +0.32 / -2.45 / -1.96 | CLIPPED at image edge |
| v7_no_crop | 3694705 | Grids | bbox_fit | -4.41 | -0.552 | -0.21 / +0.25 / -2.45 / -1.96 | CLIPPED at image edge |
| v7_no_crop | 3694782 | Grids | bbox_fit | -4.41 | -0.552 | -0.14 / +0.18 / -2.45 / -1.96 | CLIPPED at image edge |
| v7_no_crop | 3694880 | Grids | bbox_fit | -4.41 | -0.552 | -0.01 / +0.05 / -2.45 / -1.96 | CLIPPED at image edge |
| v7_no_crop | 3694982 | Grids | bbox_fit | -4.41 | -0.552 | +0.12 / -0.02 / -2.45 / -1.96 | CLIPPED at image edge |
| v7_no_crop | 3695045 | Grids | bbox_fit | -4.41 | -0.552 | +0.19 / -0.15 / -2.45 / -1.96 | CLIPPED at image edge |
| v7_no_crop | 3695123 | Grids | bbox_fit | -4.41 | -0.552 | +0.33 / -0.29 / -2.45 / -1.96 | CLIPPED at image edge |
| v7_no_crop | 3695201 | Grids | bbox_fit | -4.41 | -0.552 | +0.46 / -0.36 / -2.45 / -1.96 | CLIPPED at image edge |
| v7_no_crop | 3697629 | Grids | bbox_fit | -4.41 | -0.552 | +0.32 / -0.28 / -2.45 / -1.96 | CLIPPED at image edge |
| v7_no_crop | 3698218 | Grids | bbox_fit | -4.41 | -0.552 | +0.52 / -0.42 / -2.45 / -1.96 | CLIPPED at image edge |
| v7_no_crop | 3784562 | Grids | bbox_fit | -0.90 | -0.113 | -0.41 / -0.49 / +0.40 / -0.43 |  |
| v7_no_crop | 3923006 | Grids | bbox_fit | -4.41 | -0.552 | -0.34 / +0.38 / -2.45 / -1.96 | CLIPPED at image edge |
| v7_no_crop | 5136165 | Grids | bbox_fit | -4.41 | -0.552 | -0.14 / +0.18 / -2.45 / -1.96 | CLIPPED at image edge |
| v8_no_crop_fiducials | 3693278 | Grids | F2 | +0.39 | +0.049 | +0.05 / +0.34 / -0.10 / +0.12 |  |
| v8_no_crop_fiducials | 3693539 | Grids | F2 | +0.39 | +0.049 | +0.05 / +0.34 / -0.24 / +0.27 |  |
| v8_no_crop_fiducials | 3693688 | Grids | F2 | +0.39 | +0.049 | +0.05 / +0.34 / +0.18 / -0.15 |  |
| v8_no_crop_fiducials | 3694573 | Grids | F2 | -0.80 | -0.100 | +0.01 / +0.05 / -0.49 / -0.31 | CLIPPED at image edge |
| v8_no_crop_fiducials | 3694705 | Grids | F2 | -0.80 | -0.100 | -0.06 / +0.11 / -0.49 / -0.31 | CLIPPED at image edge |
| v8_no_crop_fiducials | 3694782 | Grids | F2 | -0.80 | -0.100 | -0.07 / +0.13 / -0.49 / -0.31 | CLIPPED at image edge |
| v8_no_crop_fiducials | 3694880 | Grids | F2 | -0.80 | -0.100 | -0.08 / +0.14 / -0.49 / -0.31 | CLIPPED at image edge |
| v8_no_crop_fiducials | 3694982 | Grids | F2 | -0.80 | -0.100 | -0.08 / +0.20 / -0.49 / -0.31 | CLIPPED at image edge |
| v8_no_crop_fiducials | 3695045 | Grids | F2 | -0.80 | -0.100 | -0.15 / +0.21 / -0.49 / -0.31 | CLIPPED at image edge |
| v8_no_crop_fiducials | 3695123 | Grids | F2 | -0.80 | -0.100 | -0.18 / +0.24 / -0.49 / -0.31 | CLIPPED at image edge |
| v8_no_crop_fiducials | 3695201 | Grids | F2 | -0.80 | -0.100 | -0.19 / +0.31 / -0.49 / -0.31 | CLIPPED at image edge |
| v8_no_crop_fiducials | 3697629 | Grids | F2 | -0.80 | -0.100 | -0.15 / +0.21 / -0.49 / -0.31 | CLIPPED at image edge |
| v8_no_crop_fiducials | 3698218 | Grids | F2 | -0.80 | -0.100 | -0.20 / +0.32 / -0.49 / -0.31 | CLIPPED at image edge |
| v8_no_crop_fiducials | 3784562 | Grids | F2 | +0.39 | +0.049 | +0.05 / +0.34 / +0.10 / -0.01 |  |
| v8_no_crop_fiducials | 3923006 | Grids | F2 | -0.80 | -0.100 | +0.02 / +0.03 / -0.49 / -0.31 | CLIPPED at image edge |
| v8_no_crop_fiducials | 5136165 | Grids | F2 | -0.80 | -0.100 | -0.03 / +0.08 / -0.49 / -0.31 | CLIPPED at image edge |

Premise: `get_BoundingBox(view)` of a datum is its drawn extent in the view (UNCONFIRMED).

### 11. Model-ink residue

Q4: does membership suppression with subcategories leave any model ink? Off-palette pixels outside the fiducials and outside any recovered boundary band. Counted, not attributed: an annotation the pass could not paint lands here too.

| variant | suppression | off-palette | fiducial px | boundary bands excluded | off-palette outside boundary |
|---|---|---|---|---|---|
| v0_control | hide_categories | 37594 | 0 | 4 | 27557 |
| v7_no_crop | external | 34489 | 0 | 0 | 34489 |
| v8_no_crop_fiducials | external | 34489 | 480 | 0 | 34489 |

### Overlays

The gate is measurement 1 only: image size == `frame_px`. IT IS A SIZE GATE AND NOTHING MORE. A capture can be exactly `frame_px` pixels and still have its CONTENT drawn at a different scale or origin -- which is finding F1 -- and `capture_overlay.py` maps UV through the SIDECAR's numbers, so on such a capture its boxes will not land on the ink. Measurement 2's fitted px/ft is echoed beside each line so that is visible here rather than only three sections up.

- `v0_control`: OVERLAY WITHHELD (fitted 17.788 / 17.582 px/ft against the frame's 18.749). measurement 1 shows image 5274x2248 against frame_px 5274x2124; the overlay is not run on a capture whose rendered region is not the frame its sidecar describes
- `v7_no_crop`: OVERLAY WITHHELD (fitted 16.337 / 16.871 px/ft against the frame's 18.754). measurement 1 shows image 4819x2054 against frame_px 5274x2124; the overlay is not run on a capture whose rendered region is not the frame its sidecar describes
- `v8_no_crop_fiducials`: OVERLAY WITHHELD (fitted 16.337 / 16.871 px/ft against the frame's 18.754). measurement 1 shows image 4819x2054 against frame_px 5274x2124; the overlay is not run on a capture whose rendered region is not the frame its sidecar describes
