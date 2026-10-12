#!/usr/bin/env python3
"""Registration-tick census: one row per captured view across Stage A runs.

Read-only. It never registers, never grids, and never writes into a capture
folder. For every model sidecar a run holds (``stage_a_grid.model_sidecars``,
the grid's own selector) it reports, side by side, what the capture RECORDED
about its registration ticks and what the exported images CONTAIN -- so a
view whose sidecar says "12 marks painted, readback deviation 0.0 ft" and
whose images hold no tick pixel at all is one row, not two stories.

Per view (``COLUMNS``):

* identity, joined from the run's views_core_*.csv the way the roll-up joins
  it (``stage_a_grid_rollup.load_views_core`` / ``views_core_columns``);
* ``registration_state`` and the fits' ``found_count`` from
  ``<view>_anno.registered.json`` (``stage_a_grid_rollup.registration_state``);
* the view template, line style and lines-unhidden records the capture wrote
  (``registration_marks.line_style`` / ``lines_unhidden``), the marks'
  placement counts and readback deviation;
* the crop as recorded: active, source, written, split state, extent in ft
  and the export's pixel size. The capture records NO crop-shape field (no
  reader of ``CropRegionShapeManager.ShapeSet`` exists in
  ``vop_interwoven/``), so the crop shape is measured from the pixels below;
* pixel measures, each read from the named TIFF: exact tick-colour pixels in
  both captures (the colours THAT capture's own registration_marks record
  names -- one shared colour, or one per tick in the older annotation
  shape, read by ``registration_marks.mark_colours``), palette pixels and
  assigned colours present in the annotation capture, how many of the 12
  recorded tick windows (the tick's bbox on the NOMINAL crop mapping,
  padded) hold no ink at all and how many fall off the image, and the
  minimum-area rectangle of the annotation capture's ink -- its angle to the
  image axes and the share of the image it covers. An axis-aligned
  rectangular crop gives an angle near 0; a crop region edited into a rotated
  rectangle gives the rotation (2025-06-21 view 3939979: 12.91 deg, every
  tick window white, 0 of 12 found).

THE RENDERED REGION (2026-10-12 follow-up, run 2025-06-21: 41 views whose
crop box is the bounding box of a rectangle rotated about 12.9 deg).
``content_tilt_deg`` is the angle of the minimum-area rectangle enclosing the
MODEL capture's ink; ``content_tilt_union_deg`` the same over the union of
both captures' ink when the two TIFFs share a pixel size (both are sized from
crop A), which is the denser evidence: 3939979's model ink alone, a few
property lines, gives 61.13 deg where the union gives 12.91 deg.
``content_tilt_edge_deg`` is the length-weighted mode of the union hull's
long edges (``edge_tilt``), which follows the crop's straight boundary rather
than the hull's overall shape, and is the tilt the polygon prediction uses
when it exists (``polygon_tilt_source`` ends in ``_edges``).
``aabb_corner_white`` says, per image corner, whether the 64 x 64 px window
the corner ticks occupy (inset 24 + gap 8 + arm 32) holds no ink in either
capture. ``ticks_inside_polygon_predicted`` counts the recorded ticks (moved
positions, from the sidecar) whose both endpoints lie inside the rectangle
inscribed in the image at the measured tilt (``inscribed_polygon``; the
whole image when the tilt is 0.00); ``ticks_found_*`` are the ticks the fit
located, from ``<view>_anno.registered.json``; ``ticks_predicted_vs_found_*``
is the per-tick confusion between the two (tp / fp / fn / tn).

PROVENANCE. A sidecar in a run's folder is not always that run's capture:
run_meta.json records a failed capture while the previous capture's files
remain, and a sidecar may carry a view the run never requested. Each row
says which (``provenance``: ``this_run``, ``not_this_run``,
``not_requested``, ``run_meta_refused``), read from run_meta.json the way the
roll-up reads it (``stage_a_grid_rollup.run_identity``); the partitions count
``this_run`` rows only. A registration record is used only when the four
source hashes it names (both sidecars, both TIFFs) still match the files on
disk; a hash that does NOT match makes its state ``stale``, its reason names
the mismatch, and its fit counts stay blank rather than being set beside
pixels they were not fitted on. A source file that is ABSENT (an archive
shipped without its TIFFs) is a different fact: the record is kept, its fit
counts are reported, and ``registration_sources_verified`` is false with the
absent file named in ``notes``.

ABSENCE is named, never blank: ``notes`` lists each file that was looked for
and not found, by name, and every column that file would have filled stays
empty. The summary JSON carries the partitions (registration_state by
view_type, scale, template, line style, split state and tick-window
evidence); it never fits a model.

Usage::

    python tools/census_registration.py <run> [<run> ...] --out <dir>
    python tools/census_registration.py <run> --no-pixels      # sidecars only
"""

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import decode_stage_a_color_id as dsc  # noqa: E402
from tools import registration_marks as rm  # noqa: E402
from tools import stage_a_grid as grid  # noqa: E402
from tools import stage_a_grid_rollup as rollup  # noqa: E402
from tools.stage_a_sidecar_shapes import frame_record  # noqa: E402
from vop_interwoven.stage_a_registration import MARK_COLOUR  # noqa: E402

SCHEMA = "vop.stage_a.census_registration.v1"
TOOL_VERSION = "1.0.0"
CSV_NAME = "census_registration.csv"
SUMMARY_NAME = "census_registration.summary.json"

# A tick window is the tick's pixel bbox on the nominal mapping, grown by this
# many pixels on every side, so a tick drawn a pixel off its predicted row is
# still inside it. 3 px is wider than any fit residual registered so far
# (at most 0.39 px, RUN_pipeline_0928_0953_REGISTRATION.md).
TICK_WINDOW_PAD_PX = 3

COLUMNS = [
    # identity
    "run_id", "view_stem", "view_id", "view_name", "view_type", "scale",
    "is_on_sheet", "views_core_state",
    # provenance: is this sidecar the run's own capture of the view?
    "provenance", "run_meta_capture_status",
    # registration
    "registration_state", "registration_reason", "registration_sources_verified",
    "anno_found_count", "anno_expected_count", "model_found_count",
    "model_expected_count",
    # the capture's own records
    "orig_view_template_id", "template_source",
    "line_style_id", "line_style_name", "line_style_path",
    "line_style_weight", "line_style_hidden_n", "line_style_hidden_names",
    "line_style_subcategory_hidden_in_view",
    "lines_unhidden_state", "lines_category_hidden_before",
    "marks_expected", "marks_painted", "marks_original", "marks_moved",
    "marks_blocked", "readback_max_deviation_ft", "capture_faults_n",
    # the crop as recorded
    "authored_crop_active", "crop_source", "crop_written", "reference_source",
    "split_crop_state", "crop_uv_w_ft", "crop_uv_h_ft", "crop_uv_aspect",
    "image_w", "image_h", "cap_applied", "frame_predicted_derived_px",
    "frame_actual_derived_px",
    # the images
    "model_tick_px", "anno_tick_px", "anno_palette_px", "anno_assigned_colours",
    "anno_colours_present", "anno_nonwhite_px", "model_nonwhite_px",
    "anno_tick_windows_white", "model_tick_windows_white",
    "anno_tick_windows_off_image", "model_tick_windows_off_image",
    "anno_ink_rect_angle_deg", "anno_ink_rect_fill",
    # the rendered region (2026-10-12 follow-up): tilt, corners, prediction
    "content_tilt_deg", "content_tilt_union_deg",
    "content_tilt_edge_deg", "content_tilt_edge_support", "polygon_tilt_source",
    "aabb_corner_white",
    "ticks_inside_polygon_predicted", "ticks_inside_polygon_names",
    "ticks_found_anno", "ticks_found_model",
    "ticks_predicted_vs_found_anno", "ticks_predicted_vs_found_model",
    # absences, by file
    "notes",
]


def _err(ex):
    return "{0}: {1}".format(type(ex).__name__, ex)


def _load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _hull(points):
    """Andrew's monotone chain over ``points`` (an (n, 2) float array)."""
    pts = sorted(set((float(x), float(y)) for x, y in points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def ink_rectangle(nonwhite):
    """``(angle_deg, fill)`` of the minimum-area rectangle enclosing the True
    pixels of ``nonwhite``: its angle to the image axes in [0, 90) and its
    area over the image's. ``(None, None)`` with fewer than three ink pixels.
    The hull is built from each row's extreme pixels, which generate the same
    convex hull as every ink pixel would."""
    rows = np.nonzero(nonwhite.any(axis=1))[0]
    if len(rows) == 0:
        return None, None
    pts = []
    for r in rows:
        cols = np.nonzero(nonwhite[r])[0]
        pts.append((cols[0], r))
        pts.append((cols[-1] + 1, r + 1))
        pts.append((cols[0], r + 1))
        pts.append((cols[-1] + 1, r))
    hull = np.array(_hull(np.array(pts, dtype=float)), dtype=float)
    if len(hull) < 3:
        return None, None
    best = None
    n = len(hull)
    for i in range(n):
        a, b = hull[i], hull[(i + 1) % n]
        theta = math.atan2(b[1] - a[1], b[0] - a[0])
        c, s = math.cos(-theta), math.sin(-theta)
        q = hull @ np.array([[c, -s], [s, c]]).T
        area = float(np.ptp(q[:, 0]) * np.ptp(q[:, 1]))
        if best is None or area < best[0]:
            best = (area, math.degrees(theta) % 90.0)
    h, w = nonwhite.shape
    return round(best[1], 2), round(best[0] / float(w * h), 4)


def tick_windows_white(nonwhite, marks, mapping):
    """How many of ``marks`` have NO ink in their window: the tick's bbox on
    ``mapping`` (pixel = a * uv + b), padded TICK_WINDOW_PAD_PX, as
    ``(white, off_image)``. A window that falls entirely off the image is
    counted in BOTH: it holds no ink, and it is not in-image evidence; the
    second count is reported beside the first so the two are never read as
    one."""
    h, w = nonwhite.shape
    white, off_image = 0, 0
    for m in marks:
        (ua, va), (ub, vb) = m["uv0"], m["uv1"]
        xs = [mapping["a_u"] * u + mapping["b_u"] for u in (ua, ub)]
        ys = [mapping["a_v"] * v + mapping["b_v"] for v in (va, vb)]
        x0 = int(math.floor(min(xs))) - TICK_WINDOW_PAD_PX
        x1 = int(math.ceil(max(xs))) + TICK_WINDOW_PAD_PX
        y0 = int(math.floor(min(ys))) - TICK_WINDOW_PAD_PX
        y1 = int(math.ceil(max(ys))) + TICK_WINDOW_PAD_PX
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w, x1), min(h, y1)
        if x1 <= x0 or y1 <= y0:
            off_image += 1
            white += 1
            continue
        if not nonwhite[y0:y1, x0:x1].any():
            white += 1
    return white, off_image


# The corner ticks occupy pixels 24..64 from each image edge (MARK_INSET_PX
# 24, MARK_GAP_PX 8, MARK_ARM_PX 32 in vop_interwoven/stage_a_registration.py);
# a corner window of this side holds them whole.
CORNER_WINDOW_PX = 64


def _hull_points(nonwhite):
    rows = np.nonzero(nonwhite.any(axis=1))[0]
    pts = []
    for r in rows:
        cols = np.nonzero(nonwhite[r])[0]
        pts.extend([(cols[0], r), (cols[-1] + 1, r + 1), (cols[0], r + 1), (cols[-1] + 1, r)])
    return _hull(np.array(pts, dtype=float)) if pts else []


# A hull edge counts toward the tilt vote when it is at least this fraction
# of the image's shorter side: the rotated crop's own boundary shows as edges
# of 0.5 to 1.7 times that length on the 06-21 site plans, while the pieces
# of a sparse plan's hull are far shorter (5576090: longest 0.17, no vote
# worth taking).
EDGE_VOTE_MIN_FRACTION = 0.10
EDGE_VOTE_BIN_DEG = 0.5


def edge_tilt(nonwhite):
    """``(tilt_deg, support)``: the length-weighted mode of the ink hull's
    long edges' angles, folded modulo 90 into (-45, 45], and the total length
    voting for it as a fraction of the shorter image side. The minimum-area
    rectangle (``ink_rectangle``) follows the hull's OVERALL shape, which on a
    sparse or L-shaped plan is not the crop's: 06-21 view 4150746 registered
    12 of 12 with a min-area angle of 9.25 deg while its long hull edges lie
    at 0 deg; 4610688 refused 0 of 12 with a min-area angle of 0.68 deg while
    its long edges lie at 13 deg, the family's crop angle. ``(None, 0.0)``
    with no edge long enough to vote."""
    h, w = nonwhite.shape
    hull = _hull_points(nonwhite)
    votes = {}
    n = len(hull)
    for i in range(n):
        (x1, y1), (x2, y2) = hull[i], hull[(i + 1) % n]
        length = math.hypot(x2 - x1, y2 - y1)
        if length < EDGE_VOTE_MIN_FRACTION * min(w, h):
            continue
        angle = math.degrees(math.atan2(y2 - y1, x2 - x1)) % 90.0
        key = (round(angle / EDGE_VOTE_BIN_DEG) * EDGE_VOTE_BIN_DEG) % 90.0
        votes[key] = votes.get(key, 0.0) + length
    if not votes:
        return None, 0.0
    angle, length = max(votes.items(), key=lambda kv: kv[1])
    return (angle if angle <= 45.0 else angle - 90.0), round(length / min(w, h), 2)


def inscribed_polygon(width, height, tilt_deg, sign=1):
    """PURE. The rectangle whose axis-aligned bounding box is ``width`` x
    ``height`` and whose sides lie at ``tilt_deg`` to the axes, as four
    corners (x, y) in pixel coordinates, centred in the box. ``sign`` picks
    the rotation direction. At tilt 0 it is the box itself. A tilt of 45 deg
    or more is folded to its complement (the sides are interchangeable)."""
    t = float(tilt_deg) % 90.0
    if t > 45.0:
        t = 90.0 - t
        sign = -sign
    th = math.radians(t)
    c, s = math.cos(th), math.sin(th)
    c2 = math.cos(2.0 * th)
    w = (width * c - height * s) / c2
    h = (height * c - width * s) / c2
    cx, cy = width / 2.0, height / 2.0
    out = []
    for dx, dy in ((-w / 2.0, -h / 2.0), (w / 2.0, -h / 2.0), (w / 2.0, h / 2.0),
                   (-w / 2.0, h / 2.0)):
        out.append((cx + dx * c - sign * dy * s, cy + sign * dx * s + dy * c))
    return out


def point_in_polygon(poly, x, y):
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xi:
                inside = not inside
    return inside


def rendered_polygon(nonwhite, tilt_deg):
    """The rectangle inscribed in the image at ``tilt_deg``, its rotation
    direction chosen as the one that holds more of the ink hull's vertices.
    The whole image at tilt 0."""
    h, w = nonwhite.shape
    if not tilt_deg:
        return [(0.0, 0.0), (float(w), 0.0), (float(w), float(h)), (0.0, float(h))]
    hull = _hull_points(nonwhite)
    best = None
    for sign in (1, -1):
        poly = inscribed_polygon(w, h, tilt_deg, sign)
        n = sum(1 for x, y in hull if point_in_polygon(poly, x, y))
        if best is None or n > best[0]:
            best = (n, poly)
    return best[1]


def corner_windows_white(nonwhite, side=CORNER_WINDOW_PX):
    h, w = nonwhite.shape
    out = {}
    for name, (r0, c0) in (("TL", (0, 0)), ("TR", (0, w - side)),
                           ("BL", (h - side, 0)), ("BR", (h - side, w - side))):
        out[name] = not nonwhite[max(0, r0):r0 + side, max(0, c0):c0 + side].any()
    return out


def ticks_inside(poly, marks, mapping):
    """The keys of the recorded ticks whose both endpoints lie inside ``poly``
    on ``mapping``."""
    inside = []
    for m in marks:
        ends = [(mapping["a_u"] * u + mapping["b_u"], mapping["a_v"] * v + mapping["b_v"])
                for u, v in (m["uv0"], m["uv1"])]
        if all(point_in_polygon(poly, x, y) for x, y in ends):
            inside.append(m["key"])
    return inside


def found_ticks(fit, marks):
    """The keys the fit located: every recorded tick not in its ``missing``
    list. None when the fit record carries no missing list."""
    if not isinstance(fit, dict) or not isinstance(fit.get("missing"), list):
        return None
    missing = set(str(m.get("key")) for m in fit["missing"] if isinstance(m, dict))
    return [m["key"] for m in marks if m["key"] not in missing]


def confusion(predicted, found, keys):
    p, f = set(predicted), set(found)
    return "tp={0},fp={1},fn={2},tn={3}".format(
        sum(1 for k in keys if k in p and k in f), sum(1 for k in keys if k in p and k not in f),
        sum(1 for k in keys if k not in p and k in f),
        sum(1 for k in keys if k not in p and k not in f))


def rendered_region_columns(masks, marks, crop, fits):
    """The tilt, corner, prediction and found columns, from the nonwhite
    masks of the captures read (``masks[which]``, ticks excluded), the
    recorded marks, crop A and the registration record's fits. Returns
    ``(columns, notes)``; every blank is explained in ``notes``."""
    cols, notes = {}, []
    model = masks.get("model")
    if model is None:
        notes.append("content_tilt_deg: the model TIFF was not read")
        return cols, notes
    angle, _fill = ink_rectangle(model)
    if angle is None:
        notes.append("content_tilt_deg: the model TIFF holds no ink to measure")
    cols["content_tilt_deg"] = angle
    anno = masks.get("anno")
    union, source = model, "model"
    if anno is not None and anno.shape == model.shape:
        union = model | anno
        cols["content_tilt_union_deg"], _fill = ink_rectangle(union)
        if cols["content_tilt_union_deg"] is not None:
            source = "union"
    elif anno is not None:
        notes.append("content_tilt_union_deg: the annotation TIFF is {0}x{1}, the model "
                     "TIFF {2}x{3}; not on one lattice".format(
                         anno.shape[1], anno.shape[0], model.shape[1], model.shape[0]))
    tilt = cols.get("content_tilt_union_deg") if source == "union" else angle
    edge, support = edge_tilt(union)
    cols["content_tilt_edge_deg"], cols["content_tilt_edge_support"] = edge, support
    if edge is not None:
        tilt, source = edge, source + "_edges"
    cols["polygon_tilt_source"] = source if tilt is not None else None
    corners = corner_windows_white(union)
    cols["aabb_corner_white"] = "|".join("{0}:{1}".format(k, "true" if v else "false")
                                         for k, v in corners.items())
    if tilt is None:
        notes.append("ticks_inside_polygon_predicted: no tilt measured")
        return cols, notes
    if not marks or not crop:
        notes.append("ticks_inside_polygon_predicted: no recorded marks or crop to place")
        return cols, notes
    h, w = model.shape
    poly = rendered_polygon(union, tilt)
    mapping = grid.nominal_mapping(crop, w, h)
    predicted = ticks_inside(poly, marks, mapping)
    keys = [m["key"] for m in marks]
    cols["ticks_inside_polygon_predicted"] = len(predicted)
    # A measured empty list is "none", never a blank (a blank is unmeasured).
    cols["ticks_inside_polygon_names"] = "|".join(predicted) or "none"
    for which in ("anno", "model"):
        found = found_ticks((fits or {}).get(which), marks)
        if found is not None:
            cols["ticks_predicted_vs_found_" + which] = confusion(predicted, found, keys)
    return cols, notes


def found_tick_columns(fits, marks, notes):
    """``ticks_found_anno`` / ``ticks_found_model`` from the registration
    record alone (no image needed); a fit that cannot be read is noted."""
    cols = {}
    for which in ("anno", "model"):
        found = found_ticks((fits or {}).get(which), marks) if marks else None
        if found is None:
            notes.append("ticks_found_{0}: no usable {1}_fit in the registration "
                         "record".format(which, "annotation" if which == "anno" else "model"))
            continue
        cols["ticks_found_" + which] = "|".join(found) or "none"
    return cols


def _pack(rgb):
    return (rgb[..., 0].astype(np.uint32) << 16) | (rgb[..., 1].astype(np.uint32) << 8) \
        | rgb[..., 2].astype(np.uint32)


def _exact_count(packed, rgb):
    r, g, b = (int(c) for c in rgb)
    return int(np.count_nonzero(packed == ((r << 16) | (g << 8) | b)))


def tick_colours(sidecar):
    """``(colours, note)``: the tick colours THIS capture's registration_marks
    record names, as ``registration_marks.mark_colours`` reads them -- the one
    shared colour (every model pass; annotation passes since the reserved
    tick colour, 2026-10-02), or one colour per tick (the older annotation
    shape). With no usable record the reserved MARK_COLOUR is assumed and
    the note says so."""
    payload, refusal = rm.recorded_marks(sidecar)
    if payload is not None:
        by_id, shared, refusal = rm.mark_colours(payload)
        if shared is not None:
            return [tuple(shared)], None
        if by_id:
            return sorted(set(by_id.values())), None
    return [tuple(MARK_COLOUR)], ("tick colours not recorded ({0}); MARK_COLOUR "
                                  "assumed".format(refusal or "no registration_marks"))


def image_columns(tiff_path, which, sidecar, excluded_ids, marks, crop):
    """The pixel columns for one capture, from the named TIFF. ``which`` is
    "anno" or "model"; ``sidecar`` is that pass's own record (its tick
    colours, its colour map). The tick windows are placed on the nominal
    mapping of ``crop`` onto THIS image's own pixel size (the annotation
    capture is sized from the same crop A as the model's; its own dimensions
    are used). Returns ``(columns, note, ink)``, ``ink`` the non-white,
    non-tick pixel mask for the rendered-region columns."""
    rgb = dsc._load_rgb_array(tiff_path)
    mapping = None
    if crop and marks:
        mapping = grid.nominal_mapping(crop, rgb.shape[1], rgb.shape[0])
    packed = _pack(rgb)
    colours, note = tick_colours(sidecar)
    keys = np.array([(int(c[0]) << 16) | (int(c[1]) << 8) | int(c[2]) for c in colours],
                    dtype=np.uint32)
    tick = np.isin(packed, keys)
    nonwhite = packed != 0xFFFFFF
    cols = {which + "_tick_px": int(np.count_nonzero(tick)),
            which + "_nonwhite_px": int(np.count_nonzero(nonwhite))}
    if marks and mapping:
        white, off_image = tick_windows_white(nonwhite, marks, mapping)
        cols[which + "_tick_windows_white"] = white
        cols[which + "_tick_windows_off_image"] = off_image
    colour_map = sidecar.get("color_assignment_map")
    if which == "anno":
        excluded = set(int(e) for e in excluded_ids or ())
        keys = [(int(c[0]) << 16) | (int(c[1]) << 8) | int(c[2])
                for eid, c in (colour_map or {}).items() if int(eid) not in excluded]
        cols["anno_assigned_colours"] = len(keys)
        if keys:
            present = np.isin(packed, np.array(keys, dtype=np.uint32))
            cols["anno_palette_px"] = int(np.count_nonzero(present))
            cols["anno_colours_present"] = int(len(set(np.unique(packed[present]).tolist())))
        else:
            cols["anno_palette_px"], cols["anno_colours_present"] = 0, 0
        angle, fill = ink_rectangle(nonwhite & ~tick)
        cols["anno_ink_rect_angle_deg"], cols["anno_ink_rect_fill"] = angle, fill
    return cols, note, nonwhite & ~tick


def marks_columns(model_sidecar):
    """The capture's own registration_marks record, from the MODEL sidecar
    (both passes carry the same marks; the model pass draws them)."""
    rmk = model_sidecar.get("registration_marks")
    cols = {}
    if not isinstance(rmk, dict):
        return cols, None
    style = rmk.get("line_style") or {}
    hidden = style.get("hidden") or []
    cols.update(line_style_id=style.get("id"), line_style_name=style.get("name"),
                line_style_path=style.get("path"),
                line_style_weight=style.get("projection_line_weight"),
                line_style_hidden_n=len(hidden),
                line_style_hidden_names="|".join(str(h.get("name")) for h in hidden),
                line_style_subcategory_hidden_in_view=style.get("subcategory_hidden_in_view"))
    unhidden = rmk.get("lines_unhidden") or {}
    cols.update(lines_unhidden_state=unhidden.get("state"),
                lines_category_hidden_before=unhidden.get("lines_category_hidden_before"))
    marks = rmk.get("marks") or []
    layout = rmk.get("layout") or {}
    cols.update(marks_expected=len(marks),
                marks_painted=sum(1 for m in marks if m.get("painted") is True),
                marks_original=sum(1 for m in marks if m.get("placement") == "original"),
                marks_moved=sum(1 for m in marks if m.get("placement") == "moved"),
                marks_blocked=sum(1 for m in marks if m.get("placement") == "blocked"),
                readback_max_deviation_ft=max(
                    [m.get("readback_max_deviation_ft") for m in marks
                     if m.get("readback_max_deviation_ft") is not None] or [None]),
                capture_faults_n=len(rmk.get("faults") or []),
                reference_source=rmk.get("reference_source"),
                split_crop_state=(rmk.get("split_crop") or {}).get("state"))
    del layout
    return cols, marks


def crop_columns(model_sidecar):
    frame = frame_record(model_sidecar)
    crop = frame.get("crop_uv")
    write = frame.get("crop_write") or {}
    cols = {"authored_crop_active": write.get("authored_crop_active"),
            "crop_source": write.get("source"), "crop_written": write.get("written"),
            "image_w": frame.get("actual_w"), "image_h": frame.get("actual_h"),
            "cap_applied": frame.get("cap_applied"),
            "frame_predicted_derived_px": frame.get("predicted_derived_px")}
    if cols["image_w"] is not None and cols["image_h"] is not None:
        cols["frame_actual_derived_px"] = (cols["image_h"]
                                           if frame.get("fit_direction") != "vertical"
                                           else cols["image_w"])
    if crop and len(crop) == 4:
        w, h = float(crop[2]) - float(crop[0]), float(crop[3]) - float(crop[1])
        cols.update(crop_uv_w_ft=round(w, 3), crop_uv_h_ft=round(h, 3),
                    crop_uv_aspect=round(w / h, 4) if h else None)
    return cols, crop, frame


def registration_staleness(registered, model_path, model, anno_path, anno):
    """``(stale, unverified)``: why a registration record no longer describes
    the files beside it -- each of the four source hashes it names
    (``source_*_sha256``) that does NOT match the file on disk now, or that
    the record does not name -- and, separately, each source whose file is
    absent or unreadable, which proves nothing either way. Both empty when
    every hash matches."""
    reasons, unverified = [], []
    sources = [("source_model_sidecar_sha256", "model sidecar", lambda: model_path),
               ("source_annotation_sidecar_sha256", "annotation sidecar",
                lambda: anno_path)]
    if model is not None:
        sources.append(("source_model_tiff_sha256", "model TIFF",
                        lambda: dsc._resolve_tiff_path(Path(model_path), model)))
    if anno is not None:
        sources.append(("source_annotation_tiff_sha256", "annotation TIFF",
                        lambda: dsc._resolve_tiff_path(Path(anno_path), anno)))
    for key, what, resolve in sources:
        recorded = registered.get(key)
        if not recorded:
            reasons.append("the registration record names no {0} for the {1}".format(
                key, what))
            continue
        try:
            now = grid.sha256_file(resolve())
        except (OSError, ValueError, FileNotFoundError) as ex:
            unverified.append("the {0} cannot be hashed now ({1})".format(what, _err(ex)))
            continue
        if now != recorded:
            reasons.append("{0} does not match the {1} on disk".format(key, what))
    return reasons, unverified


def provenance(ident, view_id):
    """``(provenance, capture_status)`` for a sidecar's view under the run's
    run_meta.json identity (``stage_a_grid_rollup.run_identity``)."""
    if ident.get("refused"):
        return "run_meta_refused", None
    if view_id is None or view_id not in ident["outcomes"]:
        return "not_requested", None
    outcome = ident["outcomes"].get(view_id)
    if outcome is None:
        return "not_this_run", "ambiguous"
    status = outcome.get("capture_status")
    return ("this_run" if status == "success" else "not_this_run"), status


def view_row(model_sidecar_path, run_id, views_core, pixels=True, ident=None):
    """One row. Every key of COLUMNS is present; ``notes`` names each file
    that was looked for and not found. ``ident`` is the run's identity
    (``stage_a_grid_rollup.run_identity``), for the row's provenance."""
    model_sidecar_path = Path(model_sidecar_path)
    stem = model_sidecar_path.stem
    row = dict((c, None) for c in COLUMNS)
    row.update(run_id=run_id, view_stem=stem)
    notes = []
    try:
        model = _load_json(model_sidecar_path)
    except (OSError, ValueError) as ex:
        row["notes"] = "model sidecar {0} unreadable: {1}".format(
            model_sidecar_path.name, _err(ex))
        return row
    row["view_id"] = model.get("view_id")
    state, by_id = views_core
    row.update(rollup.views_core_columns(state, by_id, row["view_id"]))
    row["provenance"], row["run_meta_capture_status"] = provenance(
        ident if ident is not None else {"refused": "no run identity"},
        rollup._vid(row["view_id"]) if row["view_id"] is not None else None)

    anno_path = model_sidecar_path.with_name(stem + "_anno.json")
    anno = None
    if anno_path.exists():
        try:
            anno = _load_json(anno_path)
        except (OSError, ValueError) as ex:
            notes.append("{0} unreadable: {1}".format(anno_path.name, _err(ex)))
    else:
        notes.append("no {0}".format(anno_path.name))
    if anno is not None and anno.get("orig_view_template_id") is not None:
        row["orig_view_template_id"] = anno.get("orig_view_template_id")
        row["template_source"] = anno_path.name
    elif model.get("orig_view_template_id") is not None:
        row["orig_view_template_id"] = model.get("orig_view_template_id")
        row["template_source"] = model_sidecar_path.name

    reg_path = rollup.registration_record_path(model_sidecar_path)
    row["registration_state"], row["registration_reason"] = rollup.registration_state(reg_path)
    registered = None
    if reg_path.exists():
        try:
            registered = _load_json(reg_path)
        except (OSError, ValueError) as ex:
            notes.append("{0} unreadable: {1}".format(reg_path.name, _err(ex)))
    else:
        notes.append("no {0}".format(reg_path.name))
    if registered is not None:
        stale, unverified = registration_staleness(registered, model_sidecar_path, model,
                                                   anno_path, anno)
        if stale:
            row["registration_state"] = "stale"
            row["registration_reason"] = "; ".join(stale)
            row["registration_sources_verified"] = False
            registered = None
        else:
            row["registration_sources_verified"] = not unverified
            for why in unverified:
                notes.append("registration sources: " + why)
    fits = {}
    if registered is not None:
        for name, prefix in (("annotation_fit", "anno"), ("model_fit", "model")):
            fit = registered.get(name)
            if isinstance(fit, dict):
                row[prefix + "_found_count"] = fit.get("found_count")
                row[prefix + "_expected_count"] = fit.get("expected_count")
                fits[prefix] = fit
            else:
                notes.append("{0} carries no {1}".format(reg_path.name, name))

    mcols, marks = marks_columns(model)
    row.update(mcols)
    if marks is None:
        notes.append("{0} carries no registration_marks".format(model_sidecar_path.name))
    ccols, crop, frame = crop_columns(model)
    row.update(ccols)
    if not crop:
        notes.append("{0} frame carries no crop_uv".format(model_sidecar_path.name))

    row.update(found_tick_columns(fits, marks, notes))
    if pixels:
        masks = {}
        for which, side, side_path in (("model", model, model_sidecar_path),
                                       ("anno", anno, anno_path)):
            if side is None:
                continue
            try:
                tiff = dsc._resolve_tiff_path(side_path, side)
            except Exception as ex:  # the resolver's own refusal, named
                notes.append("{0} TIFF not resolved: {1}".format(which, _err(ex)))
                continue
            if not Path(tiff).exists():
                notes.append("no {0}".format(Path(tiff).name))
                continue
            excluded = [m.get("id") for m in (marks or []) if m.get("id") is not None]
            try:
                cols, note, ink = image_columns(Path(tiff), which, side, excluded, marks, crop)
                row.update(cols)
                masks[which] = ink
                if note:
                    notes.append("{0}: {1}".format(which, note))
            except (OSError, ValueError) as ex:
                notes.append("{0} unreadable: {1}".format(Path(tiff).name, _err(ex)))
        cols, region_notes = rendered_region_columns(masks, marks, crop, fits)
        row.update(cols)
        notes.extend(region_notes)
    row["notes"] = "; ".join(notes)
    return row


def _views_core(folder):
    """The run's views_core join: looked for in the run directory, then in
    the capture folder itself (a hand-assembled folder keeps it beside the
    sidecars). Returns ``(state, by_id, where)``."""
    for where in (folder.parent, folder):
        state, _reason, by_id = rollup.load_views_core(where, None)
        if state != "absent":
            return state, by_id, where
    return "absent", {}, None


def census(run_args, pixels=True):
    rows, runs = [], []
    for run_arg in run_args:
        sidecars, folder = grid.model_sidecars(run_arg)
        folder = Path(folder)
        meta = dsc.find_run_meta(folder / "run_meta.json") or {}
        run_id = meta.get("run_id")
        ident = rollup.run_identity(meta)
        # views_core rows are keyed by RunId; with the run id known, join on it.
        state, by_id, where = _views_core(folder)
        if run_id is not None and where is not None:
            state, _reason, by_id = rollup.load_views_core(where, run_id)
        runs.append({"run": str(run_arg), "capture_dir": str(folder), "run_id": run_id,
                     "run_meta_found": bool(meta), "run_meta_refused": ident["refused"],
                     "model_sidecars": len(sidecars),
                     "views_core": (str(where) if where is not None else
                                    "absent: no views_core_*.csv in {0} or {1}".format(
                                        folder.parent, folder))})
        for path in sidecars:
            rows.append(view_row(path, run_id, (state, by_id), pixels=pixels, ident=ident))
    return runs, rows


def _partition(rows, key):
    out = {}
    for r in rows:
        k = "" if r.get(key) is None else str(r.get(key))
        out.setdefault(k, {})
        s = str(r.get("registration_state"))
        out[k][s] = out[k].get(s, 0) + 1
    return out


def summarise(runs, rows, csv_path):
    own = [r for r in rows if r.get("provenance") == "this_run"]
    partitions = dict((key, _partition(own, key)) for key in (
        "view_type", "scale", "orig_view_template_id", "line_style_id",
        "line_style_path", "split_crop_state", "crop_source",
        "anno_tick_windows_white", "model_tick_windows_white",
        "ticks_inside_polygon_predicted", "ticks_predicted_vs_found_model"))
    return {"schema": SCHEMA, "tool_version": TOOL_VERSION, "runs": runs,
            "denominator": len(rows),
            "denominator_rule": "one row per model sidecar stage_a_grid.model_sidecars "
                                "finds in each run",
            "provenance": _partition(rows, "provenance"),
            "registration_state": _partition(own, "registration_state"),
            "partitions": partitions,
            "partition_rule": "registration_state counts by each column's value, over "
                              "the rows whose provenance is this_run; a blank key is a "
                              "row the column could not be filled for (see its notes). "
                              "No model is fitted.",
            "csv": csv_path.name, "csv_sha256": grid.sha256_file(csv_path)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("runs", nargs="+", metavar="run",
                    help="a run directory or its color_id_buffer/")
    ap.add_argument("--out", required=True, help="output directory (never a capture folder)")
    ap.add_argument("--no-pixels", action="store_true",
                    help="skip every column read from a TIFF")
    args = ap.parse_args(argv)
    out = Path(args.out)
    for run_arg in args.runs:
        _s, folder = grid.model_sidecars(run_arg)
        if rollup._inside(out, folder):
            ap.error("--out {0} is inside the capture folder {1}".format(out, folder))
    runs, rows = census(args.runs, pixels=not args.no_pixels)
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / CSV_NAME
    with open(csv_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow(dict((k, rollup._cell(r.get(k))) for k in COLUMNS))
    summary = summarise(runs, rows, csv_path)
    with open(out / SUMMARY_NAME, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True, default=str)
    print("{0} rows; provenance: {1}; registration (this_run rows): {2}".format(
        len(rows),
        ", ".join("{0} {1}".format(k, sum(v.values()))
                  for k, v in sorted(summary["provenance"].items())),
        ", ".join("{0} {1}".format(k, sum(v.values()))
                  for k, v in sorted(summary["registration_state"].items()))))
    for key in ("view_type", "scale", "orig_view_template_id", "line_style_id",
                "split_crop_state", "anno_tick_windows_white"):
        print("  by {0}: {1}".format(key, json.dumps(summary["partitions"][key], sort_keys=True)))
    for r in rows:
        if r.get("notes"):
            print("  {0}: {1}".format(r["view_stem"], r["notes"]))
    print("wrote {0} and {1}".format(csv_path, out / SUMMARY_NAME))
    return 0 if rows else 2


if __name__ == "__main__":
    sys.exit(main())
