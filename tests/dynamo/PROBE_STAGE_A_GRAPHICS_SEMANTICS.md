# Stage A graphics-semantics Dynamo probe

`probe_stage_a_graphics_semantics.py` is a standalone Revit 2025 / Dynamo 3.3
CPython3 probe for measuring which temporary graphics mutations are necessary
for a clean Stage A element-ID TIFF. It is diagnostic only and does not edit
production Stage A code.

## Dynamo wiring

```text
IN[0] = target model-capable view
IN[1] = output directory
IN[2] = maximum elements to color, null/0 means all eligible elements
IN[3] = variant selection, "all" by default
```

Supported variant names are:

1. `element_overrides_only`
2. `element_overrides_smooth_edges_off`
3. `element_overrides_flat_colors`
4. `template_detached_flat_colors_smooth_edges_off`
5. `visible_filters_disabled`
6. `category_halftone_neutralized`
7. `neutral_phase_filter`
8. `current_stage_a_full_suppression`
9. `visibility_off_filters_disabled_diagnostic`

## Code inspected and reused

The probe follows the transaction-group export harness proven by
`tests/dynamo/probe_stage_a_transaction_group_export.py`: close any ambient
Dynamo transaction, start a `TransactionGroup`, commit temporary child
transactions, export while no child transaction is open, and roll the enclosing
group back in `finally`.

The current Stage A implementation in `vop_interwoven/color_id_buffer.py` was
used as the reference for mutation order and helper behavior:

- `_build_flat_color_ogs()` sets solid surface/cut foreground patterns, line
  colors, zero transparency, and `SetHalftone(False)` for each element.
- Filter handling captures all view filters but disables only filters that are
  both enabled and visible. Visibility-off filters are not changed by the normal
  Stage A path, because they express semantic hiddenness. Variants that change
  filter or phase visibility recollect elements before painting so newly exposed
  model elements are assigned colors instead of becoming off-palette contamination.
- The neutral phase filter is created/reused as `VOP_NeutralPhaseFilter`, and
  Stage A re-collects elements after the phase swap so newly visible elements
  receive colors.
- Category halftone is neutralized only for touched model categories.
- View-template detachment happens before reading or mutating template-controlled
  view graphics, and reattachment is a final restore step in production. This
  probe relies on TransactionGroup rollback instead of production restoration.
- Link/import expansion is reused through
  `expand_host_link_import_model_elements()`, `_split_expanded_elements()`, and
  `resolve_all()` without changing production link/import behavior.
- Palette construction reuses `choose_step()` and `build_palette()` so the probe
  uses the same non-black/non-near-white RGB lattice as Stage A.
- TIFF export mirrors `_export_tiff()` settings: `ZoomFitType.FitToPage`,
  horizontal fit direction, lossless TIFF, and PixelSize backoff.

## Current mutation order

Production Stage A detaches the view template first, captures filter/phase and
other graphic state, starts a suppression transaction, disables enabled+visible
filters, applies the neutral phase filter where writable, hides annotation and
view-only model categories, sets FlatColors, disables SmoothEdges, recollects
under neutral phase, expands host/link/import content, builds the palette,
neutralizes category halftone, paints element/link overrides, commits the
suppression transaction, exports TIFF with no transaction open, then restores in
a separate transaction.

This probe tests the same concepts independently. Every variant starts from the
same original view state because each one uses a separate rolled-back
TransactionGroup.

## Annotation-category hiding

`_hide_annotation_categories()` mirrors `color_id_buffer.py`'s
`_hidden_category_state()` category-selection logic (every top-level
`CategoryType.Annotation` category plus the `OST_DetailComponents` /
`OST_Lines` view-only-model exceptions). A per-view template locks
`CanCategoryBeHidden()`/`GetCategoryHidden()` to report against committed
document state, so `current_stage_a_full_suppression` (the only variant that
hides annotation categories) commits the view-template detach in its own
transaction before the main settings transaction runs
`_hide_annotation_categories()` — the same two-step ordering
`color_id_buffer.py` uses (`detach_tx` committed, then `suppress_tx`). Every
candidate category is traced regardless of outcome (`name`, `category_type`,
`can_category_be_hidden`, `was_hidden_before`, `hide_applied`) under
`category_hide_trace` in that variant's result, so an empty
`mutations.hide_annotation_categories` list is diagnosable from the JSON
alone.

## Color fidelity vs semantic visibility

Treat these as color-fidelity candidates:

- element-level overrides via the Stage A palette
- `SmoothEdges=False`
- `DisplayStyle.FlatColors`
- view-template detachment when a template blocks graphics settings
- category halftone neutralization

Treat these as visibility-semantic changes, not automatic improvements:

- disabling enabled and visible filters
- disabling visibility-off filters in the explicitly diagnostic variant
- swapping to the neutral phase filter
- hiding annotation/view-only categories in the full-suppression variant

Do not recommend disabling a filter or neutralizing phase merely because it
increases element count. Report which semantic view each variant represents.

## Output artifacts

The probe writes outputs under:

```text
<IN[1]>\graphics_semantics_probe\
```

For each variant it writes:

```text
<view>_<id>.graphics_semantics.<variant>.tiff
```

It also writes one combined JSON:

```text
<view>_<id>.graphics_semantics.json
```

The Dynamo `OUT` object contains paths, per-variant rollback result, image
analysis, element counts before/after phase changes, paint failures, semantic
comparison inputs, and a ranked recommendation.

## Image analysis

When Pillow is available in Dynamo CPython, the probe records:

- actual dimensions
- file size and SHA-256
- exact expected-palette pixel count
- off-palette foreground pixel count and percent
- exact white background pixel count
- black and near-white reserved-range violation counts; near-white non-white pixels are counted as off-palette violations instead of being swallowed as background
- number of expected colors detected
- missing assigned colors
- unexpected colors, capped to the top 50
- visible pixel area by assigned element color

An assigned element with zero visible pixels is reported as missing from the
raster, but this is not automatically a paint failure. It may be hidden by
occlusion, crop, phase/filter semantics, or Revit display behavior. Use the
paint-failure list and cross-variant comparisons to distinguish causes.

## Runtime matrix

Run at minimum on:

1. floor plan with active crop
2. floor plan with inactive crop
3. reflected ceiling plan / RCP
4. section
5. elevation
6. detail view
7. a templated model view
8. a view with enabled+visible filters
9. a view with a visibility-off filter
10. a phased view where the neutral phase filter changes visibility
11. a view with category halftone active
12. a view containing linked/imported geometry

For each run, return the combined JSON, all TIFFs, Dynamo `OUT` text if
available, and notes about the original template/filter/phase/halftone setup.

## Pass criteria

For every variant:

- the transaction group starts successfully
- the temporary child transaction commits successfully
- export occurs with no child transaction open
- the transaction group rolls back successfully in `finally`
- captured before/after state has no differences
- TIFF image analysis completes, or the variant is marked inconclusive if Pillow
  is unavailable

The probe conclusion is `FAIL` if any variant fails rollback or state safety,
`PASS` if all requested variants pass with image analysis, and `INCONCLUSIVE`
when safety passes but image analysis cannot be completed locally.
