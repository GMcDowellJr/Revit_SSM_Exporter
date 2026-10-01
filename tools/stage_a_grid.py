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
from tools.stage_a_sidecar_shapes import frame_record  # noqa: E402

Image.MAX_IMAGE_PIXELS = None

SCHEMA = "vop.stage_a.analysis_grid.v1"
TOOL_VERSION = "1.0.0"

# Greg, 2026-09-30: 2 and 8 px per cell "for starters", expressed as what they
# are -- a cell must hold whole pixels to be measured at all, and a mapping
# uncertainty is only tolerable as a fraction of the cell it could misplace a
# pixel across (2 px of uncertainty in an 8 px cell = 0.25).
MIN_PX_PER_CELL = 2.0
COARSE_UNCERTAINTY_CELLS = 0.25

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


def tick_points(payload):
    """``(u_points, v_points)`` the ticks pin: a horizontal tick pins its v
    level and its u ends, a vertical one the reverse."""
    us, vs = [], []
    for mark in payload.get("marks") or []:
        level = float(mark["level_uv"])
        span = [float(s) for s in mark["span_uv"]]
        if mark.get("orientation") == "horizontal":
            vs.append(level)
            us.extend(span)
        else:
            us.append(level)
            vs.extend(span)
    return us, vs


def disagreement_px(first, second, payload):
    """Largest pixel distance between two mappings over the tick points."""
    us, vs = tick_points(payload)
    worst = 0.0
    for u in us:
        worst = max(worst, abs((first["a_u"] - second["a_u"]) * u
                               + (first["b_u"] - second["b_u"])))
    for v in vs:
        worst = max(worst, abs((first["a_v"] - second["a_v"]) * v
                               + (first["b_v"] - second["b_v"])))
    return worst


def choose_mapping(sidecar, rgb, crop_uv):
    """``(mapping, basis_record)``: the mapping closer to the measured ticks.

    The tick fit's own residual is ``residual_max_px``. The nominal mapping's
    is its disagreement with the fit at the tick points -- an estimate, good to
    within the fit's residual, which is recorded beside it. With no usable fit
    there is nothing to measure against: the nominal mapping is used and the
    uncertainty is recorded as unmeasured, not as zero.
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
                "residual_px": max(v for v in fit["residual_max_px"].values()
                                   if v is not None)}
            if why_not is not None:
                fit = None
    if fit is None:
        record.update({"chosen": "nominal_crop", "uncertainty_px": None,
                       "reason": "no usable tick fit to measure the crop against"})
        return nominal, record
    fit_residual = record["tick_fit"]["residual_px"]
    nominal_residual = disagreement_px(nominal, fit["mapping"], payload)
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
                       "reason": "the recorded crop lands within the fit's own "
                                 "residual of the ticks"})
        return nominal, record
    record.update({"chosen": "tick_fit", "uncertainty_px": fit_residual,
                   "reason": "the recorded crop misses the measured ticks by "
                             "{0:.2f} px; the fit by {1:.2f} px".format(
                                 nominal_residual, fit_residual)})
    return dict(fit["mapping"]), record


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
    mi = cells_of_columns(spec, [0, w - 1])
    mj = cells_of_rows(spec, [0, h - 1])
    spec["crop_a_cells"] = {"i_range": [int(mi.min()), int(mi.max()) + 1],
                            "j_range": [int(mj.min()), int(mj.max()) + 1]}
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


def anno_channel_masks(anno_sidecar, rgb, excluded_ids):
    """Per-pixel channel masks for the REGISTERED annotation canvas. Its ticks
    were already subtracted (whitened) by the registration tool."""
    colour_map = dict((k, v) for k, v in (anno_sidecar.get("color_assignment_map") or {}).items()
                      if int(k) not in set(int(e) for e in excluded_ids or ()))
    packed = _pack(rgb)
    element = np.isin(packed, _colour_set(colour_map)) if colour_map else \
        np.zeros(packed.shape, dtype=bool)
    rest = ~element
    white = rest & (packed == 0xFFFFFF)
    black = rest & (packed == 0)
    return {"element": element, "element_ink": element & ink_mask(packed),
            "white": white, "black": black,
            "residual": rest & ~white & ~black,
            "total": np.ones(packed.shape, dtype=bool)}


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
    anno = (arrays["anno_element_ink"] >= OCCUPANCY_MIN_INK_PX
            if "anno_element_ink" in arrays else np.zeros_like(model))
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
    mask = np.zeros((spec["cells_h"], spec["cells_w"]), dtype=bool)
    ci, cj = spec["crop_a_cells"]["i_range"], spec["crop_a_cells"]["j_range"]
    mask[cj[0] - spec["j_range"][0]:cj[1] - spec["j_range"][0],
         ci[0] - spec["i_range"][0]:ci[1] - spec["i_range"][0]] = True
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
        spec = build_spec(sidecar, rgb, run_config, lattice=lattice)
        arrays = dict(("model_" + k, v) for k, v in
                      count_cells(spec, model_channel_masks(sidecar, rgb)).items())
        del rgb
        if registered is not None:
            anno_tiff = Path(registered["registered_tiff"])
            if not anno_tiff.is_absolute():
                anno_tiff = reg_path.parent / anno_tiff
            if sha256_file(anno_tiff) != registered.get("registered_tiff_sha256"):
                raise GridRefusal("the registered annotation TIFF does not match "
                                  "the hash its record names")
            anno_sidecar = _load_json(Path(registered["source_annotation_sidecar"])) \
                if Path(registered["source_annotation_sidecar"]).exists() else \
                _load_json(model_sidecar_path.with_name(stem + "_anno.json"))
            anno_rgb = dsc._load_rgb_array(anno_tiff)
            ox, oy = (int(v) for v in lattice["canvas_origin_model_px"])
            arrays.update(("anno_" + k, v) for k, v in count_cells(
                spec, anno_channel_masks(anno_sidecar, anno_rgb,
                                         registered.get("excluded_element_ids")),
                col_offset=ox, row_offset=oy).items())
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
