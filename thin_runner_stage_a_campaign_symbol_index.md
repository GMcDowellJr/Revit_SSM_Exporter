# Thin runner Stage A campaign symbol index

The entries below are corroborated by `thin_runner_stage_a_campaign_code_map.md` (classification
and file responsibility) and `thin_runner_stage_a_campaign_trace_map.md` (flow ownership). Revit
behavior remains unverified. `vop_interwoven` symbols are referenced dependencies
and `vop_interwoven` is the corrected authoritative production boundary.

## Shared resolution symbols

| File / symbol | Responsibility | Inputs -> outputs | Known callers | Safety / Revit / transaction / state | Side effects and likely boundary | Navigation evidence |
|---|---|---|---|---|---|---|
| `tests/dynamo/resolution_contract.py::round_half_up_positive` | Stable positive rounding | number -> int | Other contract functions; minimum probe wrappers; safe tests | Import-safe; no Revit/transaction/global mutation | None; pure shared boundary | Direct definitions/imports. |
| `...::calculate_paper_space_resolution` | Required pixels and pixels/model-foot | model width/height, scale, DPI -> dict | Probe wrappers/tests | Pure | None; callable | Direct calls. |
| `...::apply_resolution_cap` | Width-driven cap/effective DPI | required size, optional cap -> dict | report builder/tests | Pure | None | Direct calls. |
| `...::calculate_canvas_placement` | Model-to-canvas offsets/sizes | model/canvas bounds, resolution -> dict | alignment/local copies/tests | Pure | None | Direct calls. |
| `...::build_resolution_report` | Complete policy report | policy, dimensions, scale, targets, bounds source -> dict | minimum probe/tests | Pure | None; preferred observed shared calculation | Direct import/call. |
| `...::resolution_report_for_accepted_width` | Recompute after Revit backoff | report + accepted width -> dict | minimum probe/tests | Pure | None | Direct import/call. |
| `...::choose_resolution_bounds` | Active-crop vs resolved bounds choice | flags/bounds -> `(bounds, source)` | minimum probe/tests | Pure | None | Direct import/call. |
| Five probe-local resolution families (`_parse_resolution_policy`, `_resolution_runs`/`resolution_runs`, `_resolution_suffix`, `calculate_paper_space_resolution`, `apply_resolution_cap`, `build_resolution_report`, `_resolution_report_for_accepted_width`) | Duplicate policy parsing/report construction | Dynamo resolution controls/view bounds -> run configs/reports | Respective probe `run`/`_run` | Importable helpers; no direct Revit except view-bound wrappers | None until export; callable but fragmented | Repeated definitions and local callsites. |

## Probe entry, parsing, selection, export, and rollback symbols

| File / symbol | Responsibility | Inputs -> outputs | Known callers | Import-safe / Revit / transaction / global state | Artifact side effects / likely callable boundary | Navigation evidence |
|---|---|---|---|---|---|---|
| `probe_stage_a_transaction_group_export.py::run_probe` | Full transaction/export safety probe | raw view/elements, output dir, inject flag -> report | Dynamo top-level | Function importable but module wrapper unsafe; Revit; **owns group and child transaction** | TIFF+JSON; **callable boundary with wrapper isolation** | Direct top-level call; group lifecycle in function. |
| `...::_validate_inputs` | Validates/unwraps view/elements/doc/output | Document + raw inputs -> view/elements/output | `run_probe` | Revit; no transaction | Directory validation; internal boundary | Direct call. |
| `...::_snapshot_state`, `...::_diff_values` | Capture/compare view/element state | Revit objects/snapshots -> dict/diffs | `run_probe` | Revit; no ownership | None | Before/after calls. |
| `...::_apply_temporary_changes` | Temporary OGS/display changes | doc/view/elements -> colors/diagnostics | `run_probe` child transaction | Revit; caller owns transaction | Mutates document temporarily | Called between transaction Start/Commit. |
| `...::_export_tiff` | Revit TIFF export/backoff | doc/view/path/width -> path,width | `run_probe` | Revit; requires no child transaction | TIFF | Direct call. |
| `probe_stage_a_graphics_semantics.py::run` | Parse/validate and execute requested variants | raw view, output/max/selection/resolution -> report | top-level wrapper | Partly import-safe; Revit through delayed imports; no outer group | Combined JSON + TIFFs; **callable boundary** | Direct wrapper call. |
| `...::_validate_view` | Model-capable view validation | view -> exception/success | `run` | Revit/production mode import | None | Direct call. |
| `...::_variant_steps`, `VARIANTS` | Variant definitions | name -> steps | `run`, `_run_variant`, `_recommend` | Pure/static global constants | None | Direct iteration/calls. |
| `...::_run_variant` | Mutate/export/rollback one variant | doc/view/output/base/name/config -> result | `run` | Revit; **owns group/transactions** | TIFF; snapshot/diff/rollback result | Direct nested loop call. |
| `...::_export_tiff` | TIFF export | doc/view/path/resolution -> export record | `_run_variant` | Revit | TIFF | Direct call. |
| `...::_analyze_image` | Raw pending analysis placeholder | path/assignments -> pending dict | `_run_variant` | External-safe | No pixel authority; declares analyzer move | Function body diagnostic. |
| `...::_recommend` | Raw recommendation aggregation | variants -> dict | `run` | Pure | No artifact | Direct call; external metrics often pending. |
| `probe_stage_a_external_sources.py::run` | Discover sources and execute every variant | view/output/link/DWG filters/resolution -> report | top-level wrapper | Partly import-safe; Revit | JSON+TIFF; callable but **no single-variant argument** | Fixed `for variant in VARIANTS`. |
| `...::_reject_reason` | Required/model-capable view check | view -> reason/None | `run` | Revit; production mode resolver | None | Direct call. |
| `...::_discover_assignments`, `...::_identity_from_entry` | Discover/filter and normalize HOST/LINK/DWG identities | doc/view/filter inputs -> grouped assignments/discovery | `run` | Revit + production collection pipeline | None | Direct call. |
| `...::_items_for_variant` | Maps fixed variant to source assignments | variant/grouped sources -> items | `run` | Pure | None | Direct fixed loop. |
| `...::_apply_assignments` | Applies color/fallback behavior | doc/view/variant/items -> assignments | `_run_variant` | Revit; caller transaction | Temporary mutation | Direct call. |
| `...::_run_variant` | One source variant group/export/rollback | doc/view/path/variant/items/resolution -> result | `run` | Revit; **owns group/transaction** | TIFF and rollback report | Direct call. |
| `...::_classify_variant`, `...::_source_evidence_status` | Probe-local raw evidence classification | variant/assignment/image values -> dict | `_run_variant`/`run` | Pure | No artifact; not external authoritative dispatch | Direct calls. |
| `probe_stage_a_model_linework.py::_select_modes` | Parses all/single/comma mode list | selection -> list | `run` | Pure | None; reusable selector | Direct call. |
| `...::run` | Executes reference and selected modes | view/output/focused/selection/resolution -> report | top-level wrapper | Partly import-safe; Revit | JSON+TIFF; **callable boundary** | Direct wrapper call. |
| `...::_reject_reason` | View validation | view -> reason | `run` | Revit/production mode dependency | None | Direct call. |
| `...::_run_reference_element_id`, `...::_run_mode` | Reference/mode transaction, repeated exports, rollback | doc/view/path/config -> result | `run` | Revit; each **owns group/transactions** | TIFF pairs and state report | Direct loop calls. |
| `...::_select_modes`, `...::_mode_definition`, `MODES` | Variant generation/selection | text/name -> modes/definition | `run`, apply helpers | Pure/global tuple | None | Definitions/calls. |
| `...::_export_tiff` | TIFF export/backoff | doc/view/path/report -> image record | reference/mode runners | Revit | TIFF | Direct calls. |
| `probe_stage_a_image_alignment.py::_run` | Entire global-IN alignment probe | global `IN` -> report | top-level wrapper only | **Not suitable import-call boundary**; Revit; reads global state; **owns group** | TIFFs + JSONs; pasted-node entry | Direct global reads and top-level call. |
| `...::_reject_reason` | View rejection | view -> reason | `_run` | Revit/ambiguous mode resolver | None | Direct call. |
| `...::_config_for_view`, `...::_compute_bounds` | Production-backed config/model/annotation/canvas bounds | view/doc/config -> basis and bounds | `_run` | Revit + current production modules | None | Direct calls/imports. |
| `...::_create_calibration_markers`, `...::_set_crop_to_bounds` | Temporary calibration/crop mutations | doc/view/bounds -> IDs/diagnostics | `_run` | Revit; caller group, own child transactions | Temporary document mutation | Direct calls within group. |
| `...::_export_tiff` | TIFF export/discovery | doc/view/path/width -> image | `_run` | Revit | TIFF | Direct calls. |
| `probe_stage_a_minimum_id_mutations.py::make_variant`, `generate_stage1_variants`, `generate_stage2_variants`, `backward_elimination_round` | Pure variant generation/elimination bookkeeping | mutation sets/support flags -> variant dicts | `run`, safe tests | **Import-safe**, no Revit | None; strong external planning candidates for this family only | Direct safe-test imports. |
| `...::_select_variants` | Selects all or named variants | selection + generated lists -> list | `run` | Import-safe/pure | None; silently ignores unknown names | Direct call/body. |
| `...::run` | Full minimum-mutation probe | raw view/output/config/repo root -> report | guarded Dynamo wrapper | **Import-safe module**; Revit execution | JSON+CSV+TIFF; **callable boundary** | `if "IN" in globals()` wrapper. |
| `...::_load_resolution_contract`, `_add_repo_root_to_path`, `_ensure_typing_module` | Dynamo-compatible imports/shared-contract loading | paths/environment -> module/path | resolution wrappers/`run`/tests | External-safe with sys.modules/sys.path shared-state mutation | No artifacts | Direct safe-test coverage. |
| `...::_collect_assignment_set` | Production-backed frozen assignment collection | doc/view/max -> IDs/counts | `run` | Revit + current production imports | None | Direct call. |
| `...::_apply_mutation`, `mutation_status_record`, `all_mutation_statuses` | Apply/attest catalog mutations | doc/view/mutation -> status | `_run_variant`, tests for records | Revit for apply; caller owns transaction | Temporary mutation | Direct calls. |
| `...::_run_variant` | One mutation experiment | doc/view/path/variant/assignment/resolution -> result | `run` | Revit; **owns group/transactions** | TIFF and rollback/mutation evidence | Direct loop call. |
| `...::analyze_tiff`, `compare_tiff_pixels`, `pixel_data_sha256`, `classify_mutation_evidence` | Probe-local optional/raw image comparisons and classification | TIFF/data -> metrics/status | `_run_variant`, `run`, safe tests | Pillow optional; no Revit | Reads TIFF, no new image | Direct calls/tests. |
| `...::_write_json`, `...::_write_csv` | Raw report writers | path/data -> file | `run`, tests | External-safe | JSON/CSV | Direct call. |

## External analyzer symbols

| File / symbol | Responsibility | Inputs -> outputs | Known callers | Import-safe / Revit / transaction / state | Side effects / likely boundary | Navigation evidence |
|---|---|---|---|---|---|---|
| `tools/analyze_stage_a_probe.py::main` | CLI entry | argv paths -> exit code | `__main__` | Import-safe; external; no transaction | Writes analyzed artifacts via dispatch; **CLI boundary** | Guarded main call. |
| `...::collect` | Recursively selects raw JSON | paths -> JSON paths | `main` | Pure filesystem enumeration | Reads directories; excludes analyzed JSON | Direct call. |
| `...::analyze_json` | Family dispatch and analyzed report writer | JSON path -> `(output path, summary)` | `main`, safe analyzer tests | Import-safe; NumPy/Pillow; no Revit | Writes `.analyzed.json` and possibly diff TIFFs; **programmatic analyzer boundary** | Direct tests and branch dispatch. |
| `...::resolve_path`, `load_rgb`, `sha256_file`, `base_info` | Artifact path/image primitives | paths -> arrays/metadata | family analyzers | External | Reads TIFF | Direct calls. |
| `...::analyze_graphics_image`, `recommend_graphics` | Graphics pixel metrics/recommendation | TIFF/assignments or variants -> dict | `analyze_json` | External | Reads TIFF | Graphics branch. |
| `...::analyze_minimum_id_mutations`, `recommend_minimum_mutations`, `_mutation_ok`, `_variant_clean` | Normalize minimum-mutation evidence and guard recommendations | JSON/data -> mutations of data/recommendation | `analyze_json`, safe tests indirectly | External | Mutates loaded report in memory | Minimum branch. |
| `...::analyze_linework_image`, `connected_components`, `classify_mode`, `repeatability`, `rank_modes`, `write_diff` | Linework metrics, classification, ranking and diffs | TIFFs/modes -> dicts/files | `analyze_json` | External | `write_diff` creates TIFF | Linework branch. |
| `...::analyze_alignment_image`, `affine_fit`, `alignment_evidence_status`, `placement` | Alignment/marker/placement evidence | TIFF/markers/bounds -> dict | `analyze_json` | External | Reads TIFF | Alignment branch. |

No symbol exists for external-sources dispatch, transaction-probe dispatch,
campaign configuration, next-batch generation, run-manifest normalization,
campaign state, common acceptance reduction, or optional LLM interpretation.

## Current production dependency symbols used by probes

| File / symbol | Observed probe responsibility/callers | Classification |
|---|---|---|
| `vop_interwoven.config::Config` | Probe resolution/collection configuration | **Current production.** |
| `vop_interwoven.revit.view_basis::{resolve_view_mode,make_view_basis,resolve_view_bounds,xy_bounds_from_crop_box_all_corners,crop_box_from_uv_bounds}` | View validation, basis, bounds and crop conversion across probes | **Current production.** |
| `vop_interwoven.revit.annotation::compute_annotation_extents` | Alignment canvas bounds | **Current production.** |
| `vop_interwoven.pipeline::init_view_raster` | Graphics-semantics, external-sources, and model-linework raster/assignment setup | **Current production.** |
| `vop_interwoven.revit.collection::{collect_view_elements,expand_host_link_import_model_elements}` | Graphics-semantics, external-sources, minimum-mutations, and model-linework assignment/source discovery; model-linework calls them from both model-category and reference-element collectors | **Current production.** |
| `vop_interwoven.color_id_buffer::{resolve_all,build_palette,choose_step,_build_flat_color_ogs,_get_solid_pattern_id,_try_color_link_element,get_or_create_neutral_phase_filter}` | Assignment resolution, palette/OGS, link color and filter mutation | **Current production.** |
| `vop_interwoven.core.math_utils::Bounds2D`, `core.raster::ViewRaster`, `core.diagnostics::Diagnostics` | Bounds/raster/diagnostic objects | **Current production.** |

These symbols establish the production dependencies used by the probes. Their
transaction context is still owned by each calling probe where temporary Revit
mutation occurs; the production helpers are not evidence of a shared campaign
transaction owner.
