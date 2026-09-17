#!/usr/bin/env python3
"""LINK element-identity resolver for VOP Stage A color-ID captures (Phase 1b).

Standalone, Dynamo-external companion to color_id_buffer.py and tools/
decode_stage_a_color_id.py -- same split as those two (Dynamo exports/
persists, external Python analyzes). Consumes a Stage A JSON sidecar (the
raw sidecar export_color_id_buffer_view() writes, NOT decode_stage_a_
color_id.py's ``*.decoded.json`` -- that tool deliberately never reads
link_category_color_map, since LINK elements share one flat color per
category, not a unique per-element ID) plus its TIFF, and maps each LINK
category's color blob(s) back to a specific candidate LINK element.

WHY THIS EXISTS
----------------
LINK elements are colored per-category (one shared color for every LINK
element of that category in the view -- see color_id_buffer.py's
_apply_link_category_filters), not per-element like HOST elements
(color_assignment_map). A category's color mask in the TIFF can therefore
be made of several disconnected blobs -- one per LINK element of that
category actually visible -- with no way to tell which blob belongs to
which element from color alone. This module recovers that identity,
advisory-only (never authoritative), by:

  1. Segmenting each category's color mask into connected components
     (blobs) -- the same NumPy flood-fill pattern tools/analyze_stage_a_
     probe.py's connected_components() uses, extended to keep each blob's
     own pixel membership (not just its size).
  2. For each blob, gathering candidate LINK elements of that category (from
     the sidecar's "near_face_w_map"."link" section -- Phase 1b's additive
     per-element bbox/near-face-W collection in color_id_buffer.py) whose
     projected bbox footprint (converted to this TIFF's pixel space using
     the same bounds_xy the producer cropped to, and decode_stage_a_color_
     id.py's own pixel<->UV convention) intersects the blob's own pixel
     bounding box.
  3. Scoring each candidate by (blob pixels inside its own footprint) /
     (footprint pixel area) -- how much of THIS candidate's own bbox
     footprint the blob actually fills.
  4. Selecting the blob's identity as the highest-scoring candidate, tie-
     broken by nearest near_face_w (smaller = closer to the viewer -- see
     revit/collection.py's estimate_nearest_depth_from_bbox convention).

Output is advisory: {blob_id: {elem_id, confidence_score, ...}}, never fed
back into color_assignment_map or link_category_color_map, and never
treated as occlusion ground truth the way HOST color-ID decoding is.

USAGE
-----
    python tools/link_identity_resolver.py <sidecar.json> [<sidecar2.json> ...]
    python tools/link_identity_resolver.py <dir-of-sidecars>/

For input sidecar ``<name>.json``, writes a sibling
``<name>.link_identity.json`` (never overwrites the input).

OUT OF SCOPE (see PHASE 1b prompt)
------------------------------------
DWG occlusion/clipping against this data: cad_curves isn't wired into Stage
A yet, so there is no DWG point data to compare against. This module adds
no DWG-specific code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

# Repo root (parent of tools/) on sys.path so the sibling leaf module below is
# importable however this tool is invoked. It deliberately reaches for
# clamp_pad_geometry ONLY: that module is standard-library-only, so sharing it
# does NOT put vop_interwoven -- or tools/decode_stage_a_color_id.py, which
# does import vop_interwoven -- on this standalone tool's dependency path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.clamp_pad_geometry import clamp_pad_geometry  # noqa: E402

TOOL_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0"

# Same inexpensive-component-count size cap as tools/analyze_stage_a_probe.py's
# connected_components(). A category mask is usually a small subset of the
# full image, but the same worst-case guard applies here for consistency.
MAX_MASK_PIXELS_FOR_COMPONENTS = 4_000_000

# A blob's SOLE candidate must cover at least this fraction of its own
# projected bbox footprint to be reported with HIGH confidence; below this
# threshold the candidate is still selected (this resolver is advisory, not
# authoritative) but confidence_label is LOW so a downstream consumer can
# choose not to trust it without human review. Explicit, not implicit --
# CLAUDE.md's refactor rule #2 (Explicit Semantics).
CONFIDENCE_HIGH_THRESHOLD = 0.75

# Two (or more) candidates each explaining at least this fraction of one
# blob's own pixels mark that blob as a plausible MERGER of several
# touching/overlapping same-color elements (e.g. adjoining walls of the
# same category) rather than a single element -- connected-component
# segmentation cannot see the seam between them by color alone. When this
# fires, the blob's assignment is never reported HIGH confidence and lists
# every qualifying candidate, not just the top-ranked one, so a downstream
# consumer knows to treat that single blob as ambiguous instead of trusting
# a single silently-chosen winner. Explicit, not implicit -- CLAUDE.md's
# refactor rule #2 (Explicit Semantics).
_MULTI_CANDIDATE_BLOB_SHARE_THRESHOLD = 0.3

# Strictly-decreasing tie-break step applied by rank after sorting candidates
# by (coverage desc, near_face_w asc): guarantees every candidate for the
# same blob gets a numerically DISTINCT confidence_score (never a uniform
# tie) without ever reordering the coverage-based ranking, since it is far
# smaller than the smallest non-zero difference in a bbox-pixel coverage
# fraction this resolver would otherwise treat as meaningfully tied.
_RANK_TIEBREAK_EPS = 1e-9


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


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


def _load_rgb_array(tiff_path: Path) -> np.ndarray:
    with Image.open(tiff_path) as img:
        return np.asarray(img.convert("RGB"))


def connected_components_with_pixels(mask: np.ndarray) -> list[dict[str, Any]] | None:
    """Segment a boolean mask into connected components (4-connected flood
    fill), returning each blob's own pixel coordinates and bbox.

    Same NumPy stack-based flood-fill technique as tools/analyze_stage_a_
    probe.py's connected_components() (reusing the pattern, not just the
    function, per this module's PHASE 1b prerequisite read) -- extended to
    keep each blob's actual pixel membership (xs, ys) and pixel-space
    bounding box, not just its size: scoring a candidate against a blob
    needs pixel-level intersection with the candidate's own footprint
    rectangle, not just a component size.

    Returns None (skip) if the mask's own foreground PIXEL COUNT is too
    large for this inexpensive, single-threaded flood fill.

    The size cap is applied to the foreground's actual pixel count, not any
    bounding-box area (full image OR the foreground's own union AABB): the
    flood fill's real cost is the per-pixel Python-level loop below
    (`for x, y in zip(...)`), which is O(foreground pixel count) regardless
    of how those pixels are laid out spatially -- gating on the full image's
    dimensions would mark nearly every real, sheet-sized capture "skipped"
    outright (an ordinary 3600x2700 TIFF at the producer's default 150 DPI
    already exceeds MAX_MASK_PIXELS_FOR_COMPONENTS on image area alone), and
    gating on the foreground's own union bounding-box area is hardly better:
    a LINK category's elements scattered across a sheet (e.g. walls spread
    over a whole floor plan) can still produce a union AABB spanning nearly
    the entire image even though the foreground itself is a tiny fraction of
    it. Only the actual pixel count reflects the loop's real workload.
    Cropping to the foreground's own AABB is still done below, but purely as
    a memory-locality optimization for the `seen`/`cropped` arrays -- it
    plays no part in the pass/skip decision.
    """
    ys_full, xs_full = np.nonzero(mask)
    if ys_full.size == 0:
        return []
    if ys_full.size > MAX_MASK_PIXELS_FOR_COMPONENTS:
        return None
    y0, y1 = int(ys_full.min()), int(ys_full.max())
    x0, x1 = int(xs_full.min()), int(xs_full.max())
    cropped = mask[y0:y1 + 1, x0:x1 + 1]
    ch, cw = cropped.shape

    seen = np.zeros_like(cropped, dtype=bool)
    blobs: list[dict[str, Any]] = []
    ys, xs = np.nonzero(cropped)
    for x, y in zip(xs.tolist(), ys.tolist()):
        if seen[y, x]:
            continue
        stack = [(x, y)]
        seen[y, x] = True
        pixels = []
        while stack:
            px, py = stack.pop()
            pixels.append((px, py))
            for nx, ny in ((px + 1, py), (px - 1, py), (px, py + 1), (px, py - 1)):
                if 0 <= nx < cw and 0 <= ny < ch and cropped[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((nx, ny))
        # Shift back from the cropped sub-array's local coordinates to the
        # original (full mask/image) pixel-corner space.
        pxs = np.asarray([p[0] for p in pixels], dtype=np.int64) + x0
        pys = np.asarray([p[1] for p in pixels], dtype=np.int64) + y0
        blobs.append({
            "pixel_count": len(pixels),
            "bbox_px": [int(pxs.min()), int(pys.min()), int(pxs.max()), int(pys.max())],
            "xs": pxs,
            "ys": pys,
        })
    blobs.sort(key=lambda b: b["pixel_count"], reverse=True)
    for i, b in enumerate(blobs):
        b["blob_id"] = i
    return blobs


def _uv_rect_to_pixel_bbox(bbox_corners_uv, bounds_uv, image_w, image_h):
    """Inverse of decode_stage_a_color_id.py's _pixel_corner_to_uv(): map a
    UV rectangle (4 corners, as persisted by revit/collection.py's
    project_bbox_corners_uv()) into this TIFF's pixel-space AABB.

    Models Revit ExportImage's aspect-clamp padding, because the forward
    mapping this inverts does. ExportImage refuses to export beyond its
    aspect limit and pads the short axis symmetrically, so feet-per-pixel
    comes from the UNPADDED axis -- the larger of the two ratios, since the
    padded axis carries pixels its extent does not cover and therefore
    understates its own ratio -- and the other axis's pad follows from it:

        fpp   = max(crop_u / image_w, crop_v / image_h)
        pad_x = (image_w - crop_u / fpp) / 2
        pad_y = (image_h - crop_v / fpp) / 2
        x     = (u - xmin) / fpp + pad_x
        y     = (ymax - v) / fpp + pad_y

    which is the algebraic inverse of _pixel_corner_to_uv's
    ``u = xmin + (x - pad_x) * fpp`` / ``v = ymax - (y - pad_y) * fpp``.

    Stretching each axis independently across the full image instead -- what
    this did until D8 -- is correct only when both pads are zero, and
    silently displaces every mapped corner on the padded axis otherwise. It
    went unnoticed because nothing composed the two directions; that is now
    tests/test_uv_pixel_round_trip.py's job. The clamp math is no longer
    duplicated here: it comes from tools/clamp_pad_geometry.py, the one copy
    decode derives its own geometry from, so the forward and inverse mappings
    cannot drift apart again the way D1 and D8 did. Importing decode itself
    would still put vop_interwoven on this standalone tool's dependency path,
    which is why the shared helper is a standard-library-only leaf module.

    No producer-recorded export size is passed. This function is handed a TIFF
    that has already been read and has no sidecar ``resolution`` block in
    scope, so both of the helper's denominators are this image's own
    dimensions -- the correct reading when there is no second measurement to
    disagree with. A dim_check mismatch is therefore invisible here, and is
    caught upstream by decode's Guard 1.

    Returns (x0, y0, x1, y1) inclusive integer pixel bounds, clamped to the
    image, or None if bbox_corners_uv/bounds_uv is unavailable or degenerate.
    """
    if not bbox_corners_uv or bounds_uv is None:
        return None
    xmin, ymin, xmax, ymax = bounds_uv
    # image_w/image_h join the degeneracy check because they are divisors now
    # (via fpp), where before they were only multipliers.
    if xmax <= xmin or ymax <= ymin or image_w <= 0 or image_h <= 0:
        return None
    # The degeneracy guard above has already rejected everything the helper
    # raises ValueError for, so this cannot throw here; that guard stays where
    # it is because it also decides this function's None return.
    fpp, pad_x, pad_y = clamp_pad_geometry(bounds_uv, image_w, image_h)
    xs_px = []
    ys_px = []
    for u, v in bbox_corners_uv:
        xs_px.append((u - xmin) / fpp + pad_x)
        ys_px.append((ymax - v) / fpp + pad_y)
    x0_raw = math.floor(min(xs_px))
    x1_raw = math.ceil(max(xs_px)) - 1
    y0_raw = math.floor(min(ys_px))
    y1_raw = math.ceil(max(ys_px)) - 1
    # Reject a candidate whose rectangle doesn't overlap the image at all
    # BEFORE clamping: clamping x0/x1 (or y0/y1) independently to
    # [0, dim-1] would otherwise collapse a wholly off-image rectangle
    # (e.g. x1_raw < 0, entirely past the left edge) into a fabricated
    # one-pixel sliver AT the image edge (x0=x1=0) instead of correctly
    # reporting "no evidence at all" -- and that sliver could spuriously
    # overlap a real blob sitting at that edge, handing an off-view element
    # fabricated pixel evidence it never actually earned.
    if x1_raw < 0 or x0_raw > image_w - 1 or y1_raw < 0 or y0_raw > image_h - 1:
        return None
    x0 = max(0, min(int(x0_raw), image_w - 1))
    x1 = max(0, min(int(x1_raw), image_w - 1))
    y0 = max(0, min(int(y0_raw), image_h - 1))
    y1 = max(0, min(int(y1_raw), image_h - 1))
    if x1 < x0 or y1 < y0:
        return None
    return (x0, y0, x1, y1)


def _bbox_intersects(a, b):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return not (ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0)


def _score_candidates_for_blob(blob: dict[str, Any], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Score every candidate whose pixel footprint bbox intersects the
    blob's own bbox, then rank + tie-break into distinct confidence_scores.

    candidates: list of {"key", "link_inst_id", "link_elem_id",
    "near_face_w", "pixel_bbox": (x0,y0,x1,y1) | None}.

    Returns a list of scored dicts, sorted best-first (index 0 = selected),
    each carrying its own "coverage_fraction" (raw, pre-tie-break) and
    "confidence_score" (post-tie-break, strictly decreasing by rank so no
    two candidates for the same blob ever report an identical score).
    """
    blob_bbox = tuple(blob["bbox_px"])
    scored = []
    for cand in candidates:
        pb = cand["pixel_bbox"]
        if pb is None or not _bbox_intersects(blob_bbox, pb):
            continue
        x0, y0, x1, y1 = pb
        footprint_area = (x1 - x0 + 1) * (y1 - y0 + 1)
        if footprint_area <= 0:
            continue
        inside = (
            (blob["xs"] >= x0) & (blob["xs"] <= x1) &
            (blob["ys"] >= y0) & (blob["ys"] <= y1)
        )
        overlap_px = int(np.count_nonzero(inside))
        if overlap_px == 0:
            # The candidate's footprint rectangle intersects the blob's
            # bounding box but not a single one of its actual pixels -- e.g.
            # it sits in the concave notch of an L-shaped blob, or the hole
            # of a ring-shaped one. A bbox-only test cannot see that; this
            # candidate has zero pixel evidence and must not be scored or
            # emitted as an assignment, or a blob with no real candidates at
            # all would silently report one anyway (falsely, at whatever
            # near_face_w tie-break happens to apply) instead of falling
            # through to the null-assignment path below.
            continue
        coverage = overlap_px / float(footprint_area)
        scored.append({
            "key": cand["key"],
            "link_inst_id": cand["link_inst_id"],
            "link_elem_id": cand["link_elem_id"],
            "near_face_w": cand["near_face_w"],
            "footprint_pixel_area": footprint_area,
            "overlap_pixel_count": overlap_px,
            "coverage_fraction": coverage,
            # How much of the BLOB itself (not the candidate's own footprint)
            # this candidate explains -- used to detect a blob that is
            # plausibly a MERGER of several touching/overlapping same-color
            # elements (see _MULTI_CANDIDATE_BLOB_SHARE_THRESHOLD below),
            # which coverage_fraction alone cannot see (two adjoining walls
            # can each have coverage_fraction close to 1.0 while jointly
            # filling one connected-component blob).
            "blob_share": overlap_px / float(blob["pixel_count"]) if blob["pixel_count"] else 0.0,
        })

    # Rank by coverage (desc), tie-broken by nearest near_face_w (asc, None
    # sorts last -- an unresolvable near_face_w must never win a tie over a
    # candidate with real depth evidence).
    def _rank_key(c):
        w = c["near_face_w"]
        w_key = (1, 0.0) if w is None else (0, float(w))
        return (-c["coverage_fraction"], w_key)

    scored.sort(key=_rank_key)
    for rank, c in enumerate(scored):
        c["rank"] = rank
        c["confidence_score"] = max(0.0, c["coverage_fraction"] - rank * _RANK_TIEBREAK_EPS)
        c["confidence_label"] = "HIGH" if c["confidence_score"] >= CONFIDENCE_HIGH_THRESHOLD else "LOW"
    return scored


def resolve_category(rgb, link_candidates, rgb_array, bounds_uv, image_w, image_h) -> dict[str, Any]:
    """Resolve one LINK category's color mask into per-blob element identities."""
    r, g, b = (int(c) for c in rgb)
    mask = (
        (rgb_array[:, :, 0] == r) & (rgb_array[:, :, 1] == g) & (rgb_array[:, :, 2] == b)
    )
    pixel_count = int(np.count_nonzero(mask))
    if pixel_count == 0:
        return {
            "rgb": [r, g, b], "pixel_count": 0, "blobs": {},
            "candidate_count": len(link_candidates), "skipped": False, "skip_reason": None,
        }

    blobs = connected_components_with_pixels(mask)
    if blobs is None:
        return {
            "rgb": [r, g, b], "pixel_count": pixel_count, "blobs": {},
            "candidate_count": len(link_candidates), "skipped": True,
            "skip_reason": "mask exceeds inexpensive component threshold ({0} px)".format(
                MAX_MASK_PIXELS_FOR_COMPONENTS
            ),
        }

    prepared_candidates = [
        {
            "key": cand["key"],
            "link_inst_id": cand.get("link_inst_id"),
            "link_elem_id": cand.get("link_elem_id"),
            "near_face_w": cand.get("near_face_w"),
            "pixel_bbox": _uv_rect_to_pixel_bbox(cand.get("bbox_corners_uv"), bounds_uv, image_w, image_h),
        }
        for cand in link_candidates
    ]

    blobs_out = {}
    for blob in blobs:
        scored = _score_candidates_for_blob(blob, prepared_candidates)
        if not scored:
            blobs_out[str(blob["blob_id"])] = {
                "pixel_count": blob["pixel_count"],
                "bbox_px": blob["bbox_px"],
                "assignment": None,
                "candidates_considered": 0,
                "reason": "no_candidate_footprint_intersects_blob_bbox",
            }
            continue
        best = scored[0]
        # A blob is a plausible multi-element merger when two or more
        # candidates each independently explain a substantial share of its
        # own pixels -- not just of their own footprint (see blob_share's
        # docstring in _score_candidates_for_blob). Order-independent: any
        # qualifying candidate counts, not only the top-ranked one, since a
        # merger can have its largest contributor still be a clean winner by
        # coverage_fraction while a second element quietly shares the blob.
        multi_candidates = [c for c in scored if c["blob_share"] >= _MULTI_CANDIDATE_BLOB_SHARE_THRESHOLD]
        is_multi_candidate_region = len(multi_candidates) >= 2
        assignment = {
            "elem_id": best["key"],
            "link_inst_id": best["link_inst_id"],
            "link_elem_id": best["link_elem_id"],
            "confidence_score": best["confidence_score"],
            # Never HIGH when the blob is plausibly several elements merged
            # into one connected component: a single-winner HIGH label would
            # misrepresent an ambiguous region as a confidently resolved one.
            "confidence_label": "LOW" if is_multi_candidate_region else best["confidence_label"],
            "coverage_fraction": best["coverage_fraction"],
            "near_face_w": best["near_face_w"],
            "multi_candidate_region": is_multi_candidate_region,
            "other_plausible_elem_ids": (
                [c["key"] for c in multi_candidates if c["key"] != best["key"]]
                if is_multi_candidate_region else []
            ),
        }
        blobs_out[str(blob["blob_id"])] = {
            "pixel_count": blob["pixel_count"],
            "bbox_px": blob["bbox_px"],
            "assignment": assignment,
            "candidates_considered": len(scored),
            "candidate_scores": [
                {
                    "elem_id": c["key"],
                    "link_inst_id": c["link_inst_id"],
                    "link_elem_id": c["link_elem_id"],
                    "coverage_fraction": c["coverage_fraction"],
                    "blob_share": c["blob_share"],
                    "near_face_w": c["near_face_w"],
                    "confidence_score": c["confidence_score"],
                    "confidence_label": c["confidence_label"],
                }
                for c in scored
            ],
        }
    return {
        "rgb": [r, g, b], "pixel_count": pixel_count, "blobs": blobs_out,
        "candidate_count": len(link_candidates), "skipped": False, "skip_reason": None,
    }


def resolve_sidecar(sidecar_path: Path) -> dict[str, Any]:
    t0 = time.time()
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    near_face_w_map = sidecar.get("near_face_w_map")
    if near_face_w_map is None:
        raise ValueError(
            "MISSING_NEAR_FACE_W_MAP: sidecar {0} has no 'near_face_w_map' section -- "
            "re-export with the Phase 1b color_id_buffer.py to use this resolver".format(sidecar_path)
        )
    link_category_color_map = sidecar.get("link_category_color_map") or {}

    tiff_path = _resolve_tiff_path(sidecar_path, sidecar)
    rgb_array = _load_rgb_array(tiff_path)
    image_h, image_w = rgb_array.shape[:2]

    bounds_xy = sidecar.get("bounds_xy")
    bounds_uv = tuple(float(v) for v in bounds_xy) if bounds_xy and len(bounds_xy) == 4 else None

    link_entries = near_face_w_map.get("link") or {}
    by_category: dict[str, list[dict[str, Any]]] = {}
    for key, entry in link_entries.items():
        item = dict(entry)
        item["key"] = key
        by_category.setdefault(entry.get("category"), []).append(item)

    categories_out = {}
    for category_name, rgb in link_category_color_map.items():
        candidates = by_category.get(category_name, [])
        if bounds_uv is None:
            categories_out[category_name] = {
                "rgb": [int(c) for c in rgb], "pixel_count": None, "blobs": {},
                "candidate_count": len(candidates), "skipped": True,
                "skip_reason": "sidecar has no usable 'bounds_xy'; cannot map UV bbox "
                                "footprints into this TIFF's pixel space",
            }
            continue
        categories_out[category_name] = resolve_category(
            rgb, candidates, rgb_array, bounds_uv, image_w, image_h,
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "source_sidecar": str(sidecar_path),
        "source_sidecar_sha256": sha256_file(sidecar_path),
        "source_tiff": str(tiff_path),
        "source_tiff_sha256": sha256_file(tiff_path),
        "view_id": sidecar.get("view_id"),
        "confidence_high_threshold": CONFIDENCE_HIGH_THRESHOLD,
        "advisory_only": True,
        "categories": categories_out,
        "generated_at_unix": time.time(),
        "resolve_ms": round((time.time() - t0) * 1000.0, 3),
    }


def resolve_one(sidecar_path: Path) -> Path:
    doc = resolve_sidecar(sidecar_path)
    out_path = sidecar_path.with_name(sidecar_path.stem + ".link_identity.json")
    out_path.write_text(json.dumps(doc, indent=2, sort_keys=True), encoding="utf-8")
    return out_path


def collect(paths: list[str]) -> list[Path]:
    out = []
    for arg in paths:
        p = Path(arg)
        if p.is_dir():
            out += sorted(
                x for x in p.rglob("*.json")
                if not x.name.endswith((".decoded.json", ".link_identity.json"))
            )
        else:
            out.append(p)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Resolve VOP Stage A LINK category-color blobs to candidate LINK elements"
    )
    ap.add_argument("paths", nargs="+", help="Stage A sidecar JSON file(s) or directories containing them")
    ns = ap.parse_args(argv)

    failures = 0
    for sp in collect(ns.paths):
        try:
            out = resolve_one(sp)
            print(f"✓ {sp} -> {out}")
        except Exception as e:
            failures += 1
            print(f"✗ {sp}: {type(e).__name__}: {e}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
