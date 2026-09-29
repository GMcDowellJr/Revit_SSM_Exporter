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
    packed = pack_pixels(pixels)
    if shared_colour is None:
        for mark in marks:
            rgb = (colour_by_id or {}).get(int(mark["id"])) if mark.get(
                "id") is not None else None
            if rgb is None:
                missing.append({"key": mark["key"], "reason": "no colour in the "
                                "capture's colour map for id {0}".format(mark.get("id"))})
                continue
            ys, xs = np.nonzero(packed == _pack(rgb))
            if len(xs) == 0:
                missing.append({"key": mark["key"], "reason": "its colour {0} drew "
                                "no pixels".format(list(rgb))})
                continue
            found[mark["key"]] = (ys, xs)
        return found, missing, {}
    ys, xs = np.nonzero(packed == _pack(shared_colour))
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


def fit_marks_in_pixels(pixels, marks, colour_by_id=None, shared_colour=None,
                        identify_by_id=None):
    """UV -> pixel from the ticks' CENTRE LINES, per axis, with residuals.

    Also an ENDPOINT fit (tick ends against their UV span), reported beside it
    and never in its place: an end is where line caps and anti-aliasing live.
    """
    marks = [m for m in marks or [] if m.get("key")]
    if not marks:
        return {"status": "not_applicable", "reason": "no registration marks"}
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


def mark_ink_mask(pixels, payload):
    """Every pixel drawn in a mark colour, as a boolean mask -- what is
    SUBTRACTED.

    Exact colour, not the ticks' pixel rects: a rect would also erase any
    element ink crossing a tick. Both colour schemes are reserved to the
    marks -- MARK_COLOUR is outside the model palette, and each annotation
    tick has its own palette entry -- so every pixel of those colours is a
    mark, including a component the fit could not assign to a tick.
    """
    by_id, shared, _refusal = mark_colours(payload)
    colours = [shared] if shared is not None else list((by_id or {}).values())
    packed = pack_pixels(pixels)
    if not colours:
        return np.zeros(packed.shape, dtype=bool)
    return np.isin(packed, np.array([_pack(c) for c in colours], dtype=np.int32))


def fit_recorded_marks(pixels, payload, identify_by_id=None):
    """The fit for a production capture, from its own ``registration_marks``."""
    by_id, shared, refusal = mark_colours(payload)
    if refusal:
        return {"status": "unavailable", "reason": refusal}
    return fit_marks_in_pixels(pixels, payload.get("marks"), colour_by_id=by_id,
                               shared_colour=shared, identify_by_id=identify_by_id)


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
