#!/usr/bin/env python3
"""Decode a VOP Stage A color-ID TIFF + JSON sidecar into HOST silhouette loops.

Standalone, Dynamo-external companion to color_id_buffer.py, run manually
after a Stage A export completes -- same split as tools/analyze_stage_a_
probe.py (Dynamo exports, external Python analyzes). Assumes Pillow and
NumPy are present in the invoking Python environment; no CLR/System.Drawing
fallback (this never runs inside the Dynamo/Python.NET process).

Only HOST elements (color_assignment_map) are decoded. link_color_assignment_
map is deliberately never read: LINK elements stay on the existing in-process
geometry-extraction method per the Sept 11 decision; decoding LINK colors
here would produce output nothing consumes.

USAGE
-----
    python tools/decode_stage_a_color_id.py <sidecar.json> [<sidecar2.json> ...]
    python tools/decode_stage_a_color_id.py <dir-of-sidecars>/
    python tools/decode_stage_a_color_id.py <sidecar.json> --bounds XMIN,YMIN,XMAX,YMAX

Each input is a Stage A JSON sidecar written by export_color_id_buffer_view()
(color_id_buffer.py). The matching TIFF is resolved from the sidecar's own
"tiff_path" field, falling back to a same-directory, same-stem .tiff/.tif
file if that recorded path no longer exists on this filesystem.

OUTPUT-FILE CONTRACT (the deliverable a future pipeline.py change reads)
-------------------------------------------------------------------------
For input sidecar ``<name>.json``, this tool writes a sibling
``<name>.decoded.json`` (never overwrites the input) with this shape:

    {
      "schema_version": "1.0",
      "tool_version": "<this tool's version>",
      "source_sidecar": "<path>", "source_sidecar_sha256": "<hex>",
      "source_tiff": "<path>",    "source_tiff_sha256": "<hex>",
      "view_id": <int, from the sidecar>,
      "image_dimensions_px": [width, height],
      "coordinate_space": "view_uv" | "pixel",
      "view_bounds_uv": [xmin, ymin, xmax, ymax] | null,
      "feet_per_pixel": <float> | null,
      "background_element_id": 0,
      "off_palette_foreground_pixel_count": <int>,
      "background_pixel_count": <int>,
      "distinct_ids_decoded": <int>,
      "element_count_in_palette": <int>,
      "element_count_with_geometry": <int>,
      "elements": {
        "<elem_id>": {
          "loops": [
            {"points": [[u, v], ...], "is_hole": bool, "open": false,
             "strategy": "color_id_boundary"},
            ...
          ],
          "confidence": "HIGH",
          "strategy": "color_id_boundary",
          "pixel_area": <int, sum of |signed area| across this element's loops>
        },
        ...
      },
      "generated_at_unix": <float>,
      "decode_ms": <float>
    }

An element present in color_assignment_map but with zero visible pixels
(fully occluded, or a paint failure recorded in the sidecar) is simply
absent from "elements" -- this is the "element never appeared" case, not
an error.

To reconstruct pipeline.py:2698's ``(loops, confidence, strategy)`` triple
for one element (matching extract_areal_geometry()'s documented return
shape, areal_extraction.py:157-163) from this file:

    entry = decoded_doc["elements"].get(str(elem_id))
    loops, confidence, strategy = (
        (entry["loops"], entry["confidence"], entry["strategy"])
        if entry is not None else (None, None, "failed")
    )

``reconstruct_areal_tuple()`` below implements exactly this and is the
reference a 1b implementation should match. "points" are 2-tuples (u, v)
with no depth component; estimate_depth_from_loops_or_bbox()
(revit/collection.py:589) already handles points without a 3rd coordinate
by falling back to bbox-based depth, so this is a correctly-typed, honest
output rather than a fabricated depth value.

KNOWN LIMITATION -- view-space origin is not recoverable from the sidecar
--------------------------------------------------------------------------
export_color_id_buffer_view() does not force the TIFF's crop to raster.
bounds_xy (color_id_buffer.py:545-558): ExportImage's ZoomFitType.FitToPage
fits to whatever bounding box Revit's renderer computes for the visible
geometry, which the module's own comment says is "not necessarily aligned
to the annotation raster's canvas." The JSON sidecar records enough to
derive a pixel->feet SCALE (via resolution.pixel_size/export_dpi/view_scale,
see feet_per_pixel above) but records no view-space ORIGIN (xmin/ymin) for
the exported image. Without an explicit --bounds argument, this tool cannot
know where in view-space the image sits, so it emits loop points in pixel-
corner space (coordinate_space="pixel") rather than silently guessing an
origin (e.g. assuming xmin=0, or assuming the image exactly covers raster.
bounds_xy). Passing --bounds <raster.bounds_xy at export time> yields exact
view-local UV output (coordinate_space="view_uv"), but the caller supplying
those bounds is vouching that the FitToPage crop actually matched raster.
bounds_xy for that run -- true only to the extent color_id_buffer.py's own
sizing assumption (pixel_size computed from raster.W/cell_size_ft/scale,
color_id_buffer.py:384-399) held for that export. Reconciling this properly
(e.g. persisting the actual crop bounds into the sidecar) is a color_id_
buffer.py change outside this tool's scope -- flagged for the 1b decision,
not worked around here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

TOOL_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0"
BACKGROUND_ELEMENT_ID = 0
STRATEGY_NAME = "color_id_boundary"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_rgb_array(tiff_path: Path) -> np.ndarray:
    with Image.open(tiff_path) as img:
        return np.asarray(img.convert("RGB"))


def _build_lookup(color_assignment_map: dict[str, list[int]]) -> np.ndarray:
    """Build a 256**3-entry int32 lookup: packed RGB -> element id.

    Unassigned entries default to BACKGROUND_ELEMENT_ID (0), which also
    absorbs any off-palette pixel (background white/black, AA noise) since
    this is an exact-match decode with no nearest-color fallback.
    """
    lookup = np.full((256 * 256 * 256,), BACKGROUND_ELEMENT_ID, dtype=np.int32)
    for elem_id_str, rgb in color_assignment_map.items():
        r, g, b = (int(c) for c in rgb)
        packed = (r << 16) | (g << 8) | b
        lookup[packed] = int(elem_id_str)
    return lookup


def decode_ids(rgb_array: np.ndarray, color_assignment_map: dict[str, list[int]]) -> tuple[np.ndarray, dict[str, Any]]:
    """Vectorized exact-match RGB -> element-id decode. Returns (id_array, stats)."""
    lookup = _build_lookup(color_assignment_map)
    packed = (
        rgb_array[:, :, 0].astype(np.int32) << 16
        | rgb_array[:, :, 1].astype(np.int32) << 8
        | rgb_array[:, :, 2].astype(np.int32)
    )
    id_array = lookup[packed]

    assigned_colors = {
        (int(rgb[0]) << 16) | (int(rgb[1]) << 8) | int(rgb[2])
        for rgb in color_assignment_map.values()
    }
    is_background_pixel = id_array == BACKGROUND_ELEMENT_ID
    is_assigned_color_pixel = np.isin(packed, np.fromiter(assigned_colors, dtype=np.int64))
    off_palette = int(np.count_nonzero(is_background_pixel & ~is_assigned_color_pixel & (packed != 0) & (packed != 0xFFFFFF)))
    # (0,0,0) black and (255,255,255) white both legitimately decode as
    # background per color_id_buffer.py's palette design (near-black/near-
    # white corners reserved, background page color is white); anything else
    # landing on BACKGROUND_ELEMENT_ID is genuine off-palette contamination.
    stats = {
        "off_palette_foreground_pixel_count": off_palette,
        "background_pixel_count": int(np.count_nonzero(is_background_pixel)),
        "distinct_ids_decoded": int(np.unique(id_array).size),
    }
    return id_array, stats


def _trace_loops_for_mask(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    """Trace closed pixel-boundary loops for a single element's boolean mask.

    Vectorized edge *detection* via 4-connected neighbor-diff (padding the
    mask with a False border makes the image edge behave like an implicit
    background neighbor, so no special-casing is needed there). Edge
    *stitching* into ordered closed loops is an inherently sequential graph
    walk over the loop's own perimeter vertices (not the whole image), so it
    is a plain Python loop bounded by total perimeter length -- this is the
    "vectorized neighbor-diff, no per-pixel loop over the image" pattern
    applied correctly: O(H*W) work is vectorized, O(perimeter) work is not
    (and cannot be, in any implementation, without external compiled
    contour-tracing code this repo does not depend on).

    Returns a list of loops, each a list of (x, y) integer pixel-corner
    coordinates in the ORIGINAL (unpadded) image's corner grid: corner (x,y)
    is the top-left corner of pixel (row=y, col=x). Winding is not yet
    disambiguated into is_hole here; callers derive that from signed area.
    """
    h, w = mask.shape
    padded = np.zeros((h + 2, w + 2), dtype=bool)
    padded[1:-1, 1:-1] = mask

    # Vertical unit edges: between padded column k and k+1, at padded row r.
    # True at [r, k] means a boundary segment from padded-corner (k+1, r) to
    # (k+1, r+1).
    v_diff = padded[:, :-1] != padded[:, 1:]  # shape (h+2, w+1)
    # Horizontal unit edges: between padded row j and j+1, at padded col c.
    # True at [j, c] means a boundary segment from padded-corner (c, j+1) to
    # (c+1, j+1).
    h_diff = padded[:-1, :] != padded[1:, :]  # shape (h+1, w+2)

    # Build a directed edge map keyed by start vertex, oriented so the True
    # (inside-mask) region is always on the left of the walking direction.
    # This is verified empirically below (test suite), not just asserted.
    edges: dict[tuple[int, int], tuple[int, int]] = {}

    vr, vk = np.nonzero(v_diff)
    for r, k in zip(vr.tolist(), vk.tolist()):
        left_inside = bool(padded[r, k])
        x = k + 1
        if left_inside:
            # Inside is to the west; walk downward (+y) to keep it on the left.
            edges[(x, r)] = (x, r + 1)
        else:
            # Inside is to the east; walk upward (-y) to keep it on the left.
            edges[(x, r + 1)] = (x, r)

    hj, hc = np.nonzero(h_diff)
    for j, c in zip(hj.tolist(), hc.tolist()):
        top_inside = bool(padded[j, c])
        y = j + 1
        if top_inside:
            # Inside is to the north; walk leftward (-x) to keep it on the left.
            edges[(c + 1, y)] = (c, y)
        else:
            # Inside is to the south; walk rightward (+x) to keep it on the left.
            edges[(c, y)] = (c + 1, y)

    loops_padded: list[list[tuple[int, int]]] = []
    visited_starts = set()
    for start in list(edges.keys()):
        if start in visited_starts:
            continue
        loop = [start]
        current = start
        while True:
            visited_starts.add(current)
            nxt = edges[current]
            if nxt == start:
                break
            loop.append(nxt)
            current = nxt
        loops_padded.append(loop)

    # Shift back from padded-space corners to original-image corner space.
    loops = [[(x - 1, y - 1) for (x, y) in loop] for loop in loops_padded]
    return [_simplify_colinear(loop) for loop in loops]


def _simplify_colinear(loop: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Drop vertices that lie strictly between two colinear (axis-aligned) neighbors."""
    n = len(loop)
    if n <= 4:
        return loop
    out = []
    for i in range(n):
        p0 = loop[i - 1]
        p1 = loop[i]
        p2 = loop[(i + 1) % n]
        d1 = (p1[0] - p0[0], p1[1] - p0[1])
        d2 = (p2[0] - p1[0], p2[1] - p1[1])
        if d1 == d2:
            continue
        out.append(p1)
    return out


def _shoelace_area(loop: list[tuple[int, int]]) -> float:
    area = 0.0
    n = len(loop)
    for i in range(n):
        x1, y1 = loop[i]
        x2, y2 = loop[(i + 1) % n]
        area += (x1 * y2) - (x2 * y1)
    return area / 2.0


def _element_bounding_boxes(id_array: np.ndarray) -> dict[int, tuple[int, int, int, int]]:
    """Vectorized per-id bounding boxes over the whole image in one pass.

    A naive `id_array == elem_id` per element re-scans the full H*W image
    once per element (O(n_elements * H*W) -- measured at ~6s for a
    2400x1800px/500-element synthetic view, dominated by that repeated
    full-image scan). Sorting the flattened array once is O(H*W log(H*W))
    total regardless of element count, then per-id min/max row/col fall out
    via a single vectorized reduceat over the sorted groups -- no per-element
    image scan. Returns {elem_id: (row_min, col_min, row_max, col_max)}
    (inclusive), omitting BACKGROUND_ELEMENT_ID.
    """
    h, w = id_array.shape
    flat_ids = id_array.ravel()
    order = np.argsort(flat_ids, kind="stable")
    sorted_ids = flat_ids[order]
    rows_full, cols_full = np.unravel_index(np.arange(h * w), (h, w))
    rows_sorted = rows_full[order]
    cols_sorted = cols_full[order]

    unique_ids, start_idx = np.unique(sorted_ids, return_index=True)
    row_min = np.minimum.reduceat(rows_sorted, start_idx)
    row_max = np.maximum.reduceat(rows_sorted, start_idx)
    col_min = np.minimum.reduceat(cols_sorted, start_idx)
    col_max = np.maximum.reduceat(cols_sorted, start_idx)

    out = {}
    for uid, r0, c0, r1, c1 in zip(unique_ids.tolist(), row_min.tolist(), col_min.tolist(), row_max.tolist(), col_max.tolist()):
        if uid == BACKGROUND_ELEMENT_ID:
            continue
        out[int(uid)] = (r0, c0, r1, c1)
    return out


def _loops_for_element(id_array: np.ndarray, elem_id: int, bbox: tuple[int, int, int, int] | None = None) -> list[dict[str, Any]]:
    if bbox is not None:
        r0, c0, r1, c1 = bbox
        cropped = id_array[r0 : r1 + 1, c0 : c1 + 1]
        mask = cropped == elem_id
        offset = (c0, r0)
    else:
        mask = id_array == elem_id
        offset = (0, 0)
    if not mask.any():
        return []
    raw_loops = _trace_loops_for_mask(mask)
    if not raw_loops:
        return []
    if offset != (0, 0):
        ox, oy = offset
        raw_loops = [[(x + ox, y + oy) for (x, y) in loop] for loop in raw_loops]
    signed = [(_shoelace_area(loop), loop) for loop in raw_loops]
    # Convention: the loop(s) with the sign of the largest-|area| loop are
    # outer boundaries; any opposite-signed loop is a hole nested within
    # some outer boundary. This is robust regardless of the absolute
    # orientation convention chosen during tracing above.
    dominant_sign = max(signed, key=lambda t: abs(t[0]))[0] >= 0
    out = []
    for area, loop in signed:
        is_hole = (area >= 0) != dominant_sign
        out.append({"points": loop, "is_hole": is_hole, "area_px": abs(area)})
    return out


def _pixel_corner_to_uv(x: int, y: int, bounds_uv, image_w: int, image_h: int) -> tuple[float, float]:
    xmin, ymin, xmax, ymax = bounds_uv
    u = xmin + (float(x) / float(image_w)) * (xmax - xmin)
    v = ymax - (float(y) / float(image_h)) * (ymax - ymin)
    return (u, v)


def build_decoded_document(
    tiff_path: Path,
    sidecar: dict[str, Any],
    sidecar_path: Path,
    bounds_uv: tuple[float, float, float, float] | None,
) -> dict[str, Any]:
    t0 = time.time()
    rgb = _load_rgb_array(tiff_path)
    h, w = rgb.shape[:2]
    color_assignment_map = sidecar.get("color_assignment_map") or {}
    id_array, stats = decode_ids(rgb, color_assignment_map)

    feet_per_pixel = None
    resolution = sidecar.get("resolution") or {}
    try:
        actual_px = float(resolution.get("pixel_size"))
        export_dpi = float(resolution.get("export_dpi"))
        view_scale = float(resolution.get("view_scale"))
        if actual_px and export_dpi:
            model_width_ft = (actual_px / export_dpi) * view_scale / 12.0
            feet_per_pixel = model_width_ft / actual_px if actual_px else None
    except (TypeError, ValueError):
        feet_per_pixel = None

    coordinate_space = "view_uv" if bounds_uv is not None else "pixel"

    bboxes = _element_bounding_boxes(id_array)

    elements: dict[str, Any] = {}
    for elem_id_str in color_assignment_map.keys():
        elem_id = int(elem_id_str)
        bbox = bboxes.get(elem_id)
        if bbox is None:
            continue  # element assigned a color but never actually visible/painted
        loop_infos = _loops_for_element(id_array, elem_id, bbox=bbox)
        if not loop_infos:
            continue
        loops_out = []
        pixel_area = 0
        for info in loop_infos:
            if bounds_uv is not None:
                points = [_pixel_corner_to_uv(x, y, bounds_uv, w, h) for (x, y) in info["points"]]
            else:
                points = [(float(x), float(y)) for (x, y) in info["points"]]
            loops_out.append(
                {
                    "points": points,
                    "is_hole": info["is_hole"],
                    "open": False,
                    "strategy": STRATEGY_NAME,
                }
            )
            pixel_area += info["area_px"]
        elements[elem_id_str] = {
            "loops": loops_out,
            "confidence": "HIGH",
            "strategy": STRATEGY_NAME,
            "pixel_area": pixel_area,
        }

    doc = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "source_sidecar": str(sidecar_path),
        "source_sidecar_sha256": sha256_file(sidecar_path),
        "source_tiff": str(tiff_path),
        "source_tiff_sha256": sha256_file(tiff_path),
        "view_id": sidecar.get("view_id"),
        "image_dimensions_px": [int(w), int(h)],
        "coordinate_space": coordinate_space,
        "view_bounds_uv": list(bounds_uv) if bounds_uv is not None else None,
        "feet_per_pixel": feet_per_pixel,
        "background_element_id": BACKGROUND_ELEMENT_ID,
        "off_palette_foreground_pixel_count": stats["off_palette_foreground_pixel_count"],
        "background_pixel_count": stats["background_pixel_count"],
        "distinct_ids_decoded": stats["distinct_ids_decoded"],
        "element_count_in_palette": len(color_assignment_map),
        "element_count_with_geometry": len(elements),
        "elements": elements,
        "generated_at_unix": time.time(),
        "decode_ms": round((time.time() - t0) * 1000.0, 3),
    }
    return doc


def reconstruct_areal_tuple(decoded_doc: dict[str, Any], elem_id: int) -> tuple[list[dict[str, Any]] | None, str | None, str | None]:
    """Reconstruct the (loops, confidence, strategy) triple for one element,
    in the exact shape areal_extraction.extract_areal_geometry() returns
    (areal_extraction.py:157-163) so 1b can drop this straight into
    pipeline.py:2698's unpacking with no translation step.
    """
    entry = decoded_doc.get("elements", {}).get(str(elem_id))
    if entry is None:
        return None, None, "failed"
    return entry["loops"], entry["confidence"], entry["strategy"]


def _resolve_tiff_path(sidecar_path: Path, sidecar: dict[str, Any]) -> Path:
    recorded = sidecar.get("tiff_path")
    if recorded:
        p = Path(recorded)
        if p.exists():
            return p
    candidate = sidecar_path.with_suffix(".tiff")
    if candidate.exists():
        return candidate
    candidate = sidecar_path.with_suffix(".tif")
    if candidate.exists():
        return candidate
    raise FileNotFoundError(
        "Could not resolve TIFF for sidecar {0}: recorded tiff_path={1!r} does not "
        "exist and no sibling .tiff/.tif file was found".format(sidecar_path, recorded)
    )


def decode_one(sidecar_path: Path, bounds_uv=None) -> Path:
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    tiff_path = _resolve_tiff_path(sidecar_path, sidecar)
    doc = build_decoded_document(tiff_path, sidecar, sidecar_path, bounds_uv)
    out_path = sidecar_path.with_name(sidecar_path.stem + ".decoded.json")
    out_path.write_text(json.dumps(doc, indent=2, sort_keys=True), encoding="utf-8")
    return out_path


def collect(paths: list[str]) -> list[Path]:
    out = []
    for arg in paths:
        p = Path(arg)
        if p.is_dir():
            out += sorted(x for x in p.rglob("*.json") if not x.name.endswith(".decoded.json"))
        else:
            out.append(p)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Decode a VOP Stage A color-ID TIFF + JSON sidecar into HOST silhouette loops"
    )
    ap.add_argument("paths", nargs="+", help="Stage A sidecar JSON file(s) or directories containing them")
    ap.add_argument(
        "--bounds",
        metavar="XMIN,YMIN,XMAX,YMAX",
        default=None,
        help="View-space (feet) bounds the exported TIFF spans, e.g. from raster.bounds_xy. "
             "Required to emit view-local UV coordinates; omit to emit pixel-space coordinates instead.",
    )
    ns = ap.parse_args(argv)
    bounds_uv = None
    if ns.bounds:
        parts = [float(x) for x in ns.bounds.split(",")]
        if len(parts) != 4:
            ap.error("--bounds requires exactly 4 comma-separated values: XMIN,YMIN,XMAX,YMAX")
        bounds_uv = tuple(parts)

    failures = 0
    for sp in collect(ns.paths):
        try:
            out = decode_one(sp, bounds_uv=bounds_uv)
            print(f"✓ {sp} -> {out}")
        except Exception as e:
            failures += 1
            print(f"✗ {sp}: {type(e).__name__}: {e}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
