# Stage A color-ID buffer anomalies — drift onset (D) and 0.67 white blend (B)

Diagnostic instrumentation for two anomalies observed in the 2026-09-15 Stage A
run (54 views, no links loaded). **This is an investigation, not a fix.** Nothing
here changes palette generation, export defaults, the paint step, or any
analysis-side snapping or recovery, and nothing here certifies a result.

Everything below is either (a) instrumentation to be run by Greg in Revit, or
(b) a hypothesis stated against the evidence that would confirm or contradict
it. **No experiment in this document has been run.** Every per-experiment metric
table in this repository is empty until a run fills it.

## What was added

| Piece | Purpose |
|---|---|
| `tests/dynamo/probe_stage_a_drift_onset.py` | Extraction for D1–D5 |
| `tests/dynamo/probe_stage_a_white_blend.py` | Extraction for B1 and B3 |
| `tools/analyze_stage_a_probe.py --export-metrics` | All pixel analysis, incl. B2 |
| `tests/test_stage_a_export_metrics.py` | 28 tests for the metric set |
| `tests/test_stage_a_drift_onset_probe.py` | 29 tests for the D-probe planners |
| `tests/test_stage_a_white_blend_probe.py` | 22 tests for the B-probe helpers |

Both probes are registered in `revit_probe_registry.py` and go through the
generic passthrough adapter. They are deliberately **absent** from the
analyzer's `SUPPORTED_PROBES` campaign-acceptance map: they emit metrics for a
human to read, not an acceptance verdict.

## How a capture is produced

Every capture is produced by **calling production's own Stage A path**:

```
pipeline.init_view_raster
  -> revit.collection.collect_view_elements
    -> color_id_buffer.export_color_id_buffer_view
```

which is exactly what `pipeline.py:1099-1113` does. Bounds resolution, the
suppression set and its ordering, the render crop, the palette step, the paint
step, the link-category filters and the export options are all production's, so
a probe capture cannot diverge from a production one by construction. An
experiment varies exactly one input and then calls it:

| Experiment | The one input it varies |
|---|---|
| D1 | nothing — the same call twice |
| D2 | category visibility (crop pinned first, so size and density stay fixed) |
| D3 | `cfg.color_id_buffer_export_dpi`, back-solved to the target width |
| D4 | the same, lowered until native fits the ceiling |
| D5 | the view's `CropBox`, one tile at a time |
| B3 | the underlay range |

Sizing goes through DPI rather than writing `PixelSize` directly, so
production's own `round(export_dpi * paper_width_in)` and its
`MAX_STAGE_A_PIXEL_SIZE` clamp both stay on the code path — that clamp is
itself one of the things D3 and D4 are meant to characterize, so bypassing it
would defeat the experiment. Each capture's TIFF and sidecar are moved to a
case/label name; the moved **sidecar is production's own**, and is a valid
standalone input to `--export-metrics`.

### Why this shape

Earlier revisions rebuilt the capture sequence by hand and diverged from it
three times in review — wrong palette step, wrong suppression set, wrong
ordering, no render crop — each silently invalidating the measurements the
probe exists to take. Tests now assert structurally that the hand-mirrored
machinery has not come back: the probe must reference `init_view_raster`,
`collect_view_elements` and `export_color_id_buffer_view`, and must not
reference `SetElementOverrides`, `ImageExportOptions`, `PixelSize`,
`build_palette` or `_build_flat_color_ogs`.

Production's own `Diagnostics` are captured and returned with every run. A
clamped pixel size, a read-only phase filter, a crop that would not apply, a
display style that could not be set — each changes how a capture must be read,
and each is recorded rather than inferred.

## Architecture boundaries honoured

- Dynamo side does extraction only: transaction, capture, sidecar JSON. No
  probe reads a pixel.
- All pixel analysis is in `tools/analyze_stage_a_probe.py` (Pillow + NumPy),
  outside Dynamo.
- Existing files were patched, never rewritten.
- Nothing in the Stage A pipeline changed: palette generation, the paint step
  and the export defaults are *called*, not reimplemented and not modified.
- Every mutation is inside one TransactionGroup that is always rolled back,
  with a pre/post state diff so a failed rollback is visible rather than
  assumed.

## Required per-export metrics

`analyze_stage_a_probe.py --export-metrics` emits all of these per export, and
`--metrics-table` renders them as one markdown table:

| Metric | Where |
|---|---|
| `W`, `H`, `requested_pixel_size`, `accepted_pixel_size` | `resolution` |
| `native_px` = `extent_ft * dpi * 12 / view_scale`, `scale_factor` | `resolution` |
| `hard_edge_count`, `blended_edge_count`, `hard_edge_ratio` | `edges` |
| `off_palette_px`, distinct off-palette colors | `pixels` |
| overshoot rate + normalized magnitude percentiles (sampled) | `transitions.overshoot` |
| transition-width histogram and percentiles | `transitions` |
| out-of-bbox assigned px, with the 10:1 frame correction | `pixels`, `frame` |
| `fraction_3x3_solid` | `solidity` |
| pastel element count and unblended alpha distribution | `white_blend` |

Definitions that matter for reading the numbers:

- **hard edge** — an adjacent pixel pair where one pixel is *exactly* a palette
  color and the other is *exactly* white. `hard_edge_ratio` is
  `hard / (hard + blended)`.
- **transition width** — the run length of consecutive off-palette pixels
  across a boundary. A hard edge has width 0; the baseline's drifted views
  show ≥3.

  Runs are scanned along **both axes** and pooled. A boundary's transition
  runs perpendicular to it, so a row-only scan measures a horizontal edge
  along its length instead of across its width: three blended rows spanning
  the canvas come back as runs the width of the image, are then discarded as
  regions, and yield no overshoot samples at all. Plans are full of
  horizontal walls and linework, so a one-axis metric would depend on which
  way the drawing happens to be oriented. `row_runs` / `column_runs` and
  `row_samples` / `column_samples` report the split; a horizontal and a
  vertical edge of the same width now measure identically.
- **overshoot** — for a run anchored by two *different* exact colors A and B,
  the largest per-channel excursion *beyond* `[min(A,B), max(A,B)]`, normalized
  by the transition's overall endpoint separation. A box or bilinear resample
  cannot produce any; a negative-lobe kernel or a sharpening pass can. White
  anchors clip at 255, so the measurable side is almost always the darker
  endpoint.

  Two populations are excluded from the rate and counted separately, because
  including either reports a hard-edged capture as resampled:
  `degenerate_runs` (anchors the same colour on both sides — a region
  interior, not a transition, which is exactly what a composited element looks
  like) and `wide_region_runs` (wider than `max_width_scored_px`, default 32 —
  resampling spreads an edge over a kernel's support, a handful of pixels, not
  hundreds). Read `overshoot_rate` together with `n_trans`: a rate over four
  transitions is noise.
- **10:1 frame correction** — Revit clamps export aspect to 10:1 by padding the
  short axis, and `bounds_xy` does not reflect that padding, so out-of-bbox
  counts are taken against the corrected content rectangle.
  `frame.clamp_matches_actual_height` reports whether that correction actually
  predicted the exported height — if it is `false`, the clamp model is wrong for
  that capture and the out-of-bbox number should not be trusted.
- **`fraction_3x3_solid`** — the share of palette pixels whose entire 3×3
  neighbourhood is the same exact color.

- **`matched_color_count` vs `composited_color_count`** — solving the alpha is
  not sufficient on its own. A colour-to-white edge produced by resampling
  lands *exactly* on the alpha ray from the palette colour to white, so it
  unblends just as cleanly as a deliberately composited element does; a
  Lanczos-resampled synthetic scene produces perfect unblend matches and zero
  real pastels. Every candidate colour in the ranked window is classified —
  capping the profiled set at the first N *matches* let that residue, which is
  exactly what ranks high on a drifted export, fill the quota before a real
  composited element was ever examined, reporting zero pastels on a capture
  that plainly has one. `candidate_colors_considered` and
  `candidate_window_truncated` say how wide the window was;
  `matched_color_count` counts every colour that solves;
  **`composited_color_count` is the "pastel element count"** — it counts only
  matches that also have a solid interior (`solid_3x3_fraction ≥ 0.5`), and it
  is what the `pastel_colors` table column reports. Composited matches also
  carry `neighbor_palette_rgb`, which answers B2 directly: a pastel that
  unblends against white while sitting next to another element's colour has
  white as its blend target, not that element. That scan is one image pass per
  colour, so it is the one bounded stage: it is spent on the composited
  regions only, and `neighbor_scan_truncated` says when there were more than
  the budget. The reported `matches` list is capped for payload size
  (`matches_truncated`), with composited regions kept first — every count
  above is computed over the full candidate set, before that trim.

- **reading `hard_edge_ratio`** — a composited element lowers it without any
  resampling at all, because its pastel pixels are off-palette and so every
  edge it forms with white counts as blended. In the synthetic check a
  hard-edged pastel capture scores 0.47. `hard_edge_ratio` is only evidence of
  drift when read together with the transition-width distribution and the
  overshoot population counts.

### Validated against synthetic archetypes

The metric set was checked end to end on five constructed captures whose ground
truth is known (this is a check of the *instrumentation*, not of any Revit
behaviour):

| Capture | hard_ratio | overshoot (n) | trans p50 | pastel |
|---|---|---|---|---|
| hard-edged | 1.00 | — (0) | — | 0 |
| Lanczos resample | 0.00 | 0.997 (971) | 2 | 0 |
| bilinear resample | 0.00 | 0.005 (1041) | 3 | 0 |
| composited at α=0.67 | 0.47 | 1.00 (4) | 320 | 2 @ 0.67 |
| both at once | 0.00 | 0.995 (571) | 2 | 2 @ 0.67 |

The two resampled rows are the important pair: both are unambiguously
resampled, and only the negative-lobe kernel rings. The last row confirms the
two anomalies are detected independently when a single capture has both, which
matters because a SITE PLAN view could exhibit both at once.

Memory note: a 15000×12356 export is ~556 MB as RGB. The scan is stripe-wise
with a one-row halo (`_METRIC_STRIPE_ROWS`, default 512) and is verified
stripe-invariant by test, but the decoded image itself is held whole.

## Running it

The experiment set is fixed, so it is **checked in as a campaign** rather than
hand-wired per run: `tests/dynamo/campaigns/stage_a_color_id_anomalies.json`.
One Dynamo node, one input, no per-run choices.

```
paste tests/dynamo/revit_batch_dynamo.py
  IN[0] = ...\tests\dynamo\campaigns\stage_a_color_id_anomalies.json
  IN[1] = null                       # manifest root; same value EVERY run
  IN[2] = true                       # dry run: resolves every view, validates
                                     # every setting, exports nothing
  IN[3] = D:\vop_probe               # artifact root -- REQUIRED, see below
```

**`IN[3]` is not optional in practice.** The campaign's `output_directory`
values are relative, as a checked-in file's must be: an absolute path would be
wrong on every machine but the author's. Relative paths resolve against the
directory holding the campaign file, which here is inside the repository — so
without an artifact root, captures are written into the checkout. A Stage A
TIFF can reach several hundred megabytes. `.gitignore` covers the paths as a
safety net, but supplying `IN[3]` is the actual answer. Point it at a scratch
volume with room for tens of gigabytes.

Set `document.expected_title` to the exact model title before the first run —
it is a deliberate `REPLACE-…` placeholder so that running against the wrong
model fails the title check instead of producing plausible captures of
something else. Then set `IN[2] = false` and run the node once: the campaign
executes every job in a single invocation.

A dry run that passed reports `execution_status: "validation_only"` with every
job in `jobs_validated`. A run that failed configuration reports
`execution_status: "configuration_failed"` and a `configuration_error` naming
the reason — empty job lists alone do not distinguish the two, which is why
the status is in the summary and not only in the manifest.

### Pacing, resume, and where the manifest goes

`max_jobs_per_run` is 100, comfortably above the ten jobs, so one run does the
lot. Set it to 1 to step through a job at a time instead — worth doing on a
machine where disk is tight, since D2, D3 and D5 each write several captures
and a Stage A TIFF can reach ~550 MB.

`resume` is true and `on_job_error` is `continue`, which together make a long
unattended run recoverable: a probe that fails is recorded and the campaign
carries on, and if Revit or Dynamo dies partway the next run picks up from the
last completed job instead of repeating what is already done.

**Resume only works if `IN[1]` is the same every time.** It finds prior runs by
walking the manifest root for manifests with a matching campaign and batch id,
so a manifest root that changes between runs looks like a campaign that has
never run, and everything is captured again. Leave `IN[1]` null (the root is
then derived from the campaign file's own directory) or pass the same explicit
path every time — but do not alternate.

Resume also refuses to cross documents: a prior run of the same campaign
against a different model raises rather than silently mixing captures.

What this removes, all of which went wrong on the first hand-run attempt:

| | Hand-wired | Campaign |
|---|---|---|
| View choice | picked in Dynamo per run | resolved from the manifest by element id |
| Two views named "SEA LEVEL" | picked by eye | `resolve_view` refuses an ambiguous name |
| Wrong model open | discovered from the output | `expected_title` fails first |
| Case names | typed per run | validated by the dry run, against the probes' own parsers |
| Output directory | one shared folder; captures collided across views | one per job |
| Knowing what has run | remembered | `resume` plus a run manifest |

### Forcing a fresh run

`resume` skips jobs a prior run completed, which is what makes an interrupted
run cheap to continue. To deliberately re-capture everything, set:

```json
"execution_policy": { "max_jobs_per_run": 100, "on_job_error": "continue",
                      "resume": true, "allow_rerun": true }
```

**Moving the artifact root is not something you have to force.** A prior
success only counts as "already done" if it landed in the directory this run
writes to, so adding, changing, or dropping `IN[3]`/`artifact_root` re-runs the
affected jobs by itself and records a `resume` warning naming the old and new
destinations. The same applies to a prior manifest that never recorded where
it wrote: unknown counts as moved, because skipping on it risks a run that
writes nothing and calls itself successful, while re-running costs one
capture. The destination is checked *before* the configuration-drift guard, so
editing a job's settings at the same time as its output root re-runs the job
rather than failing the campaign. A run in which every job was skipped reports
`execution_status: "nothing_to_do"` with a `nothing_executed` line in the
summary, rather than `completed` — a run that wrote nothing no longer reads
like a successful one.

`allow_rerun` is the right knob, not `resume: false`. Both re-execute every
job, but `resume: false` skips the prior-run lookup entirely and with it the
check that refuses to continue a campaign whose earlier run was against a
**different document** — exactly the guard worth keeping when deliberately
re-capturing. Changing `batch_id` also works and keeps the previous run's
results attributable to the old id, which is worth doing when the two runs
should be comparable rather than one replacing the other.

### Artifact names

Every artifact a probe writes — each capture TIFF, its sidecar, and the run's
aggregate JSON report — is named:

```
<job>.<view id>.<case>.<label>.tiff     d1-hospital-level-2.871863.d1_determinism.rep0.tiff
<job>.<view id>.<view name>.<probe>.json   b3-site-plan-4.587278.SITE_PLAN_AT_LEVEL_4.white_blend.json
```

`<job>` is the basename of the run's output directory, which the batch
executor names after the job id. It is there because view id and case are not
enough: four D jobs target 871863 and two B jobs target 587278, so
`SITE_PLAN_AT_LEVEL_4.587278.white_blend.json` named both B jobs' reports.
Per-job directories only keep those apart while the files stay in them, and
they do not — captures get pooled for analysis, copied off the Revit host, and
attached to a message, and then the earlier one is what gets overwritten. A
run given no output directory gets no token rather than being stamped with the
process working directory's name.

**Delete the old capture directories as well.** None of these flags remove
anything: a re-run overwrites a capture of the same name, but a job that fails
or is skipped leaves the *previous* run's file in place, looking current.
Every capture now records `captured_at`, and the analyzer carries it into
`image.captured_at`, so a leftover can be spotted without trusting a file
modification time that copying resets — but deleting is still the reliable
answer:

```
rmdir /s /q D:\vop_probe\captures
```

Set `allow_rerun` back to false (or drop it) afterwards, or every later run
re-captures the lot.

### Analysing what it captured

The campaign gives every job its own directory, so one command covers the whole
run:

```bash
python tools/analyze_stage_a_probe.py "D:/vop_probe/captures" \
    --export-metrics --metrics-table drift_table.md
```

That walks every job directory, measures each capture once, and writes one
table plus a `.metrics.json` beside each sidecar. Both probes' output is
included; split them if you prefer two tables:

```bash
python tools/analyze_stage_a_probe.py "D:/vop_probe/captures" \
    --export-metrics --metrics-table drift_table.md
python tools/analyze_stage_a_probe.py "D:/vop_probe/captures/b1-site-plan-4" \
    "D:/vop_probe/captures/b3-site-plan-4" \
    --export-metrics --metrics-table blend_table.md
```

`--metrics-table` is written relative to the current directory, not to the
captures path. Pass `-` to print it instead.

Each job directory also holds the probe's own report — `*.drift_onset.json` or
`*.white_blend.json` — at its top level. Those carry what the table cannot:
production's `Diagnostics`, `state.restored`, the B1 read-only findings, and
each B3 variant's `variant_status`. Read them alongside the table, not instead
of it.

### Running a single experiment by hand

Still supported, and documented in each probe's module docstring — the
campaign calls the same `run_probe`. Prefer the campaign: every failure in the
table above came from the hand path.



```bash
# Dynamo node (drift block)
#   IN[0] view, IN[1] output dir, IN[2] "all" or a case subset,
#   IN[3] repetitions, IN[4] pixel sizes, IN[5] dpi, IN[6] D2 steps,
#   IN[7] tile grid, IN[8] max elements, IN[9] repo root
OUT = dynamo_main(IN)

# Analysis (outside Dynamo)
python tools/analyze_stage_a_probe.py \
    ~/Documents/_metrics/drift_onset_probe \
    --export-metrics --metrics-table drift_table.md
```

## Experiments — drift

| Case | What it varies | What it holds fixed |
|---|---|---|
| `d1_determinism` | nothing — N repeat exports | everything |
| `d2_category_load` | visible model categories, one bucket at a time | pixel size, crop, view |
| `d3_size_sweep` | requested pixel size | content |
| `d4_dpi_vs_pixel_size` | DPI lowered until native ≤ 15000, then native requested exactly | view, content, crop |
| `d5_crop_tiles` | crop rectangle: N×N tiles cut on the native pixel lattice | density |

D2 hides every hideable model category first (step 0 is the zero-content
control) then unhides buckets ordered by descending painted-element count, so
load rises monotonically. The counts come from **production's own collected
element set** for this view (`init_view_raster` → `collect_view_elements`),
taken after the crop is pinned, and a category this view draws nothing from is
left out of the sweep entirely. Enumerating `doc.Settings.Categories` instead
sweeps every category the *document* defines in category-id order, which lets
empty categories consume buckets and puts the category carrying the load in an
arbitrary late step — the eight-step sweep then cannot localize the onset.
Each step records `revealed_element_count`, so the sweep is read against drawn
content rather than category count. A loaded category the view refuses to hide
is reported under `always_visible_categories` and is the non-zero floor that
step 0 starts from. D5's interior seams are snapped to whole native
pixels; `seam_residual_px` reports any that are not, so a misaligned seam is
visible rather than confused with resampling.

D6 (hardware acceleration off) is manual and stays with Greg — no API for it is
confirmed to exist. See UNCONFIRMED below.

## Experiments — white blend

`b1_query` is strictly read-only **and runs before any mutation**, outside the
TransactionGroup entirely. Ordering matters: the paint step writes an
`OverrideGraphicSettings` with `Halftone` off and `SurfaceTransparency` 0 onto
every painted element, so a B1 pass taken after painting would read its own
overrides back and report "no element halftone anywhere" for every document —
making the halftone gate structurally incapable of ever firing. B1 reports the
view's *authored* state: underlay configuration, the phase filter and its
per-status presentation, and per-element level, overrides, category overrides,
design option, and phase created/demolished.

`b3_baseline` is the production-normalized comparison export.
`b3_underlay_off` and `b3_halftone_cleared` each run **only if B1 found that
mechanism configured on this view**, and record why they were skipped
otherwise.

**A gated variant therefore needs `b1_query` in its own selection.** B1's
finding lives in a local of the run that took it and does not cross job
boundaries, so `selection: "b3_baseline,b3_underlay_off"` skips the underlay
export — and the baseline alone is enough for the job to be reported
completed, so `resume` never retries it. `select_cases` refuses such a
selection, which the campaign dry run catches before the model is opened. The
campaign's `b3-site-plan-4` job runs B1 itself; the separate `b1-site-plan-4`
job stays because it is the read-only phase gate that decides whether the
expensive exports are worth taking at all. Each variant restores its own mutation before the next one runs —
every mutation commits into the enclosing TransactionGroup, so without an
explicit restore the underlay would stay off through the halftone export and
that TIFF would carry two changes at once, which is exactly what makes a
one-mechanism-at-a-time experiment unattributable.

`b3_halftone_cleared` additionally skips itself as **redundant** when the
probe's own production normalization already neutralized category halftone
(and the paint step already cleared element halftone). That is not an
evasion — it is the answer to B-H1 established without spending a
full-resolution TIFF: if production's own capture setup already clears
halftone at both levels, halftone cannot be what survives into a production
capture.

B2 is entirely analyzer-side: `white_blend.matches` unblends each off-palette
color against the palette over white and reports the solved alpha, and
`fraction_3x3_solid` distinguishes a composited element from a resampled edge.

## Run 1 — 2026-09-16, KSRF_Hosp_Interior_AR_V03_33 MB_2025-02-03

Revit 2025 build 25.4.41.14, Dynamo CPython3, no links loaded. 21 captures over
nine jobs. `drift_table.md` is the measured record; what follows is read off it.

### Drift: the onset is the exported HEIGHT, at ~10,000 px

Every capture in the run falls on one side of a single line, and it is not the
one the baseline proposed:

| exported height | captures | `hard_edge_ratio` |
|---|---|---|
| 5038, 5039, 5535, 7735, 9071, 9575, 9642, 9927 | 8 | **1.0** (no blended edge at all) |
| 10079, 11570, 12356, 14463 | 4 | **0.0** (no hard edge at all) |

Width does not predict it: 15000×9927 is perfectly hard-edged while 8739×10079
is fully blurred. Neither does total pixel count (148.9 Mpx clean, 88.1 Mpx
drifted), nor the requested-to-native scale factor (1.001 drifts; 0.73 does
not). The threshold lies in **(9927, 10079]**.

D3 is the controlled demonstration — one view, one content set, crop pinned,
size driven through DPI so requested ≈ native throughout:

| `(N) HOSPITAL - LEVEL 3` (929475) | W×H | scale | hard ratio | overshoot | width p50 |
|---|---|---|---|---|---|
| native | 8023×7735 | 1.001 | 1.0 | — | — |
| 10000 px | 10000×9642 | 1.001 | 1.0 | — | — |
| 12000 px | 12000×11570 | 1.001 | **0.0** | 0.249 | 3 |
| 15000 px | 15000×14463 | 1.001 | **0.0** | 0.497 | 4 |

and `(N) HOSPITAL - LEVEL 2` (871863) crosses the same line from the other
side: native 8739×10079 drifts, 0.95× (8302×9575) and 0.9× (7865×9071) are
clean.

Overshoot rises monotonically with how far the height exceeds the threshold
(0.114 at 10079, 0.249 at 11570, 0.497 at 14463), which is what an upscale from
a fixed internal height produces and what a renderer-wide quality switch does
not. The reading this supports: **Revit rasterizes at a capped height of about
10,000 px and resamples to the requested height.** At 10079 that is a 1.008×
upscale, which leaves almost no output pixel exactly aligned — hence a
`hard_edge_ratio` of 0 from a resample of barely one percent.

D5 confirms the consequence is avoidable: the same view cut into 2×2 crop
tiles, each 5038–10px tall, gives `hard_edge_ratio` 1.0 on all four tiles.

### What the other D experiments said

- **D1** — rep0 and rep1 are **byte-identical** (same SHA-256) on all three
  views. The export is deterministic; drift is not a race.
- **D4** — did not test anything. `D4_NATIVE_CEILING` is 15000, taken from
  production's `MAX_STAGE_A_PIXEL_SIZE`, so no DPI was lowered and the capture
  is a byte-for-byte duplicate of D1's. The question it was asked is
  nonetheless answered, by D3's sub-native sweep: lowering DPI until the height
  clears the threshold does give hard edges. The constant needs to be the
  height threshold, not the width ceiling.
- **D2** — **invalid, see below**, and **no longer needed**. It swept one
  category (`Point Clouds`) with zero painted elements and wrote two identical
  blank captures. Its question was whether drift depends on scene load; D3
  answered that at fixed load, and the baseline's `SEA LEVEL_49370`
  counter-example dissolves once the axis is read as height rather than
  longest. There is nothing left for a load sweep to decide.

### 0.67 white blend: it is the underlay

B1 on `SITE PLAN AT LEVEL 4` (587278), read before any mutation:
`underlay_configured: true`, base level `(E) HOSPITAL - LEVEL 4`, range
covering three levels, and **164 of 572 candidate elements sit on an underlay
level**. No element halftone, no category halftone, no surface transparency
anywhere in the candidate set.

B3 turned the underlay off with `SetUnderlayRange` — one mechanism, crop
pinned to the baseline, restored afterwards, transaction group rolled back and
state verified restored:

| | painted elements | off-palette px | hard | blended | `hard_edge_ratio` |
|---|---|---|---|---|---|
| `b3_baseline` | 658 | 24,632 | 65,653 | 66,596 | 0.496 |
| `b3_underlay_off` | 381 | **0** | 67,098 | **0** | **1.0** |

Every off-palette pixel in that view is the underlay's. 277 of the 658 painted
elements — 42% — are underlay elements: Stage A paints them with their colour
IDs, and Revit then draws them in the underlay's greyed presentation, so they
reach the TIFF as a blend of their own palette colour toward white and never
appear in pure colour. That is the baseline observation, exactly.

`pastel_colors` reads 0 on the baseline because the underlay reaches this
capture as thin linework, which has no solid 3×3 interior — the discriminator
that keeps resample residue from being counted as a composited element also
excludes blended linework. The `blend_colors` / `blend_alpha_p50` columns were
added for this, and they settle the alpha:

**All 120 off-palette colours unblend at α = 0.6666666666666666, every one of
them, with a maximum per-channel reconstruction error of 1.4e-14** — machine
zero. Not "about 0.67": exactly two thirds. Each solves against a distinct
palette colour (`distinct_palette_colors_blended: 120`), each labelled with the
element it was assigned to. That is a compositing constant applied uniformly,
not a rendering artefact, and it is what B-H5 needed: a residue colour from
resampling would scatter across the alpha ray, not land 120 times on one value.

No API for reading or setting that constant turned up: B1 probed
`GetHalftoneBrightness`, `GetUnderlayBrightness`, `HalftoneBrightness` and
`UnderlayBrightness` on the Document and all four are **absent** on this
install. So the brightness is settable in the UI per project and not reachable
from the API — which is the argument for turning the underlay off rather than
compensating for whatever value it holds.

### The width cap does not bound the image

`MAX_STAGE_A_PIXEL_SIZE = 15000` clamps `pixel_size`, and with
`FitDirectionType.Horizontal` that is the **width**. The height is derived from
the view's extents and is bounded by nothing:

    height_px ≈ pixel_size × extent_v_ft / extent_u_ft

(5427 × 1178.1/1155.0 = 5535.5 → 5535, exactly the exported height.) So the
existing cap cannot prevent drift, and `(N) HOSPITAL - LEVEL 2` proves it: 8739
px wide, far under 15000, and drifted at 10079 tall. What has to be bounded is
the derived height:

    pixel_size ≤ 9900 × extent_u_ft / extent_v_ft

On `(N) HOSPITAL - LEVEL 2` that gives 8583 px against a native 8739 — **98% of
native density** for hard edges. On `MOB 1 - LEVEL 2` it gives 12016 against
the 15000 the width cap allows today, trading 20% linear density for an
export whose every edge is exact. Where that trade is not acceptable, D5's
tiling holds native density: four tiles, `hard_edge_ratio` 1.0 on all four.

`D4_NATIVE_CEILING` was this same confusion inside the probe — it compared the
longest axis against the width ceiling, which is why D4 lowered no DPI and
duplicated D1. It is now `D4_WIDTH_CEILING` (15000, production's) and
`D4_HEIGHT_CEILING` (9900, Run 1's), applied per axis.

### The run's own defect: a stale probe module

`probe_stage_a_white_blend` executed the current code; the
`probe_stage_a_drift_onset` it captures through was the version from before
`6594d1e`. The evidence is in the artifacts: the B3 captures carry the
crop-pinning added in `ad5e48d` (later) but not the view id in their file names
added in `6594d1e` (earlier), which no single checkout produces.

Revit holds one Python interpreter open for a whole session. The campaign JSON
is re-read from disk every run; the probe modules are not. So an edited probe
stays unloaded until Revit restarts, and a run executes old code against a new
campaign — producing a complete, unflagged set of artifacts, one experiment of
which measured nothing.

D1, D3, D4 and D5 are unaffected: nothing in those code paths or in
`production_capture` changed between that version and now. **D2 must be
re-run.** Both probes now record `probe.source` per module, and a dry run
refuses a probe whose file has changed since it was imported.

## Run 2 — planned: pin the threshold, test the remedy

`campaigns/stage_a_run2_threshold_and_remedy.json`, four jobs, ten captures,
3–10 MB each. Its own `batch_id`, so Run 1's completed jobs do not resume-skip
it. **Restart Revit first** — Run 1's D2 was spoiled by a module the session
was still holding from before an edit.

Every width below is chosen for the *height* it derives, using the aspect each
view actually exported at in Run 1, and each capture has a stated prediction. A
wrong prediction falsifies the height rule instead of being absorbed by it.

| job | view | requested width | derived height | prediction |
|---|---|---|---|---|
| `d3-threshold-pin-hosp-2` | 871863 | 8564 / 8652 / 8695 / 8739 | 9877 / 9979 / 10028 / 10079 | clean, clean, **drift**, drift |
| `d3-height-cap-mob-1` | 528698 | 12000 / native (→15000) | 9885 / 12356 | clean, **drift** |
| `d3-height-cap-hosp-3` | 929475 | 10268 / 12000 | 9900 / 11570 | clean, **drift** |
| `b3-underlay-hosp-2` | 871863 | 8584 | 9877 | underlay → off-palette px → 0 |

The threshold sweep cuts (9927, 10079] down to roughly ±25 px around 10,000.
The two height-cap jobs test the remedy and carry the run's sharpest
prediction: **12000 px wide is predicted clean on MOB 1 and drifted on
(N) HOSPITAL - LEVEL 3** — same requested width, opposite outcome, differing
only in derived height. If width mattered they would agree.

`b3-underlay-hosp-2` is pinned under the height ceiling on purpose: 871863
drifts at native, and in a drifted capture the off-palette pixels are resample
residue as well as underlay blend. It also asks whether the α=2/3 blend appears
outside the SITE PLAN family at all.

D2 is dropped. **D6 needs no new campaign** — run this one twice, once with
hardware acceleration on and once off, into different `artifact_root`s; a prior
success only resume-skips when it landed where the current run writes, so the
second pass re-runs everything by itself.

Still not covered by any campaign, and both need input:

- **Fit direction.** Every capture so far used `FitDirectionType.Horizontal`,
  so PixelSize set the width exactly and the height was always the derived
  axis. Nothing distinguishes "the cap is on height" from "the cap is on
  whichever axis Revit does not fit to". Testing it means varying an
  `ImageExportOptions` field that `export_color_id_buffer_view` sets, which is
  a production edit rather than an experiment input — see the stop condition.
- **Links.** A different document, so a separate campaign; it needs the model
  and the view ids.

## Hypotheses

Statuses below are from Run 1. Verdicts marked **by run** rest on the measured
table; everything else is still open.

### Drift

| # | Hypothesis | Status | Evidence that would settle it |
|---|---|---|---|
| D-H1 | Drift is non-deterministic (a render-path race) | **contradicted by run** | D1: byte-identical repeats contradict it |
| D-H2 | Drift is a hard absolute pixel threshold near 10000 | **supported by run, on HEIGHT** | Every capture splits on exported height at a threshold in (9927, 10079]; width up to 15000 is clean. The baseline's counter-example measured the long axis, not the height |
| D-H3 | Drift is triggered by a *combination* of raster size and scene load | **not needed** — height alone separates every capture, at fixed load | D2: if unhiding categories in 49370 flips `hard_edge_ratio` at fixed size, supported; if it never flips, contradicted |
| D-H4 | Drift is caused by the request exceeding what Revit will render, followed by an upscale | **supported by run** | D3+D4: if lowering DPI so native ≤ 15000 and requesting native exactly gives hard edges, supported. `(N) HOSPITAL - LEVEL 2` drifting at scale 1.00 already weighs against it |
| D-H5 | Drift is a resample, not anti-aliasing | **supported by baseline** | AA already ruled out by stair-stepped curves in clean views; 34–60% overshoot is a negative-lobe kernel or a sharpening pass, which AA does not produce |
| D-H6 | Drift is avoidable by tiling at native density | **supported by run** | D5: per-tile `hard_edge_ratio` of 1.0 with zero seam residual supports it |

Note on D-H5: 34–60% normalized overshoot is large for Lanczos (typically under
20%) and very large for bicubic. That magnitude is more consistent with a
sharpening pass applied after a downscale than with a plain resample kernel.
The metric set reports the magnitude distribution, not just a rate, so this can
be checked rather than argued.

### 0.67 white blend

| # | Hypothesis | Status | Evidence that would settle it |
|---|---|---|---|
| B-H1 | Element-level halftone or surface transparency | **contradicted by code** | `color_id_buffer._build_flat_color_ogs` already calls `SetHalftone(False)` and `SetSurfaceTransparency(0)` on every painted element, and production clears category halftone in its capture setup; element overrides outrank category, filter, and template overrides. B1 reports the authored state, and `b3_halftone_cleared`'s redundancy skip confirms it on the run |
| B-H2 | View underlay | **confirmed by run** | Consistent with every baseline fact: the affected categories (Walls, Generic Models, Roofs, Doors, Windows) are exactly what an underlay from an adjacent level shows; the blend is uniform over the whole element; and pastels unblend to the *palette* color, so the override applied and something composited afterwards. B1's `elements_on_an_underlay_level`, then B3's `underlay_off` export |
| B-H3 | Phase filter "Overridden" graphics | **not needed** | Phase overrides sit below element overrides in Revit's precedence, so they should have been beaten by the paint step. B1 records the phase filter and its per-status presentation anyway |
| B-H4 | Design option graphics | **contradicted by run** | B1 records each element's design option |
| B-H5 | The blend target is another element, not white | **contradicted by run** — 120/120 colours unblend against white at α=2/3 with 1.4e-14 error | B2: every pastel color unblends *exactly* against white in the baseline, which already weighs against it. `white_blend.matches[].neighbor_palette_rgb` names the palette colours actually adjacent to each pastel region, so a pastel that unblends against white while touching another element settles it |

## UNCONFIRMED API assumptions

None of these has been verified by a run. Each is probed by reflection and
reported present/absent with its value rather than assumed; each probe also
emits its own `unconfirmed_api_assumptions` list.

**Export / drift**

1. `ZoomFitType.FitToPage` + `FitDirectionType.Horizontal` makes `PixelSize` the
   output *width*. (Production depends on this today.)
2. `ImageExportOptions` exposes no DPI concept independent of `PixelSize`, so
   "lowering DPI" is request-side arithmetic only.
3. Revit clamps export aspect to 10:1 by *symmetric* padding of the short axis.
   Checked per capture by `frame.clamp_matches_actual_height`.
4. Revit's `PixelSize` ceiling is a fixed value rather than install- or
   version-dependent. `_set_pixel_size` halves on rejection rather than
   assuming a constant; `resolution.pixel_size_backoff` reports when it fired.
5. No API access to hardware acceleration is known, which is why D6 is manual.
   **Now the highest-value remaining test**: if the ~10,000 px height cap is a
   renderer/GPU limit, disabling hardware acceleration may move or remove it.
6. **Untested**: whether the cap is on the *height* or on *the axis Revit does
   not fit to*. Every capture in Run 1 used `FitDirectionType.Horizontal`, so
   PixelSize set the width exactly (5427, 10000, 12000, 15000 all accepted
   verbatim) and the height was always the derived one. If the cap belongs to
   the derived axis rather than to height, exporting a tall view with
   `FitDirectionType.Vertical` would move it to the width and allow the full
   height — which would change the recommended fix entirely. One capture of
   871863 at native with Vertical fit decides it.

**Underlay / blend**

7. `ViewPlan.GetUnderlayBaseLevel` / `GetUnderlayTopLevel` /
   `GetUnderlayOrientation` exist on this install. **CONFIRMED by Run 1** on
   Revit 2025 build 25.4.41.14: B1 read the underlay range through them.
8. `ViewPlan.SetUnderlayRange(InvalidElementId, InvalidElementId)` disables the
   underlay. **CONFIRMED by Run 1**: `api_used: "SetUnderlayRange"`, and the
   resulting capture has zero off-palette pixels.
8. `BuiltInParameter.VIEW_UNDERLAY_ID` / `_BOTTOM_ID` / `_TOP_ID` are the
   pre-2018 fallback.
9. A document-level halftone/underlay brightness is reachable from the API at
   all. If it is, its numeric value against the observed alpha of 0.67 is the
   single most direct piece of evidence for B-H2.
10. `View.GetPhaseFilterOverrides` exists and returns the phase graphic
    override.
11. Underlay graphics compose over the element's *resolved override colour*
    rather than replacing it. The baseline's exact unblend to palette colours
    is consistent with this but does not establish it.

## Stop conditions

- D2 or D3 identifying a reproducible onset condition → stop and report.
- Any experiment that would need a pipeline default changed → stop and ask.
  Nothing in this instrumentation changes a default; D4 lowers DPI for the
  probe's own exports only, inside a rolled-back TransactionGroup.
