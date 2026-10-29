# Design note: hash-and-skip (capture only the views that changed)

Status: **decided by Greg 2026-10-28; S-13 confirmed the same day. Phase 1
implemented** (shadow: fingerprints recorded, nothing skipped). Phases 2 and 3
are not.

| decision | Greg, 2026-10-28 |
|---|---|
| S-7 links and imports | always captured in v1; a link-version stand-in is recorded as a future performance option |
| S-8 full refresh | every 7th run, as a setting; 0 means never |
| S-10 carried files | copy, for now |
| S-6.2 baseline | the previous archive in the series |
| S-13 audit / acceptance | 10 % audit, at least 3 per type; 0 false unchanged over at least 1,000 pairs, at least 30 per type; the bar applies PER VIEW TYPE |

## Why

Greg's direction is a daily capture. On a typical archive, though, most views
do not change from one capture to the next. Today every view is captured
every time. For one 1,014-view archive that is 1.4–2.7 h of Revit time
(`views_timing` for runs `20261021T1830` and `20261022T1122`).

Skipping the unchanged views is the largest performance lever found so far.
The other levers measured were:

| lever | measured gain | source |
|---|---|---|
| lower the 10,000 px cap | ~5 % of a run, for 2–8 % fill change | Q2, `PROBE_EXPORT_CAP.md` |
| `batch_size` (GC between chunks) | none expected: the managed heap was flat at 686 MB | `views_timing_2025-11-08.csv` |
| capture detached, never attached | ~1.6x on the attached run | 01-10 (attached) against 11-08 (detached) |
| **skip unchanged views** | **~70 % of views, about half the run time (INFERENCE from one pair)** | Q1b, below |

## Evidence

From `tests/dynamo/PROBE_CHANGE_DETECTION.md`. Runs `20261026T095313`,
`20261028T111442` and `20261028T114224`, comparing V56 (2025-12-13) with V57
(2025-12-20). All OBSERVED.

- **`Document.GetChangedElements` is unusable for archives.** Every detached
  open starts a new version history, and B rejects A's GUID exactly as it
  rejects a random one.
- **`Element.VersionGuid` works.**
  - Reading it for all 446,169 elements took 4.4–4.9 s per archive, with no
    errors.
  - Two detached opens of one file differ on 1,937 elements (0.43 %), the
    same 1,937 each time. Most are Rooms, uncategorised Elements, Room Tags,
    Specialty Equipment and Dimensions.
- **Per-view fingerprint against the captures** (50 seeded views):
  - 16 unchanged views, none with a capture that differs;
  - 27 changed views whose capture also changed;
  - 6 changed views whose pixels were identical: either members modified
    without a visible change, or members re-created with new ids;
  - 1 view new in B.
- **Unchanged by view type:**

  | view type | unchanged |
  |---|---|
  | drafting | 7 of 7 |
  | legend | 6 of 6 (a seventh was new in B) |
  | floor plan | 3 of 7 |
  | area plan, ceiling plan, elevation, section | none |

- **Cost of the check:** membership read median 135 ms (max 3.2 s), member
  types 2 ms, fingerprint under 1 ms.

**What the evidence does not cover.** One archive pair a week apart, 50
views, and 16 unchanged views. "0 false unchanged in 16" is encouraging, not
a rate. Daily pairs, other models, and views that show links or DWGs are all
untested. That is why phase 1 below changes no behaviour.

## The fingerprint

**S-1. A view's fingerprint is a SHA-1 over sorted `id:VersionGuid` pairs.**
The pairs are, for the view as opened:

| component | how it is read | what it catches |
|---|---|---|
| the view itself | its VersionGuid | crop, scale, detail level, V/G overrides, view range: settings stored on the view (INFERENCE that every such edit modifies the view element; phase 1 measures it) |
| its template | `ViewTemplateId`, then its VersionGuid | template edits |
| filters | `GetFilters()` of the view and its template, each filter's VersionGuid | filter rule and override edits |
| members | `view_membership_ids` (the same production read the capture already makes), each member's VersionGuid | added, removed, modified elements |
| member types | `GetTypeId()` of each member, each type's VersionGuid | type edits (a wall type's structure, a tag family) |
| view references | every ElementId-valued parameter of the view and template: phase, phase filter, scope box, underlay levels, associated level | phase and scope-box edits |

The probe covered the first five components. The sixth is new: phase 1 must
show that it never makes a stable view read "changed".

Each component also gets its own SHA-1. The record then says which part
moved, without storing thousands of ids per view.

**S-2. A document-level graphics fingerprint gates every skip.** Some changes
alter the drawing without touching any member: object styles, line weights,
line patterns, fill patterns, materials. So one SHA-1 covers:
- every Category and subcategory's line weights, colour, line pattern and
  material (categories are not elements, so these are read as values);
- the VersionGuid of every `LinePatternElement`, `FillPatternElement`,
  `Material` and `GraphicsStyle`.

If it differs from the baseline, **no view is skipped that run**. This
deliberately over-captures: a style edit is rare, and it is exactly the kind
of change a per-view fingerprint misses.

**S-3. Re-minted ("volatile") elements are NOT excluded.** Ignoring them
cleared 1 of 7 over-cautious views in the probe. It would also mean
maintaining a list, and a wrong list is a false-unchanged risk. So the plain
fingerprint is used.

**S-4. No geometry is read.** VersionGuid, parameters and ids only. This
stays inside the Stage A geometry-free rule, and `check_stage_a_no_geometry`
keeps proving it in CI.

**S-5. "Unknown" never skips.** Any component that cannot be read makes the
fingerprint `null`, and the view is captured. This is the same rule as the
probe's `vg_verdict`.

## When a view may be skipped

**S-6. A view is skipped only when ALL of these hold.** Otherwise it is
captured as today, and the record names the first rule that failed:

1. Skipping is enabled for the run (S-12).
2. There is a **baseline (decided: the previous archive in the series)**: the
   finalized run of the archive just before this one in the model's series
   (the capture plan's order, i.e. the previous as-of date), in which this
   view has a
   **source capture** with a fingerprint of the same basis version (S-9). The
   source capture is the run where the view was actually captured, not
   carried forward, with `capture_status` `success` or a declared
   `single_pass`.
3. The baseline and this run match on:
   - `config_hash`;
   - exporter MAJOR version;
   - Revit `version_build`;
   - open mode (detached, worksets);
   - the fingerprint basis version.
4. The document graphics fingerprint (S-2) equals the baseline's.
5. The view's fingerprint equals its source capture's fingerprint.
6. The view shows no RVT link instance and no import (S-7).
7. It is not this view's turn for a full refresh (S-8).
8. The job is a new capture, not a recapture. Recapture lists always capture.

**S-7. Views showing links or imports are always captured (in v1; decided).**
A host VersionGuid does not move when the linked model or DWG changes. How
many views this excludes is measured in phase 1. A stand-in that would let
them skip is listed under Future options.

**S-8. A periodic full refresh bounds what the fingerprint misses
(decided).** The setting is `skip_full_refresh_every_n_runs`, default **7**;
**0 means never**. It counts the model's runs since its last full capture: a
run is a full refresh, capturing every view regardless of fingerprint, when
the previous archives in the series have run N-1 times since one. The run
records `skip.full_refresh: true` and why. A refresh run is also the largest
audit (S-13): every would-skip verdict is checked against a real capture.

**S-9. The fingerprint has a basis version.** If S-1 or S-2 changes (a new
component, a different read), the version goes up. A baseline from an older
basis cannot be compared, and the view is captured.

## What a skipped view looks like in the run

The rule is the repo's existing one: **readers never infer a skip from a
missing file** (CLAUDE.md, single-pass captures).

**S-10. The source capture's files are COPIED into the new run (decided).**
Hard links, which cost no disk on NTFS, are a future option.
- The files are the model and annotation TIFFs and sidecars, under the
  view's own stem in `color_id/`.
- The bytes are identical, so every existing reader (registration, grid,
  roll-up, census) works unchanged. Each copy is checked by sha256 against its
  source; a mismatch captures the view instead.
- The sidecars are not rewritten. A sidecar's recorded `tiff_path` still
  names the source run; the decoder resolves the sibling when that path is
  gone.

The alternative is a pointer only (no files): every reader would have to
resolve it, and a deleted source run would orphan the view.

**S-11. The skip is DECLARED in `run_meta` and `views_core`.**
- `run_meta.views[]` gets `capture_status: "unchanged"`.
- `carried_from` names the source capture's `{run_id, run_dir, as_of_date}`.
  It always points at a source capture, never at another carried copy, so a
  chain of skips is one hop.
- The entry also records `fingerprint` and `skip_check`: the rules evaluated,
  the times, and the baseline compared.
- `views_core.CaptureStatus` reads `unchanged`.
- The run gains `skip_summary`: counts by outcome and by refusal rule, and
  the graphics-fingerprint verdict.
- Tools that count captures by status learn `unchanged`:
  `find_recapture_views`, `stage_a_grid_rollup`, the runs index, census
  pairs.

## Where it runs, and what it costs

- **Placement.** After the archive opens, the batch reads every element's
  VersionGuid once (about 5 s) and the document graphics fingerprint. Then,
  per view and before any collection or transaction, it reads membership,
  types, template, filters and references, and checks S-6. A captured view
  passes its membership read to the registered capture, which reads it today
  anyway, so a captured view pays nothing extra.
- **Read-only.** No transaction is opened.
- **Cost.**
  - A skipped view costs about 150 ms, against a 2.9–3.6 s median capture.
  - A captured view costs a few ms on top of its capture.
  - Per archive, about 5 s, plus one baseline lookup (`run_meta` of the
    previous run).
- **Comparability.** Skipping changes no captured value for a captured view.
  For a skipped view the run holds the source capture's bytes, and declares
  it. The switch is a run-level option recorded in `run_meta` and left out of
  `config_hash` (as `view_cache_*` is): it does not change what a capture
  produces, and putting it in the hash would break every baseline comparison.
  So, by the repo's rule:
  - the recording commits are `Analysis-Impact: none`;
  - the skip commit is `none` with the option off by default.

  Analysis that wants only freshly captured views reads `carried_from`.

**S-13. Audit and acceptance (confirmed 2026-10-28).**
- *Audit.* On every skipping run, a seeded random sample of the would-skip
  views is captured anyway and compared with its source capture.
  - The sample is **10 %** of the would-skip views (`skip_audit_fraction`),
    but **at least 3 per view type present** (`skip_audit_min_per_type`).
    The floor exists because drafting views, 589 of 1,014 and the lowest
    risk, would otherwise take most of the sample.
  - The seed is the run id, so the sample is reproducible.
  - Cost: 10 % of the savings (about 70 % of views skipped becomes about
    63 %).
- *What it measures.* If 0 false unchanged are observed in *n* checks, the
  true rate is below about 3/*n* at 95 % (the rule of three). One run's
  ~70 audits bounds it below about 4 %. A 7-run cycle (6 audited runs plus
  one full refresh, ~1,100 checks) bounds it below about 0.3 %.
- *Acceptance, phase 1 to 2.* **0 false unchanged over at least 1,000
  would-skip view pairs, with at least 30 per view type.** That is below
  0.3 % overall and below 10 % per type, at 95 %.
- *The bar is applied per view type.* A type that has met it may skip; a
  type that has not keeps being captured. Drafting views and legends reach
  30 unchanged pairs in one archive pair. Sections (0 of 7 unchanged in the
  probe, about 21 per archive) may never reach it, and are then always
  captured, which is the safe outcome.
- *The percentage is an audit fraction, not a tolerated error rate.* A false
  unchanged carries a stale capture forward silently.
- *What counts as "the capture differs".* Model pass exactly. Annotation pass
  exactly, except text anti-aliasing FRINGE: a pixel that is white in one
  capture and an element in the other, with that same element within 2 px
  (`FRINGE_RADIUS_PX`) in the first. Revit anti-aliases text whatever smooth
  edges is set to, and the edge blend is not repeatable. OBSERVED
  2026-10-29 on three of the 383 flagged drafting views of 12-27 -> 01-03
  (1, 3 and 216 differing pixels): every differing pixel was fringe and
  Text Notes only; colour assignments, element id sets and (1/8" cell,
  element) presence were identical. The validator counts such views as
  `fringe_tolerated` inside `true_unchanged`; `--exact` turns that off.
  *Declared limit:* an edit whose every changed pixel lies within 2 px of
  the same element's other pixels (a one-glyph change in very small text)
  would read as fringe. The audit uses the same definition, so this limit
  applies to it too.
- *On a false unchanged.* If an audit or a refresh finds one, skipping is
  suspended for that model (`skip.suspended`, naming the view and what
  differed) until a person clears it. Every later run captures everything
  meanwhile.

**S-12. Off by default.** A batch setting `skip_unchanged` (`batch_settings.json`)
and a thinrunner input turn it on. `run_meta.skip.enabled` records it.

## Phases

**Phase 1, shadow (no behaviour change; IMPLEMENTED).** Runs in shadow mode
get no performance benefit: every view is still captured, and the
fingerprints add about 5 s per archive plus about 150 ms per view (the
membership read is not yet shared with the capture). The 1,000 pairs need
not come from new runs, though: the batch's **fingerprint backfill** writes
fingerprints into runs already captured, so the archive series captured so
far can be scored at once, and new runs keep adding pairs.

What exists:
- `vop_interwoven/view_fingerprint.py`: the fingerprint (S-1, S-2, S-4,
  S-5, S-9), `skip_verdict` (S-6 rules 1-6; full refresh, audit and
  recapture are phase 2), `fingerprint_views`, `write_fingerprints`,
  `fingerprint_existing_run`;
- `capture_run.run_capture(record_fingerprints=True)`: the batch (setting
  `record_fingerprints`, default true) and the thinrunner (with Stage A on)
  write `view_fingerprints_<d>.json` beside every run, read BEFORE any view is
  captured; run_meta gains `view_fingerprints` (summary, or the error; a
  failure never fails the capture);
- the batch's `fingerprint` plan mode (`plan_capture_batch.py --mode
  fingerprint`): one job per archive with finalized runs, captures nothing,
  and stamps its own open as `fingerprint_opened_from`;
- `tools/validate_change_skip.py RUN_A RUN_B ...`: scores every consecutive
  pair with production's `skip_verdict` against the captures, and reports
  the acceptance bar per view type.

The original plan for phase 1:
- Every capture records its fingerprint and component hashes in the model
  sidecar and in `run_meta.views[]`. If the previous run of the same model
  is available, it also records the would-skip verdict and the first failing
  rule.
- A new `tools/validate_change_skip.py` takes two consecutive runs of one
  model, crosses would-skip against "capture differs", and lists every false
  unchanged with what differed. It reuses `analyze_change_detection_probe`'s
  comparison and its tick/frame-line exclusion.
- Run over the archive series, it gives thousands of view pairs instead of
  50, including daily pairs if any exist, plus the link/import exclusion
  rate (S-7) and the S-1 references component's behaviour.

**Phase 2, skip.**
- Enabled per batch once phase 1 meets the acceptance bar (S-13). Implements
  S-8, S-10, S-11, S-12, S-13 and the reader changes.
- The audit (S-13) and every full refresh (S-8) keep measuring the
  false-unchanged count, so it is never assumed once.

**Phase 3, analysis reuse (optional).** The grid and derive steps can link
their outputs for a carried view whose input hashes equal the source's,
instead of recomputing identical results.

## Future options (recorded, not in v1)

- **Links and imports can skip** (performance). A stand-in for "the linked
  model or DWG did not change" would let views showing them skip too:
  - the link document's own `GetDocumentVersion` (unprobed: whether a link
    loaded under a detached host keeps a stable GUID);
  - the link type's external file reference (path, size, modified time);
  - a VersionGuid read of the link document itself.

  It needs its own probe, and phase 1's count of excluded views says what it
  is worth.
- **Hard links instead of copies** (disk): no space for carried views on one
  NTFS volume. They need the run and its source on the same volume, and a
  deleted source run must not matter (a hard link keeps the data).
- **A staggered refresh** (smoother cost): refresh each view when its own
  chain of carried copies reaches N, instead of whole runs. This avoids one
  full-cost run in seven.
- **Ignoring re-minted elements** (S-3): about 2 % more views skippable on the
  probe's evidence (1 of 50). It needs a maintained list.

## Open decision for Greg

- **S-13:** confirm or change the audit fraction (10 %), the per-type floor
  (3), and the acceptance bar (0 false unchanged over at least 1,000 pairs,
  at least 30 per type).
