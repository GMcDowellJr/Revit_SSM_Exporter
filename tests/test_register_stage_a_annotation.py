"""Applying the Stage A registration: the transform and the mark subtraction.

``tools/register_stage_a_annotation.py`` and the decoder's mark exclusion,
driven by sidecars whose ``registration_marks`` blocks are built by
PRODUCTION's own ``_registration_payload`` from PRODUCTION's own mark layout
(``registration_mark_segments``) -- so the consumer is composed with the
producer's format, not with a copy of it (CLAUDE.md defect class 1).

THE FIXTURES CARRY THE ANSWER. The annotation capture is drawn at 12 px/ft and
the model capture at 18 px/ft with a different origin, so an overlay by
integer offset -- or no transform at all -- puts every element in the wrong
place. Where each element must land on the model lattice is computed from the
drawing's own constants, never by re-running the tool's arithmetic.

Every assertion on a result reads the FILE the tool wrote (defect class 4).
"""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest
from PIL import Image

from tools import decode_stage_a_color_id as dsc
from tools import register_stage_a_annotation as reg
from tools import registration_marks as rm
from vop_interwoven.stage_a_registered_capture import _registration_payload
from vop_interwoven.stage_a_registration import (
    MARK_ARM_PX, MARK_COLOUR, registration_mark_segments)

# Annotation capture: 12 px/ft, u origin at x = 30, v = 0 at y = 330.
A_U, B_U, A_V, B_V = 12.0, 30.0, -12.0, 330.0
AW, AH = 480, 360
CROP_UV = (2.0, 3.0, 32.0, 24.0)
# Model capture: 18 px/ft over its own bounds -- a different lattice.
M = 18.0
MW, MH = 700, 520
MODEL_BOUNDS = (-1.0, -2.0, -1.0 + MW / M, -2.0 + MH / M)

# Annotation elements: id -> (colour, UV rectangle).
ELEMENTS = {50: ((40, 8, 16), (10.0, 10.0, 14.0, 12.0)),
            51: ((80, 8, 16), (20.0, 15.0, 21.0, 20.0))}
MODEL_ELEMENT = (7, (1, 2, 3), (8.0, 6.0, 12.0, 9.0))


def ax(u):
    return A_U * u + B_U


def ay(v):
    return A_V * v + B_V


def mx(u):
    return M * (u - MODEL_BOUNDS[0])


def my(v):
    return -M * (v - MODEL_BOUNDS[3])


def _marks():
    layout = registration_mark_segments(CROP_UV, 1.0 / A_U)
    assert layout["state"] == "value", layout
    return [dict(seg, id=9000 + i) for i, seg in enumerate(layout["segments"])]


def _anno_colours(marks):
    return dict((m["id"], (200, 8 * (i + 1), 16)) for i, m in enumerate(marks))


def blend(colour, t):
    """``colour`` at coverage ``t`` over white, as ExportImage anti-aliases."""
    return tuple(int(round(t * c + (1.0 - t) * 255.0)) for c in colour)


# pipeline_0928_0953's elevation: a tick on a pixel BOUNDARY exported as two
# rows at these coverages, and not one pixel of its exact colour.
FRINGE = (0.62, 0.75)


def _draw_blended_h(img, seg, colour, to_x, to_y):
    """A horizontal tick straddling the row boundary nearest its level."""
    boundary = int(round(to_y(seg["level_uv"])))
    xs = sorted(to_x(u) for u in seg["span_uv"])
    x0, x1 = int(round(xs[0])), int(round(xs[1]))
    img[boundary - 1, x0:x1] = blend(colour, FRINGE[0])
    img[boundary, x0:x1] = blend(colour, FRINGE[1])


def _draw_tick(img, seg, colour, to_x, to_y, thickness=2):
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


def _fill(img, rect_uv, colour, to_x, to_y):
    u0, v0, u1, v1 = rect_uv
    img[int(round(to_y(v1))):int(round(to_y(v0))),
        int(round(to_x(u0))):int(round(to_x(u1)))] = colour


def _record(marks):
    return {"marks": {"created": marks, "layout": {"arm_px": MARK_ARM_PX}},
            "mark_reference_source": "test", "faults": [], "restore": {}}


def _write_pair(tmp_path, marks=None, drop_anno=(), drop_model=(),
                anno_marks_record=None, with_marks=True, blended_anno_h=False,
                model_crop_offset=None, extra_anno=None):
    """A model + annotation capture of one view, as production names them."""
    marks = marks if marks is not None else _marks()
    colours = _anno_colours(marks)
    anno = np.full((AH, AW, 3), 255, dtype=np.uint8)
    elements = dict(ELEMENTS)
    elements.update(extra_anno or {})
    for eid, (colour, rect) in elements.items():
        _fill(anno, rect, colour, ax, ay)
    for m in marks:
        if m["key"] in drop_anno:
            continue
        if blended_anno_h and m["orientation"] == "horizontal":
            _draw_blended_h(anno, m, colours[m["id"]], ax, ay)
        else:
            _draw_tick(anno, m, colours[m["id"]], ax, ay)
    model = np.full((MH, MW, 3), 255, dtype=np.uint8)
    _fill(model, MODEL_ELEMENT[2], MODEL_ELEMENT[1], mx, my)
    for m in marks:
        if m["key"] not in drop_model:
            _draw_tick(model, m, MARK_COLOUR, mx, my)
    model_tiff, anno_tiff = tmp_path / "V_1.tiff", tmp_path / "V_1_anno.tiff"
    Image.fromarray(model).save(str(model_tiff), format="TIFF")
    Image.fromarray(anno).save(str(anno_tiff), format="TIFF")
    anno_map = dict((str(eid), list(c)) for eid, (c, _r) in elements.items())
    anno_map.update((str(k), list(v)) for k, v in colours.items())
    model_side = {"view_id": 1, "tiff_path": str(model_tiff),
                  "bounds_xy": list(MODEL_BOUNDS),
                  "resolution": {"pixel_size": MW, "requested_pixel_size": MW,
                                 "view_scale": 96.0},
                  "color_assignment_map": {str(MODEL_ELEMENT[0]):
                                           list(MODEL_ELEMENT[1])},
                  "applied_display_style": "FlatColors",
                  "applied_smooth_edges": False}
    if model_crop_offset is not None:
        model_side["model_crop_offset_uv"] = list(model_crop_offset)
    anno_side = {"view_id": 1, "pass": "annotation", "tiff_path": str(anno_tiff),
                 "model_pass_tiff_path": str(model_tiff),
                 "resolution": {"pixel_size": AW, "requested_pixel_size": AW,
                                "view_scale": 96.0},
                 "color_assignment_map": anno_map,
                 "applied_display_style": "FlatColors",
                 "applied_smooth_edges": False}
    if with_marks:
        model_side["registration_marks"] = _registration_payload(
            "model", _record(marks), shared_colour=MARK_COLOUR)
        anno_side["registration_marks"] = _registration_payload(
            "annotation", anno_marks_record or _record(marks),
            colours_by_id=anno_map)
    model_path, anno_path = tmp_path / "V_1.json", tmp_path / "V_1_anno.json"
    model_path.write_text(json.dumps(model_side))
    anno_path.write_text(json.dumps(anno_side))
    return anno_path, model_path, colours


def _persisted(anno_path):
    _tiff, json_path = reg.output_paths(anno_path)
    return json.loads(json_path.read_text())


def _bbox_of(pixels, colour):
    ys, xs = np.nonzero(np.all(pixels == np.asarray(colour, dtype=np.uint8), axis=2))
    if len(xs) == 0:
        return None
    return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


# ======================================================================
# the transform
# ======================================================================

def test_the_annotation_capture_lands_on_the_model_lattice(tmp_path):
    anno_path, _model, _c = _write_pair(tmp_path)
    reg.register(anno_path)
    record = _persisted(anno_path)
    assert record["status"] == "registered", record["refusals"]
    assert record["annotation_to_model_px"]["scale_x"] == pytest.approx(M / A_U, rel=1e-3)
    assert record["annotation_to_model_px"]["scale_y"] == pytest.approx(M / A_U, rel=1e-3)
    out = np.asarray(Image.open(record["registered_tiff"]).convert("RGB"))
    lattice = record["lattice"]
    assert out.shape == (lattice["canvas_h"], lattice["canvas_w"], 3)
    ox, oy = lattice["model_image_origin_px"]
    for eid, (colour, (u0, v0, u1, v1)) in ELEMENTS.items():
        # Where the MODEL lattice puts this UV rectangle, from the drawing's
        # constants, shifted by where the model image sits in the canvas.
        # Half a source pixel of drawing rounding is 0.75 model px.
        want = (mx(u0) + ox, my(v1) + oy, mx(u1) + ox, my(v0) + oy)
        got = _bbox_of(out, colour)
        assert got is not None, eid
        for g, w in zip(got, want):
            assert abs(g - w) <= 1.5, (eid, got, want)


def test_the_record_names_the_tiff_it_sits_beside(tmp_path):
    """The record is written LAST, so its hash is the file's."""
    anno_path, _model, _c = _write_pair(tmp_path)
    reg.register(anno_path)
    record = _persisted(anno_path)
    tiff_path, _json = reg.output_paths(anno_path)
    assert record["registered_tiff"] == str(tiff_path)
    assert record["registered_tiff_sha256"] == hashlib.sha256(
        tiff_path.read_bytes()).hexdigest()


def test_the_resample_invents_no_colour(tmp_path):
    """Nearest neighbour: every output colour is a source colour or white. A
    blended pixel is a colour no element owns."""
    anno_path, _model, _c = _write_pair(tmp_path)
    reg.register(anno_path)
    record = _persisted(anno_path)
    out = np.asarray(Image.open(record["registered_tiff"]).convert("RGB"))
    src = np.asarray(Image.open(tmp_path / "V_1_anno.tiff").convert("RGB"))
    assert (set(map(tuple, out.reshape(-1, 3)))
            <= set(map(tuple, src.reshape(-1, 3))))


def test_resample_onto_places_pixels_by_their_centres():
    """Exact, hand-computed: scale 2 from offset 0 puts source column i at
    output columns 2i and 2i+1; offset 1 shifts it by one and leaves column 0
    uncovered."""
    src = np.zeros((1, 3, 3), dtype=np.uint8)
    src[0, :, 0] = (10, 20, 30)
    out, uncovered = reg.resample_onto(
        src, {"scale_x": 2.0, "offset_x": 1.0, "scale_y": 1.0, "offset_y": 0.0}, 7, 1)
    assert out[0, :, 0].tolist() == [255, 10, 10, 20, 20, 30, 30]
    assert uncovered == 1


# ======================================================================
# the subtraction
# ======================================================================

def test_the_marks_are_subtracted_from_the_annotation_capture(tmp_path):
    anno_path, _model, colours = _write_pair(tmp_path)
    reg.register(anno_path)
    record = _persisted(anno_path)
    out = np.asarray(Image.open(record["registered_tiff"]).convert("RGB"))
    for colour in colours.values():
        assert _bbox_of(out, colour) is None, colour
    assert record["excluded_element_ids"] == sorted(colours)
    assert record["mark_subtraction"]["annotation_mark_pixels_removed"] > 0
    # CONTROL: the element content survived the subtraction.
    for colour, _rect in ELEMENTS.values():
        assert _bbox_of(out, colour) is not None


def test_the_decoder_does_not_decode_annotation_ticks_as_elements(tmp_path):
    anno_path, _model, colours = _write_pair(tmp_path)
    doc = json.loads(dsc.decode_one(anno_path).read_text())
    assert doc["registration_marks"]["status"] == "subtracted"
    assert sorted(doc["registration_marks"]["excluded_element_ids"]) == sorted(colours)
    assert set(doc["elements"]) == set(str(e) for e in ELEMENTS)
    assert doc["off_palette_foreground_pixel_count"] == 0


def test_without_the_record_the_ticks_do_decode_as_elements(tmp_path):
    """The CONTROL for the test above: the fixture's ticks really are in the
    decode map, so the exclusion is what removed them."""
    anno_path, _model, colours = _write_pair(tmp_path, with_marks=False)
    doc = json.loads(dsc.decode_one(anno_path).read_text())
    assert doc["registration_marks"] == {"status": "absent"}
    assert set(str(k) for k in colours) <= set(doc["elements"])


def test_the_decoder_moves_model_ticks_out_of_the_off_palette_count(tmp_path):
    _anno, model_path, _c = _write_pair(tmp_path)
    doc = json.loads(dsc.decode_one(model_path).read_text())
    block = doc["registration_marks"]
    assert block["status"] == "subtracted" and block["pass"] == "model"
    assert block["fit_status"] == "value" and block["found_count"] == 12
    assert block["mark_pixels_subtracted"] > 0
    assert doc["off_palette_foreground_pixel_count"] == 0
    assert set(doc["elements"]) == {str(MODEL_ELEMENT[0])}


def test_model_ticks_without_the_record_are_contamination(tmp_path):
    """The CONTROL: the same pixels, unannounced, are off-palette -- and the
    count equals what the test above subtracted."""
    marked_dir, plain_dir = tmp_path / "marked", tmp_path / "plain"
    marked_dir.mkdir()
    plain_dir.mkdir()
    _a, marked, _c = _write_pair(marked_dir)
    _a, plain, _c = _write_pair(plain_dir, with_marks=False)
    subtracted = json.loads(dsc.decode_one(marked).read_text())[
        "registration_marks"]["mark_pixels_subtracted"]
    doc = json.loads(dsc.decode_one(plain).read_text())
    assert doc["off_palette_foreground_pixel_count"] == subtracted


def test_an_unreadable_record_subtracts_nothing(tmp_path):
    anno_path, _model, colours = _write_pair(tmp_path)
    side = json.loads(anno_path.read_text())
    side["registration_marks"]["schema"] = "vop.stage_a.registered_capture.v0"
    anno_path.write_text(json.dumps(side))
    doc = json.loads(dsc.decode_one(anno_path).read_text())
    assert doc["registration_marks"]["status"] == "refused"
    assert set(str(k) for k in colours) <= set(doc["elements"])


# ======================================================================
# refusals
# ======================================================================

def test_one_level_left_on_an_axis_is_refused_not_fitted(tmp_path):
    gone = ("left_bottom_v", "left_top_v", "mid_bottom_v", "mid_top_v")
    anno_path, _model, _c = _write_pair(tmp_path, drop_anno=gone)
    reg.register(anno_path)
    record = _persisted(anno_path)
    assert record["status"] == "refused"
    assert record["registered_tiff"] is None
    assert any("annotation capture" in r and "both of its levels" in r
               for r in record["refusals"]), record["refusals"]
    assert not reg.output_paths(anno_path)[0].exists()


def test_one_tick_short_still_registers(tmp_path):
    """The CONTROL for the refusal: three ticks on an axis are still two
    levels."""
    anno_path, _model, _c = _write_pair(tmp_path, drop_anno=("left_top_v",))
    reg.register(anno_path)
    record = _persisted(anno_path)
    assert record["status"] == "registered", record["refusals"]
    assert record["annotation_fit"]["points"]["u"] == 5


def test_a_model_capture_with_one_level_is_refused_too(tmp_path):
    """No fall-back to the model's recorded lattice: that is a premise, and
    substituting it on failure would be a guess."""
    gone = ("left_bottom_h", "right_bottom_h", "left_mid_h", "right_mid_h")
    anno_path, _model, _c = _write_pair(tmp_path, drop_model=gone)
    reg.register(anno_path)
    record = _persisted(anno_path)
    assert record["status"] == "refused"
    assert any(r.startswith("model capture") for r in record["refusals"])


def test_a_refusal_removes_the_previous_runs_tiff(tmp_path):
    anno_path, _model, _c = _write_pair(tmp_path)
    reg.register(anno_path)
    tiff_path, _json = reg.output_paths(anno_path)
    assert tiff_path.exists()
    gone = ("left_bottom_v", "left_top_v", "mid_bottom_v", "mid_top_v")
    _write_pair(tmp_path, drop_anno=gone)
    reg.register(anno_path)
    assert not tiff_path.exists()
    assert _persisted(anno_path)["stale_registered_tiff_removed"] is True


def test_marks_from_another_run_are_refused(tmp_path):
    marks = _marks()
    other = [dict(m, level_uv=m["level_uv"] + 1.0) for m in marks]
    anno_path, _model, _c = _write_pair(tmp_path, marks=marks,
                                        anno_marks_record=_record(other))
    reg.register(anno_path)
    record = _persisted(anno_path)
    assert record["status"] == "refused"
    assert any("different ticks" in r for r in record["refusals"])


def test_a_capture_without_marks_is_refused(tmp_path):
    anno_path, _model, _c = _write_pair(tmp_path, with_marks=False)
    reg.register(anno_path)
    record = _persisted(anno_path)
    assert record["status"] == "refused"
    assert any("not a registered capture" in r for r in record["refusals"])


def test_a_mirrored_transform_is_refused():
    assert rm.transform_refusal({"scale_x": 1.0, "offset_x": 0.0,
                                 "scale_y": -1.0, "offset_y": 0.0}) is not None
    assert rm.transform_refusal({"scale_x": 0.0, "offset_x": 0.0,
                                 "scale_y": 1.0, "offset_y": 0.0}) is not None
    assert rm.transform_refusal(None) is not None
    # CONTROL
    assert rm.transform_refusal({"scale_x": 1.5, "offset_x": -3.0,
                                 "scale_y": 1.5, "offset_y": 2.0}) is None


def test_main_exit_codes(tmp_path):
    good, bad = tmp_path / "good", tmp_path / "bad"
    good.mkdir()
    bad.mkdir()
    _write_pair(good)
    _write_pair(bad, with_marks=False)
    assert reg.main([str(good)]) == 0
    assert reg.main([str(bad)]) == 1
    assert reg.main([str(tmp_path / "empty_nothing_here")]) == 2


# ======================================================================
# found by the first real run (pipeline_0928_0953)
# ======================================================================

def test_anti_aliased_ticks_register(tmp_path):
    """The elevation's annotation capture: every horizontal tick exported as a
    two-row blend with no pixel of its exact colour. Registered, and placed
    as well as the exact-colour fixture is."""
    anno_path, _model, _c = _write_pair(tmp_path, blended_anno_h=True)
    reg.register(anno_path)
    record = _persisted(anno_path)
    assert record["status"] == "registered", record["refusals"]
    assert record["annotation_fit"]["found_count"] == 12
    assert record["annotation_fit"]["residual_max_px"]["v"] < 0.6
    assert record["annotation_to_model_px"]["scale_y"] == pytest.approx(M / A_U, rel=2e-3)


def test_exact_colour_alone_cannot_see_those_ticks(tmp_path):
    """The CONTROL, and the defect reproduced: the same capture read exact-
    colour only is 6 of 12 and unfittable."""
    anno_path, _model, colours = _write_pair(tmp_path, blended_anno_h=True)
    px = rm.load_rgb(tmp_path / "V_1_anno.tiff")
    fit = rm.fit_marks_in_pixels(px, _marks(), colour_by_id=colours)
    assert fit["status"] == "unavailable" and fit["found_count"] == 6


def test_blended_tick_pixels_are_subtracted(tmp_path):
    anno_path, _model, colours = _write_pair(tmp_path, blended_anno_h=True)
    reg.register(anno_path)
    out = np.asarray(Image.open(_persisted(anno_path)["registered_tiff"]).convert("RGB"))
    for colour in colours.values():
        for t in FRINGE:
            assert _bbox_of(out, blend(colour, t)) is None, (colour, t)


def test_an_element_whose_colour_is_a_tick_blend_stays_an_element(tmp_path):
    """A palette colour is an element, never a fringe -- even when it lies
    exactly on a tick colour's line to white."""
    marks = _marks()
    victim = blend(_anno_colours(marks)[marks[0]["id"]], 0.5)
    extra = {60: (victim, (5.0, 16.0, 7.0, 19.0))}
    anno_path, _model, _c = _write_pair(tmp_path, extra_anno=extra)
    reg.register(anno_path)
    record = _persisted(anno_path)
    out = np.asarray(Image.open(record["registered_tiff"]).convert("RGB"))
    assert _bbox_of(out, victim) is not None
    assert record["annotation_fit"]["residual_max_px"]["u"] < 0.6


def test_the_same_colour_unreserved_would_be_taken_for_a_tick(tmp_path):
    """The CONTROL: without the palette the element is absorbed into the
    tick's pixels -- so the reservation is what kept it."""
    marks = _marks()
    colours = _anno_colours(marks)
    victim = blend(colours[marks[0]["id"]], 0.5)
    img = np.full((AH, AW, 3), 255, dtype=np.uint8)
    _fill(img, (5.0, 16.0, 7.0, 19.0), victim, ax, ay)
    packed = rm.pack_pixels(img)
    free, _ = rm.mark_colour_keys(packed, [colours[marks[0]["id"]]], blends=True)
    kept, _ = rm.mark_colour_keys(packed, [colours[marks[0]["id"]]], blends=True,
                                  reserved_colours=[victim])
    key = rm._pack(victim)
    assert key in free[colours[marks[0]["id"]]][0].tolist()
    assert key not in kept[colours[marks[0]["id"]]][0].tolist()


def test_model_ticks_that_do_not_span_the_image_are_still_assigned():
    """Plan_CropInActive: the marks sit on the raster frame in the middle of a
    far larger crop A (y 4408-5592 of 10000). Image thirds put all six
    horizontal ticks in "mid"; the marks' own extent does not."""
    ref = (0.0, 0.0, 30.0, 20.0)
    marks = [dict(seg, id=i) for i, seg in enumerate(
        registration_mark_segments(ref, 1.0 / 12.0)["segments"])]
    img = np.full((1200, 600, 3), 255, dtype=np.uint8)
    to_x = lambda u: 12.0 * u + 120.0          # noqa: E731
    to_y = lambda v: -12.0 * v + 720.0         # noqa: E731  (ref spans y 480-720)
    for m in marks:
        _draw_tick(img, m, MARK_COLOUR, to_x, to_y)
    fit = rm.fit_marks_in_pixels(img, marks, shared_colour=MARK_COLOUR)
    assert fit["status"] == "value", fit.get("missing")
    assert fit["found_count"] == 12
    assert fit["components"]["merged_into_one_tick"] == 0
    assert fit["px_per_ft_v"] == pytest.approx(12.0, abs=0.05)


def test_the_canvas_is_the_union_of_crop_a_and_the_measured_annotation_rect(tmp_path):
    """C7 (Greg, 2026-09-29). Ink beyond the model image -- here an element
    left of crop A -- is kept, on the model's pixel phase, and nothing is
    off the canvas."""
    outside = {61: ((120, 8, 16), (-2.5, 1.0, -1.5, 3.0))}   # u < crop A's u0
    anno_path, _model, _c = _write_pair(tmp_path, extra_anno=outside)
    reg.register(anno_path)
    record = _persisted(anno_path)
    assert record["status"] == "registered", record["refusals"]
    lattice = record["lattice"]
    ax0, ay0, ax1, ay1 = lattice["annotation_rect_model_px"]
    x0, y0 = min(0, ax0), min(0, ay0)
    x1, y1 = max(MW, ax1), max(MH, ay1)
    # The fixture must make the union differ from crop A on both axes.
    assert x0 < 0 and y0 < 0 and y1 > MH
    assert lattice["model_image_origin_px"] == [-x0, -y0]
    assert (lattice["canvas_w"], lattice["canvas_h"]) == (x1 - x0, y1 - y0)
    assert lattice["canvas_origin_model_px"] == [x0, y0]
    # The measured rect is where the drawing's own constants put the
    # annotation image, to within a pixel (the fit is not exact).
    for got, want in zip((ax0, ay0, ax1, ay1),
                         (mx((0 - B_U) / A_U), my((0 - B_V) / A_V),
                          mx((AW - B_U) / A_U), my((AH - B_V) / A_V))):
        assert abs(got - want) <= 1.5, (got, want)
    # The canvas origin in view UV is the model lattice's inverse at (x0, y0).
    assert lattice["canvas_origin_uv"] == pytest.approx(
        [MODEL_BOUNDS[0] + x0 / M, MODEL_BOUNDS[3] - y0 / M], abs=0.05)
    assert record["losses"]["ink_pixels_off_canvas"] == 0
    out = np.asarray(Image.open(record["registered_tiff"]).convert("RGB"))
    ox, oy = lattice["model_image_origin_px"]
    colour, (u0, v0, u1, v1) = outside[61]
    got = _bbox_of(out, colour)
    want = (mx(u0) + ox, my(v1) + oy, mx(u1) + ox, my(v0) + oy)
    assert got is not None
    for g, w in zip(got, want):
        assert abs(g - w) <= 1.5, (got, want)
    # The model image's own content keeps its place, shifted by the origin.
    colour, (u0, v0, u1, v1) = ELEMENTS[50]
    assert abs(_bbox_of(out, colour)[0] - (mx(u0) + ox)) <= 1.5


def test_the_grid_is_no_longer_a_term_of_the_canvas(tmp_path):
    """The CONTROL that discriminates the new rule from the old one: the old
    canvas grew with model_crop_offset_uv (the grid, frame B); the new one
    must not move when it changes."""
    a_dir, b_dir = tmp_path / "a", tmp_path / "b"
    a_dir.mkdir()
    b_dir.mkdir()
    anno_a, _m, _c = _write_pair(a_dir)
    anno_b, _m, _c = _write_pair(b_dir, model_crop_offset=(2.0, 1.0, -3.0, -2.0))
    reg.register(anno_a)
    reg.register(anno_b)
    la, lb = _persisted(anno_a)["lattice"], _persisted(anno_b)["lattice"]
    for key in ("canvas_w", "canvas_h", "model_image_origin_px",
                "annotation_rect_model_px"):
        assert la[key] == lb[key], key


def test_a_fringe_pulls_the_centre_line_by_its_coverage():
    """Hand-computed: row 10 at coverage 0.2 and row 11 solid put the centre
    line at (10.5*0.2 + 11.5*1.0) / 1.2 = 11.3333, not the unweighted 11.0."""
    ys, xs = np.array([10] * 5 + [11] * 5), np.array(list(range(5)) * 2)
    weights = np.array([0.2] * 5 + [1.0] * 5)
    measured = rm._tick_measure(ys, xs, "horizontal", weights)
    assert measured["centre_px"] == pytest.approx(11.0 + 1.0 / 3.0)
    assert measured["blended_px"] == 5
    # CONTROL: unweighted, as the probe analyzer measures.
    assert rm._tick_measure(ys, xs, "horizontal")["centre_px"] == pytest.approx(11.0)


# ----------------------------------------------------------------------
# stray look-alike fringes (Plan_CropActive, RCP, Plan_CropInActive)
# ----------------------------------------------------------------------

def _stray(img, colour, x0, y0, w, h, t=0.55):
    img[y0:y0 + h, x0:x0 + w] = blend(colour, t)


def _pair_with_strays(tmp_path, draw):
    """``_write_pair`` with ``draw(anno pixels, marks, colours)`` applied to
    the annotation capture after it is written."""
    marks = _marks()
    anno_path, model_path, colours = _write_pair(tmp_path, marks=marks)
    tiff = tmp_path / "V_1_anno.tiff"
    img = np.array(Image.open(tiff).convert("RGB"))
    draw(img, marks, colours)
    Image.fromarray(img).save(str(tiff), format="TIFF")
    return anno_path, marks, colours


def _left_mid_h(marks):
    return [m for m in marks if m["key"] == "left_mid_h"][0]


def test_a_compact_fringe_blob_is_not_a_tick(tmp_path):
    """13 x 8 px of a tick's blend colour far from the tick (the shape beside
    text on Plan_CropInActive): not fitted, and not erased."""
    def draw(img, marks, colours):
        _stray(img, colours[_left_mid_h(marks)["id"]], 300, 200, 13, 8)
    anno_path, marks, colours = _pair_with_strays(tmp_path, draw)
    reg.register(anno_path)
    record = _persisted(anno_path)
    assert record["status"] == "registered", record["refusals"]
    assert max(record["annotation_fit"]["residual_max_px"].values()) < 0.6
    src = np.asarray(Image.open(tmp_path / "V_1_anno.tiff").convert("RGB"))
    stray = blend(colours[_left_mid_h(marks)["id"]], 0.55)
    assert (src[200:208, 300:313] == stray).all()      # still there to register


def test_a_line_shaped_fringe_off_the_ticks_line_is_not_a_tick(tmp_path):
    """10 x 3 px, horizontal like the tick, with a quarter of its coverage --
    passes shape and size, and is still off the tick's line."""
    def draw(img, marks, colours):
        m = _left_mid_h(marks)
        _stray(img, colours[m["id"]], 300, 200, 20, 3, t=0.9)
    anno_path, marks, _c = _pair_with_strays(tmp_path, draw)
    reg.register(anno_path)
    record = _persisted(anno_path)
    assert record["status"] == "registered", record["refusals"]
    assert max(record["annotation_fit"]["residual_max_px"].values()) < 0.6


def test_a_tick_split_by_crossing_ink_keeps_both_halves(tmp_path):
    """The CONTROL for the line rule: the two halves of a crossed tick are on
    one line, abutting, and both stay -- so the tick keeps its full length."""
    def draw(img, marks, colours):
        m = _left_mid_h(marks)
        x_mid = int(round(ax(sum(m["span_uv"]) / 2.0)))
        img[:, x_mid:x_mid + 3] = (0, 0, 0)
    anno_path, marks, _c = _pair_with_strays(tmp_path, draw)
    px = rm.load_rgb(tmp_path / "V_1_anno.tiff")
    anno_side = json.loads(anno_path.read_text())
    fit = rm.fit_recorded_marks(px, anno_side["registration_marks"],
                                reserved_colours=rm.palette_colours(anno_side))
    tick = [t for t in fit["ticks"] if t["key"] == "left_mid_h"][0]
    m = _left_mid_h(marks)
    assert tick["end_px"][1] - tick["end_px"][0] == pytest.approx(
        abs(ax(m["span_uv"][1]) - ax(m["span_uv"][0])), abs=1.0)


def test_a_fit_far_from_its_own_ticks_is_refused(tmp_path):
    """The residual bound: both mid vertical ticks recorded 0.5 ft (6 px) from
    where they drew. Fitted, and not applied."""
    marks = _marks()
    shifted = [dict(m, level_uv=m["level_uv"] + 0.5)
               if m["key"] in ("mid_bottom_v", "mid_top_v") else m for m in marks]
    anno_path, _model, _c = _write_pair(tmp_path, marks=marks,
                                        anno_marks_record=_record(shifted))
    # The model sidecar must describe the same ticks, or identity refuses first.
    model_path = tmp_path / "V_1.json"
    side = json.loads(model_path.read_text())
    side["registration_marks"] = _registration_payload(
        "model", _record(shifted), shared_colour=MARK_COLOUR)
    model_path.write_text(json.dumps(side))
    reg.register(anno_path)
    record = _persisted(anno_path)
    assert record["status"] == "refused"
    assert any("residual" in r for r in record["refusals"]), record["refusals"]
    assert record["annotation_fit"]["status"] == "value"


def _model_fit_with(tmp_path, draw):
    """The model capture's fit, production-path, after ``draw(pixels)``."""
    _anno, model_path, _c = _write_pair(tmp_path)
    tiff = tmp_path / "V_1.tiff"
    img = np.array(Image.open(tiff).convert("RGB"))
    draw(img)
    Image.fromarray(img).save(str(tiff), format="TIFF")
    side = json.loads(model_path.read_text())
    return rm.fit_recorded_marks(img, side["registration_marks"],
                                 reserved_colours=rm.palette_colours(side))


def test_model_capture_a_compact_blob_of_the_mark_colour_is_not_a_tick(tmp_path):
    """In the model capture all twelve ticks share one colour, so there is no
    per-tick line to test against; a compact blob must fail on SHAPE."""
    fit = _model_fit_with(tmp_path, lambda img: _stray(img, MARK_COLOUR, 5, 5, 13, 8))
    assert fit["status"] == "value"
    assert fit["components"]["components"] == 12
    assert max(fit["residual_max_px"].values()) < 0.6


def test_model_capture_a_faint_sliver_of_the_mark_colour_is_not_a_tick(tmp_path):
    """Line-shaped but a sliver: fails on COVERAGE against the real ticks."""
    fit = _model_fit_with(tmp_path, lambda img: _stray(img, MARK_COLOUR, 690, 510, 8, 1,
                                                       t=0.6))
    assert fit["status"] == "value"
    assert fit["components"]["components"] == 12
    assert fit["components"]["unassigned_components"] == []
    assert max(fit["residual_max_px"].values()) < 0.6


def test_model_capture_clean_is_twelve_pieces(tmp_path):
    """The CONTROL: nothing dropped from a clean capture."""
    fit = _model_fit_with(tmp_path, lambda img: None)
    assert fit["components"]["components"] == 12
    assert fit["components"]["stray_fringe_pieces_dropped"] == 0


def test_a_short_exact_piece_of_a_crossed_tick_is_still_subtracted(tmp_path):
    """Ink crossing a tick near one END leaves a short exact-colour piece,
    well under a quarter of the long one's coverage. The tick's own colour is
    reserved to it, so the piece is the tick's: fitted and subtracted, never
    filtered out as a stray fringe (review, PR #219)."""
    def draw(img, marks, colours):
        m = _left_mid_h(marks)
        xs = sorted(ax(u) for u in m["span_uv"])
        x_cut = int(round(xs[0])) + 4          # 4 px of tick before the cut
        img[:, x_cut:x_cut + 3] = (0, 0, 0)
    anno_path, marks, colours = _pair_with_strays(tmp_path, draw)
    reg.register(anno_path)
    record = _persisted(anno_path)
    assert record["status"] == "registered", record["refusals"]
    out = np.asarray(Image.open(record["registered_tiff"]).convert("RGB"))
    assert _bbox_of(out, colours[_left_mid_h(marks)["id"]]) is None
    # And the decoder: the same tick colour is gone from the off-palette count.
    doc = json.loads(dsc.decode_one(anno_path).read_text())
    assert doc["off_palette_foreground_pixel_count"] == 0


def test_sidecars_of_two_views_are_refused(tmp_path):
    """Matching ticks do not name a view: a model sidecar from another view,
    carrying a copied record, must not register (review, PR #219)."""
    anno_path, model_path, _c = _write_pair(tmp_path)
    side = json.loads(model_path.read_text())
    side["view_id"] = 2
    model_path.write_text(json.dumps(side))
    reg.register(anno_path)
    record = _persisted(anno_path)
    assert record["status"] == "refused"
    assert any("same view" in r for r in record["refusals"]), record["refusals"]


def test_a_link_category_colour_near_the_mark_colour_is_not_a_tick(tmp_path):
    """(138, 252, 12) is within blend tolerance of MARK_COLOUR's line to white.
    As a linked category's colour it is reserved: its long line is not a
    fringe, not fitted, not subtracted (review, PR #219)."""
    link = (138, 252, 12)
    assert rm.blend_coverage(np.array([rm._pack(link)]), MARK_COLOUR)[0].size == 1
    _anno, model_path, _c = _write_pair(tmp_path)
    tiff = tmp_path / "V_1.tiff"
    img = np.array(Image.open(tiff).convert("RGB"))
    ref = _left_mid_h(_marks())
    y = int(round(my(ref["level_uv"])))
    img[y - 1:y + 1, 60:300] = link          # a long link line ON a tick's row
    Image.fromarray(img).save(str(tiff), format="TIFF")
    side = json.loads(model_path.read_text())
    side["link_category_color_map"] = {"Walls": list(link)}
    model_path.write_text(json.dumps(side))
    fit = rm.fit_recorded_marks(img, side["registration_marks"],
                                reserved_colours=rm.palette_colours(side))
    assert fit["status"] == "value"
    assert max(fit["residual_max_px"].values()) < 0.6
    mask = rm.mark_ink_mask(img, side["registration_marks"], rm.palette_colours(side))
    assert not mask[y - 1:y + 1, 150:250].any()


# ======================================================================
# T1: minimum-size ticks at ModelCallout's annotation scale
# ======================================================================

@pytest.mark.parametrize("thickness", [1, 2])
def test_32px_ticks_register_at_0_63x_annotation_scale(tmp_path, monkeypatch, thickness):
    """T1 shrinks the ticks to MARK_ARM_PX = 32 px on the MODEL lattice.
    pipeline_0928_0953's ModelCallout drew its annotation capture at
    1/1.587 = 0.63x the model lattice, so there each tick is ~20 px long --
    the smallest this run will see. Drawn at 1 px (the thinnest lineweight
    T1 selects) and at 2 px, registration_marks must still find all twelve
    and register at the known scale."""
    assert MARK_ARM_PX == 32.0
    scale = 0.63
    import sys as _sys
    mod = _sys.modules[__name__]
    monkeypatch.setattr(mod, "A_U", scale * M)
    monkeypatch.setattr(mod, "A_V", -scale * M)
    # Marks are laid out in MODEL-lattice pixels, as production lays them.
    layout = registration_mark_segments(CROP_UV, 1.0 / M)
    assert layout["state"] == "value" and layout["arm_px"] == 32.0, layout
    marks = [dict(seg, id=9000 + i) for i, seg in enumerate(layout["segments"])]
    tick_len_anno = 32.0 * scale
    assert 19.0 < tick_len_anno < 21.0

    def _tick(img, seg, colour, to_x, to_y):
        # Exactly ``thickness`` rows/columns, starting at the pixel holding the
        # tick's level (_draw_tick's rounded slice can come out EMPTY at 1 px).
        if seg["orientation"] == "horizontal":
            y0 = int(np.floor(to_y(seg["level_uv"])))
            xs = sorted(to_x(u) for u in seg["span_uv"])
            img[y0:y0 + thickness, int(round(xs[0])):int(round(xs[1]))] = colour
        else:
            x0 = int(np.floor(to_x(seg["level_uv"])))
            ys = sorted(to_y(v) for v in seg["span_uv"])
            img[int(round(ys[0])):int(round(ys[1])), x0:x0 + thickness] = colour

    monkeypatch.setattr(mod, "_draw_tick", _tick)
    anno_path, _model, _c = _write_pair(tmp_path, marks=marks)
    reg.register(anno_path)
    record = _persisted(anno_path)
    assert record["status"] == "registered", record.get("refusals")
    for name in ("annotation_fit", "model_fit"):
        assert record[name]["found_count"] == 12, record[name]
        assert max(record[name]["residual_max_px"].values()) <= 2.0, record[name]
    t = record["annotation_to_model_px"]
    # 0.5 %: the fixture's ticks sit on whole pixels, so a 1 px tick's level
    # is quantised by up to half a pixel over a ~200 px baseline (measured
    # here at 0.22 %). pipeline_0928_0953's ModelCallout itself fitted
    # 1.587381 / 1.591730 on x / y.
    assert t["scale_x"] == pytest.approx(1.0 / scale, rel=5e-3)
    assert t["scale_y"] == pytest.approx(1.0 / scale, rel=5e-3)


@pytest.mark.parametrize("scale,offset", [(2.0, -10.0), (1.5, -40.3), (1.0, -7.0),
                                          (0.63, -12.4), (1.1516, 3.2)])
def test_the_canvas_holds_exactly_what_the_resampler_draws(scale, offset):
    """Composed with resample_onto, not restated (review, PR #221): at scale
    2, offset -10 the old centre-based bound started the canvas at -9 while
    the resampler also fills -10 from source column 0, clipping it.

    A source row with a distinct colour per column is resampled onto the
    canvas output_canvas() returns. Every column the resampler would draw
    must land on the canvas, and the canvas must not add a column the
    resampler leaves white beyond the model image."""
    src_w, model_w = 40, 60
    source = np.zeros((1, src_w, 3), dtype=np.uint8)
    source[0, :, 0] = np.arange(src_w)
    source[0, :, 1] = 7
    transform = {"scale_x": scale, "offset_x": offset, "scale_y": 1.0, "offset_y": 0.0}
    canvas = reg.output_canvas(None, model_w, 1, transform, src_w, 1)
    on_canvas = dict(transform, offset_x=offset + canvas["shift_x"])
    out, _uncovered = reg.resample_onto(source, on_canvas, canvas["canvas_w"], 1)
    drawn = out[0][out[0][:, 1] == 7]
    # What an UNBOUNDED resample would draw: every column in its support.
    wide = 10 * (src_w + model_w)
    ref, _u = reg.resample_onto(source, dict(transform, offset_x=offset + wide),
                                3 * wide, 1)
    want = set(ref[0][ref[0][:, 1] == 7][:, 0].tolist())
    assert set(drawn[:, 0].tolist()) == want
    assert int((ref[0][:, 1] == 7).sum()) == len(drawn)   # no pixel clipped
    # Tight: the canvas edges beyond the model image are drawn, not white.
    ink = out[0][:, 1] == 7
    if canvas["shift_x"] > 0:
        assert ink[0]
    if canvas["canvas_w"] - canvas["shift_x"] > model_w:
        assert ink[-1]
