# pyRevit extension: VOP / Stage A / Batch Capture

One button: open each archive of a capture plan, run the Stage A capture (a
new run) or the recapture of an existing run against it, close it, go on.
The loop is the Fingerprint extension's; what it runs is the exporter's
(`vop_interwoven/batch_capture.py`, `capture_run.py`, `recapture.py`).

## Install

1. pyRevit (CPython 3 engine): Settings -> Custom Extension Directories ->
   add `<checkout>\pyrevit`, reload. The tab **VOP** appears.
2. `batch_settings.json` beside `script.py`: set `plan_json`, and set
   `repo_root` to the checkout (the folder holding `vop_interwoven`) unless
   pyRevit loads the extension from the checkout's own `pyrevit/` folder,
   where it is derived. An extension COPIED into pyRevit's extensions
   folder needs it set; the button says so if it is not.

## Run

1. Manifest: the Fingerprint manifest CSV (`include`, `relative_path`,
   `full_path`, `project_folder`, `filename`), `include=1` on the archives
   to capture. The archives are Desktop Connector local paths.
2. Plan, at the command prompt, and review it:

       python tools/plan_capture_batch.py <manifest.csv> --output-root <Exports> \
           --recapture-lists <recapture_lists.json> --mode both --out capture_plan.json

   Every file gets a RECAPTURE job for each run of the lists whose recorded
   `doc_title` is the file's stem, else a CAPTURE job (a new run under
   `<Exports>/<project_folder>/<stem without date>/`, as-of the date the
   file name ends with), unless a finalized run was captured from it
   already. `unmatched_runs` lists runs the manifest holds no archive for.
   Edit the plan if needed (drop jobs, change `output_dir`, `view_types`).
3. Revit open, NO document open, press **Batch Capture**. It confirms the
   plan, opens each archive detached (`DetachAndPreserveWorksets`, all
   worksets open -- closed worksets would hide model elements), dismisses
   dialogs during the open (ids logged), runs the job, closes the previous
   document once the next is open.
4. Resumable: a job whose outcome is in the run's files is skipped (a
   finalized `run_meta.json` captured from the file; a `recaptures` entry
   covering the ids). `limit` in the settings runs that many pending jobs.
5. Afterwards, per run: `register_stage_a_annotation.py`, `stage_a_grid.py`,
   `stage_a_kinds.py`, `stage_a_grid_rollup.py`.

## What it records

* `<Exports>/batch_capture_<clock>.json`: every job's outcome, written after
  each job (an interrupted batch leaves what it did).
* each run's `run_meta.json`: `opened_from` (manifest path, detach option,
  worksets, the title Revit gave the document) and, for a new capture,
  `view_selection` (the rule, counts per ViewType kept and excluded).
* a recapture refused for a run names both titles in the batch log
  (`result.refused`) and in the closing dialog.

## Assumptions to verify on the first file

* The title of a detached document: the recapture accepts the run's
  recorded title or that title with `_detached`; which one Revit gives is
  recorded in `opened_from.title`.
* A capture made this way is a different environment from the interactive
  runs (detached, all worksets open); `opened_from` is what analysis
  compares on.
