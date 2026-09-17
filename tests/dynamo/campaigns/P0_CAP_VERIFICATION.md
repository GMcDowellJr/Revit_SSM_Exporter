# p0-cap-verification

Two Revit captures that close the P0 export-correctness checkpoint. There is
no new probe and no new comparator: both jobs run `stage_a_drift_onset`,
which already captures through production (`init_view_raster` +
`collect_view_elements` + `export_color_id_buffer_view`) and already runs in
this environment. A purpose-built P0 probe was written, failed four times on
its own plumbing, produced no measurement, and was deleted.

## What is already answered, and by what

The P0 production change is proven offline (the suite under `tests/`) and
partly proven in Revit already. Run `1b113822` — the `b3-underlay-hosp-2`
job of `stage-a-run2-threshold-and-remedy` — exercised the shipped path on a
real 8584x9900 export of `(N) HOSPITAL - LEVEL 2`:

| field | value |
|---|---|
| `dim_check` | `pass`, on the first attempt |
| fitted axis | requested 8584 -> `actual_w` 8584 |
| derived axis | `predicted_derived_px` 9900 -> `actual_h` 9900, zero error |
| `backoff_floor_px` | 250, the grid's W under horizontal fit |
| `dim_check_ceiling_px` | 10000 |
| `applied_smooth_edges` | `False` |

That capture ran at `export_dpi` 147.33 and did NOT bind the cap
(`cap_applied: false`). So what it leaves open is the capped path.

## What these two jobs add

Both capture at `native`, i.e. production's own DPI, so the cap decides the
size rather than the probe.

- **`p0-cap-fitted-axis-mob-1`** — `MOB 1 - LEVEL 2` (528698). Run 1 drove
  this view to 15000x12356 against the old ceiling, so its native request is
  well over 10000 and the cap binds on the axis `PixelSize` sets.

- **`p0-cap-derived-axis-hosp-2`** — `(N) HOSPITAL - LEVEL 2` (871863). Its
  native request at 150 DPI is 8739x10079 (the exact shape Run 1 recorded as
  drifted), so the cap binds on the axis `PixelSize` does NOT set. This is
  the case a fitted-axis-only cap waves through, and it is nameable from
  measured data — no human has to pick a candidate view.

## Acceptance

Run `python tools/analyze_stage_a_probe.py --export-metrics <capture dir>`
and read the table. For each capture:

| check | where | expected |
|---|---|---|
| longer axis within the limit | `W`, `H` | `max(W, H) <= 10000` |
| the export is the size requested | sidecar `resolution.dim_check` | `pass` |
| the cap actually bound | sidecar `resolution.cap_applied` | `true` |
| the derived axis was predicted correctly | `resolution.predicted_derived_px` vs the non-fitted actual | within 1 px |
| edges are hard | `hard_ratio` | `1.0`, **with `hard_edges` > 0** |
| no off-palette contamination | `off_px` | `0` |
| AA state was established | sidecar `applied_smooth_edges` | `false`, never `read_failed` |

`hard_ratio` is `hard / (hard + blended)` and is **null when no edges were
counted at all**. The table prints null as `NOT-MEASURED` precisely so it
cannot be read as "no data yet" when the real state is "nothing was
measured". A row showing `NOT-MEASURED` has not passed; it has not been
measured, and `hard_edges` is the field that tells the two apart.

## Not covered here

The mismatch backoff firing on a deliberately over-ceiling request. That
path is proven offline (`tests/test_stage_a_export_dimension_contract.py`),
and forcing it in Revit needs a per-capture cap override that
`stage_a_drift_onset` does not expose. Adding one means changing a probe
that works; that trade has not been made.
