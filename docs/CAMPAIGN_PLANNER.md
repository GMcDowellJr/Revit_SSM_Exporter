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

## Initial Stage A configuration

`examples/stage_a_campaign.json` encodes, rather than hard-codes: an elevation `attached_AS`/`detached_AS` mutation closure (see "Conditional fallbacks" above); repeated 150-DPI elevation alignment, gated on that closure resolving rather than on `attached_AS` specifically; Hidden Line/white-fill/black-line elevation calibration at 75/150/300 DPI; fixed-1600 plus repeated 150-DPI alignment for active/inactive floor, RCP, section, and model callout, gated on the elevation linework stage completing; the three supported reduced mutations (`attached_element_overrides_only`, `attached_AS`, `detached_ASF`); gated linework and separate manual review; and tagged RVT-link/DWG capability checks, eligible once the independent linework/alignment matrix has concluded (pass, fail, inconclusive, or blocked) rather than only on its success, before their 150-DPI runs. Replace placeholder document/view identities before use. Drafting views are rejected from the alignment matrix.

All scheduling and pass/fail decisions are deterministic. `next_recommendation` is a bounded machine-readable payload suitable for a future advisory LLM, but no LLM output is ingested as a state transition or gate.
