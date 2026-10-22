"""A synthetic Stage A run of any size, for the analysis layer's timing and
byte-equivalence gates. Not a test module (no ``test_`` prefix): the gates
import it, and it runs on its own:

    python -m tests.stage_a_bench <out run dir> [--size W H] [--views N] [--seed S]

Each view is a model + annotation capture pair in the shape production writes
(the shared registration fixture's shape, test_register_stage_a_annotation,
scaled): twelve registration ticks in both captures, the annotation pass on
its own lattice (a different scale and offset), many element rectangles
outlined and filled, DWG and link sources, black "text" inside annotation
bboxes, filled regions. Deterministic for a seed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vop_interwoven.stage_a_registered_capture import _registration_payload  # noqa: E402
from vop_interwoven.stage_a_registration import (  # noqa: E402
    MARK_ARM_PX, MARK_COLOUR, registration_mark_segments)

VIEW_SCALE = 96.0
CELL_IN = 0.125                     # 1 ft cells at 1/8" = 1'-0"
# Pixel density is per view, not a constant: Greg's capped plans
# (2025-10-25__20261012T122748) are at ~2.56 px per cell, uncapped views
# finer. Per-pixel costs follow the image size; per-cell costs (element x
# cell rows, dense arrays, derive) follow the cell count, which for one
# image size grows as 1 / (px per cell)^2. The default is the capped
# density; a view may give its own. The annotation pass is ~0.9x as fine.
MODEL_PX_PER_FT = 2.56
ANNO_OVER_MODEL = 0.9


def _integrity():
    return {"status": "value", "capture_faults": [], "rolled_back": True,
            "restore_failures": 0, "marks_still_in_project": 0, "paint_failures": 0}


def _palette(n, rng, avoid):
    """``n`` distinct non-white, non-black colours, none in ``avoid``."""
    out, seen = [], set(avoid) | {(255, 255, 255), (0, 0, 0), tuple(MARK_COLOUR)}
    while len(out) < n:
        c = tuple(int(x) for x in rng.integers(0, 230, 3))
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _rects(rng, n, w_ft, h_ft, u0, v0, min_ft, max_ft):
    out = []
    for _ in range(n):
        dw, dh = rng.uniform(min_ft, max_ft, 2)
        u = rng.uniform(u0, u0 + w_ft - dw)
        v = rng.uniform(v0, v0 + h_ft - dh)
        out.append((u, v, u + dw, v + dh))
    return out


def _paint(img, rect, colour, to_x, to_y, outline_only=False, width=2):
    x0, x1 = int(round(to_x(rect[0]))), int(round(to_x(rect[2])))
    y0, y1 = int(round(to_y(rect[3]))), int(round(to_y(rect[1])))
    x0, y0 = max(x0, 0), max(y0, 0)
    x1, y1 = min(x1, img.shape[1]), min(y1, img.shape[0])
    if x1 <= x0 or y1 <= y0:
        return
    if outline_only:
        img[y0:y0 + width, x0:x1] = colour
        img[max(y1 - width, y0):y1, x0:x1] = colour
        img[y0:y1, x0:x0 + width] = colour
        img[y0:y1, max(x1 - width, x0):x1] = colour
    else:
        img[y0:y1, x0:x1] = colour


def _tick(img, seg, colour, to_x, to_y, thickness=2):
    if seg["orientation"] == "horizontal":
        yc = to_y(seg["level_uv"])
        xs = sorted(to_x(u) for u in seg["span_uv"])
        img[int(round(yc - thickness / 2.0)):int(round(yc + thickness / 2.0)),
            int(round(xs[0])):int(round(xs[1]))] = colour
    else:
        xc = to_x(seg["level_uv"])
        ys = sorted(to_y(v) for v in seg["span_uv"])
        img[int(round(ys[0])):int(round(ys[1])),
            int(round(xc - thickness / 2.0)):int(round(xc + thickness / 2.0))] = colour


def write_view(cap, n, size=(700, 520), seed=0, model_elements=None,
               anno_elements=None, px_per_ft=MODEL_PX_PER_FT):
    """View ``V_<n>`` in the capture folder ``cap``; returns its model sidecar."""
    rng = np.random.default_rng(seed + 1000 * n)
    mw, mh = size
    px_ft = float(px_per_ft)
    bounds = (-1.0, -2.0, -1.0 + mw / px_ft, -2.0 + mh / px_ft)
    w_ft, h_ft = bounds[2] - bounds[0], bounds[3] - bounds[1]
    # Annotation lattice: its own scale and offset, the crop inset.
    a_px = ANNO_OVER_MODEL * px_ft
    crop = (bounds[0] + 0.05 * w_ft, bounds[1] + 0.05 * h_ft,
            bounds[2] - 0.05 * w_ft, bounds[3] - 0.05 * h_ft)
    aw = int((crop[2] - crop[0]) * a_px) + 60
    ah = int((crop[3] - crop[1]) * a_px) + 60
    a_bu, a_bv = 30.0 - a_px * crop[0], 30.0 + a_px * crop[3]

    def mx(u):
        return px_ft * (u - bounds[0])

    def my(v):
        return -px_ft * (v - bounds[3])

    def ax(u):
        return a_px * u + a_bu

    def ay(v):
        return -a_px * v + a_bv

    layout = registration_mark_segments(crop, 1.0 / a_px)
    assert layout["state"] == "value", layout
    marks = [dict(seg, id=900000 + i) for i, seg in enumerate(layout["segments"])]
    area = (mw * mh) / 1e6
    n_model = model_elements or max(20, int(60 * area))
    n_anno = anno_elements or max(10, int(25 * area))
    link_colours = {"Walls": (138, 30, 200), "Floors": (20, 140, 220)}
    model_colours = _palette(n_model, rng, link_colours.values())
    anno_colours = _palette(n_anno, rng, set(model_colours) | set(link_colours.values()))

    model = np.full((mh, mw, 3), 255, dtype=np.uint8)
    host_map = {}
    for k, rect in enumerate(_rects(rng, n_model, w_ft, h_ft, bounds[0], bounds[1],
                                    0.5, max(2.0, w_ft / 15.0))):
        eid = 1000 + k
        _paint(model, rect, model_colours[k], mx, my, outline_only=(k % 3 == 0))
        host_map[str(eid)] = {"source": "DWG" if k % 7 == 0 else "HOST",
                              "category": ["Walls", "Doors", "Floors", "Furniture"][k % 4]}
    for name, colour in link_colours.items():
        for rect in _rects(rng, 3, w_ft, h_ft, bounds[0], bounds[1], 1.0, 4.0):
            _paint(model, rect, colour, mx, my, outline_only=True)
    for m in marks:
        _tick(model, m, MARK_COLOUR, mx, my)

    anno = np.full((ah, aw, 3), 255, dtype=np.uint8)
    bbox_map = {}
    for k, rect in enumerate(_rects(rng, n_anno, crop[2] - crop[0], crop[3] - crop[1],
                                    crop[0], crop[1], w_ft / 400.0, w_ft / 40.0)):
        eid = 50000 + k
        filled = k % 9 == 0
        _paint(anno, rect, anno_colours[k], ax, ay, outline_only=not filled, width=1)
        # Black "text" inside the bbox: a sparse speckle.
        x0, x1 = int(ax(rect[0])) + 2, int(ax(rect[2])) - 2
        y0, y1 = int(ay(rect[3])) + 2, int(ay(rect[1])) - 2
        if x1 > x0 and y1 > y0:
            sub = anno[y0:y1, x0:x1]
            speck = rng.random(sub.shape[:2]) < 0.08
            sub[speck & np.all(sub == 255, axis=2)] = 0
        bbox_map[str(eid)] = {"category": ["Text Notes", "Dimensions", "Detail Items",
                                           "Generic Annotations"][k % 4],
                              "element_class": "FilledRegion" if filled else "TextNote",
                              "bbox_uv": {"state": "value", "value": list(rect)}}
    for m in marks:
        _tick(anno, m, MARK_COLOUR, ax, ay)

    model_tiff, anno_tiff = cap / "V_{0}.tiff".format(n), cap / "V_{0}_anno.tiff".format(n)
    Image.fromarray(model).save(str(model_tiff), format="TIFF", compression="tiff_lzw")
    Image.fromarray(anno).save(str(anno_tiff), format="TIFF", compression="tiff_lzw")
    record = {"marks": {"created": marks, "layout": {"arm_px": MARK_ARM_PX}},
              "mark_reference_source": "bench", "faults": [], "restore": {}}
    model_side = {
        "view_id": n, "tiff_path": str(model_tiff), "bounds_xy": list(bounds),
        "resolution": {"pixel_size": mw, "requested_pixel_size": mw,
                       "view_scale": VIEW_SCALE},
        "color_assignment_map": dict((str(1000 + k), list(c))
                                     for k, c in enumerate(model_colours)),
        "link_category_color_map": dict((k, list(v)) for k, v in link_colours.items()),
        "near_face_w_map": {"host": host_map},
        "applied_display_style": "FlatColors", "applied_smooth_edges": False,
        "capture_integrity": _integrity(),
        "registration_marks": _registration_payload("model", record,
                                                    shared_colour=MARK_COLOUR)}
    anno_side = {
        "view_id": n, "pass": "annotation", "tiff_path": str(anno_tiff),
        "model_pass_tiff_path": str(model_tiff),
        "resolution": {"pixel_size": aw, "requested_pixel_size": aw,
                       "view_scale": VIEW_SCALE},
        "color_assignment_map": dict((str(50000 + k), list(c))
                                     for k, c in enumerate(anno_colours)),
        "annotation_bbox_map": bbox_map, "annotation_bbox_status": {"status": "value"},
        "applied_display_style": "FlatColors", "applied_smooth_edges": False,
        "capture_integrity": _integrity(),
        "registration_marks": _registration_payload("annotation", record,
                                                    shared_colour=MARK_COLOUR)}
    model_path = cap / "V_{0}.json".format(n)
    model_path.write_text(json.dumps(model_side))
    (cap / "V_{0}_anno.json".format(n)).write_text(json.dumps(anno_side))
    return model_path


def write_run(run, sizes, seed=0, run_id="RUN_BENCH", date="2026-10-07"):
    """A finalized v1 run at ``run`` (``<date>__<run_id>`` by convention),
    one view per entry of ``sizes``: ``(w, h)`` or ``(w, h, px_per_ft)``."""
    run = Path(run)
    cap = run / "color_id_buffer"
    cap.mkdir(parents=True)
    views = []
    for n, size in enumerate(sizes, start=1):
        write_view(cap, n, tuple(size[:2]), seed,
                   px_per_ft=size[2] if len(size) > 2 else MODEL_PX_PER_FT)
        views.append({"view_id": n, "view_name": "V {0}".format(n),
                      "capture_status": "success", "capture_failure_reason": ""})
    meta = {"schema": "vop.run_meta.v1", "run_id": run_id, "git_commit": "bench",
            "finalized": True, "views_requested": [v["view_id"] for v in views],
            "views": views, "date": date, "config": {"cell_size_paper_in": CELL_IN}}
    (run / "run_meta.json").write_text(json.dumps(meta))
    (run / "views_core_{0}.csv".format(date)).write_text(
        "RunId,ViewId,ViewName,ElapsedSec\n" + "".join(
            "{0},{1},V {1},1.0\n".format(run_id, v["view_id"]) for v in views))
    return run


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run")
    ap.add_argument("--size", type=float, nargs="+", action="append",
                    metavar="W H [PX_PER_FT]")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    write_run(args.run, [(int(s[0]), int(s[1])) + tuple(s[2:3])
                     for s in (args.size or [(700, 520)])], args.seed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
