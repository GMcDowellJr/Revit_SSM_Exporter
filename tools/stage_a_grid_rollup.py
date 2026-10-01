#!/usr/bin/env python3
"""The grid roll-up: every view's analysis-grid result across Stage A runs.

Read-only. It never registers, never grids, and never writes into a capture
folder. It reads what ``register_stage_a_annotation.py`` and ``stage_a_grid.py``
already wrote and reports, per view, the state it OBSERVED -- including every
view that was NOT gridded and why. Absence of output is never a clean result.

DENOMINATOR. Rows come from what the run REQUESTED: one row per unique
view_id in run_meta.json's ``views_requested``, matched to its ``views``
outcome and to its model sidecar by view_id, plus one row per model sidecar
whose view_id the run did not request. A view that produced no Stage A
sidecar (one that went the geometry path, or was rejected by capability
gating) is therefore a row, never an absence. Model sidecars are found by
``stage_a_grid.model_sidecars`` -- the grid's own selector, imported, not
copied. Grid JSONs are never counted. A grid JSON is looked for where
``stage_a_grid.py`` writes it by default,
``<run>/analysis_grid/<view>.grid.json``.

A RUN IS REFUSED -- no rows, listed under ``refused_runs`` with the reason --
when its run_meta.json is absent or unreadable, is not ``finalized: true``,
carries no run_id, requests no views, or when the view_id sets of
``views_requested`` and ``views`` differ. Duplicate ids in
``views_requested`` are reported, never merged silently, and give one row.

The summary records the count invariant: the row_status counts sum to the
requested count plus the orphan-sidecar count. If they do not, the tool
exits 2 and says by how much.

ROW STATUS (never blank):
  * ``no_stage_a_capture`` -- requested, but no model sidecar has its
    view_id; run_meta's capture_status and failure reason are carried;
  * ``orphan_sidecar`` -- a model sidecar whose view_id is not requested
    (or cannot be read);
  * ``sidecar_ambiguous`` -- more than one model sidecar claims the view_id;
    not guessed between;
  * ``not_this_run``  -- the sidecar cannot be tied to the run: a sidecar
    carries no run id, so it is this run's only when run_meta.json records
    that view's capture as "success" (the rule of stage_a_timing_report.py).
    A failed capture leaves an older sidecar in place;
  * ``gridded``       -- grid.json status "value", and not stale;
  * ``grid_refused``  -- grid.json status other than "value"; its reason;
  * ``not_gridded``   -- no grid.json for this sidecar;
  * ``grid_stale``    -- grid.json no longer describes the files on disk:
    its model_sidecar_sha256 / model_tiff_sha256, its
    registered_annotation.record_sha256 / tiff_sha256 does not match (hashed
    with the grid's own ``sha256_file``), it was made under another run_id,
    or a registration record appeared or became usable after a ``value``
    grid was made without one;
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

Exit 0 when no run is refused and every row is ``gridded`` with registration
``registered``; 1 for any other row or a refused run; 2 when every run is
refused, when the count invariant does not hold, or on bad arguments.
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

ROW_STATUSES = ("gridded", "grid_refused", "not_gridded", "grid_stale", "unreadable",
                "not_this_run", "no_stage_a_capture", "orphan_sidecar",
                "sidecar_ambiguous")
REGISTRATION_STATES = ("registered", "refused", "absent", "unusable")
VIEWS_CORE_STATES = ("value", "absent", "ambiguous", "unreadable", "duplicate_rows",
                     "conflicting")

COLUMNS = [
    # identity
    "run_id", "view_stem", "view_id",
    "view_name", "view_type", "scale", "is_on_sheet", "views_core_state",
    # status
    "row_status", "reason", "run_meta_capture_status", "run_meta_failure_reason",
    "registration_state", "registration_reason",
    # grid
    "cells_w", "cells_h", "cell_ft", "view_scale", "px_per_cell_min",
    "uv_basis_chosen", "uncertainty_px", "uncertainty_cells", "flags",
    "frame_predicted_px", "frame_actual_px", "frame_delta_px",
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

def load_views_core(run_dir, run_id):
    """``(state, reason, {ViewId: [rows]})`` from the run's views_core_*.csv,
    holding only rows whose RunId is ``run_id``: a views_core row is
    identified by (RunId, ViewId), and ViewId alone can select another run's
    row. With no run_id, no row is joined."""
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
                if run_id is None or str(row.get("RunId", "")).strip() != str(run_id):
                    continue
                by_id.setdefault(str(row.get("ViewId", "")).strip(), []).append(row)
    except (OSError, ValueError, csv.Error) as ex:
        return "unreadable", "{0}: {1}".format(found[0].name, _err(ex)), {}
    return "value", "", by_id


VIEWS_CORE_FIELDS = (("view_name", "ViewName"), ("view_type", "ViewType"),
                     ("scale", "Scale"), ("is_on_sheet", "IsOnSheet"))


def views_core_columns(state, by_id, view_id):
    """The joined columns for one view. ``value``: exactly one row of the
    single views_core file carries its (RunId, ViewId). More than one row is
    REPORTED, not hidden (a geometry-path row beside a backfilled
    "no_outcome_reported" row is a known producer defect): each field is
    filled only when every row agrees on it, and the state is
    ``duplicate_rows`` when the rows agree on ViewType, else ``conflicting``."""
    blank = dict((k, None) for k, _c in VIEWS_CORE_FIELDS)
    if state != "value":
        return dict(blank, views_core_state=state)
    rows = by_id.get(str(view_id)) if view_id is not None else None
    if not rows:
        return dict(blank, views_core_state="absent")
    out = {}
    for key, column in VIEWS_CORE_FIELDS:
        values = set(r.get(column) for r in rows)
        out[key] = values.pop() if len(values) == 1 else None
    if len(rows) == 1:
        out["views_core_state"] = "value"
    elif len(set(r.get("ViewType") for r in rows)) == 1:
        out["views_core_state"] = "duplicate_rows"
    else:
        out["views_core_state"] = "conflicting"
    return out


# --- the run ----------------------------------------------------------------------

def _vid(value):
    """A view id as one key: an integer's digits where it is one (run_meta
    writes ints; streaming's backfill compares them as int), else its text."""
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value)


def unique_requested(requested_ids):
    """The requested view ids, each once, in request order."""
    seen, out = set(), []
    for vid in requested_ids:
        if vid not in seen:
            seen.add(vid)
            out.append(vid)
    return out


def run_identity(meta):
    """What run_meta.json says the run requested and how each view ended.

    Returns ``{"refused": reason or None, "requested": [ids in order, with
    repeats], "duplicates": {id: times}, "outcomes": {id: entry or None}}``;
    an id listed in ``views`` with differing outcomes maps to None."""
    ident = {"refused": None, "requested": [], "duplicates": {}, "outcomes": {}}
    if not meta:
        ident["refused"] = "run_meta.json is absent or unreadable"
        return ident
    if meta.get("finalized") is not True:
        ident["refused"] = "run_meta.json finalized is {0!r}, not true (an interrupted " \
                           "run)".format(meta.get("finalized"))
        return ident
    if not meta.get("run_id"):
        ident["refused"] = "run_meta.json carries no run_id, so nothing can be tied to it"
        return ident
    requested, views = meta.get("views_requested"), meta.get("views")
    if not isinstance(requested, list) or not isinstance(views, list):
        ident["refused"] = "run_meta.json views_requested / views are not both lists"
        return ident
    if not all(isinstance(v, dict) for v in views):
        ident["refused"] = "run_meta.json views holds an entry that is not a record"
        return ident
    req_ids = [_vid(v) for v in requested]
    out_ids = [_vid(v.get("view_id")) for v in views]
    if not req_ids:
        ident["refused"] = "run_meta.json requests no views"
        return ident
    only_req = sorted(set(req_ids) - set(out_ids))
    only_out = sorted(set(out_ids) - set(req_ids))
    if only_req or only_out:
        ident["refused"] = ("the view_id sets of views_requested and views differ: "
                            "{0} requested with no outcome {1}, {2} outcomes never "
                            "requested {3}".format(len(only_req), only_req[:10],
                                                   len(only_out), only_out[:10]))
        return ident
    ident["requested"] = req_ids
    for vid in req_ids:
        ident["duplicates"][vid] = ident["duplicates"].get(vid, 0) + 1
    ident["duplicates"] = dict((k, n) for k, n in ident["duplicates"].items() if n > 1)
    for v, vid in zip(views, out_ids):
        key = (v.get("capture_status"), v.get("capture_failure_reason"))
        if vid in ident["outcomes"]:
            prior = ident["outcomes"][vid]
            if prior is None or (prior.get("capture_status"),
                                 prior.get("capture_failure_reason")) != key:
                ident["outcomes"][vid] = None
        else:
            ident["outcomes"][vid] = v
    return ident


def sidecars_by_view(sidecars):
    """``({view_id: [paths]}, [(path, reason)])``: model sidecars by the
    view_id they carry, and those whose view_id cannot be read."""
    by_vid, unreadable = {}, []
    for path in sidecars:
        try:
            vid = _load_json(path).get("view_id")
        except (OSError, ValueError, AttributeError) as ex:
            unreadable.append((path, "the model sidecar cannot be read: " + _err(ex)))
            continue
        if vid is None:
            unreadable.append((path, "the model sidecar carries no view_id"))
            continue
        by_vid.setdefault(_vid(vid), []).append(path)
    return by_vid, unreadable


# --- one view --------------------------------------------------------------------

def staleness(model_sidecar_path, record, reg_path, run_id=None, grid_json=None):
    """Why ``record`` (a parsed grid.json) no longer describes the files on
    disk; empty when every hash it names still matches."""
    reasons = []
    if run_id is not None and record.get("run_id") != run_id:
        reasons.append("the grid was made under run_id {0!r}, not {1!r}".format(
            record.get("run_id"), run_id))
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
        elif "tiff_sha256" in ra:
            # The grid's annotation numbers come from the registered TIFF, so
            # it is checked too, resolved the way the grid resolves it.
            try:
                tiff = grid._resolve_recorded(_load_json(reg_path).get("registered_tiff"),
                                              reg_path.parent,
                                              "the registered annotation TIFF")
                now = grid.sha256_file(tiff)
            except (OSError, ValueError, AttributeError, grid.GridRefusal) as ex:
                now = None
                reasons.append("the registered annotation TIFF cannot be hashed now "
                               "({0})".format(_err(ex)))
            if now is not None and now != ra.get("tiff_sha256"):
                reasons.append("registered_annotation.tiff_sha256 does not match the "
                               "registered annotation TIFF on disk")
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
    elif (ra is None and record.get("status") != "value" and reg_now is not None
          and grid_json is not None
          and reg_path.stat().st_mtime_ns > Path(grid_json).stat().st_mtime_ns):
        # A refusal written after the registration was read but before the
        # grid recorded its hash names no registration provenance. The only
        # evidence left is order: a record rewritten after the refusal (a
        # re-registration) may have repaired what was refused.
        reasons.append("the registration record was rewritten after this refusal, "
                       "which records no registration hash to compare")
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
    frame = g.get("frame_check") or {}
    cols = {
        "cells_w": g.get("cells_w"), "cells_h": g.get("cells_h"),
        "cell_ft": g.get("cell_ft"), "view_scale": g.get("view_scale"),
        "px_per_cell_min": (min(px_u, px_v) if px_u is not None and px_v is not None
                            else None),
        "uv_basis_chosen": basis.get("chosen"),
        "uncertainty_px": basis.get("uncertainty_px"),
        "uncertainty_cells": g.get("uncertainty_cells"),
        "flags": "|".join(str(f) for f in g.get("flags") or []),
        # The non-fit axis (stage_a_grid.frame_check); blank when unmeasured.
        "frame_predicted_px": frame.get("predicted_px"),
        "frame_actual_px": frame.get("actual_px"),
        "frame_delta_px": frame.get("delta_px"),
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


def _blank_row(run_id, view_id, views_core, outcome):
    row = dict((c, None) for c in COLUMNS)
    row.update(run_id=run_id, view_id=view_id)
    if outcome is not None:
        row["run_meta_capture_status"] = outcome.get("capture_status")
        row["run_meta_failure_reason"] = outcome.get("capture_failure_reason")
    state, by_id = views_core
    row.update(views_core_columns(state, by_id, view_id))
    return row


def requested_row(vid, paths, grid_dir, run_id, views_core, outcome):
    """The row for one requested view id (``outcome`` None: run_meta lists it
    with conflicting outcomes)."""
    if len(paths) == 1:
        return view_row(paths[0], grid_dir, run_id, views_core, outcome)
    row = _blank_row(run_id, vid, views_core, outcome)
    if outcome is None:
        row["run_meta_capture_status"] = "ambiguous"
    row["registration_state"], row["registration_reason"] = "absent", "no model sidecar"
    if not paths:
        row["row_status"] = "no_stage_a_capture"
        row["reason"] = "requested, but no model sidecar carries view_id {0}".format(vid)
    else:
        row["row_status"] = "sidecar_ambiguous"
        row["view_stem"] = "|".join(p.stem for p in paths)
        row["reason"] = "{0} model sidecars carry view_id {1}; not guessed".format(
            len(paths), vid)
        row["registration_reason"] = "not read: the sidecar is ambiguous"
    return row


def orphan_row(path, vid, run_id, views_core, why):
    """A model sidecar the run did not request (``vid`` None: its view_id
    could not be read)."""
    row = _blank_row(run_id, vid, views_core, None)
    row.update(view_stem=Path(path).stem, row_status="orphan_sidecar", reason=why)
    row["registration_state"], row["registration_reason"] = registration_state(
        registration_record_path(path))
    return row


def view_row(model_sidecar_path, grid_dir, run_id, views_core, outcome):
    """One row for the one model sidecar of a requested view. Every key of
    COLUMNS is present."""
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
    if outcome is None:
        row["run_meta_capture_status"] = "ambiguous"
    else:
        row["run_meta_capture_status"] = outcome.get("capture_status")
        row["run_meta_failure_reason"] = outcome.get("capture_failure_reason")
    if row["run_meta_capture_status"] != "success":
        row["row_status"] = "not_this_run"
        row["reason"] = ("run_meta.json records capture_status {0!r} for view {1}; the "
                         "sidecar cannot be tied to run {2!r}".format(
                             row["run_meta_capture_status"], row["view_id"], run_id))
    elif not grid_json.exists():
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
        stale = staleness(model_sidecar_path, record, reg_path, run_id, grid_json)
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


def count_invariant(runs, rows):
    """The row_status counts against requested + orphans, each counted on
    its own: requested from run_meta.json directly, not from the rows."""
    requested = sum(r["requested_count"] for r in runs if not r["refused"])
    orphans = sum(1 for r in rows if r["row_status"] == "orphan_sidecar")
    total = sum(_counts(rows, "row_status", ROW_STATUSES).values())
    return {"rule": "sum of row_status counts == requested_count + orphan_sidecar_count",
            "row_status_total": total, "requested_count": requested,
            "orphan_sidecar_count": orphans,
            "difference": total - (requested + orphans)}


def summarise(runs, rows, csv_path):
    n = len(rows)
    invariant = count_invariant(runs, rows)
    requested_rows = [r for r in rows if r["row_status"] != "orphan_sidecar"]
    capture = {}
    for r in requested_rows:
        key = str(r["run_meta_capture_status"])
        capture[key] = capture.get(key, 0) + 1
    crosstab = {}
    for r in rows:
        vt = r["view_type"] if r["view_type"] not in (None, "") else "(not joined)"
        crosstab.setdefault(vt, {})
        crosstab[vt][r["row_status"]] = crosstab[vt].get(r["row_status"], 0) + 1
    duplicates = [{"run_id": run["run_id"], "view_id": vid, "times": times}
                  for run in runs for vid, times in sorted(run["duplicate_requested_ids"].items())]
    core_dupes = [r for r in rows if r["views_core_state"] in ("duplicate_rows", "conflicting")]
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
        "denominator_rule": ("one row per unique view_id in run_meta.json "
                             "views_requested, plus one per orphan model sidecar"),
        "requested_count": invariant["requested_count"],
        "count_invariant": invariant,
        "refused_runs": {
            "denominator": len(runs), "count": sum(1 for r in runs if r["refused"]),
            "runs": [{"run_dir": r["run_dir"], "run_id": r["run_id"], "reason": r["refused"]}
                     for r in runs if r["refused"]]},
        "duplicate_requested_ids": {
            "denominator": invariant["requested_count"],
            "denominator_rule": "unique requested view ids",
            "count": len(duplicates), "ids": duplicates},
        "views_core_duplicate_count": {
            "denominator": n, "count": len(core_dupes),
            "rule": "rows whose views_core_state is duplicate_rows or conflicting",
            "views": [_view_ref(r) for r in core_dupes]},
        "run_meta_capture_status": {
            "denominator": len(requested_rows), "denominator_rule": "requested rows",
            "counts": capture},
        "view_type_by_row_status": {
            "denominator": n, "counts": crosstab,
            "rule": "views_core ViewType (\"(not joined)\" when absent) x row_status"},
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


def run_rows(sidecars, grid_dir, run_id, views_core, ident):
    """Every row of one run that was not refused: the requested views, then
    the orphan sidecars."""
    by_vid, unreadable = sidecars_by_view(sidecars)
    rows = []
    for vid in unique_requested(ident["requested"]):
        rows.append(requested_row(vid, by_vid.get(vid, []), grid_dir, run_id, views_core,
                                  ident["outcomes"].get(vid)))
    requested = set(ident["requested"])
    for vid, paths in sorted(by_vid.items()):
        if vid not in requested:
            for path in paths:
                rows.append(orphan_row(path, vid, run_id, views_core,
                                       "view_id {0} is not in views_requested".format(vid)))
    for path, why in unreadable:
        rows.append(orphan_row(path, None, run_id, views_core, why))
    return rows


def rollup(layouts, out_dir):
    """Write the CSV, then the summary; return the summary."""
    out_dir = Path(out_dir)
    rows, runs = [], []
    for sidecars, folder, run_dir, grid_dir in layouts:
        # The decoder's own search: beside the capture folder, then one up.
        meta = dsc.find_run_meta(folder / "run_meta.json") or {}
        run_id = meta.get("run_id")
        ident = run_identity(meta)
        core_state, core_reason, core_by_id = load_views_core(run_dir, run_id)
        runs.append({"run_dir": str(run_dir), "capture_dir": str(folder),
                     "grid_dir": str(grid_dir), "run_id": run_id,
                     "git_commit": meta.get("git_commit"),
                     "run_meta_found": bool(meta),
                     "run_meta_finalized": meta.get("finalized"),
                     "refused": ident["refused"],
                     "requested_count": (0 if ident["refused"] else len(set(
                         _vid(v) for v in meta.get("views_requested") or []))),
                     "duplicate_requested_ids": ident["duplicates"],
                     "model_sidecars": len(sidecars),
                     "views_core_state": core_state,
                     "views_core_reason": core_reason})
        if not ident["refused"]:
            rows.extend(run_rows(sidecars, grid_dir, run_id, (core_state, core_by_id),
                                 ident))
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
    layouts = [run_layout(run) for run in args.runs]
    out = Path(args.out) if args.out else layouts[0][3]
    for _s, folder, _r, _g in layouts:
        if _inside(out, folder):
            ap.error("--out {0} is inside the capture folder {1}; the roll-up never "
                     "writes there".format(out, folder))
    summary, rows = rollup(layouts, out)
    n = summary["denominator"]
    print("{0} rows ({1} requested + {2} orphan sidecars): ".format(
        n, summary["requested_count"], summary["count_invariant"]["orphan_sidecar_count"])
        + ", ".join(
        "{0} {1}".format(k, v) for k, v in summary["row_status"]["counts"].items())
        + " | registration: " + ", ".join(
        "{0} {1}".format(k, v) for k, v in summary["registration_state"]["counts"].items()))
    for r in rows:
        if r["row_status"] != "gridded":
            print("{0:<12} {1} {2}: {3}".format(
                r["row_status"], r["run_id"],
                r["view_stem"] or "view {0}".format(r["view_id"]), r["reason"]))
    for r in summary["refused_runs"]["runs"]:
        print("REFUSED run {0}: {1}".format(r["run_dir"], r["reason"]))
    print("wrote {0} and {1}".format(out / CSV_NAME, out / SUMMARY_NAME))
    diff = summary["count_invariant"]["difference"]
    if diff:
        print("COUNT INVARIANT BROKEN: {0} rows, but {1} requested + {2} orphan sidecars "
              "(difference {3:+d})".format(n, summary["requested_count"],
                                           summary["count_invariant"]["orphan_sidecar_count"],
                                           diff))
        return 2
    refused = summary["refused_runs"]["count"]
    if refused == len(layouts):
        print("every run was refused; nothing was rolled up")
        return 2
    every = all(r["row_status"] == "gridded" and r["registration_state"] == "registered"
                for r in rows)
    return 0 if every and not refused else 1


if __name__ == "__main__":
    sys.exit(main())
