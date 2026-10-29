#!/usr/bin/env python3
"""Score hash-and-skip's would-skip verdicts against the captures (phase 1).

docs/DESIGN_CHANGE_SKIP.md. Given the runs of ONE model in series order (each
with its ``view_fingerprints_<d>.json``, written beside the capture or by the
batch's fingerprint backfill), for every consecutive pair (A = the previous
archive, B = this one):

  * the verdict production would give each of B's views,
    ``vop_interwoven.view_fingerprint.skip_verdict`` -- the one function, so
    this scores what phase 2 would do, not a copy of it;
  * for every would-skip view, whether its capture really is the same:
    tools/analyze_change_detection_probe's comparison (decoded ids per pass,
    ticks and the capture's own lines excluded, the frame, the bbox maps).
    A would-skip view whose capture differs is a FALSE UNCHANGED;
  * with ``--compare-all``, the same comparison for the views it would
    capture, which measures over-caution (slower: every view is decoded).

"The same capture" is judged on what ANALYSIS reads (``--basis grid``, the
default): the analysis grid of both runs (``grid/<stem>.grid.json`` + npz,
tools/stage_a_grid.py) must agree on the grid itself (cell size, origin),
on every element x cell row's PRESENCE per field (ec_px, ec_ink_px,
ec_black_px, ec_model_ink_under_px > 0 -- counts may differ) and on every
cell's occupancy class. The grid decides first; the TIFFs are decoded only
to describe a view the grid finds different. A view whose capture files are
not byte-identical but whose grid is the same is counted ``grid_tolerated``
inside ``true_unchanged``. Two captures of
one drafting view in different sessions differ by text anti-aliasing with no
model change at all (OBSERVED 2026-10-29: four archive pairs pixel-identical,
the other four 12-396 views a pair off by a few pixels each).

A row present in ONE run only and carried there by at most ``BOUNDARY_PX``
pixels is a fringe pixel that landed across a cell boundary, not an edit
(OBSERVED 2026-10-29: the last 11 grid differences of the series were each
exactly two one-sided rows -- one element's ec_px and ec_ink_px in one
cell -- and three of those views flipped back the following week). A view
whose only grid differences are such rows, and the occupancy of the cells
they lie in, is counted ``boundary_tolerated``.

Where either grid is missing or refused, or the two grids were made by
different stage_a_grid versions, the pixel comparison decides: exact for the
model pass, and for the annotation pass tolerant of FRINGE (``acd.fringe_only``:
white on one side, the same element within ``acd.FRINGE_RADIUS_PX`` on the
other), counted ``fringe_tolerated``. ``--exact`` turns that off; ``--basis
pixels`` never reads the grids. Each false unchanged records its ``basis``.

Acceptance (S-13, Greg 2026-10-28): 0 false unchanged over at least
``--min-pairs`` (1,000) compared would-skip views, and per view type at least
``--min-per-type`` (30) with 0 false unchanged. A type that has not met its
bar keeps being captured; the record says which types have.

    python tools/validate_change_skip.py RUN_A RUN_B [RUN_C ...] [--compare-all]
    python tools/validate_change_skip.py --root EXPORTS_DIR [--jobs 4] ...
        [--out validate_change_skip.json] [--min-pairs 1000] [--min-per-type 30]
        [--basis grid|pixels] [--exact] [--jobs N]

``--root`` finds every finalized run under a folder and makes one series per
model folder (the folder that holds the runs), in as-of date order; a second
run of the same as-of date in one series is dropped and listed (the latest
run id is kept). ``--jobs`` scores pairs in parallel processes.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from tools import analyze_change_detection_probe as acd  # noqa: E402
from tools import decode_stage_a_color_id as dsc  # noqa: E402
from tools import stage_a_kinds as kinds  # noqa: E402
from vop_interwoven import output_layout  # noqa: E402
from vop_interwoven import view_fingerprint as vf  # noqa: E402

SCHEMA = "vop.validate_change_skip.v1"
TOOL_VERSION = "1.5.0"
OUTCOMES = ("true_unchanged", "false_unchanged", "not_comparable")
TOLERATED = ("fringe_tolerated", "grid_tolerated", "boundary_tolerated")
# The most pixels a one-sided element x cell row may carry and still be a
# fringe pixel across a cell boundary (module docstring).
BOUNDARY_PX = 2
BASES = ("grid", "pixels")
EC_FIELDS = ("ec_px", "ec_ink_px", "ec_black_px", "ec_model_ink_under_px")


# --- the grid basis ----------------------------------------------------------------

def grid_record(grid_dir, view_id):
    """The view's ``*_<id>.grid.json`` in ``grid_dir``, or None. Two is
    ambiguous and refused."""
    grid_dir = Path(grid_dir)
    if not grid_dir.is_dir():
        return None
    hits = [p for p in grid_dir.glob("*_{0}.grid.json".format(view_id))]
    if len(hits) > 1:
        raise acd.Refusal("view {0}: more than one grid record in {1}: {2}".format(
            view_id, grid_dir, sorted(p.name for p in hits)))
    return hits[0] if hits else None


def grid_facts(rec, arrays):
    """PURE. What the comparison reads from one view's grid: the lattice
    (cell size and origin), ``present`` {(key, field, i, j): count} for every
    nonzero row, and each occupied cell's occupancy code."""
    spec = rec.get("grid") or {}
    keys = rec.get("ec_keys") or []
    present = {}
    idx = np.asarray(arrays.get("ec_key", []), dtype=np.int64)
    ci = np.asarray(arrays.get("ec_i", []), dtype=np.int64)
    cj = np.asarray(arrays.get("ec_j", []), dtype=np.int64)
    for field in EC_FIELDS:
        if field not in arrays:
            continue
        values = np.asarray(arrays[field])
        for r in np.nonzero(values > 0)[0]:
            k = keys[int(idx[r])]
            present[(k.get("layer"), k.get("element_id"), k.get("link_category"),
                     field, int(ci[r]), int(cj[r]))] = int(values[r])
    occupied = {}
    occ = arrays.get("occupancy")
    if occ is not None:
        i0, j0 = (spec.get("i_range") or [0])[0], (spec.get("j_range") or [0])[0]
        rows, cols = np.nonzero(np.asarray(occ))
        for r, c in zip(rows, cols):
            occupied[(int(c) + i0, int(r) + j0)] = int(occ[r, c])
    lattice = (spec.get("cell_ft"), tuple(spec.get("origin_uv") or ()))
    return {"lattice": lattice, "present": present, "occupied": occupied,
            "tool_version": rec.get("tool_version")}


def grid_diff(fa, fb, boundary_px=BOUNDARY_PX):
    """PURE. Two grid_facts compared. ``differs`` is False when they agree,
    and also when every difference is boundary noise (``boundary_only``):
    each one-sided row carries at most ``boundary_px`` pixels where it
    exists, and each cell whose occupancy differs holds such a row."""
    lattice_equal = fa["lattice"] == fb["lattice"]
    added = dict((k, v) for k, v in fb["present"].items() if k not in fa["present"])
    removed = dict((k, v) for k, v in fa["present"].items() if k not in fb["present"])
    cells = set(fa["occupied"]) | set(fb["occupied"])
    differ = [c for c in cells if fa["occupied"].get(c, 0) != fb["occupied"].get(c, 0)]
    one_sided = list(added.items()) + list(removed.items())
    heavy = [k for k, v in one_sided if v > boundary_px]
    noisy_cells = set((k[4], k[5]) for k, _v in one_sided)
    unexplained = [c for c in differ if c not in noisy_cells]
    exact = not (added or removed or differ)
    boundary_only = bool(lattice_equal and not exact and not heavy and not unexplained)
    return {"lattice_equal": lattice_equal, "rows_added": len(added),
            "rows_removed": len(removed), "cells_differ": len(differ),
            "one_sided_max_px": max([v for _k, v in one_sided] or [0]),
            "boundary_px": boundary_px, "boundary_only": boundary_only,
            "differs": bool(not lattice_equal or not (exact or boundary_only))}


def _tiff_of(sidecar):
    """The capture TIFF of a sidecar: ``<stem>.tiff`` beside it, else the one
    its record names (parsing a model sidecar is the slow way)."""
    beside = Path(sidecar).with_suffix(".tiff")
    if beside.is_file():
        return beside
    return dsc._resolve_tiff_path(Path(sidecar), acd._load(sidecar))


def files_identical(files_a, files_b):
    """True when every capture TIFF (model and annotation) of the view is
    byte-identical in both runs; False when any differs or exists on one
    side only."""
    for key in ("model", "anno"):
        sa, sb = files_a.get(key), files_b.get(key)
        if bool(sa) != bool(sb):
            return False
        if not sa:
            continue
        if acd.sha256_file(_tiff_of(sa)) != acd.sha256_file(_tiff_of(sb)):
            return False
    return True


def tolerated_kind(entry):
    """PURE. Which tolerance made a true_unchanged view the same, or None when
    it was the same outright: ``boundary_tolerated`` (grid, boundary rows
    only), ``grid_tolerated`` (grid the same, capture files not identical),
    ``fringe_tolerated`` (pixel basis, annotation fringe)."""
    if not entry:
        return None
    if entry.get("basis") == "grid":
        if (entry.get("grid") or {}).get("boundary_only"):
            return "boundary_tolerated"
        return None if entry.get("files_identical") else "grid_tolerated"
    return "fringe_tolerated" if acd.capture_differs(entry) else None


def compare_grid(view_id, grid_a, grid_b):
    """``(differs or None, record)``: None when the grid cannot decide (a
    record missing or refused, or made by different grid versions)."""
    paths = [grid_record(grid_a, view_id), grid_record(grid_b, view_id)]
    if None in paths:
        return None, {"reason": "no grid record in run {0}".format(
            "A" if paths[0] is None else "B")}
    facts = []
    for path in paths:
        try:
            rec, arrays = kinds.load_grid(path)
        except kinds.KindsRefusal as ex:
            return None, {"reason": "{0}: {1}".format(path.name, ex)}
        facts.append(grid_facts(rec, arrays))
    if facts[0]["tool_version"] != facts[1]["tool_version"]:
        return None, {"reason": "grid versions differ: {0} / {1}".format(
            facts[0]["tool_version"], facts[1]["tool_version"])}
    out = grid_diff(*facts)
    return out["differs"], out


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_run(run_dir):
    """``{run_dir, meta, fingerprints, statuses, reason}``: a run's record,
    its fingerprints file (None with the reason when it has none) and each
    view's capture_status."""
    run_dir = Path(run_dir)
    path, why = output_layout.find_run_meta(str(run_dir))
    if path is None:
        return {"run_dir": str(run_dir), "meta": None, "fingerprints": None,
                "statuses": {}, "reason": why}
    meta = _load(path)
    statuses = dict((str(v.get("view_id")), v.get("capture_status"))
                    for v in meta.get("views") or [])
    stamp = meta.get("view_fingerprints") or {}
    name = stamp.get("file") or output_layout.fingerprints_name(
        output_layout.census_file_date(meta, str(run_dir)))
    fp_path = run_dir / name
    if not fp_path.is_file():
        reason = stamp.get("error") or "no {0}".format(name)
        return {"run_dir": str(run_dir), "meta": meta, "fingerprints": None,
                "statuses": statuses, "reason": reason}
    return {"run_dir": str(run_dir), "meta": meta, "fingerprints": _load(fp_path),
            "statuses": statuses, "reason": None}


def _bump(table, view_type, key):
    row = table.setdefault(view_type or "(unknown)", dict(
        (k, 0) for k in ("views", "would_skip", "carried") + OUTCOMES + TOLERATED))
    row[key] += 1


def score_pair(run_a, run_b, compare=None, compare_all=False, exact=False, basis="grid"):
    """Score B's views against A. ``compare(view_id) -> (capture_differs,
    entry)`` (True / False / None) is injected; by default the captures of the
    two runs are compared with the change-detection analyzer and, where the
    pixels differ and ``basis`` is "grid", decided by their analysis grids
    (module docstring)."""
    if compare is None:
        folder_a = acd.capture_folder(run_a["run_dir"])
        folder_b = acd.capture_folder(run_b["run_dir"])
        grid_a = output_layout.grid_dir_for_capture(str(folder_a))
        grid_b = output_layout.grid_dir_for_capture(str(folder_b))

        def compare(view_id):
            fa, fb = acd.view_files(folder_a, view_id), acd.view_files(folder_b, view_id)
            if not (fa["model"] or fa["anno"]) or not (fb["model"] or fb["anno"]):
                return None, None
            grid = None
            if basis == "grid":
                # GRID FIRST: where both grids can decide, they decide, and the
                # TIFFs are decoded only to describe a difference. Identical
                # pixels give identical grids, so this is the same verdict the
                # pixels-then-grid order gave, without a decode per view.
                differs, grid = compare_grid(view_id, grid_a, grid_b)
                if differs is not None:
                    entry = {"view_id": view_id, "files_identical": files_identical(fa, fb)}
                    if differs:
                        entry.update(acd.compare_view(view_id, fa, fb))
                    entry.update(basis="grid", grid=grid)
                    return differs, entry
            entry = acd.compare_view(view_id, fa, fb)
            entry["basis"] = "pixels"
            if grid is not None:
                entry["grid"] = grid          # why the grid could not decide
            if not acd.capture_differs(entry):
                return False, entry
            return acd.capture_differs(entry, tolerate_anno_fringe=not exact), entry
    pair = {"a": run_a["run_dir"], "b": run_b["run_dir"], "by_type": {}, "rules": {},
            "changed_components": {}, "false_unchanged": [], "over_cautious": []}
    if run_a["fingerprints"] is None or run_b["fingerprints"] is None:
        pair["skipped"] = "fingerprints missing: A {0}; B {1}".format(
            run_a["reason"], run_b["reason"])
        return pair
    facts_a = vf.run_facts(run_a["meta"], run_a["fingerprints"])
    facts_b = vf.run_facts(run_b["meta"], run_b["fingerprints"])
    pair["facts"] = {"a": facts_a, "b": facts_b}
    views_a = run_a["fingerprints"].get("views") or {}
    for vid, cur in sorted((run_b["fingerprints"].get("views") or {}).items()):
        base = views_a.get(vid)
        vtype = cur.get("view_type") or (base or {}).get("view_type")
        verdict = vf.skip_verdict(facts_a, facts_b, base, cur, run_a["statuses"].get(vid))
        _bump(pair["by_type"], vtype, "views")
        rule = verdict["rule"] or "would_skip"
        pair["rules"][rule] = pair["rules"].get(rule, 0) + 1
        if rule == "fingerprint_differs":
            for name in vf.changed_components(base, cur) or []:
                pair["changed_components"][name] = pair["changed_components"].get(name, 0) + 1
        if run_b["statuses"].get(vid) == "unchanged":
            # B carried the view (phase 2): its files ARE the source's, so
            # comparing them proves nothing. The audit sample is the check.
            _bump(pair["by_type"], vtype, "carried")
            continue
        if not verdict["would_skip"] and not compare_all:
            continue
        try:
            differs, entry = compare(int(vid))
        except (acd.Refusal, OSError, ValueError, KeyError) as ex:
            differs, entry = None, {"error": "{0}: {1}".format(type(ex).__name__, ex)}
        if verdict["would_skip"]:
            _bump(pair["by_type"], vtype, "would_skip")
            outcome = {True: "false_unchanged", False: "true_unchanged",
                       None: "not_comparable"}[differs]
            _bump(pair["by_type"], vtype, outcome)
            kind = tolerated_kind(entry) if outcome == "true_unchanged" else None
            if kind:
                _bump(pair["by_type"], vtype, kind)
            if outcome == "false_unchanged":
                pair["false_unchanged"].append({
                    "view_id": int(vid), "view_type": vtype,
                    "cause_inference": acd.classify(entry) if entry else None,
                    "model_differing_px": ((entry or {}).get("model_pixels") or {}).get(
                        "differing_px"),
                    "anno_differing_px": ((entry or {}).get("anno_pixels") or {}).get(
                        "differing_px"),
                    "anno_fringe_px": ((entry or {}).get("anno_pixels") or {}).get(
                        "fringe_px"),
                    "frame_diff": (entry or {}).get("frame_diff"),
                    "basis": (entry or {}).get("basis"),
                    "grid": (entry or {}).get("grid")})
        elif differs is False:
            pair["over_cautious"].append({"view_id": int(vid), "view_type": vtype,
                                          "rule": verdict["rule"]})
    return pair


def acceptance(pairs, min_pairs, min_per_type):
    """PURE. The bar over every scored pair: per view type and overall."""
    totals = {}
    for pair in pairs:
        for vtype, row in (pair.get("by_type") or {}).items():
            t = totals.setdefault(vtype, dict((k, 0) for k in row))
            for k, v in row.items():
                t[k] += v
    compared = sum(r["true_unchanged"] + r["false_unchanged"] for r in totals.values())
    false = sum(r["false_unchanged"] for r in totals.values())
    types = {}
    for vtype, r in sorted(totals.items()):
        n = r["true_unchanged"] + r["false_unchanged"]
        types[vtype] = dict(r, compared=n, meets_bar=(r["false_unchanged"] == 0
                                                      and n >= min_per_type),
                            upper_bound_95=(3.0 / n) if n and not r["false_unchanged"] else None)
    return {"compared_would_skip": compared, "false_unchanged": false,
            "overall_meets_bar": false == 0 and compared >= min_pairs,
            "upper_bound_95": (3.0 / compared) if compared and not false else None,
            "min_pairs": min_pairs, "min_per_type": min_per_type,
            "types_meeting_bar": sorted(t for t, r in types.items() if r["meets_bar"]),
            "by_type": types,
            "rule": "rule of three: 0 failures in n checks bounds the rate below 3/n at 95 %"}


def discover(root):
    """``(series, dropped)``: under ``root``, every directory holding exactly one
    run_meta file is a run. Runs are grouped by their parent folder (one
    model) and ordered by as-of date; a finalized run is required, and of two
    runs with one as-of date the later run id is kept. ``series``: [{"model_dir",
    "runs": [run_dir, ...]}]; ``dropped``: [{"run_dir", "reason"}]."""
    found, dropped = {}, []
    dirs = set(m.parent for m in Path(root).rglob("run_meta*.json"))
    for run_dir in sorted(dirs):
        path, why = output_layout.find_run_meta(str(run_dir))
        if path is None:
            dropped.append({"run_dir": str(run_dir), "reason": why})
            continue
        try:
            meta = _load(path)
        except (OSError, ValueError) as ex:
            dropped.append({"run_dir": str(run_dir),
                            "reason": "{0}: {1}".format(type(ex).__name__, ex)})
            continue
        if meta.get("finalized") is not True:
            dropped.append({"run_dir": str(run_dir), "reason": "not finalized"})
            continue
        date = output_layout.census_file_date(meta, str(run_dir))
        found.setdefault(str(run_dir.parent), []).append(
            (date, str(meta.get("run_id") or ""), str(run_dir)))
    series = []
    for model_dir, runs in sorted(found.items()):
        by_date = {}
        for date, run_id, run_dir in sorted(runs):
            if date in by_date:
                dropped.append({"run_dir": by_date[date][1],
                                "reason": "same as-of date {0} as {1}".format(date, run_dir)})
            by_date[date] = (run_id, run_dir)
        ordered = [by_date[d][1] for d in sorted(by_date)]
        if len(ordered) > 1:
            series.append({"model_dir": model_dir, "runs": ordered})
        else:
            dropped.extend({"run_dir": r, "reason": "only run of its model folder"}
                           for r in ordered)
    return series, dropped


def suspend(pairs, runs_by_dir, now=None):
    """Write ``skip_suspended.json`` beside each run that SKIPPED (run_meta
    ``skip.enabled``) and had a false unchanged against its baseline: its
    series (``skip.series_key``) captures everything until a person removes
    the entry (docs/DESIGN_CHANGE_SKIP.md S-13). Returns the written
    entries."""
    from vop_interwoven import change_skip
    written = []
    for pair in pairs:
        if not pair.get("false_unchanged"):
            continue
        meta = (runs_by_dir.get(pair["b"]) or {}).get("meta") or {}
        skip = meta.get("skip") or {}
        if not skip.get("enabled") or not skip.get("series_key"):
            continue
        root = Path(pair["b"]).parent
        path = root / change_skip.SUSPENSION_FILE
        record = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        entry = {"run_dir": pair["b"], "baseline": pair["a"],
                 "view_ids": [f["view_id"] for f in pair["false_unchanged"]],
                 "suspended_at": now or time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "by": "tools/validate_change_skip.py --suspend",
                 "clear": "remove this entry once a person has looked at the views"}
        record[skip["series_key"]] = entry
        path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
        written.append(dict(entry, file=str(path)))
    return written


def _score_job(job):
    """One pair, in a worker process: the runs are loaded there."""
    run_a, run_b, options = job
    t0 = time.perf_counter()
    pair = score_pair(load_run(run_a), load_run(run_b), **options)
    pair["seconds"] = round(time.perf_counter() - t0, 1)
    return pair


def score_pairs(jobs, n_jobs):
    """Every job scored, in job order; in ``n_jobs`` processes when > 1."""
    if n_jobs <= 1 or len(jobs) <= 1:
        return [_score_job(j) for j in jobs]
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(max_workers=n_jobs) as pool:
        return list(pool.map(_score_job, jobs))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("runs", nargs="*", help="run directories of ONE model, in series order")
    ap.add_argument("--root", default=None,
                    help="find the runs under this folder: one series per model folder")
    ap.add_argument("--jobs", type=int, default=1, help="pairs scored in parallel")
    ap.add_argument("--suspend", action="store_true",
                    help="a false unchanged on a run that skipped suspends its series "
                         "(writes skip_suspended.json beside the run)")
    ap.add_argument("--compare-all", action="store_true",
                    help="also compare the views it would capture (measures over-caution)")
    ap.add_argument("--min-pairs", type=int, default=1000)
    ap.add_argument("--min-per-type", type=int, default=30)
    ap.add_argument("--out", default=None)
    ap.add_argument("--exact", action="store_true",
                    help="pixel basis: count annotation anti-aliasing fringe as a difference")
    ap.add_argument("--basis", choices=BASES, default="grid",
                    help="grid (default): decide differing pixels by the analysis grids; "
                         "pixels: never read the grids")
    args = ap.parse_args(argv)
    if bool(args.root) == bool(args.runs):
        ap.error("give either run directories (at least two, in series order) or --root")
    dropped = []
    if args.root:
        series, dropped = discover(args.root)
        if not series:
            ap.error("no model folder under {0} holds two finalized runs".format(args.root))
    else:
        if len(args.runs) < 2:
            ap.error("give at least two runs, in series order")
        series = [{"model_dir": None, "runs": list(args.runs)}]
    options = {"compare_all": args.compare_all, "exact": args.exact, "basis": args.basis}
    jobs = [(a, b, options) for s in series for a, b in zip(s["runs"], s["runs"][1:])]
    print("{0} series, {1} pairs, {2} job(s)".format(len(series), len(jobs), args.jobs))
    pairs = score_pairs(jobs, args.jobs)
    suspended = []
    if args.suspend:
        suspended = suspend(pairs, dict((r, load_run(r)) for p in pairs for r in (p["b"],)))
    record = {"schema": SCHEMA, "tool_version": TOOL_VERSION,
              "runs": [r for s in series for r in s["runs"]], "series": series,
              "dropped_runs": dropped, "jobs": args.jobs, "suspended": suspended,
              "compare_all": args.compare_all, "exact": args.exact, "basis": args.basis,
              "fringe_radius_px": None if args.exact else acd.FRINGE_RADIUS_PX,
              "pairs": pairs,
              "acceptance": acceptance(pairs, args.min_pairs, args.min_per_type)}
    out = Path(args.out) if args.out else Path("validate_change_skip_{0}.json".format(
        time.strftime("%Y%m%dT%H%M%S")))
    out.write_text(json.dumps(record, indent=2, sort_keys=True, default=str), encoding="utf-8")
    acc = record["acceptance"]
    for pair in pairs:
        print("{0} -> {1}: {2}".format(Path(pair["a"]).name, Path(pair["b"]).name,
                                       pair.get("skipped") or pair["rules"]))
    print("{0:<14} {1:>6} {2:>10} {3:>8} {4:>6} {5:>8} {6:>7} {7:>8} {8:>6}  bar".format(
        "view type", "views", "would_skip", "same", "grid", "boundary", "fringe", "FALSE",
        "n/c"))
    for vtype, r in acc["by_type"].items():
        print("{0:<14} {1:>6} {2:>10} {3:>8} {4:>6} {5:>8} {6:>7} {7:>8} {8:>6}  {9}".format(
            vtype, r["views"], r["would_skip"], r["true_unchanged"], r["grid_tolerated"],
            r["boundary_tolerated"], r["fringe_tolerated"], r["false_unchanged"],
            r["not_comparable"], "met" if r["meets_bar"] else "-"))
    print("overall: {0} compared, {1} false unchanged, bar {2}".format(
        acc["compared_would_skip"], acc["false_unchanged"],
        "MET" if acc["overall_meets_bar"] else "not met"))
    print("wrote {0}".format(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
