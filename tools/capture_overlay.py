#!/usr/bin/env python3
"""Draw a Stage A capture's TIFF with its own sidecar's bboxes on top.

Offline review aid for Stage A step 5. Greg reads the capture against the
drawing; this puts the sidecar's recorded rectangles, source classes and
category names onto the pixels so that reading is possible at all. It is a
companion to ``tools/decode_stage_a_color_id.py`` and
``tools/link_identity_resolver.py`` -- same split as those two (Dynamo
exports, external Python looks at it afterwards).

WHAT IT EMITS, AND WHAT IT DELIBERATELY DOES NOT
-------------------------------------------------
Pictures. One ``<sidecar-stem>.overlay.png`` per sidecar, never overwriting
the input or the TIFF.

It emits NO score, NO tolerance, NO pass/fail and NO aggregate verdict, by
design and not by omission: validation of a Stage A capture is Greg's read
of the TIFF and the sidecar against the drawing, and a tool that printed
"94% of bboxes look right" would quietly become the answer to "was the
capture correct" and replace that read. The one place a number appears is a
TRUNCATION NOTICE ("N more not shown"), which exists so that a capped panel
can never silently hide records -- it says what is absent, it does not rate
what is present.

Where the header echoes a figure from the sidecar (feet-per-pixel, the crop
rectangle, the recorded export size), it is stating what the frame used to
place the boxes was, so the drawing can be read against the right geometry.
None of it is a judgement about the capture.

MISSING AND UNAVAILABLE RECORDS ARE DRAWN, NOT DROPPED
--------------------------------------------------------
Every record in the sidecar's map reaches the picture. A record with no
usable bbox has no pixels to be drawn on -- so it is listed by id, class,
category and REASON in the panel under the image, rather than vanishing.
"Absent from the overlay" must never be the silent outcome of an
unavailable bbox, a crop that could not be applied, or a class the caller
filtered out: each of those is stated on the picture.

THE FRAME, AND WHY IT IS REFUSED RATHER THAN GUESSED
-----------------------------------------------------
UV -> pixel goes through ``tools/clamp_pad_geometry.py`` -- the one copy of
Revit's ExportImage aspect-clamp model, shared with decode and the identity
resolver -- via its ``uv_to_pixel()``. The crop rectangle comes from the
sidecar:

    model pass       "bounds_xy"                  (the rectangle set as
                                                   view.CropBox for it)
    annotation pass  "registration"."rendered_uv" (frame B, the rectangle
                                                   THAT pass rendered)

Either can be null, which means the crop could not be applied and the
TIFF's extent is whatever ``FitToPage`` chose. There is no rectangle to
map through in that case, so nothing is placed and the picture says so.
Likewise when the producer's recorded export size (``resolution.actual_w``/
``actual_h``) disagrees with the file's own dimensions: the pixels are not
the pixels the sidecar describes, and placing a box would be fabricating a
position. Both refuse; neither is reported as a verdict on the capture.

USAGE
-----
    python tools/capture_overlay.py <sidecar.json> [<sidecar2.json> ...]
    python tools/capture_overlay.py <dir-of-sidecars>/
    python tools/capture_overlay.py <sidecar.json> --labels id --only host --only dwg

LEAF MODULE
-----------
Standard library + PIL + ``tools/clamp_pad_geometry.py`` (which is
standard-library-only). NumPy is not needed: nothing here reads the
capture's pixels, it only draws on top of them. Imports nothing from
``vop_interwoven`` and nothing
from ``tools/decode_stage_a_color_id.py``, which would put the package on
this standalone tool's dependency path -- the same rule
``tools/link_identity_resolver.py`` follows.

OUT OF SCOPE
------------
Metrics, the analysis grid, and batch reporting.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

# Repo root (parent of tools/) on sys.path so the sibling leaf module below is
# importable however this tool is invoked -- same preamble as
# tools/link_identity_resolver.py, and for the same reason: clamp_pad_geometry
# is standard-library-only, so sharing it does not drag vop_interwoven onto
# this tool's dependency path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.clamp_pad_geometry import clamp_pad_geometry, uv_to_pixel  # noqa: E402

TOOL_VERSION = "1.0.0"

# Source classes. The class decides the outline colour and the label's
# trailing token; it is READ from the sidecar, never inferred from geometry.
#
# UNKNOWN_SOURCE / UNKNOWN_BASIS are their own classes rather than being
# folded into HOST or ANNOTATION: a record whose provenance the sidecar
# records as unavailable is a different thing from one whose provenance is
# known, and collapsing the two would put an unattributed rectangle on the
# picture wearing a confident colour.
CLASS_HOST = "HOST"
CLASS_DWG = "DWG"
CLASS_LINK = "LINK"
CLASS_ANNOTATION = "ANNOTATION"
CLASS_DATUM = "DATUM"
CLASS_UNKNOWN_SOURCE = "UNKNOWN_SOURCE"
CLASS_UNKNOWN_BASIS = "UNKNOWN_BASIS"

CLASS_COLORS = {
    CLASS_HOST: (0, 92, 255),
    CLASS_DWG: (255, 138, 0),
    CLASS_LINK: (0, 158, 62),
    CLASS_ANNOTATION: (198, 0, 196),
    CLASS_DATUM: (0, 172, 204),
    CLASS_UNKNOWN_SOURCE: (214, 0, 0),
    CLASS_UNKNOWN_BASIS: (214, 0, 0),
}

# --only takes these; the mapping is explicit so a filter name is never a
# lowercased class name by coincidence.
FILTER_NAMES = {
    "host": CLASS_HOST,
    "dwg": CLASS_DWG,
    "link": CLASS_LINK,
    "annotation": CLASS_ANNOTATION,
    "datum": CLASS_DATUM,
    "unknown": CLASS_UNKNOWN_SOURCE,
}

PASS_MODEL = "model"
PASS_ANNOTATION = "annotation"

# Panel geometry. A cap exists because a sheet-sized capture can carry
# hundreds of unplaced records and an uncapped panel would be taller than
# the capture it annotates; the cap is always stated on the picture.
DEFAULT_MAX_PANEL_LINES = 40
PANEL_PAD = 10
# The canvas is at least this wide, whatever the capture's own width, and
# every panel line is wrapped to fit it. A reason that runs off the right
# edge is rendered but not READABLE, which fails the same contract as not
# rendering it: the first draft clipped "...whatever FitToPage chose an" on
# a 900 px capture and lost the rest of the sentence. The capture itself
# still sits at the origin at 1:1, so pixel coordinates are unaffected.
PANEL_MIN_WIDTH = 1100
PANEL_BG = (250, 250, 250)
PANEL_RULE = (170, 170, 170)
PANEL_TEXT = (20, 20, 20)
PANEL_ALERT = (196, 0, 0)
SWATCH_PX = 12

# Outline widths. The halo is drawn first and wider so an outline stays
# visible over any colour the colour-ID buffer put underneath it -- the
# capture's own palette is saturated and arbitrary, so a single-colour
# outline would disappear against its own class colour somewhere.
OUTLINE_HALO_PX = 4
OUTLINE_PX = 2
OUTLINE_HALO_COLOR = (255, 255, 255)

# Why _font() fell back to PIL's bitmap default, when it did. None means it
# did not. Kept rather than discarded so an unreadable font file is
# distinguishable from an absent one after the fact.
FONT_FALLBACK_REASON = None

# Three-valued states, as color_id_buffer.py writes them.
_GS_VALUE = "value"
_GS_NOT_APPLICABLE = "not_applicable"
_GS_UNAVAILABLE = "unavailable"


def _font(size: int):
    """A readable font, falling back to PIL's bitmap default.

    DejaVu ships with most Pillow wheels but is not guaranteed on the
    machine reading a capture, so the failure is handled rather than
    assumed away.

    The handler KEEPS the exception rather than discarding it (Refactor
    Rule #1, and the handler shape ``tools/count_discarded_handlers.py``
    counts): a font that is present but unreadable is a different situation
    from one that is simply absent, and ``FONT_FALLBACK_REASON`` carries
    which so it is answerable later. Falling back is safe -- every label
    still renders, at the bitmap default's size.
    """
    global FONT_FALLBACK_REASON
    errors = []
    for name in ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError) as ex:
            errors.append("{0}: {1}: {2}".format(name, type(ex).__name__, ex))
    FONT_FALLBACK_REASON = "; ".join(errors)
    return ImageFont.load_default()


def _gs_read(raw: Any) -> tuple[str, Any]:
    """Read one of color_id_buffer.py's three-valued records.

    Returns ``(state, payload)`` -- payload is the value on ``value`` and
    the reason string otherwise. A raw (non-dict) value is accepted too,
    because the model pass's ``bbox_corners_uv`` predates the three-valued
    convention and is a bare list-or-None: ``None`` there carries no reason
    of its own, so one is supplied naming what was read.
    """
    if isinstance(raw, dict) and "state" in raw:
        state = raw.get("state")
        if state == _GS_VALUE:
            return (_GS_VALUE, raw.get("value"))
        return (state or _GS_UNAVAILABLE, raw.get("reason") or "no reason recorded")
    if raw is None:
        return (_GS_UNAVAILABLE, "recorded as null in the sidecar")
    return (_GS_VALUE, raw)


def _source_class_for_host_entry(entry: dict[str, Any]) -> tuple[str, str | None]:
    """HOST vs DWG for one ``near_face_w_map["host"]`` entry.

    Stage A step 1 made the distinction carryable: a painted DWG
    ImportInstance is a real element of the host document and sat in this
    bucket indistinguishable from a true HOST element until the
    three-valued ``source`` key was added. Sidecars written before that key
    existed have no ``source`` at all, which is NOT the same as a source
    that could not be read -- both land in UNKNOWN_SOURCE, with reasons
    that say which.
    """
    if "source" not in entry:
        return (CLASS_UNKNOWN_SOURCE,
                "sidecar entry carries no 'source' key (written before Stage A step 1)")
    state, payload = _gs_read(entry.get("source"))
    if state == _GS_VALUE:
        value = str(payload)
        if value in (CLASS_HOST, CLASS_DWG):
            return (value, None)
        return (CLASS_UNKNOWN_SOURCE, "unrecognised source value {0!r}".format(value))
    return (CLASS_UNKNOWN_SOURCE, str(payload))


def _annotation_class_for(entry: dict[str, Any]) -> tuple[str, str | None]:
    """ANNOTATION vs DATUM for one ``annotation_bbox_map`` entry.

    ``membership_basis`` is what put the element in the annotation pass:
    ``owner_view`` (view-specific, no model extent) or ``datum_category``
    (a grid or level, which does have one). A missing basis is its own
    class -- it is exactly the case color_id_buffer.py refuses to claim
    either way when deciding ``bbox_3d``.
    """
    basis = entry.get("membership_basis")
    if basis == "owner_view":
        return (CLASS_ANNOTATION, None)
    if basis == "datum_category":
        return (CLASS_DATUM, None)
    return (CLASS_UNKNOWN_BASIS,
            "membership_basis is {0!r}, so this element cannot be told apart "
            "from a datum".format(basis))


def _number(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return "{0:.4g}".format(value)


def read_records(sidecar: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Every bbox record in a Stage A sidecar, as a flat drawable list.

    Returns ``(pass_kind, records)``. Each record::

        key           the id the sidecar filed it under ("<elem_id>", or
                      "<link_inst_id>:<link_elem_id>" for a LINK element)
        cls           one of the CLASS_* constants
        category      category name, or None
        bbox_uv       the 4 UV corners, or None
        bbox_state    "value" | "unavailable" | "not_applicable"
        bbox_reason   why there is no rectangle, when there is not
        detail        extra label text read from this record's own keys
        class_reason  why the class is an UNKNOWN_* one, when it is

    Nothing is filtered here. A record with no bbox is returned with the
    reason it has none, because dropping it is exactly the silence this
    tool exists to prevent.

    Raises:
        ValueError: when the sidecar carries neither map, which means it is
            not a Stage A capture sidecar (or predates both). Guessing a
            pass and rendering an empty overlay would certify a file this
            tool never understood.
    """
    if sidecar.get("pass") == PASS_ANNOTATION or "annotation_bbox_map" in sidecar:
        return (PASS_ANNOTATION, _read_annotation_records(sidecar))
    if "near_face_w_map" in sidecar:
        return (PASS_MODEL, _read_model_records(sidecar))
    raise ValueError(
        "sidecar carries neither 'near_face_w_map' (model pass) nor "
        "'annotation_bbox_map' (annotation pass); there is nothing to draw "
        "and this tool will not certify a picture of a file it did not "
        "understand"
    )


def _read_model_records(sidecar: dict[str, Any]) -> list[dict[str, Any]]:
    near_face_w_map = sidecar.get("near_face_w_map") or {}
    records: list[dict[str, Any]] = []

    for key, entry in sorted((near_face_w_map.get("host") or {}).items()):
        cls, class_reason = _source_class_for_host_entry(entry)
        state, payload = _gs_read(entry.get("bbox_corners_uv"))
        near_w = _number(entry.get("near_face_w"))
        records.append({
            "key": str(key),
            "cls": cls,
            "class_reason": class_reason,
            "category": entry.get("category"),
            "bbox_uv": payload if state == _GS_VALUE else None,
            "bbox_state": state,
            "bbox_reason": None if state == _GS_VALUE else str(payload),
            "detail": "" if near_w is None else "w={0}".format(near_w),
        })

    for key, entry in sorted((near_face_w_map.get("link") or {}).items()):
        state, payload = _gs_read(entry.get("bbox_corners_uv"))
        near_w = _number(entry.get("near_face_w"))
        records.append({
            "key": str(key),
            "cls": CLASS_LINK,
            "class_reason": None,
            "category": entry.get("category"),
            "bbox_uv": payload if state == _GS_VALUE else None,
            "bbox_state": state,
            "bbox_reason": None if state == _GS_VALUE else str(payload),
            "detail": "" if near_w is None else "w={0}".format(near_w),
        })

    return records


def _status_block_read(raw: Any) -> tuple[str, str | None]:
    """Read a sidecar BLOCK-level status, which spells its key "status".

    color_id_buffer.py uses two spellings and they are not
    interchangeable: a per-FIELD three-valued record carries ``"state"``
    (``_gs_value``/``_gs_unavailable``/``_gs_not_applicable``), while a
    whole BLOCK -- ``annotation_bbox_status``, ``export_frame`` -- carries
    ``"status"``. Reading a block with the field reader silently classifies
    an "unavailable" block as a VALUE whose payload happens to be a dict,
    which is precisely the defect PR #213's review found: an annotation
    collection that raised was read as a successful one.

    Returns ``(status, reason)``; ``status`` is ``"value"`` when the block
    is absent, since a sidecar written before the block existed is not a
    failed collection.
    """
    if not isinstance(raw, dict):
        return (_GS_VALUE, None)
    status = raw.get("status")
    if status is None or status == _GS_VALUE:
        return (_GS_VALUE, None)
    return (str(status), str(raw.get("reason") or "no reason recorded"))


def _read_annotation_records(sidecar: dict[str, Any]) -> list[dict[str, Any]]:
    # An annotation collection that FAILED writes an EMPTY MAP plus a
    # status block saying so (color_id_buffer.py: `annotation_bbox_map = {}`
    # and `{"status": "unavailable", "reason": ...}`). An empty dict is
    # still a dict, so testing the map alone reads that failure as a view
    # with no annotations -- the exact silence this tool exists to prevent,
    # and what it did until PR #213's review. The STATUS decides, and it is
    # read with the block reader above because production spells it
    # "status", not "state".
    status, reason = _status_block_read(sidecar.get("annotation_bbox_status"))
    bbox_map = sidecar.get("annotation_bbox_map")
    if status != _GS_VALUE or not isinstance(bbox_map, dict):
        if status != _GS_VALUE:
            why = "annotation bbox collection reported {0}: {1}".format(status, reason)
        else:
            why = "sidecar has no 'annotation_bbox_map' to draw"
        return [{
            "key": "<annotation_bbox_map>",
            "cls": CLASS_UNKNOWN_BASIS,
            "class_reason": None,
            "category": None,
            "bbox_uv": None,
            "bbox_state": _GS_UNAVAILABLE,
            "bbox_reason": why,
            "detail": "",
        }]

    records = []
    for key, entry in sorted(bbox_map.items()):
        cls, class_reason = _annotation_class_for(entry)
        bstate, bpayload = _gs_read(entry.get("bbox_uv"))
        bbox_source = entry.get("bbox_source")
        records.append({
            "key": str(key),
            "cls": cls,
            "class_reason": class_reason,
            "category": entry.get("category"),
            "bbox_uv": bpayload if bstate == _GS_VALUE else None,
            "bbox_state": bstate,
            "bbox_reason": None if bstate == _GS_VALUE else str(bpayload),
            "detail": "" if not bbox_source else "bbox={0}".format(bbox_source),
        })
    return records


def frame_for(sidecar: dict[str, Any], pass_kind: str) -> tuple[tuple[float, ...] | None, str | None]:
    """The UV rectangle this pass's TIFF was rendered to, or why there is none.

    Two different keys, because the two passes crop to two different
    rectangles and step 2's registration data is what says so: the model
    pass renders its own (possibly narrower) crop and records it as
    ``bounds_xy``; the annotation pass renders frame B and records it as
    ``registration.rendered_uv``. Reading one pass's key on the other would
    place every box against the wrong rectangle.
    """
    if pass_kind == PASS_ANNOTATION:
        registration = sidecar.get("registration")
        if not isinstance(registration, dict):
            return (None, "annotation sidecar has no 'registration' block, so the "
                          "rectangle this pass rendered is not recorded")
        raw = registration.get("rendered_uv")
        missing = ("'registration.rendered_uv' is null: the crop could not be applied to "
                   "this pass, so the TIFF's extent is whatever FitToPage chose and no "
                   "recorded UV rectangle describes it")
    else:
        raw = sidecar.get("bounds_xy")
        missing = ("'bounds_xy' is null: the crop could not be applied to this capture, "
                   "so the TIFF's extent is whatever FitToPage chose and no recorded UV "
                   "rectangle describes it")
    if not raw or len(raw) != 4:
        return (None, missing)
    return (tuple(float(v) for v in raw), None)


def _recorded_dims(sidecar: dict[str, Any]) -> tuple[int | None, int | None]:
    resolution = sidecar.get("resolution")
    if not isinstance(resolution, dict):
        return (None, None)
    w = resolution.get("actual_w")
    h = resolution.get("actual_h")
    w = int(w) if isinstance(w, int) and not isinstance(w, bool) else None
    h = int(h) if isinstance(h, int) and not isinstance(h, bool) else None
    # BOTH or NEITHER. One axis recorded and the other not would make
    # clamp_pad_geometry take its feet-per-pixel from the producer on one
    # axis and from the file on the other -- a half-measurement that reads
    # like a measurement. There is no second dimension pair to compare
    # against in that case, which is exactly the "no producer-recorded size"
    # reading the leaf module documents.
    if w is None or h is None:
        return (None, None)
    return (w, h)


def place_records(
    records: list[dict[str, Any]],
    bounds_uv: tuple[float, ...] | None,
    image_w: int,
    image_h: int,
    measured_w: int | None = None,
    measured_h: int | None = None,
    frame_reason: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Map every record's UV rectangle into this image's pixel space.

    Each record comes back with ``pixel_corners`` -- a list of float
    ``(x, y)`` pairs, UNCLAMPED, so a rectangle that runs off the edge
    reports where it actually is -- or ``unplaced_reason``, never neither
    and never both.

    Also returns the frame the placement used::

        {"bounds_uv", "feet_per_pixel", "pad_x", "pad_y",
         "image_px", "recorded_px", "refused_reason"}

    REFUSALS, not guesses. Placement is refused outright -- every record
    unplaced, with the same reason -- when there is no crop rectangle, when
    the crop is degenerate, or when the producer's recorded export size
    disagrees with the file being read. That last one is decode's Guard 1
    in a different costume: the pads would go negative and every box would
    land somewhere plausible and wrong. The picture says which of the three
    happened.
    """
    frame: dict[str, Any] = {
        "bounds_uv": list(bounds_uv) if bounds_uv is not None else None,
        "feet_per_pixel": None,
        "pad_x": None,
        "pad_y": None,
        "image_px": [int(image_w), int(image_h)],
        "recorded_px": (
            [measured_w, measured_h] if (measured_w or measured_h) else None
        ),
        "refused_reason": None,
    }

    if bounds_uv is None:
        frame["refused_reason"] = frame_reason or "no crop rectangle recorded for this pass"
    elif (measured_w and measured_h) and (int(measured_w) != int(image_w)
                                          or int(measured_h) != int(image_h)):
        frame["refused_reason"] = (
            "recorded export size {0}x{1} px does not match this file's {2}x{3} px; "
            "the pixels are not the pixels the sidecar describes, so no box is "
            "placed".format(measured_w, measured_h, image_w, image_h)
        )
    else:
        try:
            geometry = clamp_pad_geometry(
                bounds_uv, image_w, image_h,
                measured_w=measured_w, measured_h=measured_h,
            )
        except ValueError as ex:
            frame["refused_reason"] = "{0}: {1}".format(type(ex).__name__, ex)
        else:
            frame["feet_per_pixel"], frame["pad_x"], frame["pad_y"] = geometry

    placed = []
    for record in records:
        out = dict(record)
        out["pixel_corners"] = None
        out["unplaced_reason"] = None
        # A record's OWN missing bbox is checked first, and deliberately
        # outranks a frame-level refusal: an element with no rectangle is
        # unplaceable whatever the frame does, and its own reason -- the
        # annotation collection that raised, the bbox that would not
        # resolve -- is the more specific truth. Reporting the frame's
        # reason over it buries the one that matters, which is how the
        # collection-failure record came back saying "recorded export size
        # does not match" while the failure went unmentioned.
        if out["bbox_state"] != _GS_VALUE:
            out["unplaced_reason"] = out["bbox_reason"] or "no bbox recorded"
        elif frame["refused_reason"] is not None:
            out["unplaced_reason"] = frame["refused_reason"]
        elif not out["bbox_uv"]:
            out["unplaced_reason"] = "bbox recorded as an empty corner list"
        else:
            geometry = (frame["feet_per_pixel"], frame["pad_x"], frame["pad_y"])
            corners = [
                uv_to_pixel(uv[0], uv[1], bounds_uv, image_w, image_h, geometry=geometry)
                for uv in out["bbox_uv"]
            ]
            out["pixel_corners"] = corners
            xs = [c[0] for c in corners]
            ys = [c[1] for c in corners]
            # Wholly off-image: there is no pixel to draw on, so this is a
            # panel entry rather than an invisible no-op. A rectangle that
            # merely OVERHANGS an edge is drawn and clipped by the canvas,
            # which is the honest picture of an element running off the crop.
            if max(xs) < 0 or min(xs) > image_w - 1 or max(ys) < 0 or min(ys) > image_h - 1:
                out["unplaced_reason"] = (
                    "maps entirely outside this image (x {0:.1f}..{1:.1f}, "
                    "y {2:.1f}..{3:.1f})".format(min(xs), max(xs), min(ys), max(ys))
                )
        placed.append(out)
    return (placed, frame)


def plan_overlay(
    sidecar: dict[str, Any],
    image_w: int,
    image_h: int,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Everything the renderer needs, with no image and no drawing.

    ``(pass_kind, placed_records, frame)``. Split out from ``render()`` so
    the pixel positions this tool puts a rectangle at are assertable
    directly, against a fixture whose answer is known by hand, rather than
    only through what a PNG happens to look like. Both are tested; this is
    the one that says WHERE.
    """
    pass_kind, records = read_records(sidecar)
    bounds_uv, frame_reason = frame_for(sidecar, pass_kind)
    measured_w, measured_h = _recorded_dims(sidecar)
    placed, frame = place_records(
        records, bounds_uv, image_w, image_h,
        measured_w=measured_w, measured_h=measured_h, frame_reason=frame_reason,
    )
    return (pass_kind, placed, frame)


def _label_for(record: dict[str, Any], mode: str) -> str | None:
    if mode == "none":
        return None
    if mode == "id":
        return record["key"]
    parts = [record["key"]]
    if record.get("category"):
        parts.append(str(record["category"]))
    parts.append(record["cls"])
    if record.get("detail"):
        parts.append(record["detail"])
    return "  ".join(parts)


def _draw_record(draw: ImageDraw.ImageDraw, record: dict[str, Any], font, label_mode: str,
                 image_w: int, image_h: int) -> None:
    corners = [(float(x), float(y)) for x, y in record["pixel_corners"]]
    color = CLASS_COLORS.get(record["cls"], CLASS_UNKNOWN_SOURCE)
    polygon = corners + [corners[0]]
    # Halo first, so the class colour sits on top of it. A single-colour
    # outline would vanish wherever the colour-ID buffer underneath happens
    # to carry that same colour.
    draw.line(polygon, fill=OUTLINE_HALO_COLOR, width=OUTLINE_HALO_PX, joint="curve")
    draw.line(polygon, fill=color, width=OUTLINE_PX, joint="curve")

    text = _label_for(record, label_mode)
    if not text:
        return
    x0 = min(c[0] for c in corners)
    y0 = min(c[1] for c in corners)
    tw, th = _text_size(draw, text, font)
    # Above the box when there is room, inside its top edge otherwise, and
    # always pulled back onto the canvas so a label never falls off with the
    # edge of the element it names.
    ty = y0 - th - 3 if y0 - th - 3 >= 0 else y0 + 2
    tx = max(0.0, min(float(x0), image_w - tw - 1.0))
    ty = max(0.0, min(float(ty), image_h - th - 1.0))
    draw.rectangle([tx, ty, tx + tw + 5, ty + th + 3], fill=color)
    draw.text((tx + 3, ty + 1), text, font=font, fill=(255, 255, 255))


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    return (int(box[2] - box[0]), int(box[3] - box[1]))


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    """Break one panel line into rows that fit ``max_w`` pixels.

    Greedy by word, then by CHARACTER for a single token wider than the
    line -- a recorded absolute path or an exception repr is one token and
    would otherwise overflow exactly the way an unwrapped line does. Nothing
    is elided: every character of a reason reaches the picture.
    """
    if max_w <= 0 or _text_size(draw, text, font)[0] <= max_w:
        return [text]
    rows: list[str] = []
    current = ""
    for word in text.split(" "):
        trial = word if not current else current + " " + word
        if _text_size(draw, trial, font)[0] <= max_w:
            current = trial
            continue
        if current:
            rows.append(current)
            current = ""
        while _text_size(draw, word, font)[0] > max_w and len(word) > 1:
            cut = len(word)
            while cut > 1 and _text_size(draw, word[:cut], font)[0] > max_w:
                cut -= 1
            rows.append(word[:cut])
            word = word[cut:]
        current = word
    if current:
        rows.append(current)
    return rows


def _header_lines(
    sidecar_path: Path,
    tiff_path: Path,
    pass_kind: str,
    sidecar: dict[str, Any],
    frame: dict[str, Any],
    only: list[str] | None,
    record_count: int = -1,
) -> list[tuple[str, bool]]:
    """Factual lines about the frame the boxes were placed against.

    ``(text, is_alert)``. Nothing here rates the capture: it says which
    file, which pass, which rectangle and which feet-per-pixel produced the
    positions above, so the picture can be read against the right geometry.
    """
    lines: list[tuple[str, bool]] = []
    lines.append(("{0}  ({1} pass, view_id {2})".format(
        sidecar_path.name, pass_kind, sidecar.get("view_id")), False))
    lines.append(("image: {0}  {1}x{2} px".format(
        tiff_path.name, frame["image_px"][0], frame["image_px"][1]), False))

    if frame["recorded_px"]:
        recorded = "{0}x{1}".format(frame["recorded_px"][0], frame["recorded_px"][1])
        differs = frame["recorded_px"] != frame["image_px"]
        lines.append(("sidecar-recorded export size: {0} px{1}".format(
            recorded, "  (differs from the file)" if differs else ""), differs))
    else:
        lines.append(("sidecar-recorded export size: not recorded; feet-per-pixel "
                      "measured against the file's own dimensions", False))

    if frame["bounds_uv"] is None:
        lines.append(("crop rectangle: none recorded", True))
    else:
        b = frame["bounds_uv"]
        lines.append(("crop rectangle (ft, view UV): "
                      "u {0:.4f}..{1:.4f}   v {2:.4f}..{3:.4f}".format(
                          b[0], b[2], b[1], b[3]), False))

    if frame["refused_reason"]:
        lines.append(("NOTHING PLACED -- " + frame["refused_reason"], True))
    else:
        lines.append(("feet per pixel: {0:.6g}   clamp pad: x {1:.2f} px, y {2:.2f} px".format(
            frame["feet_per_pixel"], frame["pad_x"], frame["pad_y"]), False))

    # A capture whose map carries NO records at all draws no boxes, and the
    # "not drawn: none" line below would then read as "everything was
    # drawn". Say which map was empty instead: an empty map is a fact about
    # the sidecar, not a blank picture.
    if record_count == 0:
        which = ("'annotation_bbox_map'" if pass_kind == PASS_ANNOTATION
                 else "'near_face_w_map'")
        lines.append(("{0} carries no records: this capture recorded no element "
                      "bboxes, so there is nothing to draw".format(which), True))

    if only:
        lines.append(("FILTERED: --only {0}; records of every other class are listed "
                      "below, not drawn".format(", ".join(only)), True))
    return lines


def _panel_lines(placed: list[dict[str, Any]], max_lines: int) -> tuple[list[str], int]:
    """One line per record that reached no pixels, plus how many were cut.

    This is the "never blank" half of the contract: a record the overlay
    could not draw is named here with its reason, so the picture never
    implies the sidecar had nothing to say about it.
    """
    lines = []
    for record in placed:
        if record.get("pixel_corners") is not None and not record.get("unplaced_reason"):
            continue
        reason = record.get("unplaced_reason") or "not drawn"
        if record.get("class_reason"):
            reason = "{0}; class: {1}".format(reason, record["class_reason"])
        lines.append("{0}  [{1}]  {2}  --  {3}".format(
            record["key"], record["cls"], record.get("category") or "(no category)", reason))
    truncated = max(0, len(lines) - max_lines)
    return (lines[:max_lines], truncated)


def render(
    image: Image.Image,
    sidecar_path: Path,
    tiff_path: Path,
    pass_kind: str,
    sidecar: dict[str, Any],
    placed: list[dict[str, Any]],
    frame: dict[str, Any],
    label_mode: str = "full",
    only: list[str] | None = None,
    max_panel_lines: int = DEFAULT_MAX_PANEL_LINES,
) -> Image.Image:
    """The capture at 1:1, boxes on top, header + legend + not-drawn panel below.

    The capture's own pixels are neither scaled nor tinted and sit at the
    canvas origin, so a pixel coordinate read off this picture IS the
    capture's pixel coordinate. Everything this tool adds goes underneath
    it, except the outlines and labels themselves.
    """
    base = image.convert("RGB")
    image_w, image_h = base.size

    drawn_classes = set()
    label_font = _font(12)
    panel_font = _font(13)

    overlay = base.copy()
    draw = ImageDraw.Draw(overlay)
    keep = {FILTER_NAMES[name] for name in only} if only else None
    # UNKNOWN_BASIS rides along with the "unknown" filter token; both
    # unknown classes share one colour and one intent.
    if keep and CLASS_UNKNOWN_SOURCE in keep:
        keep.add(CLASS_UNKNOWN_BASIS)

    for record in placed:
        if record.get("pixel_corners") is None:
            continue
        if keep is not None and record["cls"] not in keep:
            record["unplaced_reason"] = "excluded by --only"
            continue
        if record.get("unplaced_reason"):
            continue
        _draw_record(draw, record, label_font, label_mode, image_w, image_h)
        drawn_classes.add(record["cls"])

    header = _header_lines(sidecar_path, tiff_path, pass_kind, sidecar, frame, only,
                           record_count=len(placed))
    panel, truncated = _panel_lines(placed, max_panel_lines)

    probe = ImageDraw.Draw(overlay)
    line_h = _text_size(probe, "Ag", panel_font)[1] + 6
    canvas_w = max(image_w, PANEL_MIN_WIDTH)
    text_w = canvas_w - PANEL_PAD * 2

    header_rows = []
    for text, alert in header:
        for i, part in enumerate(_wrap(probe, text, panel_font, text_w)):
            header_rows.append((("    " + part) if i else part, alert))
    panel_rows = []
    for text in panel:
        for i, part in enumerate(_wrap(probe, text, panel_font, text_w)):
            panel_rows.append(("    " + part) if i else part)

    # header rows, the legend row, one blank row, the "not drawn" heading
    # (or its "none" substitute), the listed record rows, and the truncation
    # notice when there is one. One spare row of slack.
    rows = (len(header_rows) + 2 + 1 + len(panel_rows) + (1 if truncated else 0))
    panel_h = PANEL_PAD * 2 + (rows + 1) * line_h

    canvas = Image.new("RGB", (canvas_w, image_h + panel_h), PANEL_BG)
    canvas.paste(overlay, (0, 0))
    pdraw = ImageDraw.Draw(canvas)
    pdraw.line([(0, image_h), (canvas_w, image_h)], fill=PANEL_RULE, width=1)

    y = image_h + PANEL_PAD
    for text, alert in header_rows:
        pdraw.text((PANEL_PAD, y), text, font=panel_font,
                   fill=PANEL_ALERT if alert else PANEL_TEXT)
        y += line_h

    # Legend. Every class this picture actually drew, so a colour on the
    # image always has a name underneath it.
    x = PANEL_PAD
    pdraw.text((x, y), "drawn:", font=panel_font, fill=PANEL_TEXT)
    x += _text_size(pdraw, "drawn:", panel_font)[0] + 10
    if not drawn_classes:
        pdraw.text((x, y), "(no record reached a pixel -- see below)",
                   font=panel_font, fill=PANEL_ALERT)
    for cls in sorted(drawn_classes):
        pdraw.rectangle([x, y + 2, x + SWATCH_PX, y + 2 + SWATCH_PX], fill=CLASS_COLORS[cls])
        x += SWATCH_PX + 5
        pdraw.text((x, y), cls, font=panel_font, fill=PANEL_TEXT)
        x += _text_size(pdraw, cls, panel_font)[0] + 14
    y += line_h * 2

    if panel_rows:
        pdraw.text((PANEL_PAD, y), "not drawn:", font=panel_font, fill=PANEL_ALERT)
        y += line_h
        for text in panel_rows:
            pdraw.text((PANEL_PAD, y), text, font=panel_font, fill=PANEL_TEXT)
            y += line_h
        if truncated:
            pdraw.text((PANEL_PAD, y),
                       "{0} more not shown (--max-panel-lines).".format(truncated),
                       font=panel_font, fill=PANEL_ALERT)
            y += line_h
    else:
        pdraw.text((PANEL_PAD, y), "not drawn: none -- every record reached a pixel.",
                   font=panel_font, fill=PANEL_TEXT)
        y += line_h

    return canvas


def _resolve_tiff_path(sidecar_path: Path, sidecar: dict[str, Any]) -> Path:
    """The TIFF this sidecar describes.

    Same ladder as tools/link_identity_resolver.py: the recorded path when
    it still exists, else a sibling of the sidecar. Recorded paths are
    absolute on the capture machine and a sidecar is usually read somewhere
    else, which is why the sibling fallback exists at all.
    """
    recorded = sidecar.get("tiff_path")
    if recorded:
        p = Path(recorded)
        if p.exists():
            return p
    for suffix in (".tiff", ".tif"):
        candidate = sidecar_path.with_suffix(suffix)
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "Could not resolve TIFF for sidecar {0}: recorded tiff_path={1!r} does not "
        "exist and no sibling .tiff/.tif file was found".format(sidecar_path, recorded)
    )


def target_for(sidecar_path: Path, out_dir: Path | None) -> Path:
    """The overlay path one sidecar writes to. The ONE derivation.

    ``main()`` plans every target through this before writing any of them
    (see ``plan_targets``), and ``overlay_one`` derives its own through it
    when a caller does not supply one -- so the path a collision is checked
    against is always the path that is written.
    """
    return (out_dir or sidecar_path.parent) / (sidecar_path.stem + ".overlay.png")


def plan_targets(
    sidecar_paths: list[Path],
    out_dir: Path | None,
) -> tuple[dict[Path, Path], dict[Path, list[Path]]]:
    """Each sidecar's output path, and the targets more than one claims.

    Output names are built from the sidecar's STEM, so two captures of the
    same view from different runs -- ``run-a/view.json`` and
    ``run-b/view.json``, which a recursive scan collects together -- both
    name ``view.overlay.png``. Left alone with ``--out-dir``, the second
    save silently replaced the first while the CLI reported both inputs
    written: a batch missing an overlay it said it produced. Found by
    review on PR #213.

    Colliding sidecars are REFUSED rather than renamed. Any disambiguation
    this tool invented (a parent-directory prefix, a counter) would be a
    guess about what the file should be called, and could collide again at
    depth. The refusal names every claimant so the caller can re-run them
    separately or drop ``--out-dir``. Only the colliding ones are held
    back; the rest of the batch is written.

    Re-running the same batch over its own previous output is NOT a
    collision: one sidecar overwriting its own overlay is the idempotent
    case, and refusing it would make the tool unrunnable twice.
    """
    claims: dict[Path, list[Path]] = {}
    for sidecar_path in sidecar_paths:
        claims.setdefault(target_for(sidecar_path, out_dir), []).append(sidecar_path)
    targets = {paths[0]: target for target, paths in claims.items() if len(paths) == 1}
    collisions = {target: paths for target, paths in claims.items() if len(paths) > 1}
    return (targets, collisions)


def overlay_one(
    sidecar_path: Path,
    out_dir: Path | None = None,
    label_mode: str = "full",
    only: list[str] | None = None,
    max_panel_lines: int = DEFAULT_MAX_PANEL_LINES,
    out_path: Path | None = None,
) -> Path:
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    tiff_path = _resolve_tiff_path(sidecar_path, sidecar)
    with Image.open(tiff_path) as img:
        base = img.convert("RGB")
    pass_kind, placed, frame = plan_overlay(sidecar, base.size[0], base.size[1])
    canvas = render(
        base, sidecar_path, tiff_path, pass_kind, sidecar, placed, frame,
        label_mode=label_mode, only=only, max_panel_lines=max_panel_lines,
    )
    if out_path is None:
        out_path = target_for(sidecar_path, out_dir)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)
    return out_path


def collect(paths: list[str]) -> list[Path]:
    """Sidecars to draw, skipping this repo's own derived JSON artifacts."""
    out = []
    for arg in paths:
        p = Path(arg)
        if p.is_dir():
            out += sorted(
                x for x in p.rglob("*.json")
                if not x.name.endswith((".decoded.json", ".link_identity.json"))
            )
        else:
            out.append(p)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Draw a Stage A capture TIFF with its sidecar's bboxes, source and "
                    "category labels. Emits pictures only -- no scores, no pass/fail.",
    )
    ap.add_argument("paths", nargs="+",
                    help="Stage A sidecar JSON file(s) or directories containing them")
    ap.add_argument("--out-dir", default=None,
                    help="where to write the .overlay.png (default: beside the sidecar)")
    ap.add_argument("--labels", choices=("full", "id", "none"), default="full",
                    help="per-box label content (default: full = id, category, class, detail)")
    ap.add_argument("--only", action="append", choices=sorted(FILTER_NAMES),
                    help="draw only these source classes; repeatable. Excluded records are "
                         "still listed under the image, never silently dropped")
    ap.add_argument("--max-panel-lines", type=int, default=DEFAULT_MAX_PANEL_LINES,
                    help="cap on the not-drawn panel; the number cut is always stated")
    ns = ap.parse_args(argv)

    out_dir = Path(ns.out_dir) if ns.out_dir else None
    sidecar_paths = collect(ns.paths)
    targets, collisions = plan_targets(sidecar_paths, out_dir)

    failures = 0
    # Refused BEFORE anything is written, so a collision never half-runs a
    # batch: no claimant of a contested name is drawn at all.
    for target, claimants in sorted(collisions.items()):
        failures += 1
        print("REFUSED {0}: {1} sidecars would write this same file -- {2}. "
              "Re-run them separately, or drop --out-dir so each overlay lands "
              "beside its own sidecar.".format(
                  target, len(claimants), ", ".join(str(c) for c in claimants)))

    for sp in sidecar_paths:
        if sp not in targets:
            continue
        try:
            out = overlay_one(
                sp, out_dir=out_dir, label_mode=ns.labels, only=ns.only,
                max_panel_lines=ns.max_panel_lines, out_path=targets[sp],
            )
            print("OK {0} -> {1}".format(sp, out))
        except Exception as e:
            failures += 1
            print("FAILED {0}: {1}: {2}".format(sp, type(e).__name__, e))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
