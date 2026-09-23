# Annotation-pass variant probe — round 3 measured

Source: `data/round3_anno_pass_variants/anno_variants_probe_0923_0936.{md,json}`
(the analyzer's report and `--json-out`) plus the three combined JSONs (read,
not committed). Run `probe_0923_0936`, probe `2026-09-23.1`, views
`__Elevation_CropActive` 19293413, `__Plan_CropActive` 19290402,
`__Plan_RVTLink` 19293485, all 1:96. All 15 variants `RAN`, every one
`document_safe`, model re-export **unchanged** on all three views.

Values and what they support. No verdict on the candidates.

---

## 1. The category layer changes NOTHING in the image — and costs 23–86 s

The V7 and V10 annotation TIFFs are **byte-identical** on all three views
(`tiff_sha256`: elevation `4cf7a33e…`, plan `8807d362…`, linked plan
`c0930f01…` — the same hash for V7 and V10 in each). V10 is V7 without the
category/subcategory white overrides.

| view | V7 suppression (with category layer) | V10 (without) | category writes | V7 = V10 image |
|---|---|---|---|---|
| Elevation | 23 524 ms | **90 ms** | 391 | byte-identical |
| Plan | 45 313 ms | **82 ms** | 393 | byte-identical |
| Plan_RVTLink | 80 897 ms | **948 ms** (link filters 873) | 384 | byte-identical |

The per-write cost is not stable: ~60 ms (elevation), ~115 ms (plan), ~207 ms
(linked plan). On these three views the layer's only measurable effect is the
time.

What this does NOT cover: a view whose model content has subcategory linework
that element overrides do not reach. None of these three showed any (V7's
residue equals V10's to the byte), but three views are three views.

## 2. Linked content: mechanism 2 ran for the first time

`Plan_RVTLink`: one link instance, **4** categories whitened by production's
`_apply_link_category_filters` (HVAC Zones, Structural Framing, Floors, Walls),
0 failed, `<Sketch>` uncolorable. 873–1101 ms. Residue (section 11): V0 68
off-palette px, V7/V10 **0**.

## 3. F3 — the registration marks work, in both captures

All 8 ticks created on all three views (`readback_max_deviation_ft` 0 to
1.4e-14 — including both plans, so `NewDetailCurve` accepted the view-origin
plane), Lines visible, and the own model pass reported `model_lines_visible`
true. All 8 found in both captures of all three views.

| view | annotation px/ft u / v | model px/ft u / v | model lattice | **model marks vs recorded lattice, worst corner** | annotation → model |
|---|---|---|---|---|---|
| Elevation | 17.6346 / 17.6205 | 18.7486 / 18.7439 | 18.7486 | **0.51 px** | x' = 1.063175 x − 152.46, y' = 1.063755 y − 274.95 |
| Plan | 16.2812 / 16.2816 | 18.7418 / 18.7571 | 18.7487 | **1.01 px** | x' = 1.151135 x − 339.99, y' = 1.152047 y − 412.55 |
| Plan_RVTLink | 18.7458 / 18.7571 | 18.7458 / 18.7571 | 18.7497 | **1.13 px** | identity (x' = x, y' = y) |

* **The model row is the check against a known answer**: the marks, fitted in
  the model capture, land within 0.51–1.13 px of where the recorded lattice
  puts them at the crop's corners, over images 4 800–5 200 px wide. The ticks
  draw 1 px wide on integer rows and columns, so each level is quantised to
  about ±0.5 px; that is the size of what is left.
* **The annotation capture is drawn at a different scale on two of three
  views** — 6.3 % (elevation) and 15.1 % (plan) off the lattice — and F3
  measures by how much, per axis, with the offset. On the linked plan the
  annotation export fell exactly on the lattice: the same pixels as the model
  capture, tick for tick (see below).
* Elevation F3 (17.635 / 17.621) agrees with round 2's two independent rulers
  there: F1 on V0 (17.638 / 17.649) and model-anchored F2 (17.635 / 17.642).

**Why the linked plan came out identical.** Its annotation image is 4818 × 1623
against a 4818 × 1624 frame, and the ticks occupy the same pixels in both
captures. That fits the handoff's account: FitToPage fits each pass's own
extent, and this view's annotation extent coincides with its crop. The two
plans share the same crop; the other one's annotation extent is wider
(4819 × 2054 image for a 5274 × 2124 frame).

### What the residual does NOT tell you

Every F3 fit shows residual **0.00 / 0.00**. The two ticks at one level sit on
the same pixel row (or column), so four points at two levels collapse to two
distinct points per axis. A zero residual says the ticks agree with each other;
it says nothing about the scale. The **model-vs-lattice** column is the real
error figure. A third level per axis (a mid-edge tick) would give the fit a
residual that means something.

### Comparisons that are not independent

* Elevation `F3_vs_bbox_fit` 0.59 px: on the elevation the **marks are the only
  annotation with a bbox**, so the bbox fit IS the marks. Not a second ruler.
* `F3_vs_F2` 23.7 px (elevation) and 5.5 px (plan): F2 is the weaker ruler.
  Elevation F2's v scale comes from the fascia's 3.3 ft deep bbox; the plan
  wall still draws a 3 px sliver.
* Plan `F3_vs_bbox_fit` 26 px: the bbox fit is known to be poor there
  (dimensions do not fill their boxes).

## 4. The rollback is the restore — measured

| view | rollback per variant | post-rollback override read-back | explicit reverse it replaced (round 2 `.4`) |
|---|---|---|---|
| Elevation | 0.5–1.4 s | 2.4–3.1 s, **restored** | 33–35 s |
| Plan | 2.3–5.5 s | 2.0–2.5 s, **restored** | 49–51 s |
| Plan_RVTLink | 1.2–2.7 s | 1.9–2.3 s, **restored** | — |

Every obligation read `restored` after the rollback, no mark survived it, and
the model re-export was unchanged on every view.

## 5. Unchanged from round 2

* F1 (the crop boundary Revit draws): not found in any V7–V10 capture on any
  view, and V0's is incomplete (no rectangle closes). The marks replace it.
* Plan residue (V7–V10): 34 470 off-palette px, overwhelmingly palette-to-white
  blends; SmoothEdges off (V8, V9) changes none of it.
* Elevation residue (V7–V10): 1 340 grey px.

---

# Run `probe_0923_1239` (probe `2026-09-23.2`, twelve marks)

Source: `data/round3_anno_pass_variants/anno_variants_probe_0923_1239.{md,json}`
plus the two combined JSONs. Views: `__Elevation_CropActive` 19293413 again,
and `__Plan_CropInActive` 19291097 for the first time. Every variant
`document_safe`; model re-export unchanged on both.

## 1. A crop-INACTIVE view breaks "crop untouched": the annotation export is the whole view

| Plan_CropInActive | image | px/ft (F3) | vs model lattice 18.751 |
|---|---|---|---|
| V0 (crop written to frame B) | 5885 × 2963 | 18.74 / 18.62 (bbox fit) | ≈ 1 |
| V7–V10 (crop untouched) | **2942 × 6986** | **2.8976 / 2.8983** | **6.47× coarser** |

With no crop, FitToPage fits everything the view shows: ≈ 1015 × 2410 ft.
The width request (5885 px, the model crop) was halved to 2942 because the
height would have passed the axis cap. Production raised
`annotation_lattice_mismatch` on V7–V10, and the probe concludes
`CAPTURE_FAILED`: production refused its own capture, correctly.

The registration marks still registered it: 12/12 in both captures. The
annotation fit's residual is 0.00 / 0.33 px, which is 0.11 ft at that scale.
The model fit is 0.33 px from the recorded lattice. The annotation → model
transform is x' = 6.471175 x − 10242.85, y' = 6.469799 y − 7755.02. So
registration is not the problem on this view. **Resolution** is: 1/8" text at
1:96 is ≈ 3 px tall at 2.9 px/ft.

**A decision, not a finding:** a crop-inactive view needs a crop for the
annotation pass. Either the model pass's crop A (which that pass already
activates on this view) or frame B (V0). Both move datum clipping to that
rectangle.

## 2. The mid-edge ticks drew on the plan and only partly on the elevation

| view | annotation ticks | model ticks | residual max u / v (annotation, model) |
|---|---|---|---|
| Plan_CropInActive | **12/12** | **12/12** | 0.00 / 0.33 px, 0.33 / 0.67 px |
| Elevation | 10/12 (no `left_mid_h`, `right_mid_h`) | 10/12 (no `mid_bottom_v`, `mid_top_v`) | 0.00 / 0.00, 0.00 / 0.00 |

On the elevation each capture lost a DIFFERENT pair: the annotation capture
lost the mid-height horizontal ticks, the model capture the mid-width
vertical ones. All four were created (`readback_max_deviation_ft` 0) and
coloured, and the elevation has no levels or grids to overdraw them. With one
pair gone, each axis is back to two levels and the residual to 0 by
construction. Not explained yet. The analyzer now reports what drew where a
missing tick should be (`missing_diagnosis`); re-running it over this run
answers the question without re-running Revit.

Elevation F3 is unchanged from round 3 to four decimals (17.6346 / 17.6205;
model within 0.51 px of the lattice).

## 3. The category layer: five views now

V7 and V10 annotation TIFFs are byte-identical on both views again
(elevation `4cf7a33e…`, plan `b77665da…`). V10's suppression took 95 ms
against V7's 26.8 s on the crop-inactive plan. Five views in total, zero
differences.

### The missing elevation ticks: NOT drawn, not overdrawn (analyzer re-run)

`missing_diagnosis` over the same captures
(`anno_variants_probe_0923_1239_rerun.{md,json}`), each tick's expected
rectangle placed through that capture's fitted map:

| capture | tick | expected px | what is there |
|---|---|---|---|
| annotation | `left_mid_h` | [170, 850, 266, 856] | 679 of 679 px **white** |
| annotation | `right_mid_h` | [4876, 850, 4972, 856] | 679 of 679 px **white** |
| model | `mid_top_v` | [2578, 29, 2584, 131] | 721 of 721 px **white** |
| model | `mid_bottom_v` | [2578, 1135, 2584, 1237] | 42 px white; model elements 5686560 (238 px), 12008414 (154), 18861828 (133), 14208019 (126), 14203081 (28) |

Three of the four drew nothing at all. The fourth sits where model elements
drew, with no mark pixel among them. None was overdrawn by annotation, and
each tick appears in the OTHER capture, so none is missing from the document.
Each capture drops a different pair of the same four detail lines, all
created, all read back at their requested UV. Why is not established; it
needs a Revit run, not the analyzer.
