# Export-cap probe (Q2)

`probe_export_cap.py` answers, with measurements and before anything is
designed: **on capped views, does export time (and the size of the image
downstream) scale with pixel count, and what happens to the grid output as
the cap is lowered?** The offline half is `tools/analyze_export_cap_probe.py`.

**UNVERIFIED in Revit.** Offline tests (`tests/test_analyze_export_cap_probe.py`)
cover the plan, the job summary read from sidecars, the document check, the
statistics, and the analyzer end to end on synthetic runs that the
production registration and grid tools process unchanged. The first run is
the test of the probe itself.

## What it runs

Each job is **one ordinary capture run of one view** by production's
`capture_run.run_capture`. It uses the batch's Config (Stage A on,
registered capture on, summary detail) and changes one thing:
`color_id_buffer_cap_axis_px` is set to the job's cap. That is the
test-only override of the per-axis sizing cap (`config.py`), which
`resolution_contract.cap_axes` applies to **both** axes. A cap of 7000 px
therefore bounds the long axis at 7000 px. The model pass, the ticks
(sized on the model's own lattice, `mark_fpp_ft`), the annotation pass and
the registration all follow the cap as they would in production. The
post-export ceiling stays at 10000 px, so caps above it are refused by the
probe.

Jobs are every view × every cap × every repeat. Within each repeat, a view's
caps run back to back in an order shuffled per view by one seeded
`random.Random` (`build_plan`). Every job records `index` and
`position_in_view_block`, so the analyzer measures warm-up
(`warmup_by_position`) rather than assuming it away.

Background (not measured here): capped views are always off-sheet working
views (a one-way rule, decisions-and-learnings 2026-09-17). `LEVEL 1`
(10481315) on `2025-12-20__20261021T1830` was captured at 9151 × 9999 px,
0.363 ft/px. Grid cost was 0.25–0.5 s per megapixel on
`2025-10-25__20261012T122748`. Whether `ExportImage` time scales with
pixels or is dominated by regeneration has not been measured; that is what
this probe measures.

## How the document is kept unchanged

- The probe writes nothing to the document itself. Every write is made by
  the registered capture, inside its own `TransactionGroup`, which is rolled
  back and read back as on every capture. Each job lists that capture's
  restore faults (`restore_faults`: `view_state_not_restored`,
  `view_membership_changed`, `registration_marks_left_in_project`, ...) and
  its `capture_integrity` (`rolled_back`, `marks_still_in_project`,
  `restore_failures`).
- Before the first job and after the last, the probe counts the document's
  elements and element types and reads `IsModified` (`document_before`,
  `document_after`, `document_check`).
- It never saves.
- `RESTORE_SUMMARY` reads `DOCUMENT UNCHANGED` only when the counts match
  and no job has a restore fault. Otherwise it reads `DOCUMENT MAY HAVE
  CHANGED`, in capitals, and nothing is fixed.

`IsModified` is recorded but not used as a verdict: whether a rolled-back
group sets it is not known (INFERENCE either way).

## How to run (Greg)

1. Open the model in Revit 2025: the archive whose views you choose, e.g. the
   2025-12-20 one for `LEVEL 1`. Dynamo 3.x, CPython3 Python node.
2. Paste `tests/dynamo/probe_export_cap.py` into the node, or
   `exec(open(path).read())`.
3. Inputs:

| Input | Meaning | Default |
|---|---|---|
| `IN[0]` | output root. Keep it SHORT (e.g. `C:/p`): each job's run sits three folders deeper, and a run too deep for its file names is refused (`check_run_budget`). | required |
| `IN[1]` | the 5 capped view ids, a list | required: `10481315` (LEVEL 1) and 4 others |
| `IN[2]` | 2 uncapped control view ids, a list | none |
| `IN[3]` | caps, long-axis px | `[10000, 7000, 5000, 3500]` |
| `IN[4]` | repeats per (view, cap) | `3` |
| `IN[5]` | options (Dictionary or JSON) | `{}` |

   `IN[5]` keys (an unknown key is refused):

| Key | Meaning | Default |
|---|---|---|
| `repo_root` | the checkout; `vop_interwoven` is purged from memory and imported from here | `REVIT_SSM_EXPORTER_ROOT`, then `~/Documents/Revit_SSM_Exporter` |
| `seed` | the shuffle's seed | `20261022` |
| `expected_doc_title` | refuse to start when the open document is not this | none |
| `keep_images` | `"first_repeat"`: a later repeat's TIFFs are hashed, then deleted; `"all"` keeps them | `"first_repeat"` |

4. Run. `OUT` is the JSON path; `{"fatal": ...}` if it could not start.

**Expected run time** (INFERENCE, not measured): one full registered capture
per job, both passes. With 7 views × 4 caps × 3 repeats = 84 captures at
roughly 30 s to 2 min each, that is about 1 to 3 hours. To size it first,
run `IN[4] = 1` on `LEVEL 1` alone (4 captures). `progress_<stamp>.json` is
rewritten after every job.

**Disk** (INFERENCE): an uncompressed 10000 px RGB TIFF is about 300 MB.
With `keep_images` left at `first_repeat`, a view's four caps keep about
0.6 GB per pass, so roughly 6–8 GB for 7 views and both passes.

### Then, offline, on the same machine

```bash
python tools/analyze_export_cap_probe.py <IN[0]>/probe_export_cap_<stamp>
# optionally: --grid-all-repeats   (grid every kept repeat, not the first)
#             --bound 0.01          (the smallest cap within YOUR bound)
```

The analyzer registers the first repeat of each (view, cap), grids it with
`tools/stage_a_grid.py` unchanged, and times both. It then writes
`export_cap_analysis.json` beside the probe JSON and prints the export fit
and the fill curve. Expect about 0.25–0.5 s of grid time per megapixel, so
roughly 10–20 minutes for 28 grids.

## Files to return

From `<IN[0]>/probe_export_cap_<stamp>/`:

- `probe_export_cap_<stamp>.json` (the record, written last)
- `progress_<stamp>.json`
- `export_cap_analysis.json` (after the offline step)
- every `jobs/*/*/run_meta*.json` and `jobs/*/*/color_id/*.json` (the
  sidecars, small), and `jobs/*/*/grid/*.grid.json`

The TIFFs and npz files are not needed unless something in the analysis
needs a second look.

## Outputs

`probe_export_cap_<stamp>.json`:

- `inputs`, `repo` (where `vop_interwoven` was loaded from), `doc_title`
- `jobs[]`, one per capture, in run order: `view_id`, `cap`, `repeat`,
  `index`, `position_in_view_block`, `run_dir`, `run_capture_s`; read
  back from the sidecars (`summarize_job`): `actual_w`, `actual_h`,
  `megapixels`, `cap_applied`, `pre_cap_px`, `pixel_size`,
  `achieved_fpp_ft`, `dim_check`, `attempts`, `model_export_ms`,
  `annotation_export_ms`, `model_pass_ms`, `annotation_pass_ms`,
  `capture_total_ms` (the registered capture's own
  `registration_marks.timings_ms`); `faults`, `restore_faults`,
  `rolled_back`, `marks_still_in_project`, `restore_failures`; the TIFF
  byte sizes and sha256s; `images_deleted`
- `document_before`, `document_after`, `document_check`,
  `jobs_with_restore_faults`, `RESTORE_SUMMARY`
- `exceptions`

`export_cap_analysis.json`:

- `export_fit`: `model_export` ms against achieved megapixels, by least
  squares over the jobs exported in one attempt (`excluded_retried` names
  the others: their time includes the retries). Gives slope, intercept, r²
  and each point's residual. Also `export_fit_per_view`.
- `warmup_by_position`: the mean ms above the (view, cap) median, by
  position in the view's block.
- `timing`: per (view, cap), median / min / max / spread of the model and
  annotation export ms and the capture total, the achieved px, the TIFF
  bytes, and the number of distinct model-TIFF hashes over the repeats (1 is
  byte-identical output).
- `grid`: per gridded job, `px_per_cell`, the crop-A cells, `model_ink_cells`,
  `fill_fraction`, `model_px_fraction`, `anno_ink_cells`,
  `per_category_ink_cells` and `per_category_fill`, `register_s`, `grid_s`,
  and the npz and registered-TIFF bytes. A refusal (for example fewer than
  2 px per cell) is kept as the data point (`grid_refusal`).
- `grid_cost_fit`: grid seconds against megapixels.
- `grid_vs_reference`: per view and cap, the change against that view's
  largest cap: `delta_fill`, `relative_delta_fill`,
  `delta_model_px_fraction`, `delta_anno_ink_cells`,
  `delta_per_category_fill`, and the model-ink cells gained, lost and their
  Jaccard overlap. Cells are compared by their signed (i, j) index, which is
  anchored to view UV, so the same cell is compared at every cap.
- `fill_curve`: per cap, over the capped views, the largest |Δ fill|, the
  largest relative |Δ fill| and the smallest Jaccard. **No bound is
  chosen.** Read off the smallest cap within your own bound, or pass
  `--bound` to get `bound_query.smallest_cap_within`: the smallest cap such
  that it and every larger cap stay within the bound.
- `thin_line_bias`: per view, fill by achieved px per cell, highest first,
  and `steps_raising_fill` out of `steps_down_in_resolution`. A line that no
  longer renders below 1 px would push fill UP as resolution falls; this
  count is where that would show.
- `sizes`: TIFF, registered-TIFF and npz bytes against megapixels.

Definitions:

- **fill fraction** = model-ink cells inside crop A (occupancy `model_only`
  or `overlap`, the grid's ink rule) / crop-A cells.
- **model pixel fraction** = model element pixels in crop-A cells / crop-A
  pixels.

## What is reimplemented, not imported

Nothing that captures or measures. The capture is production's
`run_capture`; the registration and grid are the repository's tools,
unchanged. The probe copies one thing, the batch's four Config lines
(`BatchCapture _config`), because a pyRevit script cannot be imported.

## Results

*(Greg: fill in after the run.)*
