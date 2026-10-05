#!/usr/bin/env python3
"""Kinds and classes over the analysis grid's element x cell accounting.

Read-only over ``stage_a_grid.py``'s outputs: it never re-grids and never
writes into a capture folder.

A KIND is a distinct ``(layer, source, category, element_class)`` tuple of a
grid ``ec_keys`` entry -- what the capture recorded about an element, nothing
inferred. Kinds, not classes, are what is aggregated; a CLASS is a lookup
applied afterwards through a versioned map (default
``tools/maps/stage_a_class_map.v1.json``), so changing the map never touches
the primary record.

    python tools/stage_a_kinds.py inventory <run> [<run> ...] [--out DIR]
    python tools/stage_a_kinds.py derive <run> [<run> ...]

``inventory``: every kind across the runs' grid outputs, with its view count,
keys, px and ink px, and the class the map gives it (``unmapped`` where no
rule matches -- listed, never guessed). Writes ``kinds_inventory.csv`` and
then ``kinds_inventory.json`` naming the CSV's and the class map's hashes.

``derive``: per gridded view, ``<view>.kinds.npz`` -- for each annotation
class, legacy bucket and model class, dense per-cell counts -- and then
``<view>.kinds.json`` naming the class map, the grid record and the arrays'
hashes (written last, CLAUDE.md defect class 4). It is a SEPARATE file: the
grid npz stays independent of the map version. The record also carries the
geometry path's channel shape (A4), labelled legacy.

A ``<run>`` is a run directory, its color_id_buffer/, or an analysis_grid/
directory.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import stage_a_grid as grid  # noqa: E402

SCHEMA_KINDS = "vop.stage_a.kinds.v1"
SCHEMA_INVENTORY = "vop.stage_a.kinds_inventory.v1"
TOOL_VERSION = "1.0.0"

DEFAULT_CLASS_MAP = Path(__file__).resolve().parent / "maps" / "stage_a_class_map.v1.json"
INVENTORY_CSV = "kinds_inventory.csv"
INVENTORY_JSON = "kinds_inventory.json"

# Which families classify which layer's keys.
FAMILIES = {"annotation_class": "annotation", "legacy_bucket": "annotation",
            "model_class": "model"}
# Per-class fields. occupancy_px is item 3's annotation occupancy weight per
# key: its ink, its assigned black, and its whole area if it is a filled
# region -- so summed over classes it equals the grid's
# anno_element_ink + anno_filled_region + anno_black_assigned in every cell.
LAYER_FIELDS = {"annotation": ("px", "ink_px", "black_px", "occupancy_px",
                               "model_ink_under_px"),
                "model": ("px", "ink_px", "model_ink_under_px")}
KIND_FIELDS = ("layer", "source", "category", "element_class")
RULE_CONDITIONS = ("element_class", "category", "category_upper_contains")


class KindsRefusal(Exception):
    """An input cannot be used; the message says why."""


def sha256_file(path):
    return grid.sha256_file(path)


def _load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# --- the class map ----------------------------------------------------------------

def load_class_map(path=None):
    """``(map, sha256, path)``. A map whose families, defaults or rules do not
    hold together is refused, not partly applied."""
    path = Path(path) if path else DEFAULT_CLASS_MAP
    try:
        cmap = _load_json(path)
    except (OSError, ValueError) as ex:
        raise KindsRefusal("the class map {0} cannot be read: {1}: {2}".format(
            path, type(ex).__name__, ex))
    for family in FAMILIES:
        spec = cmap.get(family)
        if not isinstance(spec, dict):
            raise KindsRefusal("the class map has no {0!r} family".format(family))
        classes = spec.get("classes")
        if not classes or len(set(classes)) != len(classes):
            raise KindsRefusal("{0}: classes missing or repeated".format(family))
        if spec.get("default") not in classes:
            raise KindsRefusal("{0}: default {1!r} is not one of its classes".format(
                family, spec.get("default")))
        for n, rule in enumerate(spec.get("rules") or []):
            when = rule.get("when")
            if not isinstance(when, dict) or not when:
                raise KindsRefusal("{0} rule {1}: no conditions".format(family, n))
            unknown = sorted(set(when) - set(RULE_CONDITIONS))
            if unknown:
                raise KindsRefusal("{0} rule {1}: unknown condition(s) {2}".format(
                    family, n, unknown))
            if rule.get("class") not in classes:
                raise KindsRefusal("{0} rule {1}: class {2!r} is not one of its "
                                   "classes".format(family, n, rule.get("class")))
    return cmap, sha256_file(path), path


def _matches(when, key):
    for cond, accepted in when.items():
        if cond == "category_upper_contains":
            if str(accepted) not in str(key.get("category") or "").upper():
                return False
        elif key.get(cond) not in accepted:
            return False
    return True


def classify(key, cmap, family):
    """``(class, rule index or None)``: the first matching rule's class, or
    the family's default with None."""
    spec = cmap[family]
    for n, rule in enumerate(spec.get("rules") or []):
        if _matches(rule["when"], key):
            return rule["class"], n
    return spec["default"], None


def kind_of(key):
    return tuple(key.get(f) for f in KIND_FIELDS)


def families_for(layer):
    return [f for f, lay in FAMILIES.items() if lay == layer]


# --- the grid's outputs -----------------------------------------------------------

def grid_dir_of(run_arg):
    """An analysis_grid directory for a run argument (the grid's own default,
    ``<capture folder>.parent / "analysis_grid"``), or the argument itself
    when it already holds grid records."""
    p = Path(run_arg)
    if p.is_dir() and any(p.glob("*.grid.json")):
        return p
    _sidecars, folder = grid.model_sidecars(p)
    return Path(folder).parent / "analysis_grid"


def grid_records(grid_dir):
    return sorted(Path(grid_dir).glob("*.grid.json"))


def load_grid(json_path):
    """``(record, arrays)`` for a ``value`` grid carrying the element x cell
    accounting, its npz verified against the hash the record names. Anything
    else is refused with the reason."""
    json_path = Path(json_path)
    try:
        rec = _load_json(json_path)
    except (OSError, ValueError) as ex:
        raise KindsRefusal("the grid record cannot be read: {0}: {1}".format(
            type(ex).__name__, ex))
    if not isinstance(rec, dict):
        raise KindsRefusal("the grid record is not an object")
    if rec.get("status") != "value":
        raise KindsRefusal("the grid refused this view: {0}".format(rec.get("reason")))
    if "ec_keys" not in rec:
        raise KindsRefusal("the grid record carries no element x cell accounting "
                           "(schema {0!r}, made before A1); re-run stage_a_grid".format(
                               rec.get("schema")))
    npz = json_path.parent / str(rec.get("npz"))
    if not npz.exists():
        raise KindsRefusal("the grid npz {0} named by the record is missing".format(npz.name))
    if sha256_file(npz) != rec.get("npz_sha256"):
        raise KindsRefusal("the grid npz does not match the hash its record names")
    with np.load(str(npz)) as data:
        arrays = dict((k, data[k]) for k in data.files)
    return rec, arrays


def view_stem(json_path):
    return Path(json_path).name[:-len(".grid.json")]


def _key_sums(arrays, n_keys, field):
    if not len(arrays["ec_key"]):
        return np.zeros(n_keys, dtype=np.int64)
    return np.bincount(arrays["ec_key"], weights=arrays[field],
                       minlength=n_keys).astype(np.int64)


# --- inventory ----------------------------------------------------------------------

INVENTORY_COLUMNS = ["layer", "source", "category", "element_class", "views", "keys",
                     "keys_with_px", "px", "ink_px", "black_px",
                     "annotation_class", "annotation_class_basis",
                     "legacy_bucket", "legacy_bucket_basis",
                     "model_class", "model_class_basis"]


def _basis(cmap, family, rule_index):
    if rule_index is None:
        return "default"
    return cmap[family]["rules"][rule_index].get("basis", "unrecorded")


def inventory(grid_dirs, cmap):
    """``(rows, views)``: one row per kind across every usable grid record,
    and one entry per grid record found, with whether it was used."""
    kinds, views = {}, []
    for gdir in grid_dirs:
        for path in grid_records(gdir):
            entry = {"grid_dir": str(gdir), "view_stem": view_stem(path)}
            try:
                rec, arr = load_grid(path)
            except KindsRefusal as ex:
                views.append(dict(entry, used=False, reason=str(ex)))
                continue
            entry.update(used=True, run_id=rec.get("run_id"), view_id=rec.get("view_id"))
            views.append(entry)
            keys = rec["ec_keys"]
            sums = dict((f, _key_sums(arr, len(keys), "ec_" + f))
                        for f in ("px", "ink_px", "black_px"))
            for n, key in enumerate(keys):
                kind = kind_of(key)
                row = kinds.get(kind)
                if row is None:
                    row = dict(zip(KIND_FIELDS, kind), views=set(), keys=0, keys_with_px=0,
                               px=0, ink_px=0, black_px=0)
                    for family in FAMILIES:
                        if FAMILIES[family] == key["layer"]:
                            cls, rule = classify(key, cmap, family)
                            row[family], row[family + "_basis"] = cls, _basis(cmap, family, rule)
                        else:
                            row[family], row[family + "_basis"] = None, None
                    kinds[kind] = row
                row["views"].add((str(gdir), view_stem(path)))
                row["keys"] += 1
                row["keys_with_px"] += int(sums["px"][n] > 0)
                for f in ("px", "ink_px", "black_px"):
                    row[f] += int(sums[f][n])
    rows = []
    for row in kinds.values():
        rows.append(dict(row, views=len(row["views"])))
    rows.sort(key=lambda r: (r["layer"] or "", -r["px"], str(r["category"]),
                             str(r["element_class"]), str(r["source"])))
    return rows, views


def write_inventory(rows, views, cmap_info, out_dir):
    """Write the CSV, then the record naming its hash; return the record."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / INVENTORY_CSV
    with open(csv_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=INVENTORY_COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow(dict((k, "" if r.get(k) is None else r.get(k))
                                 for k in INVENTORY_COLUMNS))
    cmap, cmap_sha, cmap_path = cmap_info
    used = [v for v in views if v["used"]]
    unmapped = [r for r in rows if "unmapped" in (r.get("annotation_class"),)]
    record = {
        "schema": SCHEMA_INVENTORY, "tool_version": TOOL_VERSION,
        "class_map": {"path": str(cmap_path), "sha256": cmap_sha,
                      "version": cmap.get("version")},
        "grid_records": {"denominator": len(views), "used": len(used),
                         "not_used": [v for v in views if not v["used"]]},
        "kinds": len(rows),
        "kinds_by_layer": dict((lay, sum(1 for r in rows if r["layer"] == lay))
                               for lay in ("model", "annotation")),
        "unmapped_kinds": {
            "denominator": sum(1 for r in rows if r["layer"] == "annotation"),
            "denominator_rule": "annotation kinds",
            "count": len(unmapped),
            "kinds": [dict((k, r[k]) for k in KIND_FIELDS + ("views", "px", "ink_px"))
                      for r in unmapped]},
        "keys_with_null_category": dict(
            (lay, {"keys": sum(r["keys"] for r in rows
                               if r["layer"] == lay and r["category"] is None),
                   "denominator": sum(r["keys"] for r in rows if r["layer"] == lay)})
            for lay in ("model", "annotation")),
        "csv": csv_path.name, "csv_sha256": sha256_file(csv_path),
    }
    with open(out_dir / INVENTORY_JSON, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True, default=str)
    return record


# --- derived class arrays -------------------------------------------------------------

def _row_fields(rec, arr):
    """Per ec_ row, every per-class field (LAYER_FIELDS), int64."""
    keys = rec["ec_keys"]
    filled = np.array([k.get("element_class") in grid.FILLED_REGION_CLASSES
                       for k in keys] or [False])
    px = arr["ec_px"].astype(np.int64)
    ink = arr["ec_ink_px"].astype(np.int64)
    black = arr["ec_black_px"].astype(np.int64)
    is_filled = filled[arr["ec_key"]] if len(arr["ec_key"]) else np.zeros(0, dtype=bool)
    return {"px": px, "ink_px": ink, "black_px": black,
            "occupancy_px": ink + black + np.where(is_filled, px, 0),
            "model_ink_under_px": arr["ec_model_ink_under_px"].astype(np.int64)}


def layer_dense(arrays, layer):
    """The grid's dense arrays each per-class field must sum to, per cell."""
    if layer == "model":
        return {"px": arrays["model_host"] + arrays["model_dwg"] + arrays["model_link"],
                "ink_px": (arrays["model_host_ink"] + arrays["model_dwg_ink"]
                           + arrays["model_link_ink"]),
                "model_ink_under_px": arrays.get("model_ink_under_anno",
                                                 np.zeros_like(arrays["model_total"]))}
    return {"px": arrays["anno_element"], "ink_px": arrays["anno_element_ink"],
            "black_px": arrays["anno_black_assigned"],
            "occupancy_px": (arrays["anno_element_ink"] + arrays["anno_filled_region"]
                             + arrays["anno_black_assigned"]),
            "model_ink_under_px": arrays["model_ink_under_anno"]}


def class_arrays(rec, arr, cmap):
    """``(arrays, family_records)``: for each family applicable to a layer the
    grid holds, dense per-cell counts per class and field, named
    ``<family>__<class>__<field>``. A class with no row in the view gets no
    array (its record says so). Every field, summed over a family's classes,
    is checked against the grid's dense arrays in every cell; a difference
    is a refusal with the numbers."""
    g = rec["grid"]
    shape = (g["cells_h"], g["cells_w"])
    keys = rec["ec_keys"]
    fields = _row_fields(rec, arr)
    jj = arr["ec_j"].astype(np.int64) - g["j_range"][0]
    ii = arr["ec_i"].astype(np.int64) - g["i_range"][0]
    out, records = {}, {}
    gridded_layers = ["model"] + (["annotation"] if "anno_element" in arr else [])
    for family, layer in FAMILIES.items():
        classes = cmap[family]["classes"]
        if layer not in gridded_layers:
            records[family] = {"state": "not_applicable",
                               "reason": "the view has no registered annotation"}
            continue
        key_class = np.array([classes.index(classify(k, cmap, family)[0])
                              if k["layer"] == layer else -1 for k in keys] or [-1])
        row_class = key_class[arr["ec_key"]] if len(arr["ec_key"]) else np.zeros(0, int)
        dense = layer_dense(arr, layer)
        running = dict((f, np.zeros(shape, dtype=np.int64)) for f in LAYER_FIELDS[layer])
        totals, present = {}, []
        for c, name in enumerate(classes):
            rows = row_class == c
            totals[name] = dict((f, int(fields[f][rows].sum())) for f in LAYER_FIELDS[layer])
            totals[name]["keys"] = int(sum(1 for kc in key_class if kc == c))
            if not rows.any():
                continue
            present.append(name)
            for f in LAYER_FIELDS[layer]:
                a = np.zeros(shape, dtype=np.int64)
                np.add.at(a, (jj[rows], ii[rows]), fields[f][rows])
                running[f] += a
                out["{0}__{1}__{2}".format(family, name, f)] = a.astype(np.int32)
        broken = []
        for f in LAYER_FIELDS[layer]:
            diff = int((running[f] != dense[f]).sum())
            if diff:
                broken.append("{0}: classes {1} vs grid {2}, {3} cells differ".format(
                    f, int(running[f].sum()), int(dense[f].sum()), diff))
        if broken:
            raise KindsRefusal("{0} does not reproduce the grid's {1} channels: {2}".format(
                family, layer, "; ".join(broken)))
        records[family] = {"state": "value", "layer": layer, "classes": list(classes),
                           "fields": list(LAYER_FIELDS[layer]),
                           "classes_with_rows": present, "totals": totals}
    return out, records


# --- derivations over the stored counts (A3) ---------------------------------------
#
# Presence and dominant type are DERIVED here, by the reader, from the counts
# the grid and the derivation store (D-A3). Nothing below is stored in place
# of a count.

def family_counts(arrays, family, classes, fields, shape):
    """``{class: {field: array}}`` for one family from a kinds npz's arrays; a
    class with no array (no pixel in the view) is zeros, as the npz states."""
    out = {}
    for name in classes:
        out[name] = {}
        for f in fields:
            a = arrays.get("{0}__{1}__{2}".format(family, name, f))
            out[name][f] = (np.zeros(shape, dtype=np.int64) if a is None
                            else np.asarray(a, dtype=np.int64))
    return out


def presence(counts, field, min_px=1):
    """PURE. ``{class: bool array}``: the cells where the class holds at least
    ``min_px`` of ``field``."""
    if min_px < 1:
        raise ValueError("min_px must be at least 1 (0 would call every cell present)")
    return dict((name, by_field[field] >= min_px) for name, by_field in counts.items())


def dominant(counts, field, order):
    """PURE. Per cell, the class with the most ``field``.

    Returns ``{"index": int array (position in ``order``; -1 where every class
    is 0), "classes": order, "tie_cells": bool array, "ties": int}``. A tie --
    two or more classes sharing the cell's nonzero maximum -- goes to the
    class EARLIEST in ``order``, a declared fixed order, never to whichever
    was read last; how many cells were decided that way is ``ties``."""
    order = list(order)
    missing = [c for c in order if c not in counts]
    if missing:
        raise ValueError("dominant(): classes {0} have no counts".format(missing))
    stack = np.stack([np.asarray(counts[c][field]) for c in order])
    best = stack.max(axis=0)
    index = np.argmax(stack, axis=0).astype(np.int16)    # first maximum = earliest
    empty = best <= 0
    index[empty] = -1
    tie_cells = ((stack == best[None]).sum(axis=0) > 1) & ~empty
    return {"index": index, "classes": order, "tie_cells": tie_cells,
            "ties": int(tie_cells.sum())}


def legacy_anno_partition(counts, anno_present, order, inside=None):
    """The geometry path's AnnoFinalCells_<bucket> on this grid: each cell the
    annotation occupancy rule marks present goes to ONE bucket, its dominant
    legacy bucket by ``occupancy_px`` (dominant()).

    The geometry path's partition took the cell's FINAL anno_key -- the last
    writer -- so it depended on draw order; this one is deterministic. Its
    invariant (anno_types_sum_to_anno_present, metrics_manifest.v1.json) is
    checked: a present cell with no bucket refuses."""
    dom = dominant(counts, "occupancy_px", order)
    lost = anno_present & (dom["index"] < 0)
    if lost.any():
        raise KindsRefusal("{0} annotation-present cells hold no legacy bucket's "
                           "occupancy_px".format(int(lost.sum())))

    def _over(mask):
        sel = anno_present & mask
        cells = dict(("AnnoFinalCells_" + name,
                      int((sel & (dom["index"] == n)).sum())) for n, name in enumerate(order))
        cells["AnnoPresentFinal"] = int(sel.sum())
        cells["ties"] = int((sel & dom["tie_cells"]).sum())
        cells["anno_types_sum_to_anno_present"] = (
            sum(cells["AnnoFinalCells_" + n] for n in order) == cells["AnnoPresentFinal"])
        return cells
    out = {"all_cells": _over(np.ones(anno_present.shape, dtype=bool))}
    if inside is not None:
        out["inside_crop_a"] = _over(inside)
    return out


def class_cells(pres, inside):
    """``{class: {"all_cells": n, "inside_crop_a": n}}`` from presence()."""
    return dict((name, {"all_cells": int(m.sum()), "inside_crop_a": int((m & inside).sum())})
                for name, m in pres.items())


def derivations(families, arrays, grid_arrays, cmap, min_px=1):
    """The derived products a kinds record carries, from the arrays it is
    about to write and the grid's: cells_with_ink per class (presence of
    ink_px at ``min_px``), the legacy annotation partition, and the legacy
    ModelClassCells_* multihot (presence of model-class ink)."""
    shape = grid_arrays["model_total"].shape
    inside = grid_arrays["inside_crop_a"].astype(bool)
    out = {"presence_min_px": min_px, "cells_with_ink": {},
           "cells_with_ink_rule": "presence(<family>, ink_px, min_px): cells where the "
                                  "class holds at least min_px ink pixels",
           "tie_rule": "dominant(): ties go to the class earliest in the class map's "
                       "declared order"}
    for family, rec in families.items():
        if rec.get("state") != "value":
            continue
        counts = family_counts(arrays, family, rec["classes"], rec["fields"], shape)
        out["cells_with_ink"][family] = class_cells(presence(counts, "ink_px", min_px),
                                                    inside)
        if family == "legacy_bucket":
            occ = grid_arrays["occupancy"]
            present = np.isin(occ, (grid.OCCUPANCY_CODES["anno_only"],
                                    grid.OCCUPANCY_CODES["overlap"]))
            part = legacy_anno_partition(counts, present, rec["classes"], inside)
            part["rule"] = ("each annotation-present cell (the grid's occupancy rule) to "
                            "its dominant legacy bucket by occupancy_px")
            part["note"] = ("The geometry path's AnnoFinalCells_* took each cell's final "
                            "anno_key (the last writer), so it depended on draw order; "
                            "dominant-by-px is deterministic. IMPORT_VIEW_OWNED is not a "
                            "v1 bucket (A5).")
            out["legacy_anno_partition"] = part
        if family == "model_class":
            out["model_class_cells_multihot"] = dict(
                ("ModelClassCells_" + name, n) for name, n in
                out["cells_with_ink"][family].items())
    return out


# --- the geometry path's channel shape (A4), labelled legacy ----------------------

LEGACY_PARTITION_8 = ("Cells_Empty", "Cells_ModelOnly", "Cells_AnnoOnly", "Cells_ExtOnly",
                      "Cells_ModelAnno", "Cells_ModelExt", "Cells_AnnoExt", "Cells_All3")


def legacy_channel_flags(arrays):
    """PURE. Per-cell M, A, E and the host / DWG / RVT flags, from the grid's
    dense arrays: M is model ink (host + DWG + link) at the occupancy
    threshold, A the annotation occupancy rule (the grid's own ``occupancy``
    codes), E DWG + link ink at the same threshold. E is therefore a subset
    of M, as Ext was of Model in the geometry path."""
    t = grid.OCCUPANCY_MIN_INK_PX
    occ = arrays["occupancy"]
    return {
        "M": (arrays["model_host_ink"] + arrays["model_dwg_ink"]
              + arrays["model_link_ink"]) >= t,
        "A": np.isin(occ, (grid.OCCUPANCY_CODES["anno_only"], grid.OCCUPANCY_CODES["overlap"])),
        "E": (arrays["model_dwg_ink"] + arrays["model_link_ink"]) >= t,
        "host": arrays["model_host_ink"] >= t,
        "dwg": arrays["model_dwg_ink"] >= t,
        "rvt": arrays["model_link_ink"] >= t,
    }


def legacy_partition_8(flags):
    """PURE. final_state_scanner's source_partition_8, in its precedence
    (All3, ModelAnno, ModelExt, AnnoExt, ModelOnly, AnnoOnly, ExtOnly,
    Empty), as a per-cell index into LEGACY_PARTITION_8."""
    m, a, e = flags["M"], flags["A"], flags["E"]
    out = np.zeros(m.shape, dtype=np.uint8)
    order = [("Cells_All3", m & a & e), ("Cells_ModelAnno", m & a),
             ("Cells_ModelExt", m & e), ("Cells_AnnoExt", a & e),
             ("Cells_ModelOnly", m), ("Cells_AnnoOnly", a), ("Cells_ExtOnly", e)]
    done = np.zeros(m.shape, dtype=bool)
    for name, cond in order:
        hit = cond & ~done
        out[hit] = LEGACY_PARTITION_8.index(name)
        done |= hit
    return out


def legacy_channel_shape(arrays):
    """The geometry path's channel shape on this grid, over all cells and
    inside crop A: source_partition_8 and the ExtFinalCells_* flags. Its
    invariants (metrics_manifest.v1.json) are evaluated and recorded."""
    flags = legacy_channel_flags(arrays)
    part = legacy_partition_8(flags)
    inside = arrays["inside_crop_a"].astype(bool)

    def _over(mask):
        counts = dict((name, int(((part == n) & mask).sum()))
                      for n, name in enumerate(LEGACY_PARTITION_8))
        counts["TotalCells"] = int(mask.sum())
        e, host, dwg, rvt = flags["E"], flags["host"], flags["dwg"], flags["rvt"]
        counts.update({"ExtFinalCells_Any": int((e & mask).sum()),
                       "ExtFinalCells_Only": int((e & ~host & mask).sum()),
                       "ExtFinalCells_DWG": int((dwg & mask).sum()),
                       "ExtFinalCells_RVT": int((rvt & mask).sum()),
                       "ExtFinalCells_DWG_RVT": int((dwg & rvt & mask).sum())})
        counts["invariants"] = {
            "partition_sums_to_total":
                sum(counts[n] for n in LEGACY_PARTITION_8) == counts["TotalCells"],
            "ext_any_inclusion_exclusion":
                counts["ExtFinalCells_Any"] == counts["ExtFinalCells_DWG"]
                + counts["ExtFinalCells_RVT"] - counts["ExtFinalCells_DWG_RVT"],
            "ext_only_le_ext_any":
                counts["ExtFinalCells_Only"] <= counts["ExtFinalCells_Any"]}
        return counts
    return part, {
        "label": "legacy: the geometry path's source_partition_8 and ExtFinalCells_* "
                 "(vop_interwoven/metrics/final_state_scanner.py), reproduced on this "
                 "grid. Not a new definition.",
        "rule": "M = host+DWG+link ink >= {0} px; A = the grid's annotation occupancy; "
                "E = DWG+link ink >= {0} px. E is within M, as Ext was within Model "
                "(model_presence_mode 'any'), so Cells_AnnoExt and Cells_ExtOnly are "
                "structurally 0.".format(grid.OCCUPANCY_MIN_INK_PX),
        "codes": dict((name, n) for n, name in enumerate(LEGACY_PARTITION_8)),
        "all_cells": _over(np.ones(part.shape, dtype=bool)),
        "inside_crop_a": _over(inside)}


# --- one view --------------------------------------------------------------------------

def derive_view(grid_json, cmap_info, out_dir=None):
    """Write ``<view>.kinds.npz`` and then ``<view>.kinds.json``; return the
    record. A refusal is a record too (with no npz)."""
    grid_json = Path(grid_json)
    out_dir = Path(out_dir) if out_dir else grid_json.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = view_stem(grid_json)
    cmap, cmap_sha, cmap_path = cmap_info
    record = {"schema": SCHEMA_KINDS, "tool_version": TOOL_VERSION,
              "view_stem": stem, "grid_json": str(grid_json),
              "grid_json_sha256": sha256_file(grid_json),
              "class_map": {"path": str(cmap_path), "sha256": cmap_sha,
                            "version": cmap.get("version"),
                            "schema": cmap.get("schema")}}
    json_path = out_dir / (stem + ".kinds.json")
    npz_path = out_dir / (stem + ".kinds.npz")
    try:
        rec, arr = load_grid(grid_json)
        record.update(run_id=rec.get("run_id"), view_id=rec.get("view_id"),
                      grid_npz_sha256=rec.get("npz_sha256"),
                      lattice_anchor=rec["grid"].get("lattice_anchor"))
        out, families = class_arrays(rec, arr, cmap)
        derived = derivations(families, out, arr, cmap)
        part, shape = legacy_channel_shape(arr)
        out["legacy_source_partition_8"] = part
        if npz_path.exists():
            npz_path.unlink()
        np.savez_compressed(npz_path, **out)
        record.update({
            "status": "value", "families": families, "derived": derived,
            "channel_shape_legacy": shape,
            "npz": npz_path.name, "npz_sha256": sha256_file(npz_path),
            "layout": "arrays are (cells_h, cells_w) on the grid's cells (row 0 is "
                      "j_range[0]); <family>__<class>__<field>; a class absent from the "
                      "npz has no pixel in the view"})
    except KindsRefusal as ex:
        if npz_path.exists():
            npz_path.unlink()
        record.update({"status": "refused", "reason": str(ex)})
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True, default=str)
    return record


# --- the command line ------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    inv = sub.add_parser("inventory", help="every kind across the runs' grid outputs")
    inv.add_argument("runs", nargs="+", metavar="run")
    inv.add_argument("--out", default=None,
                     help="output directory (default, one run only: its analysis_grid)")
    inv.add_argument("--class-map", default=None)
    der = sub.add_parser("derive", help="per-view class arrays and the legacy channel shape")
    der.add_argument("runs", nargs="+", metavar="run")
    der.add_argument("--class-map", default=None)
    args = ap.parse_args(argv)
    try:
        cmap_info = load_class_map(args.class_map)
    except KindsRefusal as ex:
        print("REFUSED: {0}".format(ex))
        return 2
    grid_dirs = [grid_dir_of(r) for r in args.runs]
    missing = [str(d) for d in grid_dirs if not grid_records(d)]
    if missing:
        print("no grid records in: {0}".format(", ".join(missing)))
        return 2
    if args.command == "inventory":
        if len(grid_dirs) > 1 and not args.out:
            ap.error("--out is required with more than one run")
        rows, views = inventory(grid_dirs, cmap_info[0])
        out = Path(args.out) if args.out else grid_dirs[0]
        record = write_inventory(rows, views, cmap_info, out)
        print("{0} kinds from {1} of {2} grid records (class map v{3}, sha256 {4})".format(
            record["kinds"], record["grid_records"]["used"],
            record["grid_records"]["denominator"], cmap_info[0].get("version"),
            cmap_info[1][:12]))
        print("{0:<10} {1:<6} {2:<32} {3:<20} {4:>5} {5:>12} {6:>10}  {7}".format(
            "layer", "source", "category", "element_class", "views", "px", "ink_px",
            "class"))
        for r in rows:
            cls = r["model_class"] if r["layer"] == "model" else "{0} / {1}".format(
                r["annotation_class"], r["legacy_bucket"])
            print("{0:<10} {1:<6} {2:<32} {3:<20} {4:>5} {5:>12} {6:>10}  {7}".format(
                r["layer"], str(r["source"]), str(r["category"])[:32],
                str(r["element_class"])[:20], r["views"], r["px"], r["ink_px"], cls))
        for v in record["grid_records"]["not_used"]:
            print("NOT USED {0}: {1}".format(v["view_stem"], v["reason"]))
        print("wrote {0} and {1}".format(out / INVENTORY_CSV, out / INVENTORY_JSON))
        return 1 if record["grid_records"]["not_used"] else 0
    worst = 0
    for gdir in grid_dirs:
        for path in grid_records(gdir):
            rec = derive_view(path, cmap_info)
            if rec["status"] != "value":
                worst = 1
                print("REFUSED {0}: {1}".format(rec["view_stem"], rec["reason"]))
                continue
            print("{0:<40} {1}".format(rec["view_stem"][:40], ", ".join(
                "{0} {1}".format(f, len(r.get("classes_with_rows") or []))
                for f, r in sorted(rec["families"].items()))))
    return worst


if __name__ == "__main__":
    sys.exit(main())
