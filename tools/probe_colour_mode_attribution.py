"""What each colour mode loses in attribution, against the colour-ID baseline.

    python tools/probe_colour_mode_attribution.py <probe_out_dir> <baseline_run_dir>

Offline half of the colour-mode probe (tests/dynamo/probe_colour_mode.py,
docs/notes/export_floor_20261118T1002.md, Part B). The probe exported, per
view, the arms below beside the existing colour-ID capture (the baseline):

  as_is     no colour overrides; annotation hidden. Ink carries no label.
  category  every model category's Object Styles line colour set once for
            the document. A pixel's colour names a CATEGORY.
  type      one ParameterFilterElement per family type in the view, coloured.
            A pixel's colour names a TYPE.

THE CENSUS BOXES are the baseline model sidecar's ``near_face_w_map.host``
entries: each element's ``uv_rect``, the bounding box projected to view UV by
the capture itself (the same entries tools/stage_a_element_census.py keys
elements by). They are mapped to pixels through the baseline frame's crop and
image size (``stage_a_grid.nominal_mapping``). Linked elements are left out:
the baseline paints links per CATEGORY, so it has no per-element truth for
them, and their pixels are counted as ``baseline_link_px`` instead.

ATTRIBUTION, per ink pixel of an arm (ink = any pixel that is not white):
the candidate elements are the boxes that contain the pixel, narrowed (in
the labelled arms) to elements whose label matches the pixel's colour.

  unique      exactly one candidate: the pixel is that element's
  ambiguous   two or more candidates (same-colour boxes overlap)
  unassigned  none: no box, or a colour that is no label (black fill
              patterns, anti-aliased fringe, an override that won)

GROUND TRUTH is the baseline's decoded element id at the same pixel
(``stage_a_grid.model_pixels``, ticks removed). It needs the two images on
one lattice, so an arm whose image size differs from the baseline's is
REFUSED for that view rather than resampled. The probe exports at the
baseline's pixel size, and an active authored crop is never written, so the
sizes agree for a cropped view.

PER VIEW AND ARM: the shares of ink pixels unique, ambiguous and unassigned;
the share of unique pixels whose element is the baseline's; for the labelled
arms, the share of ink pixels whose colour is not the label of the baseline
element there (``label_mismatch``: another view-level setting took
precedence, or a fill/fringe); the same three shares per category group
(walls, casework, furniture, doors, other; the group is the BASELINE
element's); and the per-element cell-count error against the baseline
(median and p90 of |arm - baseline| cells at ``--cell-px``).

LOAD, DOC BURDEN, FIDELITY. None of the three is defined in this
repository: they are the analysis layer's. What is computed is the input
they are built from. ``ink_cells`` (cells holding any ink, the grid's model
occupancy rule, ``stage_a_grid.OCCUPANCY_MIN_INK_PX``) and the per-element
cells, in each arm and in the baseline. Whether the three metrics move is
for the analysis layer to compute from those. The output says so rather
than inventing them.

Exit 0 written; 2 the probe record or the baseline run cannot be read.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ARMS = ("as_is", "category", "type")
LABELLED_ARMS = ("category", "type")
CATEGORY_GROUPS = (("walls", ("Walls",)), ("casework", ("Casework",)),
                   ("furniture", ("Furniture", "Furniture Systems")),
                   ("doors", ("Doors",)))
WHITE = 0xFFFFFF
OFF_PALETTE = -1
AMBIGUOUS = -1
DEFAULT_CELL_PX = 16


# ======================================================================
# PURE
# ======================================================================

def category_group(name):
    for group, names in CATEGORY_GROUPS:
        if name in names:
            return group
    return "other"


def pack(rgb):
    """``(H, W, 3)`` uint8 -> ``(H, W)`` int64 packed 0xRRGGBB."""
    rgb = np.asarray(rgb)
    return ((rgb[..., 0].astype(np.int64) << 16) | (rgb[..., 1].astype(np.int64) << 8)
            | rgb[..., 2].astype(np.int64))


def label_image(packed, label_colour):
    """PURE. Per pixel, the label whose colour it is, else OFF_PALETTE.
    ``label_colour``: {label (int): (r, g, b)}. Two labels sharing a colour
    is refused: the image could not say which."""
    by_colour = {}
    for label, rgb in label_colour.items():
        key = (int(rgb[0]) << 16) | (int(rgb[1]) << 8) | int(rgb[2])
        if key in by_colour and by_colour[key] != int(label):
            raise ValueError("labels {0} and {1} share colour {2}".format(
                by_colour[key], label, tuple(rgb)))
        by_colour[key] = int(label)
    out = np.full(packed.shape, OFF_PALETTE, dtype=np.int64)
    if by_colour:
        colours = np.array(sorted(by_colour), dtype=np.int64)
        labels = np.array([by_colour[c] for c in colours], dtype=np.int64)
        idx = np.searchsorted(colours, packed)
        idx = np.clip(idx, 0, len(colours) - 1)
        hit = colours[idx] == packed
        out[hit] = labels[idx[hit]]
    return out


def uv_rect_to_px(rect, mapping, shape):
    """PURE. ``[umin, vmin, umax, vmax]`` -> ``(x0, y0, x1, y1)`` half-open,
    clipped to the image, or None when it falls outside. ``mapping`` is
    ``stage_a_grid.nominal_mapping``'s (pixel = a * uv + b; pixel i spans
    [i, i+1])."""
    umin, vmin, umax, vmax = (float(v) for v in rect)
    xs = sorted((mapping["a_u"] * umin + mapping["b_u"], mapping["a_u"] * umax + mapping["b_u"]))
    ys = sorted((mapping["a_v"] * vmin + mapping["b_v"], mapping["a_v"] * vmax + mapping["b_v"]))
    h, w = shape
    x0, x1 = max(0, int(np.floor(xs[0]))), min(w, int(np.ceil(xs[1])))
    y0, y1 = max(0, int(np.floor(ys[0]))), min(h, int(np.ceil(ys[1])))
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def attribute(ink, boxes, labels=None):
    """PURE. ``(owner, n_candidates)``.

    ``ink``: bool (H, W). ``boxes``: [{"id", "rect": (x0, y0, x1, y1),
    "label"}]. ``labels``: int (H, W) per-pixel label (OFF_PALETTE where the
    colour is none), or None for an unlabelled arm. With ``labels``, a box
    matches only pixels of its own label, and a box whose label is None
    matches none (an element the probe could not label cannot be told by
    colour). ``owner``: the element id where exactly one box matches, 0 where
    none, AMBIGUOUS (-1) where two or more."""
    count = np.zeros(ink.shape, dtype=np.int32)
    owner = np.zeros(ink.shape, dtype=np.int64)
    for box in boxes:
        rect = box.get("rect")
        if rect is None:
            continue
        x0, y0, x1, y1 = rect
        hit = ink[y0:y1, x0:x1]
        if labels is not None:
            if box.get("label") is None:
                continue
            hit = hit & (labels[y0:y1, x0:x1] == int(box["label"]))
        count[y0:y1, x0:x1] += hit
        sub = owner[y0:y1, x0:x1]
        sub[hit] = int(box["id"])
    owner[count == 0] = 0
    owner[count > 1] = AMBIGUOUS
    owner[~ink] = 0
    return owner, count


def _shares(ink, owner, where=None):
    sel = ink if where is None else ink & where
    n = int(sel.sum())
    if n == 0:
        return {"ink_px": 0, "unique": None, "ambiguous": None, "unassigned": None}
    return {"ink_px": n,
            "unique": round(float(((owner > 0) & sel).sum()) / n, 6),
            "ambiguous": round(float(((owner == AMBIGUOUS) & sel).sum()) / n, 6),
            "unassigned": round(float(((owner == 0) & sel).sum()) / n, 6)}


def element_cells(ids, cell_px, keep=None):
    """PURE. ``{element_id: cells}``: for each id > 0 in ``ids`` (restricted
    to ``keep`` where given), the number of ``cell_px`` blocks holding at
    least one of its pixels."""
    h, w = ids.shape
    rows, cols = np.nonzero((ids > 0) if keep is None else ((ids > 0) & keep))
    if rows.size == 0:
        return {}
    cell = (rows // cell_px) * ((w + cell_px - 1) // cell_px) + (cols // cell_px)
    pairs = np.unique(np.stack([ids[rows, cols], cell]), axis=1)
    elems, counts = np.unique(pairs[0], return_counts=True)
    return dict((int(e), int(c)) for e, c in zip(elems, counts))


def ink_cells(mask, cell_px):
    """PURE. Cells holding at least one pixel of ``mask``."""
    rows, cols = np.nonzero(mask)
    if rows.size == 0:
        return 0
    w = mask.shape[1]
    return int(np.unique((rows // cell_px) * ((w + cell_px - 1) // cell_px)
                         + (cols // cell_px)).size)


def _q(values, q):
    return None if not values else round(float(np.percentile(values, q)), 3)


def view_arm_report(ink, owner, truth, truth_category, cell_px=DEFAULT_CELL_PX,
                    labels=None, truth_label=None):
    """PURE. The report for one view and arm.

    ``truth``: the baseline's element id per pixel (0 none). ``truth_category``:
    {element_id: category name}. ``truth_label``: {element_id: label} in this
    arm's labelling (category or type id), for ``label_mismatch``."""
    out = _shares(ink, owner)
    unique = owner > 0
    known = unique & (truth > 0)
    out["unique_correct"] = (None if not known.any() else
                             round(float((owner[known] == truth[known]).sum())
                                   / float(known.sum()), 6))
    out["unique_on_no_baseline_element"] = int((unique & (truth == 0)).sum())
    groups = {}
    if truth_category:
        ids = np.unique(truth[ink & (truth > 0)])
        by_group = {}
        for e in ids:
            by_group.setdefault(category_group(truth_category.get(int(e), "")), []).append(e)
        for group, members in sorted(by_group.items()):
            groups[group] = _shares(ink, owner, np.isin(truth, np.array(members)))
    out["by_category_group"] = groups
    if labels is not None and truth_label is not None:
        on = ink & (truth > 0)
        n = int(on.sum())
        if n:
            expected = np.full(truth.shape, OFF_PALETTE - 1, dtype=np.int64)
            for e, lab in truth_label.items():
                if lab is not None:
                    expected[truth == int(e)] = int(lab)
            out["label_mismatch"] = round(float((on & (labels != expected)).sum()) / n, 6)
            out["off_palette"] = round(float((on & (labels == OFF_PALETTE)).sum()) / n, 6)
    base = element_cells(truth, cell_px)
    arm = element_cells(np.where(unique, owner, 0), cell_px)
    errs = [abs(arm.get(e, 0) - base.get(e, 0)) for e in set(base) | set(arm)]
    out["element_cell_abs_error"] = {"median": _q(errs, 50), "p90": _q(errs, 90),
                                     "elements": len(errs)}
    out["ink_cells"] = ink_cells(ink, cell_px)
    out["baseline_element_cells_total"] = int(sum(base.values()))
    out["arm_element_cells_total"] = int(sum(arm.values()))
    return out


# ======================================================================
# I/O
# ======================================================================

def _load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _load_rgb(path):
    from PIL import Image
    with Image.open(path) as img:
        return np.asarray(img.convert("RGB"))


def baseline_paths(run_dir, view_id):
    """``(model_sidecar, model_tiff)`` of ``view_id`` in a capture run."""
    from vop_interwoven import output_layout
    for layout in (output_layout.V2, output_layout.V1):
        folder = os.path.join(run_dir, output_layout.CAPTURE_DIR[layout])
        hits = [p for p in glob.glob(os.path.join(folder, "*_{0}.json".format(view_id)))
                if re.search(r"_{0}\.json$".format(view_id), os.path.basename(p))]
        if len(hits) == 1:
            return hits[0], hits[0][:-len(".json")] + ".tiff"
        if len(hits) > 1:
            raise ValueError("more than one model sidecar for view {0} in {1}".format(
                view_id, folder))
    raise FileNotFoundError("no model sidecar for view {0} under {1}".format(view_id, run_dir))


def census_boxes(sidecar, mapping, shape):
    """The host census boxes, in pixels, with each element's category name."""
    from tools import stage_a_sidecar_shapes as shapes
    boxes, categories = [], {}
    for key, entry in ((sidecar.get("near_face_w_map") or {}).get("host") or {}).items():
        if not isinstance(entry, dict):
            continue
        state, rect = shapes.model_entry_uv(entry)
        cstate, cat = shapes.model_entry_category(entry)
        if cstate == shapes.VALUE:
            categories[int(key)] = cat
        if state != shapes.VALUE or not rect:
            continue
        us = [c[0] for c in rect]
        vs = [c[1] for c in rect]
        boxes.append({"id": int(key), "rect": uv_rect_to_px(
            (min(us), min(vs), max(us), max(vs)), mapping, shape)})
    return boxes, categories


def analyze_view(view_record, baseline_run_dir, cell_px=DEFAULT_CELL_PX):
    """One view of the probe record: the report per arm, or a refusal."""
    from tools import stage_a_grid as grid
    from tools.stage_a_sidecar_shapes import frame_record
    view_id = int(view_record["view_id"])
    sidecar_path, tiff_path = baseline_paths(baseline_run_dir, view_id)
    sidecar = _load_json(sidecar_path)
    rgb = _load_rgb(tiff_path)
    shape = rgb.shape[:2]
    masks, truth, _packed, _decode = grid.model_pixels(sidecar, rgb)
    frame = frame_record(sidecar)
    if not frame.get("crop_uv"):
        return {"view_id": view_id, "refused": "the baseline frame records no crop_uv"}
    mapping = grid.nominal_mapping(frame["crop_uv"], shape[1], shape[0])
    boxes, categories = census_boxes(sidecar, mapping, shape)
    out = {"view_id": view_id, "baseline_sidecar": sidecar_path,
           "baseline_shape": list(shape), "census_boxes": len(boxes),
           "baseline_link_px": int(masks["link"].sum()),
           "baseline_ink_cells": ink_cells(truth > 0, cell_px), "arms": {}}
    element_labels = dict((int(k), v) for k, v in
                          (view_record.get("element_labels") or {}).items())
    for arm in ARMS:
        rec = (view_record.get("arms") or {}).get(arm)
        if not rec or not rec.get("tiff_path") or not os.path.isfile(rec["tiff_path"]):
            out["arms"][arm] = {"refused": "no exported image for this arm"}
            continue
        arm_rgb = _load_rgb(rec["tiff_path"])
        if arm_rgb.shape[:2] != shape:
            out["arms"][arm] = {"refused": "image {0} is not the baseline's {1}: not on "
                                "one lattice, not resampled".format(
                                    list(arm_rgb.shape[:2]), list(shape))}
            continue
        packed = pack(arm_rgb)
        ink = packed != WHITE
        labels = truth_label = None
        arm_boxes = boxes
        if arm in LABELLED_ARMS:
            label_colour = dict((int(k), v) for k, v in (rec.get("label_colour") or {}).items())
            labels = label_image(packed, label_colour)
            truth_label = dict((e, (element_labels.get(e) or {}).get(arm))
                               for e in element_labels)
            arm_boxes = [dict(b, label=truth_label.get(b["id"])) for b in boxes]
        owner, _count = attribute(ink, arm_boxes, labels)
        report = view_arm_report(ink, owner, truth, categories, cell_px, labels, truth_label)
        report["timings_ms"] = rec.get("timings_ms")
        report["precedence"] = rec.get("precedence")
        out["arms"][arm] = report
    return out


METRICS_NOT_COMPUTED = (
    "Load, Doc Burden and Fidelity are defined in the analysis layer, not in this "
    "repository; ink_cells and the per-element cells are their inputs, reported per "
    "arm and for the baseline")


def run(probe_dir, baseline_run_dir, cell_px=DEFAULT_CELL_PX):
    records = sorted(glob.glob(os.path.join(probe_dir, "probe_colour_mode_*.json")))
    if len(records) != 1:
        raise FileNotFoundError("expected one probe_colour_mode_*.json in {0}, found {1}".format(
            probe_dir, len(records)))
    record = _load_json(records[0])
    views = []
    for view_record in record.get("views") or []:
        try:
            views.append(analyze_view(view_record, baseline_run_dir, cell_px))
        except (OSError, ValueError, KeyError) as ex:
            views.append({"view_id": view_record.get("view_id"),
                          "refused": "{0}: {1}".format(type(ex).__name__, ex)})
    return {"probe_record": records[0], "baseline_run_dir": baseline_run_dir,
            "cell_px": cell_px, "metrics_not_computed": METRICS_NOT_COMPUTED,
            "object_styles_restored": record.get("object_styles_restored"),
            "views": views}


CSV_COLUMNS = ("view_id", "arm", "refused", "ink_px", "unique", "ambiguous", "unassigned",
               "unique_correct", "label_mismatch", "off_palette",
               "element_cell_err_median", "element_cell_err_p90", "ink_cells",
               "baseline_ink_cells", "walls_ambiguous", "casework_ambiguous",
               "furniture_ambiguous", "doors_ambiguous", "other_ambiguous")


def csv_rows(result):
    for view in result["views"]:
        arms = view.get("arms") or {}
        if not arms:
            yield {"view_id": view.get("view_id"), "refused": view.get("refused")}
        for arm, rep in arms.items():
            row = {"view_id": view["view_id"], "arm": arm, "refused": rep.get("refused"),
                   "baseline_ink_cells": view.get("baseline_ink_cells")}
            for key in ("ink_px", "unique", "ambiguous", "unassigned", "unique_correct",
                        "label_mismatch", "off_palette", "ink_cells"):
                row[key] = rep.get(key)
            err = rep.get("element_cell_abs_error") or {}
            row["element_cell_err_median"] = err.get("median")
            row["element_cell_err_p90"] = err.get("p90")
            for group, _names in CATEGORY_GROUPS + (("other", ()),):
                row[group + "_ambiguous"] = ((rep.get("by_category_group") or {})
                                             .get(group) or {}).get("ambiguous")
            yield row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("probe_out_dir")
    parser.add_argument("baseline_run_dir",
                        help="the capture run holding the views' colour-ID captures (the "
                             "census boxes are its model sidecars' near_face_w_map)")
    parser.add_argument("--cell-px", type=int, default=DEFAULT_CELL_PX)
    args = parser.parse_args(argv)
    try:
        result = run(args.probe_out_dir, args.baseline_run_dir, args.cell_px)
    except (OSError, ValueError) as ex:
        print("refused: {0}".format(ex), file=sys.stderr)
        return 2
    json_path = os.path.join(args.probe_out_dir, "colour_mode_attribution.json")
    csv_path = os.path.join(args.probe_out_dir, "colour_mode_attribution.csv")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for row in csv_rows(result):
            writer.writerow(dict((k, "" if row.get(k) is None else row.get(k))
                                 for k in CSV_COLUMNS))
    print("wrote {0} and {1} ({2} views)".format(json_path, csv_path, len(result["views"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
