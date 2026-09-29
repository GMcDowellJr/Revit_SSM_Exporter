"""ONE implementation of the Stage A registration-mark fit, and what applies it.

The registered capture (``vop_interwoven/stage_a_registered_capture.py``)
draws twelve detail-line ticks at known view UV into BOTH captures and records
them in each sidecar under ``registration_marks``. It does not fit them: pixels
are read after the capture, outside Revit. Until this module the fit existed
only inside the probe analyzer (``tools/notes/anno_pass_variant_report.py``),
so nothing that consumes a production capture could use it. It was LIFTED
here, not copied -- the analyzer imports these names back -- because a fit
computed in two places is CLAUDE.md defect class 1 waiting to happen.

Consumers:
  * ``tools/notes/anno_pass_variant_report.py`` -- the probe's F3 section;
  * ``tools/register_stage_a_annotation.py`` -- resamples the annotation
    capture onto the model lattice through the composed transform;
  * ``tools/decode_stage_a_color_id.py`` -- subtracts the marks' pixels so a
    decode never reads a tick as element content or as contamination.

CONVENTIONS
-----------
Continuous pixel coordinates: pixel ``i`` spans ``[i, i+1]``, so a tick one
pixel wide at column 10 has its centre line at 10.5. A mapping is
``{"a_u", "b_u", "a_v", "b_v"}`` with ``x = a_u*u + b_u`` and
``y = a_v*v + b_v`` (``a_v`` negative: +v is up, +y is down).

WHAT THIS REFUSES
-----------------
An axis whose found ticks sit at fewer than two distinct levels fixes no
scale; the fit returns ``status: "unavailable"`` rather than a line through
one level. ``compose_pixel_transform`` returns None when either side is
missing, and ``transform_refusal`` names a transform that mirrors or
collapses an axis. A caller must treat each as a refusal, never as a default.

LEAF MODULE: standard library + numpy + PIL. Imports nothing from
``vop_interwoven`` and nothing from any other tool.
"""
from __future__ import annotations

from collections import Counter

import numpy as np
from PIL import Image

WHITE = (255, 255, 255)

# The only schema the registered capture writes. A record under another
# schema is refused by ``recorded_marks``, not read on the hope it matches.
REGISTERED_CAPTURE_SCHEMA = "vop.stage_a.registered_capture.v1"

MISSING_TICK_PAD_PX = 3

# The worst centre-line residual a fit may carry and still be APPLIED. Every
# fit on pipeline_0928_0953 that located its ticks correctly came in at or
# under 0.39 px; the ones that had taken stray pixels for ticks were at
# 199-596 px. A fit that far from its own ticks is not a registration, and
# resampling through it would be a guess. Reporting (the probe analyzer) is
# not bound by this; applying is.
MAX_APPLIED_RESIDUAL_PX = 2.0


def fit_axis(xs, ys):
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


def axis_fit_record(levels, positions):
    """Least-squares ``position = a * level + b``, with residuals."""
    fit = fit_axis([float(v) for v in levels], [float(p) for p in positions])
    if fit is None:
        return None
    a, b = fit
    residuals = [abs(a * lv + b - p) for lv, p in zip(levels, positions)]
    return {"a": a, "b": b, "residual_max_px": max(residuals) if residuals else None,
            "points": len(levels)}


def load_rgb(path):
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


def pack_pixels(pixels):
    return ((pixels[:, :, 0].astype(np.int32) << 16)
            | (pixels[:, :, 1].astype(np.int32) << 8)
            | pixels[:, :, 2].astype(np.int32))


def _pack(rgb):
    r, g, b = (int(c) for c in rgb)
    return (r << 16) | (g << 8) | b


def unpack(key):
    key = int(key)
    return ((key >> 16) & 255, (key >> 8) & 255, key & 255)


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


def _tick_measure(ys, xs, orientation, weights=None):
    """A tick's centre line and extent, in continuous pixel coordinates (pixel
    i spans [i, i+1], as every other fit here).

    ``weights``: each pixel's COVERAGE (1.0 for the exact tick colour, the
    blend fraction for an anti-aliased fringe). The centre line is then the
    coverage-weighted mean, and the ends come from pixels at least half
    covered. None weighs every pixel 1 -- the exact-colour behaviour."""
    ys = np.asarray(ys, dtype=float)
    xs = np.asarray(xs, dtype=float)
    out = {"pixel_count": int(len(xs)),
           "pixel_bbox": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]}
    if weights is None:
        w = np.ones(len(xs))
        solid = np.ones(len(xs), dtype=bool)
    else:
        w = np.asarray(weights, dtype=float)
        solid = w >= 0.5
        if not solid.any():
            solid = np.ones(len(xs), dtype=bool)
        out["coverage_px"] = float(w.sum())
        out["blended_px"] = int(np.count_nonzero(w < 1.0 - 1e-9))
    if orientation == "horizontal":
        out["centre_px"] = float((ys * w).sum() / w.sum()) + 0.5
        out["end_px"] = [float(xs[solid].min()), float(xs[solid].max()) + 1.0]
    else:
        out["centre_px"] = float((xs * w).sum() / w.sum()) + 0.5
        out["end_px"] = [float(ys[solid].min()), float(ys[solid].max()) + 1.0]
    return out


# A pixel is an anti-aliased fringe of tick colour ``c`` when it lies on the
# line from ``c`` to white: ``p = t*c + (1-t)*255`` in every channel, to within
# BLEND_TOLERANCE levels, at a coverage ``t`` of at least BLEND_MIN_COVERAGE.
# Measured on pipeline_0928_0953's elevation: every horizontal tick of the
# annotation capture landed on a pixel BOUNDARY and exported as two rows at
# t = 0.75 and 0.62, with no pixel of its exact colour -- so an exact-colour
# locator found 4 of 12 ticks and the capture could not be registered.
BLEND_TOLERANCE = 3.0
BLEND_MIN_COVERAGE = 0.1
# COLOUR ALONE CANNOT IDENTIFY A FRINGE. The annotation palette is a wheel of
# ~300 saturated hues, and a faint fringe of one lies within BLEND_TOLERANCE of
# a neighbour's line to white: on the same run's Plan_CropActive each tick
# colour matched 40-370 pixels far from its tick, nearly all under 50 %
# coverage, and the fit's residual went from 0.24 px to 522 px. So a blend
# is a tick pixel only inside a TICK-SIZED connected piece: one holding at
# least this fraction of the largest piece's coverage, with at least one
# pixel at 50 % or more. A tick crossed by other ink splits into comparable
# pieces and keeps both; a stray fringe is a few faint pixels and does not.
TICK_PIECE_MIN_FRACTION = 0.25
# ...and LINE-SHAPED. Coverage alone let compact fringe blobs through
# (13 x 8 px beside text on Plan_CropInActive, 11 x 23 px on the RCP, both
# still 596 / 199 px residuals): a tick piece's bbox must be at least this
# many times longer than it is wide, and -- where the tick's orientation is
# known (the annotation capture) -- long in THAT direction.
TICK_PIECE_MIN_ASPECT = 3.0
# ...and ON ONE LINE. Plan_CropInActive's ticks are 21 px long (4.15 px/ft),
# so a 10 x 3 px fringe passes both tests above and still took the fit to a
# 584 px residual. A tick split by crossing ink leaves pieces on ONE centre
# line, abutting along it; a stray fringe lies anywhere. So, where the tick's
# orientation is known, a piece is kept only if its centre line is within
# this many pixels of the strongest piece's, and the gap between them along
# the tick is no longer than the strongest piece itself.
TICK_PIECE_MAX_OFF_LINE_PX = 2.0


def blend_coverage(unique_packed, rgb):
    """``(packed colours, coverage)`` for the members of ``unique_packed`` that
    are ``rgb`` blended toward white (``rgb`` itself included, at 1.0)."""
    unique_packed = np.asarray(unique_packed, dtype=np.int64)
    colours = np.stack([(unique_packed >> 16) & 255, (unique_packed >> 8) & 255,
                        unique_packed & 255], axis=1).astype(float)
    depth = 255.0 - np.asarray(rgb, dtype=float)
    norm = float(depth @ depth)
    if norm <= 0.0:
        return unique_packed[:0], np.zeros(0)
    toward_white = 255.0 - colours
    t = (toward_white @ depth) / norm
    residual = np.abs(t[:, None] * depth[None, :] - toward_white).max(axis=1)
    keep = ((t >= BLEND_MIN_COVERAGE) & (t <= 1.0 + 1e-9)
            & (residual <= BLEND_TOLERANCE))
    return unique_packed[keep], np.minimum(t[keep], 1.0)


def mark_colour_keys(packed, colours, blends=False, reserved_colours=()):
    """Which packed pixel values may belong to each mark colour, and at what
    coverage: ``({colour: (keys, coverage)}, ambiguous keys)``.

    Exact colour only unless ``blends``. A value that is some element's own
    palette colour (``reserved_colours``) is never a blend -- it is that
    element. A value that two mark colours could both explain is reported as
    ambiguous and offered to BOTH: colour cannot say which tick it belongs to,
    and position can -- ``_tick_pieces`` keeps it only where it touches a
    tick-sized piece."""
    colours = [tuple(int(c) for c in rgb) for rgb in colours]
    if not blends:
        return dict((c, (np.array([_pack(c)], dtype=np.int64), np.ones(1)))
                    for c in colours), set()
    unique = np.unique(packed[packed != 0xFFFFFF])
    own = set(_pack(c) for c in colours)
    reserved = set(_pack(c) for c in reserved_colours) - own
    per, owners = {}, {}
    for c in colours:
        keys, cover = blend_coverage(unique, c)
        if reserved:
            ok = ~np.isin(keys, np.fromiter(reserved, dtype=np.int64))
            keys, cover = keys[ok], cover[ok]
        per[c] = (keys, cover)
        for k in keys.tolist():
            owners[k] = owners.get(k, 0) + 1
    return per, set(k for k, n in owners.items() if n > 1)


def _pixels_of(packed, keys, cover, weighted, exact_colour=None):
    """``(ys, xs, weights or None, exact or None)`` of the pixels holding
    ``keys``; ``exact`` marks those of ``exact_colour`` itself."""
    select = np.isin(packed, keys)
    ys, xs = np.nonzero(select)
    if not weighted:
        return ys, xs, None, None
    order = np.argsort(keys)
    values = packed[ys, xs].astype(np.int64)
    weights = cover[order][np.searchsorted(keys[order], values)]
    exact = (values == _pack(exact_colour)) if exact_colour is not None else None
    return ys, xs, weights, exact


def _line_shaped(ys, xs, orientation=None):
    width = float(xs.max() - xs.min() + 1)
    height = float(ys.max() - ys.min() + 1)
    if orientation == "horizontal":
        return width >= TICK_PIECE_MIN_ASPECT * height
    if orientation == "vertical":
        return height >= TICK_PIECE_MIN_ASPECT * width
    return max(width, height) >= TICK_PIECE_MIN_ASPECT * min(width, height)


def _tick_pieces(ys, xs, weights, orientation=None, exact=None):
    """The connected pieces of a candidate pixel set, as index arrays, and how
    many were dropped as stray fringe.

    A piece holding any pixel of the tick's EXACT colour (``exact``) is always
    kept: that colour is reserved to the mark, so every piece of it is the
    mark's -- including a short one left where crossing ink cut a tick near
    its end, which the filters below would otherwise drop and leave
    unsubtracted (review, PR #219). Only BLEND-ONLY pieces are filtered: not
    line-shaped (TICK_PIECE_MIN_ASPECT, along ``orientation`` when it is
    known), without one pixel half covered, under TICK_PIECE_MIN_FRACTION of
    the strongest piece's coverage, or off its line. Nothing is dropped from
    an exact-colour-only set (``weights`` None)."""
    pieces = [np.asarray(c) for c in _components(ys, xs)]
    if weights is None or not pieces:
        return pieces, 0
    anchored = [c for c in pieces if exact is not None and bool(exact[c].any())]
    anchored_ids = set(id(c) for c in anchored)
    blend_only = [c for c in pieces if id(c) not in anchored_ids]
    shaped = [c for c in blend_only if float(weights[c].max()) >= 0.5
              and _line_shaped(ys[c], xs[c], orientation)]
    if not shaped and not anchored:
        return [], len(pieces)
    strongest = max(float(weights[c].sum()) for c in anchored + shaped)
    floor = TICK_PIECE_MIN_FRACTION * strongest
    kept = anchored + [c for c in shaped if float(weights[c].sum()) >= floor]
    if orientation in ("horizontal", "vertical") and len(kept) > 1:
        best = kept[int(np.argmax([float(weights[c].sum()) for c in kept]))]
        along, across = (xs, ys) if orientation == "horizontal" else (ys, xs)

        def _on_line(c):
            if abs(float(across[c].mean()) - float(across[best].mean())) > \
                    TICK_PIECE_MAX_OFF_LINE_PX:
                return False
            length = float(along[best].max() - along[best].min() + 1)
            gap = max(float(along[c].min() - along[best].max()),
                      float(along[best].min() - along[c].max()), 0.0)
            return gap <= length
        kept = [c for c in kept
                if c is best or id(c) in anchored_ids or _on_line(c)]
    return kept, len(pieces) - len(kept)


def _take(ys, xs, weights, pieces):
    if not pieces:
        return ys[:0], xs[:0], None if weights is None else weights[:0]
    index = np.concatenate(pieces)
    return ys[index], xs[index], None if weights is None else weights[index]


def _locate(pixels, marks, colour_by_id=None, shared_colour=None,
            blends=False, reserved_colours=()):
    """``locate_mark_pixels``, plus every pixel it attributed to a mark --
    assigned to a tick or not -- as ``(ys, xs)``: what is subtracted."""
    found, missing = {}, []
    packed = pack_pixels(pixels)
    ink_ys, ink_xs = [], []
    if shared_colour is None:
        by_mark = dict((m["key"], (colour_by_id or {}).get(int(m["id"])))
                       for m in marks if m.get("id") is not None)
        per, ambiguous = mark_colour_keys(
            packed, [c for c in by_mark.values() if c is not None], blends,
            reserved_colours)
        dropped = 0
        for mark in marks:
            rgb = by_mark.get(mark["key"])
            if rgb is None:
                missing.append({"key": mark["key"], "reason": "no colour in the "
                                "capture's colour map for id {0}".format(mark.get("id"))})
                continue
            keys, cover = per[tuple(int(c) for c in rgb)]
            ys, xs, weights, exact = _pixels_of(packed, keys, cover, blends, rgb)
            if blends:
                pieces, n = _tick_pieces(ys, xs, weights, mark.get("orientation"),
                                         exact)
                dropped += n
                ys, xs, weights = _take(ys, xs, weights, pieces)
            if len(xs) == 0:
                missing.append({"key": mark["key"], "reason": "its colour {0} drew "
                                "no pixels{1}".format(list(rgb), (
                                    ", exact or blended toward white"
                                    if blends else ""))})
                continue
            found[mark["key"]] = (ys, xs, weights)
            ink_ys.append(ys)
            ink_xs.append(xs)
        stats = ({"ambiguous_blend_colours": len(ambiguous),
                  "stray_fringe_pieces_dropped": dropped} if blends else {})
        return found, missing, stats, (ink_ys, ink_xs)
    per, ambiguous = mark_colour_keys(packed, [shared_colour], blends,
                                      reserved_colours)
    keys, cover = per[tuple(int(c) for c in shared_colour)]
    ys, xs, weights, exact = _pixels_of(packed, keys, cover, blends, shared_colour)
    pieces, dropped = _tick_pieces(ys, xs, weights, exact=exact)
    by_slot = {}
    stats = {"components": 0, "merged_into_one_tick": 0}
    if blends:
        stats["ambiguous_blend_colours"] = len(ambiguous)
        stats["stray_fringe_pieces_dropped"] = dropped
    if pieces:
        kept_ys, kept_xs, _w = _take(ys, xs, weights, pieces)
        ink_ys.append(kept_ys)
        ink_xs.append(kept_xs)
        x_lo, x_hi = float(kept_xs.min()), float(kept_xs.max()) + 1.0
        y_lo, y_hi = float(kept_ys.min()), float(kept_ys.max()) + 1.0
        stats["frame_px"] = [x_lo, y_lo, x_hi, y_hi]

    def _third(value, lo, hi, names):
        f = (value - lo) / (hi - lo)
        return names[0 if f < 1.0 / 3.0 else (1 if f < 2.0 / 3.0 else 2)]

    for component in pieces:
        stats["components"] += 1
        cy, cx = ys[component], xs[component]
        cw = weights[component] if weights is not None else None
        orientation = ("horizontal" if (cx.max() - cx.min()) >= (cy.max() - cy.min())
                       else "vertical")
        x, y = cx.mean() + 0.5, cy.mean() + 0.5
        if orientation == "horizontal":
            corner = "{0}_{1}".format(
                "left" if x < (x_lo + x_hi) / 2.0 else "right",
                _third(y, y_lo, y_hi, ("top", "mid", "bottom")))
        else:
            corner = "{0}_{1}".format(
                _third(x, x_lo, x_hi, ("left", "mid", "right")),
                "top" if y < (y_lo + y_hi) / 2.0 else "bottom")
        slot = "{0}_{1}".format(corner, orientation[0])
        if slot in by_slot:
            stats["merged_into_one_tick"] += 1
            old = by_slot[slot]
            by_slot[slot] = (np.concatenate([old[0], cy]), np.concatenate([old[1], cx]),
                             None if cw is None else np.concatenate([old[2], cw]))
        else:
            by_slot[slot] = (cy, cx, cw)
    keys = set(m["key"] for m in marks)
    stats["unassigned_components"] = sorted(k for k in by_slot if k not in keys)
    for mark in marks:
        if mark["key"] in by_slot:
            found[mark["key"]] = by_slot[mark["key"]]
        else:
            missing.append({"key": mark["key"], "reason": "no component of the "
                            "shared colour in that corner and orientation"})
    return found, missing, stats, (ink_ys, ink_xs)


def locate_mark_pixels(pixels, marks, colour_by_id=None, shared_colour=None,
                       blends=False, reserved_colours=()):
    """``{mark key: (ys, xs, weights)}`` for each tick found, plus the reasons
    for the ones that were not. ``weights`` is None unless ``blends``.

    ``colour_by_id``: the annotation capture, one palette colour per tick.
    ``shared_colour``: the model capture, one reserved colour for all twelve;
    components are assigned to ticks by their bbox aspect (the orientation)
    and where their centroid falls within the MARKS' OWN EXTENT -- the bbox
    of every mark pixel kept: a horizontal tick by HALF across and THIRD down
    (left/right x top/mid/bottom), a vertical one by THIRD across and HALF
    down. That needs no mapping, only the layout's promise that corner ticks
    sit at the extremes and mid ticks inside the middle third. It is the
    marks' extent, not the image's, because the marks need not span the
    image: on a crop-inactive plan they sit on the raster frame in the middle
    of a far larger crop A, and image thirds put all six horizontal ticks in
    "mid" (pipeline_0928_0953, Plan_CropInActive: 8 of 12, 4 merged).
    Several components landing on one tick (a tick crossed by other ink) are
    merged and counted; a component that fits no tick is counted too.
    """
    found, missing, stats, _ink = _locate(pixels, marks, colour_by_id,
                                          shared_colour, blends, reserved_colours)
    return found, missing, stats


def fit_marks_in_pixels(pixels, marks, colour_by_id=None, shared_colour=None,
                        identify_by_id=None, blends=False, reserved_colours=()):
    """UV -> pixel from the ticks' CENTRE LINES, per axis, with residuals.

    Also an ENDPOINT fit (tick ends against their UV span), reported beside it
    and never in its place: an end is where line caps and anti-aliasing live.
    """
    marks = [m for m in marks or [] if m.get("key")]
    if not marks:
        return {"status": "not_applicable", "reason": "no registration marks"}
    found, missing, stats = locate_mark_pixels(pixels, marks, colour_by_id,
                                               shared_colour, blends,
                                               reserved_colours)
    ticks = []
    centre = {"horizontal": ([], []), "vertical": ([], [])}
    ends = {"horizontal": ([], []), "vertical": ([], [])}
    mark_pixel_rects = []
    for mark in marks:
        if mark["key"] not in found:
            continue
        ys, xs, weights = found[mark["key"]]
        measured = _tick_measure(ys, xs, mark["orientation"], weights)
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
    u_fit = axis_fit_record(*centre["vertical"])
    v_fit = axis_fit_record(*centre["horizontal"])
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
    end_u = axis_fit_record(*ends["horizontal"])
    end_v = axis_fit_record(*ends["vertical"])
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


def registration_mark_fit(tiff_path, marks, colour_by_id=None, shared_colour=None,
                          identify_by_id=None):
    """``fit_marks_in_pixels`` on a file; an unreadable file is ``unavailable``."""
    marks = [m for m in marks or [] if m.get("key")]
    if not marks:
        return {"status": "not_applicable", "reason": "no registration marks"}
    try:
        pixels = load_rgb(tiff_path)
    except (OSError, ValueError) as ex:
        return {"status": "unavailable",
                "reason": "could not read {0}: {1}: {2}".format(
                    tiff_path, type(ex).__name__, ex)}
    return fit_marks_in_pixels(pixels, marks, colour_by_id, shared_colour,
                               identify_by_id)


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


def residual_refusal(fit):
    """Why a ``value`` fit is too inconsistent with its own ticks to apply."""
    worst = max(v for v in (fit.get("residual_max_px") or {}).values()
                if v is not None)
    if worst > MAX_APPLIED_RESIDUAL_PX:
        return "worst tick residual {0:.2f} px exceeds {1} px: the fit does not " \
               "agree with its own ticks".format(worst, MAX_APPLIED_RESIDUAL_PX)
    return None


def transform_refusal(transform):
    """Why ``transform`` cannot be applied, or None. A negative scale mirrors
    an axis and a zero or non-finite one collapses it: both mean the two fits
    disagree about which way the view runs, which no resample can repair."""
    if transform is None:
        return "no transform: one of the two captures has no mark fit"
    for axis in ("x", "y"):
        scale = transform["scale_" + axis]
        offset = transform["offset_" + axis]
        if not (np.isfinite(scale) and np.isfinite(offset)) or scale <= 0.0:
            return "scale_{0} = {1!r}: the fits disagree on the {0} axis's " \
                   "direction".format(axis, scale)
    return None


def mapping_agreement(first, second, probe_uv):
    """How far two UV->pixel mappings disagree. Deltas only, no verdict.

    ``probe_uv`` is the rectangle whose corners are pushed through both --
    a disagreement in scale is only meaningful at a place in the image.
    THE DISAGREEMENT IS THE NUMBER.
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

    The model capture rendered ``bounds_xy`` (crop A) at its own pixel count.
    That is a PREMISE -- it ignores ExportImage's aspect-clamp pad, which
    ``tools/clamp_pad_geometry.py`` models -- so any figure derived through it
    is labelled model-anchored by whoever reports it.
    """
    if not bounds_uv or len(bounds_uv) != 4:
        return None
    x0, y0, x1, y1 = (float(v) for v in bounds_uv)
    if x1 <= x0 or y1 <= y0 or not image_w or not image_h:
        return None
    a_u = float(image_w) / (x1 - x0)
    a_v = -float(image_h) / (y1 - y0)
    return {"a_u": a_u, "b_u": -a_u * x0, "a_v": a_v, "b_v": -a_v * y1}


# ======================================================================
# THE PRODUCTION RECORD: a sidecar's ``registration_marks`` block
# ======================================================================

def recorded_marks(sidecar):
    """``(payload, None)`` for a sidecar's usable ``registration_marks``
    block, else ``(None, reason)``. ``(None, None)`` means the capture has no
    marks at all -- not a registered capture -- which is not a refusal."""
    payload = sidecar.get("registration_marks")
    if payload is None:
        return None, None
    if not isinstance(payload, dict):
        return None, "registration_marks is a {0}, not a record".format(
            type(payload).__name__)
    if payload.get("schema") != REGISTERED_CAPTURE_SCHEMA:
        return None, "registration_marks schema {0!r} is not {1!r}".format(
            payload.get("schema"), REGISTERED_CAPTURE_SCHEMA)
    if payload.get("pass") not in ("model", "annotation"):
        return None, "registration_marks pass {0!r} is neither model nor " \
                     "annotation".format(payload.get("pass"))
    if not payload.get("marks"):
        return None, "registration_marks records no marks"
    return payload, None


def mark_colours(payload):
    """``(colour_by_id, shared_colour, refusal)`` for a payload, as
    ``locate_mark_pixels`` takes them. The MODEL pass paints every tick one
    reserved colour; the ANNOTATION pass paints each its own palette colour.
    A model record whose ticks disagree on the colour is refused."""
    marks = payload.get("marks") or []
    if payload.get("pass") == "model":
        colours = set(tuple(int(c) for c in m["rgb"]) for m in marks if m.get("rgb"))
        if len(colours) != 1:
            return None, None, "the model record names {0} tick colours; it " \
                               "must name exactly one".format(len(colours))
        return None, colours.pop(), None
    by_id = {}
    for mark in marks:
        if mark.get("id") is not None and mark.get("rgb"):
            by_id[int(mark["id"])] = tuple(int(c) for c in mark["rgb"])
    return by_id, None, None


def mark_ink_mask(pixels, payload, reserved_colours=()):
    """Every pixel attributed to a mark, as a boolean mask -- what is
    SUBTRACTED. Exactly the pixels the fit locates (anti-aliased fringes
    included, stray look-alike fringes elsewhere in the image not), plus any
    piece of the model's reserved colour that fit no tick. Not the ticks'
    pixel rects: a rect would also erase any element ink crossing a tick.
    """
    by_id, shared, _refusal = mark_colours(payload)
    mask = np.zeros(pixels.shape[:2], dtype=bool)
    if shared is None and not by_id:
        return mask
    _found, _missing, _stats, (ink_ys, ink_xs) = _locate(
        pixels, payload.get("marks") or [], colour_by_id=by_id,
        shared_colour=shared, blends=True, reserved_colours=reserved_colours)
    for ys, xs in zip(ink_ys, ink_xs):
        mask[ys, xs] = True
    return mask


def palette_colours(sidecar):
    """The capture's own palette, as the ``reserved_colours`` above."""
    return [tuple(int(c) for c in rgb)
            for rgb in (sidecar.get("color_assignment_map") or {}).values()]


def fit_recorded_marks(pixels, payload, identify_by_id=None, reserved_colours=()):
    """The fit for a production capture, from its own ``registration_marks``,
    anti-aliased fringes included (weighted by coverage)."""
    by_id, shared, refusal = mark_colours(payload)
    if refusal:
        return {"status": "unavailable", "reason": refusal}
    return fit_marks_in_pixels(pixels, payload.get("marks"), colour_by_id=by_id,
                               shared_colour=shared, identify_by_id=identify_by_id,
                               blends=True, reserved_colours=reserved_colours)


def marks_identity_refusal(first, second):
    """Why two payloads are NOT the same marks, or None.

    Composing a fit from one run's marks with another run's would register
    nothing, and it would look exactly like a registration. The ticks are
    compared by key, id and recorded UV."""
    def _identity(payload):
        return sorted((m.get("key"), m.get("id"), round(float(m["level_uv"]), 9),
                       tuple(round(float(v), 9) for v in m["span_uv"]))
                      for m in payload.get("marks") or [])
    if _identity(first) != _identity(second):
        return "the two sidecars' registration_marks describe different ticks " \
               "(key, id or UV differ); they are not one capture's marks"
    return None
