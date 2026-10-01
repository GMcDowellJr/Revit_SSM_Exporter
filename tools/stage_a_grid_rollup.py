#!/usr/bin/env python3
"""The grid roll-up: every view's analysis-grid result across Stage A runs.

Read-only. It never registers, never grids, and never writes into a capture
folder. It reads what ``register_stage_a_annotation.py`` and ``stage_a_grid.py``
already wrote and reports, per view, the state it OBSERVED -- including every
view that was NOT gridded and why. Absence of output is never a clean result.

DENOMINATOR. Rows are enumerated from the model sidecars, by
``stage_a_grid.model_sidecars`` -- the grid's own selector, imported, not
copied. Each model sidecar is exactly one row; grid JSONs are never counted.
A grid JSON is looked for where ``stage_a_grid.py`` writes it by default,
``<run>/analysis_grid/<view>.grid.json``.

ROW STATUS (never blank):
  * ``gridded``       -- grid.json status "value", and not stale;
  * ``grid_refused``  -- grid.json status other than "value"; its reason;
  * ``not_gridded``   -- no grid.json for this sidecar;
  * ``grid_stale``    -- grid.json no longer describes the files on disk:
    its model_sidecar_sha256 / model_tiff_sha256 or its
    registered_annotation.record_sha256 does not match (hashed with the
    grid's own ``sha256_file``), or a registration record appeared or became
    usable after a ``value`` grid was made without one;
  * ``unreadable``    -- grid.json cannot be parsed; the exception.

REGISTRATION STATE (never blank): ``registered``, ``refused`` (its refusals
joined), ``absent`` (no ``<view>_anno.registered.json``) or ``unusable`` (the
record is not refused but fails the grid's own test -- status "registered"
with a lattice -- which is when the grid records
``registered_annotation.status == "unusable"``; or the record cannot be
read). A gridded row that is not ``registered`` is "model-only".

Outputs, in ``--out`` (default ``<run>/analysis_grid/`` for a single run;
required for more than one): ``grid_rollup.csv``, then
``grid_rollup.summary.json`` carrying the CSV's sha256 -- the summary is
written last (CLAUDE.md, defect class 4). An empty CSV cell means "not
applicable for this row status", never zero.

The roll-up reports states; it does not judge the run. Every count in the
summary sits beside its denominator.

    python tools/stage_a_grid_rollup.py <run> [<run> ...] [--out DIR]

Exit 0 when every row is ``gridded`` with registration ``registered``; 1 for
any other row; 2 when a run has no model sidecars, or on bad arguments.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import decode_stage_a_color_id as dsc  # noqa: E402
from tools import register_stage_a_annotation as reg  # noqa: E402
from tools import stage_a_grid as grid  # noqa: E402

SCHEMA = "vop.stage_a.grid_rollup.v1"
TOOL_VERSION = "1.0.0"

CSV_NAME = "grid_rollup.csv"
SUMMARY_NAME = "grid_rollup.summary.json"

ROW_STATUSES = ("gridded", "grid_refused", "not_gridded", "grid_stale", "unreadable")
REGISTRATION_STATES = ("registered", "refused", "absent", "unusable")
VIEWS_CORE_STATES = ("value", "absent", "ambiguous", "unreadable")

COLUMNS = [
    # identity
    "run_id", "view_stem", "view_id",
    "view_name", "view_type", "scale", "is_on_sheet", "views_core_state",
    # status
    "row_status", "reason", "registration_state", "registration_reason",
    # grid
    "cells_w", "cells_h", "cell_ft", "view_scale", "px_per_cell_min",
    "uv_basis_chosen", "uncertainty_px", "uncertainty_cells", "flags",
    # faults
    "capture_faults_model_n", "capture_faults_annotation_n", "capture_faults_detail",
    # occupancy
    "inside_crop_a_empty", "inside_crop_a_model_only", "inside_crop_a_anno_only",
    "inside_crop_a_overlap", "outside_crop_a_anno_only",
    # ink (image_totals)
    "model_host_ink", "model_dwg_ink", "model_link_ink",
    "anno_element_ink", "anno_residual", "anno_filled_region",
    # black pixels
    "black_total", "black_assigned", "black_unassigned", "black_ambiguous",
    # filled regions
    "filled_region_class_recorded", "filled_region_elements", "filled_region_px",
    # overlap
    "model_ink_px", "under_annotation_px", "under_filled_region_px",
    "fraction_under_annotation",
]


def _err(ex):
    return "{0}: {1}".format(type(ex).__name__, ex)


def _load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _cell(value):
    """A CSV cell: empty for "not applicable", never a stand-in zero."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


# --- where things are ------------------------------------------------------------

def run_layout(run_arg):
    """``(sidecars, capture_folder, run_dir, grid_dir)`` for one run argument,
    using the grid's own selector and the grid's own default output folder
    (``stage_a_grid.main``: ``<capture folder>.parent / "analysis_grid"``)."""
    sidecars, folder = grid.model_sidecars(run_arg)
    folder = Path(folder)
    return sidecars, folder, folder.parent, folder.parent / "analysis_grid"


def registration_record_path(model_sidecar_path):
    """``<view>_anno.registered.json``, named by the registration tool."""
    p = Path(model_sidecar_path)
    return reg.output_paths(p.with_name(p.stem + "_anno.json"))[1]


# --- views_core ------------------------------------------------------------------

def load_views_core(run_dir):
    """``(state, reason, {ViewId: [rows]})`` from the run's views_core_*.csv."""
    found = sorted(Path(run_dir).glob("views_core_*.csv"))
    if not found:
        return "absent", "no views_core_*.csv in {0}".format(run_dir), {}
    if len(found) > 1:
        return "ambiguous", "{0} views_core files: {1}".format(
            len(found), ", ".join(p.name for p in found)), {}
    by_id = {}
    try:
        with open(found[0], encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                by_id.setdefault(str(row.get("ViewId", "")).strip(), []).append(row)
    except (OSError, ValueError, csv.Error) as ex:
        return "unreadable", "{0}: {1}".format(found[0].name, _err(ex)), {}
    return "value", "", by_id


def views_core_columns(state, by_id, view_id):
    """The joined columns for one view: ``value`` only when exactly one row of
    the single views_core file carries its ViewId."""
    blank = {"view_name": None, "view_type": None, "scale": None, "is_on_sheet": None}
    if state != "value":
        return dict(blank, views_core_state=state)
    rows = by_id.get(str(view_id)) if view_id is not None else None
    if not rows:
        return dict(blank, views_core_state="absent")
    if len(rows) > 1:
        return dict(blank, views_core_state="ambiguous")
    row = rows[0]
    return {"view_name": row.get("ViewName"), "view_type": row.get("ViewType"),
            "scale": row.get("Scale"), "is_on_sheet": row.get("IsOnSheet"),
            "views_core_state": "value"}


# --- one view --------------------------------------------------------------------

def staleness(model_sidecar_path, record, reg_path):
    """Why ``record`` (a parsed grid.json) no longer describes the files on
    disk; empty when every hash it names still matches."""
    reasons = []
    if record.get("model_sidecar_sha256") != grid.sha256_file(model_sidecar_path):
        reasons.append("model_sidecar_sha256 does not match the model sidecar on disk")
    if "model_tiff_sha256" in record:
        try:
            sidecar = _load_json(model_sidecar_path)
            tiff = dsc._resolve_tiff_path(Path(model_sidecar_path), sidecar)
            now = grid.sha256_file(tiff)
        except (OSError, ValueError) as ex:
            now = None
            reasons.append("the model TIFF cannot be hashed now ({0})".format(_err(ex)))
        if now is not None and now != record.get("model_tiff_sha256"):
            reasons.append("model_tiff_sha256 does not match the model TIFF on disk")
    ra = record.get("registered_annotation")
    reg_now = grid.sha256_file(reg_path) if reg_path.exists() else None
    if isinstance(ra, dict) and "record_sha256" in ra:
        if ra.get("record_sha256") != reg_now:
            reasons.append("registered_annotation.record_sha256 does not match the "
                           "registration record on disk" if reg_now else
                           "the registration record the grid used is gone")
    elif isinstance(ra, dict) and ra.get("status") == "unusable":
        if reg_now is None:
            reasons.append("the registration record the grid found unusable is gone")
        else:
            try:
                usable = _usable(_load_json(reg_path))
            except (OSError, ValueError, AttributeError) as ex:
                usable = False
                reasons.append("the registration record cannot be read now ({0})".format(
                    _err(ex)))
            if usable:
                reasons.append("the registration record is now registered; the grid "
                               "was made when it was not")
    elif ra is None and record.get("status") == "value" and reg_now is not None:
        # A "value" grid reads any record present, so it saw none.
        reasons.append("a registration record exists that the grid was made without")
    return reasons


def _usable(registered):
    """The grid's own test of a registration record (``stage_a_grid.grid_view``
    consumes one only with status "registered" AND a lattice)."""
    return registered.get("status") == "registered" and bool(registered.get("lattice"))


def registration_state(reg_path):
    """``(state, reason)`` from the registration record on disk. ``unusable``
    is the grid's own verdict (``_usable``) on a record that is not refused:
    on a row that is not stale it is exactly what the grid recorded as
    ``registered_annotation.status == "unusable"``."""
    if not reg_path.exists():
        return "absent", ""
    try:
        disk = _load_json(reg_path)
        status = disk.get("status")
    except (OSError, ValueError, AttributeError) as ex:
        return "unusable", "the registration record cannot be read: " + _err(ex)
    if status == "refused":
        return "refused", "; ".join(str(r) for r in disk.get("refusals") or [])
    if _usable(disk):
        return "registered", ""
    return "unusable", "the registration record's status is {0!r}{1}".format(
        status, "" if disk.get("lattice") else ", with no lattice")


def grid_columns(record):
    """The grid's own numbers for a ``gridded`` row."""
    g = record.get("grid") or {}
    basis = g.get("uv_basis") or {}
    px_u, px_v = g.get("px_per_cell_u"), g.get("px_per_cell_v")
    faults = record.get("capture_faults") or {}
    occ = record.get("occupancy") or {}
    inside = occ.get("inside_crop_a") or {}
    outside = occ.get("outside_crop_a") or {}
    totals = record.get("image_totals") or {}
    black = record.get("black")
    filled = record.get("filled_region")
    over = record.get("model_ink_under_annotation")
    cols = {
        "cells_w": g.get("cells_w"), "cells_h": g.get("cells_h"),
        "cell_ft": g.get("cell_ft"), "view_scale": g.get("view_scale"),
        "px_per_cell_min": (min(px_u, px_v) if px_u is not None and px_v is not None
                            else None),
        "uv_basis_chosen": basis.get("chosen"),
        "uncertainty_px": basis.get("uncertainty_px"),
        "uncertainty_cells": g.get("uncertainty_cells"),
        "flags": "|".join(str(f) for f in g.get("flags") or []),
        "capture_faults_model_n": (len(faults["model"]) if "model" in faults else None),
        "capture_faults_annotation_n": (len(faults["annotation"])
                                        if "annotation" in faults else None),
        "capture_faults_detail": ";".join(
            "{0}:{1}".format(k, ",".join(str(f) for f in v))
            for k, v in sorted(faults.items()) if v),
        "inside_crop_a_empty": inside.get("empty"),
        "inside_crop_a_model_only": inside.get("model_only"),
        "inside_crop_a_anno_only": inside.get("anno_only"),
        "inside_crop_a_overlap": inside.get("overlap"),
        "outside_crop_a_anno_only": outside.get("anno_only"),
    }
    for key in ("model_host_ink", "model_dwg_ink", "model_link_ink",
                "anno_element_ink", "anno_residual", "anno_filled_region"):
        cols[key] = totals.get(key)
    if isinstance(black, dict):
        cols.update(black_total=black.get("total"), black_assigned=black.get("assigned"),
                    black_unassigned=black.get("unassigned"),
                    black_ambiguous=black.get("ambiguous"))
    if isinstance(filled, dict):
        cols.update(filled_region_class_recorded=filled.get("element_class_recorded"),
                    filled_region_elements=filled.get("elements"),
                    filled_region_px=filled.get("px"))
    if isinstance(over, dict):
        cols.update(model_ink_px=over.get("model_ink_px"),
                    under_annotation_px=over.get("under_annotation_px"),
                    under_filled_region_px=over.get("under_filled_region_px"),
                    fraction_under_annotation=over.get("fraction_under_annotation"))
    return cols


def view_row(model_sidecar_path, grid_dir, run_id, views_core):
    """One row for one model sidecar. Every key of COLUMNS is present."""
    model_sidecar_path = Path(model_sidecar_path)
    stem = model_sidecar_path.stem
    row = dict((c, None) for c in COLUMNS)
    row.update(run_id=run_id, view_stem=stem)
    try:
        row["view_id"] = _load_json(model_sidecar_path).get("view_id")
    except (OSError, ValueError, AttributeError) as ex:
        row["view_id"] = None
        row["reason"] = "the model sidecar cannot be read: " + _err(ex)
    reg_path = registration_record_path(model_sidecar_path)
    grid_json = Path(grid_dir) / (stem + ".grid.json")
    record = None
    if not grid_json.exists():
        row["row_status"] = "not_gridded"
        row["reason"] = "no {0} in {1}".format(grid_json.name, grid_dir)
    else:
        try:
            record = _load_json(grid_json)
            if not isinstance(record, dict):
                raise ValueError("the grid record is a {0}, not an object".format(
                    type(record).__name__))
        except (OSError, ValueError) as ex:
            record = None
            row["row_status"] = "unreadable"
            row["reason"] = _err(ex)
    if record is not None:
        if row["view_id"] is None:
            row["view_id"] = record.get("view_id")
        stale = staleness(model_sidecar_path, record, reg_path)
        if stale:
            row["row_status"] = "grid_stale"
            row["reason"] = "; ".join(stale)
        elif record.get("status") == "value":
            row["row_status"] = "gridded"
            row["reason"] = None
            row.update(grid_columns(record))
        else:
            row["row_status"] = "grid_refused"
            row["reason"] = record.get("reason") or "grid status {0!r}".format(
                record.get("status"))
    row["registration_state"], row["registration_reason"] = registration_state(
        reg_path)
    state, by_id = views_core
    row.update(views_core_columns(state, by_id, row["view_id"]))
    return row


# --- the roll-up ---------------------------------------------------------------------

def _counts(rows, key, names):
    out = dict((n, 0) for n in names)
    for r in rows:
        out[r[key]] = out.get(r[key], 0) + 1
    return out


def _view_ref(r):
    return {"run_id": r["run_id"], "view_stem": r["view_stem"], "view_id": r["view_id"],
            "view_name": r["view_name"]}


def summarise(runs, rows, csv_path):
    n = len(rows)
    gridded = [r for r in rows if r["row_status"] == "gridded"]
    model_only = [r for r in gridded if r["registration_state"] != "registered"]
    flags = {}
    for r in gridded:
        for f in (r["flags"] or "").split("|"):
            if f:
                flags[f] = flags.get(f, 0) + 1
    with_black = [r for r in gridded if r["black_unassigned"] is not None]
    with_fr = [r for r in gridded if r["filled_region_elements"] is not None]
    return {
        "schema": SCHEMA, "tool_version": TOOL_VERSION,
        "runs": runs,
        "denominator": n,
        "denominator_rule": "one row per model sidecar (stage_a_grid.model_sidecars)",
        "row_status": {"denominator": n,
                       "counts": _counts(rows, "row_status", ROW_STATUSES)},
        "registration_state": {"denominator": n,
                               "counts": _counts(rows, "registration_state",
                                                 REGISTRATION_STATES)},
        "model_only_count": {
            "denominator": len(gridded), "denominator_rule": "gridded rows",
            "count": len(model_only),
            "rule": "gridded rows whose registration_state is not registered"},
        "flag_counts": {"denominator": len(gridded),
                        "denominator_rule": "gridded rows", "counts": flags},
        "filled_region_views": {
            "denominator": len(with_fr),
            "denominator_rule": "gridded rows that measured filled regions",
            "count": sum(1 for r in with_fr if r["filled_region_elements"] > 0),
            "views": [dict(_view_ref(r), filled_region_elements=r["filled_region_elements"],
                           filled_region_px=r["filled_region_px"])
                      for r in with_fr if r["filled_region_elements"] > 0]},
        "black_unassigned_views": {
            "denominator": len(with_black),
            "denominator_rule": "gridded rows that measured black pixels",
            "count": sum(1 for r in with_black if r["black_unassigned"] > 0),
            "views": [dict(_view_ref(r), black_unassigned=r["black_unassigned"],
                           black_total=r["black_total"])
                      for r in sorted(with_black, key=lambda r: -r["black_unassigned"])
                      if r["black_unassigned"] > 0]},
        "not_gridded_rows": {
            "denominator": n,
            "count": n - len(gridded),
            "rows": [dict(_view_ref(r), row_status=r["row_status"], reason=r["reason"],
                          registration_state=r["registration_state"],
                          registration_reason=r["registration_reason"])
                     for r in rows if r["row_status"] != "gridded"]},
        "csv": csv_path.name,
        "csv_sha256": grid.sha256_file(csv_path),
    }


def rollup(layouts, out_dir):
    """Write the CSV, then the summary; return the summary."""
    out_dir = Path(out_dir)
    rows, runs = [], []
    for sidecars, folder, run_dir, grid_dir in layouts:
        meta = dsc.find_run_meta(sidecars[0]) or {}
        run_id = meta.get("run_id")
        core_state, core_reason, core_by_id = load_views_core(run_dir)
        runs.append({"run_dir": str(run_dir), "capture_dir": str(folder),
                     "grid_dir": str(grid_dir), "run_id": run_id,
                     "git_commit": meta.get("git_commit"),
                     "run_meta_found": bool(meta),
                     "model_sidecars": len(sidecars),
                     "views_core_state": core_state,
                     "views_core_reason": core_reason})
        for path in sidecars:
            rows.append(view_row(path, grid_dir, run_id, (core_state, core_by_id)))
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / CSV_NAME
    with open(csv_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow(dict((k, _cell(r[k])) for k in COLUMNS))
    summary = summarise(runs, rows, csv_path)
    with open(out_dir / SUMMARY_NAME, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True, default=str)
    return summary, rows


def _inside(path, folder):
    path, folder = Path(path).resolve(), Path(folder).resolve()
    return path == folder or folder in path.parents


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("runs", nargs="+", metavar="run",
                    help="a run directory or its color_id_buffer/")
    ap.add_argument("--out", default=None,
                    help="output directory (default, one run only: <run>/analysis_grid)")
    args = ap.parse_args(argv)
    if len(args.runs) > 1 and not args.out:
        ap.error("--out is required with more than one run")
    layouts = []
    for run in args.runs:
        layout = run_layout(run)
        if not layout[0]:
            print("no model sidecars under {0}".format(layout[1]))
            return 2
        layouts.append(layout)
    out = Path(args.out) if args.out else layouts[0][3]
    for _s, folder, _r, _g in layouts:
        if _inside(out, folder):
            ap.error("--out {0} is inside the capture folder {1}; the roll-up never "
                     "writes there".format(out, folder))
    summary, rows = rollup(layouts, out)
    n = summary["denominator"]
    print("{0} model sidecars: ".format(n) + ", ".join(
        "{0} {1}".format(k, v) for k, v in summary["row_status"]["counts"].items())
        + " | registration: " + ", ".join(
        "{0} {1}".format(k, v) for k, v in summary["registration_state"]["counts"].items()))
    for r in rows:
        if r["row_status"] != "gridded":
            print("{0:<12} {1} {2}: {3}".format(r["row_status"], r["run_id"],
                                                r["view_stem"], r["reason"]))
    print("wrote {0} and {1}".format(out / CSV_NAME, out / SUMMARY_NAME))
    every = all(r["row_status"] == "gridded" and r["registration_state"] == "registered"
                for r in rows)
    return 0 if every else 1


if __name__ == "__main__":
    sys.exit(main())
