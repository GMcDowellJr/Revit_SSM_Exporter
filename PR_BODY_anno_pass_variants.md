# Probe: candidate fixes for the Stage A annotation pass (F1–F4, run 20260922T085737)

**This is a PROBE.** No production default changes, and nothing here decides a
fix. The probe reports values; your read of the output is the gate. The only
PASS/FAIL it makes is about **itself** — whether each variant ran, whether it
measured its candidate, and whether the view was put back.

Branch: `claude/intelligent-faraday-1rbdox`, from `main` at `a3cd0b2`.

**Round 2.** Round 1 ran on two views and its measured output is in
`tools/notes/ROUND1_ANNO_PASS_VARIANTS_FINDINGS.md`, with the JSONs it quotes in
`tools/notes/data/round1_anno_pass_variants/`. Three things came out of it: F2 is
answered, the annotation-crop-offset hypothesis is **falsified**, and the
suppression mechanism changed — it now runs off **membership**, not category. The
variant set below is round 2's; round 1's is retired by name, with reasons.

---

## What round 1 measured, in three lines

**F1 — the export fits CONTENT, not the crop, and the brief's expectation was
backwards.** `u` matched to the pixel on **all ten** captures, because `u` is the
requested axis and `PixelSize` pins it. Every disagreement is on `v`, and with
frame B the export *grew past* the crop: **+442 px (35%)** on the elevation,
**+124 px (5.8%)** on the plan. With B′ it *shrank* by **−12 px (0.6%)** and
**−87 px (3.5%)**. So B′ is close to right and its residual is overshoot — a
margin of background, not lost content.

**F4 is not a check that missed a case — it is a check aimed at the axis that
cannot move.** `dim_check` compares the requested axis, which `PixelSize` pins,
so it reported `pass` on all ten while `v` was off by up to 35%. It is
structurally incapable of failing. Still **not fixed** here; see the last
section.

**The annotation crop offsets do nothing.** `v0_offsets0` came back
**byte-identical** to `v0_control` on both views (elevation `e9ab768b`, plan
`b047a741`).

---

## What each variant changes — ROUND 2

| variant | white membership suppression | SmoothEdges off | expanded frame B′ | production suppression mode |
|---|---|---|---|---|
| `v0_control` | — | — | — | `hide_categories` (shipped) |
| `v4_white_membership` | **yes** | — | — | `external` |
| `v5_white_membership_smooth_edges_off` | **yes** | **yes** | — | `external` |
| `v6_white_membership_expanded_frame` | **yes** | **yes** | **yes** | `external` |

That table is `variant_plan()`, asserted row by row in
`tests/test_probe_stage_a_anno_pass_variants.py`.

### Retired after round 1 — named and refused, not deleted

`RETIRED_VARIANTS` carries the reasons in code, and the registry **refuses** a
campaign that still asks for one rather than quietly running nothing.

| retired | why |
|---|---|
| `v0_offsets0` | **FALSIFIED** — byte-identical to `v0_control` on both round-1 views. |
| `v1_white_filter` | **SUPERSEDED** by `v4`. |
| `v2_white_filter_smooth_edges_off` | **SUPERSEDED** by `v5`. |
| `v3_..._expanded_frame` | **SUPERSEDED** by `v6`. |

`white_filter_include_view_only_model_categories` is **deleted** with them: its
premise was that a category-level mechanism has to choose whether to include
Detail Items and the shared Lines category, and it does not any more. It is still
**refused by name** rather than silently ignored. The annotation crop offsets are
still *read* and still a restore obligation; only the writer is gone.

### V4 — MEMBERSHIP, NOT CATEGORY, decides what is suppressed

"Annotation" is content visible only in the view it is placed in. A drafting line
and a detail item are annotation even though their categories carry a Model
label; a model line in the *same* `OST_Lines` category is not. **No
category-level mechanism can separate those two**, which is why the v1–v3 family
is retired rather than tuned.

Suppression now runs off the **same split the painting uses** —
`split_stage_a_pass_membership`, `OwnerViewId` plus datum categories:

- every element in the **MODEL** set gets an element-level **white override**;
- every element in the **ANNOTATION** set is left alone for the pass to paint;
- **nothing is hidden**, in either set.

**Revit's `Element > Filter > Category` precedence is what makes the
shared-category case work**, and the design depends on it: a white *category*
override on `OST_Lines` suppresses model lines while the pass's own per-element
palette paint still wins for the drafting lines in that same category. The
separation is done by precedence, not by picking categories apart.

The split is resolved **once** in `_run_native` and shared by all four variants —
a per-variant split would let two variants suppress different sets and then be
read against each other as if they had not.

#### Four mechanisms, because element overrides do not reach everything

| # | mechanism | reaches |
|---|---|---|
| 1 | element-level white override | every MODEL-membership element, **including DWG `ImportInstance`s** |
| 2 | production's own `_apply_link_category_filters`, **white** instead of a palette colour | LINKED RVT content, where a per-element override is impossible |
| 3 | category **and SUBcategory** white overrides | the subcategory linework an element override does not govern |
| 4 | — | whatever none of the above reached: a **NAMED LIST**, never an absence |

Mechanism 2 **calls** production's existing link path rather than building a
second one, so it keeps production's restore rule: a filter this run *created*
may be deleted, a filter it *reused* is shared with other views or templates and
is only removed from this view.

Mechanism 3 is a **complement to mechanism 1, not a fallback for it** — you saw
roof fascia survive a white filter and go white only when the **subcategory** was
overridden alongside the parent, so `cat.SubCategories` is walked and each one
overridden. A category whose `SubCategories` cannot be read is itself an
`unreached` entry, because an unwalked subcategory is the roof-fascia case
arriving silently.

> **Mechanism 3 writes only where the current override is BLANK.** Overwriting an
> authored category override would destroy graphics this probe cannot put back:
> reapplying a captured `OverrideGraphicSettings` across a transaction boundary is
> the pattern this module's history warns about, and it is how the curtain-panel
> bug happened. Blank-only means the explicit restore is a blank write, which
> **is** the original state and is verifiable. An authored or unreadable override
> is left alone and named in `unreached`.

`unreached` is the honest answer to "is the annotation TIFF annotation on white".
An empty list is a claim; a populated one is a bound on it. **Anything in it that
turns out to be visible in the image is the list's whole purpose.**

### V5 — V4 plus SmoothEdges off

Kept, not settled. Round 1: its predecessors V1 and V2 were byte-identical on the
plan (`13fc4e90`) and different on the elevation (`b1b2d3c4` vs `43f8f6c6`).
Since the elevation's annotation set is a **single** element with no category and
no resolvable bbox, that difference is in *model-suppression residue* rather than
in annotation edges — a reason to keep the variant and a hint about what it is
measuring.

### V6 — V5 plus the expanded frame B′

B′ itself is unchanged from round 1; what changed is the reading of it, above.
`revit/annotation.py`'s `compute_annotation_extents` (:123) and
`is_extent_driver_annotation` (:47) are **not modified and not called
differently** — that path is the retained arbiter. B′ comes from the probe's own
pure `expanded_frame_uv()` and is sized through production's
`frame_export_geometry`, so the existing pixel cap applies by being *called*
rather than re-derived.

---

## Production changes (PATCH-only, `color_id_buffer.py`)

Two opt-in switches on `export_annotation_color_id_buffer_view`, read with
`getattr` and **deliberately absent from `Config`** — no production default, no
`to_dict()`/`from_dict()` form, no way to reach a production run. A probe assigns
the attribute on its own cfg object.

| cfg attribute | default | probe value |
|---|---|---|
| `color_id_buffer_anno_model_suppression` | `"hide_categories"` | `"external"` for V4–V6 |
| `color_id_buffer_anno_smooth_edges_off` | `False` | `True` for V5–V6 |

`"external"` means the annotation pass hides no model category and disables no
view filter, so suppression *the probe* applied survives into the export — the
shipped pass disables every enabled+visible filter, which would undo exactly
that. It covers membership-based suppression **unchanged**, and a second mode name
was deliberately not added: that contract is "the caller suppressed model content
by some other means; do not hide, do not disable filters", and nothing in it is
specific to how. Two names for one behaviour is the "computed in two places"
shape. An **unknown** mode raises rather than defaulting — a typo falling back
would make a variant that measured nothing look like one that did.

`SmoothEdges` is written **after** the `DisplayStyle` change; that ordering is the
mitigation for an UNCONFIRMED claim, and it is what the tests assert.

**The one production fix that is not probe-only: R5.** Round 1's plan run
returned `success=false` / `annotation_view_state_not_restored` on **every**
variant, while all eight read-back obligations said `restored` and
`document_safe` was `true`. Five good captures read as failures. The raiser,
verbatim:

```
ArgumentException: Category cannot be overridden.
Parameter name: categoryId
   at Autodesk.Revit.DB.View.SetCategoryOverrides(ElementId categoryId, ...)
```

Two fixes, and the sidecar says which carries the weight:

1. **Ordering.** `category_halftone_state` was recorded *before*
   `SetCategoryOverrides`, so a category Revit refuses up front was entered
   anyway and the restore loop tried to undo a change that never happened. The
   state is now written only after the write succeeds.
2. **Restore-side classification, which is the half that actually fixes R5.** The
   plan sidecar holds **eleven** categories in `category_halftone_state` —
   eleven suppress writes *succeeded* — with two failures at restore. The
   offenders accepted the override going in and refused it coming out, so fix 1
   does not touch that case. A refusal is now classified and kept out of
   `restore_failures`; a restore that genuinely **failed** still fails the
   capture.

`category_halftone_outcomes` records, per category, `applied` / `not_overridable`
/ `failed` going in and `restored` / `not_overridable` / `failed` coming out. Not
failing is not the same as not happening, and halftone changes exported colour.

Also: the sidecar gains `model_suppression_mode`, `applied_smooth_edges`,
`smooth_edges_read_error` and `category_halftone_outcomes`. One **recording**
`ViewDisplayModel` dispose helper replaces what would have been three more
`except Exception: pass` copies. And `_export_tiff`'s `dim_check` gets a **TODO
citing F4 and nothing else**.

**No longer here: the `capture_faults` persistence fix, now
[PR #216](https://github.com/GMcDowellJr/Revit_SSM_Exporter/pull/216).** Review
round 2 found that production serialises `state_out` a hundred lines before it
computes `capture_faults`, so the persisted sidecar never carries them — on every
Stage A annotation run, not just this probe's. Since it is not this probe's defect
it was split out at Greg's request, with its own three tests and its own mutation
record against `main`. **This branch is unaffected:** the combined record reads
faults from the returned metadata, never the file, and the analyzer prefers the
combined record while saying which source it used. Only a pointer comment at the
assignment site remains here, and it goes when #216 lands.

---

## UNCONFIRMED API claims

Nothing here was observed on a Revit host. Each claim is resolved by reflection at
runtime and recorded three-valued — an absent property is `unavailable` **with a
reason**, never a 0 and never a `False` — and all of them are collected into the
combined report's `unconfirmed_api_claims`.

| claim | what depends on it |
|---|---|
| a white **element** override suppresses a model element's own graphics | **all** of V4–V6, mechanism 1 |
| `cat.SubCategories` is walked and each subcategory takes a white override | mechanism 3 — your roof-fascia observation is the evidence FOR it, not a confirmation of the API |
| whether **subcategory** overrides reach **LINKED** content | recorded, not assumed; **unexercised** on the round-2 set |
| production's `_apply_link_category_filters` applies a white filter override that suppresses LINKED content | mechanism 2 |
| `View.IsCategoryOverridable` exists | only an optimisation — absent, the refusal is classified from the exception instead |
| `ViewCropRegionShapeManager.*AnnotationCropOffset` exist and are READABLE | the offsets record and its restore obligation only; nothing writes them now |
| `View.GetLinkOverrides(ElementId).LinkVisibilityType` exists | the "not By Host View" record only |
| setting `View.DisplayStyle` does not replace the `ViewDisplayModel` and discard a `SmoothEdges` written before it | **mitigated** by the write ordering; recorded because the ordering *is* the mitigation |
| `ExportImage` fits the crop box unioned with the extents of annotations drawn beyond it | F1's primary hypothesis — **measured**, not assumed |

The viewer-element category list is resolved name by name and the **unresolved
names are recorded**, so a category that contributed nothing because it does not
exist on this host is distinguishable from one the view simply has none of.

---

## Dynamo run steps

```text
IN[0] = target view
IN[1] = output directory        (outside the checkout — a Stage A TIFF can be
                                 several hundred MB)
IN[2] = variant selection       ("all", or a comma-separated subset)
IN[3] = export dpi              (default 150)
```

1. Open a **copy** of the FVOT BLDG 1 test model.
2. **Restart Revit, or reset the Dynamo CPython3 engine**, so the probe is
   re-imported. Revit holds one interpreter open for the whole session; a probe
   imported before an edit stays imported after it, which is how the 2026-09-16
   run executed an old probe against a new campaign and flagged nothing.
3. Paste `tests/dynamo/probe_stage_a_anno_pass_variants.py` into a CPython3
   Python Script node with the four inputs above, or drive it through
   `revit_batch_dynamo.py` (registered as `stage_a_anno_pass_variants`, with
   settings validated in `IN[2] = True` validation-only mode).
4. Run **one view at a time**, and copy `OUT` out as text after each.

**Round 2 runs TWO views and stops.**

| order | view | id | why this one |
|---|---|---|---|
| 1 | `Elevation_CropActive` | 19293413 | round-1 baseline; **6535** model members against **1** annotation member, so it is the model-suppression reach test |
| 2 | `Plan_CropActive` | 19290402 | round-1 baseline; 283 annotation members across 11 categories, and the only view known to trigger R5 |

Then send both combined JSONs before going further. The remaining six wait on
your read: `Plan_CropInActive` 19291097, `Plan_DWG` 19294180 (first exercise of
mechanism 1 on a DWG `ImportInstance`), `Plan_RVTLink` 19293485 (**first exercise
of mechanism 2** — neither round-2 view has a linked RVT), `RCP_CropActive`
19293283, `Section_CropActive` 19293421, `ModelCallout_CropActive` 19293458.

**If the restore read-back fails on the first view, stop the run**, send the
combined JSON, and do not open the next view.

### Output layout and expected file count

```text
<IN[1]>/anno_pass_variants_probe/
    <view>_<id>.anno_pass_variants.json            one combined report per view
    model/color_id_buffer/<view>_<id>.tiff|.json   the model pass, once per view
    v0_control/color_id_buffer/<view>_<id>_anno.tiff|.json
    v4_white_membership/color_id_buffer/...
    v5_white_membership_smooth_edges_off/color_id_buffer/...
    v6_white_membership_expanded_frame/color_id_buffer/...
```

Each variant gets its own output directory because production's TIFF and sidecar
names are fixed per view — four variants sharing one directory would overwrite
one file four times.

For the round-2 two-view set, at four variants:

| artifact | count |
|---|---|
| annotation captures (2 views × 4 variants × 2 files) | **16** |
| model pairs (2 views × TIFF + sidecar) | **4** |
| combined probe reports (1 per view) | **2** |
| **total** | **22** |

All eight views later, at four variants: 64 + 16 + 8 = **88**.

Fewer means a variant was skipped or a view stopped early, and the combined
report says which and why in `skip_reason` / `stopped_early`. The two repeat model
exports per view leave **no** artifact — their scratch directories are deleted.
The combined JSON is **finalized before it is written**, and `_write_combined`
refuses a report without the `report_finalized` stamp.

### Reading the output

```bash
python tools/notes/anno_pass_variant_report.py <the anno_pass_variants_probe dir> \
    --out anno_variants.md
```

Leaf module (stdlib + numpy + PIL, plus `tools/clamp_pad_geometry.py`). Values
only — no score, no tolerance, no pass/fail, no recommendation:

0. **what the capture reports it actually did** — `model_suppression_mode`,
   `applied_smooth_edges`, the display style, the probe's conclusion, whether the
   variant **measured its candidate**, production's own `success`, any capture
   faults, and **which source those faults were read from**. A failed annotation
   bbox collection **withholds 2, 2b, 2c, 3 and 4** with production's reason;
   1 and 5 still run, because a failed bbox collection does not make the pixels
   unreadable;
1. image size vs `frame_px` on **both** axes (F4 — `dim_check` inspects the
   requested axis only, so a nonzero `dh` beside `dim_check=pass` is F4 exactly);
2. **registration fit measured from the pixels**, both axes independently, with
   per-side margins in feet and paper inches and the residual median/max — plus
   **2c**, which states the fit's own premise and bounds it;
3. how far the recorded bboxes reach past the rendered frame, per side, printed
   beside (2);
4. coverage per category — painted, colour present, bbox region holds ink, using
   the **fitted** mapping so it stays valid on exactly the captures where
   registration failed;
5. off-palette pixels split into palette-to-white blends, black/grey and other,
   with a top-10 colour list;
6. model TIFF hash vs V0.

`capture_overlay.py` runs only where image == `frame_px`; that is a **size gate
only**, so the fitted px/ft is echoed beside every overlay line.

**The analyzer has already been run over round 1** — its report is
`tools/notes/ROUND1_ANNO_PASS_VARIANTS_FINDINGS.md`, committed with the JSONs it
quotes. Measurements 2, 4 and 5 are marked NOT MEASURED there because only the
JSONs were supplied, not the TIFFs.

---

## Restore

Per variant: a `TransactionGroup` rolled back in `finally`, explicit restore steps
inside it, and a read-back taken **twice** — after the explicit restore (the real
measurement) and after the rollback (the safety net). Step 6 is what the contract
is judged on; step 8 exists so the two can be told apart, because an explicit
restore that failed while the rollback saved it is a probe defect worth fixing,
not a clean run.

**Eight obligations, named individually.** `crop_box` (corners **and**
`CropBoxActive`), `smooth_edges`, `annotation_crop_offsets`,
`model_category_visibility` (**every** MODEL category, not a sample),
`view_filters`, `view_template_id`, `display_style`, plus `probe_filter_deleted`
and `explicit_restore_steps`.

`probe_filter_deleted` asks **two** questions: the id is gone from the view **and**
`doc.GetElement` confirms the `ParameterFilterElement` is gone from the project.
Each obligation is three-valued: unreadable is `unverified`, which is **not**
`restored`.

Under V4–V6 model category visibility is **never written, in either direction**,
which is what makes `model_category_visibility: restored` a real statement rather
than a no-op write's echo.

Only `document_safe` stops the run. A variant that errored, or that did not
measure its candidate, with the view verifiably restored does not: the other
candidates are still worth measuring.

---

## Checks, before and after

| | before (`a3cd0b2`) | after (`d9e274d`) |
|---|---|---|
| suite | 1442 passed, 2 xfailed | **1646 passed, 2 xfailed** |
| `count_discarded_handlers vop_interwoven tools` | 179 | **179** |
| `check_stage_a_no_geometry vop_interwoven` | PROVEN | **PROVEN** |
| `check_no_bare_except --paths vop_interwoven tools` | OK | **OK** |
| flake8 (`--max-line-length=120`) | — | clean; no new findings in the pre-existing files |

The 204 new tests are **23** production-switch tests
(`tests/test_stage_a_annotation_pass_probe_switches.py`), **43** analyzer tests
(`tests/test_anno_pass_variant_report.py`) and **138** probe helper/adapter tests
(`tests/test_probe_stage_a_anno_pass_variants.py`).

**My round-2 tests were weak and mutation found it.** The first set inspected
**source text**, so four mutations of the real loops — parent-only subcategory
walk, overwrite authored overrides, treat an unreadable override as blank, neuter
the call site — all stayed **green**, because `if False:` and `None and f(...)`
keep the strings being asserted on. Replaced with tests that drive the real
function against the shared fake Revit DB and assert on the record. All five
mutations now fail, and the clean-suppression **control** caught a fixture
artifact on the way: the fake `Category` has no `SubCategories`, which a real one
always does, so it reported a spurious `unreached`.

The switch tests were falsified the same way — six mutations, each turning a test
red, including relocating the `SmoothEdges` block above the `DisplayStyle` write,
which turns **exactly** the ordering test red and nothing else. That pass also
found one test that **could not fail**: it read `view.filter_enabled` after the
restore, which is `True` either way. It now asserts the state **at export time**.

The probe's helper tests live in `tests/`, not `tests/dynamo/` — `conftest.py`
refuses to collect the latter without `VOP_RUN_DYNAMO_TESTS=1`, so putting
Revit-free arithmetic behind that gate would mean it never runs.

---

## Notes worth your attention

**Cost is ≈1× the model pass's own paint, not a multiple.** The model membership
set is **4783** elements on the plan and **6535** on the elevation, and the model
pass already writes one per-element override per model element. No stop-and-raise
on cost.

**Nothing in either view outranks a white override** — `authored_model_overrides`
came back **0** on both. But the elevation's scan was **capped at 5000 of 6437**,
so that answer was a prefix; the default cap is raised to **8000** so round 2's is
complete.

**Neither round-2 view has a linked RVT** (`link_visibility.instance_count == 0`
on both), so **mechanism 2 is built and unexercised**. A clean round-2 run on
these two views is *not* evidence that the link path works.

**The elevation's annotation set is ONE element** with no category and no
resolvable bbox. So F2's answer rests entirely on the plan, and the elevation is a
model-suppression *reach* test rather than an annotation-fidelity one.

**V6 does not answer the model-lattice question, and says so.** V6 renders B′
while the model pass was sized from B, so production's
`annotation_lattice_mismatch` check would be comparing different frames. The probe
passes `model_accepted_px = None` so production **skips** it — "not answered"
rather than a fabricated pass — and records the real relationship between the two
lattices under `frame_prime.record.lattice_relationship`.

**Whether a variant changed how the model pass renders has a repeatability
control.** Re-hashing the model TIFF after each variant only catches a variant
*clobbering* the file. A repeat export is therefore taken **before** any variant,
with the view untouched; only if that is byte-identical does the post-variant
comparison mean anything. If it is not, the verdict says `not_applicable` with
that reason rather than reporting the comparison anyway.

**One open decision for you.** The `capture_faults` serialize-before-finalize fix
below touches **every** Stage A annotation sidecar, not just this probe's. It is
in this PR because that is where it was found; say the word and it comes out into
its own commit or PR.

---

## Review history

Three rounds of Codex review, nine findings, all confirmed and fixed. They are
kept verbatim below because each one names a defect CLASS this repo keeps
shipping, and the fixes are live in round 2's code. They refer to the round-1
variant names (`v1`–`v3`, `v0_offsets0`) because that is what was under review at
the time; the retirement table above maps them onto `v4`–`v6`.

---

## Review round 1 — three Codex findings, all confirmed and fixed

None were marked optional, so each was verified against the code rather than
argued with. All three were real.

**P1 — a variant could report `RAN` without having measured its candidate.**
A TIFF existing proves an export happened, not that the variant's mutation was
applied, and **both** switches can decline without raising:
`applied_smooth_edges` comes back `"read_failed"` / `"unchanged (failed)"` when
the `ViewDisplayModel` read or write fails, and `model_suppression_mode` comes
back `"hide_categories"` if production suppressed after all. Either way V2/V3
would have been analysed under a name claiming AA was off while measuring V1's
behaviour — and the analyzer was **reading `applied_smooth_edges` and never
rendering it**, so the failure was invisible in three places at once.

Fixed at the root rather than at the instance: `variant_measurement_check()`
verifies **every** mutation the variant's plan requested (the review named only
the SmoothEdges half; the suppression-mode half is the same defect one switch
over), a variant that did not get it concludes `DID_NOT_MEASURE`, the run and
the envelope's `execution_status` follow, and the analyzer gained **section 0**
which prints it first.

A first pass at this left the gate inline in `_run_variant`. Mutating its
measurement branch to `elif False:` **left the whole suite green** — the tests
bound the checker and nothing bound the call site, which is CLAUDE.md's own
"exercise the call site" corollary. `variant_conclusion()` is now extracted and
bound, and that mutation turns three tests red.

**P2 — the persisted combined JSON was serialised before it was finalized.**
Confirmed exactly as reported: `_write_combined` ran before `paths` and
`conclusion` were set, so the file the analyzer and Greg read permanently said
`INCONCLUSIVE` with no paths, while only the in-memory object returned to Dynamo
was correct. Two representations of one run, disagreeing, with the durable one
wrong.

Fixed with a **refusal**, not just a reorder: `finalize_native_report()` stamps
`report_finalized` and `_write_combined` raises without it, so a future call site
that writes early fails loudly. The test **reloads the file from disk** rather
than inspecting the return value — a test on the return value could not have
seen this.

**P1 — the registration fit rested on an unasserted premise.** Correct, and the
fixtures were the problem: every one drew solid rectangles exactly filling each
bbox, which makes ink centroid == bbox centre *by construction*. For real text, a
tag with a leader or a dimension it need not hold.

The method itself is kept — the brief specifies it and it is precedented at a
0.44 px median on the run's plan — but its premise is now measured rather than
assumed. A uniform displacement is **not recoverable** from a capture (nothing
distinguishes it from the whole render sitting that far over), so the report does
not pretend to measure it; it measures whether one is **possible and by how
much** (ink span as a fraction of its recorded bbox, solidity, and the resulting
bound in pixels), plus a **second fit** anchored on the ink's own bbox centre
whose deltas are ~0 exactly when the premise holds. New fixtures draw ink that
does *not* fill its box, and an L-shaped one where the two anchors genuinely
diverge.

My first attempt at this measured the displacement **through the fit that
absorbs it** and returned ~0 on the very fixture built to expose it. Its own test
caught that, and the replacement measures outside the fit. The offset-ink test
now pins the *signature* — left and right margins moving in opposite directions
and summing to ~0 — which is what keeps a uniform ink offset distinguishable from
the genuinely-oversized F1 render, where both come back positive.

Checks after the round: **1577 passed, 2 xfailed**; handler count **179**;
geometry-free **PROVEN**; lint clean. Each fix was falsified by mutating it back.

---

## Review round 2 — three more Codex findings, all confirmed and fixed

**P1 — a capture production declared INVALID came back `RAN`.** Confirmed: the
probe recorded production's `success` and `failure_reason` but
`variant_conclusion` never saw them, so `annotation_frame_not_applied`, a
lattice or dimension mismatch, or an unverified restore all concluded `RAN`.
Correctly noted as distinct from round 1: these faults arise *after* the switch
was applied, so `variant_measurement_check` cannot see them. Production's verdict
is now checked first, such a variant concludes `CAPTURE_FAILED`, and a
`capture_success` of `None` is not treated as a pass. A second test pins the
*wiring*, which is the lesson round 1 taught.

**P1 — the analyzer's section 0 printed `none` for exactly the failed captures
it exists to expose.** Root cause was a **production** defect, not an analyzer
one: `export_annotation_color_id_buffer_view` serialises `state_out` about a
hundred lines before it computes `capture_faults`, so the persisted sidecar never
carries them — the same serialize-before-finalize shape as round 1's P2, one file
over, and affecting every Stage A annotation sidecar rather than just this
probe's. **The production half of that fix now lives in
[PR #216](https://github.com/GMcDowellJr/Revit_SSM_Exporter/pull/216)**, split out
because it is not this probe's defect; the test that drives the real function and
reads the file back went with it. What stays here is the analyzer half, which is
this probe's: it prefers the combined record while **saying which source it
used**, because a sidecar from an earlier build has no such key, and an absent
key reads `UNKNOWN`, not `none`.

**P2 — a zero fitted slope raised `ZeroDivisionError`.** Confirmed reachable:
`fit_axis` returns `None` only when the *sample* values have no spread, and
validly returns `0.0` when distinct recorded UVs all render at the same pixel.
Every inversion then divided by it, and one collapsed capture aborted the whole
multi-view report before any other view was written. Now reported `unavailable`
with the degenerate mapping recorded, with a test that the rest of the report is
still produced.

Checks after the round: **1598 passed, 2 xfailed**; handler count **179**;
geometry-free **PROVEN**; lint clean. All five mutations turn tests red.

---

## Review round 3 — three more findings, all confirmed and fixed

**P1 — a failed annotation bbox collection read as "no annotations".** The reader
deliberately empties `bbox_entries` and keeps production's reason, and nothing
consumed that: the fit said "0 matched", the excursion came back `status: value`
over zero rectangles, and coverage printed an empty table. All four bbox-derived
measurements are now gated on the collection status with the same reason, and a
line near the top of the view's section states it. The review was also right that
the previous test asserted only the reader's intermediate field; the new one
drives `build_report` and asserts on the text a reader consumes.

**P2 — `probe_filter_deleted` checked only half of what its comment promised.**
`RemoveFilter` succeeding while `doc.Delete` fails leaves the id off the view and
a project-wide `ParameterFilterElement` alive; view membership alone declared
that restored. The verdict now also requires `doc.GetElement` to confirm the
element is gone, with "could not determine" as `unverified`. And
`explicit_step_errors` — recorded and consulted by nothing — is now its own
obligation, because a clean rollback could otherwise let the variant conclude
`RAN` and falsely validate an explicit restore the rollback had rescued.

**P1 — a model capture production rejected still ran all five variants.**
`export_color_id_buffer_view` can return a TIFF and usable geometry with
`success=False`. The probe now refuses before any variant, naming the
`failure_reason` and still writing the report — five captures against a rejected
foundation cost a Revit session and prove nothing, and which model faults are
tolerable is Greg's call.

Checks: **1610 passed, 2 xfailed**; handler count **179**; geometry-free
**PROVEN**; lint clean. All four mutations turn tests red.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01LKUqQaYLBZGUMEwj5PLdoa
