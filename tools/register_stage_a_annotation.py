#!/usr/bin/env python3
"""Put a registered Stage A annotation capture onto the model pass's lattice.

With ``Config.color_id_buffer_registered_capture`` on, the annotation pass
keeps the view's authored crop, so its export fits its own extent and its
pixels are NOT the model lattice (6-15 % off in scale wherever the view has an
active crop). Both captures carry the same twelve registration ticks, recorded
in each sidecar under ``registration_marks``. This tool:

  1. fits the ticks in BOTH captures (``tools/registration_marks.py`` -- the
     one implementation, shared with the probe analyzer and the decoder);
  2. composes the two fits into annotation pixel -> model pixel;
  3. SUBTRACTS the marks from the annotation capture -- every pixel of a tick
     colour becomes white -- and resamples it onto the model image's pixel
     grid, NEAREST NEIGHBOUR: colour IDs are identities, and a blended pixel
     would be a colour no element owns;
  4. writes ``<annotation stem>.registered.tiff`` and, LAST, a
     ``<annotation stem>.registered.json`` record that names the TIFF's hash,
     so the record is never written ahead of the image it describes
     (CLAUDE.md defect class 4).

THE TARGET IS THE MODEL CAPTURE'S MARKS, not its recorded lattice. Both ends
are then measured. The model's recorded lattice (``bounds_xy`` at its pixel
count) is a premise that ignores ExportImage's aspect-clamp pad; the record
reports how far the model's marks sit from it, as a number, and does not
choose between them on the strength of it.

The model capture is not rewritten here: the decoder subtracts its marks
itself (``tools/decode_stage_a_color_id.py``), from the same module.

REFUSALS -- a refused view gets a record with ``status: "refused"`` and its
reasons, and no TIFF (a stale one from an earlier run is removed, so a TIFF
beside a record is always that record's):
  * a sidecar has no ``registration_marks``, or one of an unknown schema;
  * the two sidecars' marks are not the same ticks (key, id, UV);
  * either capture's fit is unavailable -- above all an axis with ticks at
    fewer than two levels, which fixes no scale. That is refused, never
    fitted through one level and never replaced by the recorded lattice;
  * the composed transform mirrors or collapses an axis.

LOSSES ARE COUNTED, NOT HIDDEN: annotation ink that maps outside the model
image, model pixels no annotation pixel covers, and colours present before the
resample and absent after it (a line thinner than the resample step).

USAGE
-----
    python tools/register_stage_a_annotation.py <View_123_anno.json> [...]
    python tools/register_stage_a_annotation.py <color_id_buffer dir>/
    python tools/register_stage_a_annotation.py <anno.json> --model <View_123.json>

Exit 0 when every capture registered, 1 when any was refused, 2 when an input
could not be read at all.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import registration_marks as rm  # noqa: E402

Image.MAX_IMAGE_PIXELS = None

TOOL_VERSION = "1.0.0"
SCHEMA = "vop.stage_a.registered_annotation.v1"
ANNO_SUFFIX = "_anno"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def output_paths(anno_sidecar_path):
    p = Path(anno_sidecar_path)
    return (p.with_name(p.stem + ".registered.tiff"),
            p.with_name(p.stem + ".registered.json"))


def model_sidecar_for(anno_sidecar_path, anno_sidecar):
    """The model pass's sidecar: named by the annotation sidecar's own
    ``model_pass_tiff_path`` where that exists, else the ``_anno`` sibling."""
    recorded = anno_sidecar.get("model_pass_tiff_path")
    if recorded:
        candidate = Path(recorded).with_suffix(".json")
        if candidate.exists():
            return candidate
    p = Path(anno_sidecar_path)
    if p.stem.endswith(ANNO_SUFFIX):
        return p.with_name(p.stem[:-len(ANNO_SUFFIX)] + ".json")
    return None


def resolve_tiff(sidecar_path, sidecar):
    recorded = sidecar.get("tiff_path")
    if recorded and Path(recorded).exists():
        return Path(recorded)
    for suffix in (".tiff", ".tif"):
        candidate = Path(sidecar_path).with_suffix(suffix)
        if candidate.exists():
            return candidate
    raise FileNotFoundError("no TIFF for {0}: recorded tiff_path={1!r} does not "
                            "exist and no sibling .tiff/.tif".format(
                                sidecar_path, recorded))


def resample_onto(source, transform, out_w, out_h):
    """``source`` (H x W x 3) resampled onto an ``out_w`` x ``out_h`` grid.

    Nearest neighbour, through the inverse map at each OUTPUT pixel's centre:
    model pixel ``j`` has centre ``j + 0.5``; the annotation position there is
    ``(j + 0.5 - offset) / scale``, and the pixel containing it is its floor.
    Separable, because the transform has no rotation or shear. Output pixels
    whose preimage falls off the source are white, and are counted.
    """
    src_h, src_w = source.shape[0], source.shape[1]
    xs = np.floor((np.arange(out_w) + 0.5 - transform["offset_x"])
                  / transform["scale_x"]).astype(np.int64)
    ys = np.floor((np.arange(out_h) + 0.5 - transform["offset_y"])
                  / transform["scale_y"]).astype(np.int64)
    col_ok = (xs >= 0) & (xs < src_w)
    row_ok = (ys >= 0) & (ys < src_h)
    out = np.full((out_h, out_w, 3), 255, dtype=source.dtype)
    out[np.ix_(row_ok, col_ok)] = source[np.ix_(ys[row_ok], xs[col_ok])]
    uncovered = int(out_w * out_h - int(row_ok.sum()) * int(col_ok.sum()))
    return out, uncovered


def _ink(pixels):
    return np.any(pixels != 255, axis=2)


def _colour_set(pixels, mask):
    return set(np.unique(rm.pack_pixels(pixels)[mask]).tolist())


def _ink_outside(ink, transform, out_w, out_h):
    """Annotation ink pixels whose centres land outside the model image."""
    ys, xs = np.nonzero(ink)
    xm = transform["scale_x"] * (xs + 0.5) + transform["offset_x"]
    ym = transform["scale_y"] * (ys + 0.5) + transform["offset_y"]
    return int(np.count_nonzero((xm < 0) | (xm >= out_w) | (ym < 0) | (ym >= out_h)))


def _fit_summary(fit):
    keep = ("status", "reason", "found_count", "expected_count", "missing",
            "mapping", "px_per_ft_u", "px_per_ft_v", "isotropy_u_over_v",
            "residual_max_px", "points", "mark_pixel_rects", "mark_pixels",
            "components", "image_w", "image_h", "endpoint_fit")
    return dict((k, fit[k]) for k in keep if k in fit)


def register(anno_sidecar_path, model_sidecar_path=None):
    """Register one annotation capture. Returns the record it wrote."""
    t0 = time.time()
    anno_sidecar_path = Path(anno_sidecar_path)
    tiff_out, json_out = output_paths(anno_sidecar_path)
    anno_sidecar = json.loads(anno_sidecar_path.read_text(encoding="utf-8"))
    if model_sidecar_path is None:
        model_sidecar_path = model_sidecar_for(anno_sidecar_path, anno_sidecar)
    if model_sidecar_path is None or not Path(model_sidecar_path).exists():
        raise FileNotFoundError("no model sidecar for {0} (tried {1})".format(
            anno_sidecar_path, model_sidecar_path))
    model_sidecar_path = Path(model_sidecar_path)
    model_sidecar = json.loads(model_sidecar_path.read_text(encoding="utf-8"))
    anno_tiff = resolve_tiff(anno_sidecar_path, anno_sidecar)
    model_tiff = resolve_tiff(model_sidecar_path, model_sidecar)

    record = {
        "schema": SCHEMA, "tool_version": TOOL_VERSION,
        "view_id": anno_sidecar.get("view_id"),
        "source_annotation_sidecar": str(anno_sidecar_path),
        "source_annotation_sidecar_sha256": sha256_file(anno_sidecar_path),
        "source_annotation_tiff": str(anno_tiff),
        "source_annotation_tiff_sha256": sha256_file(anno_tiff),
        "source_model_sidecar": str(model_sidecar_path),
        "source_model_sidecar_sha256": sha256_file(model_sidecar_path),
        "source_model_tiff": str(model_tiff),
        "source_model_tiff_sha256": sha256_file(model_tiff),
        "refusals": [],
        "registered_tiff": None, "registered_tiff_sha256": None,
    }
    refusals = record["refusals"]

    anno_marks, why = rm.recorded_marks(anno_sidecar)
    if anno_marks is None:
        refusals.append("annotation sidecar: " + (why or "no registration_marks; "
                                                  "not a registered capture"))
    model_marks, why = rm.recorded_marks(model_sidecar)
    if model_marks is None:
        refusals.append("model sidecar: " + (why or "no registration_marks; "
                                             "not a registered capture"))
    if anno_marks is not None and model_marks is not None:
        if anno_marks.get("pass") != "annotation" or model_marks.get("pass") != "model":
            refusals.append("the sidecars' registration_marks passes are {0!r} and "
                            "{1!r}, not annotation and model".format(
                                anno_marks.get("pass"), model_marks.get("pass")))
        why = rm.marks_identity_refusal(anno_marks, model_marks)
        if why:
            refusals.append(why)
        # The capture's own faults travel with the result: a registration can
        # succeed on a capture whose restore did not.
        record["capture_faults"] = list(anno_marks.get("faults") or [])

    if not refusals:
        anno_px = rm.load_rgb(anno_tiff)
        model_px = rm.load_rgb(model_tiff)
        anno_fit = rm.fit_recorded_marks(anno_px, anno_marks)
        model_fit = rm.fit_recorded_marks(model_px, model_marks)
        record["annotation_fit"] = _fit_summary(anno_fit)
        record["model_fit"] = _fit_summary(model_fit)
        out_h, out_w = model_px.shape[0], model_px.shape[1]
        lattice = rm.model_lattice_mapping(model_sidecar.get("bounds_xy"), out_w, out_h)
        record["model_marks_vs_lattice"] = rm.mapping_agreement(
            model_fit.get("mapping") if model_fit.get("status") == "value" else None,
            lattice, model_sidecar.get("bounds_xy"))
        for name, fit in (("annotation", anno_fit), ("model", model_fit)):
            if fit.get("status") != "value":
                refusals.append("{0} capture's marks cannot be fitted: {1}".format(
                    name, fit.get("reason")))
        if not refusals:
            transform = rm.compose_pixel_transform(anno_fit["mapping"],
                                                   model_fit["mapping"])
            why = rm.transform_refusal(transform)
            if why:
                refusals.append(why)
            record["annotation_to_model_px"] = transform
        if not refusals:
            mask = rm.mark_ink_mask(anno_px, anno_marks)
            cleaned = anno_px.copy()
            cleaned[mask] = 255
            ink_before = _ink(cleaned)
            out, uncovered = resample_onto(cleaned, transform, out_w, out_h)
            ink_after = _ink(out)
            before, after = _colour_set(cleaned, ink_before), _colour_set(out, ink_after)
            record["mark_subtraction"] = {
                "annotation_mark_pixels_removed": int(np.count_nonzero(mask)),
                "method": "every pixel of a recorded tick colour set to white",
                "model_capture": "not rewritten; the decoder subtracts its marks",
            }
            record["excluded_element_ids"] = sorted(
                int(m["id"]) for m in anno_marks["marks"] if m.get("id") is not None)
            record["lattice"] = {"image_w": out_w, "image_h": out_h,
                                 "bounds_xy": model_sidecar.get("bounds_xy")}
            record["losses"] = {
                "ink_pixels_before": int(np.count_nonzero(ink_before)),
                "ink_pixels_after": int(np.count_nonzero(ink_after)),
                "ink_pixels_outside_model_image": _ink_outside(
                    ink_before, transform, out_w, out_h),
                "model_pixels_not_covered": uncovered,
                "colours_lost_in_resample": [list(rm.unpack(c))
                                             for c in sorted(before - after)],
            }
            Image.fromarray(out).save(str(tiff_out), format="TIFF")
            record["registered_tiff"] = str(tiff_out)
            record["registered_tiff_sha256"] = sha256_file(tiff_out)

    record["status"] = "refused" if refusals else "registered"
    if refusals and tiff_out.exists():
        tiff_out.unlink()
        record["stale_registered_tiff_removed"] = True
    record["elapsed_ms"] = round((time.time() - t0) * 1000.0, 3)
    # LAST: after the TIFF exists and its hash is in the record.
    json_out.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    return record


def collect(paths):
    out = []
    for arg in paths:
        p = Path(arg)
        if p.is_dir():
            out += sorted(x for x in p.rglob("*" + ANNO_SUFFIX + ".json"))
        else:
            out.append(p)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("paths", nargs="+",
                    help="annotation sidecar(s) (<view>_anno.json) or directories")
    ap.add_argument("--model", default=None,
                    help="the model sidecar, when it cannot be found beside a "
                         "single annotation sidecar")
    ns = ap.parse_args(argv)
    targets = collect(ns.paths)
    if ns.model and len(targets) != 1:
        ap.error("--model names one model sidecar, so it takes one annotation sidecar")
    code = 0
    for path in targets:
        try:
            record = register(path, Path(ns.model) if ns.model else None)
        except (OSError, ValueError) as ex:
            print("unreadable {0}: {1}: {2}".format(path, type(ex).__name__, ex))
            code = 2
            continue
        if record["status"] == "registered":
            t = record["annotation_to_model_px"]
            print("registered {0} -> {1}  x' = {2:.6f} x {3:+.2f}, y' = {4:.6f} y "
                  "{5:+.2f}".format(path, record["registered_tiff"], t["scale_x"],
                                    t["offset_x"], t["scale_y"], t["offset_y"]))
        else:
            print("REFUSED {0}: {1}".format(path, "; ".join(record["refusals"])))
            code = max(code, 1)
    if not targets:
        print("no annotation sidecars found under {0}".format(ns.paths))
        code = 2
    return code


if __name__ == "__main__":
    sys.exit(main())
