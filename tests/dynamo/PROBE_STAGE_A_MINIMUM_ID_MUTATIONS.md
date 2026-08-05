# Stage A Minimum Host Element-ID Mutations Probe

`probe_stage_a_minimum_id_mutations.py` is a focused follow-up to the Stage A
graphics-semantics probe.  It does **not** replace that evidence and it does
**not** modify production Stage A code.  Its job is to identify the minimum set
of temporary view mutations needed for a clean host element-ID TIFF while
preserving the original model-visibility semantics.

## Dynamo wiring

Wire a Python node to:

```text
IN[0] = target model-capable view
IN[1] = output directory
IN[2] = maximum host elements to color; null/0 means all eligible
IN[3] = variant selection; default "all"
IN[4] = resolution policy: "paper_space_dpi" (default), "fixed_pixel_width", or "both"
IN[5] = target DPI for paper_space_dpi; default 150
IN[6] = fixed pixel width diagnostic control; default 1600
IN[7] = optional maximum pixel dimension cap
IN[8] = optional repository root containing `vop_interwoven` and `tests/dynamo`
```

Use `paper_space_dpi` at 150 DPI as the authoritative production comparison.
Use `both` only when you also want the fixed 1600 px diagnostic control. The
probe is safe to paste directly into a Dynamo Python node: its import bootstrap
does not require Dynamo to define `__file__`. If Dynamo cannot import the repo
from its current search paths, wire `IN[8]` to the repository root folder rather
than relying on implicit path discovery.

## Resolution contract reused

After the optional `IN[8]` repository root is placed on `sys.path`, the probe imports `tests.dynamo.resolution_contract` and uses the same
`calculate_paper_space_resolution()`, `apply_resolution_cap()`,
`resolution_report_for_accepted_width()`, and `choose_resolution_bounds()` helper
contract used by the revised Stage A probe tests.  It does not implement a
second paper-space formula.  Every authoritative variant in one run is invalid
for comparison unless these values match:

- bounds source and bounds values
- accepted pixel width
- actual TIFF dimensions
- accepted pixels per model foot / pixel-to-UV density
- frozen assignment set and element-to-RGB mapping


## Dynamo compatibility notes

The probe creates its temporary assignment raster with the current repository
`ViewRaster` signature: keyword arguments for `width`, `height`, `cell_size`,
`bounds=Bounds2D(...)`, `tile_size`, and `cfg`.  If Dynamo reports a
`ViewRaster` constructor/type error, confirm that `IN[8]` points at the same
repository checkout as the pasted probe code.  The probe also installs a small
`typing` fallback before importing production collection modules, because some
Dynamo Python-node environments omit the stdlib `typing` module even though the
production collection policy only uses it for annotations.

## Required first run

First run `IN[3] = "all"` on the same elevation used for the prior graphics
semantics probe.  Return the JSON, CSV, and all TIFF files so the previous
results can be compared directly against this factorial and leave-one-out run.

## Mutation inventory

The probe reports one inventory record per current Stage A or full-suppression
candidate mutation.  Each runtime variant records:

```text
mutation_id
API property or method
original value
requested value
whether the mutation was applied
effective value after application
whether a template controlled it
expected fidelity effect
possible visibility-semantic effect
restore mechanism
```

Current inventory:

| mutation_id | API property or method | Expected fidelity effect | Possible visibility-semantic effect | Restore |
| --- | --- | --- | --- | --- |
| `detach_template` | `view.ViewTemplateId = ElementId.InvalidElementId` | Prerequisite that unlocks template-controlled settings. | May alter evaluated instance settings if API preservation is imperfect. | TransactionGroup rollback. |
| `hide_annotation_categories` | `view.SetCategoryHidden(category.Id, True)` for annotation categories plus `OST_DetailComponents` and `OST_Lines` | Removes annotation/view-only foreground. | Intentional model-only scope change, not a model-category visibility change. | TransactionGroup rollback. |
| `smooth_edges_off` | `GetViewDisplayModel(); SmoothEdges=False; SetViewDisplayModel()` | Removes anti-aliased edge blends. | No intended model visible-set change; edge occupancy can change. | TransactionGroup rollback. |
| `display_style_flat_colors` | `view.DisplayStyle = DisplayStyle.FlatColors` | Removes lighting/material tint. | Should not change visible model set; support varies by view type. | TransactionGroup rollback. |
| `visible_filter_graphics_neutralized` | `view.SetFilterOverrides(fid, OverrideGraphicSettings())` while retaining `GetFilterVisibility(fid)` | Removes filter graphic tint/halftone/transparency. | Preserves filter visibility booleans; rule inclusion remains. | TransactionGroup rollback. |
| `category_halftone_neutralized` | `GetCategoryOverrides(cat); SetHalftone(False); SetCategoryOverrides()` | Removes category halftone lightening. | No category visibility change intended. | TransactionGroup rollback. |
| `phase_graphics_neutralized` | Graphic-only phase override reset when a safe API path exists | Would remove phase graphic tint while preserving rules. | Cannot be recommended if API path changes phase visibility. | TransactionGroup rollback. |
| `phase_filter_neutralized` | `VIEW_PHASE_FILTER` set to neutral phase filter | Can remove phase graphic effects. | Semantic unless visible model set is proven unchanged. | TransactionGroup rollback. |
| `visibility_off_filters_disabled` | `SetIsFilterEnabled(fid, False)` for filters whose visibility is false | May reveal assigned colors, but this is not fidelity. | Changes original filter visibility semantics; diagnostic only. | TransactionGroup rollback. |
| `shadows_off` | `ViewDisplayModel.ShowShadows=False` | Removes shadow tint if exposed. | No intended visible-set change. | TransactionGroup rollback. |
| `ambient_occlusion_off` | `ViewDisplayModel.AmbientOcclusion=False` | Removes AO tint if exposed. | No intended visible-set change. | TransactionGroup rollback. |
| `sketchy_lines_off` | `ViewDisplayModel.SketchyLines=False` | Removes sketchy line perturbation if exposed. | No intended visible-set change. | TransactionGroup rollback. |
| `depth_cueing_off` | `ViewDisplayModel.DepthCueing=False` | Removes depth fade if exposed. | No intended visible-set change. | TransactionGroup rollback. |
| `background_neutralized` | Graphic-display background settings where safely exposed | Stabilizes background classification. | No intended visible-set change. | TransactionGroup rollback. |
| `silhouettes_removed` | Silhouette display setting where safely exposed | Removes silhouette edge colors. | Linework occupancy can change; no model-set change intended. | TransactionGroup rollback. |

A completed export is not proof that a mutation was applied.  A mutation counts
as tested only when its status is `APPLIED` or `ALREADY_MATCHED`.  Exported TIFFs
from variants with `FAILED`, `BLOCKED_BY_TEMPLATE`, or `UNSUPPORTED` requested
mutations must not be used as evidence for those mutations.

## Stage 1 factorial design

The probe begins with these controls:

1. `attached_element_overrides_only`
2. `detached_preserve_original_display`

Then, with template detached, it tests every combination of:

```text
A = annotation/view-only categories hidden
S = SmoothEdges false
F = DisplayStyle FlatColors
```

Required variants:

```text
detached_none
detached_A
detached_S
detached_F
detached_AS
detached_AF
detached_SF
detached_ASF
```

If `DisplayStyle.FlatColors` is unsupported, the probe reports the unsupported
status and the reduced factorial remains valid only for the supported settings.

## Stage 2 remaining-mutation design

The probe uses `detach_template + A + S + F` as the initial faithful candidate
baseline for runtime evidence gathering.  Runtime analysis must then choose the
smallest clean Stage 1 variant before drawing conclusions.  Each remaining
full-suppression candidate is generated twice:

```text
baseline_plus_<mutation>
full_minus_<mutation>
```

It also emits `full_suppression_reference`.

If multiple mutations appear redundant, use the JSON evidence to perform the
iterative backward-elimination workflow documented in the task: remove all
individually redundant mutations, rerun `reduced_candidate`, and then rerun
leave-one-out variants for every retained mutation until no additional mutation
can be removed without fidelity or semantic failure.


## Attestation and rollback details

Template detachment is committed in its own child transaction before annotation
category hiding, matching the earlier graphics-semantics probe behavior required
for templated views.  If any later child transaction fails, the probe rolls that
child transaction back before rolling back the `TransactionGroup`.  Semantic
mutations such as phase-filter neutralization and visibility-off-filter disabling
remain diagnostic-only even when attested successfully.

## Semantic diagnostics excluded from the default recommendation

These variants are labeled diagnostic and are not eligible for the default
recommended production minimum:

```text
diagnostic_disable_visibility_off_filters
diagnostic_neutral_phase_filter
diagnostic_recollect_after_filter_change
diagnostic_recollect_after_phase_change
```

They intentionally test changes that can alter the original model visible set.
Changing a visibility-off filter to visible or replacing the phase filter may
produce more colored pixels, but that is a semantic change rather than a color
fidelity improvement unless the returned evidence proves the visible model set is
unchanged.

## Filter and phase reporting

For every filter the probe records:

```text
filter_id
filter_name
original_visibility
effective_visibility
original_graphic_override_summary
effective_graphic_override_summary
enabled
```

Filter graphic neutralization and filter visibility disabling are separate
mutations.  The default production candidate must retain visibility-off filters.

For phase behavior the probe records the view's phase-filter parameter, read-only
state, requested neutral filter, created neutral-filter status, and recollection
metadata for diagnostics.  Changing `VIEW_PHASE_FILTER` is semantic unless the
returned evidence proves the visible model-element set is unchanged.

## Image analysis

When Pillow is available inside Dynamo, each TIFF includes decoded-pixel metrics:

- requested and actual resolution metadata
- actual dimensions
- file SHA-256
- pixel-data SHA-256
- background pixel count
- exact expected-palette pixel count
- off-palette foreground count and percent
- unexpected RGB values and counts
- black and near-white violations
- assigned colors detected
- missing assigned colors
- visible pixel count by assigned element
- assigned elements with zero visible pixels

If Pillow is unavailable, the TIFF still exports and the JSON marks
`external_analysis_required`.  Use decoded pixels, not TIFF byte equality, for
visual equivalence.

## Reduced cross-view run matrix

After the elevation factorial identifies a reduced candidate, run the reduced
candidate and both reference controls (`attached_element_overrides_only` and
`full_suppression_reference`) on:

- floor plan with active crop
- floor plan with inactive crop
- RCP
- section
- elevation
- detail view

Rerun the full factorial only on a view type where the reduced candidate fails or
produces different evidence.


## External analysis

After the Dynamo run, analyze the returned JSON/TIFF bundle with:

```bash
python tools/analyze_stage_a_probe.py <output-directory-or-minimum_id_mutations.json>
```

The analyzer recognizes `stage_a_minimum_id_mutations` reports, fills decoded
pixel metrics into each attested variant, marks blocked/failed mutation exports
as non-evidence, and writes `<name>.minimum_id_mutations.analyzed.json` with the
recommendation schema.

## Output artifacts

The probe writes:

```text
<view>_<id>.<resolution>.<variant>.tiff
<view>_<id>.<resolution>.minimum_id_mutations.json
<view>_<id>.<resolution>.mutation_matrix.csv
```

Return the JSON, CSV, and all TIFFs.  Also return the previous graphics-semantics
runtime report when comparing against prior evidence.

## Completion versus verified mutation

Probe completion means the variant ran, rolled back, and produced JSON/CSV
records.  A requested mutation is verified only when its status is `APPLIED` or
`ALREADY_MATCHED` and the effective value matches the requested value before
export.  TIFFs from blocked or failed requested mutations are marked as
non-evidence for that mutation.
