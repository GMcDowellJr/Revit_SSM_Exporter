#!/usr/bin/env python3
"""Per view x variant evidence table for the annotation-pass variant probe.

Offline companion to ``tests/dynamo/probe_stage_a_anno_pass_variants.py``. The
probe runs in Revit and writes captures; this reads them afterwards and prints
what each one contains.

WHAT IT EMITS, AND WHAT IT DELIBERATELY DOES NOT
-------------------------------------------------
Numbers and tables. NO score, NO tolerance, NO pass/fail, NO aggregate
verdict, and no "this variant looks better" -- the probe brief is explicit
that Greg's read of the output is the gate and that the probe reports values
rather than outcomes. The same rule ``tools/capture_overlay.py`` follows and
for the same reason: a tool that printed "V2 fixes 96% of the blends" would
quietly become the answer to "which fix is right".

Where a measurement cannot be made, that is stated with its reason and the row
is left empty. An empty cell is never a zero.

THE SIX MEASUREMENTS (the probe brief's numbering)
---------------------------------------------------
1  image size vs the sidecar's ``frame_px``, on BOTH axes. The existing
   ``dim_check`` compares only the requested axis (finding F4), which is how
   annotation images up to 440 px off on the derived axis passed.
2  REGISTRATION FIT, measured from the pixels rather than assumed from the
   sidecar. Exact-palette-colour centroids are matched against the sidecar's
   recorded ``bbox_uv`` centres and a linear map is FITTED to the pairs. That
   fit is what says whether the rendered region is the frame the sidecar
   describes -- the method that fit plan V0 to a 0.44 px median.
3  how far the recorded annotation bboxes reach PAST the rendered frame, per
   side, printed beside (2)'s fitted margins. On the 20260922T085737 run those
   two agreed on 10 of 12 sides to within ~0.3 ft, which is the observation
   F1's hypothesis rests on.
4  coverage per category: painted, colour present, and bbox region holds ink.
   The ink test uses the FITTED mapping from (2), not the sidecar's, so it
   stays valid on exactly the captures where registration failed -- which are
   the ones worth looking at.
5  non-white, non-palette pixels, split into palette-to-white blends,
   black/grey, and other, with a top-10 colour list (finding F3).
6  the model TIFF's hash against V0's.

``tools/capture_overlay.py`` is invoked ONLY on captures where (1) shows the
image size equal to ``frame_px``. Otherwise the overlay is withheld and the
report says so and why: that tool maps UV through the sidecar's recorded
export size and REFUSES when the file disagrees, so running it on a
mis-registered capture produces either a refusal panel or, worse, boxes placed
against a rectangle that was never rendered.

LEAF MODULE
-----------
Standard library + numpy + PIL, plus the sibling leaf module
``tools/clamp_pad_geometry.py`` (standard-library-only) for the SIDECAR's own
UV->pixel model, which is what the fitted one is compared against. Imports
nothing from ``vop_interwoven`` and nothing from any other tool.

USAGE
-----
    python tools/notes/anno_pass_variant_report.py <probe-dir> [<probe-dir> ...]
    python tools/notes/anno_pass_variant_report.py <combined.anno_pass_variants.json>
    ... --out report.md --no-overlay
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.clamp_pad_geometry import clamp_pad_geometry  # noqa: E402

WHITE = (255, 255, 255)
# "Collinear within 3 RGB units" -- the probe brief's own figure for a
# palette-to-white blend.
BLEND_TOLERANCE = 3.0
# A pixel whose three channels sit within this of each other is grey. Viewer
# heads render black, and black is the grey with all channels at 0.
GREY_TOLERANCE = 3
# The ink test's threshold, from the probe brief.
INK_FRACTION_THRESHOLD = 0.02
# Rows read at a time. Bounds peak memory on a 10000 px capture, which at 3
# bytes a pixel is 300 MB read whole and ~15 MB a chunk.
CHUNK_ROWS = 512
# Distinct off-palette colours tracked before the tally stops growing. A run
# that hits it is reported as capped, so the top-10 list is never presented as
# if it came from a complete tally.
MAX_TRACKED_COLORS = 200_000


# ======================================================================
# PURE GEOMETRY / FIT  (unit-tested in tests/test_anno_pass_variant_report.py)
# ======================================================================

def fit_axis(xs: list[float], ys: list[float]) -> tuple[float, float] | None:
    """Ordinary least squares ``y = slope * x + intercept``, or None.

    Returns None -- never a slope of 0 and never a divide-by-zero -- when the
    inputs cannot determine a line: fewer than two samples, or every sample at
    the same ``x``. A caller has to decide what "could not be fitted" means;
    handing back a flat line would make an unfittable capture read like one
    rendered at zero scale.
    """
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    n = float(len(xs))
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx <= 0.0:
        return None
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = sxy / sxx
    return (slope, mean_y - slope * mean_x)


def rect_center(rect: tuple[float, float, float, float]) -> tuple[float, float]:
    return ((rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0)


def rect_from_corners(corners: Any) -> tuple[float, float, float, float] | None:
    """``(xmin, ymin, xmax, ymax)`` from ``bbox_uv``'s four ``[u, v]`` pairs."""
    if corners is None:
        return None
    try:
        if len(corners) == 4 and all(isinstance(c, (int, float)) for c in corners):
            return (min(float(corners[0]), float(corners[2])),
                    min(float(corners[1]), float(corners[3])),
                    max(float(corners[0]), float(corners[2])),
                    max(float(corners[1]), float(corners[3])))
        us = [float(c[0]) for c in corners]
        vs = [float(c[1]) for c in corners]
    except (TypeError, ValueError, IndexError):
        return None
    if not us or not vs:
        return None
    return (min(us), min(vs), max(us), max(vs))


def ink_vs_bbox_geometry(samples, mapping):
    """Does an element's INK fill its recorded bbox? Per category.

    THIS MEASURES THE FIT'S PREMISE, and the first version of this function
    measured the wrong thing -- caught by its own test, which is the only reason
    this comment is accurate. That version reported the per-sample residual of
    the fit, and a displacement that is the SAME for every element is ABSORBED
    INTO THE FITTED INTERCEPT, so it came back ~0 by construction. Measuring the
    premise through the fit that hides it is circular.

    WHAT IS ACTUALLY RECOVERABLE, and what is not:

      * NOT recoverable: a uniform displacement between every element's ink and
        its bbox centre. Nothing in the capture distinguishes "the ink sits 3 px
        left of every box" from "the whole render sits 3 px left". That is
        exactly why it is dangerous, and this function does not pretend to
        measure it.
      * Recoverable, and diagnostic of whether such a displacement is POSSIBLE:
        how much of its recorded bbox an element's ink actually occupies. Ink
        that fills its box cannot be off-centre in it. Ink occupying a third of
        it can be, by up to a third of the box.

    So what is reported is the fill: the ink's own pixel bbox against the
    recorded bbox mapped through the fitted SCALE (which a uniform translation
    does not corrupt), plus the fraction of that ink bbox the ink covers. A
    category whose ink fills its boxes puts a bound on the offset; one whose ink
    occupies a fraction says the margins in 2b rest on a premise this capture
    cannot confirm.

    Returns ``{}`` when there is nothing to measure through -- never zeros,
    which would read as "the ink fills its box".
    """
    if not mapping or not samples:
        return {}
    scale_u = abs(mapping.get("a_u") or 0.0)
    scale_v = abs(mapping.get("a_v") or 0.0)
    if scale_u <= 0.0 or scale_v <= 0.0:
        return {}
    per_category = {}
    for sample in samples:
        recorded_u_px = float(sample.get("bbox_u_ft") or 0.0) * scale_u
        recorded_v_px = float(sample.get("bbox_v_ft") or 0.0) * scale_v
        ink_u_px = float(sample.get("ink_w_px") or 0.0)
        ink_v_px = float(sample.get("ink_h_px") or 0.0)
        if recorded_u_px <= 0.0 or recorded_v_px <= 0.0:
            continue
        bucket = per_category.setdefault(
            sample.get("category") or "<no category>",
            {"span_u": [], "span_v": [], "solidity": [], "slack_u_px": [],
             "slack_v_px": []})
        bucket["span_u"].append(ink_u_px / recorded_u_px)
        bucket["span_v"].append(ink_v_px / recorded_v_px)
        # How far the ink COULD be off-centre inside its recorded box, in px.
        # This is the bound the margins in 2b are uncertain by.
        bucket["slack_u_px"].append(max(0.0, (recorded_u_px - ink_u_px) / 2.0))
        bucket["slack_v_px"].append(max(0.0, (recorded_v_px - ink_v_px) / 2.0))
        area = ink_u_px * ink_v_px
        if area > 0.0:
            bucket["solidity"].append(float(sample.get("ink_pixels") or 0) / area)

    def _stats(values):
        if not values:
            return None
        ordered = sorted(values)
        mid = len(ordered) // 2
        median = (ordered[mid] if len(ordered) % 2
                  else (ordered[mid - 1] + ordered[mid]) / 2.0)
        return {"median": median, "min": ordered[0], "max": ordered[-1],
                "count": len(ordered)}

    out = {}
    for name, bucket in per_category.items():
        out[name] = {
            # ink bbox extent / recorded bbox extent. 1.0 = the ink spans its
            # whole box, so it cannot be off-centre in it.
            "span_fraction_u": _stats(bucket["span_u"]),
            "span_fraction_v": _stats(bucket["span_v"]),
            # ink pixels / ink bbox area. <1 means the ink is not a solid
            # rectangle (a glyph run, an L, a leader), so its CENTROID can sit
            # away from its own bbox centre too.
            "solidity": _stats(bucket["solidity"]),
            # The bound, in pixels, on how wrong a margin could be because of
            # this. The number to read 2b against.
            "possible_offset_u_px": _stats(bucket["slack_u_px"]),
            "possible_offset_v_px": _stats(bucket["slack_v_px"]),
        }
    return out


def fit_registration(samples, frame_uv, image_w, image_h, view_scale,
                     anchor="ink_centroid"):
    """Fit UV -> pixel from matched (bbox centre, colour centroid) pairs.

    ``anchor`` selects which pixel feature stands for an element's position:

        "ink_centroid"  the centroid of its exact-colour pixels. The probe
                        brief's method, and the one precedented at a 0.44 px
                        median on the run's plan view.
        "ink_bbox"      the centre of the bounding box of those same pixels.

    BOTH ARE REPORTED, and the reason is finding 3 on PR #215: neither anchor
    is guaranteed to coincide with the centre of ``get_BoundingBox(view)`` for
    real text, tags or dimensions. The two agree exactly when the ink fills its
    box and diverge when it does not -- so running both and printing them side
    by side turns an unasserted premise into a visible measurement. Where they
    agree the anchor choice does not matter; where they disagree, the margins
    below rest on a premise this capture does not satisfy, and the reader can
    see it without opening a sidecar.

    ``samples`` is ``[{"u": float, "v": float, "x": float, "y": float}, ...]``.
    The two axes are fitted INDEPENDENTLY, on purpose: if the rendered region
    is not the frame, u and v can be off by different amounts (F1 reported
    17.81 px/ft against a frame's 18.75 on a plan and 30.8 against 37.5 on a
    section), and a single isotropic scale would average that away.

    Returns a record. ``status`` is "value" only when both axes fitted; a
    capture with every annotation on one row cannot determine the v axis and is
    reported as such rather than fitted to whatever the noise says.

    Every derived figure is expressed in the units the reader needs: px/ft for
    the scales, FEET and PAPER INCHES for the margins, pixels for the
    residuals.
    """
    scale = float(view_scale)
    n = len(samples)
    out: dict[str, Any] = {"sample_count": n}
    if n < 2:
        out["status"] = "unavailable"
        out["reason"] = (
            "{0} matched sample(s); a linear fit needs at least 2".format(n))
        return out

    key_x = "x" if anchor == "ink_centroid" else "bbox_x"
    key_y = "y" if anchor == "ink_centroid" else "bbox_y"
    if any(s.get(key_x) is None or s.get(key_y) is None for s in samples):
        out["status"] = "unavailable"
        out["reason"] = ("anchor {0!r} needs {1}/{2} on every sample and at least "
                         "one is missing".format(anchor, key_x, key_y))
        return out
    out["anchor"] = anchor
    us = [float(s["u"]) for s in samples]
    vs = [float(s["v"]) for s in samples]
    xs = [float(s[key_x]) for s in samples]
    ys = [float(s[key_y]) for s in samples]

    fit_u = fit_axis(us, xs)
    fit_v = fit_axis(vs, ys)
    if fit_u is None or fit_v is None:
        out["status"] = "unavailable"
        out["reason"] = (
            "u axis {0}, v axis {1}: an axis with no spread in the samples cannot "
            "be fitted, so no mapping is reported".format(
                "fitted" if fit_u else "not fitted",
                "fitted" if fit_v else "not fitted"))
        out["distinct_u"] = len(set(round(u, 6) for u in us))
        out["distinct_v"] = len(set(round(v, 6) for v in vs))
        return out

    a_u, b_u = fit_u
    a_v, b_v = fit_v
    out["status"] = "value"
    # px/ft. a_v is NEGATIVE on an intact capture (v grows up, y grows down),
    # so its magnitude is the scale and its SIGN is reported separately -- a
    # positive a_v means the capture is flipped, which is a different finding
    # from a wrong scale and must not be hidden by an abs().
    out["px_per_ft_u"] = a_u
    out["px_per_ft_v"] = abs(a_v)
    out["v_axis_sign"] = ("negative (expected)" if a_v < 0 else "POSITIVE (flipped)")
    out["intercept_u_px"] = b_u
    out["intercept_v_px"] = b_v

    residuals = []
    for u, v, x, y in zip(us, vs, xs, ys):
        dx = (a_u * u + b_u) - x
        dy = (a_v * v + b_v) - y
        residuals.append(math.hypot(dx, dy))
    residuals.sort()
    mid = len(residuals) // 2
    out["residual_median_px"] = (residuals[mid] if len(residuals) % 2
                                 else (residuals[mid - 1] + residuals[mid]) / 2.0)
    out["residual_max_px"] = residuals[-1]

    fx0, fy0, fx1, fy1 = (float(c) for c in frame_uv)
    # THE FRAME'S OWN px/ft, from the sidecar, for the comparison the brief
    # asks for. Through the shared clamp-pad derivation rather than
    # frame_px/extent, because Revit's aspect clamp pads the short axis and a
    # naive ratio would attribute that pad to the scale.
    try:
        feet_per_pixel, pad_x, pad_y = clamp_pad_geometry(
            (fx0, fy0, fx1, fy1), image_w, image_h)
        out["frame_px_per_ft"] = 1.0 / feet_per_pixel
        out["frame_pad_px"] = [pad_x, pad_y]
        out["px_per_ft_u_minus_frame"] = a_u - (1.0 / feet_per_pixel)
        out["px_per_ft_v_minus_frame"] = abs(a_v) - (1.0 / feet_per_pixel)
    except ValueError as ex:
        out["frame_px_per_ft"] = None
        out["frame_px_per_ft_reason"] = "{0}: {1}".format(type(ex).__name__, ex)

    # Offset at (u0, v1) -- the frame's TOP-LEFT corner. On a capture that
    # registers exactly this is the aspect-clamp pad and nothing else.
    out["frame_top_left_predicted_px"] = [a_u * fx0 + b_u, a_v * fy1 + b_v]

    # What UV rectangle the IMAGE actually covers, by inverting the fit. The
    # per-side margin is then how far that runs past the frame; positive means
    # the render is bigger than the frame on that side, which is F1.
    u_at_left = (0.0 - b_u) / a_u
    u_at_right = (float(image_w) - b_u) / a_u
    v_at_top = (0.0 - b_v) / a_v
    v_at_bottom = (float(image_h) - b_v) / a_v
    margins_ft = {
        "left": fx0 - min(u_at_left, u_at_right),
        "right": max(u_at_left, u_at_right) - fx1,
        "top": max(v_at_top, v_at_bottom) - fy1,
        "bottom": fy0 - min(v_at_top, v_at_bottom),
    }
    out["fitted_image_uv"] = [min(u_at_left, u_at_right), min(v_at_top, v_at_bottom),
                              max(u_at_left, u_at_right), max(v_at_top, v_at_bottom)]
    out["margin_ft"] = margins_ft
    out["margin_paper_in"] = dict(
        (side, value * 12.0 / scale) for side, value in margins_ft.items())
    out["mapping"] = {"a_u": a_u, "b_u": b_u, "a_v": a_v, "b_v": b_v}
    # The premise, measured -- and measured OUTSIDE the fit, because the fit
    # absorbs exactly the displacement in question. See ink_vs_bbox_geometry.
    out["ink_vs_bbox_by_category"] = ink_vs_bbox_geometry(samples, out["mapping"])
    out["feet_per_pixel_fitted"] = (1.0 / a_u) if a_u else None
    return out


def uv_rect_to_pixel_rect(rect_uv, mapping, image_w, image_h):
    """A UV rectangle as integer, image-clamped pixel bounds, or None.

    ``None`` when the rectangle maps WHOLLY outside the image: there are no
    pixels to test, which is a different fact from "the region is blank" and
    must not be collapsed into a 0% ink reading.
    """
    a_u, b_u = mapping["a_u"], mapping["b_u"]
    a_v, b_v = mapping["a_v"], mapping["b_v"]
    x_candidates = [a_u * rect_uv[0] + b_u, a_u * rect_uv[2] + b_u]
    y_candidates = [a_v * rect_uv[1] + b_v, a_v * rect_uv[3] + b_v]
    x0, x1 = min(x_candidates), max(x_candidates)
    y0, y1 = min(y_candidates), max(y_candidates)
    if x1 < 0 or x0 > image_w - 1 or y1 < 0 or y0 > image_h - 1:
        return None
    ix0 = max(0, int(math.floor(x0)))
    iy0 = max(0, int(math.floor(y0)))
    ix1 = min(image_w, int(math.ceil(x1)) + 1)
    iy1 = min(image_h, int(math.ceil(y1)) + 1)
    if ix1 <= ix0 or iy1 <= iy0:
        return None
    return (ix0, iy0, ix1, iy1)


def ink_fraction(region: np.ndarray) -> float:
    """Fraction of a HxWx3 region that is not pure white.

    Raises on an empty region rather than returning 0.0: a zero here would be
    read as "the element painted nothing", and a region with no pixels is not
    that fact.
    """
    if region.size == 0:
        raise ValueError("ink_fraction needs a non-empty region")
    non_white = ~np.all(region == 255, axis=-1)
    return float(non_white.sum()) / float(non_white.size)


def classify_offpalette(rgb, palette_array, blend_tolerance=BLEND_TOLERANCE,
                        grey_tolerance=GREY_TOLERANCE):
    """One off-palette colour's class: "blend", "grey" or "other".

    A "blend" is within ``blend_tolerance`` of the SEGMENT between some palette
    colour and white -- the shape a one-pixel anti-aliased edge between a
    painted element and the background takes. Distance to the segment, not to
    the line through it, so a colour beyond either endpoint is not swept in.

    BLEND IS CHECKED FIRST and that ORDERING IS A CHOICE, not a fact: a grey
    pixel is also collinear with white and any grey palette colour, so the two
    classes genuinely overlap. The caller is handed ``also_grey`` alongside the
    class so the overlap is visible in the report rather than decided silently
    here -- the ordering is stated, and its cost is counted.
    """
    colour = np.asarray(rgb, dtype=np.float64)
    is_grey = bool(colour.max() - colour.min() <= grey_tolerance)
    if palette_array is not None and len(palette_array):
        white = np.array(WHITE, dtype=np.float64)
        directions = white - palette_array                      # (N, 3)
        deltas = colour - palette_array                         # (N, 3)
        lengths_sq = (directions ** 2).sum(axis=1)              # (N,)
        with np.errstate(invalid="ignore", divide="ignore"):
            t = (deltas * directions).sum(axis=1) / lengths_sq
        # A palette colour that IS white has a zero-length segment; the only
        # point on it is white itself, so clamp t to 0 there rather than
        # letting a nan decide the classification.
        t = np.where(lengths_sq > 0.0, t, 0.0)
        t = np.clip(t, 0.0, 1.0)
        closest = palette_array + t[:, None] * directions
        distances = np.sqrt(((colour - closest) ** 2).sum(axis=1))
        if float(distances.min()) <= blend_tolerance:
            return ("blend", is_grey)
    return (("grey", True) if is_grey else ("other", False))


# ======================================================================
# IMAGE SCANNING
# ======================================================================

def _pack(rgb) -> int:
    return (int(rgb[0]) << 16) | (int(rgb[1]) << 8) | int(rgb[2])


def _unpack(key: int) -> tuple[int, int, int]:
    return ((key >> 16) & 0xFF, (key >> 8) & 0xFF, key & 0xFF)


def scan_image(path, palette_rgbs):
    """One chunked pass over the TIFF. Everything (2), (4) and (5) need.

    Returns a dict with, per palette colour that appears: pixel count, the
    centroid of its pixels, and whether any of them touches the image border.
    Plus the whole-image tallies (5) needs.

    CENTROID OF THE COLOUR'S MASK, not of a labelled connected component, and
    that is a deliberate simplification with a stated cost. Each palette colour
    belongs to exactly one element, so on an intact capture the two are the same
    number. Where an element's ink is split across the image the mask centroid
    sits between the parts -- which is why ``touches_border`` is reported and
    why (2) EXCLUDES any colour whose mask reaches an edge: a clipped element's
    centroid is not its centre, and using it would pull the fit.

    Chunked by rows so peak memory does not scale with the capture.
    """
    keys_sorted = np.array(sorted({_pack(rgb) for rgb in palette_rgbs}),
                           dtype=np.int64)
    n_colors = len(keys_sorted)
    counts = np.zeros(n_colors, dtype=np.int64)
    sum_x = np.zeros(n_colors, dtype=np.float64)
    sum_y = np.zeros(n_colors, dtype=np.float64)
    min_x = np.full(n_colors, np.inf)
    min_y = np.full(n_colors, np.inf)
    max_x = np.full(n_colors, -np.inf)
    max_y = np.full(n_colors, -np.inf)
    border = np.zeros(n_colors, dtype=bool)

    white_key = _pack(WHITE)
    total_pixels = 0
    white_pixels = 0
    palette_pixels = 0
    offpalette_tally: Counter[int] = Counter()
    offpalette_capped = False

    with Image.open(path) as handle:
        image = handle.convert("RGB")
        width, height = image.size
        for top in range(0, height, CHUNK_ROWS):
            bottom = min(height, top + CHUNK_ROWS)
            block = np.asarray(image.crop((0, top, width, bottom)), dtype=np.uint8)
            rows, cols, _ = block.shape
            keys = ((block[:, :, 0].astype(np.int64) << 16)
                    | (block[:, :, 1].astype(np.int64) << 8)
                    | block[:, :, 2].astype(np.int64))
            flat = keys.reshape(-1)
            total_pixels += flat.size

            is_white = flat == white_key
            white_pixels += int(is_white.sum())

            if n_colors:
                idx = np.searchsorted(keys_sorted, flat)
                idx_clipped = np.clip(idx, 0, n_colors - 1)
                is_palette = keys_sorted[idx_clipped] == flat
            else:
                idx_clipped = np.zeros(flat.shape, dtype=np.int64)
                is_palette = np.zeros(flat.shape, dtype=bool)
            palette_pixels += int(is_palette.sum())

            if is_palette.any():
                ys, xs = np.divmod(np.nonzero(is_palette)[0], cols)
                ys = ys.astype(np.float64) + float(top)
                xs = xs.astype(np.float64)
                hit = idx_clipped[is_palette]
                counts += np.bincount(hit, minlength=n_colors)
                sum_x += np.bincount(hit, weights=xs, minlength=n_colors)
                sum_y += np.bincount(hit, weights=ys, minlength=n_colors)
                np.minimum.at(min_x, hit, xs)
                np.minimum.at(min_y, hit, ys)
                np.maximum.at(max_x, hit, xs)
                np.maximum.at(max_y, hit, ys)
                on_border = ((xs <= 0.0) | (xs >= float(width - 1))
                             | (ys <= 0.0) | (ys >= float(height - 1)))
                if on_border.any():
                    border[hit[on_border]] = True

            # The off-palette TOTAL is derived from the three counters below
            # and is therefore always complete. Only the PER-COLOUR tally is
            # bounded, and `offpalette_capped` is what says so -- so a capped
            # run still reports the right total beside a split it labels as a
            # prefix, rather than a smaller total that looks complete.
            other = ~(is_white | is_palette)
            if other.any() and not offpalette_capped:
                values, value_counts = np.unique(flat[other], return_counts=True)
                for key, count in zip(values.tolist(), value_counts.tolist()):
                    if (key not in offpalette_tally
                            and len(offpalette_tally) >= MAX_TRACKED_COLORS):
                        offpalette_capped = True
                        break
                    offpalette_tally[key] += count

    blobs = {}
    for index in range(n_colors):
        if counts[index] == 0:
            continue
        blobs[int(keys_sorted[index])] = {
            "pixel_count": int(counts[index]),
            "centroid_px": [float(sum_x[index] / counts[index]),
                            float(sum_y[index] / counts[index])],
            "pixel_bbox": [float(min_x[index]), float(min_y[index]),
                           float(max_x[index]), float(max_y[index])],
            "touches_border": bool(border[index]),
        }
    return {
        "image_w": width, "image_h": height,
        "total_pixels": total_pixels,
        "white_pixels": white_pixels,
        "palette_pixels": palette_pixels,
        "offpalette_pixels": total_pixels - white_pixels - palette_pixels,
        "offpalette_tally": offpalette_tally,
        "offpalette_tally_capped": offpalette_capped,
        "blobs": blobs,
    }


def classify_offpalette_tally(tally, palette_rgbs, capped):
    """(5): split the off-palette population into blends, grey and other."""
    palette_array = (np.array([[float(c) for c in rgb] for rgb in palette_rgbs],
                              dtype=np.float64)
                     if palette_rgbs else np.empty((0, 3), dtype=np.float64))
    out = {
        "blend_pixels": 0, "grey_pixels": 0, "other_pixels": 0,
        "blend_also_grey_pixels": 0,
        "distinct_colors": len(tally),
        "tally_capped": bool(capped),
        "top_colors": [],
    }
    if capped:
        out["tally_capped_note"] = (
            "more than {0} distinct off-palette colours were seen; the per-colour "
            "tally stopped growing, so the split and the top-10 below describe a "
            "PREFIX of the population, not all of it".format(MAX_TRACKED_COLORS))
    for key, count in tally.items():
        rgb = _unpack(int(key))
        kind, also_grey = classify_offpalette(rgb, palette_array)
        out[kind + "_pixels"] += int(count)
        if kind == "blend" and also_grey:
            out["blend_also_grey_pixels"] += int(count)
    for key, count in tally.most_common(10):
        rgb = _unpack(int(key))
        kind, also_grey = classify_offpalette(rgb, palette_array)
        out["top_colors"].append({"rgb": list(rgb), "pixels": int(count),
                                  "class": kind, "also_grey": bool(also_grey)})
    return out


def sha256_of(path) -> str | None:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


# ======================================================================
# SIDECAR READING
# ======================================================================

def _gs_value(raw):
    """A three-valued field's payload, or None. Production spells it "state"."""
    if isinstance(raw, dict) and "state" in raw:
        return raw.get("value") if raw.get("state") == "value" else None
    return raw


def _block_ok(raw) -> tuple[bool, str | None]:
    """A BLOCK-level status. Production spells this one "status", not "state".

    The two spellings are not interchangeable, and reading a block with the
    field reader classifies an "unavailable" block as a value whose payload
    happens to be a dict -- the defect PR #213's review found in
    ``capture_overlay.py``. An ABSENT block is fine: a sidecar written before
    the block existed is not a failed collection.
    """
    if not isinstance(raw, dict):
        return (True, None)
    status = raw.get("status")
    if status is None or status == "value":
        return (True, None)
    return (False, "{0}: {1}".format(status, raw.get("reason") or "no reason recorded"))


def read_annotation_sidecar(path):
    """Everything this report needs out of one annotation sidecar.

    Refuses rather than substituting: a sidecar whose bbox collection reported
    unavailable has no rectangles to fit against, and inventing a frame from
    ``frame_px`` alone would place every measurement against a rectangle the
    capture may never have rendered.
    """
    with open(path, encoding="utf-8") as handle:
        sidecar = json.load(handle)

    registration = sidecar.get("registration") or {}
    resolution = sidecar.get("resolution") or {}
    record: dict[str, Any] = {
        "sidecar_path": str(path),
        "view_id": sidecar.get("view_id"),
        "view_scale": resolution.get("view_scale"),
        "frame_px": registration.get("frame_px"),
        "rendered_uv": registration.get("rendered_uv"),
        "frame_snapped_uv": registration.get("frame_snapped_uv"),
        "achieved_fpp_ft": registration.get("achieved_fpp_ft"),
        "recorded_actual_w": resolution.get("actual_w"),
        "recorded_actual_h": resolution.get("actual_h"),
        "dim_check": resolution.get("dim_check"),
        "pixel_size": resolution.get("pixel_size"),
        "requested_pixel_size": resolution.get("requested_pixel_size"),
        "model_suppression_mode": sidecar.get("model_suppression_mode"),
        "applied_smooth_edges": sidecar.get("applied_smooth_edges"),
        "applied_display_style": sidecar.get("applied_display_style"),
        "capture_faults": sidecar.get("capture_faults") or [],
        "tiff_path": sidecar.get("tiff_path"),
        "paint_failed_element_ids": set(
            int(v) for v in (sidecar.get("paint_failed_element_ids") or [])),
        "color_assignment_count": sidecar.get("color_assignment_count"),
    }

    # A MALFORMED ENTRY IS RECORDED, NOT SKIPPED. Every count in section 4 is
    # a count of these maps, so an entry dropped here comes back as an element
    # that was never assigned a colour -- which is a different fact about the
    # capture and one nothing downstream could recover.
    colors = {}
    malformed_colors = []
    for key, rgb in (sidecar.get("color_assignment_map") or {}).items():
        try:
            colors[int(key)] = (int(rgb[0]), int(rgb[1]), int(rgb[2]))
        except (TypeError, ValueError, IndexError) as ex:
            malformed_colors.append({"key": str(key), "value": repr(rgb),
                                     "reason": "{0}: {1}".format(
                                         type(ex).__name__, ex)})
    record["color_map"] = colors
    record["malformed_color_entries"] = malformed_colors

    ok, reason = _block_ok(sidecar.get("annotation_bbox_status"))
    record["bbox_status_ok"] = ok
    record["bbox_status_reason"] = reason
    entries = {}
    malformed_bbox_keys = []
    if ok:
        for key, entry in (sidecar.get("annotation_bbox_map") or {}).items():
            try:
                element_id = int(key)
            except (TypeError, ValueError) as ex:
                malformed_bbox_keys.append({"key": str(key),
                                            "reason": "{0}: {1}".format(
                                                type(ex).__name__, ex)})
                continue
            rect = rect_from_corners(_gs_value((entry or {}).get("bbox_uv")))
            entries[element_id] = {
                "rect_uv": rect,
                "category": (entry or {}).get("category"),
                "membership_basis": (entry or {}).get("membership_basis"),
                "bbox_source": (entry or {}).get("bbox_source"),
            }
    record["bbox_entries"] = entries
    record["malformed_bbox_keys"] = malformed_bbox_keys
    return record


def frame_rect_for(record) -> tuple[Any, str | None]:
    """The rectangle this annotation capture RENDERED, or why there is none.

    ``registration.rendered_uv`` and nothing else. It is null exactly when the
    frame-B crop could not be applied, in which case the export is FitToPage's
    automatic extent: there is no rectangle to measure against and the report
    says so rather than falling back to ``frame_snapped_uv``, which would be
    describing a rectangle that was not rendered.
    """
    rendered = record.get("rendered_uv")
    if not rendered or len(rendered) != 4:
        return (None, "registration.rendered_uv is null: frame B was not applied as "
                      "the view crop, so the TIFF's extent is FitToPage's automatic "
                      "one and there is no recorded rectangle to measure against")
    rect = tuple(float(v) for v in rendered)
    if rect[2] <= rect[0] or rect[3] <= rect[1]:
        return (None, "registration.rendered_uv is degenerate: {0}".format(rect))
    return (rect, None)


# ======================================================================
# ONE CAPTURE
# ======================================================================

def analyze_capture(sidecar_path, tiff_path, model_tiff_sha=None,
                    v0_model_tiff_sha=None):
    """The six measurements for one view x variant capture."""
    record = read_annotation_sidecar(sidecar_path)
    result: dict[str, Any] = {
        "sidecar_path": str(sidecar_path),
        "tiff_path": str(tiff_path),
        "sidecar": record,
        "measurements": {},
    }
    measurements = result["measurements"]

    palette = sorted(set(record["color_map"].values()))
    scan = scan_image(tiff_path, palette)
    image_w, image_h = scan["image_w"], scan["image_h"]

    # ---- (1) image size vs frame_px, BOTH axes ------------------------
    frame_px = record.get("frame_px")
    size = {"image_w": image_w, "image_h": image_h,
            "recorded_actual_w": record["recorded_actual_w"],
            "recorded_actual_h": record["recorded_actual_h"],
            "dim_check": record["dim_check"]}
    if frame_px and len(frame_px) == 2:
        size["frame_px_w"] = int(frame_px[0])
        size["frame_px_h"] = int(frame_px[1])
        size["delta_w"] = image_w - int(frame_px[0])
        size["delta_h"] = image_h - int(frame_px[1])
        size["matches_frame_px"] = (size["delta_w"] == 0 and size["delta_h"] == 0)
    else:
        size["frame_px_reason"] = "the sidecar records no registration.frame_px"
        size["matches_frame_px"] = False
    measurements["size"] = size

    # ---- (2) registration fit ----------------------------------------
    frame_rect, frame_reason = frame_rect_for(record)
    samples = []
    sample_diagnostics = {"no_rect": 0, "color_absent": 0, "border_clipped": 0,
                          "matched": 0}
    by_color_key = {}
    for element_id, rgb in record["color_map"].items():
        by_color_key.setdefault(_pack(rgb), []).append(element_id)
    for element_id, entry in record["bbox_entries"].items():
        rect = entry.get("rect_uv")
        if rect is None:
            sample_diagnostics["no_rect"] += 1
            continue
        rgb = record["color_map"].get(element_id)
        if rgb is None:
            sample_diagnostics["color_absent"] += 1
            continue
        blob = scan["blobs"].get(_pack(rgb))
        if blob is None:
            sample_diagnostics["color_absent"] += 1
            continue
        if len(by_color_key.get(_pack(rgb), ())) != 1:
            # Two elements sharing a colour make the centroid ambiguous, which
            # is a palette collision and a finding of its own -- not a sample.
            sample_diagnostics.setdefault("color_collision", 0)
            sample_diagnostics["color_collision"] += 1
            continue
        if blob["touches_border"]:
            sample_diagnostics["border_clipped"] += 1
            continue
        u, v = rect_center(rect)
        pixel_bbox = blob["pixel_bbox"]
        samples.append({
            "element_id": element_id, "u": u, "v": v,
            "category": entry.get("category"),
            # The brief's anchor.
            "x": blob["centroid_px"][0], "y": blob["centroid_px"][1],
            # The second anchor: the centre of the ink's own bounding box.
            # Equal to the centroid when the ink fills its box; different when
            # it does not, which is finding 3's whole point.
            "bbox_x": (pixel_bbox[0] + pixel_bbox[2]) / 2.0,
            "bbox_y": (pixel_bbox[1] + pixel_bbox[3]) / 2.0,
            # Raw ink geometry, for the premise measurement. Deliberately NOT
            # derived through the fit: the fit absorbs the very displacement
            # being looked for.
            "ink_w_px": pixel_bbox[2] - pixel_bbox[0] + 1.0,
            "ink_h_px": pixel_bbox[3] - pixel_bbox[1] + 1.0,
            "ink_pixels": blob["pixel_count"],
            "bbox_u_ft": rect[2] - rect[0],
            "bbox_v_ft": rect[3] - rect[1],
        })
        sample_diagnostics["matched"] += 1

    if frame_rect is None:
        fit = {"status": "unavailable", "reason": frame_reason,
               "sample_count": len(samples)}
        alt_fit = {"status": "unavailable", "reason": frame_reason}
    else:
        fit = fit_registration(samples, frame_rect, image_w, image_h,
                               record.get("view_scale") or 1.0,
                               anchor="ink_centroid")
        # THE SECOND ANCHOR. Not a fallback -- a cross-check on the premise
        # that an element's ink is centred in its recorded bbox. See
        # fit_registration's docstring and ink_offset_distribution.
        alt_fit = fit_registration(samples, frame_rect, image_w, image_h,
                                   record.get("view_scale") or 1.0,
                                   anchor="ink_bbox")
    fit["sample_diagnostics"] = sample_diagnostics
    fit["frame_uv"] = list(frame_rect) if frame_rect else None
    measurements["registration"] = fit
    measurements["registration_ink_bbox_anchor"] = alt_fit
    measurements["anchor_agreement"] = _anchor_agreement(fit, alt_fit)

    # ---- (3) bbox excursion past the rendered frame, per side ---------
    if frame_rect is None:
        measurements["bbox_excursion"] = {
            "status": "unavailable", "reason": frame_reason}
    else:
        fx0, fy0, fx1, fy1 = frame_rect
        excursion = {"status": "value", "left": 0.0, "right": 0.0,
                     "top": 0.0, "bottom": 0.0, "rect_count": 0,
                     "beyond_count": 0}
        for entry in record["bbox_entries"].values():
            rect = entry.get("rect_uv")
            if rect is None:
                continue
            excursion["rect_count"] += 1
            left = fx0 - rect[0]
            bottom = fy0 - rect[1]
            right = rect[2] - fx1
            top = rect[3] - fy1
            if max(left, bottom, right, top) > 0.0:
                excursion["beyond_count"] += 1
            excursion["left"] = max(excursion["left"], left)
            excursion["bottom"] = max(excursion["bottom"], bottom)
            excursion["right"] = max(excursion["right"], right)
            excursion["top"] = max(excursion["top"], top)
        scale = float(record.get("view_scale") or 1.0)
        excursion["paper_in"] = dict(
            (side, excursion[side] * 12.0 / scale)
            for side in ("left", "right", "top", "bottom"))
        measurements["bbox_excursion"] = excursion

    # ---- (4) coverage per category -----------------------------------
    measurements["coverage"] = _coverage_by_category(
        record, scan, measurements["registration"], tiff_path, image_w, image_h)

    # ---- (5) off-palette split ---------------------------------------
    off = classify_offpalette_tally(
        scan["offpalette_tally"], palette, scan["offpalette_tally_capped"])
    off["total_pixels"] = scan["total_pixels"]
    off["white_pixels"] = scan["white_pixels"]
    off["palette_pixels"] = scan["palette_pixels"]
    off["offpalette_pixels"] = scan["offpalette_pixels"]
    off["offpalette_fraction"] = (
        scan["offpalette_pixels"] / scan["total_pixels"]
        if scan["total_pixels"] else None)
    measurements["offpalette"] = off

    # ---- (6) model TIFF hash -----------------------------------------
    measurements["model_tiff"] = {
        "sha256": model_tiff_sha,
        "v0_sha256": v0_model_tiff_sha,
        "matches_v0": (None if not (model_tiff_sha and v0_model_tiff_sha)
                       else model_tiff_sha == v0_model_tiff_sha),
    }
    return result


def _anchor_agreement(primary, secondary):
    """How far the two anchors' answers differ. Deltas only, no verdict.

    Reported rather than thresholded on purpose: what counts as "close enough"
    for a margin figure is Greg's call, and a tool that picked a number would
    be making it. Where these are ~0 the premise holds on this capture and the
    anchor choice is immaterial; where they are not, the margins rest on
    something this capture does not satisfy.
    """
    if primary.get("status") != "value" or secondary.get("status") != "value":
        return {"status": "unavailable",
                "reason": "one of the two anchors did not fit ({0} / {1})".format(
                    primary.get("status"), secondary.get("status"))}
    out = {"status": "value",
           "px_per_ft_u_delta": primary["px_per_ft_u"] - secondary["px_per_ft_u"],
           "px_per_ft_v_delta": primary["px_per_ft_v"] - secondary["px_per_ft_v"],
           "margin_ft_delta": {}}
    for side in ("left", "right", "top", "bottom"):
        out["margin_ft_delta"][side] = (
            primary["margin_ft"][side] - secondary["margin_ft"][side])
    return out


def _coverage_by_category(record, scan, fit, tiff_path, image_w, image_h):
    """(4): painted / colour present / bbox region holds ink, per category.

    The ink test goes through the FITTED mapping, never the sidecar's. That is
    the whole point: on a capture where registration failed the sidecar's
    mapping puts every rectangle in the wrong place, so an ink test built on it
    would report a clean capture as empty. The fitted mapping is measured from
    this image's own pixels and stays correct there.

    When there is no fitted mapping the ink column is left UNMEASURED with its
    reason, rather than falling back to the sidecar's and reporting numbers
    that describe nowhere.
    """
    mapping = fit.get("mapping") if fit.get("status") == "value" else None
    by_category: dict[str, dict[str, Any]] = {}
    failed = record["paint_failed_element_ids"]

    ink_source = ("fitted mapping from measurement (2)" if mapping else None)
    ink_unavailable_reason = (
        None if mapping else
        "no fitted mapping (measurement 2 is {0}: {1}); the sidecar's mapping is "
        "deliberately NOT substituted".format(
            fit.get("status"), fit.get("reason") or "see measurement 2"))

    image = Image.open(tiff_path).convert("RGB") if mapping else None
    try:
        for element_id, entry in record["bbox_entries"].items():
            name = entry.get("category") or "<no category>"
            bucket = by_category.setdefault(name, {
                "assigned": 0, "painted": 0, "color_present": 0,
                "bbox_with_rect": 0, "ink_tested": 0, "ink_present": 0,
                "ink_off_image": 0, "ink_untested": 0})
            rgb = record["color_map"].get(element_id)
            if rgb is not None:
                bucket["assigned"] += 1
                if element_id not in failed:
                    bucket["painted"] += 1
                if _pack(rgb) in scan["blobs"]:
                    bucket["color_present"] += 1
            rect = entry.get("rect_uv")
            if rect is None:
                continue
            bucket["bbox_with_rect"] += 1
            if mapping is None:
                bucket["ink_untested"] += 1
                continue
            pixel_rect = uv_rect_to_pixel_rect(rect, mapping, image_w, image_h)
            if pixel_rect is None:
                bucket["ink_off_image"] += 1
                continue
            region = np.asarray(image.crop(pixel_rect), dtype=np.uint8)
            bucket["ink_tested"] += 1
            if ink_fraction(region) > INK_FRACTION_THRESHOLD:
                bucket["ink_present"] += 1
    finally:
        if image is not None:
            image.close()

    return {"ink_source": ink_source,
            "ink_unavailable_reason": ink_unavailable_reason,
            "ink_threshold": INK_FRACTION_THRESHOLD,
            "by_category": by_category}


# ======================================================================
# DISCOVERY
# ======================================================================

def discover_runs(target: Path):
    """Find every (combined JSON, variant -> capture) set under ``target``.

    Discovery is BY STRUCTURE and then CROSS-CHECKED against the combined
    JSON's recorded basenames, because the paths the probe recorded are
    absolute paths on the Revit machine and the analysis usually happens
    somewhere else. A recorded path that resolves is used; otherwise the file
    is looked for at ``<probe dir>/<variant>/color_id_buffer/<basename>``.

    A variant whose capture cannot be located either way is REPORTED MISSING
    with both paths tried. It is never quietly replaced by the only sidecar
    that happens to be in the tree -- which would attribute one variant's
    pixels to another.
    """
    target = target.resolve()
    combined_files: list[Path] = []
    if target.is_file():
        combined_files = [target]
    else:
        combined_files = sorted(target.rglob("*.anno_pass_variants.json"))

    runs = []
    for combined_path in combined_files:
        with open(combined_path, encoding="utf-8") as handle:
            combined = json.load(handle)
        probe_dir = combined_path.parent
        entry = {"combined_path": str(combined_path), "combined": combined,
                 "probe_dir": str(probe_dir), "captures": [], "missing": []}
        for variant_report in combined.get("variants", []):
            variant = variant_report.get("variant")
            if variant_report.get("skipped"):
                entry["missing"].append({
                    "variant": variant, "reason": "skipped by the probe: {0}".format(
                        variant_report.get("skip_reason"))})
                continue
            anno = variant_report.get("annotation_pass") or {}
            sidecar = _locate(anno.get("sidecar_path"), probe_dir, variant)
            tiff = _locate(anno.get("tiff_path"), probe_dir, variant)
            if sidecar is None or tiff is None:
                entry["missing"].append({
                    "variant": variant,
                    "reason": "could not locate the {0}".format(
                        "sidecar" if sidecar is None else "TIFF"),
                    "recorded_sidecar": anno.get("sidecar_path"),
                    "recorded_tiff": anno.get("tiff_path"),
                    "searched_under": str(probe_dir / str(variant)),
                })
                continue
            entry["captures"].append({"variant": variant, "sidecar": sidecar,
                                      "tiff": tiff,
                                      "variant_report": variant_report})
        model = combined.get("model_pass") or {}
        entry["model_tiff"] = _locate(model.get("tiff_path"), probe_dir, "model")
        runs.append(entry)
    return runs


def _locate(recorded, probe_dir: Path, variant) -> Path | None:
    if not recorded:
        return None
    direct = Path(str(recorded))
    if direct.is_file():
        return direct
    candidate = probe_dir / str(variant) / "color_id_buffer" / direct.name
    if candidate.is_file():
        return candidate
    matches = sorted((probe_dir / str(variant)).rglob(direct.name))
    return matches[0] if len(matches) == 1 else None


# ======================================================================
# REPORT
# ======================================================================

def _fmt(value, spec="{0:.3f}"):
    if value is None:
        return "--"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return spec.format(value)
    return str(value)


def _table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return lines


def render_run(run, analyses, overlay_results):
    """One view's section of the report. Numbers only."""
    combined = run["combined"]
    inputs = combined.get("inputs") or {}
    lines = []
    lines.append("## View {0} (id {1}, {2}, scale 1:{3})".format(
        inputs.get("view_name"), inputs.get("view_id"), inputs.get("view_type"),
        _fmt(inputs.get("view_scale"), "{0:.0f}")))
    lines.append("")
    lines.append("Probe `{0}` version {1}; combined report `{2}`.".format(
        (combined.get("probe") or {}).get("name"),
        (combined.get("probe") or {}).get("version"), run["combined_path"]))
    model = combined.get("model_pass") or {}
    lines.append("")
    lines.append("Model pass: success={0}, failure_reason={1}, "
                 "achieved dpi={2}, frame_px={3}.".format(
                     model.get("success"), model.get("failure_reason"),
                     _fmt(((model.get("geometry") or {}).get(
                         "achieved_export_dpi")), "{0:.2f}"),
                     (model.get("geometry") or {}).get("frame_px")))
    reexport = combined.get("model_reexport_after_variants") or {}
    verdict = reexport.get("verdict") or {}
    if verdict:
        lines.append("Model re-export after the variants: **{0}** -- {1}".format(
            verdict.get("status"),
            verdict.get("reason")
            or "the control held and the post-variant hash matches"))
    for missing in run.get("missing", []):
        lines.append("")
        lines.append("- `{0}`: NO CAPTURE ANALYSED. {1}".format(
            missing.get("variant"), missing.get("reason")))
    for item in analyses:
        sidecar = item["analysis"]["sidecar"]
        for label, records in (
                ("colour map", sidecar.get("malformed_color_entries")),
                ("bbox map key", sidecar.get("malformed_bbox_keys"))):
            if records:
                lines.append("")
                lines.append("- `{0}`: {1} malformed {2} entr{3} in the sidecar, "
                             "NOT counted in any table below: {4}".format(
                                 item["variant"], len(records), label,
                                 "y" if len(records) == 1 else "ies",
                                 records[:5]))
    lines.append("")

    # ---- (0) what production says it DID ----------------------------
    #
    # This section exists because these fields were being READ and never
    # RENDERED, which made a variant that measured nothing look exactly like
    # one that did. `applied_smooth_edges` is the whole content of V2, and
    # `model_suppression_mode` is the whole content of V1: a capture that came
    # back "read_failed" or "hide_categories" measured the variant BELOW it.
    lines.append("### 0. What the capture reports it actually did")
    lines.append("")
    rows = []
    for item in analyses:
        sidecar = item["analysis"]["sidecar"]
        variant_report = item.get("variant_report") or {}
        measurement = variant_report.get("measurement") or {}
        faults = sidecar.get("capture_faults") or []
        rows.append([
            item["variant"],
            _fmt(sidecar.get("model_suppression_mode")),
            _fmt(sidecar.get("applied_smooth_edges")),
            _fmt(sidecar.get("applied_display_style")),
            _fmt(variant_report.get("conclusion")),
            ("yes" if measurement.get("measured") is True
             else ("NO" if measurement.get("measured") is False else "--")),
            (", ".join(str(f.get("fault")) for f in faults) or "none"),
        ])
    lines.extend(_table(["variant", "suppression mode", "applied_smooth_edges",
                         "display style", "probe conclusion",
                         "measured its candidate", "capture faults"], rows))
    lines.append("")
    lines.append("`applied_smooth_edges` is four-valued: `not_attempted` (the pass "
                 "was not asked), `read_failed`, `unchanged (failed)`, or `False` "
                 "(**confirmed off**). A V2/V3 row that is anything but `False` did "
                 "NOT have anti-aliasing disabled and therefore measures the same "
                 "behaviour as the variant above it -- the probe reports that as "
                 "`DID_NOT_MEASURE` rather than `RAN`.")
    lines.append("")
    for item in analyses:
        unmet = ((item.get("variant_report") or {}).get("measurement")
                 or {}).get("unmet") or []
        for entry in unmet:
            lines.append("- `{0}` DID NOT MEASURE: requested {1}, production "
                         "reported `{2}`. {3}".format(
                             item["variant"], entry.get("requested"),
                             entry.get("production_reported"),
                             entry.get("why_it_matters")))
    if any(((item.get("variant_report") or {}).get("measurement") or {}).get("unmet")
           for item in analyses):
        lines.append("")

    # ---- (1) size ---------------------------------------------------
    lines.append("### 1. Image size vs `frame_px`, both axes")
    lines.append("")
    rows = []
    for item in analyses:
        size = item["analysis"]["measurements"]["size"]
        rows.append([item["variant"],
                     "{0}x{1}".format(size["image_w"], size["image_h"]),
                     "{0}x{1}".format(size.get("frame_px_w", "--"),
                                      size.get("frame_px_h", "--")),
                     _fmt(size.get("delta_w")), _fmt(size.get("delta_h")),
                     _fmt(size.get("matches_frame_px")),
                     _fmt(size.get("dim_check"))])
    lines.extend(_table(["variant", "image", "frame_px", "dw", "dh",
                         "equal", "sidecar dim_check"], rows))
    lines.append("")
    lines.append("`dw`/`dh` are image minus `frame_px`. `dim_check` is the "
                 "sidecar's own verdict, which inspects the requested axis only "
                 "(finding F4) -- so a nonzero `dh` beside `dim_check=pass` is "
                 "exactly the case F4 names.")
    lines.append("")

    # ---- (2) registration -------------------------------------------
    lines.append("### 2. Registration fit (measured from the pixels)")
    lines.append("")
    rows = []
    for item in analyses:
        fit = item["analysis"]["measurements"]["registration"]
        if fit.get("status") != "value":
            rows.append([item["variant"], "NOT FITTED", "--", "--", "--", "--",
                         "--", fit.get("reason") or "--"])
            continue
        rows.append([
            item["variant"],
            "{0} pairs".format(fit["sample_count"]),
            _fmt(fit.get("px_per_ft_u")),
            _fmt(fit.get("px_per_ft_v")),
            _fmt(fit.get("frame_px_per_ft")),
            "({0}, {1})".format(_fmt(fit["frame_top_left_predicted_px"][0], "{0:.1f}"),
                                _fmt(fit["frame_top_left_predicted_px"][1], "{0:.1f}")),
            "{0} / {1}".format(_fmt(fit.get("residual_median_px"), "{0:.2f}"),
                               _fmt(fit.get("residual_max_px"), "{0:.2f}")),
            fit.get("v_axis_sign", "--")])
    lines.extend(_table(["variant", "samples", "px/ft u", "px/ft v",
                         "frame px/ft", "offset at (u0,v1) px",
                         "residual med/max px", "v axis"], rows))
    lines.append("")
    lines.append("#### 2b. Fitted per-side margin (how much bigger the render is "
                 "than the frame)")
    lines.append("")
    rows = []
    for item in analyses:
        fit = item["analysis"]["measurements"]["registration"]
        if fit.get("status") != "value":
            rows.append([item["variant"], "--", "--", "--", "--", "NOT FITTED"])
            continue
        ft = fit["margin_ft"]
        inches = fit["margin_paper_in"]
        rows.append([item["variant"]]
                    + ["{0} ft / {1} in".format(_fmt(ft[side], "{0:.2f}"),
                                                _fmt(inches[side], "{0:.3f}"))
                       for side in ("left", "right", "top", "bottom")]
                    + [""])
    lines.extend(_table(["variant", "left", "right", "top", "bottom", ""], rows))
    lines.append("")
    lines.append("#### 2c. The fit's own premise: does the ink sit where the bbox "
                 "says?")
    lines.append("")
    lines.append("The fit matches an element's exact-colour centroid against the "
                 "centre of its recorded `bbox_uv`. **Those coincide only when the "
                 "ink fills the box**, which real text, a tag with a leader or a "
                 "dimension need not do. A displacement that is the SAME for every "
                 "element is absorbed into the fitted intercept, so it leaves the "
                 "residuals in 2 clean while shifting every margin in 2b by exactly "
                 "that amount; one that varies with size or position corrupts the "
                 "scale instead and does inflate the residuals. Both are surfaced "
                 "here. No tolerance is applied -- what is close enough for a margin "
                 "figure is your call.")
    lines.append("")
    rows = []
    for item in analyses:
        agreement = item["analysis"]["measurements"].get("anchor_agreement") or {}
        alt = item["analysis"]["measurements"].get(
            "registration_ink_bbox_anchor") or {}
        if agreement.get("status") != "value":
            rows.append([item["variant"], "--", "--", "--", "--", "--",
                         agreement.get("reason") or "--"])
            continue
        deltas = agreement["margin_ft_delta"]
        rows.append([item["variant"],
                     _fmt(alt.get("px_per_ft_u")), _fmt(alt.get("px_per_ft_v")),
                     _fmt(agreement["px_per_ft_u_delta"], "{0:.4f}"),
                     _fmt(agreement["px_per_ft_v_delta"], "{0:.4f}"),
                     " / ".join(_fmt(deltas[side], "{0:.3f}")
                                for side in ("left", "right", "top", "bottom")),
                     ""])
    lines.extend(_table(["variant", "ink-bbox px/ft u", "ink-bbox px/ft v",
                         "px/ft u delta", "px/ft v delta",
                         "margin delta ft (l/r/t/b)", ""], rows))
    lines.append("")
    lines.append("The second anchor is the centre of the ink's own bounding box "
                 "rather than its centroid. The two are identical when the ink "
                 "fills its bbox and diverge when it does not, so a row of ~0 "
                 "deltas says the anchor choice does not matter on this capture "
                 "and the margins above do not rest on the premise.")
    lines.append("")
    lines.append("A UNIFORM displacement -- every element's ink sitting the same "
                 "distance off its box centre -- is NOT recoverable from a capture: "
                 "nothing distinguishes it from the whole render sitting that far "
                 "over, which is why it is the dangerous case. What IS recoverable "
                 "is whether such a displacement is POSSIBLE, and by how much: ink "
                 "that spans its whole box cannot be off-centre in it. The table "
                 "below bounds it.")
    lines.append("")
    for item in analyses:
        premise = (item["analysis"]["measurements"]["registration"]
                   .get("ink_vs_bbox_by_category") or {})
        if not premise:
            continue
        lines.append("##### `{0}` does the ink fill its recorded bbox?".format(
            item["variant"]))
        lines.append("")
        rows = []
        for name in sorted(premise):
            entry = premise[name]

            def _cell(stats, spec="{0:.3f}"):
                if not stats:
                    return "--"
                return "{0} ({1} .. {2})".format(
                    _fmt(stats["median"], spec), _fmt(stats["min"], spec),
                    _fmt(stats["max"], spec))

            rows.append([
                name,
                (entry["span_fraction_u"] or {}).get("count", "--"),
                _cell(entry["span_fraction_u"]),
                _cell(entry["span_fraction_v"]),
                _cell(entry["solidity"]),
                _cell(entry["possible_offset_u_px"], "{0:.1f}"),
                _cell(entry["possible_offset_v_px"], "{0:.1f}"),
            ])
        lines.extend(_table(
            ["category", "n", "ink span / bbox u", "ink span / bbox v",
             "solidity", "possible offset u px", "possible offset v px"], rows))
        lines.append("")
        lines.append("`ink span / bbox` of 1.0 means the ink reaches both edges of "
                     "its recorded box, so it cannot be off-centre in it and the "
                     "margins above are bounded. `solidity` is ink pixels over ink "
                     "bbox area: below 1 the ink is not a solid rectangle -- a glyph "
                     "run, an L, a leader -- so its centroid can also sit away from "
                     "its own bbox centre. `possible offset` is how far, in pixels, "
                     "a margin in 2b could be wrong because of this.")
        lines.append("")

    for item in analyses:
        fit = item["analysis"]["measurements"]["registration"]
        diagnostics = fit.get("sample_diagnostics") or {}
        lines.append("- `{0}` samples: matched {1}, no bbox {2}, colour absent "
                     "{3}, clipped at an image edge {4}{5}.".format(
                         item["variant"], diagnostics.get("matched"),
                         diagnostics.get("no_rect"),
                         diagnostics.get("color_absent"),
                         diagnostics.get("border_clipped"),
                         ", palette collisions {0}".format(
                             diagnostics["color_collision"])
                         if diagnostics.get("color_collision") else ""))
    lines.append("")

    # ---- (3) excursion ----------------------------------------------
    lines.append("### 3. How far recorded annotation bboxes reach past the "
                 "rendered frame")
    lines.append("")
    rows = []
    for item in analyses:
        excursion = item["analysis"]["measurements"]["bbox_excursion"]
        if excursion.get("status") != "value":
            rows.append([item["variant"], "--", "--", "--", "--", "--",
                         excursion.get("reason") or "--"])
            continue
        rows.append([item["variant"]]
                    + ["{0} ft / {1} in".format(
                        _fmt(excursion[side], "{0:.2f}"),
                        _fmt(excursion["paper_in"][side], "{0:.3f}"))
                       for side in ("left", "right", "top", "bottom")]
                    + ["{0} of {1}".format(excursion["beyond_count"],
                                           excursion["rect_count"]), ""])
    lines.extend(_table(["variant", "left", "right", "top", "bottom",
                         "bboxes outside", ""], rows))
    lines.append("")
    lines.append("Read this table against 2b. Where they agree, the render is "
                 "behaving as if it had been fitted to the crop unioned with the "
                 "annotations drawn beyond it.")
    lines.append("")

    # ---- (4) coverage ------------------------------------------------
    lines.append("### 4. Coverage per category")
    lines.append("")
    for item in analyses:
        coverage = item["analysis"]["measurements"]["coverage"]
        lines.append("#### `{0}`".format(item["variant"]))
        lines.append("")
        if coverage["ink_source"] is None:
            lines.append("Ink column UNMEASURED: {0}".format(
                coverage["ink_unavailable_reason"]))
        else:
            lines.append("Ink test threshold >{0:.0%} non-white, placed through "
                         "the {1}.".format(coverage["ink_threshold"],
                                           coverage["ink_source"]))
        lines.append("")
        rows = []
        for name in sorted(coverage["by_category"]):
            bucket = coverage["by_category"][name]
            rows.append([name, bucket["assigned"], bucket["painted"],
                         bucket["color_present"], bucket["bbox_with_rect"],
                         bucket["ink_tested"], bucket["ink_present"],
                         bucket["ink_off_image"], bucket["ink_untested"]])
        lines.extend(_table(["category", "assigned", "painted", "colour present",
                             "bbox", "ink tested", "ink present", "bbox off image",
                             "ink untested"], rows))
        lines.append("")

    # ---- (5) off-palette --------------------------------------------
    lines.append("### 5. Non-white, non-palette pixels")
    lines.append("")
    rows = []
    for item in analyses:
        off = item["analysis"]["measurements"]["offpalette"]
        rows.append([item["variant"], off["total_pixels"], off["white_pixels"],
                     off["palette_pixels"], off["offpalette_pixels"],
                     _fmt(off.get("offpalette_fraction"), "{0:.5f}"),
                     off["blend_pixels"], off["grey_pixels"],
                     off["other_pixels"], off["distinct_colors"],
                     _fmt(off["tally_capped"])])
    lines.extend(_table(["variant", "total", "white", "palette", "off-palette",
                         "off-palette frac", "blend", "grey", "other",
                         "distinct", "tally capped"], rows))
    lines.append("")
    lines.append("`blend` is within {0:.0f} RGB units of the segment between some "
                 "palette colour and white. `grey` is all three channels within "
                 "{1} of each other. The two OVERLAP -- a grey pixel is also "
                 "collinear with white -- and blend is tested first; the "
                 "`blend also grey` count below states what that ordering "
                 "costs.".format(BLEND_TOLERANCE, GREY_TOLERANCE))
    lines.append("")
    for item in analyses:
        off = item["analysis"]["measurements"]["offpalette"]
        lines.append("- `{0}` blend also grey: {1} px.{2}".format(
            item["variant"], off["blend_also_grey_pixels"],
            " " + off["tally_capped_note"] if off.get("tally_capped_note") else ""))
    lines.append("")
    for item in analyses:
        off = item["analysis"]["measurements"]["offpalette"]
        lines.append("#### `{0}` top 10 off-palette colours".format(item["variant"]))
        lines.append("")
        lines.extend(_table(
            ["rgb", "pixels", "class", "also grey"],
            [[str(colour["rgb"]), colour["pixels"], colour["class"],
              _fmt(colour["also_grey"])] for colour in off["top_colors"]]
            or [["--", "--", "--", "--"]]))
        lines.append("")

    # ---- (6) model hash ---------------------------------------------
    lines.append("### 6. Model TIFF hash vs V0")
    lines.append("")
    rows = []
    for item in analyses:
        model_hash = item["analysis"]["measurements"]["model_tiff"]
        rows.append([item["variant"],
                     (model_hash["sha256"] or "--")[:16],
                     (model_hash["v0_sha256"] or "--")[:16],
                     _fmt(model_hash["matches_v0"])])
    lines.extend(_table(["variant", "model sha256 (16)", "V0 sha256 (16)",
                         "equal"], rows))
    lines.append("")
    lines.append("The model pass runs ONCE per view, so this column detects a "
                 "variant CLOBBERING the model artifact -- a path collision, a "
                 "stray write. Whether a variant changed how the model pass "
                 "RENDERS is the probe's own `model_reexport_after_variants` "
                 "verdict, quoted at the top of this view's section, which has "
                 "its own repeatability control.")
    lines.append("")

    # ---- overlay ----------------------------------------------------
    lines.append("### Overlays")
    lines.append("")
    lines.append("The gate is measurement 1 only: image size == `frame_px`. IT IS A "
                 "SIZE GATE AND NOTHING MORE. A capture can be exactly `frame_px` "
                 "pixels and still have its CONTENT drawn at a different scale or "
                 "origin -- which is finding F1 -- and `capture_overlay.py` maps UV "
                 "through the SIDECAR's numbers, so on such a capture its boxes will "
                 "not land on the ink. Measurement 2's fitted px/ft is echoed beside "
                 "each line so that is visible here rather than only three sections "
                 "up.")
    lines.append("")
    fits = dict((item["variant"], item["analysis"]["measurements"]["registration"])
                for item in analyses)
    for entry in overlay_results:
        fit = fits.get(entry["variant"]) or {}
        if fit.get("status") == "value":
            context = ("fitted {0} / {1} px/ft against the frame's {2}".format(
                _fmt(fit.get("px_per_ft_u")), _fmt(fit.get("px_per_ft_v")),
                _fmt(fit.get("frame_px_per_ft"))))
        else:
            context = "measurement 2 did not fit: {0}".format(
                fit.get("reason") or fit.get("status"))
        if entry["ran"]:
            lines.append("- `{0}`: overlay written ({1}) -> {2}".format(
                entry["variant"], context,
                entry.get("stdout_tail") or "(see tool output)"))
        else:
            lines.append("- `{0}`: OVERLAY WITHHELD ({1}). {2}".format(
                entry["variant"], context, entry["reason"]))
    lines.append("")
    return lines


def maybe_run_overlay(analysis, sidecar_path, enabled):
    """Run ``tools/capture_overlay.py``, but only where (1) says image == frame_px.

    The overlay maps UV through the sidecar's recorded export size and refuses
    when the file disagrees with it. On a capture whose image is not the frame,
    running it either produces a refusal panel that says nothing new or places
    boxes against a rectangle that was never rendered. Withheld WITH THE REASON
    in both cases, never silently skipped.
    """
    size = analysis["measurements"]["size"]
    if not enabled:
        return {"ran": False, "reason": "overlays disabled with --no-overlay"}
    if not size.get("matches_frame_px"):
        return {"ran": False,
                "reason": "measurement 1 shows image {0}x{1} against frame_px "
                          "{2}x{3}; the overlay is not run on a capture whose "
                          "rendered region is not the frame its sidecar "
                          "describes".format(
                              size["image_w"], size["image_h"],
                              size.get("frame_px_w"), size.get("frame_px_h"))}
    overlay_tool = _REPO_ROOT / "tools" / "capture_overlay.py"
    if not overlay_tool.is_file():
        return {"ran": False, "reason": "tools/capture_overlay.py is not present"}
    try:
        completed = subprocess.run(
            [sys.executable, str(overlay_tool), str(sidecar_path)],
            capture_output=True, text=True, timeout=1800, check=False)
    except (OSError, subprocess.SubprocessError) as ex:
        return {"ran": False, "reason": "capture_overlay.py could not be run: "
                                        "{0}: {1}".format(type(ex).__name__, ex)}
    if completed.returncode != 0:
        return {"ran": False,
                "reason": "capture_overlay.py exited {0}: {1}".format(
                    completed.returncode,
                    (completed.stderr or "").strip()[-400:] or "no stderr")}
    tail = [line for line in (completed.stdout or "").splitlines() if line.strip()]
    return {"ran": True, "stdout_tail": tail[-1] if tail else ""}


# ======================================================================
# CLI
# ======================================================================

def _recorded_model_sha(run, variant):
    """The model TIFF hash the PROBE recorded after ``variant`` rolled back."""
    for variant_report in (run.get("combined") or {}).get("variants", []):
        if variant_report.get("variant") != variant:
            continue
        record = (variant_report.get("model_tiff") or {}).get("sha256") or {}
        return record.get("value") if record.get("state") == "value" else None
    return None


def build_report(targets, overlay_enabled=True):
    lines = ["# Annotation-pass variant report", "",
             "Values only. No pass/fail, no score, no recommendation -- the "
             "probe brief puts the judgement with Greg, and a tool that rated "
             "the variants would replace that read.", ""]
    any_run = False
    for target in targets:
        for run in discover_runs(Path(target)):
            any_run = True
            model_tiff = run.get("model_tiff")
            model_sha = sha256_of(model_tiff) if model_tiff else None
            analyses = []
            overlay_results = []
            # THE PROBE'S OWN per-variant reading, preferred over re-hashing the
            # file here. The probe re-hashes the model TIFF after each variant's
            # rollback, inside the run; hashing it once afterwards would make
            # every row trivially equal and could not see a variant that
            # clobbered the file and a later one that restored it. The
            # analyzer's own hash is the fallback for a run whose probe did not
            # record one.
            v0_sha = _recorded_model_sha(run, "v0_control") or model_sha
            for capture in run["captures"]:
                analysis = analyze_capture(
                    capture["sidecar"], capture["tiff"],
                    model_tiff_sha=(_recorded_model_sha(run, capture["variant"])
                                    or model_sha),
                    v0_model_tiff_sha=v0_sha)
                analyses.append({"variant": capture["variant"],
                                 "analysis": analysis,
                                 "variant_report": capture.get("variant_report")})
                overlay = maybe_run_overlay(analysis, capture["sidecar"],
                                            overlay_enabled)
                overlay["variant"] = capture["variant"]
                overlay_results.append(overlay)
            lines.extend(render_run(run, analyses, overlay_results))
    if not any_run:
        lines.append("No `*.anno_pass_variants.json` found under: {0}".format(
            ", ".join(str(t) for t in targets)))
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Per view x variant evidence for the annotation-pass "
                    "variant probe. Emits values, never a verdict.")
    parser.add_argument("targets", nargs="+",
                        help="probe output directories, or "
                             "*.anno_pass_variants.json files")
    parser.add_argument("--out", default=None,
                        help="write the report here instead of stdout")
    parser.add_argument("--no-overlay", dest="overlay", action="store_false",
                        help="do not invoke tools/capture_overlay.py")
    parser.set_defaults(overlay=True)
    args = parser.parse_args(argv)
    report = build_report(args.targets, overlay_enabled=args.overlay)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print("wrote {0}".format(args.out))
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
