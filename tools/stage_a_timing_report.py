"""P2: reconcile a Stage A run's per-view ElapsedSec with its capture timings.

For each view in ``views_core*.csv``, reads the MODEL sidecar's
``registration_marks.timings_ms`` (written by the registered capture) and
reports, per view:

  * ``elapsed_ms``          -- views_core ElapsedSec, the pipeline's own clock;
  * ``capture_total_ms``    -- the registered capture's ``total``;
  * ``outside_capture_ms``  -- elapsed - total: pipeline work around the
    capture (collection, raster init, the frame-B extent, the registration
    sidecar writes);
  * ``unaccounted_ms``      -- what total holds beyond its partition phases.

Nothing is hidden: a view whose sidecar carries no timings (a run before P2)
is reported with the reason, never dropped. Standard library only.

    python tools/stage_a_timing_report.py <run_dir> [--json]
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys


def _model_sidecars(run_dir):
    out = {}
    for path in glob.glob(os.path.join(run_dir, "color_id_buffer", "*.json")):
        if path.endswith("_anno.json"):
            continue
        try:
            with open(path) as handle:
                sidecar = json.load(handle)
        except (OSError, ValueError) as ex:
            out.setdefault("_unreadable", []).append("{0}: {1}".format(path, ex))
            continue
        view_id = sidecar.get("view_id")
        if view_id is not None:
            out[str(view_id)] = sidecar
    return out


def timing_rows(run_dir):
    """PURE over the files: one row per views_core view."""
    cores = sorted(glob.glob(os.path.join(run_dir, "views_core*.csv")))
    if not cores:
        raise FileNotFoundError("no views_core*.csv under {0}".format(run_dir))
    sidecars = _model_sidecars(run_dir)
    rows = []
    for core in cores:
        with open(core, newline="") as handle:
            for rec in csv.DictReader(handle):
                view_id = str(rec.get("ViewId"))
                row = {"view_id": view_id, "view_name": rec.get("ViewName"),
                       "elapsed_ms": None, "capture_total_ms": None,
                       "outside_capture_ms": None, "unaccounted_ms": None,
                       "reason": None}
                try:
                    row["elapsed_ms"] = round(float(rec.get("ElapsedSec")) * 1000.0, 3)
                except (TypeError, ValueError):
                    row["reason"] = "ElapsedSec unreadable: {0!r}".format(rec.get("ElapsedSec"))
                timings = ((sidecars.get(view_id) or {}).get("registration_marks") or {}
                           ).get("timings_ms")
                if not timings or timings.get("total") is None:
                    row["reason"] = row["reason"] or (
                        "no model sidecar" if view_id not in sidecars else
                        "the sidecar carries no registration_marks.timings_ms")
                else:
                    row["capture_total_ms"] = timings.get("total")
                    row["unaccounted_ms"] = timings.get("unaccounted")
                    if row["elapsed_ms"] is not None:
                        row["outside_capture_ms"] = round(
                            row["elapsed_ms"] - float(timings["total"]), 3)
                    row["phases_ms"] = dict(timings)
                rows.append(row)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("run_dir")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    rows = timing_rows(args.run_dir)
    if args.json:
        json.dump(rows, sys.stdout, indent=2, sort_keys=True)
        return 0
    print("{0:<28} {1:>10} {2:>10} {3:>10} {4:>10}  {5}".format(
        "view", "elapsed", "capture", "outside", "unacc.", "note"))
    for row in rows:
        def _f(v):
            return "-" if v is None else "{0:.0f}".format(v)
        print("{0:<28} {1:>10} {2:>10} {3:>10} {4:>10}  {5}".format(
            str(row["view_name"])[:28], _f(row["elapsed_ms"]),
            _f(row["capture_total_ms"]), _f(row["outside_capture_ms"]),
            _f(row["unaccounted_ms"]), row["reason"] or ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
