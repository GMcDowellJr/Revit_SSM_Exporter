#!/usr/bin/env python3
"""Offline analysis of a capture-state probe run (tests/dynamo/probe_capture_state.py).

The probe writes TIFFs and one JSON; Pillow and NumPy may not exist in
Dynamo, so every pixel is measured here instead.

Per TIFF: image size, distinct colour count, the fraction of non-white pixels
in a 2 % border band (a background indicator), the count of grey pixels
(r == g == b, neither white nor black) and the top 10 colours.

Per step against the S0 export of the same question and view: the changed
pixel count and fraction when the sizes match. When they do not the pair is
reported as ``size_differs`` -- never resized.

Q3 (scope box / crop shape): per view, one row per step with the written
box, the read-back box, the exported pixels, the pixels the box implies at
the same fit direction and pixel size, and the difference on the axis Revit
did NOT fit to.

Writes ``<probe_dir>/probe_capture_state_analysis.json`` LAST, carrying the
probe JSON's sha256, and prints a table. REFUSES (exit 2, with reasons, the
written record saying ``status: "refused"``) when the probe JSON is missing
or ambiguous or unreadable, when a TIFF the JSON names as exported is
missing, or when a TIFF cannot be read.

    python tools/analyze_capture_state_probe.py <probe_dir | probe json>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

SCHEMA = "vop.probe.capture_state.analysis.v1"
TOOL_VERSION = "1.0.0"
ANALYSIS_NAME = "probe_capture_state_analysis.json"
PROBE_GLOB = "probe_capture_state_*.json"

# The border band is this fraction of each image dimension, at least 1 px.
BORDER_FRACTION = 0.02
TOP_COLOURS = 10
# More than this many pixels between exported and implied, on the non-fit
# axis, is a mismatch (the analysis grid's frame check uses the same 1 px).
NON_FIT_TOLERANCE_PX = 1

WHITE = 0xFFFFFF
BLACK = 0x000000


class Refusal(Exception):
    """The analysis cannot be made; ``reasons`` says why."""

    def __init__(self, reasons):
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --- per image ---------------------------------------------------------------

def load_rgb(path):
    with Image.open(path) as img:
        return np.asarray(img.convert("RGB"), dtype=np.uint8)


def pack(rgb):
    return ((rgb[..., 0].astype(np.int64) << 16) | (rgb[..., 1].astype(np.int64) << 8)
            | rgb[..., 2].astype(np.int64))


def border_band_px(h, w, fraction=BORDER_FRACTION):
    """``(rows, cols)`` of the band: ``fraction`` of each dimension, at least 1."""
    return (max(1, int(math.ceil(fraction * h))), max(1, int(math.ceil(fraction * w))))


def border_mask(h, w, fraction=BORDER_FRACTION):
    rows, cols = border_band_px(h, w, fraction)
    mask = np.zeros((h, w), dtype=bool)
    mask[:rows, :] = mask[h - rows:, :] = True
    mask[:, :cols] = mask[:, w - cols:] = True
    return mask


def image_metrics(rgb):
    """PURE. The per-image numbers."""
    h, w = rgb.shape[:2]
    packed = pack(rgb)
    colours, counts = np.unique(packed, return_counts=True)
    band = border_mask(h, w)
    band_px = int(band.sum())
    non_white_band = int((band & (packed != WHITE)).sum())
    grey = ((rgb[..., 0] == rgb[..., 1]) & (rgb[..., 1] == rgb[..., 2])
            & (packed != WHITE) & (packed != BLACK))
    order = sorted(range(len(colours)), key=lambda i: (-int(counts[i]), int(colours[i])))
    top = [{"rgb": [int(colours[i]) >> 16, (int(colours[i]) >> 8) & 255, int(colours[i]) & 255],
            "count": int(counts[i])} for i in order[:TOP_COLOURS]]
    return {"size_px": [int(w), int(h)], "pixel_count": int(w * h),
            "distinct_colours": int(len(colours)),
            "border_band_px": list(border_band_px(h, w)),
            "border_pixels": band_px, "border_non_white_pixels": non_white_band,
            "border_non_white_fraction": non_white_band / float(band_px),
            "grey_pixels": int(grey.sum()), "top_colours": top}


def pair_metrics(base_rgb, rgb):
    """PURE. ``rgb`` against ``base_rgb``: changed pixels, or size_differs."""
    bh, bw = base_rgb.shape[:2]
    h, w = rgb.shape[:2]
    if (bh, bw) != (h, w):
        return {"state": "size_differs", "baseline_size_px": [int(bw), int(bh)],
                "size_px": [int(w), int(h)], "changed_pixels": None,
                "changed_fraction": None}
    changed = int(np.any(base_rgb != rgb, axis=2).sum())
    return {"state": "value", "size_px": [int(w), int(h)], "changed_pixels": changed,
            "changed_fraction": changed / float(w * h)}


# --- Q3 ------------------------------------------------------------------------

def _value(record):
    if isinstance(record, dict) and record.get("state") == "value":
        return record.get("value")
    return None


def _extent(box):
    if not isinstance(box, dict) or "min" not in box or "max" not in box:
        return None
    return [float(box["max"][0]) - float(box["min"][0]),
            float(box["max"][1]) - float(box["min"][1])]


def implied_px(extent_ft, pixel_size, fit_direction):
    """PURE. ``[w, h]`` a box of ``extent_ft`` exports to at ``pixel_size``
    on the fitted axis, the other axis scaled by the box's aspect."""
    if not extent_ft or not pixel_size or extent_ft[0] <= 0 or extent_ft[1] <= 0:
        return None
    if str(fit_direction).lower() == "vertical":
        return [int(round(pixel_size * extent_ft[0] / extent_ft[1])), int(pixel_size)]
    return [int(pixel_size), int(round(pixel_size * extent_ft[1] / extent_ft[0]))]


def non_fit_axis(fit_direction):
    """``(name, index)`` of the axis Revit derives rather than fits."""
    return ("width", 0) if str(fit_direction).lower() == "vertical" else ("height", 1)


def _refused_row(step, restored_after):
    """A step the probe did not run (or that raised): no numbers at all, so
    it can never read as a match or a mismatch."""
    return {"step": step.get("step"), "box_source": "refused",
            "refused": step.get("refused"), "refused_reason": step.get("refused_reason"),
            "blocked_by": step.get("blocked_by"), "restored_after": restored_after,
            "written_extent_ft": None, "read_back_extent_ft": None,
            "read_back_equal": None, "crop_write_state": None,
            "exported_px": None, "implied_px": None, "fit_direction": None,
            "non_fit_axis": None, "non_fit_delta_px": None, "non_fit_mismatch": None,
            "at_export": None}


def q3_rows(report, measured_by_file):
    """One row per Q3 step per view. ``measured_by_file`` maps a TIFF file
    name to its measured ``[w, h]``.

    ``restored_after`` is the probe's restore check after that step's
    rollback (``restore_checks[step]["restored"]``): False means the view did
    not return to its baseline, and the probe refused every later step."""
    views = []
    for view in ((report.get("questions") or {}).get("q3") or {}).get("views") or []:
        entry = {"view_id": view.get("view_id"), "role": view.get("role"), "rows": []}
        if view.get("refused"):
            entry["refused"] = view["refused"]
        checks = view.get("restore_checks") or {}
        for step in view.get("steps") or []:
            restored_after = (checks.get(step.get("step")) or {}).get("restored")
            if step.get("refused"):
                entry["rows"].append(_refused_row(step, restored_after))
                continue
            export = step.get("export") or {}
            common = step.get("common") or {}
            written = step.get("written_box")
            read_back = step.get("read_back_box") or _value(common.get("crop_box"))
            write_state = step.get("crop_write_state")
            if write_state is not None and write_state != "value":
                # The crop write was rejected: no box was written, so there
                # is nothing to imply from. Never the attempted box, never
                # the read-back.
                box_source, box = "write_failed", None
            elif written:
                box_source, box = "written", written
            else:
                box_source, box = "s0_read", read_back
            fit = export.get("fit_direction") or "horizontal"
            implied = implied_px(_extent(box), export.get("requested_pixel_size"), fit)
            exported = measured_by_file.get(export.get("file"))
            axis, index = non_fit_axis(fit)
            row = {"step": step.get("step"), "box_source": box_source,
                   "restored_after": restored_after,
                   "written_extent_ft": _extent(written),
                   "read_back_extent_ft": _extent(read_back),
                   "read_back_equal": step.get("read_back_equal"),
                   "crop_write_state": write_state,
                   "exported_px": exported, "implied_px": implied,
                   "fit_direction": fit, "non_fit_axis": axis,
                   "non_fit_delta_px": None, "non_fit_mismatch": None,
                   "at_export": step.get("at_export")}
            if exported and implied:
                row["non_fit_delta_px"] = int(exported[index]) - int(implied[index])
                row["non_fit_mismatch"] = abs(row["non_fit_delta_px"]) > NON_FIT_TOLERANCE_PX
            entry["rows"].append(row)
        views.append(entry)
    return views


# --- the run -------------------------------------------------------------------

def find_probe_json(target):
    target = Path(target)
    if target.is_file():
        return target, target.parent
    if not target.is_dir():
        raise Refusal(["{0} is neither a probe folder nor a probe JSON".format(target)])
    found = sorted(target.glob(PROBE_GLOB))
    found = [p for p in found if p.name != ANALYSIS_NAME]
    if not found:
        raise Refusal(["no {0} in {1}: the probe JSON is missing".format(PROBE_GLOB, target)])
    if len(found) > 1:
        raise Refusal(["{0} probe JSONs in {1} ({2}); name one".format(
            len(found), target, ", ".join(p.name for p in found))])
    return found[0], target


def _split_step(step):
    question, _sep, name = str(step).partition("_")
    return question, name


def analyse(probe_json, probe_dir):
    """The analysis record, or raises Refusal."""
    try:
        report = json.loads(Path(probe_json).read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        raise Refusal(["the probe JSON cannot be read: {0}: {1}".format(
            type(ex).__name__, ex)])
    exports = report.get("exports")
    if not isinstance(exports, list):
        raise Refusal(["the probe JSON carries no exports list"])
    reasons, produced, failed = [], [], []
    for export in exports:
        if export.get("state") != "value":
            failed.append({"step": export.get("step"), "view_id": export.get("view_id"),
                           "file": export.get("file"), "error": export.get("error")})
            continue
        path = Path(probe_dir) / str(export.get("file"))
        if not path.is_file():
            reasons.append("TIFF {0} named in the probe JSON is missing".format(export.get("file")))
            continue
        produced.append((export, path))
    if reasons:
        raise Refusal(reasons)
    images, pixels = [], {}
    for export, path in produced:
        try:
            rgb = load_rgb(path)
        except Exception as ex:
            reasons.append("TIFF {0} cannot be read: {1}: {2}".format(
                path.name, type(ex).__name__, ex))
            continue
        pixels[path.name] = rgb
        question, step = _split_step(export.get("step"))
        images.append(dict(image_metrics(rgb), file=path.name, step=export.get("step"),
                           question=question, step_name=step, view_id=export.get("view_id"),
                           probe_dims_px=_value(export.get("dims_px"))))
    if reasons:
        raise Refusal(reasons)
    baselines = dict(((i["question"], i["view_id"]), i) for i in images if i["step_name"] == "S0")
    pairs = []
    for img in images:
        if img["step_name"] == "S0":
            continue
        base = baselines.get((img["question"], img["view_id"]))
        pair = {"step": img["step"], "view_id": img["view_id"], "file": img["file"],
                "baseline": base["file"] if base else None}
        if base is None:
            pair.update(state="baseline_unavailable", changed_pixels=None,
                        changed_fraction=None)
        else:
            pair.update(pair_metrics(pixels[base["file"]], pixels[img["file"]]))
        pairs.append(pair)
    measured = dict((i["file"], i["size_px"]) for i in images)
    return {"status": "value", "images": images, "pairs_vs_s0": pairs,
            "failed_exports": failed, "q3": q3_rows(report, measured),
            "verify_changed": report.get("verify_changed"),
            "verify_summary": report.get("VERIFY_SUMMARY")}


def run(target):
    """Analyse and write the record; returns ``(record, path or None)``."""
    record = {"schema": SCHEMA, "tool_version": TOOL_VERSION,
              "probe_json": None, "probe_json_sha256": None}
    out_dir = Path(target) if Path(target).is_dir() else Path(target).parent
    try:
        probe_json, probe_dir = find_probe_json(target)
        out_dir = probe_dir
        record["probe_json"] = probe_json.name
        record["probe_json_sha256"] = sha256_file(probe_json)
        record.update(analyse(probe_json, probe_dir))
    except Refusal as ex:
        record.update(status="refused", reasons=ex.reasons)
    if not out_dir.is_dir():
        return record, None
    path = out_dir / ANALYSIS_NAME
    # Written LAST, once the record is complete (CLAUDE.md, defect class 4).
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)
    return record, path


def _fmt(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        return "{0:.4f}".format(v)
    if isinstance(v, list):
        return "x".join(_fmt(x) if not isinstance(x, float) else "{0:.2f}".format(x) for x in v)
    return str(v)


def print_tables(record):
    if record["status"] != "value":
        print("REFUSED")
        for reason in record["reasons"]:
            print("  - " + reason)
        return
    print("{0:<28} {1:>11} {2:>8} {3:>9} {4:>9}".format(
        "image", "size", "colours", "border%", "grey px"))
    for img in record["images"]:
        print("{0:<28} {1:>11} {2:>8} {3:>9.3f} {4:>9}".format(
            img["file"][:28], _fmt(img["size_px"]), img["distinct_colours"],
            100.0 * img["border_non_white_fraction"], img["grey_pixels"]))
    print()
    print("{0:<28} {1:<20} {2:>12} {3:>9}".format("step vs S0", "state", "changed px", "changed%"))
    for pair in record["pairs_vs_s0"]:
        frac = pair.get("changed_fraction")
        print("{0:<28} {1:<20} {2:>12} {3:>9}".format(
            pair["file"][:28], pair["state"], _fmt(pair.get("changed_pixels")),
            "-" if frac is None else "{0:.3f}".format(100.0 * frac)))
    for view in record["q3"]:
        print()
        print("Q3 view {0} ({1}){2}".format(view["view_id"], view["role"],
                                            "  REFUSED: {0}".format(view["refused"])
                                            if view.get("refused") else ""))
        print("  {0:<4} {1:<12} {2:>15} {3:>15} {4:>11} {5:>11} {6:>8} {7:<8} {8}".format(
            "step", "box", "written ft", "read-back ft", "exported", "implied",
            "non-fit", "mismatch", "restored"))
        for row in view["rows"]:
            print("  {0:<4} {1:<12} {2:>15} {3:>15} {4:>11} {5:>11} {6:>8} {7:<8} {8}".format(
                row["step"], row["box_source"], _fmt(row["written_extent_ft"]),
                _fmt(row["read_back_extent_ft"]), _fmt(row["exported_px"]),
                _fmt(row["implied_px"]), _fmt(row["non_fit_delta_px"]),
                _fmt(row["non_fit_mismatch"]), _fmt(row["restored_after"])))
            if row.get("refused"):
                print("       refused: {0}".format(row.get("refused_reason") or row["refused"]))
    if record.get("failed_exports"):
        print()
        print("failed exports: {0}".format(", ".join(
            str(f["file"]) for f in record["failed_exports"])))
    print()
    print("probe verify: {0}".format(record.get("verify_summary")))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("probe", help="the probe's capture_state_<timestamp> folder, or its JSON")
    args = ap.parse_args(argv)
    record, path = run(args.probe)
    print_tables(record)
    if path is not None:
        print("wrote {0}".format(path))
    return 0 if record["status"] == "value" else 2


if __name__ == "__main__":
    sys.exit(main())
