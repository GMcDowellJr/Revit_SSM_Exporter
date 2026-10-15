# Hops_Interior_AR series 2025-02-03..2025-11-01 — the 207 "not gridded" rows: small views and the lattice mismatch

KSRF / Hops_Interior_AR, 44 runs under
`C:\Users\gmcdowell\Documents\VOP\Exports\KSRF\Hops_Interior_AR\<date>__<run_id>`,
registered Stage A capture; `tools/find_recapture_views.py --include-failed`
(1.2.0) lists 207 failed-capture view rows across them. Agent task of
2026-10-15. HEAD is `main` after PR #236 merged (release v2.0.0, `c75b750`);
the branch is `claude/festive-gauss-sz69yy` from it.

**The file name.** The task asked for `RUN_<date>_<run_id>_SMALL_VIEWS.md`.
No run folder, run_meta.json or sidecar of the series reached this session
(§6), so there is no run_id to name it by; it is named by the series. Rename
it to the run whose files are read first.

Every claim is OBSERVED (file / function / run named) or INFERENCE. An
absence names the store checked.

## 0. Answer, as far as the files allow

**Received for this task: no run files.** The only series data in the tree
is `tools/notes/data/views_core_2025-10-25.csv` (run `20251025T000000`, an
older build, no sidecars; its README says it is reference data). Google
Drive was searched for `run_meta`, `Hops_Interior_AR` and `recapture_views`
and holds none of them (OBSERVED: `search_files`, 2026-10-15, empty result).
So:

- **Group A** (`annotation_lattice_mismatch`, six 1/48 plans): the
  MECHANISM is settled from the code (§1.1: two branches, one predicate, now
  extracted so the recorded numbers can be run through it); WHICH branch the
  six plans took, and why one environment and not the other, is INFERENCE
  until the sidecar fields in §1.3 are read. No capture-side fix is made,
  because the cause is not shown to be in the capture (§1.4 says what the
  fix would be and what it would break). The capture now RECORDS the one
  number that discriminates the leading candidate on the next run
  (`registration_marks.annotation_extent_prediction`, §1.5).
- **Group B** (small or thin views): the RULE each named view trips is
  INFERENCE from the view's type and scale (§2.1); the policy is decided and
  implemented (§2.2): a view the ticks cannot be placed on is refused BEFORE
  any export with the measured size, the outcome is `unmeasurable`, and the
  finder and the roll-up tell it from a failed capture, in old runs too.
- **Deliverable (d)**: `capture_status` / `CaptureStatus` `unmeasurable`,
  reason `below_capture_floor: ...` or `annotation_export_degenerate` (§3).
- **Comparability**: `Analysis-Impact: none` on every commit (§4): no view
  that captured successfully before captures differently; only the outcome
  of views that never captured changes.

## 1. Group A — `annotation_lattice_mismatch` on the six Wall Types BP plans

Views 4148625, 9468987, 9560929, 9594635, 9595813, 9596211, FloorPlan, scale
48, template "Wall Study" (OBSERVED: `views_core_2025-10-25.csv` rows; that
run is `20251025T000000`, the same model under an older build, so the
metadata is evidence about the views, not about any series run). Greg's
reading of the series: clean 08-23..09-06 and on the 11-01 batch run,
`annotation_lattice_mismatch` on every interactive run 09-13..10-25 (not
re-read here: no run_meta on hand).

### 1.1 The mechanism, from the code (OBSERVED)

`vop_interwoven/color_id_buffer.py`, annotation pass, the block headed "THE
SHARED LATTICE IS THE WHOLE PROMISE OF THIS PASS": the fault is raised in two
cases, now one pure predicate `annotation_lattice_mismatch_faults()` (this
branch) with the same two messages:

1. **The annotation export was accepted at a PixelSize other than the one
   the lattice requires.** For a view whose authored crop is ACTIVE (the
   registered capture sets `color_id_buffer_anno_crop_mode =
   "authored_else_crop_a"`), the required figure is the MODEL pass's
   `crop_px` on the fitted axis (`requested_px_source: "model_crop_px"`).
   What Revit accepts is what `_export_tiff()` returns after its
   measure-and-back-off loop: the export passes only when the fitted axis
   is within 1 px of the request AND neither axis exceeds `max_axis_px`
   (10000, `resolution_contract.MAX_STAGE_A_AXIS_PX`); otherwise it halves
   WHAT REVIT ACCEPTED and re-exports, at most `MAX_MISMATCH_RETRIES` (2)
   times. The annotation pass renders the view's DRAWN extent, not the
   crop: FitToPage fits the drawn content, annotation outside the crop
   included (the comment above `anno_crop_mode` cites the 2026-09-22
   handoff's 21.4 % scale mismatch on one section). So an annotation
   extent whose DERIVED axis exceeds 10000 px at the model lattice comes
   back `mismatch`, is halved, passes at half, and the pass is then
   off-lattice by exactly 2x.
2. **The MODEL export was accepted off its own request**
   (`model_accepted_px != requested_px`). `_set_pixel_size_with_backoff()`
   halves the request when Revit raises on `PixelSize`; the model pass's
   own dimension check compares the file against what was ACCEPTED, so it
   passes and the model sidecar reports `dim_check: "pass"` with
   `pixel_size` at half the request. Only the annotation pass, holding both
   halves through `geometry_out`, raises.

Both are reproduced from the real functions in
`tests/test_annotation_lattice_mismatch_arithmetic.py` (branch 1: 10000 x
10667 -> mismatch -> 5000 x 5333 -> pass -> fault; branch 2: 10000 asked,
5000 taken, file 5000 x 3333, model check passes, fault at the annotation
pass). The sizing for a 1/48 plan at the default 150 dpi (OBSERVED
constants): requested 48 / (12 x 150) = 0.026667 ft/px, so the cap holds any
crop A longer than 266.7 ft and the lattice is then crop_fit_ft / 10000.

### 1.2 Why one environment and not the other — candidates (INFERENCE)

| candidate | branch | what it needs | discriminated by |
|---|---|---|---|
| **A1. Annotation past the crop grew the drawn extent past the cap** on the derived axis (a grid line's head, an elevation marker, a tag far outside the crop; or a linked model's annotation) in the interactive document and not in the batch-opened one | 1 | model `cap_applied` true or crop A near the cap; annotation `dim_check_attempts[0]` with one axis > 10000 and a second attempt at half | annotation sidecar `frame.dim_check_attempts` (every attempt's requested / accepted / actual_w / actual_h); from the next run, `registration_marks.annotation_extent_prediction.exceeds_cap` (§1.5) |
| **A2. Revit's own PixelSize ceiling was lower in the interactive session** (the backoff's docstring: it "can vary by version/install"; a 10000 px request rejected, 5000 taken) | 2 | model `frame.dim_check_attempts[0].accepted_px` < `requested_px` | model sidecar `frame.pixel_size` vs `dim_check_attempts[0].requested_px`; `views_diagnostics` warning `pixel_size_backoff` |
| **A3. A different crop A** (the plans' crop changed in the archive on 09-13: crop A longer, cap applied, lattice coarser) with the annotation extent unchanged | 1 | passing and failing runs' `frame.crop_uv` differ | model sidecar `frame.crop_uv`, `cap_applied`, `pixel_size`; `registration_marks.crop_shape` |
| **A4. A different exporter build** between the as-of 09-06 and 09-13 runs (run_id is the execution clock; the as-of date is the archive date, so the series may have been run on several builds) | either | `run_meta.exporter_version` differs across the boundary | `run_meta.json` `exporter_version`, `git_commit` per run |
| **A5. The batch run's document is smaller** (links not loaded when opened detached from Desktop Connector paths; `opened_from` records detached + worksets, not links) so the annotation extent in the 11-01 run stayed inside the cap | 1 | 11-01 annotation `actual_h` well under 10000 where 10-25's first attempt is over | 11-01 vs 10-25 annotation `dim_check_attempts`; `run_meta.opened_from`; `link_categories` in the registration record |

A1 is the leading candidate because it is the only one in which the SAME
request produces a different file: the six plans are the largest floor
plans in the model, so they are the ones at the cap, where any growth of the
derived axis tips it. "A larger crop because a workset was open" (the task's
candidate) goes the WRONG way for the batch run: the batch opens ALL
worksets (`batch_capture.py`, `DetachAndPreserveWorksets`, never
`CloseAllWorksets`), so it would have the larger extent, yet it passed. A5
(links absent in the batch) is the version of that candidate consistent with
the evidence.

### 1.3 What to read from the sidecars (the OBSERVED facts to fill in)

From one FAILING run (any of 09-13..10-25) and one PASSING run (09-06 and
11-01), per view, `color_id_buffer/<stem>.json` and `<stem>_anno.json`:

| field | model sidecar | annotation sidecar |
|---|---|---|
| `frame.fit_direction`, `frame.view_scale` | the axis | same |
| `frame.pixel_size`, `frame.pre_cap_px`, `frame.cap_applied`, `frame.max_axis_px` | request and cap | accepted |
| `frame.dim_check`, `frame.dim_check_attempts[]` (`requested_px`, `accepted_px`, `actual_w`, `actual_h`, `dim_check`) | branch 2 shows here | branch 1 shows here |
| `frame.crop_uv`, `frame.crop_px`, `frame.achieved_fpp_ft` | the lattice | `registration.requested_px_source` |
| `capture_integrity.capture_faults[].fault` / `.detail` | — | the fault's message names the accepted and required px |
| `registration_marks.crop_shape`, `.mark_reference_source`, `.annotation_extent_prediction` (next run only) | A3, A1 | — |
| `run_meta.json`: `exporter_version`, `git_commit`, `opened_from`, `views[].capture_failure_reason` | A4, A5 | — |

`tests/test_annotation_lattice_mismatch_arithmetic.py::RECORDED` takes one
entry per view from these (model requested/accepted, annotation required,
annotation attempts) and runs them through the predicate unchanged; the
placeholder entry is marked INFERRED.

### 1.4 Could the capture size both passes onto one lattice instead? (question 3)

Yes, arithmetically, for branch 1: size crop A so that the PREDICTED
annotation extent (crop A united with every annotation bbox, which
`annotation_avoid_rects()` already collects before the model pass for the
tick relocation) fits the cap. `frame_export_geometry(union, crop_a, ...)`
then gives the lattice; for the test's 300 x 320 ft case it is 0.032 against
0.030 ft/px (9375 x 10000 frame, crop A 9375 x 6250), and the annotation
pass's request of 9375 renders 9375 x 10000 with no halving
(`test_the_proposed_fix_sizes_crop_a_against_the_predicted_union_...`).

What it does to comparability: the lattice changes ONLY where the union
exceeds the cap, which is exactly where the capture refuses today, so a
view that captured before keeps its lattice -- IF FitToPage's extent is the
bbox union. That is the unconfirmed premise (A1), and the reason the fix is
not made in this branch: made on the wrong premise it would change the
lattice of views that pass today (every view whose bbox union exceeds the
cap while Revit's actual extent does not), which is `breaking`. When the
sidecars confirm branch 1 and the prediction (§1.5) matches the measured
extent, the fix is a `fix(capture)!` declared breaking for the six plans'
runs after it (their lattice becomes 6-7 % coarser in the test case; the
08-23..09-06 captures of the same views were at the crop A lattice) and
`none` for every other view. Branch 2 has no capture-side fix: a ceiling
Revit imposes per install can only be recorded.

### 1.5 What was changed for group A (this branch)

- `color_id_buffer.annotation_lattice_mismatch_faults()`: the two inline
  checks extracted into one pure predicate, used at the call site; same
  messages, same faults (refactor, `Analysis-Impact: none`).
- `stage_a_registration.annotation_extent_prediction()` and
  `registration_marks.annotation_extent_prediction` in BOTH sidecars: crop A
  and the union of crop A with the annotation bboxes, in px on the model
  lattice, `growth` per axis and `exceeds_cap`. A diagnostic read by nothing
  in the capture (`none`). On the next run of the six plans it says, before
  the model pass, whether A1 can be the cause.
- `tests/test_annotation_lattice_mismatch_arithmetic.py`.

## 2. Group B — views below the capture floor

### 2.1 The rules, and which each view trips (per-view: INFERENCE until the sidecars)

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

Per view (metadata OBSERVED from `views_core_2025-10-25.csv`; the rule
column is INFERENCE; the sidecar column names the field that settles it):

| view | type | scale | floor on the short side | rule (inferred) | settles it |
|---|---|---|---|---|---|
| 4622 Drawing Index Submission Matrix Legend | Legend | 1 | 192 px = 0.107 ft (1.28 in) | tick floor: "a single number" is far under 1.28 in + one 1/8 in cell | model sidecar `registration_marks.faults[registration_marks_incomplete].message` ("... N px on its short side, too small for ...") and `frame.crop_uv` |
| 142022 Frame Types | Legend | 48 | 192 px = 5.12 ft | tick floor if the legend's elements span under 5.12 ft on the short side; else degenerate | same |
| 10981328 PLANNING KEYNOTE LEGEND (A112) | Legend | 128 | 192 px = 13.65 ft | tick floor (a keynote table 1.28 in high on paper) | same |
| 11067410 / 11067417 / 11067424 (G010, 2023-06-27_...) | Legend | 1 | 1.28 in | tick floor (text blocks under 1.28 in on their short side) | same |
| 8169028 Glazing Study - L4 East | Elevation | 96 | 192 px = 10.24 ft | a thin strip: tick floor if the crop is under 10.24 ft tall; `aspect_clamp` if the annotation export is >= 9.95:1 | model `registration_marks.faults`; annotation `capture_faults[annotation_export_degenerate].detail` (`reason`, `actual_px`, `model_px`, `aspect`, `coarsening_lower_bound`) |
| 9236660 Section 12 | Section | 128 | 192 px = 13.65 ft | same two candidates | same |

Views 10981328 and 11067410/17/24 are "new 10-11, never captured" (Greg); a
legend that was never on a sheet before 10-11 is consistent with their
appearing then. Not checked: no run_meta.

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

On the series: re-running the finder over the 44 runs (no recapture needed)
moves every old-shape row whose model sidecar is on disk with the "too
small" fault message to `below_capture_floor`, and every
`annotation_export_degenerate` row too; a row whose sidecar is gone stays
`capture_failed` and says it is a candidate. The six group A plans stay
`capture_failed` (`annotation_lattice_mismatch` is not a floor rule).

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
  failing until the sidecars decide §1.4.

## 5. Verification

- `python -m pytest tests/ -q`: 2584 passed at `c75b750` before the change
  (2 xfailed); after it, the count in the PR's description.
- Mutation (OBSERVED, this session): with `raise CaptureBelowFloor`
  disabled, `tests/test_capture_floor.py::test_a_view_under_the_floor_is_
  refused_before_any_export` is red (the view exports twice and comes back
  `registration_failed`); restored, green. The control beside it exports
  both passes and carries the extent prediction in both FILES.
- flake8 at 120 columns: the files this branch touches add no finding
  beyond main's (84 pre-existing in `csv_export.py`, `color_id_buffer.py`
  and `stage_a_registration.py`, none on changed lines).

## 6. Files requested from Greg (one batch)

For group A: from ONE failing run (10-25 is the latest) and TWO passing
runs (09-06 interactive, 11-01 batch), `run_meta.json` and, for the six
plans, `color_id_buffer/<stem>.json` and `<stem>_anno.json` (no TIFFs
needed). For group B: from the latest run, `run_meta.json` and the model
and annotation sidecars of 4622, 142022, 10981328, 11067410, 11067417,
11067424, 8169028, 9236660. Plus the finder's `recapture_views.csv` for the
series. With those, §1.2's table becomes OBSERVED, `RECORDED` in the
arithmetic test takes the six plans' numbers, and §2.1's rule column is
read rather than inferred.
