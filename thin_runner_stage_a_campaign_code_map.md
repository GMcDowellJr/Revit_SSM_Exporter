# Thin runner Stage A campaign code map

## Scope and evidence rules

This map is a static-navigation artifact for commit `b0cff4f`. It does not assert
Revit runtime behavior. The corrected repository authority is
`vop_interwoven/`: it is the current production implementation for this campaign.
That classification is established by the corrected request, corroborated by
repository documentation, production entry points, and direct imports from the
current probes.

Evidence levels used here are **observed** (definition/import/call/site exists),
**documented** (a repository document says so), and **UNKNOWN** (not established
by navigation evidence). Filename similarity is never classification evidence.

## 1. Current production implementation (`vop_interwoven/`)

| Path | Responsibility | Classification and evidence | Imports / known callers | Environment / transaction | Configuration / artifacts |
|---|---|---|---|---|---|
| `vop_interwoven/config.py` | Production `Config` and resolution/raster configuration | **Current production.** Corrected authority; imported by current probes and production entry points. | Called by `entry_dynamo`, pipeline helpers, and five Stage A probes. | Mixed external/Revit configuration; no transaction ownership. | Consumes constructor settings; returns configuration objects. |
| `vop_interwoven/entry_dynamo.py` | Production Dynamo entry functions and current-document/view access | **Current production.** Package entry point documented in `CLAUDE.md` and package README. | Imports `Config` and `process_document_views`; called by Dynamo helpers/thinrunner streaming. | Revit-dependent entry boundary; downstream pipeline owns processing rather than probe groups. | Dynamo arguments/config; production CSV/PNG/result objects, not Stage A probe TIFFs. |
| `vop_interwoven/pipeline.py` | Production view processing, raster initialization, collection/raster coordination | **Current production.** Imported by production entry/streaming and current probes (`init_view_raster`). | `entry_dynamo`, `streaming`, graphics/external-source probes. | Revit-dependent processing; no Stage A probe `TransactionGroup` ownership observed. | `Config`, Document/views; pipeline results/metrics. |
| `vop_interwoven/revit/view_basis.py` | View capability/mode validation, basis construction, crop/model bounds resolution | **Current production.** Direct imports from five probes and production modules. | Pipeline and Stage A probes. | Revit-dependent; no transaction ownership for read calculations; crop construction is called inside probe-owned groups. | View/Config/bounds inputs; basis and resolved-bound dictionaries. |
| `vop_interwoven/revit/collection.py` | Current view collection and HOST/LINK/DWG expansion | **Current production.** Pipeline and three current probes import it. | Pipeline, graphics/external/minimum probes. | Revit-dependent; collection does not own probe transaction groups. | View/config/diagnostics; element collections and source entries. |
| `vop_interwoven/revit/annotation.py` | Current annotation collection/extents | **Current production.** Pipeline and alignment probe imports. | Pipeline and image-alignment probe. | Revit-dependent; alignment caller owns temporary transaction group. | Document/view/basis/config; annotation extents/results. |
| `vop_interwoven/color_id_buffer.py` | Production color-ID assignment, palettes, OGS, source resolution and neutral filter helper | **Current production.** Direct dependency of four probes. | Graphics, external-sources, model-linework, minimum-mutations probes and production callers. | Revit-dependent mutation helpers; probe callers own transactions. | Elements/palette/view; assignment metadata and temporary graphics mutations. |
| `vop_interwoven/core/math_utils.py` | Shared bounds/math objects | **Current production.** Used by production and current probes. | Raster/basis/alignment/minimum probes. | External-safe math definitions; no transaction. | Objects/dictionaries only. |
| `vop_interwoven/core/raster.py` | `ViewRaster` and production raster state | **Current production.** Pipeline and minimum probe dependency. | Pipeline/collection/minimum probe. | Primarily calculation/state; no Stage A group ownership. | In-memory raster structures. |
| `vop_interwoven/core/diagnostics.py` | Production diagnostic recording | **Current production.** Pipeline/collection and external-source probe dependency. | Production processing and probe discovery. | No transaction ownership. | In-memory diagnostics serialized by callers. |

Other files under `vop_interwoven/` remain current production by the corrected
repository boundary, but are not expanded here unless static navigation connects
them to the Stage A campaign execution, evidence, or planning concerns.

## 2. Current Dynamo probes (`tests/dynamo/`)

These files are current probes because they are tracked under the requester-
designated probe directory, have matching operator documentation, and are
referenced by the current external analyzer or safe tests.

| Path | Responsibility | Classification / evidence | Imports and known callers | Revit / transactions | Configuration consumed | Artifacts produced |
|---|---|---|---|---|---|---|
| `tests/dynamo/probe_stage_a_transaction_group_export.py` | Proves export after committed child transaction and enclosing rollback, including injected failure | **Current probe.** Matching `PROBE_STAGE_A_TRANSACTION_GROUP.md`; Dynamo top-level calls `run_probe`. | Delayed Autodesk/RevitServices imports; intentionally no production import. Dynamo node wrapper is the caller. | Revit-dependent. `run_probe` owns one `TransactionGroup`, child `Transaction`, rollback, and state comparison. | `IN[0]` view, `IN[1]` elements, `IN[2]` output dir, `IN[3]` failure injection. | TIFF; JSON report; reduced `OUT` summary. |
| `tests/dynamo/probe_stage_a_graphics_semantics.py` | Compares graphics-neutralization variants | **Current probe.** Matching guide; analyzer dispatch recognizes `graphics_semantics`. | Delayed imports from current production `vop_interwoven.config`, `pipeline`, `revit.collection`, `revit.view_basis`, and `color_id_buffer`; Dynamo wrapper calls `run`. | Revit-dependent. `_run_variant` owns a group and child transactions; group rollback in `finally`; snapshots/diffs view state. | `IN[0:8]`: view, output, max elements, one variant or `all`, resolution policy, DPI, fixed width, cap. | Per-variant TIFF; combined raw JSON; path-bearing `OUT`. |
| `tests/dynamo/probe_stage_a_external_sources.py` | Exercises HOST/LINK/DWG discovery, assignment and fallback evidence | **Current probe.** Matching guide. Analyzer does **not** recognize this schema. | Delayed imports from current production `vop_interwoven` config, basis, pipeline, collection, and color buffer. Dynamo wrapper calls `run`. | Revit-dependent. `_run_variant` owns group/child transaction and rollback. | `IN[0:8]`: view, output, optional link/DWG filters, resolution controls. No variant selector. | Per-variant TIFF; combined external-sources JSON; `OUT`. |
| `tests/dynamo/probe_stage_a_model_linework.py` | Compares rendering modes and element-ID reference export | **Current probe.** Matching guide; analyzer dispatch recognizes `model_linework`. | Delayed imports from current production `vop_interwoven` config, basis, and color buffer. Dynamo wrapper calls `run`. | Revit-dependent. `_run_mode` and `_run_reference_element_id` own independent groups and rollback/state reports. | `IN[0:8]`: view, output, focused elements, comma-separated mode(s)/`all`, resolution controls. | TIFF pairs/reference TIFF, raw combined JSON; external analyzer adds diff TIFF and ranking. |
| `tests/dynamo/probe_stage_a_image_alignment.py` | Compares original/model/canvas bounds with temporary calibration markers | **Current probe.** Matching guide; analyzer recognizes alignment shape. | Delayed imports from current production `vop_interwoven` config, math bounds, view basis, and annotation. Top-level calls `_run()`. | Revit-dependent. `_run` owns a single group across selected modes/resolutions, child transactions, rollback and crop/marker restoration checks. | Direct reads of `IN[0:8]`: view, output, one mode/`all`, marker flag, resolution controls. | Repeated TIFFs; one alignment JSON per mode/resolution (each contains the aggregate result); `OUT`. |
| `tests/dynamo/probe_stage_a_minimum_id_mutations.py` | Generates Stage 1/2 mutation variants and mutation evidence matrix | **Current probe.** Imported by safe tests and recognized by analyzer. | Imports `tests.dynamo.resolution_contract` with fallback; delayed imports from current production `vop_interwoven` config/raster/collection/color buffer. Guarded Dynamo wrapper calls `run`. | Revit-dependent execution; pure generators/classifiers are external-safe. `_run_variant` owns a group and transactions; reports mutation attestation and rollback. | `IN[0:9]`: view, output, max host elements, comma/semicolon variant selection, resolution controls, optional repo root. | TIFF per selected variant; raw JSON; mutation CSV; `OUT`. |
| `tests/dynamo/resolution_contract.py` | Shared pure resolution/canvas calculations | **Current shared probe helper.** Directly imported by minimum-mutations probe and safe tests. | `json`, `math`; callers above and `test_stage_a_resolution_contract.py`. | External/import-safe; no transaction. | Bounds dimensions, view scale, DPI/fixed width/cap, accepted width. | Returned dictionaries only. |

### Dynamo wrapper and callable classification

| Probe | Wrapper behavior on ordinary import | Classification |
|---|---|---|
| minimum ID mutations | Executes only when `IN` exists | **Import-safe and callable** (pure helpers and `run`; Revit required only when executing probe paths). |
| graphics semantics, external sources, model linework | Unconditional `try` wrapper synthesizes inputs when `IN` is absent, calls `run`, catches failure, and assigns module `OUT` | **Partly callable with a wrapper**; import completes but has entry-wrapper side effects and a failure-valued `OUT`. |
| transaction-group export | Unconditionally reads `IN` inside nested catches and sets `OUT` | **Partly callable with a wrapper** through `run_probe`; not a clean library import. |
| image alignment | `_run()` directly reads global `IN`; top-level catches and writes `OUT`; `_run` has no argument boundary | **Executable only as a pasted Dynamo-node script** for current orchestration purposes (helpers are importable, but the probe entry is global-`IN` coupled). |

## 3. Current external analysis and planning tools (`tools/`)

| Path | Responsibility | Classification / evidence | Imports / callers | Environment / transaction | Configuration / artifacts |
|---|---|---|---|---|---|
| `tools/analyze_stage_a_probe.py` | External Pillow/NumPy TIFF analysis and JSON augmentation | **Current analyzer.** Named by probe guides and imported by safe tests. | stdlib, NumPy, Pillow; CLI `main`, tests call `analyze_json`. | External; no Revit/transaction. | Input paths (JSON or recursively scanned directory); dispatch inferred from `probe.name`/filename/shape. | `<stem>.analyzed.json`; linework diff TIFFs; console status. No analyzed CSV. |

No separate specialized Stage A analyzer exists under current `tools/`. The
specialized functions (`analyze_graphics_image`,
`analyze_minimum_id_mutations`, `analyze_linework_image`, and
`analyze_alignment_image`) are all inside the single dispatcher. It handles
minimum mutations, graphics semantics, model linework, and alignment; it skips
transaction-group and external-sources JSON as unrecognized.

There is **no current external campaign definition reader, campaign-state
writer, next-batch generator, or optional LLM interpreter** in `tools/`.

## 4. Shared non-Revit helpers and safe tests

| Path | Responsibility | Classification / evidence | Dependencies / side effects |
|---|---|---|---|
| `tests/dynamo/test_stage_a_resolution_contract.py` | Verifies resolution, cap, canvas, inactive-crop-source contract | **Current safe test**; imports only shared contract and pytest. No Revit or artifacts beyond pytest temporaries. |
| `tests/dynamo/test_stage_a_minimum_id_mutations.py` | Verifies pure variant generation, selection-related data, hashes, classification, serialization, import shims and production-signature assumptions | **Current safe test**; directly imports minimum-mutations symbols. Uses tmp paths; monkeypatches/import shims. |
| `tests/dynamo/test_analyze_stage_a_minimum_id_mutations.py` | Verifies external dispatcher normalization and recommendation guards | **Current safe test**; imports `tools.analyze_stage_a_probe.analyze_json`; creates fixture JSON/TIFF paths under pytest temp storage. |
| `tests/conftest.py`, `tests/__init__.py`, `tests/dynamo/__init__.py` | Test package/bootstrap support | **Current shared test infrastructure** by pytest/package discovery. |

Other safe root tests exercise `vop_interwoven`, but static navigation does not
show them as campaign/probe contracts; they are outside this pack's focused
Stage A probe surface.

## 5. Confirmed legacy or superseded implementations

| Path | Responsibility | Classification / evidence |
|---|---|---|
| `legacy/` | Historical implementation material | **Confirmed legacy** by repository `CLAUDE.md` directory description and name. No Stage A probe/analyzer import found. |
| `archive/`, including `archive/refactor1/` | Previous refactor material | **Confirmed archived/superseded** by repository `CLAUDE.md` and directory naming. No Stage A probe/analyzer import found. |
| `tests/archive/ssm_vop_v1/` | Archived test baseline/Dynamo graph | **Confirmed archived test artifact** by path and README/manifest role; no current Stage A caller found. |

These classifications are documentation/caller based, not similarity based.
“Unreferenced” here is limited to searches from `vop_interwoven/`,
current Stage A probe files, current Stage A safe tests, and current tools; it is
not a claim that no repository file ever mentions them.

## 6. Root-level dependencies and remaining ambiguity

| Path | Responsibility | Classification / navigation evidence |
|---|---|---|
| `tests/dynamo/resolution_contract.py` | Shared pure probe resolution contract | **Current non-production helper.** Imported by minimum-mutations probe and safe tests. |
| `tools/analyze_stage_a_probe.py` | External analyzer/dispatcher | **Current external tool.** Named by probe docs and imported by safe tests. |
| Other root-level implementations | Potential historical or alternate implementations | **UNKNOWN unless listed as legacy above.** No status is assigned from location or similarity alone. |

Every production dependency claimed by this map now traces to `vop_interwoven/`.

## Ownership boundary matrix

| Concern | Observed owner | Status |
|---|---|---|
| View resolution/validation | Production source of truth is `vop_interwoven.revit.view_basis`; probes add their own input rejection/unwrapping | Production owner confirmed; probe adapters remain fragmented. |
| Probe configuration | Dynamo positional `IN` wrapper and each probe's `run`/`_run` | Exists, probe-specific; no external job schema. |
| Variant generation/selection | Per-probe constants/selectors; external-sources has fixed all-variants loop | Exists but inconsistent. |
| Resolution calculations | Duplicated local functions in five probes; minimum-mutations delegates to `tests/dynamo/resolution_contract.py`; transaction probe has fixed 1600/backoff | Fragmented. |
| Revit transaction/rollback | Probe or per-variant function, not an outer runner | Exists within each probe. |
| TIFF/raw JSON | Each probe's export/report functions | Exists; naming/schema differ. |
| External image analysis | `tools/analyze_stage_a_probe.py` for four probe families | Partial dispatcher. |
| Acceptance classification | Mixed: probes classify safety/partial evidence; analyzer computes image-based recommendations for supported families | Fragmented; Dynamo correctly marks several results pending external analysis, but external-sources still contains internal image/classification paths. |
| Campaign state | None found | **Gap.** |
| Next-batch generation | None found | **Gap.** |
| Optional LLM interpretation | None found | **Gap.** |

## Direct answers to scoping questions

1. **Individual variants/cases?** Graphics: one named variant; linework: one or
   comma-separated modes; minimum mutations: comma/semicolon names (unknown
   names silently select nothing); alignment: one mode; transaction: normal vs
   failure-injection; external sources: **no**, always all fixed variants.
2. **Safely imported outside Dynamo?** Only minimum mutations is cleanly guarded.
   Others either run a caught failing wrapper or are global-`IN` coupled.
3. **Logic separated from wrapper?** Yes for five probes via `run`/`run_probe`;
   no adequate argument-taking entry boundary for image alignment.
4. **One script call multiple probes without `exec`?** Statically possible only
   after normal module imports and calling exposed entries, but wrapper side
   effects and alignment's global `IN` prevent a clean all-probe dispatcher now.
5. **Transaction group owner?** Each probe execution function/per-variant helper;
   no shared runner owner.
6. **One configured job without duplicated probe logic?** For the five callable
   entries, plausibly yes from static signatures; alignment lacks that boundary,
   external sources lacks variant selection, and no job adapter/schema exists.
7. **Resolved `View` accepted?** Five public entries accept raw wrappers or DB
   views and unwrap internally; alignment reads raw `IN[0]`. None accepts a
   planner-resolved persistent identity and performs lookup by ID/UniqueId.
8. **Consistent raw schema?** No. Probe identity, variants/modes/exports, paths,
   rollback, conclusion, and errors differ materially.
9. **Analyzer all probes?** No: four families only; transaction-group and
   external-sources outputs are skipped.
10. **Deterministic next batch blockers?** No campaign/job/state schema; no
    stable cross-probe result/acceptance schema; incomplete analyzer dispatch;
    selectors and view identity differ; repetitions/dependencies/eligibility are
    not represented; partial completion rules are absent.
11. **Demonstrably current root dependencies?** `tests/dynamo/resolution_contract.py`,
    `tools/analyze_stage_a_probe.py`, and the listed `vop_interwoven` modules are
    directly imported/called by current probes/tests. `vop_interwoven` is the
    confirmed current production boundary.
12. **Older unreferenced implementations?** `legacy/`, `archive/`, and
    `tests/archive/ssm_vop_v1/` have no imports/callers from the scoped current
    surfaces. More granular historical classifications remain `UNKNOWN` unless
    caller, import, entry-point, or documentation evidence establishes them.
