# Thin runner Stage A campaign trace map

This trace uses the classifications in `thin_runner_stage_a_campaign_code_map.md`. “Observed”
means a static call/data path exists; “gap” means orchestration behavior must not
be claimed as present. The corrected authoritative production boundary is
`vop_interwoven/`.

## A. Revit execution flow

### Common shape (five argument-taking probes)

```text
Dynamo IN (positional, probe-specific)
  -> top-level wrapper parses defaults
  -> run(...) / run_probe(...)
  -> unwrap Dynamo object and acquire Document
  -> probe-local rejection/validation
  -> probe-local resolution run(s) and variant/mode selection
  -> force-close Dynamo TransactionManager transaction
  -> probe/per-variant TransactionGroup
       -> child Transaction(s): temporary graphics/crop/markers
       -> Commit child transaction
       -> export TIFF while group remains open
       -> finally: TransactionGroup.RollBack
       -> capture and compare restored state
  -> build/write probe-specific raw JSON (and mutation CSV)
  -> Dynamo OUT (full report or reduced summary)
```

The five are transaction-group export (`run_probe`), graphics semantics (`run`),
external sources (`run`), model linework (`run`), and minimum mutations (`run`).
Image alignment follows the same Revit shape but `_run()` reads `IN` directly.

### Probe-specific input, identity, selection, and artifacts

| Probe | Input parsing and view identity | Selection/resolution/repetition | Transaction/restoration | Artifact and OUT path |
|---|---|---|---|---|
| transaction group | `IN[0]` view, `IN[1]` elements; `_validate_inputs` enforces DB View/current document and elements/current document. Identity is ID/name only. | `IN[3]` chooses normal or injected failure. Fixed requested width 1600 with backoff. Required second run is advisory, not internally repeated. | `run_probe` owns group; records start, child commit, export-open-state, rollback and before/after equality. | `<view>_<id>.<mode>.transaction_group_probe.{tiff,json}`; `OUT` is a reduced report. |
| graphics semantics | Wrapper defaults `IN[0:8]`; `_unwrap`, `_validate_view`; mode validation delegates to production `vop_interwoven.revit.view_basis`. ID/name recorded, no lookup. | One exact variant or `all`; `paper_space_dpi`, `fixed_pixel_width`, or `both`; no repetitions. | `_run_variant` owns one group per variant/resolution; records snapshots/diffs/rollback exceptions. | `graphics_semantics_probe/<view>_<id>.graphics_semantics.<resolution>.<variant>.tiff` plus combined JSON and path-bearing report in `OUT`. |
| external sources | View plus optional link/DWG instances; `_discover_assignments` discovers then filters. ID/name and source element IDs, no view lookup. | Always all fixed `VARIANTS`; resolution policy can produce one/two runs; no repetition selector. | `_run_variant` owns one group each; reports rollback and state. | `external_sources_probe/...<resolution>.<variant>.external_sources.tiff`, combined JSON, full report `OUT`. |
| model linework | View, optional focused elements; `_reject_reason`; ID/name. | `_select_modes`: `all`, one, or comma list; resolution run(s). Each mode exports twice internally for repeatability; reference exports also repeated. | Independent group for reference and each mode; rollback/state fields per result. | `model_linework_probe` TIFFs and combined JSON; full report `OUT`. External diff TIFF paths do not exist yet in raw report. |
| image alignment | `_run` directly reads `IN`; `_reject_reason`; view ID/name/type. | `original`, `model_bounds`, `canvas_bounds`, or `all`; resolution run(s); two sequential exports per case. | One group spans requested cases; crop/markers restored by rollback; reports crop-state differences and marker existence. | Alignment TIFFs and per-case `.alignment.json` files containing aggregate data; full result `OUT`. |
| minimum mutations | Guarded wrapper; `run` unwraps `InternalElement`; document presence validates view. ID/name. | `_select_variants` supports comma/semicolon names or all; Stage 1 and Stage 2 generators; resolution run(s); no repetitions. | `_run_variant` owns group; per-mutation attestation, visible-set diagnostics, rollback and state. | Root output TIFFs, `<resolution>.minimum_id_mutations.json`, `.mutation_matrix.csv`; full report `OUT`. |

### Resolution and view-bound trace

```text
policy/DPI/fixed width/cap
  -> local _resolution_runs/resolution_runs
  -> bounds from active crop OR production vop_interwoven resolve_view_bounds
  -> paper-space/fixed-width calculation
  -> width-only Revit ImageExportOptions pixel-size backoff
  -> actual TIFF dimensions
  -> finalized resolution report
```

Observed exception: minimum mutations delegates pure math to
`tests/dynamo/resolution_contract.py`; the others duplicate calculations.
Transaction-group export does not implement the shared policy. Production view
bounds resolve through `vop_interwoven.revit.view_basis`, while probe resolution
math remains duplicated except for the minimum-mutations shared test contract.

### Errors and partial completion

Each probe catches/report errors differently. Per-variant probes can append a
failed or skipped case and continue; resolution failures may be diagnostics and
continue to another run. Top-level wrappers generally turn fatal exceptions into
`OUT.conclusion=FAIL`; minimum mutations has no top-level catch inside its `IN`
guard. There is no shared job status, attempt ID, partial-completion manifest, or
idempotent resume contract.

## B. External analysis flow

```text
raw JSON path or directory
  -> tools/analyze_stage_a_probe.py:main
  -> collect (recursive *.json, excluding *.analyzed.json)
  -> analyze_json loads JSON and chooses family
       -> resolve referenced TIFF paths relative to JSON
       -> Pillow RGB load + NumPy calculations
       -> family-specific analysis/classification/recommendation
  -> write <raw-stem>.analyzed.json
       + optional model-linework diff TIFF(s)
  -> CLI exit nonzero only for raised analyzer errors
```

| Dispatch | Calculations | Acceptance/evidence output |
|---|---|---|
| minimum ID mutations | Normalize images, mutation attestation cleanliness, palette/pixel differences, comparable dimensions | `recommend_minimum_mutations`; guards recommendations when real metrics/attestation are absent. |
| graphics semantics | Exact/near palette, dark/near-white/background and assigned-color counts | `recommend_graphics`; per-variant analyzed status. |
| model linework | foreground/dark/gray/unexpected colors, components, repeatability, pairwise diff TIFF | `classify_mode`, `rank_modes`. |
| alignment | dimensions, marker positions, affine fit residual, bounds placement | `alignment_evidence_status`, placement report. |
| external sources | **Gap: unrecognized and skipped.** | Probe-local raw classification only; no authoritative external TIFF dispatch. |
| transaction group | **Gap: unrecognized and skipped.** | Optional in-Revit Pillow inspection may exist, but no common external acceptance. |

The analyzer writes analyzed JSON, not analyzed CSV. PASS/FAIL/INCONCLUSIVE is
not normalized at a common top level across families. Its “SKIP unrecognized”
return is not a process failure, so a future planner cannot use exit code alone
as acceptance evidence.

## C. Proposed orchestration boundary (existing symbols vs gaps)

This section maps candidates; it does not propose an implementation patch or
claim the orchestration already exists.

```text
campaign.json                         [GAP: no schema/reader]
  -> next_batch.json                  [GAP: no planner/writer]
  -> Dynamo thin executor             [GAP: no dispatcher/job adapter]
       candidates: probe run/run_probe; alignment has no suitable entry
  -> revit_run_manifest.json          [GAP: no shared manifest builder]
       candidates: existing probe raw reports/path inventories
  -> external analyzer
       existing: analyze_json / family analyzers (partial coverage)
  -> campaign_state.json              [GAP: no state/acceptance reducer]
  -> next eligible batch              [GAP: no dependency/eligibility engine]
```

### Field-by-field boundary trace

| Required datum | Current source/sink | Deterministic orchestration status |
|---|---|---|
| View identity | Probe inputs accept live object; reports store integer ID/name (alignment adds type) | **Gap:** no document identity, UniqueId contract, lookup function, missing/stale/ambiguous handling. |
| Probe identity | Most reports have `probe.name/version`; alignment lacks the normal `probe` object | Partial/inconsistent. |
| Variant/case | `variant`, `mode`, injected flag, alignment export key | Partial; external sources cannot select one; no common job case ID. |
| Resolution | Policy fields and reports; duplicated owners | Partial; no shared serialized case schema/version. |
| Repetitions | Hard-coded pairs in alignment/linework; advisory second transaction run | **Gap:** no general count/index/attempt ID. |
| Output directory | Positional `IN`; probes create different subdirectories | Partial; no run-root convention or collision policy. |
| Artifact paths | Probe-specific `paths`, `output_files`, nested `export/images` | Partial/inconsistent; no portable manifest contract. |
| Mutation attestation | Minimum-mutations `mutations` statuses and assignment/visible-set evidence | Exists only for that family. |
| Rollback | Probe-specific group/state fields | Present but differently named/shaped. |
| Errors/partial completion | `exceptions`, diagnostics, skipped cases, fatal `OUT` | Present but not normalized; no resume semantics. |
| Analyzer selection | `analyze_json` string/shape dispatch | Partial; two probe families unsupported and no explicit analyzer ID/version. |
| Acceptance result | Family recommendations/classifiers and probe conclusions | Fragmented; no external common reducer. |

### Boundary conclusions

* A thin executor can statically reuse five callable entries without copying
  their internal probe logic, but wrapper import effects and image alignment's
  global `IN` coupling are unresolved callable-boundary problems.
* Transaction ownership must remain inside current probes unless a future
  source-level design deliberately changes the contract; nesting an outer group
  is not established safe by static evidence.
* The Dynamo side must not infer image-dependent downstream acceptance. Existing
  `requires_external_analysis`/pending markers support that separation for four
  families, but schemas and coverage are incomplete.
* An external planner cannot yet deterministically produce a next batch because
  campaign stage/dependency rules, stable identities, normalized attempts,
  analyzer completion, acceptance reduction, and state transition contracts are
  all absent.

## Minimal source request for the next source-level scope

Ranked to cross the smallest set of missing ownership boundaries (maximum five):

1. **`vop_interwoven/entry_dynamo.py`** — executor import/bootstrap and current
   Document/View access boundary.
2. **`vop_interwoven/revit/view_basis.py`** — resolved
   View contract, identity, eligibility, and resolution source of truth.
3. **`tests/dynamo/probe_stage_a_image_alignment.py`** — least-callable probe and
   multi-case/repetition boundary.
4. **`tools/analyze_stage_a_probe.py`** — analyzer dispatch and external
   acceptance evidence boundary.
5. **Any existing authoritative campaign/schema/state/planner module; if none,
   explicitly provide “NONE”** — distinguishes absent contracts from omitted
   checkout content.
