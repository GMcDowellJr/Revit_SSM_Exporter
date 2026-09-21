"""Unit tests for tools/capture_overlay.py (Stage A step 5: review overlay).

THE GATE THIS FILE IS
---------------------
A synthetic fixture renders its bboxes at KNOWN pixel positions -- positions
worked out by hand from the crop rectangle and the image size, not read back
off the tool's own output -- and one of those fixtures is PADDED (its crop
aspect and its pixel aspect disagree, so Revit's ExportImage aspect clamp
puts a non-zero pad on one axis).

The padded fixture is the one that discriminates. An overlay that stretched
u and v independently across the full image -- the mapping D8 had to be
fixed out of tools/link_identity_resolver.py -- lands every box somewhere
plausible on an unpadded fixture and is only wrong once a pad exists. So
test_padded_fixture_discriminates_against_independent_stretch asserts the
two candidate mappings give VISIBLY different answers on this fixture, which
is what makes the position assertion above it worth anything. Without it,
both mappings would pass and the fixture would prove nothing.

Everything here is synthetic: no Revit, no capture, no Stage A run.
"""
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from tools import capture_overlay as co  # noqa: E402
from tools.clamp_pad_geometry import clamp_pad_geometry  # noqa: E402
import link_identity_resolver as lir  # noqa: E402


def _uv_rect(umin, vmin, umax, vmax):
    """The 4-corner form revit/collection.py's project_bbox_corners_uv emits:
    min-min, max-min, max-max, min-max."""
    return [[umin, vmin], [umax, vmin], [umax, vmax], [umin, vmax]]


def _model_sidecar(bounds_xy, host=None, link=None, actual=None, view_id=7):
    sidecar = {
        "view_id": view_id,
        "bounds_xy": list(bounds_xy) if bounds_xy is not None else None,
        "near_face_w_map": {"host": host or {}, "link": link or {}},
    }
    if actual is not None:
        sidecar["resolution"] = {"actual_w": actual[0], "actual_h": actual[1]}
    return sidecar


def _anno_sidecar(rendered_uv, bbox_map, actual=None, view_id=7):
    return {
        "view_id": view_id,
        "pass": "annotation",
        "registration": {
            "rendered_uv": list(rendered_uv) if rendered_uv is not None else None,
        },
        "resolution": ({"actual_w": actual[0], "actual_h": actual[1]}
                       if actual is not None else {}),
        "annotation_bbox_map": bbox_map,
        "annotation_bbox_status": {"state": "value", "value": {"elapsed_ms": 1.0}},
    }


def _host_entry(rect, category="Walls", source="HOST", near_face_w=12.5):
    return {
        "bbox_corners_uv": rect,
        "near_face_w": near_face_w,
        "category": category,
        "bbox_3d": {"state": "value", "value": {"min": [0, 0, 0], "max": [1, 1, 1]}},
        "source": {"state": "value", "value": source},
        "category_state": {"state": "value", "value": category},
        "import_symbol_state": {"state": "not_applicable", "reason": "not a DWG"},
        "view_specific_state": {"state": "not_applicable", "reason": "not a DWG"},
    }


def _placed_by_key(placed):
    return {r["key"]: r for r in placed}


# --- the gate: known pixel positions, unpadded and padded ---------------------

def test_unpadded_fixture_places_bbox_at_known_pixels():
    """100 ft square crop into a 100x100 px image: 1 ft per pixel, no pad.

    v increases upward in UV and y increases downward in pixels, so the UV
    rectangle u[10,20] x v[10,20] must land at x[10,20], y[80,90].
    """
    sidecar = _model_sidecar(
        (0.0, 0.0, 100.0, 100.0),
        host={"101": _host_entry(_uv_rect(10.0, 10.0, 20.0, 20.0))},
        actual=(100, 100),
    )
    pass_kind, placed, frame = co.plan_overlay(sidecar, 100, 100)

    assert pass_kind == co.PASS_MODEL
    assert frame["refused_reason"] is None
    assert frame["feet_per_pixel"] == pytest.approx(1.0)
    assert (frame["pad_x"], frame["pad_y"]) == (pytest.approx(0.0), pytest.approx(0.0))

    record = _placed_by_key(placed)["101"]
    assert record["unplaced_reason"] is None
    assert record["cls"] == co.CLASS_HOST
    assert record["pixel_corners"] == [
        pytest.approx((10.0, 90.0)),   # (umin, vmin)
        pytest.approx((20.0, 90.0)),   # (umax, vmin)
        pytest.approx((20.0, 80.0)),   # (umax, vmax)
        pytest.approx((10.0, 80.0)),   # (umin, vmax)
    ]


# A 40 x 200 ft crop rendered into a 400 x 1000 px image. The v axis fits at
# 0.2 ft/px and the u axis would fit at 0.1, so v is the UNPADDED axis, fpp
# is 0.2, and u carries a 100 px pad on each side. Worked by hand:
#
#   fpp   = max(40/400, 200/1000) = 0.2
#   pad_x = (400 - 40/0.2) / 2 = 100
#   pad_y = (1000 - 200/0.2) / 2 = 0
#   x(u)  = u/0.2 + 100      ->  u=10 -> 150   u=18 -> 190
#   y(v)  = (200 - v)/0.2    ->  v=50 -> 750   v=150 -> 250
#
# u = 18, not 20, DELIBERATELY. The two candidate mappings here are
# x = 5u + 100 (clamp-pad) and x = 10u (independent stretch); they cross at
# u = 20, so a rectangle whose max corner sat there would agree under both
# models on that corner and the discrimination test below would be half
# blind. The fixture's first draft did exactly that and the test caught it.
PADDED_BOUNDS = (0.0, 0.0, 40.0, 200.0)
PADDED_IMAGE = (400, 1000)
PADDED_RECT_UV = _uv_rect(10.0, 50.0, 18.0, 150.0)
PADDED_EXPECTED_PX = [(150.0, 750.0), (190.0, 750.0), (190.0, 250.0), (150.0, 250.0)]


def test_padded_fixture_places_bbox_at_known_pixels():
    sidecar = _model_sidecar(
        PADDED_BOUNDS,
        host={"202": _host_entry(PADDED_RECT_UV, category="Floors")},
        actual=list(PADDED_IMAGE),
    )
    _kind, placed, frame = co.plan_overlay(sidecar, *PADDED_IMAGE)

    assert frame["refused_reason"] is None
    assert frame["feet_per_pixel"] == pytest.approx(0.2)
    assert frame["pad_x"] == pytest.approx(100.0)
    assert frame["pad_y"] == pytest.approx(0.0)

    record = _placed_by_key(placed)["202"]
    assert record["pixel_corners"] == [pytest.approx(p) for p in PADDED_EXPECTED_PX]


def test_padded_fixture_discriminates_against_independent_stretch():
    """The fixture above must FAIL under the mapping this tool must not use.

    Stretching u and v independently across the full image implies
    non-square pixels; it is the pre-D8 mapping. On the padded fixture it
    puts the box's left edge at 100 px rather than 150. If the two agreed,
    the position assertion above would pass under either model and prove
    nothing about the pad -- see the fixture's own note on why u = 18.
    """
    xmin, ymin, xmax, ymax = PADDED_BOUNDS
    image_w, image_h = PADDED_IMAGE
    stretched = [
        ((u - xmin) / (xmax - xmin) * image_w, (ymax - v) / (ymax - ymin) * image_h)
        for u, v in PADDED_RECT_UV
    ]
    assert stretched[0][0] == pytest.approx(100.0)
    for clamped, stretch in zip(PADDED_EXPECTED_PX, stretched):
        assert clamped[0] != pytest.approx(stretch[0])
    # ...and the pad is on u only, so the v axis agrees under both models.
    # Stated so the discrimination above is not mistaken for "everything
    # differs": only the padded axis does.
    for clamped, stretch in zip(PADDED_EXPECTED_PX, stretched):
        assert clamped[1] == pytest.approx(stretch[1])


def test_uv_to_pixel_agrees_with_the_identity_resolver_inline_copy():
    """Compose the shared uv_to_pixel() against the inline uv->pixel copy in
    tools/link_identity_resolver.py.

    Both model the same clamp pad, and nothing else in the repo runs them
    back to back. This is the composition CLAUDE.md's defect class #1 asks
    for: testing each alone binds each to its own copy.
    """
    image_w, image_h = PADDED_IMAGE
    geometry = clamp_pad_geometry(PADDED_BOUNDS, image_w, image_h)
    corners = [
        co.uv_to_pixel(u, v, PADDED_BOUNDS, image_w, image_h, geometry=geometry)
        for u, v in PADDED_RECT_UV
    ]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    import math
    mine = (int(math.floor(min(xs))), int(math.floor(min(ys))),
            int(math.ceil(max(xs))) - 1, int(math.ceil(max(ys))) - 1)
    theirs = lir._uv_rect_to_pixel_bbox(PADDED_RECT_UV, PADDED_BOUNDS, image_w, image_h)
    assert mine == theirs


# --- end to end: the PNG really carries the box at those pixels --------------

def _write_capture(tmp_path, sidecar, size, name="cap"):
    tiff_path = tmp_path / "{0}.tiff".format(name)
    Image.new("RGB", size, (255, 255, 255)).save(tiff_path)
    sidecar = dict(sidecar)
    sidecar["tiff_path"] = str(tiff_path)
    sidecar_path = tmp_path / "{0}.json".format(name)
    sidecar_path.write_text(json.dumps(sidecar, indent=2, sort_keys=True), encoding="utf-8")
    return sidecar_path


def _color_bbox(image, color):
    """Pixel bbox of every pixel exactly equal to ``color``, or None."""
    pixels = image.load()
    w, h = image.size
    xs, ys = [], []
    for y in range(h):
        for x in range(w):
            if pixels[x, y] == color:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def test_rendered_png_draws_the_box_at_the_known_pixels(tmp_path):
    """The rendered outline's own pixels sit on the hand-computed rectangle.

    Tolerance is 2 px on each edge and comes from the outline's WIDTH
    (OUTLINE_PX = 2, drawn centred on the path), not from an observed
    residual: a 2 px stroke straddles the geometric edge by one pixel each
    way, plus one for PIL's own rasterisation of the join.
    """
    sidecar = _model_sidecar(
        (0.0, 0.0, 100.0, 100.0),
        host={"101": _host_entry(_uv_rect(10.0, 10.0, 20.0, 20.0))},
        actual=(100, 100),
    )
    sidecar_path = _write_capture(tmp_path, sidecar, (100, 100))
    out = co.overlay_one(sidecar_path, label_mode="none")

    assert out.name == "cap.overlay.png"
    with Image.open(out) as img:
        rendered = img.convert("RGB")
        # The canvas is widened to PANEL_MIN_WIDTH so panel text has room to
        # wrap, and the panel is appended BELOW the capture, which stays at
        # 1:1 at the origin -- so a pixel read off this picture inside the
        # capture's own 100x100 region is a capture pixel.
        assert rendered.size[0] == max(100, co.PANEL_MIN_WIDTH)
        assert rendered.size[1] > 100
        box = _color_bbox(rendered.crop((0, 0, 100, 100)), co.CLASS_COLORS[co.CLASS_HOST])

    assert box is not None
    for got, want in zip(box, (10, 80, 20, 90)):
        assert abs(got - want) <= 2


def test_rendered_png_draws_the_padded_box_at_the_known_pixels(tmp_path):
    sidecar = _model_sidecar(
        PADDED_BOUNDS,
        host={"202": _host_entry(PADDED_RECT_UV, category="Floors")},
        actual=list(PADDED_IMAGE),
    )
    sidecar_path = _write_capture(tmp_path, sidecar, PADDED_IMAGE, name="padded")
    out = co.overlay_one(sidecar_path, label_mode="none")

    with Image.open(out) as img:
        rendered = img.convert("RGB")
        capture = rendered.crop((0, 0, PADDED_IMAGE[0], PADDED_IMAGE[1]))
        box = _color_bbox(capture, co.CLASS_COLORS[co.CLASS_HOST])

    assert box is not None
    for got, want in zip(box, (150, 250, 190, 750)):
        assert abs(got - want) <= 2


# --- missing / unavailable records render visibly, never blank ---------------

def test_unavailable_bbox_is_listed_with_its_reason_not_dropped():
    sidecar = _model_sidecar(
        (0.0, 0.0, 100.0, 100.0),
        host={
            "101": _host_entry(_uv_rect(10.0, 10.0, 20.0, 20.0)),
            "102": _host_entry(None, category="Doors"),
        },
        actual=(100, 100),
    )
    _kind, placed, _frame = co.plan_overlay(sidecar, 100, 100)
    by_key = _placed_by_key(placed)

    assert set(by_key) == {"101", "102"}
    assert by_key["102"]["pixel_corners"] is None
    assert by_key["102"]["unplaced_reason"]

    lines, truncated = co._panel_lines(placed, co.DEFAULT_MAX_PANEL_LINES)
    assert truncated == 0
    assert len(lines) == 1
    assert "102" in lines[0] and "Doors" in lines[0]


def test_panel_text_is_actually_drawn_under_the_capture(tmp_path):
    """The not-drawn panel is rendered, not merely computed.

    Asserted by looking for ink: the panel band under the capture must
    carry pixels that are neither its background nor its rule colour.
    """
    sidecar = _model_sidecar(
        (0.0, 0.0, 100.0, 100.0),
        host={"102": _host_entry(None, category="Doors")},
        actual=(100, 100),
    )
    sidecar_path = _write_capture(tmp_path, sidecar, (100, 100), name="unavail")
    out = co.overlay_one(sidecar_path)

    with Image.open(out) as img:
        rendered = img.convert("RGB")
        panel = rendered.crop((0, 100, rendered.size[0], rendered.size[1]))
        colors = {c for _count, c in panel.getcolors(maxcolors=1 << 20)}

    assert colors - {co.PANEL_BG, co.PANEL_RULE}


def test_panel_truncation_is_stated_rather_than_silent():
    records = [
        {"key": str(i), "cls": co.CLASS_HOST, "class_reason": None, "category": "Walls",
         "bbox_uv": None, "bbox_state": "unavailable", "bbox_reason": "no bbox",
         "detail": "", "pixel_corners": None, "unplaced_reason": "no bbox"}
        for i in range(10)
    ]
    lines, truncated = co._panel_lines(records, max_lines=3)
    assert len(lines) == 3
    assert truncated == 7


def test_long_panel_reasons_wrap_instead_of_running_off_the_canvas():
    """A reason clipped by the right edge is rendered but not readable,
    which fails the same contract as not rendering it. Every character of
    the reason has to reach the picture."""
    from PIL import ImageDraw
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    font = co._font(13)
    text = ("311888  [HOST]  Generic Models  --  'bounds_xy' is null: the crop could "
            "not be applied to this capture, so the TIFF's extent is whatever "
            "FitToPage chose and no recorded UV rectangle describes it")
    rows = co._wrap(probe, text, font, 600)
    assert len(rows) > 1
    for row in rows:
        assert co._text_size(probe, row, font)[0] <= 600
    assert "".join(rows).replace(" ", "") == text.replace(" ", "")


def test_wrap_breaks_a_single_over_long_token_rather_than_overflowing():
    """A recorded absolute path or an exception repr is one token."""
    from PIL import ImageDraw
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    font = co._font(13)
    token = "C:/Users/greg/Documents/_metrics/" + "verylongsegment" * 12
    rows = co._wrap(probe, token, font, 300)
    assert len(rows) > 1
    for row in rows:
        assert co._text_size(probe, row, font)[0] <= 300
    assert "".join(rows) == token


def test_off_image_record_is_reported_rather_than_clamped_to_the_edge():
    """A rectangle wholly off the image has no pixel to be drawn on.

    It must be reported as such, not collapsed onto the edge -- the same
    fabrication _uv_rect_to_pixel_bbox's own off-image guard exists to
    prevent, one layer up.
    """
    sidecar = _model_sidecar(
        (0.0, 0.0, 100.0, 100.0),
        host={"103": _host_entry(_uv_rect(-50.0, 10.0, -40.0, 20.0))},
        actual=(100, 100),
    )
    _kind, placed, _frame = co.plan_overlay(sidecar, 100, 100)
    record = _placed_by_key(placed)["103"]
    assert record["pixel_corners"] is not None      # where it really is...
    assert "entirely outside" in record["unplaced_reason"]   # ...and it is not drawable


def test_overhanging_record_is_drawn_not_refused():
    """Partially off-image is the honest picture of an element running off
    the crop, so it is drawn and clipped by the canvas -- the control that
    keeps the off-image rule above from swallowing every edge element."""
    sidecar = _model_sidecar(
        (0.0, 0.0, 100.0, 100.0),
        host={"104": _host_entry(_uv_rect(-5.0, 10.0, 20.0, 20.0))},
        actual=(100, 100),
    )
    _kind, placed, _frame = co.plan_overlay(sidecar, 100, 100)
    record = _placed_by_key(placed)["104"]
    assert record["unplaced_reason"] is None
    assert record["pixel_corners"][0][0] == pytest.approx(-5.0)


# --- refusals ----------------------------------------------------------------

def test_missing_crop_rectangle_refuses_every_placement():
    sidecar = _model_sidecar(
        None,
        host={"101": _host_entry(_uv_rect(10.0, 10.0, 20.0, 20.0))},
        actual=(100, 100),
    )
    _kind, placed, frame = co.plan_overlay(sidecar, 100, 100)
    assert "bounds_xy" in frame["refused_reason"]
    assert placed[0]["pixel_corners"] is None
    assert placed[0]["unplaced_reason"] == frame["refused_reason"]


def test_recorded_export_size_mismatch_refuses_every_placement():
    """decode's Guard 1 in a different costume: if the file is not the size
    the producer recorded, the pads go negative and every box lands
    somewhere plausible and wrong. Refuse instead."""
    sidecar = _model_sidecar(
        (0.0, 0.0, 100.0, 100.0),
        host={"101": _host_entry(_uv_rect(10.0, 10.0, 20.0, 20.0))},
        actual=(120, 100),
    )
    _kind, placed, frame = co.plan_overlay(sidecar, 100, 100)
    assert "120x100" in frame["refused_reason"]
    assert "100x100" in frame["refused_reason"]
    assert placed[0]["pixel_corners"] is None


def test_degenerate_crop_refuses_rather_than_dividing_by_zero():
    sidecar = _model_sidecar(
        (10.0, 10.0, 10.0, 50.0),
        host={"101": _host_entry(_uv_rect(10.0, 10.0, 20.0, 20.0))},
        actual=(100, 100),
    )
    _kind, placed, frame = co.plan_overlay(sidecar, 100, 100)
    assert "ValueError" in frame["refused_reason"]
    assert placed[0]["unplaced_reason"] == frame["refused_reason"]


def test_half_recorded_export_size_is_treated_as_not_recorded():
    """One axis recorded and the other not is a half-measurement that reads
    like a measurement; both denominators must then come from the file."""
    sidecar = _model_sidecar((0.0, 0.0, 100.0, 100.0), host={}, actual=(100, None))
    _kind, _placed, frame = co.plan_overlay(sidecar, 100, 100)
    assert frame["recorded_px"] is None
    assert frame["refused_reason"] is None


def test_sidecar_with_neither_map_is_refused_not_drawn_empty():
    with pytest.raises(ValueError) as exc:
        co.read_records({"view_id": 1, "bounds_xy": [0, 0, 1, 1]})
    assert "near_face_w_map" in str(exc.value)
    assert "annotation_bbox_map" in str(exc.value)


# --- source and category classification --------------------------------------

def test_host_entry_source_values_map_to_their_own_classes():
    sidecar = _model_sidecar(
        (0.0, 0.0, 100.0, 100.0),
        host={
            "101": _host_entry(_uv_rect(1.0, 1.0, 2.0, 2.0), source="HOST"),
            "102": _host_entry(_uv_rect(3.0, 3.0, 4.0, 4.0), source="DWG"),
        },
        link={
            "9:55": {
                "bbox_corners_uv": _uv_rect(5.0, 5.0, 6.0, 6.0),
                "near_face_w": 3.0, "category": "Walls",
                "link_inst_id": 9, "link_elem_id": 55,
            },
        },
        actual=(100, 100),
    )
    _kind, placed, _frame = co.plan_overlay(sidecar, 100, 100)
    by_key = _placed_by_key(placed)
    assert by_key["101"]["cls"] == co.CLASS_HOST
    assert by_key["102"]["cls"] == co.CLASS_DWG
    assert by_key["9:55"]["cls"] == co.CLASS_LINK


def test_unreadable_source_is_its_own_class_not_folded_into_host():
    entry = _host_entry(_uv_rect(1.0, 1.0, 2.0, 2.0))
    entry["source"] = {"state": "unavailable", "reason": "expansion failed"}
    sidecar = _model_sidecar((0.0, 0.0, 100.0, 100.0), host={"101": entry}, actual=(100, 100))
    _kind, placed, _frame = co.plan_overlay(sidecar, 100, 100)
    record = placed[0]
    assert record["cls"] == co.CLASS_UNKNOWN_SOURCE
    assert record["class_reason"] == "expansion failed"


def test_sidecar_written_before_the_source_key_says_so():
    entry = _host_entry(_uv_rect(1.0, 1.0, 2.0, 2.0))
    del entry["source"]
    sidecar = _model_sidecar((0.0, 0.0, 100.0, 100.0), host={"101": entry}, actual=(100, 100))
    _kind, placed, _frame = co.plan_overlay(sidecar, 100, 100)
    assert placed[0]["cls"] == co.CLASS_UNKNOWN_SOURCE
    assert "Stage A step 1" in placed[0]["class_reason"]


# --- annotation pass ----------------------------------------------------------

def _anno_entry(rect, basis="owner_view", category="Text Notes", bbox_source="view"):
    return {
        "bbox_uv": ({"state": "value", "value": rect} if rect is not None
                    else {"state": "unavailable", "reason": "no bbox resolvable"}),
        "bbox_3d": {"state": "not_applicable", "reason": "view-specific"},
        "bbox_source": bbox_source,
        "membership_basis": basis,
        "category": category,
    }


def test_annotation_pass_uses_registration_rendered_uv_as_its_frame():
    """The two passes crop to two different rectangles. Reading the model
    pass's key here would place every annotation against the wrong one."""
    sidecar = _anno_sidecar(
        (0.0, 0.0, 100.0, 100.0),
        {"301": _anno_entry(_uv_rect(10.0, 10.0, 20.0, 20.0))},
        actual=(100, 100),
    )
    pass_kind, placed, frame = co.plan_overlay(sidecar, 100, 100)
    assert pass_kind == co.PASS_ANNOTATION
    assert frame["bounds_uv"] == [0.0, 0.0, 100.0, 100.0]
    assert placed[0]["cls"] == co.CLASS_ANNOTATION
    assert placed[0]["pixel_corners"][0] == pytest.approx((10.0, 90.0))


def test_annotation_membership_basis_separates_datums_from_annotations():
    sidecar = _anno_sidecar(
        (0.0, 0.0, 100.0, 100.0),
        {
            "301": _anno_entry(_uv_rect(10.0, 10.0, 20.0, 20.0), basis="owner_view"),
            "302": _anno_entry(_uv_rect(30.0, 30.0, 40.0, 40.0), basis="datum_category",
                               category="Grids"),
            "303": _anno_entry(_uv_rect(50.0, 50.0, 60.0, 60.0), basis=None),
        },
        actual=(100, 100),
    )
    _kind, placed, _frame = co.plan_overlay(sidecar, 100, 100)
    by_key = _placed_by_key(placed)
    assert by_key["301"]["cls"] == co.CLASS_ANNOTATION
    assert by_key["302"]["cls"] == co.CLASS_DATUM
    assert by_key["303"]["cls"] == co.CLASS_UNKNOWN_BASIS
    assert "cannot be told apart from a datum" in by_key["303"]["class_reason"]


def test_annotation_pass_with_no_rendered_rectangle_refuses():
    sidecar = _anno_sidecar(None, {"301": _anno_entry(_uv_rect(1.0, 1.0, 2.0, 2.0))},
                            actual=(100, 100))
    _kind, placed, frame = co.plan_overlay(sidecar, 100, 100)
    assert "rendered_uv" in frame["refused_reason"]
    assert placed[0]["pixel_corners"] is None


def test_failed_annotation_collection_renders_as_a_record_not_an_empty_view():
    """An annotation map that could not be collected is not a view with no
    annotations. One record stands for the failure so the picture cannot be
    read as 'there was nothing here'."""
    sidecar = _anno_sidecar((0.0, 0.0, 100.0, 100.0), None, actual=(100, 100))
    sidecar["annotation_bbox_map"] = None
    sidecar["annotation_bbox_status"] = {"state": "unavailable", "reason": "collection raised"}
    _kind, placed, _frame = co.plan_overlay(sidecar, 100, 100)
    assert len(placed) == 1
    assert placed[0]["key"] == "<annotation_bbox_map>"
    assert "collection raised" in placed[0]["unplaced_reason"]


# --- filters state themselves on the picture ---------------------------------

def test_only_filter_lists_excluded_records_rather_than_dropping_them(tmp_path):
    sidecar = _model_sidecar(
        (0.0, 0.0, 100.0, 100.0),
        host={
            "101": _host_entry(_uv_rect(10.0, 10.0, 20.0, 20.0), source="HOST"),
            "102": _host_entry(_uv_rect(30.0, 30.0, 40.0, 40.0), source="DWG"),
        },
        actual=(100, 100),
    )
    sidecar_path = _write_capture(tmp_path, sidecar, (100, 100), name="filtered")
    with Image.open(tmp_path / "filtered.tiff") as img:
        base = img.convert("RGB")
    _kind, placed, frame = co.plan_overlay(sidecar, 100, 100)
    canvas = co.render(base, sidecar_path, tmp_path / "filtered.tiff", co.PASS_MODEL,
                       sidecar, placed, frame, label_mode="none", only=["host"])

    by_key = _placed_by_key(placed)
    assert by_key["101"]["unplaced_reason"] is None
    assert by_key["102"]["unplaced_reason"] == "excluded by --only"

    capture = canvas.crop((0, 0, 100, 100))
    assert _color_bbox(capture, co.CLASS_COLORS[co.CLASS_HOST]) is not None
    assert _color_bbox(capture, co.CLASS_COLORS[co.CLASS_DWG]) is None


# --- CLI ----------------------------------------------------------------------

def test_cli_writes_one_overlay_per_sidecar_and_skips_derived_json(tmp_path):
    sidecar = _model_sidecar(
        (0.0, 0.0, 100.0, 100.0),
        host={"101": _host_entry(_uv_rect(10.0, 10.0, 20.0, 20.0))},
        actual=(100, 100),
    )
    _write_capture(tmp_path, sidecar, (100, 100), name="a")
    (tmp_path / "a.decoded.json").write_text("{}", encoding="utf-8")
    (tmp_path / "a.link_identity.json").write_text("{}", encoding="utf-8")

    assert co.main([str(tmp_path)]) == 0
    assert (tmp_path / "a.overlay.png").exists()
    assert not (tmp_path / "a.decoded.overlay.png").exists()
    assert not (tmp_path / "a.link_identity.overlay.png").exists()


def test_cli_reports_a_sidecar_it_cannot_draw_and_exits_nonzero(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"view_id": 1}), encoding="utf-8")
    Image.new("RGB", (10, 10), (255, 255, 255)).save(tmp_path / "bad.tiff")
    assert co.main([str(bad)]) == 1


def test_cli_out_dir_keeps_the_capture_directory_untouched(tmp_path):
    sidecar = _model_sidecar((0.0, 0.0, 100.0, 100.0), host={}, actual=(100, 100))
    sidecar_path = _write_capture(tmp_path, sidecar, (100, 100), name="b")
    out_dir = tmp_path / "review"
    assert co.main([str(sidecar_path), "--out-dir", str(out_dir)]) == 0
    assert (out_dir / "b.overlay.png").exists()
    assert not (tmp_path / "b.overlay.png").exists()
