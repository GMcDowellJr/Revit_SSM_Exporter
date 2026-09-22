# Annotation-pass variant probe — round 1 measured

Source: the two combined reports and two annotation sidecars from the round-1 run
(2026-09-22), views `__Elevation_CropActive` 19293413 and `__Plan_CropActive`
19290402. Both at 1:96, achieved fpp **0.0533372 ft/px**, achieved dpi 149.989,
no cap applied.

Values only. Where a measurement needs the TIFFs, it says so rather than
guessing — **the TIFFs were not supplied, so items 2, 4 and 5 of the analyzer's
six are not in this report.** Everything below comes from the JSONs.

---

## 1. Image size vs `frame_px`, on both axes

| view | variant | u actual/frame | v actual/frame | v delta |
|---|---|---|---|---|
| Elevation | `v0_control` | 5164 / 5164 **(+0)** | 1709 / 1267 | **+442 px** = +23.58 ft = +2.947 paper in |
| Elevation | `v0_offsets0` | 5164 / 5164 (+0) | 1709 / 1267 | +442 px |
| Elevation | `v1_white_filter` | 5164 / 5164 (+0) | 1709 / 1267 | +442 px |
| Elevation | `v2_..._smooth_edges_off` | 5164 / 5164 (+0) | 1709 / 1267 | +442 px |
| Elevation | `v3_..._expanded_frame` | 5314 / 5314 (+0) | 1954 / 1966 | **−12 px** = −0.64 ft = −0.080 paper in |
| Plan | `v0_control` | 5274 / 5274 **(+0)** | 2248 / 2124 | **+124 px** = +6.61 ft = +0.827 paper in |
| Plan | `v0_offsets0` | 5274 / 5274 (+0) | 2248 / 2124 | +124 px |
| Plan | `v1_white_filter` | 5274 / 5274 (+0) | 2248 / 2124 | +124 px |
| Plan | `v2_..._smooth_edges_off` | 5274 / 5274 (+0) | 2248 / 2124 | +124 px |
| Plan | `v3_..._expanded_frame` | 5697 / 5697 (+0) | 2429 / 2516 | **−87 px** = −4.64 ft = −0.580 paper in |

**`u` matched to the pixel in all ten captures.** That is not a good sign, it is a
structural one: `u` is the REQUESTED axis, so `ImageExportOptions.PixelSize` pins
it and it cannot disagree. Every disagreement is on `v`, the derived axis.

### This makes F4 worse than "dim_check missed 440 px"

`dim_check` compares the **requested** axis against what Revit accepted, and the
requested axis is the pinned one. On these captures `dim_check` is **structurally
incapable of failing**: it reported `pass` on all ten, including the one that is
442 px (35%) wrong on the axis that actually moves. It is not a check that missed
a case; it is a check aimed at the axis that cannot vary.

---

## 2. Registration fit — NOT MEASURED

Needs the annotation TIFFs. The fitted px/ft, the per-side margins and the
residuals all come from matching exact-palette-colour centroids against the
sidecar's `bbox_uv`, and there are no pixels here to match. The analyzer is ready
to run on the output directory the moment it is available:

```bash
python tools/notes/anno_pass_variant_report.py <anno_pass_variants_probe dir> \
    --out round1.md
```

---

## 3. Where the shortfalls sit — partly answered

**Entirely on `v`.** `u` is +0 on every capture, so no extent was lost on left or
right in any variant; the 12 px and 87 px are both wholly on the vertical axis.

**Which `v` side — top or bottom — is not derivable from the JSONs.** It needs the
fitted mapping, i.e. the TIFFs. What the JSONs do give is that B′ grew nearly
symmetrically, so a split is as likely as one side:

| view | bbox excursion past B, top | bottom | v total | B′ v-growth |
|---|---|---|---|---|
| Elevation | 14.62 ft | 14.66 ft | 29.27 ft | +37.27 ft |
| Plan | 6.01 ft | 6.86 ft | 12.87 ft | +20.87 ft |

### B′ is close to right, not wrong

The brief reads R3 as "B′ alone cannot guarantee the frame". The numbers say
something more useful — **the export fits the CONTENT on the derived axis,
ignoring the crop's `v` extent, and B′ nearly brackets that content**:

| view | v error with B | v error with B′ |
|---|---|---|
| Elevation | **+442 px (+35%)** — the export GREW past the crop | **−12 px (0.6%)** |
| Plan | **+124 px (+5.8%)** — GREW past the crop | **−87 px (3.5%)** |

One rule explains all four numbers: where B is too small the export expands to
the content; where B′ overshoots it shrinks to the content. So:

- the UNCONFIRMED claim *"ExportImage honours a crop larger than the drawn
  content"* reads **FALSE** — confirmed, as the brief says;
- but the complementary claim, that the export also **ignores a crop smaller than
  the content**, is now confirmed too, and it is the one that was doing the
  damage: B was undershooting by up to 35%;
- B′ reduces the `v` error from 35% to 0.6% on the elevation and from 5.8% to
  3.5% on the plan. It is a large improvement, and its residual is **overshoot**,
  which costs a margin of background rather than losing content.

What remains true is the consequence: **the rendered rectangle is never the crop
rectangle**, so registration must be fitted from pixels and can never be read off
the sidecar's frame. That is why measurement 2 exists and why the overlay is
gated.

---

## 4. Coverage per category — F2 ANSWERED, on the plan only

From the plan's V3 sidecar (`model_suppression_mode: "external"`, so the white
filter was in force and model categories were not hidden). 283 annotation
members, **0 paint failures**, and every category's bbox resolved:

| category | members | with a resolved bbox |
|---|---|---|
| Dimensions | 135 | 135 |
| Wall Tags | 53 | 53 |
| Window Tags | 27 | 27 |
| Door Tags | 26 | 26 |
| Room Tags | 17 | 17 |
| Grids | 16 | 16 |
| Views | 3 | 3 |
| Lines | 2 | 2 |
| Revision Clouds | 1 | 1 |
| Revision Cloud Tags | 1 | 1 |
| Attached Detail Groups | 1 | 1 |
| *(no category)* | 1 | 0 |

These are exactly the figures R1 reports as the "after" side (dimensions 135/135,
wall tags 53/53, window 27/27, door 26/26, room 17/17). The "before" side needs a
V0 sidecar, which was not supplied — so this run confirms the recovered state,
not the delta.

Two details that matter for round 2:

- **`Lines` contributes 2 annotation members** on the plan, placed by
  `OwnerViewId`. So detail lines are already in the annotation set by membership,
  which is exactly the premise of the round-2 design.
- **Grids arrive by `datum_category`, not by ownership** — `basis_counts` reads
  `owner_view: 267`, `datum_category: 16`, and `OST_Grids: 16`. `OST_GridHeads`,
  `OST_Levels` and `OST_LevelHeads` are all 0, so on this view heads are not
  separate elements.

### The elevation has ONE annotation member

`annotation_count: 1`, `owner_view: 1`, `datum_category: 0` — and that single
element has **no category and no resolvable bbox**. 6535 model members against
1 annotation member.

So the elevation is a near-empty annotation view, and two things follow:

1. **F2's answer rests entirely on the plan.** The elevation cannot corroborate it.
2. **The elevation's value is as a model-suppression reach test.** Its V1 and V2
   TIFFs differ (`b1b2d3c4` vs `43f8f6c6`) even though the annotation content is a
   single unpaintable element — so whatever differs between them is *model*
   content the white filter did not fully suppress. That is consistent with the
   roof-fascia subcategory observation, and it makes the elevation the better of
   the two views for testing subcategory reach.

---

## 5. Blend / grey / other split — NOT MEASURED

Needs the TIFFs. Recorded here because it is the one measurement that would say
what the V1↔V2 elevation difference actually is.

---

## 6. Model TIFF hashes

Byte-identical across all five variants on both views, and the post-variant
re-export verdict is **`unchanged`** on both — so the repeatability control held
and no variant changed how the model pass renders.

| view | model sha256 (16) |
|---|---|
| Elevation | `330bcaf4ec45e592` |
| Plan | `ee9e812769ec2735` |

---

## Annotation TIFF hashes — R2 and R4

| view | V0 | V0-offsets0 | V1 | V2 | V3 |
|---|---|---|---|---|---|
| Elevation | `e9ab768b` | `e9ab768b` | `b1b2d3c4` | `43f8f6c6` | `b586daa7` |
| Plan | `b047a741` | `b047a741` | `13fc4e90` | `13fc4e90` | `73cd4977` |

- **R2 confirmed.** V0 and V0-offsets0 are byte-identical on **both** views. The
  annotation crop offsets changed nothing. Variant dropped.
- **R4 confirmed and now explained.** SmoothEdges changed nothing on the plan
  (V1 == V2) and something on the elevation (V1 ≠ V2). Since the elevation's
  annotation content is one unpaintable element, the elevation difference is in
  model-suppression residue rather than in annotation edges — which is a reason to
  keep the variant and a hint about what it is measuring.

---

## R5, the defect — root cause confirmed from the run

Every plan variant returned `success=false` /
`annotation_view_state_not_restored`, while all eight read-back obligations said
`restored` and `document_safe` was `true`. The elevation had no faults at all.

The plan sidecar names the raiser twice, verbatim:

```
ArgumentException: Category cannot be overridden.
Parameter name: categoryId
   at Autodesk.Revit.DB.View.SetCategoryOverrides(ElementId categoryId, ...)
```

**And the sidecar shows it is the accept-then-refuse case, not the refuse-up-front
one.** `category_halftone_state` holds **11** categories — meaning eleven suppress
writes *succeeded* — with two failures at restore. So the two offenders accepted
the override on the way in and refused it on the way out.

That matters for the fix: recording the suppress state only after a successful
write does not touch this case. The **restore-side** classification is the
load-bearing half. Both are in, and the classifier is bound to the verbatim string
above rather than to a paraphrase.

The 11 categories: `-2006080`, `-2006060`, `-2005011`, `-2000480`, `-2000460`,
`-2000450`, `-2000278`, `-2000260`, `-2000220` (Grids), `-2000097`, `-2000051`
(Lines).

---

## Two facts round 2 needs, from these runs

**Element-override cost.** The model membership set is **4783** elements on the
plan and **6535** on the elevation. The model pass already writes one
per-element override per model element, so membership-based white overrides are
≈1× the model pass's own paint cost per view, not a multiple of it. No stop-and-
raise on cost.

**Neither view has a linked RVT.** `link_visibility.instance_count` is **0** on
both, and `not_by_host_view_count` is 0. So the per-category link-filter path
added for round 2 will be **built but unexercised** on the two re-run views — its
first real exercise is `Plan_RVTLink` 19293485. Recorded so a green round-2 run is
not read as evidence that the link path works.

**Also observed:** `authored_model_overrides` is **0** on both views (plan scanned
all 4598; elevation capped at 5000 of 6437). So nothing in these two views
outranks a white override via an authored per-element override. The elevation's
scan was capped — that cap is worth raising for round 2 so the answer is complete
rather than a prefix.

**And the sidecars carry no `capture_faults` key at all** — the production defect
found in PR #215 review round 2, visible here in the field: the file was written
before the faults were computed. Fixed at `ea8297d`; these round-1 sidecars
predate it, which is why the fault had to be read from the combined report.
