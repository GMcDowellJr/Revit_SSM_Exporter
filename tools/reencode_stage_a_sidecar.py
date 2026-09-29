#!/usr/bin/env python3
"""Re-encode an archive Stage A sidecar into the current writer shape (C1-C6).

For the offline gate: take a sidecar written before C1-C6 (e.g. a
pipeline_0928_0953 model sidecar), write it in the shape the writer produces
now, and report the bytes before and after. Decoding and registering both
files must then give identical element pixel counts and transforms, because
every dropped field is exactly derivable from what remains
(tools/stage_a_sidecar_shapes.py rebuilds them).

What it cannot add: ``bbox_transform`` (C3) needs the bbox's Transform,
which an archive sidecar never recorded. Every entry is written without it,
exactly as the writer does for an axis-aligned bbox -- so a re-encoded file
UNDER-reports rotation. It is a size/compatibility check, not a capture.

    python tools/reencode_stage_a_sidecar.py <old.json> [<out.json>]

Uses the WRITER's own C2/C4 helpers (vop_interwoven.color_id_buffer), not a
copy of them, so the re-encoded shape cannot drift from the real one.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.stage_a_sidecar_shapes import frame_record  # noqa: E402
from vop_interwoven.color_id_buffer import (  # noqa: E402
    _plain_or_state, _round_ft, _round_geometry,
)

_C1_DROPPED = ("import_symbol_state", "view_specific_state")
_C6_DROPPED = ("requested_export_dpi", "effective_export_dpi", "requested_axis",
               "requested_fpp_ft")


def reencode_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """One near_face_w_map entry, old shape -> C1-C4 shape."""
    out = dict(entry)
    for key in _C1_DROPPED:
        out.pop(key, None)
    if "source" in out:
        out["source"] = _plain_or_state(out["source"])
    cat_state = out.pop("category_state", None)
    if isinstance(cat_state, dict) and cat_state.get("state") != "value":
        out["category_state"] = cat_state
    if "bbox_corners_uv" in out:
        corners = out.pop("bbox_corners_uv")
        if corners:
            us = [c[0] for c in corners]
            vs = [c[1] for c in corners]
            out["uv_rect"] = _round_geometry([min(us), min(vs), max(us), max(vs)])
        else:
            out["uv_rect"] = {"state": "unavailable",
                              "reason": "recorded as null in the archive sidecar"}
    if "bbox_3d" in out:
        out["bbox_3d"] = _round_geometry(_plain_or_state(out["bbox_3d"]))
    if "near_face_w" in out:
        out["near_face_w"] = _round_ft(out["near_face_w"])
    return out


def reencode_frame(sidecar: dict[str, Any]) -> dict[str, Any]:
    """The C5 frame record with C6's requested values removed."""
    frame = dict(frame_record(sidecar))
    for key in _C6_DROPPED:
        frame.pop(key, None)
    if frame.get("dim_check_attempts"):
        frame.pop("requested_pixel_size", None)
    if "crop_uv" in frame and frame.get("crop_snapped_uv") == frame.get("crop_uv"):
        frame.pop("crop_snapped_uv", None)
    if "frame_uv" in frame and frame.get("raster_bounds_uv") == frame.get("frame_uv"):
        frame.pop("raster_bounds_uv", None)
    return frame


def reencode(sidecar: dict[str, Any]) -> dict[str, Any]:
    if isinstance(sidecar.get("frame"), dict):
        return sidecar                    # already the current shape
    out = dict(sidecar)
    out["frame"] = reencode_frame(sidecar)
    for key in ("resolution", "export_frame", "bounds_xy"):
        out.pop(key, None)
    nfw = sidecar.get("near_face_w_map")
    if isinstance(nfw, dict):
        out["near_face_w_map"] = {
            bucket: {k: reencode_entry(v) for k, v in (nfw.get(bucket) or {}).items()}
            for bucket in nfw}
    return out


def _dump(obj) -> str:
    # The writer's own serialisation (color_id_buffer: indent=2, sort_keys).
    return json.dumps(obj, indent=2, sort_keys=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("sidecar")
    ap.add_argument("out", nargs="?")
    args = ap.parse_args(argv)
    src = Path(args.sidecar)
    old = json.loads(src.read_text(encoding="utf-8"))
    new = reencode(old)
    dst = Path(args.out) if args.out else src.with_name(src.stem + ".reencoded.json")
    dst.write_text(_dump(new), encoding="utf-8")
    before, after = len(_dump(old).encode()), len(_dump(new).encode())
    print("{0}: {1} B -> {2} B ({3:+.1f} %), written {4}".format(
        src.name, before, after, 100.0 * (after - before) / before, dst))
    return 0


if __name__ == "__main__":
    sys.exit(main())
