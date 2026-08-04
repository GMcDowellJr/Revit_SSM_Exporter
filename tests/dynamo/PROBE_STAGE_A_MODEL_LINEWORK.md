# Stage A model-linework TIFF probe

`probe_stage_a_model_linework.py` is a standalone Revit 2025 / Dynamo 3.3
CPython3 probe that tests whether a second Revit-rendered TIFF can recover
visible model linework — including silhouettes, inter-element boundaries, and
internal same-element edges — without extracting solids, enumerating faces,
creating DirectShapes, or changing production Stage A code.

## Dynamo wiring

```text
IN[0] = target model-capable view
IN[1] = output directory
IN[2] = optional test element or list of elements for focused analysis
IN[3] = rendering modes, default "all"
```

`IN[2]` is recorded for focused manual review only. Standard modes do not
isolate those elements, because isolation would change view semantics and is not
part of this feasibility test.

## Rendering modes

The probe runs these modes independently from the same original view state:

1. `hidden_line_original`
2. `hidden_line_white_fill_black_lines`
3. `flat_colors_white_fill_black_lines`
4. `wireframe_black_lines`
5. `current_view_model_only_reference`

You can run one mode or a comma-separated list through `IN[3]`; use `all` for
the complete matrix.

Every mode uses the same current crop behavior, horizontal pixel size, TIFF
format, and annotation-category hiding. Each mode records the exact settings it
attempted to apply. Before the linework modes, the probe generates a separate
rolled-back element-ID reference TIFF using Stage A-style palette painting only
for dimensional comparison; this reference is not a linework candidate.

## Transaction safety

The probe follows the proven transaction-group rollback harness:

1. Close Dynamo's ambient `TransactionManager` transaction immediately before
   starting each `TransactionGroup`.
2. Start one `TransactionGroup` per rendering mode.
3. Apply temporary graphics in a committed child transaction.
4. Export with no child transaction open.
5. Roll back the enclosing `TransactionGroup` in `finally`.
6. Capture before/after state and fail the mode if rollback fails or state
   differs.

This is diagnostic only. The production linework flag must remain disabled by
default regardless of a successful sample.

## Annotation-category hiding

`_hide_annotation_categories()` mirrors `color_id_buffer.py`'s
`_hidden_category_state()` category-selection logic (every top-level
`CategoryType.Annotation` category plus the `OST_DetailComponents` /
`OST_Lines` view-only-model exceptions), but a per-view template locks
`CanCategoryBeHidden()`/`GetCategoryHidden()` to report against committed
document state. Every mode therefore commits the view-template detach in its
own transaction, before the main settings transaction runs
`_hide_annotation_categories()` — the same two-step ordering
`color_id_buffer.py` uses (`detach_tx` committed, then `suppress_tx`). Each
candidate category is traced regardless of outcome (`name`, `category_type`,
`can_category_be_hidden`, `was_hidden_before`, `hide_applied`) under
`settings_applied.annotation_category_hide_trace`, so an empty
`annotation_categories_hidden` list is diagnosable from the JSON alone.

## Graphic policy under test

The intended analytical linework image has:

- known white background
- known dark line color
- no surface or cut fills where possible
- no shadows, ambient occlusion, sketchy lines, depth cueing, or anti-aliasing
- annotation and view-only content hidden
- model visibility and occlusion preserved

The probe does **not** use geometry extraction, face enumeration, material
painting, temporary DirectShapes, or replacement geometry. It changes only view
and category graphics inside rolled-back transactions.

## Mode intent

- `hidden_line_original`: Hidden Line display with annotation/view-only content
  hidden and display effects disabled, but no category line/fill overrides.
- `hidden_line_white_fill_black_lines`: Hidden Line display plus black category
  cut/projection lines and white category surface/cut fills.
- `flat_colors_white_fill_black_lines`: FlatColors display plus black category
  cut/projection lines and white category surface/cut fills.
- `wireframe_black_lines`: Wireframe display plus black category cut/projection
  lines and no explicit fill override.
- `current_view_model_only_reference`: Keeps the current display style, hides
  annotation/view-only content, disables display effects, and exports a
  same-crop/same-pixel-size reference.

## Output artifacts

The probe writes outputs under:

```text
<IN[1]>\model_linework_probe\
```

For the generated element-ID dimensional reference it writes:

```text
<view>_<id>.model_linework.element_id_reference_1.tiff
<view>_<id>.model_linework.element_id_reference_2.tiff
```

For each linework mode it writes two sequential TIFFs:

```text
<view>_<id>.model_linework.<mode>.linework_1.tiff
<view>_<id>.model_linework.<mode>.linework_2.tiff
```

It writes one combined JSON:

```text
<view>_<id>.model_linework.json
```

When Pillow is available and dimensions match, it also writes difference images
between the first mode and each later mode:

```text
<view>_<id>.model_linework.<mode>_minus_<first_mode>.diff.tiff
```

The Dynamo `OUT` object contains the same combined report plus paths to TIFFs,
difference images, and JSON.

## Image analysis

For every exported TIFF, the probe records:

- dimensions
- SHA-256
- file size
- dark-line pixel count
- grayscale non-background pixel count
- non-dark gray foreground pixel count, used as fill/shading/hatch contamination evidence
- unexpected-color pixel count
- near-white/white background pixel count
- non-background content rectangle
- connected-component count and largest component sizes when the image is small
  enough for inexpensive analysis
- sequential-export repeatability by SHA-256 and dimensions
- dimensional agreement with the generated element-ID reference TIFF

If Pillow is unavailable, the probe still records file size and SHA-256 but marks
pixel analysis inconclusive. If the element-ID reference export fails or produces
no dimensions, the overall report is `FAIL` or `INCONCLUSIVE` even if individual
linework mode exports succeed, because dimension alignment cannot be validated.

## Required conclusions

For each mode the combined JSON includes fields for:

- internal edges: `present` / `absent` / `uncertain`
- hidden/back edges: `present` / `absent` / `uncertain`
- fill contamination: `acceptable` / `unacceptable`; non-dark gray foreground above tolerance rejects candidate status even when pixels are grayscale
- link behavior: `supported` / `unresolved`
- DWG behavior: `supported` / `unresolved`
- dimension alignment: `pass` / `fail`
- candidate status: `recommended` / `rejected` / `inconclusive`

Automatic image analysis cannot prove whether a dark line is a correct internal
edge or a hidden/back edge. The script therefore leaves internal-edge and
hidden/back-edge classification as `uncertain` until manual review confirms the
representative conditions below.

## Manual review checklist

For each mode, inspect the TIFF alongside a screenshot of the same Revit view and
answer:

1. Are visible model silhouettes present?
2. Are inter-element boundaries present where two visible model elements touch?
3. Are internal same-element edges present, such as wall openings, curtain grids,
   panel seams, stair subcomponents, family edges, floor/roof boundaries, or DWG
   linework inside an import?
4. Are hidden or back edges visible? If yes, identify the object and mode.
5. Are cut edges and projection edges both visible where expected?
6. Are fills, material patterns, surface patterns, shadows, depth cueing, sketchy
   lines, anti-aliasing, or annotations contaminating the image?
7. Do linked RVT elements and DWG imports show expected visible linework?
8. Do both sequential TIFFs have identical dimensions and hashes?
9. Do linework TIFF dimensions match the generated element-ID reference TIFF?

Return marked-up screenshots or notes that identify the representative
conditions being evaluated. The JSON metrics are necessary but not sufficient for
accepting a linework configuration.

## Representative content matrix

Run the probe on views containing as many of these as possible:

- wall with opening
- curtain wall
- floor or roof edge
- stair
- mechanical family
- linked RVT content
- DWG import

Also run across the normal Stage A view matrix where practical: floor plan,
reflected ceiling plan, section, elevation, detail view, templated view, and a
view with filters/phase settings that affect visibility.

## Evidence to return

For each run, return:

- the combined `*.model_linework.json`
- all `*.linework_1.tiff` and `*.linework_2.tiff` files
- all generated `*.diff.tiff` files
- Dynamo `OUT` copied as text if possible
- screenshots or notes completing the manual review checklist
- the view name, id, view type, template/filter/phase context, and which
  representative content was present
