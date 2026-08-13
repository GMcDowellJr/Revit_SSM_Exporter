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
- `BATCHED -> EXECUTED | ELIGIBLE | SUPERSEDED`
- `EXECUTED -> ANALYZED | FAILED | INCONCLUSIVE | SUPERSEDED`
- `ANALYZED -> PASSED | FAILED | INCONCLUSIVE | SUPERSEDED`
- `FAILED | INCONCLUSIVE -> NEEDS_DIAGNOSTIC | SUPERSEDED`
- `BLOCKED -> ELIGIBLE | NEEDS_DIAGNOSTIC | SKIPPED | SUPERSEDED`
- `NEEDS_DIAGNOSTIC -> ELIGIBLE | BLOCKED | SUPERSEDED`; all accepted, skipped, or superseded evidence can only be superseded.

Every transition has a reason code and deduplicated history event. A normalized `PASS` is the only default successful dependency. `FAIL`/`INCONCLUSIVE`, restoration/rollback failures, missing records, and conflicts never become passes. Dependencies are job-specific, so one view cannot block unrelated matrix cells. Manual review is a separate record and can remain pending after automated `PASSED`.

Batch selection sorts eligible jobs by configured stage and job order, applies `execution_defaults.batch_size`, and moves only selected jobs to `BATCHED`. Already batched/executed jobs are not selected. With no eligible work, the recommendation distinguishes complete, Revit execution, analyzer, failure, manual review, and invalid/conflicting state.

## Diagnostics, resets, and amendments

Authorize only a configured expansion, for example the targeted A/S/F rule:

```bash
python tools/campaign_planner.py diagnostic campaign.json campaign_state.json request JOB_ID --rule targeted_asf
python tools/campaign_planner.py diagnostic campaign.json campaign_state.json clear JOB_ID
python tools/campaign_planner.py diagnostic campaign.json campaign_state.json reset JOB_ID
python tools/campaign_planner.py manual-review campaign.json campaign_state.json JOB_ID ACCEPTED operator-name
```

A request materializes only the configured variants for that cell, never the full factorial. A reset preserves old provenance as `SUPERSEDED`; it does not erase evidence. To amend execution-affecting settings, create a new campaign version/ID and initialize new state. Notes may change without drift. Copy/reference prior raw evidence through new normalized records only when the campaign explicitly models that evidence; never edit old state fingerprints.

## Initial Stage A configuration

`examples/stage_a_campaign.json` encodes, rather than hard-codes: elevation attached-AS closure; repeated 150-DPI elevation alignment; Hidden Line/white-fill/black-line elevation calibration at 75/150/300 DPI; fixed-1600 plus repeated 150-DPI alignment for active/inactive floor, RCP, section, and model callout; the three supported reduced mutations; gated linework and separate manual review; and tagged RVT-link/DWG capability checks before their 150-DPI runs. Replace placeholder document/view identities before use. Drafting views are rejected from the alignment matrix.

All scheduling and pass/fail decisions are deterministic. `next_recommendation` is a bounded machine-readable payload suitable for a future advisory LLM, but no LLM output is ingested as a state transition or gate.
