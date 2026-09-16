#!/usr/bin/env python3
"""Decode a VOP Stage A color-ID TIFF + JSON sidecar into HOST silhouette loops.

Standalone, Dynamo-external companion to color_id_buffer.py, run manually
after a Stage A export completes -- same split as tools/analyze_stage_a_
probe.py (Dynamo exports, external Python analyzes). Assumes Pillow and
NumPy are present in the invoking Python environment; no CLR/System.Drawing
fallback (this never runs inside the Dynamo/Python.NET process).

Only HOST elements (color_assignment_map) are decoded. link_category_color_
map is deliberately never read: LINK elements are colored per-category via
view filters (one shared color per category, not a unique per-element ID),
so decoding them here would only ever reconstruct category-level blobs, not
individual elements -- LINK elements stay on the existing in-process
geometry-extraction method per the Sept 11 decision.

USAGE
-----
    python tools/decode_stage_a_color_id.py <sidecar.json> [<sidecar2.json> ...]
    python tools/decode_stage_a_color_id.py <dir-of-sidecars>/
    python tools/decode_stage_a_color_id.py <sidecar.json> --bounds XMIN,YMIN,XMAX,YMAX

Each input is a Stage A JSON sidecar written by export_color_id_buffer_view()
(color_id_buffer.py). The matching TIFF is resolved from the sidecar's own
"tiff_path" field, falling back to a same-directory, same-stem .tiff/.tif
file if that recorded path no longer exists on this filesystem.

Since export_color_id_buffer_view() now crops the export to raster.bounds_xy
and persists that same rectangle into the sidecar's "bounds_xy" field
(color_id_buffer.py's own JSON, not this tool's output "view_bounds_uv"),
--bounds is normally unnecessary: when omitted, decode_one() reads
"bounds_xy" from the sidecar itself and only falls back to pixel-space
output when that field is absent or null (e.g. an older sidecar, or a
capture where the crop could not be applied -- see KNOWN LIMITATION below).
An explicit --bounds still overrides the sidecar's own field when given.

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
      "capture_reliable": <bool>,
      "capture_unreliable_reason": <str> | null,
      "image_dimensions_px": [width, height],
      "coordinate_space": "view_uv" | "pixel",
      "view_bounds_uv": [xmin, ymin, xmax, ymax] | null,
      "model_crop_offset_uv": [dxmin, dymin, dxmax, dymax] | null,
      "grid_bounds_uv": [xmin, ymin, xmax, ymax] | null,
      "feet_per_pixel": <float> | null,
      "feet_per_pixel_unreliable_reason": <str> | null,
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
          "confidence": "HIGH" | "MEDIUM",
          "strategy": "color_id_boundary",
          "pixel_area": <int, sum of |signed area| across this element's loops>
        },
        ...
      },
      "generated_at_unix": <float>,
      "decode_ms": <float>
    }

"confidence" is "HIGH" only when "capture_reliable" is true for the whole
document (Stage A confirmed DisplayStyle.FlatColors and smooth-edges-off,
color_id_buffer.py:560-624 -- see _capture_reliability()); otherwise every
element in this decode is "MEDIUM", since the render is not a guaranteed
exact-match, anti-aliasing-off capture and must not be handed AREAL+HIGH
occlusion authority (pipeline.py:2443-2444) it hasn't earned. Similarly,
"feet_per_pixel" is null (with "feet_per_pixel_unreliable_reason" set) when
color_id_buffer.py capped the request and the sidecar predates the
"pre_cap_px" field, so "requested_pixel_size" is the capped value rather
than the model's true desired width and nothing records the latter -- see
_capture_reliability() and the feet_per_pixel computation in
build_decoded_document() for exactly what is checked.

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

KNOWN LIMITATION -- crop not guaranteed on every capture
---------------------------------------------------------
export_color_id_buffer_view() sets view.CropBox/CropBoxActive to a rectangle
it computes before export (color_id_buffer.py's compute_model_crop() --
raster.model_clip_bounds, the narrower model-only crop, when available,
else raster.bounds_xy unchanged) and ExportImage's ZoomFitType.FitToPage
then fits to that explicit crop rather than an auto-computed visible-
geometry extent, so on a normal capture the TIFF's pixel grid corresponds
exactly to whichever rectangle was actually used and the sidecar's
"bounds_xy" field records that same rectangle (not recomputed -- the exact
tuple that was set). This tool reads it automatically (see USAGE above) and
uses it directly for pixel<->UV conversion: that rectangle, not raster.
bounds_xy, is what the TIFF's pixel grid actually spans, and pixel->UV
conversion is a scale relationship (feet-per-pixel), not just an origin --
using the wrong-sized rectangle would misscale every decoded point, not
merely offset it.

The crop can still fail to apply on a given capture -- no raster/bounds_xy
was passed to export_color_id_buffer_view(), the view has no CropBox (some
non-croppable view types), or setting CropBox/CropBoxActive raised -- in
which case color_id_buffer.py records "bounds_xy": null and the TIFF's
extent is whatever FitToPage auto-computed instead. This tool cannot
recover a view-space origin for that case from the sidecar alone, so it
falls back to pixel-corner space (coordinate_space="pixel") rather than
silently guessing an origin. An explicit --bounds argument can still be
supplied by a caller who otherwise knows the true crop for such a capture.

MODEL_CROP_OFFSET_UV -- reconstructing raster.bounds_xy, not a point shift
----------------------------------------------------------------------------
When color_id_buffer.py narrows the crop to raster.model_clip_bounds, its
sidecar also records "model_crop_offset_uv": (dxmin, dymin, dxmax, dymax),
the four-corner relationship between that narrower crop (the sidecar's own
"bounds_xy" field) and the raster's own, possibly wider, bounds_xy:

    raster.bounds_xy.xmin == bounds_xy.xmin - dxmin   (equivalently:
    raster.bounds_xy.ymin == bounds_xy.ymin - dymin    bounds_xy.CORNER ==
    raster.bounds_xy.xmax == bounds_xy.xmax - dxmax    raster.bounds_xy.CORNER
    raster.bounds_xy.ymax == bounds_xy.ymax - dymax    + offset.CORNER)

This tool reconstructs raster.bounds_xy from it as "grid_bounds_uv" in the
output (informational -- e.g. for a future consumer that needs to know the
full shared cell grid this capture's narrower crop sits within, not just
this capture's own extent). It is NOT added to decoded pixel/UV coordinates
anywhere in this tool: "view_bounds_uv" (and therefore every decoded point)
is already computed against the sidecar's own "bounds_xy" -- the rectangle
this specific TIFF was actually cropped to -- which is already the correct,
final, shared view-local UV coordinate frame (the same one raster.bounds_xy
is expressed in; narrowing which rectangle gets rendered does not change
that frame, only how much of it this one capture covers). Adding the offset
to those already-correct points would shift them a second time and corrupt
them. The offset is reconstruction/provenance data about the RECTANGLES
only, applied by grid_bounds_uv's corner arithmetic above -- never a
per-point translation.
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

# Repo root (parent of tools/) on sys.path so `vop_interwoven` is importable
# regardless of the caller's current working directory -- this tool is meant
# to be invoked as `python tools/decode_stage_a_color_id.py ...` from
# anywhere, same as tools/analyze_stage_a_probe.py, not only from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Pure-Python constant, safe to import outside Revit/Dynamo (color_id_buffer.py
# defers all Revit API imports to inside function bodies).
from vop_interwoven.color_id_buffer import MAX_STAGE_A_PIXEL_SIZE

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

    # Every directed edge is oriented so the True (inside-mask) region is on
    # the left of the walking direction, and tagged with the padded-space
    # (row, col) of the inside pixel that "owns" it. At a diagonal-only pixel
    # contact (a 2x2 neighborhood like TL=True, BR=True, TR=BL=False), the
    # shared corner vertex is a degree-4 pinch point: TWO distinct pixels each
    # contribute one outgoing edge from that vertex, which a plain
    # vertex->vertex dict cannot represent (a second assignment silently
    # overwrites the first, and the walk below then never returns to its
    # start -- an infinite loop, not just a wrong answer). Edges are stored
    # per-start-vertex as a list, and traversal disambiguates a multi-edge
    # vertex by continuing with the outgoing edge that shares the same owner
    # pixel as the incoming edge, so each pixel's own contour is walked to
    # completion without jumping across the pinch into a different pixel's
    # loop.
    out_edges: dict[tuple[int, int], list[tuple[tuple[int, int], tuple[int, int]]]] = {}

    def _add_edge(start, end, owner):
        out_edges.setdefault(start, []).append((end, owner))

    vr, vk = np.nonzero(v_diff)
    for r, k in zip(vr.tolist(), vk.tolist()):
        left_inside = bool(padded[r, k])
        x = k + 1
        if left_inside:
            # Inside is to the west; walk downward (+y) to keep it on the left.
            _add_edge((x, r), (x, r + 1), (r, k))
        else:
            # Inside is to the east; walk upward (-y) to keep it on the left.
            _add_edge((x, r + 1), (x, r), (r, k + 1))

    hj, hc = np.nonzero(h_diff)
    for j, c in zip(hj.tolist(), hc.tolist()):
        top_inside = bool(padded[j, c])
        y = j + 1
        if top_inside:
            # Inside is to the north; walk leftward (-x) to keep it on the left.
            _add_edge((c + 1, y), (c, y), (j, c))
        else:
            # Inside is to the south; walk rightward (+x) to keep it on the left.
            _add_edge((c, y), (c + 1, y), (j + 1, c))

    # Consume edges as (start, index-into-out_edges[start]) so a vertex with
    # several outgoing edges (a pinch point) tracks each one's used state
    # independently, rather than per-vertex as if only one could ever exist.
    used = {start: [False] * len(lst) for start, lst in out_edges.items()}

    def _take(vertex, preferred_owner):
        candidates = out_edges.get(vertex, [])
        used_here = used[vertex]
        choice = None
        for i, (_end, owner) in enumerate(candidates):
            if not used_here[i] and owner == preferred_owner:
                choice = i
                break
        if choice is None:
            for i, (_end, _owner) in enumerate(candidates):
                if not used_here[i]:
                    choice = i
                    break
        if choice is None:
            return None
        used_here[choice] = True
        end, owner = candidates[choice]
        return end, owner

    loops_padded: list[list[tuple[int, int]]] = []
    for start, candidates in out_edges.items():
        for i in range(len(candidates)):
            if used[start][i]:
                continue
            used[start][i] = True
            end0, owner0 = candidates[i]
            loop = [start]
            current, owner = end0, owner0
            while current != start:
                loop.append(current)
                nxt = _take(current, owner)
                if nxt is None:
                    # Defensive: a well-formed mask boundary always closes: every
                    # vertex's in-degree equals its out-degree. Bail out rather
                    # than hang if that invariant is ever violated.
                    break
                current, owner = nxt
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


# Row-chunk budget for _element_bounding_boxes: bounds the several full-chunk
# int64 temporaries (order/row-index/col-index arrays, each 8 bytes/pixel) to
# roughly this many pixels at once. At the producer's documented ceiling
# (MAX_STAGE_A_PIXEL_SIZE, a square image at that per-axis limit), sorting the WHOLE
# flattened image in one pass -- as an earlier version of this function did --
# allocates several such int64 arrays simultaneously (order, unravelled row/
# col indices, their sorted copies) for ~1.8GB each, ~10GB+ peak; verified by
# measuring actual numpy array sizes at that resolution. Chunking by rows
# keeps the same vectorized sort+reduceat technique (no per-pixel Python
# loop) but bounds peak temporary memory to one chunk's worth regardless of
# total image size.
_BBOX_CHUNK_PIXEL_BUDGET = 20_000_000


def _element_bounding_boxes(id_array: np.ndarray) -> dict[int, tuple[int, int, int, int]]:
    """Vectorized per-id bounding boxes, computed in row chunks to bound memory.

    A naive `id_array == elem_id` per element re-scans the full H*W image
    once per element (O(n_elements * H*W) -- measured at ~6s for a
    2400x1800px/500-element synthetic view, dominated by that repeated
    full-image scan). Sorting one chunk's flattened pixels is O(chunk*log(chunk));
    per-id min/max row/col within the chunk fall out via a single vectorized
    reduceat over the sorted groups -- no per-element or per-pixel Python
    loop, and no full-image-sized temporary array. Chunk bounding boxes are
    merged into a running per-id result as each chunk completes. Returns
    {elem_id: (row_min, col_min, row_max, col_max)} (inclusive), omitting
    BACKGROUND_ELEMENT_ID.
    """
    h, w = id_array.shape
    rows_per_chunk = max(1, _BBOX_CHUNK_PIXEL_BUDGET // max(1, w))

    out: dict[int, tuple[int, int, int, int]] = {}
    for row_start in range(0, h, rows_per_chunk):
        row_end = min(h, row_start + rows_per_chunk)
        chunk = id_array[row_start:row_end, :]
        chunk_h = row_end - row_start

        flat_ids = chunk.ravel()
        order = np.argsort(flat_ids, kind="stable")
        sorted_ids = flat_ids[order]
        rows_full, cols_full = np.unravel_index(np.arange(chunk_h * w), (chunk_h, w))
        rows_sorted = rows_full[order]
        cols_sorted = cols_full[order]

        unique_ids, start_idx = np.unique(sorted_ids, return_index=True)
        row_min = np.minimum.reduceat(rows_sorted, start_idx)
        row_max = np.maximum.reduceat(rows_sorted, start_idx)
        col_min = np.minimum.reduceat(cols_sorted, start_idx)
        col_max = np.maximum.reduceat(cols_sorted, start_idx)

        for uid, r0, c0, r1, c1 in zip(
            unique_ids.tolist(), row_min.tolist(), col_min.tolist(), row_max.tolist(), col_max.tolist()
        ):
            if uid == BACKGROUND_ELEMENT_ID:
                continue
            r0 += row_start
            r1 += row_start
            prev = out.get(int(uid))
            if prev is None:
                out[int(uid)] = (r0, c0, r1, c1)
            else:
                pr0, pc0, pr1, pc1 = prev
                out[int(uid)] = (min(pr0, r0), min(pc0, c0), max(pr1, r1), max(pc1, c1))
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


def _capture_reliability(sidecar: dict[str, Any]) -> tuple[bool, str | None]:
    """Whether this view's Stage A capture is a guaranteed clean, exact-match
    render, per the producer's own recorded settings -- not assumed.

    export_color_id_buffer_view() only guarantees exact-match colors (no
    lighting/shading tint, no anti-aliased edge blending) when it actually
    achieved DisplayStyle.FlatColors AND successfully disabled smooth edges
    (color_id_buffer.py:560-624). On older Revit hosts, or a view type that
    doesn't expose these settings, it falls back to plain Shading (still
    lit/shadowed) or leaves the view's display unchanged, and records exactly
    that in "applied_display_style"/"applied_smooth_edges" -- it does not
    retroactively fail the export. Assuming HIGH confidence regardless would
    hand a possibly tinted/anti-aliased, exact-match-only decode occlusion
    authority it has not earned (AREAL HIGH is the only elem_class/confidence
    combination that gates occlusion, pipeline.py:2443-2444).
    """
    display_style = sidecar.get("applied_display_style")
    smooth_edges = sidecar.get("applied_smooth_edges")
    if display_style == "FlatColors" and smooth_edges is False:
        return True, None
    return False, (
        "Stage A did not confirm a clean flat-color, anti-aliasing-off capture for "
        "this view (applied_display_style={0!r}, applied_smooth_edges={1!r}); "
        "decoded colors may be lit/shaded or anti-aliased rather than exact palette "
        "matches".format(display_style, smooth_edges)
    )


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
    model_crop_offset_uv: tuple[float, float, float, float] | None = None,
) -> dict[str, Any]:
    t0 = time.time()
    rgb = _load_rgb_array(tiff_path)
    h, w = rgb.shape[:2]
    color_assignment_map = sidecar.get("color_assignment_map") or {}
    id_array, stats = decode_ids(rgb, color_assignment_map)

    feet_per_pixel = None
    feet_per_pixel_unreliable_reason = None
    resolution = sidecar.get("resolution") or {}
    try:
        actual_px = float(resolution.get("pixel_size"))
        if bounds_uv is not None:
            # The rectangle this TIFF was actually cropped to is known
            # directly (bounds_uv, from the sidecar's own "bounds_xy" --
            # see decode_one()): its width in feet divided by the actual
            # pixel width IS feet-per-pixel exactly, regardless of any
            # PixelSize backoff (color_id_buffer.py:285-316) or crop
            # narrowing (color_id_buffer.py's compute_model_crop() can crop
            # to raster.model_clip_bounds, narrower than the raster.W/cell_
            # size_ft/scale math the estimate below is derived from) --
            # unlike that estimate, this needs no MAX_STAGE_A_PIXEL_SIZE
            # clamp caveat at all, since it never goes through requested_
            # pixel_size.
            crop_width_ft = float(bounds_uv[2]) - float(bounds_uv[0])
            if actual_px and crop_width_ft:
                feet_per_pixel = crop_width_ft / actual_px
        else:
            # No known crop rectangle (coordinate_space="pixel" fallback) --
            # estimate physical width from raster.W/cell_size_ft/scale via
            # requested_pixel_size (not the possibly-backed-off actual
            # pixel_size, which is what raster.W/cell_size_ft/scale was
            # originally sized to, color_id_buffer.py:384-399: the model
            # width, in pixels, the export was SUPPOSED to be. Deriving
            # model_width_ft from actual_px instead would make it cancel out
            # of feet_per_pixel entirely, independent of actual_px, silently
            # reporting the pre-backoff scale even when Revit's PixelSize
            # backoff actually shrank the export -- wrong by requested_px/
            # actual_px on any degraded-resolution export). This estimate is
            # only as good as FitToPage's own auto-computed extent matching
            # it, since there is no known crop rectangle to measure directly.
            # "pre_cap_px" is the UNCAPPED request, written since the
            # two-axis cap landed, and it is the only field that still means
            # "the model's real paper extent in pixels" once a cap fires.
            # "requested_pixel_size" is post-cap, so preferring it on a
            # capped export would understate model_width_ft below.
            requested_px = resolution.get("pre_cap_px")
            if not requested_px:
                requested_px = resolution.get("requested_pixel_size")
            requested_px = float(requested_px) if requested_px else actual_px
            export_dpi = float(resolution.get("export_dpi"))
            view_scale = float(resolution.get("view_scale"))
            if actual_px and export_dpi:
                if not resolution.get("pre_cap_px") and requested_px >= MAX_STAGE_A_PIXEL_SIZE:
                    # Sidecars written before the two-axis cap landed carry
                    # only the post-cap "requested_pixel_size": the true
                    # pre-cap desired width (from raster.W/cell_size_ft) was
                    # never persisted, so requested_px here is the cap value,
                    # not the model's real paper extent, and model_width_ft/
                    # feet_per_pixel would be silently wrong (underestimated)
                    # even when Revit accepted this exact pixel count with no
                    # further backoff. Current sidecars record "pre_cap_px"
                    # and take the branch above instead; for the older ones
                    # there is no way to recover the true value, so report
                    # the gap rather than a wrong number.
                    feet_per_pixel_unreliable_reason = (
                        "requested_pixel_size ({0}) is at or above MAX_STAGE_A_PIXEL_SIZE "
                        "({1}) and this sidecar carries no pre_cap_px, so it predates the "
                        "two-axis cap and the true pre-cap desired width is not "
                        "recoverable from it".format(int(requested_px), MAX_STAGE_A_PIXEL_SIZE)
                    )
                else:
                    model_width_ft = (requested_px / export_dpi) * view_scale / 12.0
                    feet_per_pixel = model_width_ft / actual_px
    except (TypeError, ValueError):
        feet_per_pixel = None

    coordinate_space = "view_uv" if bounds_uv is not None else "pixel"

    grid_bounds_uv = None
    if bounds_uv is not None and model_crop_offset_uv is not None:
        # Reconstruct raster.bounds_xy (the shared cell grid's own rectangle,
        # possibly wider than this capture's own crop) from bounds_uv (this
        # capture's actual crop) + the offset color_id_buffer.py recorded
        # between them. See compute_model_crop()'s docstring (color_id_
        # buffer.py) and this module's own MODEL_CROP_OFFSET_UV section
        # above for the exact convention -- informational only, never
        # applied to the decoded points themselves (those are already
        # correct in the shared view-local UV frame once decoded against
        # bounds_uv, above).
        dxmin, dymin, dxmax, dymax = model_crop_offset_uv
        grid_bounds_uv = [
            float(bounds_uv[0]) - float(dxmin),
            float(bounds_uv[1]) - float(dymin),
            float(bounds_uv[2]) - float(dxmax),
            float(bounds_uv[3]) - float(dymax),
        ]

    capture_reliable, capture_unreliable_reason = _capture_reliability(sidecar)
    # AREAL occlusion authority requires HIGH specifically (pipeline.py:2443-
    # 2444); MEDIUM keeps this as visible, non-occluding proxy geometry --
    # the same policy AREAL's own MEDIUM/LOW confidence already gets
    # elsewhere in the pipeline -- rather than silently asserting exact-match
    # fidelity the producer itself did not confirm for this view.
    element_confidence = "HIGH" if capture_reliable else "MEDIUM"

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
            "confidence": element_confidence,
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
        "capture_reliable": capture_reliable,
        "capture_unreliable_reason": capture_unreliable_reason,
        "image_dimensions_px": [int(w), int(h)],
        "coordinate_space": coordinate_space,
        "view_bounds_uv": list(bounds_uv) if bounds_uv is not None else None,
        "model_crop_offset_uv": list(model_crop_offset_uv) if model_crop_offset_uv is not None else None,
        "grid_bounds_uv": grid_bounds_uv,
        "feet_per_pixel": feet_per_pixel,
        "feet_per_pixel_unreliable_reason": feet_per_pixel_unreliable_reason,
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
    model_crop_offset_uv = None
    if bounds_uv is None:
        sidecar_bounds = sidecar.get("bounds_xy")
        if sidecar_bounds is not None and len(sidecar_bounds) == 4:
            bounds_uv = tuple(float(x) for x in sidecar_bounds)
            # Only trust the sidecar's own offset when bounds_uv also came
            # from the sidecar: an explicit --bounds override means the
            # caller is asserting a crop rectangle other than what
            # color_id_buffer.py recorded, and the offset (computed against
            # THAT recorded rectangle) would no longer describe the
            # override's relationship to raster.bounds_xy.
            sidecar_offset = sidecar.get("model_crop_offset_uv")
            if sidecar_offset is not None and len(sidecar_offset) == 4:
                model_crop_offset_uv = tuple(float(x) for x in sidecar_offset)
    tiff_path = _resolve_tiff_path(sidecar_path, sidecar)
    doc = build_decoded_document(tiff_path, sidecar, sidecar_path, bounds_uv, model_crop_offset_uv)
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
             "Normally unnecessary: each sidecar's own \"bounds_xy\" field is used "
             "automatically when this is omitted. Overrides the sidecar's field when given; "
             "falls back to pixel-space coordinates only if neither is available.",
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
