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

For `stage_a_transaction_group_export`, JSON settings must contain one or both of
`element_ids` (integer Revit element IDs) and `element_unique_ids` (strings).
The registry resolves these through the current document and passes actual Revit
elements to the adapter. This resolution also occurs in validation-only mode;
JSON cannot supply `raw_elements` directly.

## Resolution and resumption

`UniqueId` is preferred and resolved with `Document.GetElement`. Name-only
resolution must have exactly one match. Every supplied name, type, crop, and
template assertion is checked even after identity resolution. Document title is
required; configured Revit version and path are assertions.

Every invocation receives its own `<manifest-root>/<run-id>/revit_run_manifest.json`,
written through a flushed temporary file and atomic replacement. Resume reads
only manifests with the same campaign, batch, and exact resolved document
identity (title, path, and Revit version). A differing prior document stops
resumption rather than reusing its evidence. A completed job is skipped only
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
