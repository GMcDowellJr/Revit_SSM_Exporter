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

ONE RUN ONLY (Codex, PR #222). A sidecar carries no run id, so a join on
ViewId alone would hand a historical views_core row -- or a view whose capture
failed and left an older sidecar in place -- plausible timings from another
run. So the run is fixed by ``run_meta.json``: only views_core rows whose
RunId is that run's are read, and a view's sidecar is joined only when
run_meta records its capture as "success". Any other row is reported with the
reason, never joined. A run directory without a readable run_meta.json is
refused.

Nothing is hidden: a view whose sidecar carries no timings (a run before P2)
is reported with the reason, never dropped. Standard library only.

Every run writes the report into the run directory, beside views_core, named
as the run's other dated files are: ``views_timing_<suffix>.csv`` and
``views_timing_<suffix>.json``, where ``<suffix>`` is whatever the
thinrunner's date/override named views_core_<suffix>.csv by -- a tag such as
``PR_221`` as given, a date as YYYY-MM-DD. It is read from that file's name;
run_meta's run_tag, else its date, stands in only when no views_core file
names one, and a run that names none (or two) is refused rather than named by
guess. The files are written only after every row is built.

    python tools/stage_a_timing_report.py <run_dir> [--json]

Exit: 0 written; 2 the run could not be reported (no run_meta.json, no
run_id, no views_core, no date/override to name it by).
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sys


# The production MODEL sidecar's own name: "<view name>_<view id>.json".
# Derived files beside it -- <x>.decoded.json, <x>_anno.registered.json -- and
# the annotation sidecar (<x>_anno.json) carry the same view_id, so matching
# on "*.json" let whichever the filesystem listed last win (Codex, PR #222).
_MODEL_SIDECAR_NAME = re.compile(r"_(\d+)\.json$")


def _model_sidecars(run_dir):
    """``{view_id: sidecar}`` for the model sidecars only. A view id claimed
    by two files is not guessed between: it maps to None, and the row says
    so."""
    out = {}
    for path in sorted(glob.glob(os.path.join(run_dir, "color_id_buffer", "*.json"))):
        match = _MODEL_SIDECAR_NAME.search(os.path.basename(path))
        if match is None:
            continue
        try:
            with open(path) as handle:
                sidecar = json.load(handle)
        except (OSError, ValueError) as ex:
            out.setdefault("_unreadable", []).append("{0}: {1}".format(path, ex))
            continue
        view_id = sidecar.get("view_id")
        if view_id is None or str(view_id) != match.group(1):
            continue
        key = str(view_id)
        out[key] = None if key in out else sidecar
    return out


TIMING_STEM = "views_timing"
_CORE_NAME = re.compile(r"^views_core_(.+)\.csv$")

# Fixed CSV columns; the per-phase columns follow, one per phase key.
CSV_COLUMNS = ("run_id", "view_id", "view_name", "elapsed_ms", "capture_total_ms",
               "outside_capture_ms", "unaccounted_ms", "reason")


def _run_meta(run_dir):
    path = os.path.join(run_dir, "run_meta.json")
    with open(path) as handle:
        return json.load(handle)


def file_date_str(run_dir):
    """The suffix of the run's dated filenames -- what the thinrunner's
    date/override named them by (run_meta.identity_file_date_str(): the
    override as given when it is a tag such as "PR_221", its YYYY-MM-DD when
    it is a date, else the run's as-of date).

    Read from the name the exporter actually wrote, views_core_<suffix>.csv,
    so the report cannot drift from it. Only when no views_core file names
    one does run_meta stand in: its ``run_tag`` (the override, when a tag),
    else its ``date``. Raises when neither tells it, or when views_core files
    name more than one suffix."""
    suffixes = set()
    for core in glob.glob(os.path.join(run_dir, "views_core*.csv")):
        match = _CORE_NAME.match(os.path.basename(core))
        if match:
            suffixes.add(match.group(1))
    if len(suffixes) == 1:
        return suffixes.pop()
    if len(suffixes) > 1:
        raise ValueError("views_core files under {0} name more than one date/override "
                         "({1}); not guessed between".format(run_dir, sorted(suffixes)))
    meta = _run_meta(run_dir)
    named = meta.get("run_tag") or meta.get("date")
    if named:
        return str(named)
    raise ValueError("neither a views_core_<date>.csv nor run_meta.json's run_tag/date "
                     "under {0} names the run; refusing to name the timing report "
                     "by guess".format(run_dir))


def output_paths(run_dir):
    """``(csv_path, json_path)`` the report is written to."""
    stem = os.path.join(run_dir, "{0}_{1}".format(TIMING_STEM, file_date_str(run_dir)))
    return stem + ".csv", stem + ".json"


def _phase_keys(rows):
    keys = set()
    for row in rows:
        keys.update(row.get("phases_ms") or {})
    return sorted(keys - {"total", "unaccounted"})


def write_report(run_dir, rows):
    """Write ``rows`` (finished) as CSV and JSON into the run directory;
    returns ``(csv_path, json_path)``."""
    csv_path, json_path = output_paths(run_dir)
    phases = _phase_keys(rows)
    with open(csv_path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(list(CSV_COLUMNS) + ["phase_{0}_ms".format(k) for k in phases])
        for row in rows:
            timed = row.get("phases_ms") or {}
            writer.writerow([("" if row.get(c) is None else row.get(c)) for c in CSV_COLUMNS]
                            + [("" if timed.get(k) is None else timed.get(k)) for k in phases])
    with open(json_path, "w") as handle:
        json.dump({"run_id": _run_identity(run_dir)[0],
                   "file_date": file_date_str(run_dir), "views": rows},
                  handle, indent=2, sort_keys=True)
    return csv_path, json_path


def _run_identity(run_dir):
    """``(run_id, {view_id: capture_status})`` from run_meta.json, or raises."""
    path = os.path.join(run_dir, "run_meta.json")
    meta = _run_meta(run_dir)
    run_id = meta.get("run_id")
    if not run_id:
        raise ValueError("{0} carries no run_id; refusing to join timings "
                         "across runs".format(path))
    status = dict((str(v.get("view_id")), v.get("capture_status"))
                  for v in meta.get("views") or [] if isinstance(v, dict))
    return str(run_id), status


def timing_rows(run_dir):
    """PURE over the files: one row per views_core view OF THIS RUN."""
    run_id, capture_status = _run_identity(run_dir)
    cores = sorted(glob.glob(os.path.join(run_dir, "views_core*.csv")))
    if not cores:
        raise FileNotFoundError("no views_core*.csv under {0}".format(run_dir))
    sidecars = _model_sidecars(run_dir)
    rows = []
    for core in cores:
        with open(core, newline="") as handle:
            for rec in csv.DictReader(handle):
                if str(rec.get("RunId")) != run_id:
                    continue
                view_id = str(rec.get("ViewId"))
                row = {"view_id": view_id, "view_name": rec.get("ViewName"),
                       "elapsed_ms": None, "capture_total_ms": None,
                       "outside_capture_ms": None, "unaccounted_ms": None,
                       "reason": None}
                try:
                    row["elapsed_ms"] = round(float(rec.get("ElapsedSec")) * 1000.0, 3)
                except (TypeError, ValueError):
                    row["reason"] = "ElapsedSec unreadable: {0!r}".format(rec.get("ElapsedSec"))
                row["run_id"] = run_id
                status = capture_status.get(view_id)
                if status != "success":
                    row["reason"] = row["reason"] or (
                        "run_meta records capture_status {0!r}; a sidecar beside "
                        "it may be another run's, so it is not joined".format(status))
                    rows.append(row)
                    continue
                if view_id in sidecars and sidecars[view_id] is None:
                    row["reason"] = row["reason"] or (
                        "more than one model sidecar claims this view; not guessed")
                    rows.append(row)
                    continue
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
    try:
        rows = timing_rows(args.run_dir)
        csv_path, json_path = write_report(args.run_dir, rows)
    except (OSError, ValueError) as ex:      # FileNotFoundError is an OSError
        print("timing report refused: {0}: {1}".format(type(ex).__name__, ex),
              file=sys.stderr)
        return 2
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
    print("wrote {0}\nwrote {1}".format(csv_path, json_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
