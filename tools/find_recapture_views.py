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
* ``capture_failed`` -- run_meta.json records the view's capture as
  ``failed``, ``annotation_failed`` or ``registration_failed`` (the reason
  is the row's), whether an older sidecar is still on disk for it or none
  is (``provenance`` ``no_sidecar``). The analysis never grids such a view,
  so a series shows it as a measure lost (2025-09-13 to 2025-10-25: six
  "(N) ... Wall Types BP" plans). Listed under ``failed`` in every run's
  entry; ``--include-failed`` puts the ids in ``recapture`` too;
* ``below_capture_floor`` -- the view is below the capture floor
  (``vop_interwoven/capture_floor.py``): run_meta records it
  ``unmeasurable``, or a build before that status recorded the tick rule
  as ``registration_failed`` with exactly ``registration_marks_incomplete;
  registration_marks_may_not_draw`` AND the model sidecar on disk records
  the layout refused for size (``evidence`` ``sidecar``). Unmeasurable, not
  failed: a recapture would refuse the same way, so these are listed under
  ``below_floor`` in every run's entry and NEVER put in ``recapture``, not
  even by ``--include-failed``. The old shape WITHOUT a sidecar stays
  ``capture_failed``; its reason says it is a floor candidate;
* ``not_this_run`` / ``not_requested`` -- a sidecar that is not the run's own
  capture (``provenance``) for another reason (an ambiguous outcome, a view
  the run never requested), listed and not counted.

Outputs, in ``--out``: ``recapture_views.csv`` (one row per view, every run)
and ``recapture_lists.json`` (per run: run_dir, run_id, as-of date, the
view ids to recapture, the ids to review, the failed captures, the views
below the capture floor) -- the list
a patch capture takes. A run whose run_meta.json is refused by the roll-up's
identity rules is listed with the reason and contributes no views.

    python tools/find_recapture_views.py <run> [<run> ...] --out <dir> [--include-failed]
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
from vop_interwoven import capture_floor  # noqa: E402

SCHEMA = "vop.stage_a.recapture_views.v1"
# 1.1.0 (2026-10-13): run_dir is the run's RECORDED directory
# (run_meta.run_dir) with the scanned path beside it; rows join their run by
# capture directory; the found counts are compared with the ticks PAINTED.
# 1.2.0 (2026-10-15): a capture run_meta records as failed is `capture_failed`
# (reason from run_meta; a row even with no sidecar), listed per run under
# `failed`; --include-failed adds the ids to `recapture`.
# 1.3.0 (2026-10-15): a view below the capture floor (capture_floor.floor_rule:
# run_meta `unmeasurable`, or the old registration_failed shape with the
# sidecar's size refusal) is `below_capture_floor`, listed per run under
# `below_floor` and never in `recapture`.
TOOL_VERSION = "1.3.0"
CSV_NAME = "recapture_views.csv"
LISTS_NAME = "recapture_lists.json"
# run_meta capture statuses that are a failed capture (csv_export.
# stage_a_capture_status); "success" and "unmeasurable" are the only other
# values it writes.
FAILED_STATUSES = ("failed", "annotation_failed", "registration_failed")
BELOW_FLOOR = "below_capture_floor"
COLUMNS = ["run_dir", "scanned_dir", "run_id", "as_of_date", "view_id", "view_stem",
           "view_name", "view_type", "scale", "provenance", "registration_state", "verdict",
           "marks_expected", "marks_painted", "marks_blocked", "anno_found_count",
           "model_found_count", "reason"]


def verdict(row):
    """PURE. ``(verdict, reason)`` for one census row."""
    if row.get("provenance") != "this_run":
        return row.get("provenance") or "unknown", "not the run's own capture"
    state = row.get("registration_state")
    expected = _int(row.get("marks_expected"))
    painted = _int(row.get("marks_painted"))
    # The ticks the capture PAINTED, not the ones it planned: a tick whose
    # override failed never drew, and relocating the ticks would not change
    # that (Codex, PR #236).
    drawn = painted if painted is not None else expected
    if state == "registered":
        return "keep", ""
    if state == "stale":
        return "review", "registration record does not match the files: " + str(
            row.get("registration_reason"))
    if state == "absent":
        if drawn:
            return "recapture", "ticks drawn but no registration record"
        if expected and not drawn:
            return "review", "{0} ticks planned, none painted (overrides failed), and no " \
                             "registration record".format(expected)
        return "review", "no ticks drawn and no registration record"
    found = [row.get("anno_found_count"), row.get("model_found_count")]
    counts = [c for c in (_int(f) for f in found) if c is not None]
    blocked = _int(row.get("marks_blocked"))
    anno, model = _int(found[0]), _int(found[1])
    # Ticks the annotation itself covers (2025-11-01 view 11068525: two
    # annotation symbols span the whole frame; every tick is placed
    # "blocked" and painted over in the annotation pass while the model pass
    # finds all twelve). Relocating the ticks cannot change that: review.
    if state == "refused" and expected and blocked is not None and blocked >= expected:
        return "review", "every tick blocked: annotation covers the frame, no band to move " \
                         "to; a recapture would not change it: {0}".format(
                             row.get("registration_reason"))
    if (state == "refused" and drawn and model is not None and anno is not None
            and model >= drawn and anno < drawn):
        return "review", "the model pass found every tick, the annotation pass {0} of {1}: " \
                         "annotation paints over the ticks; a recapture would not change " \
                         "it: {2}".format(anno, drawn, row.get("registration_reason"))
    if state == "refused" and drawn and counts and min(counts) < drawn:
        return "recapture", "{0} / {1} ticks found of {2} painted: {3}".format(
            found[0], found[1], drawn, row.get("registration_reason"))
    if state == "refused" and expected and painted is not None and painted < expected:
        return "review", "{0} of {1} ticks painted (overrides failed), every painted tick " \
                         "found: {2}".format(painted, expected, row.get("registration_reason"))
    if state == "refused":
        return "review", "refused with every tick found: " + str(row.get("registration_reason"))
    return "review", "registration state {0!r}".format(state)


def _outcome_entry(vid, outcome):
    return {"view_id": int(vid), "view_name": outcome.get("view_name"),
            "capture_status": outcome.get("capture_status"),
            "reason": outcome.get("capture_failure_reason") or ""}


def failed_captures(ident):
    """PURE. The views run_meta.json records as a failed capture, from the
    run's identity (``stage_a_grid_rollup.run_identity``): ``[{view_id,
    view_name, capture_status, reason}]`` sorted by id. An ambiguous outcome
    (two entries that disagree, None in the identity) is not a failure that
    can be named and is left out; a refused identity yields nothing. A
    capture whose run_meta outcome alone names a floor rule
    (``capture_floor.floor_rule`` with no sidecar) is NOT a failure: see
    ``below_floor_captures``."""
    if ident.get("refused"):
        return []
    failed = []
    for vid, outcome in ident["outcomes"].items():
        if outcome is None or outcome.get("capture_status") not in FAILED_STATUSES:
            continue
        if capture_floor.floor_rule(outcome.get("capture_status"),
                                    outcome.get("capture_failure_reason")) is not None:
            continue
        failed.append(_outcome_entry(vid, outcome))
    return sorted(failed, key=lambda f: f["view_id"])


def below_floor_captures(ident):
    """PURE. The views run_meta.json alone records as below the capture
    floor (``unmeasurable``, or a floor rule under a failed status), as
    ``failed_captures`` shapes them plus ``rule`` and ``evidence``. The old
    registration_failed shape needs the sidecar and is decided per row in
    ``find``, not here."""
    if ident.get("refused"):
        return []
    below = []
    for vid, outcome in ident["outcomes"].items():
        if outcome is None:
            continue
        rule = capture_floor.floor_rule(outcome.get("capture_status"),
                                        outcome.get("capture_failure_reason"))
        if rule is None:
            continue
        below.append(dict(_outcome_entry(vid, outcome), **rule))
    return sorted(below, key=lambda f: f["view_id"])


def failed_reason(failure):
    """PURE. The row's reason for a failed capture; the old floor shape
    without a sidecar to decide it says it is a candidate."""
    reason = "capture_status {0}: {1}".format(failure["capture_status"],
                                              failure["reason"] or "(no reason recorded)")
    if capture_floor.old_shape_candidate(failure["capture_status"], failure["reason"]):
        reason += ("; a below-floor candidate (an unavailable tick layout records these "
                   "two faults) with no sidecar on disk to confirm it")
    return reason


def below_floor_reason(entry):
    """PURE. The row's reason for a view below the capture floor."""
    return "{0} ({1}): capture_status {2}: {3}".format(
        BELOW_FLOOR, entry["evidence"], entry["capture_status"],
        entry["reason"] or "(no reason recorded)")


def _registration_record(folder, row):
    """The MODEL sidecar's ``registration_marks`` record for a census row,
    or None when the sidecar is not there or does not read."""
    stem = row.get("view_stem")
    if not stem:
        return None
    try:
        with open(str(Path(folder) / (stem + ".json")), encoding="utf-8") as handle:
            sidecar = json.load(handle)
    except (OSError, ValueError):
        return None
    return sidecar.get("registration_marks") if isinstance(sidecar, dict) else None


def _int(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def find(run_args, include_failed=False):
    runs, rows = census.census(run_args, pixels=False)
    out_rows, lists = [], []
    for run in runs:
        folder = Path(run["capture_dir"])
        meta = dsc.find_run_meta(folder / "run_meta.json") or {}
        ident = census.rollup.run_identity(meta)
        failed = failed_captures(ident)
        failed_by_id = dict((str(f["view_id"]), f) for f in failed)
        below = below_floor_captures(ident)
        below_by_id = dict((str(f["view_id"]), f) for f in below)
        # run_dir is the directory the RUN records (run_meta.run_dir, the
        # exporter's own path): the list is consumed where the run lives,
        # not where it was scanned (a copy, a temp folder); the scanned path
        # is kept beside it.
        entry = {"run_dir": meta.get("run_dir") or str(folder.parent),
                 "scanned_dir": str(folder.parent), "run_id": run.get("run_id"),
                 "as_of_date": meta.get("date"), "refused": run.get("run_meta_refused"),
                 "recapture": [], "review": [], "keep": 0, "failed": failed,
                 "below_floor": below, "include_failed": bool(include_failed)}
        with_sidecar = set()
        for r in rows:
            if r.get("capture_dir") != run["capture_dir"]:
                continue
            v, why = verdict(r)
            vid_key = census.rollup._vid(r.get("view_id"))
            failure = failed_by_id.get(vid_key)
            floor_entry = below_by_id.get(vid_key)
            if floor_entry is not None and r.get("provenance") == "not_this_run":
                v, why = BELOW_FLOOR, below_floor_reason(floor_entry)
                with_sidecar.add(floor_entry["view_id"])
            elif failure is not None and r.get("provenance") == "not_this_run":
                # The sidecar on disk is an older capture's (or the failed
                # attempt's own); the run's record of the view is the failure.
                # The old floor shape is decided by THIS sidecar's
                # registration record (capture_floor.floor_rule, "sidecar").
                rule = capture_floor.floor_rule(failure["capture_status"], failure["reason"],
                                                _registration_record(folder, r))
                if rule is not None:
                    floor_entry = dict(failure, **rule)
                    below.append(floor_entry)
                    failed.remove(failure)
                    v, why = BELOW_FLOOR, below_floor_reason(floor_entry)
                else:
                    v, why = "capture_failed", failed_reason(failure)
                with_sidecar.add(failure["view_id"])
            out_rows.append(dict((c, r.get(c)) for c in COLUMNS if c in r) | {
                "run_dir": entry["run_dir"], "scanned_dir": entry["scanned_dir"],
                "as_of_date": entry["as_of_date"], "verdict": v, "reason": why})
            if v == "recapture":
                entry["recapture"].append(int(r["view_id"]))
            elif v == "review":
                entry["review"].append(int(r["view_id"]))
            elif v == "keep":
                entry["keep"] += 1
        for which, items, reason_of in (("capture_failed", failed, failed_reason),
                                        (BELOW_FLOOR, below, below_floor_reason)):
            for item in items:
                if item["view_id"] not in with_sidecar:
                    # No sidecar at all: the capture wrote nothing for the view.
                    out_rows.append(dict((c, None) for c in COLUMNS) | {
                        "run_dir": entry["run_dir"], "scanned_dir": entry["scanned_dir"],
                        "run_id": entry["run_id"], "as_of_date": entry["as_of_date"],
                        "view_id": item["view_id"], "view_name": item["view_name"],
                        "provenance": "no_sidecar", "verdict": which,
                        "reason": reason_of(item)})
        below.sort(key=lambda f: f["view_id"])
        if include_failed:
            # Never the views below the floor: a recapture refuses them the
            # same way (CaptureBelowFloor), and the list must not say
            # otherwise.
            entry["recapture"].extend(f["view_id"] for f in failed)
        entry["recapture"].sort()
        entry["review"].sort()
        lists.append(entry)
    return lists, out_rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("runs", nargs="+", metavar="run")
    ap.add_argument("--out", required=True)
    ap.add_argument("--include-failed", action="store_true",
                    help="put the views run_meta.json records as a failed capture "
                         "(failed, annotation_failed, registration_failed) in each "
                         "run's recapture list; they are listed under 'failed' either way. "
                         "Views below the capture floor ('below_floor') are never added")
    args = ap.parse_args(argv)
    out = Path(args.out)
    for run_arg in args.runs:
        _s, folder = grid.model_sidecars(run_arg)
        if census.rollup._inside(out, folder):
            ap.error("--out {0} is inside the capture folder {1}".format(out, folder))
    lists, rows = find(args.runs, include_failed=args.include_failed)
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
        print("{0}: run {1} as of {2}: recapture {3}, review {4}, keep {5}, failed "
              "captures {6}{7}, below the capture floor {8}{9}".format(
                  e["run_dir"], e["run_id"], e["as_of_date"], len(e["recapture"]),
                  len(e["review"]), e["keep"], len(e["failed"]),
                  " (in the recapture list)" if e["failed"] and e["include_failed"] else "",
                  len(e["below_floor"]),
                  "  REFUSED: " + str(e["refused"]) if e["refused"] else ""))
    print("{0} views to recapture across {1} runs; wrote {2} and {3}".format(
        total, len(lists), out / CSV_NAME, out / LISTS_NAME))
    return 0


if __name__ == "__main__":
    sys.exit(main())
