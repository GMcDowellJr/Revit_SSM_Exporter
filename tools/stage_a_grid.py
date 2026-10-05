#!/usr/bin/env python3
"""The analysis grid: which cell every pixel of a Stage A view falls in.

Design: docs/DESIGN_ANALYSIS_GRID.md (item 2 of the analysis layer). One
definition, used by every analysis-layer product:

  * CELL SIZE is the requested paper cell -- ``cell_size_paper_in`` from the
    run's run_meta.json config, times the view scale, over 12. Never adaptive,
    never guessed: missing either input is a refusal.
  * ORIGIN is crop A's lower-left corner (``frame.crop_uv``'s minimum). Cell
    (i, j) covers u in [u0 + i*cell, u0 + (i+1)*cell) and v likewise, with j
    running UP. Annotation ink left of or below crop A gets NEGATIVE indices
    -- expected, and kept signed.
  * EXTENT is the union of the model image and the registered annotation
    canvas (``<view>_anno.registered.json``), in whole cells. The annotation
    canvas is usually larger but need not be.
  * PIXEL -> UV is ONE mapping for both images (the registered annotation TIFF
    is already on the model lattice). It is whichever of the two available
    mappings lands closer to the measured registration ticks: the model
    capture's recorded crop (the nominal mapping, as the decoder uses) or the
    tick fit itself. Both residuals are recorded, and so is the choice.
  * A pixel belongs to the cell containing its CENTRE.
  * Resolution is judged per view from cell size against pixel count: fewer
    than MIN_PX_PER_CELL pixels per cell is refused, and a mapping uncertainty
    above COARSE_UNCERTAINTY_CELLS of a cell is flagged ``coarse``. A capped
    export is analysed and flagged ``capped``.

Outputs, per view, in ``--out`` (default ``<run>/analysis_grid/``):
``<view>.grid.npz`` (per-cell pixel counts by channel, ink counts, and the
``occupancy`` class per cell; row 0 is the LOWEST j), then ``<view>.grid.json``
naming the npz's hash -- the record is written last (CLAUDE.md, defect class
4) -- and ``<view>.grid.png``, the occupancy in vop_raster's colours.

OCCUPANCY (item 3): a cell is model and/or annotation by INK -- element pixels
with a differently coloured neighbour -- not by filled area. See
OCCUPANCY_MIN_INK_PX.

    python tools/stage_a_grid.py <run dir | color_id_buffer dir> [--out DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import decode_stage_a_color_id as dsc  # noqa: E402
from tools import registration_marks as rm  # noqa: E402
from tools.clamp_pad_geometry import clamp_pad_geometry  # noqa: E402
from tools.stage_a_sidecar_shapes import capture_integrity, frame_record  # noqa: E402

Image.MAX_IMAGE_PIXELS = None

SCHEMA = "vop.stage_a.analysis_grid.v1"
TOOL_VERSION = "1.1.0"

# Greg, 2026-09-30: 2 and 8 px per cell "for starters", expressed as what they
# are -- a cell must hold whole pixels to be measured at all, and a mapping
# uncertainty is only tolerable as a fraction of the cell it could misplace a
# pixel across (2 px of uncertainty in an 8 px cell = 0.25).
MIN_PX_PER_CELL = 2.0
COARSE_UNCERTAINTY_CELLS = 0.25

# Greg, 2026-10-01 (run 20261001T084840_1d0b0c1): 31 views found no ticks,
# gridded on the nominal crop mapping, and came back a different size on the
# axis Revit did NOT fit to (MOHAVE 11999340: 639 px against the frame's 729).
# dim_check passed them because it measures the fit axis only. REPORT AND FLAG,
# never refuse: more than this many pixels of difference on the non-fit axis is
# flagged ``frame_mismatch``; a frame that cannot be compared is flagged
# ``frame_unmeasured`` and is never read as a match.
FRAME_MISMATCH_TOLERANCE_PX = 1

MODEL_CHANNELS = ("host", "dwg", "link", "host_ink", "dwg_ink", "link_ink",
                  "tick", "white", "black", "residual", "total")
ANNO_CHANNELS = ("element", "element_ink", "white", "black", "residual", "total")

# Item 3 (Greg, 2026-10-01): occupancy is INK -- "ink as a proxy for work";
# filled area would count building mass. An element pixel is ink when a
# 4-neighbour has a different colour (its own outline, or a line one pixel
# from white); the image border is not an edge, since the drawing continues
# past it. A cell is occupied by a layer when it holds at least this many of
# that layer's ink pixels. Annotation includes datums, view markers and
# revision clouds (the geometry path's known gap).
OCCUPANCY_MIN_INK_PX = 1
OCCUPANCY_CODES = {"empty": 0, "model_only": 1, "anno_only": 2, "overlap": 3}

# Greg, 2026-10-01. A FILLED REGION counts by area, not ink: it masks the
# model, and the mask is what is relevant. Identified by the capture's
# element_class (filled and masking regions share Detail Items with detail
# components, so category cannot tell them apart).
FILLED_REGION_CLASSES = ("FilledRegion",)
# BLACK pixels on the annotation canvas (view-symbol text and the like are
# drawn in black, not in the element's colour) lie inside an annotation
# element's bounding box and are assigned to it: the smallest box that
# contains the pixel centre, padded by this much for the mapping.
BLACK_BBOX_PAD_PX = 1.0

# vop_raster's colours (vop_interwoven/png_export.py), so the two pictures
# read the same way.
PNG_EMPTY = (255, 255, 255)
PNG_MODEL = (0, 200, 0)
PNG_ANNO = (100, 149, 237)
PNG_OVERLAP = (255, 165, 0)
PNG_CROP_A_EDGE = (128, 128, 128)

_MODEL_SIDECAR_NAME = re.compile(r"_(\d+)\.json$")


class GridRefusal(Exception):
    """The view's grid cannot be defined; the message says why."""


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --- the mapping ---------------------------------------------------------------

def nominal_mapping(crop_uv, image_w, image_h):
    """``pixel = a * uv + b`` from the recorded crop, with the aspect-clamp pad
    -- the decoder's own frame (``_pixel_corner_to_uv``), inverted. Pixel i
    spans [i, i+1]."""
    fpp, pad_x, pad_y = clamp_pad_geometry(tuple(crop_uv), image_w, image_h)
    xmin, _ymin, _xmax, ymax = (float(v) for v in crop_uv)
    return {"a_u": 1.0 / fpp, "b_u": -xmin / fpp + pad_x,
            "a_v": -1.0 / fpp, "b_v": ymax / fpp + pad_y}


def residual_at_measured_ticks(mapping, ticks):
    """Largest pixel distance between where ``mapping`` puts each tick's
    centre line and where the fit MEASURED it (``ticks`` from
    registration_marks.fit_marks_in_pixels: a horizontal tick pins a row
    from its v level, a vertical one a column from its u level). The fit's
    own ``residual_max_px`` is this quantity for the fit's mapping, so both
    candidates are scored against the same observations."""
    worst = 0.0
    for tick in ticks:
        level = float(tick["level_uv"])
        if tick["orientation"] == "horizontal":
            predicted = mapping["a_v"] * level + mapping["b_v"]
        else:
            predicted = mapping["a_u"] * level + mapping["b_u"]
        worst = max(worst, abs(predicted - float(tick["centre_px"])))
    return worst


def choose_mapping(sidecar, rgb, crop_uv):
    """``(mapping, basis_record)``: the mapping closer to the measured ticks.

    Both mappings are scored the same way: the largest distance between where
    each puts a tick's centre line and where the fit MEASURED it
    (residual_at_measured_ticks). With no usable fit there is nothing to
    measure against: the nominal mapping is used and the uncertainty is
    recorded as unmeasured, not as zero.
    """
    h, w = rgb.shape[:2]
    nominal = nominal_mapping(crop_uv, w, h)
    record = {"nominal_crop": {"mapping": nominal}}
    payload, refusal = rm.recorded_marks(sidecar)
    fit = None
    if payload is None:
        record["tick_fit"] = {"status": "unavailable",
                              "reason": refusal or "the capture records no ticks"}
    else:
        fit = rm.fit_recorded_marks(rgb, payload,
                                    reserved_colours=rm.palette_colours(sidecar))
        if fit.get("status") != "value":
            record["tick_fit"] = {"status": "unavailable", "reason": fit.get("reason")}
            fit = None
        else:
            why_not = rm.residual_refusal(fit)
            record["tick_fit"] = {
                "status": "value" if why_not is None else "refused",
                "reason": why_not, "mapping": fit["mapping"],
                "found_count": fit.get("found_count"),
                "expected_count": fit.get("expected_count"),
                "residual_px": residual_at_measured_ticks(fit["mapping"],
                                                          fit.get("ticks") or []),
                "measured_ticks": len(fit.get("ticks") or [])}
            if why_not is not None:
                fit = None
    if fit is None:
        record.update({"chosen": "nominal_crop", "uncertainty_px": None,
                       "reason": "no usable tick fit to measure the crop against"})
        return nominal, record
    fit_residual = record["tick_fit"]["residual_px"]
    nominal_residual = residual_at_measured_ticks(nominal, fit.get("ticks") or [])
    record["nominal_crop"]["residual_px"] = nominal_residual
    # The ticks sit inset from the image edges; the two mappings can disagree
    # far more at the corners (Plan_CropInActive, 1453: 0.41 px at the ticks,
    # about 2.4 px at the corners). Recorded, so a reader sees how much the
    # choice matters across the whole image, not only where it was measured.
    record["disagreement_at_image_corners_px"] = max(
        abs((nominal["a_u"] - fit["mapping"]["a_u"]) * u + nominal["b_u"] - fit["mapping"]["b_u"])
        for u in ((0.0 - fit["mapping"]["b_u"]) / fit["mapping"]["a_u"],
                  (w - fit["mapping"]["b_u"]) / fit["mapping"]["a_u"]))
    record["disagreement_at_image_corners_px"] = max(
        record["disagreement_at_image_corners_px"],
        max(abs((nominal["a_v"] - fit["mapping"]["a_v"]) * v + nominal["b_v"] - fit["mapping"]["b_v"])
            for v in ((0.0 - fit["mapping"]["b_v"]) / fit["mapping"]["a_v"],
                      (h - fit["mapping"]["b_v"]) / fit["mapping"]["a_v"])))
    if nominal_residual <= fit_residual:
        record.update({"chosen": "nominal_crop", "uncertainty_px": nominal_residual,
                       "reason": "the recorded crop lands at least as close to "
                                 "the measured ticks as the fit"})
        return nominal, record
    record.update({"chosen": "tick_fit", "uncertainty_px": fit_residual,
                   "reason": "the recorded crop misses the measured ticks by "
                             "{0:.2f} px; the fit by {1:.2f} px".format(
                                 nominal_residual, fit_residual)})
    return dict(fit["mapping"]), record


# --- the frame check ---------------------------------------------------------

def frame_check(frame):
    """PURE. The non-fit axis: the size the frame predicts against the size
    the export came back. ``state`` is "value" only when every input is
    present and readable; otherwise "unavailable" with a reason.

    The prediction is ``crop_px`` on that axis, which describes the image
    only when the capture was sized on crop A (``sizing_frame == "crop_a"``).
    A frame-B capture's image is frame B's, so its crop_px predicts nothing
    about the image size and the check is unavailable for it, not passed.
    """
    fit = frame.get("fit_direction")
    record = {"fit_direction": fit, "predicted_px": None, "actual_px": None,
              "delta_px": None}

    def _unavailable(reason):
        record.update(state="unavailable", reason=reason)
        return record

    fit_norm = str(fit).strip().lower() if fit is not None else None
    if fit_norm not in ("horizontal", "vertical"):
        return _unavailable("the frame records no usable fit_direction ({0!r})".format(fit))
    if frame.get("sizing_frame") != "crop_a":
        return _unavailable("the frame is not crop-A sized (sizing_frame {0!r}), so "
                            "crop_px does not predict the image".format(
                                frame.get("sizing_frame")))
    # Horizontal fit sets the width; the height is derived, and vice versa.
    axis, index, actual_key = (("height", 1, "actual_h") if fit_norm == "horizontal"
                               else ("width", 0, "actual_w"))
    record["axis"] = axis
    crop_px = frame.get("crop_px")
    if not isinstance(crop_px, (list, tuple)) or len(crop_px) != 2:
        return _unavailable("the frame records no crop_px ({0!r})".format(crop_px))
    predicted, actual = crop_px[index], frame.get(actual_key)
    for name, value in (("crop_px[{0}]".format(index), predicted), (actual_key, actual)):
        # A pixel count is a finite whole number. NaN, an infinity (json
        # reads "Infinity") and a fraction are unmeasured, never converted:
        # int() raises on an infinity and would silently truncate 729.6.
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(value) or value != int(value):
            return _unavailable("the frame's {0} is not a whole pixel count "
                                "({1!r})".format(name, value))
    record.update(predicted_px=int(predicted), actual_px=int(actual),
                  delta_px=int(actual) - int(predicted), state="value")
    return record


def frame_check_flag(check):
    """The flag a frame_check earns, or None."""
    if check.get("state") != "value":
        return "frame_unmeasured"
    if abs(check["delta_px"]) > FRAME_MISMATCH_TOLERANCE_PX:
        return "frame_mismatch"
    return None


# --- the grid ----------------------------------------------------------------

def cells_of_columns(spec, model_cols):
    """Cell i for each model-pixel column (centres), signed."""
    u = (np.asarray(model_cols, dtype=np.float64) + 0.5 - spec["mapping"]["b_u"]) \
        / spec["mapping"]["a_u"]
    return np.floor((u - spec["origin_uv"][0]) / spec["cell_ft"]).astype(np.int64)


def cells_of_rows(spec, model_rows):
    """Cell j for each model-pixel row (centres), signed, j UP."""
    v = (np.asarray(model_rows, dtype=np.float64) + 0.5 - spec["mapping"]["b_v"]) \
        / spec["mapping"]["a_v"]
    return np.floor((v - spec["origin_uv"][1]) / spec["cell_ft"]).astype(np.int64)


def cell_to_uv(spec, i, j):
    """Cell (i, j)'s lower-left corner in view UV."""
    return (spec["origin_uv"][0] + i * spec["cell_ft"],
            spec["origin_uv"][1] + j * spec["cell_ft"])


def pixel_to_uv(spec, model_x, model_y):
    """A continuous model-pixel coordinate (pixel i spans [i, i+1]) in UV."""
    m = spec["mapping"]
    return ((model_x - m["b_u"]) / m["a_u"], (model_y - m["b_v"]) / m["a_v"])


def crop_a_cell_ranges(spec, crop, w, h):
    """The cells crop A covers: those holding a model pixel CENTRE that lies
    inside ``crop`` -- the same pixel-centre rule every count uses. Not the
    image's own extent: an aspect-clamped capture pads the image outside the
    crop, and those pad pixels are not inside crop A."""
    m = spec["mapping"]
    cols = np.arange(w, dtype=np.float64)
    rows = np.arange(h, dtype=np.float64)
    u = (cols + 0.5 - m["b_u"]) / m["a_u"]
    v = (rows + 0.5 - m["b_v"]) / m["a_v"]
    in_u = cols[(u >= crop[0]) & (u <= crop[2])]
    in_v = rows[(v >= crop[1]) & (v <= crop[3])]
    if not len(in_u) or not len(in_v):
        raise GridRefusal("no model pixel centre lies inside crop A")
    mi = cells_of_columns(spec, in_u)
    mj = cells_of_rows(spec, in_v)
    return {"i_range": [int(mi.min()), int(mi.max()) + 1],
            "j_range": [int(mj.min()), int(mj.max()) + 1]}


def split_crop_bands(sidecar):
    """The view-UV bands a SPLIT crop shows, or None for an unsplit view.

    Probe Q7: a split crop is captured un-split (inside the capture's rolled-
    back group) so it registers like any view; the capture records under
    ``registration_marks.split_crop`` which bands of the crop the view
    actually shows. Everything outside them was captured but is not in the
    view. A split whose bands are not recorded (a vertical split, an unread
    crop) or that was not removed is REFUSED: gridding the whole crop would
    count the hidden middle as the view's."""
    payload = sidecar.get("registration_marks")
    rec = payload.get("split_crop") if isinstance(payload, dict) else None
    # "no_crop_region": a drafting view or a legend, which has no crop to
    # split; the whole frame is the view.
    if not isinstance(rec, dict) or rec.get("state") in (None, "not_split",
                                                         "no_crop_region"):
        return None
    if rec.get("state") != "removed":
        raise GridRefusal("the view's crop is split and the capture did not take it "
                          "un-split ({0}: {1})".format(rec.get("state"),
                                                       rec.get("reason")))
    bands = rec.get("bands_uv")
    if not bands:
        raise GridRefusal("the view's crop is split and the bands it shows are not "
                          "recorded ({0})".format(rec.get("bands_reason")))
    return [[float(c) for c in band] for band in bands]


def build_spec(sidecar, rgb, run_config, lattice=None):
    """The GridSpec for one view, or raises GridRefusal."""
    frame = frame_record(sidecar)
    scale = frame.get("view_scale")
    crop = frame.get("crop_uv")
    cell_in = (run_config or {}).get("cell_size_paper_in")
    if not cell_in:
        raise GridRefusal("the run's config records no cell_size_paper_in "
                          "(run_meta.json missing or incomplete); the cell size "
                          "is not guessed")
    if not scale:
        raise GridRefusal("the frame record carries no view_scale")
    if not crop or len(crop) != 4:
        raise GridRefusal("the frame record carries no crop_uv")
    cell_ft = float(cell_in) * float(scale) / 12.0
    mapping, basis = choose_mapping(sidecar, rgb, crop)
    px_u = abs(mapping["a_u"]) * cell_ft
    px_v = abs(mapping["a_v"]) * cell_ft
    px_per_cell = min(px_u, px_v)
    if px_per_cell < MIN_PX_PER_CELL:
        raise GridRefusal("{0:.2f} px per cell is below the {1} px floor: a cell "
                          "this small cannot be measured".format(
                              px_per_cell, MIN_PX_PER_CELL))
    h, w = rgb.shape[:2]
    spec = {"cell_ft": cell_ft, "cell_size_paper_in": float(cell_in),
            "view_scale": float(scale),
            "origin_uv": [float(crop[0]), float(crop[1])],
            "mapping": mapping, "uv_basis": basis,
            "px_per_cell_u": px_u, "px_per_cell_v": px_v,
            "model_image_px": [w, h]}
    # Extent over model pixel CENTRES: the model image and, when present,
    # the registered annotation canvas (both on the model lattice).
    x0, y0, x1, y1 = 0, 0, w, h
    if lattice is not None:
        ox, oy = (int(v) for v in lattice["canvas_origin_model_px"])
        x0, y0 = min(x0, ox), min(y0, oy)
        x1 = max(x1, ox + int(lattice["canvas_w"]))
        y1 = max(y1, oy + int(lattice["canvas_h"]))
    i_ends = cells_of_columns(spec, [x0, x1 - 1])
    j_ends = cells_of_rows(spec, [y0, y1 - 1])
    spec["i_range"] = [int(i_ends.min()), int(i_ends.max()) + 1]
    spec["j_range"] = [int(j_ends.min()), int(j_ends.max()) + 1]
    spec["crop_a_cells"] = crop_a_cell_ranges(spec, crop, w, h)
    # Q7: a split crop shows only these bands of crop A (inside_crop_a).
    spec["split_bands_uv"] = split_crop_bands(sidecar)
    spec["cells_w"] = spec["i_range"][1] - spec["i_range"][0]
    spec["cells_h"] = spec["j_range"][1] - spec["j_range"][0]
    unc = basis.get("uncertainty_px")
    spec["uncertainty_cells"] = None if unc is None else unc / px_per_cell
    flags = []
    if unc is None:
        flags.append("uncertainty_unmeasured")
    elif spec["uncertainty_cells"] > COARSE_UNCERTAINTY_CELLS:
        flags.append("coarse")
    if frame.get("cap_applied"):
        flags.append("capped")
    if lattice is None:
        flags.append("no_registered_annotation")
    if spec["split_bands_uv"] is not None:
        flags.append("split_crop")
    spec["frame_check"] = frame_check(frame)
    frame_flag = frame_check_flag(spec["frame_check"])
    if frame_flag is not None:
        flags.append(frame_flag)
    spec["flags"] = flags
    return spec


# --- counting ----------------------------------------------------------------

def _accumulate(spec, counts, channel_masks, model_cols, model_rows):
    """Add each channel mask's pixels to its per-cell count."""
    i = cells_of_columns(spec, model_cols) - spec["i_range"][0]
    j = cells_of_rows(spec, model_rows) - spec["j_range"][0]
    flat = (j[:, None] * spec["cells_w"] + i[None, :]).ravel()
    n = spec["cells_w"] * spec["cells_h"]
    for name, mask in channel_masks.items():
        counts[name] += np.bincount(flat[mask.ravel()], minlength=n)


def _pack(rgb):
    return (rgb[..., 0].astype(np.int64) << 16) | (rgb[..., 1].astype(np.int64) << 8) \
        | rgb[..., 2].astype(np.int64)


def _colour_set(colour_map):
    return np.array(sorted(set((int(c[0]) << 16) | (int(c[1]) << 8) | int(c[2])
                               for c in colour_map.values())), dtype=np.int64)


def ink_mask(packed):
    """PURE. Pixels with a 4-neighbour of a different packed colour. The
    image border is not an edge."""
    edge = np.zeros(packed.shape, dtype=bool)
    edge[:, :-1] |= packed[:, :-1] != packed[:, 1:]
    edge[:, 1:] |= packed[:, 1:] != packed[:, :-1]
    edge[:-1] |= packed[:-1] != packed[1:]
    edge[1:] |= packed[1:] != packed[:-1]
    return edge


def model_channel_masks(sidecar, rgb):
    """Per-pixel channel masks for the model image, in the decoder's S1
    precedence (element, tick, white, black, link, residual), so their image
    totals equal its ``pixel_stats``."""
    decode_map, _block, mark_mask = dsc._registration_mark_exclusion(rgb, sidecar)
    id_array, _stats = dsc.decode_ids(rgb, decode_map)
    packed = _pack(rgb)
    host_map = (sidecar.get("near_face_w_map") or {}).get("host") or {}
    dwg_ids = np.array(sorted(int(k) for k, e in host_map.items()
                              if isinstance(e, dict) and e.get("source") == "DWG"),
                       dtype=np.int64)
    element = id_array != dsc.BACKGROUND_ELEMENT_ID
    dwg = element & np.isin(id_array, dwg_ids)
    rest = ~element
    tick = rest & mark_mask if mark_mask is not None else np.zeros_like(rest)
    rest = rest & ~tick
    white = rest & (packed == 0xFFFFFF)
    black = rest & (packed == 0)
    rest = rest & ~white & ~black
    link = rest & np.isin(packed, _colour_set(sidecar.get("link_category_color_map") or {}))
    edge = ink_mask(packed)
    return {"host": element & ~dwg, "dwg": dwg, "link": link,
            "host_ink": element & ~dwg & edge, "dwg_ink": dwg & edge,
            "link_ink": link & edge, "tick": tick,
            "white": white, "black": black, "residual": rest & ~link,
            "total": np.ones(packed.shape, dtype=bool)}


def _bbox_corners(entry):
    """An annotation_bbox_map entry's UV corners, or None."""
    bb = entry.get("bbox_uv")
    if isinstance(bb, dict):
        if bb.get("state") != "value":
            return None
        bb = bb.get("value")
    if not bb:
        return None
    if len(bb) == 4 and not isinstance(bb[0], (list, tuple)):
        u0, v0, u1, v1 = (float(x) for x in bb)
        return [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
    return [(float(c[0]), float(c[1])) for c in bb]


def assign_black(black, bbox_map, excluded_ids, spec, canvas_origin,
                 pad=BLACK_BBOX_PAD_PX):
    """``(assigned mask, record)``: each black pixel to the smallest
    annotation bounding box containing its centre (padded by ``pad`` px).
    Pixels in no box stay unassigned; pixels in several are counted as
    ambiguous and go to the smallest."""
    ys, xs = np.nonzero(black)
    record = {"total": int(len(xs)), "pad_px": pad, "rule": "smallest containing "
              "annotation bbox"}
    mask = np.zeros(black.shape, dtype=bool)
    if not len(xs):
        record.update(assigned=0, ambiguous=0, unassigned=0, by_element={})
        return mask, record
    m = spec["mapping"]
    px = xs + float(canvas_origin[0]) + 0.5
    py = ys + float(canvas_origin[1]) + 0.5
    best_area = np.full(len(xs), np.inf)
    best_id = np.zeros(len(xs), dtype=np.int64)
    hits = np.zeros(len(xs), dtype=np.int64)
    excluded = set(int(e) for e in excluded_ids or ())
    not_ids = []
    for key, entry in bbox_map.items():
        if not str(key).lstrip("-").isdigit():
            not_ids.append(str(key))     # recorded below, not silently dropped
            continue
        eid = int(key)
        corners = _bbox_corners(entry) if eid not in excluded else None
        if not corners:
            continue
        bx = [m["a_u"] * u + m["b_u"] for u, _v in corners]
        by = [m["a_v"] * v + m["b_v"] for _u, v in corners]
        x0, x1, y0, y1 = min(bx), max(bx), min(by), max(by)
        inside = (px >= x0 - pad) & (px <= x1 + pad) & (py >= y0 - pad) & (py <= y1 + pad)
        area = (x1 - x0) * (y1 - y0)
        hits += inside
        better = inside & (area < best_area)
        best_area[better] = area
        best_id[better] = eid
    assigned = hits > 0
    mask[ys[assigned], xs[assigned]] = True
    ids, counts = np.unique(best_id[assigned], return_counts=True)
    if not_ids:
        record["skipped_non_id_keys"] = not_ids[:20]
    record.update(assigned=int(assigned.sum()), ambiguous=int((hits > 1).sum()),
                  unassigned=int((~assigned).sum()),
                  by_element=dict((str(int(i)), int(c)) for i, c in zip(ids, counts)))
    return mask, record


def anno_channel_masks(anno_sidecar, rgb, excluded_ids, spec=None, canvas_origin=(0, 0)):
    """``(masks, info)`` for the REGISTERED annotation canvas. Its ticks were
    already subtracted (whitened) by the registration tool.

    ``element`` is every annotation-element pixel, ``element_ink`` its ink,
    ``filled_region`` the pixels of filled regions (counted by area),
    ``black_assigned`` the black pixels inside an annotation bbox."""
    excluded = set(int(e) for e in excluded_ids or ())
    colour_map = dict((k, v) for k, v in (anno_sidecar.get("color_assignment_map") or {}).items()
                      if int(k) not in excluded)
    packed = _pack(rgb)
    if colour_map:
        ids, _stats = dsc.decode_ids(rgb, colour_map)
    else:
        ids = np.zeros(packed.shape, dtype=np.int32)
    element = ids != dsc.BACKGROUND_ELEMENT_ID
    bbox_map = anno_sidecar.get("annotation_bbox_map") or {}
    class_recorded = any(isinstance(e, dict) and "element_class" in e
                         for e in bbox_map.values())
    fr_ids = sorted(int(k) for k, e in bbox_map.items()
                    if isinstance(e, dict) and e.get("element_class") in FILLED_REGION_CLASSES
                    and int(k) not in excluded)
    filled = element & np.isin(ids, np.array(fr_ids, dtype=np.int64))
    rest = ~element
    white = rest & (packed == 0xFFFFFF)
    black = rest & (packed == 0)
    if spec is not None:
        black_assigned, black_record = assign_black(black, bbox_map, excluded, spec,
                                                    canvas_origin)
    else:
        black_assigned = np.zeros_like(black)
        black_record = {"total": int(black.sum()), "assigned": 0,
                        "reason": "no grid mapping to place the bboxes"}
    masks = {"element": element, "element_ink": element & ink_mask(packed),
             "filled_region": filled, "white": white, "black": black,
             "black_assigned": black_assigned,
             "residual": rest & ~white & ~black,
             "total": np.ones(packed.shape, dtype=bool)}
    info = {"black": black_record,
            "filled_region": {"element_class_recorded": class_recorded,
                              "elements": len(fr_ids), "px": int(filled.sum())}}
    if not class_recorded:
        info["filled_region"]["reason"] = (
            "this capture records no element_class, so filled regions cannot be "
            "told from detail components and are counted by ink")
    return masks, info


def count_cells(spec, masks, col_offset=0, row_offset=0):
    """Per-cell counts, shape (cells_h, cells_w), row 0 = lowest j."""
    n = spec["cells_w"] * spec["cells_h"]
    counts = dict((name, np.zeros(n, dtype=np.int64)) for name in masks)
    h, w = next(iter(masks.values())).shape
    cols = np.arange(w) + col_offset
    step = 512
    for r0 in range(0, h, step):
        r1 = min(h, r0 + step)
        rows = np.arange(r0, r1) + row_offset
        _accumulate(spec, counts, dict((k, m[r0:r1]) for k, m in masks.items()),
                    cols, rows)
    return dict((k, v.reshape(spec["cells_h"], spec["cells_w"])) for k, v in counts.items())


def occupancy(arrays):
    """PURE. Per-cell class (OCCUPANCY_CODES) from the ink counts: model ink
    is host + DWG + link ink, annotation ink the registered canvas's."""
    model = (arrays["model_host_ink"] + arrays["model_dwg_ink"]
             + arrays["model_link_ink"]) >= OCCUPANCY_MIN_INK_PX
    if "anno_element_ink" in arrays:
        # Ink, plus a filled region's whole area, plus the black pixels
        # assigned to an annotation element.
        anno = (arrays["anno_element_ink"] + arrays.get("anno_filled_region", 0)
                + arrays.get("anno_black_assigned", 0)) >= OCCUPANCY_MIN_INK_PX
    else:
        anno = np.zeros_like(model)
    out = np.zeros(model.shape, dtype=np.uint8)
    out[model & ~anno] = OCCUPANCY_CODES["model_only"]
    out[anno & ~model] = OCCUPANCY_CODES["anno_only"]
    out[model & anno] = OCCUPANCY_CODES["overlap"]
    return out


def occupancy_summary(occ, inside):
    """Cell counts by class, over the whole grid and inside crop A."""
    def _counts(mask):
        return dict((name, int(((occ == code) & mask).sum()))
                    for name, code in OCCUPANCY_CODES.items())
    return {"all_cells": _counts(np.ones(occ.shape, dtype=bool)),
            "inside_crop_a": _counts(inside),
            "outside_crop_a": _counts(~inside)}


def inside_crop_a(spec):
    """The cells the view SHOWS: crop A's cells, and for a split crop only
    those whose centre lies in one of its bands (``split_bands_uv``)."""
    mask = np.zeros((spec["cells_h"], spec["cells_w"]), dtype=bool)
    ci, cj = spec["crop_a_cells"]["i_range"], spec["crop_a_cells"]["j_range"]
    mask[cj[0] - spec["j_range"][0]:cj[1] - spec["j_range"][0],
         ci[0] - spec["i_range"][0]:ci[1] - spec["i_range"][0]] = True
    bands = spec.get("split_bands_uv")
    if bands:
        i = np.arange(spec["i_range"][0], spec["i_range"][1])
        j = np.arange(spec["j_range"][0], spec["j_range"][1])
        u = spec["origin_uv"][0] + (i + 0.5) * spec["cell_ft"]
        v = spec["origin_uv"][1] + (j + 0.5) * spec["cell_ft"]
        shown = np.zeros_like(mask)
        for u0, v0, u1, v1 in bands:
            shown |= (((v >= v0) & (v <= v1))[:, None]
                      & ((u >= u0) & (u <= u1))[None, :])
        mask &= shown
    return mask


# --- the picture ---------------------------------------------------------------

def render_png(spec, arrays, path, px_per_cell=4):
    """The occupancy classes in vop_raster's colours, j flipped so north is
    up; crop A's outline in grey."""
    occ = arrays["occupancy"]
    img = np.full(occ.shape + (3,), 255, dtype=np.uint8)
    img[occ == OCCUPANCY_CODES["model_only"]] = PNG_MODEL
    img[occ == OCCUPANCY_CODES["anno_only"]] = PNG_ANNO
    img[occ == OCCUPANCY_CODES["overlap"]] = PNG_OVERLAP
    img = img[::-1]
    img = np.repeat(np.repeat(img, px_per_cell, axis=0), px_per_cell, axis=1)
    inside = inside_crop_a(spec)[::-1]
    rows = np.where(inside.any(axis=1))[0]
    cols = np.where(inside.any(axis=0))[0]
    if len(rows) and len(cols):
        r0, r1 = rows[0] * px_per_cell, (rows[-1] + 1) * px_per_cell - 1
        c0, c1 = cols[0] * px_per_cell, (cols[-1] + 1) * px_per_cell - 1
        img[r0, c0:c1 + 1] = img[r1, c0:c1 + 1] = PNG_CROP_A_EDGE
        img[r0:r1 + 1, c0] = img[r0:r1 + 1, c1] = PNG_CROP_A_EDGE
    Image.fromarray(img, mode="RGB").save(path)


# --- one view ------------------------------------------------------------------

# Faults about the PROJECT after the capture (a restore, a rollback, a
# read-back), not about the pixels it exported. Every other fault -- and any
# fault this list does not name, including one added later -- refuses the
# view: an unknown fault is not assumed harmless.
RESTORE_ONLY_FAULTS = frozenset((
    "model_view_state_not_restored", "annotation_view_state_not_restored",
    "view_state_not_restored", "annotation_overrides_not_restored",
    "annotation_overrides_unverified", "rollback_raised", "rollback_failed",
    "registration_marks_left_in_project", "detail_lines_left_hidden",
    "detail_lines_unverified", "element_overrides_left_behind",
    "element_overrides_unverified", "view_membership_changed",
    "view_membership_unverified",
))


def check_capture_integrity(sidecar, which):
    """The capture's restore-only faults, once none of its faults invalidates
    the pixels it exported. A sidecar with no readable integrity record is
    refused: absent is never read as clean."""
    integrity = capture_integrity(sidecar)
    if not isinstance(integrity, dict):
        raise GridRefusal("the {0} sidecar records no capture_integrity, so the "
                          "capture cannot be shown clean".format(which))
    faults = integrity.get("capture_faults")
    if not isinstance(faults, list):
        raise GridRefusal("the {0} capture's fault list is unavailable ({1})".format(
            which, faults))
    names = [f.get("fault") if isinstance(f, dict) else str(f) for f in faults]
    invalidating = [n for n in names if n not in RESTORE_ONLY_FAULTS]
    if invalidating:
        raise GridRefusal("the {0} capture records fault(s) that invalidate its "
                          "pixels: {1}".format(which, ", ".join(
                              str(n) for n in invalidating)))
    return names


def _resolve_recorded(recorded, base_dir, what):
    """A path a record wrote: as recorded (absolute, or relative to the
    working directory it was written from), else relative to the record's
    folder, else its file name beside the record. A path that resolves
    nowhere is refused, naming what was tried."""
    if not recorded:
        raise GridRefusal("the record names no path for {0}".format(what))
    p = Path(recorded)
    for candidate in (p, Path(base_dir) / p, Path(base_dir) / p.name):
        if candidate.exists():
            return candidate
    raise GridRefusal("{0} {1!r} was found neither as recorded nor beside the "
                      "record in {2}".format(what, str(recorded), base_dir))


def _check_registration_sources(registered, reg_path, model_sidecar_path,
                                model_tiff_sha256):
    """The annotation sidecar's path, once every source the registration
    record names is shown to be the capture now on disk. A view recaptured
    without re-registering would otherwise pair today's model with
    yesterday's lattice and registered pixels, and look like a clean result."""
    anno_sidecar = _resolve_recorded(registered.get("source_annotation_sidecar"),
                                     reg_path.parent, "the annotation sidecar")
    anno_tiff = _resolve_recorded(registered.get("source_annotation_tiff"),
                                  reg_path.parent, "the source annotation TIFF")
    current = {
        "source_model_sidecar_sha256": sha256_file(model_sidecar_path),
        "source_model_tiff_sha256": model_tiff_sha256,
        "source_annotation_sidecar_sha256": sha256_file(anno_sidecar),
        "source_annotation_tiff_sha256": sha256_file(anno_tiff),
    }
    for key, now in sorted(current.items()):
        recorded = registered.get(key)
        if not recorded:
            raise GridRefusal("the registration record carries no {0}; its "
                              "sources cannot be verified".format(key))
        if recorded != now:
            raise GridRefusal("the registration is stale: {0} does not match the "
                              "file now on disk (re-run "
                              "register_stage_a_annotation)".format(key))
    return anno_sidecar


def _load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def grid_view(model_sidecar_path, out_dir, run_meta=None, png_px_per_cell=4):
    """Grid one view; returns the record written. A refusal is a record too."""
    model_sidecar_path = Path(model_sidecar_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = model_sidecar_path.stem
    run_meta = run_meta if run_meta is not None else dsc.find_run_meta(model_sidecar_path)
    run_config = (run_meta or {}).get("config") or {}
    record = {"schema": SCHEMA, "tool_version": TOOL_VERSION,
              "run_id": (run_meta or {}).get("run_id"),
              "model_sidecar": str(model_sidecar_path),
              "model_sidecar_sha256": sha256_file(model_sidecar_path),
              "png_rule": "the occupancy classes (ink, item 3)"}
    json_path = out_dir / (stem + ".grid.json")
    try:
        sidecar = _load_json(model_sidecar_path)
        record["view_id"] = sidecar.get("view_id")
        record["capture_faults"] = {"model": check_capture_integrity(sidecar, "model")}
        tiff = dsc._resolve_tiff_path(model_sidecar_path, sidecar)
        record["model_tiff_sha256"] = sha256_file(tiff)
        rgb = dsc._load_rgb_array(tiff)
        reg_path = model_sidecar_path.with_name(stem + "_anno.registered.json")
        registered = _load_json(reg_path) if reg_path.exists() else None
        lattice = None
        if registered is not None:
            # register_stage_a_annotation writes "registered" or "refused".
            if registered.get("status") != "registered" or not registered.get("lattice"):
                record["registered_annotation"] = {
                    "status": "unusable", "reason": registered.get("status")}
                registered = None
            else:
                lattice = registered["lattice"]
                anno_sidecar_path = _check_registration_sources(
                    registered, reg_path, model_sidecar_path,
                    record["model_tiff_sha256"])
        spec = build_spec(sidecar, rgb, run_config, lattice=lattice)
        model_masks = model_channel_masks(sidecar, rgb)
        h, w = rgb.shape[:2]
        del rgb
        arrays = dict(("model_" + k, v) for k, v in count_cells(spec, model_masks).items())
        model_ink = model_masks["host_ink"] | model_masks["dwg_ink"] | model_masks["link_ink"]
        del model_masks
        if registered is not None:
            anno_tiff = _resolve_recorded(registered["registered_tiff"], reg_path.parent,
                                          "the registered annotation TIFF")
            if sha256_file(anno_tiff) != registered.get("registered_tiff_sha256"):
                raise GridRefusal("the registered annotation TIFF does not match "
                                  "the hash its record names")
            anno_sidecar = _load_json(anno_sidecar_path)
            record["capture_faults"]["annotation"] = check_capture_integrity(
                anno_sidecar, "annotation")
            bbox_status = anno_sidecar.get("annotation_bbox_status")
            if not isinstance(bbox_status, dict) or bbox_status.get("status") != "value":
                raise GridRefusal(
                    "the annotation bbox map is unavailable ({0}): black pixels and "
                    "filled regions cannot be attributed".format(
                        (bbox_status or {}).get("reason") or bbox_status))
            # The registered pixels were PLACED by the registration's model
            # mapping, whichever mapping the grid chose for model pixels, so
            # bboxes are projected onto them with that same mapping.
            placement = (registered.get("model_fit") or {}).get("mapping")
            if not placement:
                raise GridRefusal("the registration record carries no model_fit "
                                  "mapping to place the annotation bboxes")
            anno_rgb = dsc._load_rgb_array(anno_tiff)
            ox, oy = (int(v) for v in lattice["canvas_origin_model_px"])
            anno_masks, anno_info = anno_channel_masks(
                anno_sidecar, anno_rgb, registered.get("excluded_element_ids"),
                spec=dict(spec, mapping=placement), canvas_origin=(ox, oy))
            del anno_rgb
            arrays.update(("anno_" + k, v) for k, v in count_cells(
                spec, anno_masks, col_offset=ox, row_offset=oy).items())
            # NOT BINARY: the two captures share one lattice, so how much of
            # the model's ink lies under annotation is measured per pixel.
            # The model image sits in the canvas at (-ox, -oy).
            cover = (anno_masks["element"] | anno_masks["black_assigned"])[
                -oy:-oy + h, -ox:-ox + w]
            filled = anno_masks["filled_region"][-oy:-oy + h, -ox:-ox + w]
            arrays.update(count_cells(spec, {
                "model_ink_under_anno": model_ink & cover,
                "model_ink_under_filled_region": model_ink & filled}))
            del anno_masks, cover, filled
            record["black"] = anno_info["black"]
            record["filled_region"] = anno_info["filled_region"]
            ink_px = int(model_ink.sum())
            record["model_ink_under_annotation"] = {
                "model_ink_px": ink_px,
                "under_annotation_px": int(arrays["model_ink_under_anno"].sum()),
                "under_filled_region_px": int(arrays["model_ink_under_filled_region"].sum()),
                "fraction_under_annotation": (
                    int(arrays["model_ink_under_anno"].sum()) / float(ink_px)
                    if ink_px else None),
                "per_cell": "model_ink_under_anno / (model_host_ink + model_dwg_ink "
                            "+ model_link_ink) in the npz"}
            record["registered_annotation"] = {
                "record": str(reg_path), "record_sha256": sha256_file(reg_path),
                "tiff_sha256": registered.get("registered_tiff_sha256")}
        arrays["inside_crop_a"] = inside_crop_a(spec)
        arrays["occupancy"] = occupancy(arrays)
        npz_path = out_dir / (stem + ".grid.npz")
        np.savez_compressed(npz_path, **arrays)
        png_path = out_dir / (stem + ".grid.png")
        render_png(spec, arrays, png_path, px_per_cell=png_px_per_cell)
        record.update({
            "status": "value", "grid": spec,
            "image_totals": dict((k, int(v.sum())) for k, v in arrays.items()
                                 if k not in ("inside_crop_a", "occupancy")),
            "occupancy": dict(occupancy_summary(arrays["occupancy"],
                                                arrays["inside_crop_a"]),
                              codes=OCCUPANCY_CODES,
                              rule="ink: an element pixel with a 4-neighbour of a "
                                   "different colour; occupied at >= {0} ink px per "
                                   "layer".format(OCCUPANCY_MIN_INK_PX)),
            "npz": npz_path.name, "npz_sha256": sha256_file(npz_path),
            "png": png_path.name,
            "layout": "arrays are (cells_h, cells_w); row 0 is j_range[0] (the "
                      "LOWEST v), column 0 is i_range[0]"})
    except GridRefusal as ex:
        record.update({"status": "refused", "reason": str(ex)})
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True, default=str)
    return record


def model_sidecars(folder):
    folder = Path(folder)
    if (folder / "color_id_buffer").is_dir():
        folder = folder / "color_id_buffer"
    return sorted(p for p in folder.glob("*.json")
                  if _MODEL_SIDECAR_NAME.search(p.name)), folder


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run", help="a run directory or its color_id_buffer/")
    ap.add_argument("--out", default=None,
                    help="output directory (default: <run>/analysis_grid)")
    ap.add_argument("--png-px-per-cell", type=int, default=4)
    args = ap.parse_args(argv)
    paths, folder = model_sidecars(args.run)
    if not paths:
        print("no model sidecars under {0}".format(folder))
        return 2
    out = Path(args.out) if args.out else folder.parent / "analysis_grid"
    worst = 0
    for path in paths:
        rec = grid_view(path, out, png_px_per_cell=args.png_px_per_cell)
        if rec["status"] != "value":
            worst = 1
            print("REFUSED {0}: {1}".format(path.name, rec.get("reason")))
            continue
        g = rec["grid"]
        print("{0:<40} {1:>5} x {2:<5} cells  {3:5.2f} px/cell  basis {4:<12} "
              "unc {5}  {6}".format(
                  path.stem[:40], g["cells_w"], g["cells_h"],
                  min(g["px_per_cell_u"], g["px_per_cell_v"]), g["uv_basis"]["chosen"],
                  "-" if g["uv_basis"]["uncertainty_px"] is None else
                  "{0:.2f}px".format(g["uv_basis"]["uncertainty_px"]),
                  ",".join(g["flags"])))
    return worst


if __name__ == "__main__":
    sys.exit(main())
