#!/usr/bin/env python3
"""The analysis grid: which cell every pixel of a Stage A view falls in.

Design: docs/DESIGN_ANALYSIS_GRID.md (item 2 of the analysis layer). One
definition, used by every analysis-layer product:

  * CELL SIZE is the requested paper cell -- ``cell_size_paper_in`` from the
    run's run_meta.json config, times the view scale, over 12. Never adaptive,
    never guessed: missing either input is a refusal.
  * ORIGIN is anchored to VIEW UV, not to crop A (D-A9, Greg 2026-10-05):
    per axis ``origin = floor(crop_min / cell) * cell``, so every cell
    boundary is a whole multiple of the cell from view UV (0, 0) and a crop
    edit no longer renumbers the cells. Cell (i, j) covers u in
    [u0 + i*cell, u0 + (i+1)*cell) and v likewise, with j running UP. Crop A
    starts inside cell 0 (``crop_a_offset_cells``); annotation ink left of or
    below it gets NEGATIVE indices -- expected, and kept signed.
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
``occupancy`` class per cell; row 0 is the LOWEST j; plus the ``ec_*`` element
x cell rows, A1), then ``<view>.grid.json``
naming the npz's hash -- the record is written last (CLAUDE.md, defect class
4) -- and ``<view>.grid.png``, the occupancy in vop_raster's colours.

OCCUPANCY (item 3): a cell is model and/or annotation by INK -- element pixels
with a differently coloured neighbour -- not by filled area. See
OCCUPANCY_MIN_INK_PX.

SINGLE-PASS VIEWS (2.1.0, vop_interwoven/capture_passes.py): a view whose
capture DECLARES one pass skipped is gridded from its one image and flagged
``single_pass`` (never ``no_registered_annotation``); the skipped pass's
channels are written as zeros and the record carries ``capture_passes`` and
``skipped_pass``. A model-only view keeps its model sidecar and gains zero
``anno_*`` channels. An annotation-only view has no model sidecar: its
``<view>_anno.json`` is selected (view_sidecars), the grid files are named by
the view stem, its pixels are placed by ``single_pass_frame.crop_uv`` and the
image size (the nominal mapping), and its model layer is an empty model
pass's white image. Two-pass views grid exactly as in 2.0.0.

    python tools/stage_a_grid.py <run dir | color_id_buffer dir> [--out DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import decode_stage_a_color_id as dsc  # noqa: E402
from tools import npz_write  # noqa: E402
from vop_interwoven import output_layout  # noqa: E402
from tools import registration_marks as rm  # noqa: E402
from tools.clamp_pad_geometry import clamp_pad_geometry  # noqa: E402
from tools.stage_a_sidecar_shapes import capture_integrity, frame_record  # noqa: E402
from vop_interwoven import capture_passes as cp  # noqa: E402

Image.MAX_IMAGE_PIXELS = None

SCHEMA = "vop.stage_a.analysis_grid.v2"
TOOL_VERSION = "2.1.0"

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
ANNO_SUFFIX = "_anno"


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


def choose_mapping(sidecar, rgb, crop_uv, located=None):
    """``(mapping, basis_record)``: the mapping closer to the measured ticks.

    ``located``: registration_marks.locate_recorded_marks for this image, so
    the ticks the fit measures are the ones the decode subtracts, located
    once (grid_view).

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
                                    reserved_colours=rm.palette_colours(sidecar),
                                    located=located)
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


def _crop_axes(spec, crop, w, h):
    """Model-pixel columns and rows, their centres' u and v, and whether each
    centre lies inside ``crop`` on its axis (edges inclusive)."""
    m = spec["mapping"]
    cols = np.arange(w, dtype=np.float64)
    rows = np.arange(h, dtype=np.float64)
    u = (cols + 0.5 - m["b_u"]) / m["a_u"]
    v = (rows + 0.5 - m["b_v"]) / m["a_v"]
    return (cols, rows, u, v, (u >= crop[0]) & (u <= crop[2]),
            (v >= crop[1]) & (v <= crop[3]))


def crop_a_pixel_mask(spec, crop, w, h):
    """Model pixels whose CENTRE lies inside crop A -- and, for a split crop,
    inside one of the bands the view shows (``split_bands_uv``, edges
    inclusive as in inside_crop_a). Counted per cell as ``crop_a_px``, which
    makes an edge cell's partial coverage measurable where ``inside_crop_a``
    only says the cell holds at least one such centre."""
    _cols, _rows, u, v, col_in, row_in = _crop_axes(spec, crop, w, h)
    mask = row_in[:, None] & col_in[None, :]
    bands = spec.get("split_bands_uv")
    if bands:
        shown = np.zeros_like(mask)
        for u0, v0, u1, v1 in bands:
            shown |= (((v >= v0) & (v <= v1))[:, None]
                      & ((u >= u0) & (u <= u1))[None, :])
        mask &= shown
    return mask


def crop_a_cell_ranges(spec, crop, w, h):
    """The cells crop A covers: those holding a model pixel CENTRE that lies
    inside ``crop`` -- the same pixel-centre rule every count uses. Not the
    image's own extent: an aspect-clamped capture pads the image outside the
    crop, and those pad pixels are not inside crop A."""
    cols, rows, _u, _v, col_in, row_in = _crop_axes(spec, crop, w, h)
    in_u, in_v = cols[col_in], rows[row_in]
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


LATTICE_ANCHOR = "view_uv"
ORIGIN_RULE = "origin_uv[k] = floor(crop_min_uv[k] / cell_ft) * cell_ft, per axis"
# A crop minimum within this RELATIVE distance of a whole number of cells is
# that whole number: floor() of 2.9999999999 would otherwise move the origin
# a whole cell for a float artefact of the crop read-back.
ORIGIN_SNAP_REL = 1e-9


def lattice_origin(crop_min_uv, cell_ft):
    """PURE. D-A9 (Greg, 2026-10-05): the cell lattice is anchored to VIEW UV.
    Per axis, the largest whole multiple of ``cell_ft`` at or below crop A's
    minimum (ORIGIN_RULE), so cell boundaries fall on multiples of the cell
    from view UV (0, 0) whatever the crop."""
    out = []
    for value in crop_min_uv:
        k = float(value) / float(cell_ft)
        nearest = round(k)
        if abs(k - nearest) <= ORIGIN_SNAP_REL * max(1.0, abs(k)):
            k = nearest
        out.append(math.floor(k) * float(cell_ft))
    return out


def build_spec(sidecar, rgb, run_config, lattice=None, located=None, single_pass=False):
    """The GridSpec for one view, or raises GridRefusal. ``located``: as
    choose_mapping takes it. ``single_pass``: the capture DECLARES one pass
    (vop_interwoven/capture_passes.py), so the view has no second image to
    register and is flagged ``single_pass``, never
    ``no_registered_annotation``."""
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
    mapping, basis = choose_mapping(sidecar, rgb, crop, located)
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
            "origin_uv": lattice_origin((crop[0], crop[1]), cell_ft),
            "lattice_anchor": LATTICE_ANCHOR, "origin_rule": ORIGIN_RULE,
            "crop_uv": [float(c) for c in crop],
            "mapping": mapping, "uv_basis": basis,
            "px_per_cell_u": px_u, "px_per_cell_v": px_v,
            "model_image_px": [w, h]}
    spec["crop_a_offset_cells"] = [(float(crop[k]) - spec["origin_uv"][k]) / cell_ft
                                   for k in (0, 1)]
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
    if single_pass:
        flags.append("single_pass")
    elif lattice is None:
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
    """Add each channel mask's pixels to its per-cell count.

    A chunk of pixel rows falls in a contiguous band of cell rows, so each
    bincount is sized to that band and added into its slice of the counts:
    the same sums as a bincount over every cell, without allocating and
    adding a whole-grid array per chunk per channel -- a cost that grew with
    the cell count, i.e. as 1 / (px per cell)^2 for one image size."""
    w = spec["cells_w"]
    i = cells_of_columns(spec, model_cols) - spec["i_range"][0]
    j = cells_of_rows(spec, model_rows) - spec["j_range"][0]
    lo, hi = int(j.min()), int(j.max()) + 1
    if lo < 0 or hi > spec["cells_h"]:
        # A whole-grid bincount raised on these too (a negative index, or a
        # result longer than the counts).
        raise ValueError("pixel rows map to cell rows [{0}, {1}) outside the grid's "
                         "[0, {2})".format(lo, hi, spec["cells_h"]))
    flat = ((j - lo)[:, None] * w + i[None, :]).ravel()
    n = (hi - lo) * w
    for name, mask in channel_masks.items():
        counts[name][lo * w:hi * w] += np.bincount(flat[mask.ravel()], minlength=n)


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
    return model_pixels(sidecar, rgb)[0]


def decoded_id_in(packed, colour_map, ids):
    """``np.isin(decode_ids(colour_map) ids, ids)`` ON EVERY ELEMENT PIXEL
    (decoded id != 0), from the packed colours, through a bool table over the
    2**24 colours: a pixel's decoded id is in ``ids`` exactly when its
    colour's (last-named, as _build_lookup) id is. A background pixel is
    False here, where isin would say True if ``ids`` held 0; both callers AND
    this with ``element``, so they cannot tell the two apart.
    The table is bounded by the colour space, never by the id range (Revit
    2024+ ElementIds are 64-bit), and replaces a full-image isin.
    tests/test_stage_a_grid_element_cell.py composes the two."""
    wanted = set(int(i) for i in ids)
    last = {}
    for elem_id_str, rgb in colour_map.items():
        r, g, b = (int(c) for c in rgb)
        last[(r << 16) | (g << 8) | b] = int(elem_id_str)
    lut = np.zeros(256 * 256 * 256, dtype=bool)
    hits = [c for c, eid in last.items() if eid in wanted]
    if hits:
        lut[np.array(hits, dtype=np.int64)] = True
    return lut[packed]


def model_pixels(sidecar, rgb, packed=None):
    """``(masks, id_array, packed, decode_map)``: model_channel_masks' masks,
    with the decoded element id of every pixel (0 = none), the packed colours
    and the decode map they came from (the ticks' ids removed). ``packed``:
    registration_marks.pack_pixels(rgb), when the caller has it."""
    packed = rm.pack_pixels(rgb) if packed is None else packed
    decode_map, _block, mark_mask = dsc._registration_mark_exclusion(
        rgb, sidecar, with_fit=False, packed=packed)
    id_array, _stats = dsc.decode_ids(rgb, decode_map, packed=packed, with_stats=False)
    host_map = (sidecar.get("near_face_w_map") or {}).get("host") or {}
    dwg_ids = np.array(sorted(int(k) for k, e in host_map.items()
                              if isinstance(e, dict) and e.get("source") == "DWG"),
                       dtype=np.int64)
    element = id_array != dsc.BACKGROUND_ELEMENT_ID
    dwg = element & decoded_id_in(packed, decode_map, dwg_ids)
    rest = ~element
    tick = rest & mark_mask if mark_mask is not None else np.zeros_like(rest)
    rest = rest & ~tick
    white = rest & (packed == 0xFFFFFF)
    black = rest & (packed == 0)
    rest = rest & ~white & ~black
    link = rest & np.isin(packed, _colour_set(sidecar.get("link_category_color_map") or {}))
    edge = ink_mask(packed)
    masks = {"host": element & ~dwg, "dwg": dwg, "link": link,
             "host_ink": element & ~dwg & edge, "dwg_ink": dwg & edge,
             "link_ink": link & edge, "tick": tick,
             "white": white, "black": black, "residual": rest & ~link,
             "total": np.ones(packed.shape, dtype=bool)}
    return masks, id_array, packed, decode_map


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
                 pad=BLACK_BBOX_PAD_PX, return_ids=False):
    """``(assigned mask, record)``: each black pixel to the smallest
    annotation bounding box containing its centre (padded by ``pad`` px).
    Pixels in no box stay unassigned; pixels in several are counted as
    ambiguous and go to the smallest.

    ``return_ids``: also return, third, the element id each black pixel was
    assigned to (int64, ``black.shape``, 0 where none) -- the ``best_id``
    already computed, for the element x cell accounting. ``"assigned"``:
    only the assigned pixels' ids, 1-D in row-major order -- what
    ``ids[mask]`` would give -- without a canvas-sized int64 array. The mask
    and the record are the same either way."""
    ys, xs = np.nonzero(black)
    record = {"total": int(len(xs)), "pad_px": pad, "rule": "smallest containing "
              "annotation bbox"}
    mask = np.zeros(black.shape, dtype=bool)
    sparse_ids = return_ids == "assigned"
    ids_out = (np.zeros(0, dtype=np.int64) if sparse_ids
               else np.zeros(black.shape, dtype=np.int64) if return_ids else None)
    if not len(xs):
        record.update(assigned=0, ambiguous=0, unassigned=0, by_element={})
        return (mask, record, ids_out) if return_ids else (mask, record)
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
    if sparse_ids:
        # np.nonzero is row-major, so this is ids_out[mask]'s order.
        ids_out = best_id[assigned]
    elif return_ids:
        ids_out[ys[assigned], xs[assigned]] = best_id[assigned]
    ids, counts = np.unique(best_id[assigned], return_counts=True)
    if not_ids:
        record["skipped_non_id_keys"] = not_ids[:20]
    record.update(assigned=int(assigned.sum()), ambiguous=int((hits > 1).sum()),
                  unassigned=int((~assigned).sum()),
                  by_element=dict((str(int(i)), int(c)) for i, c in zip(ids, counts)))
    return (mask, record, ids_out) if return_ids else (mask, record)


def anno_channel_masks(anno_sidecar, rgb, excluded_ids, spec=None, canvas_origin=(0, 0)):
    """``(masks, info)`` for the REGISTERED annotation canvas. Its ticks were
    already subtracted (whitened) by the registration tool.

    ``element`` is every annotation-element pixel, ``element_ink`` its ink,
    ``filled_region`` the pixels of filled regions (counted by area),
    ``black_assigned`` the black pixels inside an annotation bbox."""
    return anno_pixels(anno_sidecar, rgb, excluded_ids, spec, canvas_origin)[:2]


def anno_pixels(anno_sidecar, rgb, excluded_ids, spec=None, canvas_origin=(0, 0),
                return_packed=False, black_ids_assigned=False):
    """``(masks, info, ids, black_ids, colour_map)``: anno_channel_masks'
    masks and info, with each pixel's decoded element id and each black
    pixel's assigned element id (0 = none), and the colour map decoded (the
    excluded ids removed). ``return_packed``: the packed colours too, sixth.
    ``black_ids_assigned``: ``black_ids`` is only the black_assigned pixels'
    ids, 1-D row-major (assign_black's ``return_ids="assigned"``)."""
    excluded = set(int(e) for e in excluded_ids or ())
    colour_map = dict((k, v) for k, v in (anno_sidecar.get("color_assignment_map") or {}).items()
                      if int(k) not in excluded)
    packed = rm.pack_pixels(rgb)
    if colour_map:
        ids, _stats = dsc.decode_ids(rgb, colour_map, packed=packed, with_stats=False)
    else:
        ids = np.zeros(packed.shape, dtype=np.int32)
    element = ids != dsc.BACKGROUND_ELEMENT_ID
    bbox_map = anno_sidecar.get("annotation_bbox_map") or {}
    class_recorded = any(isinstance(e, dict) and "element_class" in e
                         for e in bbox_map.values())
    fr_ids = sorted(int(k) for k, e in bbox_map.items()
                    if isinstance(e, dict) and e.get("element_class") in FILLED_REGION_CLASSES
                    and int(k) not in excluded)
    filled = element & decoded_id_in(packed, colour_map, fr_ids)
    rest = ~element
    white = rest & (packed == 0xFFFFFF)
    black = rest & (packed == 0)
    if spec is not None:
        black_assigned, black_record, black_ids = assign_black(
            black, bbox_map, excluded, spec, canvas_origin,
            return_ids="assigned" if black_ids_assigned else True)
    else:
        black_assigned = np.zeros_like(black)
        black_ids = (np.zeros(0, dtype=np.int64) if black_ids_assigned
                     else np.zeros(black.shape, dtype=np.int64))
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
    if return_packed:
        return masks, info, ids, black_ids, colour_map, packed
    return masks, info, ids, black_ids, colour_map


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


def crop_a_coverage(arrays):
    """PURE. How crop A's pixels fall in the cells: ``px`` model pixel centres
    inside crop A; ``cells_with_px`` cells holding at least one (the
    ``inside_crop_a`` population, by pixel centre); ``cells_partial`` those
    cells where some of the cell's model pixels lie OUTSIDE crop A (the
    lattice is anchored to view UV, so crop A's edges fall inside cells)."""
    crop_px, total = arrays["crop_a_px"], arrays["model_total"]
    with_px = crop_px > 0
    return {"px": int(crop_px.sum()), "cells_with_px": int(with_px.sum()),
            "cells_partial": int((with_px & (crop_px < total)).sum()),
            "rule": "crop_a_px: model pixel centres inside crop A (and its "
                    "split bands); cells_partial: 0 < crop_a_px < model_total"}


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


# --- the element x cell accounting (A1) -----------------------------------------
#
# The primary granular record: for every (key, cell) pair with any pixel, how
# many of the key's pixels, ink pixels, assigned black pixels and model ink
# pixels under annotation fall in the cell. A KEY is one model element (host
# or DWG), one linked category (link pixels are colour-per-category; per-
# element link identity is not captured, ledger M1) or one annotation
# element. Unattributed pixels are not keys: they stay in the dense channels.
# Presence, dominant type and every threshold are derivations of these
# counts, never stored in their place (D-A3).

EC_FIELDS = ("ec_px", "ec_ink_px", "ec_black_px", "ec_model_ink_under_px")
EC_RULES = {
    "ec_px": "the key's pixels in the cell",
    "ec_ink_px": "the key's ink pixels (item 3: a 4-neighbour of a different "
                 "colour); a filled region's ink, not its area",
    "ec_black_px": "annotation keys: black pixels assigned to the element "
                   "(smallest containing bbox); 0 for model keys",
    "ec_model_ink_under_px": "annotation key: model ink pixels under the "
                             "element's own pixels and its assigned black; model "
                             "key: the key's ink pixels under ANY annotation "
                             "(element | black_assigned)",
}


def _plain_str(value):
    return value if isinstance(value, str) else None


def model_keys(sidecar, decode_map):
    """``(keys, element_ids, link_colours)``: one key per element id the model
    decode map can decode, then one per linked category. A key's ``source``
    and ``category`` are the near_face_w_map host entry's, as recorded; an
    element with no entry, or a source recorded as a failure state, gets
    null, never an inferred value. ``element_class`` is null for model keys:
    the capture does not record it."""
    host_map = (sidecar.get("near_face_w_map") or {}).get("host") or {}
    ids = sorted(set(int(k) for k in decode_map
                     if str(k).lstrip("-").isdigit()
                     and int(k) != dsc.BACKGROUND_ELEMENT_ID))
    keys = []
    for eid in ids:
        entry = host_map.get(str(eid))
        entry = entry if isinstance(entry, dict) else {}
        source = _plain_str(entry.get("source"))
        keys.append({"layer": "model", "element_id": eid, "link_category": None,
                     "source": source if source in ("HOST", "DWG") else None,
                     "category": _plain_str(entry.get("category")),
                     "element_class": None})
    link_map = sidecar.get("link_category_color_map") or {}
    colours, seen = [], {}
    for name in sorted(link_map):
        c = link_map[name]
        packed = (int(c[0]) << 16) | (int(c[1]) << 8) | int(c[2])
        if packed in seen:
            raise GridRefusal("linked categories {0!r} and {1!r} share the colour {2}: "
                              "their pixels cannot be told apart".format(
                                  seen[packed], name, list(c)))
        seen[packed] = name
        colours.append(packed)
        keys.append({"layer": "model", "element_id": None, "link_category": str(name),
                     "source": "LINK", "category": str(name), "element_class": None})
    return keys, np.array(ids, dtype=np.int64), np.array(colours, dtype=np.int64)


def annotation_keys(anno_sidecar, colour_map, black_by_element):
    """``(keys, element_ids, info)``: one key per element in the annotation
    colour map (the excluded ids already removed), fields from
    annotation_bbox_map. An element absent from the bbox map keeps null
    category and element_class and is counted, never dropped. An element that
    received black pixels but is not in the colour map (painted nothing, but
    its bbox holds black) gets a key too, counted separately, so its black
    pixels are not lost."""
    bbox_map = anno_sidecar.get("annotation_bbox_map") or {}
    painted = set(int(k) for k in colour_map)
    black_only = sorted(set(int(k) for k in black_by_element) - painted)
    keys, absent = [], 0
    ids = sorted(painted | set(black_only))     # sorted: _key_index searches it
    for eid in ids:
        entry = bbox_map.get(str(eid))
        if not isinstance(entry, dict):
            absent += 1
            entry = {}
        keys.append({"layer": "annotation", "element_id": eid, "link_category": None,
                     "source": None, "category": _plain_str(entry.get("category")),
                     "element_class": _plain_str(entry.get("element_class"))})
    return keys, np.array(ids, dtype=np.int64), {
        "keys_absent_from_bbox_map": absent,
        "keys_black_only": len(black_only)}


def _key_index(values, table, base):
    """Each value's position in the sorted ``table`` plus ``base``; -1 where
    the value is not in it."""
    out = np.full(values.shape, -1, dtype=np.int64)
    if not len(table):
        return out
    pos = np.clip(np.searchsorted(table, values), 0, len(table) - 1)
    hit = table[pos] == values
    out[hit] = pos[hit] + base
    return out


# A KEY INDEX is a position in a view's ec_keys -- not an ElementId -- so it
# is bounded by the key count, which is at most the decode map's colours
# (2**24), the linked categories and the black-only annotation elements.
# Per-pixel key arrays are int32 (half the memory of int64 on the largest
# arrays the grid holds); a key count int32 cannot hold is REFUSED, never
# wrapped (narrow_key_index). ElementIds themselves stay int64 throughout.
KEY_INDEX_DTYPE = np.int32


def narrow_key_index(values, n_keys):
    """``values`` (key indices in [-1, n_keys)) as KEY_INDEX_DTYPE, or
    GridRefusal when ``n_keys`` or a value does not fit: never wrapped."""
    limit = np.iinfo(KEY_INDEX_DTYPE).max
    values = np.asarray(values)
    if n_keys > limit or (values.size and (int(values.max()) > limit
                                          or int(values.min()) < -1)):
        raise GridRefusal("{0} element x cell keys (largest index {1}) do not fit "
                          "{2}; refused rather than wrapped".format(
                              n_keys, int(values.max()) if values.size else None,
                              np.dtype(KEY_INDEX_DTYPE).name))
    return values.astype(KEY_INDEX_DTYPE, copy=False)


def colour_key_index(packed, colours, keys):
    """``keys[k]`` at each pixel whose packed colour is ``colours[k]``, -1
    elsewhere, by one gather through a 2**24 table. A colour listed twice
    takes its LAST key, as _build_lookup's dict walk does. KEY_INDEX_DTYPE:
    the keys are narrowed (narrow_key_index) before the table is filled."""
    keys = np.asarray(keys, dtype=np.int64)
    lut = np.full(256 * 256 * 256, -1, dtype=KEY_INDEX_DTYPE)
    colours = np.asarray(colours, dtype=np.int64)
    if len(colours):
        # The last occurrence of each colour: first in the reversed list.
        uniq, first = np.unique(colours[::-1], return_index=True)
        lut[uniq] = narrow_key_index(keys[::-1][first],
                                     int(keys.max()) + 1 if len(keys) else 0)
    return lut[packed]


def decoded_key_index(packed, colour_map, table, base):
    """``_key_index(decode_ids(colour_map) ids, table, base)`` from the packed
    colours in ONE gather (colour_key_index): the same values -- -1 where the
    colour decodes to no id in ``table`` -- as KEY_INDEX_DTYPE, without the
    id array's int64 copy and a full-image searchsorted.
    tests/test_stage_a_grid_element_cell.py composes the two."""
    colours, ids = [], []
    for elem_id_str, rgb in colour_map.items():
        r, g, b = (int(c) for c in rgb)
        colours.append((r << 16) | (g << 8) | b)
        ids.append(int(elem_id_str))
    ids = np.asarray(ids, dtype=np.int64)
    table = np.asarray(table, dtype=np.int64)
    keys = np.full(len(ids), -1, dtype=np.int64)
    if len(table) and len(ids):
        pos = np.searchsorted(table, ids)
        hit = pos < len(table)
        hit[hit] = table[pos[hit]] == ids[hit]
        keys[hit] = pos[hit] + base
    return colour_key_index(packed, colours, keys)


def _sparse(spec, keys, mask, col_offset=0, row_offset=0, step=512):
    """``(codes, counts)`` of the pixels where ``mask`` holds, by
    ``key * n_cells + cell``, each code once, ascending."""
    n = spec["cells_w"] * spec["cells_h"]
    h, w = mask.shape
    i = cells_of_columns(spec, np.arange(w) + col_offset) - spec["i_range"][0]
    parts_c, parts_n = [], []
    for r0 in range(0, h, step):
        r1 = min(h, r0 + step)
        j = cells_of_rows(spec, np.arange(r0, r1) + row_offset) - spec["j_range"][0]
        flat = j[:, None] * spec["cells_w"] + i[None, :]
        m = mask[r0:r1]
        # int64 before the multiply: a key index times the cell count can
        # pass int32 (KEY_INDEX_DTYPE) long before either factor does.
        code = keys[r0:r1][m].astype(np.int64) * n + flat[m]
        if len(code):
            c, k = np.unique(code, return_counts=True)
            parts_c.append(c)
            parts_n.append(k)
    if not parts_c:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
    codes = np.concatenate(parts_c)
    counts = np.concatenate(parts_n)
    # Each chunk's codes are sorted and unique, so a stable sort only merges
    # runs; equal codes are then summed. np.unique(codes, return_inverse)
    # plus a float bincount gave the same codes and the same sums.
    order = np.argsort(codes, kind="stable")
    codes, counts = codes[order], counts[order]
    starts = np.flatnonzero(np.concatenate([[True], codes[1:] != codes[:-1]]))
    return codes[starts], np.add.reduceat(counts, starts).astype(np.int64)


def sorted_union(arrays):
    """PURE. ``np.unique(np.concatenate(arrays))`` for arrays that are each
    sorted: a stable sort merges their runs (near-linear), then repeats are
    dropped. numpy 2's hash-based np.unique took 27.7 s on 18 M such codes
    where this takes 0.25 s; the result is the same sorted unique array."""
    if not arrays:
        return np.zeros(0, dtype=np.int64)
    merged = np.sort(np.concatenate(arrays), kind="stable")
    if not len(merged):
        return merged
    return merged[np.concatenate([[True], merged[1:] != merged[:-1]])]


def merge_sparse(spec, parts):
    """The ec_ arrays from ``{field: (codes, counts)}``: one row per code any
    field holds, zeros where a field has none."""
    n = spec["cells_w"] * spec["cells_h"]
    all_codes = [c for c, _k in parts.values() if len(c)]
    codes = sorted_union(all_codes)
    out = {"ec_key": narrow_key_index(codes // n, int(codes.max() // n) + 1
                                      if len(codes) else 0)}
    flat = codes % n
    out["ec_i"] = (flat % spec["cells_w"] + spec["i_range"][0]).astype(np.int32)
    out["ec_j"] = (flat // spec["cells_w"] + spec["j_range"][0]).astype(np.int32)
    for field in EC_FIELDS:
        values = np.zeros(len(codes), dtype=np.int64)
        c, k = parts.get(field, (np.zeros(0, dtype=np.int64),) * 2)
        if len(c):
            values[np.searchsorted(codes, c)] = k
        if values.size and values.max() > np.iinfo(np.int32).max:
            raise GridRefusal("{0} exceeds int32 in one (key, cell)".format(field))
        out[field] = values.astype(np.int32)
    return out


def ec_dense(spec, ec, rows, field):
    """PURE. ``field`` summed over the ec_ rows selected by the boolean
    ``rows``, as a dense (cells_h, cells_w) array."""
    return dense_sum(ec["ec_j"][rows].astype(np.int64) - spec["j_range"][0],
                     ec["ec_i"][rows].astype(np.int64) - spec["i_range"][0],
                     ec[field][rows], (spec["cells_h"], spec["cells_w"]))


def dense_sum(jj, ii, values, shape):
    """PURE. ``values`` summed into a ``shape`` int64 array at ``(jj, ii)``:
    what ``np.add.at(out, (jj, ii), values)`` gives on a zero ``out``, by one
    np.bincount. Exact: the weights are whole numbers and every sum is a pixel
    count (< 2**53, float64's exact-integer range), checked. An index outside
    ``shape`` raises IndexError, as np.add.at does -- including a negative
    one, which np.add.at would wrap (the ec_ rows are built inside the grid,
    merge_sparse, so none is)."""
    flat, inside = cell_flat(jj, ii, shape)
    if not inside.all():
        raise IndexError("cell index outside the {0} x {1} grid".format(*shape))
    return dense_sum_flat(flat, values, shape)


def cell_flat(jj, ii, shape):
    """PURE. ``(jj * w + ii, inside)``: each row's flat cell index and whether
    its (jj, ii) lies in ``shape`` -- for a caller that selects many subsets
    of one set of rows (stage_a_kinds.class_arrays) and checks each."""
    h, w = shape
    jj = np.asarray(jj, dtype=np.int64)
    ii = np.asarray(ii, dtype=np.int64)
    return jj * w + ii, (jj >= 0) & (jj < h) & (ii >= 0) & (ii < w)


def dense_sum_flat(flat, values, shape):
    """dense_sum on flat cell indices already shown inside ``shape``."""
    h, w = shape
    sums = np.bincount(flat, weights=values, minlength=h * w)
    if len(sums) and sums.max() >= 2 ** 53:
        raise OverflowError("a cell sum exceeds float64's exact-integer range")
    return sums.astype(np.int64).reshape(h, w)


def ec_invariants(spec, ec, keys, arrays):
    """The accounting checked against the dense channels, every check with
    its numbers. Raises GridRefusal naming each one that does not hold.

    Annotation keys' ``ec_model_ink_under_px`` sums to EXACTLY
    ``model_ink_under_anno``, not merely at least it: element pixels and
    black pixels are disjoint (black is ``~element``) and each black pixel
    has at most one assigned element, so every covered pixel has exactly one
    annotation owner."""
    layer = np.array([k["layer"] for k in keys] or ["model"])[ec["ec_key"]] \
        if len(ec["ec_key"]) else np.zeros(0, dtype="<U10")
    model_rows = layer == "model"
    anno_rows = layer == "annotation"
    is_link = np.array([k["source"] == "LINK" for k in keys] or [False])
    is_dwg = np.array([k["source"] == "DWG" for k in keys] or [False])
    link_rows = model_rows & is_link[ec["ec_key"]] if len(ec["ec_key"]) else model_rows
    dwg_rows = model_rows & is_dwg[ec["ec_key"]] if len(ec["ec_key"]) else model_rows
    elem_rows = model_rows & ~link_rows
    zero = np.zeros((spec["cells_h"], spec["cells_w"]), dtype=np.int64)
    checks = [
        ("model element px == model_host + model_dwg", elem_rows, "ec_px",
         arrays["model_host"] + arrays["model_dwg"]),
        ("model DWG-source px == model_dwg", dwg_rows, "ec_px", arrays["model_dwg"]),
        ("model link px == model_link", link_rows, "ec_px", arrays["model_link"]),
        ("model element ink == model_host_ink + model_dwg_ink", elem_rows, "ec_ink_px",
         arrays["model_host_ink"] + arrays["model_dwg_ink"]),
        ("model DWG-source ink == model_dwg_ink", dwg_rows, "ec_ink_px",
         arrays["model_dwg_ink"]),
        ("model link ink == model_link_ink", link_rows, "ec_ink_px",
         arrays["model_link_ink"]),
        ("model black == 0", model_rows, "ec_black_px", zero),
        ("model ink under annotation == model_ink_under_anno", model_rows,
         "ec_model_ink_under_px", arrays.get("model_ink_under_anno", zero)),
    ]
    if "anno_element" in arrays:
        checks += [
            ("annotation px == anno_element", anno_rows, "ec_px", arrays["anno_element"]),
            ("annotation ink == anno_element_ink", anno_rows, "ec_ink_px",
             arrays["anno_element_ink"]),
            ("annotation black == anno_black_assigned", anno_rows, "ec_black_px",
             arrays["anno_black_assigned"]),
            ("annotation model ink under == model_ink_under_anno", anno_rows,
             "ec_model_ink_under_px", arrays["model_ink_under_anno"]),
        ]
    elif anno_rows.any():
        raise GridRefusal("annotation keys hold rows but no annotation was gridded")
    out, broken = [], []
    for name, rows, field, dense in checks:
        got = ec_dense(spec, ec, rows, field)
        bad = got != dense
        rec = {"check": name, "ec_total": int(got.sum()), "dense_total": int(dense.sum()),
               "cells_differing": int(bad.sum())}
        out.append(rec)
        if rec["cells_differing"] or rec["ec_total"] != rec["dense_total"]:
            broken.append(rec)
    if broken:
        raise GridRefusal("the element x cell accounting does not reproduce the dense "
                          "channels: " + "; ".join(
                              "{check}: ec {ec_total} vs dense {dense_total}, "
                              "{cells_differing} cells differ".format(**b) for b in broken))
    return out


# The channel masks element_cell_accounting reads; grid_view frees the rest
# once they are counted.
ACCOUNTING_MODEL_MASKS = ("host", "dwg", "link", "host_ink", "dwg_ink", "link_ink")
ACCOUNTING_ANNO_MASKS = ("element", "element_ink", "black_assigned")


def _keep_masks(masks, names):
    """Drop, in place, every mask not in ``names``."""
    for name in [k for k in masks if k not in names]:
        del masks[name]


def element_cell_accounting(spec, model, anno=None):
    """``(ec arrays, keys, info)``.

    ``model``: ``{"sidecar", "packed", "decode_map", "masks", "cover"}``
    -- the model image's packed colours and channel masks, and ``cover``
    (element | black_assigned on the model image's window, or None with no
    registered annotation).
    ``anno``: ``{"sidecar", "packed", "black_ids_assigned", "colour_map",
    "masks", "origin", "black_by_element"}`` for the registered canvas, or
    None; ``black_ids_assigned`` is the assigned element id of each
    black_assigned pixel, in row-major order (``black_ids[black_assigned]``).
    Per-pixel keys are KEY_INDEX_DTYPE."""
    keys, elem_ids, link_colours = model_keys(model["sidecar"], model["decode_map"])
    masks = model["masks"]
    n_elem = len(elem_ids)
    mkey = decoded_key_index(model["packed"], model["decode_map"], elem_ids, 0)
    mkey[~(masks["host"] | masks["dwg"])] = -1
    # Link category k (model_keys' name order; its colours are distinct) is
    # key n_elem + k. Merged in place: where a link key holds, it wins.
    lkey = colour_key_index(model["packed"], link_colours,
                            n_elem + np.arange(len(link_colours)))
    lkey[~masks["link"]] = -1
    linked = lkey >= 0
    mkey[linked] = lkey[linked]
    del lkey, linked
    model_ink = masks["host_ink"] | masks["dwg_ink"] | masks["link_ink"]
    keyed = mkey >= 0
    parts = {"ec_px": _sparse(spec, mkey, keyed),
             "ec_ink_px": _sparse(spec, mkey, keyed & model_ink)}
    if model.get("cover") is not None:
        parts["ec_model_ink_under_px"] = _sparse(spec, mkey, keyed & model_ink
                                                 & model["cover"])
    info = {"model_keys": len(keys), "link_keys": len(link_colours),
            "model_keys_null_source": sum(1 for k in keys[:n_elem] if k["source"] is None),
            "model_keys_null_category": sum(1 for k in keys if k["category"] is None)}
    if anno is not None:
        base = len(keys)
        akeys, a_ids, a_info = annotation_keys(anno["sidecar"], anno["colour_map"],
                                               anno["black_by_element"])
        keys = keys + akeys
        info.update(a_info, annotation_keys=len(akeys),
                    annotation_keys_null_category=sum(1 for k in akeys
                                                      if k["category"] is None),
                    annotation_keys_null_element_class=sum(
                        1 for k in akeys if k["element_class"] is None))
        am = anno["masks"]
        ox, oy = anno["origin"]
        akey = decoded_key_index(anno["packed"], anno["colour_map"], a_ids, base)
        akey[~am["element"]] = -1
        # Searched only where a black pixel was assigned: everywhere else the
        # key is -1 whatever the search would have found.
        bkey = np.full(am["black_assigned"].shape, -1, dtype=KEY_INDEX_DTYPE)
        bkey[am["black_assigned"]] = narrow_key_index(
            _key_index(np.asarray(anno["black_ids_assigned"], dtype=np.int64),
                       a_ids, base), len(keys))
        anno_parts = {"ec_px": _sparse(spec, akey, akey >= 0, ox, oy),
                      "ec_ink_px": _sparse(spec, akey, (akey >= 0) & am["element_ink"],
                                           ox, oy),
                      "ec_black_px": _sparse(spec, bkey, bkey >= 0, ox, oy)}
        # Each covered canvas pixel has ONE annotation owner: its element, or
        # the element its black pixel was assigned to.
        owner = akey
        unowned = owner < 0
        owner[unowned] = bkey[unowned]
        del akey, bkey, unowned
        h, w = model_ink.shape
        window = owner[-oy:-oy + h, -ox:-ox + w]
        anno_parts["ec_model_ink_under_px"] = _sparse(spec, window,
                                                      (window >= 0) & model_ink)
        del owner, window
        for field, (c, k) in anno_parts.items():
            if field in parts:
                pc, pk = parts[field]
                parts[field] = (np.concatenate([pc, c]), np.concatenate([pk, k]))
            else:
                parts[field] = (c, k)
    ec = merge_sparse(spec, parts)
    info["rows"] = int(len(ec["ec_key"]))
    return ec, keys, info


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


def _grid_tail(spec, arrays, inputs, record, out_dir, stem, png_px_per_cell):
    """The accounting, the npz, the PNG and the record's numbers: the end of
    every gridded view, whichever passes it holds. ``inputs`` is the list
    ``[model_in, anno_in]``, emptied here so the pixel masks it holds are
    freed once the accounting has read them (G2)."""
    model_in, anno_in = inputs
    del inputs[:]
    # A1: the element x cell accounting, checked against the dense
    # channels BEFORE anything is written; a mismatch refuses the view.
    ec, ec_keys, ec_info = element_cell_accounting(spec, model_in, anno_in)
    del model_in, anno_in
    ec_info["invariants"] = ec_invariants(spec, ec, ec_keys, arrays)
    arrays["inside_crop_a"] = inside_crop_a(spec)
    arrays["occupancy"] = occupancy(arrays)
    npz_path = out_dir / (stem + ".grid.npz")
    npz_write.savez_compressed(npz_path, dict(arrays, **ec))
    png_path = out_dir / (stem + ".grid.png")
    render_png(spec, arrays, png_path, px_per_cell=png_px_per_cell)
    record.update({
        "status": "value", "grid": spec,
        "image_totals": dict((k, int(v.sum())) for k, v in arrays.items()
                             if k not in ("inside_crop_a", "occupancy")),
        "crop_a_coverage": crop_a_coverage(arrays),
        "occupancy": dict(occupancy_summary(arrays["occupancy"],
                                            arrays["inside_crop_a"]),
                          codes=OCCUPANCY_CODES,
                          rule="ink: an element pixel with a 4-neighbour of a "
                               "different colour; occupied at >= {0} ink px per "
                               "layer".format(OCCUPANCY_MIN_INK_PX)),
        "npz": npz_path.name, "npz_sha256": sha256_file(npz_path),
        "npz_bytes": npz_path.stat().st_size,
        "png": png_path.name,
        "ec_keys": ec_keys, "ec_rows": ec_info["rows"],
        "element_cell": dict(ec_info, fields=EC_RULES,
                             layout="ec_* are 1-D, one entry per (key, cell) "
                                    "row with any nonzero field; ec_key indexes "
                                    "ec_keys; ec_i, ec_j are signed cell indices"),
        "layout": "dense arrays are (cells_h, cells_w); row 0 is j_range[0] (the "
                  "LOWEST v), column 0 is i_range[0]. ec_* arrays are the "
                  "element x cell rows (see element_cell)"})


# --- single-pass captures (vop_interwoven/capture_passes.py) -------------------
#
# A view whose membership proved one pass empty was captured by the other pass
# alone, and its sidecar DECLARES it (``capture_passes``, ``skipped_pass``).
# The grid reads that one image directly. The skipped pass's channels are
# written as ZEROS -- the pass was measured empty by its membership -- so every
# reader sees both layers, and the record carries the declaration through.
# A missing file is never read as a single pass: without the declaration a
# model sidecar with no registration is ``no_registered_annotation`` and an
# annotation sidecar with no model sidecar is not gridded, as before.

def declared_single_pass(sidecar, record, kept):
    """True when ``sidecar`` declares a single pass that kept ``kept``; the
    declaration goes into ``record``. A malformed declaration is refused."""
    passes, skipped, error = cp.declared_passes(sidecar)
    if error is not None:
        raise GridRefusal("the sidecar's pass declaration is malformed: " + error)
    if passes is None:
        return False
    record["capture_passes"] = passes
    if skipped is not None:
        record["skipped_pass"] = skipped
    return passes == [kept]


def view_stem(sidecar_path):
    """The view's file stem: a model sidecar's own, or an annotation-only
    sidecar's without its ``_anno`` suffix -- what the grid files are named by."""
    stem = Path(sidecar_path).stem
    return stem[:-len(ANNO_SUFFIX)] if stem.endswith(ANNO_SUFFIX) else stem


def annotation_only_sidecar(path):
    """True when ``path`` is an ``<x>_anno.json`` that DECLARES an
    annotation-only capture and has no model sidecar beside it."""
    path = Path(path)
    if not path.stem.endswith(ANNO_SUFFIX) or path.with_name(
            view_stem(path) + ".json").exists():
        return False
    try:
        return cp.is_single_pass(_load_json(path), cp.PASS_ANNOTATION)
    except (OSError, ValueError):
        return False


def refuse_beside_annotation_only(model_sidecar_path):
    """A model sidecar beside an annotation sidecar that declares the model
    pass skipped is from another capture of the view; neither is guessed."""
    p = Path(model_sidecar_path)
    anno = p.with_name(p.stem + ANNO_SUFFIX + ".json")
    if anno.exists():
        try:
            declared = cp.is_single_pass(_load_json(anno), cp.PASS_ANNOTATION)
        except (OSError, ValueError):
            declared = False
        if declared:
            raise GridRefusal("{0} declares an annotation-only capture of this view, so "
                              "this model sidecar is from another capture".format(anno.name))


def empty_annotation_layer(spec, arrays, shape, record, model_ink):
    """The annotation channels of a declared model-only view: zero in every
    cell, with zero black / filled-region / overlap records."""
    zeros = np.zeros(shape, dtype=bool)
    names = ("element", "element_ink", "filled_region", "white", "black",
             "black_assigned", "residual", "total")
    arrays.update(("anno_" + k, v) for k, v in count_cells(
        spec, dict((k, zeros) for k in names)).items())
    arrays.update(count_cells(spec, {"model_ink_under_anno": zeros,
                                     "model_ink_under_filled_region": zeros}))
    reason = "single_pass: the annotation pass was skipped (membership_empty)"
    record["black"] = {"total": 0, "assigned": 0, "unassigned": 0, "ambiguous": 0,
                       "reason": reason}
    record["filled_region"] = {"element_class_recorded": None, "elements": 0, "px": 0,
                               "reason": reason}
    record["model_ink_under_annotation"] = {
        "model_ink_px": int(model_ink.sum()), "under_annotation_px": 0,
        "under_filled_region_px": 0, "fraction_under_annotation": (
            0.0 if int(model_ink.sum()) else None),
        "per_cell": "model_ink_under_anno / (model_host_ink + model_dwg_ink "
                    "+ model_link_ink) in the npz"}
    record["registered_annotation"] = {"status": "single_pass", "reason": reason}


# The model sidecar an annotation-only view's empty model layer is decoded
# from: no colours, no links, no ticks.
_EMPTY_MODEL_SIDECAR = {"color_assignment_map": {}, "link_category_color_map": {},
                        "near_face_w_map": {"host": {}, "link": {}}}


def grid_annotation_only(anno_sidecar_path, out_dir, run_meta=None, png_px_per_cell=4):
    """Grid a declared ANNOTATION-ONLY view from its one image.

    The image is its own lattice: ``single_pass_frame.crop_uv`` (the
    rectangle the capture's frame-bound lines fix the export to) and the
    image's size give the nominal mapping, exactly as a model image's
    recorded crop does. No tick fit exists, so the uncertainty is
    unmeasured, and ``frame_check`` measures the non-fit axis against
    ``crop_px``. The model layer is the white image an empty model pass
    renders: every model channel but ``white`` and ``total`` counts zero."""
    anno_sidecar_path = Path(anno_sidecar_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = view_stem(anno_sidecar_path)
    run_meta = run_meta if run_meta is not None else dsc.find_run_meta(anno_sidecar_path)
    run_config = (run_meta or {}).get("config") or {}
    record = {"schema": SCHEMA, "tool_version": TOOL_VERSION,
              "run_id": (run_meta or {}).get("run_id"),
              "annotation_sidecar": str(anno_sidecar_path),
              "annotation_sidecar_sha256": sha256_file(anno_sidecar_path),
              "png_rule": "the occupancy classes (ink, item 3)"}
    json_path = out_dir / (stem + ".grid.json")
    try:
        anno = _load_json(anno_sidecar_path)
        record["view_id"] = anno.get("view_id")
        if not declared_single_pass(anno, record, cp.PASS_ANNOTATION):
            raise GridRefusal("the annotation sidecar does not declare an annotation-only "
                              "capture, and no model sidecar is beside it")
        if anno_sidecar_path.with_name(stem + ".json").exists():
            raise GridRefusal("a model sidecar sits beside a declared annotation-only "
                              "capture; it is from another capture of the view")
        frame_extra = anno.get("single_pass_frame")
        if not isinstance(frame_extra, dict) or not frame_extra.get("crop_uv"):
            raise GridRefusal("the annotation-only capture records no single_pass_frame, "
                              "so its pixels have no place in view UV")
        record["capture_faults"] = {"annotation": check_capture_integrity(
            anno, "annotation")}
        bbox_status = anno.get("annotation_bbox_status")
        if not isinstance(bbox_status, dict) or bbox_status.get("status") != "value":
            raise GridRefusal(
                "the annotation bbox map is unavailable ({0}): black pixels and "
                "filled regions cannot be attributed".format(
                    (bbox_status or {}).get("reason") or bbox_status))
        tiff = dsc._resolve_tiff_path(anno_sidecar_path, anno)
        record["annotation_tiff_sha256"] = sha256_file(tiff)
        rgb = dsc._load_rgb_array(tiff)
        h, w = rgb.shape[:2]
        # The annotation sidecar's frame, completed with the lattice it was
        # sized on: what a model sidecar's frame carries.
        view = dict(anno, frame=dict(frame_record(anno), **frame_extra))
        spec = build_spec(view, rgb, run_config, single_pass=True)
        white = np.full((h, w, 3), 255, dtype=np.uint8)
        model_masks, _ids, model_packed, decode_map = model_pixels(
            _EMPTY_MODEL_SIDECAR, white)
        del white, _ids
        arrays = dict(("model_" + k, v) for k, v in count_cells(spec, model_masks).items())
        _keep_masks(model_masks, ACCOUNTING_MODEL_MASKS)
        arrays["crop_a_px"] = count_cells(spec, {"crop_a_px": crop_a_pixel_mask(
            spec, spec["crop_uv"], w, h)})["crop_a_px"]
        model_ink = model_masks["host_ink"] | model_masks["dwg_ink"] | model_masks["link_ink"]
        excluded = [int(m["id"]) for m in (anno.get("registration_marks") or {}).get(
            "marks") or [] if m.get("id") is not None]
        anno_masks, anno_info, anno_ids, black_ids, anno_colours, anno_packed = \
            anno_pixels(anno, rgb, excluded, spec=spec, canvas_origin=(0, 0),
                        return_packed=True, black_ids_assigned=True)
        del rgb, anno_ids
        arrays.update(("anno_" + k, v) for k, v in count_cells(spec, anno_masks).items())
        cover = anno_masks["element"] | anno_masks["black_assigned"]
        arrays.update(count_cells(spec, {
            "model_ink_under_anno": model_ink & cover,
            "model_ink_under_filled_region": model_ink & anno_masks["filled_region"]}))
        _keep_masks(anno_masks, ACCOUNTING_ANNO_MASKS)
        model_in = {"sidecar": _EMPTY_MODEL_SIDECAR, "packed": model_packed,
                    "decode_map": decode_map, "masks": model_masks, "cover": cover}
        anno_in = {"sidecar": anno, "packed": anno_packed,
                   "black_ids_assigned": black_ids, "colour_map": anno_colours,
                   "masks": anno_masks, "origin": (0, 0),
                   "black_by_element": anno_info["black"].get("by_element") or {}}
        del anno_packed, black_ids, anno_masks, cover
        record["black"] = anno_info["black"]
        record["filled_region"] = anno_info["filled_region"]
        record["model_ink_under_annotation"] = {
            "model_ink_px": 0, "under_annotation_px": 0, "under_filled_region_px": 0,
            "fraction_under_annotation": None,
            "per_cell": "model_ink_under_anno / (model_host_ink + model_dwg_ink "
                        "+ model_link_ink) in the npz"}
        record["registered_annotation"] = {
            "status": "single_pass",
            "reason": "single_pass: the model pass was skipped (membership_empty); the "
                      "annotation image is the view's lattice"}
        inputs = [model_in, anno_in]
        del model_in, anno_in, model_masks, model_packed, model_ink
        _grid_tail(spec, arrays, inputs, record, out_dir, stem, png_px_per_cell)
    except GridRefusal as ex:
        record.update({"status": "refused", "reason": str(ex)})
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True, default=str)
    return record


def grid_view(model_sidecar_path, out_dir, run_meta=None, png_px_per_cell=4):
    """Grid one view; returns the record written. A refusal is a record too.
    A declared annotation-only sidecar is gridded by grid_annotation_only."""
    if annotation_only_sidecar(model_sidecar_path):
        return grid_annotation_only(model_sidecar_path, out_dir, run_meta, png_px_per_cell)
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
        model_only = declared_single_pass(sidecar, record, cp.PASS_MODEL)
        if not model_only:
            refuse_beside_annotation_only(model_sidecar_path)
        record["capture_faults"] = {"model": check_capture_integrity(sidecar, "model")}
        tiff = dsc._resolve_tiff_path(model_sidecar_path, sidecar)
        record["model_tiff_sha256"] = sha256_file(tiff)
        rgb = dsc._load_rgb_array(tiff)
        # One pack and one tick locate per image, shared by the mapping fit
        # and the decode's mark subtraction (identical calls before).
        packed = rm.pack_pixels(rgb)
        payload, _why = rm.recorded_marks(sidecar)
        located = (rm.locate_recorded_marks(rgb, payload, rm.palette_colours(sidecar),
                                            packed) if payload is not None else None)
        reg_path = Path(output_layout.registered_paths(
            model_sidecar_path.with_name(stem + "_anno.json"))[1])
        # A declared model-only capture has no annotation pass to register.
        registered = (_load_json(reg_path) if reg_path.exists() and not model_only
                      else None)
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
        spec = build_spec(sidecar, rgb, run_config, lattice=lattice, located=located,
                          single_pass=model_only)
        model_masks, model_ids, model_packed, decode_map = model_pixels(sidecar, rgb,
                                                                        packed=packed)
        # Freed at their last use (G2): the decoded ids (the keys come from
        # the packed colours), the pixels, then the counted-only channels.
        del packed, located, model_ids
        h, w = rgb.shape[:2]
        del rgb
        arrays = dict(("model_" + k, v) for k, v in count_cells(spec, model_masks).items())
        _keep_masks(model_masks, ACCOUNTING_MODEL_MASKS)
        arrays["crop_a_px"] = count_cells(spec, {"crop_a_px": crop_a_pixel_mask(
            spec, spec["crop_uv"], w, h)})["crop_a_px"]
        model_ink = model_masks["host_ink"] | model_masks["dwg_ink"] | model_masks["link_ink"]
        model_in = {"sidecar": sidecar, "packed": model_packed,
                    "decode_map": decode_map, "masks": model_masks, "cover": None}
        anno_in = None
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
            anno_masks, anno_info, anno_ids, black_ids, anno_colours, anno_packed = \
                anno_pixels(anno_sidecar, anno_rgb, registered.get("excluded_element_ids"),
                            spec=dict(spec, mapping=placement), canvas_origin=(ox, oy),
                            return_packed=True, black_ids_assigned=True)
            del anno_rgb, anno_ids
            # The accounting reads each black pixel's id only where it was
            # assigned: that 1-D selection, never a canvas of int64.
            black_ids_assigned = black_ids
            del black_ids
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
            model_in["cover"] = cover
            del filled
            _keep_masks(anno_masks, ACCOUNTING_ANNO_MASKS)
            anno_in = {"sidecar": anno_sidecar, "packed": anno_packed,
                       "black_ids_assigned": black_ids_assigned,
                       "colour_map": anno_colours, "masks": anno_masks,
                       "origin": (ox, oy),
                       "black_by_element": anno_info["black"].get("by_element") or {}}
            del anno_packed, black_ids_assigned, anno_masks
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
        if model_only:
            # The skipped annotation pass counts ZERO in every annotation
            # channel: measured empty by its membership, not unmeasured.
            empty_annotation_layer(spec, arrays, (h, w), record, model_ink)
        inputs = [model_in, anno_in]
        del model_in, anno_in, model_masks, model_packed, model_ink
        _grid_tail(spec, arrays, inputs, record, out_dir, stem, png_px_per_cell)
    except GridRefusal as ex:
        record.update({"status": "refused", "reason": str(ex)})
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True, default=str)
    return record


def model_sidecars(folder):
    """``(paths, capture folder)`` for a run directory or its capture folder
    (``color_id/`` or ``color_id_buffer/``: vop_interwoven/output_layout). A
    run holding both is refused: a run writes one layout."""
    folder = Path(folder)
    found = [folder / name for name in (output_layout.CAPTURE_DIR[output_layout.V2],
                                        output_layout.CAPTURE_DIR[output_layout.V1])
             if (folder / name).is_dir()]
    if len(found) > 1:
        raise GridRefusal("{0} holds both capture folders ({1}); a run writes one "
                          "layout".format(folder, ", ".join(p.name for p in found)))
    if found:
        folder = found[0]
    return sorted(p for p in folder.glob("*.json")
                  if _MODEL_SIDECAR_NAME.search(p.name)), folder


def view_sidecars(folder):
    """``(paths, capture folder)``: every model sidecar (model_sidecars), and
    every annotation sidecar that DECLARES an annotation-only capture and has
    no model sidecar beside it -- one sidecar per captured view. Only an
    annotation sidecar with no model sibling is opened to read its
    declaration."""
    paths, folder = model_sidecars(folder)
    anno = [p for p in sorted(Path(folder).glob("*" + ANNO_SUFFIX + ".json"))
            if annotation_only_sidecar(p)]
    return sorted(paths + anno), folder


def grid_job(path, out, run_meta, png_px_per_cell):
    """One view of main(): ``(exit code, line, size entry or None)``, the
    size entry ``(ec_rows, npz bytes, keys, stem)`` for the closing table. A
    module-level function of picklable arguments that prints nothing
    (tools/stage_a_jobs.py)."""
    start = time.perf_counter()
    rec = grid_view(path, out, run_meta=run_meta, png_px_per_cell=png_px_per_cell)
    seconds = time.perf_counter() - start
    if rec["status"] != "value":
        return 1, "REFUSED {0}: {1}  {2:.2f}s".format(path.name, rec.get("reason"),
                                                     seconds), None
    g = rec["grid"]
    line = ("{0:<40} {1:>5} x {2:<5} cells  {3:5.2f} px/cell  basis {4:<12} "
            "unc {5}  {6}  {7:.2f}s".format(
                path.stem[:40], g["cells_w"], g["cells_h"],
                min(g["px_per_cell_u"], g["px_per_cell_v"]), g["uv_basis"]["chosen"],
                "-" if g["uv_basis"]["uncertainty_px"] is None else
                "{0:.2f}px".format(g["uv_basis"]["uncertainty_px"]),
                ",".join(g["flags"]), seconds))
    return 0, line, (rec["ec_rows"], rec["npz_bytes"], len(rec["ec_keys"]), path.stem)


def _finish(results):
    sizes = [r[2] for r in results if r[2] is not None]
    lines = []
    if sizes:
        # No size threshold: the numbers are printed for a person to judge.
        lines.append("largest element x cell tables (ec_rows, npz bytes, keys), of {0} "
                     "gridded views:".format(len(sizes)))
        for rows, size, keys, stem in sorted(sizes, reverse=True)[:5]:
            lines.append("  {0:>10} rows  {1:>12} bytes  {2:>7} keys  {3}".format(
                rows, size, keys, stem))
    return lines, max([r[0] for r in results] or [0])


def plan(run, out=None, png_px_per_cell=4):
    """main()'s work as a stage_a_jobs.Plan: one grid_job per model sidecar,
    then the largest element x cell tables from the gathered results."""
    from tools import stage_a_jobs
    paths, folder = view_sidecars(run)
    if not paths:
        return stage_a_jobs.Plan(lines=["no model sidecars under {0}".format(folder)],
                                 early=2)
    out = Path(out) if out else Path(output_layout.grid_dir_for_capture(folder))
    # Every sidecar is in ``folder``, so find_run_meta finds the same record
    # for each: read it once, not once per view.
    run_meta = dsc.find_run_meta(paths[0])
    return stage_a_jobs.Plan(
        jobs=[stage_a_jobs.Job(grid_job, (path, out, run_meta, png_px_per_cell))
              for path in paths], finish=_finish)


def main(argv=None):
    from tools import stage_a_jobs
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run", help="a run directory or its color_id_buffer/")
    ap.add_argument("--out", default=None,
                    help="output directory (default: <run>/analysis_grid)")
    ap.add_argument("--png-px-per-cell", type=int, default=4)
    args = ap.parse_args(argv)
    return stage_a_jobs.run_serial(plan(args.run, args.out, args.png_px_per_cell))


if __name__ == "__main__":
    sys.exit(main())
