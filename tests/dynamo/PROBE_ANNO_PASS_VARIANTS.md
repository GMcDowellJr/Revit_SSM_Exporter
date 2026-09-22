# Stage A annotation-pass variant probe — round 2 (revised)

`probe_stage_a_anno_pass_variants.py` runs the candidate fixes for the Stage A
annotation pass beside the current pass, on one view at a time, and writes what
each produced.

**It is a PROBE.** It changes no production default and it concludes nothing
about which fix is right. Greg's read of the output is the gate; the probe
reports values. The only PASS/FAIL it makes is about **itself** — whether each
variant ran, whether it measured what it is named after, and whether the view
was put back — because a variant that silently failed to restore the document
is not evidence, it is damage.

Round 1's measured output is in
`tools/notes/ROUND1_ANNO_PASS_VARIANTS_FINDINGS.md`, with the JSONs it quotes in
`tools/notes/data/round1_anno_pass_variants/`. Read that first.

---

## The decision this round is built on

**THE CAPTURE DOES NOT MODIFY THE CROP.** Not the model crop region, not the
annotation crop.

Why, measured on round 1: the annotation pass set `view.CropBox` to frame B,
which is wider than the view's authored crop. Datum extents clip to the crop, so
widening it **lengthens level and grid lines and walks their heads outward**,
and it pulls in content from outside the authored crop — the dashed line Greg
found in the expanded section is another section seen beyond, unpainted and
therefore black. The annotation raster has not been showing the drawing.

This is not only V6's problem. **The shipped pass sets the crop to B**, so every
capture taken so far, V0 included, moved the crop and dragged the datums with
it.

The rule for what counts: **if it is in the view and it prints, it counts.**
Reference planes and scope boxes are out.

Consequences:

* `v6` (the expanded frame B′) is **dropped**, and the B′ code is deleted — not
  left un-runnable.
* The annotation pass gets a mode in which it writes neither `CropBox` nor
  `CropBoxActive` (P2, below).
* B stops being an instruction to Revit. Whether it survives as a recorded
  quantity is an open question this round informs, not one it answers.
* Registration is **measured**, not asserted: F1 and F2 below.

> **What is still true, and is out of this round's scope.** The MODEL pass is
> unchanged: it sets the crop to A (the model clip bounds snapped out to the
> pixel lattice, ≤ 1 px per side from the authored crop) for its own export and
> restores it — and on a **crop-inactive** view it *activates* a crop. The
> combined report records both, per view, in `crop_relationships`
> (`model_pass_crop_a_minus_authored_ft`, `model_pass_activates_a_crop`), so
> the size of what the model pass still moves is a number rather than a guess.

---

## Variants

| variant | membership white suppression | crop | CropBoxVisible (F1) | fiducials (F2) | SmoothEdges off | own model capture | production suppression mode |
|---|---|---|---|---|---|---|---|
| `v0_control` | — | **written to B** (shipped) | — | — | — | — | `hide_categories` |
| `v7_no_crop` | **yes** | **untouched** | — | — | — | — | `external` |
| `v8_no_crop_fiducials` | **yes** | **untouched** | **on** | **yes** | **yes** | **yes** | `external` |

That table is `variant_plan()`, asserted row by row in
`tests/test_probe_stage_a_anno_pass_variants.py`. `v0_control` still moves the
crop on purpose: it is the control for exactly the behaviour being removed.

### Retired — named, reasoned and refused

`RETIRED_VARIANTS` carries the reasons in code, and the registry **refuses** a
campaign that still asks for one rather than quietly running nothing.

| retired | why |
|---|---|
| `v0_offsets0` | **FALSIFIED** in round 1: byte-identical to `v0_control` on both views. |
| `v1`–`v3` (`white_filter…`) | Superseded: category-based suppression cannot separate a drafting line from a model line — both are `OST_Lines`. |
| `v4_white_membership`, `v5_…_smooth_edges_off` | Superseded by `v7`/`v8`: they still set the crop to B. |
| `v6_white_membership_expanded_frame` | **DROPPED.** Its B′ widened the crop further still, which is the defect. The variant and the B′ code (`expanded_frame_uv`, `frame_prime_drivers`, `viewer_extent_elements`, `_build_frame_prime`, `paper_margin_ft`) are **deleted**; a test asserts none of them is importable. |

`expanded_frame_margin_in` is refused by name, as
`white_filter_include_view_only_model_categories` was: its premise is gone.

---

## V7 — membership suppression, crop untouched

### Suppression by membership, not category

"Annotation" is content visible only in the view it is placed in. Drafting lines
and detail items are annotation despite Model-labelled categories; model lines
in the same category are not. So suppression runs off the **same split the
painting uses** — `split_stage_a_pass_membership`, `OwnerViewId` plus datum
categories — resolved **once** per view and shared by every variant:

- every element in the **MODEL** set gets an element-level **white override**;
- every element in the **ANNOTATION** set is left alone for the pass to paint;
- **nothing is hidden**, anywhere.

Revit's `Element > Filter > Category` precedence is what makes the
shared-category case work: a white *category* override on `OST_Lines`
suppresses model lines while the pass's own per-element palette paint still wins
for the drafting lines in that same category.

| # | mechanism | reaches |
|---|---|---|
| 1 | element-level white override | every MODEL member, **including DWG `ImportInstance`s** (element-overridable, confirmed) |
| 2 | production's `_apply_link_category_filters`, called with **white** — not a second link mechanism | LINKED RVT content, where a per-element override is impossible |
| 3 | category **and SUBcategory** white overrides (`cat.SubCategories` walked; roof fascia survived a parent-only override) | subcategory linework an element override does not govern |
| 4 | — | whatever none of the above reached: a **named list** (`unreached`), never an absence |

Mechanism 3 writes only where the current override is **blank**, so its restore
is a blank write, which *is* the original state and is verifiable. An authored or
unreadable category override is left alone and named in `unreached`.

**UNCONFIRMED, and recorded:** whether subcategory overrides reach **linked**
content. Neither `Elevation_CropActive` nor `Plan_CropActive` has a linked RVT
(`link_visibility.instance_count == 0` on both), so mechanism 2 is built but
unexercised on them; its first real test is `Plan_RVTLink` 19293485.

### The crop

Production's `color_id_buffer_anno_crop_mode = "untouched"`: no `CropBox`
write, no `CropBoxActive` write, no crop restore. `registration.rendered_uv` is
`None` **by construction** with `rendered_uv_reason` saying so, and the capture
is not faulted for it. The requested axis is the model pass's own `crop_px`.
That lands on the model lattice **only when the export's extent is the crop**:
FitToPage fits each pass's own extent, and with annotation visible that extent
is the union including datum heads past the crop (21.4% scale mismatch on the
one section measured by hand, 2026-09-22). The scale is an output; F1/F2 measure
it.

---

## V8 — V7 plus F1, F2 and SmoothEdges off

### F1 — the crop region as fiducial

`CropBoxVisible` on for **both** passes. The boundary draws at the crop's own UV
bounds, which the probe reads from `view.CropBox` **without changing it** — so a
capture carries its own ruler, and model-to-annotation registration follows
from it directly. It may also stop the shrink-to-content case by guaranteeing
ink at the crop edge; that is a second thing this measures.

* **"Both passes"** means V8 takes its **own model capture**, boundary visible,
  into `v8_no_crop_fiducials/model/`. The shared model pass stays the shipped
  one — it is the foundation V0 and V7 register against. V8's own model capture
  runs **before** the white suppression, because the model pass paints every
  model element and restores each to a blank, which would wipe white overrides
  applied first. Whether its lattice equals the shared one is recorded
  (`own_model_pass.geometry_matches_shared`). In that capture the boundary draws
  at **crop A** — the model pass sets the crop to A for its export — and the
  analyzer checks it against A's lattice, the one known answer.
* **CropBoxVisible is a NEW restore obligation**: snapshotted, restored and read
  back like the rest.
* **UNCONFIRMED: whether `ImageExportOptions` draws the crop boundary at all.**
  It has no hide-crop-boundaries flag (Print Setup and PDF export both do),
  which is suggestive, not evidence. One export settles it: the analyzer's
  section 7 reports `NOT FOUND` with its reason. If it does not draw, record
  that and fall through to F2 — do not work around it.
* **A non-rectangular crop draws its SHAPE.** The probe reads
  `GetCropRegionShapeManager().GetCropShape()` and records the curve loop
  (`crop_loop_record`): every edge with its orientation, the distinct u levels
  of its vertical edges and v levels of its horizontal ones, oblique edges
  named. The decoder matches levels, never four assumed edges.
* **The boundary is not documentation content.** The probe writes
  `probe_crop_boundary` into both V8 sidecars — `present: true`,
  `must_be_subtracted: true`, where it is drawn — and the analyzer's
  `--json-out` records the recovered pixel rectangles to subtract. Never
  silently skipped.

### F2 — the fiducial pair

Two **model** elements left unsuppressed and painted **reserved** colours
instead of white, at known UV. Works with no crop at all, which F1 does not
(`Plan_CropInActive` has nothing to draw).

* **Reserved colours** `(251, 11, 139)` and `(11, 139, 251)`: every one has a
  251 channel, which is prime and so is off every palette lattice from step 2 to
  8. `colour_on_lattice` states that per capture, `fiducial_colour_record`
  checks the capture's actual palette for a collision, and a test composes it
  with production's `build_palette` over a full step-8 palette.
* **Choice** (`choose_fiducial_pair`): inside the authored crop (crop A when
  there is none), inset 2%, at least 6 px and at most 5% of the reference on
  each axis, and maximising `min(|du|, |dv|)` — both axes, because a pair on one
  row determines nothing about v. **Exact**, by bisection over an O(n) sweep,
  and checked against brute force over generated pools.
* Painted **after** the white suppression inside the same transaction, so the
  two element overrides replace the white ones; the white suppression's blank
  restore over every model member reverses them.
* `probe_fiducials` goes into the annotation sidecar: ids, colours, `rect_uv`,
  and the colour check.

**Where F1 and F2 are both available they must AGREE, and the disagreement is
the number worth reporting.** The analyzer's section 9.

---

## Production code this uses, and the three switches

The annotation pass is production's `export_annotation_color_id_buffer_view` —
**called, never reimplemented**. Three probe-only switches, read with `getattr`
off the cfg object and **absent from `Config`** (no production default, no
`to_dict()`, no way to reach a production run):

| attribute | default | probe value |
|---|---|---|
| `color_id_buffer_anno_model_suppression` | `"hide_categories"` | `"external"` for V7, V8 |
| `color_id_buffer_anno_smooth_edges_off` | `False` | `True` for V8 |
| `color_id_buffer_anno_crop_mode` | `"frame_b"` | `"untouched"` for V7, V8 |

An **unknown** mode raises `ValueError` rather than defaulting.

| brief item | status |
|---|---|
| **P1** — a restore refused on a non-overridable category does not fail the capture; three-valued at the site | **already landed** at `9d23a99` (round 1's R5). Nothing further needed; see below. |
| **P2** — a mode in which the pass writes neither `CropBox` nor `CropBoxActive` | `color_id_buffer_anno_crop_mode`, **this round** |
| **P3** — CropBoxVisible, if it belongs inside the pass's transaction | **left in the probe.** A caller can set it from outside: it is a display property, not something the pass's own transaction has to order (unlike SmoothEdges, which must follow the DisplayStyle change). |

`dim_check` keeps its TODO (F4); nothing else in production changes.

---

## Transaction and restore discipline

Per variant, in order:

1. snapshot **before** — read by one function, so every reading is the same
   reading;
2. `TransactionGroup.Start`;
3. a. (V8) a committed child `Transaction` turns `CropBoxVisible` on;
   b. (V8) V8's own model capture, boundary visible;
   c. a committed child `Transaction` applies the white membership suppression,
      then (V8) paints the fiducials;
4. production's annotation pass runs, with **no child transaction open**;
5. a committed child `Transaction` reverses 3c and 3a;
6. snapshot, and the read-back verdict — **the real measurement**, taken before
   the rollback so it judges the explicit restore;
7. `TransactionGroup.RollBack` in `finally`;
8. snapshot again, and a second verdict — the safety net.

**Nothing here writes the crop.** The crop's extent is read (`crop_region_record`)
and read back (the `crop_box` and `crop_region_shape` obligations), never set.

### The obligations, named individually

`crop_box` (corners **and** `CropBoxActive`), **`crop_box_visible`** (new),
**`crop_region_shape`** (new — "the capture did not touch the crop" as a
read-back, not a claim), `smooth_edges`, `annotation_crop_offsets`,
`model_category_visibility` (every MODEL category), `view_filters`,
`view_template_id`, `display_style` — plus `probe_filter_deleted[...]` (both
off the view **and** gone from the project) and `explicit_restore_steps`.

Each is three-valued; a property that could not be **read** is `unverified`,
which is not `restored`.

### `_run_variant` is DRIVEN by a test, not grepped

`tests/test_probe_anno_pass_run_variant.py` runs the real `_run_variant` and the
real production passes against the shared fake Revit DB, and asserts what each
**export** saw: V8's model capture before any white override, the fiducials in
their colours and every other model member white at the annotation export,
`CropBoxVisible` on for both exports and off afterwards, and **no `CropBox` or
`CropBoxActive` write under any annotation transaction** — with V0 as the
control that does write. Six mutations of the call sites (including moving the
own model capture after the suppression) each turn it red. Five of them were
also run against the older source-grep tests: four stayed green there.

### P1 — a category Revit REFUSES to override is not a capture fault

Round 1's plan run returned `success=false` /
`annotation_view_state_not_restored` on every variant while all read-back
obligations said `restored` — the offenders accepted the halftone override going
in and refused it coming out (`ArgumentException: Category cannot be
overridden`). Fixed at `9d23a99` on both sides: suppress-side state is recorded
only after a successful write, and a restore-side refusal is classified
`not_overridable` and kept out of `restore_failures`, while a restore that
genuinely **failed** still fails the capture. `category_halftone_outcomes`
records each category three-valued. The probe's mechanism-3 restore applies the
same classification.

### Conclusions, most disqualifying first

`FAIL` (document not as found — **stops the run**) → `ERRORED` → `INCONCLUSIVE`
(no TIFF) → `CAPTURE_FAILED` (production declared the capture invalid) →
`DID_NOT_MEASURE` → `RAN`.

**`DID_NOT_MEASURE`** now covers every request, each one reported with what was
received instead: SmoothEdges not confirmed off; suppression mode not
`external`; **crop mode not `untouched`** (the one thing V7/V8 exist not to
do — a production build without the switch reports `None`, which is not
`untouched` either); **CropBoxVisible not read back `True` before the pass**;
**fewer than two fiducials painted**. The document is still safe in that case,
so it does not stop the run.

`document_safe` is the **only** thing that stops the run. The model pass's own
verdict gates it: a model capture production rejected runs no variant.

### The model pass

Runs **once per view**, shipped behaviour, and every variant registers against
it. Its TIFF is re-hashed after every variant (catches clobbering), and a repeat
export before and after the variants — with a repeatability **control** —
answers whether a variant changed how the model pass renders
(`model_reexport_after_variants.verdict`). V8's own model capture is separate
and is not part of that comparison.

---

## Dynamo wiring

```text
IN[0] = target view
IN[1] = output directory
IN[2] = optional variant selection ("all", or a comma-separated subset)
IN[3] = optional export dpi (default 150)
```

Via the campaign/batch pipeline (`stage_a_anno_pass_variants` in
`revit_probe_registry.py`), validated before any transaction:

```json
"settings": {
  "selection": "v0_control,v7_no_crop,v8_no_crop_fiducials",
  "export_dpi": 150,
  "authored_override_scan_max": 8000,
  "model_reexport_check": true
}
```

---

## Running it — round 2 (revised)

**Re-run order, and where to stop:**

| order | view | id | then |
|---|---|---|---|
| 1 | `Elevation_CropActive` | 19293413 | **a restore read-back failure here stops the run** |
| 2 | `Plan_CropActive` | 19290402 | **STOP. Send both combined JSONs.** |
| 3 | `Plan_CropInActive` | 19291097 | the crop-less case, which is what F2 exists for — after Greg reads 1 and 2 |

The remaining views (`Plan_DWG` 19294180, `Plan_RVTLink` 19293485 — first
exercise of mechanism 2, `RCP_CropActive` 19293283, `Section_CropActive`
19293421, `ModelCallout_CropActive` 19293458) wait.

1. Open the FVOT BLDG 1 test model (a **copy**, as always).
2. Restart Revit, or reset the Dynamo CPython3 engine, so the edited probe is
   re-imported — a probe imported before an edit stays imported after it.
3. Paste `probe_stage_a_anno_pass_variants.py` into a CPython3 Python Script
   node with the four inputs above, or drive it through `revit_batch_dynamo.py`.
4. Point `IN[1]` at a writable directory **outside the checkout**.
5. One view at a time; copy `OUT` out as text after each.

### Stop and raise

* **Cost.** `suppression_cost` sits at the top of each combined report: the
  white suppression's elapsed time against the whole model pass's. Production
  does not time its paint separately, so the ratio is a **lower bound** on
  suppression-vs-paint. If it is material, report the number before running more
  views.
* **No boundary.** If section 7 says `NOT FOUND` on V8, `CropBoxVisible` does
  not draw in an export: say so and run F2 alone.
* **Linked subcategories.** If subcategory overrides cannot reach linked
  content, that is an answer — record it (`unreached`).

### Output layout, and the expected file count

```text
<IN[1]>/anno_pass_variants_probe/
    <view>_<id>.anno_pass_variants.json                    one per view
    model/color_id_buffer/<view>_<id>.tiff / .json          the shared model pass
    v0_control/color_id_buffer/<view>_<id>_anno.tiff / .json
    v7_no_crop/color_id_buffer/<view>_<id>_anno.tiff / .json
    v8_no_crop_fiducials/color_id_buffer/<view>_<id>_anno.tiff / .json
    v8_no_crop_fiducials/model/color_id_buffer/<view>_<id>.tiff / .json
```

Per view: 3 annotation pairs (6) + the shared model pair (2) + V8's model pair
(2) + 1 combined report = **11**. The two-view stop: **22**. With
`Plan_CropInActive`: **33**. Fewer means a variant was skipped or a view stopped
early, and the combined report says which and why.

---

## Reading the output

```bash
python tools/notes/anno_pass_variant_report.py <the anno_pass_variants_probe dir> \
    --out anno_variants.md --json-out anno_variants.json
```

Values only — no score, no tolerance, no pass/fail. Per view it first prints
**the crop context**: whether the authored crop is active, how far V0 widens it
to B, how far the model pass's crop A sits from it (and whether it activates
one), the crop shape, the white-suppression cost and the chosen fiducials.
Then, per capture, sections 0–6 as before (what the capture did, size vs
`frame_px`, the bbox registration fit, excursion, coverage, off-palette split,
model hash) — an **untouched** capture is framed by the **authored** crop, and a
crop-less one gets its scale fitted with margins withheld — and five new ones:

| section | question it answers |
|---|---|
| 7. F1 — the crop boundary | **Q1**: does the export contain the crop boundary, and does its recovered position match `view.CropBox`? (`NOT FOUND` with the reason; px/ft per axis against the lattice's; u/v isotropy — 1 means the crop's own shape at one scale; V8's model capture against A's lattice) |
| 8. F2 — the fiducials | the pair's ink edges vs recorded extents (with a residual), and vs their drawn extents in the model capture |
| 9. F1 vs F2 vs bbox fit | **Q2**: with the crop untouched, is the rendered rectangle stable, and do F1 and F2 agree? The authored crop's corners pushed through both maps — the disagreement, in pixels |
| 10. Datum extents | **Q3**: do datum extents now match the drawing? Each datum's ink against the bbox recorded before the capture touched the view. V0 should read long; V7/V8 should not |
| 11. Model-ink residue | **Q4**: does membership suppression with subcategories leave any model ink? Off-palette pixels outside the fiducials and the recovered boundary |

`--json-out` writes, per capture, the recovered boundary pixel rectangles (what
to subtract), the fiducial measurements and the mapping agreement.

---

## UNCONFIRMED API claims

Nothing in this probe was observed on a Revit host in the session that wrote
it. Each claim is resolved by reflection at runtime and recorded three-valued —
an absent property is `unavailable` **with a reason**, never a 0 or a `False`.

| claim | what depends on it |
|---|---|
| `ImageExportOptions` draws the crop boundary when `CropBoxVisible` is on | **all of F1** — section 7 is the evidence either way |
| `View.CropBoxVisible` is settable from outside the pass and survives its template detach | F1; read back immediately before the pass as `during_capture` |
| `View.GetCropRegionShapeManager().GetCropShape()` returns the crop's curve loops | F1's shape record; absent, the decoder assumes a rectangle from `CropBox` **and says so** |
| a white **element** override suppresses a model element's own graphics | V7, V8, mechanism 1 |
| `cat.SubCategories` is walked and each takes a white override — and whether that reaches **linked** content | mechanism 3; linked reach unexercised on the round-2 views |
| production's `_apply_link_category_filters` with white suppresses linked content | mechanism 2 |
| `View.GetLinkOverrides(ElementId).LinkVisibilityType` exists | the "not By Host View" record only |
| setting `View.DisplayStyle` does not discard a `SmoothEdges` written before it | **mitigated** by writing SmoothEdges after DisplayStyle |
| with the crop untouched, `ExportImage` renders the authored crop (or the content union) at a stable scale | F1 and F2 **measure** this rather than assume it |
| `get_BoundingBox(view)` of a datum is its drawn 2-D extent in the view | section 10's premise |

---

## Evidence to send back

* every `*.anno_pass_variants.json` (the two-view stop: two of them);
* every `*_anno.tiff` / `*_anno.json`, per variant, and **V8's model pair**;
* the shared model pair per view;
* `OUT` copied as text, per view;
* the analyzer's report **and its `--json-out`**;
* whether the crop boundary is visible in the V8 images at all;
* whether the datums in V7/V8 look like the drawing, and in V0 like they did;
* anything visible in V7/V8 that is not white and not annotation — above all
  anything the `unreached` list does not name.

---

## Handoff delta 2026-09-22 — ExportImage registration, checked against this branch

One section view, exported by hand in the UI. Nothing in it is confirmed across
views. What it changes here, and the §6 code checks answered against the code:

| handoff item | against this branch |
|---|---|
| Production ZoomType | **FitToPage**, one-axis `PixelSize`, `FitDirection` from cfg (`_export_one_tiff`). Unchanged by the frame-B sizing. |
| ExportRange | Production uses **`SetOfViews`**, not `CurrentView`. The handoff's extent findings were measured under `CurrentView`; that they carry over to `SetOfViews` is **UNTESTED**. |
| decode `fpp = max(crop_u/actual_w, crop_v/actual_h)` on the annotation pass? | `decode_stage_a_color_id.py` and `colorid_to_occupancy.py` read the MODEL sidecar (`bounds_xy`) only; no decoder consumes an annotation capture. The `max()` path does reach annotation captures in **`capture_overlay.py`** (via `registration.rendered_uv`) and in the analyzer's **`frame px/ft`** comparator column. Both assume image extent == the rectangle, which the handoff shows is false whenever annotation extends past the crop. The analyzer's own maps (bbox fit, F1, F2) are measured and do not use it; the overlay is gated on size only and refuses untouched captures (`rendered_uv` is None). |
| dim_check / `predicted_derived_px` / 10,000 px cap assume FitToPage | Yes, all three. Moving to Zoom would make pixel dimensions an output and all three would need re-deriving. Not changed here. |
| TIFF resolution tag 1x1; `ImageResolution` likely ignored under FitToPage | Production does not set `ImageResolution`; the sidecar's `achieved_export_dpi` is derived from the frame geometry, not read from the file. Treat it as nominal. |
| model-pass crop line on the image border, half-clipped | The analyzer now flags border-touching bands (`border_clipped_band_count`) and says to read the model capture against its lattice (the image edges ARE crop A), not the biased centres. |
| crop line is ink in the annotation pass | Already masked: `boundary_px_rect` excluded from the residue count and written by `--json-out` to subtract. |
| drawn rectangle = the model crop, not the annotation extent | Holds under V7/V8, which never write the crop: F1 is the authored crop. |
| crop can be drawn when not expected (`crop_off_test_anno2`) | The analyzer runs boundary detection on EVERY capture, V0 and V7 included, and prints whether the probe turned it on — an authored view that already shows its crop is visible there. |
| native decode, per-axis affine, no resampling | What sections 7–9 do: per-axis scale + offset fitted per capture, pixels never resampled. |

Still OPEN from the handoff: fiducial edge bias across views, `SetOfViews` vs
`CurrentView` extent, `ImageResolution` under FitToPage, the two test-set
anomalies.

---

## What is deliberately NOT here

* **`dim_check` is not fixed.** F4 keeps its TODO.
* **The model pass still writes crop A.** Out of scope for this round;
  recorded per view in `crop_relationships`.
* **No production default changes**, and the three switches are not `Config`
  fields.
* **No verdict on the candidates.** Not in the probe, not in the analyzer, not
  in this document.
