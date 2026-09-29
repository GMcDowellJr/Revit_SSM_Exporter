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
    MARK_COLOUR, registration_mark_segments)

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
    return {"marks": {"created": marks, "layout": {"arm_px": 96.0}},
            "mark_reference_source": "test", "faults": [], "restore": {}}


def _write_pair(tmp_path, marks=None, drop_anno=(), drop_model=(),
                anno_marks_record=None, with_marks=True):
    """A model + annotation capture of one view, as production names them."""
    marks = marks if marks is not None else _marks()
    colours = _anno_colours(marks)
    anno = np.full((AH, AW, 3), 255, dtype=np.uint8)
    for eid, (colour, rect) in ELEMENTS.items():
        _fill(anno, rect, colour, ax, ay)
    for m in marks:
        if m["key"] not in drop_anno:
            _draw_tick(anno, m, colours[m["id"]], ax, ay)
    model = np.full((MH, MW, 3), 255, dtype=np.uint8)
    _fill(model, MODEL_ELEMENT[2], MODEL_ELEMENT[1], mx, my)
    for m in marks:
        if m["key"] not in drop_model:
            _draw_tick(model, m, MARK_COLOUR, mx, my)
    model_tiff, anno_tiff = tmp_path / "V_1.tiff", tmp_path / "V_1_anno.tiff"
    Image.fromarray(model).save(str(model_tiff), format="TIFF")
    Image.fromarray(anno).save(str(anno_tiff), format="TIFF")
    anno_map = dict((str(eid), list(c)) for eid, (c, _r) in ELEMENTS.items())
    anno_map.update((str(k), list(v)) for k, v in colours.items())
    model_side = {"view_id": 1, "tiff_path": str(model_tiff),
                  "bounds_xy": list(MODEL_BOUNDS),
                  "resolution": {"pixel_size": MW, "requested_pixel_size": MW,
                                 "view_scale": 96.0},
                  "color_assignment_map": {str(MODEL_ELEMENT[0]):
                                           list(MODEL_ELEMENT[1])},
                  "applied_display_style": "FlatColors",
                  "applied_smooth_edges": False}
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
    assert out.shape == (MH, MW, 3)
    for eid, (colour, (u0, v0, u1, v1)) in ELEMENTS.items():
        # Where the MODEL lattice puts this UV rectangle, from the drawing's
        # constants. Half a source pixel of drawing rounding is 0.75 model px.
        want = (mx(u0), my(v1), mx(u1), my(v0))
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
