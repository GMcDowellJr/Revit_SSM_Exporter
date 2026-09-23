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

ROUND 2 (REVISED) ADDS FIVE, because the capture no longer moves the crop and
registration has to be MEASURED from fiducials rather than read off a frame:

7  F1 -- the crop boundary (V8 turns CropBoxVisible on), recovered from the
   pixels as bands that close into one rectangle -- or, for a non-rectangular
   crop, match its shape level by level. "NOT FOUND" on V8 is the answer that
   ImageExportOptions does not draw it. V8's own model capture is checked
   too, against the one known answer: its lattice.
8  F2 -- the fiducial pair's ink edges against their recorded extents, and
   again against their drawn extents in the model capture.
9  F1 vs F2 vs the bbox fit. The disagreement is the number.
10 datum extents: each datum's ink against the bbox recorded before the
   capture touched the view.
11 model-ink residue: off-palette pixels outside the fiducials and the
   recovered boundary.

``--json-out`` writes the recovered boundary rectangles and fiducial
positions per capture, so a consumer can subtract them.

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
    # A ZERO OR NON-FINITE SLOPE IS AN UNAVAILABLE FIT, NOT AN EXCEPTION.
    # fit_axis returns None only when the SAMPLE x values have no spread; when
    # distinct recorded UV positions all render at the SAME pixel it validly
    # returns a slope of 0.0, and every inversion below then divides by it. A
    # collapsed or malformed capture is exactly an input this diagnostic exists
    # to report -- and one of them used to abort the whole multi-view report
    # with a ZeroDivisionError before any of the other views were written.
    if not all(math.isfinite(value) for value in (a_u, b_u, a_v, b_v)) or (
            a_u == 0.0 or a_v == 0.0):
        out["status"] = "unavailable"
        out["reason"] = (
            "the fit produced a degenerate mapping (px/ft u={0!r}, v={1!r}): every "
            "sample renders at the same pixel on at least one axis, so there is no "
            "invertible mapping and no margin to report".format(a_u, a_v))
        out["degenerate_mapping"] = {"a_u": a_u, "b_u": b_u, "a_v": a_v, "b_v": b_v}
        return out
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
    out["mapping"] = {"a_u": a_u, "b_u": b_u, "a_v": a_v, "b_v": b_v}
    out["ink_vs_bbox_by_category"] = ink_vs_bbox_geometry(samples, out["mapping"])
    out["feet_per_pixel_fitted"] = (1.0 / a_u) if a_u else None

    if frame_uv is None:
        # AN UNTOUCHED CAPTURE OF A CROP-LESS VIEW. No rectangle was handed to
        # Revit and none is authored, so there is no frame to measure margins
        # against -- but the SCALE is still measured, which is most of what a
        # crop-less capture can say. The margins are withheld, not zeroed.
        out["frame_px_per_ft"] = None
        out["frame_px_per_ft_reason"] = "no reference rectangle"
        out["margin_ft"] = None
        out["margin_paper_in"] = None
        out["frame_top_left_predicted_px"] = None
        return out

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
    # The premise (ink_vs_bbox_by_category, above) is measured OUTSIDE the fit,
    # because the fit absorbs exactly the displacement in question.
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


def scan_image(path, palette_rgbs, fiducial_rgbs=()):
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

    ``fiducial_rgbs`` (V8's F2 colours) are tracked as blobs like palette
    colours, but counted as ``fiducial_pixels`` -- neither palette nor
    off-palette -- because the probe put them there on purpose, and leaving
    them in the off-palette tally would read as model ink the suppression
    missed.
    """
    fiducial_keys = {_pack(rgb) for rgb in fiducial_rgbs or ()}
    keys_sorted = np.array(sorted({_pack(rgb) for rgb in palette_rgbs}
                                  | fiducial_keys), dtype=np.int64)
    fiducial_sorted = np.array(sorted(fiducial_keys), dtype=np.int64)
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
    fiducial_pixels = 0
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
            if len(fiducial_sorted):
                is_fiducial = np.isin(flat, fiducial_sorted)
                fiducial_pixels += int(is_fiducial.sum())
                palette_pixels += int((is_palette & ~is_fiducial).sum())
            else:
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
        "fiducial_pixels": fiducial_pixels,
        "offpalette_pixels": (total_pixels - white_pixels - palette_pixels
                              - fiducial_pixels),
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
# F1 -- THE CROP BOUNDARY, recovered from the pixels
# ======================================================================
#
# ROUND 2 (REVISED). V8 turns CropBoxVisible on, so the crop region draws at
# the crop's own UV bounds -- read from view.CropBox without changing it --
# and the capture carries its own ruler. Whether ImageExportOptions draws the
# boundary AT ALL is unconfirmed; this is the measurement that settles it, so
# "no boundary found" is reported as a finding with its reason, never papered
# over.
#
# A candidate boundary pixel is any pixel that is not white, not a palette
# colour and not a fiducial: the boundary is not painted, so it renders in
# its own line colour. A boundary edge is then a BAND of consecutive rows (or
# columns) each holding a long straight run of such pixels.

# A run must reach this fraction of the image's extent on its axis to be a
# boundary candidate. A crop edge spans the whole crop; text and leaders do not.
BOUNDARY_MIN_RUN_FRACTION = 0.25
# The longest bands kept per axis before the rectangle search, which is
# O(n^2) per axis pair.
BOUNDARY_MAX_BANDS = 12
# How far a band's span may fall short of the perpendicular pair it must meet,
# as a fraction of the extent, before the four are not one rectangle.
BOUNDARY_SPAN_TOLERANCE_FRACTION = 0.02
BOUNDARY_SPAN_TOLERANCE_MIN_PX = 3.0


# A drawn edge is crossed by annotation (a grid, a dimension, a tag leader)
# painted in a palette colour, which breaks its run into pieces. Gaps up to
# this many pixels -- or this fraction of the axis, whichever is larger -- are
# bridged. Round 2's plan capture is why: its crop's right edge was cut into
# pieces too short to qualify, and a rectangle was closed on an interior line.
BOUNDARY_GAP_MIN_PX = 8
BOUNDARY_GAP_FRACTION = 0.01


def boundary_gap_px(extent):
    return max(BOUNDARY_GAP_MIN_PX, int(BOUNDARY_GAP_FRACTION * extent))


def _row_longest_run(mask_row: np.ndarray, gap: int = 0) -> tuple[int, int, int]:
    """(length, first, last) of the longest True run in a 1-D mask, where
    False gaps of up to ``gap`` pixels do not break a run. ``length`` is the
    span, gaps included."""
    if not mask_row.any():
        return (0, -1, -1)
    padded = np.concatenate(([0], mask_row.astype(np.int8), [0]))
    delta = np.diff(padded)
    starts = np.flatnonzero(delta == 1)
    ends = np.flatnonzero(delta == -1)          # exclusive
    best = (0, -1, -1)
    run_start, run_end = int(starts[0]), int(ends[0])
    for start, end in zip(starts[1:].tolist(), ends[1:].tolist()):
        if start - run_end <= gap:
            run_end = end
            continue
        if run_end - run_start > best[0]:
            best = (run_end - run_start, run_start, run_end - 1)
        run_start, run_end = start, end
    if run_end - run_start > best[0]:
        best = (run_end - run_start, run_start, run_end - 1)
    return best


def scan_axis_lines(path, excluded_rgbs, boundary_rgbs=()):
    """One chunked pass: the longest candidate run in every row and column.

    A candidate is a GREY pixel (all channels within GREY_TOLERANCE) that is
    not white and not in ``excluded_rgbs`` (the palette, the fiducials) -- or
    any pixel in ``boundary_rgbs``, the colours a capture is KNOWN to have
    painted its crop-region element in. Grey only, because the anti-aliased
    fringe of a painted grid line is a palette-to-white blend: coloured, off
    the palette, straight and long, and without this it would read as an edge.
    Runs bridge gaps of ``boundary_gap_px`` along their own axis.
    """
    excluded = {_pack(rgb) for rgb in excluded_rgbs} | {_pack(WHITE)}
    forced = {_pack(rgb) for rgb in boundary_rgbs or ()}
    excluded -= forced
    keys_sorted = np.array(sorted(excluded), dtype=np.int64)
    forced_sorted = np.array(sorted(forced), dtype=np.int64)
    rows = []
    with Image.open(path) as handle:
        image = handle.convert("RGB")
        width, height = image.size
        row_gap = boundary_gap_px(width)
        col_gap = boundary_gap_px(height)
        col_start = np.zeros(width, dtype=np.int64)
        col_last = np.full(width, -(10 ** 9), dtype=np.int64)
        col_best = np.zeros(width, dtype=np.int64)
        col_best_start = np.full(width, -1, dtype=np.int64)
        col_best_end = np.full(width, -1, dtype=np.int64)
        for top in range(0, height, CHUNK_ROWS):
            bottom = min(height, top + CHUNK_ROWS)
            block = np.asarray(image.crop((0, top, width, bottom)), dtype=np.int64)
            keys = (block[:, :, 0] << 16) | (block[:, :, 1] << 8) | block[:, :, 2]
            idx = np.clip(np.searchsorted(keys_sorted, keys), 0, len(keys_sorted) - 1)
            not_excluded = keys_sorted[idx] != keys
            grey = (block.max(axis=2) - block.min(axis=2)) <= GREY_TOLERANCE
            candidate = not_excluded & grey
            if len(forced_sorted):
                candidate |= np.isin(keys, forced_sorted)
            for offset in range(candidate.shape[0]):
                row = candidate[offset]
                rows.append(_row_longest_run(row, row_gap))
                y = top + offset
                restart = row & ((y - col_last - 1) > col_gap)
                col_start[restart] = y
                col_last[row] = y
                length = col_last - col_start + 1
                better = row & (length > col_best)
                col_best[better] = length[better]
                col_best_start[better] = col_start[better]
                col_best_end[better] = y
    cols = [(int(col_best[x]), int(col_best_start[x]), int(col_best_end[x]))
            for x in range(width)]
    return {"image_w": width, "image_h": height, "rows": rows, "cols": cols,
            "gap_px": {"row": row_gap, "col": col_gap}}


def group_bands(runs, min_run):
    """Consecutive indices whose longest run is >= ``min_run``, as bands.

    A band is one drawn line a few pixels thick. ``centre`` is in continuous
    pixel coordinates -- index i covers [i, i+1) -- so a one-pixel line at
    index 10 is centred at 10.5, the same convention the UV maps use.
    """
    bands = []
    current = None
    for index, (length, first, last) in enumerate(runs):
        if length >= min_run:
            if current is not None and index == current["last"] + 1:
                current["last"] = index
                current["run_max"] = max(current["run_max"], length)
                current["span"][0] = min(current["span"][0], first)
                current["span"][1] = max(current["span"][1], last)
            else:
                current = {"first": index, "last": index, "run_max": length,
                           "span": [first, last]}
                bands.append(current)
    for band in bands:
        band["centre"] = (band["first"] + band["last"] + 1) / 2.0
        band["thickness_px"] = band["last"] - band["first"] + 1
    return bands


def mark_border_clipped(bands, extent):
    """Flag bands that touch the image border on their own axis.

    A model pass renders exactly its crop, so its boundary sits ON the image
    edge with half the stroke clipped, and the band's centre is biased inward
    by up to half a stroke (handoff 2026-09-22: 3042 vs 3039 rows). Flagged,
    never silently fitted as if whole.
    """
    for band in bands:
        band["border_clipped"] = bool(band["first"] == 0 or band["last"] == extent - 1)
    return bands


def _spans(band, lo, hi, tolerance):
    """The band runs from ``lo`` to ``hi`` and STOPS there, within tolerance.

    Both ends, not just coverage. A crop edge ends at its corners; a band that
    merely covers the gap between two perpendicular lines and runs on past one
    of them is closing a rectangle on the wrong line -- which is exactly what
    round 2's plan capture did, at u/v isotropy 0.49.
    """
    return (abs(band["span"][0] - lo) <= tolerance
            and abs(band["span"][1] + 1 - hi) <= tolerance)


def pick_rectangle(row_bands, col_bands, image_w, image_h):
    """The four bands that form ONE rectangle, or None with the reason.

    Consistency, not proximity: the left and right column bands must each span
    the gap between the top and bottom row bands, and vice versa. Of every
    consistent quadruple the largest is taken -- the crop is the outermost
    rectangle a view draws. Returns ``(record, reason)``.
    """
    rows = sorted(row_bands, key=lambda b: -b["run_max"])[:BOUNDARY_MAX_BANDS]
    cols = sorted(col_bands, key=lambda b: -b["run_max"])[:BOUNDARY_MAX_BANDS]
    tol_x = max(BOUNDARY_SPAN_TOLERANCE_MIN_PX,
                BOUNDARY_SPAN_TOLERANCE_FRACTION * image_w)
    tol_y = max(BOUNDARY_SPAN_TOLERANCE_MIN_PX,
                BOUNDARY_SPAN_TOLERANCE_FRACTION * image_h)
    best = None
    for i, top in enumerate(rows):
        for bottom in rows:
            if bottom["centre"] <= top["centre"]:
                continue
            for left in cols:
                for right in cols:
                    if right["centre"] <= left["centre"]:
                        continue
                    if not (_spans(top, left["centre"], right["centre"], tol_x)
                            and _spans(bottom, left["centre"], right["centre"], tol_x)
                            and _spans(left, top["centre"], bottom["centre"], tol_y)
                            and _spans(right, top["centre"], bottom["centre"], tol_y)):
                        continue
                    area = ((right["centre"] - left["centre"])
                            * (bottom["centre"] - top["centre"]))
                    if best is None or area > best[0]:
                        best = (area, top, bottom, left, right)
    if best is None:
        return (None, "{0} row band(s) and {1} column band(s) long enough to be a "
                      "crop edge, but no four of them close into one "
                      "rectangle".format(len(row_bands), len(col_bands)))
    _area, top, bottom, left, right = best
    return ({"top": top, "bottom": bottom, "left": left, "right": right}, None)


def _axis_fit_record(levels, positions):
    """Least-squares ``position = a * level + b``, with residuals."""
    fit = fit_axis([float(v) for v in levels], [float(p) for p in positions])
    if fit is None:
        return None
    a, b = fit
    residuals = [abs(a * lv + b - p) for lv, p in zip(levels, positions)]
    return {"a": a, "b": b, "residual_max_px": max(residuals) if residuals else None,
            "points": len(levels)}


def recover_crop_boundary(line_scan, shape, crop_uv):
    """F1: the crop boundary's pixel position, and the UV->pixel map it implies.

    ``shape`` is the probe's ``crop_loop_record`` (or None), ``crop_uv`` the
    crop's UV bounds read from view.CropBox. A RECTANGULAR crop is matched as
    one consistent rectangle. A non-rectangular one is matched level by level:
    the distinct u levels of its vertical edges against the column bands and
    the v levels against the row bands, in order -- never as four edges.
    Returns a record whose ``status`` is "value" only when both axes fitted.
    """
    image_w, image_h = line_scan["image_w"], line_scan["image_h"]
    row_bands = mark_border_clipped(
        group_bands(line_scan["rows"], BOUNDARY_MIN_RUN_FRACTION * image_w), image_h)
    col_bands = mark_border_clipped(
        group_bands(line_scan["cols"], BOUNDARY_MIN_RUN_FRACTION * image_h), image_w)
    out = {"row_band_count": len(row_bands), "col_band_count": len(col_bands),
           "min_run_fraction": BOUNDARY_MIN_RUN_FRACTION}
    if not row_bands and not col_bands:
        out["status"] = "not_found"
        out["reason"] = ("no row or column holds a straight non-palette run of "
                         ">= {0:.0%} of the image: the crop boundary is NOT in this "
                         "capture".format(BOUNDARY_MIN_RUN_FRACTION))
        return out
    is_rectangle = shape.get("is_rectangle") if shape else None
    if shape is None or is_rectangle:
        levels_source = ("crop_loop_record" if shape else
                         "view.CropBox bounds (the crop SHAPE was unavailable, so "
                         "it is ASSUMED rectangular)")
        if shape:
            u_levels = list(shape["distinct_u_levels"])
            v_levels = list(shape["distinct_v_levels"])
        elif crop_uv:
            u_levels = [float(crop_uv[0]), float(crop_uv[2])]
            v_levels = [float(crop_uv[1]), float(crop_uv[3])]
        else:
            out["status"] = "unavailable"
            out["reason"] = "neither the crop shape nor view.CropBox's UV is known"
            return out
        rect, reason = pick_rectangle(row_bands, col_bands, image_w, image_h)
        if rect is None:
            out["status"] = "not_found"
            out["reason"] = reason
            out["row_bands"] = row_bands[:BOUNDARY_MAX_BANDS]
            out["col_bands"] = col_bands[:BOUNDARY_MAX_BANDS]
            return out
        u_fit = _axis_fit_record(u_levels, [rect["left"]["centre"],
                                            rect["right"]["centre"]])
        # v grows UP, rows grow DOWN: the top band is the larger v.
        v_fit = _axis_fit_record(v_levels, [rect["bottom"]["centre"],
                                            rect["top"]["centre"]])
        out["bands"] = rect
        out["match"] = "one consistent rectangle"
    else:
        levels_source = "crop_loop_record (non-rectangular)"
        u_levels = list(shape["distinct_u_levels"])
        v_levels = list(shape["distinct_v_levels"])
        cols = sorted(sorted(col_bands, key=lambda b: -b["run_max"])[:len(u_levels)],
                      key=lambda b: b["centre"])
        rows = sorted(sorted(row_bands, key=lambda b: -b["run_max"])[:len(v_levels)],
                      key=lambda b: -b["centre"])
        if len(cols) != len(u_levels) or len(rows) != len(v_levels):
            out["status"] = "not_found"
            out["reason"] = ("the crop shape has {0} u level(s) and {1} v level(s); "
                             "the image has {2} column band(s) and {3} row "
                             "band(s)".format(len(u_levels), len(v_levels),
                                              len(col_bands), len(row_bands)))
            return out
        u_fit = _axis_fit_record(u_levels, [b["centre"] for b in cols])
        v_fit = _axis_fit_record(v_levels, [b["centre"] for b in rows])
        out["bands"] = {"cols": cols, "rows": rows}
        out["match"] = ("matched by RANK of the longest bands to the shape's "
                        "levels; a residual above a pixel says the rank match "
                        "picked the wrong line")
    out["levels_source"] = levels_source
    if u_fit is None or v_fit is None or u_fit["a"] == 0.0 or v_fit["a"] == 0.0:
        out["status"] = "unavailable"
        out["reason"] = "the boundary bands did not determine both axes"
        return out
    out["status"] = "value"
    out["mapping"] = {"a_u": u_fit["a"], "b_u": u_fit["b"],
                      "a_v": v_fit["a"], "b_v": v_fit["b"]}
    out["px_per_ft_u"] = u_fit["a"]
    out["px_per_ft_v"] = abs(v_fit["a"])
    out["v_axis_sign"] = ("negative (expected)" if v_fit["a"] < 0
                          else "POSITIVE (flipped)")
    out["isotropy_u_over_v"] = u_fit["a"] / abs(v_fit["a"])
    out["residual_max_px"] = {"u": u_fit["residual_max_px"],
                              "v": v_fit["residual_max_px"]}
    out["boundary_px_rect"] = _boundary_pixel_rects(out["bands"])
    chosen = (list(out["bands"].values()) if "top" in out["bands"]
              else out["bands"]["cols"] + out["bands"]["rows"])
    out["border_clipped_band_count"] = sum(1 for b in chosen if b.get("border_clipped"))
    if out["border_clipped_band_count"]:
        out["border_clipped_note"] = (
            "{0} boundary band(s) touch the image border, so their stroke is "
            "half-clipped and their centres are biased inward by up to half a "
            "stroke; in a MODEL capture the image edges ARE the crop, so read the "
            "lattice comparison, not these centres".format(
                out["border_clipped_band_count"]))
    return out


def _boundary_pixel_rects(bands):
    """Every recovered band as an integer pixel rectangle (x0, y0, x1, y1),
    end-exclusive: what a consumer subtracts."""
    if "top" in bands:
        rows, cols = [bands["top"], bands["bottom"]], [bands["left"], bands["right"]]
    else:
        rows, cols = bands.get("rows", []), bands.get("cols", [])
    return ([[b["span"][0], b["first"], b["span"][1] + 1, b["last"] + 1] for b in rows]
            + [[b["first"], b["span"][0], b["last"] + 1, b["span"][1] + 1]
               for b in cols])


# ======================================================================
# F2 -- THE FIDUCIAL PAIR
# ======================================================================

def fiducial_fit(fiducials, blobs, image_w, image_h):
    """F2: fit UV -> pixel from the two fiducials' INK EXTENTS.

    Each fiducial contributes its left and right edge on u and its top and
    bottom edge on v -- four points per axis for the pair -- matched against
    its recorded ``rect_uv``. Edges, not centroids, so the fit has a residual
    that says whether the ink really spans the recorded box (a projected 3D
    AABB can be looser than the element it bounds). A fiducial whose ink
    touches the image border is clipped, and is excluded rather than fitted.
    """
    per = []
    us, xs, vs, ys = [], [], [], []
    for fiducial in fiducials or []:
        rgb = tuple(int(c) for c in fiducial.get("rgb") or ())
        rect = fiducial.get("rect_uv")
        blob = blobs.get(_pack(rgb)) if len(rgb) == 3 else None
        entry = {"id": fiducial.get("id"), "rgb": list(rgb), "rect_uv": rect,
                 "found": blob is not None}
        if blob is not None:
            entry.update({"pixel_count": blob["pixel_count"],
                          "pixel_bbox": blob["pixel_bbox"],
                          "touches_border": blob["touches_border"]})
        per.append(entry)
        if blob is None or rect is None or blob["touches_border"]:
            continue
        x0, y0, x1, y1 = blob["pixel_bbox"]
        u0, v0, u1, v1 = (float(v) for v in rect)
        us += [u0, u1]
        xs += [x0, x1 + 1.0]
        vs += [v1, v0]
        ys += [y0, y1 + 1.0]
    # A fiducial with no rect_uv contributed nothing to the fit, so it is not
    # usable. Counting it let the plan's model-anchored F2 (whose wall never
    # drew in the model capture) fit ONE fiducial -- two points per axis, zero
    # residual -- and report that as a value.
    out = {"fiducials": per,
           "usable_count": sum(1 for e in per if e["found"]
                               and e["rect_uv"] is not None
                               and not e.get("touches_border"))}
    if out["usable_count"] < 2:
        out["status"] = "unavailable"
        out["reason"] = ("{0} usable fiducial(s) of {1}; F2 needs both, unclipped, "
                         "in their reserved colours, each with a UV extent".format(
                             out["usable_count"], len(per)))
        return out
    u_fit = _axis_fit_record(us, xs)
    v_fit = _axis_fit_record(vs, ys)
    if u_fit is None or v_fit is None or u_fit["a"] == 0.0 or v_fit["a"] == 0.0:
        out["status"] = "unavailable"
        out["reason"] = "the fiducials did not determine both axes"
        return out
    out["status"] = "value"
    out["mapping"] = {"a_u": u_fit["a"], "b_u": u_fit["b"],
                      "a_v": v_fit["a"], "b_v": v_fit["b"]}
    out["px_per_ft_u"] = u_fit["a"]
    out["px_per_ft_v"] = abs(v_fit["a"])
    out["isotropy_u_over_v"] = u_fit["a"] / abs(v_fit["a"])
    out["residual_max_px"] = {"u": u_fit["residual_max_px"],
                              "v": v_fit["residual_max_px"]}
    return out


# ======================================================================
# REGISTRATION MARKS (V9) -- detail lines the probe drew at KNOWN view UV
# ======================================================================
#
# Twelve ticks just inside the crop: two per corner and one mid-edge per side.
# A horizontal tick's centre ROW is its v; a vertical tick's centre COLUMN is
# its u. Six points per axis at THREE levels, so each fit has a residual that
# measures something (round 3's two levels gave 0.00 by construction). In the
# annotation capture production painted every tick its own palette colour (the
# sidecar's colour map names it); in the model capture all twelve share one
# reserved colour and are told apart as connected components.

def _load_rgb(path):
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


def _components(ys, xs):
    """8-connected components of a small pixel set, as index lists. Pure numpy
    and python: scipy is not a dependency of this tool."""
    index = {(int(y), int(x)): i for i, (y, x) in enumerate(zip(ys, xs))}
    parent = list(range(len(index)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for (y, x), i in index.items():
        for dy, dx in ((0, 1), (1, -1), (1, 0), (1, 1)):
            j = index.get((y + dy, x + dx))
            if j is not None:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj
    groups = {}
    for i in range(len(parent)):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _tick_measure(ys, xs, orientation):
    """A tick's centre line and extent, in continuous pixel coordinates (pixel
    i spans [i, i+1], as every other fit here)."""
    ys = np.asarray(ys, dtype=float)
    xs = np.asarray(xs, dtype=float)
    out = {"pixel_count": int(len(xs)),
           "pixel_bbox": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]}
    if orientation == "horizontal":
        out["centre_px"] = float(ys.mean()) + 0.5
        out["end_px"] = [float(xs.min()), float(xs.max()) + 1.0]
    else:
        out["centre_px"] = float(xs.mean()) + 0.5
        out["end_px"] = [float(ys.min()), float(ys.max()) + 1.0]
    return out


def locate_mark_pixels(pixels, marks, colour_by_id=None, shared_colour=None):
    """``{mark key: (ys, xs)}`` for each tick found, plus the reasons for the
    ones that were not.

    ``colour_by_id``: the annotation capture, one palette colour per tick.
    ``shared_colour``: the model capture, one reserved colour for all twelve;
    components are assigned to ticks by their bbox aspect (the orientation)
    and where their centroid falls: a horizontal tick by image HALF across and
    THIRD down (left/right x top/mid/bottom), a vertical one by THIRD across and
    HALF down. That needs no mapping, only the layout's promise that corner
    ticks stay in the outer thirds and mid ticks on the centre line. Several
    components landing on one tick (a tick crossed by other ink) are merged
    and counted; a component that fits no tick is counted too.
    """
    found, missing = {}, []
    if shared_colour is None:
        for mark in marks:
            rgb = (colour_by_id or {}).get(int(mark["id"])) if mark.get(
                "id") is not None else None
            if rgb is None:
                missing.append({"key": mark["key"], "reason": "no colour in the "
                                "capture's colour map for id {0}".format(mark.get("id"))})
                continue
            mask = np.all(pixels == np.asarray(rgb, dtype=pixels.dtype), axis=2)
            ys, xs = np.nonzero(mask)
            if len(xs) == 0:
                missing.append({"key": mark["key"], "reason": "its colour {0} drew "
                                "no pixels".format(list(rgb))})
                continue
            found[mark["key"]] = (ys, xs)
        return found, missing, {}
    mask = np.all(pixels == np.asarray(shared_colour, dtype=pixels.dtype), axis=2)
    ys, xs = np.nonzero(mask)
    height, width = pixels.shape[0], pixels.shape[1]
    by_slot = {}
    stats = {"components": 0, "merged_into_one_tick": 0}
    for component in _components(ys, xs):
        stats["components"] += 1
        cy, cx = ys[component], xs[component]
        orientation = ("horizontal" if (cx.max() - cx.min()) >= (cy.max() - cy.min())
                       else "vertical")
        x, y = cx.mean(), cy.mean()

        def _third(value, extent, names):
            return names[0 if value < extent / 3.0 else
                         (1 if value < 2.0 * extent / 3.0 else 2)]

        if orientation == "horizontal":
            corner = "{0}_{1}".format("left" if x < width / 2.0 else "right",
                                      _third(y, height, ("top", "mid", "bottom")))
        else:
            corner = "{0}_{1}".format(_third(x, width, ("left", "mid", "right")),
                                      "top" if y < height / 2.0 else "bottom")
        slot = "{0}_{1}".format(corner, orientation[0])
        if slot in by_slot:
            stats["merged_into_one_tick"] += 1
            by_slot[slot] = (np.concatenate([by_slot[slot][0], cy]),
                             np.concatenate([by_slot[slot][1], cx]))
        else:
            by_slot[slot] = (cy, cx)
    keys = set(m["key"] for m in marks)
    stats["unassigned_components"] = sorted(k for k in by_slot if k not in keys)
    for mark in marks:
        if mark["key"] in by_slot:
            found[mark["key"]] = by_slot[mark["key"]]
        else:
            missing.append({"key": mark["key"], "reason": "no component of the "
                            "shared colour in that corner and orientation"})
    return found, missing, stats


def registration_mark_fit(tiff_path, marks, colour_by_id=None, shared_colour=None,
                          identify_by_id=None):
    """F3: UV -> pixel from the ticks' CENTRE LINES, per axis, with residuals.

    Also an ENDPOINT fit (tick ends against their UV span), reported beside it
    and never in its place: an end is where line caps and anti-aliasing live.
    """
    marks = [m for m in marks or [] if m.get("key")]
    if not marks:
        return {"status": "not_applicable", "reason": "no registration marks"}
    try:
        pixels = _load_rgb(tiff_path)
    except (OSError, ValueError) as ex:
        return {"status": "unavailable",
                "reason": "could not read {0}: {1}: {2}".format(
                    tiff_path, type(ex).__name__, ex)}
    found, missing, stats = locate_mark_pixels(pixels, marks, colour_by_id,
                                               shared_colour)
    ticks = []
    centre = {"horizontal": ([], []), "vertical": ([], [])}
    ends = {"horizontal": ([], []), "vertical": ([], [])}
    mark_pixel_rects = []
    for mark in marks:
        if mark["key"] not in found:
            continue
        ys, xs = found[mark["key"]]
        measured = _tick_measure(ys, xs, mark["orientation"])
        tick = dict(key=mark["key"], id=mark.get("id"),
                    orientation=mark["orientation"], level_uv=mark["level_uv"],
                    span_uv=mark["span_uv"], **measured)
        ticks.append(tick)
        mark_pixel_rects.append(measured["pixel_bbox"])
        centre[mark["orientation"]][0].append(float(mark["level_uv"]))
        centre[mark["orientation"]][1].append(measured["centre_px"])
        lo, hi = (float(v) for v in mark["span_uv"])
        if mark["orientation"] == "horizontal":   # ends are u
            ends["horizontal"][0].extend([lo, hi])
            ends["horizontal"][1].extend(measured["end_px"])
        else:                                       # ends are v; +v is -y
            ends["vertical"][0].extend([hi, lo])
            ends["vertical"][1].extend(measured["end_px"])
    out = {"ticks": ticks, "missing": missing, "found_count": len(ticks),
           "expected_count": len(marks), "components": stats,
           "mark_pixel_rects": mark_pixel_rects,
           "mark_pixels": int(sum(t["pixel_count"] for t in ticks)),
           "image_w": int(pixels.shape[1]), "image_h": int(pixels.shape[0])}
    # u from the VERTICAL ticks' columns, v from the HORIZONTAL ticks' rows.
    u_fit = _axis_fit_record(*centre["vertical"])
    v_fit = _axis_fit_record(*centre["horizontal"])
    if (u_fit is None or v_fit is None or u_fit["a"] == 0.0 or v_fit["a"] == 0.0
            or len(set(round(v, 9) for v in centre["vertical"][0])) < 2
            or len(set(round(v, 9) for v in centre["horizontal"][0])) < 2):
        out["status"] = "unavailable"
        out["reason"] = ("{0} of {1} ticks found; each axis needs ticks at both of "
                         "its levels".format(len(ticks), len(marks)))
        return out
    out["status"] = "value"
    out["mapping"] = {"a_u": u_fit["a"], "b_u": u_fit["b"],
                      "a_v": v_fit["a"], "b_v": v_fit["b"]}
    out["px_per_ft_u"] = u_fit["a"]
    out["px_per_ft_v"] = abs(v_fit["a"])
    out["isotropy_u_over_v"] = u_fit["a"] / abs(v_fit["a"])
    out["residual_max_px"] = {"u": u_fit["residual_max_px"],
                              "v": v_fit["residual_max_px"]}
    out["points"] = {"u": u_fit["points"], "v": v_fit["points"]}
    end_u = _axis_fit_record(*ends["horizontal"])
    end_v = _axis_fit_record(*ends["vertical"])
    out["endpoint_fit"] = {
        "px_per_ft_u": end_u["a"] if end_u else None,
        "px_per_ft_v": abs(end_v["a"]) if end_v else None,
        "residual_max_px": {"u": end_u["residual_max_px"] if end_u else None,
                            "v": end_v["residual_max_px"] if end_v else None},
    }
    # WHAT DREW WHERE A MISSING TICK SHOULD BE. Round 3b's elevation lost the
    # two mid-height horizontal ticks from the annotation capture and the two
    # mid-width vertical ones from the model capture, with every other tick
    # present -- and "not found" alone cannot say whether the tick was
    # overdrawn, drawn in another colour, or never drawn. The fitted map puts
    # each missing tick at a pixel rectangle; its colours are counted there.
    out["missing_diagnosis"] = [
        _diagnose_missing_tick(pixels, mark, out["mapping"],
                               identify_by_id or colour_by_id)
        for mark in marks
        if mark["key"] in set(m["key"] for m in missing)]
    return out


MISSING_TICK_PAD_PX = 3


def _diagnose_missing_tick(pixels, mark, mapping, colour_by_id=None):
    """The colours inside the rectangle where ``mark`` should have drawn."""
    lo, hi = (float(v) for v in mark["span_uv"])
    level = float(mark["level_uv"])
    if mark["orientation"] == "horizontal":
        xs = sorted((mapping["a_u"] * lo + mapping["b_u"],
                     mapping["a_u"] * hi + mapping["b_u"]))
        y = mapping["a_v"] * level + mapping["b_v"]
        x0, x1, y0, y1 = xs[0], xs[1], y, y
    else:
        ys = sorted((mapping["a_v"] * lo + mapping["b_v"],
                     mapping["a_v"] * hi + mapping["b_v"]))
        x = mapping["a_u"] * level + mapping["b_u"]
        x0, x1, y0, y1 = x, x, ys[0], ys[1]
    pad = MISSING_TICK_PAD_PX
    height, width = pixels.shape[0], pixels.shape[1]
    c0, c1 = max(0, int(x0) - pad), min(width, int(x1) + pad + 1)
    r0, r1 = max(0, int(y0) - pad), min(height, int(y1) + pad + 1)
    out = {"key": mark["key"], "id": mark.get("id"),
           "expected_px_rect": [c0, r0, c1 - 1, r1 - 1]}
    if c1 <= c0 or r1 <= r0:
        out["reason"] = "the expected rectangle falls outside the image"
        return out
    window = pixels[r0:r1, c0:c1].reshape(-1, 3)
    counts = Counter(tuple(int(c) for c in rgb) for rgb in window)
    by_colour = dict((tuple(v), k) for k, v in (colour_by_id or {}).items())
    out["window_px"] = int(window.shape[0])
    out["white_px"] = int(counts.pop(WHITE, 0))
    out["colours"] = [{"rgb": list(rgb), "px": n,
                       "element_id": by_colour.get(rgb)}
                      for rgb, n in counts.most_common(5)]
    return out


def _model_registration_marks(marks, context, probe_uv):
    """The same ticks in the MODEL capture, and the check only the model
    capture can give: its lattice is KNOWN (bounds_xy at its pixel count), so
    the marks' fit there is compared against an answer, not another fit."""
    model_sidecar = context.get("model_sidecar")
    model_tiff = context.get("model_tiff")
    if not model_sidecar or not model_tiff:
        return {"status": "unavailable", "reason": "no model capture located"}
    try:
        with open(model_sidecar, encoding="utf-8") as handle:
            model = json.load(handle)
    except (OSError, ValueError) as ex:
        return {"status": "unavailable",
                "reason": "model sidecar unreadable: {0}: {1}".format(
                    type(ex).__name__, ex)}
    colour = context.get("registration_mark_colour")
    if not colour:
        return {"status": "unavailable",
                "reason": "the combined report records no mark colour"}
    model_colours, unreadable_keys = {}, []
    for key, rgb in (model.get("color_assignment_map") or {}).items():
        try:
            model_colours[int(key)] = tuple(int(c) for c in rgb)
        except (TypeError, ValueError):
            unreadable_keys.append(str(key))
    fit = registration_mark_fit(model_tiff, marks,
                                shared_colour=tuple(int(c) for c in colour),
                                identify_by_id=model_colours)
    fit["model_colour_map_unreadable_keys"] = unreadable_keys
    fit["model_lines_visible"] = model.get("model_lines_visible")
    lattice = (model_lattice_mapping(model.get("bounds_xy"), fit.get("image_w"),
                                     fit.get("image_h"))
               if fit.get("image_w") else None)
    fit["lattice_mapping"] = lattice
    fit["vs_lattice"] = mapping_agreement(
        fit.get("mapping") if fit.get("status") == "value" else None,
        lattice, probe_uv)
    return fit


def compose_pixel_transform(from_mapping, to_mapping):
    """Pixel in one capture -> pixel in the other, through view UV. PURE.

    ``x_to = sx * x_from + ox`` (and y likewise): what post-processing applies
    to put the annotation capture onto the model lattice. Both maps must be
    ``position = a * uv + b``.
    """
    if not from_mapping or not to_mapping:
        return None
    sx = to_mapping["a_u"] / from_mapping["a_u"]
    sy = to_mapping["a_v"] / from_mapping["a_v"]
    return {"scale_x": sx, "offset_x": to_mapping["b_u"] - sx * from_mapping["b_u"],
            "scale_y": sy, "offset_y": to_mapping["b_v"] - sy * from_mapping["b_v"]}


def mapping_agreement(first, second, probe_uv):
    """How far two UV->pixel mappings disagree. Deltas only, no verdict.

    ``probe_uv`` is the rectangle whose corners are pushed through both -- the
    authored crop -- because a disagreement in scale is only meaningful at a
    place in the image. THE DISAGREEMENT IS THE NUMBER: where F1 and F2 are
    both available they must agree, and by how much they do not is what this
    reports.
    """
    if not first or not second:
        return {"status": "unavailable",
                "reason": "one of the two mappings is not available"}
    out = {"status": "value",
           "px_per_ft_u_delta": first["a_u"] - second["a_u"],
           "px_per_ft_v_delta": abs(first["a_v"]) - abs(second["a_v"]),
           "corners": []}
    if probe_uv:
        u0, v0, u1, v1 = (float(v) for v in probe_uv)
        worst = 0.0
        for u, v in ((u0, v1), (u1, v1), (u0, v0), (u1, v0)):
            dx = (first["a_u"] * u + first["b_u"]) - (second["a_u"] * u + second["b_u"])
            dy = (first["a_v"] * v + first["b_v"]) - (second["a_v"] * v + second["b_v"])
            out["corners"].append({"uv": [u, v], "dx_px": dx, "dy_px": dy})
            worst = max(worst, abs(dx), abs(dy))
        out["worst_corner_px"] = worst
    return out


def model_lattice_mapping(bounds_uv, image_w, image_h):
    """The MODEL capture's own UV -> pixel map, from its recorded crop.

    The model capture is the established foundation: it rendered
    ``bounds_xy`` (crop A) at its own pixel count. That is a PREMISE, and the
    report labels any figure derived through it as model-anchored.
    """
    if not bounds_uv or len(bounds_uv) != 4:
        return None
    x0, y0, x1, y1 = (float(v) for v in bounds_uv)
    if x1 <= x0 or y1 <= y0 or not image_w or not image_h:
        return None
    a_u = float(image_w) / (x1 - x0)
    a_v = -float(image_h) / (y1 - y0)
    return {"a_u": a_u, "b_u": -a_u * x0, "a_v": a_v, "b_v": -a_v * y1}


def pixel_rect_to_uv(pixel_bbox, mapping):
    """An inclusive pixel bbox -> the UV rectangle it covers, through a map."""
    x0, y0, x1, y1 = (float(v) for v in pixel_bbox)
    us = sorted(((x0 - mapping["b_u"]) / mapping["a_u"],
                 (x1 + 1.0 - mapping["b_u"]) / mapping["a_u"]))
    vs = sorted(((y0 - mapping["b_v"]) / mapping["a_v"],
                 (y1 + 1.0 - mapping["b_v"]) / mapping["a_v"]))
    return (us[0], vs[0], us[1], vs[1])


def datum_extents(record, blobs, mapping, view_scale):
    """Do datum extents in the capture match the drawing? Per datum.

    Each datum's INK extent (its palette colour's pixel bbox) is mapped into
    UV through ``mapping`` and compared, side by side, with the bbox recorded
    BEFORE the capture touched the view -- so against the authored crop. Under
    V0 the crop was widened to B, which lengthens datums; under V7/V8 it was
    not. Positive ``*_ft`` means the ink reaches FURTHER than the record.

    PREMISE, stated: ``get_BoundingBox(view)`` for a datum is taken as its
    drawn 2-D extent in this view. Unconfirmed; a datum whose record is its 3-D
    extent would read long on the axis the view looks along.
    """
    rows = []
    if not mapping:
        return {"status": "unavailable",
                "reason": "no mapping from pixels to UV", "datums": rows}
    for element_id, entry in record["bbox_entries"].items():
        if entry.get("membership_basis") != "datum_category":
            continue
        rect = entry.get("rect_uv")
        rgb = record["color_map"].get(element_id)
        blob = blobs.get(_pack(rgb)) if rgb is not None else None
        row = {"element_id": element_id, "category": entry.get("category"),
               "recorded_uv": list(rect) if rect else None}
        if rect is None or blob is None:
            row["status"] = "unmeasured"
            row["reason"] = ("no recorded bbox" if rect is None
                             else "its colour is not in the image")
            rows.append(row)
            continue
        ink = pixel_rect_to_uv(blob["pixel_bbox"], mapping)
        row["status"] = "value"
        row["ink_uv"] = list(ink)
        row["touches_border"] = blob["touches_border"]
        row["left_ft"] = rect[0] - ink[0]
        row["bottom_ft"] = rect[1] - ink[1]
        row["right_ft"] = ink[2] - rect[2]
        row["top_ft"] = ink[3] - rect[3]
        horizontal = (rect[2] - rect[0]) >= (rect[3] - rect[1])
        recorded_len = (rect[2] - rect[0]) if horizontal else (rect[3] - rect[1])
        ink_len = (ink[2] - ink[0]) if horizontal else (ink[3] - ink[1])
        row["long_axis"] = "u" if horizontal else "v"
        row["length_delta_ft"] = ink_len - recorded_len
        row["length_delta_paper_in"] = (ink_len - recorded_len) * 12.0 / float(
            view_scale or 1.0)
        rows.append(row)
    return {"status": "value", "datums": rows,
            "premise": "get_BoundingBox(view) of a datum is its drawn extent in "
                       "this view (UNCONFIRMED)"}


def count_offpalette_outside(path, palette_rgbs, excluded_pixel_rects):
    """Off-palette pixels OUTSIDE the given pixel rectangles.

    The model-ink residue question: under V7/V8 every model member is white, so
    what is neither white nor palette is model ink the suppression did not
    reach -- except the pixels this capture put there on purpose (the fiducials,
    excluded by colour in the palette list, and the crop boundary, excluded by
    rectangle here).
    """
    keys_sorted = np.array(sorted({_pack(rgb) for rgb in palette_rgbs}
                                  | {_pack(WHITE)}), dtype=np.int64)
    total = 0
    with Image.open(path) as handle:
        image = handle.convert("RGB")
        width, height = image.size
        for top in range(0, height, CHUNK_ROWS):
            bottom = min(height, top + CHUNK_ROWS)
            block = np.asarray(image.crop((0, top, width, bottom)), dtype=np.uint8)
            keys = ((block[:, :, 0].astype(np.int64) << 16)
                    | (block[:, :, 1].astype(np.int64) << 8)
                    | block[:, :, 2].astype(np.int64))
            idx = np.clip(np.searchsorted(keys_sorted, keys), 0, len(keys_sorted) - 1)
            off = keys_sorted[idx] != keys
            for x0, y0, x1, y1 in excluded_pixel_rects or []:
                r0, r1 = max(int(y0), top) - top, min(int(y1), bottom) - top
                if r1 > r0:
                    off[r0:r1, max(0, int(x0)):max(0, int(x1))] = False
            total += int(off.sum())
    return total


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
        # Two keys on purpose. "capture_faults" keeps the convenient
        # already-defaulted list; "capture_faults_raw" preserves ABSENT as None,
        # which is a different fact from an empty list and is what lets
        # _capture_faults_for report "UNKNOWN" instead of "none".
        "capture_faults": sidecar.get("capture_faults") or [],
        "capture_faults_raw": sidecar.get("capture_faults"),
        "tiff_path": sidecar.get("tiff_path"),
        "paint_failed_element_ids": set(
            int(v) for v in (sidecar.get("paint_failed_element_ids") or [])),
        "color_assignment_count": sidecar.get("color_assignment_count"),
        # Round 2 (revised). "frame_b" or "untouched"; None on a sidecar written
        # before the switch existed, which behaved as "frame_b".
        "crop_mode": registration.get("crop_mode"),
        # What the mode RESOLVED to ("frame_b" / "crop_a" / "none"). Written
        # from 2026-09-23 on; older sidecars leave it None and crop_mode alone
        # decides (crop_left_alone below).
        "crop_applied": registration.get("crop_applied"),
        "rendered_uv_reason": registration.get("rendered_uv_reason"),
        "probe_crop_boundary": sidecar.get("probe_crop_boundary"),
        "probe_fiducials": sidecar.get("probe_fiducials"),
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


def crop_left_alone(record) -> bool:
    """Did this capture leave the view's crop as found? PURE.

    "untouched" always does. "authored_else_crop_a" does only where the view's
    own crop was active (crop_applied "none"); on a crop-inactive view it
    writes crop A and records it as rendered_uv, like frame_b writes B.
    """
    mode = record.get("crop_mode")
    if mode == "untouched":
        return True
    return mode == "authored_else_crop_a" and record.get("crop_applied") == "none"


def frame_rect_for(record, authored_crop_uv=None) -> tuple[Any, str | None]:
    """The rectangle this annotation capture RENDERED, or why there is none.

    ``registration.rendered_uv`` and nothing else. It is null exactly when the
    frame-B crop could not be applied, in which case the export is FitToPage's
    automatic extent: there is no rectangle to measure against and the report
    says so rather than falling back to ``frame_snapped_uv``, which would be
    describing a rectangle that was not rendered.
    """
    rendered = record.get("rendered_uv")
    if (not rendered) and crop_left_alone(record):
        # ROUND 2 (REVISED). Nothing was handed to Revit, so the reference is
        # the AUTHORED crop the capture left alone -- the rectangle the
        # probe's combined report read from view.CropBox. The margins against
        # it say how far the render grew past the crop the drawing has.
        if authored_crop_uv and len(authored_crop_uv) == 4:
            rect = tuple(float(v) for v in authored_crop_uv)
            if rect[2] > rect[0] and rect[3] > rect[1]:
                return (rect, None)
        return (None, "crop_mode 'untouched' and no ACTIVE authored crop was "
                      "recorded, so there is no rectangle to measure margins "
                      "against; the scale is still fitted")
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
                    v0_model_tiff_sha=None, context=None):
    """The measurements for one view x variant capture.

    ``context`` carries what the probe's combined report knows and the sidecar
    does not: ``authored_crop_uv`` and ``authored_crop_active``,
    ``authored_shape`` (crop_loop_record), ``fiducials`` (the painted F2 pair),
    ``model_sidecar``/``model_tiff`` (the model capture to anchor F2 against)
    and ``achieved_fpp_ft`` (the lattice). All optional; a capture analysed
    without it gets the original six measurements.
    """
    context = context or {}
    record = read_annotation_sidecar(sidecar_path)
    result: dict[str, Any] = {
        "sidecar_path": str(sidecar_path),
        "tiff_path": str(tiff_path),
        "sidecar": record,
        "measurements": {},
    }
    measurements = result["measurements"]

    palette = sorted(set(record["color_map"].values()))
    fiducials = context.get("fiducials") or []
    fiducial_rgbs = [tuple(int(c) for c in f.get("rgb")) for f in fiducials
                     if f.get("rgb")]
    scan = scan_image(tiff_path, palette, fiducial_rgbs)
    image_w, image_h = scan["image_w"], scan["image_h"]
    authored_uv = (context.get("authored_crop_uv")
                   if context.get("authored_crop_active") else None)

    # A FAILED BBOX COLLECTION IS NOT A VIEW WITH NO ANNOTATIONS, and every
    # measurement below is built on that map. read_annotation_sidecar
    # deliberately empties bbox_entries when production reported the collection
    # unavailable and keeps the reason -- but nothing consumed that, so the
    # report said "0 matched", emitted a value-valued excursion over zero
    # rectangles and printed empty coverage: indistinguishable from a clean
    # capture that simply has no usable bboxes. Gated here, once, so all four
    # bbox-derived measurements carry the same reason rather than each
    # independently reading an empty dict as a fact. (PR #215 review, round 3.)
    bbox_unavailable = None
    if not record["bbox_status_ok"]:
        bbox_unavailable = {
            "status": "unavailable",
            "reason": "production reported the annotation bbox collection as "
                      "{0}. Every measurement below depends on that map, so none "
                      "is reported -- an empty map here is a FAILED collection, "
                      "not a view without annotations.".format(
                          record["bbox_status_reason"]),
        }

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
    frame_rect, frame_reason = frame_rect_for(record, authored_uv)
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

    if bbox_unavailable is not None:
        fit = dict(bbox_unavailable, sample_count=0)
        alt_fit = dict(bbox_unavailable)
    elif frame_rect is None and not crop_left_alone(record):
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
    fit["frame_source"] = (
        "registration.rendered_uv" if record.get("rendered_uv")
        else ("the AUTHORED crop (crop left as found)" if frame_rect else None))
    if frame_rect is None and frame_reason:
        fit["frame_reason"] = frame_reason
    measurements["registration"] = fit
    measurements["registration_ink_bbox_anchor"] = alt_fit
    measurements["anchor_agreement"] = _anchor_agreement(fit, alt_fit)

    # ---- (3) bbox excursion past the rendered frame, per side ---------
    if bbox_unavailable is not None:
        measurements["bbox_excursion"] = dict(bbox_unavailable)
    elif frame_rect is None:
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
    if bbox_unavailable is not None:
        measurements["coverage"] = dict(
            bbox_unavailable, ink_source=None,
            ink_unavailable_reason=bbox_unavailable["reason"],
            ink_threshold=INK_FRACTION_THRESHOLD, by_category={})
    else:
        measurements["coverage"] = _coverage_by_category(
            record, scan, measurements["registration"], tiff_path,
            image_w, image_h)
    measurements["bbox_collection"] = (
        dict(bbox_unavailable) if bbox_unavailable is not None
        else {"status": "value", "entry_count": len(record["bbox_entries"])})

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

    # ---- (7) F1: the crop boundary ------------------------------------
    crop_ids = set(int(v) for v in context.get("crop_element_ids") or [])
    crop_rgbs = [rgb for eid, rgb in record["color_map"].items() if eid in crop_ids]
    line_scan = scan_axis_lines(tiff_path, palette + fiducial_rgbs,
                                boundary_rgbs=crop_rgbs)
    boundary = recover_crop_boundary(
        line_scan, context.get("authored_shape"), context.get("authored_crop_uv"))
    boundary["probe_turned_it_on"] = bool(record.get("probe_crop_boundary"))
    if context.get("achieved_fpp_ft"):
        boundary["lattice_px_per_ft"] = 1.0 / float(context["achieved_fpp_ft"])
    measurements["crop_boundary"] = boundary

    # ---- (8) F2: the fiducial pair --------------------------------------
    if fiducials:
        f2 = fiducial_fit(fiducials, scan["blobs"], image_w, image_h)
        f2["model_anchored"] = _model_anchored_fiducials(
            fiducials, scan["blobs"], image_w, image_h, context)
    else:
        f2 = {"status": "not_applicable",
              "reason": "this capture has no F2 fiducials (only V8 paints them)"}
    if context.get("achieved_fpp_ft"):
        f2["lattice_px_per_ft"] = 1.0 / float(context["achieved_fpp_ft"])
    measurements["fiducials"] = f2

    probe_rect = context.get("authored_crop_uv") or (
        list(frame_rect) if frame_rect else None)

    # ---- (12) F3: the registration marks, in BOTH captures ---------------
    marks = context.get("registration_marks") or []
    if marks:
        f3 = registration_mark_fit(tiff_path, marks,
                                   colour_by_id=record["color_map"])
        f3["model"] = _model_registration_marks(marks, context, probe_rect)
        model_marks = f3["model"]
        anno_map = f3.get("mapping") if f3.get("status") == "value" else None
        f3["annotation_to_model_px"] = {
            # Through the marks as found in the model capture: both ends
            # measured, nothing assumed about either lattice.
            "via_model_marks": compose_pixel_transform(
                anno_map, model_marks.get("mapping")
                if model_marks.get("status") == "value" else None),
            # Through the model capture's RECORDED lattice: what production's
            # decode assumes about the model image.
            "via_model_lattice": compose_pixel_transform(
                anno_map, model_marks.get("lattice_mapping")),
        }
    else:
        f3 = {"status": "not_applicable",
              "reason": "this capture has no registration marks (only V9 draws them)"}
    if context.get("achieved_fpp_ft"):
        f3["lattice_px_per_ft"] = 1.0 / float(context["achieved_fpp_ft"])
    measurements["registration_marks"] = f3

    # ---- (9) F1 vs F2 vs F3 vs the bbox fit -----------------------------
    maps = {
        "F1": boundary.get("mapping") if boundary.get("status") == "value" else None,
        "F2": f2.get("mapping") if f2.get("status") == "value" else None,
        "F3": f3.get("mapping") if f3.get("status") == "value" else None,
        "bbox_fit": fit.get("mapping") if fit.get("status") == "value" else None,
    }
    measurements["mapping_agreement"] = {
        "probe_uv": probe_rect,
        "F1_vs_F2": mapping_agreement(maps["F1"], maps["F2"], probe_rect),
        "F1_vs_bbox_fit": mapping_agreement(maps["F1"], maps["bbox_fit"], probe_rect),
        "F2_vs_bbox_fit": mapping_agreement(maps["F2"], maps["bbox_fit"], probe_rect),
        "F3_vs_F2": mapping_agreement(maps["F3"], maps["F2"], probe_rect),
        "F3_vs_bbox_fit": mapping_agreement(maps["F3"], maps["bbox_fit"], probe_rect),
    }

    # ---- (10) datum extents, through the best available map -------------
    # The marks first: their UV is what the probe DREW, not a bbox or a
    # boundary Revit may clip.
    chosen = next(((name, maps[name]) for name in ("F3", "F1", "F2", "bbox_fit")
                   if maps[name]), (None, None))
    if bbox_unavailable is not None:
        datums = dict(bbox_unavailable, datums=[])
    else:
        datums = datum_extents(record, scan["blobs"], chosen[1],
                               record.get("view_scale") or 1.0)
    datums["mapping_used"] = chosen[0]
    measurements["datum_extents"] = datums

    # ---- (11) model-ink residue -----------------------------------------
    excluded = (boundary.get("boundary_px_rect") or []) if boundary.get(
        "status") == "value" else []
    residue_pixels = (count_offpalette_outside(tiff_path, palette + fiducial_rgbs,
                                               excluded)
                      if excluded else scan["offpalette_pixels"])
    measurements["model_ink_residue"] = {
        "offpalette_pixels": scan["offpalette_pixels"],
        "fiducial_pixels": scan["fiducial_pixels"],
        "boundary_rects_excluded": len(excluded),
        "offpalette_outside_boundary": residue_pixels,
        "suppression_mode": record.get("model_suppression_mode"),
        "category_layer": context.get("category_layer"),
        "note": "off-palette pixels that are neither the fiducials nor inside a "
                "recovered crop-boundary band. Under membership suppression "
                "every model member is white, so these are candidate model ink "
                "the suppression did not reach -- or annotation the pass could "
                "not paint. They are counted, not attributed.",
    }

    # ---- (6) model TIFF hash -----------------------------------------
    measurements["model_tiff"] = {
        "sha256": model_tiff_sha,
        "v0_sha256": v0_model_tiff_sha,
        "matches_v0": (None if not (model_tiff_sha and v0_model_tiff_sha)
                       else model_tiff_sha == v0_model_tiff_sha),
    }
    return result


def _model_anchored_fiducials(fiducials, blobs, image_w, image_h, context):
    """F2 again, with each fiducial's UV extent taken from the MODEL capture's
    ink instead of its projected AABB.

    A projected 3-D bounding box can be looser than the element it bounds, so
    the plain F2 fit rests on "the ink spans the recorded box". The model
    capture paints the same element flat in its own palette colour, on a
    lattice whose UV mapping is recorded -- so its ink, mapped through that
    lattice, is the element's DRAWN extent. Model-anchored: it inherits the
    premise that the model capture registers.
    """
    model_sidecar = context.get("model_sidecar")
    model_tiff = context.get("model_tiff")
    if not model_sidecar or not model_tiff:
        return {"status": "unavailable", "reason": "no model capture located"}
    try:
        with open(model_sidecar, encoding="utf-8") as handle:
            model = json.load(handle)
    except (OSError, ValueError) as ex:
        return {"status": "unavailable",
                "reason": "model sidecar unreadable: {0}: {1}".format(
                    type(ex).__name__, ex)}
    colour_map = model.get("color_assignment_map") or {}
    wanted = {}
    for fiducial in fiducials:
        rgb = colour_map.get(str(fiducial.get("id")))
        if rgb is not None:
            wanted[int(fiducial["id"])] = tuple(int(c) for c in rgb)
    if len(wanted) < len(fiducials):
        return {"status": "unavailable",
                "reason": "{0} of {1} fiducial(s) have no colour in the model "
                          "capture".format(len(fiducials) - len(wanted),
                                           len(fiducials))}
    model_scan = scan_image(model_tiff, sorted(set(wanted.values())))
    lattice = model_lattice_mapping(model.get("bounds_xy"), model_scan["image_w"],
                                    model_scan["image_h"])
    if lattice is None:
        return {"status": "unavailable",
                "reason": "the model sidecar records no usable bounds_xy"}
    anchored = []
    ink_ratio = []
    for fiducial in fiducials:
        blob = model_scan["blobs"].get(_pack(wanted[int(fiducial["id"])]))
        anno_blob = blobs.get(_pack(tuple(int(c) for c in fiducial.get("rgb") or ())))
        # THE PREMISE, measured: the same element must draw the same shape in
        # both captures. Round 2's plan wall drew 463 px in the annotation
        # capture (cut region lost to the white category override) and far more
        # in the model one, and the anchored fit came back at u/v 0.63 with a
        # zero residual -- two points per edge, nothing to disagree with.
        ink_ratio.append({
            "id": fiducial.get("id"),
            "annotation_px": anno_blob["pixel_count"] if anno_blob else None,
            "model_px": blob["pixel_count"] if blob else None,
        })
        entry = dict(fiducial)
        entry["rect_uv"] = (list(pixel_rect_to_uv(blob["pixel_bbox"], lattice))
                            if blob is not None and not blob["touches_border"]
                            else None)
        anchored.append(entry)
    out = fiducial_fit(anchored, blobs, image_w, image_h)
    out["model_drawn_uv"] = [a["rect_uv"] for a in anchored]
    out["ink_pixels_annotation_vs_model"] = ink_ratio
    out["premise"] = "the model capture registers on its recorded bounds_xy"
    return out


def analyze_model_boundary(model_sidecar, model_tiff, crop_element_ids=()):
    """F1 in the MODEL half of V8: is the boundary there too, and where?

    The model pass sets the crop to its snapped crop A for its own export, so
    the boundary draws at A -- the sidecar's ``bounds_xy`` -- and the model
    capture's lattice says exactly where A is in pixels. Comparing the
    recovered boundary's mapping with that lattice is the ONE place the
    boundary can be checked against a known answer rather than against another
    fit. On a capture that renders exactly A the boundary sits on the image
    edge and may be clipped, which the band record shows.
    """
    try:
        with open(model_sidecar, encoding="utf-8") as handle:
            model = json.load(handle)
    except (OSError, ValueError) as ex:
        return {"status": "unavailable",
                "reason": "model sidecar unreadable: {0}: {1}".format(
                    type(ex).__name__, ex)}
    palette = sorted(set(tuple(int(c) for c in rgb)
                         for rgb in (model.get("color_assignment_map") or {}).values()))
    bounds = model.get("bounds_xy")
    # The model pass paints every model member it collects -- the crop-region
    # element included, if it is one -- so the boundary can be a PALETTE colour
    # here. Round 2's elevation model capture found no closing rectangle.
    colour_map = model.get("color_assignment_map") or {}
    crop_rgbs = [tuple(int(c) for c in colour_map[str(eid)])
                 for eid in crop_element_ids or () if str(eid) in colour_map]
    line_scan = scan_axis_lines(model_tiff, palette, boundary_rgbs=crop_rgbs)
    out = recover_crop_boundary(line_scan, None, bounds)
    out["drawn_at_uv"] = bounds
    out["crop_element_colours"] = [list(rgb) for rgb in crop_rgbs]
    out["probe_crop_boundary"] = model.get("probe_crop_boundary")
    lattice = model_lattice_mapping(bounds, line_scan["image_w"], line_scan["image_h"])
    out["lattice_mapping"] = lattice
    out["vs_lattice"] = mapping_agreement(out.get("mapping"), lattice, bounds)
    return out


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
    # A FRAMELESS fit (crop untouched on a crop-INACTIVE view) measures the
    # scale and withholds margins -- there is no rectangle to have a margin
    # from. The scale deltas still stand; the margin deltas are named absent,
    # never computed from a None (Plan_CropInActive, probe_0923_1239).
    if primary.get("margin_ft") is None or secondary.get("margin_ft") is None:
        out["margin_ft_delta"] = None
        out["margin_reason"] = ("no margins: at least one fit is frameless "
                                "(no rendered rectangle to measure them from)")
        return out
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
                        " or the ".join(name for name, found in
                                        (("sidecar", sidecar), ("TIFF", tiff))
                                        if found is None)),
                    "recorded_sidecar": anno.get("sidecar_path"),
                    "recorded_tiff": anno.get("tiff_path"),
                    "searched_under": str(probe_dir / str(variant)),
                })
                continue
            own = variant_report.get("own_model_pass") or {}
            own_sidecar = _locate(own.get("sidecar_path"), probe_dir,
                                  "{0}/model".format(variant))
            own_tiff = _locate(own.get("tiff_path"), probe_dir,
                               "{0}/model".format(variant))
            entry["captures"].append({"variant": variant, "sidecar": sidecar,
                                      "tiff": tiff,
                                      "variant_report": variant_report,
                                      "own_model_sidecar": own_sidecar,
                                      "own_model_tiff": own_tiff})
        model = combined.get("model_pass") or {}
        entry["model_tiff"] = _locate(model.get("tiff_path"), probe_dir, "model")
        entry["model_sidecar"] = _locate(model.get("sidecar_path"), probe_dir, "model")
        runs.append(entry)
    return runs


def capture_context(run, capture):
    """What the combined report knows about one capture that its sidecar does
    not: the authored crop, its shape, the fiducials, and the model capture to
    anchor F2 against (V8's own when it has one, else the shared one)."""
    combined = run.get("combined") or {}
    authored = combined.get("authored_crop") or {}
    variant_report = capture.get("variant_report") or {}
    geometry = (combined.get("model_pass") or {}).get("geometry") or {}
    return {
        "authored_crop_uv": _gs_value(authored.get("crop_box_uv")),
        "authored_crop_active": _gs_value(authored.get("crop_box_active")),
        "authored_shape": _gs_value(authored.get("shape")),
        "fiducials": ((variant_report.get("pre_state") or {}).get("fiducials")
                      or {}).get("painted") or [],
        "registration_marks": ((variant_report.get("pre_state") or {}).get(
            "registration_marks") or {}).get("created") or [],
        "registration_mark_colour": ((variant_report.get("pre_state") or {}).get(
            "registration_marks") or {}).get("colour"),
        "category_layer": ((variant_report.get("pre_state") or {}).get(
            "white_membership_suppression") or {}).get("category_layer"),
        "model_sidecar": capture.get("own_model_sidecar") or run.get("model_sidecar"),
        "model_tiff": capture.get("own_model_tiff") or run.get("model_tiff"),
        "achieved_fpp_ft": geometry.get("achieved_fpp_ft"),
        "crop_element_ids": (combined.get("crop_region_elements") or {}).get(
            "crop_element_ids") or [],
    }


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


def _render_crop_context(combined):
    """The view-level facts round 2 (revised) turns on: where the crop is, how
    far each pass moves it, what the white suppression costs, which fiducials
    were chosen. Printed once per view, before any capture's numbers."""
    lines = []
    relationships = combined.get("crop_relationships")
    if not relationships:
        return lines
    lines.append("")
    lines.append("**The crop.** Authored crop active: {0}; UV {1}.".format(
        _fmt(relationships.get("authored_crop_active")),
        relationships.get("authored_crop_uv")))

    def _sides(delta):
        if not delta:
            return "--"
        return " / ".join("{0} {1}".format(side, _fmt(delta[side], "{0:+.2f}"))
                          for side in ("left", "right", "top", "bottom"))

    lines.append("- V0 widens it to frame B by (ft): {0}".format(
        _sides(relationships.get("v0_widens_crop_by_ft"))))
    lines.append("- the MODEL pass sets it to crop A, which differs from the "
                 "authored crop by (ft): {0}{1}".format(
                     _sides(relationships.get("model_pass_crop_a_minus_authored_ft")),
                     " -- and on this crop-INACTIVE view the model pass ACTIVATES "
                     "a crop" if relationships.get("model_pass_activates_a_crop")
                     else ""))
    shape = _gs_value((combined.get("authored_crop") or {}).get("shape"))
    if shape:
        lines.append("- crop shape: {0} loop(s), {1} edge(s), rectangle: {2}, "
                     "oblique edges: {3}".format(
                         shape.get("loop_count"), shape.get("edge_count"),
                         _fmt(shape.get("is_rectangle")),
                         shape.get("oblique_edge_count")))
    else:
        reason = ((combined.get("authored_crop") or {}).get("shape") or {}).get(
            "reason")
        lines.append("- crop shape: UNAVAILABLE ({0}); F1 assumes a rectangle "
                     "from view.CropBox and says so".format(reason))
    cost = combined.get("suppression_cost") or {}
    if cost.get("suppression_ms") is not None:
        lines.append("- **white-suppression cost**: {0} ms for {1} element "
                     "override(s), against {2} ms for the whole model pass "
                     "(ratio {3}; a LOWER bound on suppression-vs-paint, since "
                     "production does not time its paint alone)".format(
                         _fmt(cost.get("suppression_ms"), "{0:.0f}"),
                         cost.get("element_override_count"),
                         _fmt(cost.get("model_pass_total_ms"), "{0:.0f}"),
                         _fmt(cost.get("ratio_to_model_pass_total"), "{0:.2f}")))
    by_mechanism = cost.get("by_mechanism_ms") or {}
    if by_mechanism:
        calls = cost.get("api_call_counts") or {}
        lines.append("  - by mechanism (ms): " + ", ".join(
            "{0} {1}".format(key.replace("_ms", ""), _fmt(value, "{0:.0f}"))
            for key, value in sorted(by_mechanism.items(), key=lambda kv: -kv[1])))
        lines.append("  - API calls: " + ", ".join(
            "{0} {1}".format(key, value) for key, value in sorted(calls.items())))
    for entry in combined.get("variants") or []:
        commit_ms = (entry.get("transaction_group") or {}).get("pre_state_commit_ms")
        restore = entry.get("restore") or {}
        own_cost = (entry.get("pre_state") or {}).get("suppression_cost") or {}
        if own_cost.get("suppression_ms") is not None:
            layer = ((entry.get("pre_state") or {}).get(
                "white_membership_suppression") or {}).get("category_layer")
            lines.append("  - `{0}` suppression{1}: {2} ms ({3})".format(
                entry.get("variant"),
                "" if layer is not False else " WITHOUT the category layer",
                _fmt(own_cost.get("suppression_ms"), "{0:.0f}"),
                ", ".join("{0} {1}".format(k.replace("_ms", ""),
                                           _fmt(v, "{0:.0f}"))
                          for k, v in sorted(
                              (own_cost.get("by_mechanism_ms") or {}).items(),
                              key=lambda kv: -kv[1]) if v)))
        if restore.get("mode") == "transaction_group_rollback":
            lines.append("  - `{0}`: pre-state commit {1} ms; restore = group "
                         "rollback, {2} ms; post-rollback element-override "
                         "read-back {3} ms ({4})".format(
                             entry.get("variant"), _fmt(commit_ms, "{0:.0f}"),
                             _fmt(restore.get("rollback_ms"), "{0:.0f}"),
                             _fmt((restore.get("element_overrides_after_rollback")
                                   or {}).get("elapsed_ms"), "{0:.0f}"),
                             (restore.get("element_overrides_after_rollback")
                              or {}).get("status", "--")))
            continue
        if commit_ms is None and not restore.get("element_blank_writes_ms"):
            continue
        lines.append("  - `{0}`: pre-state commit {1} ms; restore blank writes {2} "
                     "ms, category/link reverse {3} ms, restore commit {4} ms".format(
                         entry.get("variant"), _fmt(commit_ms, "{0:.0f}"),
                         _fmt(restore.get("element_blank_writes_ms"), "{0:.0f}"),
                         _fmt(restore.get("reverse_category_and_link_ms"), "{0:.0f}"),
                         _fmt(restore.get("commit_ms"), "{0:.0f}")))
    choice = combined.get("fiducial_choice") or {}
    if choice.get("state") == "value":
        lines.append("- F2 fiducials: {0}; separated {1} ft on u and {2} ft on v "
                     "({3} of {4} candidates kept, reference {5})".format(
                         ", ".join("{0} ({1})".format(c.get("id"), c.get("category"))
                                   for c in choice.get("pair") or []),
                         _fmt(choice.get("separation_u_ft"), "{0:.1f}"),
                         _fmt(choice.get("separation_v_ft"), "{0:.1f}"),
                         choice.get("kept_count"), choice.get("candidate_count"),
                         choice.get("reference_source")))
    elif choice:
        lines.append("- F2 fiducials: none chosen -- {0}".format(choice.get("reason")))
    return lines


def _render_mapping_row(name, record):
    if not record or record.get("status") != "value":
        return [name, "--", "--", "--", "--",
                (record or {}).get("reason") or (record or {}).get("status") or "--"]
    residual = record.get("residual_max_px") or {}
    if isinstance(residual, dict):
        residual_text = "{0} / {1}".format(_fmt(residual.get("u"), "{0:.2f}"),
                                           _fmt(residual.get("v"), "{0:.2f}"))
    else:
        residual_text = _fmt(residual, "{0:.2f}")
    return [name, _fmt(record.get("px_per_ft_u"), "{0:.4f}"),
            _fmt(record.get("px_per_ft_v"), "{0:.4f}"),
            _fmt(record.get("isotropy_u_over_v"), "{0:.5f}"),
            residual_text, ""]


def _render_f1_f2(analyses):
    lines = []
    # ---- (7) F1 ------------------------------------------------------
    lines.append("### 7. F1 -- the crop boundary, recovered from the pixels")
    lines.append("")
    lines.append("Q1: does the exported image contain the crop boundary, and does "
                 "its recovered position match `view.CropBox`? A boundary is a "
                 "band of rows (or columns) each holding a straight run of "
                 "non-white, non-palette, non-fiducial pixels of >= {0:.0%} of "
                 "the image, closing into one rectangle (or matching the crop "
                 "shape's levels). `NOT FOUND` on a V8 row is the answer that "
                 "ImageExportOptions does not draw it.".format(
                     BOUNDARY_MIN_RUN_FRACTION))
    lines.append("")
    rows = []
    for item in analyses:
        boundary = item["analysis"]["measurements"].get("crop_boundary") or {}
        rects = boundary.get("boundary_px_rect") or []
        rows.append([
            item["variant"], _fmt(boundary.get("probe_turned_it_on")),
            (boundary.get("status") or "--").upper(),
            "{0} / {1}".format(boundary.get("row_band_count", "--"),
                               boundary.get("col_band_count", "--")),
            _fmt(boundary.get("px_per_ft_u"), "{0:.4f}"),
            _fmt(boundary.get("px_per_ft_v"), "{0:.4f}"),
            _fmt(boundary.get("lattice_px_per_ft"), "{0:.4f}"),
            _fmt(boundary.get("isotropy_u_over_v"), "{0:.5f}"),
            "; ".join(str(r) for r in rects) or "--",
            boundary.get("reason") or ""])
    lines.extend(_table(["variant", "probe turned it on", "boundary",
                         "row/col bands", "px/ft u", "px/ft v", "lattice px/ft",
                         "u/v isotropy", "boundary px rects (subtract these)", ""],
                        rows))
    lines.append("")
    lines.append("`isotropy` of 1 means the boundary's pixel rectangle has the "
                 "crop's own aspect -- the recovered position is the crop's "
                 "shape at one uniform scale. `lattice px/ft` is the model "
                 "capture's (1 / achieved fpp): an untouched capture that "
                 "rendered exactly the authored crop at the requested count would "
                 "match it.")
    lines.append("")
    for item in analyses:
        model_boundary = item["analysis"]["measurements"].get("model_crop_boundary")
        if not model_boundary:
            continue
        agreement = model_boundary.get("vs_lattice") or {}
        lines.append("- `{0}` MODEL capture (boundary drawn at crop A {1}): "
                     "{2}{3}".format(
                         item["variant"], model_boundary.get("drawn_at_uv"),
                         (model_boundary.get("status") or "--").upper(),
                         "; against the model lattice: px/ft u {0}, v {1}, worst "
                         "corner {2} px".format(
                             _fmt(agreement.get("px_per_ft_u_delta"), "{0:+.4f}"),
                             _fmt(agreement.get("px_per_ft_v_delta"), "{0:+.4f}"),
                             _fmt(agreement.get("worst_corner_px"), "{0:.2f}"))
                         if agreement.get("status") == "value"
                         else " -- {0}".format(model_boundary.get("reason")
                                               or agreement.get("reason"))))
    lines.append("")

    # ---- (8) F2 ------------------------------------------------------
    lines.append("### 8. F2 -- the fiducial pair")
    lines.append("")
    rows = []
    for item in analyses:
        f2 = item["analysis"]["measurements"].get("fiducials") or {}
        if f2.get("status") == "not_applicable":
            continue
        for fiducial in f2.get("fiducials") or []:
            rows.append([item["variant"], fiducial.get("id"),
                         str(fiducial.get("rgb")), _fmt(fiducial.get("found")),
                         fiducial.get("pixel_count", "--"),
                         str(fiducial.get("pixel_bbox", "--")),
                         _fmt(fiducial.get("touches_border")),
                         str(fiducial.get("rect_uv"))])
    lines.extend(_table(["variant", "element", "colour", "found", "pixels",
                         "ink px bbox", "clipped", "recorded rect_uv"],
                        rows or [["--"] * 8]))
    lines.append("")
    rows = []
    for item in analyses:
        f2 = item["analysis"]["measurements"].get("fiducials") or {}
        if f2.get("status") == "not_applicable":
            continue
        rows.append(_render_mapping_row(
            "`{0}` F2 (recorded bbox)".format(item["variant"]), f2))
        rows.append(_render_mapping_row(
            "`{0}` F2 (model-anchored)".format(item["variant"]),
            f2.get("model_anchored")))
    lines.extend(_table(["fit", "px/ft u", "px/ft v", "u/v isotropy",
                         "residual max px u / v", ""], rows or [["--"] * 6]))
    lines.append("")
    for item in analyses:
        anchored = ((item["analysis"]["measurements"].get("fiducials") or {})
                    .get("model_anchored") or {})
        for entry in anchored.get("ink_pixels_annotation_vs_model") or []:
            lines.append("- `{0}` fiducial {1}: {2} px in the annotation capture, {3} "
                         "px in the model capture{4}".format(
                             item["variant"], entry.get("id"),
                             entry.get("annotation_px"), entry.get("model_px"),
                             " -- the element drew NOTHING in the model capture in its "
                             "model colour, so it has no model-drawn extent"
                             if entry.get("model_px") is None
                             else " -- the element did NOT draw the same shape in both, "
                             "so the model-anchored row does not hold"
                             if (entry.get("annotation_px") and entry.get("model_px")
                                 and abs(entry["annotation_px"] - entry["model_px"])
                                 > 0.5 * max(entry["annotation_px"], entry["model_px"]))
                             else ""))
    lines.append("")
    lines.append("F2 fits each fiducial's INK EDGES against its recorded extent: "
                 "four points per axis for the pair, so it has a residual. The "
                 "recorded extent is a projected 3-D bbox, which can be looser "
                 "than the element; the model-anchored row replaces it with the "
                 "element's drawn extent in the model capture, at the cost of "
                 "assuming the model capture registers.")
    lines.append("")

    # ---- (9) F1 vs F2 ------------------------------------------------
    lines.append("### 9. F1 vs F2 vs F3 vs the bbox fit")
    lines.append("")
    lines.append("Q2: with the crop untouched, is the rendered rectangle stable, "
                 "and do F1 and F2 agree? The disagreement is the number. "
                 "Corners are the authored crop's, pushed through both maps.")
    lines.append("")
    rows = []
    for item in analyses:
        agreement = item["analysis"]["measurements"].get("mapping_agreement") or {}
        for key in ("F1_vs_F2", "F1_vs_bbox_fit", "F2_vs_bbox_fit",
                    "F3_vs_F2", "F3_vs_bbox_fit"):
            entry = agreement.get(key)
            if entry is None:
                continue
            if (key.startswith("F3") and entry.get("status") != "value"
                    and (item["analysis"]["measurements"].get(
                        "registration_marks") or {}).get("status") == "not_applicable"):
                continue
            if entry.get("status") != "value":
                rows.append([item["variant"], key, "--", "--", "--",
                             entry.get("reason") or "--"])
                continue
            rows.append([item["variant"], key,
                         _fmt(entry.get("px_per_ft_u_delta"), "{0:+.4f}"),
                         _fmt(entry.get("px_per_ft_v_delta"), "{0:+.4f}"),
                         _fmt(entry.get("worst_corner_px"), "{0:.2f}"), ""])
    lines.extend(_table(["variant", "pair", "px/ft u delta", "px/ft v delta",
                         "worst corner px", ""], rows))
    lines.append("")

    # ---- (10) datums -------------------------------------------------
    lines.append("### 10. Datum extents: ink vs the recorded (authored-crop) bbox")
    lines.append("")
    lines.append("Q3: do datum extents in the capture match the drawing? Positive "
                 "means the ink reaches FURTHER than the bbox recorded before the "
                 "capture touched the view. Under V0 the crop was widened to B, "
                 "which lengthens datums; under V7/V8 it was not.")
    lines.append("")
    rows = []
    for item in analyses:
        datums = item["analysis"]["measurements"].get("datum_extents") or {}
        if datums.get("status") != "value":
            rows.append([item["variant"], "--", "--", "--", "--", "--", "--",
                         datums.get("reason") or "--"])
            continue
        for datum in datums.get("datums") or []:
            if datum.get("status") != "value":
                rows.append([item["variant"], datum.get("element_id"),
                             datum.get("category"), "--", "--", "--", "--",
                             datum.get("reason")])
                continue
            rows.append([
                item["variant"], datum.get("element_id"), datum.get("category"),
                datums.get("mapping_used"),
                _fmt(datum.get("length_delta_ft"), "{0:+.2f}"),
                _fmt(datum.get("length_delta_paper_in"), "{0:+.3f}"),
                " / ".join(_fmt(datum.get(side + "_ft"), "{0:+.2f}")
                           for side in ("left", "right", "top", "bottom")),
                "CLIPPED at image edge" if datum.get("touches_border") else ""])
    lines.extend(_table(["variant", "datum", "category", "map", "length delta ft",
                         "paper in", "per side ft l/r/t/b", ""],
                        rows or [["--"] * 8]))
    lines.append("")
    lines.append("Premise: `get_BoundingBox(view)` of a datum is its drawn extent in "
                 "the view (UNCONFIRMED).")
    lines.append("")

    # ---- (11) residue ------------------------------------------------
    lines.append("### 11. Model-ink residue")
    lines.append("")
    lines.append("Q4: does membership suppression with subcategories leave any "
                 "model ink? Off-palette pixels outside the fiducials and outside "
                 "any recovered boundary band. Counted, not attributed: an "
                 "annotation the pass could not paint lands here too.")
    lines.append("")
    rows = []
    for item in analyses:
        residue = item["analysis"]["measurements"].get("model_ink_residue") or {}
        rows.append([item["variant"], _fmt(residue.get("suppression_mode")),
                     _fmt(residue.get("category_layer")),
                     residue.get("offpalette_pixels", "--"),
                     residue.get("fiducial_pixels", "--"),
                     residue.get("boundary_rects_excluded", "--"),
                     residue.get("offpalette_outside_boundary", "--")])
    lines.extend(_table(["variant", "suppression", "category layer", "off-palette",
                         "fiducial px", "boundary bands excluded",
                         "off-palette outside boundary"], rows))
    lines.append("")
    lines.append("`category layer` is mechanism 3 (category and subcategory white "
                 "overrides). V10 runs without it: the V7 vs V10 difference in this "
                 "table is what that layer removes, and the cost section above is "
                 "what it costs.")
    lines.append("")
    lines.extend(_render_registration_marks(analyses))
    return lines


def _render_registration_marks(analyses):
    """Section 12: F3, the probe's own ticks, in both captures."""
    items = [item for item in analyses
             if (item["analysis"]["measurements"].get("registration_marks") or {})
             .get("status") != "not_applicable"]
    if not items:
        return []
    lines = ["### 12. F3 -- registration marks, in BOTH captures", "",
             "Detail-line ticks the probe drew at KNOWN view UV, inset inside "
             "the crop, then removed by rolling back. Horizontal ticks' centre "
             "rows give v, vertical ticks' centre columns give u. Twelve ticks "
             "give six points per axis at three levels, so the residual measures "
             "disagreement between levels (a round-3 capture had eight ticks at "
             "two levels, whose residual is 0 by construction). The model row "
             "is checked against the "
             "model capture's RECORDED lattice -- the one place this method meets "
             "a known answer. The endpoint fit uses tick ends (caps, "
             "anti-aliasing) and is shown beside the centre-line fit, never in "
             "its place.", ""]
    rows = []
    for item in items:
        f3 = item["analysis"]["measurements"]["registration_marks"]
        for label, record in (("annotation", f3), ("model", f3.get("model") or {})):
            residual = record.get("residual_max_px") or {}
            ends = record.get("endpoint_fit") or {}
            vs = record.get("vs_lattice") or {}
            rows.append([
                item["variant"], label,
                "{0}/{1}".format(record.get("found_count", "--"),
                                 record.get("expected_count", "--")),
                _fmt(record.get("px_per_ft_u"), "{0:.4f}"),
                _fmt(record.get("px_per_ft_v"), "{0:.4f}"),
                _fmt(record.get("isotropy_u_over_v"), "{0:.5f}"),
                "{0} / {1}".format(_fmt(residual.get("u"), "{0:.2f}"),
                                   _fmt(residual.get("v"), "{0:.2f}")),
                "{0} / {1}".format(_fmt(ends.get("px_per_ft_u"), "{0:.4f}"),
                                   _fmt(ends.get("px_per_ft_v"), "{0:.4f}")),
                (_fmt(vs.get("worst_corner_px"), "{0:.2f}")
                 if vs.get("status") == "value" else "--"),
                record.get("reason") or ""])
    lines.extend(_table(["variant", "capture", "ticks", "px/ft u", "px/ft v",
                         "u/v isotropy", "residual max px u / v",
                         "endpoint fit px/ft u / v", "vs model lattice worst px",
                         ""], rows))
    lines.append("")
    for item in items:
        f3 = item["analysis"]["measurements"]["registration_marks"]
        lattice = f3.get("lattice_px_per_ft")
        if lattice:
            lines.append("- `{0}` model lattice: {1} px/ft".format(
                item["variant"], _fmt(lattice, "{0:.4f}")))
        for name, transform in sorted((f3.get("annotation_to_model_px") or {}).items()):
            if not transform:
                lines.append("- `{0}` annotation -> model pixels {1}: unavailable".format(
                    item["variant"], name))
                continue
            lines.append("- `{0}` annotation -> model pixels {1}: x' = {2} x {3:+.2f}, "
                         "y' = {4} y {5:+.2f}".format(
                             item["variant"], name,
                             _fmt(transform["scale_x"], "{0:.6f}"),
                             transform["offset_x"],
                             _fmt(transform["scale_y"], "{0:.6f}"),
                             transform["offset_y"]))
        for label, record in (("annotation", f3), ("model", f3.get("model") or {})):
            for miss in record.get("missing") or []:
                lines.append("- `{0}` {1}: tick {2} not found -- {3}".format(
                    item["variant"], label, miss.get("key"), miss.get("reason")))
            for diag in record.get("missing_diagnosis") or []:
                lines.append(
                    "  - where {0} should be (px {1}): {2} of {3} px white; "
                    "other colours: {4}".format(
                        diag["key"], diag.get("expected_px_rect"),
                        diag.get("white_px", "--"), diag.get("window_px", "--"),
                        ", ".join("{0} x{1}{2}".format(
                            c["rgb"], c["px"],
                            " (element {0})".format(c["element_id"])
                            if c.get("element_id") is not None else "")
                            for c in diag.get("colours") or []) or "none"))
            stats = record.get("components") or {}
            if stats.get("merged_into_one_tick") or stats.get("unassigned_components"):
                lines.append("- `{0}` {1}: {2} component(s), {3} merged into a tick "
                             "already found, unassigned: {4}".format(
                                 item["variant"], label, stats.get("components"),
                                 stats.get("merged_into_one_tick"),
                                 stats.get("unassigned_components")))
            if record.get("mark_pixels"):
                lines.append("- `{0}` {1}: {2} mark pixels in {3} rect(s) to subtract "
                             "in post".format(item["variant"], label,
                                              record["mark_pixels"],
                                              len(record.get("mark_pixel_rects") or [])))
    lines.append("")
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
    lines.extend(_render_crop_context(combined))
    for missing in run.get("missing", []):
        lines.append("")
        lines.append("- `{0}`: NO CAPTURE ANALYSED. {1}".format(
            missing.get("variant"), missing.get("reason")))
    if not analyses:
        # NOTHING TO TABULATE IS NOT AN EMPTY TABLE. A heading over zero rows
        # reads as "measured, nothing found"; it is "not measured", and the
        # lines above say why for each variant.
        lines.append("")
        lines.append("**No capture of this view could be analysed**, so none of "
                     "the per-capture measurements (sections 0-11) is reported. "
                     "Each needs an annotation sidecar AND its TIFF.")
        lines.append("")
        return lines
    # A FAILED BBOX COLLECTION, stated where a reader cannot miss it. Sections 2,
    # 2b, 3 and 4 all say "unavailable" with the same reason below, but a line
    # here means it is not something you have to notice four tables in.
    for item in analyses:
        collection = item["analysis"]["measurements"].get("bbox_collection") or {}
        if collection.get("status") != "value":
            lines.append("")
            lines.append("- `{0}`: **ANNOTATION BBOX COLLECTION FAILED.** {1} "
                         "Measurements 2, 2b, 2c, 3 and 4 are all withheld for "
                         "this variant.".format(
                             item["variant"], collection.get("reason")))
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
        faults, faults_source = _capture_faults_for(item)
        rows.append([
            item["variant"],
            _fmt(sidecar.get("model_suppression_mode")),
            _fmt(sidecar.get("applied_smooth_edges")),
            _fmt(sidecar.get("applied_display_style")),
            _fmt(variant_report.get("conclusion")),
            ("yes" if measurement.get("measured") is True
             else ("NO" if measurement.get("measured") is False else "--")),
            _fmt((variant_report.get("annotation_pass") or {}).get("success")),
            (", ".join(str(f.get("fault")) for f in faults)
             if faults else ("none" if faults_source != "unavailable"
                             else "UNKNOWN (not recorded)")),
            faults_source,
        ])
    lines.extend(_table(["variant", "suppression mode", "applied_smooth_edges",
                         "display style", "probe conclusion",
                         "measured its candidate", "production success",
                         "capture faults", "faults read from"], rows))
    lines.append("")
    lines.append("`capture faults` come from the probe's combined record "
                 "(`annotation_pass.capture_faults`) in preference to the "
                 "annotation sidecar, and the last column says which was used. "
                 "The sidecar is the fallback because production wrote it before "
                 "computing its own faults until `dcb4e65`, so an older capture's "
                 "file carries none however faulted it was. `UNKNOWN (not "
                 "recorded)` means neither source had the field -- which is not "
                 "the same fact as `none`.")
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
        top_left = fit.get("frame_top_left_predicted_px") or (None, None)
        rows.append([
            item["variant"],
            "{0} pairs".format(fit["sample_count"]),
            _fmt(fit.get("px_per_ft_u")),
            _fmt(fit.get("px_per_ft_v")),
            _fmt(fit.get("frame_px_per_ft")),
            "({0}, {1})".format(_fmt(top_left[0], "{0:.1f}"),
                                _fmt(top_left[1], "{0:.1f}")),
            "{0} / {1}".format(_fmt(fit.get("residual_median_px"), "{0:.2f}"),
                               _fmt(fit.get("residual_max_px"), "{0:.2f}")),
            fit.get("v_axis_sign", "--")])
    lines.extend(_table(["variant", "samples", "px/ft u", "px/ft v",
                         "frame px/ft", "offset at (u0,v1) px",
                         "residual med/max px", "v axis"], rows))
    lines.append("")
    for item in analyses:
        fit = item["analysis"]["measurements"]["registration"]
        if fit.get("frame_source"):
            lines.append("- `{0}` frame: {1}".format(item["variant"],
                                                    fit["frame_source"]))
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
        if fit.get("margin_ft") is None:
            rows.append([item["variant"], "--", "--", "--", "--",
                         fit.get("frame_reason") or "no reference rectangle"])
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
        deltas = agreement.get("margin_ft_delta")
        rows.append([item["variant"],
                     _fmt(alt.get("px_per_ft_u")), _fmt(alt.get("px_per_ft_v")),
                     _fmt(agreement["px_per_ft_u_delta"], "{0:.4f}"),
                     _fmt(agreement["px_per_ft_v_delta"], "{0:.4f}"),
                     (" / ".join(_fmt(deltas[side], "{0:.3f}")
                                 for side in ("left", "right", "top", "bottom"))
                      if deltas else "--"),
                     agreement.get("margin_reason") or ""])
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

    lines.extend(_render_f1_f2(analyses))

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


def _capture_faults_for(item):
    """One capture's faults, and WHICH SOURCE they came from.

    The probe's combined record is preferred over the annotation sidecar, and
    the reason is a producer bug this review round found: production serialised
    ``state_out`` about a hundred lines BEFORE it computed
    ``capture_faults``, so the persisted sidecar never carried them. Section 0
    therefore printed `none` for precisely the failed captures it exists to
    expose. Production is fixed as of `dcb4e65`, but a sidecar written by an
    earlier build still has no such key, and this tool reads files from runs it
    did not produce.

    Returns ``(faults, source)`` where source is "combined_record", "sidecar" or
    "unavailable". "unavailable" is NOT an empty fault list: neither source had
    the field, and reporting that as "none" is the same silence being fixed.
    """
    variant_report = item.get("variant_report") or {}
    annotation_pass = variant_report.get("annotation_pass") or {}
    if "capture_faults" in annotation_pass:
        faults = annotation_pass.get("capture_faults")
        if faults is not None:
            return (list(faults), "combined_record")
    sidecar_raw = item["analysis"]["sidecar"].get("capture_faults_raw")
    if sidecar_raw is not None:
        return (list(sidecar_raw), "sidecar")
    return ([], "unavailable")


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


def _json_record(run, capture, analysis):
    """The machine-readable half: where the boundary and fiducials WERE, so a
    consumer can subtract them without re-deriving anything."""
    inputs = (run.get("combined") or {}).get("inputs") or {}
    measurements = analysis["measurements"]
    boundary = measurements.get("crop_boundary") or {}
    model_boundary = measurements.get("model_crop_boundary") or {}
    return {
        "view_id": inputs.get("view_id"), "view_name": inputs.get("view_name"),
        "variant": capture["variant"],
        "annotation_tiff": str(capture["tiff"]),
        "crop_boundary": {
            "status": boundary.get("status"), "reason": boundary.get("reason"),
            "boundary_px_rects": boundary.get("boundary_px_rect"),
            "mapping": boundary.get("mapping"),
            "must_be_subtracted": boundary.get("status") == "value",
        },
        "model_crop_boundary": ({
            "status": model_boundary.get("status"),
            "boundary_px_rects": model_boundary.get("boundary_px_rect"),
            "model_tiff": str(capture.get("own_model_tiff")),
        } if model_boundary else None),
        "fiducials": {
            "status": (measurements.get("fiducials") or {}).get("status"),
            "per_fiducial": (measurements.get("fiducials") or {}).get("fiducials"),
            "mapping": (measurements.get("fiducials") or {}).get("mapping"),
        },
        "registration_marks": _marks_json(measurements.get("registration_marks")),
        "mapping_agreement": measurements.get("mapping_agreement"),
    }


def _marks_json(f3):
    """F3 for a consumer: both mappings, the composed annotation -> model pixel
    transform, and the mark pixels to subtract from each capture."""
    f3 = f3 or {}
    if f3.get("status") == "not_applicable":
        return {"status": "not_applicable"}
    model = f3.get("model") or {}
    return {
        "status": f3.get("status"), "reason": f3.get("reason"),
        "mapping": f3.get("mapping"),
        "mark_pixel_rects": f3.get("mark_pixel_rects"),
        "model": {"status": model.get("status"), "reason": model.get("reason"),
                  "mapping": model.get("mapping"),
                  "lattice_mapping": model.get("lattice_mapping"),
                  "vs_lattice": model.get("vs_lattice"),
                  "mark_pixel_rects": model.get("mark_pixel_rects")},
        "annotation_to_model_px": f3.get("annotation_to_model_px"),
        "must_be_subtracted": True,
    }


def build_report(targets, overlay_enabled=True, json_records=None):
    json_records = json_records if json_records is not None else []
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
                    v0_model_tiff_sha=v0_sha,
                    context=capture_context(run, capture))
                if capture.get("own_model_sidecar") and capture.get("own_model_tiff"):
                    analysis["measurements"]["model_crop_boundary"] = (
                        analyze_model_boundary(
                            capture["own_model_sidecar"], capture["own_model_tiff"],
                            capture_context(run, capture).get("crop_element_ids")))
                analyses.append({"variant": capture["variant"],
                                 "analysis": analysis,
                                 "variant_report": capture.get("variant_report")})
                json_records.append(_json_record(run, capture, analysis))
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
    parser.add_argument("--json-out", default=None,
                        help="also write the recovered crop boundary and "
                             "fiducial positions, per capture, as JSON")
    parser.set_defaults(overlay=True)
    args = parser.parse_args(argv)
    json_records = []
    report = build_report(args.targets, overlay_enabled=args.overlay,
                          json_records=json_records)
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"captures": json_records}, indent=2, sort_keys=True,
                       default=str), encoding="utf-8")
        print("wrote {0}".format(args.json_out))
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print("wrote {0}".format(args.out))
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
