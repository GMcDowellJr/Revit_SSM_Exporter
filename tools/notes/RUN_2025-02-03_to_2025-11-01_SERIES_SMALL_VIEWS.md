# Hops_Interior_AR series 2025-02-03..2025-11-01 — the 207 "not gridded" rows: small views and the lattice mismatch

KSRF / Hops_Interior_AR, 44 runs under
`C:\Users\gmcdowell\Documents\VOP\Exports\KSRF\Hops_Interior_AR\<date>__<run_id>`,
registered Stage A capture; `tools/find_recapture_views.py --include-failed`
(1.2.0) lists 207 failed-capture view rows across them. Agent task of
2026-10-15. HEAD is `main` after PR #236 merged (release v2.0.0, `c75b750`);
the branch is `claude/festive-gauss-sz69yy` from it.

**The file name.** The task asked for `RUN_<date>_<run_id>_SMALL_VIEWS.md`.
The one run received (§0) is `2025-09-06__20261011T094136`, a PASSING run;
the failing runs the question is about have not been received, so the note
is named by the series. Rename it to the failing run whose files are read.

Every claim is OBSERVED (file / function / run named) or INFERENCE. An
absence names the store checked.

## 0. Answer, as far as the files allow

**Received: one run.** `2025-09-06__20261011T094136_pass-interactive.zip`:
its `run_meta.json` and the model and annotation sidecars of the six Wall
Types BP plans (13 files; no TIFF, no group B sidecar). Google Drive was
searched for `run_meta`, `Hops_Interior_AR` and `recapture_views` and holds
none (OBSERVED: `search_files`, 2026-10-15, empty). The only other series
data in the tree is `tools/notes/data/views_core_2025-10-25.csv` (run
`20251025T000000`, an older build, no sidecars).

- **The received run is NOT an interactive capture.** Its `run_meta.
  opened_from` is `{"source": "batch_capture", "detach":
  "DetachAndPreserveWorksets", "worksets": "all", "title":
  "KSRF_Hosp_Interior_AR_V41_3.1 GB_2025-09-06_detached"}` (OBSERVED). It
  was run on 2026-10-11 by exporter `1.2.0+src.d3ccc3e116cb`, commit
  `036c729c`, Revit 2025 (25.4.41.14), config hash `9400eaef`, 284 views
  requested; on 2026-10-13 52 of its views were recaptured by 1.3.0 for
  `ticks_outside_crop_loop` (`recaptures[0]`; none of the six plans, whose
  sidecars carry `registration_marks.recapture: null`). So the pass/fail
  split is NOT batch-vs-interactive as labelled: at least one passing run
  was batch-opened. What the failing runs' `opened_from` says is the first
  thing to read (§1.3).
- **Group A** (`annotation_lattice_mismatch`): the mechanism is settled
  from the code (§1.1) and the passing run's numbers are OBSERVED (§1.2):
  all six plans accepted at their request, annotation export at the
  model's size to the pixel, and **four of them 1-2 px under the 10000 px
  ceiling on the derived axis** -- 0.03-0.06 ft of model space from a
  dimension mismatch. The bbox-union premise of the first proposed fix is
  FALSIFIED by the same run (§1.4). Which branch the failing runs took is
  still INFERENCE: no failing sidecar received. No capture-side fix is
  made.
- **Group B** (small or thin views): run_meta of the passing run records
  4622, 142022, 9236660 and 8902097 in the tick-floor shape and 8169028
  and 9080636 as `annotation_export_degenerate` (OBSERVED, §2.1); the four
  G010/A112 legends were not requested by that run (consistent with "new
  10-11"). The measured sizes still need those views' sidecars. The policy
  is decided and implemented (§2.2), and the finder run read-only over the
  received run_meta already separates the two degenerate views from the
  four candidates (§2.3).
- **Deliverable (d)**: `capture_status` / `CaptureStatus` `unmeasurable`,
  reason `below_capture_floor: ...` or `annotation_export_degenerate` (§3).
- **Comparability**: `Analysis-Impact: none` on every commit (§4).

## 1. Group A — `annotation_lattice_mismatch` on the six Wall Types BP plans

Views 4148625, 9468987, 9560929, 9594635, 9595813, 9596211, FloorPlan,
template "Wall Study". Greg's reading of the series: clean 08-23..09-06 and
on the 11-01 batch run, `annotation_lattice_mismatch` on every run
09-13..10-25 (not re-read here: no failing run_meta on hand).

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

### 1.2 The passing run's numbers (OBSERVED, `2025-09-06__20261011T094136`)

Horizontal fit, 150 dpi, every crop authored and active, both passes'
`dim_check: "pass"` on their first and only attempt, `capture_faults: []`
in both sidecars, 12 of 12 ticks drawn (`registration_marks.marks`),
`mark_reference_source: "authored_crop"`:

| view | scale | crop A (ft) | pre_cap_px | fitted px | derived predicted / actual | annotation export | headroom under 10000 |
|---|---|---|---|---|---|---|---|
| 4148625 LOWER LEVEL | 48 | 222.6 x 297.7 | 8347 | 7476 (capped) | 10000 / 9999 | 7476 x 9999 | 1 px = 0.030 ft |
| 9468987 LEVEL 1 | 48 | 279.9 x 292.9 | 10495 | 9555 (capped) | 9999 / 9998 | 9555 x 9998 | 2 px = 0.059 ft |
| 9560929 LEVEL 2 | 48 | 280.4 x 300.2 | 10516 | 9340 (capped) | 10000 / 9999 | 9340 x 9999 | 1 px = 0.030 ft |
| 9594635 LEVEL 3 | 48 | 341.7 x 284.4 | 12813 | 10000 (capped, fitted axis) | 8324 / 8323 | 10000 x 8323 | fitted axis AT the ceiling |
| 9595813 LEVEL 4 | 48 | 281.6 x 311.2 | 10561 | 9050 (capped) | 10000 / 9999 | 9050 x 9999 | 1 px = 0.031 ft |
| 9596211 LEVEL 5 | **128** | 202.1 x 354.7 | 2842 | 2842 (not capped) | 4989 / 4988 | 2842 x 4988 | 5012 px |

Three facts follow:

- **Branch 2 is not in play here**: every `accepted_px` equals its
  `requested_px` in both sidecars. Revit 2025 took 10000 for LEVEL 3.
- **Four plans are capped ON THE DERIVED AXIS, and the cap puts them 1-2
  px under the ceiling.** `cap_axes` scales the request by
  `cap / derived`, so the derived axis lands at the ceiling by
  construction; Revit then rounds it to 9998-9999. The annotation pass
  re-exports the same extent at the same request and must come back under
  10000 too. It did here. **Any +1 or +2 px on the annotation export's
  derived axis -- a different rounding, 0.03 ft more extent -- is a
  mismatch, a halving, and `annotation_lattice_mismatch`.** LEVEL 3,
  capped on the FITTED axis, has 1677 px of play on the derived axis and
  would need the extent to grow 20 % to fail the same way; LEVEL 5 at
  1/128 is not capped at all.
- **LEVEL 5 was at scale 128 in the 2025-09-06 archive and at 48 in the
  2025-10-25 one** (`views_core_2025-10-25.csv`, an older build but the
  same model). At 1/48 its 202 x 355 ft crop is 7574 x 13298 px before the
  cap: capped on the derived axis like the other four, 1-2 px under the
  ceiling. A scale change in the archive is a model-side event that puts a
  view INTO the fragile set; the sidecar of a failing run says when.

### 1.3 Why one run and not the other — candidates (INFERENCE), and what decides them

| candidate | branch | what §1.2 says | decided by (failing run) |
|---|---|---|---|
| **A1'. The annotation export's derived axis came back 1-2 px larger than the model's** (9998 -> 10001: a different rounding, a crop read 0.03 ft taller, the annotation crop region, a datum head at the edge) | 1 | four plans have 1-2 px of headroom; LEVEL 3 and LEVEL 5 (at 1/128) do not. If LEVEL 3 ALSO failed on 09-13..10-25, a 1-2 px effect cannot be the whole story | annotation sidecar `frame.dim_check_attempts[0].actual_h` just over 10000 (A1') or far over (a real extent change); `frame.crop_uv` vs 09-06's; which of the six failed |
| **A1. Annotation past the crop extended the drawn extent** | 1 | **Falsified as a bbox-union effect**: unions 1.05-1.80x crop A (detail items with bboxes to -105 ft, 299 ft) and the export at crop A's size to the pixel, on five plans (§1.4) | same fields; `actual_h` far over 10000 would revive a different form of it (an element Revit draws outside the crop that it did not draw in batch) |
| **A2. Revit's PixelSize ceiling lower in that session** | 2 | not seen: 10000 accepted | model sidecar `frame.dim_check_attempts[0]` with `accepted_px < requested_px`; `views_diagnostics` warning `pixel_size_backoff` |
| **A3. A different crop A in the archive** | 1 | LEVEL 5's scale changed between archives; crops may have too | `frame.crop_uv` per run against the table above |
| **A4. A different exporter build** | either | 09-06 ran on 1.2.0+src.d3ccc3e116cb, 2026-10-11; the series was captured over several days and builds | `run_meta.exporter_version` / `git_commit` per run; the 09-13 run's against 09-06's |
| **A5. Opened differently** (the received "interactive" run is batch-opened, detached, all worksets) | 1 | the labels do not hold: see §0 | `run_meta.opened_from` per run: absent for a true interactive run, `batch_capture` otherwise |

A1' is the leading candidate because it needs only what §1.2 shows: a
lattice that is, by construction, within rounding of the ceiling. It is
INFERENCE until a failing annotation sidecar's first attempt is read.

### 1.4 Could the capture size both passes onto one lattice instead? (question 3)

**Sizing crop A against the annotation bbox union is NOT the fix.** The
arithmetic is in `test_sizing_crop_a_against_the_bbox_union_would_change_
passing_views_and_is_not_the_fix`: it would have coarsened the five passing
plans' lattices by 5-80 % (their unions exceed crop A by that much) while
their exports needed nothing. `breaking`, for nothing. Rejected on the
evidence of §1.2.

**What would hold both passes on one lattice** if A1' is confirmed: leave
headroom under the ceiling on the derived axis when the cap binds it
(`cap_axes` targeting, say, `cap - 8` instead of `cap`), so a ±2 px
rounding difference between the two passes cannot cross it. That changes
the fitted pixel count of EVERY derived-axis-capped view that captures
cleanly today (four of these six, and every other capped plan in the
model): their lattice coarsens by ~0.1 %. That is `breaking` -- for every
capped view in the series, not just the failing ones -- and it is the
reason it is not made here: a MAJOR for an unconfirmed cause. If the
failing sidecars show `actual_h` of 10001-10002 (A1'), the fix is a
`fix(resolution)!` with that declaration; if they show a large extent
change, the cause is in the document and the fix is elsewhere. Branch 2
has no capture-side fix: a ceiling Revit imposes can only be recorded.

### 1.5 What was changed for group A (this branch)

- `color_id_buffer.annotation_lattice_mismatch_faults()`: the two inline
  checks extracted into one pure predicate, used at the call site; same
  messages, same faults (refactor, `Analysis-Impact: none`).
- `stage_a_registration.annotation_extent_prediction()` and
  `registration_marks.annotation_extent_prediction` in BOTH sidecars: crop A
  united with the annotation bboxes, in px on the model lattice, `growth`
  per axis and `exceeds_cap` -- a diagnostic read by nothing (`none`).
  **It is not a predictor of the export's extent** (§1.4: the 09-06 run
  would have reported `exceeds_cap: true` on five plans that exported at
  crop A's size). It is recorded so a failing run has the union beside the
  measured extent, which no received file has shown together.
- `tests/test_annotation_lattice_mismatch_arithmetic.py`: both branches,
  a control, the rejected proposal's arithmetic, and `RECORDED` with the
  six 09-06 entries (OBSERVED, no fault, headroom pinned per plan) and one
  INFERRED failing shape until a failing run is read.

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

Per view. The SHAPE column is OBSERVED from `run_meta.views[]` of
`2025-09-06__20261011T094136` (status; reason); the measured size needs the
view's sidecar, not received:

| view | type / scale (views_core 10-25) | floor on the short side | shape recorded 09-06 (OBSERVED) | rule | settles the size |
|---|---|---|---|---|---|
| 4622 Drawing Index Submission Matrix Legend | Legend / 1 | 192 px = 1.28 in | `registration_failed`; `registration_marks_incomplete;registration_marks_may_not_draw` | tick floor (the shape an unavailable layout writes; a lines-hidden view losing a tick writes the same -- INFERENCE until the sidecar's fault message is read) | model sidecar `registration_marks.faults[registration_marks_incomplete].message` ("... N px on its short side, too small for ...") |
| 142022 Frame Types | Legend / 48 | 192 px = 5.12 ft | same | tick floor (same caveat) | same |
| 9236660 Section 12 | Section / 128 | 192 px = 13.65 ft | same | tick floor, NOT the aspect clamp (same caveat) | same, plus `frame.crop_uv` |
| 8902097 ILLUSTRATIVE VIEW - Level 1 - Providers Sleep Room - AB | not in views_core 10-25 | -- | same | tick floor (same caveat); not in Greg's list, same shape | same |
| 8169028 Glazing Study - L4 East | Elevation / 96 | -- | `annotation_failed`; `annotation_export_degenerate` | degenerate export (OBSERVED by fault name); `aspect_clamp` vs `coarsening` needs the detail | annotation `capture_faults[annotation_export_degenerate].detail` (`reason`, `actual_px`, `model_px`, `aspect`, `coarsening_lower_bound`) |
| 9080636 ILLUSTRATIVE VIEW ... AB Copy 1 | not in views_core 10-25 | -- | same as 8169028 | same | same |
| 10981328, 11067410, 11067417, 11067424 | Legend / 128, 1, 1, 1 | 1.28 in (13.65 ft at 1/128) | **not requested by the 09-06 run** (OBSERVED: `views_requested`) | tick floor (INFERENCE) | the 10-11+ runs' sidecars |

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

### 2.3 The finder over the received run (OBSERVED, read-only, this branch)

`python tools/find_recapture_views.py <run_0906> --out <elsewhere>` over
the received `run_meta.json` plus the six plans' sidecars:

| | |
|---|---|
| `failed` | 63: 59 3D views "rejected by capability gating" + 4622, 142022, 8902097, 9236660 (`capture_failed`, reason ending "a below-floor candidate (an unavailable tick layout records these two faults) with no sidecar on disk to confirm it") |
| `below_floor` | 2: 8169028, 9080636 (`rule annotation_export_degenerate`, `evidence run_meta`) |
| `recapture` | the six plans, "ticks drawn but no registration record" -- an artefact of the zip carrying no `_anno.registered.json`, not a finding |

With the four candidates' model sidecars beside the run_meta the same
command moves them to `below_floor` with `evidence sidecar`, or leaves them
`capture_failed` if their fault message is not the size refusal.

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
  (2 xfailed); 2603 after the first push; the count after the 09-06
  evidence is in the PR.
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

## 6. Files still needed (one batch)

For group A, ONE FAILING run (10-25 is the latest; 09-13 is the first):
`run_meta.json` (its `opened_from`, `exporter_version`, and which of the
six failed) and the six plans' `color_id_buffer/<stem>.json` and
`<stem>_anno.json`. The annotation sidecar's `frame.dim_check_attempts[0].
actual_h` decides between A1' (10001-10002), a real extent change (far
over), and branch 2 (`accepted_px < requested_px` in the MODEL sidecar);
`frame.crop_uv` against §1.2 decides A3. The 11-01 batch run's for the
same six would show whether its lattice is 09-06's.

For group B, from the latest run: the model and annotation sidecars of
4622, 142022, 9236660, 8902097 (the fault message with the measured px),
8169028 and 9080636 (the degenerate detail), and of 10981328, 11067410,
11067417, 11067424 from any run that requested them. Plus the finder's
`recapture_views.csv` for the series.
