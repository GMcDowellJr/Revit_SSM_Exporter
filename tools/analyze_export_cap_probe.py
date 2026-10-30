#!/usr/bin/env python3
"""Offline analysis of the export-cap probe (Q2, tests/dynamo/probe_export_cap.py).

The probe captured each view once per (cap, repeat) with production's
registered capture and ``color_id_buffer_cap_axis_px`` set to the cap. This
tool, per job:

  * registers the annotation capture (tools/register_stage_a_annotation.py)
    and grids the view (tools/stage_a_grid.py), both UNCHANGED, timing each;
  * reads the grid's record and npz: model ink cells inside crop A, fill
    fraction (model ink cells / crop-A cells), the model's pixel fraction,
    annotation ink cells, per-category model fill (from the element x cell
    rows), px per cell;
  * hashes the model TIFF, so repeats at one cap can be checked for identical
    output.

Then it reports, without choosing anything:

  * ``export_fit``: model ``ExportImage`` ms (the registered capture's own
    ``timings_ms.model_export``) against achieved megapixels, an ordinary
    least-squares line over every job exported in ONE attempt, with each
    point's residual; per view as well; and the mean residual by position in
    the view's block (the warm-up, measured);
  * ``timing``: per (view, cap), the median, min, max and spread of the
    repeats;
  * ``grid_vs_reference``: per view and cap, the change against that view's
    largest-cap result (fill, pixel fraction, per category, and the cells
    gained, lost and their Jaccard overlap);
  * ``fill_curve``: per cap, the largest |change in fill fraction| over the
    capped views. Read off the smallest cap within your own bound, or pass
    ``--bound`` to have it computed (no default: the bound is Greg's);
  * ``thin_line_bias``: per view, fill by achieved resolution, highest first,
    and how many steps down in resolution RAISED fill;
  * ``sizes``: model, annotation and registered TIFF bytes and npz bytes
    against megapixels.

Writes ``<probe dir>/export_cap_analysis.json`` LAST, carrying the probe
JSON's sha256. Refuses (exit 2) a probe JSON that is missing, ambiguous,
unreadable or not finalized.

    python tools/analyze_export_cap_probe.py <probe dir | json> [--grid-all-repeats]
        [--bound 0.01]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SCHEMA = "vop.probe.export_cap.analysis.v1"
TOOL_VERSION = "1.1.0"
ANALYSIS_NAME = "export_cap_analysis.json"
PROBE_GLOB = "probe_export_cap_*.json"
MODEL_INK_CODES = (1, 3)        # stage_a_grid.OCCUPANCY_CODES model_only, overlap
ANNO_INK_CODES = (2, 3)


class Refusal(Exception):
    pass


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# --- PURE: statistics ----------------------------------------------------------

def ols(points):
    """PURE. Ordinary least squares ``y = intercept + slope * x`` over
    ``[(x, y, key)]``: slope, intercept, r2, n and each point's residual.
    Fewer than two distinct x values is no fit (``None`` with the reason)."""
    pts = [(float(x), float(y), key) for x, y, key in points
           if x is not None and y is not None]
    if len(set(p[0] for p in pts)) < 2:
        return {"fit": None, "reason": "fewer than two distinct x values", "n": len(pts)}
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pts)
    slope = sxy / sxx
    intercept = my - slope * mx
    residuals = [(key, p[1] - (intercept + slope * p[0])) for p, key in
                 ((p, p[2]) for p in pts)]
    ss_res = sum(r * r for _k, r in residuals)
    ss_tot = sum((p[1] - my) ** 2 for p in pts)
    return {"fit": {"slope_per_x": slope, "intercept": intercept,
                    "r2": (1.0 - ss_res / ss_tot) if ss_tot > 0 else None},
            "n": n, "residuals": dict((str(k), round(r, 3)) for k, r in residuals),
            "residual_abs_max": round(max(abs(r) for _k, r in residuals), 3)}


def spread(values):
    """PURE. ``{n, median, min, max, spread}`` of the non-None values."""
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return {"n": 0, "median": None, "min": None, "max": None, "spread": None}
    return {"n": len(vals), "median": statistics.median(vals), "min": vals[0],
            "max": vals[-1], "spread": vals[-1] - vals[0]}


def cell_change(ref_cells, cells):
    """PURE. Two sets of cell indices: gained, lost, Jaccard overlap."""
    ref_cells, cells = set(ref_cells), set(cells)
    union = ref_cells | cells
    return {"gained": len(cells - ref_cells), "lost": len(ref_cells - cells),
            "jaccard": (len(ref_cells & cells) / float(len(union))) if union else 1.0}


def steps_rising(series):
    """PURE. ``series``: fill values ordered from HIGHEST resolution down.
    ``(rises, steps)``: how many steps down in resolution raised the value."""
    vals = [v for v in series if v is not None]
    steps = list(zip(vals, vals[1:]))
    return sum(1 for a, b in steps if b > a), len(steps)


def smallest_cap_within(curve, bound, key="max_abs_delta_fill"):
    """PURE. The smallest cap such that it and every larger cap stay within
    ``bound`` on ``key``; None when even the largest does not. ``curve`` is
    ``[{cap, key...}]``. A cap with no measurement, or with any view the grid
    refused (``views_refused``), breaks the run: unmeasured is not within."""
    best = None
    for row in sorted(curve, key=lambda r: -r["cap"]):
        v = row.get(key)
        if v is None or v > bound or row.get("views_refused"):
            break
        best = row["cap"]
    return best


# --- PURE: the grid's numbers for one job --------------------------------------------

def grid_metrics(record, npz):
    """PURE over the grid record (dict) and its npz arrays (a mapping). The
    model-ink cells inside crop A are returned separately as a set of signed
    ``(i, j)`` cell indices and not written."""
    grid = record["grid"]
    inside = np.asarray(npz["inside_crop_a"], dtype=bool)
    occ = np.asarray(npz["occupancy"])
    model_ink = np.isin(occ, MODEL_INK_CODES) & inside
    anno_ink = np.isin(occ, ANNO_INK_CODES) & inside
    inside_cells = int(inside.sum())
    elem_px = sum(np.asarray(npz[k]) for k in ("model_host", "model_dwg", "model_link")
                  if k in npz)
    crop_px = np.asarray(npz["crop_a_px"])
    per_cat = {}
    keys = record.get("ec_keys") or []
    if "ec_key" in npz and len(keys):
        ek, ei, ej = (np.asarray(npz[k]) for k in ("ec_key", "ec_i", "ec_j"))
        ink = np.asarray(npz["ec_ink_px"])
        i0, j0 = grid["i_range"][0], grid["j_range"][0]
        for idx, key in enumerate(keys):
            if key.get("layer") != "model":
                continue
            sel = (ek == idx) & (ink > 0)
            if not sel.any():
                continue
            name = key.get("category") or "(unrecorded)"
            per_cat.setdefault(name, set()).update(
                (int(i), int(j)) for i, j in zip(ei[sel], ej[sel])
                if inside[int(j) - j0, int(i) - i0])
    # Cells by their SIGNED (i, j) index, anchored to view UV: the dense
    # arrays' origin moves with each grid's extent (the annotation canvas is
    # part of it), so a position in the array names different cells in two
    # grids of one view.
    rows, cols = np.nonzero(model_ink)
    return {
        "px_per_cell": min(grid["px_per_cell_u"], grid["px_per_cell_v"]),
        "cells_w": grid["cells_w"], "cells_h": grid["cells_h"], "flags": grid.get("flags"),
        "inside_cells": inside_cells,
        "model_ink_cells": int(model_ink.sum()),
        "fill_fraction": (model_ink.sum() / float(inside_cells)) if inside_cells else None,
        "anno_ink_cells": int(anno_ink.sum()),
        "model_px_fraction": (float(np.asarray(elem_px)[inside].sum())
                              / float(crop_px[inside].sum())) if crop_px[inside].sum() else None,
        "per_category_ink_cells": dict((k, len(v)) for k, v in sorted(per_cat.items())),
        "per_category_fill": dict((k, len(v) / float(inside_cells)) for k, v in
                                  sorted(per_cat.items())) if inside_cells else {},
        "npz_bytes": record.get("npz_bytes"),
    }, set((int(c) + grid["i_range"][0], int(r) + grid["j_range"][0])
           for r, c in zip(rows, cols))


# --- the jobs ------------------------------------------------------------------

def find_probe_json(target):
    target = Path(target)
    if target.is_file():
        return target
    found = sorted(target.glob(PROBE_GLOB))
    if len(found) != 1:
        raise Refusal("expected one {0} in {1}, found {2}".format(
            PROBE_GLOB, target, [p.name for p in found]))
    return found[0]


def job_run_dir(probe_dir, job):
    """The job's run directory under THIS probe folder: the recorded path
    when it exists, else ``jobs/<label>/<its folder name>``, else the one
    folder in ``jobs/<label>/`` (the folder copied off the Revit machine)."""
    recorded = job.get("run_dir")
    if recorded and Path(recorded).is_dir():
        return Path(recorded)
    base = probe_dir / "jobs" / job["label"]
    if recorded:
        name = str(recorded).replace("\\", "/").rstrip("/").split("/")[-1]
        if (base / name).is_dir():
            return base / name
    subs = [p for p in base.iterdir() if p.is_dir()] if base.is_dir() else []
    if len(subs) == 1:
        return subs[0]
    raise Refusal("job {0}: run directory not found (recorded {1!r})".format(
        job["label"], recorded))


def job_sidecars(run_dir, view_id):
    from tools.analyze_change_detection_probe import capture_folder, view_files
    return view_files(capture_folder(run_dir), view_id)


def job_model_tiff_sha256(probe_dir, job):
    """The sha256 of the job's model TIFF: the probe's own record (it hashes
    every image, then deletes a later repeat's), else the file's, else None."""
    from tools import decode_stage_a_color_id as dsc
    if job.get("model_tiff_sha256"):
        return job["model_tiff_sha256"]
    try:
        files = job_sidecars(job_run_dir(probe_dir, job), job["view_id"])
        if not files["model"]:
            return None
        sidecar = json.loads(files["model"].read_text(encoding="utf-8"))
        return sha256_file(dsc._resolve_tiff_path(files["model"], sidecar))
    except (Refusal, OSError, ValueError):
        return None


def grid_job(probe_dir, job):
    """Register and grid one job; ``(row, cells)``."""
    from tools import register_stage_a_annotation as rsa
    from tools import stage_a_grid as sag
    from vop_interwoven import output_layout
    row = {"label": job["label"]}
    files = job_sidecars(job_run_dir(probe_dir, job), job["view_id"])
    if not files["model"]:
        return dict(row, status="no_model_sidecar"), None
    if files["anno"]:
        t0 = time.perf_counter()
        reg = rsa.register(files["anno"], files["model"])
        row["register_s"] = round(time.perf_counter() - t0, 3)
        row["register_status"] = reg.get("status")
        tiff = (reg.get("registered_tiff") if reg.get("status") == "registered" else None)
        row["registered_tiff_bytes"] = Path(tiff).stat().st_size if tiff and Path(
            tiff).exists() else None
    out = Path(output_layout.grid_dir_for_capture(files["model"].parent))
    t0 = time.perf_counter()
    rec = sag.grid_view(files["model"], out)
    row["grid_s"] = round(time.perf_counter() - t0, 3)
    row["grid_status"] = rec.get("status")
    if rec.get("status") != "value":
        return dict(row, grid_refusal=rec.get("reason")), None
    with np.load(out / rec["npz"]) as npz:
        metrics, cells = grid_metrics(rec, dict(npz))
    row.update(metrics)
    row["model_tiff_sha256"] = rec.get("model_tiff_sha256")
    return row, cells


def analyze(target, grid_all_repeats=False, bound=None, gridder=grid_job,
            hasher=job_model_tiff_sha256):
    probe_path = find_probe_json(target)
    try:
        probe = json.loads(probe_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        raise Refusal("probe JSON unreadable: {0}".format(ex))
    if not probe.get("finalized"):
        raise Refusal("probe JSON is not finalized: {0}".format(probe_path))
    probe_dir = probe_path.parent
    capped = set(probe["inputs"]["capped_view_ids"])
    jobs = [j for j in probe.get("jobs") or [] if not j.get("error")]
    errors = [{"label": j["label"], "error": j["error"]} for j in probe.get("jobs") or []
              if j.get("error")]

    # Grid: the first repeat of each (view, cap) the grid RAN on, or all of
    # them. A grid refusal (too few px per cell) is that cap's result and is
    # not retried on a later repeat: run 20261026T101435 retried them onto
    # repeats whose TIFFs the probe had deleted. Only a job the grid could
    # not run on at all (a missing file) passes to the next repeat.
    rows, cells = {}, {}
    first, settled, refused = {}, set(), {}
    for job in sorted(jobs, key=lambda j: (j["repeat"], j["index"])):
        key = (job["view_id"], job["cap"])
        if not grid_all_repeats and key in settled:
            continue
        try:
            row, c = gridder(probe_dir, job)
        except (Refusal, OSError, ValueError) as ex:
            row, c = {"label": job["label"], "status": "{0}: {1}".format(
                type(ex).__name__, ex)}, None
        rows[job["label"]] = row
        if row.get("grid_status") in ("value", "refused"):
            settled.add(key)
        if row.get("grid_status") == "refused":
            refused.setdefault(key, {"label": job["label"], "reason": row.get("grid_refusal")})
        if c is not None:
            cells[job["label"]] = c
            first.setdefault(key, job["label"])

    # Export ms against megapixels: jobs exported in ONE attempt only.
    one = [j for j in jobs if j.get("attempts") == 1 and j.get("model_export_ms") is not None]
    retried = sorted(j["label"] for j in jobs if (j.get("attempts") or 0) > 1)
    fit = ols([(j.get("megapixels"), j["model_export_ms"], j["label"]) for j in one])
    per_view_fit = dict((str(v), ols([(j.get("megapixels"), j["model_export_ms"], j["label"])
                                      for j in one if j["view_id"] == v]))
                        for v in sorted(set(j["view_id"] for j in one)))
    by_vc = {}
    for j in jobs:
        by_vc.setdefault((j["view_id"], j["cap"]), []).append(j)
    tiff_hash = dict((j["label"], hasher(probe_dir, j)) for j in jobs)
    timing = []
    for (vid, cap), js in sorted(by_vc.items()):
        hashes = sorted(set(tiff_hash[j["label"]] for j in js if tiff_hash[j["label"]]))
        timing.append({"view_id": vid, "cap": cap, "capped_view": vid in capped,
                       "actual_px": sorted(set((j.get("actual_w"), j.get("actual_h"))
                                               for j in js)),
                       "megapixels": spread([j.get("megapixels") for j in js])["median"],
                       "model_export_ms": spread([j.get("model_export_ms") for j in js]),
                       "annotation_export_ms": spread([j.get("annotation_export_ms")
                                                       for j in js]),
                       "capture_total_ms": spread([j.get("capture_total_ms") for j in js]),
                       "model_tiff_bytes": spread([j.get("model_tiff_bytes") for j in js]),
                       "repeats_hashed": sum(1 for j in js if tiff_hash[j["label"]]),
                       "distinct_model_tiff_hashes": len(hashes),
                       "attempts": sorted(set(j.get("attempts") for j in js))})
    # The warm-up: residual from the (view, cap) median, by block position.
    by_pos = {}
    for j in jobs:
        med = spread([x.get("model_export_ms") for x in by_vc[(j["view_id"], j["cap"])]])
        if j.get("model_export_ms") is not None and med["median"] is not None:
            by_pos.setdefault(j["position_in_view_block"], []).append(
                j["model_export_ms"] - med["median"])
    warmup = dict((str(p), {"n": len(v), "mean_ms_above_median": round(sum(v) / len(v), 3)})
                  for p, v in sorted(by_pos.items()))

    # Grid change against each view's largest-cap result.
    vs_ref, curve_rows, bias = [], {}, {}
    for vid in sorted(set(j["view_id"] for j in jobs)):
        caps = sorted((c for (v, c) in first if v == vid), reverse=True)
        if not caps:
            continue
        ref_label = first[(vid, caps[0])]
        ref = rows[ref_label]
        series = []
        for cap in caps:
            label = first[(vid, cap)]
            r = rows[label]
            if r["fill_fraction"] is None or ref["fill_fraction"] is None:
                vs_ref.append({"view_id": vid, "cap": cap, "reference_cap": caps[0],
                               "unmeasured": "no crop-A cells"})
                continue
            change = cell_change(cells[ref_label], cells[label])
            d_fill = r["fill_fraction"] - ref["fill_fraction"]
            cats = set(r["per_category_fill"]) | set(ref["per_category_fill"])
            entry = {"view_id": vid, "cap": cap, "reference_cap": caps[0],
                     "capped_view": vid in capped, "px_per_cell": r["px_per_cell"],
                     "fill_fraction": r["fill_fraction"], "delta_fill": d_fill,
                     "relative_delta_fill": (d_fill / ref["fill_fraction"]
                                             if ref["fill_fraction"] else None),
                     "delta_model_px_fraction": (
                         None if r["model_px_fraction"] is None
                         or ref["model_px_fraction"] is None
                         else r["model_px_fraction"] - ref["model_px_fraction"]),
                     "delta_anno_ink_cells": r["anno_ink_cells"] - ref["anno_ink_cells"],
                     "cells": change,
                     "delta_per_category_fill": dict(
                         (k, r["per_category_fill"].get(k, 0.0)
                          - ref["per_category_fill"].get(k, 0.0)) for k in sorted(cats)),
                     "grid_s": r.get("grid_s")}
            vs_ref.append(entry)
            series.append((r["px_per_cell"], r["fill_fraction"]))
            if vid in capped:
                curve_rows.setdefault(cap, []).append(entry)
        rises, steps = steps_rising([f for _p, f in sorted(series, reverse=True)])
        bias[str(vid)] = {"by_resolution": [{"px_per_cell": p, "fill_fraction": f}
                                            for p, f in sorted(series, reverse=True)],
                          "steps_down_in_resolution": steps, "steps_raising_fill": rises}
    # Every cap any capped view was captured at, with the views the grid
    # REFUSED there counted beside the deltas: a cap where most views cannot
    # be gridded must not read as within any bound.
    all_caps = sorted(set(j["cap"] for j in jobs if j["view_id"] in capped), reverse=True)
    curve = []
    for cap in all_caps:
        es = curve_rows.get(cap) or []
        ref_ = sorted((vid, r["reason"]) for (vid, c), r in refused.items()
                      if c == cap and vid in capped)
        curve.append({
            "cap": cap, "views": len(es), "views_refused": len(ref_),
            "refused": [{"view_id": v, "reason": why} for v, why in ref_],
            "max_abs_delta_fill": max((abs(e["delta_fill"]) for e in es), default=None),
            "max_abs_relative_delta_fill": max(
                (abs(e["relative_delta_fill"]) for e in es
                 if e["relative_delta_fill"] is not None), default=None),
            "min_jaccard": min((e["cells"]["jaccard"] for e in es), default=None)})
    sizes = [{"label": j["label"], "megapixels": j.get("megapixels"),
              "model_tiff_bytes": j.get("model_tiff_bytes"),
              "annotation_tiff_bytes": j.get("annotation_tiff_bytes"),
              "registered_tiff_bytes": rows.get(j["label"], {}).get("registered_tiff_bytes"),
              "npz_bytes": rows.get(j["label"], {}).get("npz_bytes")} for j in jobs]
    grid_cost = ols([(j.get("megapixels"), rows[j["label"]].get("grid_s"), j["label"])
                     for j in jobs if rows.get(j["label"], {}).get("grid_s") is not None])
    record = {
        "schema": SCHEMA, "tool_version": TOOL_VERSION, "status": "value",
        "probe_json": str(probe_path), "probe_json_sha256": sha256_file(probe_path),
        "restore_summary": probe.get("RESTORE_SUMMARY"),
        "job_errors": errors, "jobs_analyzed": len(jobs),
        "export_fit": dict(fit, x="achieved megapixels", y="model_export ms",
                           excluded_retried=retried),
        "export_fit_per_view": per_view_fit,
        "warmup_by_position": warmup,
        "timing": timing,
        "grid_cost_fit": dict(grid_cost, x="achieved megapixels", y="grid seconds"),
        "grid": rows,
        "grid_vs_reference": vs_ref,
        "fill_curve": curve,
        "thin_line_bias": bias,
        "sizes": sizes,
        "rules": {
            "fill_fraction": "model ink cells (occupancy model_only|overlap) inside crop A / "
                             "crop-A cells",
            "export_ms": "registration_marks.timings_ms.model_export: every ExportImage "
                         "attempt of the model pass; jobs with more than one attempt are "
                         "left out of the fit",
            "reference": "each view's largest cap, first gridded repeat"}}
    if bound is not None:
        record["bound_query"] = {"bound": bound, "key": "max_abs_delta_fill",
                                 "smallest_cap_within": smallest_cap_within(curve, bound)}
    return probe_path, record


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("probe", help="the probe folder or its probe_export_cap_*.json")
    ap.add_argument("--grid-all-repeats", action="store_true",
                    help="grid every repeat, not only the first of each (view, cap)")
    ap.add_argument("--bound", type=float, default=None,
                    help="report the smallest cap whose max |delta fill| stays within this")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    try:
        probe_path, record = analyze(args.probe, args.grid_all_repeats, args.bound)
    except Refusal as ex:
        print("REFUSED: {0}".format(ex))
        return 2
    out = Path(args.out) if args.out else probe_path.parent / ANALYSIS_NAME
    out.write_text(json.dumps(record, indent=2, sort_keys=True, default=str), encoding="utf-8")
    f = record["export_fit"].get("fit")
    if f:
        print("model export ms = {0:.1f} + {1:.1f} x Mpx  (r2 {2}, n {3})".format(
            f["intercept"], f["slope_per_x"], f["r2"], record["export_fit"]["n"]))
    print("cap     gridded refused  max|dfill|  min jaccard")
    for row in record["fill_curve"]:
        print("{0:>6} {1:>8} {2:>7} {3:>11} {4:>12}".format(
            row["cap"], row["views"], row["views_refused"],
            "-" if row["max_abs_delta_fill"] is None else "{0:.5f}".format(
                row["max_abs_delta_fill"]),
            "-" if row["min_jaccard"] is None else "{0:.4f}".format(row["min_jaccard"])))
    print("wrote {0}".format(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
