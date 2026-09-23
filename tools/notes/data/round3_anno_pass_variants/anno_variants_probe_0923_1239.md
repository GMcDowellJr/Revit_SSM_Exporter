# Annotation-pass variant report

Values only. No pass/fail, no score, no recommendation -- the probe brief puts the judgement with Greg, and a tool that rated the variants would replace that read.

## View __Elevation_CropActive (id 19293413, 3, scale 1:96)

Probe `stage_a_anno_pass_variants` version 2026-09-23.2; combined report `C:\Users\gmcdowell\Documents\VOP\Exports\FVoT\probe_0923_1239\anno_pass_variants_probe\__Elevation_CropActive_19293413.anno_pass_variants.json`.

Model pass: success=True, failure_reason=None, achieved dpi=149.99, frame_px=[5164, 1267].
Model re-export after the variants: **unchanged** -- the control held and the post-variant hash matches

**The crop.** Authored crop active: yes; UV [-104.01772534812247, -4.810097715203943, 171.41561007680002, 62.7311886414472].
- V0 widens it to frame B by (ft): left +0.00 / right -0.00 / top +0.04 / bottom +0.00
- the MODEL pass sets it to crop A, which differs from the authored crop by (ft): left +0.00 / right -0.00 / top +0.04 / bottom +0.00
- crop shape: 1 loop(s), 4 edge(s), rectangle: yes, oblique edges: 0
- **white-suppression cost**: 31217 ms for 6536 element override(s), against 28521 ms for the whole model pass (ratio 1.09; a LOWER bound on suppression-vs-paint, since production does not time its paint alone)
  - by mechanism (ms): category_override_writes 31004, element_overrides 117, category_override_reads 29, category_collect 7, build_override 4, subcategory_walk 1, link_category_filters 0
  - API calls: category_override_reads 395, category_override_writes 391, element_override_writes 6536, subcategory_lists_read 40
  - `v0_control`: pre-state commit 2 ms; restore = group rollback, 565 ms; post-rollback element-override read-back -- ms (not_applicable)
  - `v7_no_crop` suppression: 31217 ms (category_override_writes 31004, element_overrides 117, category_override_reads 29, category_collect 7, build_override 4, subcategory_walk 1)
  - `v7_no_crop`: pre-state commit 2967 ms; restore = group rollback, 1355 ms; post-rollback element-override read-back 3197 ms (restored)
  - `v8_no_crop_fiducials` suppression: 29613 ms (category_override_writes 29443, element_overrides 85, category_override_reads 22, category_collect 8, subcategory_walk 4, build_override 1)
  - `v8_no_crop_fiducials`: pre-state commit 2239 ms; restore = group rollback, 1495 ms; post-rollback element-override read-back 2800 ms (restored)
  - `v9_registration_marks` suppression: 35569 ms (category_override_writes 35357, element_overrides 119, category_override_reads 19, category_collect 15, subcategory_walk 5, build_override 3)
  - `v9_registration_marks`: pre-state commit 2200 ms; restore = group rollback, 1675 ms; post-rollback element-override read-back 2982 ms (restored)
  - `v10_element_only_white` suppression WITHOUT the category layer: 106 ms (element_overrides 93, build_override 2)
  - `v10_element_only_white`: pre-state commit 2588 ms; restore = group rollback, 1085 ms; post-rollback element-override read-back 2715 ms (restored)
- F2 fiducials: 8852504 (Fascias), 14208030 (Walls); separated 176.8 ft on u and 60.3 ft on v (1278 of 6535 candidates kept, reference authored_crop)

### 0. What the capture reports it actually did

| variant | suppression mode | applied_smooth_edges | display style | probe conclusion | measured its candidate | production success | capture faults | faults read from |
|---|---|---|---|---|---|---|---|---|
| v0_control | hide_categories | not_attempted | FlatColors | RAN | yes | yes | none | combined_record |
| v7_no_crop | external | not_attempted | FlatColors | RAN | yes | yes | none | combined_record |
| v8_no_crop_fiducials | external | no | FlatColors | RAN | yes | yes | none | combined_record |
| v9_registration_marks | external | no | FlatColors | RAN | yes | yes | none | combined_record |
| v10_element_only_white | external | not_attempted | FlatColors | RAN | yes | yes | none | combined_record |

`capture faults` come from the probe's combined record (`annotation_pass.capture_faults`) in preference to the annotation sidecar, and the last column says which was used. The sidecar is the fallback because production wrote it before computing its own faults until `dcb4e65`, so an older capture's file carries none however faulted it was. `UNKNOWN (not recorded)` means neither source had the field -- which is not the same fact as `none`.

`applied_smooth_edges` is four-valued: `not_attempted` (the pass was not asked), `read_failed`, `unchanged (failed)`, or `False` (**confirmed off**). A V2/V3 row that is anything but `False` did NOT have anti-aliasing disabled and therefore measures the same behaviour as the variant above it -- the probe reports that as `DID_NOT_MEASURE` rather than `RAN`.

### 1. Image size vs `frame_px`, both axes

| variant | image | frame_px | dw | dh | equal | sidecar dim_check |
|---|---|---|---|---|---|---|
| v0_control | 5164x1709 | 5164x1267 | 0 | 442 | no | pass |
| v7_no_crop | 5164x1708 | 5164x1267 | 0 | 441 | no | pass |
| v8_no_crop_fiducials | 5164x1708 | 5164x1267 | 0 | 441 | no | pass |
| v9_registration_marks | 5164x1708 | 5164x1267 | 0 | 441 | no | pass |
| v10_element_only_white | 5164x1708 | 5164x1267 | 0 | 441 | no | pass |

`dw`/`dh` are image minus `frame_px`. `dim_check` is the sidecar's own verdict, which inspects the requested axis only (finding F4) -- so a nonzero `dh` beside `dim_check=pass` is exactly the case F4 names.

### 2. Registration fit (measured from the pixels)

| variant | samples | px/ft u | px/ft v | frame px/ft | offset at (u0,v1) px | residual med/max px | v axis |
|---|---|---|---|---|---|---|---|
| v0_control | NOT FITTED | -- | -- | -- | -- | -- | 0 matched sample(s); a linear fit needs at least 2 |
| v7_no_crop | NOT FITTED | -- | -- | -- | -- | -- | 0 matched sample(s); a linear fit needs at least 2 |
| v8_no_crop_fiducials | NOT FITTED | -- | -- | -- | -- | -- | 0 matched sample(s); a linear fit needs at least 2 |
| v9_registration_marks | 10 pairs | 17.635 | 17.631 | 18.749 | (142.3, 258.3) | 0.12 / 0.59 | negative (expected) |
| v10_element_only_white | NOT FITTED | -- | -- | -- | -- | -- | 0 matched sample(s); a linear fit needs at least 2 |

- `v0_control` frame: registration.rendered_uv
- `v7_no_crop` frame: the AUTHORED crop (crop_mode untouched)
- `v8_no_crop_fiducials` frame: the AUTHORED crop (crop_mode untouched)
- `v9_registration_marks` frame: the AUTHORED crop (crop_mode untouched)
- `v10_element_only_white` frame: the AUTHORED crop (crop_mode untouched)

#### 2b. Fitted per-side margin (how much bigger the render is than the frame)

| variant | left | right | top | bottom |  |
|---|---|---|---|---|---|
| v0_control | -- | -- | -- | -- | NOT FITTED |
| v7_no_crop | -- | -- | -- | -- | NOT FITTED |
| v8_no_crop_fiducials | -- | -- | -- | -- | NOT FITTED |
| v9_registration_marks | 8.07 ft / 1.009 in | 9.32 ft / 1.165 in | 14.65 ft / 1.832 in | 14.68 ft / 1.835 in |  |
| v10_element_only_white | -- | -- | -- | -- | NOT FITTED |

#### 2c. The fit's own premise: does the ink sit where the bbox says?

The fit matches an element's exact-colour centroid against the centre of its recorded `bbox_uv`. **Those coincide only when the ink fills the box**, which real text, a tag with a leader or a dimension need not do. A displacement that is the SAME for every element is absorbed into the fitted intercept, so it leaves the residuals in 2 clean while shifting every margin in 2b by exactly that amount; one that varies with size or position corrupts the scale instead and does inflate the residuals. Both are surfaced here. No tolerance is applied -- what is close enough for a margin figure is your call.

| variant | ink-bbox px/ft u | ink-bbox px/ft v | px/ft u delta | px/ft v delta | margin delta ft (l/r/t/b) |  |
|---|---|---|---|---|---|---|
| v0_control | -- | -- | -- | -- | -- | one of the two anchors did not fit (unavailable / unavailable) |
| v7_no_crop | -- | -- | -- | -- | -- | one of the two anchors did not fit (unavailable / unavailable) |
| v8_no_crop_fiducials | -- | -- | -- | -- | -- | one of the two anchors did not fit (unavailable / unavailable) |
| v9_registration_marks | 17.635 | 17.631 | 0.0000 | 0.0000 | 0.000 / 0.000 / 0.000 / 0.000 |  |
| v10_element_only_white | -- | -- | -- | -- | -- | one of the two anchors did not fit (unavailable / unavailable) |

The second anchor is the centre of the ink's own bounding box rather than its centroid. The two are identical when the ink fills its bbox and diverge when it does not, so a row of ~0 deltas says the anchor choice does not matter on this capture and the margins above do not rest on the premise.

A UNIFORM displacement -- every element's ink sitting the same distance off its box centre -- is NOT recoverable from a capture: nothing distinguishes it from the whole render sitting that far over, which is why it is the dangerous case. What IS recoverable is whether such a displacement is POSSIBLE, and by how much: ink that spans its whole box cannot be off-centre in it. The table below bounds it.

- `v0_control` samples: matched 0, no bbox 1, colour absent 0, clipped at an image edge 0.
- `v7_no_crop` samples: matched 0, no bbox 1, colour absent 0, clipped at an image edge 0.
- `v8_no_crop_fiducials` samples: matched 0, no bbox 1, colour absent 0, clipped at an image edge 0.
- `v9_registration_marks` samples: matched 10, no bbox 1, colour absent 2, clipped at an image edge 0.
- `v10_element_only_white` samples: matched 0, no bbox 1, colour absent 0, clipped at an image edge 0.

### 3. How far recorded annotation bboxes reach past the rendered frame

| variant | left | right | top | bottom | bboxes outside |  |
|---|---|---|---|---|---|---|
| v0_control | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0 of 0 |  |
| v7_no_crop | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0 of 0 |  |
| v8_no_crop_fiducials | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0 of 0 |  |
| v9_registration_marks | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0 of 12 |  |
| v10_element_only_white | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0 of 0 |  |

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

#### `v9_registration_marks`

Ink test threshold >2% non-white, placed through the fitted mapping from measurement (2).

| category | assigned | painted | colour present | bbox | ink tested | ink present | bbox off image | ink untested |
|---|---|---|---|---|---|---|---|---|
| <no category> | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| Lines | 12 | 12 | 10 | 12 | 12 | 10 | 0 | 0 |

#### `v10_element_only_white`

Ink column UNMEASURED: no fitted mapping (measurement 2 is unavailable: 0 matched sample(s); a linear fit needs at least 2); the sidecar's mapping is deliberately NOT substituted

| category | assigned | painted | colour present | bbox | ink tested | ink present | bbox off image | ink untested |
|---|---|---|---|---|---|---|---|---|
| <no category> | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |

### 5. Non-white, non-palette pixels

| variant | total | white | palette | off-palette | off-palette frac | blend | grey | other | distinct | tally capped |
|---|---|---|---|---|---|---|---|---|---|---|
| v0_control | 8825276 | 8813199 | 0 | 12077 | 0.00137 | 0 | 11497 | 580 | 29 | no |
| v7_no_crop | 8820112 | 8818772 | 0 | 1340 | 0.00015 | 0 | 1340 | 0 | 23 | no |
| v8_no_crop_fiducials | 8820112 | 8818251 | 0 | 1340 | 0.00015 | 0 | 1340 | 0 | 23 | no |
| v9_registration_marks | 8820112 | 8817350 | 901 | 1340 | 0.00015 | 0 | 1340 | 0 | 23 | no |
| v10_element_only_white | 8820112 | 8818772 | 0 | 1340 | 0.00015 | 0 | 1340 | 0 | 23 | no |

`blend` is within 3 RGB units of the segment between some palette colour and white. `grey` is all three channels within 3 of each other. The two OVERLAP -- a grey pixel is also collinear with white -- and blend is tested first; the `blend also grey` count below states what that ordering costs.

- `v0_control` blend also grey: 0 px.
- `v7_no_crop` blend also grey: 0 px.
- `v8_no_crop_fiducials` blend also grey: 0 px.
- `v9_registration_marks` blend also grey: 0 px.
- `v10_element_only_white` blend also grey: 0 px.

#### `v0_control` top 10 off-palette colours

| rgb | pixels | class | also grey |
|---|---|---|---|
| [0, 0, 0] | 10518 | grey | yes |
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

#### `v10_element_only_white` top 10 off-palette colours

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
| v0_control | 330bcaf4ec45e592 | 330bcaf4ec45e592 | yes |
| v7_no_crop | 330bcaf4ec45e592 | 330bcaf4ec45e592 | yes |
| v8_no_crop_fiducials | 330bcaf4ec45e592 | 330bcaf4ec45e592 | yes |
| v9_registration_marks | 330bcaf4ec45e592 | 330bcaf4ec45e592 | yes |
| v10_element_only_white | 330bcaf4ec45e592 | 330bcaf4ec45e592 | yes |

The model pass runs ONCE per view, so this column detects a variant CLOBBERING the model artifact -- a path collision, a stray write. Whether a variant changed how the model pass RENDERS is the probe's own `model_reexport_after_variants` verdict, quoted at the top of this view's section, which has its own repeatability control.

### 7. F1 -- the crop boundary, recovered from the pixels

Q1: does the exported image contain the crop boundary, and does its recovered position match `view.CropBox`? A boundary is a band of rows (or columns) each holding a straight run of non-white, non-palette, non-fiducial pixels of >= 25% of the image, closing into one rectangle (or matching the crop shape's levels). `NOT FOUND` on a V8 row is the answer that ImageExportOptions does not draw it.

| variant | probe turned it on | boundary | row/col bands | px/ft u | px/ft v | lattice px/ft | u/v isotropy | boundary px rects (subtract these) |  |
|---|---|---|---|---|---|---|---|---|---|
| v0_control | no | NOT_FOUND | 0 / 3 | -- | -- | 18.7486 | -- | -- | 0 row band(s) and 3 column band(s) long enough to be a crop edge, but no four of them close into one rectangle |
| v7_no_crop | no | NOT_FOUND | 0 / 0 | -- | -- | 18.7486 | -- | -- | no row or column holds a straight non-palette run of >= 25% of the image: the crop boundary is NOT in this capture |
| v8_no_crop_fiducials | yes | NOT_FOUND | 0 / 0 | -- | -- | 18.7486 | -- | -- | no row or column holds a straight non-palette run of >= 25% of the image: the crop boundary is NOT in this capture |
| v9_registration_marks | yes | NOT_FOUND | 0 / 0 | -- | -- | 18.7486 | -- | -- | no row or column holds a straight non-palette run of >= 25% of the image: the crop boundary is NOT in this capture |
| v10_element_only_white | no | NOT_FOUND | 0 / 0 | -- | -- | 18.7486 | -- | -- | no row or column holds a straight non-palette run of >= 25% of the image: the crop boundary is NOT in this capture |

`isotropy` of 1 means the boundary's pixel rectangle has the crop's own aspect -- the recovered position is the crop's shape at one uniform scale. `lattice px/ft` is the model capture's (1 / achieved fpp): an untouched capture that rendered exactly the authored crop at the requested count would match it.

- `v8_no_crop_fiducials` MODEL capture (boundary drawn at crop A [-104.01772534812247, -4.810097715203943, 171.4156100768, 62.76814318010526]): NOT_FOUND -- no row or column holds a straight non-palette run of >= 25% of the image: the crop boundary is NOT in this capture
- `v9_registration_marks` MODEL capture (boundary drawn at crop A [-104.01772534812247, -4.810097715203943, 171.4156100768, 62.76814318010526]): NOT_FOUND -- no row or column holds a straight non-palette run of >= 25% of the image: the crop boundary is NOT in this capture

### 8. F2 -- the fiducial pair

| variant | element | colour | found | pixels | ink px bbox | clipped | recorded rect_uv |
|---|---|---|---|---|---|---|---|
| v8_no_crop_fiducials | 8852504 | [251, 11, 139] | yes | 368 | [1246.0, 341.0, 1429.0, 342.0] | no | [-41.51502225001987, 57.58333333333331, -31.015022250016784, 60.916666666666664] |
| v8_no_crop_fiducials | 14208030 | [11, 139, 251] | yes | 153 | [4452.0, 1376.0, 4459.0, 1395.0] | no | [140.37039441664712, -1.737695094585085, 140.7870610833138, -0.4166666666666676] |
| v9_registration_marks | 8852504 | [251, 11, 139] | yes | 368 | [1246.0, 341.0, 1429.0, 342.0] | no | [-41.51502225001987, 57.58333333333331, -31.015022250016784, 60.916666666666664] |
| v9_registration_marks | 14208030 | [11, 139, 251] | yes | 153 | [4452.0, 1376.0, 4459.0, 1395.0] | no | [140.37039441664712, -1.737695094585085, 140.7870610833138, -0.4166666666666676] |

| fit | px/ft u | px/ft v | u/v isotropy | residual max px u / v |  |
|---|---|---|---|---|---|
| `v8_no_crop_fiducials` F2 (recorded bbox) | 17.6312 | 17.2797 | 1.02034 | 0.58 / 28.58 |  |
| `v8_no_crop_fiducials` F2 (model-anchored) | 17.6346 | 17.6417 | 0.99960 | 0.30 / 0.53 |  |
| `v9_registration_marks` F2 (recorded bbox) | 17.6312 | 17.2797 | 1.02034 | 0.58 / 28.58 |  |
| `v9_registration_marks` F2 (model-anchored) | 17.6346 | 17.6417 | 0.99960 | 0.30 / 0.53 |  |

- `v8_no_crop_fiducials` fiducial 8852504: 368 px in the annotation capture, 195 px in the model capture
- `v8_no_crop_fiducials` fiducial 14208030: 153 px in the annotation capture, 190 px in the model capture
- `v9_registration_marks` fiducial 8852504: 368 px in the annotation capture, 195 px in the model capture
- `v9_registration_marks` fiducial 14208030: 153 px in the annotation capture, 190 px in the model capture

F2 fits each fiducial's INK EDGES against its recorded extent: four points per axis for the pair, so it has a residual. The recorded extent is a projected 3-D bbox, which can be looser than the element; the model-anchored row replaces it with the element's drawn extent in the model capture, at the cost of assuming the model capture registers.

### 9. F1 vs F2 vs F3 vs the bbox fit

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
| v9_registration_marks | F1_vs_F2 | -- | -- | -- | one of the two mappings is not available |
| v9_registration_marks | F1_vs_bbox_fit | -- | -- | -- | one of the two mappings is not available |
| v9_registration_marks | F2_vs_bbox_fit | -0.0040 | -0.3511 | 24.28 |  |
| v9_registration_marks | F3_vs_F2 | +0.0034 | +0.3409 | 23.69 |  |
| v9_registration_marks | F3_vs_bbox_fit | -0.0006 | -0.0103 | 0.60 |  |
| v10_element_only_white | F1_vs_F2 | -- | -- | -- | one of the two mappings is not available |
| v10_element_only_white | F1_vs_bbox_fit | -- | -- | -- | one of the two mappings is not available |
| v10_element_only_white | F2_vs_bbox_fit | -- | -- | -- | one of the two mappings is not available |

### 10. Datum extents: ink vs the recorded (authored-crop) bbox

Q3: do datum extents in the capture match the drawing? Positive means the ink reaches FURTHER than the bbox recorded before the capture touched the view. Under V0 the crop was widened to B, which lengthens datums; under V7/V8 it was not.

| variant | datum | category | map | length delta ft | paper in | per side ft l/r/t/b |  |
|---|---|---|---|---|---|---|---|
| v0_control | -- | -- | -- | -- | -- | -- | no mapping from pixels to UV |
| v7_no_crop | -- | -- | -- | -- | -- | -- | no mapping from pixels to UV |
| v10_element_only_white | -- | -- | -- | -- | -- | -- | no mapping from pixels to UV |

Premise: `get_BoundingBox(view)` of a datum is its drawn extent in the view (UNCONFIRMED).

### 11. Model-ink residue

Q4: does membership suppression with subcategories leave any model ink? Off-palette pixels outside the fiducials and outside any recovered boundary band. Counted, not attributed: an annotation the pass could not paint lands here too.

| variant | suppression | category layer | off-palette | fiducial px | boundary bands excluded | off-palette outside boundary |
|---|---|---|---|---|---|---|
| v0_control | hide_categories | -- | 12077 | 0 | 0 | 12077 |
| v7_no_crop | external | yes | 1340 | 0 | 0 | 1340 |
| v8_no_crop_fiducials | external | yes | 1340 | 521 | 0 | 1340 |
| v9_registration_marks | external | yes | 1340 | 521 | 0 | 1340 |
| v10_element_only_white | external | no | 1340 | 0 | 0 | 1340 |

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
- `v9_registration_marks` annotation: 901 mark pixels in 10 rect(s) to subtract in post
- `v9_registration_marks` model: tick mid_bottom_v not found -- no component of the shared colour in that corner and orientation
- `v9_registration_marks` model: tick mid_top_v not found -- no component of the shared colour in that corner and orientation
- `v9_registration_marks` model: 960 mark pixels in 10 rect(s) to subtract in post

### Overlays

The gate is measurement 1 only: image size == `frame_px`. IT IS A SIZE GATE AND NOTHING MORE. A capture can be exactly `frame_px` pixels and still have its CONTENT drawn at a different scale or origin -- which is finding F1 -- and `capture_overlay.py` maps UV through the SIDECAR's numbers, so on such a capture its boxes will not land on the ink. Measurement 2's fitted px/ft is echoed beside each line so that is visible here rather than only three sections up.

- `v0_control`: OVERLAY WITHHELD (measurement 2 did not fit: 0 matched sample(s); a linear fit needs at least 2). measurement 1 shows image 5164x1709 against frame_px 5164x1267; the overlay is not run on a capture whose rendered region is not the frame its sidecar describes
- `v7_no_crop`: OVERLAY WITHHELD (measurement 2 did not fit: 0 matched sample(s); a linear fit needs at least 2). measurement 1 shows image 5164x1708 against frame_px 5164x1267; the overlay is not run on a capture whose rendered region is not the frame its sidecar describes
- `v8_no_crop_fiducials`: OVERLAY WITHHELD (measurement 2 did not fit: 0 matched sample(s); a linear fit needs at least 2). measurement 1 shows image 5164x1708 against frame_px 5164x1267; the overlay is not run on a capture whose rendered region is not the frame its sidecar describes
- `v9_registration_marks`: OVERLAY WITHHELD (fitted 17.635 / 17.631 px/ft against the frame's 18.749). measurement 1 shows image 5164x1708 against frame_px 5164x1267; the overlay is not run on a capture whose rendered region is not the frame its sidecar describes
- `v10_element_only_white`: OVERLAY WITHHELD (measurement 2 did not fit: 0 matched sample(s); a linear fit needs at least 2). measurement 1 shows image 5164x1708 against frame_px 5164x1267; the overlay is not run on a capture whose rendered region is not the frame its sidecar describes

## View __Plan_CropInActive (id 19291097, 1, scale 1:96)

Probe `stage_a_anno_pass_variants` version 2026-09-23.2; combined report `C:\Users\gmcdowell\Documents\VOP\Exports\FVoT\probe_0923_1239\anno_pass_variants_probe\__Plan_CropInActive_19291097.anno_pass_variants.json`.

Model pass: success=True, failure_reason=None, achieved dpi=150.01, frame_px=[5885, 2940].
Model re-export after the variants: **unchanged** -- the control held and the post-variant hash matches

**The crop.** Authored crop active: no; UV [-91.46926561670251, 2250.749394451395, 165.49485231475583, 2337.3310543423245].
- V0 widens it to frame B by (ft): left +28.41 / right +28.47 / top +45.62 / bottom +24.58
- the MODEL pass sets it to crop A, which differs from the authored crop by (ft): left +28.41 / right +28.47 / top +45.62 / bottom +24.58 -- and on this crop-INACTIVE view the model pass ACTIVATES a crop
- crop shape: 1 loop(s), 4 edge(s), rectangle: yes, oblique edges: 0
- **white-suppression cost**: 26815 ms for 4833 element override(s), against 36917 ms for the whole model pass (ratio 0.73; a LOWER bound on suppression-vs-paint, since production does not time its paint alone)
  - by mechanism (ms): category_override_writes 26642, element_overrides 75, category_override_reads 28, category_collect 7, build_override 4, subcategory_walk 2, link_category_filters 0
  - API calls: category_override_reads 396, category_override_writes 393, element_override_writes 4833, subcategory_lists_read 35
  - `v0_control`: pre-state commit 3 ms; restore = group rollback, 2159 ms; post-rollback element-override read-back -- ms (not_applicable)
  - `v7_no_crop` suppression: 26815 ms (category_override_writes 26642, element_overrides 75, category_override_reads 28, category_collect 7, build_override 4, subcategory_walk 2)
  - `v7_no_crop`: pre-state commit 5108 ms; restore = group rollback, 3189 ms; post-rollback element-override read-back 1890 ms (restored)
  - `v8_no_crop_fiducials` suppression: 23038 ms (category_override_writes 22876, element_overrides 71, category_override_reads 34, category_collect 7, build_override 4, subcategory_walk 3)
  - `v8_no_crop_fiducials`: pre-state commit 3600 ms; restore = group rollback, 3319 ms; post-rollback element-override read-back 2068 ms (restored)
  - `v9_registration_marks` suppression: 26726 ms (category_override_writes 26502, element_overrides 108, category_override_reads 29, category_collect 21, build_override 5, subcategory_walk 4)
  - `v9_registration_marks`: pre-state commit 4196 ms; restore = group rollback, 3486 ms; post-rollback element-override read-back 2204 ms (restored)
  - `v10_element_only_white` suppression WITHOUT the category layer: 95 ms (element_overrides 82, build_override 4)
  - `v10_element_only_white`: pre-state commit 4138 ms; restore = group rollback, 3287 ms; post-rollback element-override read-back 2496 ms (restored)
- F2 fiducials: 7425328 (Elevations), 3383634 (Views); separated 126.5 ft on u and 112.1 ft on v (2319 of 4833 candidates kept, reference model_crop_a)

### 0. What the capture reports it actually did

| variant | suppression mode | applied_smooth_edges | display style | probe conclusion | measured its candidate | production success | capture faults | faults read from |
|---|---|---|---|---|---|---|---|---|
| v0_control | hide_categories | not_attempted | FlatColors | RAN | yes | yes | none | combined_record |
| v7_no_crop | external | not_attempted | FlatColors | CAPTURE_FAILED | yes | no | annotation_lattice_mismatch | combined_record |
| v8_no_crop_fiducials | external | no | FlatColors | CAPTURE_FAILED | yes | no | annotation_lattice_mismatch | combined_record |
| v9_registration_marks | external | no | FlatColors | CAPTURE_FAILED | yes | no | annotation_lattice_mismatch | combined_record |
| v10_element_only_white | external | not_attempted | FlatColors | CAPTURE_FAILED | yes | no | annotation_lattice_mismatch | combined_record |

`capture faults` come from the probe's combined record (`annotation_pass.capture_faults`) in preference to the annotation sidecar, and the last column says which was used. The sidecar is the fallback because production wrote it before computing its own faults until `dcb4e65`, so an older capture's file carries none however faulted it was. `UNKNOWN (not recorded)` means neither source had the field -- which is not the same fact as `none`.

`applied_smooth_edges` is four-valued: `not_attempted` (the pass was not asked), `read_failed`, `unchanged (failed)`, or `False` (**confirmed off**). A V2/V3 row that is anything but `False` did NOT have anti-aliasing disabled and therefore measures the same behaviour as the variant above it -- the probe reports that as `DID_NOT_MEASURE` rather than `RAN`.

### 1. Image size vs `frame_px`, both axes

| variant | image | frame_px | dw | dh | equal | sidecar dim_check |
|---|---|---|---|---|---|---|
| v0_control | 5885x2963 | 5885x2940 | 0 | 23 | no | pass |
| v7_no_crop | 2942x6986 | 5885x2940 | -2943 | 4046 | no | pass |
| v8_no_crop_fiducials | 2942x6986 | 5885x2940 | -2943 | 4046 | no | pass |
| v9_registration_marks | 2942x6986 | 5885x2940 | -2943 | 4046 | no | pass |
| v10_element_only_white | 2942x6986 | 5885x2940 | -2943 | 4046 | no | pass |

`dw`/`dh` are image minus `frame_px`. `dim_check` is the sidecar's own verdict, which inspects the requested axis only (finding F4) -- so a nonzero `dh` beside `dim_check=pass` is exactly the case F4 names.

### 2. Registration fit (measured from the pixels)

| variant | samples | px/ft u | px/ft v | frame px/ft | offset at (u0,v1) px | residual med/max px | v axis |
|---|---|---|---|---|---|---|---|
| v0_control | 27 pairs | 18.743 | 18.617 | 18.751 | (21.1, 34.0) | 19.67 / 269.12 | negative (expected) |
| v7_no_crop | 283 pairs | 2.906 | 2.984 | -- | (--, --) | 2.87 / 34.80 | negative (expected) |
| v8_no_crop_fiducials | 283 pairs | 2.906 | 2.984 | -- | (--, --) | 2.87 / 34.80 | negative (expected) |
| v9_registration_marks | 295 pairs | 2.905 | 2.965 | -- | (--, --) | 2.50 / 35.54 | negative (expected) |
| v10_element_only_white | 283 pairs | 2.906 | 2.984 | -- | (--, --) | 2.87 / 34.80 | negative (expected) |

- `v0_control` frame: registration.rendered_uv

#### 2b. Fitted per-side margin (how much bigger the render is than the frame)

| variant | left | right | top | bottom |  |
|---|---|---|---|---|---|
| v0_control | 1.12 ft / 0.141 in | -0.99 ft / -0.124 in | 1.83 ft / 0.228 in | 0.54 ft / 0.068 in |  |
| v7_no_crop | -- | -- | -- | -- | crop_mode 'untouched' and no ACTIVE authored crop was recorded, so there is no rectangle to measure margins against; the scale is still fitted |
| v8_no_crop_fiducials | -- | -- | -- | -- | crop_mode 'untouched' and no ACTIVE authored crop was recorded, so there is no rectangle to measure margins against; the scale is still fitted |
| v9_registration_marks | -- | -- | -- | -- | crop_mode 'untouched' and no ACTIVE authored crop was recorded, so there is no rectangle to measure margins against; the scale is still fitted |
| v10_element_only_white | -- | -- | -- | -- | crop_mode 'untouched' and no ACTIVE authored crop was recorded, so there is no rectangle to measure margins against; the scale is still fitted |

#### 2c. The fit's own premise: does the ink sit where the bbox says?

The fit matches an element's exact-colour centroid against the centre of its recorded `bbox_uv`. **Those coincide only when the ink fills the box**, which real text, a tag with a leader or a dimension need not do. A displacement that is the SAME for every element is absorbed into the fitted intercept, so it leaves the residuals in 2 clean while shifting every margin in 2b by exactly that amount; one that varies with size or position corrupts the scale instead and does inflate the residuals. Both are surfaced here. No tolerance is applied -- what is close enough for a margin figure is your call.

| variant | ink-bbox px/ft u | ink-bbox px/ft v | px/ft u delta | px/ft v delta | margin delta ft (l/r/t/b) |  |
|---|---|---|---|---|---|---|
| v0_control | 18.745 | 18.738 | -0.0020 | -0.1214 | 1.092 / -1.059 / 0.575 / 0.456 |  |
| v7_no_crop | 2.899 | 2.909 | 0.0070 | 0.0753 | -- | no margins: at least one fit is frameless (no rendered rectangle to measure them from) |
| v8_no_crop_fiducials | 2.899 | 2.909 | 0.0070 | 0.0753 | -- | no margins: at least one fit is frameless (no rendered rectangle to measure them from) |
| v9_registration_marks | 2.899 | 2.906 | 0.0061 | 0.0587 | -- | no margins: at least one fit is frameless (no rendered rectangle to measure them from) |
| v10_element_only_white | 2.899 | 2.909 | 0.0070 | 0.0753 | -- | no margins: at least one fit is frameless (no rendered rectangle to measure them from) |

The second anchor is the centre of the ink's own bounding box rather than its centroid. The two are identical when the ink fills its bbox and diverge when it does not, so a row of ~0 deltas says the anchor choice does not matter on this capture and the margins above do not rest on the premise.

A UNIFORM displacement -- every element's ink sitting the same distance off its box centre -- is NOT recoverable from a capture: nothing distinguishes it from the whole render sitting that far over, which is why it is the dangerous case. What IS recoverable is whether such a displacement is POSSIBLE, and by how much: ink that spans its whole box cannot be off-centre in it. The table below bounds it.

##### `v0_control` does the ink fill its recorded bbox?

| category | n | ink span / bbox u | ink span / bbox v | solidity | possible offset u px | possible offset v px |
|---|---|---|---|---|---|---|
| Dimensions | 2 | 0.954 (0.907 .. 1.000) | 0.952 (0.897 .. 1.007) | 0.056 (0.052 .. 0.060) | 1.0 (0.0 .. 1.9) | 0.8 (0.0 .. 1.6) |
| Grids | 18 | 1.014 (1.000 .. 1.014) | 1.007 (1.007 .. 1.021) | 0.015 (0.010 .. 0.016) | 0.0 (0.0 .. 0.0) | 0.0 (0.0 .. 0.0) |
| Lines | 1 | 1.145 (1.145 .. 1.145) | 1.153 (1.153 .. 1.153) | 0.143 (0.143 .. 0.143) | 0.0 (0.0 .. 0.0) | 0.0 (0.0 .. 0.0) |
| Revision Cloud Tags | 1 | 0.928 (0.928 .. 0.928) | 0.902 (0.902 .. 0.902) | 0.088 (0.088 .. 0.088) | 1.9 (1.9 .. 1.9) | 2.7 (2.7 .. 2.7) |
| Revision Clouds | 1 | 1.015 (1.015 .. 1.015) | 1.014 (1.014 .. 1.014) | 0.064 (0.064 .. 0.064) | 0.0 (0.0 .. 0.0) | 0.0 (0.0 .. 0.0) |
| Views | 3 | 0.997 (0.995 .. 1.002) | 1.012 (1.002 .. 1.014) | 0.016 (0.015 .. 0.018) | 0.8 (0.0 .. 0.9) | 0.0 (0.0 .. 0.0) |

`ink span / bbox` of 1.0 means the ink reaches both edges of its recorded box, so it cannot be off-centre in it and the margins above are bounded. `solidity` is ink pixels over ink bbox area: below 1 the ink is not a solid rectangle -- a glyph run, an L, a leader -- so its centroid can also sit away from its own bbox centre. `possible offset` is how far, in pixels, a margin in 2b could be wrong because of this.

##### `v7_no_crop` does the ink fill its recorded bbox?

| category | n | ink span / bbox u | ink span / bbox v | solidity | possible offset u px | possible offset v px |
|---|---|---|---|---|---|---|
| Dimensions | 135 | 0.964 (0.187 .. 1.038) | 0.969 (0.309 .. 1.199) | 0.146 (0.015 .. 0.400) | 0.4 (0.0 .. 26.0) | 0.6 (0.0 .. 24.9) |
| Door Tags | 25 | 1.010 (0.541 .. 1.376) | 1.117 (0.842 .. 1.340) | 0.267 (0.125 .. 0.333) | 0.0 (0.0 .. 2.1) | 0.0 (0.0 .. 0.7) |
| Grids | 19 | 0.998 (0.860 .. 1.118) | 0.975 (0.975 .. 1.089) | 0.060 (0.049 .. 0.074) | 0.8 (0.0 .. 1.0) | 4.8 (0.0 .. 4.8) |
| Revision Cloud Tags | 1 | 0.838 (0.838 .. 0.838) | 0.574 (0.574 .. 0.574) | 0.429 (0.429 .. 0.429) | 0.7 (0.7 .. 0.7) | 1.9 (1.9 .. 1.9) |
| Revision Clouds | 1 | 1.030 (1.030 .. 1.030) | 1.006 (1.006 .. 1.006) | 0.136 (0.136 .. 0.136) | 0.0 (0.0 .. 0.0) | 0.0 (0.0 .. 0.0) |
| Room Tags | 18 | 1.022 (0.885 .. 1.090) | 0.944 (0.944 .. 0.973) | 0.298 (0.194 .. 0.315) | 0.0 (0.0 .. 1.2) | 0.3 (0.2 .. 0.3) |
| Views | 3 | 0.940 (0.922 .. 1.008) | 0.990 (0.987 .. 1.005) | 0.046 (0.041 .. 0.052) | 2.3 (0.0 .. 2.7) | 0.2 (0.0 .. 0.3) |
| Wall Tags | 53 | 1.057 (0.802 .. 1.300) | 1.097 (0.954 .. 1.279) | 0.198 (0.127 .. 0.592) | 0.0 (0.0 .. 1.2) | 0.0 (0.0 .. 0.2) |
| Window Tags | 27 | 0.580 (0.386 .. 0.824) | 0.847 (0.847 .. 0.989) | 0.400 (0.271 .. 0.514) | 2.2 (0.9 .. 3.2) | 0.5 (0.0 .. 0.5) |

`ink span / bbox` of 1.0 means the ink reaches both edges of its recorded box, so it cannot be off-centre in it and the margins above are bounded. `solidity` is ink pixels over ink bbox area: below 1 the ink is not a solid rectangle -- a glyph run, an L, a leader -- so its centroid can also sit away from its own bbox centre. `possible offset` is how far, in pixels, a margin in 2b could be wrong because of this.

##### `v8_no_crop_fiducials` does the ink fill its recorded bbox?

| category | n | ink span / bbox u | ink span / bbox v | solidity | possible offset u px | possible offset v px |
|---|---|---|---|---|---|---|
| Dimensions | 135 | 0.964 (0.187 .. 1.038) | 0.969 (0.309 .. 1.199) | 0.146 (0.015 .. 0.400) | 0.4 (0.0 .. 26.0) | 0.6 (0.0 .. 24.9) |
| Door Tags | 25 | 1.010 (0.541 .. 1.376) | 1.117 (0.842 .. 1.340) | 0.267 (0.125 .. 0.333) | 0.0 (0.0 .. 2.1) | 0.0 (0.0 .. 0.7) |
| Grids | 19 | 0.998 (0.860 .. 1.118) | 0.975 (0.975 .. 1.089) | 0.060 (0.049 .. 0.074) | 0.8 (0.0 .. 1.0) | 4.8 (0.0 .. 4.8) |
| Revision Cloud Tags | 1 | 0.838 (0.838 .. 0.838) | 0.574 (0.574 .. 0.574) | 0.429 (0.429 .. 0.429) | 0.7 (0.7 .. 0.7) | 1.9 (1.9 .. 1.9) |
| Revision Clouds | 1 | 1.030 (1.030 .. 1.030) | 1.006 (1.006 .. 1.006) | 0.136 (0.136 .. 0.136) | 0.0 (0.0 .. 0.0) | 0.0 (0.0 .. 0.0) |
| Room Tags | 18 | 1.022 (0.885 .. 1.090) | 0.944 (0.944 .. 0.973) | 0.298 (0.194 .. 0.315) | 0.0 (0.0 .. 1.2) | 0.3 (0.2 .. 0.3) |
| Views | 3 | 0.940 (0.922 .. 1.008) | 0.990 (0.987 .. 1.005) | 0.046 (0.041 .. 0.052) | 2.3 (0.0 .. 2.7) | 0.2 (0.0 .. 0.3) |
| Wall Tags | 53 | 1.057 (0.802 .. 1.300) | 1.097 (0.954 .. 1.279) | 0.198 (0.127 .. 0.592) | 0.0 (0.0 .. 1.2) | 0.0 (0.0 .. 0.2) |
| Window Tags | 27 | 0.580 (0.386 .. 0.824) | 0.847 (0.847 .. 0.989) | 0.400 (0.271 .. 0.514) | 2.2 (0.9 .. 3.2) | 0.5 (0.0 .. 0.5) |

`ink span / bbox` of 1.0 means the ink reaches both edges of its recorded box, so it cannot be off-centre in it and the margins above are bounded. `solidity` is ink pixels over ink bbox area: below 1 the ink is not a solid rectangle -- a glyph run, an L, a leader -- so its centroid can also sit away from its own bbox centre. `possible offset` is how far, in pixels, a margin in 2b could be wrong because of this.

##### `v9_registration_marks` does the ink fill its recorded bbox?

| category | n | ink span / bbox u | ink span / bbox v | solidity | possible offset u px | possible offset v px |
|---|---|---|---|---|---|---|
| Dimensions | 135 | 0.965 (0.187 .. 1.039) | 0.976 (0.311 .. 1.206) | 0.146 (0.015 .. 0.400) | 0.4 (0.0 .. 26.0) | 0.5 (0.0 .. 24.6) |
| Door Tags | 25 | 1.011 (0.541 .. 1.377) | 1.124 (0.848 .. 1.349) | 0.267 (0.125 .. 0.333) | 0.0 (0.0 .. 2.1) | 0.0 (0.0 .. 0.7) |
| Grids | 19 | 0.998 (0.861 .. 1.119) | 0.981 (0.981 .. 1.096) | 0.060 (0.049 .. 0.074) | 0.8 (0.0 .. 0.9) | 3.5 (0.0 .. 3.5) |
| Revision Cloud Tags | 1 | 0.838 (0.838 .. 0.838) | 0.578 (0.578 .. 0.578) | 0.429 (0.429 .. 0.429) | 0.7 (0.7 .. 0.7) | 1.8 (1.8 .. 1.8) |
| Revision Clouds | 1 | 1.031 (1.031 .. 1.031) | 1.012 (1.012 .. 1.012) | 0.136 (0.136 .. 0.136) | 0.0 (0.0 .. 0.0) | 0.0 (0.0 .. 0.0) |
| Room Tags | 18 | 1.023 (0.885 .. 1.090) | 0.950 (0.950 .. 0.979) | 0.298 (0.194 .. 0.315) | 0.0 (0.0 .. 1.2) | 0.2 (0.1 .. 0.2) |
| Views | 3 | 0.940 (0.922 .. 1.009) | 0.997 (0.994 .. 1.011) | 0.046 (0.041 .. 0.052) | 2.3 (0.0 .. 2.6) | 0.1 (0.0 .. 0.1) |
| Wall Tags | 53 | 1.057 (0.802 .. 1.301) | 1.104 (0.960 .. 1.288) | 0.198 (0.127 .. 0.592) | 0.0 (0.0 .. 1.2) | 0.0 (0.0 .. 0.2) |
| Window Tags | 27 | 0.580 (0.387 .. 0.824) | 0.853 (0.853 .. 0.995) | 0.400 (0.271 .. 0.514) | 2.2 (0.9 .. 3.2) | 0.5 (0.0 .. 0.5) |

`ink span / bbox` of 1.0 means the ink reaches both edges of its recorded box, so it cannot be off-centre in it and the margins above are bounded. `solidity` is ink pixels over ink bbox area: below 1 the ink is not a solid rectangle -- a glyph run, an L, a leader -- so its centroid can also sit away from its own bbox centre. `possible offset` is how far, in pixels, a margin in 2b could be wrong because of this.

##### `v10_element_only_white` does the ink fill its recorded bbox?

| category | n | ink span / bbox u | ink span / bbox v | solidity | possible offset u px | possible offset v px |
|---|---|---|---|---|---|---|
| Dimensions | 135 | 0.964 (0.187 .. 1.038) | 0.969 (0.309 .. 1.199) | 0.146 (0.015 .. 0.400) | 0.4 (0.0 .. 26.0) | 0.6 (0.0 .. 24.9) |
| Door Tags | 25 | 1.010 (0.541 .. 1.376) | 1.117 (0.842 .. 1.340) | 0.267 (0.125 .. 0.333) | 0.0 (0.0 .. 2.1) | 0.0 (0.0 .. 0.7) |
| Grids | 19 | 0.998 (0.860 .. 1.118) | 0.975 (0.975 .. 1.089) | 0.060 (0.049 .. 0.074) | 0.8 (0.0 .. 1.0) | 4.8 (0.0 .. 4.8) |
| Revision Cloud Tags | 1 | 0.838 (0.838 .. 0.838) | 0.574 (0.574 .. 0.574) | 0.429 (0.429 .. 0.429) | 0.7 (0.7 .. 0.7) | 1.9 (1.9 .. 1.9) |
| Revision Clouds | 1 | 1.030 (1.030 .. 1.030) | 1.006 (1.006 .. 1.006) | 0.136 (0.136 .. 0.136) | 0.0 (0.0 .. 0.0) | 0.0 (0.0 .. 0.0) |
| Room Tags | 18 | 1.022 (0.885 .. 1.090) | 0.944 (0.944 .. 0.973) | 0.298 (0.194 .. 0.315) | 0.0 (0.0 .. 1.2) | 0.3 (0.2 .. 0.3) |
| Views | 3 | 0.940 (0.922 .. 1.008) | 0.990 (0.987 .. 1.005) | 0.046 (0.041 .. 0.052) | 2.3 (0.0 .. 2.7) | 0.2 (0.0 .. 0.3) |
| Wall Tags | 53 | 1.057 (0.802 .. 1.300) | 1.097 (0.954 .. 1.279) | 0.198 (0.127 .. 0.592) | 0.0 (0.0 .. 1.2) | 0.0 (0.0 .. 0.2) |
| Window Tags | 27 | 0.580 (0.386 .. 0.824) | 0.847 (0.847 .. 0.989) | 0.400 (0.271 .. 0.514) | 2.2 (0.9 .. 3.2) | 0.5 (0.0 .. 0.5) |

`ink span / bbox` of 1.0 means the ink reaches both edges of its recorded box, so it cannot be off-centre in it and the margins above are bounded. `solidity` is ink pixels over ink bbox area: below 1 the ink is not a solid rectangle -- a glyph run, an L, a leader -- so its centroid can also sit away from its own bbox centre. `possible offset` is how far, in pixels, a margin in 2b could be wrong because of this.

- `v0_control` samples: matched 27, no bbox 1, colour absent 258, clipped at an image edge 1.
- `v7_no_crop` samples: matched 283, no bbox 1, colour absent 3, clipped at an image edge 0.
- `v8_no_crop_fiducials` samples: matched 283, no bbox 1, colour absent 3, clipped at an image edge 0.
- `v9_registration_marks` samples: matched 295, no bbox 1, colour absent 3, clipped at an image edge 0.
- `v10_element_only_white` samples: matched 283, no bbox 1, colour absent 3, clipped at an image edge 0.

### 3. How far recorded annotation bboxes reach past the rendered frame

| variant | left | right | top | bottom | bboxes outside |  |
|---|---|---|---|---|---|---|
| v0_control | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 1.25 ft / 0.156 in | 0.00 ft / 0.000 in | 1 of 286 |  |
| v7_no_crop | -- | -- | -- | -- | -- | crop_mode 'untouched' and no ACTIVE authored crop was recorded, so there is no rectangle to measure margins against; the scale is still fitted |
| v8_no_crop_fiducials | -- | -- | -- | -- | -- | crop_mode 'untouched' and no ACTIVE authored crop was recorded, so there is no rectangle to measure margins against; the scale is still fitted |
| v9_registration_marks | -- | -- | -- | -- | -- | crop_mode 'untouched' and no ACTIVE authored crop was recorded, so there is no rectangle to measure margins against; the scale is still fitted |
| v10_element_only_white | -- | -- | -- | -- | -- | crop_mode 'untouched' and no ACTIVE authored crop was recorded, so there is no rectangle to measure margins against; the scale is still fitted |

Read this table against 2b. Where they agree, the render is behaving as if it had been fitted to the crop unioned with the annotations drawn beyond it.

### 4. Coverage per category

#### `v0_control`

Ink test threshold >2% non-white, placed through the fitted mapping from measurement (2).

| category | assigned | painted | colour present | bbox | ink tested | ink present | bbox off image | ink untested |
|---|---|---|---|---|---|---|---|---|
| <no category> | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| Attached Detail Groups | 1 | 1 | 0 | 1 | 1 | 0 | 0 | 0 |
| Dimensions | 135 | 135 | 2 | 135 | 135 | 8 | 0 | 0 |
| Door Tags | 26 | 26 | 0 | 26 | 26 | 3 | 0 | 0 |
| Grids | 19 | 19 | 19 | 19 | 19 | 4 | 0 | 0 |
| Lines | 2 | 2 | 2 | 2 | 2 | 1 | 0 | 0 |
| Revision Cloud Tags | 1 | 1 | 1 | 1 | 1 | 1 | 0 | 0 |
| Revision Clouds | 1 | 1 | 1 | 1 | 1 | 1 | 0 | 0 |
| Room Tags | 18 | 18 | 0 | 18 | 18 | 1 | 0 | 0 |
| Views | 3 | 3 | 3 | 3 | 3 | 2 | 0 | 0 |
| Wall Tags | 53 | 53 | 0 | 53 | 53 | 2 | 0 | 0 |
| Window Tags | 27 | 27 | 0 | 27 | 27 | 0 | 0 | 0 |

#### `v7_no_crop`

Ink test threshold >2% non-white, placed through the fitted mapping from measurement (2).

| category | assigned | painted | colour present | bbox | ink tested | ink present | bbox off image | ink untested |
|---|---|---|---|---|---|---|---|---|
| <no category> | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| Attached Detail Groups | 1 | 1 | 0 | 1 | 1 | 1 | 0 | 0 |
| Dimensions | 135 | 135 | 135 | 135 | 135 | 135 | 0 | 0 |
| Door Tags | 26 | 26 | 25 | 26 | 26 | 26 | 0 | 0 |
| Grids | 19 | 19 | 19 | 19 | 19 | 18 | 0 | 0 |
| Lines | 2 | 2 | 1 | 2 | 2 | 2 | 0 | 0 |
| Revision Cloud Tags | 1 | 1 | 1 | 1 | 1 | 1 | 0 | 0 |
| Revision Clouds | 1 | 1 | 1 | 1 | 1 | 1 | 0 | 0 |
| Room Tags | 18 | 18 | 18 | 18 | 18 | 18 | 0 | 0 |
| Views | 3 | 3 | 3 | 3 | 3 | 3 | 0 | 0 |
| Wall Tags | 53 | 53 | 53 | 53 | 53 | 53 | 0 | 0 |
| Window Tags | 27 | 27 | 27 | 27 | 27 | 27 | 0 | 0 |

#### `v8_no_crop_fiducials`

Ink test threshold >2% non-white, placed through the fitted mapping from measurement (2).

| category | assigned | painted | colour present | bbox | ink tested | ink present | bbox off image | ink untested |
|---|---|---|---|---|---|---|---|---|
| <no category> | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| Attached Detail Groups | 1 | 1 | 0 | 1 | 1 | 1 | 0 | 0 |
| Dimensions | 135 | 135 | 135 | 135 | 135 | 135 | 0 | 0 |
| Door Tags | 26 | 26 | 25 | 26 | 26 | 26 | 0 | 0 |
| Grids | 19 | 19 | 19 | 19 | 19 | 18 | 0 | 0 |
| Lines | 2 | 2 | 1 | 2 | 2 | 2 | 0 | 0 |
| Revision Cloud Tags | 1 | 1 | 1 | 1 | 1 | 1 | 0 | 0 |
| Revision Clouds | 1 | 1 | 1 | 1 | 1 | 1 | 0 | 0 |
| Room Tags | 18 | 18 | 18 | 18 | 18 | 18 | 0 | 0 |
| Views | 3 | 3 | 3 | 3 | 3 | 3 | 0 | 0 |
| Wall Tags | 53 | 53 | 53 | 53 | 53 | 53 | 0 | 0 |
| Window Tags | 27 | 27 | 27 | 27 | 27 | 27 | 0 | 0 |

#### `v9_registration_marks`

Ink test threshold >2% non-white, placed through the fitted mapping from measurement (2).

| category | assigned | painted | colour present | bbox | ink tested | ink present | bbox off image | ink untested |
|---|---|---|---|---|---|---|---|---|
| <no category> | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| Attached Detail Groups | 1 | 1 | 0 | 1 | 1 | 1 | 0 | 0 |
| Dimensions | 135 | 135 | 135 | 135 | 135 | 135 | 0 | 0 |
| Door Tags | 26 | 26 | 25 | 26 | 26 | 26 | 0 | 0 |
| Grids | 19 | 19 | 19 | 19 | 19 | 19 | 0 | 0 |
| Lines | 14 | 14 | 13 | 14 | 14 | 8 | 0 | 0 |
| Revision Cloud Tags | 1 | 1 | 1 | 1 | 1 | 1 | 0 | 0 |
| Revision Clouds | 1 | 1 | 1 | 1 | 1 | 1 | 0 | 0 |
| Room Tags | 18 | 18 | 18 | 18 | 18 | 18 | 0 | 0 |
| Views | 3 | 3 | 3 | 3 | 3 | 3 | 0 | 0 |
| Wall Tags | 53 | 53 | 53 | 53 | 53 | 53 | 0 | 0 |
| Window Tags | 27 | 27 | 27 | 27 | 27 | 27 | 0 | 0 |

#### `v10_element_only_white`

Ink test threshold >2% non-white, placed through the fitted mapping from measurement (2).

| category | assigned | painted | colour present | bbox | ink tested | ink present | bbox off image | ink untested |
|---|---|---|---|---|---|---|---|---|
| <no category> | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| Attached Detail Groups | 1 | 1 | 0 | 1 | 1 | 1 | 0 | 0 |
| Dimensions | 135 | 135 | 135 | 135 | 135 | 135 | 0 | 0 |
| Door Tags | 26 | 26 | 25 | 26 | 26 | 26 | 0 | 0 |
| Grids | 19 | 19 | 19 | 19 | 19 | 18 | 0 | 0 |
| Lines | 2 | 2 | 1 | 2 | 2 | 2 | 0 | 0 |
| Revision Cloud Tags | 1 | 1 | 1 | 1 | 1 | 1 | 0 | 0 |
| Revision Clouds | 1 | 1 | 1 | 1 | 1 | 1 | 0 | 0 |
| Room Tags | 18 | 18 | 18 | 18 | 18 | 18 | 0 | 0 |
| Views | 3 | 3 | 3 | 3 | 3 | 3 | 0 | 0 |
| Wall Tags | 53 | 53 | 53 | 53 | 53 | 53 | 0 | 0 |
| Window Tags | 27 | 27 | 27 | 27 | 27 | 27 | 0 | 0 |

### 5. Non-white, non-palette pixels

| variant | total | white | palette | off-palette | off-palette frac | blend | grey | other | distinct | tally capped |
|---|---|---|---|---|---|---|---|---|---|---|
| v0_control | 17437255 | 17316714 | 84860 | 35681 | 0.00205 | 11313 | 24368 | 0 | 400 | no |
| v7_no_crop | 20552812 | 20523753 | 24246 | 4813 | 0.00023 | 4595 | 217 | 1 | 1287 | no |
| v8_no_crop_fiducials | 20552812 | 20523686 | 24246 | 4813 | 0.00023 | 4596 | 216 | 1 | 1288 | no |
| v9_registration_marks | 20552812 | 20523514 | 24418 | 4813 | 0.00023 | 4596 | 214 | 3 | 1290 | no |
| v10_element_only_white | 20552812 | 20523753 | 24246 | 4813 | 0.00023 | 4595 | 217 | 1 | 1287 | no |

`blend` is within 3 RGB units of the segment between some palette colour and white. `grey` is all three channels within 3 of each other. The two OVERLAP -- a grey pixel is also collinear with white -- and blend is tested first; the `blend also grey` count below states what that ordering costs.

- `v0_control` blend also grey: 0 px.
- `v7_no_crop` blend also grey: 0 px.
- `v8_no_crop_fiducials` blend also grey: 0 px.
- `v9_registration_marks` blend also grey: 0 px.
- `v10_element_only_white` blend also grey: 0 px.

#### `v0_control` top 10 off-palette colours

| rgb | pixels | class | also grey |
|---|---|---|---|
| [0, 0, 0] | 22037 | grey | yes |
| [137, 137, 137] | 336 | grey | yes |
| [195, 195, 195] | 333 | grey | yes |
| [71, 71, 71] | 245 | grey | yes |
| [253, 116, 116] | 210 | blend | no |
| [154, 254, 206] | 200 | blend | no |
| [236, 236, 236] | 178 | grey | yes |
| [95, 253, 231] | 168 | blend | no |
| [148, 110, 253] | 168 | blend | no |
| [253, 108, 156] | 168 | blend | no |

#### `v7_no_crop` top 10 off-palette colours

| rgb | pixels | class | also grey |
|---|---|---|---|
| [195, 195, 195] | 61 | grey | yes |
| [208, 208, 208] | 35 | grey | yes |
| [236, 236, 236] | 27 | grey | yes |
| [221, 221, 221] | 26 | grey | yes |
| [65, 199, 253] | 25 | blend | no |
| [254, 211, 160] | 20 | blend | no |
| [86, 205, 253] | 18 | blend | no |
| [160, 254, 158] | 18 | blend | no |
| [182, 168, 254] | 18 | blend | no |
| [189, 166, 254] | 18 | blend | no |

#### `v8_no_crop_fiducials` top 10 off-palette colours

| rgb | pixels | class | also grey |
|---|---|---|---|
| [195, 195, 195] | 60 | grey | yes |
| [208, 208, 208] | 35 | grey | yes |
| [236, 236, 236] | 27 | grey | yes |
| [221, 221, 221] | 26 | grey | yes |
| [65, 199, 253] | 25 | blend | no |
| [254, 211, 160] | 20 | blend | no |
| [86, 205, 253] | 18 | blend | no |
| [160, 254, 158] | 18 | blend | no |
| [182, 168, 254] | 18 | blend | no |
| [189, 166, 254] | 18 | blend | no |

#### `v9_registration_marks` top 10 off-palette colours

| rgb | pixels | class | also grey |
|---|---|---|---|
| [195, 195, 195] | 60 | grey | yes |
| [208, 208, 208] | 34 | grey | yes |
| [221, 221, 221] | 26 | grey | yes |
| [236, 236, 236] | 26 | grey | yes |
| [65, 199, 253] | 25 | blend | no |
| [254, 211, 160] | 20 | blend | no |
| [86, 205, 253] | 18 | blend | no |
| [160, 254, 158] | 18 | blend | no |
| [182, 168, 254] | 18 | blend | no |
| [189, 166, 254] | 18 | blend | no |

#### `v10_element_only_white` top 10 off-palette colours

| rgb | pixels | class | also grey |
|---|---|---|---|
| [195, 195, 195] | 61 | grey | yes |
| [208, 208, 208] | 35 | grey | yes |
| [236, 236, 236] | 27 | grey | yes |
| [221, 221, 221] | 26 | grey | yes |
| [65, 199, 253] | 25 | blend | no |
| [254, 211, 160] | 20 | blend | no |
| [86, 205, 253] | 18 | blend | no |
| [160, 254, 158] | 18 | blend | no |
| [182, 168, 254] | 18 | blend | no |
| [189, 166, 254] | 18 | blend | no |

### 6. Model TIFF hash vs V0

| variant | model sha256 (16) | V0 sha256 (16) | equal |
|---|---|---|---|
| v0_control | a3e7f9c8e7a61851 | a3e7f9c8e7a61851 | yes |
| v7_no_crop | a3e7f9c8e7a61851 | a3e7f9c8e7a61851 | yes |
| v8_no_crop_fiducials | a3e7f9c8e7a61851 | a3e7f9c8e7a61851 | yes |
| v9_registration_marks | a3e7f9c8e7a61851 | a3e7f9c8e7a61851 | yes |
| v10_element_only_white | a3e7f9c8e7a61851 | a3e7f9c8e7a61851 | yes |

The model pass runs ONCE per view, so this column detects a variant CLOBBERING the model artifact -- a path collision, a stray write. Whether a variant changed how the model pass RENDERS is the probe's own `model_reexport_after_variants` verdict, quoted at the top of this view's section, which has its own repeatability control.

### 7. F1 -- the crop boundary, recovered from the pixels

Q1: does the exported image contain the crop boundary, and does its recovered position match `view.CropBox`? A boundary is a band of rows (or columns) each holding a straight run of non-white, non-palette, non-fiducial pixels of >= 25% of the image, closing into one rectangle (or matching the crop shape's levels). `NOT FOUND` on a V8 row is the answer that ImageExportOptions does not draw it.

| variant | probe turned it on | boundary | row/col bands | px/ft u | px/ft v | lattice px/ft | u/v isotropy | boundary px rects (subtract these) |  |
|---|---|---|---|---|---|---|---|---|---|
| v0_control | no | NOT_FOUND | 1 / 2 | -- | -- | 18.7512 | -- | -- | 1 row band(s) and 2 column band(s) long enough to be a crop edge, but no four of them close into one rectangle |
| v7_no_crop | no | NOT_FOUND | 0 / 0 | -- | -- | 18.7512 | -- | -- | no row or column holds a straight non-palette run of >= 25% of the image: the crop boundary is NOT in this capture |
| v8_no_crop_fiducials | yes | NOT_FOUND | 0 / 0 | -- | -- | 18.7512 | -- | -- | no row or column holds a straight non-palette run of >= 25% of the image: the crop boundary is NOT in this capture |
| v9_registration_marks | yes | NOT_FOUND | 0 / 0 | -- | -- | 18.7512 | -- | -- | no row or column holds a straight non-palette run of >= 25% of the image: the crop boundary is NOT in this capture |
| v10_element_only_white | no | NOT_FOUND | 0 / 0 | -- | -- | 18.7512 | -- | -- | no row or column holds a straight non-palette run of >= 25% of the image: the crop boundary is NOT in this capture |

`isotropy` of 1 means the boundary's pixel rectangle has the crop's own aspect -- the recovered position is the crop's shape at one uniform scale. `lattice px/ft` is the model capture's (1 / achieved fpp): an untouched capture that rendered exactly the authored crop at the requested count would match it.

- `v8_no_crop_fiducials` MODEL capture (boundary drawn at crop A [-119.88027454934752, 2226.16545952551, 193.9668105407394, 2382.955677055647]): NOT_FOUND -- no row or column holds a straight non-palette run of >= 25% of the image: the crop boundary is NOT in this capture
- `v9_registration_marks` MODEL capture (boundary drawn at crop A [-119.88027454934752, 2226.16545952551, 193.9668105407394, 2382.955677055647]): NOT_FOUND -- no row or column holds a straight non-palette run of >= 25% of the image: the crop boundary is NOT in this capture

### 8. F2 -- the fiducial pair

| variant | element | colour | found | pixels | ink px bbox | clipped | recorded rect_uv |
|---|---|---|---|---|---|---|---|
| v8_no_crop_fiducials | 7425328 | [251, 11, 139] | yes | 30 | [1671.0, 1303.0, 1682.0, 1314.0] | no | [-91.52219778092892, 2340.6392890765183, -85.52219778092886, 2346.639289076517] |
| v8_no_crop_fiducials | 3383634 | [11, 139, 251] | yes | 37 | [2032.0, 1633.0, 2048.0, 1641.0] | no | [35.15750906434223, 2230.1654595255086, 40.814363313834605, 2232.993886650255] |
| v9_registration_marks | 7425328 | [251, 11, 139] | yes | 30 | [1671.0, 1303.0, 1682.0, 1314.0] | no | [-91.52219778092892, 2340.6392890765183, -85.52219778092886, 2346.639289076517] |
| v9_registration_marks | 3383634 | [11, 139, 251] | yes | 37 | [2032.0, 1633.0, 2048.0, 1641.0] | no | [35.15750906434223, 2230.1654595255086, 40.814363313834605, 2232.993886650255] |

| fit | px/ft u | px/ft v | u/v isotropy | residual max px u / v |  |
|---|---|---|---|---|---|
| `v8_no_crop_fiducials` F2 (recorded bbox) | 2.8725 | 2.9302 | 0.98030 | 2.67 / 2.86 |  |
| `v8_no_crop_fiducials` F2 (model-anchored) | -- | -- | -- | -- | 2 of 2 fiducial(s) have no colour in the model capture |
| `v9_registration_marks` F2 (recorded bbox) | 2.8725 | 2.9302 | 0.98030 | 2.67 / 2.86 |  |
| `v9_registration_marks` F2 (model-anchored) | -- | -- | -- | -- | 2 of 2 fiducial(s) have no colour in the model capture |


F2 fits each fiducial's INK EDGES against its recorded extent: four points per axis for the pair, so it has a residual. The recorded extent is a projected 3-D bbox, which can be looser than the element; the model-anchored row replaces it with the element's drawn extent in the model capture, at the cost of assuming the model capture registers.

### 9. F1 vs F2 vs F3 vs the bbox fit

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
| v8_no_crop_fiducials | F2_vs_bbox_fit | -0.0338 | -0.0542 | 4.65 |  |
| v9_registration_marks | F1_vs_F2 | -- | -- | -- | one of the two mappings is not available |
| v9_registration_marks | F1_vs_bbox_fit | -- | -- | -- | one of the two mappings is not available |
| v9_registration_marks | F2_vs_bbox_fit | -0.0326 | -0.0349 | 4.52 |  |
| v9_registration_marks | F3_vs_F2 | +0.0252 | -0.0320 | 3.47 |  |
| v9_registration_marks | F3_vs_bbox_fit | -0.0075 | -0.0669 | 3.20 |  |
| v10_element_only_white | F1_vs_F2 | -- | -- | -- | one of the two mappings is not available |
| v10_element_only_white | F1_vs_bbox_fit | -- | -- | -- | one of the two mappings is not available |
| v10_element_only_white | F2_vs_bbox_fit | -- | -- | -- | one of the two mappings is not available |

### 10. Datum extents: ink vs the recorded (authored-crop) bbox

Q3: do datum extents in the capture match the drawing? Positive means the ink reaches FURTHER than the bbox recorded before the capture touched the view. Under V0 the crop was widened to B, which lengthens datums; under V7/V8 it was not.

| variant | datum | category | map | length delta ft | paper in | per side ft l/r/t/b |  |
|---|---|---|---|---|---|---|---|
| v0_control | 3693278 | Grids | bbox_fit | +0.13 | +0.016 | +1.09 / -0.96 / +0.10 / -0.02 |  |
| v0_control | 3693539 | Grids | bbox_fit | +0.13 | +0.016 | +1.09 / -0.96 / +0.19 / -0.10 |  |
| v0_control | 3693688 | Grids | bbox_fit | +0.13 | +0.016 | +1.09 / -0.96 / -0.20 / +0.28 |  |
| v0_control | 3694573 | Grids | bbox_fit | +0.93 | +0.116 | +1.07 / -1.01 / +0.44 / +0.49 |  |
| v0_control | 3694705 | Grids | bbox_fit | +0.93 | +0.116 | +1.09 / -1.03 / +0.44 / +0.49 |  |
| v0_control | 3694782 | Grids | bbox_fit | +0.93 | +0.116 | +1.09 / -1.04 / +0.44 / +0.49 |  |
| v0_control | 3694880 | Grids | bbox_fit | +0.93 | +0.116 | +1.05 / -1.00 / +0.44 / +0.49 |  |
| v0_control | 3694982 | Grids | bbox_fit | +0.93 | +0.116 | +1.07 / -1.02 / +0.44 / +0.49 |  |
| v0_control | 3695045 | Grids | bbox_fit | +0.93 | +0.116 | +1.03 / -0.98 / +0.44 / +0.49 |  |
| v0_control | 3695123 | Grids | bbox_fit | +0.93 | +0.116 | +1.04 / -0.99 / +0.44 / +0.49 |  |
| v0_control | 3695201 | Grids | bbox_fit | +0.93 | +0.116 | +1.01 / -0.95 / +0.44 / +0.49 |  |
| v0_control | 3697629 | Grids | bbox_fit | +0.93 | +0.116 | +1.05 / -0.99 / +0.44 / +0.49 |  |
| v0_control | 3698218 | Grids | bbox_fit | +0.93 | +0.116 | +1.02 / -0.96 / +0.44 / +0.49 |  |
| v0_control | 3784562 | Grids | bbox_fit | +0.13 | +0.016 | +1.09 / -0.96 / -0.12 / +0.20 |  |
| v0_control | 3839177 | Grids | bbox_fit | +0.19 | +0.023 | +1.13 / -0.95 / +0.30 / -0.22 |  |
| v0_control | 3839178 | Grids | bbox_fit | +0.19 | +0.023 | +1.13 / -0.95 / +0.54 / -0.46 |  |
| v0_control | 3923006 | Grids | bbox_fit | +0.93 | +0.116 | +1.11 / -1.06 / +0.44 / +0.49 |  |
| v0_control | 5136165 | Grids | bbox_fit | +0.93 | +0.116 | +1.08 / -1.03 / +0.44 / +0.49 |  |
| v0_control | 6961360 | Grids | bbox_fit | +0.19 | +0.023 | +1.13 / -0.95 / +0.58 / -0.55 | CLIPPED at image edge |
| v7_no_crop | 3693278 | Grids | bbox_fit | -0.71 | -0.089 | -0.58 / -0.13 / -0.21 / +0.57 |  |
| v7_no_crop | 3693539 | Grids | bbox_fit | -0.71 | -0.089 | -0.58 / -0.13 / -0.51 / +0.86 |  |
| v7_no_crop | 3693688 | Grids | bbox_fit | -0.71 | -0.089 | -0.58 / -0.13 / +0.94 / -0.92 |  |
| v7_no_crop | 3694573 | Grids | bbox_fit | -3.19 | -0.399 | -1.12 / +0.56 / -1.61 / -1.58 |  |
| v7_no_crop | 3694705 | Grids | bbox_fit | -3.19 | -0.399 | -1.06 / +0.50 / -1.61 / -1.58 |  |
| v7_no_crop | 3694782 | Grids | bbox_fit | -3.19 | -0.399 | -1.03 / +0.47 / -1.61 / -1.58 |  |
| v7_no_crop | 3694880 | Grids | bbox_fit | -3.19 | -0.399 | -0.96 / +0.40 / -1.61 / -1.58 |  |
| v7_no_crop | 3694982 | Grids | bbox_fit | -3.19 | -0.399 | -0.90 / +0.34 / -1.61 / -1.58 |  |
| v7_no_crop | 3695045 | Grids | bbox_fit | -3.19 | -0.399 | -0.83 / +0.27 / -1.61 / -1.58 |  |
| v7_no_crop | 3695123 | Grids | bbox_fit | -3.19 | -0.399 | +0.27 / +0.21 / -1.61 / -1.58 |  |
| v7_no_crop | 3695201 | Grids | bbox_fit | -3.19 | -0.399 | -0.36 / +0.14 / -1.61 / -1.58 |  |
| v7_no_crop | 3697629 | Grids | bbox_fit | -3.19 | -0.399 | +0.27 / +0.21 / -1.61 / -1.58 |  |
| v7_no_crop | 3698218 | Grids | bbox_fit | -3.19 | -0.399 | -0.51 / -0.05 / -1.61 / -1.58 |  |
| v7_no_crop | 3784562 | Grids | bbox_fit | -0.71 | -0.089 | -0.58 / -0.13 / +0.64 / -0.29 |  |
| v7_no_crop | 3839177 | Grids | bbox_fit | -0.38 | -0.048 | -0.34 / -0.04 / -0.97 / +1.33 |  |
| v7_no_crop | 3839178 | Grids | bbox_fit | -0.38 | -0.048 | -0.34 / -0.04 / -1.82 / +2.18 |  |
| v7_no_crop | 3923006 | Grids | bbox_fit | -3.19 | -0.399 | -0.28 / +0.76 / -1.61 / -1.58 |  |
| v7_no_crop | 5136165 | Grids | bbox_fit | -3.19 | -0.399 | -1.06 / +0.50 / -1.61 / -1.58 |  |
| v7_no_crop | 6961360 | Grids | bbox_fit | -0.38 | -0.048 | -0.34 / -0.04 / -2.37 / +2.73 |  |
| v8_no_crop_fiducials | 3693278 | Grids | F2 | +2.70 | +0.337 | +1.22 / +1.48 / -0.48 / +0.92 |  |
| v8_no_crop_fiducials | 3693539 | Grids | F2 | +2.70 | +0.337 | +1.22 / +1.48 / -0.49 / +0.93 |  |
| v8_no_crop_fiducials | 3693688 | Grids | F2 | +2.70 | +0.337 | +1.22 / +1.48 / -0.14 / +0.23 |  |
| v8_no_crop_fiducials | 3694573 | Grids | F2 | -0.91 | -0.114 | +0.22 / -0.74 / -0.98 / +0.06 |  |
| v8_no_crop_fiducials | 3694705 | Grids | F2 | -0.91 | -0.114 | -0.06 / -0.46 / -0.98 / +0.06 |  |
| v8_no_crop_fiducials | 3694782 | Grids | F2 | -0.91 | -0.114 | -0.27 / -0.25 / -0.98 / +0.06 |  |
| v8_no_crop_fiducials | 3694880 | Grids | F2 | -0.91 | -0.114 | -0.56 / +0.04 / -0.98 / +0.06 |  |
| v8_no_crop_fiducials | 3694982 | Grids | F2 | -0.91 | -0.114 | -0.84 / +0.33 / -0.98 / +0.06 |  |
| v8_no_crop_fiducials | 3695045 | Grids | F2 | -0.91 | -0.114 | -1.13 / +0.61 / -0.98 / +0.06 |  |
| v8_no_crop_fiducials | 3695123 | Grids | F2 | -0.91 | -0.114 | -0.50 / +1.03 / -0.98 / +0.06 |  |
| v8_no_crop_fiducials | 3695201 | Grids | F2 | -0.91 | -0.114 | -1.48 / +1.31 / -0.98 / +0.06 |  |
| v8_no_crop_fiducials | 3697629 | Grids | F2 | -0.91 | -0.114 | -0.37 / +0.90 / -0.98 / +0.06 |  |
| v8_no_crop_fiducials | 3698218 | Grids | F2 | -0.91 | -0.114 | -1.83 / +1.31 / -0.98 / +0.06 |  |
| v8_no_crop_fiducials | 3784562 | Grids | F2 | +2.70 | +0.337 | +1.22 / +1.48 / -0.15 / +0.58 |  |
| v8_no_crop_fiducials | 3839177 | Grids | F2 | +2.90 | +0.363 | +1.37 / +1.53 / -0.69 / +1.13 |  |
| v8_no_crop_fiducials | 3839178 | Grids | F2 | +2.90 | +0.363 | +1.37 / +1.53 / -1.00 / +1.44 |  |
| v8_no_crop_fiducials | 3923006 | Grids | F2 | -0.91 | -0.114 | +1.27 / -0.75 / -0.98 / +0.06 |  |
| v8_no_crop_fiducials | 5136165 | Grids | F2 | -0.91 | -0.114 | -0.19 / -0.33 / -0.98 / +0.06 |  |
| v8_no_crop_fiducials | 6961360 | Grids | F2 | +2.90 | +0.363 | +1.37 / +1.53 / -1.29 / +1.73 |  |
| v9_registration_marks | 3693278 | Grids | F3 | +0.15 | +0.019 | -0.13 / +0.28 / +0.34 / +0.15 |  |
| v9_registration_marks | 3693539 | Grids | F3 | +0.15 | +0.019 | -0.13 / +0.28 / +0.50 / -0.02 |  |
| v9_registration_marks | 3693688 | Grids | F3 | +0.15 | +0.019 | -0.13 / +0.28 / +0.19 / -0.05 |  |
| v9_registration_marks | 3694573 | Grids | F3 | +0.47 | +0.058 | -0.79 / +0.24 / +0.40 / +0.07 |  |
| v9_registration_marks | 3694705 | Grids | F3 | +0.47 | +0.058 | -0.81 / +0.26 / +0.40 / +0.07 |  |
| v9_registration_marks | 3694782 | Grids | F3 | +0.47 | +0.058 | -0.84 / +0.29 / +0.40 / +0.07 |  |
| v9_registration_marks | 3694880 | Grids | F3 | +0.47 | +0.058 | -0.86 / +0.31 / +0.40 / +0.07 |  |
| v9_registration_marks | 3694982 | Grids | F3 | +0.47 | +0.058 | -0.89 / +0.34 / +0.40 / +0.07 |  |
| v9_registration_marks | 3695045 | Grids | F3 | +0.47 | +0.058 | -0.91 / +0.36 / +0.40 / +0.07 |  |
| v9_registration_marks | 3695123 | Grids | F3 | +0.47 | +0.058 | +0.07 / +0.42 / +0.40 / +0.07 |  |
| v9_registration_marks | 3695201 | Grids | F3 | +0.47 | +0.058 | -0.65 / +0.44 / +0.40 / +0.07 |  |
| v9_registration_marks | 3697629 | Grids | F3 | +0.47 | +0.058 | +0.10 / +0.39 / +0.40 / +0.07 |  |
| v9_registration_marks | 3698218 | Grids | F3 | +0.47 | +0.058 | -0.85 / +0.30 / +0.40 / +0.07 |  |
| v9_registration_marks | 3784562 | Grids | F3 | +0.15 | +0.019 | -0.13 / +0.28 / +0.36 / +0.13 |  |
| v9_registration_marks | 3839177 | Grids | F3 | +0.45 | +0.056 | +0.09 / +0.36 / +0.46 / +0.02 |  |
| v9_registration_marks | 3839178 | Grids | F3 | +0.45 | +0.056 | +0.09 / +0.36 / +0.48 / +0.00 |  |
| v9_registration_marks | 3923006 | Grids | F3 | +0.47 | +0.058 | +0.11 / +0.38 / +0.40 / +0.07 |  |
| v9_registration_marks | 5136165 | Grids | F3 | +0.47 | +0.058 | -0.84 / +0.29 / +0.40 / +0.07 |  |
| v9_registration_marks | 6961360 | Grids | F3 | +0.45 | +0.056 | +0.09 / +0.36 / +0.35 / +0.14 |  |
| v10_element_only_white | 3693278 | Grids | bbox_fit | -0.71 | -0.089 | -0.58 / -0.13 / -0.21 / +0.57 |  |
| v10_element_only_white | 3693539 | Grids | bbox_fit | -0.71 | -0.089 | -0.58 / -0.13 / -0.51 / +0.86 |  |
| v10_element_only_white | 3693688 | Grids | bbox_fit | -0.71 | -0.089 | -0.58 / -0.13 / +0.94 / -0.92 |  |
| v10_element_only_white | 3694573 | Grids | bbox_fit | -3.19 | -0.399 | -1.12 / +0.56 / -1.61 / -1.58 |  |
| v10_element_only_white | 3694705 | Grids | bbox_fit | -3.19 | -0.399 | -1.06 / +0.50 / -1.61 / -1.58 |  |
| v10_element_only_white | 3694782 | Grids | bbox_fit | -3.19 | -0.399 | -1.03 / +0.47 / -1.61 / -1.58 |  |
| v10_element_only_white | 3694880 | Grids | bbox_fit | -3.19 | -0.399 | -0.96 / +0.40 / -1.61 / -1.58 |  |
| v10_element_only_white | 3694982 | Grids | bbox_fit | -3.19 | -0.399 | -0.90 / +0.34 / -1.61 / -1.58 |  |
| v10_element_only_white | 3695045 | Grids | bbox_fit | -3.19 | -0.399 | -0.83 / +0.27 / -1.61 / -1.58 |  |
| v10_element_only_white | 3695123 | Grids | bbox_fit | -3.19 | -0.399 | +0.27 / +0.21 / -1.61 / -1.58 |  |
| v10_element_only_white | 3695201 | Grids | bbox_fit | -3.19 | -0.399 | -0.36 / +0.14 / -1.61 / -1.58 |  |
| v10_element_only_white | 3697629 | Grids | bbox_fit | -3.19 | -0.399 | +0.27 / +0.21 / -1.61 / -1.58 |  |
| v10_element_only_white | 3698218 | Grids | bbox_fit | -3.19 | -0.399 | -0.51 / -0.05 / -1.61 / -1.58 |  |
| v10_element_only_white | 3784562 | Grids | bbox_fit | -0.71 | -0.089 | -0.58 / -0.13 / +0.64 / -0.29 |  |
| v10_element_only_white | 3839177 | Grids | bbox_fit | -0.38 | -0.048 | -0.34 / -0.04 / -0.97 / +1.33 |  |
| v10_element_only_white | 3839178 | Grids | bbox_fit | -0.38 | -0.048 | -0.34 / -0.04 / -1.82 / +2.18 |  |
| v10_element_only_white | 3923006 | Grids | bbox_fit | -3.19 | -0.399 | -0.28 / +0.76 / -1.61 / -1.58 |  |
| v10_element_only_white | 5136165 | Grids | bbox_fit | -3.19 | -0.399 | -1.06 / +0.50 / -1.61 / -1.58 |  |
| v10_element_only_white | 6961360 | Grids | bbox_fit | -0.38 | -0.048 | -0.34 / -0.04 / -2.37 / +2.73 |  |

Premise: `get_BoundingBox(view)` of a datum is its drawn extent in the view (UNCONFIRMED).

### 11. Model-ink residue

Q4: does membership suppression with subcategories leave any model ink? Off-palette pixels outside the fiducials and outside any recovered boundary band. Counted, not attributed: an annotation the pass could not paint lands here too.

| variant | suppression | category layer | off-palette | fiducial px | boundary bands excluded | off-palette outside boundary |
|---|---|---|---|---|---|---|
| v0_control | hide_categories | -- | 35681 | 0 | 0 | 35681 |
| v7_no_crop | external | yes | 4813 | 0 | 0 | 4813 |
| v8_no_crop_fiducials | external | yes | 4813 | 67 | 0 | 4813 |
| v9_registration_marks | external | yes | 4813 | 67 | 0 | 4813 |
| v10_element_only_white | external | no | 4813 | 0 | 0 | 4813 |

`category layer` is mechanism 3 (category and subcategory white overrides). V10 runs without it: the V7 vs V10 difference in this table is what that layer removes, and the cost section above is what it costs.

### 12. F3 -- registration marks, in BOTH captures

Detail-line ticks the probe drew at KNOWN view UV, inset inside the crop, then removed by rolling back. Horizontal ticks' centre rows give v, vertical ticks' centre columns give u. Twelve ticks give six points per axis at three levels, so the residual measures disagreement between levels (a round-3 capture had eight ticks at two levels, whose residual is 0 by construction). The model row is checked against the model capture's RECORDED lattice -- the one place this method meets a known answer. The endpoint fit uses tick ends (caps, anti-aliasing) and is shown beside the centre-line fit, never in its place.

| variant | capture | ticks | px/ft u | px/ft v | u/v isotropy | residual max px u / v | endpoint fit px/ft u / v | vs model lattice worst px |  |
|---|---|---|---|---|---|---|---|---|---|
| v9_registration_marks | annotation | 12/12 | 2.8976 | 2.8983 | 0.99979 | 0.00 / 0.33 | 2.8970 / 2.8969 | -- |  |
| v9_registration_marks | model | 12/12 | 18.7512 | 18.7512 | 1.00000 | 0.33 / 0.67 | 18.7512 / 18.7489 | 0.33 |  |

- `v9_registration_marks` model lattice: 18.7512 px/ft
- `v9_registration_marks` annotation -> model pixels via_model_lattice: x' = 6.471175 x -10242.52, y' = 6.469799 y -7754.85
- `v9_registration_marks` annotation -> model pixels via_model_marks: x' = 6.471175 x -10242.85, y' = 6.469799 y -7755.02
- `v9_registration_marks` annotation: 172 mark pixels in 12 rect(s) to subtract in post
- `v9_registration_marks` model: 1152 mark pixels in 12 rect(s) to subtract in post

### Overlays

The gate is measurement 1 only: image size == `frame_px`. IT IS A SIZE GATE AND NOTHING MORE. A capture can be exactly `frame_px` pixels and still have its CONTENT drawn at a different scale or origin -- which is finding F1 -- and `capture_overlay.py` maps UV through the SIDECAR's numbers, so on such a capture its boxes will not land on the ink. Measurement 2's fitted px/ft is echoed beside each line so that is visible here rather than only three sections up.

- `v0_control`: OVERLAY WITHHELD (fitted 18.743 / 18.617 px/ft against the frame's 18.751). measurement 1 shows image 5885x2963 against frame_px 5885x2940; the overlay is not run on a capture whose rendered region is not the frame its sidecar describes
- `v7_no_crop`: OVERLAY WITHHELD (fitted 2.906 / 2.984 px/ft against the frame's --). measurement 1 shows image 2942x6986 against frame_px 5885x2940; the overlay is not run on a capture whose rendered region is not the frame its sidecar describes
- `v8_no_crop_fiducials`: OVERLAY WITHHELD (fitted 2.906 / 2.984 px/ft against the frame's --). measurement 1 shows image 2942x6986 against frame_px 5885x2940; the overlay is not run on a capture whose rendered region is not the frame its sidecar describes
- `v9_registration_marks`: OVERLAY WITHHELD (fitted 2.905 / 2.965 px/ft against the frame's --). measurement 1 shows image 2942x6986 against frame_px 5885x2940; the overlay is not run on a capture whose rendered region is not the frame its sidecar describes
- `v10_element_only_white`: OVERLAY WITHHELD (fitted 2.906 / 2.984 px/ft against the frame's --). measurement 1 shows image 2942x6986 against frame_px 5885x2940; the overlay is not run on a capture whose rendered region is not the frame its sidecar describes
