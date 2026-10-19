#!/usr/bin/env python3
"""The element census: what was modeled, kept apart from how much it is drawn.

Read-only over run folders. It never registers, never grids, never decodes a
TIFF and never writes into a capture folder. It reads what
``stage_a_grid.py`` and the capture already wrote.

WHY. Summing model cells over every view of a run (the BI "Load Cells")
counts an element once per view it is drawn in: a sheet set of the same
geometry adds cells and no modeling. The census keys the model layer by
ELEMENT IDENTITY, so a run carries "how many elements, how big" separately
from "how many times drawn", and a series carries elements created and
deleted between runs independent of view count.

WHICH VIEWS. The roll-up's own rows (``stage_a_grid_rollup.run_rows``,
imported, not copied): a requested view is MEASURED when its row is
``gridded``; every other requested view is UNMEASURED, counted by its row
status, and contributes no element. A view whose capture failed (run_meta
``views[]`` capture_status other than "success") is never ``gridded``, so it
is unmeasured, never a source of deletions. Orphan sidecars are not
requested and are not read.

ELEMENT KEYS. ``host:<element_id>`` for an element of the host document --
source ``HOST`` or ``DWG`` from the model sidecar's ``near_face_w_map.host``
entry (``stage_a_sidecar_shapes.model_entry_source``; ``unrecorded`` where
the entry is absent or its read failed, never a stand-in) -- and
``link:<link_inst_id>:<link_elem_id>`` for a linked element, from
``near_face_w_map.link``. Link pixels are colour-per-CATEGORY in the capture
(``stage_a_grid.model_keys``; per-element link identity is not in the
pixels), so a linked element's views and bbox are measured and its cells are
NOT: its pixel columns are empty with ``cells_state`` ``link_category_only``,
and the run carries the linked categories' cells as one figure of their own.
A link element is never keyed by colour or category.

PER ELEMENT (one CSV per run): the views it is collected in (its key is in
the view's grid ``ec_keys`` or, for a link, its ``near_face_w_map.link``),
the views where it holds a pixel, and summed over views: px, ink px, cells
with any pixel and cells with ink (``ink >= stage_a_grid.OCCUPANCY_MIN_INK_PX``,
the grid's model occupancy rule). Every per-key sum is
``stage_a_kinds._key_sums`` -- the roll-up's reader -- over the grid's
``ec_`` rows, one row per (key, cell). Its bbox extent and centre are the
sidecar's ``bbox_3d`` (``model_entry_bbox_3d``), from the first measured
view, with a count of views whose bbox differs. ``bbox_3d`` is the element's
axis-aligned extent in model/host feet, an upper bound on its shape
(``color_id_buffer._collect_near_face_w_data``), so the centre is a place to
match on, not the element's centroid.

FIRST APPEARANCE. An element's first appearance is the measured view where
it holds the most ink cells (ties: the lowest view id). Request order is not
used: it is the order views were enumerated, which changes when views are
added, and would move the figure for no modeling reason. ``first_cells`` +
``repeat_cells`` == ``cells_ink``.

PER RUN: distinct elements by source and by category, the views-per-element
distribution, cells per element, the first-appearance share of element ink
cells, and the grid's own occupancy model cells summed over the measured
views (``model_only + overlap`` over all cells) -- the figure a per-view
sum like Load Cells is built from, beside the identity-keyed ones.

ACROSS RUNS (ordered by run_meta ``date``, the as-of date; two runs with one
as-of date are refused, exit 2), per element: first run seen and how, last
run seen and how. Between adjacent runs each element is ``persisted``
(seen in both), or seen in one only and then, from the views it was
collected in on the side that saw it:
  * any of those views requested but UNMEASURED on the other side ->
    ``unmeasured`` (a failed capture is never a creation or a deletion);
  * else any of them MEASURED on the other side -> ``created`` / ``gone``;
  * else none of them requested there -> ``new_view`` / ``view_dropped``:
    the view, not the element, changed.
``gone`` means "not drawn in any view that drew it before"; that it was
deleted from the model is INFERENCE (it may have been hidden). Each pair of
adjacent runs also records the Jaccard overlap of host element ids over the
views measured in both, the check that element ids are stable between runs.

REFUSALS. A run is refused -- listed with the reason, no rows -- when the
roll-up refuses it (run_meta.json absent, not finalized, no run_id, request
sets differ), when it has no as-of date, or when any gridded view's element
x cell accounting cannot be read: a grid made before ``ec_keys`` is the
named refusal ``grid_predates_ec_keys``, never a zero.

Outputs in ``--out`` (default, one run only: its grid folder, ``grid/`` in a
v2 run and ``analysis_grid/`` in a v1 run; required for more): ``<as_of_date>__<run_id>.elements.csv`` per run,
``element_census_runs.csv``, ``element_census_series.csv``, then
``element_census.summary.json`` carrying every CSV's sha256, written last
(CLAUDE.md, defect class 4). An empty CSV cell is "not applicable", never 0.
Columns are appended, never reordered.

    python tools/stage_a_element_census.py <run> [<run> ...] [--out DIR]

One line is printed as each run is read (its time, views and elements), so
a series run shows where it is. A run argument that is not a directory is a
bad argument (exit 2) before anything is read, and runs are recorded by
their absolute path.

1.1.0: views are folded into the run's per-element totals one at a time and
their element records dropped, so memory follows distinct elements, not
views x elements (2025-11-01: 12,768 elements in 358 views, ~281,000
element-in-view records). ``anno_cells_occupancy`` sums the measured views
that counted an annotation layer, and the appended ``anno_cells_views``
says how many did; 1.0.0 left it blank when any one view was model-only.

1.3.0 (review, PR #238): appends ``doc_title`` to the runs CSV; refuses two
runs with one run_id; ``runs_unmeasured_between`` counts every run between
sightings where a view that drew the element was unmeasured; a later run's
recorded source replaces ``unrecorded``; ``anno_cells_occupancy`` is blank when
no view counted annotation; an empty link map beside link pixels is
``link_identity_unrecorded``; the roll-up's per-key detail is not read.

1.2.0 appends ``bbox_cx_ft`` / ``bbox_cy_ft`` / ``bbox_cz_ft`` to the
element CSV: the bbox_3d centre, so an element gone in one run and one
created in the next can be matched on category, place and size (the 44-run
series: 30-50 % of a week's gone and created match on category and size
alone, with no position to tell replace-in-place from new modeling).

Exit 0 when no run is refused and every requested view is measured; 1 for
an unmeasured view or a refused run; 2 when every run is refused, two runs
share an as-of date, or on bad arguments (a run that is not a directory).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import decode_stage_a_color_id as dsc  # noqa: E402
from tools import stage_a_grid as grid  # noqa: E402
from tools import stage_a_grid_rollup as rollup  # noqa: E402
from tools import stage_a_kinds as kinds  # noqa: E402
from tools import stage_a_sidecar_shapes as shapes  # noqa: E402

SCHEMA = "vop.stage_a.element_census.v1"
TOOL_VERSION = "1.3.0"

ELEMENTS_SUFFIX = ".elements.csv"
RUNS_CSV = "element_census_runs.csv"
SERIES_CSV = "element_census_series.csv"
SUMMARY_NAME = "element_census.summary.json"

SOURCES = ("HOST", "DWG", "LINK", "unrecorded")
HOST_BUCKET = ("HOST", "DWG", "unrecorded")
CELLS_STATES = ("value", "link_category_only")
VIEWS_PER_ELEMENT_BINS = ((1, 1), (2, 2), (3, 5), (6, 10), (11, 20), (21, 50), (51, None))
ENTER = ("series_start", "created", "unmeasured", "new_view")
LEAVE = ("series_end", "gone", "unmeasured", "view_dropped")
TRANSITIONS = ("persisted", "created", "gone", "unmeasured_in", "unmeasured_out",
               "new_view", "view_dropped")

ELEMENT_COLUMNS = [
    "run_id", "as_of_date", "element_key", "source", "element_id", "link_inst_id",
    "link_elem_id", "category", "views_collected", "views_with_px", "cells_state",
    "px", "ink_px", "cells_px", "cells_ink", "first_view_id", "first_cells",
    "repeat_cells", "bbox_state", "bbox_dx_ft", "bbox_dy_ft", "bbox_dz_ft",
    "bbox_differs_views", "source_conflict_views",
    # 1.2.0: appended. The bbox_3d centre, in the sidecar's model/host feet.
    "bbox_cx_ft", "bbox_cy_ft", "bbox_cz_ft",
]
RUN_COLUMNS = (
    ["as_of_date", "run_id", "run_dir", "refused", "views_requested", "views_measured",
     "views_unmeasured", "views_capture_failed", "views_below_capture_floor",
     "model_cells_occupancy", "model_cells_occupancy_inside_crop_a",
     "anno_cells_occupancy", "elements_distinct"]
    + ["elements_" + s for s in SOURCES]
    + ["element_cells_ink", "host_elements_with_ink", "cells_per_element",
       "cells_per_element_median", "first_cells", "repeat_cells", "first_share",
       "link_category_cells_ink", "views_per_element_mean", "views_per_element_median",
       "views_per_element_max", "link_identity_unrecorded_views",
       "prev_as_of_date", "common_measured_views", "host_id_jaccard_common_views"]
    + [t for t in TRANSITIONS] + ["{0}_LINK".format(t) for t in TRANSITIONS]
    # 1.1.0: appended. anno_cells_occupancy sums the measured views that
    # counted an annotation layer; this many did (the rest are model-only).
    + ["anno_cells_views"]
    # 1.3.0: appended. The run's document title, so a series that mixes
    # models (element ids are per model) shows it.
    + ["doc_title"])
SERIES_COLUMNS = [
    "element_key", "source", "category", "first_as_of_date", "first_run_id",
    "first_how", "last_as_of_date", "last_run_id", "last_how", "runs_seen",
    "runs_in_series", "runs_unmeasured_between", "reappearances",
]


class CensusRefusal(Exception):
    """A run (or the series) the census declines, with the reason."""


def _err(ex):
    return "{0}: {1}".format(type(ex).__name__, ex)


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _vid_sort(vid):
    """Order view ids numerically where they are integers."""
    try:
        return (0, int(vid), "")
    except (TypeError, ValueError):
        return (1, 0, str(vid))


# --- one measured view ----------------------------------------------------------

def key_sums(arrays, n_keys):
    """``{field: int array}`` per ec key: px, ink px, cells with any pixel and
    cells with ink, every one through ``stage_a_kinds._key_sums`` (the
    roll-up's reader). The grid writes one ``ec_`` row per (key, cell)
    (``stage_a_grid.merge_sparse``), so a key's cells are its rows that
    satisfy the rule."""
    cells = {"ec_key": arrays["ec_key"],
             "ec_cell_px": (arrays["ec_px"] > 0).astype(np.int64),
             "ec_cell_ink": (arrays["ec_ink_px"] >= grid.OCCUPANCY_MIN_INK_PX).astype(
                 np.int64)}
    return {"px": kinds._key_sums(arrays, n_keys, "ec_px"),
            "ink_px": kinds._key_sums(arrays, n_keys, "ec_ink_px"),
            "cells_px": kinds._key_sums(cells, n_keys, "ec_cell_px"),
            "cells_ink": kinds._key_sums(cells, n_keys, "ec_cell_ink")}


def load_view_grid(grid_json):
    """``(record, arrays)`` of a gridded view, or CensusRefusal: a grid made
    before ``ec_keys`` is named ``grid_predates_ec_keys``."""
    try:
        rec = _load_json(grid_json)
    except (OSError, ValueError) as ex:
        raise CensusRefusal("{0}: the grid record cannot be read: {1}".format(
            Path(grid_json).name, _err(ex)))
    if isinstance(rec, dict) and rec.get("status") == "value" and "ec_keys" not in rec:
        raise CensusRefusal("grid_predates_ec_keys: {0} (schema {1!r}) carries no element "
                            "x cell accounting; re-run stage_a_grid".format(
                                Path(grid_json).name, rec.get("schema")))
    try:
        return kinds.load_grid(grid_json)
    except kinds.KindsRefusal as ex:
        raise CensusRefusal("{0}: {1}".format(Path(grid_json).name, ex))


def _bbox(entry):
    state, payload = shapes.model_entry_bbox_3d(entry)
    if state != shapes.VALUE:
        return state, None
    try:
        lo, hi = payload["min"], payload["max"]
        extent = tuple(round(float(hi[k]) - float(lo[k]), 6) for k in range(3))
        centre = tuple(round((float(hi[k]) + float(lo[k])) / 2.0, 6) for k in range(3))
        return shapes.VALUE, extent + centre
    except (KeyError, TypeError, ValueError, IndexError):
        return "unreadable", None


def _value_or(reader, entry, default):
    state, payload = reader(entry)
    return payload if state == shapes.VALUE and payload is not None else default


def _occupancy_cells(rec, where, classes):
    occ = (rec.get("occupancy") or {}).get(where) or {}
    if any(occ.get(c) is None for c in classes):
        return None
    return sum(int(occ[c]) for c in classes)


def view_census(model_sidecar_path, grid_json):
    """The elements of one measured view and its view-level figures."""
    rec, arr = load_view_grid(grid_json)
    keys = rec["ec_keys"]
    sums = key_sums(arr, len(keys))
    try:
        sidecar = _load_json(model_sidecar_path)
    except (OSError, ValueError) as ex:
        raise CensusRefusal("{0}: the model sidecar cannot be read: {1}".format(
            Path(model_sidecar_path).name, _err(ex)))
    nfw = sidecar.get("near_face_w_map")
    nfw = nfw if isinstance(nfw, dict) else None
    host_map = (nfw or {}).get("host") or {}
    elements, link_cells, link_px = {}, 0, 0
    for n, k in enumerate(keys):
        if k.get("layer") != "model":
            continue
        if k.get("element_id") is None:
            # A linked category's pixels: colour-per-category, no element.
            link_cells += int(sums["cells_ink"][n])
            link_px += int(sums["px"][n])
            continue
        eid = int(k["element_id"])
        entry = host_map.get(str(eid))
        entry = entry if isinstance(entry, dict) else {}
        source = _value_or(shapes.model_entry_source, entry, None)
        bbox_state, bbox = _bbox(entry) if entry else ("unavailable", None)
        elements["host:{0}".format(eid)] = {
            "source": source if source in ("HOST", "DWG") else "unrecorded",
            "element_id": eid, "link_inst_id": None, "link_elem_id": None,
            "category": _value_or(shapes.model_entry_category, entry, k.get("category")),
            "cells_state": "value",
            "px": int(sums["px"][n]), "ink_px": int(sums["ink_px"][n]),
            "cells_px": int(sums["cells_px"][n]), "cells_ink": int(sums["cells_ink"][n]),
            "bbox_state": bbox_state, "bbox": bbox}
    # Link identity is recorded only when the sidecar names link elements: an
    # empty map beside linked-category pixels records none of them.
    link_recorded = bool(isinstance(nfw, dict) and isinstance(nfw.get("link"), dict)
                         and nfw["link"])
    for raw_key, entry in sorted(((nfw or {}).get("link") or {}).items()):
        entry = entry if isinstance(entry, dict) else {}
        inst, elem = entry.get("link_inst_id"), entry.get("link_elem_id")
        if inst is None or elem is None:
            m = re.match(r"^(-?\d+):(-?\d+)$", str(raw_key))
            if not m:
                raise CensusRefusal("{0}: near_face_w_map.link key {1!r} names no link "
                                    "instance and element".format(
                                        Path(model_sidecar_path).name, raw_key))
            inst, elem = m.group(1), m.group(2)
        inst, elem = int(inst), int(elem)
        bbox_state, bbox = _bbox(entry)
        elements["link:{0}:{1}".format(inst, elem)] = {
            "source": "LINK", "element_id": None, "link_inst_id": inst,
            "link_elem_id": elem,
            "category": _value_or(shapes.model_entry_category, entry, None),
            "cells_state": "link_category_only",
            "px": None, "ink_px": None, "cells_px": None, "cells_ink": None,
            "bbox_state": bbox_state, "bbox": bbox}
    anno_counted = rollup.annotation_layer_counted(rec)
    return {
        "elements": elements,
        "model_cells": _occupancy_cells(rec, "all_cells", ("model_only", "overlap")),
        "model_cells_inside": _occupancy_cells(rec, "inside_crop_a",
                                               ("model_only", "overlap")),
        "anno_cells": (_occupancy_cells(rec, "all_cells", ("anno_only", "overlap"))
                       if anno_counted else None),
        "link_category_cells_ink": link_cells,
        "link_identity_unrecorded": (not link_recorded) and link_px > 0,
    }


# --- one run ------------------------------------------------------------------

_DATED_DIR = re.compile(r"^(\d{4}-\d{2}-\d{2})__")


def as_of_date(meta, run_dir):
    """run_meta's ``date`` (the as-of date), else the run directory's
    ``<date>__`` prefix (``run_meta.run_directory``), else None."""
    date = meta.get("date")
    if isinstance(date, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        return date
    m = _DATED_DIR.match(Path(run_dir).name)
    return m.group(1) if m else None


def _blank_run(run_dir, run_id, date, refused):
    return {"run_dir": str(run_dir), "run_id": run_id, "as_of_date": date,
            "refused": refused, "views": {}, "elements": {}, "measured": [],
            "unmeasured": {}, "requested": [], "doc_title": None}


def census_run(run_arg):
    """One run: its measured views' elements and its unmeasured views."""
    sidecars, folder, run_dir, grid_dir = rollup.run_layout(run_arg)
    meta = dsc.find_run_meta(folder / "run_meta.json") or {}
    run_id = meta.get("run_id")
    date = as_of_date(meta, run_dir)
    ident = rollup.run_identity(meta)
    if ident["refused"]:
        return _blank_run(run_dir, run_id, date, ident["refused"])
    if date is None:
        return _blank_run(run_dir, run_id, None,
                          "no as-of date: run_meta.json carries no \"date\" and the run "
                          "directory has no <date>__ prefix")
    core_state, _reason, core_by_id = rollup.load_views_core(run_dir, run_id)
    rows = rollup.run_rows(sidecars, grid_dir, run_id, (core_state, core_by_id), ident,
                           with_keys=False)
    run = _blank_run(run_dir, run_id, date, None)
    title = meta.get("doc_title")
    run["doc_title"] = (title.get("value") if isinstance(title, dict) else title)
    run["requested"] = rollup.unique_requested(ident["requested"])
    refusals, gridded = [], []
    for r in rows:
        if r["row_status"] == "orphan_sidecar":
            continue
        vid = rollup._vid(r["view_id"])
        if r["row_status"] != "gridded":
            run["unmeasured"][vid] = {"row_status": r["row_status"], "reason": r["reason"],
                                      "capture_status": r["run_meta_capture_status"]}
            continue
        gridded.append((vid, r["view_stem"]))
    # One view's elements at a time, folded into the run's totals and dropped:
    # a 358-view run holds ~280,000 element-in-view records (2025-11-01), and
    # keeping them all for every run of a series is what memory cannot hold.
    # Views are folded in view-id order, so "first seen" is order-free.
    for vid, stem in sorted(gridded, key=lambda vs: _vid_sort(vs[0])):
        try:
            view = view_census(folder / (stem + ".json"),
                               Path(grid_dir) / (stem + ".grid.json"))
        except CensusRefusal as ex:
            refusals.append(str(ex))
            continue
        fold(run["elements"], vid, view.pop("elements"), view)
        run["views"][vid] = view
    if refusals:
        named = any(s.startswith("grid_predates_ec_keys") for s in refusals)
        return _blank_run(run_dir, run_id, date, "{0}{1} gridded view(s) cannot be read "
                          "element by element: {2}".format(
                              "grid_predates_ec_keys: " if named else "", len(refusals),
                              "; ".join(refusals[:5])))
    run["measured"] = sorted(run["views"], key=_vid_sort)
    finish(run["elements"])
    return run


def fold(out, vid, elements, view):
    """Add one measured view's elements to the per-element totals ``out``.
    Views must arrive in ascending view-id order (``census_run`` sorts them):
    the first view seen gives an element's category and bbox, and a tie for
    the largest view keeps the lower id. Records the view's host-document
    element keys on ``view["host_keys"]`` for the id-overlap check."""
    view["host_keys"] = frozenset(k for k, e in elements.items() if e["source"] in HOST_BUCKET)
    for key, e in elements.items():
        a = out.get(key)
        if a is None:
            a = out[key] = {
                "source": e["source"], "element_id": e["element_id"],
                "link_inst_id": e["link_inst_id"], "link_elem_id": e["link_elem_id"],
                "category": e["category"], "cells_state": e["cells_state"],
                "views": [], "views_with_px": 0, "bbox_state": e["bbox_state"],
                "bbox": e["bbox"], "bbox_differs_views": 0, "source_conflict_views": 0,
                "first_view_id": None, "first_cells": None}
            for f in ("px", "ink_px", "cells_px", "cells_ink"):
                a[f] = 0 if e["cells_state"] == "value" else None
        a["views"].append(vid)
        if a["source"] == "unrecorded":
            a["source"] = e["source"]
        elif e["source"] not in ("unrecorded", a["source"]):
            a["source_conflict_views"] += 1
        if a["category"] is None:
            a["category"] = e["category"]
        if e["bbox"] != a["bbox"]:
            a["bbox_differs_views"] += 1
        if e["cells_state"] == "value":
            for f in ("px", "ink_px", "cells_px", "cells_ink"):
                a[f] += e[f]
            a["views_with_px"] += int(e["px"] > 0)
            if a["first_cells"] is None or e["cells_ink"] > a["first_cells"]:
                a["first_view_id"], a["first_cells"] = vid, e["cells_ink"]


def finish(out):
    """Close the per-element totals once every view is folded."""
    for a in out.values():
        if a["cells_state"] != "value":
            a.update(first_view_id=None, first_cells=None, repeat_cells=None,
                     views_with_px=None)
        else:
            a["repeat_cells"] = a["cells_ink"] - a["first_cells"]


def element_rows(run):
    rows = []
    for key in sorted(run["elements"]):
        a = run["elements"][key]
        bbox = a["bbox"] or (None,) * 6
        rows.append({
            "run_id": run["run_id"], "as_of_date": run["as_of_date"], "element_key": key,
            "source": a["source"], "element_id": a["element_id"],
            "link_inst_id": a["link_inst_id"], "link_elem_id": a["link_elem_id"],
            "category": a["category"], "views_collected": len(a["views"]),
            "views_with_px": a["views_with_px"], "cells_state": a["cells_state"],
            "px": a["px"], "ink_px": a["ink_px"], "cells_px": a["cells_px"],
            "cells_ink": a["cells_ink"], "first_view_id": a["first_view_id"],
            "first_cells": a["first_cells"], "repeat_cells": a["repeat_cells"],
            "bbox_state": a["bbox_state"], "bbox_dx_ft": bbox[0], "bbox_dy_ft": bbox[1],
            "bbox_dz_ft": bbox[2], "bbox_differs_views": a["bbox_differs_views"],
            "source_conflict_views": a["source_conflict_views"],
            "bbox_cx_ft": bbox[3], "bbox_cy_ft": bbox[4], "bbox_cz_ft": bbox[5]})
    return rows


def _median(values):
    return float(np.median(values)) if values else None


def views_per_element_hist(counts):
    out = {}
    for lo, hi in VIEWS_PER_ELEMENT_BINS:
        if hi is None:
            name = "{0}+".format(lo)
        else:
            name = str(lo) if lo == hi else "{0}-{1}".format(lo, hi)
        out[name] = sum(1 for c in counts if c >= lo and (hi is None or c <= hi))
    return out


def run_stats(run):
    """The per-run figures (a ``RUN_COLUMNS`` row plus the summary's detail)."""
    els = run["elements"]
    unmeasured = run["unmeasured"].values()
    host = [a for a in els.values() if a["source"] in HOST_BUCKET]
    cells = [a["cells_ink"] for a in host]
    views_n = [len(a["views"]) for a in els.values()]
    total = sum(cells)
    first = sum(a["first_cells"] for a in host)

    def _sum(field):
        values = [v[field] for v in run["views"].values()]
        return None if any(x is None for x in values) else sum(values)
    row = {
        "as_of_date": run["as_of_date"], "run_id": run["run_id"], "run_dir": run["run_dir"],
        "doc_title": run["doc_title"],
        "refused": run["refused"], "views_requested": len(run["requested"]),
        "views_measured": len(run["measured"]), "views_unmeasured": len(run["unmeasured"]),
        "views_capture_failed": sum(1 for u in unmeasured
                                    if u["capture_status"] != "success"
                                    and u["row_status"] != "below_capture_floor"),
        "views_below_capture_floor": sum(1 for u in unmeasured
                                         if u["row_status"] == "below_capture_floor"),
        "model_cells_occupancy": _sum("model_cells"),
        "model_cells_occupancy_inside_crop_a": _sum("model_cells_inside"),
        # Blank, not 0, when no measured view counted an annotation layer.
        "anno_cells_occupancy": (sum(v["anno_cells"] for v in run["views"].values()
                                     if v["anno_cells"] is not None)
                                 if any(v["anno_cells"] is not None
                                        for v in run["views"].values()) else None),
        "anno_cells_views": sum(1 for v in run["views"].values()
                                if v["anno_cells"] is not None),
        "elements_distinct": len(els),
        "element_cells_ink": total,
        "host_elements_with_ink": sum(1 for c in cells if c > 0),
        "cells_per_element": (total / float(len(host))) if host else None,
        "cells_per_element_median": _median(cells),
        "first_cells": first, "repeat_cells": total - first,
        "first_share": (first / float(total)) if total else None,
        "link_category_cells_ink": sum(v["link_category_cells_ink"]
                                       for v in run["views"].values()),
        "views_per_element_mean": (sum(views_n) / float(len(views_n))) if views_n else None,
        "views_per_element_median": _median(views_n),
        "views_per_element_max": max(views_n) if views_n else None,
        "link_identity_unrecorded_views": sum(1 for v in run["views"].values()
                                              if v["link_identity_unrecorded"]),
    }
    for s in SOURCES:
        row["elements_" + s] = sum(1 for a in els.values() if a["source"] == s)
    by_cat = {}
    for a in els.values():
        by_cat.setdefault(a["source"], {})
        cat = a["category"] if a["category"] is not None else "(none)"
        by_cat[a["source"]][cat] = by_cat[a["source"]].get(cat, 0) + 1
    unmeasured_by = {}
    for u in unmeasured:
        unmeasured_by[u["row_status"]] = unmeasured_by.get(u["row_status"], 0) + 1
    detail = {
        "elements_by_source_category": by_cat,
        "views_per_element": {
            "denominator": len(views_n), "denominator_rule": "distinct elements",
            "bins": views_per_element_hist(views_n),
            "host_bucket": views_per_element_hist([len(a["views"]) for a in host]),
            "LINK": views_per_element_hist([len(a["views"]) for a in els.values()
                                            if a["source"] == "LINK"])},
        "unmeasured_views_by_row_status": {"denominator": len(run["requested"]),
                                           "counts": unmeasured_by},
        "unmeasured_views": [dict(view_id=vid, **u) for vid, u in
                             sorted(run["unmeasured"].items(), key=lambda kv: _vid_sort(kv[0]))],
    }
    return row, detail


# --- across runs ------------------------------------------------------------------

def _side(views, measured, unmeasured):
    """How an element's views stand on the OTHER run: any unmeasured there
    wins (a failed capture never makes a creation or a deletion), then any
    measured there; else none of them was requested there ("dropped")."""
    if any(v in unmeasured for v in views):
        return "unmeasured"
    if any(v in measured for v in views):
        return "measured"
    return "dropped"


def transitions(prev, cur):
    """``({key: (enter_how, leave_how)}, counts)`` between two adjacent runs.
    ``enter_how`` is how a key seen in ``cur`` relates to ``prev``;
    ``leave_how`` how a key seen in ``prev`` relates to ``cur``."""
    pm, pu = set(prev["measured"]), set(prev["unmeasured"])
    cm, cu = set(cur["measured"]), set(cur["unmeasured"])
    counts = dict(("{0}{1}".format(t, sfx), 0) for t in TRANSITIONS for sfx in ("", "_LINK"))
    enter, leave = {}, {}
    for key in set(prev["elements"]) | set(cur["elements"]):
        p, c = prev["elements"].get(key), cur["elements"].get(key)
        sfx = "_LINK" if (c or p)["source"] == "LINK" else ""
        if p and c:
            word = "persisted"
        elif c:
            side = _side(c["views"], pm, pu)
            word = {"unmeasured": "unmeasured_in", "measured": "created",
                    "dropped": "new_view"}[side]
            enter[key] = {"unmeasured_in": "unmeasured", "created": "created",
                          "new_view": "new_view"}[word]
        else:
            side = _side(p["views"], cm, cu)
            word = {"unmeasured": "unmeasured_out", "measured": "gone",
                    "dropped": "view_dropped"}[side]
            leave[key] = {"unmeasured_out": "unmeasured", "gone": "gone",
                          "view_dropped": "view_dropped"}[word]
        counts[word + sfx] += 1
    return enter, leave, counts


def host_id_overlap(prev, cur):
    """Host element ids over the views measured in both runs: ``(views,
    jaccard)``; jaccard None when neither run holds one there."""
    common = set(prev["measured"]) & set(cur["measured"])

    def _ids(run):
        return set(k for vid in common for k in run["views"][vid]["host_keys"])
    a, b = _ids(prev), _ids(cur)
    union = a | b
    return len(common), (len(a & b) / float(len(union)) if union else None)


def series(runs):
    """Per element across the (ordered, unrefused) runs, and per-run
    transition counts keyed by run_id."""
    per_run, elements = {}, {}
    for i, run in enumerate(runs):
        if i == 0:
            enter, leave, counts = {}, {}, None
            overlap = (None, None)
        else:
            enter, leave, counts = transitions(runs[i - 1], run)
            overlap = host_id_overlap(runs[i - 1], run)
        per_run[run["run_id"]] = {"counts": counts, "prev": runs[i - 1] if i else None,
                                  "overlap": overlap}
        for key, how in leave.items():
            elements[key]["leave_hows"].append((i, how))
        for key, a in run["elements"].items():
            e = elements.get(key)
            if e is None:
                e = elements[key] = {"source": a["source"], "category": a["category"],
                                     "seen": [], "first_how": ("series_start" if i == 0
                                                               else enter.get(key)),
                                     "leave_hows": []}
            e["seen"].append(i)
            e["views"] = a["views"]
            if a["category"] is not None:
                e["category"] = a["category"]
            if e["source"] == "unrecorded" and a["source"] != "unrecorded":
                e["source"] = a["source"]        # as aggregate() composes it
        # Absent here though seen before: unmeasured when any view that last
        # drew it is unmeasured in this run (counted for EVERY such run).
        unmeasured = set(run["unmeasured"])
        for key, e in elements.items():
            if key not in run["elements"] and set(e.get("views") or ()) & unmeasured:
                e.setdefault("unmeasured_runs", []).append(i)
    rows = []
    for key in sorted(elements):
        e = elements[key]
        first, last = e["seen"][0], e["seen"][-1]
        last_how = "series_end" if last == len(runs) - 1 else dict(e["leave_hows"]).get(
            last + 1)
        between = range(first, last + 1)
        rows.append({
            "element_key": key, "source": e["source"], "category": e["category"],
            "first_as_of_date": runs[first]["as_of_date"], "first_run_id": runs[first]["run_id"],
            "first_how": e["first_how"],
            "last_as_of_date": runs[last]["as_of_date"], "last_run_id": runs[last]["run_id"],
            "last_how": last_how, "runs_seen": len(e["seen"]),
            "runs_in_series": len(runs),
            "runs_unmeasured_between": sum(1 for i in e.get("unmeasured_runs", [])
                                           if i in between),
            "reappearances": sum(1 for a, b in zip(e["seen"], e["seen"][1:]) if b > a + 1)})
    return per_run, rows


# --- writing ----------------------------------------------------------------------

def _file_part(text):
    """``text`` safe as one file-name part: no path separators or traversal."""
    return re.sub(r'[\\/:*?"<>|]', "_", str(text)).replace("..", "_")


def _write_csv(path, columns, rows):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for r in rows:
            writer.writerow(dict((k, _cell(r.get(k))) for k in columns))
    return grid.sha256_file(path)


def census(run_args, out_dir, progress=None):
    """Every run, the series, the CSVs, then the summary. Raises
    CensusRefusal (exit 2) when two runs share an as-of date. ``progress``,
    when given, is called with one line per run as it is read."""
    runs = []
    for n, arg in enumerate(run_args, 1):
        started = time.time()
        run = census_run(arg)
        runs.append(run)
        if progress is not None:
            progress("[{0}/{1}] {2:.0f}s {3}".format(
                n, len(run_args), time.time() - started,
                "REFUSED {0}: {1}".format(run["run_dir"], run["refused"]) if run["refused"]
                else "{0} {1}: {2} measured / {3} requested views, {4} distinct "
                     "elements".format(run["as_of_date"], run["run_id"],
                                       len(run["measured"]), len(run["requested"]),
                                       len(run["elements"]))))
    live = [r for r in runs if not r["refused"]]
    dates = {}
    for r in live:
        dates.setdefault(r["as_of_date"], []).append(r["run_dir"])
    ids = {}
    for r in live:
        ids.setdefault(r["run_id"], []).append(r["run_dir"])
    same_id = dict((i, dirs) for i, dirs in ids.items() if len(dirs) > 1)
    if same_id:
        raise CensusRefusal("runs share a run_id, so they cannot be told apart: "
                            + "; ".join("{0}: {1}".format(i, ", ".join(dirs))
                                        for i, dirs in sorted(same_id.items())))
    shared = dict((d, dirs) for d, dirs in dates.items() if len(dirs) > 1)
    if shared:
        raise CensusRefusal("runs share an as-of date, so the series has no order: "
                            + "; ".join("{0}: {1}".format(d, ", ".join(dirs))
                                        for d, dirs in sorted(shared.items())))
    live.sort(key=lambda r: r["as_of_date"])
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # The summary is the write-last completion mark: a previous census's must
    # not outlive this one's partial output.
    stale = out_dir / SUMMARY_NAME
    if stale.exists():
        stale.unlink()
    per_run, series_rows = series(live)
    hashes, run_rows, details = {}, [], {}
    for run in live:
        name = "{0}__{1}{2}".format(run["as_of_date"], _file_part(run["run_id"]),
                                    ELEMENTS_SUFFIX)
        hashes[name] = _write_csv(out_dir / name, ELEMENT_COLUMNS, element_rows(run))
        row, detail = run_stats(run)
        info = per_run[run["run_id"]]
        row["prev_as_of_date"] = info["prev"]["as_of_date"] if info["prev"] else None
        row["common_measured_views"], row["host_id_jaccard_common_views"] = info["overlap"]
        for k, v in (info["counts"] or {}).items():
            row[k] = v
        run_rows.append(row)
        details[run["run_id"]] = detail
    for run in runs:
        if run["refused"]:
            run_rows.append({"as_of_date": run["as_of_date"], "run_id": run["run_id"],
                             "run_dir": run["run_dir"], "refused": run["refused"]})
    hashes[RUNS_CSV] = _write_csv(out_dir / RUNS_CSV, RUN_COLUMNS, run_rows)
    hashes[SERIES_CSV] = _write_csv(out_dir / SERIES_CSV, SERIES_COLUMNS, series_rows)
    summary = {
        "schema": SCHEMA, "tool_version": TOOL_VERSION,
        "runs": [{"run_dir": r["run_dir"], "run_id": r["run_id"],
                  "as_of_date": r["as_of_date"], "refused": r["refused"]} for r in runs],
        "refused_runs": {"denominator": len(runs),
                         "count": sum(1 for r in runs if r["refused"])},
        "views_unmeasured": {
            "denominator": sum(len(r["requested"]) for r in live),
            "denominator_rule": "requested views of the runs not refused",
            "count": sum(len(r["unmeasured"]) for r in live)},
        "per_run": details,
        "series_first_how": {"denominator": len(series_rows),
                             "counts": dict((h, sum(1 for s in series_rows
                                                    if s["first_how"] == h)) for h in ENTER)},
        "series_last_how": {"denominator": len(series_rows),
                            "counts": dict((h, sum(1 for s in series_rows
                                                   if s["last_how"] == h)) for h in LEAVE)},
        "definitions": {
            "measured_view": "a requested view whose grid roll-up row is gridded",
            "cells_ink": "a key's ec_ rows with ec_ink_px >= stage_a_grid."
                         "OCCUPANCY_MIN_INK_PX, through stage_a_kinds._key_sums",
            "first_cells": "the ink cells of the element's largest measured view "
                           "(ties: lowest view id)",
            "model_cells_occupancy": "grid occupancy all_cells model_only + overlap, "
                                     "summed over measured views",
            "link": "link elements are counted by identity; their cells are "
                    "colour-per-category and are carried as link_category_cells_ink",
        },
        "csv_sha256": hashes,
    }
    with open(out_dir / SUMMARY_NAME, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True, default=str)
    return summary, runs


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("runs", nargs="+", metavar="run",
                    help="a run directory or its capture folder (color_id/ or color_id_buffer/)")
    ap.add_argument("--out", default=None,
                    help="output directory (default, one run only: its grid/ or analysis_grid/)")
    args = ap.parse_args(argv)
    if len(args.runs) > 1 and not args.out:
        ap.error("--out is required with more than one run")
    missing = [run for run in args.runs if not Path(run).is_dir()]
    if missing:
        ap.error("not a directory: {0}".format(", ".join(missing)))
    args.runs = [str(Path(run).resolve()) for run in args.runs]
    layouts = [rollup.run_layout(run) for run in args.runs]
    out = Path(args.out) if args.out else layouts[0][3]
    for _s, folder, _r, _g in layouts:
        if rollup._inside(out, folder):
            ap.error("--out {0} is inside the capture folder {1}; the census never "
                     "writes there".format(out, folder))
    try:
        summary, runs = census(args.runs, out,
                               progress=lambda line: print(line, flush=True))
    except CensusRefusal as ex:
        print("REFUSED: {0}".format(ex))
        return 2
    print("wrote {0}".format(out / SUMMARY_NAME))
    refused = summary["refused_runs"]["count"]
    if refused == len(runs):
        print("every run was refused; nothing was counted")
        return 2
    return 0 if not refused and not summary["views_unmeasured"]["count"] else 1


if __name__ == "__main__":
    sys.exit(main())
