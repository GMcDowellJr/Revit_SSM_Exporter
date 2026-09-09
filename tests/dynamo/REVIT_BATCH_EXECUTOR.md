# Revit batch executor (PR 2)

The executor consumes only an externally prepared `next_batch.json`. It validates
execution evidence; it does **not** inspect TIFFs, decide acceptance, evaluate
dependencies, or gate later campaign stages. See `next_batch.schema.json` and
`next_batch.example.json` for the version 1.0 contract.

## Dynamo entry

Paste or import `revit_batch_dynamo.py`. The pasted script discovers the checkout
from the batch path, current directory, or `REVIT_SSM_EXPORTER_ROOT` /
`VOP_REPO_ROOT` before importing its modules. Supply `IN[0]` as the batch path,
optional `IN[1]` as the manifest root, and optional `IN[2] = true` for
validation only. Validation-only still writes a manifest, but invokes no probe
and creates no TIFF. `OUT` contains identities, executed/failed/deferred job IDs,
manifest path, and whether another invocation is required.

The fixed registry in `revit_probe_registry.py` contains the six PR 1 adapters.
Each adapter calls `run_probe(raw_view=..., output_dir=..., **settings)`; there is
no `exec`, configured import, transaction group, analyzer, or planner call.
`stage_a_minimum_id_mutations` has its own explicit adapter (rather than the
generic passthrough): campaign `dpi` maps to `run_probe(target_dpi=...)`,
the campaign job's `variant` maps to `run_probe(selection=...)`, and
`comparison_reference` (analysis-only provenance) is stripped and never
forwarded. The same function backs `validate_settings`, so validation-only
mode rejects unknown settings, unsupported aliases, a conflicting
`dpi`/`target_dpi` pair, and an unknown variant/selection - without starting
a transaction or exporting a TIFF.

### `output_directory` resolution

A job's `output_directory` is resolved to a canonical absolute path before
it ever reaches an adapter or `validate_settings` - independent of whatever
directory the Revit/Dynamo process happens to be running in. An already
absolute path is used unchanged; a relative path resolves against an
explicit `artifact_root` (an `execute_batch` parameter) when one is
configured, and otherwise against the directory containing `next_batch.json`
itself (a location that is always known, unlike the process cwd). The
manifest records both the job's configured `output_directory` (its relative
identity, exactly as authored) and `output_directory_resolved` (the
canonical absolute path actually used for dispatch and artifacts).

For `stage_a_transaction_group_export`, JSON settings must contain one or both of
`element_ids` (integer Revit element IDs) and `element_unique_ids` (strings).
The registry resolves these through the current document and passes actual Revit
elements to the adapter. This resolution also occurs in validation-only mode;
JSON cannot supply `raw_elements` directly.

`stage_a_external_sources` similarly cannot discover its RVT-link/DWG-import
targets on its own from a view - `run_probe()`'s `raw_links`/`raw_dwgs`
parameters require real elements. JSON settings may supply
`link_instance_unique_ids`/`link_instance_ids` and
`dwg_import_unique_ids`/`dwg_import_ids`; the registry resolves these through
the current document the same way as `stage_a_transaction_group_export`
(`UniqueId` preferred), also in validation-only mode. Omitting both is legal
JSON but not useful: every variant will report no discovered candidates for
its required source type and the campaign result reads `INCONCLUSIVE`/`FAIL`,
not as a clear "missing input" error - see `PROBE_STAGE_A_EXTERNAL_SOURCES.md`.

## Resolution and resumption

`UniqueId` is preferred and resolved with `Document.GetElement`. Name-only
resolution must have exactly one match. Every supplied name, type, crop, and
template assertion is checked even after identity resolution. Document title is
required; configured Revit version and path are assertions.

Every invocation receives its own `<manifest-root>/<run-id>/revit_run_manifest.json`
(`run_id` a fresh UUID4 hex per call unless one is explicitly supplied), written
through a flushed temporary file and atomic replacement. **This is intentional
run-identity behavior, not an overwrite defect**: each Dynamo invocation is a
distinct, independently-auditable execution attempt, and its manifest must
never be silently replaced by a later invocation's manifest even when they
target the same batch (e.g. a retry, or a resumed run after a partial
`execution_limit`/`stopped_after_job_error`). Resume across invocations does
not rely on directory reuse - it walks `manifest_root` and matches on
campaign/batch/document identity plus each job's own configuration
fingerprint (see below), so distinct per-run-id manifest directories coexist
correctly and every one of them remains part of the permanent evidence trail
tying a job back to the exact Revit execution that produced it. Resume reads
only evidence-bearing manifests with the same campaign, batch, and exact
resolved document identity (title, path, and Revit version). Manifests without
completed jobs, including configuration failures, do not bind resume identity.
A differing document with completed evidence stops resumption rather than
reusing that evidence. A completed job is skipped only
when its deterministic SHA-256 configuration fingerprint matches; drift stops
the run. `allow_rerun` explicitly overrides the skip. Job order is preserved and
only the first `max_jobs_per_run` eligible jobs execute. Remaining jobs are
recorded as `execution_limit`; `stop` records later selected jobs as not attempted,
while `continue` runs independent listed jobs.

Malformed JSON and contract-validation failures also produce a
`configuration_failed` manifest. If identity fields could not be parsed, their
manifest values are `null`; the batch source and structured configuration error
still identify the failed request.

## Manual Revit acceptance procedure (still required)

1. Set validation-only and confirm schema, document, registry, and every view validate.
2. Run one harmless configured probe and inspect its raw artifacts and manifest.
3. Confirm the raw envelope reports rollback and state restoration.
4. Re-run with resume enabled and confirm the completed job is skipped.
5. Run two jobs with `max_jobs_per_run: 1`; confirm the second is deferred.
6. Use an invalid or duplicate name reference and confirm no substitute is selected.

These are manual acceptance checks, not claims of completed Revit validation.
