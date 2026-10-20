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

OUTPUTS (2.0.0). Three steps, so adding a capture costs one run's census and
one pair rather than the whole series (44 runs, ~45 min, under 1.3.0):

  * PER RUN, into the run's grid folder (``grid/`` in v2, ``analysis_grid/``
    in v1; names ``output_layout.census_names``, dated by ``file_date`` in
    both layouts; an undated census an earlier 2.0.0 wrote is renamed, not
    recomputed, by the driver):
    ``element_census_<d>.csv`` (the per-element columns above, plus
    ``view_ids``), then ``element_census_<d>.summary.json`` -- the run
    figures (the runs-CSV row without the transition columns), the measured
    and unmeasured view ids, refusals, the tool version, the CSV's sha256 and
    the sha256 of every input read (``census_inputs``) -- written LAST
    (CLAUDE.md, defect class 4). A refused run: a summary saying so, no CSV.
  * PAIR, into the later run's grid folder (``transitions_names``):
    ``element_transitions_<d>.csv``, one row per element key on either side
    with its class, both sides' presence and the views that decided it, then
    the summary: counts per class (and ``_LINK``), common measured views, the
    host-id Jaccard, the predecessor's run_id / path / as-of date, both census
    files' sha256. The predecessor is the run with the previous as-of date in
    the same model folder (``predecessors``), never run_id or folder order.
    The pair is read from the two census FILES through ``classify``,
    ``transitions`` and ``host_id_overlap``, the functions the series uses.
    The first run: a ``no_predecessor`` summary and no CSV, never zeros. Two
    runs on one as-of date refuse, by name, their pairs and the next run's.
  * DRIVER, ``--root <model folder>``: walks the runs
    (``stage_a_runs_index.discover``) and computes only what is missing or
    stale (``census_staleness``: an input hash, the schema, the version or
    the CSV changed; ``pair_staleness``: the predecessor is no longer the
    previous as-of date, or either census changed), printing what it did and
    why.

``--out DIR`` with run arguments is the multi-run convenience, built on the
same functions: ``<as_of_date>__<run_id>.elements.csv`` per run,
``element_census_runs.csv``, ``element_census_series.csv``, then
``element_census.summary.json`` naming every CSV's sha256 (as in 1.3.0). The
series is otherwise BI's (tools/notes/ELEMENT_CENSUS_2025-02-03_to_2025-11-01.md
section 8). An empty CSV cell is "not applicable", never 0. Columns are
appended, never reordered.

    python tools/stage_a_element_census.py --root <model folder>
    python tools/stage_a_element_census.py <run> [<run> ...]           # each run's census
    python tools/stage_a_element_census.py <run> [<run> ...] --out DIR

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

Exit, ``--root``: 0 nothing refused, 1 a census or pair refused, 2 no run
found. Runs without ``--out``: 0 every view measured, 1 a view unmeasured, 2
a run refused. ``--out``: 0 when no run is refused and every requested view
is measured; 1 for an unmeasured view or a refused run; 2 when every run is
refused, two runs share an as-of date, or on bad arguments (a run that is not
a directory).
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
from vop_interwoven import output_layout  # noqa: E402

SCHEMA = "vop.stage_a.element_census.v1"
RUN_SCHEMA = "vop.stage_a.element_census.run.v1"
PAIR_SCHEMA = "vop.stage_a.element_transitions.v1"
TOOL_VERSION = "2.0.0"

# The multi-run --out convenience (one folder, every run).
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
    # 2.0.0: appended. The measured views the element was collected in,
    # ";"-separated, in view-id order: what the pair step classifies on.
    "view_ids",
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
# The run-level figures a per-run census summary carries: the runs-CSV row
# without the columns that compare a run with its predecessor.
PAIR_RUN_COLUMNS = (["prev_as_of_date", "common_measured_views",
                     "host_id_jaccard_common_views"]
                    + [t for t in TRANSITIONS] + ["{0}_LINK".format(t) for t in TRANSITIONS])
RUN_FIGURE_COLUMNS = [c for c in RUN_COLUMNS if c not in PAIR_RUN_COLUMNS]
TRANSITION_COLUMNS = [
    "run_id", "as_of_date", "prev_run_id", "prev_as_of_date", "element_key", "source",
    "category", "class", "in_prev", "in_cur", "deciding_view_ids",
]
SERIES_COLUMNS = [
    "element_key", "source", "category", "first_as_of_date", "first_run_id",
    "first_how", "last_as_of_date", "last_run_id", "last_how", "runs_seen",
    "runs_in_series", "runs_unmeasured_between", "reappearances",
]


DEFINITIONS = {
    "measured_view": "a requested view whose grid roll-up row is gridded",
    "cells_ink": "a key's ec_ rows with ec_ink_px >= stage_a_grid."
                 "OCCUPANCY_MIN_INK_PX, through stage_a_kinds._key_sums",
    "first_cells": "the ink cells of the element's largest measured view "
                   "(ties: lowest view id)",
    "model_cells_occupancy": "grid occupancy all_cells model_only + overlap, "
                             "summed over measured views",
    "link": "link elements are counted by identity; their cells are "
            "colour-per-category and are carried as link_category_cells_ink",
}


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
            "bbox_cx_ft": bbox[3], "bbox_cy_ft": bbox[4], "bbox_cz_ft": bbox[5],
            "view_ids": ";".join(sorted(a["views"], key=_vid_sort))})
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
    """``(how, deciding views)``: how an element's views stand on the OTHER
    run. Any unmeasured there wins (a failed capture never makes a creation
    or a deletion), then any measured there; else none of them was requested
    there ("dropped"). The deciding views are the ones that matched."""
    hit = [v for v in views if v in unmeasured]
    if hit:
        return "unmeasured", hit
    hit = [v for v in views if v in measured]
    if hit:
        return "measured", hit
    return "dropped", list(views)


def classify(prev, cur):
    """``{key: record}`` for every element key on either side of two adjacent
    runs: its transition word (``TRANSITIONS``), the views that decided it
    (none for ``persisted``: presence on both sides decides it), presence on
    each side, and its source and category (the later side's where both have
    it). The one classifier: ``transitions`` counts it and the pair step
    writes it."""
    pm, pu = set(prev["measured"]), set(prev["unmeasured"])
    cm, cu = set(cur["measured"]), set(cur["unmeasured"])
    out = {}
    for key in set(prev["elements"]) | set(cur["elements"]):
        p, c = prev["elements"].get(key), cur["elements"].get(key)
        if p and c:
            word, deciding = "persisted", []
        elif c:
            side, deciding = _side(c["views"], pm, pu)
            word = {"unmeasured": "unmeasured_in", "measured": "created",
                    "dropped": "new_view"}[side]
        else:
            side, deciding = _side(p["views"], cm, cu)
            word = {"unmeasured": "unmeasured_out", "measured": "gone",
                    "dropped": "view_dropped"}[side]
        e = c or p
        out[key] = {"word": word, "deciding_views": sorted(deciding, key=_vid_sort),
                    "source": e["source"],
                    "category": (c or {}).get("category") if (c or {}).get("category")
                    is not None else (p or {}).get("category"),
                    "in_prev": p is not None, "in_cur": c is not None}
    return out


ENTER_OF = {"unmeasured_in": "unmeasured", "created": "created", "new_view": "new_view"}
LEAVE_OF = {"unmeasured_out": "unmeasured", "gone": "gone", "view_dropped": "view_dropped"}


def transitions(prev, cur):
    """``({key: enter_how}, {key: leave_how}, counts)`` between two adjacent
    runs, from ``classify``. ``enter_how`` is how a key seen in ``cur``
    relates to ``prev``; ``leave_how`` how a key seen in ``prev`` relates to
    ``cur``."""
    counts = dict(("{0}{1}".format(t, sfx), 0) for t in TRANSITIONS for sfx in ("", "_LINK"))
    enter, leave = {}, {}
    for key, rec in classify(prev, cur).items():
        word = rec["word"]
        if word in ENTER_OF:
            enter[key] = ENTER_OF[word]
        elif word in LEAVE_OF:
            leave[key] = LEAVE_OF[word]
        counts[word + ("_LINK" if rec["source"] == "LINK" else "")] += 1
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
        "definitions": DEFINITIONS,
        "csv_sha256": hashes,
    }
    with open(out_dir / SUMMARY_NAME, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True, default=str)
    return summary, runs


# --- per run, into the run's grid folder (2.0.0) -------------------------------------

class RunFiles(object):
    """Where one run's census and transition files are, in its own layout
    (``vop_interwoven/output_layout``: v2 names dated by ``file_date``, v1
    undated), and the identity read from its run_meta."""

    def __init__(self, run_arg):
        self.sidecars, self.folder, self.run_dir, self.grid_dir = rollup.run_layout(run_arg)
        self.run_dir = Path(self.run_dir).resolve()
        self.grid_dir = Path(self.grid_dir)
        self.meta = dsc.find_run_meta(Path(self.folder) / "run_meta.json") or {}
        self.run_id = self.meta.get("run_id")
        self.as_of_date = as_of_date(self.meta, self.run_dir)
        self.layout = output_layout.layout_of_capture_dir(self.folder)
        d = output_layout.file_date(self.meta) or self.as_of_date or "undated"
        self.census_csv, self.census_summary = output_layout.census_names(self.layout, d)
        self.pair_csv, self.pair_summary = output_layout.transitions_names(self.layout, d)

    @property
    def name(self):
        return self.run_dir.name

    def census_path(self, which):
        return self.grid_dir / (self.census_csv if which == "csv" else self.census_summary)

    def pair_path(self, which):
        return self.grid_dir / (self.pair_csv if which == "csv" else self.pair_summary)


def _rel(path, base):
    try:
        return Path(path).resolve().relative_to(Path(base).resolve()).as_posix()
    except ValueError:
        return str(path)


def census_inputs(files):
    """``{path relative to the run: sha256}`` of every file the census reads:
    the run_meta, the views_core file(s), every model sidecar, each view's
    registration record and its grid json and npz where present. Hashing
    them is how a recapture, a re-registration or a re-grid makes a census
    stale. The TIFFs are not hashed: the census never reads one, and every
    writer that replaces one rewrites the record that names its hash (the
    sidecar, the registration record, the grid) -- a DECLARED LIMIT, not a
    proof: a TIFF swapped by hand under unchanged records is not seen."""
    paths = set(output_layout.run_meta_candidates(files.run_dir))
    paths |= set(output_layout.run_meta_candidates(files.folder))
    paths |= set(str(p) for p in files.run_dir.glob("views_core_*.csv"))
    for sidecar in files.sidecars:
        sidecar = Path(sidecar)
        paths.add(str(sidecar))
        reg_path = rollup.registration_record_path(sidecar)
        for extra in (reg_path, files.grid_dir / (sidecar.stem + ".grid.json"),
                      files.grid_dir / (sidecar.stem + ".grid.npz")):
            if Path(extra).is_file():
                paths.add(str(extra))
    return dict((_rel(p, files.run_dir), grid.sha256_file(p)) for p in sorted(paths))


def _write_json(path, record):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True, default=str)
    return grid.sha256_file(path)


def _unlink(path):
    if Path(path).exists():
        Path(path).unlink()


def _ours(rec, schema):
    return isinstance(rec, dict) and rec.get("schema") == schema


def remove_undated(files, step):
    """Remove the undated census (``step`` "census") or transitions files an
    earlier 2.0.0 wrote into the run's grid folder, now that their dated
    names are written: only a summary of this tool's own schema, and the CSV
    it names under the undated name. Anything else of that name is left."""
    legacy_csv, legacy_summary = (output_layout.CENSUS_UNDATED if step == "census"
                                  else output_layout.TRANSITIONS_UNDATED)
    old = files.grid_dir / legacy_summary
    current = files.census_summary if step == "census" else files.pair_summary
    if legacy_summary == current or not old.is_file():
        return False
    rec, _why = _read_record(old)
    if not _ours(rec, RUN_SCHEMA if step == "census" else PAIR_SCHEMA):
        return False
    old.unlink()
    if rec.get("csv") == legacy_csv:
        _unlink(files.grid_dir / legacy_csv)
    return True


def adopt_undated(files):
    """Rename a run's undated census (an earlier 2.0.0's names) to its dated
    names rather than recompute it: the CSV first, then the summary, now
    naming the dated CSV, written to its dated name and the undated one
    removed (so an interruption leaves an undated summary naming a missing
    CSV, which is stale and recomputed -- never a dated summary without its
    CSV). Only a current-version summary of this schema whose CSV is the one
    it names is adopted. Returns the old summary name, or None. The pairs
    that recorded the old summary's sha256 are then stale and recomputed."""
    legacy_csv, legacy_summary = output_layout.CENSUS_UNDATED
    old, new = files.grid_dir / legacy_summary, files.census_path("summary")
    if old == new or new.exists() or not old.is_file():
        return None
    rec, _why = _read_record(old)
    if rec is None or _version_check(rec, RUN_SCHEMA) or _csv_check(rec, files.grid_dir):
        return None
    if rec.get("state") == "value":
        if rec.get("csv") != legacy_csv:
            return None
        (files.grid_dir / legacy_csv).replace(files.census_path("csv"))
        rec["csv"] = files.census_csv
    rec["renamed_from"] = legacy_summary
    _write_json(new, rec)
    old.unlink()
    return legacy_summary


def write_run_census(files, inputs=None):
    """The census of one run, into its grid folder: the element CSV, then
    the summary naming its sha256 and the sha256 of every input (written
    last; CLAUDE.md, defect class 4). A refused run gets a summary saying so
    and no CSV. Returns the summary as written."""
    if inputs is None:
        inputs = census_inputs(files)
    run = census_run(files.run_dir)
    files.grid_dir.mkdir(parents=True, exist_ok=True)
    csv_path, summary_path = files.census_path("csv"), files.census_path("summary")
    # The summary is the write-last completion mark: the previous one must
    # not outlive this census's partial output.
    _unlink(summary_path)
    row, detail = run_stats(run) if not run["refused"] else (
        {"as_of_date": run["as_of_date"], "run_id": run["run_id"],
         "run_dir": run["run_dir"], "refused": run["refused"]}, None)
    summary = {
        "schema": RUN_SCHEMA, "tool_version": TOOL_VERSION,
        "state": "refused" if run["refused"] else "value", "refused": run["refused"],
        "run_id": run["run_id"], "as_of_date": run["as_of_date"],
        "run_dir": str(files.run_dir), "run_dir_name": files.name, "layout": files.layout,
        "doc_title": run["doc_title"],
        "figures": dict((c, row.get(c)) for c in RUN_FIGURE_COLUMNS),
        "detail": detail,
        "measured_view_ids": list(run["measured"]),
        "unmeasured_view_ids": sorted(run["unmeasured"], key=_vid_sort),
        "inputs": inputs,
        "inputs_rule": "sha256 of the run_meta, views_core, every model sidecar, and each "
                       "view's registration record, grid json and grid npz present",
        "csv": None, "csv_sha256": None,
        "definitions": DEFINITIONS,
    }
    if run["refused"]:
        _unlink(csv_path)
    else:
        summary["csv"] = csv_path.name
        summary["csv_sha256"] = _write_csv(csv_path, ELEMENT_COLUMNS, element_rows(run))
    _write_json(summary_path, summary)
    remove_undated(files, "census")
    return summary


def _read_record(path):
    try:
        rec = _load_json(path)
    except (OSError, ValueError) as ex:
        return None, "{0} cannot be read: {1}".format(Path(path).name, _err(ex))
    if not isinstance(rec, dict):
        return None, "{0} is not a JSON object".format(Path(path).name)
    return rec, None


def _csv_check(rec, folder):
    """Why the CSV a summary names is not the one on disk, or None."""
    if rec.get("state") != "value":
        return None
    name = rec.get("csv")
    if not isinstance(name, str) or Path(name).name != name:
        return "the summary names csv {0!r}, not a file beside it".format(name)
    path = Path(folder) / name
    if not path.is_file():
        return "{0} named by the summary is missing".format(name)
    if grid.sha256_file(path) != rec.get("csv_sha256"):
        return "{0} does not match the sha256 its summary records".format(name)
    return None


def _version_check(rec, schema):
    if rec.get("schema") != schema:
        return "the summary's schema is {0!r}, not {1!r}".format(rec.get("schema"), schema)
    if rec.get("tool_version") != TOOL_VERSION:
        return "written by census {0}, this is {1}".format(
            rec.get("tool_version"), TOOL_VERSION)
    return None


def _listed(names, limit=5):
    names = sorted(names)
    return ", ".join(names[:limit]) + (" (+{0} more)".format(len(names) - limit)
                                       if len(names) > limit else "")


def census_staleness(files, inputs):
    """Why the run's census on disk is not current: empty when it is.
    Stale when it is absent, from another schema or census version, its CSV
    is not the one it names, or any input's sha256 differs from the one it
    recorded (or an input was added or removed)."""
    path = files.census_path("summary")
    if not path.is_file():
        return ["no {0}".format(path.name)]
    rec, why = _read_record(path)
    if rec is None:
        return [why]
    why = _version_check(rec, RUN_SCHEMA) or _csv_check(rec, files.grid_dir)
    if why:
        return [why]
    recorded = rec.get("inputs") if isinstance(rec.get("inputs"), dict) else {}
    reasons = []
    changed = [k for k in inputs if k in recorded and recorded[k] != inputs[k]]
    added = [k for k in inputs if k not in recorded]
    removed = [k for k in recorded if k not in inputs]
    for word, keys in (("changed", changed), ("added", added), ("removed", removed)):
        if keys:
            reasons.append("{0} input(s) {1}: {2}".format(len(keys), word, _listed(keys)))
    return reasons


def load_run_census(files):
    """A run's census as the pair step needs it, read from its FILES: the
    measured and unmeasured view ids, and per element its source, category
    and views; per measured view its host-document keys. CensusRefusal when
    the census is absent, refused, or its CSV is not the one it names."""
    path = files.census_path("summary")
    if not path.is_file():
        raise CensusRefusal("{0} has no census ({1})".format(files.name, path.name))
    rec, why = _read_record(path)
    if rec is None:
        raise CensusRefusal("{0}: {1}".format(files.name, why))
    why = _version_check(rec, RUN_SCHEMA) or _csv_check(rec, files.grid_dir)
    if why:
        raise CensusRefusal("{0}: {1}".format(files.name, why))
    if rec.get("state") != "value":
        raise CensusRefusal("the census of {0} is refused: {1}".format(
            files.name, rec.get("refused")))
    measured = [str(v) for v in rec.get("measured_view_ids") or []]
    run = {"run_id": rec.get("run_id"), "as_of_date": rec.get("as_of_date"),
           "run_dir": str(files.run_dir), "measured": measured,
           "unmeasured": dict((str(v), {}) for v in rec.get("unmeasured_view_ids") or []),
           "elements": {}, "views": dict((v, {"host_keys": set()}) for v in measured)}
    with open(str(files.grid_dir / rec["csv"]), encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            views = [v for v in (row.get("view_ids") or "").split(";") if v]
            run["elements"][row["element_key"]] = {
                "source": row["source"], "category": row["category"] or None,
                "views": views}
            if row["source"] in HOST_BUCKET:
                for v in views:
                    if v in run["views"]:
                        run["views"][v]["host_keys"].add(row["element_key"])
    for view in run["views"].values():
        view["host_keys"] = frozenset(view["host_keys"])
    return run, rec


# --- the pair step ----------------------------------------------------------------------

def predecessors(all_files):
    """``{run_dir: (state, predecessor RunFiles or None, reason)}``. A run's
    predecessor is the run with the previous as-of date (run_meta ``date``)
    in the same model folder (the run folder's parent, ``model_key`` in
    tools/stage_a_runs_index.py). Never run_id or folder order. Refused by
    name when the run or the previous date is shared by two runs, or the
    run has no as-of date; ``no_predecessor`` for the first run."""
    groups = {}
    for f in all_files:
        groups.setdefault(str(f.run_dir.parent), []).append(f)
    out = {}
    for folder, members in groups.items():
        by_date = {}
        for f in members:
            if f.as_of_date is not None:
                by_date.setdefault(f.as_of_date, []).append(f)
        for f in members:
            if f.as_of_date is None:
                out[str(f.run_dir)] = ("refused", None, "{0} has no as-of date".format(f.name))
                continue
            same = by_date[f.as_of_date]
            if len(same) > 1:
                out[str(f.run_dir)] = ("refused", None, "two runs on as-of date {0} in {1}: "
                                       "{2}".format(f.as_of_date, folder,
                                                    _listed(g.name for g in same)))
                continue
            earlier = sorted(d for d in by_date if d < f.as_of_date)
            if not earlier:
                out[str(f.run_dir)] = ("no_predecessor", None,
                                       "no run in {0} has an earlier as-of date".format(folder))
                continue
            prev = by_date[earlier[-1]]
            if len(prev) > 1:
                out[str(f.run_dir)] = ("refused", None, "the previous as-of date {0} has two "
                                       "runs in {1}: {2}".format(
                                           earlier[-1], folder, _listed(g.name for g in prev)))
                continue
            out[str(f.run_dir)] = ("value", prev[0], None)
    return out


def _summary_sha(files):
    path = files.census_path("summary")
    return grid.sha256_file(path) if path.is_file() else None


def pair_identity(cur, choice):
    """What a pair's outputs are a function of: the predecessor chosen (by
    run_id, folder name and as-of date -- not by absolute path, so a renamed
    model folder does not make every pair stale) and both census summaries'
    sha256 (each names its CSV's)."""
    state, prev, reason = choice
    return {"state": state, "reason": reason,
            "predecessor": None if prev is None else {
                "run_id": prev.run_id, "run_dir_name": prev.name,
                "as_of_date": prev.as_of_date},
            "census_summary_sha256": {"cur": _summary_sha(cur),
                                      "prev": None if prev is None else _summary_sha(prev)}}


def pair_staleness(cur, choice):
    """Why the run's transitions on disk are not current: empty when they are."""
    path = cur.pair_path("summary")
    if not path.is_file():
        return ["no {0}".format(path.name)]
    rec, why = _read_record(path)
    if rec is None:
        return [why]
    why = _version_check(rec, PAIR_SCHEMA) or _csv_check(rec, cur.grid_dir)
    if why:
        return [why]
    want, have = pair_identity(cur, choice), rec.get("identity") or {}
    reasons = []
    if want["predecessor"] != have.get("predecessor"):
        reasons.append("the predecessor is now {0}, it was {1}".format(
            (want["predecessor"] or {}).get("run_dir_name"),
            (have.get("predecessor") or {}).get("run_dir_name")))
    for side in ("cur", "prev"):
        recorded = (have.get("census_summary_sha256") or {}).get(side)
        if want["census_summary_sha256"][side] != recorded:
            reasons.append("the {0} census changed".format(
                "run's own" if side == "cur" else "predecessor's"))
    if (want["state"], want["reason"]) != (have.get("state"), have.get("reason")) \
            and not reasons:
        reasons.append("the pair's state is now {0}".format(want["state"]))
    return reasons


def write_pair(cur, choice):
    """Run ``cur`` against its predecessor, into ``cur``'s grid folder: the
    transitions CSV (one row per element key on either side), then the
    summary naming its sha256 and both census files' (written last). No
    predecessor or a refused pair: a summary saying so and no CSV, never
    zeros. Read from the census FILES, through ``classify`` and
    ``host_id_overlap`` -- the functions the series uses."""
    state, prev, reason = choice
    identity = pair_identity(cur, choice)
    cur.grid_dir.mkdir(parents=True, exist_ok=True)
    csv_path, summary_path = cur.pair_path("csv"), cur.pair_path("summary")
    _unlink(summary_path)
    summary = {
        "schema": PAIR_SCHEMA, "tool_version": TOOL_VERSION,
        "run_id": cur.run_id, "as_of_date": cur.as_of_date, "run_dir": str(cur.run_dir),
        "run_dir_name": cur.name, "model_folder": str(cur.run_dir.parent),
        "predecessor": None if prev is None else {
            "run_id": prev.run_id, "as_of_date": prev.as_of_date,
            "run_dir": str(prev.run_dir), "run_dir_name": prev.name},
        "counts": None, "common_measured_views": None, "host_id_jaccard_common_views": None,
        "census": {}, "csv": None, "csv_sha256": None,
    }
    rows = None
    if state == "value":
        try:
            p_run, p_rec = load_run_census(prev)
            c_run, c_rec = load_run_census(cur)
        except CensusRefusal as ex:
            state, reason = "refused", str(ex)
        else:
            for side, files, rec in (("cur", cur, c_rec), ("prev", prev, p_rec)):
                summary["census"][side] = {
                    "summary": files.census_summary,
                    "summary_sha256": identity["census_summary_sha256"][side],
                    "csv": rec.get("csv"), "csv_sha256": rec.get("csv_sha256")}
            classes = classify(p_run, c_run)
            _e, _l, summary["counts"] = transitions(p_run, c_run)
            summary["common_measured_views"], summary["host_id_jaccard_common_views"] = (
                host_id_overlap(p_run, c_run))
            rows = [{"run_id": cur.run_id, "as_of_date": cur.as_of_date,
                     "prev_run_id": prev.run_id, "prev_as_of_date": prev.as_of_date,
                     "element_key": key, "source": r["source"], "category": r["category"],
                     "class": r["word"], "in_prev": r["in_prev"], "in_cur": r["in_cur"],
                     "deciding_view_ids": ";".join(r["deciding_views"])}
                    for key, r in sorted(classes.items())]
    # The identity is what the outputs were computed FROM (the choice); the
    # state is how it ended (a census refusal found while reading refuses it).
    summary.update(state=state, reason=reason, identity=identity)
    if rows is None:
        _unlink(csv_path)
    else:
        summary["csv"] = csv_path.name
        summary["csv_sha256"] = _write_csv(csv_path, TRANSITION_COLUMNS, rows)
    _write_json(summary_path, summary)
    remove_undated(cur, "pair")
    return summary


# --- the incremental driver -------------------------------------------------------------

def find_runs(root):
    """Every run folder under ``root`` (a model folder, or an export root),
    found by the runs index's own walk (tools/stage_a_runs_index.discover)."""
    from tools import stage_a_runs_index as idx
    found, scan = idx.discover(Path(root), 4)
    return [Path(run_dir) for run_dir, _meta in found], scan


def drive(root, progress=None):
    """Compute only the per-run censuses and pairs under ``root`` that are
    missing or stale; returns one action record per census and per pair:
    ``{"step", "run", "action" (computed | fresh), "state", "why"}``.
    Censuses come first: a pair's staleness is a function of both census
    summaries."""
    say = progress or (lambda line: None)
    run_dirs, scan = find_runs(root)
    for s in scan["skipped"]:
        say("skipped {0}: {1}".format(s["path_rel"], s["reason"]))
    all_files = [RunFiles(r) for r in run_dirs]
    actions = []
    all_files.sort(key=lambda x: (str(x.run_dir.parent), x.as_of_date or "", x.name))
    for n, f in enumerate(all_files, 1):
        started = time.time()
        adopted = adopt_undated(f)
        if adopted:
            say("        renamed {0} -> {1} in {2}".format(adopted, f.census_summary, f.name))
        inputs = census_inputs(f)
        why = census_staleness(f, inputs)
        if why:
            rec = write_run_census(f, inputs)
            action = "computed"
        else:
            rec = _load_json(f.census_path("summary"))
            action = "fresh"
        actions.append({"step": "census", "run": f.name, "action": action,
                        "state": rec.get("state"), "why": "; ".join(why)})
        say("[{0}/{1}] census {2:<8} {3:.0f}s {4} ({5}){6}".format(
            n, len(all_files), action, time.time() - started, f.name, rec.get("state"),
            ": " + "; ".join(why) if why else ""))
    choices = predecessors(all_files)
    for f in all_files:
        choice = choices[str(f.run_dir)]
        why = pair_staleness(f, choice)
        if why:
            rec = write_pair(f, choice)
            action = "computed"
        else:
            rec = _load_json(f.pair_path("summary"))
            action = "fresh"
        prev = rec.get("predecessor") or {}
        actions.append({"step": "pair", "run": f.name, "action": action,
                        "state": rec.get("state"), "predecessor": prev.get("run_dir_name"),
                        "why": "; ".join(why)})
        say("pair   {0:<8} {1} <- {2} ({3}{4}){5}".format(
            action, f.name, prev.get("run_dir_name") or "-", rec.get("state"),
            ": " + rec["reason"] if rec.get("reason") else "",
            ": " + "; ".join(why) if why else ""))
    return actions


def _per_run(run_dirs):
    """Each run's own census, into its grid folder, computed whether or not
    it is stale (the run was named). 0 every run measured in full, 1 a view
    unmeasured, 2 a run refused."""
    worst = 0
    for n, run_dir in enumerate(run_dirs, 1):
        started = time.time()
        files = RunFiles(run_dir)
        rec = write_run_census(files)
        fig = rec["figures"]
        if rec["state"] != "value":
            print("[{0}/{1}] {2:.0f}s REFUSED {3}: {4}".format(
                n, len(run_dirs), time.time() - started, files.run_dir, rec["refused"]))
            worst = 2
        else:
            print("[{0}/{1}] {2:.0f}s {3} {4}: {5} measured / {6} requested views, {7} "
                  "distinct elements".format(n, len(run_dirs), time.time() - started,
                                             rec["as_of_date"], rec["run_id"],
                                             fig["views_measured"], fig["views_requested"],
                                             fig["elements_distinct"]))
            if fig["views_unmeasured"]:
                worst = max(worst, 1)
        print("wrote {0}".format(files.census_path("summary")))
    return worst


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("runs", nargs="*", metavar="run",
                    help="a run directory or its capture folder (color_id/ or color_id_buffer/)")
    ap.add_argument("--out", default=None,
                    help="the multi-run convenience: every run's elements, the runs CSV "
                         "and the series CSV in this one folder")
    ap.add_argument("--root", default=None,
                    help="a model folder (or an export root): compute only the per-run "
                         "censuses and pairs that are missing or stale")
    args = ap.parse_args(argv)
    if args.root:
        if args.runs or args.out:
            ap.error("--root takes no run arguments and no --out")
        if not Path(args.root).is_dir():
            ap.error("not a directory: {0}".format(args.root))
        actions = drive(args.root, progress=lambda line: print(line, flush=True))
        if not actions:
            print("no run folder under {0}".format(args.root))
            return 2
        done = [a for a in actions if a["action"] == "computed"]
        print("{0} census(es) and {1} pair(s) computed; {2} fresh".format(
            sum(1 for a in done if a["step"] == "census"),
            sum(1 for a in done if a["step"] == "pair"), len(actions) - len(done)))
        refused = [a for a in actions if a["state"] == "refused"]
        for a in refused:
            print("REFUSED {0} {1}".format(a["step"], a["run"]))
        return 1 if refused else 0
    if not args.runs:
        ap.error("give run folders, or --root")
    missing = [run for run in args.runs if not Path(run).is_dir()]
    if missing:
        ap.error("not a directory: {0}".format(", ".join(missing)))
    args.runs = [str(Path(run).resolve()) for run in args.runs]
    if not args.out:
        return _per_run(args.runs)
    layouts = [rollup.run_layout(run) for run in args.runs]
    out = Path(args.out)
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
