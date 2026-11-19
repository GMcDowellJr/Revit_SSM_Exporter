"""Is a slow Stage A export the VIEW's, or the CLOCK's?

    python tools/analyze_export_floor.py <views_timing_<d>.csv> [--slow-ms 15000]

docs/notes/export_floor_20261118T1002.md. Reads a run's views_timing CSV
(tools/stage_a_timing_report.py), rebuilds an approximate timeline of every
export, and reports what predicts a slow one:

* the hazard table: P(slow) by the time since the previous slow export ENDED,
  and by the time since it STARTED;
* the single threshold on either clock that best separates slow from fast,
  and its accuracy;
* the penalty: a slow export's duration less the fast baseline at its size
  is NOT computed here (sizes are in export_fields.csv); what is reported is
  the slow and fast duration quantiles per pass;
* when the export timer was split (``phase_<pass>_export.<part>_ms``
  columns, added for this diagnosis), which part holds the slow exports'
  excess.

The timeline is APPROXIMATE, and says so. Rows are taken in capture order
(sorted by the cumulative ``mem_view_start_page_faults`` when every captured
row has it, else file order). A view starts at the running sum of
``loop_gap_from_previous_ms + loop_total_ms``; the model export starts after
the pipeline steps before the capture, the partition phases before
model_pass, and the model pass's laps before its export (setup ..
suppress_commit), which are contiguous, so its error is the capture's
``unaccounted`` (~0.6 s median). The annotation export's start inside its
pass is not timed: it is placed halfway through the pass's non-export time,
an error of up to a few seconds.

Stdlib only. Exit 0 printed; 2 no captured row.
"""
from __future__ import annotations

import argparse
import csv
import sys

SLOW_MS = 15000.0
PRE_CAPTURE = ("preamble", "view_start_mark", "mode", "signature", "view_identity",
               "raster_init", "strategy_diag", "collect_elements")
PRE_MODEL = ("authored_override_scan", "split_remove", "lines_unhide",
             "co_sizing_first_attempt", "mark_layout", "marks_create")
MODEL_BEFORE_EXPORT = ("setup", "graphics_state", "template_detach", "suppress_setup",
                       "expand", "link_filters", "link_proxies", "near_face_w", "paint",
                       "suppress_commit")
EXPORT_PARTS = ("export_image", "list_stage", "move", "read_dims", "cleanup")
SINCE_BINS = {"since_end_s": (0, 10, 20, 24, 26, 28, 30, 32, 40, 60, 90, 1e9),
              "since_start_s": (0, 30, 40, 44, 46, 48, 50, 52, 54, 56, 60, 90, 1e9)}


def _f(row, key):
    value = row.get(key)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _z(row, key):
    value = _f(row, key)
    return 0.0 if value is None else value


def captured_rows(path):
    """The captured rows (no ``reason``) in capture order."""
    with open(path, newline="", encoding="utf-8") as handle:
        rows = [r for r in csv.DictReader(handle) if not (r.get("reason") or "").strip()]
    faults = [_f(r, "mem_view_start_page_faults") for r in rows]
    if rows and all(v is not None for v in faults):
        order = sorted(range(len(rows)), key=lambda i: faults[i])
        rows = [rows[i] for i in order]
    return rows


def export_events(rows):
    """``[{t, ms, pass, view_id, parts}]`` in time order; ``t`` in ms from
    the first captured view's start. Only CAPTURED rows advance the clock.
    A carried or unmeasurable row has loop time too (run 20261118T1002:
    median 5.9 s) but no page-fault stamp, so its place in the order is
    unknown and it is left out. Were such rows interleaved with the captured
    ones, the hazard table would blur; how sharp it comes out is the check.
    """
    events = []
    clock = 0.0
    for row in rows:
        start = clock + _z(row, "loop_gap_from_previous_ms")
        clock = start + _z(row, "loop_total_ms")
        cap = (start + _z(row, "loop_view_start_mark_ms")
               + sum(_z(row, "pipeline_{0}_ms".format(k)) for k in PRE_CAPTURE))
        model_start = cap + sum(_z(row, "phase_{0}_ms".format(k)) for k in PRE_MODEL)
        model_ms = _f(row, "phase_model_export_ms")
        if model_ms is not None:
            events.append({
                "t": model_start + sum(_z(row, "phase_model_{0}_ms".format(k))
                                       for k in MODEL_BEFORE_EXPORT),
                "ms": model_ms, "pass": "model", "view_id": row.get("view_id"),
                "parts": _parts(row, "model")})
        anno_ms = _f(row, "phase_annotation_export_ms")
        if anno_ms is not None:
            anno_start = (model_start + _z(row, "phase_model_pass_ms")
                          + _z(row, "phase_suppression_ms"))
            rest = (_z(row, "phase_annotation_pass_ms") - anno_ms
                    - _z(row, "phase_sidecar_write_annotation_ms"))
            events.append({"t": anno_start + max(0.0, rest) / 2.0, "ms": anno_ms,
                           "pass": "annotation", "view_id": row.get("view_id"),
                           "parts": _parts(row, "annotation")})
    events.sort(key=lambda e: e["t"])
    return events


def _parts(row, name):
    out = {}
    for part in EXPORT_PARTS:
        value = _f(row, "phase_{0}_export.{1}_ms".format(name, part))
        if value is not None:
            out[part] = value
    return out


def annotate(events, slow_ms=SLOW_MS):
    """PURE. Adds ``slow``, ``since_end_s`` and ``since_start_s`` (time since
    the previous slow export ended / started; None before the first)."""
    last_end = last_start = None
    for ev in events:
        ev["slow"] = ev["ms"] > slow_ms
        ev["since_end_s"] = None if last_end is None else (ev["t"] - last_end) / 1000.0
        ev["since_start_s"] = None if last_start is None else (ev["t"] - last_start) / 1000.0
        if ev["slow"]:
            last_end, last_start = ev["t"] + ev["ms"], ev["t"]
    return events


def hazard(events, key, bins=None):
    """PURE. ``[(lo, hi, n, n_slow)]`` over ``key``."""
    bins = bins or SINCE_BINS[key]
    out = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        sel = [e for e in events if e[key] is not None and lo < e[key] <= hi]
        out.append((lo, hi, len(sel), sum(1 for e in sel if e["slow"])))
    return out


def best_threshold(events, key, lo=5.0, hi=120.0, step=0.5):
    """PURE. ``(threshold_s, accuracy)`` of "slow iff key > threshold" over
    the events that have ``key``; ties go to the lowest threshold."""
    sel = [e for e in events if e[key] is not None]
    if not sel:
        return None, None
    best = (None, -1.0)
    th = lo
    while th <= hi:
        acc = sum(1 for e in sel if e["slow"] == (e[key] > th)) / float(len(sel))
        if acc > best[1]:
            best = (th, acc)
        th += step
    return best


def quantiles(values, qs=(5, 25, 50, 75, 95)):
    values = sorted(values)
    if not values:
        return []
    return [values[min(len(values) - 1, int(round(q / 100.0 * (len(values) - 1))))]
            for q in qs]


def report(events, out=None):
    out = out or sys.stdout
    slow = [e for e in events if e["slow"]]
    out.write("exports {0}, slow {1}\n".format(len(events), len(slow)))
    for name in ("model", "annotation"):
        s = [e["ms"] / 1000.0 for e in events if e["pass"] == name and e["slow"]]
        f = [e["ms"] / 1000.0 for e in events if e["pass"] == name and not e["slow"]]
        out.write("{0}: slow n={1} q5..q95={2}; fast n={3} q5..q95={4}\n".format(
            name, len(s), [round(v, 2) for v in quantiles(s)], len(f),
            [round(v, 2) for v in quantiles(f)]))
    starts = [e["t"] for e in slow]
    gaps = [(b - a) / 1000.0 for a, b in zip(starts, starts[1:])]
    out.write("slow-to-slow start gap s q5..q95: {0}\n".format(
        [round(v, 1) for v in quantiles(gaps)]))
    for key in ("since_end_s", "since_start_s"):
        out.write("\nP(slow) by {0}\n".format(key))
        for lo, hi, n, k in hazard(events, key):
            if n:
                out.write("  ({0:g}, {1:g}] n={2} slow={3:.2f}\n".format(lo, hi, n, k / float(n)))
        th, acc = best_threshold(events, key)
        out.write("  best single threshold {0} s, accuracy {1:.4f}\n".format(th, acc))
    with_parts = [e for e in events if e["parts"]]
    if with_parts:
        out.write("\nexport parts (median ms), slow vs fast:\n")
        for part in EXPORT_PARTS:
            s = [e["parts"][part] for e in with_parts if e["slow"] and part in e["parts"]]
            f = [e["parts"][part] for e in with_parts if not e["slow"] and part in e["parts"]]
            out.write("  {0}: slow {1} fast {2}\n".format(
                part, quantiles(s, (50,)), quantiles(f, (50,))))
    else:
        out.write("\nno phase_<pass>_export.<part>_ms columns: the run predates the "
                  "split export timer\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("views_timing_csv")
    parser.add_argument("--slow-ms", type=float, default=SLOW_MS)
    args = parser.parse_args(argv)
    rows = captured_rows(args.views_timing_csv)
    if not rows:
        print("refused: no captured row in {0}".format(args.views_timing_csv),
              file=sys.stderr)
        return 2
    report(annotate(export_events(rows), slow_ms=args.slow_ms))
    return 0


if __name__ == "__main__":
    sys.exit(main())
