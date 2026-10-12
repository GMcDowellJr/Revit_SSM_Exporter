#!/usr/bin/env python3
"""Which views of which runs need a new Stage A capture, from the sidecars.

Read-only. For every run folder given (a run directory or its
``color_id_buffer/``) it runs the sidecar half of ``census_registration``
(no TIFF is read) and classifies each of the run's own views:

* ``recapture``  -- the registration was refused with fewer ticks found than
  the capture drew (in either pass), or no registration record exists for a
  capture that recorded ticks: the ticks did not render, which on run
  2025-06-21__20261008T093539 was a crop rotated against the view's axes on
  every one of 52 views (RUN_2025-06-21_20261008T093539_ROTATION.md). A new
  capture with ticks placed inside the crop loop is what fixes it;
* ``review``     -- refused with every tick found (a fit residual, as 929475:
  127 px): a different defect, not fixed by recapturing;
* ``keep``       -- registered;
* ``not_this_run`` / ``not_requested`` -- a sidecar that is not the run's own
  capture (``provenance``), listed and not counted.

Outputs, in ``--out``: ``recapture_views.csv`` (one row per view, every run)
and ``recapture_lists.json`` (per run: run_dir, run_id, as-of date, the
view ids to recapture, the ids to review) -- the list a patch capture takes.
A run whose run_meta.json is refused by the roll-up's identity rules is
listed with the reason and contributes no views.

    python tools/find_recapture_views.py <run> [<run> ...] --out <dir>
"""

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import census_registration as census  # noqa: E402
from tools import decode_stage_a_color_id as dsc  # noqa: E402
from tools import stage_a_grid as grid  # noqa: E402

SCHEMA = "vop.stage_a.recapture_views.v1"
TOOL_VERSION = "1.0.0"
CSV_NAME = "recapture_views.csv"
LISTS_NAME = "recapture_lists.json"
COLUMNS = ["run_dir", "run_id", "as_of_date", "view_id", "view_stem", "view_name",
           "view_type", "scale", "provenance", "registration_state", "verdict",
           "marks_expected", "anno_found_count", "model_found_count", "reason"]


def verdict(row):
    """PURE. ``(verdict, reason)`` for one census row."""
    if row.get("provenance") != "this_run":
        return row.get("provenance") or "unknown", "not the run's own capture"
    state = row.get("registration_state")
    expected = row.get("marks_expected")
    if state == "registered":
        return "keep", ""
    if state == "stale":
        return "review", "registration record does not match the files: " + str(
            row.get("registration_reason"))
    if state == "absent":
        if expected:
            return "recapture", "ticks drawn but no registration record"
        return "review", "no ticks drawn and no registration record"
    found = [row.get("anno_found_count"), row.get("model_found_count")]
    try:
        counts = [int(f) for f in found if f not in (None, "")]
    except ValueError:
        counts = []
    if state == "refused" and expected and counts and min(counts) < int(expected):
        return "recapture", "{0} / {1} ticks found of {2}: {3}".format(
            found[0], found[1], expected, row.get("registration_reason"))
    if state == "refused":
        return "review", "refused with every tick found: " + str(row.get("registration_reason"))
    return "review", "registration state {0!r}".format(state)


def find(run_args):
    runs, rows = census.census(run_args, pixels=False)
    out_rows, lists = [], []
    for run in runs:
        folder = Path(run["capture_dir"])
        meta = dsc.find_run_meta(folder / "run_meta.json") or {}
        entry = {"run_dir": str(folder.parent), "run_id": run.get("run_id"),
                 "as_of_date": meta.get("date"), "refused": run.get("run_meta_refused"),
                 "recapture": [], "review": [], "keep": 0}
        for r in rows:
            if r.get("run_id") != run.get("run_id"):
                continue
            v, why = verdict(r)
            out_rows.append(dict((c, r.get(c)) for c in COLUMNS if c in r) | {
                "run_dir": entry["run_dir"], "as_of_date": entry["as_of_date"],
                "verdict": v, "reason": why})
            if v == "recapture":
                entry["recapture"].append(int(r["view_id"]))
            elif v == "review":
                entry["review"].append(int(r["view_id"]))
            elif v == "keep":
                entry["keep"] += 1
        entry["recapture"].sort()
        entry["review"].sort()
        lists.append(entry)
    return lists, out_rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("runs", nargs="+", metavar="run")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    out = Path(args.out)
    for run_arg in args.runs:
        _s, folder = grid.model_sidecars(run_arg)
        if census.rollup._inside(out, folder):
            ap.error("--out {0} is inside the capture folder {1}".format(out, folder))
    lists, rows = find(args.runs)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / CSV_NAME, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow(dict((k, census.rollup._cell(r.get(k))) for k in COLUMNS))
    with open(out / LISTS_NAME, "w", encoding="utf-8") as handle:
        json.dump({"schema": SCHEMA, "tool_version": TOOL_VERSION, "runs": lists},
                  handle, indent=2, sort_keys=True)
    total = 0
    for e in lists:
        total += len(e["recapture"])
        print("{0}: run {1} as of {2}: recapture {3}, review {4}, keep {5}{6}".format(
            e["run_dir"], e["run_id"], e["as_of_date"], len(e["recapture"]),
            len(e["review"]), e["keep"],
            "  REFUSED: " + str(e["refused"]) if e["refused"] else ""))
    print("{0} views to recapture across {1} runs; wrote {2} and {3}".format(
        total, len(lists), out / CSV_NAME, out / LISTS_NAME))
    return 0


if __name__ == "__main__":
    sys.exit(main())
