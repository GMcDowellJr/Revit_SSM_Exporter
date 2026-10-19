#!/usr/bin/env python
"""Plan a Stage A batch capture: which archive gets which job, written as
capture_plan.json for review BEFORE Revit opens anything.

    python tools/plan_capture_batch.py <manifest.csv> --output-root <Exports> \\
        [--recapture-lists recapture_lists.json] [--mode both|capture|recapture] \\
        [--no-skip-captured] [--view-types FloorPlan,Section,...] [--limit N] \\
        [--out capture_plan.json]

The manifest is the Fingerprint extension's (columns include, relative_path,
full_path, project_folder, filename; include=1 rows are planned). Each
included file gets

* a RECAPTURE job for every run of ``--recapture-lists``
  (tools/find_recapture_views.py) whose recorded doc_title is the file's
  stem -- the run's run_meta.json is read for that;
* else, in mode capture/both, a CAPTURE job (a NEW run under
  ``<output-root>/`` itself -- the selected folder, not a derived
  subfolder (2026-10-19) -- as-of the date the
  file name carries), unless a finalized run under the output root was
  already captured from it.

Runs that name views but match no manifest file are listed under
``unmatched_runs``; files given no job under ``skipped``. Edit the plan
(output_dir, view_types, drop jobs) and point the pyRevit button at it.
Read-only on everything but ``--out``.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from vop_interwoven.batch_capture import (  # noqa: E402
    DEFAULT_VIEW_TYPES, BatchPlanError, build_plan, index_runs, load_manifest,
)
from vop_interwoven.recapture import RecaptureRefused, load_recapture_lists  # noqa: E402


def plan_from_args(args):
    entries = load_manifest(args.manifest, limit=args.limit)
    runs = load_recapture_lists(args.recapture_lists) if args.recapture_lists else []
    run_index = index_runs(args.output_root)
    # The recapture lists' runs may live outside the output root: index them
    # by their own directories too.
    known = set(os.path.normpath(r["run_dir"]) for r in run_index if "run_dir" in r)
    for run in runs:
        run_dir = run.get("run_dir")
        if run_dir and os.path.normpath(run_dir) not in known:
            run_index.extend(index_runs(run_dir, max_depth=0))
            known.add(os.path.normpath(run_dir))
    view_types = ([t.strip() for t in args.view_types.split(",") if t.strip()]
                  if args.view_types else DEFAULT_VIEW_TYPES)
    plan = build_plan(entries, args.output_root, recapture_runs=runs, run_index=run_index,
                      mode=args.mode, skip_captured=not args.no_skip_captured,
                      view_types=view_types)
    plan["manifest"] = os.path.abspath(args.manifest)
    plan["recapture_lists"] = os.path.abspath(args.recapture_lists) if args.recapture_lists else None
    plan["manifest_entries"] = len(entries)
    plan["runs_indexed"] = len(run_index)
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("manifest")
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--recapture-lists")
    parser.add_argument("--mode", default="both", choices=("both", "capture", "recapture"))
    parser.add_argument("--no-skip-captured", action="store_true",
                        help="plan a capture job even for a file a finalized run was captured from")
    parser.add_argument("--view-types", help="comma-separated ViewType names for capture jobs")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--out", default="capture_plan.json")
    args = parser.parse_args(argv)
    try:
        plan = plan_from_args(args)
    except (BatchPlanError, RecaptureRefused, IOError, OSError) as ex:
        print("REFUSED: {0}: {1}".format(type(ex).__name__, ex))
        return 2
    with open(args.out, "w") as handle:
        json.dump(plan, handle, indent=2, sort_keys=True)
    kinds = {}
    for job in plan["jobs"]:
        kinds[job["kind"]] = kinds.get(job["kind"], 0) + 1
    print("plan: {0}".format(os.path.abspath(args.out)))
    print("  manifest files: {0}   jobs: {1}   skipped: {2}   unmatched runs: {3}".format(
        plan["manifest_entries"], kinds, len(plan["skipped"]), len(plan["unmatched_runs"])))
    for run in plan["unmatched_runs"]:
        print("  UNMATCHED run {0} ({1!r}): {2} view(s)".format(
            run.get("run_id"), run.get("doc_title"), len(run["view_ids"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
