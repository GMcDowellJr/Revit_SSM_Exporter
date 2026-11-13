# Design note: seeding co-sizing from the previous run (S0, 2026-11-13)

Status: S0 findings (report only). S1 implements them in
`vop_interwoven/co_size_seed.py`.

Why: run `20261110T1151` (model as-of 2026-04-13, 1,804 views, 493 captured
min) spent `phase_co_sizing_first_attempt_ms` 15.1 min (3.1 %) on discarded
first attempts, concentrated on the largest plans. P_OVERALL - LEVEL 1 spent
142 s of 301 s in its first attempt, and the top 8 views spent 47–76 s each.

## Where last run's co-sizing lives

OBSERVED at `2c95e3d`:

- `stage_a_registered_capture.export_registered_stage_a_view` builds
  `record["co_sizing"]`: `co_sized_request(...)` plus `attempts`,
  `first_attempt` and `second_attempt`. It is set only when co-sizing
  applied, or when it was `not_applicable` and the annotation pass failed.
  Otherwise it is absent.
- `_registration_payload` writes it into BOTH sidecars as
  `registration_marks.co_sizing`, which is `None` when absent.
- It is NOT in `run_meta.json` (checked: `run_meta.view_outcomes` copies
  `capture_passes`, `skipped_pass`, `capture_pass_decision` and
  `loop_timing` only). It is NOT in `views_core` either (checked:
  `csv_export.get_stage_a_core_csv_header`).
- No reader under `tools/` or `vop_interwoven/` reads it (checked by grep for
  `co_sizing` at `2c95e3d`).

Reading it from sidecars would mean opening one per view of the previous run.
Model sidecars are 3–4 MB on the fixture views, annotation sidecars 40–330 KB,
across ~1,800 views. S1 therefore carries a compact record into each
`run_meta.views[]` entry (`co_sizing`), so the next run reads one file.

## How a later run finds the previous run

`change_skip` already resolves a baseline:
- `batch_capture.index_runs(output_root, max_depth=1)` lists the runs;
- `change_skip.series_key(doc_title)` identifies the model;
- `change_skip.series_runs(index, key, before_date)` selects.

Its selection does not fit here, for two reasons (OBSERVED):

1. `series_runs` keeps runs with an as-of date STRICTLY before this run's.
   Two back-to-back runs of one document (gate 4) share a date, so neither
   sees the other.
2. `series_key` returns None for a title with no `_YYYY-MM-DD` archive date,
   and such a document has no baseline at all. The 8-view test model is
   titled `FVOT_ BLDG 1_ Arch25_<user>_detached`, which has none.

S1 reuses `index_runs`, `series_key` and `run_meta_of`, with its own
selector (`co_size_seed.previous_capture_run`):
- the same document: the series key, else the title without `_detached`;
- finalized, not this run;
- an as-of date no later than this run's;
- the latest by (as-of date, run id). Run ids are the execution clock, so
  two runs on one day order by when they ran.

## The size key

These are the things the first attempt's request R and
`co_sized_request(...)` depend on, all readable before attempt 1:

| input | why | read from |
|---|---|---|
| `view_id` | identity | the view |
| `requested_px` (R) | the unseeded first attempt's request | `crop_a_export_geometry(view, raster, cfg)` on the caller's config (no co-sized bound) |
| `crop_px`, `crop_snapped_uv` | the crop the model pass sizes from | same |
| `view_scale` | sets feet per pixel at a dpi | `view.Scale` |
| `export_dpi` | sets R | `cfg.color_id_buffer_export_dpi` |
| `fit_direction` | which axis is fitted and which is derived | `cfg.color_id_buffer_fit_direction` |
| `max_axis_px` | the ceiling `co_sized_request` fits under | `cfg.color_id_buffer_cap_axis_px` or `MAX_STAGE_A_AXIS_PX` |
| `min_axis_px` | `co_sized_request`'s floor | the geometry record |
| `config_hash` | any config change | `view_fingerprint.portable_config_hash(streaming.run_config(cfg))` |

**Inputs the key cannot read before attempt 1.** The annotation export's drawn
extent cannot be read. It is what overruns (section lines past the crop), and
it is known only by exporting. The key is blind to annotation edits that leave
the crop, scale and config alone.

The view's change fingerprint (`view_fingerprint.py`) covers members and would
see such edits. It is not used: it is read only when fingerprints are
recorded, and VersionGuid re-mints about 0.4 % of elements on every detached
open (CLAUDE.md). A large view would almost never key-match, and large views
are where the cost is.

So the key is necessary, not sufficient. S1 adds a check after the export:
- a seeded attempt is CONFIRMED only when its model export and annotation
  export measure exactly what last run's co-sized attempt measured;
- otherwise it is REFUTED, and one more attempt runs.

## What a refuted seed costs

A refuted seed costs one more attempt and never a third, the same as an
unseeded co-sized view today. The retry's request comes from what the seeded
attempt measured (`co_size_seed.refuted_request`). The drawn extent is fixed
in feet, so at the model's request R its derived axis is below
`(D_s + 1) * R / s`. That bound pins the unseeded `D_R` to within a pixel, but
not exactly.

Measured over 112,776 generated wrong seeds, with an extent that scales with
the request:
- 78 % of retries land on the unseeded `fit_px`;
- the rest land 1–2 px below it;
- none land above it, and none go over the ceiling.

A refuted seed therefore does not always reproduce the unseeded capture. It
can be up to 2 px coarser on the fitted axis. It happens only when the size
key matched but the drawing changed.
