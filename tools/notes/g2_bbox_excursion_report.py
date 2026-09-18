#!/usr/bin/env python3
"""G2: per-element decoded UV extent vs the element's own Revit-side UV bbox.

Measures, per view and per HOST element, how far an element's decoded extent
falls outside -- or inside -- the bbox recorded for it during Stage A
capture, on all FOUR edges, with U and V reported separately.

    python tools/notes/g2_bbox_excursion_report.py <run>/color_id_buffer/
    python tools/notes/g2_bbox_excursion_report.py <run>/color_id_buffer/ \
        --per-element-csv /tmp/g2_per_element.csv

REFERENCE (see tools/notes/g2_source_findings.md, Q3)
-----------------------------------------------------
sidecar["near_face_w_map"]["host"]["<id>"]["bbox_corners_uv"], written by
color_id_buffer.py:2403/2715 from project_bbox_uv_and_near_face_w()
(revit/collection.py:896). That is Revit BoundingBoxXYZ corners projected
through the ViewBasis -- computed BEFORE the TIFF is rendered, touching no
pixel count, no feet_per_pixel and no image dimension. It is therefore
independent of the mapping under test, and the D1 patch cannot move it.

The same entry carries "category", which is what the per-category breakdown
in section 2 groups on.

Deliberately NOT used as the reference: decode's own
_element_bounding_boxes(id_array), which is the element's pixel extent in
the decoded image. Measuring against that is circular -- the patch moves
measurement and reference together and any gate passes trivially.

CLIPPED IS A REFERENCE TEST, NOT A PIXEL TEST
----------------------------------------------
An element is CLIPPED when its reference bbox crosses the crop rectangle:
the image cannot show its full extent, so every edge figure for it is a
lower bound. That is not the same population as "its painted pixels reach a
border of the image", which is carried separately as
``touches_image_edge``. On the 2026-09-17 byColor run the two differ
sharply -- Elev 5: 30 clipped against 22 touching; Section 1: 25 against 6 --
so the two tests are not interchangeable and neither substitutes for the
other.

This distinction is load-bearing. Two of that run's six recorded per-view
figures, 0.1056 and 0.1055 ft on Elev 5 and Elev 9, come ENTIRELY from
clipped elements: those views' unclipped maxima are 0.0438 and 0.0487 ft,
less than half. A report that pools the two populations puts both views
inside a "0.097-0.151 ft floor" they do not belong to.

WHAT THIS MEASUREMENT CAN AND CANNOT CLAIM
------------------------------------------
bbox_corners_uv is the AABB of 8 projected corners -- a SUPERSET of the
element's true silhouette. A POSITIVE excursion is therefore hard evidence
of a mapping error. A NEGATIVE one is not evidence of anything: it is the
slack between a silhouette and its own AABB, and on a planar element viewed
face-on that slack is most of the bbox. Containment is NOT proof of a
correct mapping.

That asymmetry is why this tool does not report a per-view aggregate over
all elements. A single number mixing a small positive sub-pixel term on
stroked linear elements with large negative AABB slack on planar ones is not
a coherent statistic, and averaging them produces a figure that describes no
element in the run. Section 2's per-category breakdown is the reportable
form; section 1's per-view lines are kept only as the population each
category was drawn from, and are labelled as such.

WHY BOTH MAPPINGS
-----------------
Section 0 replicates the pre-D1 and post-D1 pixel->UV mappings verbatim
(each citing its commit) so a single invocation produces both columns from
provably identical harness code. It is D1's before/after evidence and
nothing else; sections 1 and 2 report the CURRENT (post-D1) mapping only.
The stash protocol remains available as an independent cross-check via
--use-installed-decode, which imports the live _pixel_corner_to_uv from
tools/decode_stage_a_color_id.py and PROBES it to report which of the two
mappings it actually resolved to.

SIGN CONVENTION
---------------
Per edge, positive means the DECODED extent lies OUTSIDE the reference bbox
on that edge; negative means it lies inside it. Every distribution in this
report is signed. A population whose median is negative is not "better" than
one whose median is positive -- it is a different population, mostly AABB
slack, and it is reported separately for that reason.

HOST ONLY, AND WHY LINK IS NOT A SECOND SCOPE
----------------------------------------------
The standalone report this folds in iterated
``near_face_w_map[scope]`` for scope in ("host", "link") and carried a
``scope`` column. Its LINK branch cannot produce a row: it looks the element
up in the pixel extents, and those extents are keyed off
``color_assignment_map``, which is per-element HOST identity. LINK elements
are painted per CATEGORY through ``link_category_color_map`` (see
color_id_buffer.py's _apply_link_category_filters), and a LINK key
("<link_inst_id>:<link_elem_id>") never appears in the HOST map -- which is
why its own six-view CSV is 800 rows, all host, zero link. The branch is not
reproduced here rather than carried as dead code. Recovering LINK identity
from a category blob is tools/link_identity_resolver.py's job, and it is
advisory, never occlusion truth.

TWO DISCRIMINATOR LINES DELIBERATELY NOT PRINTED
-------------------------------------------------
The standalone report printed, per view, ``med_px/dpi=`` and ``med_ft/fpp=``.
``med_ft/fpp`` is ALGEBRAICALLY IDENTICAL to ``med_px``: worst_px is
worst_ft/fpp per element, fpp is constant within a view, and the median
commutes with division by a positive constant. Verified bit-for-bit on all
six views of the 2026-09-17 byColor run. So the second figure is a table
column reprinted under a name that reads like an independent check, beneath
a heading ("DISCRIMINATORS") that reads as though a test ran. None did. Both
lines are gone.

NOT REPORTED, DELIBERATELY
--------------------------
- No view tier, working/sheet status, or importance label. The axis cap
  fires on grid extent, not on sheet membership, and nothing in the pipeline
  records sheet membership at all. cap_applied and effective_dpi are emitted
  as fields; any tier label derived from them here would be an undeclared
  promotion of one to the other.
- No "residual floor" constant. The quantity is a mixture -- see the
  per-category split -- and a single number for it would be fiction.
- No pass/fail verdict against any threshold. Distributions only.
- No H1/H3/H4 discriminator block. ``near_face_w``, ``export_dpi`` and the
  pixel-size columns are CARRIED in the per-element CSV, because dropping
  data a previous tool collected is a silent loss -- but nothing here
  correlates them. Those hypotheses are recorded do-not-investigate.

Standalone: PIL + NumPy + the standard library, plus the shared leaf module
tools/clamp_pad_geometry.py. Imports nothing from vop_interwoven, and
nothing else from tools/ unless --use-installed-decode is passed.
"""
import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.clamp_pad_geometry import clamp_pad_geometry  # noqa: E402


# A summary statistic is printed only for a cell with at least this many
# measured elements. Below it the cell's DISTRIBUTION is still printed in
# full -- every value, not a sample -- and only the median/percentile line is
# withheld.
#
# WHY 8. The statistic being suppressed is a median, and the failure mode is
# an order statistic sitting next to an extreme. At n = 3 the median is the
# 2nd of 3 values and one outlier moves it outright; the current per-view
# data contains cells of n = 3 alongside cells of n = 56, and a per-category
# split reproduces that same spread one level down. Requiring n >= 8 puts at
# least three observations strictly on each side of the median, so neither a
# single value nor a pair can carry it, and it is the smallest n for which
# both quartiles land at interior ranks (2.25 and 6.75).
#
# This is a REPORTING RULE, not a tolerance. Nothing is compared against it
# to decide whether a value is acceptable; it decides only whether a line is
# printed as a summary or as a list. No measurement changes when it changes.
MIN_N_FOR_SUMMARY = 8

EDGES = ("u_lo", "u_hi", "v_lo", "v_hi")
U_EDGES = ("u_lo", "u_hi")
V_EDGES = ("v_lo", "v_hi")


# --------------------------------------------------------------------------
# The two mappings under comparison, replicated verbatim.
# --------------------------------------------------------------------------

def uv_baseline(x, y, bounds_uv, image_w, image_h, _dims):
    """Pre-D1 mapping -- tools/decode_stage_a_color_id.py @ a9051f2:564-568.

        u = xmin + (x / image_w) * (xmax - xmin)
        v = ymax - (y / image_h) * (ymax - ymin)

    Each axis stretched across the FULL image, including Revit's clamp pad.
    """
    xmin, ymin, xmax, ymax = bounds_uv
    u = float(xmin) + (float(x) / float(image_w)) * (float(xmax) - float(xmin))
    v = float(ymax) - (float(y) / float(image_h)) * (float(ymax) - float(ymin))
    return u, v


def uv_patched(x, y, bounds_uv, image_w, image_h, dims):
    """Post-D1 mapping -- _pixel_corner_to_uv() @ cdb0b2a, via the shared helper."""
    xmin, _ymin, _xmax, ymax = (float(c) for c in bounds_uv)
    fpp, pad_x, pad_y = clamp_pad_geometry(
        bounds_uv, image_w, image_h, measured_w=dims[0], measured_h=dims[1])
    return (xmin + (float(x) - pad_x) * fpp,
            ymax - (float(y) - pad_y) * fpp)


# --------------------------------------------------------------------------

def decode_pixel_extents(tiff_path, color_assignment_map):
    """{elem_id_str: (c0, r0, c1, r1)} for every painted element.

    Exact-match RGB -> id decode, same lookup construction as
    decode_stage_a_color_id.decode_ids(). Returns pixel-CORNER extents: an
    element occupying columns c0..c1-1 and rows r0..r1-1 inclusive spans
    corners c0..c1 and r0..r1, which is precisely the extent its traced
    loops would report. No loop tracing is involved -- only the axis-aligned
    extent is under test.
    """
    with Image.open(tiff_path) as img:
        rgb = np.asarray(img.convert("RGB"))
    h, w = rgb.shape[:2]
    packed = (rgb[:, :, 0].astype(np.int32) << 16
              | rgb[:, :, 1].astype(np.int32) << 8
              | rgb[:, :, 2].astype(np.int32))

    # Scan the image ONCE for which palette colors are actually present, then
    # take extents only for those. Iterating the whole palette instead would
    # cost a full pass per assigned element -- hundreds of passes over ~10 Mpx
    # on a real capture, for elements that painted nothing.
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
        mask = (packed == code)
        rows = np.nonzero(mask.any(axis=1))[0]
        cols = np.nonzero(mask.any(axis=0))[0]
        if rows.size and cols.size:
            out[code_to_id[code]] = (int(cols.min()), int(rows.min()),
                                     int(cols.max()) + 1, int(rows.max()) + 1,
                                     int(mask.sum()))
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


def capture_fields(sidecar, fpp):
    """The capture-describing fields this report emits, and nothing derived
    from them beyond what is stated here.

    effective_dpi is the dpi this capture ACHIEVED: view_scale / (12 * fpp).
    It is identically actual_fit_px / paper_fit_in -- substitute
    fpp = (paper_fit_in * view_scale / 12) / actual_fit_px and the view_scale
    cancels -- so it cannot drift from the producer's own figure.

    requested_export_dpi is what was ASKED for. It is not a measurement, and
    reading it as one has already produced a false conclusion once. The three
    ways the two diverge are all in color_id_buffer.py's sizing path: the
    max(64, ...) floor on pre_cap_px, the two-axis cap, and the dimension-
    mismatch backoff.
    """
    res = sidecar.get("resolution") or {}
    view_scale = res.get("view_scale")
    effective_dpi = None
    if view_scale and fpp:
        effective_dpi = float(view_scale) / (12.0 * float(fpp))
    requested = res.get("requested_export_dpi")
    if requested is None:
        # Sidecars written before the field was renamed.
        requested = res.get("export_dpi")
    return {
        "view_scale": view_scale,
        "effective_dpi": effective_dpi,
        "requested_export_dpi": requested,
        "cap_applied": res.get("cap_applied"),
        "dim_check": res.get("dim_check"),
        "actual_w": res.get("actual_w"),
        "actual_h": res.get("actual_h"),
    }


def measure_view(sidecar_path, mappings):
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    bounds = sidecar.get("bounds_xy")
    if not bounds or len(bounds) != 4:
        return {"view": sidecar_path.stem, "skip": "no bounds_xy (crop did not apply)"}
    bounds = tuple(float(v) for v in bounds)

    color_map = sidecar.get("color_assignment_map") or {}
    host_ref = ((sidecar.get("near_face_w_map") or {}).get("host") or {})

    tiff = resolve_tiff(sidecar_path, sidecar)
    extents, image_w, image_h = decode_pixel_extents(tiff, color_map)

    res = sidecar.get("resolution") or {}
    aw, ah = res.get("actual_w"), res.get("actual_h")
    # The producer's recorded export size, or None -- deliberately NOT
    # pre-resolved to this image's own dimensions here. clamp_pad_geometry()
    # already owns that fallback (`float(measured_w) if measured_w else
    # float(image_w)`), and computing it a second time at the call site is the
    # "one quantity derived in two places" shape CLAUDE.md records. The
    # numbers are identical either way; the point is that there is one
    # derivation of them, so the two cannot drift.
    dims = (aw or None, ah or None)
    fpp, pad_x, pad_y = clamp_pad_geometry(
        bounds, image_w, image_h, measured_w=dims[0], measured_h=dims[1])

    xmin, ymin, xmax, ymax = bounds
    rows = []
    no_ref = 0
    for elem_id_str, (c0, r0, c1, r1, npx) in extents.items():
        entry = host_ref.get(elem_id_str) or {}
        corners = entry.get("bbox_corners_uv")
        if not corners:
            no_ref += 1
            continue
        us = [float(c[0]) for c in corners]
        vs = [float(c[1]) for c in corners]
        ref_umin, ref_umax = min(us), max(us)
        ref_vmin, ref_vmax = min(vs), max(vs)

        # CLIPPED means the element's REFERENCE bbox crosses the crop
        # rectangle, so the image physically cannot show its full extent and
        # every edge figure for it is a lower bound rather than a
        # measurement. It is NOT the same test as "the painted pixels reach
        # a border of the image", which is reported alongside it as
        # touches_image_edge: on the 2026-09-17 byColor run Elev 5 has 30
        # clipped against 22 touching, and Section 1 has 25 against 6, so
        # substituting one for the other changes the population materially.
        #
        # TOLERANCE CARRIED, NOT INTRODUCED: the half-pixel epsilon is the
        # one the standalone report used. Whether any element in that run
        # actually sits within half a pixel of the crop boundary -- i.e.
        # whether the epsilon ever changes a classification -- is NOT
        # established; its per-element CSV does not carry the absolute
        # reference coordinates needed to check. See the session report.
        eps = fpp * 0.5
        clipped = (ref_umin < xmin - eps or ref_umax > xmax + eps
                   or ref_vmin < ymin - eps or ref_vmax > ymax + eps)
        touches_image_edge = (c0 == 0 or r0 == 0
                              or c1 >= image_w or r1 >= image_h)

        ref_du = ref_umax - ref_umin
        ref_dv = ref_vmax - ref_vmin
        row = {
            "elem_id": elem_id_str,
            "category": entry.get("category"),
            "near_face_w": entry.get("near_face_w"),
            "clipped": clipped,
            "touches_image_edge": touches_image_edge,
            "px_c0": c0, "px_r0": r0, "px_c1": c1, "px_r1": r1,
            "px_count": npx, "px_w": c1 - c0, "px_h": r1 - r0,
            "ref_du_ft": ref_du, "ref_dv_ft": ref_dv,
            "ref_aspect": (ref_du / ref_dv) if ref_dv > 0 else float("inf"),
        }
        for name, fn in mappings.items():
            # Corner (c0, r0) is the top-left: U min, V max.
            # Corner (c1, r1) is the bottom-right: U max, V min.
            dec_umin, dec_vmax = fn(c0, r0, bounds, image_w, image_h, dims)
            dec_umax, dec_vmin = fn(c1, r1, bounds, image_w, image_h, dims)
            # Signed, per edge. Positive => decoded lies OUTSIDE the ref bbox.
            edges = {
                "u_lo": ref_umin - dec_umin,
                "u_hi": dec_umax - ref_umax,
                "v_lo": ref_vmin - dec_vmin,
                "v_hi": dec_vmax - ref_vmax,
            }
            # worst = the per-element max over all four edges, UNCLAMPED.
            # This is the quantity the six recorded per-view figures are
            # maxima of, and the one Task 1's "which edge supplies the max"
            # is asked about, so it is carried rather than recomputed.
            worst_edge = max(edges, key=edges.get)
            edges["worst"] = edges[worst_edge]
            edges["worst_edge"] = worst_edge
            edges["worst_px"] = edges[worst_edge] / fpp
            row[name] = edges
        rows.append(row)

    return {"view": sidecar_path.stem, "painted": len(extents), "measured": len(rows),
            "no_ref": no_ref, "image": (image_w, image_h), "fpp": fpp,
            "pad_x": pad_x, "pad_y": pad_y, "rows": rows,
            "capture": capture_fields(sidecar, fpp)}


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def _fmt_n(values):
    return "n={0}".format(len(values))


def summarize(values):
    """(n, median, p10, p90, min, max) or None when n is below the floor.

    Returns None -- rather than a smaller or a differently-computed statistic
    -- so a caller cannot accidentally print a summary for a cell that has
    none. The distribution is the caller's to print regardless.
    """
    n = len(values)
    if n < MIN_N_FOR_SUMMARY:
        return None
    s = sorted(values)
    return {
        "n": n,
        "median": statistics.median(s),
        "p10": s[max(0, int(round(0.10 * (n - 1))))],
        "p90": s[min(n - 1, int(round(0.90 * (n - 1))))],
        "min": s[0],
        "max": s[-1],
        # The share of the cell lying OUTSIDE the reference bbox. Reported
        # because the sign is the whole distinction between a mapping error
        # and silhouette-to-AABB slack, and a median alone hides it: a cell
        # can have a negative median and still contain positive excursions.
        "pos_frac": sum(1 for v in s if v > 0.0) / float(n),
    }


def _print_cell(label, values, indent="    "):
    """One distribution. Summary line only when n clears the floor; the
    values themselves either way."""
    if not values:
        print("{0}{1:<34} n=0   (no measured elements)".format(indent, label))
        return
    s = summarize(values)
    if s is None:
        print("{0}{1:<34} n={2:<3} SUMMARY SUPPRESSED (n < {3}); values: {4}".format(
            indent, label, len(values), MIN_N_FOR_SUMMARY,
            " ".join("{0:+.4f}".format(v) for v in sorted(values))))
    else:
        print("{0}{1:<34} n={2:<4} med {3:+.4f}   p10 {4:+.4f}   p90 {5:+.4f}   "
              "min {6:+.4f}   max {7:+.4f} ft   outside {8:.0%}".format(
                  indent, label, s["n"], s["median"], s["p10"], s["p90"],
                  s["min"], s["max"], s["pos_frac"]))


def _edge_values(rows, mapping, edges):
    return [r[mapping][e] for r in rows for e in edges]


def _which_edge_supplies_max(rows, mapping):
    """(edge_name, value, elem_id, category) for the largest excursion.

    This is the question section 1's max answers, and it is reported by EDGE
    NAME rather than as a bare number: a max drawn from u_hi and a max drawn
    from v_lo are not the same measurement, and a report that names only the
    number cannot be compared against one that measured a different edge set.
    """
    best = None
    for r in rows:
        for e in EDGES:
            v = r[mapping][e]
            if best is None or v > best[1]:
                best = (e, v, r["elem_id"], r["category"])
    return best


def report_view_population(results, mapping):
    print("\n" + "=" * 78)
    print("SECTION 1 -- per view. The POPULATION each category cell is drawn from.")
    print("Not a per-view quality figure: each line mixes positive sub-pixel terms")
    print("on stroked linear elements with large negative AABB slack on planar ones.")
    print("=" * 78)
    for r in results:
        cap = r["capture"]
        eff = cap["effective_dpi"]
        req = cap["requested_export_dpi"]
        print("\n{0}".format(r["view"]))
        print("    image {0}x{1} px   fpp {2:.6f} ft/px   pad ({3:.3f}, {4:.3f}) px".format(
            r["image"][0], r["image"][1], r["fpp"], r["pad_x"], r["pad_y"]))
        print("    effective_dpi {0}   requested_export_dpi {1}   cap_applied {2}   "
              "dim_check {3}".format(
                  "{0:.3f}".format(eff) if eff is not None else "n/a",
                  "{0:.3f}".format(float(req)) if req is not None else "n/a",
                  cap["cap_applied"], cap["dim_check"]))
        print("    painted {0}   measured {1}   no_ref {2}".format(
            r["painted"], r["measured"], r["no_ref"]))

        unclipped = [x for x in r["rows"] if not x["clipped"]]
        clipped = [x for x in r["rows"] if x["clipped"]]
        touching = sum(1 for x in r["rows"] if x["touches_image_edge"])
        print("    n_clipped {0}  (REFERENCE bbox crosses the crop, so every edge "
              "figure for it is a lower bound)".format(len(clipped)))
        print("    n_touching_image_edge {0}  (painted pixels reach a border -- a "
              "DIFFERENT population, reported, never substituted)".format(touching))

        for scope_label, scope_rows in (("unclipped", unclipped), ("clipped", clipped)):
            if not scope_rows:
                continue
            print("  {0}:".format(scope_label))
            _print_cell("worst (max over all 4 edges)",
                        [x[mapping]["worst"] for x in scope_rows])
            _print_cell("U edges (u_lo, u_hi)", _edge_values(scope_rows, mapping, U_EDGES))
            _print_cell("V edges (v_lo, v_hi)", _edge_values(scope_rows, mapping, V_EDGES))
            for e in EDGES:
                _print_cell("edge {0}".format(e), [x[mapping][e] for x in scope_rows],
                            indent="      ")
            top = _which_edge_supplies_max(scope_rows, mapping)
            if top is not None:
                axis = "U" if top[0] in U_EDGES else "V"
                print("      MAX {0:+.4f} ft on edge {1} ({2} axis), element {3}, "
                      "category {4}".format(top[1], top[0], axis, top[2], top[3]))
                # Named because a max drawn from a U edge and a max drawn
                # from a V edge are not the same measurement, and a figure
                # reported without its edge cannot be compared against one
                # that measured a different edge set.


def report_categories(results, mapping):
    print("\n" + "=" * 78)
    print("SECTION 2 -- per category, U and V reported separately.")
    print("This is the reportable form. A category is a population of like")
    print("silhouette-to-AABB relationships; a view is not.")
    print("=" * 78)
    by_cat = {}
    for r in results:
        for row in r["rows"]:
            cat = row["category"] or "(uncategorized)"
            by_cat.setdefault(cat, {"unclipped": [], "clipped": []})
            by_cat[cat]["clipped" if row["clipped"] else "unclipped"].append(row)

    for cat in sorted(by_cat):
        cells = by_cat[cat]
        total = len(cells["unclipped"]) + len(cells["clipped"])
        print("\n{0}   (elements n={1}; unclipped {2}, clipped {3})".format(
            cat, total, len(cells["unclipped"]), len(cells["clipped"])))
        for scope_label in ("unclipped", "clipped"):
            scope_rows = cells[scope_label]
            if not scope_rows:
                continue
            print("  {0}:".format(scope_label))
            _print_cell("worst (max over all 4 edges)",
                        [x[mapping]["worst"] for x in scope_rows])
            _print_cell("U edges (u_lo, u_hi)", _edge_values(scope_rows, mapping, U_EDGES))
            _print_cell("V edges (v_lo, v_hi)", _edge_values(scope_rows, mapping, V_EDGES))
            for e in EDGES:
                _print_cell("edge {0}".format(e), [x[mapping][e] for x in scope_rows],
                            indent="      ")


def report_mapping_comparison(results, names):
    print("\n" + "=" * 78)
    print("SECTION 0 -- pre-D1 vs post-D1 mapping. D1's before/after evidence only.")
    print("Sections 1 and 2 report the post-D1 mapping alone.")
    print("=" * 78)
    head = "%-26s %8s %9s %9s" % ("view", "measured", "pad_x", "pad_y")
    for n in names:
        head += " | %-13s %-13s" % (n[:4] + " maxU", n[:4] + " maxV")
    print(head)
    for r in results:
        line = "%-26s %8d %9.3f %9.3f" % (
            r["view"][:26], r["measured"], r["pad_x"], r["pad_y"])
        for n in names:
            us = _edge_values(r["rows"], n, U_EDGES) or [float("nan")]
            vs = _edge_values(r["rows"], n, V_EDGES) or [float("nan")]
            line += " | %13.4f %13.4f" % (max(us), max(vs))
        print(line)


def write_per_element_csv(path, results, names):
    fields = ["view", "elem_id", "category", "near_face_w",
              "clipped", "touches_image_edge",
              "px_c0", "px_r0", "px_c1", "px_r1", "px_count", "px_w", "px_h",
              "ref_du_ft", "ref_dv_ft", "ref_aspect",
              "fpp", "effective_dpi", "requested_export_dpi", "cap_applied",
              "view_scale", "pad_x", "pad_y"]
    for n in names:
        fields += ["{0}_{1}".format(n, e) for e in EDGES]
        fields += ["{0}_worst_ft".format(n), "{0}_worst_px".format(n),
                   "{0}_worst_edge".format(n)]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in results:
            cap = r["capture"]
            for row in r["rows"]:
                rec = {
                    "view": r["view"], "elem_id": row["elem_id"],
                    "category": row["category"], "near_face_w": row["near_face_w"],
                    "clipped": int(row["clipped"]),
                    "touches_image_edge": int(row["touches_image_edge"]),
                    "px_c0": row["px_c0"], "px_r0": row["px_r0"],
                    "px_c1": row["px_c1"], "px_r1": row["px_r1"],
                    "px_count": row["px_count"], "px_w": row["px_w"],
                    "px_h": row["px_h"], "ref_du_ft": row["ref_du_ft"],
                    "ref_dv_ft": row["ref_dv_ft"], "ref_aspect": row["ref_aspect"],
                    "fpp": r["fpp"], "effective_dpi": cap["effective_dpi"],
                    "requested_export_dpi": cap["requested_export_dpi"],
                    "cap_applied": cap["cap_applied"], "view_scale": cap["view_scale"],
                    "pad_x": r["pad_x"], "pad_y": r["pad_y"],
                }
                for n in names:
                    for e in EDGES:
                        rec["{0}_{1}".format(n, e)] = row[n][e]
                    rec["{0}_worst_ft".format(n)] = row[n]["worst"]
                    rec["{0}_worst_px".format(n)] = row[n]["worst_px"]
                    rec["{0}_worst_edge".format(n)] = row[n]["worst_edge"]
                w.writerow(rec)
    print("\nper-element CSV written: {0}".format(path))


def probe_installed_decode():
    """Report which mapping tools/decode_stage_a_color_id.py currently implements.

    Used under the stash protocol so the measured commit is never assumed.
    """
    from tools import decode_stage_a_color_id as dsc
    bounds = (0.0, 0.0, 200.0, 8.0)   # 25:1 crop -> the clamp would pad it
    w, h = 10000, 1000
    got = dsc._pixel_corner_to_uv(0, 0, bounds, w, h)
    exp_base = uv_baseline(0, 0, bounds, w, h, (w, h))
    exp_patch = uv_patched(0, 0, bounds, w, h, (w, h))
    if abs(got[1] - exp_patch[1]) < 1e-9:
        return dsc, "patched (post-D1)"
    if abs(got[1] - exp_base[1]) < 1e-9:
        return dsc, "baseline (pre-D1)"
    return dsc, "UNRECOGNISED (matches neither replica)"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sidecar_dir", help="a byColor run's color_id_buffer/ directory")
    ap.add_argument("--per-element-csv", default=None,
                    help="also write every measured element's four signed edge "
                         "excursions to this CSV")
    ap.add_argument("--use-installed-decode", action="store_true",
                    help="also measure through the live tools/decode_stage_a_color_id.py "
                         "(the stash-protocol cross-check); reports which mapping it is")
    ns = ap.parse_args(argv)

    mappings = {"baseline": uv_baseline, "patched": uv_patched}
    if ns.use_installed_decode:
        dsc, which = probe_installed_decode()
        print("installed tools/decode_stage_a_color_id.py implements: {0}\n".format(which))

        def uv_installed(x, y, bounds_uv, image_w, image_h, _dims):
            return dsc._pixel_corner_to_uv(x, y, bounds_uv, image_w, image_h)

        mappings["installed"] = uv_installed

    paths = sorted(p for p in Path(ns.sidecar_dir).rglob("*.json")
                   if not p.name.endswith(".decoded.json"))
    if not paths:
        print("no sidecars under {0}".format(ns.sidecar_dir))
        return 1

    names = list(mappings)
    results = []
    for sp in paths:
        try:
            r = measure_view(sp, mappings)
        except (OSError, ValueError, KeyError, FileNotFoundError) as ex:
            print("%-26s  ERROR %s: %s" % (sp.stem[:26], type(ex).__name__, ex))
            continue
        if r.get("skip"):
            print("%-26s  %s" % (r["view"][:26], r["skip"]))
            continue
        results.append(r)

    if not results:
        print("\nNO MEASURABLE VIEWS. Q4 says the reference bboxes exist only in the "
              "byColor run; if this WAS byColor, stop and report rather than reaching "
              "for byGeom.")
        return 1

    report_mapping_comparison(results, names)
    report_view_population(results, "patched")
    report_categories(results, "patched")

    total = sum(r["measured"] for r in results)
    no_ref = sum(r["no_ref"] for r in results)
    print("\nTOTALS  measured={0} elements across {1} views, no_ref={2} "
          "(unmeasurable: bbox_corners_uv null)".format(total, len(results), no_ref))
    print("Summary statistics are suppressed below n={0}; see the module docstring "
          "for why, and note it is a reporting rule, not a tolerance.".format(
              MIN_N_FOR_SUMMARY))

    if ns.per_element_csv:
        write_per_element_csv(ns.per_element_csv, results, names)
    return 0


if __name__ == "__main__":
    sys.exit(main())
