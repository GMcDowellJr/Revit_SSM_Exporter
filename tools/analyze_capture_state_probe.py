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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

Image.MAX_IMAGE_PIXELS = None

SCHEMA = "vop.probe.capture_state.analysis.v1"
TOOL_VERSION = "1.2.0"
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
            "non_white_pixels": int((packed != WHITE).sum()),
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


# --- round 2 (1.1.0): writes, Q1b, Q3b, Q5b, Q6 ------------------------------

# TransactionStatus by number. Probe 2026-10-01.x recorded statuses with
# str(), which under Python.NET 3 gives the NUMBER ("3"); 2026-10-02.1 records
# the name. Both read the same here.
TRANSACTION_STATUS_NAMES = {"0": "Uninitialized", "1": "Started", "2": "RolledBack",
                            "3": "Committed", "4": "Pending", "5": "Error", "6": "Proceed"}
# Pixels added round a mark's projected segment: the line's own width and
# the nominal mapping's sub-pixel error.
WINDOW_PAD_PX = 3
# Which unmarked Q6 export each marked one is compared with. S1/S3 are round
# 2's; the rest are probe 2026-10-02.2's Q6b (tests/dynamo/probe_capture_state
# .py, run_q6_view), reported only when the probe recorded them.
Q6_TWINS = {"S1": "S0", "S3": "S2", "S5": "S4", "S7": "S6", "S10": "S6",
            "S9": "S8"}
Q6_VARIANTS = {"S1": "no_crop_write", "S3": "crop_write",
               "S5": "temporary_style_attached", "S7": "production_order_detached",
               "S10": "temporary_style_detached", "S9": "lines_unhidden"}
Q6_ROUND2_STEPS = ("S1", "S3")
# B: S8 (template detached, OST_Lines unhidden, every line hidden one by one)
# against S6 (template detached only) -- whether the view still shows as
# authored once Lines is unhidden. 0 changed pixels is "yes".
Q6_AUTHORED_PAIRS = {"S8": "S6"}
_THREE_VALUED_KEYS = frozenset(("state", "value", "error"))


def status_name(status):
    """A recorded transaction status by name, whichever form it was recorded in."""
    if status is None:
        return None
    text = str(status)
    return TRANSACTION_STATUS_NAMES.get(text, text)


def recorded(container, key):
    """What the probe recorded under ``key`` -- the value of a three-valued
    record, the raw value otherwise -- or ``{"unavailable": reason}``. Never a
    default: a missing or unreadable field says so."""
    if not isinstance(container, dict) or key not in container:
        return {"unavailable": "not recorded"}
    value = container[key]
    if isinstance(value, dict) and "state" in value and set(value) <= _THREE_VALUED_KEYS:
        if value["state"] == "value":
            return value.get("value")
        return {"unavailable": "{0}: {1}".format(value["state"], value.get("error"))}
    return value


def is_unavailable(value):
    return isinstance(value, dict) and set(value) == {"unavailable"}


def iter_writes(node, path=""):
    """Every write record (probe 2026-10-02.1: attempted / read_back /
    took_effect) anywhere in the probe JSON, with its path."""
    if isinstance(node, dict):
        if {"attempted", "read_back", "took_effect"} <= set(node):
            yield path, node
        for key, value in node.items():
            for item in iter_writes(value, "{0}/{1}".format(path, key)):
                yield item
    elif isinstance(node, list):
        for i, value in enumerate(node):
            for item in iter_writes(value, "{0}[{1}]".format(path, i)):
                yield item


def writes_section(report):
    """``(writes, commit_without_effect)``. A write that COMMITTED and did
    not take effect (read-back != attempted) is a finding, listed on its own."""
    writes = []
    for path, w in iter_writes(report.get("questions") or {}):
        writes.append({"path": path, "attempted": w.get("attempted"),
                       "state": w.get("state"), "error": w.get("error"),
                       "commit_status": w.get("commit_status"),
                       "commit_status_name": status_name(w.get("commit_status")),
                       "read_back": recorded(w, "read_back"),
                       "took_effect": w.get("took_effect"),
                       # False: the write asked for the value already there, so
                       # its took_effect cannot show a silent no-op. Absent
                       # before probe 2026-10-02.1.
                       "attempted_differs_from_before": w.get("attempted_differs_from_before")})
    silent = [w for w in writes
              if w["commit_status_name"] == "Committed" and w["took_effect"] is False]
    return writes, silent


def _views(report, question):
    return ((report.get("questions") or {}).get(question) or {}).get("views") or []


def _write_summary(write):
    if not isinstance(write, dict):
        return {"unavailable": "not recorded"}
    return {"state": write.get("state"), "commit_status_name": status_name(write.get("commit_status")),
            "took_effect": write.get("took_effect") if "took_effect" in write
            else {"unavailable": "not recorded (probe before 2026-10-02.1)"},
            "attempted_differs_from_before": write.get(
                "attempted_differs_from_before", {"unavailable": "not recorded"})}


def q1b_section(report):
    out = []
    for view in _views(report, "q1b"):
        entry = {"view_id": view.get("view_id"), "refused": view.get("refused")}
        for step in view.get("steps") or []:
            if step.get("step") != "S1":
                continue
            if step.get("refused"):
                entry.update(s1_refused=step["refused"], s1_reason=step.get("refused_reason"))
                continue
            m = step.get("membership") or {}
            details = m.get("details") or {}

            def _row(element_id, change):
                d = recorded(details, str(element_id))
                if is_unavailable(d):
                    return {"id": element_id, "change": change, "category": d,
                            "owner_view_id": d, "class": d}
                if not d.get("exists"):
                    return {"id": element_id, "change": change, "exists": False}
                return {"id": element_id, "change": change, "exists": True,
                        "class": d.get("class"), "category": recorded(d, "category"),
                        "owner_view_id": recorded(d, "owner_view_id")}
            rows = ([_row(i, "added") for i in m.get("added") or []]
                    + [_row(i, "removed") for i in m.get("removed") or []])
            by_category = {}
            for r in rows:
                cat = r.get("category")
                key = "{0}:{1}".format(r["change"], cat if not isinstance(cat, dict) else "unavailable")
                by_category[key] = by_category.get(key, 0) + 1
            diff = step.get("parameter_diff") or {}
            entry.update({
                "identity_write": _write_summary((step.get("writes") or {}).get("crop_box_identity")),
                "added_count": m.get("added_count"), "removed_count": m.get("removed_count"),
                "truncated": m.get("truncated"), "members": rows,
                "by_change_and_category": by_category,
                "watch_ids": step.get("watch_ids") or {"unavailable": "not recorded"},
                "parameters_changed": diff.get("changed") if diff else {"unavailable": "not recorded"},
                "parameters_unreadable": len(diff.get("unreadable") or []) if diff else None})
        out.append(entry)
    return out


def _same_transform(a, b, tol=1.0e-6):
    if not isinstance(a, dict) or not isinstance(b, dict):
        return None
    return all(abs(float(x) - float(y)) <= tol
               for key in ("origin", "basis_x", "basis_y", "basis_z")
               for x, y in zip(a.get(key) or [], b.get(key) or []))


def q3b_section(report, images_by_file, pairs_by_file):
    out = []
    for view in _views(report, "q3b"):
        entry = {"view_id": view.get("view_id"), "role": view.get("role"),
                 "refused": view.get("refused"),
                 "baseline_scope_box": view.get("baseline_scope_box"), "rows": []}
        s0_image = None
        for step in view.get("steps") or []:
            export = step.get("export") or {}
            image = images_by_file.get(export.get("file"))
            if step.get("step") == "S0":
                s0_image = image
            row = {"step": step.get("step"), "refused": step.get("refused"),
                   "refused_reason": step.get("refused_reason"),
                   "writes": dict((k, _write_summary(w)) for k, w in (step.get("writes") or {}).items()),
                   "scope_box_at_export": recorded(step.get("at_export") or {}, "scope_box"),
                   "transform_kept": _same_transform(step.get("read_back_transform"),
                                                     step.get("s0_transform")),
                   "read_back_extent_ft": step.get("read_back_extent_ft"),
                   "s0_extent_ft": step.get("s0_extent_ft"),
                   "exported_px": image["size_px"] if image else None,
                   "non_white_pixels": image["non_white_pixels"] if image else None,
                   "non_white_vs_s0": None, "changed_fraction_vs_s0": None}
            if image and s0_image and step.get("step") != "S0":
                row["non_white_vs_s0"] = image["non_white_pixels"] - s0_image["non_white_pixels"]
                pair = pairs_by_file.get(image["file"]) or {}
                row["changed_fraction_vs_s0"] = (pair.get("changed_fraction")
                                                 if pair.get("state") == "value" else pair.get("state"))
            entry["rows"].append(row)
        out.append(entry)
    return out


def q5b_section(report):
    out = []
    for view in _views(report, "q5b"):
        entry = {"view_id": view.get("view_id"), "primary_view_id": view.get("primary_view_id"),
                 "dependent_template_id": view.get("dependent_template_id"),
                 "primary_template_id": view.get("primary_template_id"),
                 "refused": view.get("refused"), "note": view.get("note"), "variants": {}}
        for step in view.get("steps") or []:
            name = step.get("step")
            if step.get("refused"):
                entry["variants"][name] = {"refused": step["refused"],
                                           "refused_reason": step.get("refused_reason")}
                continue
            v = {"detach_primary_template": _write_summary(step.get("detach_primary_template"))}
            if name == "A":
                flat = step.get("dependent_flat_colors") or {}
                v.update(dependent_template_id_after=recorded(step, "dependent_template_id_after"),
                         dependent_flat_colors=flat.get("outcome", {"unavailable": "not recorded"}))
            else:
                flat = step.get("primary_flat_colors") or {}
                v.update(primary_flat_colors=flat.get("outcome", {"unavailable": "not recorded"}),
                         dependent_display_style_before=recorded(step, "dependent_display_style_before"),
                         dependent_display_style_after=recorded(step, "dependent_display_style_after"),
                         dependent_followed_primary=step.get("dependent_followed_primary"))
            entry["variants"][name] = v
        out.append(entry)
    return out


def mark_window(uv0, uv1, crop_uv, w, h, pad=WINDOW_PAD_PX):
    """``(window, reason)``: the pixel rectangle ``[x0, y0, x1, y1)`` a mark
    from ``uv0`` to ``uv1`` projects to, padded, on an image of ``w`` x ``h``
    exported from ``crop_uv`` -- the grid's own nominal mapping
    (stage_a_grid.nominal_mapping, the decoder's frame), not a copy. None and
    a reason when it falls outside the image."""
    from tools.stage_a_grid import nominal_mapping
    m = nominal_mapping(crop_uv, w, h)
    xs = [m["a_u"] * float(p[0]) + m["b_u"] for p in (uv0, uv1)]
    ys = [m["a_v"] * float(p[1]) + m["b_v"] for p in (uv0, uv1)]
    x0, x1 = int(math.floor(min(xs))) - pad, int(math.ceil(max(xs))) + pad
    y0, y1 = int(math.floor(min(ys))) - pad, int(math.ceil(max(ys))) + pad
    cx0, cy0, cx1, cy1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if cx0 >= cx1 or cy0 >= cy1:
        return None, "the window [{0}, {1}, {2}, {3}] lies outside the {4} x {5} image".format(
            x0, y0, x1, y1, w, h)
    return [cx0, cy0, cx1, cy1], None


def export_matches_crop(crop_uv, export, shape):
    """Whether the exported image IS the crop: on the axis Revit did not fit
    to, the image is within NON_FIT_TOLERANCE_PX of what the crop's aspect
    implies at the requested pixel size (implied_px, the Q3 table's own
    arithmetic). Plans in round 2 exported ~190 px taller than their crop;
    the nominal mapping does not hold there."""
    if shape is None:
        return {"matches": None, "reason": "no marked export"}
    if is_unavailable(crop_uv) or not crop_uv:
        return {"matches": None, "reason": "the crop at export is unavailable"}
    h, w = shape
    fit = (export or {}).get("fit_direction") or "horizontal"
    implied = implied_px([float(crop_uv[2]) - float(crop_uv[0]),
                          float(crop_uv[3]) - float(crop_uv[1])],
                         (export or {}).get("requested_pixel_size"), fit)
    if implied is None:
        return {"matches": None, "reason": "no requested pixel size to imply from"}
    axis, index = non_fit_axis(fit)
    delta = [w, h][index] - implied[index]
    return {"matches": abs(delta) <= NON_FIT_TOLERANCE_PX, "implied_px": implied,
            "exported_px": [w, h], "non_fit_axis": axis, "non_fit_delta_px": delta,
            "reason": "{0} {1} px exported against {2} px implied by the crop".format(
                axis, [w, h][index], implied[index])}


def rendered_verdict(window_counts, twin_same_size, mark_colour_known):
    """PURE. ``(rendered, basis)``: true / false / "unmeasured".

    1. Pixels the marks changed against the unmarked export of the same state
       (same size): any -> true, none -> false.
    2. Otherwise, a pixel in the mark colour -> true.
    3. Otherwise, a window with no non-white pixel -> false.
    4. Otherwise "unmeasured": there is content, none of it in the mark
       colour, and nothing to compare it with."""
    if twin_same_size:
        return (window_counts["changed_vs_unmarked"] > 0), "changed_vs_unmarked"
    if mark_colour_known and window_counts["mark_colour"]:
        return True, "mark_colour"
    if window_counts["non_white"] == 0:
        return False, "no_non_white_pixels"
    return "unmeasured", ("non-white pixels in the window, none in the mark colour, and no "
                          "unmarked export of the same size to compare")


def _unmeasured(row, reason):
    row.update(rendered="unmeasured", rendered_basis=None, unmeasured_reason=reason,
               window_px=None, non_white_px=None, mark_colour_px=None,
               changed_vs_unmarked_px=None)
    return row


def q6_rows(report, pixels, images_by_file):
    """One row per (view, marked step, mark)."""
    rows = []
    for view in _views(report, "q6"):
        steps = dict((s.get("step"), s) for s in view.get("steps") or [])
        for name in sorted(Q6_TWINS, key=lambda n: int(n[1:])):
            if name not in Q6_ROUND2_STEPS and name not in steps:
                continue
            step = steps.get(name) or {}
            base = {"view_id": view.get("view_id"), "step": name, "variant": Q6_VARIANTS[name]}
            if not step or step.get("refused"):
                rows.append(dict(base, mark_id=None, refused=step.get("refused", "not recorded"),
                                 refused_reason=step.get("refused_reason") or view.get("refused")))
                continue
            filters = recorded(step, "view_filters")
            template = recorded(step, "template_vg_control")
            colour = (step.get("marks_record") or {}).get("colour")
            export = step.get("export") or {}
            marked = pixels.get(export.get("file"))
            twin_step = steps.get(Q6_TWINS[name]) or {}
            twin = pixels.get((twin_step.get("export") or {}).get("file"))
            crop_uv = recorded(step, "crop_uv_at_export")
            crop_active = recorded(step, "crop_box_active_at_export")
            # Per marked export, before any window: did the marks change ANY
            # pixel against the unmarked twin, and is the image the crop?
            # (Round 2, 9948: 137 px changed in the image, 0 inside windows
            # placed through a crop the export did not match.)
            twin_same = marked is not None and twin is not None and twin.shape == marked.shape
            changed_anywhere = (int(np.any(twin != marked, axis=2).sum()) if twin_same else None)
            export_vs_crop = export_matches_crop(
                crop_uv, export, marked.shape[:2] if marked is not None else None)
            for mark in step.get("mark_rows") or []:
                row = dict(base, mark_id=mark.get("id"), key=mark.get("key"),
                           orientation=mark.get("orientation"), placement=mark.get("placement"),
                           uv0=mark.get("uv0"), uv1=mark.get("uv1"), uv_source=mark.get("uv_source"),
                           painted=mark.get("painted"))
                for field in ("line_style_id", "line_style_name", "line_style_category_id",
                              "line_style_category_name", "lines_category_hidden",
                              "line_style_subcategory_hidden", "element_hidden",
                              "in_view_collector"):
                    row[field] = recorded(mark, field)
                row["view_filters"] = (filters if is_unavailable(filters) else
                                       [dict((k, recorded(f, k)) for k in
                                             ("name", "kind", "enabled", "visible", "includes_ost_lines"))
                                        for f in filters or []])
                row["template_vg_control"] = (template if is_unavailable(template) else
                                              dict((k, recorded(template, k) if k in template
                                                    else {"unavailable": "not recorded"})
                                                   for k in ("state", "template_id", "model_categories",
                                                             "annotation_categories", "filters")))
                row.update(changed_anywhere_px=changed_anywhere, export_vs_crop=export_vs_crop)
                if marked is None:
                    rows.append(_unmeasured(row, "the marked export is missing or failed"))
                    continue
                if changed_anywhere == 0:
                    # Nothing the marks drew reached the image at all, so no
                    # mark rendered -- whatever the mapping, no window needed.
                    _unmeasured(row, None)
                    row.update(rendered=False, rendered_basis="no_pixel_changed_anywhere",
                               unmeasured_reason=None)
                    rows.append(row)
                    continue
                if is_unavailable(crop_uv) or not crop_uv:
                    rows.append(_unmeasured(row, "the crop at export is unavailable ({0})".format(
                        crop_uv.get("unavailable") if isinstance(crop_uv, dict) else crop_uv)))
                    continue
                if crop_active is not True:
                    rows.append(_unmeasured(row, "the crop was not active at export ({0}), so "
                                                 "the image extent is not the crop".format(crop_active)))
                    continue
                if export_vs_crop.get("matches") is not True:
                    rows.append(_unmeasured(row, "the export is not the crop ({0}), so a window "
                                                 "placed through the crop would miss; the marks "
                                                 "changed {1} px somewhere in the image".format(
                                                     export_vs_crop.get("reason"), changed_anywhere)))
                    continue
                if not mark.get("uv0") or not mark.get("uv1"):
                    rows.append(_unmeasured(row, "the mark's UV is not recorded"))
                    continue
                h, w = marked.shape[:2]
                window, reason = mark_window(mark["uv0"], mark["uv1"], crop_uv, w, h)
                if window is None:
                    rows.append(_unmeasured(row, reason))
                    continue
                x0, y0, x1, y1 = window
                cut = marked[y0:y1, x0:x1]
                packed = pack(cut)
                counts = {"non_white": int((packed != WHITE).sum()),
                          "mark_colour": (int((packed == ((int(colour[0]) << 16) | (int(colour[1]) << 8)
                                                          | int(colour[2]))).sum())
                                          if colour else None),
                          "changed_vs_unmarked": None}
                if twin_same:
                    counts["changed_vs_unmarked"] = int(
                        np.any(twin[y0:y1, x0:x1] != cut, axis=2).sum())
                rendered, basis = rendered_verdict(counts, twin_same, bool(colour))
                row.update(window_px=window, non_white_px=counts["non_white"],
                           mark_colour_px=counts["mark_colour"],
                           changed_vs_unmarked_px=counts["changed_vs_unmarked"],
                           rendered=rendered, rendered_basis=basis,
                           unmeasured_reason=basis if rendered == "unmeasured" else None)
                rows.append(row)
    return rows


def q6_authored_rows(report, pixels):
    """B: per view, the pixels S8 changes against S6 (Q6_AUTHORED_PAIRS).
    0 means unhiding OST_Lines with every line hidden one by one left the
    view as authored. ``changed_px`` None, with the reason, when either
    export is missing or the two differ in size."""
    rows = []
    for view in _views(report, "q6"):
        steps = dict((s.get("step"), s) for s in view.get("steps") or [])
        for name, twin_name in sorted(Q6_AUTHORED_PAIRS.items()):
            if name not in steps:
                continue
            step, twin = steps.get(name) or {}, steps.get(twin_name) or {}
            row = {"view_id": view.get("view_id"), "step": name, "twin": twin_name,
                   "writes": dict((k, (w or {}).get("took_effect"))
                                  for k, w in (step.get("writes") or {}).items()
                                  if isinstance(w, dict)),
                   "changed_px": None, "reason": None}
            a = pixels.get((step.get("export") or {}).get("file"))
            b = pixels.get((twin.get("export") or {}).get("file"))
            if step.get("refused") or twin.get("refused"):
                row["reason"] = "refused: {0}".format(step.get("refused") or twin.get("refused"))
            elif a is None or b is None:
                row["reason"] = "an export is missing or failed"
            elif a.shape != b.shape:
                row["reason"] = "the exports differ in size ({0} vs {1})".format(
                    list(a.shape[:2]), list(b.shape[:2]))
            else:
                row["changed_px"] = int(np.any(a != b, axis=2).sum())
            rows.append(row)
    return rows


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
    images_by_file = dict((i["file"], i) for i in images)
    pairs_by_file = dict((p["file"], p) for p in pairs)
    writes, silent = writes_section(report)
    return {"status": "value", "probe_version": (report.get("probe") or {}).get("version"),
            "images": images, "pairs_vs_s0": pairs,
            "failed_exports": failed, "q3": q3_rows(report, measured),
            "writes": writes, "commit_without_effect": silent,
            "q1b": q1b_section(report),
            "q3b": q3b_section(report, images_by_file, pairs_by_file),
            "q5b": q5b_section(report),
            "q6": q6_rows(report, pixels, images_by_file),
            "q6_authored": q6_authored_rows(report, pixels),
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
    _print_round2(record)
    if record.get("failed_exports"):
        print()
        print("failed exports: {0}".format(", ".join(
            str(f["file"]) for f in record["failed_exports"])))
    print()
    print("probe verify: {0}".format(record.get("verify_summary")))


def _short(v):
    if is_unavailable(v):
        return "n/a"
    return "-" if v is None else str(v)


def _print_round2(record):
    silent = record.get("commit_without_effect") or []
    if record.get("writes"):
        print()
        print("writes: {0}; committed WITHOUT effect: {1}".format(len(record["writes"]), len(silent)))
        for w in silent:
            print("  {0}".format(w["path"]))
    for v in record.get("q1b") or []:
        print()
        print("Q1b view {0}: identity write {1}; +{2} / -{3} members; {4} parameter(s) "
              "changed".format(v["view_id"], v.get("identity_write"), v.get("added_count"),
                               v.get("removed_count"), len(v.get("parameters_changed") or [])
                               if isinstance(v.get("parameters_changed"), list) else "n/a"))
        for key, w in sorted((v.get("watch_ids") or {}).items()):
            if isinstance(w, dict) and "in_before" in w:
                print("  watch {0}: before {1}, after {2}".format(key, w["in_before"], w["in_after"]))
    for v in record.get("q3b") or []:
        print()
        print("Q3b view {0} ({1})".format(v["view_id"], v["role"]))
        for row in v["rows"]:
            print("  {0:<3} {1:<28} scope@export {2:<10} px {3:<11} non-white vs S0 {4:<8} {5}".format(
                row["step"], row.get("refused") or "",
                _short(row["scope_box_at_export"]), _fmt(row["exported_px"]),
                _short(row["non_white_vs_s0"]),
                " ".join("{0}:took_effect={1}".format(k, w.get("took_effect"))
                         for k, w in row["writes"].items() if isinstance(w, dict))))
    for v in record.get("q5b") or []:
        print()
        print("Q5b view {0} (primary {1}){2}".format(
            v["view_id"], v["primary_view_id"], "  REFUSED: " + v["refused"] if v.get("refused") else ""))
        for name, variant in sorted(v["variants"].items()):
            print("  {0}: {1}".format(name, variant))
    if record.get("q6"):
        print()
        print("Q6  {0:<9} {1:<4} {2:<15} {3:>6} {4:>6} {5:>6} {6:>6} {7:<11} {8}".format(
            "view", "step", "mark", "lines", "subcat", "elem", "in_vw", "rendered", "basis / reason"))
        for r in record["q6"]:
            if r.get("mark_id") is None:
                print("    {0:<9} {1:<4} refused: {2}".format(r["view_id"], r["step"],
                                                              r.get("refused_reason") or r.get("refused")))
                continue
            print("    {0:<9} {1:<4} {2:<15} {3:>6} {4:>6} {5:>6} {6:>6} {7:<11} {8}".format(
                r["view_id"], r["step"], str(r.get("key"))[:15], _short(r["lines_category_hidden"]),
                _short(r["line_style_subcategory_hidden"]), _short(r["element_hidden"]),
                _short(r["in_view_collector"]), str(r["rendered"]),
                r.get("rendered_basis") or r.get("unmeasured_reason")))
    for r in record.get("q6_authored") or []:
        print("Q6b {0:<9} {1} vs {2}: changed px {3}  {4}  writes {5}".format(
            r["view_id"], r["step"], r["twin"], _fmt(r["changed_px"]),
            r.get("reason") or "", r["writes"]))


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
