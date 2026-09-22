# Stage A annotation-pass variant probe — round 2

`probe_stage_a_anno_pass_variants.py` runs the candidate fixes for the Stage A
annotation pass beside the current pass, on one view at a time, and writes what
each produced.

**Round 2.** Round 1's measured output is in
`tools/notes/ROUND1_ANNO_PASS_VARIANTS_FINDINGS.md`, with the JSONs it quotes in
`tools/notes/data/round1_anno_pass_variants/`. F2 is answered, the annotation
crop offsets are falsified, and the suppression mechanism changed: it now runs off
membership rather than category. Read that report before this document.

**It is a PROBE.** It changes no production default and it concludes nothing
about which fix is right. Greg's read of the output is the gate; the probe
reports values. The only PASS/FAIL it makes is about **itself** — whether each
variant ran, and whether the view was put back — because a variant that
silently failed to restore the document is not evidence, it is damage.

---

## What each variant changes — ROUND 2

| variant | white membership suppression | SmoothEdges off | expanded frame B′ | production suppression mode |
|---|---|---|---|---|
| `v0_control` | — | — | — | `hide_categories` (shipped) |
| `v4_white_membership` | **yes** | — | — | `external` |
| `v5_white_membership_smooth_edges_off` | **yes** | **yes** | — | `external` |
| `v6_white_membership_expanded_frame` | **yes** | **yes** | **yes** | `external` |

That table is `variant_plan()`, and `tests/test_probe_stage_a_anno_pass_variants.py`
asserts it row by row.

### Retired after round 1 — named, not deleted

`RETIRED_VARIANTS` carries the reasons in code, and the registry **refuses** a
campaign that still asks for one rather than running nothing.

| retired | why |
|---|---|
| `v0_offsets0` | **FALSIFIED.** Byte-identical to `v0_control` on **both** round-1 views (elevation `e9ab768b`, plan `b047a741`). The annotation crop offsets changed nothing. |
| `v1_white_filter` | **SUPERSEDED** by `v4`. |
| `v2_white_filter_smooth_edges_off` | **SUPERSEDED** by `v5`. |
| `v3_white_filter_smooth_edges_off_expanded_frame` | **SUPERSEDED** by `v6`. |

The v1–v3 family suppressed by **category** — one rule-less
`ParameterFilterElement` over every filterable MODEL category. That cannot
separate a drafting line from a model line, because both live in `OST_Lines`. The
setting that used to ask whether to include Detail Items and Lines
(`white_filter_include_view_only_model_categories`) is **gone with them**: its
premise was that a category-level mechanism has to choose, and it does not any
more.

The annotation crop offsets are still **read** and still a restore obligation.
Only the writer is gone.

### V4 — membership, not category, decides what is suppressed

"Annotation" is content visible only in the view it is placed in. A drafting line
and a detail item are annotation even though their categories carry a Model
label; a model line in the *same* `OST_Lines` category is not. So suppression
runs off the **same split the painting uses** — `split_stage_a_pass_membership`,
`OwnerViewId` plus datum categories:

- every element in the **MODEL** set gets an element-level **white override**;
- every element in the **ANNOTATION** set is left alone for the pass to paint;
- **nothing is hidden**, in either set.

**Revit's precedence is what makes the shared-category case work**, and the design
depends on it: `Element > Filter > Category`. A white *category* override on
`OST_Lines` suppresses model lines while the annotation pass's own per-element
palette paint still wins for the drafting lines in that same category. The
separation is done by precedence, not by picking categories apart.

The split is resolved **once** in `_run_native` and shared by all four variants —
a per-variant split would let two variants suppress different sets and then be
read against each other as if they had not.

#### Four mechanisms, because element overrides do not reach everything

| # | mechanism | reaches |
|---|---|---|
| 1 | element-level white override | every MODEL-membership element, **including DWG `ImportInstance`s** (element-overridable, confirmed 2026-09-21) |
| 2 | per-category `ParameterFilterElement` + `SetFilterOverrides`, **white** | LINKED RVT content, where a per-element override is impossible (ledger M1) |
| 3 | category **and SUBcategory** white overrides | the subcategory linework an element override does not govern |
| 4 | — | whatever none of the above reached: a **NAMED LIST**, never an absence |

Mechanism 2 **calls production's own `_apply_link_category_filters`** with white
instead of a palette colour. It is not a second link mechanism, and it keeps
production's restore rule: a filter this run *created* may be deleted, a filter it
*reused* is shared with other views or templates and is only removed from this
view.

Mechanism 3 is a **complement to mechanism 1, not a fallback for it**. Greg
observed roof fascia surviving a white filter and going white only when the
**subcategory** was overridden alongside the parent, so `cat.SubCategories` is
walked and each one overridden. A category whose `SubCategories` cannot be read is
itself an `unreached` entry — an unwalked subcategory is the roof-fascia case
arriving silently.

> **Mechanism 3 writes only where the current override is BLANK.** Overwriting an
> authored category override would destroy graphics this probe cannot put back:
> reapplying a captured `OverrideGraphicSettings` across a transaction boundary is
> the pattern this module's history warns about, and it is how the curtain-panel
> bug happened. Writing only over blank means the explicit restore is a blank
> write, which **is** the original state and is verifiable. An authored or
> unreadable override is left alone and named in `unreached`.

#### What is recorded, per view

Element override count and every failure by id; DWG `ImportInstance` ids; the link
filter's categories, created and reused filter ids and failed categories; category
and subcategory overrides applied, skipped-as-authored, refused and failed; and
`unreached` with a reason per entry plus `unreached_count`.

`unreached` is the honest answer to "is the annotation TIFF annotation on white".
An empty list is a claim; a populated one is a bound on it.

#### UNCONFIRMED, and recorded rather than assumed

Whether **subcategory** overrides reach **linked** content. Mechanism 2 works at
category level through a filter; whether a linked element's subcategory linework
follows that filter override is not established.

Round 1's two views have **no linked RVT instances at all**
(`link_visibility.instance_count == 0` on both), so mechanism 2 is **built but
unexercised** on the re-run set — a clean round-2 run on these two views is *not*
evidence that the link path works. Its first real exercise is `Plan_RVTLink`
19293485.

### V5 — V4 plus SmoothEdges off

Kept, not settled. Round 1: its predecessors V1 and V2 were byte-identical on the plan
(`13fc4e90`) and different on the elevation (`b1b2d3c4` vs `43f8f6c6`). Since the
elevation's annotation set is a **single** element with no category and no
resolvable bbox, the elevation difference is in *model-suppression residue* rather
than in annotation edges — a reason to keep the variant, and a hint about what it
is measuring.

### V6 — V5 plus the expanded frame B′

B′ unchanged from round 1. What changed is the reading of it — see
`tools/notes/ROUND1_ANNO_PASS_VARIANTS_FINDINGS.md`. On the derived axis the
export tracks **content**, not the crop: with B it *grew* past the crop by +442 px
(35%) on the elevation and +124 px (5.8%) on the plan, and with B′ it *shrank* by
only −12 px (0.6%) and −87 px (3.5%). So B′ is close to right, and its residual is
overshoot — a margin of background rather than lost content.

**`u` matched to the pixel in all ten round-1 captures**, because `u` is the
requested axis and `PixelSize` pins it. Every disagreement is on `v`. That is also
why `dim_check` reported `pass` on all ten: it compares the requested axis, which
cannot vary. F4 is not a check that missed a case — it is a check aimed at the
axis that cannot move.

---

## Production code this uses, and the two switches

The annotation pass itself is production's
`export_annotation_color_id_buffer_view` — **called, never reimplemented**. Two
opt-in switches were added to it. Both are read with `getattr` off the cfg
object and both are **deliberately absent from `Config`**: they have no
production default, no `to_dict()`/`from_dict()` representation and no way to
reach a production run. A probe assigns the attribute on its own cfg object.

| attribute | default | probe value |
|---|---|---|
| `color_id_buffer_anno_model_suppression` | `"hide_categories"` | `"external"` for V4–V6 |
| `color_id_buffer_anno_smooth_edges_off` | `False` | `True` for V5–V6 |

`"external"` covers membership-based suppression **unchanged**, and a second mode
name was deliberately not added: that mode's contract is "the CALLER has already
suppressed model content by some other means; do not hide, do not disable
filters", and nothing in it is specific to how the caller did it. Two names for
one behaviour is the "computed in two places" shape. An unknown mode still
raises.

An **unknown** suppression mode raises `ValueError` rather than defaulting. The
mode decides whether "the annotation TIFF is annotation on white" was arranged
by production or by its caller, and a typo falling back to `"hide_categories"`
would make a variant that measured nothing indistinguishable from one that did.

Also reused rather than re-answered: `_get_solid_pattern_id` (which fill pattern
is the solid one), `_override_is_cleared` (what a blank override is),
`split_stage_a_pass_membership`, `resolve_element_bbox`,
`project_bbox_corners_uv`, `frame_export_geometry`, `init_view_raster`,
`collect_view_elements`, `export_color_id_buffer_view`, and
`stage_a_probe_contract.element_id_value`.

---

## Transaction and restore discipline

Per variant, in order:

1. snapshot **before** — seven properties, read by one function so all three
   readings are the same reading;
2. `TransactionGroup.Start`;
3. a committed child `Transaction` applies this variant's pre-state (zeroed
   offsets, or the white filter);
4. production's annotation pass runs, with **no child transaction open**;
5. a committed child `Transaction` reverses step 3;
6. snapshot, and the read-back verdict — **the real measurement**, taken before
   the rollback so it judges the explicit restore;
7. `TransactionGroup.RollBack` in `finally`;
8. snapshot again, and a second verdict — the safety net.

Step 6 is what the restore contract is judged on. Step 8 exists so that a
failure at step 5 still leaves the document as found, and so the two can be told
apart: an explicit restore that failed while the rollback saved it is a probe
defect worth fixing, not a clean run.

### The eight obligations, named individually

`crop_box` (corners **and** `CropBoxActive`), `smooth_edges`,
`annotation_crop_offsets`, `model_category_visibility` (**every** MODEL
category, not a sample), `view_filters`, `view_template_id`, `display_style` —
plus `probe_filter_deleted` and `explicit_restore_steps`.

`probe_filter_deleted` asks **two** questions: the id is gone from the view
**and** `doc.GetElement` confirms the `ParameterFilterElement` itself is gone
from the project. `RemoveFilter` succeeding while `doc.Delete` fails leaves the
id off the view and the element alive project-wide, and checking only view
membership declared that restored — the case this document and the code's own
comment promised to catch and did not. Not being able to determine it is
`unverified`, never `restored`.

`explicit_restore_steps` fails when any explicit restore step raised. Those
errors were recorded in `explicit_step_errors` and consulted by nothing, so a
clean `TransactionGroup` rollback could let the variant conclude `RAN` — falsely
validating an explicit restore the rollback had actually rescued. (Both: review
finding on PR #215, round 3, P2.)

Each is three-valued. A property that could not be **read** in either snapshot
is `unverified`, which is **not** `restored` — the same rule production's
override read-back follows, and the rule that stops a probe certifying a view it
never managed to inspect.

Under V4–V6 model category visibility is **never written, in either direction**,
which is what makes `model_category_visibility: restored` a real statement
rather than a no-op write's echo.

### R5: a category Revit REFUSES to override is not a capture fault

Round 1's plan run returned `success=false` /
`annotation_view_state_not_restored` on **every** variant, with "2 restore step(s)
raised", while all eight read-back obligations said `restored` and
`document_safe` was `true`. Five good captures read as failures. The raiser,
verbatim:

```
ArgumentException: Category cannot be overridden.
Parameter name: categoryId
   at Autodesk.Revit.DB.View.SetCategoryOverrides(ElementId categoryId, ...)
```

Two fixes in production, and the sidecar says which one carries the weight:

1. **Ordering.** `category_halftone_state` was recorded *before*
   `SetCategoryOverrides`, so a category Revit refuses up front was entered anyway
   and the restore loop tried to undo a change that never happened. The state is
   now written only after the write succeeds.
2. **Restore-side classification, which is the half that actually fixes R5.** The
   plan sidecar holds **eleven** categories in `category_halftone_state` — eleven
   suppress writes *succeeded* — with two failures at restore. The offenders
   accepted the override going in and refused it coming out, so fix 1 does not
   touch that case. A refusal is now classified and kept out of
   `restore_failures`; a restore that genuinely **failed** still fails the capture.

`category_halftone_outcomes` records, per category, `applied` / `not_overridable`
/ `failed` going in and `restored` / `not_overridable` / `failed` coming out. Not
failing is not the same as not happening, and halftone changes exported colour.

### A capture PRODUCTION declared invalid

Separate from, and checked before, the measurement question below. Production
sets `success = False` with `capture_faults` such as
`annotation_frame_not_applied`, `annotation_lattice_mismatch`,
`export_dim_mismatch` or `annotation_overrides_unverified` — and **those arise
after** any switch was applied, so `variant_measurement_check` cannot see them:
it inspects the two switches and nothing else.

Such a variant concludes **`CAPTURE_FAILED`**, the run and the envelope follow,
and `variants_with_failed_captures` names each one with its `failure_reason`.
Production's verdict is checked *before* the switch check because it is the more
fundamental fact; both are recorded on the variant either way, so neither is
hidden by whichever wins. A `capture_success` of `None` — the annotation pass
returned nothing to read it from — is **not** treated as a pass.

(Review finding on PR #215, round 2, P1. The probe was discarding production's
own `success` and `failure_reason`, so a capture production called invalid came
back `RAN`.)

> **The persisted annotation sidecar now carries `capture_faults`.** Production
> serialised `state_out` about a hundred lines *before* it computed them, so the
> file never had them — only the returned in-memory metadata did, and any
> consumer reading the file saw a faulted capture as a clean one. Fixed at
> `dcb4e65`+, with `failure_reason` persisted beside the list it is derived
> from. The analyzer still prefers the probe's combined record and **says which
> source it used**, because a sidecar written by an earlier build has no such
> key — and an absent key is reported as `UNKNOWN`, not `none`.

### A variant that did not MEASURE its candidate

A TIFF existing proves an export happened. It does not prove the export
measured the variant, and **both** production switches can decline without
raising:

* `applied_smooth_edges` comes back `"read_failed"` or `"unchanged (failed)"`
  when the `ViewDisplayModel` read or write fails. Production correctly does
  not raise — an unconfirmed AA state costs decode confidence, not the export —
  so V5/V6 can return a real TIFF that measured **V4's** behaviour.
* `model_suppression_mode` is what production says it did. A V4–V6 capture that
  came back `"hide_categories"` hid model categories and disabled the probe's
  own suppression: it measured **V0's** suppression.

Either way the capture is not evidence about its candidate, so the variant
concludes **`DID_NOT_MEASURE`** rather than `RAN`, the run concludes the same,
the envelope's `execution_status` is `inconclusive`, and the analyzer's
**section 0** prints it as the first thing in the report. The document is still
safe in that case, so it does **not** stop the run.

(Found by review on PR #215, as a P1. Before it, the probe declared `RAN` on
TIFF existence alone; the fields were recorded in the sidecar, absent from the
conclusion and never rendered — three places to look and no place that said so.
`variant_conclusion()` is extracted precisely so the *call site* is bound by a
test and not only the checker it calls.)

### Two different questions, and only one of them stops the run

`document_safe` is "is the view as this variant found it". It is the **only**
thing that stops the run, because continuing past a document in an unknown state
makes every later artifact evidence about nothing.

`conclusion` is "did this variant produce a capture". An annotation pass that
raised **with the view verifiably restored** is a failed variant and a safe
document: the run continues, because the other four candidates are still worth
measuring and stopping would throw away the whole view's evidence over one of
them.

### The model pass's own verdict gates the run

`export_color_id_buffer_view` can return a TIFF **and** usable geometry with
`success = False` — an `export_dim_mismatch` that survived the halving backoff is
the reachable case. Every annotation variant registers against that capture, so
the probe **refuses and runs no variant** when the model pass rejects its own
capture, naming the `failure_reason`. The model TIFF and sidecar are still on
disk as the evidence, and the combined report is written.

Refusing rather than running-and-flagging, for the same reason the
missing-geometry branch refuses: which model faults are tolerable is Greg's call,
and five captures against a rejected foundation cost a Revit session and prove
nothing. (Review finding on PR #215, round 3, P1: the status was recorded here
and composed into no decision at all.)

### The model pass

Runs **once per view**, shipped behaviour, neither switch set. Its
`frame_export_geometry` result is caught through `geometry_out` and handed to
every variant — production's own wiring, so V0/V4/V5 register against the model
capture exactly as a production run would.

Its TIFF is re-hashed after every variant. **That detects a variant CLOBBERING
the model artifact** — a path collision, a stray write — and nothing more,
because nothing rewrote the file.

Whether a variant changed how the model pass **renders** is a different
question, and it is answered with a **control**: a repeat export is taken
*before* any variant runs, with the view untouched. Only if that comes back
byte-identical to the original does the post-variant comparison mean anything.

| `model_reexport_after_variants.verdict.status` | meaning |
|---|---|
| `unchanged` | the control held **and** the post-variant hash matches |
| `changed` | the control held and it does not — a variant left the view in a state the model pass renders differently, which the per-variant re-hash cannot see |
| `not_applicable` | the control did **not** hold (two exports of the untouched view already differ on this host — a TIFF timestamp, a non-deterministic collector order), or a hash is missing. The question is **not answered**. |

Both repeat exports go to a throwaway directory that is deleted, so the run
still publishes exactly **one model pair per view**. `model_reexport_check =
false` turns both off; the cost is two extra model exports per view.

---

## Dynamo wiring

```text
IN[0] = target view
IN[1] = output directory
IN[2] = optional variant selection ("all", or a comma-separated subset)
IN[3] = optional export dpi (default 150)
```

### Via the campaign/batch pipeline

The registry adapter `stage_a_anno_pass_variants`
(`revit_probe_registry.py`) validates settings **before** a transaction starts
or a TIFF is exported, reusing the probe's own `select_variants` and
`variant_plan` rather than a second copy of the vocabulary — so a campaign typo
is refused in `IN[2] = True` validation-only mode, not discovered after five
exports.

```json
"settings": {
  "selection": "v0_control,v4_white_membership",
  "export_dpi": 150,
  "expanded_frame_margin_in": 0.5,
  "authored_override_scan_max": 8000,
  "model_reexport_check": true
}
```

A campaign job's `variant` maps onto `selection`; a job that sets both to
different values is **refused**, not silently resolved one way.

---

## Running it — the 20260922T085737 view set

**Round 2 runs TWO views and stops.** `Elevation_CropActive` 19293413 then
`Plan_CropActive` 19290402 — the two with a round-1 baseline — then send both
combined JSONs before going further.

| order | view | id | why this one |
|---|---|---|---|
| 1 | `Elevation_CropActive` | 19293413 | round-1 baseline; 6535 model members against **1** annotation member, so it is the model-suppression reach test |
| 2 | `Plan_CropActive` | 19290402 | round-1 baseline; 283 annotation members across 11 categories, and the only view known to trigger R5 |

The remaining six wait on Greg reading those two:

| view | id | note |
|---|---|---|
| `Plan_CropInActive` | 19291097 | |
| `Plan_DWG` | 19294180 | first exercise of mechanism 1 on a DWG `ImportInstance` |
| `Plan_RVTLink` | 19293485 | **first exercise of mechanism 2**; neither round-2 view has a linked RVT |
| `RCP_CropActive` | 19293283 | got no B expansion at all in the 20260922 run (B == A) |
| `Section_CropActive` | 19293421 | as above |
| `ModelCallout_CropActive` | 19293458 | |

1. Open the FVOT BLDG 1 test model (a **copy**, as always).
2. Restart Revit, or reset the Dynamo CPython3 engine, so the edited probe is
   re-imported. Revit holds one interpreter open for the whole session; a probe
   imported before an edit stays imported after it, which is how a run on
   2026-09-16 executed an old probe against a new campaign and flagged nothing.
3. Paste `probe_stage_a_anno_pass_variants.py` into a CPython3 Python Script
   node with the four inputs above, or drive it through
   `revit_batch_dynamo.py`.
4. Point `IN[1]` at a writable directory **outside the checkout** — a Stage A
   TIFF can reach several hundred megabytes.
5. Run it for **one view at a time**, and copy `OUT` out as text after each.

### Output layout, and the expected file count

```text
<IN[1]>/anno_pass_variants_probe/
    <view>_<id>.anno_pass_variants.json          one combined report per view
    model/color_id_buffer/<view>_<id>.tiff        the model pass, once per view
    model/color_id_buffer/<view>_<id>.json
    v0_control/color_id_buffer/<view>_<id>_anno.tiff
    v0_control/color_id_buffer/<view>_<id>_anno.json
    v4_white_membership/color_id_buffer/...
    v5_white_membership_smooth_edges_off/color_id_buffer/...
    v6_white_membership_expanded_frame/color_id_buffer/...
```

Each variant gets its own output directory because production's TIFF and sidecar
names are fixed per view — five variants sharing one directory would overwrite
one file five times.

For the round-2 two-view set, at four variants:

| artifact | count |
|---|---|
| annotation captures (2 views × 4 variants × 2 files) | **16** |
| model pairs (2 views × TIFF + sidecar) | **4** |
| combined probe reports (1 per view) | **2** |
| **total** | **22** |

For all eight views later, at four variants: 64 + 16 + 8 = **88**.

Fewer means a variant was skipped or a view stopped early, and the combined
report says which and why in `skip_reason` / `stopped_early`. The two repeat
model exports per view leave **no** artifact — their scratch directories are
deleted.

**If the restore read-back fails on the first view, stop the run**, send the
combined JSON, and do not open the next view.

The combined JSON is **finalized before it is written**: its `conclusion` and
`paths` are set first, and `_write_combined` **refuses** a report without the
`report_finalized` stamp. (Review finding on PR #215, P2: it used to be
serialised first, so the persisted file — the one the analyzer and you actually
read — permanently said `INCONCLUSIVE` and carried no paths, while only the
in-memory object returned to Dynamo was right.)

---

## Reading the output

```bash
python tools/notes/anno_pass_variant_report.py <the anno_pass_variants_probe dir> \
    --out anno_variants.md
```

Leaf module: standard library + numpy + PIL, plus `tools/clamp_pad_geometry.py`.
It emits the brief's six measurements as tables and **nothing evaluative** — no
score, no tolerance, no pass/fail, no "this variant looks better".

0. **What the capture reports it actually did** — `model_suppression_mode`,
   `applied_smooth_edges`, the display style, the probe's conclusion, whether
   the variant measured its candidate, production's own `success`, any capture
   faults, and **which source those faults were read from**. This section exists
   because those fields were being *read and never rendered*, which made a
   variant that measured nothing look exactly like one that did.
   **A failed annotation bbox collection withholds 2, 2b, 2c, 3 and 4**, with
   production's reason, and says so in a line near the top of the view's
   section. Production writes an empty map plus a status block when its
   collection raises; reading that map as "this view has no annotations" made a
   failed collection indistinguishable from a clean capture — the fit said "0
   matched", the excursion came back `value` over zero rectangles, and coverage
   printed an empty table (review finding on PR #215, round 3, P1).

1. **Image size vs `frame_px`, on both axes.** `dim_check` inspects the
   requested axis only (F4), so a nonzero `dh` beside `dim_check=pass` is F4
   exactly.
2. **Registration fit, measured from the pixels.** Exact-palette-colour
   centroids matched against the sidecar's `bbox_uv` centres, a linear map
   fitted to the pairs, both axes **independently** (F1 saw u and v off by
   different amounts). A fit whose slope comes back **zero or non-finite** on
   either axis — every sample rendering at the same pixel, i.e. a collapsed
   capture — is reported `unavailable` with that reason rather than inverted;
   one such capture used to abort the whole multi-view report with a
   `ZeroDivisionError` before any other view was written (PR #215 round 2, P2). Reports px/ft on u and v against the frame's own, the
   offset at (u0, v1), the per-side margin in feet **and** paper inches, and the
   residual median and max. A colour whose mask touches an image edge is
   **excluded and counted**: a clipped element's centroid is not its centre.

   **2c states the fit's own premise and bounds it.** The fit assumes an
   element's ink is centred in its recorded bbox, and for real text, a tag with
   a leader or a dimension it need not be. A displacement that is the *same* for
   every element is absorbed into the fitted intercept — so it leaves the
   residuals clean while shifting every margin in 2b by exactly that amount.
   Such a uniform displacement is **not recoverable** from a capture (nothing
   distinguishes it from the whole render sitting that far over), so the report
   does not pretend to measure it. What it measures is whether one is
   **possible, and by how much**: the ink's span as a fraction of its recorded
   bbox, its solidity, and the resulting bound in pixels — plus a **second fit**
   anchored on the ink's own bbox centre, whose deltas are ~0 exactly when the
   premise holds. Ink that reaches both edges of its box cannot be off-centre in
   it. (Review finding on PR #215, P1. The first attempt at this measured the
   displacement *through the fit that absorbs it* and returned ~0 by
   construction; its own test caught that.)
3. **How far the recorded bboxes reach past the rendered frame, per side** —
   printed beside 2b. Where they agree, the render is behaving as if fitted to
   the crop unioned with the annotations drawn beyond it.
4. **Coverage per category**: painted, colour present, bbox region holds ink.
   The ink test uses the **fitted** mapping from (2), never the sidecar's — so
   it stays valid on exactly the captures where registration failed, which are
   the ones worth looking at. With no fitted mapping the ink column is
   **unmeasured with its reason**, not 0.
5. **Off-palette pixels** split into palette-to-white blends (within 3 RGB units
   of the segment between some palette colour and white), black/grey, and other,
   with a top-10 colour list. The blend and grey classes **overlap**; blend is
   tested first and `blend also grey` states what that ordering costs.
6. **Model TIFF hash vs V0**, from the probe's own per-variant readings.

`tools/capture_overlay.py` is invoked **only** where (1) shows image ==
`frame_px`. Otherwise the overlay is **withheld with the reason**: that tool maps
UV through the sidecar's recorded export size and refuses when the file
disagrees, so running it on a mis-registered capture yields either a refusal
panel or boxes placed against a rectangle that was never rendered.

> **That gate is a SIZE gate and nothing more, which matters for F1.** A capture
> whose CONTENT was drawn at a different scale or origin is still exactly
> `frame_px` pixels, so it passes the gate and gets an overlay whose boxes will
> not land on the ink — and that is precisely the F1 case. The report therefore
> echoes measurement 2's fitted px/ft beside every overlay line, so a fit of
> 17.8 against a frame's 18.7 is visible where the overlay is mentioned rather
> than three sections up. An overlay being written is not a statement that the
> capture registers.

---

## UNCONFIRMED API claims

Nothing in this probe was observed on a Revit host in the session that wrote it.
Each claim is resolved by reflection at runtime and recorded three-valued — an
absent property is `unavailable` **with a reason**, never a 0 and never a
`False`. They are collected into the combined report's `unconfirmed_api_claims`.

| claim | what depends on it |
|---|---|
| `ViewCropRegionShapeManager.Left/Right/Top/BottomAnnotationCropOffset` exist and are READABLE, via `View.GetCropRegionShapeManager()` | the offsets record and its restore obligation only; nothing writes them as of round 2 |
| a white **element** override suppresses a model element's own graphics | **all** of V4–V6, mechanism 1 |
| `cat.SubCategories` is walked and each subcategory takes a white override | mechanism 3 — and Greg's roof-fascia observation is the evidence FOR it, not a confirmation of the API |
| whether **subcategory** overrides reach **LINKED** content | recorded, not assumed; neither round-2 view has a linked RVT so this is unexercised |
| `View.IsCategoryOverridable` exists | only an optimisation — absent, the refusal is classified from the exception instead |
| production's `_apply_link_category_filters` applies a white filter override that suppresses LINKED content | mechanism 2 |
| `View.GetLinkOverrides(ElementId).LinkVisibilityType` exists | the "not By Host View" record only; its absence degrades the record, not the variant |
| setting `View.DisplayStyle` does not replace the `ViewDisplayModel` and discard a `SmoothEdges` written before it | **mitigated** by writing SmoothEdges after DisplayStyle; recorded because the ordering *is* the mitigation |
| `ExportImage` fits the crop box unioned with the extents of annotations drawn beyond it | F1's primary hypothesis — which this probe **measures** rather than assumes |

The viewer-element category list (`OST_Viewers`, `OST_Elev`,
`OST_ElevationMarks`, `OST_Sections`, `OST_SectionHeads`, `OST_Callouts`,
`OST_CalloutHeads`, `OST_CalloutBoundary`, `OST_ReferenceViewer`) is resolved
name by name and the **unresolved names are recorded**, so a category that
contributed nothing because it does not exist on this host is distinguishable
from one the view simply has none of. That is the "an incomplete token set is the
same defect as an incomplete pattern" lesson, applied to a category list.

---

## Cost and reach, measured from round 1

**Element-override cost: ≈1× the model pass's own paint, not a multiple.** The
model membership set is **4783** elements on the plan and **6535** on the
elevation; the model pass already writes one per-element override per model
element. No stop-and-raise on cost.

**Nothing in either view outranks a white override.**
`authored_model_overrides` came back **0** on both. The elevation's scan was
capped at 5000 of 6437, so that answer is a prefix — the default cap is raised to
8000 for round 2 so it is complete.

**Neither round-2 view has a linked RVT.** Mechanism 2 is built and unexercised
there; `Plan_RVTLink` 19293485 is its first real test.

## Evidence to send back

* every `*.anno_pass_variants.json`;
* every `*_anno.tiff` and `*_anno.json`, per variant;
* the model pair per view;
* `OUT` copied as text, per view;
* the analyzer's report;
* notes on whether V4's image really is annotation-on-white in Revit — and in
  particular whether the roof fascia is white now that subcategories are walked;
* whether V6's frame holds the grids and viewer marks B was missing;
* anything in `unreached` that turns out to be visible in the image, which is the
  list's whole purpose.

---

## What is deliberately NOT here

* **`dim_check` is not fixed.** F4 gets a TODO at its call site citing F4 and
  nothing else. The fix lands after Greg reads this probe, because what the
  derived axis should be compared against — `geom["predicted_derived_px"]`, or
  the frame's other axis — is one of the things measured here.
* **No production default changes**, and the two switches are not `Config`
  fields, so nothing about them is reachable from a production run.
* **No verdict on the candidates.** Not in the probe, not in the analyzer, not
  in this document.
