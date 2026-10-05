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
  4. writes ``<annotation stem>.registered.tiff`` -- LOSSLESSLY compressed
     (deflate, else LZW; never raw by silent fallback, never lossy: the
     decoder reads exact palette colours) -- and, LAST, a
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
  * the two sidecars do not carry the same ``view_id``;
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
from tools.stage_a_sidecar_shapes import legacy_view  # noqa: E402

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


# Lossless only, in order of preference. A lossy codec (jpeg, webp) would
# blend palette colours, and a colour ID no element owns is a wrong answer,
# not a smaller file.
LOSSLESS_TIFF_CODECS = ("tiff_deflate", "tiff_lzw")
# TIFF tag 259 (Compression) values each codec may write: Pillow writes
# "tiff_deflate" as Adobe Deflate (8); 32946 is the older deflate value.
TIFF_COMPRESSION_TAG = 259
LOSSLESS_TAG_VALUES = {"tiff_deflate": (8, 32946), "tiff_lzw": (5,)}


class NoLosslessTiffCodec(RuntimeError):
    """No lossless TIFF codec could write the registered image."""


def write_lossless_tiff(pixels, path, codecs=LOSSLESS_TIFF_CODECS):
    """Write ``pixels`` to ``path`` as a TIFF with the first codec in
    ``codecs`` that writes, and return that codec's name.

    Never falls back to raw: the uncompressed write was ~160x larger on a real
    view (241 MB vs a 1.5 MB unregistered capture). Each attempt writes a
    sibling temporary file, reads its compression tag back (a writer that
    silently ignored the codec is refused), and only then replaces ``path``
    -- so a failed write leaves an earlier registration's TIFF, and the
    record that hashes it, untouched (Codex, PR #230). If no codec writes,
    NoLosslessTiffCodec is raised."""
    import os
    import tempfile
    path = Path(path)
    image = Image.fromarray(pixels)
    failures = []
    for codec in codecs:
        fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp",
                                   dir=str(path.parent))
        os.close(fd)
        tmp = Path(tmp)
        try:
            try:
                image.save(str(tmp), format="TIFF", compression=codec)
            except (OSError, ValueError, KeyError) as ex:
                failures.append("{0}: {1}: {2}".format(codec, type(ex).__name__, ex))
                continue
            with Image.open(str(tmp)) as written:
                tag = written.tag_v2.get(TIFF_COMPRESSION_TAG)
            if tag not in LOSSLESS_TAG_VALUES.get(codec, ()):
                raise NoLosslessTiffCodec(
                    "{0} was written with TIFF compression tag {1!r}, not {2} "
                    "({3})".format(path, tag, codec, LOSSLESS_TAG_VALUES.get(codec)))
            os.replace(str(tmp), str(path))
            return codec
        finally:
            if tmp.exists():
                tmp.unlink()
    raise NoLosslessTiffCodec(
        "no lossless TIFF codec could write {0} (tried {1}); refusing to write "
        "it uncompressed: {2}".format(path, ", ".join(codecs), "; ".join(failures)))


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


def _ink_off_canvas(ink, transform, out_w, out_h):
    """Mask of annotation ink pixels whose centres land off the canvas."""
    ys, xs = np.nonzero(ink)
    xm = transform["scale_x"] * (xs + 0.5) + transform["offset_x"]
    ym = transform["scale_y"] * (ys + 0.5) + transform["offset_y"]
    off = np.zeros(ink.shape, dtype=bool)
    off[ys, xs] = (xm < 0) | (xm >= out_w) | (ym < 0) | (ym >= out_h)
    return off


def _resample_support(scale, offset, src_n):
    """``[lo, hi)``: the output pixels resample_onto() fills from a source axis
    of ``src_n`` pixels. It samples output pixel j at source
    ``floor((j + 0.5 - offset) / scale)``, which lies in ``[0, src_n)`` iff
    ``offset - 0.5 <= j < offset + scale * src_n - 0.5`` (scale > 0; a
    non-positive scale is refused before this is reached). Derived from the
    resampler's own inequality, not from the forward map of pixel centres: at
    scale 2, offset -10 the centre of source column 0 maps to -9, but the
    resampler also fills -10 from it (review, PR #221)."""
    lo = int(np.ceil(offset - 0.5))
    hi = int(np.ceil(offset + scale * src_n - 0.5))
    return lo, hi


def output_canvas(model_mapping, model_w, model_h, transform, anno_w, anno_h):
    """C7 (Greg, 2026-09-29): union(model crop A, measured annotation rect).

    Crop A is the model image itself -- the model capture renders exactly A
    -- so it is ``[0, model_w] x [0, model_h]`` on the model lattice. The
    annotation rect is MEASURED: the annotation capture's own pixel extent
    carried onto the model lattice by the fitted mark transform, bounded by
    exactly the output pixels resample_onto() fills from it -- see
    _resample_support(). So nothing the resample would draw falls off the
    canvas, and no row or column is added that it would leave white.

    The canvas keeps the model's pixel PHASE: it is the model image with
    whole pixels added on each side, so the model image sits in it at the
    integer ``model_image_origin_px``. Its origin is recorded both as that
    offset and in view UV (``canvas_origin_uv``, the canvas's top-left
    corner, through the model's mark fit).

    Replaces the grid-extended canvas (frame B), which the registered
    capture no longer records.
    """
    ax0, ax1 = _resample_support(transform["scale_x"], transform["offset_x"], anno_w)
    ay0, ay1 = _resample_support(transform["scale_y"], transform["offset_y"], anno_h)
    x0, y0 = min(0, ax0), min(0, ay0)
    x1, y1 = max(int(model_w), ax1), max(int(model_h), ay1)
    origin_uv = None
    if model_mapping and model_mapping.get("a_u") and model_mapping.get("a_v"):
        origin_uv = [(x0 - model_mapping["b_u"]) / model_mapping["a_u"],
                     (y0 - model_mapping["b_v"]) / model_mapping["a_v"]]
    return {"canvas_w": x1 - x0, "canvas_h": y1 - y0,
            "model_image_origin_px": [-x0, -y0], "shift_x": -x0, "shift_y": -y0,
            "canvas_origin_model_px": [x0, y0],
            "canvas_origin_uv": origin_uv,
            "annotation_rect_model_px": [ax0, ay0, ax1, ay1],
            "covers": "union(model crop A, measured annotation rect), on the "
                      "model's pixel phase"}


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
    anno_sidecar = legacy_view(json.loads(anno_sidecar_path.read_text(encoding="utf-8")))
    if model_sidecar_path is None:
        model_sidecar_path = model_sidecar_for(anno_sidecar_path, anno_sidecar)
    if model_sidecar_path is None or not Path(model_sidecar_path).exists():
        raise FileNotFoundError("no model sidecar for {0} (tried {1})".format(
            anno_sidecar_path, model_sidecar_path))
    model_sidecar_path = Path(model_sidecar_path)
    model_sidecar = legacy_view(json.loads(model_sidecar_path.read_text(encoding="utf-8")))
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
        # The marks alone do not name the view: a model sidecar from another
        # view (a wrong --model) carrying a copied record would pass the tick
        # comparison and register two different views (review, PR #219).
        if (anno_sidecar.get("view_id") is None or model_sidecar.get("view_id") is None
                or str(anno_sidecar.get("view_id")) != str(model_sidecar.get("view_id"))):
            refusals.append("the sidecars do not name the same view (annotation "
                            "view_id {0!r}, model view_id {1!r})".format(
                                anno_sidecar.get("view_id"), model_sidecar.get("view_id")))
        # The capture's own faults travel with the result: a registration can
        # succeed on a capture whose restore did not.
        record["capture_faults"] = list(anno_marks.get("faults") or [])

    if not refusals:
        anno_px = rm.load_rgb(anno_tiff)
        model_px = rm.load_rgb(model_tiff)
        anno_palette = rm.palette_colours(anno_sidecar)
        anno_fit = rm.fit_recorded_marks(anno_px, anno_marks,
                                         reserved_colours=anno_palette)
        model_fit = rm.fit_recorded_marks(model_px, model_marks,
                                          reserved_colours=rm.palette_colours(
                                              model_sidecar))
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
            elif rm.residual_refusal(fit):
                refusals.append("{0} capture: {1}".format(name, rm.residual_refusal(fit)))
        if not refusals:
            transform = rm.compose_pixel_transform(anno_fit["mapping"],
                                                   model_fit["mapping"])
            why = rm.transform_refusal(transform)
            if why:
                refusals.append(why)
            record["annotation_to_model_px"] = transform
        if not refusals:
            mask = rm.mark_ink_mask(anno_px, anno_marks, anno_palette)
            cleaned = anno_px.copy()
            cleaned[mask] = 255
            canvas = output_canvas(model_fit["mapping"], out_w, out_h, transform,
                                   anno_px.shape[1], anno_px.shape[0])
            on_canvas = dict(transform, offset_x=transform["offset_x"] + canvas["shift_x"],
                             offset_y=transform["offset_y"] + canvas["shift_y"])
            ink_before = _ink(cleaned)
            off_canvas = _ink_off_canvas(ink_before, on_canvas, canvas["canvas_w"],
                                         canvas["canvas_h"])
            out, uncovered = resample_onto(cleaned, on_canvas, canvas["canvas_w"],
                                           canvas["canvas_h"])
            ink_after = _ink(out)
            before = _colour_set(cleaned, ink_before & ~off_canvas)
            after = _colour_set(out, ink_after)
            record["mark_subtraction"] = {
                "annotation_mark_pixels_removed": int(np.count_nonzero(mask)),
                "method": "every pixel of a tick colour, or of its blend toward "
                          "white, set to white",
                "model_capture": "not rewritten; the decoder subtracts its marks",
            }
            record["excluded_element_ids"] = sorted(
                int(m["id"]) for m in anno_marks["marks"] if m.get("id") is not None)
            record["annotation_to_canvas_px"] = on_canvas
            record["lattice"] = dict(canvas, model_image_w=out_w, model_image_h=out_h,
                                     bounds_xy=model_sidecar.get("bounds_xy"))
            record["losses"] = {
                "ink_pixels_before": int(np.count_nonzero(ink_before)),
                "ink_pixels_after": int(np.count_nonzero(ink_after)),
                "ink_pixels_off_canvas": int(np.count_nonzero(off_canvas)),
                "canvas_pixels_not_covered": uncovered,
                "colours_lost_in_resample": [list(rm.unpack(c))
                                             for c in sorted(before - after)],
            }
            write_lossless_tiff(out, tiff_out)
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
