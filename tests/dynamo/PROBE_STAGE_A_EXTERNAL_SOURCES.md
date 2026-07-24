# Stage A external-source Dynamo probe

`probe_stage_a_external_sources.py` is a standalone Revit 2025 / Dynamo 3.3
CPython3 probe for linked-RVT and DWG Stage A behavior. It tests whether HOST,
LINK, and DWG identities can remain distinct through the color-assignment/export
contract without modifying production Stage A code.

## Dynamo wiring

```text
IN[0] = target view
IN[1] = output directory
IN[2] = optional link instance(s)
IN[3] = optional DWG import instance(s)
```

When `IN[2]` or `IN[3]` are omitted, candidates are discovered from the target
view through the repository's existing view collection and link/import expansion
logic. When inputs are supplied, they filter matching discovered link/import
instances only; the probe reports discovery explicitly and never silently
substitutes unrelated elements.

## Production code inspected and reused

The probe reuses the same external-source surfaces used by Stage A without
editing them:

- `collect_view_elements()` for host visible-model collection.
- `expand_host_link_import_model_elements()` for HOST/LINK/DWG expansion with the probe config explicitly setting `include_dwg_imports=True` so visible DWG imports can be discovered when present.
- `LinkedElementProxy` identity fields: `source_type`, `source_id`,
  `source_label`, `LinkInstanceId`, and linked/import element `Id`.
- `_build_flat_color_ogs()` for Stage A-style solid color overrides.
- `_try_color_link_element()` for Revit `LinkElementId` overrides.
- `build_palette()` / `choose_step()` for deterministic non-background RGB
  assignment.
- Revit `ImageExportOptions` settings matching Stage A diagnostics:
  `ZoomFitType.FitToPage`, horizontal fit direction, and lossless TIFF.

DWG imports are reported as `source_type = "DWG"`; the probe does not collapse
DWG into HOST in its output schema.

## Required variants

Each variant runs in a separate transaction group from the same original state:

1. `host_reference_coloring`
2. `linked_per_element_linkelementid_coloring`
3. `forced_linked_override_failure_hide_instance_fallback`
4. `dwg_importinstance_coloring`
5. `mixed_host_link_dwg_export`

If a required source type is not discovered in the view, that variant is marked
skipped with a clear reason instead of substituting unrelated elements. The
aggregate report remains `INCONCLUSIVE` unless the required HOST, LINK, and DWG
source-family variants actually run and pass; a host-only view cannot make the
external-source probe look complete.

## Transaction safety

For every non-skipped variant the probe:

1. force-closes Dynamo's ambient transaction;
2. starts a `TransactionGroup`;
3. snapshots element/link/import hidden and override state where the API permits;
4. applies temporary paints/fallbacks in a committed child transaction;
5. exports with no child transaction open;
6. rolls back the enclosing group in `finally`;
7. snapshots again and records state differences.

If rollback fails or captured state differs after rollback, the variant and probe
fail and identify the affected assignment/view context in JSON.

## Assignment schema

Every assignment records:

- `assignment_key`
- `rgb`
- `source_type`: `HOST`, `LINK`, or `DWG`
- `source_id`
- `source_label`
- `host_element_id`
- `link_instance_id`
- `linked_element_id`
- `import_instance_id`
- `category`
- `paint_success`
- `paint_failure`
- `hidden_link_fallback`

This schema is intentionally explicit because Stage A sidecars may need to keep
HOST, LINK, and DWG attribution separate.

## Failure semantics

A linked element that cannot be colored must not remain as unassigned analytical
contamination. The forced-failure variant skips the `LinkElementId` override and
hides the owning link instance to exercise the current fallback path. The report
states whether that fallback prevented expected linked colors from appearing,
whether rollback restored hidden state, and why this should remain a measured
fallback rather than an assumed final production policy.

## Image evidence

For every TIFF, the probe records:

- dimensions
- SHA-256
- file size
- exact expected-color pixel count by assignment
- missing expected colors
- whether Pillow was available for exact-color analysis

Exact linked colors in TIFF indicate that `LinkElementId` overrides rendered for
that sample. Missing linked colors are interpreted together with paint failures,
hidden-link fallback records, and rollback snapshots. A variant cannot return
`PASS` solely because rollback succeeded and Pillow ran: required source colors
must render for HOST/LINK/DWG coloring variants, while the forced linked-failure
variant requires hidden-owning-link fallback evidence.

## Output artifacts

The probe writes outputs under:

```text
<IN[1]>\external_sources_probe\
```

For each non-skipped variant it writes:

```text
<view>_<id>.<variant>.external_sources.tiff
```

It writes one combined JSON report:

```text
<view>_<id>.external_sources.json
```

The Dynamo `OUT` object returns:

- paths
- discovery report
- per-source success
- exact-color evidence
- rollback evidence
- supported identity fields
- required sidecar schema changes
- recommended linked fallback
- remaining limitations

## Runtime matrix

Run on views containing, where possible:

1. visible host model elements
2. visible linked RVT model elements
3. linked RVT content where `LinkElementId` overrides are expected to work
4. linked RVT content where override support is uncertain
5. visible DWG/DXF `ImportInstance` content
6. mixed HOST/LINK/DWG content in one crop
7. a templated view that may block visibility/graphics changes
8. a view with existing link hidden state or filters

## Evidence to return

For each run, return:

- the combined `*.external_sources.json`
- all `*.external_sources.tiff` files
- Dynamo `OUT` copied as text when possible
- view name, id, type, template/filter context
- which link instances were supplied or discovered
- which DWG imports were supplied or discovered
- notes on whether linked colors, DWG colors, and hidden-link fallback behavior
  match the visible Revit view
