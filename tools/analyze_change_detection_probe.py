#!/usr/bin/env python3
"""Offline ground truth for the change-detection probe (Q1).

tests/dynamo/probe_change_detection.py asks Revit which elements changed
between archive A and archive B and turns that into a per-view verdict
(``api_verdict``: changed / unchanged / unknown). This tool checks that
verdict against the captures ALREADY MADE of A and B (two run directories,
e.g. the 2026-10-21 batch's), view by view:

  * model pass: the decoded element-id images (ticks removed, the union of
    both captures' tick masks excluded from the comparison), compared pixel
    by pixel; linked pixels compared by linked CATEGORY name, since a run's
    link colours are its own;
  * annotation pass: the same, on the raw annotation TIFFs;
  * frame: view scale, fit, pixel size, achieved size and fpp, crop;
  * bbox maps: the model sidecar's ``near_face_w_map`` host entries and the
    annotation sidecar's ``annotation_bbox_map``: ids added, removed, and
    entries whose geometry moved.

A view's capture DIFFERS when any of those is non-zero; every count is
reported so a reader can apply another rule. Nothing here is a threshold:
"differs" is "> 0", and the counts are beside it.

The table crosses api_verdict with capture differs / same / not comparable.
A FALSE UNCHANGED is a view the API called unchanged whose capture differs;
each is listed with what differs and a cause label (``view_setting``,
``element``, ``link``, ``type_or_graphics``, ``other``). Cause labels are
INFERENCE from the captures, not observation of the model.

Writes ``<probe dir>/change_detection_analysis.json`` LAST, carrying the probe
JSON's sha256, and prints the table. Refuses (exit 2) a probe JSON that is
missing, unreadable, not finalized, or stopped at reflection.

    python tools/analyze_change_detection_probe.py <probe dir | json> \
        --run-a <run dir of A> --run-b <run dir of B>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import decode_stage_a_color_id as dsc  # noqa: E402
from tools import registration_marks as rm  # noqa: E402
from tools.stage_a_sidecar_shapes import frame_record  # noqa: E402
from vop_interwoven import output_layout  # noqa: E402

SCHEMA = "vop.probe.change_detection.analysis.v1"
TOOL_VERSION = "1.0.0"
ANALYSIS_NAME = "change_detection_analysis.json"
PROBE_GLOB = "probe_change_detection_*.json"
FRAME_KEYS = ("view_scale", "fit_direction", "pixel_size", "actual_w", "actual_h",
              "max_axis_px", "achieved_fpp_ft", "crop_uv")
GEOMETRY_TOL_FT = 1.0e-6
LINK_BASE = -1000
LIST_LIMIT = 50


class Refusal(Exception):
    pass


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# --- finding a view's capture in a run --------------------------------------------

def capture_folder(run_dir):
    """The run's capture folder (v2 ``color_id/`` or v1 ``color_id_buffer/``),
    or the folder itself when it already is one. Both present is refused."""
    run_dir = Path(run_dir)
    found = [run_dir / name for name in (output_layout.CAPTURE_DIR[output_layout.V2],
                                         output_layout.CAPTURE_DIR[output_layout.V1])
             if (run_dir / name).is_dir()]
    if len(found) > 1:
        raise Refusal("{0} holds both capture folders".format(run_dir))
    return found[0] if found else run_dir


def view_files(folder, view_id):
    """``{"model": path|None, "anno": path|None}`` for one view id: the sidecar
    whose stem ends ``_<id>`` and its ``_<id>_anno`` sibling. Two matches for
    one id is ambiguous and raises."""
    model = [p for p in folder.glob("*_{0}.json".format(view_id))
             if re.search(r"_{0}\.json$".format(view_id), p.name)]
    anno = [p for p in folder.glob("*_{0}_anno.json".format(view_id))]
    if len(model) > 1 or len(anno) > 1:
        raise Refusal("view {0}: more than one sidecar in {1}: {2}".format(
            view_id, folder, sorted(p.name for p in model + anno)))
    return {"model": model[0] if model else None, "anno": anno[0] if anno else None}


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# --- PURE comparisons -------------------------------------------------------------

def _close(a, b, tol=GEOMETRY_TOL_FT):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and \
            not isinstance(a, bool) and not isinstance(b, bool):
        return abs(float(a) - float(b)) <= tol
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_close(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(_close(a[k], b[k], tol) for k in a)
    return a == b


def frame_diff(frame_a, frame_b, keys=FRAME_KEYS):
    """PURE. The FRAME_KEYS whose values differ (floats within 1e-6)."""
    return sorted(k for k in keys if not _close((frame_a or {}).get(k), (frame_b or {}).get(k)))


def bbox_map_diff(map_a, map_b, fields):
    """PURE. ``{added, removed, moved, ids...}`` between two id -> entry maps;
    an entry has MOVED when any of ``fields`` differs beyond 1e-6 ft."""
    map_a, map_b = map_a or {}, map_b or {}
    ka, kb = set(map(str, map_a)), set(map(str, map_b))
    moved = sorted(k for k in ka & kb
                   if any(not _close((map_a[k] or {}).get(f), (map_b[k] or {}).get(f))
                          for f in fields))
    added, removed = sorted(kb - ka), sorted(ka - kb)
    return {"added": len(added), "removed": len(removed), "moved": len(moved),
            "added_ids": added[:LIST_LIMIT], "removed_ids": removed[:LIST_LIMIT],
            "moved_ids": moved[:LIST_LIMIT]}


def id_image_diff(ids_a, ids_b, exclude=None):
    """PURE. Two decoded images of identical shape. ``{shape_equal, differing_px,
    compared_px, excluded_px, element_ids, link_px}``: ``element_ids`` are the
    host ids on either side of a differing pixel (0 and link codes left out);
    ``link_px`` the differing pixels where either side is a linked category."""
    if ids_a.shape != ids_b.shape:
        return {"shape_equal": False, "shape_a": list(ids_a.shape),
                "shape_b": list(ids_b.shape), "differing_px": None}
    diff = ids_a != ids_b
    excluded = 0
    if exclude is not None:
        excluded = int((diff & exclude).sum())
        diff &= ~exclude
    a, b = ids_a[diff], ids_b[diff]
    elements = np.union1d(a[a > 0], b[b > 0])
    link = int(((a <= LINK_BASE) | (b <= LINK_BASE)).sum())
    compared = ids_a.size - (int(exclude.sum()) if exclude is not None else 0)
    return {"shape_equal": True, "differing_px": int(diff.sum()), "compared_px": compared,
            "excluded_px": excluded, "link_px": link,
            "element_ids": [int(x) for x in elements[:LIST_LIMIT]],
            "element_id_count": int(elements.size)}


def classify(entry):
    """PURE. Cause labels (INFERENCE) for one compared view: what in the
    capture differs, never what changed in the model."""
    labels = []
    if entry.get("frame_diff"):
        labels.append("view_setting")
    bbox_moves = 0
    for key in ("model_bbox", "anno_bbox"):
        d = entry.get(key) or {}
        bbox_moves += (d.get("added") or 0) + (d.get("removed") or 0) + (d.get("moved") or 0)
    if bbox_moves:
        labels.append("element")
    pix = [entry.get(k) or {} for k in ("model_pixels", "anno_pixels")]
    link = sum(p.get("link_px") or 0 for p in pix)
    if link:
        labels.append("link")
    elem_px = sum((p.get("differing_px") or 0) for p in pix) - link
    if elem_px > 0 and not bbox_moves and not entry.get("frame_diff"):
        # Pixels moved while every element's recorded geometry held: a type,
        # a graphic override, a visibility change -- appearance, not position.
        labels.append("type_or_graphics")
    if any(p.get("shape_equal") is False for p in pix) and "view_setting" not in labels:
        labels.append("view_setting")
    return labels or ["other"]


def capture_differs(entry):
    """PURE. True / False, or None when a needed part was not comparable."""
    parts = []
    for key in ("model_pixels", "anno_pixels"):
        p = entry.get(key)
        if p is None:
            continue
        if p.get("shape_equal") is False:
            return True
        parts.append((p.get("differing_px") or 0) > 0)
    for key in ("model_bbox", "anno_bbox"):
        d = entry.get(key)
        if d is not None:
            parts.append(bool(d["added"] or d["removed"] or d["moved"]))
    if entry.get("frame_diff"):
        return True
    if entry.get("model_pixels") is None:
        return None
    return any(parts)


def cross_table(rows):
    """PURE. ``{api_verdict: {differs, same, not_comparable}}``."""
    table = {}
    for row in rows:
        api = row.get("api_verdict") or "not_probed"
        cap = {True: "differs", False: "same", None: "not_comparable"}[row.get("capture_differs")]
        table.setdefault(api, {"differs": 0, "same": 0, "not_comparable": 0})[cap] += 1
    return table


# --- decoding ------------------------------------------------------------------

def link_codes(names):
    """``{category name: code}``: stable negative codes for a union of names."""
    return dict((n, LINK_BASE - i) for i, n in enumerate(sorted(names)))


def decoded(sidecar_path, codes):
    """``(ids, tick mask or None)``: the capture decoded to element ids with
    its ticks removed, and linked pixels set to their category's code."""
    sidecar = _load(sidecar_path)
    tiff = dsc._resolve_tiff_path(Path(sidecar_path), sidecar)
    rgb = dsc._load_rgb_array(tiff)
    packed = rm.pack_pixels(rgb)
    decode_map, _block, mask = dsc._registration_mark_exclusion(rgb, sidecar, with_fit=False,
                                                                 packed=packed)
    ids, _stats = dsc.decode_ids(rgb, decode_map, packed=packed, with_stats=False)
    ids = ids.astype(np.int64)
    for name, rgb_c in (sidecar.get("link_category_color_map") or {}).items():
        c = (int(rgb_c[0]) << 16) | (int(rgb_c[1]) << 8) | int(rgb_c[2])
        ids[(packed == c) & (ids == 0)] = codes[name]
    if mask is not None:
        ids[mask] = 0
    return ids, mask


def compare_pass(path_a, path_b):
    sa, sb = _load(path_a), _load(path_b)
    codes = link_codes(set(sa.get("link_category_color_map") or {})
                       | set(sb.get("link_category_color_map") or {}))
    ids_a, mask_a = decoded(path_a, codes)
    ids_b, mask_b = decoded(path_b, codes)
    exclude = None
    if ids_a.shape == ids_b.shape and (mask_a is not None or mask_b is not None):
        exclude = np.zeros(ids_a.shape, dtype=bool)
        for m in (mask_a, mask_b):
            if m is not None:
                exclude |= m
    return id_image_diff(ids_a, ids_b, exclude)


def compare_view(view_id, files_a, files_b):
    entry = {"view_id": view_id,
             "files_a": dict((k, str(v) if v else None) for k, v in files_a.items()),
             "files_b": dict((k, str(v) if v else None) for k, v in files_b.items())}
    if not files_a["model"] or not files_b["model"]:
        # A single-pass annotation-only view has no model sidecar: its anno
        # pair is still compared below, but the model part is not comparable.
        entry["model_pixels"] = None
    else:
        ma, mb = _load(files_a["model"]), _load(files_b["model"])
        entry["frame_diff"] = frame_diff(frame_record(ma), frame_record(mb))
        entry["model_pixels"] = compare_pass(files_a["model"], files_b["model"])
        entry["model_bbox"] = bbox_map_diff(
            (ma.get("near_face_w_map") or {}).get("host"),
            (mb.get("near_face_w_map") or {}).get("host"), ("bbox_3d", "uv_rect"))
    if files_a["anno"] and files_b["anno"]:
        aa, ab = _load(files_a["anno"]), _load(files_b["anno"])
        entry["anno_pixels"] = compare_pass(files_a["anno"], files_b["anno"])
        entry["anno_bbox"] = bbox_map_diff(aa.get("annotation_bbox_map"),
                                           ab.get("annotation_bbox_map"),
                                           ("bbox_uv", "bbox_3d"))
        if entry.get("model_pixels") is None and not files_a["model"] and not files_b["model"]:
            entry["model_pixels"] = {"shape_equal": True, "differing_px": 0,
                                     "note": "annotation-only capture in both runs"}
    elif bool(files_a["anno"]) != bool(files_b["anno"]):
        entry["anno_pixels"] = {"shape_equal": False, "differing_px": None,
                                "note": "annotation capture in one run only"}
    return entry


# --- the run -------------------------------------------------------------------

def find_probe_json(target):
    target = Path(target)
    if target.is_file():
        return target
    found = sorted(target.glob(PROBE_GLOB))
    if len(found) != 1:
        raise Refusal("expected one {0} in {1}, found {2}".format(
            PROBE_GLOB, target, [p.name for p in found]))
    return found[0]


def analyze(target, run_a, run_b):
    probe_path = find_probe_json(target)
    try:
        probe = _load(probe_path)
    except (OSError, ValueError) as ex:
        raise Refusal("probe JSON unreadable: {0}".format(ex))
    if not probe.get("finalized"):
        raise Refusal("probe JSON is not finalized (the probe did not finish): {0}".format(
            probe_path))
    if probe.get("stopped"):
        raise Refusal("the probe stopped before opening anything: {0}".format(probe["stopped"]))
    folder_a, folder_b = capture_folder(run_a), capture_folder(run_b)
    rows = []
    for view in probe.get("views") or []:
        vid = int(view["view_id"])
        row = {"view_id": vid, "name": (view.get("name") or {}).get("value"),
               "api_verdict": view.get("api_verdict") if view.get("present_in_b")
               else "absent_in_b",
               "api_fired": view.get("fired"), "api_unread": view.get("unread"),
               "membership_diff": view.get("membership_diff")}
        try:
            fa, fb = view_files(folder_a, vid), view_files(folder_b, vid)
            if not (fa["model"] or fa["anno"]) or not (fb["model"] or fb["anno"]):
                row.update(capture_differs=None,
                           not_comparable="no capture of the view in run {0}".format(
                               "A" if not (fa["model"] or fa["anno"]) else "B"))
            else:
                entry = compare_view(vid, fa, fb)
                row.update(entry)
                row["capture_differs"] = capture_differs(entry)
                if row["capture_differs"]:
                    row["cause_inference"] = classify(entry)
        except (Refusal, OSError, ValueError, KeyError) as ex:
            row.update(capture_differs=None,
                       not_comparable="{0}: {1}".format(type(ex).__name__, ex))
        rows.append(row)
    false_unchanged = [
        {"view_id": r["view_id"], "name": r["name"], "cause_inference": r.get("cause_inference"),
         "frame_diff": r.get("frame_diff"),
         "model_differing_px": (r.get("model_pixels") or {}).get("differing_px"),
         "anno_differing_px": (r.get("anno_pixels") or {}).get("differing_px"),
         "model_bbox": r.get("model_bbox"), "anno_bbox": r.get("anno_bbox")}
        for r in rows if r["api_verdict"] == "unchanged" and r.get("capture_differs")]
    false_changed = [r["view_id"] for r in rows
                     if r["api_verdict"] == "changed" and r.get("capture_differs") is False]
    return probe_path, {
        "schema": SCHEMA, "tool_version": TOOL_VERSION, "status": "value",
        "probe_json": str(probe_path), "probe_json_sha256": sha256_file(probe_path),
        "run_a": str(run_a), "run_b": str(run_b),
        "per_view_against": probe.get("per_view_against"),
        "table": cross_table(rows),
        "false_unchanged": false_unchanged, "false_unchanged_count": len(false_unchanged),
        "api_changed_capture_same": false_changed,
        "rule": "capture differs: any decoded-id pixel (ticks excluded), frame field, "
                "or bbox-map entry differs (> 0, no threshold); cause labels are INFERENCE",
        "views": rows}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("probe", help="the probe folder or its probe_change_detection_*.json")
    ap.add_argument("--run-a", required=True, help="run directory captured from archive A")
    ap.add_argument("--run-b", required=True, help="run directory captured from archive B")
    ap.add_argument("--out", default=None, help="analysis JSON path (default: beside the probe)")
    args = ap.parse_args(argv)
    try:
        probe_path, record = analyze(args.probe, args.run_a, args.run_b)
    except Refusal as ex:
        print("REFUSED: {0}".format(ex))
        return 2
    out = Path(args.out) if args.out else probe_path.parent / ANALYSIS_NAME
    out.write_text(json.dumps(record, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print("api_verdict      differs   same  not_comparable")
    for api, row in sorted(record["table"].items()):
        print("{0:<15} {1:>8} {2:>6} {3:>15}".format(api, row["differs"], row["same"],
                                                     row["not_comparable"]))
    print("false unchanged: {0}".format(record["false_unchanged_count"]))
    for fu in record["false_unchanged"]:
        print("  {0} {1}: {2}".format(fu["view_id"], fu["name"], ",".join(fu["cause_inference"])))
    print("wrote {0}".format(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
