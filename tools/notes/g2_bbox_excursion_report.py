#!/usr/bin/env python3
"""G2: per-element decoded V extent vs the element's own Revit-side UV bbox.

Measures, per view and per HOST element, how far an element's decoded
vertical extent falls OUTSIDE the bbox recorded for it during Stage A
capture -- at BOTH the pre-D1 and post-D1 pixel->UV mappings, in one pass.

    python tools/notes/g2_bbox_excursion_report.py <run>/color_id_buffer/

REFERENCE (see tools/notes/g2_source_findings.md, Q3)
-----------------------------------------------------
sidecar["near_face_w_map"]["host"]["<id>"]["bbox_corners_uv"], written by
color_id_buffer.py:2403/2715 from project_bbox_uv_and_near_face_w()
(revit/collection.py:897). That is Revit BoundingBoxXYZ corners projected
through the ViewBasis -- computed BEFORE the TIFF is rendered, touching no
pixel count, no feet_per_pixel and no image dimension. It is therefore
independent of the mapping under test, and the D1 patch cannot move it.

Deliberately NOT used as the reference: decode's own
_element_bounding_boxes(id_array), which is the element's pixel extent in
the decoded image. Measuring against that is circular -- the patch moves
measurement and reference together and the gate passes trivially.

WHY BOTH MAPPINGS IN ONE RUN
----------------------------
The task specifies stash / measure / unstash / measure. Both mappings are
instead replicated verbatim below (each citing its commit), so a single
invocation produces both columns from provably identical harness code --
which is what the two-commit protocol is for. The stash protocol remains
available as an independent cross-check via --use-installed-decode, which
imports the live _pixel_corner_to_uv from tools/decode_stage_a_color_id.py
and PROBES it to report which of the two mappings it actually resolved to,
so there is no ambiguity about what was measured under the stash.

WHAT THIS MEASUREMENT CAN AND CANNOT CLAIM
------------------------------------------
bbox_corners_uv is the AABB of 8 projected corners -- a SUPERSET of the
element's true silhouette. So an excursion is hard evidence of a mapping
error, but containment is NOT proof of a correct mapping: a wrong mapping
can still land inside a generous bbox. Read a 0-count as "no element
escapes its own bound", not as "the mapping is verified".

Counts are reported at two tolerances because the pre-fix figure this is
being compared against (33/81) came from code of unknown definition:
  strict  -- any excursion > 0 ft
  1px     -- excursion > one feet_per_pixel, absorbing pixel-corner slop
The worst excursion in feet is reported regardless of tolerance.

Standalone: Pillow + NumPy only. Imports nothing from vop_interwoven, and
nothing from tools/ unless --use-installed-decode is passed.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image


# --------------------------------------------------------------------------
# The two mappings under comparison, replicated verbatim.
# --------------------------------------------------------------------------

def v_baseline(y, bounds_uv, image_w, image_h, _dims):
    """Pre-D1 mapping -- tools/decode_stage_a_color_id.py @ a9051f2:564-568.

        v = ymax - (float(y) / float(image_h)) * (ymax - ymin)

    V stretched across the FULL image height, including Revit's clamp pad.
    """
    xmin, ymin, xmax, ymax = bounds_uv
    return ymax - (float(y) / float(image_h)) * (ymax - ymin)


def clamp_pad_geometry(bounds_uv, image_w, image_h, dims):
    """Post-D1 -- _clamp_pad_geometry(), tools/decode_stage_a_color_id.py @ cdb0b2a.

    dims is (fit_w, fit_h): the producer's recorded export size when the
    sidecar has one, else the image's own -- matching the patch's
    measured_w/measured_h preference.
    """
    xmin, ymin, xmax, ymax = (float(c) for c in bounds_uv)
    crop_u_ft, crop_v_ft = xmax - xmin, ymax - ymin
    fit_w, fit_h = float(dims[0]), float(dims[1])
    feet_per_pixel = max(crop_u_ft / fit_w, crop_v_ft / fit_h)
    pad_x = (float(image_w) - crop_u_ft / feet_per_pixel) / 2.0
    pad_y = (float(image_h) - crop_v_ft / feet_per_pixel) / 2.0
    return feet_per_pixel, pad_x, pad_y


def v_patched(y, bounds_uv, image_w, image_h, dims):
    """Post-D1 mapping -- _pixel_corner_to_uv() @ cdb0b2a: v = ymax - (y - pad_y) * fpp."""
    _xmin, _ymin, _xmax, ymax = (float(c) for c in bounds_uv)
    fpp, _pad_x, pad_y = clamp_pad_geometry(bounds_uv, image_w, image_h, dims)
    return ymax - (float(y) - pad_y) * fpp


# --------------------------------------------------------------------------

def decode_row_extents(tiff_path, color_assignment_map):
    """{elem_id_str: (min_row, max_row_exclusive)} for every painted element.

    Exact-match RGB -> id decode, same lookup construction as
    decode_stage_a_color_id.decode_ids(). Only pixel ROW extents are needed
    (V is the axis under test), so no loop tracing is involved: an element
    occupying rows r0..r1 inclusive spans pixel CORNER rows r0..r1+1, which
    is precisely the V extent its traced loops would report.
    """
    with Image.open(tiff_path) as img:
        rgb = np.asarray(img.convert("RGB"))
    h, w = rgb.shape[:2]
    packed = (rgb[:, :, 0].astype(np.int32) << 16
              | rgb[:, :, 1].astype(np.int32) << 8
              | rgb[:, :, 2].astype(np.int32))

    # Scan the image ONCE for which palette colors are actually present,
    # then take row extents only for those. Iterating the whole palette
    # instead would cost a full pass per assigned element -- hundreds of
    # passes over ~10 Mpx on a real capture, for elements that painted
    # nothing.
    code_to_id = {}
    for elem_id_str, c in color_assignment_map.items():
        code_to_id[(int(c[0]) << 16) | (int(c[1]) << 8) | int(c[2])] = elem_id_str
    present = np.intersect1d(
        np.unique(packed),
        np.fromiter(code_to_id, dtype=np.int64, count=len(code_to_id)),
        assume_unique=False,
    )

    out = {}
    for code in present.tolist():
        rows = np.nonzero((packed == code).any(axis=1))[0]
        if rows.size:
            out[code_to_id[code]] = (int(rows.min()), int(rows.max()) + 1)
    return out, w, h


def resolve_tiff(sidecar_path, sidecar):
    recorded = sidecar.get("tiff_path")
    if recorded and Path(recorded).exists():
        return Path(recorded)
    for suffix in (".tiff", ".tif"):
        cand = sidecar_path.with_suffix(suffix)
        if cand.exists():
            return cand
    raise FileNotFoundError("no TIFF for {0}".format(sidecar_path.name))


def measure_view(sidecar_path, mappings):
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    bounds = sidecar.get("bounds_xy")
    if not bounds or len(bounds) != 4:
        return {"view": sidecar_path.stem, "skip": "no bounds_xy (crop did not apply)"}
    bounds = tuple(float(v) for v in bounds)

    color_map = sidecar.get("color_assignment_map") or {}
    host_ref = ((sidecar.get("near_face_w_map") or {}).get("host") or {})

    tiff = resolve_tiff(sidecar_path, sidecar)
    row_extents, image_w, image_h = decode_row_extents(tiff, color_map)

    res = sidecar.get("resolution") or {}
    aw, ah = res.get("actual_w"), res.get("actual_h")
    dims = (float(aw), float(ah)) if (aw and ah) else (float(image_w), float(image_h))
    fpp, pad_x, pad_y = clamp_pad_geometry(bounds, image_w, image_h, dims)

    stats = {name: {"strict": 0, "onepx": 0, "worst": 0.0, "worst_elem": None}
             for name in mappings}
    measured = 0
    no_ref = 0
    painted = len(row_extents)

    for elem_id_str, (r0, r1) in row_extents.items():
        entry = host_ref.get(elem_id_str) or {}
        corners = entry.get("bbox_corners_uv")
        if not corners:
            no_ref += 1
            continue
        vs = [float(c[1]) for c in corners]
        ref_vmin, ref_vmax = min(vs), max(vs)
        measured += 1
        for name, fn in mappings.items():
            # Corner rows r0 (top, => V max) and r1 (bottom, => V min).
            dec_vmax = fn(r0, bounds, image_w, image_h, dims)
            dec_vmin = fn(r1, bounds, image_w, image_h, dims)
            exc = max(0.0, ref_vmin - dec_vmin, dec_vmax - ref_vmax)
            s = stats[name]
            if exc > 0.0:
                s["strict"] += 1
            if exc > fpp:
                s["onepx"] += 1
            if exc > s["worst"]:
                s["worst"], s["worst_elem"] = exc, elem_id_str

    return {"view": sidecar_path.stem, "painted": painted, "measured": measured,
            "no_ref": no_ref, "image": (image_w, image_h), "fpp": fpp,
            "pad_x": pad_x, "pad_y": pad_y, "stats": stats}


def probe_installed_decode():
    """Report which mapping tools/decode_stage_a_color_id.py currently implements.

    Used under the stash protocol so the measured commit is never assumed.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from tools import decode_stage_a_color_id as dsc
    bounds = (0.0, 0.0, 200.0, 8.0)   # 25:1 crop -> the clamp would pad it
    w, h = 10000, 1000
    got = dsc._pixel_corner_to_uv(0, 0, bounds, w, h)[1]
    exp_base = v_baseline(0, bounds, w, h, (w, h))
    exp_patch = v_patched(0, bounds, w, h, (w, h))
    if abs(got - exp_patch) < 1e-9:
        return dsc, "patched (post-D1)"
    if abs(got - exp_base) < 1e-9:
        return dsc, "baseline (pre-D1)"
    return dsc, "UNRECOGNISED (matches neither replica)"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sidecar_dir", help="a byColor run's color_id_buffer/ directory")
    ap.add_argument("--use-installed-decode", action="store_true",
                    help="also measure through the live tools/decode_stage_a_color_id.py "
                         "(the stash-protocol cross-check); reports which mapping it is")
    ns = ap.parse_args(argv)

    mappings = {"baseline": v_baseline, "patched": v_patched}
    if ns.use_installed_decode:
        dsc, which = probe_installed_decode()
        print("installed tools/decode_stage_a_color_id.py implements: {0}\n".format(which))

        def v_installed(y, bounds_uv, image_w, image_h, _dims):
            return dsc._pixel_corner_to_uv(0, y, bounds_uv, image_w, image_h)[1]

        mappings["installed"] = v_installed

    paths = sorted(p for p in Path(ns.sidecar_dir).rglob("*.json")
                   if not p.name.endswith(".decoded.json"))
    if not paths:
        print("no sidecars under {0}".format(ns.sidecar_dir))
        return 1

    names = list(mappings)
    head = "%-26s %7s %8s %6s %9s %9s" % ("view", "painted", "measured", "no_ref", "pad_x", "pad_y")
    for n in names:
        head += " | %11s %11s %11s" % (n[:4] + " strict", n[:4] + " 1px", n[:4] + " worst")
    print(head)

    totals = {n: {"strict": 0, "onepx": 0, "worst": 0.0} for n in names}
    grand_measured = grand_no_ref = 0
    for sp in paths:
        try:
            r = measure_view(sp, mappings)
        except (OSError, ValueError, KeyError, FileNotFoundError) as ex:
            print("%-26s  ERROR %s: %s" % (sp.stem[:26], type(ex).__name__, ex))
            continue
        if r.get("skip"):
            print("%-26s  %s" % (r["view"][:26], r["skip"]))
            continue
        line = "%-26s %7d %8d %6d %9.3f %9.3f" % (
            r["view"][:26], r["painted"], r["measured"], r["no_ref"], r["pad_x"], r["pad_y"])
        for n in names:
            s = r["stats"][n]
            line += " | %5d/%-5d %5d/%-5d %11.4f" % (
                s["strict"], r["measured"], s["onepx"], r["measured"], s["worst"])
            totals[n]["strict"] += s["strict"]
            totals[n]["onepx"] += s["onepx"]
            totals[n]["worst"] = max(totals[n]["worst"], s["worst"])
        grand_measured += r["measured"]
        grand_no_ref += r["no_ref"]
        print(line)

    print("\nTOTALS  measured={0} elements, no_ref={1} (unmeasurable: bbox_corners_uv null)"
          .format(grand_measured, grand_no_ref))
    for n in names:
        t = totals[n]
        print("  %-10s strict %d/%d   >1px %d/%d   worst V excursion %.4f ft"
              % (n, t["strict"], grand_measured, t["onepx"], grand_measured, t["worst"]))
    if grand_measured == 0:
        print("\nNO REFERENCE BBOXES FOUND. Q4 says these exist only in the byColor run; "
              "if this WAS byColor, stop and report rather than reaching for byGeom.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
