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

`run_probe()`'s `raw_links`/`raw_dwgs` parameters are **optional filters, not
required identity** - `_discover_assignments()` always calls `_collect_expanded()`
to discover candidates from the target view first, and only narrows that
discovered set down to the supplied elements when `raw_links`/`raw_dwgs` is
non-empty (an empty/omitted filter is unrestricted, not "nothing to work
with"). If a run reports `"No discovered candidates for required source
type(s)"` with no filter supplied, that means the view itself has no visible
element of that source type discoverable by `_collect_expanded()` - check
that the RVT link/DWG import is actually visible (not hidden, not filtered
out by a view filter or workset) in the view being probed, not that an input
was "forgotten". For DWG specifically, visibility alone is not enough:
`_collect_from_dwg_imports()` (`vop_interwoven/revit/linked_documents.py`)
deliberately excludes any `ImportInstance` whose `ViewSpecific` property is
true, so a visible view-specific CAD import is never discovered - only
model-level DWG imports are candidates.

### Via the campaign/batch pipeline (`stage_a_cycle.py` / `campaign_planner.py`)

IN[2]/IN[3] above are for running this probe directly from a Dynamo graph.
Through the campaign/batch pipeline, campaign JSON cannot carry a live Revit
element, so the registry's `stage_a_external_sources` adapter
(`revit_probe_registry.py`) resolves optional campaign-facing settings into
`raw_links`/`raw_dwgs` before dispatch, the same optional-filter semantics as
IN[2]/IN[3] above - useful for narrowing a probe run to a specific link/import
instance in a view that has several, not for supplying identity the probe
otherwise couldn't discover:

```json
"settings": {
  "resolution_policy": "fixed_pixel_width",
  "fixed_pixel_width": 1600,
  "link_instance_unique_ids": ["<RevitLinkInstance UniqueId>"],
  "dwg_import_unique_ids": ["<ImportInstance UniqueId>"]
}
```

`link_instance_ids`/`dwg_import_ids` (integer `ElementId` values) are also
accepted; `UniqueId` is preferred for the same reason it is elsewhere in this
pipeline (stable across some document round-trips ElementId is not). Any
other unrecognized setting is rejected before a transaction starts or a TIFF
is exported - this same resolution function backs `validate_settings`, so a
misconfigured job is caught in `IN[2] = True` validation-only mode too.

An external-source campaign job should also set `selection` to the variants
that match its own source-family capability contract - an RVT-link job to
`["linked_per_element_linkelementid_coloring",
"forced_linked_override_failure_hide_instance_fallback"]`, a DWG job to
`["dwg_importinstance_coloring"]` - so the analyzer only evaluates the cases
that job actually requested (see "Source-family capability contract" above).
`selection` is independent of image-resolution settings
(`resolution_policy`/`target_dpi`/`fixed_pixel_width`): a resolution label
such as `fixed_1600` is a `case`/`variant` name for the campaign's own job
bookkeeping, never a source-family selector.

## Production code inspected and reused

The probe reuses the same external-source surfaces used by Stage A without
editing them:

- `collect_view_elements()` for host visible-model collection.
- `expand_host_link_import_model_elements()` for HOST/LINK/DWG expansion with the probe config explicitly setting `include_dwg_imports=True` so visible DWG imports can be discovered when present.
- `LinkedElementProxy` identity fields: `source_type`, `source_id`,
  `source_label`, `LinkInstanceId`, and linked/import element `Id`.
- `_build_flat_color_ogs()` for Stage A-style solid color overrides.
- `_try_color_link_element_detailed()` for Revit `LinkElementId` overrides.
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
skipped with a clear reason instead of substituting unrelated elements. A
skipped `dwg_importinstance_coloring` variant names the actual exclusion
reason (see "DWG eligibility diagnostics" below) rather than an unexplained
`DWG = 0` when a DWG `ImportInstance` was supplied but excluded.

### Source-family capability contract (requested cases only)

`host_reference_coloring`, `linked_per_element_linkelementid_coloring` +
`forced_linked_override_failure_hide_instance_fallback`, and
`dwg_importinstance_coloring` map to three independent source-family
capability contracts - HOST, LINK, and DWG (`SOURCE_FAMILY_VARIANTS`). A job
requests a family by selecting one or more of its variants (`selection`
setting); **a family is only evaluated, and only gates PASS/FAIL, when the
job actually requested it.** An RVT-link-only job (selecting only the two
LINK variants) is never penalized for a missing HOST or DWG case, and a
DWG-only job is never penalized for a missing LINK or HOST case. A view that
happens to also contain host geometry does not implicitly make HOST
required.

LINK capability specifically distinguishes:

- `APPLIED` - the `LinkElementId` override rendered.
- `UNSUPPORTED` - the override API returned `False` with no exception: an
  explicit, expected finding that per-linked-element host-style recoloring is
  not supported in the current Revit/API environment. This is **not** a
  required Stage A PASS criterion and is reported as its own capability
  result (`linked_per_element_linkelementid_coloring` concludes
  `"UNSUPPORTED"`, not `"FAIL"`), never a generic visual failure.
- `FAILED` - an unexpected exception was raised attempting the override; a
  real failure needing investigation, distinct from a clean `UNSUPPORTED`.
- `NOT_TESTED` - `forced_linked_override_failure_hide_instance_fallback`
  deliberately skips the attempt to exercise the whole-link-instance
  suppression fallback instead.

The LINK family can PASS from `UNSUPPORTED` capability plus a passing
whole-link fallback alone; per-linked-element host-style recoloring is
explicitly **not** required. Obtaining individual linked-RVT linework/element
identity beyond what `LinkElementId`/`View.SetElementOverrides` already
exposes would require linked-document/raw-geometry extraction and
translation - out of scope for this probe and not implemented here.

### DWG eligibility diagnostics

`discovery.dwg_eligibility_diagnostics` records, for every supplied and/or
view-collected `ImportInstance`, its element id, `ViewSpecific`, whether the
current production discovery policy
(`_collect_from_dwg_imports` in `vop_interwoven/revit/linked_documents.py`)
treats it as eligible, and an explicit `exclusion_reason` when it does not
(`EXCLUDED_VIEW_SPECIFIC_IMPORT`, `NOT_FOUND_IN_VIEW_IMPORT_INSTANCE_COLLECTOR`,
or `EXCLUDED_BY_PRODUCTION_DISCOVERY_POLICY_UNDETERMINED_REASON`) - without
changing that production policy. A campaign fixture that is itself
view-specific is a fixture/case eligibility problem, not evidence that DWG
handling is broken.

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
- `link_override_status`: `APPLIED` / `UNSUPPORTED` / `FAILED` / `NOT_TESTED` for
  LINK assignments (`null` for HOST/DWG); see the source-family capability
  contract above.
- `hidden_link_fallback`

This schema is intentionally explicit because Stage A sidecars may need to keep
HOST, LINK, and DWG attribution separate. Each discovered item produces
exactly one assignment record - `_apply_assignments()` appends to its result
list exactly once per item, and `diagnostics`/`paint_diagnostics` are derived
from those same records afterward rather than tracked as a second, parallel
list that could double-count a failing assignment.

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

## 2026 production paper-space resolution update

Additional optional Dynamo inputs preserve the original wiring:

```text
IN[4] = resolution policy: "paper_space_dpi" (default), "fixed_pixel_width", or "both"
IN[5] = target DPI, default 150
IN[6] = fixed diagnostic pixel width, default 1600
IN[7] = optional maximum pixel dimension, default null
```

Keep fixed 1600 px for API capability diagnostics and override-failure reproduction. Use `paper_space_dpi` at 150 DPI for one production-resolution confirmation after a HOST, LINK, or DWG method succeeds. Do not treat higher DPI as a fix for linked-element override failures; API capability and raster resolution remain separate conclusions. After API behavior is resolved, include one known linked-RVT view and one known visible-DWG view in the runtime matrix.

Inactive-crop production runs use the repository's centralized `resolve_view_bounds()` path to obtain model-only bounds when available. If Revit backs off `ImageExportOptions.PixelSize`, the resolution block is recomputed from the accepted width so API capability conclusions are not mixed with stale density metadata.
