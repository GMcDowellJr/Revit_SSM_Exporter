# Stage A lattice-overrun probe

`probe_stage_a_lattice_overrun.py` measures group A of
`tools/notes/RUN_2025-02-03_to_2025-11-01_SERIES_SMALL_VIEWS.md`: the six
"(N) LEVEL x - Wall Types BP" plans that fail `annotation_lattice_mismatch` on
every archive 09-13..10-25. **Run it on the 2025-10-25 archive.**

**UNVERIFIED in Revit.** Driven end to end on the fake DB only
(`tests/test_stage_a_lattice_overrun_probe.py`). Its first run is the test of
the probe itself: check every variant's `variant_record.took_effect` and that
`restore_faults` is empty before reading anything else.

## What it runs

Each variant is one ordinary capture run of the listed views by
`capture_run.run_capture` (the thinrunner's loop, the thinrunner's config),
with one probe-only switch, `color_id_buffer_anno_extent_variant`, applied by
the registered capture INSIDE its TransactionGroup, after the model pass and
before the annotation pass. The model pass is the shipped one; the group's
rollback is the restore and is read back as on every capture.

| variant | what the annotation pass sees | question |
|---|---|---|
| `as_is` | the view as authored | the failure, measured: first-attempt annotation extent vs the model's |
| `section_markers_hidden` | annotation members of category Views (OST_Viewers) hidden | are the markers the overrun? |
| `annotation_crop_on` | `AnnotationCropActive = True` (offsets recorded) | does the annotation crop bound the export, and at what size? |
| `co_sized` | (after the fix) the shipped capture | do both passes land on one lattice under the ceiling? |

The first three run with the shipped co-sizing OFF (`color_id_buffer_co_size =
False`, probe-only) so each is measured alone. `co_sized` is offered only on a
build that has it.

## Inputs

```
IN[0]  output root (required)
IN[1]  view ids          default [4148625, 9468987, 9560929, 9594635, 9595813, 9596211]
IN[2]  variants          "all" (default) or a comma list
IN[3]  options           {"repo_root": "...", "expected_doc_title": "...",
                          "expected_config_hash": "9400eaef"}
OUT    path of <root>/probe_lattice_overrun_<clock>.json
```

Each variant's run lands in `<root>/<variant>/<date>__<run_id>_probe_<variant>/`
with ordinary sidecars and TIFFs.

## Reading it

`verdict[variant]`: `fits` / `overrun` view ids, `restore_faults`, and
`painted_delta_vs_as_is` / `content_lost` -- the annotation elements the
variant took out of the capture. Per view under `runs[variant].views[]`:
both passes' `dim_check_attempts`, the annotation's first derived axis against
the model's, the variant record (`section_markers.past_reference_ids`, the
annotation-crop offsets) and, for `co_sized`, the `co_sizing` record.

What decides the fix:

- `section_markers_hidden` fits where `as_is` overran: the markers are the
  overrun (the note's diagnosis, confirmed). It is NOT a fix by itself: the
  markers are annotation the capture keeps on purpose (`content_lost`).
- `annotation_crop_on` fits AND loses nothing inside crop A: a fix that keeps
  the lattice. Revit hides an annotation that touches the annotation crop, so
  check the in-crop pixels offline before believing `painted_delta` alone:

  ```
  python tools/register_stage_a_annotation.py <root>/<variant>/<run dir>
  python tools/decode_stage_a_color_id.py <...>
  ```
- otherwise `co_sized`: both passes fit, one lattice, nothing hidden; the
  cost is the coarsening recorded in `co_sizing.coarsening`.
