#!/usr/bin/env python3
"""Compare the per-view timing of several Stage A runs, step by step.

Reads two or more ``views_timing_<suffix>.csv`` files (tools/stage_a_timing_report.py)
and reports, per run, the MEDIAN of every step over its captured views, and,
against the FIRST file given (the reference), the median per-view RATIO of
each step over the views both runs captured. A ratio is taken only where both
sides are > 0. Nothing is thresholded: medians, quartiles and counts.

Written for the 3.0 -> 3.2 question (2026-10-27): on the 867 views shared by
2025-12-20__20261021T1830 and 2025-11-08__20261022T1459 the commit steps ran
5.7x and marks_create 2.7x slower while model_export held at 0.99x. Ratios
over many runs (one exporter, several archives; one archive, several
batches) say whether that follows the archive, the batch or the build.

    python tools/compare_views_timing.py REF.csv OTHER.csv [OTHER.csv ...]
        [--steps phase_marks_create_ms,phase_model_restore_commit_ms] [--json OUT]
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

DEFAULT_STEPS = ("elapsed_ms", "capture_total_ms", "phase_marks_create_ms",
                 "phase_model_restore_commit_ms", "phase_model_suppress_commit_ms",
                 "phase_annotation_pass_ms", "phase_model_pass_ms",
                 "phase_model_export_ms", "phase_annotation_export_ms",
                 "phase_model_graphics_state_ms", "outside_capture_ms")


def _num(row, key):
    try:
        return float(row[key])
    except (KeyError, TypeError, ValueError):
        return None


def load(path):
    """``(label, {view_id: row})`` for the CAPTURED views of one file (a row
    with a capture_total_ms); the label is the file's run id(s)."""
    with open(path, encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    runs = sorted(set(r.get("run_id") or "?" for r in rows))
    captured = dict((r["view_id"], r) for r in rows if _num(r, "capture_total_ms") is not None)
    return "{0} ({1})".format(Path(path).name, ",".join(runs)), captured


def quartiles(values):
    """PURE. ``{n, q1, median, q3}`` of the values (None when there are none)."""
    vals = sorted(values)
    if not vals:
        return {"n": 0, "q1": None, "median": None, "q3": None}
    if len(vals) == 1:
        return {"n": 1, "q1": vals[0], "median": vals[0], "q3": vals[0]}
    q = statistics.quantiles(vals, n=4)
    return {"n": len(vals), "q1": q[0], "median": statistics.median(vals), "q3": q[2]}


def compare(runs, steps=DEFAULT_STEPS):
    """PURE. ``runs``: ``[(label, {view_id: row})]``, the first the reference.
    Per run, the medians of each step; per later run, the per-view ratios
    against the reference on the views both captured."""
    ref_label, ref = runs[0]
    out = {"reference": ref_label, "steps": list(steps), "runs": []}
    for label, views in runs:
        entry = {"label": label, "views": len(views),
                 "median": dict((s, quartiles([v for v in (_num(r, s) for r in views.values())
                                                if v is not None])["median"]) for s in steps)}
        if label != ref_label:
            common = sorted(set(ref) & set(views))
            entry["common_views"] = len(common)
            entry["ratio_to_reference"] = {}
            for s in steps:
                ratios = []
                for vid in common:
                    a, b = _num(ref[vid], s), _num(views[vid], s)
                    if a and b and a > 0 and b > 0:
                        ratios.append(b / a)
                entry["ratio_to_reference"][s] = quartiles(ratios)
        out["runs"].append(entry)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="+", help="views_timing CSVs; the first is the reference")
    ap.add_argument("--steps", default=None, help="comma list of columns (default: the main steps)")
    ap.add_argument("--json", default=None, help="also write the comparison here")
    args = ap.parse_args(argv)
    if len(args.files) < 2:
        ap.error("give at least two views_timing files")
    steps = tuple(s.strip() for s in args.steps.split(",")) if args.steps else DEFAULT_STEPS
    record = compare([load(f) for f in args.files], steps)
    for run in record["runs"]:
        print("{0}: {1} captured views{2}".format(
            run["label"], run["views"],
            "" if "common_views" not in run else ", {0} shared with the reference".format(
                run["common_views"])))
    print("{0:<36}".format("median ms") + "".join("{0:>14}".format(i) for i in
                                                  range(len(record["runs"]))))
    for s in steps:
        print("{0:<36}".format(s) + "".join(
            "{0:>14}".format("-" if r["median"][s] is None else "{0:.0f}".format(r["median"][s]))
            for r in record["runs"]))
    print("{0:<36}".format("median ratio to run 0 (q1-q3)"))
    for s in steps:
        cells = []
        for r in record["runs"][1:]:
            q = r["ratio_to_reference"][s]
            cells.append("-" if q["median"] is None else "{0:.2f} ({1:.2f}-{2:.2f})".format(
                q["median"], q["q1"], q["q3"]))
        print("{0:<36}".format(s) + "".join("{0:>22}".format(c) for c in cells))
    if args.json:
        Path(args.json).write_text(json.dumps(record, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
