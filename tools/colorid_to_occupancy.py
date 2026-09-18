#!/usr/bin/env python3
"""Rebuild a VOP per-cell occupancy grid from Stage A color-ID exports, and
optionally diff it cell-by-cell against a geometry-path run of the same views.

Runs outside Dynamo (Pillow + NumPy). Reads only run artifacts; writes only
its own outputs. Nothing in vop_interwoven is imported or modified.

Usage
    python colorid_to_occupancy.py COLORID_DIR [--geom-run GEOM_DIR]
                                   [--out OUT_DIR] [--thresholds 0,0.05,0.25,0.5]

COLORID_DIR   a run's color_id_buffer/ directory (sidecar JSON + TIFF pairs)
--geom-run    a geometry-path run directory of the SAME views, containing
              views_perf_*.csv, views_vop_*.csv and vop_raster/*.png
--out         where to write grids, diffs and the summary (default: ./occ_out)

Grid definition
    With --geom-run, W/H/cell come from the geometry run, so cells align by
    construction and any disagreement is about content, not framing.
    Without it, W is taken from resolution.backoff_floor_px on requested_axis
    (the producer records the grid axis size there) and cell size is derived;
    every such view is marked grid_basis="assumed" in the summary.

Pixel -> model space
    The export is isotropic and Revit pads the clamped axis symmetrically
    (10:1 aspect clamp). This tool derives feet-per-pixel from the UNPADDED
    axis and recovers both pads, rather than stretching each axis over the
    full image independently. On an unpadded view the derived pads are 0
    within rounding; on a clamped view they are the real bands.

Comparison channels (they are not the same thing -- see summary output)
    colorid_ink : a cell is occupied if its palette-pixel coverage >= threshold
    geom_png    : model ink cells read from vop_raster PNG
                  (0,255,0 = model edge; 0,128,0 = proxy; 255,0,0 = anno)
    geom_csv    : views_vop aggregate ModelOnly/Overlap, which uses
                  has_model_present(mode="any") = occ OR edge OR proxy, a
                  SUPERSET of what the PNG draws. Aggregate only.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image

# Repo root (parent of tools/) on sys.path so the sibling leaf module below is
# importable however this tool is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.clamp_pad_geometry import clamp_pad_geometry  # noqa: E402

Image.MAX_IMAGE_PIXELS = None

# vop_raster PNGs come from either of TWO exporters with DIFFERENT palettes,
# and export_raster_to_png() prefers the Pillow one whenever NumPy and Pillow
# are both present (png_export.py:449-451) -- which is the common path. This
# tool originally recognised only the .NET constants, so on a normal run every
# model-only and annotation-only cell was missed and the reported IoUs and
# diff PNGs were invalid.
#
#   Pillow  (png_export.py:398-406): edge (0,200,0), proxy (0,100,0),
#           anno-only (100,149,237), anno-over-model (255,165,0), and in
#           cut_vs_projection mode cut (64,64,64) / projection (192,192,192).
#   .NET    (png_export.py:248-256): edge (0,255,0), proxy (0,128,0),
#           anno (255,0,0). Its orange and grey constants are declared but
#           never reached -- that path is strictly priority-based
#           (edge > proxy > anno, :274-296), so it emits no overlap colour at
#           all and an annotated model cell simply renders green.
#
# Both palettes are accepted. A colour that means "model" in one and
# something else in the other would make this ambiguous; none does.
GEOM_MODEL_RGBS = (
    (0, 200, 0), (0, 100, 0),      # Pillow: edge, proxy
    (0, 255, 0), (0, 128, 0),      # .NET:   edge, proxy
    (64, 64, 64), (192, 192, 192),  # cut_vs_projection: cut, projection
    (255, 165, 0),                  # Pillow: annotation over model
)
GEOM_ANNO_RGBS = (
    (100, 149, 237),                # Pillow: annotation only
    (255, 0, 0),                    # .NET:   annotation
    (255, 165, 0),                  # Pillow: annotation over model
)

ROW_CHUNK = 512


def pack(rgb) -> int:
    return (int(rgb[0]) << 16) | (int(rgb[1]) << 8) | int(rgb[2])


def load_sidecar(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def tiff_for(sidecar_path: Path) -> Path:
    local = sidecar_path.with_suffix(".tiff")
    if local.exists():
        return local
    raise FileNotFoundError(f"no TIFF beside {sidecar_path}")


def frame_from_capture(sidecar, img_w, img_h):
    """Return (fpp, pad_x, pad_y, crop) using the isotropic + symmetric-pad model.

    crop is the sidecar's bounds_xy AS RECORDED -- color_id_buffer.py:2694
    writes that field as exactly the rectangle the view was cropped to (the
    same tuple handed to view.CropBox at :2063), so it already IS the
    rectangle the TIFF content spans.

    model_crop_offset_uv is deliberately NOT applied here. It describes
    raster.bounds_xy -> this crop (color_id_buffer.py:2697-2707: ADD it to
    raster.bounds_xy's corners to reconstruct the crop), so adding it to the
    crop applies it a second time. That is what this function used to do: on
    a narrowed capture it produced a rectangle that was neither the crop nor
    the grid, and disagreed with tools/decode_stage_a_color_id.py on
    feet_per_pixel by 60% (see tools/notes/frame_anchor_findings.md, G7).
    To go the other way -- crop -> the wider shared grid -- SUBTRACT it, as
    decode_stage_a_color_id.py:678-685 does for grid_bounds_uv.
    """
    bounds = sidecar.get("bounds_xy")
    if not bounds or len(bounds) != 4:
        raise ValueError("sidecar has no usable bounds_xy")
    cxmin, cymin, cxmax, cymax = (float(v) for v in bounds)

    # The clamp model itself lives in tools/clamp_pad_geometry.py -- one copy,
    # shared with decode and with the LINK resolver. It raises ValueError on a
    # degenerate crop, which is the same refusal this function already made.
    #
    # No producer-recorded export size is passed: this tool reads the TIFF it
    # was pointed at, so both of the helper's denominators are that image's own
    # dimensions. A dim_check mismatch is not detectable here and is decode's
    # Guard 1 to catch.
    fpp, pad_x, pad_y = clamp_pad_geometry(
        (cxmin, cymin, cxmax, cymax), img_w, img_h)
    return fpp, pad_x, pad_y, (cxmin, cymin, cxmax, cymax)


def grid_bounds_from_capture(sidecar, crop):
    """Return the SHARED RASTER GRID's rectangle (raster.bounds_xy), which is
    what W/H/cell are defined against -- not the narrower rectangle this TIFF
    was cropped to.

    color_id_buffer.py:2697-2707 defines the offset as raster.bounds_xy ->
    render crop, so going the other way SUBTRACTS it. Same arithmetic
    decode_stage_a_color_id.py:678-685 uses for its grid_bounds_uv.

    On a capture with a zero offset (no narrowing) this is the crop itself.
    """
    off = sidecar.get("model_crop_offset_uv") or [0.0, 0.0, 0.0, 0.0]
    return (float(crop[0]) - float(off[0]), float(crop[1]) - float(off[1]),
            float(crop[2]) - float(off[2]), float(crop[3]) - float(off[3]))


def grid_from_geom(view_id, geom):
    rec = geom["perf"].get(view_id)
    vop = geom["vop"].get(view_id)
    if not rec or not vop:
        return None
    try:
        W = int(float(rec["Width"]))
        H = int(float(rec["Height"]))
    except (KeyError, TypeError, ValueError):
        return None
    cell = None
    for key in ("CellSizeEffective_ft", "CellSize_ft", "CellSizeRequested_ft"):
        raw = vop.get(key)
        if raw:
            try:
                cell = float(raw)
                break
            except ValueError:
                continue
    if not W or not H or not cell:
        return None
    return {"W": W, "H": H, "cell": cell, "basis": "geom_run"}


def grid_assumed(sidecar, grid_bounds):
    """Reconstruct W/H/cell when no geometry run is available.

    ``backoff_floor_px`` is raster.W (or raster.H under vertical fit) --
    color_id_buffer.py:1735-1740 sets it from the raster, :2683 records it --
    so it counts cells across the FULL shared grid. Dividing the narrowed TIFF
    crop's extent by it understates the cell size by the crop-to-grid
    difference, and the other dimension then comes out wrong too. Take both
    extents from the grid's own rectangle.
    """
    res = sidecar.get("resolution") or {}
    axis = res.get("requested_axis") or "width"
    grid_axis_px = res.get("backoff_floor_px")
    gxmin, gymin, gxmax, gymax = grid_bounds
    grid_u = gxmax - gxmin
    grid_v = gymax - gymin
    if not grid_axis_px or grid_u <= 0 or grid_v <= 0:
        return None
    n = int(grid_axis_px)
    if axis == "height":
        cell = grid_v / n
        H, W = n, max(1, int(round(grid_u / cell)))
    else:
        cell = grid_u / n
        W, H = n, max(1, int(round(grid_v / cell)))
    return {"W": W, "H": H, "cell": cell, "basis": "assumed"}


def colorid_cell_coverage(tiff_path, sidecar, grid, fpp, pad_x, pad_y, crop, grid_bounds):
    """Return (covered_px, ink_px, total_px) per cell, each shape (H, W).

    covered_px : pixels whose color is an assigned palette color (FILL channel,
                 comparable to depth-tested interior occupancy).
    ink_px     : palette pixels that have a 4-neighbour of a different color
                 (INK channel, comparable to the geometry path's model_edge
                 cells, which is what vop_raster PNGs draw).
    total_px   : every in-grid pixel, so coverage = covered/total.
    """
    cmap = sidecar.get("color_assignment_map") or {}
    palette = np.array(sorted({pack(v) for v in cmap.values()}), dtype=np.int64)

    W, H, cell = grid["W"], grid["H"], grid["cell"]
    # Cell indices are measured from the SHARED GRID's origin. crop[0:2] is
    # this TIFF's own (possibly narrowed) origin; using it would shift every
    # index by the crop-to-grid offset on an annotation-expanded view, while
    # the geometry grid and the vop_raster PNG stay anchored at
    # raster.bounds_xy. Pixel -> UV below still uses crop, which is what the
    # TIFF actually spans.
    gxmin, gymin = grid_bounds[0], grid_bounds[1]

    im = Image.open(tiff_path)
    img_w, img_h = im.size

    cols = np.arange(img_w, dtype=np.float64)
    u = crop[0] + (cols + 0.5 - pad_x) * fpp
    i_idx = np.floor((u - gxmin) / cell).astype(np.int64)
    i_ok = (i_idx >= 0) & (i_idx < W)

    covered = np.zeros(H * W, dtype=np.int64)
    ink = np.zeros(H * W, dtype=np.int64)
    total = np.zeros(H * W, dtype=np.int64)

    arr = np.asarray(im.convert("RGB"))
    packed_all = (arr[..., 0].astype(np.int64) << 16) | \
                 (arr[..., 1].astype(np.int64) << 8) | arr[..., 2]
    del arr

    for y0 in range(0, img_h, ROW_CHUNK):
        y1 = min(img_h, y0 + ROW_CHUNK)
        rows = np.arange(y0, y1, dtype=np.float64)
        v = crop[3] - (rows + 0.5 - pad_y) * fpp
        j_idx = np.floor((v - gymin) / cell).astype(np.int64)
        j_ok = (j_idx >= 0) & (j_idx < H)
        if not j_ok.any():
            continue

        packed = packed_all[y0:y1]
        ok = j_ok[:, None] & i_ok[None, :]
        flat_idx = (j_idx[:, None] * W + i_idx[None, :])

        total += np.bincount(flat_idx[ok], minlength=H * W)
        is_pal = np.isin(packed, palette)
        hit = is_pal & ok
        if hit.any():
            covered += np.bincount(flat_idx[hit], minlength=H * W)

        # Boundary pixels: palette pixel with a 4-neighbour of another color.
        # Rows above/below the chunk are read from the full array so chunk
        # seams do not manufacture edges.
        diff = np.zeros_like(packed, dtype=bool)
        diff[:, :-1] |= packed[:, :-1] != packed[:, 1:]
        diff[:, 1:] |= packed[:, 1:] != packed[:, :-1]
        up = packed_all[max(0, y0 - 1):y1 - 1] if y0 > 0 else packed[:-1]
        if y0 > 0:
            diff |= packed != up
        else:
            diff[1:] |= packed[1:] != packed[:-1]
        if y1 < img_h:
            diff |= packed != packed_all[y0 + 1:y1 + 1]
        else:
            diff[:-1] |= packed[:-1] != packed[1:]

        edge = hit & diff
        if edge.any():
            ink += np.bincount(flat_idx[edge], minlength=H * W)

    return covered.reshape(H, W), ink.reshape(H, W), total.reshape(H, W)


def geom_png_masks(png_path, W, H):
    """Read a vop_raster PNG into (model_ink, anno) boolean grids.

    The PNG is written at N px/cell with j flipped so the origin is
    bottom-left; this reverses both.
    """
    im = Image.open(png_path).convert("RGB")
    pw, ph = im.size
    if pw % W or ph % H:
        return None, None, f"PNG {pw}x{ph} is not an integer multiple of grid {W}x{H}"
    sx, sy = pw // W, ph // H
    arr = np.asarray(im)
    # Sample each cell's centre pixel.
    ys = (np.arange(H) * sy + sy // 2)
    xs = (np.arange(W) * sx + sx // 2)
    sample = arr[np.ix_(ys, xs)]
    packed = (sample[..., 0].astype(np.int64) << 16) | \
             (sample[..., 1].astype(np.int64) << 8) | sample[..., 2]
    model = np.isin(packed, np.array([pack(c) for c in GEOM_MODEL_RGBS], dtype=np.int64))
    anno = np.isin(packed, np.array([pack(c) for c in GEOM_ANNO_RGBS], dtype=np.int64))
    # PNG row 0 is the top; grid row 0 is the bottom.
    return model[::-1], anno[::-1], None


def write_diff_png(path, colorid, geom, scale=4):
    """both = grey, colorid-only = magenta, geom-only = cyan, neither = white."""
    H, W = colorid.shape
    out = np.full((H, W, 3), 255, dtype=np.uint8)
    both = colorid & geom
    c_only = colorid & ~geom
    g_only = geom & ~colorid
    out[both] = (120, 120, 120)
    out[c_only] = (220, 0, 220)
    out[g_only] = (0, 190, 220)
    img = Image.fromarray(out[::-1])  # bottom-left origin -> image top-left
    if scale > 1:
        img = img.resize((W * scale, H * scale), Image.NEAREST)
    img.save(path)


def load_geom_run(geom_dir: Path):
    perf, vop, rasters = {}, {}, {}
    for p in geom_dir.glob("views_perf_*.csv"):
        with p.open(newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                perf[str(row.get("ViewId", "")).strip()] = row
    for p in geom_dir.glob("views_vop_*.csv"):
        with p.open(newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                vop[str(row.get("ViewId", "")).strip()] = row
    raster_dir = geom_dir / "vop_raster"
    if raster_dir.is_dir():
        for p in raster_dir.glob("*.png"):
            vid = p.stem.rsplit("_", 1)[-1]
            rasters[vid] = p
    return {"perf": perf, "vop": vop, "raster": rasters}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("colorid_dir")
    ap.add_argument("--geom-run", default=None)
    ap.add_argument("--out", default="occ_out")
    ap.add_argument("--thresholds", default="0,0.05,0.25,0.5",
                    help="coverage fractions at which a cell counts as occupied")
    ap.add_argument("--primary-threshold", type=float, default=0.0,
                    help="threshold used for the cellwise diff (default 0 = any pixel)")
    ns = ap.parse_args(argv)

    cdir = Path(ns.colorid_dir)
    out_dir = Path(ns.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    thresholds = [float(t) for t in ns.thresholds.split(",") if t.strip() != ""]
    geom = load_geom_run(Path(ns.geom_run)) if ns.geom_run else None

    sidecars = sorted(p for p in cdir.glob("*.json")
                      if not p.name.endswith((".metrics.json", ".decoded.json")))
    summary = []

    for sp in sidecars:
        rec = {"sidecar": sp.name}
        try:
            sc = load_sidecar(sp)
            vid = str(sc.get("view_id"))
            rec["view_id"] = vid
            tp = tiff_for(sp)
            with Image.open(tp) as im:
                img_w, img_h = im.size
            fpp, pad_x, pad_y, crop = frame_from_capture(sc, img_w, img_h)
            rec.update({
                "image_px": [img_w, img_h],
                "feet_per_pixel": fpp,
                "pad_px": [round(pad_x, 2), round(pad_y, 2)],
            })

            grid_bounds = grid_bounds_from_capture(sc, crop)
            rec["grid_bounds_uv"] = [round(v, 6) for v in grid_bounds]
            grid = (grid_from_geom(vid, geom) if geom else None) or grid_assumed(sc, grid_bounds)
            if grid is None:
                rec["error"] = "no grid definition available"
                summary.append(rec)
                continue
            rec["grid"] = {"W": grid["W"], "H": grid["H"],
                           "cell_ft": grid["cell"], "basis": grid["basis"]}

            covered, ink, total = colorid_cell_coverage(
                tp, sc, grid, fpp, pad_x, pad_y, crop, grid_bounds)
            with np.errstate(invalid="ignore", divide="ignore"):
                frac = np.where(total > 0, covered / np.maximum(total, 1), 0.0)

            rec["cells_total"] = int(grid["W"] * grid["H"])
            rec["fill_cells_by_threshold"] = {
                str(t): int(((frac > t) if t == 0 else (frac >= t)).sum())
                for t in thresholds
            }
            rec["ink_cells"] = int((ink > 0).sum())
            rec["px_per_cell_mean"] = float(total.mean())

            np.save(out_dir / f"{sp.stem}.coverage.npy", frac.astype(np.float32))
            np.save(out_dir / f"{sp.stem}.ink.npy", (ink > 0))

            t = ns.primary_threshold
            colorid_mask = (frac > 0) if t == 0 else (frac >= t)
            colorid_ink = ink > 0

            if geom:
                vrow = geom["vop"].get(vid)
                if vrow:
                    def _i(key):
                        try:
                            return int(float(vrow.get(key) or 0))
                        except ValueError:
                            return 0
                    geom_any = _i("ModelOnly") + _i("Overlap")
                    rec["geom_csv"] = {
                        "TotalCells": _i("TotalCells"),
                        "model_any_cells": geom_any,
                        "AnnoOnly": _i("AnnoOnly"),
                        "Overlap": _i("Overlap"),
                    }
                    rec["colorid_vs_csv_ratio"] = (
                        round(int(colorid_mask.sum()) / geom_any, 4) if geom_any else None
                    )
                png = geom["raster"].get(vid)
                if png:
                    gmodel, ganno, err = geom_png_masks(png, grid["W"], grid["H"])
                    if err:
                        rec["geom_png_error"] = err
                    else:
                        def _confusion(mask):
                            both = int((mask & gmodel).sum())
                            c_only = int((mask & ~gmodel).sum())
                            g_only = int((gmodel & ~mask).sum())
                            union = both + c_only + g_only
                            return {
                                "both": both,
                                "colorid_only": c_only,
                                "geom_only": g_only,
                                "iou": round(both / union, 4) if union else None,
                                "geom_only_also_anno": int((gmodel & ~mask & ganno).sum()),
                            }
                        # The PNG draws model INK, so the ink channel is the
                        # like-for-like comparison; fill is reported too
                        # because it is what the metrics consume.
                        rec["cellwise_vs_geom_png_ink"] = _confusion(colorid_ink)
                        rec["cellwise_vs_geom_png_fill"] = _confusion(colorid_mask)
                        write_diff_png(out_dir / f"{sp.stem}.diff_ink.png",
                                       colorid_ink, gmodel)
                        write_diff_png(out_dir / f"{sp.stem}.diff_fill.png",
                                       colorid_mask, gmodel)
        except Exception as e:  # one bad view must not lose the rest
            rec["error"] = f"{type(e).__name__}: {e}"
        summary.append(rec)
        print(json.dumps(rec, indent=1))

    with (out_dir / "occupancy_comparison.json").open("w", encoding="utf-8") as f:
        json.dump({"colorid_dir": str(cdir),
                   "geom_run": ns.geom_run,
                   "primary_threshold": ns.primary_threshold,
                   "views": summary}, f, indent=2)
    print(f"\nsummary -> {out_dir / 'occupancy_comparison.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
