# Hops_Interior_AR series 2025-02-03..2025-11-01 — the 207 "not gridded" rows: small views and the lattice mismatch

KSRF / Hops_Interior_AR, 44 runs under
`C:\Users\gmcdowell\Documents\VOP\Exports\KSRF\Hops_Interior_AR\<date>__<run_id>`,
registered Stage A capture; `tools/find_recapture_views.py --include-failed`
(1.2.0) lists 207 failed-capture view rows across them. Agent task of
2026-10-15. HEAD is `main` after PR #236 merged (release v2.0.0, `c75b750`);
the branch is `claude/festive-gauss-sz69yy` from it.

**The file name.** The task asked for `RUN_<date>_<run_id>_SMALL_VIEWS.md`.
Three runs were received (§0): two passing and one failing. The note is
named by the series because its findings come from comparing them; the
failing run is `2025-10-25__20261012T122748`.

Every claim is OBSERVED (file / function / run named) or INFERENCE. An
absence names the store checked.

## 0. Answer

**Received: three runs**, each as `run_meta.json` plus the six Wall Types
BP plans' model and annotation sidecars (no TIFF, no group B sidecar):

| archive | run_id | exporter | `opened_from` | the six plans | label on the zip |
|---|---|---|---|---|---|
| 2025-09-06 | `20261011T094136` | `1.2.0+src.d3ccc3e116cb` | `batch_capture`, `DetachAndPreserveWorksets`, worksets `all` | success | "pass-interactive" |
| 2025-10-25 | `20261012T122748` | `1.2.0+src.d3ccc3e116cb` | `batch_capture`, `DetachAndPreserveWorksets`, worksets `all` | `annotation_failed` / `annotation_lattice_mismatch`, all six | "fail-interactive" |
| 2025-11-01 | `20261013T111808` | `1.3.0+src.a469a97b3c59` | `null` (no record: an interactive capture) | success | "pass-batch" |

All three: Revit 2025 (25.4.41.14), config hash `9400eaef`, horizontal
fit, 150 dpi (OBSERVED). **The labels are inverted.** 09-06 and 10-25 are
the SAME build opened the SAME way (the batch runner, detached, all
worksets), one passing and one failing; 11-01 is the interactive one, on
the next build, passing. Neither the environment nor the build separates
pass from fail.

- **Group A is settled (OBSERVED, §1.2-1.3): branch 1, caused by the
  archive's content.** In the 10-25 archive the six plans show 29-43
  elements of the `Views` category (section and elevation markers,
  `view_reference_category` 43-63 in the membership), 10-12 of them with
  bboxes PAST the crop -- section lines 7.3 ft above and 19-35 ft below
  it, and on LEVEL 3 one reaching 203 ft above. Revit's FitToPage export
  with annotation visible rendered that extent: the annotation export came
  back 1.2 % to 61 % TALLER than the model's, over the 10000 px ceiling on
  every plan, was halved, and was accepted at exactly half the lattice. In
  the 09-06 archive the plans' memberships hold NO `Views` element
  (`view_reference_category` 0), and in the 11-01 archive 14-27 of them
  but none past the crop (the six long section lines, ids 1625260,
  1820032, 1873156, 2537144, 3196570, 3246467, are absent): both exports
  at the crop's size. The first failing archive, 09-13, is a week after
  the markers were absent; the first passing one, 11-01, a week after
  they were present. What changed in the model (sections created, then
  hidden in these plans or their markers' "hide at scales" set) is
  INFERENCE; that these markers are the difference is OBSERVED.
- **The capture cannot put both passes on one lattice when the
  annotation extent overruns the cap**; it halves and refuses. The fix
  that holds (§1.4): re-size both passes with `cap_axes` on the
  annotation's MEASURED derived axis and re-export both. Its arithmetic
  is in the test; the re-export loop is not made here (it needs a Revit
  run to validate the second model export inside the group) and is
  `breaking` for the six plans in the runs where the markers show.
- **Two passing captures were not on one lattice either** (11-01, LEVEL 3
  and LEVEL 4: annotation derived axis 0.3 % and 1.5 % off the model's,
  under the cap, no fault): the registration tool measures and resamples
  that; the refusal is about the cap, not about a shared lattice as such.
- **Group B**: the run_meta of 10-25 and 11-01 record every named view
  (OBSERVED, §2.1): 4622, 142022, 9236660, 8902097 and 10981328 in the
  tick-floor shape; 8169028, 9080636, 11067410, 11067417 and 11067424 as
  `annotation_export_degenerate`. The measured sizes still need those
  views' sidecars. The policy is implemented (§2.2); the finder over the
  three run_metas separates the degenerate five from the five candidates
  (§2.3).
- **Deliverable (d)**: `capture_status` / `CaptureStatus` `unmeasurable`,
  reason `below_capture_floor: ...` or `annotation_export_degenerate` (§3).
- **Comparability**: `Analysis-Impact: none` on every commit (§4).

## 1. Group A — `annotation_lattice_mismatch` on the six Wall Types BP plans

Views 4148625, 9468987, 9560929, 9594635, 9595813, 9596211, FloorPlan,
template "Wall Study". Greg's reading of the series: clean 08-23..09-06 and
on 11-01, `annotation_lattice_mismatch` on every run 09-13..10-25. The
10-25 run_meta confirms all six `annotation_failed` /
`annotation_lattice_mismatch`; the 09-06 and 11-01 run_metas confirm all
six `success` (OBSERVED).

### 1.1 The mechanism, from the code (OBSERVED)

`vop_interwoven/color_id_buffer.py`, annotation pass, the block headed "THE
SHARED LATTICE IS THE WHOLE PROMISE OF THIS PASS": the fault is raised in two
cases, now one pure predicate `annotation_lattice_mismatch_faults()` (this
branch) with the same two messages:

1. **The annotation export was accepted at a PixelSize other than the one
   the lattice requires.** With the authored crop ACTIVE (every plan here:
   `frame.crop_write.authored_crop_active: true`, `registration.
   crop_applied: "none"`, `requested_px_source: "model_crop_px"`) the
   required figure is the MODEL pass's `crop_px` on the fitted axis. What
   Revit accepts is what `_export_tiff()` returns after its
   measure-and-back-off loop: the export passes only when the fitted axis
   is within 1 px of the request AND neither axis exceeds `max_axis_px`
   (10000, `resolution_contract.MAX_STAGE_A_AXIS_PX`); otherwise it halves
   WHAT REVIT ACCEPTED and re-exports, at most `MAX_MISMATCH_RETRIES` (2)
   times. An annotation export whose DERIVED axis comes back even 1 px
   over the ceiling is halved, passes at half, and the pass is off-lattice
   by exactly 2x.
2. **The MODEL export was accepted off its own request**
   (`model_accepted_px != requested_px`): `_set_pixel_size_with_backoff()`
   halves on an ArgumentException from `PixelSize`; the model pass's own
   dimension check compares the file against what was ACCEPTED and passes.
   Only the annotation pass, holding both halves, raises.

Both branches are reproduced from the real functions in
`tests/test_annotation_lattice_mismatch_arithmetic.py`.

### 1.2 The three runs' numbers (OBSERVED)

Every crop authored and active (`frame.crop_write.authored_crop_active:
true`, `registration.crop_applied: "none"`, `requested_px_source:
"model_crop_px"`), 12 of 12 ticks drawn, `mark_reference_source:
"authored_crop"`, model pass `dim_check: "pass"` on one attempt in every
run. Crops are identical in 10-25 and 11-01; 09-06 differs on LEVEL 3
(0.37 ft shorter crop) and LEVEL 5 (scale 128, not 48).

**Model pass** (10-25 = 11-01):

| view | scale | crop A (ft) | fitted px (capped on) | derived predicted / actual | ft/px |
|---|---|---|---|---|---|
| 4148625 LOWER LEVEL | 48 | 222.6 x 297.7 | 7476 (derived) | 10000 / 9999 | 0.02977 |
| 9468987 LEVEL 1 | 48 | 279.9 x 292.9 | 9555 (derived) | 9999 / 9998 | 0.02929 |
| 9560929 LEVEL 2 | 48 | 280.4 x 300.2 | 9340 (derived) | 10000 / 9999 | 0.03002 |
| 9594635 LEVEL 3 | 48 | 341.7 x 287.9 | 10000 (fitted) | 8426 / 8425 | 0.03417 |
| 9595813 LEVEL 4 | 48 | 281.6 x 311.2 | 9050 (derived) | 10000 / 9999 | 0.03112 |
| 9596211 LEVEL 5 | 48 | 202.1 x 354.7 | 5697 (derived) | 10000 / 9999 | 0.03547 |

**Annotation pass**, first attempt `actual_w x actual_h` against the
model's, then what was accepted:

| view | 09-06 | 10-25 (FAIL) | 11-01 |
|---|---|---|---|
| 4148625 | 7476 x 9999 = model | 7476 x **10512** (+5.1 %) -> halved -> 3738 x 5256 | 7476 x 9999 = model |
| 9468987 | 9555 x 9998 = model | 9555 x **10345** (+3.5 %) -> 4777 x 5172 | 9555 x 9998 = model |
| 9560929 | 9340 x 9999 = model | 9340 x **10121** (+1.2 %) -> 4670 x 5060 | 9340 x 9999 = model |
| 9594635 | 10000 x 8323 = model | 10000 x **13575** (+61 %) -> 5000 x 6787 | 10000 x 8448 (+0.3 %, under the cap: pass) |
| 9595813 | 9050 x 9999 = model | 9050 x **10305** (+3.1 %) -> 4525 x 5152 | 9050 x 9852 (-1.5 %: pass) |
| 9596211 | 2842 x 4988 = model (1/128) | 5697 x **10273** (+2.7 %) -> 2848 x 5135 | 5697 x 9999 = model |

Every 10-25 fault is branch 1 and reads, e.g., "the annotation export
was accepted at 4777 px on the width axis but the shared lattice requires
9555 px" (`capture_integrity.capture_faults`, annotation sidecar). Branch 2
never occurs: `accepted_px == requested_px` in every model sidecar.

**What is past the crop**, from `annotation_bbox_map` (view-UV bboxes,
`bbox_source: "view"`) against `frame.crop_uv`:

| run | `Views` elements in the plan's membership (`view_reference_category`) | of them past the crop | V extent past the crop (below / above) | other elements past the crop |
|---|---|---|---|---|
| 09-06 | 0 (0) on all six | 0 | 0 / 0 | 1-2 `Detail Items` with bboxes to -105 ft and 299 ft |
| 10-25 | 29-43 (43-63) | 10-12 | 19-35 ft / 7.3 ft; LEVEL 3: 35 / **203 ft** | the same Detail Items |
| 11-01 | 14-27 (28-47) | 0 (LEVEL 4: 2 elevation markers 1 ft past the right edge) | 0 / 0 | the same Detail Items |

The `Detail Items` past the crop (ids 9469050, 9469121 and their
siblings, bboxes 198 x 324 ft) are in all three runs and extend the export
in none of them. The `Views` elements past the crop are in 10-25 only, and
the export grew in 10-25 only; the growth is monotone in their overrun
(LEVEL 2 +20 ft: +1.2 %; LEVEL 3 +238 ft: +61 %) though not equal to the
bbox union (Revit draws a section line, not its bbox). The six section
lines are absent from the 09-06 and 11-01 memberships altogether, not
present-and-inside.

### 1.3 Why one run and not the other

OBSERVED: the archive's content. The same build, opened the same way,
passes on the 09-06 archive and fails on the 10-25 one; the next build,
opened interactively, passes on the 11-01 archive. The one thing that
differs between the failing archive and both passing ones, in these six
plans, is the presence of section markers whose lines run past the crop.
The candidates of the first draft of this note (a lower Revit ceiling, a
different crop, a different build, a batch-vs-interactive extent, a
rounding at the ceiling) are each contradicted by a column of §1.2.

INFERENCE: what happened in the model between 09-06 and 09-13 (the
sections -- ids in the 1.6M-3.3M range, so old elements -- became visible
in the Wall Types BP plans: a template or filter change, a "hide at scales
coarser than" change, a workset or design option made visible) and between
10-25 and 11-01 (hidden again). The sidecars cannot say which; a diff of
the two archives' view settings can.

### 1.4 Could the capture size both passes onto one lattice instead? (question 3)

Yes, and the arithmetic is one existing function. When the annotation
export's first attempt overruns the ceiling on the derived axis, the
capture knows the annotation's aspect from that attempt; `cap_axes(model_
request, anno_actual_h)` gives the request at which the annotation fits,
and both passes re-exported at that request share one lattice:

| view | model request | anno first attempt | co-sized request | both passes then | lattice coarsening |
|---|---|---|---|---|---|
| 4148625 | 7476 | 7476 x 10512 | 7111 | 7111 x 9511 (model), 7111 x 9998 (anno) | 5.1 % |
| 9468987 | 9555 | 9555 x 10345 | 9236 | 9236 x 9664 / 9236 x 9999 | 3.5 % |
| 9560929 | 9340 | 9340 x 10121 | 9228 | 9228 x 9879 / 9228 x 9999 | 1.2 % |
| 9594635 | 10000 | 10000 x 13575 | 7366 | 7366 x 6206 / 7366 x 9999 | 35.8 % |
| 9595813 | 9050 | 9050 x 10305 | 8782 | 8782 x 9703 / 8782 x 9999 | 3.1 % |
| 9596211 | 5697 | 5697 x 10273 | 5545 | 5545 x 9732 / 5545 x 9998 | 2.7 % |

(`test_one_lattice_for_both_passes_is_cap_axes_on_the_measured_annotation_
extent`: the twelve passing entries of the three runs are unchanged by it,
the six failing ones land 1-2 px under the ceiling.)

What it costs: two more exports of a near-10000 px view, only on the
mismatch path; and the lattice of those views in those runs is 1-36 %
coarser than crop A alone gives them -- the 1/8" cell is 18.75 px at the
model's own lattice and 12-18 px at the co-sized one, still well above the
registration's 0.4 px residual. What it changes elsewhere: nothing, since
every view whose annotation export fits the cap keeps its request
(`cap_applied: false` in the test for all twelve passing entries).

Why it is not made in this branch: the re-export runs INSIDE the
registered capture's TransactionGroup after the model pass has painted
and after the white suppression has been applied, so a second model
export means re-painting the model members, re-hiding the detail lines,
re-exporting, re-suppressing, then re-exporting the annotation -- a change
to the one sequence the rollback restores, which this session cannot run
against Revit. It is a `fix(capture)!` to be validated by a probe on
these six views of the 10-25 archive, declared `breaking` for the views
that take the co-sized lattice (they failed before, so the rule of this
repo would allow `none`; the lattice of the same view differing between
runs of a series is what analysis has to know about, so `breaking` is the
honest declaration) and `none` for every other view. Until then the six
plans stay `annotation_failed` in the archives where the markers show.

The first draft's alternative -- sizing crop A against the annotation bbox
union before the model pass -- is rejected on the evidence: the union
exceeds the cap on five passing plans (the Detail Items) and the export
does not follow it (`test_sizing_crop_a_against_the_bbox_union_would_
change_passing_views_and_is_not_the_fix`).

### 1.5 What was changed for group A (this branch)

- `color_id_buffer.annotation_lattice_mismatch_faults()`: the two inline
  checks extracted into one pure predicate, used at the call site; same
  messages, same faults (refactor, `Analysis-Impact: none`).
- `stage_a_registration.annotation_extent_prediction()` and
  `registration_marks.annotation_extent_prediction` in BOTH sidecars: crop A
  united with the annotation bboxes, in px on the model lattice, `growth`
  per axis and `exceeds_cap` -- a diagnostic read by nothing (`none`).
  **It is not a predictor of the export's extent** (§1.2: the Detail
  Items past the crop put the union over the cap on five plans in every
  run, and the export followed only the `Views` elements). Recorded so the
  union and the measured extent sit together in one file; a per-category
  union would be the better diagnostic, and is a follow-up.
- `tests/test_annotation_lattice_mismatch_arithmetic.py`: both branches,
  a control, the rejected proposal's arithmetic, the co-sizing arithmetic
  of §1.4, and `RECORDED` with the eighteen OBSERVED entries of the three
  runs (six passing on 09-06 with the headroom pinned, six failing on
  10-25 with the overrun and the halving pinned, six passing on 11-01 two
  of them with the annotation off the model's lattice under the cap).

## 2. Group B — views below the capture floor

### 2.1 The rules, and which each view trips

OBSERVED rules:

- `stage_a_registration.registration_mark_segments()`: a reference
  rectangle whose short side is under 3 x (inset 24 + gap 8 + minimum arm
  32) = **192 px** on the model lattice cannot take the twelve ticks
  (`mark_floor_px()`, this branch). At the default 150 dpi that is **1.28
  paper inches** on the short side, whatever the scale, before the cap
  (a capped view's lattice is coarser, so its floor in paper inches is
  larger). For a legend or drafting view the rectangle is every element's
  bbox plus one cell (`view_basis.resolve_view_element_bounds()`, no crop
  region); for a cropped view it is the authored crop (or the largest
  rectangle inside a non-rectangular loop).
- `color_id_buffer.annotation_export_degenerate()`: an annotation export at
  aspect >= 9.95 (Revit's 10:1 clamp) or whose derived axis says its
  feet-per-pixel is >= 4x the model capture's.
- What an unavailable layout produced before this branch (OBSERVED:
  `create_registration_marks()` returns `expected_count 0, created_count 0,
  lines_category_hidden_in_view None`; the registered capture then faults
  `registration_marks_incomplete` with "0 of 0 marks drawn: <the layout's
  reason>" and `registration_marks_may_not_draw` on the None): BOTH faults,
  every time, and the capture went on to export both passes.

Per view. The SHAPE column is OBSERVED from `run_meta.views[]` of the
three received runs (identical in 10-25 and 11-01; 09-06 did not request
the four 10-11 legends); the measured size needs the view's sidecar, not
received:

| view | type / scale (views_core 10-25) | floor on the short side | shape recorded (OBSERVED) | rule | settles the size |
|---|---|---|---|---|---|
| 4622 Drawing Index Submission Matrix Legend | Legend / 1 | 192 px = 1.28 in | `registration_failed`; `registration_marks_incomplete;registration_marks_may_not_draw` (all three runs) | tick floor (the shape an unavailable layout writes; a lines-hidden view losing a tick writes the same -- INFERENCE until the sidecar's fault message is read) | model sidecar `registration_marks.faults[registration_marks_incomplete].message` ("... N px on its short side, too small for ...") |
| 142022 Frame Types | Legend / 48 | 192 px = 5.12 ft | same, all three runs | tick floor (same caveat) | same |
| 9236660 Section 12 | Section / 128 | 192 px = 13.65 ft | same, all three runs | tick floor, NOT the aspect clamp (same caveat) | same, plus `frame.crop_uv` |
| 8902097 ILLUSTRATIVE VIEW - Level 1 - Providers Sleep Room - AB | not in views_core 10-25 | -- | same, all three runs | tick floor (same caveat); not in Greg's list, same shape | same |
| 10981328 PLANNING KEYNOTE LEGEND | Legend / 128 | 13.65 ft | same, 10-25 and 11-01 (not requested 09-06) | tick floor (same caveat) | same |
| 8169028 Glazing Study - L4 East | Elevation / 96 | -- | `annotation_failed`; `annotation_export_degenerate`, all three runs | degenerate export (OBSERVED by fault name); `aspect_clamp` vs `coarsening` needs the detail | annotation `capture_faults[annotation_export_degenerate].detail` (`reason`, `actual_px`, `model_px`, `aspect`, `coarsening_lower_bound`) |
| 9080636 ILLUSTRATIVE VIEW ... AB Copy 1 | not in views_core 10-25 | -- | same, all three runs | same | same |
| 11067410, 11067417, 11067424 (G010) | Legend / 1 | 1.28 in | `annotation_failed`; `annotation_export_degenerate`, 10-25 and 11-01 (not requested 09-06) | degenerate export, NOT the tick floor: a scale-1 text block that took the ticks and whose annotation export is at the 10:1 clamp or 4x coarser (INFERENCE which) | same detail |

### 2.2 The policy, decided and implemented

The three options, against "keep every existing successful capture
identical":

- **Widen the frame of small views to 192 px.** For a legend the padding is
  white and harmless to the pixels, but it changes `frame.crop_uv` and the
  grid's cell count for every legend under the floor -- none of which
  captured successfully, so `none` would hold -- while for a cropped thin
  section it renders model geometry the author cropped out, and for the
  10:1 clamp it does nothing. Rejected: two rules, two behaviours.
- **Fewer or shorter ticks on small frames.** `MARK_MIN_ARM_PX` 32 is the
  smallest arm `tools/registration_marks.py` is proven to locate
  (`tests/test_register_stage_a_annotation.py`), and the locator assigns
  ticks by image thirds; a frame under 192 px has no third to put a tick
  in. The analyzer would have to accept a different mark design for these
  views only. Rejected.
- **Refuse with a named reason the analysis treats as "unmeasurable, not
  failed".** Chosen. One rule, decided before any export, with the measured
  size in the reason; the degenerate export, which is only measurable after
  the export, joins it at the status level.

Implemented (this branch):

- `vop_interwoven/capture_floor.py` -- ONE place: the rule names
  (`FLOOR_RULES`: `below_capture_floor`, `annotation_export_degenerate`),
  the status `unmeasurable`, and `floor_rule(status, reason,
  registration_marks)` which classifies the NEW shape (status
  `unmeasurable`, or a floor fault under a failed status) from run_meta
  alone and the OLD shape (`registration_failed` with exactly
  `registration_marks_incomplete;registration_marks_may_not_draw`) only
  with the model sidecar's `registration_marks.faults` message as evidence
  -- the one place the old capture persisted the "too small" reason
  (OBSERVED: `_registration_payload()` writes `marks` as the created list
  and `layout` None, so the reason survives nowhere else). The old shape
  without a sidecar stays a failed capture: a view hiding its lines and
  losing a tick records the same two faults.
- `stage_a_registration.registration_mark_segments()` records
  `below_floor` (short side px, floor px, extent, reference, fpp) beside
  the reason; `mark_floor_px()` is the floor.
- `stage_a_registered_capture`: `CaptureBelowFloor` raised after the mark
  layout and before any export; the group still rolls back and reads the
  view back; the outcome is `success False, failure_reason
  "below_capture_floor: the reference rectangle is N px on its short side,
  too small for 32 px ticks inset 24 px (floor 192 px; reference <source>;
  <fpp> ft/px)"`, with `registration.below_capture_floor` carrying the
  measurement. No TIFF, no sidecar. A recapture (`recapture.run_recapture`)
  gets the same outcome and REPLACES the old failed entry and views_core row
  with `unmeasurable` (it is not `recapture_refused`, which keeps them).
- `csv_export.stage_a_capture_status()`: `unmeasurable` when the model
  failure reason or the annotation failure reason is a floor rule; carried
  unchanged into `run_meta.views[].capture_status`, `views_core
  CaptureStatus` and `views_diagnostics`.
- `tools/find_recapture_views.py` 1.3.0: verdict `below_capture_floor`
  (reason "below_capture_floor (run_meta|sidecar): capture_status ...:
  ..."), per-run list `below_floor` with `rule` and `evidence`, NEVER in
  `recapture`, not with `--include-failed`; the old shape without a sidecar
  is `capture_failed` with "a below-floor candidate ... no sidecar on disk
  to confirm it" in its reason.
- `tools/stage_a_grid_rollup.py` 1.2.0: row status `below_capture_floor`
  (appended to `ROW_STATUSES`; counted in `row_status.counts`;
  `run_meta_capture_status.counts` gains `unmeasurable`), for a requested
  view with no sidecar or with one on disk, by the same classifier.

### 2.3 The finder over the received runs (OBSERVED, read-only, this branch)

`python tools/find_recapture_views.py <run> --out <elsewhere>` over each
received `run_meta.json` plus the six plans' sidecars:

| run | `failed` | `below_floor` (`evidence run_meta`, rule `annotation_export_degenerate`) | candidates left in `failed` with "a below-floor candidate ... no sidecar on disk to confirm it" |
|---|---|---|---|
| 09-06 | 63 (59 3D views "rejected by capability gating" + 4) | 8169028, 9080636 | 4622, 142022, 8902097, 9236660 |
| 10-25 | 76 (65 gating + the six plans `annotation_lattice_mismatch` + 5) | 8169028, 9080636, 11067410, 11067417, 11067424 | 4622, 142022, 8902097, 9236660, 10981328 |
| 11-01 | 71 (66 gating + 5) | the same five | the same five |

The six plans are listed `recapture` on 09-06 and 11-01 ("ticks drawn but
no registration record") only because the zips carry no
`_anno.registered.json`; on 10-25 they are `capture_failed` with
`annotation_lattice_mismatch`, which is not a floor rule. With the five
candidates' model sidecars beside the run_meta the same command moves them
to `below_floor` with `evidence sidecar`, or leaves them `capture_failed`
if their fault message is not the size refusal.

On the series: re-running the finder over the 44 runs (no recapture needed)
reclassifies every old-shape row whose model sidecar is on disk with the
"too small" fault message, and every `annotation_export_degenerate` row;
a row whose sidecar is gone stays `capture_failed` and says it is a
candidate. The six group A plans stay `capture_failed`
(`annotation_lattice_mismatch` is not a floor rule).

## 3. Deliverable (d): what BI filters on

| store | field | value | meaning |
|---|---|---|---|
| `run_meta.json` `views[]` | `capture_status` | `unmeasurable` | below the capture floor; `capture_failure_reason` starts with `below_capture_floor:` (the tick rule, with the measured px) or is `annotation_export_degenerate` |
| `views_core_<date>.csv` | `CaptureStatus` / `CaptureFailureReason` | same | same |
| `views_diagnostics` | `capture_status` | same | same |
| `recapture_lists.json` | `runs[].below_floor[]` | `{view_id, view_name, capture_status, reason, rule, evidence}` | old runs reclassified by the finder, evidence named |
| `grid_rollup.csv` | `row_status` | `below_capture_floor` | the row's reason names rule and evidence |

A change-vs-previous category for these views is neither "gone" (they are
requested and have an outcome) nor "lost" (nothing was measured before);
`unmeasurable` in both runs is "no change", and `unmeasurable` after a
`success` is the one transition worth a look (a legend that grew below the
floor, or a crop that shrank).

## 4. Analysis impact

`Analysis-Impact: none` on every commit that touches `vop_interwoven/`:

- A view whose layout is unavailable for size never produced a successful
  capture (OBSERVED: `registration_marks_incomplete` fires on
  `expected_count 0`, every time), so refusing it before the export changes
  the outcome of no view that captured before. Its files change (none are
  written where a failed model TIFF and sidecar were), which the roll-up
  already treated as not this run's.
- `annotation_export_degenerate` keeps its fault, its files and its sidecar;
  only the status string changes, from `annotation_failed` to
  `unmeasurable`, for views that never captured.
- The lattice-mismatch predicate is an extraction with identical faults and
  messages; `annotation_extent_prediction` is a new diagnostic key.
- `annotation_lattice_mismatch` itself is unchanged: the six plans keep
  failing until a failing run decides §1.3.

## 5. Verification

- `python -m pytest tests/ -q`: 2584 passed at `c75b750` before the change
  (2 xfailed); 2603 after the first push, 2609 with the 09-06 entries,
  and the count with all three runs' entries is in the PR.
- Mutation (OBSERVED, this session): with `raise CaptureBelowFloor`
  disabled, `tests/test_capture_floor.py::test_a_view_under_the_floor_is_
  refused_before_any_export` is red (the view exports twice and comes back
  `registration_failed`); restored, green. The control beside it exports
  both passes and carries the extent prediction in both FILES.
- flake8 at 120 columns: the files this branch touches add no finding
  beyond main's (84 pre-existing in `csv_export.py`, `color_id_buffer.py`
  and `stage_a_registration.py`, none on changed lines).
- `tools/check_stage_a_no_geometry.py vop_interwoven`: PROVEN (333
  reachable, 0 geometry); `check_no_bare_except`: OK; `analysis_version.py
  check`: every `vop_interwoven/` commit declared.

## 6. Files still needed

Group A needs no more files: the cause is OBSERVED. What would move §1.3's
INFERENCE to OBSERVED is a Revit-side diff of the six plans' view settings
between the 09-06 and 09-13 archives (template, filters, the section
markers' "hide at scales coarser than", worksets, design options), which
no sidecar records.

Group B: the model and annotation sidecars of 4622, 142022, 9236660,
8902097 and 10981328 (the `registration_marks_incomplete` fault message
with the measured px), and of 8169028, 9080636, 11067410, 11067417 and
11067424 (the degenerate detail), from the 10-25 or 11-01 run. Plus the
finder's `recapture_views.csv` for the series.
