#!/usr/bin/env python3
"""Fill in the Phase 1 Q4 table and the Phase 2 G2/G3/G7 gates from a real run.

The byColor/byGeom runs are not in this repository, so the Phase 1 findings
and the D1 gate tests had to be answered from code and from synthetic
fixtures. This script produces the real numbers on a machine that has a run.

    python tools/notes/frame_delta_report.py <run>/color_id_buffer/
    python tools/notes/frame_delta_report.py <run>/color_id_buffer/ \
        --colorid-to-occupancy /path/to/colorid_to_occupancy.py

Reports, per sidecar:
  Q4  grid extent (paper_fit_in * scale / 12) vs the crop the TIFF images,
      the delta, the cell size, and whether the delta is pure ceil() slack.
  G3  the clamp pad recovered on that view.
  G7  feet_per_pixel from this repo's decode vs colorid_to_occupancy.py's
      frame_from_capture(), when that file is supplied.

Read-only. Writes nothing, decodes no TIFF pixels.
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools import decode_stage_a_color_id as dsc  # noqa: E402


def _image_dims(sidecar, sidecar_path):
    res = sidecar.get("resolution") or {}
    if res.get("actual_w") and res.get("actual_h"):
        return int(res["actual_w"]), int(res["actual_h"]), "sidecar_actual_dims"
    try:
        from PIL import Image
        with Image.open(dsc._resolve_tiff_path(sidecar_path, sidecar)) as img:
            return img.width, img.height, "decoded_image_dims"
    except Exception as ex:
        raise RuntimeError("no dimensions for {0}: {1}".format(sidecar_path.name, ex))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sidecar_dir", help="a run's color_id_buffer/ directory")
    ap.add_argument("--colorid-to-occupancy", default=None,
                    help="path to colorid_to_occupancy.py, to run the G7 comparison")
    ns = ap.parse_args(argv)

    c2o = None
    if ns.colorid_to_occupancy:
        spec = importlib.util.spec_from_file_location("c2o", ns.colorid_to_occupancy)
        c2o = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(c2o)

    paths = sorted(p for p in Path(ns.sidecar_dir).rglob("*.json")
                   if not p.name.endswith(".decoded.json"))
    if not paths:
        print("no sidecars under {0}".format(ns.sidecar_dir))
        return 1

    hdr = ("view", "fit", "grid_ft", "crop_ft", "delta_ft", "delta_%", "cell_ft",
           "offset0", "roundup", "pad_x", "pad_y", "fpp", "g7")
    print("%-26s %-6s %10s %10s %9s %8s %8s %7s %8s %9s %9s %14s %10s" % hdr)

    worst_g7 = 0.0
    g7_disagree = []
    for sp in paths:
        try:
            sidecar = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, ValueError) as ex:
            print("%-26s  unreadable: %s" % (sp.stem[:26], ex))
            continue
        bounds = sidecar.get("bounds_xy")
        res = sidecar.get("resolution") or {}
        if not bounds or len(bounds) != 4:
            print("%-26s  no bounds_xy (crop did not apply)" % sp.stem[:26])
            continue

        xmin, ymin, xmax, ymax = (float(v) for v in bounds)
        crop_u, crop_v = xmax - xmin, ymax - ymin
        axis = res.get("requested_axis") or "width"
        crop_fit = crop_v if axis == "height" else crop_u

        paper_fit_in = res.get("paper_fit_in")
        scale = res.get("view_scale")
        grid_ft = (float(paper_fit_in) * float(scale) / 12.0
                   if paper_fit_in and scale else float("nan"))
        delta = grid_ft - crop_fit
        pct = 100.0 * delta / crop_fit if crop_fit else float("nan")

        # cell_size_ft_effective: grid extent / the grid's own cell count on
        # the fitted axis, which color_id_buffer.py records as backoff_floor_px
        # (= raster.W or raster.H -- color_id_buffer.py:1735-1740, :2683).
        n_cells = res.get("backoff_floor_px")
        cell = grid_ft / float(n_cells) if n_cells else float("nan")

        off = sidecar.get("model_crop_offset_uv") or [0.0, 0.0, 0.0, 0.0]
        offset_zero = all(abs(float(v)) < 1e-9 for v in off)
        # Pure ceil() slack iff the crop IS the grid's own rectangle (zero
        # offset, so no annotation/buffer term) and the slack is under one cell.
        pure = offset_zero and (0.0 <= delta < cell) if cell == cell else False

        w, h, _basis = _image_dims(sidecar, sp)
        fpp, pad_x, pad_y = dsc._clamp_pad_geometry(
            (xmin, ymin, xmax, ymax), w, h, measured_w=w, measured_h=h)

        g7 = "-"
        if c2o is not None:
            try:
                c_fpp = c2o.frame_from_capture(sidecar, w, h)[0]
                d = abs(fpp - c_fpp)
                worst_g7 = max(worst_g7, d)
                g7 = "%.1e" % d
                if d > 1e-6:
                    g7_disagree.append((sp.stem, fpp, c_fpp, d))
            except Exception as ex:
                g7 = type(ex).__name__

        print("%-26s %-6s %10.3f %10.3f %9.3f %8.3f %8.4f %7s %8s %9.3f %9.3f %14.9f %10s" % (
            sp.stem[:26], axis, grid_ft, crop_fit, delta, pct, cell,
            "yes" if offset_zero else "NO", "yes" if pure else "NO",
            pad_x, pad_y, fpp, g7))

    if c2o is not None:
        print("\nG7: worst |delta| = %.3e ft/px; %d view(s) disagree beyond 1e-6"
              % (worst_g7, len(g7_disagree)))
        for stem, a, b, d in g7_disagree:
            print("  DISAGREE %s: decode=%.9f colorid_to_occupancy=%.9f delta=%.9f"
                  % (stem, a, b, d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
