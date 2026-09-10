# External staged campaign planner

`tools/campaign_planner.py` is a standard-library-only process outside Revit. It does not import Revit APIs, probe registries, or analyzers.

## Contract and operator loop

1. `campaign.json` is immutable intent, validated against `campaign/campaign.schema.json`.
2. The planner atomically persists `campaign_state.json`, described by `campaign/campaign_state.schema.json`, and emits PR 2's `next_batch.json` contract. The optional campaign fingerprint is accepted by the thin executor; each job's execution fingerprint remains the executor's fingerprint of the exact five job fields.
3. Dynamo executes that file and writes one `revit_run_manifest.json` plus raw TIFF/JSON artifacts. A manifest proves execution only; it never proves acceptance.
4. `tools/analyze_stage_a_probe.py` runs externally and emits PR 3 normalized acceptance records. The planner ingests those records, gates dependants, and emits the next batch.

```bash
python tools/campaign_planner.py validate examples/stage_a_campaign.json
python tools/campaign_planner.py init examples/stage_a_campaign.json work/campaign_state.json
python tools/campaign_planner.py next-batch examples/stage_a_campaign.json work/campaign_state.json work/next_batch.json
# Run work/next_batch.json using the Dynamo thin executor.
python tools/campaign_planner.py ingest-runs examples/stage_a_campaign.json work/campaign_state.json evidence/*/revit_run_manifest.json
python tools/analyze_stage_a_probe.py evidence/*/revit_run_manifest.json
python tools/campaign_planner.py ingest-analysis examples/stage_a_campaign.json work/campaign_state.json evidence/*/*.analyzed.json
python tools/campaign_planner.py status examples/stage_a_campaign.json work/campaign_state.json
```

State writes use a flushed temporary file and atomic replacement. Resume by rerunning the appropriate ingest and next-batch commands: evidence identities and transition-event identities make ingestion idempotent. Duplicate identities with different content become explicit conflicts.

## Identity, transitions, and gates

A job ID is derived from campaign ID, stage, view key, probe, case, variant, and repetition, with a SHA-256 suffix. Canonical configuration fingerprints exclude notes and timestamps but include all execution-affecting configuration. Campaign drift supersedes prior jobs and requires an explicitly initialized amended campaign state rather than reusing evidence ambiguously.

Legal transitions are:

- `PLANNED -> ELIGIBLE | BLOCKED | NEEDS_DIAGNOSTIC | SKIPPED | SUPERSEDED`
- `ELIGIBLE -> BATCHED | BLOCKED | SKIPPED | SUPERSEDED`
- `BATCHED -> EXECUTED | FAILED | ELIGIBLE | SUPERSEDED` (`FAILED` is used directly when the executor reports failure without an analyzable result envelope)
- `EXECUTED -> ANALYZED | FAILED | INCONCLUSIVE | SUPERSEDED`
- `ANALYZED -> PASSED | FAILED | INCONCLUSIVE | SUPERSEDED`
- `FAILED -> NEEDS_DIAGNOSTIC | SUPERSEDED`
- `INCONCLUSIVE -> NEEDS_DIAGNOSTIC | SUPERSEDED | PASSED | FAILED` (`PASSED`/`FAILED` are reachable *only* through an authorized, gate-scoped manual review resolving the specific analyzer gate that produced the INCONCLUSIVE outcome - see "Gate-scoped manual review" below; never a side effect of an unrelated review or of ingesting a new analysis record)
- `BLOCKED -> ELIGIBLE | NEEDS_DIAGNOSTIC | SKIPPED | SUPERSEDED`
- `NEEDS_DIAGNOSTIC -> ELIGIBLE | BLOCKED | SUPERSEDED`; all accepted, skipped, or superseded evidence can only be superseded.

Every transition has a reason code and deduplicated history event. A normalized `PASS` is the only default successful dependency. `FAIL`/`INCONCLUSIVE`, restoration/rollback failures, missing records, and conflicts never become passes. An envelope-less executor failure is finalized directly as `FAILED` because the external analyzer has no record it can analyze; failures with an envelope still await normalized analysis. Dependencies are job-specific, so one view cannot block unrelated matrix cells. Manual review is a separate record and can remain pending after automated `PASSED`; a rejected required review produces `BLOCKED_BY_FAILURE` with reason `MANUAL_REVIEW_REJECTED`, never campaign completion.

Batch selection sorts eligible jobs by configured stage and job order, applies `execution_defaults.batch_size`, and moves only selected jobs to `BATCHED`. Already batched/executed jobs are not selected. With no eligible work, the recommendation distinguishes complete, Revit execution, analyzer, failure, manual review, and invalid/conflicting state.

### Dependency `statuses`: `PASS` / `FAIL` / `INCONCLUSIVE` / `BLOCKED`

Each `dependencies` row lists which of its upstream job's outcomes satisfy it. `BLOCKED` is a legal, but never implicit, member of that set: a dependent job only treats an upstream `BLOCKED` status as satisfying when its own dependency row explicitly lists `BLOCKED`. This is how Stage 7 (`stage_a_external_sources`) is wired: its jobs depend on every Stage 6 linework-validation job with `statuses: ["PASS", "FAIL", "INCONCLUSIVE", "BLOCKED"]` - external-source investigation proceeds once the independent alignment/mutation matrix has *concluded*, whatever it concluded, but never before every one of those jobs reaches a terminal-or-blocked state, and never merely because *some* stage failed. A dependency row that does not list `BLOCKED` still treats an upstream `BLOCKED` (like `NEEDS_DIAGNOSTIC`/`SKIPPED`/`SUPERSEDED`) as a hard, non-recoverable-without-intervention gate failure for that dependent, exactly as before.

### Stage status: `ACTIVE` / `BLOCKED` / `COMPLETE` / `COMPLETE_WITH_FAILURES`

`stage_status` never reports `COMPLETE` unless every job in the stage actually succeeded (`PASSED`/`SKIPPED`). A stage whose jobs are all terminal but include a `FAILED`/`INCONCLUSIVE`/`SUPERSEDED` job reports `COMPLETE_WITH_FAILURES` - complete in the sense that no more work will happen in it on its own, but never presented as if it succeeded.

## Conditional fallbacks (deterministic mutation closures)

An optional, additive top-level campaign array, `conditional_fallbacks`, expresses a generic "if job X fails for *this specific, machine-readable reason*, schedule fallback job Y" branch - the mechanism Stage 1's elevation mutation closure uses to decide between `attached_AS` and `detached_AS` without ever hard-coding a run, view, or result:

```json
{
  "fallback_id": "elevation_mutation_closure",
  "trigger_job": "s1.attached_as",
  "trigger_reason_codes": ["MUTATION_BLOCKED_BY_TEMPLATE_ONLY"],
  "fallback_job": { "job_key": "s1.detached_as", "view_key": "elevation",
    "probe_id": "stage_a_minimum_id_mutations", "case": "closure_fallback",
    "variant": "detached_AS", "repetition": 1, "settings": {"dpi": 150} }
}
```

Each fallback rule tracks one **closure** in `state["closures"][fallback_id]`, which is the "separate closure-decision record" this campaign uses instead of overloading a single job's acceptance status:

- `PENDING` until `trigger_job` reaches a terminal status.
- `trigger_job` **PASSED** -> `RESOLVED_PRIMARY`, `candidate_job_id` = the trigger job. Nothing else happens.
- `trigger_job` **FAILED/INCONCLUSIVE** and its normalized acceptance record's `reason_codes` are a superset of the configured `trigger_reason_codes` -> the `fallback_job` is materialized exactly once (with `provenance.fallback_of_job_id`/`trigger_reason_codes_matched` recorded on it) and the closure becomes `AWAITING_FALLBACK_EXECUTION`. Once the fallback job itself reaches a terminal status, the closure resolves to `RESOLVED_FALLBACK_PASS` or `RESOLVED_FALLBACK_FAILED`, `candidate_job_id` = the fallback job.
- `trigger_job` **FAILED/INCONCLUSIVE for any other reason** (an unrelated mutation failure, a rollback failure, anything whose reason codes do not match) -> `RESOLVED_FAILED` directly on the trigger job. The fallback is never scheduled, and `attached_AS` is never manually marked passed.

A `dependencies` row may target a closure instead of a job with `requires_closure` (in place of `requires_job`); `PASS` is satisfied only by `RESOLVED_PRIMARY`/`RESOLVED_FALLBACK_PASS`, everything else maps to `FAIL`. This is how Stage 2 (elevation alignment) depends on the *outcome of the closure*, not on `attached_AS` specifically - so it opens up correctly whichever candidate (attached or detached) actually resolved the closure, with full provenance preserved back through both attempts.

Analyzer-side, `MUTATION_BLOCKED_BY_TEMPLATE_ONLY` is emitted (alongside the always-present `MUTATION_ATTESTATION_FAILED`) only when *every* unmet requested mutation on the failing variant is `BLOCKED_BY_TEMPLATE`; a variant with even one unrelated mutation failure alongside a template block never gets this code, so an unrelated failure can never accidentally trigger the fallback.

## Gate-scoped manual review

Distinct from the campaign-authored `manual_review_required`/`manual_review_reason` job flags (used e.g. for Stage 6's linework visual inspection, which never changes a job's own status - it only gates `CAMPAIGN_COMPLETE`), `ingest_analysis` automatically seeds a **gate-scoped** manual review requirement (`manual_review_requirements[job_id]["gate"] == "rendered_semantic_preservation"`) whenever a normalized acceptance record's only reason for `INCONCLUSIVE` is `MANUAL_SEMANTIC_REVIEW_REQUIRED`. Recording an outcome for that specific requirement (`manual-review JOB_ID ACCEPTED|REJECTED reviewer`) transitions the job itself - but *only* when `MANUAL_SEMANTIC_REVIEW_REQUIRED` was the job's sole reason code: `ACCEPTED` -> `PASSED`, `REJECTED` -> `FAILED`. If any other FAIL/INCONCLUSIVE reason is present on the same job (e.g. `MUTATION_ATTESTATION_FAILED` alongside it), no gate-scoped requirement is seeded at all and the job's own automated result stands - a manual review can never become a blanket override of an unrelated automated finding.

### Manual review requires a reviewable artifact

Every requirement in `manual_review_requirements` carries its own `artifact_references`, populated only from raster (PNG/TIFF/JPEG) paths actually recorded on the job's own `artifact_references` (`_sync_review_evidence`, called from `ingest_analysis`). A requirement is `PENDING` (actionable) only while at least one such reviewable artifact is on record.

If a job that needs manual review reaches a terminal status with nothing reviewable ever recorded, its requirement is downgraded from `PENDING` to `EVIDENCE_MISSING` with a `diagnostic` explaining why, instead of staying an actionable review. For the automated gate specifically, an `INCONCLUSIVE`/`MANUAL_SEMANTIC_REVIEW_REQUIRED` result with no reviewable artifact is seeded directly as `EVIDENCE_MISSING` with `reason: "SEMANTIC_PRESERVATION_NOT_AUTOMATICALLY_VERIFIED"` - it is never presented to an operator as "manual review required", because an unresolved automated check is not the same thing as an actual reviewable artifact existing.

`record_manual_review` refuses `ACCEPTED` and `REJECTED` outright (`NO_REVIEWABLE_ARTIFACT` `CampaignError`) for any requirement with no reviewable `artifact_references`, whatever its status - an operator can never rubber-stamp a review with nothing to look at. `compute_next_recommendation` treats any `EVIDENCE_MISSING` requirement the same way it treats a rejected review: it reports `BLOCKED_BY_FAILURE`/`MANUAL_REVIEW_EVIDENCE_MISSING` and the campaign can never report `CAMPAIGN_COMPLETE` while one remains.

`manual_review_evidence_warnings(state)` (also surfaced under `status`'s `manual_review_evidence_warnings`) is a read-only check for state written before this rule existed: an `ACCEPTED`/`REJECTED` requirement with no reviewable `artifact_references` is flagged `DECISION_RECORDED_WITHOUT_REVIEWABLE_ARTIFACT` without rewriting the historical record.

### Packaging a campaign for review

```bash
python tools/campaign_planner.py package campaign.json campaign_state.json out/package --source-root evidence/
```

`package_campaign` copies every raster artifact referenced by a `PENDING`, `ACCEPTED`, or `REJECTED` manual review requirement into `<output>/artifacts/manual_review/<job_id>/`, alongside self-contained `campaign.json`/`campaign_state.json` copies whose `artifact_references` are rewritten to those packaged, relative paths - so the package can be reviewed without access to the original evidence directory. A referenced artifact that cannot be found on disk fails the whole command (`MISSING_ARTIFACT_SOURCE`) rather than silently shipping a state that points at nothing.

## Diagnostics, resets, and amendments

Authorize only a configured expansion, for example the targeted A/S/F rule:

```bash
python tools/campaign_planner.py diagnostic campaign.json campaign_state.json request JOB_ID --rule targeted_asf
python tools/campaign_planner.py diagnostic campaign.json campaign_state.json clear JOB_ID
python tools/campaign_planner.py diagnostic campaign.json campaign_state.json reset JOB_ID
python tools/campaign_planner.py manual-review campaign.json campaign_state.json JOB_ID ACCEPTED operator-name
```

A request materializes only the configured variants for that source job and cell, never the full factorial. The failed source remains in `NEEDS_DIAGNOSTIC`; it is not rescheduled alongside its diagnostic variants. Diagnostic IDs include the source job identity, so repeated jobs receive independent expansions. Rules without a non-empty `variants` expansion are rejected rather than authorizing work that cannot be scheduled. A reset preserves old provenance as `SUPERSEDED`; it does not erase evidence. To amend execution-affecting settings, create a new campaign version/ID and initialize new state. Notes may change without drift. Copy/reference prior raw evidence through new normalized records only when the campaign explicitly models that evidence; never edit old state fingerprints.

`status` always derives its recommendation from current job and review state without changing scheduling state. It therefore reports newly eligible work as `GENERATE_NEXT_BATCH` immediately after analysis ingestion instead of repeating an obsolete `RUN_BATCH_IN_REVIT` recommendation.

`migrate` backfills structural containers added by newer planner code (currently `closures`) into an older on-disk `campaign_state.json`, so it keeps loading. It never touches job statuses, history, or run/analysis linkage, and it never reconciles evidence against a campaign document that has since changed - that is still `CONFIGURATION_DRIFT`, handled the same way it always has been (see "Recovering an existing failed Stage 1 state" below):

```bash
python tools/campaign_planner.py migrate campaign.json campaign_state.json
```

## Recovering an existing failed Stage 1 state

This is the exact situation the supplied `stage-a-initial` evidence describes: `attached_AS` executed successfully in Revit, was correctly analyzed as `FAIL` (`MUTATION_ATTESTATION_FAILED`, blocked specifically by the view template), and Stages 2-7 are `BLOCKED`/gated as a result - captured under an older campaign document that had no `conditional_fallbacks` construct to express the `detached_AS` fallback.

1. **Keep everything.** Do not delete or edit `campaign_state.json`, any `manifests/<run_id>/revit_run_manifest.json`, any `*.analyzed.json`, or anything under `raw/`. Back up the state file for reference: `cp campaign_state.json campaign_state.json.pre-fallback-fix.bak`.
2. **Adopt the corrected `campaign.json`** (this repo's `examples/stage_a_campaign.json` as a starting point, or your own copy with real view identities): it fixes the `dpi`/`selection`/`attached_element_overrides_only`/Stage 4-7 dependency-wiring defects and adds the `elevation_mutation_closure` fallback plus `s2.*`'s `requires_closure` gate.
3. **Migrate, then initialize a fresh state bound to the corrected campaign.** The corrected campaign's document differs from the old one (new `conditional_fallbacks`, fixed dependencies), so this **is** `CONFIGURATION_DRIFT` by design - the existing safety net supersedes every prior job (never deletes; `SUPERSEDED` jobs keep their full `run_ids`/`analysis_record_ids`/`history`) rather than silently reusing state bound to a different configuration fingerprint:
   ```bash
   python tools/campaign_planner.py migrate campaign.json campaign_state.json   # optional if already on the latest schema
   python tools/campaign_planner.py init campaign.json campaign_state.json     # fresh state, same job_id for s1.attached_as
   ```
   `s1.attached_as`'s `job_id` is derived only from campaign_id/stage/view/probe/case/variant/repetition, so it is identical before and after this migration - the superseded job in the backed-up state file and the newly-initialized job are provably the same identity, linking the original successful Revit manifest and its failed normalized acceptance to the campaign going forward even though execution/analysis evidence is not auto-replayed into the fresh state (see next step).
4. **Regenerate and run the detached_AS fallback batch.** The fresh state's only eligible job is `s1.attached_as` again (evidence is not implicitly reused across a superseding init - Revit re-execution under the corrected settings mapping is required so the fingerprints the executor records actually match this campaign version):
   ```bash
   python tools/campaign_planner.py next-batch campaign.json campaign_state.json next_batch.json
   ```
   Run `next_batch.json` through the Dynamo thin executor. In Dynamo, first run with `IN[2] = True` (validation-only) to confirm the adapter accepts the batch's settings (no unknown keys, no `dpi`/`target_dpi` conflict, `attached_AS` a supported selection) without starting a transaction or exporting a TIFF; then run for real.
   ```bash
   python tools/campaign_planner.py ingest-runs campaign.json campaign_state.json manifests/<run_id>/revit_run_manifest.json
   python tools/analyze_stage_a_probe.py manifests/<run_id>/revit_run_manifest.json
   python tools/campaign_planner.py ingest-analysis campaign.json campaign_state.json manifests/<run_id>/revit_run_manifest.analyzed.json
   python tools/campaign_planner.py status campaign.json campaign_state.json
   ```
   If `attached_AS` again reports `FAIL` with `MUTATION_BLOCKED_BY_TEMPLATE_ONLY`, the planner automatically materializes `s1.detached_as` as `ELIGIBLE`. Repeat `next-batch` / run in Dynamo / `ingest-runs` / analyze / `ingest-analysis` for it.
5. **Status before elevation alignment (Stage 2) becomes eligible.** `python tools/campaign_planner.py status campaign.json campaign_state.json` must show the closure resolved (`state["closures"]["elevation_mutation_closure"]["status"]` is `RESOLVED_PRIMARY` or `RESOLVED_FALLBACK_PASS`, never left `AWAITING_FALLBACK_EXECUTION`/`RESOLVED_FAILED`) before `s2.align.r1`/`s2.align.r2` appear as `ELIGIBLE`; `next_recommendation.code` reads `GENERATE_NEXT_BATCH` once they do.

At no point does this procedure instruct manually editing `campaign_state.json` to set a job `PASSED`, deleting a prior manifest, or discarding the original `attached_AS` evidence - the superseded job record, and the backed-up state file, keep that linkage permanently auditable.

## `stage_a_cycle.py` - one command between Dynamo runs

`tools/stage_a_cycle.py` is a thin, idempotent wrapper around the planner and
`tools/analyze_stage_a_probe.py`. It runs outside Revit, imports no Revit API
modules, executes no probes, and makes no acceptance/gating/fallback decision
itself - every decision above still comes from `campaign_planner.py` and
`analyze_stage_a_probe.py` exactly as documented above. It exists only to
remove the manual `ingest-runs` / analyze / `ingest-analysis` / `next-batch`
sequence between Dynamo invocations:

```cmd
python tools\campaign_planner.py validate examples\stage_a_campaign.json
python tools\campaign_planner.py init examples\stage_a_campaign.json campaign\campaign_state.json
python tools\stage_a_cycle.py advance examples\stage_a_campaign.json campaign\campaign_state.json
:: run Dynamo using the printed NEXT_BATCH
python tools\stage_a_cycle.py advance examples\stage_a_campaign.json campaign\campaign_state.json
:: run Dynamo again, repeat until CAMPAIGN_COMPLETE or a stop action
```

One-time validation and initialization (`validate`, `init`) are unchanged and
still required first: `advance` refuses to run against a campaign/state pair
that has never been initialized, and prints the exact `init` command to run
instead of silently creating or resetting state.

### Paths

Every path defaults deterministically from the state file's own directory,
never from the process's current directory:

| Default | Override |
|---|---|
| `<state-dir>\next_batch.json` | `--batch PATH` |
| `<state-dir>\manifests\` | `--manifest-root PATH` |
| `<state-dir>\analysis\` | `--analysis-root PATH` |
| `<state-dir>\raw\` | `--artifact-root PATH` |
| `<state-dir>\batches\` | (not overridable) |

`--manifest-root` is where the wrapper recursively looks for
`revit_run_manifest.json` files (see `REVIT_BATCH_EXECUTOR.md`; if Dynamo's
thin executor writes elsewhere, pass its manifest root here or point the
executor at this directory). `--artifact-root` is reported for visibility
only - the wrapper never reads or writes raw TIFFs/JSON itself; if Dynamo's
`execute_batch(..., artifact_root=...)` is configured with a different root,
pass the same value here so `--json`/`--dry-run` output reflects where
artifacts actually land. Analysis is written next to its manifest (matching
`analyze_json()`'s own contract, which always writes a sibling
`<manifest>.analyzed.json`) and additionally mirrored into
`<analysis-root>\<run_id>\revit_run_manifest.analyzed.json` for a stable,
predictable browsing location; the copy next to the manifest is authoritative
and is never overwritten with different content, and no raw report or TIFF is
ever written or modified by this script.

### What one `advance` call does

1. **Validate**: loads and validates the campaign and state, confirms the
   campaign/state binding (a changed campaign is `CONFIGURATION_DRIFT`,
   reported and left exactly as `campaign_planner.py`'s own CLI already
   handles it - existing jobs superseded, nothing silently reused), resolves
   every path, and acquires a bounded lock file
   (`<state-dir>\.stage_a_cycle.lock`) so two `advance` invocations against
   the same state cannot interleave writes. A lock older than six hours, or
   owned by a local process that is confirmed no longer running, is treated
   as abandoned and reclaimed automatically - a crashed invocation never
   permanently blocks recovery.
2. **Discover manifests**: recursively finds every `revit_run_manifest.json`
   under `--manifest-root`, validates its schema, and classifies it -
   unrelated-campaign and validation-only manifests are counted and skipped;
   everything else is handed to the planner's own `ingest_manifests()`
   unchanged, so unknown job IDs, batch/fingerprint drift, and duplicate or
   conflicting run evidence are exactly the conflicts `campaign_planner.py`
   already defines (`status` will show them under `conflicts`). Discovery
   orders manifests by their own recorded `started_at`/`run_id`, never by
   file modification time.
3. **Ingest execution**: exactly `campaign_planner.py`'s ingestion logic,
   with a full atomic state write after this step (and after every step
   below) so the same command can resume from wherever a prior invocation
   stopped.
4. **Analyze outstanding evidence**: for every run backing a job that is
   `EXECUTED` but not yet `ANALYZED`, calls `analyze_stage_a_probe.analyze_json()`
   on that run's manifest exactly once (an existing `*.analyzed.json` is
   reused, never recomputed or overwritten). An unexpected analyzer failure
   (a malformed/unknown probe report, not an ordinary FAIL/INCONCLUSIVE
   result - those are normal analyzer output) is recorded to
   `<analysis-root>\<run_id>\analyzer_error.json`, stops the cycle with a
   nonzero exit, and generates no batch; state up to that point is already
   persisted and safe to resume from by rerunning the same command once the
   underlying report is fixed.
5. **Ingest normalized acceptance**: exactly `campaign_planner.py`'s
   `ingest_analysis()`. `PASS`/`FAIL`/`INCONCLUSIVE`, reason codes,
   manual-review seeding, and conditional-fallback closures (including
   `attached_AS -> detached_AS`) are entirely the planner's own decision;
   the wrapper never reinterprets them.
6. **Decide the next action** and print/return exactly one of:

   | Action | Meaning | Exit code |
   |---|---|---|
   | `RUN_DYNAMO` | a batch is ready (existing pending batch, or a freshly generated one) - run it, then call `advance` again | 0 |
   | `CAMPAIGN_COMPLETE` | every job reached a terminal planner state | 0 |
   | `MANUAL_REVIEW_REQUIRED` | a gate-scoped or campaign-authored manual review is pending | 3 |
   | `DIAGNOSTIC_AUTHORIZATION_REQUIRED` | one or more jobs are `FAILED` with no further automatic path; authorize a diagnostic yourself via `campaign_planner.py diagnostic ... request` if appropriate | 4 |
   | `BLOCKED` | jobs are blocked with no eligible work and no rejected review or clean failure to point at | 5 |
   | `CONFLICT` | the planner recorded a conflict (duplicate/mismatched evidence, inconsistent pending batch) - investigate under `state["conflicts"]` before continuing | 6 |
   | `ERROR` | state not initialized, campaign drift, analyzer failure, lock contention, or another recoverable stop - see `errors` | 1 |

   When a batch is already pending (jobs still `BATCHED`), `advance` never
   regenerates it: it verifies `next_batch.json` still matches the
   immutable snapshot in `<state-dir>\batches\<batch_id>.json` and republishes
   only if that specific file is missing (e.g. after a crash between writing
   the snapshot and publishing it) - if it exists and disagrees, that is
   `NEXT_BATCH_MISMATCH`, an `ERROR` that must be investigated rather than
   guessed past. Only once no batch is pending does it ask the planner to
   generate the next one, write the immutable batch snapshot, and then
   publish `next_batch.json`, in that order, so an interruption at any point
   resumes without duplicating a batch identity.

Console output stays concise, e.g.:

```
ACTION: RUN_DYNAMO
CAMPAIGN: stage-a-initial
BATCH: stage-a-initial.batch.0002
JOBS: 1
NEXT_BATCH: C:\...\campaign\next_batch.json
VALIDATION_MANIFESTS_IGNORED: 1
RUNS_INGESTED: 1
ANALYSES_CREATED: 1
ANALYSES_INGESTED: 1
```

When the batch includes a materialized conditional fallback, an extra
annotation line follows (informational only - the authoritative data is
`fallback_context` in `--json`):

```
NEXT_JOB: detached_AS
REASON: attached_AS blocked by view-template control
```

Pass `--json` for a stable, script-friendly summary instead (never parse the
console text): `action`, `campaign_id`, `state_path`, `batch_id`,
`batch_path`, `job_ids`, `manifests_discovered`, `manifests_ingested`,
`validation_manifests_ignored`, `analyses_created`, `analyses_ingested`,
`conflicts`, `warnings`, `errors`, plus the resolved `manifest_root`,
`analysis_root`, `artifact_root`, and `batches_root` for visibility.

### `--dry-run`

Validates configuration and paths, discovers and classifies manifests, and
reports what ingestion/analysis/batch generation *would* do - without
writing state, analysis output, or batch files, and without invoking the
analyzer on evidence that has not already been analyzed (an existing
`*.analyzed.json` is read, since that is a plain file read rather than an
analysis invocation; outstanding evidence with no analyzed file yet is
reported as pending, not analyzed). The result always carries
`"dry_run": true`.

### Recovering after interruption

Every phase persists before the next one starts, so rerunning the exact same
`advance` command after any interruption (killed process, machine restart,
`Ctrl+C`) always resumes correctly: it can never duplicate ingested run/
analysis history, never regress a job's status, never lose evidence already
on disk, and never mint a second batch identity for work that was already
committed to a batch. If the interruption happened mid-lock (the process
died holding `.stage_a_cycle.lock`), the next invocation reclaims the lock
automatically once it goes stale rather than blocking forever.

### Investigating stop actions

- **`CONFLICT`**: inspect `state["conflicts"]` (or the `conflicts` field of
  `--json`) - each entry names the conflict code (`RUN_CONFLICT`,
  `ANALYSIS_CONFLICT`, `UNKNOWN_JOB`, ...) and the existing vs. incoming
  values. These are the same conflicts `campaign_planner.py ingest-runs`/
  `ingest-analysis` would report; nothing here is wrapper-specific. Resolve
  by correcting or removing the offending evidence file, then rerun.
- **`BLOCKED` / `DIAGNOSTIC_AUTHORIZATION_REQUIRED`**: run
  `python tools\campaign_planner.py status campaign.json campaign_state.json`
  for the full picture, then use the documented diagnostic/manual-review
  commands below.
- **`ERROR`**: read the `errors` list - `STATE_NOT_INITIALIZED` names the
  exact `init` command to run; `CONFIGURATION_DRIFT` means the campaign
  document changed after `init` (see "Recovering an existing failed Stage 1
  state" above); `ANALYZER_INVOCATION_FAILED` names the run and manifest to
  inspect (and leaves an `analyzer_error.json` record next to it);
  `CYCLE_LOCK_HELD` names the pid/host that owns the lock.

### Manual review and diagnostic authorization stay explicit

`advance` never records a manual review outcome and never authorizes a
diagnostic - both remain deliberate operator actions through the existing
commands:

```cmd
python tools\campaign_planner.py manual-review campaign.json campaign_state.json JOB_ID ACCEPTED operator-name
python tools\campaign_planner.py diagnostic campaign.json campaign_state.json request JOB_ID --rule targeted_asf
```

After either, call `advance` again to pick up the resulting eligible work.

### Validation-only Dynamo runs

Running Dynamo's thin executor with `IN[2] = True` (validation-only, see
`REVIT_BATCH_EXECUTOR.md`) still writes a `revit_run_manifest.json`, but with
`execution_status: "validation_only"` and every job recorded `validated_only`
- no probe ran and no TIFF was produced. `advance` recognizes and counts
these (`VALIDATION_MANIFESTS_IGNORED`) but never ingests them for campaign
progression: a validation-only run proves the batch is well-formed, not that
any evidence was produced, so it must never silently advance a job's status.

### Debugging with the individual commands

`stage_a_cycle.py` composes `campaign_planner.py` and
`analyze_stage_a_probe.py` without replacing them - both remain fully usable
on their own (e.g. to re-run just the analyzer on one report, or to inspect
`status` without triggering discovery/analysis/batch generation). Every
Dynamo invocation still writes its own uniquely-identified
`revit_run_manifest.json` under a fresh `<run_id>` directory (see
"Resolution and resumption" in `REVIT_BATCH_EXECUTOR.md`) - this is what lets
`advance` discover and process partial batches spanning multiple Dynamo
invocations correctly.

## Initial Stage A configuration

`examples/stage_a_campaign.json` is the **core host-element color-ID feasibility campaign**. It answers exactly one question: can Stage A reliably assign unique colors to visible host Revit elements and export a raster from which those elements can be recovered, across elevation / floor plan (active crop) / floor plan (inactive crop) / RCP / section / callout? It contains three stages:

1. **`01_mutation_recipe_confirmation`** - elevation reference view only. Confirms a viable minimum-mutation recipe (`attached_AS`, falling back to `detached_AS` only when a view template specifically blocks `attached_AS` - see "Conditional fallbacks" above) and, in the same job, produces the reviewable elevation host color-ID raster. The recipe-confirmation raster *is* the elevation validation artifact; it is never re-rendered a second time solely to be reviewed.
2. **`02_alignment_resolution_confirmation`** - confirms the chosen export resolution/alignment policy (150 DPI, `mode=all`) is technically usable: twice at elevation (determinism), once per remaining core view (each view's own bounds/geometry resolution is a distinct technical case). No repeated multi-DPI/fixed-width experimentation runs here by default.
3. **`03_cross_view_host_raster_matrix`** - applies the *single* recipe selected at Stage 1 to each remaining core view (with the same per-view template-block fallback safety net, via that view's own `<view>_mutation_closure`), at the single confirmed resolution policy, producing one reviewable host color-ID raster per view. No full mutation-variant factorial runs here by default.

Every core-view job in this campaign carries `manual_review_required` with a concrete review question (see "Making the manual review concrete" below), and the campaign's optional top-level `host_color_id_feasibility.core_views` field maps each of the six view keys to the `conditional_fallbacks` closure that resolves its recipe - see `campaign_planner.host_color_id_feasibility()` below for how that becomes the aggregate PASS/FAIL/MIXED/INCONCLUSIVE conclusion. Replace placeholder document/view identities before use. Drafting views are rejected from the alignment matrix.

External-source capability testing (RVT link/DWG), model-linework rendering diagnostics, the full mutation-variant factorial, and resolution-sensitivity experiments all still exist - they moved to a **separate, non-blocking** `examples/stage_a_followup_campaign.json`, with its own `campaign_id`, its own `campaign_state.json`, and no dependency of any kind on the core campaign. The core campaign can reach `CAMPAIGN_COMPLETE` and an explicit `HOST_COLOR_ID_FEASIBILITY_*` conclusion whatever state that follow-up campaign is in, including never having been run at all.

All scheduling and pass/fail decisions are deterministic. `next_recommendation` is a bounded machine-readable payload suitable for a future advisory LLM, but no LLM output is ingested as a state transition or gate.

## Aggregate host-element color-ID feasibility

`campaign_planner.host_color_id_feasibility(campaign, state)` (also surfaced as `host_color_id_feasibility` in `status`'s JSON output, and via the standalone `feasibility` CLI command) derives the campaign's core conclusion. It is purely a read of already-computed state - it never re-derives acceptance and never adds a new gate:

- For each `view_key -> {"closure_id": ...}` entry in `campaign["host_color_id_feasibility"]["core_views"]`, it resolves that closure's `candidate_job_id` and reads that job's current status.
- A view is `PASS` only once its candidate job reached `PASSED` - which itself already required every automated technical gate (mutation attestation, rollback, state restoration, TIFF dimensions, palette fidelity) *and* an ACCEPTED manual review (see "Gate-scoped manual review" above); `FAIL` once the candidate job reached `FAILED`; otherwise `NOT_TESTED` (not yet concluded - never guessed as a pass or a fail).
- The overall `conclusion` is `HOST_COLOR_ID_FEASIBILITY_PASS` only when every configured core view is `PASS`; `HOST_COLOR_ID_FEASIBILITY_INCONCLUSIVE` while any core view is still `NOT_TESTED`; otherwise `HOST_COLOR_ID_FEASIBILITY_FAIL` when every concluded view failed, or `HOST_COLOR_ID_FEASIBILITY_MIXED` when both `PASS` and `FAIL` views are present.

`tools/stage_a_cycle.py`'s `advance` prints this conclusion (`HOST_COLOR_ID_FEASIBILITY: ...`) the moment the core campaign reaches `CAMPAIGN_COMPLETE`, and includes each `job_id`/view identity/artifact path/review question for every pending review in its `MANUAL_REVIEW_REQUIRED` output - see "Making the manual review concrete" below.

## Making the manual review concrete

Every core-view job's `manual_review_reason` is the same concrete review question (not a generic placeholder):

> Does this raster accurately represent the visible host-model elements and their boundaries/occlusion for this view, with annotations/non-model graphics excluded as intended and without obvious color contamination that would prevent element-ID recovery?

The operator should inspect the raster for: host elements represented by assigned unique colors; boundaries credible relative to the Revit view; foreground/background occlusion behaving plausibly; annotations and non-model graphics excluded as intended; no obvious gradient/shadow/halftone/anti-aliasing contamination that materially breaks ID recovery; no major unexpected holes or phantom regions; and no gross alignment/crop discrepancy. The operator is never asked to prove internal Revit API semantics not visible in the raster - that is the generic `MANUAL_SEMANTIC_REVIEW_REQUIRED`/`SEMANTIC_PRESERVATION_NOT_AUTOMATICALLY_VERIFIED` distinction described in "Gate-scoped manual review" above, which never becomes a second blocking gate once this concrete question has been answered `ACCEPTED`.

`tools/stage_a_cycle.py advance` never asks an operator to review an artifact that does not exist: a job whose manual review has no reviewable raster on record surfaces as `MANUAL_REVIEW_EVIDENCE_MISSING` (a `BLOCKED` action), never as an actionable `MANUAL_REVIEW_REQUIRED` entry - see "Manual review requires a reviewable artifact" above.
