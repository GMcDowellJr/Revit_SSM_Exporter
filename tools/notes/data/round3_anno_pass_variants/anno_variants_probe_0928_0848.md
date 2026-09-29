# Annotation-pass variant report

Values only. No pass/fail, no score, no recommendation -- the probe brief puts the judgement with Greg, and a tool that rated the variants would replace that read.

## View __Elevation_CropActive (id 19293413, 3, scale 1:96)

Probe `stage_a_anno_pass_variants` version 2026-09-23.3; combined report `C:\Users\gmcdowell\Documents\VOP\Exports\FVoT\probe_0928_0848\anno_pass_variants_probe\__Elevation_CropActive_19293413.anno_pass_variants.json`.

Model pass: success=True, failure_reason=None, achieved dpi=149.99, frame_px=[5164, 1267].
Model re-export after the variants: **unchanged** -- the control held and the post-variant hash matches

**The crop.** Authored crop active: yes; UV [-104.01772534812247, -4.810097715203943, 171.41561007680002, 62.7311886414472].
- V0 widens it to frame B by (ft): left +0.00 / right -0.00 / top +0.04 / bottom +0.00
- the MODEL pass sets it to crop A, which differs from the authored crop by (ft): left +0.00 / right -0.00 / top +0.04 / bottom +0.00
- crop shape: 1 loop(s), 4 edge(s), rectangle: yes, oblique edges: 0
- **white-suppression cost**: 21733 ms for 6536 element override(s), against 26379 ms for the whole model pass (ratio 0.82; a LOWER bound on suppression-vs-paint, since production does not time its paint alone)
  - by mechanism (ms): category_override_writes 21560, element_overrides 88, category_override_reads 26, category_collect 9, subcategory_walk 1, build_override 1, link_category_filters 0
  - API calls: category_override_reads 395, category_override_writes 391, element_override_writes 6536, subcategory_lists_read 40
  - `v0_control`: pre-state commit 2 ms; restore = group rollback, 499 ms; post-rollback element-override read-back -- ms (not_applicable)
  - `v7_no_crop` suppression: 21733 ms (category_override_writes 21560, element_overrides 88, category_override_reads 26, category_collect 9, subcategory_walk 1, build_override 1)
  - `v7_no_crop`: pre-state commit 2137 ms; restore = group rollback, 1047 ms; post-rollback element-override read-back 2998 ms (restored)
  - `v8_no_crop_fiducials` suppression: 23934 ms (category_override_writes 23738, element_overrides 87, category_override_reads 32, category_collect 22, build_override 3, subcategory_walk 1)
  - `v8_no_crop_fiducials`: pre-state commit 1716 ms; restore = group rollback, 1112 ms; post-rollback element-override read-back 3005 ms (restored)
  - `v9_registration_marks` suppression: 24400 ms (category_override_writes 24227, element_overrides 90, category_override_reads 24, category_collect 9, subcategory_walk 5, build_override 2)
  - `v9_registration_marks`: pre-state commit 1751 ms; restore = group rollback, 1147 ms; post-rollback element-override read-back 3012 ms (restored)
  - `v10_element_only_white` suppression WITHOUT the category layer: 90 ms (element_overrides 81, build_override 2)
  - `v10_element_only_white`: pre-state commit 2179 ms; restore = group rollback, 999 ms; post-rollback element-override read-back 2770 ms (restored)
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
- `v7_no_crop` frame: the AUTHORED crop (crop left as found)
- `v8_no_crop_fiducials` frame: the AUTHORED crop (crop left as found)
- `v9_registration_marks` frame: the AUTHORED crop (crop left as found)
- `v10_element_only_white` frame: the AUTHORED crop (crop left as found)

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
  - where left_mid_h should be (px [170, 850, 266, 856]): 679 of 679 px white; other colours: none
  - where right_mid_h should be (px [4876, 850, 4972, 856]): 679 of 679 px white; other colours: none
- `v9_registration_marks` annotation: 901 mark pixels in 10 rect(s) to subtract in post
- `v9_registration_marks` model: tick mid_bottom_v not found -- no component of the shared colour in that corner and orientation
- `v9_registration_marks` model: tick mid_top_v not found -- no component of the shared colour in that corner and orientation
  - where mid_bottom_v should be (px [2578, 1135, 2584, 1237]): 42 of 721 px white; other colours: [0, 234, 186] x238 (element 5686560), [78, 246, 6] x154 (element 12008414), [132, 210, 6] x133 (element 18861828), [6, 126, 216] x126 (element 14208019), [6, 60, 216] x28 (element 14203081)
  - where mid_top_v should be (px [2578, 29, 2584, 131]): 721 of 721 px white; other colours: none
- `v9_registration_marks` model: 960 mark pixels in 10 rect(s) to subtract in post

### Overlays

The gate is measurement 1 only: image size == `frame_px`. IT IS A SIZE GATE AND NOTHING MORE. A capture can be exactly `frame_px` pixels and still have its CONTENT drawn at a different scale or origin -- which is finding F1 -- and `capture_overlay.py` maps UV through the SIDECAR's numbers, so on such a capture its boxes will not land on the ink. Measurement 2's fitted px/ft is echoed beside each line so that is visible here rather than only three sections up.

- `v0_control`: OVERLAY WITHHELD (measurement 2 did not fit: 0 matched sample(s); a linear fit needs at least 2). measurement 1 shows image 5164x1709 against frame_px 5164x1267; the overlay is not run on a capture whose rendered region is not the frame its sidecar describes
- `v7_no_crop`: OVERLAY WITHHELD (measurement 2 did not fit: 0 matched sample(s); a linear fit needs at least 2). measurement 1 shows image 5164x1708 against frame_px 5164x1267; the overlay is not run on a capture whose rendered region is not the frame its sidecar describes
- `v8_no_crop_fiducials`: OVERLAY WITHHELD (measurement 2 did not fit: 0 matched sample(s); a linear fit needs at least 2). measurement 1 shows image 5164x1708 against frame_px 5164x1267; the overlay is not run on a capture whose rendered region is not the frame its sidecar describes
- `v9_registration_marks`: OVERLAY WITHHELD (fitted 17.635 / 17.631 px/ft against the frame's 18.749). measurement 1 shows image 5164x1708 against frame_px 5164x1267; the overlay is not run on a capture whose rendered region is not the frame its sidecar describes
- `v10_element_only_white`: OVERLAY WITHHELD (measurement 2 did not fit: 0 matched sample(s); a linear fit needs at least 2). measurement 1 shows image 5164x1708 against frame_px 5164x1267; the overlay is not run on a capture whose rendered region is not the frame its sidecar describes

## View __Plan_CropInActive (id 19291097, 1, scale 1:96)

Probe `stage_a_anno_pass_variants` version 2026-09-23.3; combined report `C:\Users\gmcdowell\Documents\VOP\Exports\FVoT\probe_0928_0848\anno_pass_variants_probe\__Plan_CropInActive_19291097.anno_pass_variants.json`.

Model pass: success=True, failure_reason=None, achieved dpi=33.18, frame_px=[4285, 10000].
Model re-export after the variants: **unchanged** -- the control held and the post-variant hash matches

**The crop.** Authored crop active: no; UV [-91.46926561670251, 2250.749394451395, 165.49485231475583, 2337.3310543423245].
- V0 widens it to frame B by (ft): left +574.63 / right +201.54 / top +459.48 / bottom +1865.00
- the MODEL pass sets it to crop A, which differs from the authored crop by (ft): left +574.63 / right +201.54 / top +459.48 / bottom +1865.00 -- and on this crop-INACTIVE view the model pass ACTIVATES a crop
- crop shape: 1 loop(s), 4 edge(s), rectangle: yes, oblique edges: 0
- **white-suppression cost**: 21630 ms for 4837 element override(s), against 30425 ms for the whole model pass (ratio 0.71; a LOWER bound on suppression-vs-paint, since production does not time its paint alone)
  - by mechanism (ms): category_override_writes 21481, element_overrides 69, category_override_reads 22, category_collect 8, subcategory_walk 4, build_override 3, link_category_filters 0
  - API calls: category_override_reads 396, category_override_writes 393, element_override_writes 4837, subcategory_lists_read 35
  - `v0_control`: pre-state commit 1 ms; restore = group rollback, 2107 ms; post-rollback element-override read-back -- ms (not_applicable)
  - `v7_no_crop` suppression: 21630 ms (category_override_writes 21481, element_overrides 69, category_override_reads 22, category_collect 8, subcategory_walk 4, build_override 3)
  - `v7_no_crop`: pre-state commit 4362 ms; restore = group rollback, 2994 ms; post-rollback element-override read-back 1954 ms (restored)
  - `v8_no_crop_fiducials` suppression: 21728 ms (category_override_writes 21586, element_overrides 55, category_override_reads 18, category_collect 16, build_override 2)
  - `v8_no_crop_fiducials`: pre-state commit 3392 ms; restore = group rollback, 3334 ms; post-rollback element-override read-back 2018 ms (restored)
  - `v9_registration_marks` suppression: 20138 ms (category_override_writes 19991, element_overrides 55, category_override_reads 29, category_collect 6, subcategory_walk 2, build_override 2)
  - `v9_registration_marks`: pre-state commit 3972 ms; restore = group rollback, 2997 ms; post-rollback element-override read-back 1973 ms (restored)
  - `v10_element_only_white` suppression WITHOUT the category layer: 86 ms (element_overrides 75, build_override 1)
  - `v10_element_only_white`: pre-state commit 4314 ms; restore = group rollback, 3329 ms; post-rollback element-override read-back 2066 ms (restored)
- F2 fiducials: 5140547 (Rooms), 12005562 (Property Line Segments); separated 437.5 ft on u and 1685.3 ft on v (1232 of 4837 candidates kept, reference model_crop_a)

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
| v0_control | 4285x10000 | 4285x10000 | 0 | 0 | yes | pass |
| v7_no_crop | 4285x10000 | 4285x10000 | 0 | 0 | yes | pass |
| v8_no_crop_fiducials | 4285x10000 | 4285x10000 | 0 | 0 | yes | pass |
| v9_registration_marks | 4285x10000 | 4285x10000 | 0 | 0 | yes | pass |
| v10_element_only_white | 4285x10000 | 4285x10000 | 0 | 0 | yes | pass |

`dw`/`dh` are image minus `frame_px`. `dim_check` is the sidecar's own verdict, which inspects the requested axis only (finding F4) -- so a nonzero `dh` beside `dim_check=pass` is exactly the case F4 names.

### 2. Registration fit (measured from the pixels)

| variant | samples | px/ft u | px/ft v | frame px/ft | offset at (u0,v1) px | residual med/max px | v axis |
|---|---|---|---|---|---|---|---|
| v0_control | 28 pairs | 4.143 | 4.123 | 4.148 | (6.0, 11.8) | 3.07 / 27.52 | negative (expected) |
| v7_no_crop | 284 pairs | 4.160 | 4.261 | 4.148 | (-9.0, -57.7) | 3.90 / 54.95 | negative (expected) |
| v8_no_crop_fiducials | 284 pairs | 4.160 | 4.261 | 4.148 | (-9.0, -57.7) | 3.90 / 54.95 | negative (expected) |
| v9_registration_marks | 294 pairs | 4.153 | 4.149 | 4.148 | (-3.8, -1.2) | 2.61 / 54.79 | negative (expected) |
| v10_element_only_white | 284 pairs | 4.160 | 4.261 | 4.148 | (-9.0, -57.7) | 3.90 / 54.95 | negative (expected) |

- `v0_control` frame: registration.rendered_uv
- `v7_no_crop` frame: registration.rendered_uv
- `v8_no_crop_fiducials` frame: registration.rendered_uv
- `v9_registration_marks` frame: registration.rendered_uv
- `v10_element_only_white` frame: registration.rendered_uv

#### 2b. Fitted per-side margin (how much bigger the render is than the frame)

| variant | left | right | top | bottom |  |
|---|---|---|---|---|---|
| v0_control | 1.44 ft / 0.180 in | -0.31 ft / -0.039 in | 2.86 ft / 0.358 in | 11.60 ft / 1.450 in |  |
| v7_no_crop | -2.16 ft / -0.270 in | -0.92 ft / -0.115 in | -13.53 ft / -1.692 in | -50.75 ft / -6.344 in |  |
| v8_no_crop_fiducials | -2.16 ft / -0.270 in | -0.92 ft / -0.115 in | -13.53 ft / -1.692 in | -50.75 ft / -6.344 in |  |
| v9_registration_marks | -0.92 ft / -0.115 in | -0.36 ft / -0.045 in | -0.29 ft / -0.036 in | -0.39 ft / -0.049 in |  |
| v10_element_only_white | -2.16 ft / -0.270 in | -0.92 ft / -0.115 in | -13.53 ft / -1.692 in | -50.75 ft / -6.344 in |  |

#### 2c. The fit's own premise: does the ink sit where the bbox says?

The fit matches an element's exact-colour centroid against the centre of its recorded `bbox_uv`. **Those coincide only when the ink fills the box**, which real text, a tag with a leader or a dimension need not do. A displacement that is the SAME for every element is absorbed into the fitted intercept, so it leaves the residuals in 2 clean while shifting every margin in 2b by exactly that amount; one that varies with size or position corrupts the scale instead and does inflate the residuals. Both are surfaced here. No tolerance is applied -- what is close enough for a margin figure is your call.

| variant | ink-bbox px/ft u | ink-bbox px/ft v | px/ft u delta | px/ft v delta | margin delta ft (l/r/t/b) |  |
|---|---|---|---|---|---|---|
| v0_control | 4.144 | 4.147 | -0.0010 | -0.0240 | 0.899 / -0.649 / 2.982 / 11.069 |  |
| v7_no_crop | 4.154 | 4.136 | 0.0064 | 0.1247 | -0.805 / -0.787 / -14.659 / -56.086 |  |
| v8_no_crop_fiducials | 4.154 | 4.136 | 0.0064 | 0.1247 | -0.805 / -0.787 / -14.659 / -56.086 |  |
| v9_registration_marks | 4.150 | 4.148 | 0.0031 | 0.0012 | -0.247 / -0.525 / -0.075 / -0.631 |  |
| v10_element_only_white | 4.154 | 4.136 | 0.0064 | 0.1247 | -0.805 / -0.787 / -14.659 / -56.086 |  |

The second anchor is the centre of the ink's own bounding box rather than its centroid. The two are identical when the ink fills its bbox and diverge when it does not, so a row of ~0 deltas says the anchor choice does not matter on this capture and the margins above do not rest on the premise.

A UNIFORM displacement -- every element's ink sitting the same distance off its box centre -- is NOT recoverable from a capture: nothing distinguishes it from the whole render sitting that far over, which is why it is the dangerous case. What IS recoverable is whether such a displacement is POSSIBLE, and by how much: ink that spans its whole box cannot be off-centre in it. The table below bounds it.

##### `v0_control` does the ink fill its recorded bbox?

| category | n | ink span / bbox u | ink span / bbox v | solidity | possible offset u px | possible offset v px |
|---|---|---|---|---|---|---|
| Dimensions | 2 | 0.889 (0.776 .. 1.001) | 0.937 (0.868 .. 1.007) | 0.195 (0.183 .. 0.206) | 0.5 (0.0 .. 1.0) | 0.2 (0.0 .. 0.5) |
| Grids | 19 | 1.002 (0.905 .. 1.086) | 1.007 (1.007 .. 1.091) | 0.048 (0.041 .. 0.056) | 0.0 (0.0 .. 0.8) | 0.0 (0.0 .. 0.0) |
| Lines | 1 | 0.740 (0.740 .. 0.740) | 0.744 (0.744 .. 0.744) | 1.000 (1.000 .. 1.000) | 0.2 (0.2 .. 0.2) | 0.2 (0.2 .. 0.2) |
| Revision Cloud Tags | 1 | 0.755 (0.755 .. 0.755) | 0.665 (0.665 .. 0.665) | 0.319 (0.319 .. 0.319) | 1.5 (1.5 .. 1.5) | 2.0 (2.0 .. 2.0) |
| Revision Clouds | 1 | 1.063 (1.063 .. 1.063) | 1.016 (1.016 .. 1.016) | 0.112 (0.112 .. 0.112) | 0.0 (0.0 .. 0.0) | 0.0 (0.0 .. 0.0) |
| Views | 3 | 0.945 (0.910 .. 1.013) | 1.011 (1.010 .. 1.013) | 0.034 (0.030 .. 0.042) | 3.4 (0.0 .. 3.7) | 0.0 (0.0 .. 0.0) |

`ink span / bbox` of 1.0 means the ink reaches both edges of its recorded box, so it cannot be off-centre in it and the margins above are bounded. `solidity` is ink pixels over ink bbox area: below 1 the ink is not a solid rectangle -- a glyph run, an L, a leader -- so its centroid can also sit away from its own bbox centre. `possible offset` is how far, in pixels, a margin in 2b could be wrong because of this.

##### `v7_no_crop` does the ink fill its recorded bbox?

| category | n | ink span / bbox u | ink span / bbox v | solidity | possible offset u px | possible offset v px |
|---|---|---|---|---|---|---|
| Dimensions | 135 | 0.962 (0.240 .. 20.416) | 0.959 (0.430 .. 4.520) | 0.107 (0.000 .. 0.396) | 0.5 (0.0 .. 26.9) | 0.8 (0.0 .. 34.5) |
| Door Tags | 25 | 0.941 (0.604 .. 1.282) | 1.095 (0.811 .. 1.252) | 0.192 (0.090 .. 0.298) | 0.4 (0.0 .. 2.6) | 0.0 (0.0 .. 1.3) |
| Grids | 19 | 0.998 (0.901 .. 1.082) | 0.975 (0.975 .. 1.056) | 0.046 (0.040 .. 0.054) | 0.8 (0.0 .. 1.2) | 6.8 (0.0 .. 6.8) |
| Lines | 1 | 0.737 (0.737 .. 0.737) | 0.719 (0.719 .. 0.719) | 1.000 (1.000 .. 1.000) | 0.2 (0.2 .. 0.2) | 0.2 (0.2 .. 0.2) |
| Revision Cloud Tags | 1 | 0.752 (0.752 .. 0.752) | 0.643 (0.643 .. 0.643) | 0.319 (0.319 .. 0.319) | 1.5 (1.5 .. 1.5) | 2.2 (2.2 .. 2.2) |
| Revision Clouds | 1 | 1.059 (1.059 .. 1.059) | 0.983 (0.983 .. 0.983) | 0.108 (0.108 .. 0.108) | 0.0 (0.0 .. 0.0) | 0.5 (0.5 .. 0.5) |
| Room Tags | 18 | 1.032 (0.927 .. 1.042) | 0.955 (0.876 .. 0.955) | 0.223 (0.183 .. 0.256) | 0.0 (0.0 .. 1.1) | 0.3 (0.3 .. 1.3) |
| Views | 3 | 0.941 (0.907 .. 1.009) | 0.978 (0.977 .. 0.980) | 0.034 (0.030 .. 0.041) | 3.7 (0.0 .. 3.9) | 0.7 (0.6 .. 0.7) |
| Wall Tags | 53 | 1.022 (0.809 .. 1.136) | 1.039 (0.983 .. 1.152) | 0.128 (0.088 .. 0.512) | 0.0 (0.0 .. 1.5) | 0.0 (0.0 .. 0.3) |
| Window Tags | 27 | 0.472 (0.405 .. 0.719) | 0.890 (0.791 .. 0.890) | 0.339 (0.212 .. 0.460) | 3.9 (2.0 .. 4.4) | 0.6 (0.6 .. 1.1) |

`ink span / bbox` of 1.0 means the ink reaches both edges of its recorded box, so it cannot be off-centre in it and the margins above are bounded. `solidity` is ink pixels over ink bbox area: below 1 the ink is not a solid rectangle -- a glyph run, an L, a leader -- so its centroid can also sit away from its own bbox centre. `possible offset` is how far, in pixels, a margin in 2b could be wrong because of this.

##### `v8_no_crop_fiducials` does the ink fill its recorded bbox?

| category | n | ink span / bbox u | ink span / bbox v | solidity | possible offset u px | possible offset v px |
|---|---|---|---|---|---|---|
| Dimensions | 135 | 0.962 (0.240 .. 20.416) | 0.959 (0.430 .. 4.520) | 0.107 (0.000 .. 0.396) | 0.5 (0.0 .. 26.9) | 0.8 (0.0 .. 34.5) |
| Door Tags | 25 | 0.941 (0.604 .. 1.282) | 1.095 (0.811 .. 1.252) | 0.192 (0.090 .. 0.298) | 0.4 (0.0 .. 2.6) | 0.0 (0.0 .. 1.3) |
| Grids | 19 | 0.998 (0.901 .. 1.082) | 0.975 (0.975 .. 1.056) | 0.046 (0.040 .. 0.054) | 0.8 (0.0 .. 1.2) | 6.8 (0.0 .. 6.8) |
| Lines | 1 | 0.737 (0.737 .. 0.737) | 0.719 (0.719 .. 0.719) | 1.000 (1.000 .. 1.000) | 0.2 (0.2 .. 0.2) | 0.2 (0.2 .. 0.2) |
| Revision Cloud Tags | 1 | 0.752 (0.752 .. 0.752) | 0.643 (0.643 .. 0.643) | 0.319 (0.319 .. 0.319) | 1.5 (1.5 .. 1.5) | 2.2 (2.2 .. 2.2) |
| Revision Clouds | 1 | 1.059 (1.059 .. 1.059) | 0.983 (0.983 .. 0.983) | 0.108 (0.108 .. 0.108) | 0.0 (0.0 .. 0.0) | 0.5 (0.5 .. 0.5) |
| Room Tags | 18 | 1.032 (0.927 .. 1.042) | 0.955 (0.876 .. 0.955) | 0.223 (0.183 .. 0.256) | 0.0 (0.0 .. 1.1) | 0.3 (0.3 .. 1.3) |
| Views | 3 | 0.941 (0.907 .. 1.009) | 0.978 (0.977 .. 0.980) | 0.034 (0.030 .. 0.041) | 3.7 (0.0 .. 3.9) | 0.7 (0.6 .. 0.7) |
| Wall Tags | 53 | 1.022 (0.809 .. 1.136) | 1.039 (0.983 .. 1.152) | 0.128 (0.088 .. 0.512) | 0.0 (0.0 .. 1.5) | 0.0 (0.0 .. 0.3) |
| Window Tags | 27 | 0.472 (0.405 .. 0.719) | 0.890 (0.791 .. 0.890) | 0.339 (0.212 .. 0.460) | 3.9 (2.0 .. 4.4) | 0.6 (0.6 .. 1.1) |

`ink span / bbox` of 1.0 means the ink reaches both edges of its recorded box, so it cannot be off-centre in it and the margins above are bounded. `solidity` is ink pixels over ink bbox area: below 1 the ink is not a solid rectangle -- a glyph run, an L, a leader -- so its centroid can also sit away from its own bbox centre. `possible offset` is how far, in pixels, a margin in 2b could be wrong because of this.

##### `v9_registration_marks` does the ink fill its recorded bbox?

| category | n | ink span / bbox u | ink span / bbox v | solidity | possible offset u px | possible offset v px |
|---|---|---|---|---|---|---|
| Dimensions | 135 | 0.963 (0.240 .. 20.452) | 0.985 (0.441 .. 4.643) | 0.107 (0.000 .. 0.396) | 0.4 (0.0 .. 26.9) | 0.4 (0.0 .. 32.9) |
| Door Tags | 25 | 0.943 (0.605 .. 1.284) | 1.125 (0.833 .. 1.286) | 0.192 (0.090 .. 0.298) | 0.4 (0.0 .. 2.6) | 0.0 (0.0 .. 1.1) |
| Grids | 19 | 1.000 (0.903 .. 1.084) | 1.001 (1.001 .. 1.085) | 0.046 (0.040 .. 0.054) | 0.0 (0.0 .. 0.8) | 0.0 (0.0 .. 0.0) |
| Lines | 1 | 0.738 (0.738 .. 0.738) | 0.739 (0.739 .. 0.739) | 1.000 (1.000 .. 1.000) | 0.2 (0.2 .. 0.2) | 0.2 (0.2 .. 0.2) |
| Revision Cloud Tags | 1 | 0.754 (0.754 .. 0.754) | 0.661 (0.661 .. 0.661) | 0.319 (0.319 .. 0.319) | 1.5 (1.5 .. 1.5) | 2.1 (2.1 .. 2.1) |
| Revision Clouds | 1 | 1.060 (1.060 .. 1.060) | 1.009 (1.009 .. 1.009) | 0.108 (0.108 .. 0.108) | 0.0 (0.0 .. 0.0) | 0.0 (0.0 .. 0.0) |
| Room Tags | 18 | 1.034 (0.929 .. 1.043) | 0.980 (0.900 .. 0.980) | 0.223 (0.183 .. 0.256) | 0.0 (0.0 .. 1.0) | 0.1 (0.1 .. 1.0) |
| Views | 3 | 0.943 (0.908 .. 1.011) | 1.005 (1.004 .. 1.006) | 0.034 (0.030 .. 0.041) | 3.6 (0.0 .. 3.8) | 0.0 (0.0 .. 0.0) |
| Wall Tags | 53 | 1.024 (0.811 .. 1.138) | 1.067 (1.010 .. 1.183) | 0.128 (0.088 .. 0.512) | 0.0 (0.0 .. 1.5) | 0.0 (0.0 .. 0.0) |
| Window Tags | 27 | 0.473 (0.406 .. 0.721) | 0.914 (0.813 .. 0.914) | 0.339 (0.212 .. 0.460) | 3.9 (1.9 .. 4.4) | 0.4 (0.4 .. 0.9) |

`ink span / bbox` of 1.0 means the ink reaches both edges of its recorded box, so it cannot be off-centre in it and the margins above are bounded. `solidity` is ink pixels over ink bbox area: below 1 the ink is not a solid rectangle -- a glyph run, an L, a leader -- so its centroid can also sit away from its own bbox centre. `possible offset` is how far, in pixels, a margin in 2b could be wrong because of this.

##### `v10_element_only_white` does the ink fill its recorded bbox?

| category | n | ink span / bbox u | ink span / bbox v | solidity | possible offset u px | possible offset v px |
|---|---|---|---|---|---|---|
| Dimensions | 135 | 0.962 (0.240 .. 20.416) | 0.959 (0.430 .. 4.520) | 0.107 (0.000 .. 0.396) | 0.5 (0.0 .. 26.9) | 0.8 (0.0 .. 34.5) |
| Door Tags | 25 | 0.941 (0.604 .. 1.282) | 1.095 (0.811 .. 1.252) | 0.192 (0.090 .. 0.298) | 0.4 (0.0 .. 2.6) | 0.0 (0.0 .. 1.3) |
| Grids | 19 | 0.998 (0.901 .. 1.082) | 0.975 (0.975 .. 1.056) | 0.046 (0.040 .. 0.054) | 0.8 (0.0 .. 1.2) | 6.8 (0.0 .. 6.8) |
| Lines | 1 | 0.737 (0.737 .. 0.737) | 0.719 (0.719 .. 0.719) | 1.000 (1.000 .. 1.000) | 0.2 (0.2 .. 0.2) | 0.2 (0.2 .. 0.2) |
| Revision Cloud Tags | 1 | 0.752 (0.752 .. 0.752) | 0.643 (0.643 .. 0.643) | 0.319 (0.319 .. 0.319) | 1.5 (1.5 .. 1.5) | 2.2 (2.2 .. 2.2) |
| Revision Clouds | 1 | 1.059 (1.059 .. 1.059) | 0.983 (0.983 .. 0.983) | 0.108 (0.108 .. 0.108) | 0.0 (0.0 .. 0.0) | 0.5 (0.5 .. 0.5) |
| Room Tags | 18 | 1.032 (0.927 .. 1.042) | 0.955 (0.876 .. 0.955) | 0.223 (0.183 .. 0.256) | 0.0 (0.0 .. 1.1) | 0.3 (0.3 .. 1.3) |
| Views | 3 | 0.941 (0.907 .. 1.009) | 0.978 (0.977 .. 0.980) | 0.034 (0.030 .. 0.041) | 3.7 (0.0 .. 3.9) | 0.7 (0.6 .. 0.7) |
| Wall Tags | 53 | 1.022 (0.809 .. 1.136) | 1.039 (0.983 .. 1.152) | 0.128 (0.088 .. 0.512) | 0.0 (0.0 .. 1.5) | 0.0 (0.0 .. 0.3) |
| Window Tags | 27 | 0.472 (0.405 .. 0.719) | 0.890 (0.791 .. 0.890) | 0.339 (0.212 .. 0.460) | 3.9 (2.0 .. 4.4) | 0.6 (0.6 .. 1.1) |

`ink span / bbox` of 1.0 means the ink reaches both edges of its recorded box, so it cannot be off-centre in it and the margins above are bounded. `solidity` is ink pixels over ink bbox area: below 1 the ink is not a solid rectangle -- a glyph run, an L, a leader -- so its centroid can also sit away from its own bbox centre. `possible offset` is how far, in pixels, a margin in 2b could be wrong because of this.

- `v0_control` samples: matched 28, no bbox 1, colour absent 258, clipped at an image edge 0.
- `v7_no_crop` samples: matched 284, no bbox 1, colour absent 2, clipped at an image edge 0.
- `v8_no_crop_fiducials` samples: matched 284, no bbox 1, colour absent 2, clipped at an image edge 0.
- `v9_registration_marks` samples: matched 294, no bbox 1, colour absent 4, clipped at an image edge 0.
- `v10_element_only_white` samples: matched 284, no bbox 1, colour absent 2, clipped at an image edge 0.

### 3. How far recorded annotation bboxes reach past the rendered frame

| variant | left | right | top | bottom | bboxes outside |  |
|---|---|---|---|---|---|---|
| v0_control | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0 of 286 |  |
| v7_no_crop | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0 of 286 |  |
| v8_no_crop_fiducials | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0 of 286 |  |
| v9_registration_marks | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0 of 298 |  |
| v10_element_only_white | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0.00 ft / 0.000 in | 0 of 286 |  |

Read this table against 2b. Where they agree, the render is behaving as if it had been fitted to the crop unioned with the annotations drawn beyond it.

### 4. Coverage per category

#### `v0_control`

Ink test threshold >2% non-white, placed through the fitted mapping from measurement (2).

| category | assigned | painted | colour present | bbox | ink tested | ink present | bbox off image | ink untested |
|---|---|---|---|---|---|---|---|---|
| <no category> | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| Attached Detail Groups | 1 | 1 | 0 | 1 | 1 | 1 | 0 | 0 |
| Dimensions | 135 | 135 | 2 | 135 | 135 | 75 | 0 | 0 |
| Door Tags | 26 | 26 | 0 | 26 | 26 | 12 | 0 | 0 |
| Grids | 19 | 19 | 19 | 19 | 19 | 19 | 0 | 0 |
| Lines | 2 | 2 | 2 | 2 | 2 | 2 | 0 | 0 |
| Revision Cloud Tags | 1 | 1 | 1 | 1 | 1 | 1 | 0 | 0 |
| Revision Clouds | 1 | 1 | 1 | 1 | 1 | 1 | 0 | 0 |
| Room Tags | 18 | 18 | 0 | 18 | 18 | 8 | 0 | 0 |
| Views | 3 | 3 | 3 | 3 | 3 | 3 | 0 | 0 |
| Wall Tags | 53 | 53 | 0 | 53 | 53 | 10 | 0 | 0 |
| Window Tags | 27 | 27 | 0 | 27 | 27 | 1 | 0 | 0 |

#### `v7_no_crop`

Ink test threshold >2% non-white, placed through the fitted mapping from measurement (2).

| category | assigned | painted | colour present | bbox | ink tested | ink present | bbox off image | ink untested |
|---|---|---|---|---|---|---|---|---|
| <no category> | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| Attached Detail Groups | 1 | 1 | 0 | 1 | 1 | 1 | 0 | 0 |
| Dimensions | 135 | 135 | 135 | 135 | 135 | 135 | 0 | 0 |
| Door Tags | 26 | 26 | 25 | 26 | 26 | 26 | 0 | 0 |
| Grids | 19 | 19 | 19 | 19 | 19 | 18 | 0 | 0 |
| Lines | 2 | 2 | 2 | 2 | 2 | 2 | 0 | 0 |
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
| Lines | 2 | 2 | 2 | 2 | 2 | 2 | 0 | 0 |
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
| Lines | 14 | 14 | 12 | 14 | 14 | 6 | 0 | 0 |
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
| Lines | 2 | 2 | 2 | 2 | 2 | 2 | 0 | 0 |
| Revision Cloud Tags | 1 | 1 | 1 | 1 | 1 | 1 | 0 | 0 |
| Revision Clouds | 1 | 1 | 1 | 1 | 1 | 1 | 0 | 0 |
| Room Tags | 18 | 18 | 18 | 18 | 18 | 18 | 0 | 0 |
| Views | 3 | 3 | 3 | 3 | 3 | 3 | 0 | 0 |
| Wall Tags | 53 | 53 | 53 | 53 | 53 | 53 | 0 | 0 |
| Window Tags | 27 | 27 | 27 | 27 | 27 | 27 | 0 | 0 |

### 5. Non-white, non-palette pixels

| variant | total | white | palette | off-palette | off-palette frac | blend | grey | other | distinct | tally capped |
|---|---|---|---|---|---|---|---|---|---|---|
| v0_control | 42850000 | 42829690 | 14432 | 5878 | 0.00014 | 1831 | 4047 | 0 | 197 | no |
| v7_no_crop | 42850000 | 42805542 | 36789 | 7669 | 0.00018 | 7338 | 330 | 1 | 1481 | no |
| v8_no_crop_fiducials | 42850000 | 42805542 | 36789 | 7669 | 0.00018 | 7338 | 330 | 1 | 1481 | no |
| v9_registration_marks | 42850000 | 42804582 | 37749 | 7669 | 0.00018 | 7338 | 330 | 1 | 1481 | no |
| v10_element_only_white | 42850000 | 42805542 | 36789 | 7669 | 0.00018 | 7338 | 330 | 1 | 1481 | no |

`blend` is within 3 RGB units of the segment between some palette colour and white. `grey` is all three channels within 3 of each other. The two OVERLAP -- a grey pixel is also collinear with white -- and blend is tested first; the `blend also grey` count below states what that ordering costs.

- `v0_control` blend also grey: 0 px.
- `v7_no_crop` blend also grey: 0 px.
- `v8_no_crop_fiducials` blend also grey: 0 px.
- `v9_registration_marks` blend also grey: 0 px.
- `v10_element_only_white` blend also grey: 0 px.

#### `v0_control` top 10 off-palette colours

| rgb | pixels | class | also grey |
|---|---|---|---|
| [0, 0, 0] | 3722 | grey | yes |
| [195, 195, 195] | 93 | grey | yes |
| [236, 236, 236] | 46 | grey | yes |
| [254, 197, 129] | 40 | blend | no |
| [167, 167, 167] | 38 | grey | yes |
| [168, 137, 254] | 32 | blend | no |
| [254, 143, 143] | 32 | blend | no |
| [254, 161, 132] | 32 | blend | no |
| [95, 253, 231] | 30 | blend | no |
| [103, 146, 253] | 30 | blend | no |

#### `v7_no_crop` top 10 off-palette colours

| rgb | pixels | class | also grey |
|---|---|---|---|
| [195, 195, 195] | 92 | grey | yes |
| [236, 236, 236] | 46 | grey | yes |
| [254, 197, 129] | 40 | blend | no |
| [167, 167, 167] | 39 | grey | yes |
| [168, 137, 254] | 32 | blend | no |
| [182, 168, 254] | 32 | blend | no |
| [254, 143, 143] | 32 | blend | no |
| [254, 161, 132] | 32 | blend | no |
| [95, 253, 231] | 30 | blend | no |
| [103, 146, 253] | 30 | blend | no |

#### `v8_no_crop_fiducials` top 10 off-palette colours

| rgb | pixels | class | also grey |
|---|---|---|---|
| [195, 195, 195] | 92 | grey | yes |
| [236, 236, 236] | 46 | grey | yes |
| [254, 197, 129] | 40 | blend | no |
| [167, 167, 167] | 39 | grey | yes |
| [168, 137, 254] | 32 | blend | no |
| [182, 168, 254] | 32 | blend | no |
| [254, 143, 143] | 32 | blend | no |
| [254, 161, 132] | 32 | blend | no |
| [95, 253, 231] | 30 | blend | no |
| [103, 146, 253] | 30 | blend | no |

#### `v9_registration_marks` top 10 off-palette colours

| rgb | pixels | class | also grey |
|---|---|---|---|
| [195, 195, 195] | 92 | grey | yes |
| [236, 236, 236] | 46 | grey | yes |
| [254, 197, 129] | 40 | blend | no |
| [167, 167, 167] | 39 | grey | yes |
| [168, 137, 254] | 32 | blend | no |
| [182, 168, 254] | 32 | blend | no |
| [254, 143, 143] | 32 | blend | no |
| [254, 161, 132] | 32 | blend | no |
| [95, 253, 231] | 30 | blend | no |
| [103, 146, 253] | 30 | blend | no |

#### `v10_element_only_white` top 10 off-palette colours

| rgb | pixels | class | also grey |
|---|---|---|---|
| [195, 195, 195] | 92 | grey | yes |
| [236, 236, 236] | 46 | grey | yes |
| [254, 197, 129] | 40 | blend | no |
| [167, 167, 167] | 39 | grey | yes |
| [168, 137, 254] | 32 | blend | no |
| [182, 168, 254] | 32 | blend | no |
| [254, 143, 143] | 32 | blend | no |
| [254, 161, 132] | 32 | blend | no |
| [95, 253, 231] | 30 | blend | no |
| [103, 146, 253] | 30 | blend | no |

### 6. Model TIFF hash vs V0

| variant | model sha256 (16) | V0 sha256 (16) | equal |
|---|---|---|---|
| v0_control | b932d4db9985ecea | b932d4db9985ecea | yes |
| v7_no_crop | b932d4db9985ecea | b932d4db9985ecea | yes |
| v8_no_crop_fiducials | b932d4db9985ecea | b932d4db9985ecea | yes |
| v9_registration_marks | b932d4db9985ecea | b932d4db9985ecea | yes |
| v10_element_only_white | b932d4db9985ecea | b932d4db9985ecea | yes |

The model pass runs ONCE per view, so this column detects a variant CLOBBERING the model artifact -- a path collision, a stray write. Whether a variant changed how the model pass RENDERS is the probe's own `model_reexport_after_variants` verdict, quoted at the top of this view's section, which has its own repeatability control.

### 7. F1 -- the crop boundary, recovered from the pixels

Q1: does the exported image contain the crop boundary, and does its recovered position match `view.CropBox`? A boundary is a band of rows (or columns) each holding a straight run of non-white, non-palette, non-fiducial pixels of >= 25% of the image, closing into one rectangle (or matching the crop shape's levels). `NOT FOUND` on a V8 row is the answer that ImageExportOptions does not draw it.

| variant | probe turned it on | boundary | row/col bands | px/ft u | px/ft v | lattice px/ft | u/v isotropy | boundary px rects (subtract these) |  |
|---|---|---|---|---|---|---|---|---|---|
| v0_control | no | NOT_FOUND | 1 / 0 | -- | -- | 4.1476 | -- | -- | 1 row band(s) and 0 column band(s) long enough to be a crop edge, but no four of them close into one rectangle |
| v7_no_crop | no | NOT_FOUND | 0 / 0 | -- | -- | 4.1476 | -- | -- | no row or column holds a straight non-palette run of >= 25% of the image: the crop boundary is NOT in this capture |
| v8_no_crop_fiducials | yes | NOT_FOUND | 0 / 0 | -- | -- | 4.1476 | -- | -- | no row or column holds a straight non-palette run of >= 25% of the image: the crop boundary is NOT in this capture |
| v9_registration_marks | yes | NOT_FOUND | 0 / 0 | -- | -- | 4.1476 | -- | -- | no row or column holds a straight non-palette run of >= 25% of the image: the crop boundary is NOT in this capture |
| v10_element_only_white | no | NOT_FOUND | 0 / 0 | -- | -- | 4.1476 | -- | -- | no row or column holds a straight non-palette run of >= 25% of the image: the crop boundary is NOT in this capture |

`isotropy` of 1 means the boundary's pixel rectangle has the crop's own aspect -- the recovered position is the crop's shape at one uniform scale. `lattice px/ft` is the model capture's (1 / achieved fpp): an untouched capture that rendered exactly the authored crop at the requested count would match it.

- `v8_no_crop_fiducials` MODEL capture (boundary drawn at crop A [-666.1001379893444, 385.7497439023258, 367.03918212172607, 2796.8100008709853]): NOT_FOUND -- no row or column holds a straight non-palette run of >= 25% of the image: the crop boundary is NOT in this capture
- `v9_registration_marks` MODEL capture (boundary drawn at crop A [-666.1001379893444, 385.7497439023258, 367.03918212172607, 2796.8100008709853]): NOT_FOUND -- no row or column holds a straight non-palette run of >= 25% of the image: the crop boundary is NOT in this capture

### 8. F2 -- the fiducial pair

| variant | element | colour | found | pixels | ink px bbox | clipped | recorded rect_uv |
|---|---|---|---|---|---|---|---|
| v8_no_crop_fiducials | 5140547 | [251, 11, 139] | no | -- | -- | -- | [-122.13886473368413, 2273.724510817523, -114.13886473368413, 2279.724510817523] |
| v8_no_crop_fiducials | 12005562 | [11, 139, 251] | no | -- | -- | -- | [311.7405726032978, 536.3334597452382, 327.0359028240714, 646.4159137735551] |
| v9_registration_marks | 5140547 | [251, 11, 139] | no | -- | -- | -- | [-122.13886473368413, 2273.724510817523, -114.13886473368413, 2279.724510817523] |
| v9_registration_marks | 12005562 | [11, 139, 251] | no | -- | -- | -- | [311.7405726032978, 536.3334597452382, 327.0359028240714, 646.4159137735551] |

| fit | px/ft u | px/ft v | u/v isotropy | residual max px u / v |  |
|---|---|---|---|---|---|
| `v8_no_crop_fiducials` F2 (recorded bbox) | -- | -- | -- | -- | 0 usable fiducial(s) of 2; F2 needs both, unclipped, in their reserved colours, each with a UV extent |
| `v8_no_crop_fiducials` F2 (model-anchored) | -- | -- | -- | -- | 2 of 2 fiducial(s) have no colour in the model capture |
| `v9_registration_marks` F2 (recorded bbox) | -- | -- | -- | -- | 0 usable fiducial(s) of 2; F2 needs both, unclipped, in their reserved colours, each with a UV extent |
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
| v8_no_crop_fiducials | F2_vs_bbox_fit | -- | -- | -- | one of the two mappings is not available |
| v9_registration_marks | F1_vs_F2 | -- | -- | -- | one of the two mappings is not available |
| v9_registration_marks | F1_vs_bbox_fit | -- | -- | -- | one of the two mappings is not available |
| v9_registration_marks | F2_vs_bbox_fit | -- | -- | -- | one of the two mappings is not available |
| v9_registration_marks | F3_vs_F2 | -- | -- | -- | one of the two mappings is not available |
| v9_registration_marks | F3_vs_bbox_fit | -0.0051 | -0.0012 | 0.78 |  |
| v10_element_only_white | F1_vs_F2 | -- | -- | -- | one of the two mappings is not available |
| v10_element_only_white | F1_vs_bbox_fit | -- | -- | -- | one of the two mappings is not available |
| v10_element_only_white | F2_vs_bbox_fit | -- | -- | -- | one of the two mappings is not available |

### 10. Datum extents: ink vs the recorded (authored-crop) bbox

Q3: do datum extents in the capture match the drawing? Positive means the ink reaches FURTHER than the bbox recorded before the capture touched the view. Under V0 the crop was widened to B, which lengthens datums; under V7/V8 it was not.

| variant | datum | category | map | length delta ft | paper in | per side ft l/r/t/b |  |
|---|---|---|---|---|---|---|---|
| v0_control | 3693278 | Grids | bbox_fit | +0.67 | +0.083 | +0.88 / -0.21 / +0.24 / +0.13 |  |
| v0_control | 3693539 | Grids | bbox_fit | +0.67 | +0.083 | +0.88 / -0.21 / +0.30 / +0.07 |  |
| v0_control | 3693688 | Grids | bbox_fit | +0.67 | +0.083 | +0.88 / -0.21 / -0.17 / +0.29 |  |
| v0_control | 3694573 | Grids | bbox_fit | +0.94 | +0.117 | +0.87 / -0.53 / +0.37 / +0.57 |  |
| v0_control | 3694705 | Grids | bbox_fit | +0.94 | +0.117 | +0.22 / -0.60 / +0.37 / +0.57 |  |
| v0_control | 3694782 | Grids | bbox_fit | +0.94 | +0.117 | +0.76 / -0.42 / +0.37 / +0.57 |  |
| v0_control | 3694880 | Grids | bbox_fit | +0.94 | +0.117 | +0.11 / -0.49 / +0.37 / +0.57 |  |
| v0_control | 3694982 | Grids | bbox_fit | +0.94 | +0.117 | +0.18 / -0.32 / +0.37 / +0.57 |  |
| v0_control | 3695045 | Grids | bbox_fit | +0.94 | +0.117 | +0.73 / -0.39 / +0.37 / +0.57 |  |
| v0_control | 3695123 | Grids | bbox_fit | +0.94 | +0.117 | +0.60 / -0.26 / +0.37 / +0.57 |  |
| v0_control | 3695201 | Grids | bbox_fit | +0.94 | +0.117 | +0.67 / -0.33 / +0.37 / +0.57 |  |
| v0_control | 3697629 | Grids | bbox_fit | +0.94 | +0.117 | +0.08 / -0.46 / +0.37 / +0.57 |  |
| v0_control | 3698218 | Grids | bbox_fit | +0.94 | +0.117 | +0.00 / -0.38 / +0.37 / +0.57 |  |
| v0_control | 3784562 | Grids | bbox_fit | +0.67 | +0.083 | +0.88 / -0.21 / -0.11 / +0.23 |  |
| v0_control | 3839177 | Grids | bbox_fit | +0.56 | +0.070 | +0.86 / -0.30 / +0.46 / -0.09 |  |
| v0_control | 3839178 | Grids | bbox_fit | +0.56 | +0.070 | +0.86 / -0.30 / +0.54 / -0.17 |  |
| v0_control | 3923006 | Grids | bbox_fit | +0.94 | +0.117 | +0.82 / -0.48 / +0.37 / +0.57 |  |
| v0_control | 5136165 | Grids | bbox_fit | +0.94 | +0.117 | +0.75 / -0.40 / +0.37 / +0.57 |  |
| v0_control | 6961360 | Grids | bbox_fit | +0.56 | +0.070 | +0.86 / -0.30 / +0.71 / -0.34 |  |
| v7_no_crop | 3693278 | Grids | bbox_fit | -0.52 | -0.065 | -0.45 / -0.07 / -0.23 / +0.46 |  |
| v7_no_crop | 3693539 | Grids | bbox_fit | -0.52 | -0.065 | -0.45 / -0.07 / -0.69 / +0.91 |  |
| v7_no_crop | 3693688 | Grids | bbox_fit | -0.52 | -0.065 | -0.45 / -0.07 / +0.82 / -0.83 |  |
| v7_no_crop | 3694573 | Grids | bbox_fit | -3.19 | -0.399 | -0.30 / +0.62 / -1.74 / -1.45 |  |
| v7_no_crop | 3694705 | Grids | bbox_fit | -3.19 | -0.399 | -0.83 / +0.43 / -1.74 / -1.45 |  |
| v7_no_crop | 3694782 | Grids | bbox_fit | -3.19 | -0.399 | -0.20 / +0.53 / -1.74 / -1.45 |  |
| v7_no_crop | 3694880 | Grids | bbox_fit | -3.19 | -0.399 | -0.73 / +0.34 / -1.74 / -1.45 |  |
| v7_no_crop | 3694982 | Grids | bbox_fit | -3.19 | -0.399 | -0.54 / +0.39 / -1.74 / -1.45 |  |
| v7_no_crop | 3695045 | Grids | bbox_fit | -3.19 | -0.399 | +0.13 / +0.20 / -1.74 / -1.45 |  |
| v7_no_crop | 3695123 | Grids | bbox_fit | -3.19 | -0.399 | +0.17 / +0.16 / -1.74 / -1.45 |  |
| v7_no_crop | 3695201 | Grids | bbox_fit | -3.19 | -0.399 | +0.36 / -0.04 / -1.74 / -1.45 |  |
| v7_no_crop | 3697629 | Grids | bbox_fit | -3.19 | -0.399 | -0.40 / +0.00 / -1.74 / -1.45 |  |
| v7_no_crop | 3698218 | Grids | bbox_fit | -3.19 | -0.399 | -0.24 / -0.16 / -1.74 / -1.45 |  |
| v7_no_crop | 3784562 | Grids | bbox_fit | -0.52 | -0.065 | -0.45 / -0.07 / +0.37 / -0.38 |  |
| v7_no_crop | 3839177 | Grids | bbox_fit | -0.58 | -0.073 | -0.43 / -0.15 / -1.01 / +1.24 |  |
| v7_no_crop | 3839178 | Grids | bbox_fit | -0.58 | -0.073 | -0.43 / -0.15 / -1.91 / +2.14 |  |
| v7_no_crop | 3923006 | Grids | bbox_fit | -3.19 | -0.399 | -0.42 / +0.74 / -1.74 / -1.45 |  |
| v7_no_crop | 5136165 | Grids | bbox_fit | -3.19 | -0.399 | -0.26 / +0.58 / -1.74 / -1.45 |  |
| v7_no_crop | 6961360 | Grids | bbox_fit | -0.58 | -0.073 | -0.43 / -0.15 / -2.22 / +2.45 |  |
| v8_no_crop_fiducials | 3693278 | Grids | bbox_fit | -0.52 | -0.065 | -0.45 / -0.07 / -0.23 / +0.46 |  |
| v8_no_crop_fiducials | 3693539 | Grids | bbox_fit | -0.52 | -0.065 | -0.45 / -0.07 / -0.69 / +0.91 |  |
| v8_no_crop_fiducials | 3693688 | Grids | bbox_fit | -0.52 | -0.065 | -0.45 / -0.07 / +0.82 / -0.83 |  |
| v8_no_crop_fiducials | 3694573 | Grids | bbox_fit | -3.19 | -0.399 | -0.30 / +0.62 / -1.74 / -1.45 |  |
| v8_no_crop_fiducials | 3694705 | Grids | bbox_fit | -3.19 | -0.399 | -0.83 / +0.43 / -1.74 / -1.45 |  |
| v8_no_crop_fiducials | 3694782 | Grids | bbox_fit | -3.19 | -0.399 | -0.20 / +0.53 / -1.74 / -1.45 |  |
| v8_no_crop_fiducials | 3694880 | Grids | bbox_fit | -3.19 | -0.399 | -0.73 / +0.34 / -1.74 / -1.45 |  |
| v8_no_crop_fiducials | 3694982 | Grids | bbox_fit | -3.19 | -0.399 | -0.54 / +0.39 / -1.74 / -1.45 |  |
| v8_no_crop_fiducials | 3695045 | Grids | bbox_fit | -3.19 | -0.399 | +0.13 / +0.20 / -1.74 / -1.45 |  |
| v8_no_crop_fiducials | 3695123 | Grids | bbox_fit | -3.19 | -0.399 | +0.17 / +0.16 / -1.74 / -1.45 |  |
| v8_no_crop_fiducials | 3695201 | Grids | bbox_fit | -3.19 | -0.399 | +0.36 / -0.04 / -1.74 / -1.45 |  |
| v8_no_crop_fiducials | 3697629 | Grids | bbox_fit | -3.19 | -0.399 | -0.40 / +0.00 / -1.74 / -1.45 |  |
| v8_no_crop_fiducials | 3698218 | Grids | bbox_fit | -3.19 | -0.399 | -0.24 / -0.16 / -1.74 / -1.45 |  |
| v8_no_crop_fiducials | 3784562 | Grids | bbox_fit | -0.52 | -0.065 | -0.45 / -0.07 / +0.37 / -0.38 |  |
| v8_no_crop_fiducials | 3839177 | Grids | bbox_fit | -0.58 | -0.073 | -0.43 / -0.15 / -1.01 / +1.24 |  |
| v8_no_crop_fiducials | 3839178 | Grids | bbox_fit | -0.58 | -0.073 | -0.43 / -0.15 / -1.91 / +2.14 |  |
| v8_no_crop_fiducials | 3923006 | Grids | bbox_fit | -3.19 | -0.399 | -0.42 / +0.74 / -1.74 / -1.45 |  |
| v8_no_crop_fiducials | 5136165 | Grids | bbox_fit | -3.19 | -0.399 | -0.26 / +0.58 / -1.74 / -1.45 |  |
| v8_no_crop_fiducials | 6961360 | Grids | bbox_fit | -0.58 | -0.073 | -0.43 / -0.15 / -2.22 / +2.45 |  |
| v9_registration_marks | 3693278 | Grids | F3 | +0.35 | +0.043 | -0.03 / +0.38 / +0.18 / +0.16 |  |
| v9_registration_marks | 3693539 | Grids | F3 | +0.35 | +0.043 | -0.03 / +0.38 / +0.14 / +0.20 |  |
| v9_registration_marks | 3693688 | Grids | F3 | +0.35 | +0.043 | -0.03 / +0.38 / +0.04 / +0.06 |  |
| v9_registration_marks | 3694573 | Grids | F3 | +0.18 | +0.022 | +0.01 / +0.33 / +0.01 / +0.17 |  |
| v9_registration_marks | 3694705 | Grids | F3 | +0.18 | +0.022 | -0.61 / +0.23 / +0.01 / +0.17 |  |
| v9_registration_marks | 3694782 | Grids | F3 | +0.18 | +0.022 | -0.05 / +0.39 / +0.01 / +0.17 |  |
| v9_registration_marks | 3694880 | Grids | F3 | +0.18 | +0.022 | -0.67 / +0.29 / +0.01 / +0.17 |  |
| v9_registration_marks | 3694982 | Grids | F3 | +0.18 | +0.022 | -0.57 / +0.43 / +0.01 / +0.17 |  |
| v9_registration_marks | 3695045 | Grids | F3 | +0.18 | +0.022 | +0.02 / +0.32 / +0.01 / +0.17 |  |
| v9_registration_marks | 3695123 | Grids | F3 | +0.18 | +0.022 | -0.06 / +0.40 / +0.01 / +0.17 |  |
| v9_registration_marks | 3695201 | Grids | F3 | +0.18 | +0.022 | +0.04 / +0.30 / +0.01 / +0.17 |  |
| v9_registration_marks | 3697629 | Grids | F3 | +0.18 | +0.022 | -0.60 / +0.22 / +0.01 / +0.17 |  |
| v9_registration_marks | 3698218 | Grids | F3 | +0.18 | +0.022 | -0.61 / +0.23 / +0.01 / +0.17 |  |
| v9_registration_marks | 3784562 | Grids | F3 | +0.35 | +0.043 | -0.03 / +0.38 / +0.01 / +0.09 |  |
| v9_registration_marks | 3839177 | Grids | F3 | +0.25 | +0.031 | -0.04 / +0.29 / +0.22 / +0.12 |  |
| v9_registration_marks | 3839178 | Grids | F3 | +0.25 | +0.031 | -0.04 / +0.29 / +0.11 / +0.23 |  |
| v9_registration_marks | 3923006 | Grids | F3 | +0.18 | +0.022 | -0.07 / +0.41 / +0.01 / +0.17 |  |
| v9_registration_marks | 5136165 | Grids | F3 | +0.18 | +0.022 | -0.07 / +0.41 / +0.01 / +0.17 |  |
| v9_registration_marks | 6961360 | Grids | F3 | +0.25 | +0.031 | -0.04 / +0.29 / +0.20 / +0.14 |  |
| v10_element_only_white | 3693278 | Grids | bbox_fit | -0.52 | -0.065 | -0.45 / -0.07 / -0.23 / +0.46 |  |
| v10_element_only_white | 3693539 | Grids | bbox_fit | -0.52 | -0.065 | -0.45 / -0.07 / -0.69 / +0.91 |  |
| v10_element_only_white | 3693688 | Grids | bbox_fit | -0.52 | -0.065 | -0.45 / -0.07 / +0.82 / -0.83 |  |
| v10_element_only_white | 3694573 | Grids | bbox_fit | -3.19 | -0.399 | -0.30 / +0.62 / -1.74 / -1.45 |  |
| v10_element_only_white | 3694705 | Grids | bbox_fit | -3.19 | -0.399 | -0.83 / +0.43 / -1.74 / -1.45 |  |
| v10_element_only_white | 3694782 | Grids | bbox_fit | -3.19 | -0.399 | -0.20 / +0.53 / -1.74 / -1.45 |  |
| v10_element_only_white | 3694880 | Grids | bbox_fit | -3.19 | -0.399 | -0.73 / +0.34 / -1.74 / -1.45 |  |
| v10_element_only_white | 3694982 | Grids | bbox_fit | -3.19 | -0.399 | -0.54 / +0.39 / -1.74 / -1.45 |  |
| v10_element_only_white | 3695045 | Grids | bbox_fit | -3.19 | -0.399 | +0.13 / +0.20 / -1.74 / -1.45 |  |
| v10_element_only_white | 3695123 | Grids | bbox_fit | -3.19 | -0.399 | +0.17 / +0.16 / -1.74 / -1.45 |  |
| v10_element_only_white | 3695201 | Grids | bbox_fit | -3.19 | -0.399 | +0.36 / -0.04 / -1.74 / -1.45 |  |
| v10_element_only_white | 3697629 | Grids | bbox_fit | -3.19 | -0.399 | -0.40 / +0.00 / -1.74 / -1.45 |  |
| v10_element_only_white | 3698218 | Grids | bbox_fit | -3.19 | -0.399 | -0.24 / -0.16 / -1.74 / -1.45 |  |
| v10_element_only_white | 3784562 | Grids | bbox_fit | -0.52 | -0.065 | -0.45 / -0.07 / +0.37 / -0.38 |  |
| v10_element_only_white | 3839177 | Grids | bbox_fit | -0.58 | -0.073 | -0.43 / -0.15 / -1.01 / +1.24 |  |
| v10_element_only_white | 3839178 | Grids | bbox_fit | -0.58 | -0.073 | -0.43 / -0.15 / -1.91 / +2.14 |  |
| v10_element_only_white | 3923006 | Grids | bbox_fit | -3.19 | -0.399 | -0.42 / +0.74 / -1.74 / -1.45 |  |
| v10_element_only_white | 5136165 | Grids | bbox_fit | -3.19 | -0.399 | -0.26 / +0.58 / -1.74 / -1.45 |  |
| v10_element_only_white | 6961360 | Grids | bbox_fit | -0.58 | -0.073 | -0.43 / -0.15 / -2.22 / +2.45 |  |

Premise: `get_BoundingBox(view)` of a datum is its drawn extent in the view (UNCONFIRMED).

### 11. Model-ink residue

Q4: does membership suppression with subcategories leave any model ink? Off-palette pixels outside the fiducials and outside any recovered boundary band. Counted, not attributed: an annotation the pass could not paint lands here too.

| variant | suppression | category layer | off-palette | fiducial px | boundary bands excluded | off-palette outside boundary |
|---|---|---|---|---|---|---|
| v0_control | hide_categories | -- | 5878 | 0 | 0 | 5878 |
| v7_no_crop | external | yes | 7669 | 0 | 0 | 7669 |
| v8_no_crop_fiducials | external | yes | 7669 | 0 | 0 | 7669 |
| v9_registration_marks | external | yes | 7669 | 0 | 0 | 7669 |
| v10_element_only_white | external | no | 7669 | 0 | 0 | 7669 |

`category layer` is mechanism 3 (category and subcategory white overrides). V10 runs without it: the V7 vs V10 difference in this table is what that layer removes, and the cost section above is what it costs.

### 12. F3 -- registration marks, in BOTH captures

Detail-line ticks the probe drew at KNOWN view UV, inset inside the crop, then removed by rolling back. Horizontal ticks' centre rows give v, vertical ticks' centre columns give u. Twelve ticks give six points per axis at three levels, so the residual measures disagreement between levels (a round-3 capture had eight ticks at two levels, whose residual is 0 by construction). The model row is checked against the model capture's RECORDED lattice -- the one place this method meets a known answer. The endpoint fit uses tick ends (caps, anti-aliasing) and is shown beside the centre-line fit, never in its place.

| variant | capture | ticks | px/ft u | px/ft v | u/v isotropy | residual max px u / v | endpoint fit px/ft u / v | vs model lattice worst px |  |
|---|---|---|---|---|---|---|---|---|---|
| v9_registration_marks | annotation | 10/12 | 4.1476 | 4.1476 | 1.00000 | 0.33 / 0.00 | 4.1476 / 4.1476 | -- |  |
| v9_registration_marks | model | 10/12 | 4.1476 | 4.1476 | 1.00000 | 0.33 / 0.00 | 4.1476 / 4.1476 | 0.50 |  |

- `v9_registration_marks` model lattice: 4.1476 px/ft
- `v9_registration_marks` annotation -> model pixels via_model_lattice: x' = 1.000000 x +0.33, y' = 1.000000 y +0.50
- `v9_registration_marks` annotation -> model pixels via_model_marks: x' = 1.000000 x +0.00, y' = 1.000000 y +0.00
- `v9_registration_marks` annotation: tick left_mid_h not found -- its colour [54, 246, 0] drew no pixels
- `v9_registration_marks` annotation: tick right_mid_h not found -- its colour [246, 0, 126] drew no pixels
  - where left_mid_h should be (px [28, 4996, 130, 5002]): 721 of 721 px white; other colours: none
  - where right_mid_h should be (px [4153, 4996, 4255, 5002]): 721 of 721 px white; other colours: none
- `v9_registration_marks` annotation: 960 mark pixels in 10 rect(s) to subtract in post
- `v9_registration_marks` model: tick left_mid_h not found -- no component of the shared colour in that corner and orientation
- `v9_registration_marks` model: tick right_mid_h not found -- no component of the shared colour in that corner and orientation
  - where left_mid_h should be (px [28, 4996, 130, 5002]): 721 of 721 px white; other colours: none
  - where right_mid_h should be (px [4153, 4996, 4255, 5002]): 721 of 721 px white; other colours: none
- `v9_registration_marks` model: 960 mark pixels in 10 rect(s) to subtract in post

### Overlays

The gate is measurement 1 only: image size == `frame_px`. IT IS A SIZE GATE AND NOTHING MORE. A capture can be exactly `frame_px` pixels and still have its CONTENT drawn at a different scale or origin -- which is finding F1 -- and `capture_overlay.py` maps UV through the SIDECAR's numbers, so on such a capture its boxes will not land on the ink. Measurement 2's fitted px/ft is echoed beside each line so that is visible here rather than only three sections up.

- `v0_control`: overlay written (fitted 4.143 / 4.123 px/ft against the frame's 4.148) -> OK C:\Users\gmcdowell\Documents\VOP\Exports\FVoT\probe_0928_0848\anno_pass_variants_probe\v0_control\color_id_buffer\__Plan_CropInActive_19291097_anno.json -> C:\Users\gmcdowell\Documents\VOP\Exports\FVoT\probe_0928_0848\anno_pass_variants_probe\v0_control\color_id_buffer\__Plan_CropInActive_19291097_anno.overlay.png
- `v7_no_crop`: overlay written (fitted 4.160 / 4.261 px/ft against the frame's 4.148) -> OK C:\Users\gmcdowell\Documents\VOP\Exports\FVoT\probe_0928_0848\anno_pass_variants_probe\v7_no_crop\color_id_buffer\__Plan_CropInActive_19291097_anno.json -> C:\Users\gmcdowell\Documents\VOP\Exports\FVoT\probe_0928_0848\anno_pass_variants_probe\v7_no_crop\color_id_buffer\__Plan_CropInActive_19291097_anno.overlay.png
- `v8_no_crop_fiducials`: overlay written (fitted 4.160 / 4.261 px/ft against the frame's 4.148) -> OK C:\Users\gmcdowell\Documents\VOP\Exports\FVoT\probe_0928_0848\anno_pass_variants_probe\v8_no_crop_fiducials\color_id_buffer\__Plan_CropInActive_19291097_anno.json -> C:\Users\gmcdowell\Documents\VOP\Exports\FVoT\probe_0928_0848\anno_pass_variants_probe\v8_no_crop_fiducials\color_id_buffer\__Plan_CropInActive_19291097_anno.overlay.png
- `v9_registration_marks`: overlay written (fitted 4.153 / 4.149 px/ft against the frame's 4.148) -> OK C:\Users\gmcdowell\Documents\VOP\Exports\FVoT\probe_0928_0848\anno_pass_variants_probe\v9_registration_marks\color_id_buffer\__Plan_CropInActive_19291097_anno.json -> C:\Users\gmcdowell\Documents\VOP\Exports\FVoT\probe_0928_0848\anno_pass_variants_probe\v9_registration_marks\color_id_buffer\__Plan_CropInActive_19291097_anno.overlay.png
- `v10_element_only_white`: overlay written (fitted 4.160 / 4.261 px/ft against the frame's 4.148) -> OK C:\Users\gmcdowell\Documents\VOP\Exports\FVoT\probe_0928_0848\anno_pass_variants_probe\v10_element_only_white\color_id_buffer\__Plan_CropInActive_19291097_anno.json -> C:\Users\gmcdowell\Documents\VOP\Exports\FVoT\probe_0928_0848\anno_pass_variants_probe\v10_element_only_white\color_id_buffer\__Plan_CropInActive_19291097_anno.overlay.png
