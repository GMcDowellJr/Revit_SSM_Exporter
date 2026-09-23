# Annotation-pass variant probe — round 2 (revised) measured

Source: `data/round2_anno_pass_variants/anno_variants_probe_1355.{md,json}` —
the analyzer's report and `--json-out` over run `probe_1355`, probe version
`2026-09-22.2`, views `__Elevation_CropActive` 19293413 and `__Plan_CropActive`
19290402, both 1:96. The combined JSONs and TIFFs were not supplied, so what
needs them is marked as such.

Values and what they support. No verdict on the candidates.

---

## Two numbers in that report are WRONG — do not read them

**1. Plan, `v0_control`, section 7 (F1) and every V0 row of section 10.** The
detector closed the crop rectangle on an **interior** line: the crop's right
edge was cut into short runs by painted annotation crossing it, and a black
line at x ≈ 2580 that runs *past* the top and bottom edges satisfied the
coverage test. Result: px/ft 8.78 on u against 17.82 on v (isotropy 0.49), and
datum extents of **+299 ft** that are an artefact of that map, not of the
capture. Fixed in the analyzer (below); re-run it.

**2. Plan, `v8`, section 8, "F2 (model-anchored)".** px/ft 11.72 / 18.74 with a
**zero** residual. The wall fiducial drew **463 px** in the annotation capture —
a 3 px sliver of a 10.6 × 1 ft wall — because the probe painted it with an
override that sets no CUT graphics, so the white category cut override won
where the wall is cut. The two captures did not draw the same shape, and two
points per edge have nothing left to disagree with. Fixed in the probe (below).

---

## Q1 — is the crop boundary in the image?

| view | V0 (probe did NOT turn it on) | V7 | V8 (probe turned it ON) |
|---|---|---|---|
| Elevation | **found**, isotropy 0.99939, 17.64 px/ft | not found | **not found** |
| Plan | found (but mis-closed, see above) | not found | **not found** |

The boundary is drawn in V0, where the probe never touched `CropBoxVisible`, so
both authored views already show their crop. It is gone in V7 — which does not
touch `CropBoxVisible` — and in V8, which turned it on. The one thing V7 and V8
share and V0 lacks is **membership white suppression**. So this does NOT
answer "ImageExportOptions does not draw the boundary"; it says **the
suppression whitened it**, most plausibly because the view's crop-region
element is a model member by membership (no owner view) and took the white
element override.

That is an inference from four images, not an observation of the element. The
combined reports confirm the premise: `snapshot_before.crop_box_visible` is
**true** on both views, and V8's `crop_box_visible.during_capture` is true —
the boundary was on and still did not draw. The probe now looks the element up
(`crop_region_elements`, under OST_Viewers **or** OST_Views — Revit refused a
category override on "Views" (-2000278) on both views, so such elements are in
the model set) and V8 leaves it untouched. The next run confirms or refutes it.

V8's elevation MODEL capture also found no closing rectangle (2 row bands, 1
column band). The model pass paints every model member it collects, so if the
crop-region element is one, its boundary is a palette colour there. The
analyzer now treats that element's colour as a boundary candidate.

## Q2 — with the crop untouched, is the rendering stable, and do F1 and F2 agree?

Not answerable from this run: F1 was never available on a V7/V8 capture.

What does agree, across variants on the **elevation**:

| map | px/ft u | px/ft v | u/v | residual max u / v px |
|---|---|---|---|---|
| V0, F1 (crop boundary) | 17.6377 | 17.6485 | 0.99939 | exact (rectangle) |
| V8, F2 model-anchored | 17.6346 | 17.6417 | 0.99960 | 0.30 / 0.53 |
| V8, F2 recorded bbox | 17.6312 | 17.2797 | 1.02034 | 0.58 / 28.58 |

Two independent rulers on two different captures agree to 0.003–0.007 px/ft.
The recorded-bbox F2 is off on v because the fascia's 3-D bbox is 3.3 ft deep
while it draws 2 px tall; the model-anchored row removes exactly that. All of
them sit **~5.9 % below the lattice** (18.749 px/ft): the annotation export fits
its own extent — 1708 rows against the frame's 1267 — not the crop, as the
2026-09-22 handoff measured by hand.

**Plan, V7/V8**: the image is 4819 × 2054 (the model crop's 4819 px requested);
the bbox fit says 16.34 / 16.87 px/ft (residual median 19 px — dimensions do
not fill their boxes), F2 recorded-bbox 16.26 / 16.38 (residual 9 / 7 px).
Scale ≈ 13 % below the lattice: extent again, not crop.

## Q3 — do datum extents match the drawing?

**Plan V8 through F2** (the best map available): grids read **+0.39 ft** or
**−0.80 ft** long (+0.05 / −0.10 paper in), against an F2 residual of up to
9 px ≈ 0.55 ft — so within what the map can resolve. V7 through the bbox fit:
−0.90 / −4.41 ft, through a much worse map. The V0 figures are invalid (above).
The 12 grids flagged CLIPPED touch the image edge, which is expected when the
extent is fitted to content: the outermost ink sits on the border.

## Q4 — does membership suppression leave model ink?

| view | V0 off-palette | V7 | V8 | of which blends (V7/V8) |
|---|---|---|---|---|
| Elevation | 24 129 (≈12 100 is the boundary) | 1 340 | 1 340 | 0 |
| Plan | 37 594 | 34 489 | 34 489 | 33 827 |

Elevation V7/V8: **1 340** non-white, non-palette pixels in an 8.8 Mpx image,
all grey — a small residue, not attributed yet. Plan V7/V8: 98 % of the
off-palette pixels are **palette-to-white blends** (anti-aliased annotation
edges), 660 grey, 2 other.

**SmoothEdges off changed nothing.** V8 confirmed it off (`applied_smooth_edges
= False`) and its off-palette population is identical to V7's on both views —
33 827 blends on the plan either way. F3's blends are not controlled by
`SmoothEdges` in a FlatColors export.

## F2 (the category dependency) — confirmed recovered

Plan, colour present: Dimensions **2/135 → 135/135**, Door Tags **0 → 26**,
Room Tags **0 → 17**, Wall Tags **0 → 53**, Window Tags **0 → 27**, V0 → V7.
Hiding model categories takes dependent annotation with it; membership
suppression does not.

## STOP AND RAISE — the suppression cost

| view | white suppression | whole model pass | ratio |
|---|---|---|---|
| Elevation | 32 323 ms, 6 536 overrides | 28 357 ms | **1.14** |
| Plan | 35 716 ms, 4 788 overrides | 33 078 ms | **1.08** |

The suppression alone costs **more than the entire model pass, export
included** — ≈ 5 ms per element. Production does not time its paint alone, so
the ratio against the paint is higher still. By the brief's rule this is the
number to report before running more views.

## A probe defect the combined reports exposed

`link_categories` is `unavailable` on both views: `'Document' object has no
attribute 'warn'`. The probe passed the document where production expects its
diagnostics object, so **mechanism 2 (linked RVT) never ran**. Neither view has
a link, so no capture here is affected; `Plan_RVTLink` would have been. Fixed in
`2026-09-22.4`, and the discovery is now a function a test drives.

## Other facts from the run

* Model re-export after the variants: **unchanged** on both views, with the
  repeatability control holding. No variant changed how the model pass renders.
* Elevation: its single annotation member has no bbox and draws nothing, so
  sections 2–4 are empty there by content, not by failure.
* Elevation: V0 barely moves the crop (+0.04 ft on top only) because B ≈ A on
  this view; the plan's V0 widens it by 10.7–16.0 ft per side.

---

## What changed as a result

Analyzer (`tools/notes/anno_pass_variant_report.py`):

* boundary candidates are **grey** pixels only (plus a named crop-element
  colour) — a painted line's anti-aliased fringe is off-palette, long and
  straight, but coloured;
* runs **bridge gaps** (≥ 8 px or 1 % of the axis) where painted annotation
  crosses an edge;
* each edge of a closed rectangle must **stop at its corners**, not merely
  cover the gap — the plan failure is a regression test;
* the crop-region element's palette colour is a boundary candidate, so a model
  capture that painted it still yields F1;
* F2 model-anchored reports annotation-vs-model ink pixels per fiducial and
  says when the shapes differ.

Probe (`2026-09-22.3`, then `.4` for the per-mechanism timing and the link fix):

* fiducials are painted with the full override (projection + cut lines, all
  four patterns) in their reserved colour;
* the view's crop-region element (OST_Viewers, named like the view) is looked
  up and recorded; **V8 leaves it untouched** by the white suppression and the
  restore; V7 still whitens it, as the control.

---

# Run `probe_0923_0827` (probe `2026-09-22.4`)

Source: `data/round2_anno_pass_variants/anno_variants_probe_0923_0827.{md,json}`
plus the two combined JSONs (read, not committed). Same two views. All variants
`RAN`, model re-export **unchanged** on both.

## One number in that report is WRONG — the plan's model-anchored F2 again

`11.7179 / 18.7371 px/ft, residual 0.00 / 0.00`. The wall fiducial (12261162)
has a model colour but drew **no pixels** in the model capture ("None px"), so
it had no model-drawn extent. The fit then ran on the other fiducial alone:
two points per axis, which any line fits exactly. The analyzer counted a
fiducial with no UV extent as usable; it no longer does, and reports F2
unavailable. Test: `test_a_fiducial_the_model_capture_did_not_draw_makes_f2_unavailable`
(red with the fix reverted; its control stays green).

## Q1 — the crop-element hypothesis is REFUTED on the elevation

| | elevation | plan |
|---|---|---|
| crop element found | **19293412** (OST_Viewers, named like the view, = view id − 1) | **none** (5 viewers in the model set, none named like the plan; id − 1 absent) |
| V8 excluded it from suppression and restore | yes (`excluded_element_ids` [19293412], 6535 overrides) | nothing to exclude |
| V8 boundary | **not found** (0 / 0 bands) | not found |
| V8 black `[0,0,0]` px | 358 — identical to V7 | — |

With its crop-region element left untouched, the elevation's V8 still draws no
boundary, and its black population is identical to V7's, which whitened that
element. So whitening that element did not remove the boundary. What remains
different between V0 (boundary drawn) and V7/V8 (absent) is **two** things at
once: V0 writes the crop (`frame_b`) and V0 hides categories instead of the
membership white. This run cannot separate them. A variant that crosses them
(untouched crop + `hide_categories`, or `frame_b` + membership white) can.

`Views` (−2000278) is still refused a category override and is not written.
`Viewers` (−2000279) does not appear in the category walk at all, so no
category override was written on it either.

## Q1 — V0 changed between runs, and it is the pixels, not only the analyzer

| elevation V0 | `.2` (probe_1355) | `.4` (this run) |
|---|---|---|
| boundary | found, 17.64 px/ft | not found: **0 row** bands, 3 column bands |
| black px | 22 570 | 10 518 |

The black count halved, and no row carries a long run. That fits the vertical
edges drawing and the horizontal ones being absent (off the image or not
drawn), not a detector failing on a line that is present. It is a
reading of counts: the V0 TIFFs from both runs are needed to confirm it. Plan V0: 1 row and 2
column bands, and no rectangle closes.

## F2 — the plan wall is not a usable fiducial

Painting the cut graphics changed nothing: the same 463 px, the same bbox
`[1186, 415, 1340, 417]` (a 155 × 3 px line for a 10.6 × 1 ft wall), and no ink
at all in the model capture. It is not being cut-overridden. It draws as one
line, or not at all. Fiducial choice should require the element to have drawn a
filled area in the model capture, rather than trusting its bbox.

## The suppression cost is the CATEGORY writes

| | elevation | plan |
|---|---|---|
| suppression total | 21 471 ms | 26 594 ms |
| `category_override_writes` | **21 290 ms / 391 calls ≈ 54 ms each** | **26 462 ms / 393 ≈ 67 ms each** |
| `element_overrides` | 92 ms / 6 536 calls | 65 ms / 4 784 |
| everything else | < 50 ms | < 25 ms |
| restore: category/link reverse (V7, V8) | 33 177, 34 910 ms | 48 996, 50 820 ms |
| pre-state commit / restore commit | 1.6–2.2 s / 1.8 s | 3.4–3.7 s / 3.3 s |
| ratio vs the whole model pass | 0.83 | 1.12 |

Element overrides are ~0.01 ms each. The ~390 category/subcategory writes are
**over 99 %** of the suppression, and reversing them costs 1.5–2× as much again.
Two things follow as questions, not conclusions:

* whether the category layer buys anything the element layer does not — a
  variant with element overrides only, compared on residue (section 11), measures
  it;
* whether production needs the reverse at all: the capture already runs inside
  a TransactionGroup, and rolling it back undoes the writes without 390 more
  API calls. The probe reverses explicitly because its read-back obligations
  require it.

## Other

* Link discovery now reports `value` on both views (neither has a link; the
  field named `colorable_predicate_error` carries the predicate's source string,
  `live_filterable_lookup` — a misleading name, not an error).
* Plan datums: V0 through the bbox fit +0.55 / +1.70 ft; V7 −0.90 / −4.41 ft;
  V8 through F2 (recorded bbox) +0.39 / −0.80 ft — unchanged from `.2`.

## What changed as a result (round 3, probe `2026-09-23.1`)

* **V9** draws its own ruler: eight detail-line ticks at known UV inside the
  crop, in both passes (the model pass leaves `OST_Lines` visible for it through
  a probe-only switch). Analyzer section 12.
* **V10** drops the category layer, to put a number on what it buys.
* **The restore is the group rollback**, read back afterwards; the 33–51 s
  explicit reverse is gone.
* Next run adds **`Plan_RVTLink` 19293485**, the first with a link, so the link
  filters are timed and V10 is measured where the category layer might matter.
