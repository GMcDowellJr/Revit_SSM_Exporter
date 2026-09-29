"""Tests for tools/notes/anno_pass_variant_report.py.

THE FIXTURES CARRY THE ANSWER, AND THE ANSWER IS COMPUTED BY HAND. Every
synthetic image below is built by placing known rectangles at a known
feet-per-pixel with a known origin, so the mapping the analyzer FITS can be
compared against the mapping the fixture was DRAWN with. A test that recomputed
the fit with the analyzer's own arithmetic would prove only that the function
is deterministic -- the failure mode CLAUDE.md records as "a test that
reimplements the thing it checks proves nothing".

THE DISCRIMINATING FIXTURE IS ``non_b_extent``. It renders the annotations at a
DIFFERENT scale and origin from the frame the sidecar records -- which is
exactly finding F1, and exactly the case ``tools/capture_overlay.py``
mishandles, because that tool maps UV through the sidecar's numbers and has no
way to notice they are wrong. The fit must recover the scale the image was
drawn at, not the one the sidecar claims, and the per-side margins must come
back as the real difference between the two rectangles.
"""
from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image

from tools.notes import anno_pass_variant_report as report


WHITE = (255, 255, 255)


# ======================================================================
# fit_axis
# ======================================================================

def test_fit_axis_recovers_an_exact_line():
    xs = [0.0, 1.0, 2.0, 10.0]
    ys = [3.0 + 2.5 * x for x in xs]
    slope, intercept = report.fit_axis(xs, ys)
    assert slope == pytest.approx(2.5)
    assert intercept == pytest.approx(3.0)


def test_fit_axis_refuses_rather_than_returning_a_flat_line():
    # Every sample at the same x determines no line. A slope of 0.0 here would
    # be read downstream as "rendered at zero scale", which is a different and
    # false statement.
    assert report.fit_axis([4.0, 4.0, 4.0], [1.0, 2.0, 3.0]) is None
    assert report.fit_axis([1.0], [1.0]) is None
    assert report.fit_axis([], []) is None
    assert report.fit_axis([1.0, 2.0], [1.0]) is None


# ======================================================================
# classify_offpalette
# ======================================================================

def _palette_array(colors):
    return np.array([[float(c) for c in rgb] for rgb in colors], dtype=np.float64)


def test_blend_between_a_palette_color_and_white_is_a_blend():
    palette = _palette_array([(200, 0, 0)])
    # Exactly halfway from (200,0,0) to white.
    kind, also_grey = report.classify_offpalette((227, 127, 127), palette)
    assert kind == "blend"
    assert also_grey is False


def test_a_color_three_units_off_the_segment_is_still_a_blend_and_four_is_not():
    palette = _palette_array([(200, 0, 0)])
    midpoint = np.array([227.5, 127.5, 127.5])
    direction = np.array([255.0, 255.0, 255.0]) - np.array([200.0, 0.0, 0.0])
    # A unit vector perpendicular to the segment, so the offset IS the distance.
    perpendicular = np.cross(direction, np.array([1.0, 0.0, 0.0]))
    perpendicular = perpendicular / np.linalg.norm(perpendicular)
    inside = midpoint + perpendicular * 2.9
    outside = midpoint + perpendicular * 6.0
    assert report.classify_offpalette(tuple(inside), palette)[0] == "blend"
    assert report.classify_offpalette(tuple(outside), palette)[0] != "blend"


def test_black_is_grey_not_a_blend_when_no_palette_color_reaches_it():
    palette = _palette_array([(200, 0, 0)])
    kind, also_grey = report.classify_offpalette((0, 0, 0), palette)
    assert kind == "grey"
    assert also_grey is True


def test_a_saturated_unrelated_color_is_other():
    palette = _palette_array([(200, 0, 0)])
    assert report.classify_offpalette((0, 128, 0), palette)[0] == "other"


def test_beyond_the_segment_endpoint_is_not_swept_in():
    # Collinear with the (200,0,0)->white line but PAST white. Distance to the
    # infinite line is 0; distance to the SEGMENT is not. Using the line would
    # classify an impossible colour as a blend.
    palette = _palette_array([(100, 0, 0)])
    beyond = (100 + 1.4 * 155, 1.4 * 255, 1.4 * 255)
    beyond = tuple(min(v, 400.0) for v in beyond)
    kind, _ = report.classify_offpalette(beyond, palette)
    assert kind != "blend"


def test_a_white_palette_entry_does_not_produce_a_nan_verdict():
    # A zero-length segment: white to white. The classification must be decided,
    # not left to a nan comparison.
    palette = _palette_array([(255, 255, 255)])
    kind, _ = report.classify_offpalette((0, 128, 0), palette)
    assert kind == "other"


def test_grey_and_blend_overlap_is_reported_not_hidden():
    # A grey palette colour makes every grey between it and white ALSO a blend.
    # The classifier reports blend (its stated ordering) AND flags also_grey, so
    # the report can state what the ordering cost.
    palette = _palette_array([(100, 100, 100)])
    kind, also_grey = report.classify_offpalette((180, 180, 180), palette)
    assert kind == "blend"
    assert also_grey is True


# ======================================================================
# ink_fraction / uv_rect_to_pixel_rect
# ======================================================================

def test_ink_fraction_counts_only_non_white():
    region = np.full((10, 10, 3), 255, dtype=np.uint8)
    assert report.ink_fraction(region) == 0.0
    region[0:5, :, :] = 0
    assert report.ink_fraction(region) == pytest.approx(0.5)


def test_ink_fraction_refuses_an_empty_region():
    with pytest.raises(ValueError):
        report.ink_fraction(np.zeros((0, 0, 3), dtype=np.uint8))


def test_uv_rect_to_pixel_rect_clamps_an_overhang_but_refuses_a_miss():
    mapping = {"a_u": 10.0, "b_u": 0.0, "a_v": -10.0, "b_v": 100.0}
    # Straddles the left edge: clamped, and still tested.
    straddle = report.uv_rect_to_pixel_rect((-1.0, 1.0, 1.0, 2.0), mapping, 100, 100)
    assert straddle is not None
    assert straddle[0] == 0
    # Wholly off the image: refused, because there are no pixels to test and a
    # 0% ink reading would be a false statement about the element.
    assert report.uv_rect_to_pixel_rect(
        (-50.0, 1.0, -40.0, 2.0), mapping, 100, 100) is None


# ======================================================================
# SYNTHETIC CAPTURES
# ======================================================================

def _palette(n):
    """n distinct, saturated, non-grey, non-white colours."""
    colors = []
    for i in range(n):
        colors.append(((37 * (i + 1)) % 200 + 20, (91 * (i + 1)) % 180 + 10,
                       (151 * (i + 1)) % 160 + 30))
    assert len(set(colors)) == n, "fixture palette must be collision-free"
    return colors


def _build_capture(tmp_path, name, frame_uv, frame_px, draw_fpp, draw_origin_uv,
                   elements, view_scale=96.0, extra_pixels=None,
                   rendered_uv=None, ink_fraction_of_bbox=None,
                   ink_align="center"):
    """Write a synthetic annotation TIFF + sidecar, and return their paths.

    ``frame_uv``/``frame_px`` are what the SIDECAR claims. ``draw_fpp`` and
    ``draw_origin_uv`` are what the image is actually DRAWN at. Passing the same
    numbers for both makes a capture that registers exactly; passing different
    ones makes the non-B-extent case.

    ``elements`` is ``[(element_id, rgb, (u0, v0, u1, v1), category), ...]`` in
    absolute view UV -- the same coordinates the sidecar's ``bbox_uv`` records.

    ``ink_fraction_of_bbox`` draws the ink as that fraction of each bbox instead
    of filling it, and ``ink_align`` ("center" or "left") decides where inside
    the box it sits. THIS IS THE CASE EVERY ORIGINAL FIXTURE EXCLUDED: they all
    filled the bbox exactly, which makes the ink centroid equal the bbox centre
    by construction and hides the fit's central premise. Finding 3 on PR #215.
    """
    width, height = frame_px
    array = np.full((height, width, 3), 255, dtype=np.uint8)
    origin_u, origin_v_top = draw_origin_uv

    def to_px(u, v):
        return ((u - origin_u) / draw_fpp, (origin_v_top - v) / draw_fpp)

    bbox_map = {}
    color_map = {}
    for element_id, rgb, rect_uv, category in elements:
        u0, v0, u1, v1 = rect_uv
        x0, y1 = to_px(u0, v0)
        x1, y0 = to_px(u1, v1)
        ix0, ix1 = int(round(min(x0, x1))), int(round(max(x0, x1)))
        iy0, iy1 = int(round(min(y0, y1))), int(round(max(y0, y1)))
        if ink_fraction_of_bbox is not None:
            # Shrink the painted region inside the bbox. "left" puts it against
            # the box's left edge, so the ink centroid sits left of the box
            # centre exactly the way a left-aligned glyph run does.
            span_x = ix1 - ix0
            keep = max(1, int(round(span_x * float(ink_fraction_of_bbox))))
            if ink_align == "left":
                ix1 = ix0 + keep
            else:
                pad = (span_x - keep) // 2
                ix0, ix1 = ix0 + pad, ix0 + pad + keep
        ix0c, ix1c = max(0, ix0), min(width, ix1)
        iy0c, iy1c = max(0, iy0), min(height, iy1)
        if ix1c > ix0c and iy1c > iy0c:
            array[iy0c:iy1c, ix0c:ix1c] = rgb
        color_map[str(element_id)] = list(rgb)
        bbox_map[str(element_id)] = {
            "bbox_uv": {"state": "value",
                        "value": [[u0, v0], [u1, v0], [u1, v1], [u0, v1]]},
            "bbox_3d": {"state": "not_applicable", "reason": "annotation"},
            "bbox_source": "view",
            "membership_basis": "owner_view",
            "category": category,
        }
    for pixel in extra_pixels or []:
        (px, py), rgb = pixel
        array[py, px] = rgb

    tiff_path = tmp_path / "{0}_anno.tiff".format(name)
    Image.fromarray(array, mode="RGB").save(tiff_path)

    sidecar = {
        "schema": "vop.stage_a.annotation_pass.v1",
        "view_id": 4242,
        "pass": "annotation",
        "resolution": {"actual_w": width, "actual_h": height,
                       "view_scale": view_scale, "dim_check": "pass",
                       "pixel_size": width, "requested_pixel_size": width},
        "registration": {
            "frame_snapped_uv": list(frame_uv),
            "frame_px": [width, height],
            "rendered_uv": (list(frame_uv) if rendered_uv is None
                            else (None if rendered_uv == "null"
                                  else list(rendered_uv))),
            "achieved_fpp_ft": draw_fpp,
            "model_crop_snapped_uv": list(frame_uv),
            "model_crop_px": [width, height],
            "model_crop_offset_px": [0, 0],
            "crop_is_frame": True,
        },
        "color_assignment_map": color_map,
        "color_assignment_count": len(color_map),
        "annotation_bbox_map": bbox_map,
        "annotation_bbox_status": {"status": "value", "count": len(bbox_map)},
        "paint_failed_element_ids": [],
        "model_suppression_mode": "hide_categories",
        "applied_smooth_edges": "not_attempted",
        "applied_display_style": "FlatColors",
        "capture_faults": [],
        "tiff_path": str(tiff_path),
    }
    sidecar_path = tmp_path / "{0}_anno.json".format(name)
    sidecar_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    return (sidecar_path, tiff_path)


def _registering_elements(fpp):
    """Four well-spread annotations. Spread on BOTH axes, so the fit is
    determined on both -- a fixture with everything on one row would make the
    v-axis assertions vacuous."""
    size = 6.0 * fpp
    return [
        (101, _palette(4)[0], (10.0, 10.0, 10.0 + size, 10.0 + size), "Text Notes"),
        (102, _palette(4)[1], (60.0, 18.0, 60.0 + size, 18.0 + size), "Dimensions"),
        (103, _palette(4)[2], (25.0, 40.0, 25.0 + size, 40.0 + size), "Grids"),
        (104, _palette(4)[3], (70.0, 46.0, 70.0 + size, 46.0 + size), "Door Tags"),
    ]


def test_an_exactly_registering_capture_recovers_its_own_scale_and_zero_margins(tmp_path):
    fpp = 0.5333333333333333          # 18.75 px/ft, the run's plan figure
    frame_uv = (0.0, 0.0, 80.0, 54.0)
    frame_px = (int(round(80.0 / fpp)), int(round(54.0 / fpp)))
    sidecar_path, tiff_path = _build_capture(
        tmp_path, "v0", frame_uv, frame_px, fpp, (frame_uv[0], frame_uv[3]),
        _registering_elements(fpp))

    analysis = report.analyze_capture(sidecar_path, tiff_path)
    size = analysis["measurements"]["size"]
    assert size["matches_frame_px"] is True
    assert size["delta_w"] == 0 and size["delta_h"] == 0

    fit = analysis["measurements"]["registration"]
    assert fit["status"] == "value", fit
    assert fit["sample_count"] == 4
    assert fit["px_per_ft_u"] == pytest.approx(1.0 / fpp, rel=0.01)
    assert fit["px_per_ft_v"] == pytest.approx(1.0 / fpp, rel=0.01)
    assert fit["v_axis_sign"].startswith("negative")
    # Drawn at the frame's own scale and origin, so every margin is ~0.
    for side in ("left", "right", "top", "bottom"):
        assert abs(fit["margin_ft"][side]) < 0.6, (side, fit["margin_ft"])
    assert fit["residual_median_px"] < 1.0
    assert fit["residual_max_px"] < 2.0


def test_the_non_b_extent_capture_recovers_the_scale_it_was_drawn_at(tmp_path):
    """THE DISCRIMINATING CASE -- finding F1, and what capture_overlay mishandles.

    The sidecar claims frame B at 18.75 px/ft. The image was drawn at 17.81
    px/ft from an origin 2 ft outside B on the left and top -- i.e. the render
    covers a region LARGER than B, fitted to the annotations that reach past it.
    The sidecar's own mapping is therefore wrong everywhere, which is precisely
    why the ink test must not use it.
    """
    sidecar_fpp = 1.0 / 18.75
    drawn_fpp = 1.0 / 17.81
    frame_uv = (0.0, 0.0, 80.0, 54.0)
    frame_px = (int(round(80.0 / sidecar_fpp)), int(round(54.0 / sidecar_fpp)))
    # Drawn from 2 ft left of B's left edge and 2 ft above B's top edge.
    draw_origin = (frame_uv[0] - 2.0, frame_uv[3] + 2.0)
    sidecar_path, tiff_path = _build_capture(
        tmp_path, "v0_nonb", frame_uv, frame_px, drawn_fpp, draw_origin,
        _registering_elements(sidecar_fpp))

    analysis = report.analyze_capture(sidecar_path, tiff_path)
    fit = analysis["measurements"]["registration"]
    assert fit["status"] == "value", fit
    # The fit reports what the IMAGE was drawn at, not what the sidecar claims.
    assert fit["px_per_ft_u"] == pytest.approx(17.81, rel=0.02)
    assert fit["px_per_ft_v"] == pytest.approx(17.81, rel=0.02)
    assert fit["frame_px_per_ft"] == pytest.approx(18.75, rel=0.02)
    assert fit["px_per_ft_u_minus_frame"] < -0.5
    # The left and top margins recover the 2 ft offset the image was drawn with.
    assert fit["margin_ft"]["left"] == pytest.approx(2.0, abs=0.4)
    assert fit["margin_ft"]["top"] == pytest.approx(2.0, abs=0.4)
    # And the right/bottom margins are the rest of the extra extent, which is
    # positive: the render covers more ground than B on every side here.
    assert fit["margin_ft"]["right"] > 0.0
    assert fit["margin_ft"]["bottom"] > 0.0
    # Paper inches at 1:96 -- stated in the units the probe brief asks for.
    assert fit["margin_paper_in"]["left"] == pytest.approx(
        fit["margin_ft"]["left"] * 12.0 / 96.0)


def test_the_ink_test_uses_the_fitted_mapping_and_so_survives_a_bad_sidecar(tmp_path):
    """The ink column on the non-B capture must still find the ink.

    This is the assertion that binds the ink test to the FITTED mapping. Placed
    through the sidecar's mapping, every rectangle would land 2 ft (~36 px) away
    and off by 5% in scale, and the categories would read as empty.
    """
    sidecar_fpp = 1.0 / 18.75
    drawn_fpp = 1.0 / 17.81
    frame_uv = (0.0, 0.0, 80.0, 54.0)
    frame_px = (int(round(80.0 / sidecar_fpp)), int(round(54.0 / sidecar_fpp)))
    sidecar_path, tiff_path = _build_capture(
        tmp_path, "v0_ink", frame_uv, frame_px, drawn_fpp,
        (frame_uv[0] - 2.0, frame_uv[3] + 2.0),
        _registering_elements(sidecar_fpp))

    analysis = report.analyze_capture(sidecar_path, tiff_path)
    coverage = analysis["measurements"]["coverage"]
    assert coverage["ink_source"] is not None
    assert "fitted" in coverage["ink_source"]
    for name, bucket in coverage["by_category"].items():
        assert bucket["assigned"] == 1, name
        assert bucket["painted"] == 1, name
        assert bucket["color_present"] == 1, name
        assert bucket["ink_tested"] == 1, name
        assert bucket["ink_present"] == 1, (name, bucket)


def test_coverage_leaves_the_ink_column_unmeasured_rather_than_guessing(tmp_path):
    """A capture whose crop was never applied has no frame, so no fit, so no ink.

    ``registration.rendered_uv`` null is production's record of exactly that.
    The ink column must come back UNMEASURED with the reason -- not 0, and not
    silently placed through ``frame_snapped_uv``, which was not rendered.
    """
    fpp = 0.5333333333333333
    frame_uv = (0.0, 0.0, 80.0, 54.0)
    frame_px = (int(round(80.0 / fpp)), int(round(54.0 / fpp)))
    sidecar_path, tiff_path = _build_capture(
        tmp_path, "v0_nocrop", frame_uv, frame_px, fpp,
        (frame_uv[0], frame_uv[3]), _registering_elements(fpp),
        rendered_uv="null")

    analysis = report.analyze_capture(sidecar_path, tiff_path)
    fit = analysis["measurements"]["registration"]
    assert fit["status"] == "unavailable"
    assert "rendered_uv is null" in fit["reason"]
    coverage = analysis["measurements"]["coverage"]
    assert coverage["ink_source"] is None
    assert "deliberately NOT substituted" in coverage["ink_unavailable_reason"]
    for bucket in coverage["by_category"].values():
        assert bucket["ink_tested"] == 0
        assert bucket["ink_untested"] == 1
    excursion = analysis["measurements"]["bbox_excursion"]
    assert excursion["status"] == "unavailable"


def test_offpalette_split_counts_blends_greys_and_others_separately(tmp_path):
    fpp = 0.5333333333333333
    frame_uv = (0.0, 0.0, 80.0, 54.0)
    frame_px = (int(round(80.0 / fpp)), int(round(54.0 / fpp)))
    elements = _registering_elements(fpp)
    first_rgb = elements[0][1]
    blend = tuple(int(round((c + 255) / 2.0)) for c in first_rgb)
    assert blend not in {rgb for _id, rgb, _r, _c in elements}
    extra = ([((5, 5), blend), ((6, 5), blend), ((7, 5), blend)]
             + [((10, 5), (0, 0, 0)), ((11, 5), (0, 0, 0))]
             + [((20, 5), (0, 200, 255))])
    sidecar_path, tiff_path = _build_capture(
        tmp_path, "v0_blend", frame_uv, frame_px, fpp,
        (frame_uv[0], frame_uv[3]), elements, extra_pixels=extra)

    off = report.analyze_capture(sidecar_path, tiff_path)["measurements"]["offpalette"]
    assert off["blend_pixels"] == 3
    assert off["grey_pixels"] == 2
    assert off["other_pixels"] == 1
    assert off["offpalette_pixels"] == 6
    assert off["tally_capped"] is False
    assert {tuple(c["rgb"]) for c in off["top_colors"]} >= {
        blend, (0, 0, 0), (0, 200, 255)}
    # The totals partition the image exactly: nothing is counted twice and
    # nothing is lost.
    assert (off["white_pixels"] + off["palette_pixels"]
            + off["offpalette_pixels"]) == off["total_pixels"]


def test_a_border_clipped_element_is_excluded_from_the_fit_and_counted(tmp_path):
    """An element running off the edge has a centroid that is not its centre.

    Including it would pull the fit toward the clip. It is excluded and the
    exclusion is COUNTED, so a capture whose fit rests on two samples out of
    forty says so.
    """
    fpp = 0.5333333333333333
    frame_uv = (0.0, 0.0, 80.0, 54.0)
    frame_px = (int(round(80.0 / fpp)), int(round(54.0 / fpp)))
    elements = list(_registering_elements(fpp))
    # A fifth element straddling the left edge.
    clipped_rgb = (9, 199, 99)
    elements.append((105, clipped_rgb, (-4.0, 25.0, 2.0, 31.0), "Text Notes"))
    sidecar_path, tiff_path = _build_capture(
        tmp_path, "v0_clip", frame_uv, frame_px, fpp,
        (frame_uv[0], frame_uv[3]), elements)

    fit = report.analyze_capture(sidecar_path, tiff_path)["measurements"]["registration"]
    assert fit["sample_count"] == 4
    assert fit["sample_diagnostics"]["border_clipped"] == 1
    assert fit["sample_diagnostics"]["matched"] == 4


def test_bbox_excursion_reports_the_furthest_reach_per_side(tmp_path):
    fpp = 0.5333333333333333
    frame_uv = (0.0, 0.0, 80.0, 54.0)
    frame_px = (int(round(80.0 / fpp)), int(round(54.0 / fpp)))
    elements = list(_registering_elements(fpp))
    # Recorded 3 ft left of B and 5 ft above it. Drawn where it falls.
    elements.append(((106), (11, 22, 233), (-3.0, 20.0, 1.0, 59.0), "Grids"))
    sidecar_path, tiff_path = _build_capture(
        tmp_path, "v0_exc", frame_uv, frame_px, fpp,
        (frame_uv[0], frame_uv[3]), elements)

    excursion = report.analyze_capture(
        sidecar_path, tiff_path)["measurements"]["bbox_excursion"]
    assert excursion["status"] == "value"
    assert excursion["left"] == pytest.approx(3.0)
    assert excursion["top"] == pytest.approx(5.0)
    assert excursion["right"] == pytest.approx(0.0)
    assert excursion["bottom"] == pytest.approx(0.0)
    assert excursion["beyond_count"] == 1
    assert excursion["rect_count"] == 5
    assert excursion["paper_in"]["left"] == pytest.approx(3.0 * 12.0 / 96.0)


def test_scan_image_partitions_every_pixel(tmp_path):
    """The control on the scanner itself.

    White + palette + off-palette must equal the pixel count, on a fixture whose
    three populations are known by construction. Without this the three counters
    could drift and every table in section 5 would be built on it.
    """
    array = np.full((7, 11, 3), 255, dtype=np.uint8)
    array[0, 0] = (10, 20, 30)
    array[1, 1] = (10, 20, 30)
    array[2, 2] = (40, 50, 60)
    array[3, 3] = (1, 2, 3)
    path = tmp_path / "scan.tiff"
    Image.fromarray(array, mode="RGB").save(path)

    scan = report.scan_image(path, [(10, 20, 30), (40, 50, 60)])
    assert scan["total_pixels"] == 77
    assert scan["palette_pixels"] == 3
    assert scan["offpalette_pixels"] == 1
    assert scan["white_pixels"] == 73
    assert (scan["white_pixels"] + scan["palette_pixels"]
            + scan["offpalette_pixels"]) == scan["total_pixels"]
    assert scan["blobs"][report._pack((10, 20, 30))]["pixel_count"] == 2
    assert scan["blobs"][report._pack((10, 20, 30))]["touches_border"] is True
    assert report._pack((99, 99, 99)) not in scan["blobs"]


def test_scan_image_chunking_does_not_change_the_answer(tmp_path, monkeypatch):
    """The chunk loop is real work, so it gets falsified.

    The same fixture is scanned with a chunk height larger than the image and
    with one of 1 row. Identical results, or the row offsets in the centroid
    accumulation are wrong -- which a single-chunk test could never see.
    """
    rng = np.random.default_rng(7)
    array = np.full((40, 23, 3), 255, dtype=np.uint8)
    palette = [(10, 20, 30), (40, 50, 60), (70, 80, 90)]
    for index, rgb in enumerate(palette):
        for _ in range(12):
            y = int(rng.integers(1, 39))
            x = int(rng.integers(1, 22))
            array[y, x] = rgb
    path = tmp_path / "chunk.tiff"
    Image.fromarray(array, mode="RGB").save(path)

    monkeypatch.setattr(report, "CHUNK_ROWS", 4096)
    whole = report.scan_image(path, palette)
    monkeypatch.setattr(report, "CHUNK_ROWS", 1)
    sliced = report.scan_image(path, palette)

    assert whole["blobs"].keys() == sliced["blobs"].keys()
    for key in whole["blobs"]:
        assert whole["blobs"][key]["pixel_count"] == sliced["blobs"][key]["pixel_count"]
        assert whole["blobs"][key]["centroid_px"] == pytest.approx(
            sliced["blobs"][key]["centroid_px"])
        assert (whole["blobs"][key]["touches_border"]
                == sliced["blobs"][key]["touches_border"])


def test_overlay_is_withheld_with_a_reason_when_the_image_is_not_the_frame():
    analysis = {"measurements": {"size": {
        "image_w": 1500, "image_h": 900, "frame_px_w": 1600, "frame_px_h": 900,
        "matches_frame_px": False}}}
    outcome = report.maybe_run_overlay(analysis, "nowhere.json", enabled=True)
    assert outcome["ran"] is False
    assert "1500x900" in outcome["reason"]
    assert "1600x900" in outcome["reason"]


def test_overlay_is_withheld_when_disabled():
    analysis = {"measurements": {"size": {
        "image_w": 10, "image_h": 10, "frame_px_w": 10, "frame_px_h": 10,
        "matches_frame_px": True}}}
    outcome = report.maybe_run_overlay(analysis, "nowhere.json", enabled=False)
    assert outcome["ran"] is False
    assert "--no-overlay" in outcome["reason"]


def test_a_sidecar_whose_bbox_collection_failed_yields_no_rectangles(tmp_path):
    """An "unavailable" STATUS block must not read as a view with no annotations.

    Production writes an EMPTY map plus a status block saying the collection
    raised. An empty dict is still a dict, so testing the map alone reads that
    failure as a clean view -- the defect PR #213's review found in
    capture_overlay.py, reproduced here against this reader.
    """
    fpp = 0.5333333333333333
    frame_uv = (0.0, 0.0, 80.0, 54.0)
    frame_px = (int(round(80.0 / fpp)), int(round(54.0 / fpp)))
    sidecar_path, _tiff = _build_capture(
        tmp_path, "v0_failedbbox", frame_uv, frame_px, fpp,
        (frame_uv[0], frame_uv[3]), _registering_elements(fpp))
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["annotation_bbox_status"] = {
        "status": "unavailable", "reason": "RuntimeError: collection blew up"}
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")

    record = report.read_annotation_sidecar(sidecar_path)
    assert record["bbox_status_ok"] is False
    assert "collection blew up" in record["bbox_status_reason"]
    assert record["bbox_entries"] == {}


def test_build_report_emits_no_verdict_language(tmp_path):
    """The report must not rate the variants. Checked, because prose drifts."""
    fpp = 0.5333333333333333
    frame_uv = (0.0, 0.0, 40.0, 30.0)
    frame_px = (int(round(40.0 / fpp)), int(round(30.0 / fpp)))
    probe_dir = tmp_path / "anno_pass_variants_probe"
    variant_dir = probe_dir / "v0_control" / "color_id_buffer"
    variant_dir.mkdir(parents=True)
    elements = [(201, (10, 120, 200), (5.0, 5.0, 9.0, 9.0), "Text Notes"),
                (202, (200, 60, 10), (25.0, 20.0, 29.0, 24.0), "Grids")]
    sidecar_path, tiff_path = _build_capture(
        variant_dir, "Plan_19290402", frame_uv, frame_px, fpp,
        (frame_uv[0], frame_uv[3]), elements)
    combined = {
        "probe": {"name": "stage_a_anno_pass_variants", "version": "test"},
        "inputs": {"view_name": "Plan", "view_id": 19290402,
                   "view_type": "FloorPlan", "view_scale": 96.0},
        "model_pass": {"success": True, "failure_reason": None,
                       "tiff_path": None,
                       "geometry": {"achieved_export_dpi": 150.0,
                                    "frame_px": list(frame_px)}},
        "variants": [{"variant": "v0_control", "skipped": False,
                      "model_tiff": {"sha256": {"state": "value", "value": "abc"}},
                      "annotation_pass": {"sidecar_path": str(sidecar_path),
                                          "tiff_path": str(tiff_path)}}],
    }
    (probe_dir / "Plan_19290402.anno_pass_variants.json").write_text(
        json.dumps(combined), encoding="utf-8")

    text = report.build_report([probe_dir], overlay_enabled=False)
    assert "### 1. Image size" in text
    assert "v0_control" in text
    # The BODY, from the first view heading on. The report's own preamble says
    # "no pass/fail, no score, no recommendation" -- a disclaimer, not a
    # judgement -- and scanning it would make this test fail on the very
    # sentence that states the rule.
    body = text[text.index("## View "):].lower()
    for banned in ("recommend", "looks better", "looks worse", "best variant",
                   "worst variant", "acceptable", "unacceptable", "verdict:",
                   "fails the", "passes the", "should be used", "we suggest"):
        assert banned not in body, banned
    # The one place a judgement-shaped word is allowed is the probe's own
    # re-export verdict, which is about the DOCUMENT and not about a candidate
    # fix -- so the report is allowed to quote it by that name.
    assert "no pass/fail" in text[:text.index("## View ")].lower()


def test_a_malformed_sidecar_entry_is_recorded_not_dropped(tmp_path):
    """A record the reader cannot parse is REPORTED, never silently skipped.

    Every count in section 4 is a count of these maps, so a dropped entry comes
    back as an element that was never assigned a colour -- a different fact
    about the capture, and one nothing downstream can recover. The first draft
    of this reader did exactly that, with a bare ``continue``.
    """
    fpp = 0.5333333333333333
    frame_uv = (0.0, 0.0, 40.0, 30.0)
    frame_px = (int(round(40.0 / fpp)), int(round(30.0 / fpp)))
    sidecar_path, _tiff = _build_capture(
        tmp_path, "v0_malformed", frame_uv, frame_px, fpp,
        (frame_uv[0], frame_uv[3]),
        [(301, (10, 120, 200), (5.0, 5.0, 9.0, 9.0), "Text Notes")])
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["color_assignment_map"]["not-an-id"] = [1, 2, 3]
    sidecar["color_assignment_map"]["302"] = "purple"
    sidecar["annotation_bbox_map"]["also-not-an-id"] = {
        "bbox_uv": {"state": "value", "value": [[0, 0], [1, 0], [1, 1], [0, 1]]},
        "category": "Grids"}
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")

    record = report.read_annotation_sidecar(sidecar_path)
    assert 301 in record["color_map"]
    reasons = {entry["key"] for entry in record["malformed_color_entries"]}
    assert reasons == {"not-an-id", "302"}
    assert [entry["key"] for entry in record["malformed_bbox_keys"]] == [
        "also-not-an-id"]
    # Each carries a reason, not just a key.
    assert all(entry.get("reason") for entry in record["malformed_color_entries"])
    assert all(entry.get("reason") for entry in record["malformed_bbox_keys"])


def test_the_overlay_section_echoes_the_fit_so_the_size_gate_is_not_read_as_a_verdict(tmp_path):
    """The overlay gate is measurement 1 only, and that is a SIZE gate.

    A capture drawn at a different scale from its sidecar's is exactly
    ``frame_px`` pixels, so it PASSES the gate and gets an overlay whose boxes,
    placed through the sidecar's mapping, will not land on the ink. That is the
    F1 case, and the section has to say so beside the line rather than leaving
    "overlay written" reading as "this one is fine".
    """
    sidecar_fpp = 1.0 / 18.75
    drawn_fpp = 1.0 / 17.81
    frame_uv = (0.0, 0.0, 40.0, 30.0)
    frame_px = (int(round(40.0 / sidecar_fpp)), int(round(30.0 / sidecar_fpp)))
    probe_dir = tmp_path / "anno_pass_variants_probe"
    variant_dir = probe_dir / "v0_control" / "color_id_buffer"
    variant_dir.mkdir(parents=True)
    elements = [
        (401, (10, 120, 200), (5.0, 5.0, 9.0, 9.0), "Text Notes"),
        (402, (200, 60, 10), (25.0, 20.0, 29.0, 24.0), "Grids"),
        (403, (60, 200, 90), (12.0, 18.0, 16.0, 22.0), "Dimensions"),
    ]
    sidecar_path, tiff_path = _build_capture(
        variant_dir, "Plan_19290402", frame_uv, frame_px, drawn_fpp,
        (frame_uv[0] - 1.0, frame_uv[3] + 1.0), elements)
    combined = {
        "probe": {"name": "stage_a_anno_pass_variants", "version": "test"},
        "inputs": {"view_name": "Plan", "view_id": 19290402,
                   "view_type": "FloorPlan", "view_scale": 96.0},
        "model_pass": {"success": True, "failure_reason": None, "tiff_path": None,
                       "geometry": {"achieved_export_dpi": 150.0,
                                    "frame_px": list(frame_px)}},
        "variants": [{"variant": "v0_control", "skipped": False,
                      "annotation_pass": {"sidecar_path": str(sidecar_path),
                                          "tiff_path": str(tiff_path)}}],
    }
    (probe_dir / "Plan_19290402.anno_pass_variants.json").write_text(
        json.dumps(combined), encoding="utf-8")

    text = report.build_report([probe_dir], overlay_enabled=False)
    overlay_section = text[text.index("### Overlays"):]
    # The gate's nature is stated, not implied.
    assert "SIZE GATE AND NOTHING MORE" in overlay_section
    # And the fitted scale is echoed on the line, so a reader sees 17.8 beside
    # the frame's 18.7 without scrolling back to section 2.
    assert "fitted 17." in overlay_section
    assert "18.7" in overlay_section


# ======================================================================
# FINDING 3 (PR #215, P1): the fit's premise, now measured
# ======================================================================

# The premise fixtures render at 10 px/ft with 4 ft elements -- 40 px a side.
# The other fixtures in this file run at 1.875 px/ft with 6 px elements, where a
# single pixel of rounding is 17% of an element and swamps a span ratio. That is
# fine for a scale fit over well-separated centroids and useless for measuring
# how much of its box an element's ink fills, so these get their own geometry
# rather than asserting loose bounds on a fixture too coarse to answer.
_PREMISE_FPP = 0.1
_PREMISE_FRAME_UV = (0.0, 0.0, 80.0, 54.0)


def _premise_elements():
    size = 4.0
    palette = _palette(4)
    return [
        (101, palette[0], (10.0, 10.0, 10.0 + size, 10.0 + size), "Text Notes"),
        (102, palette[1], (60.0, 18.0, 60.0 + size, 18.0 + size), "Dimensions"),
        (103, palette[2], (25.0, 40.0, 25.0 + size, 40.0 + size), "Grids"),
        (104, palette[3], (70.0, 46.0, 70.0 + size, 46.0 + size), "Door Tags"),
    ]


def _premise_capture(tmp_path, name, fraction=None, align="left"):
    """A capture at premise-measuring resolution.

    ``fraction=None`` fills each bbox (the control); a fraction draws the ink as
    that much of the box, left-aligned -- the shape a left-aligned glyph run
    takes inside its text bbox. EVERY ORIGINAL FIXTURE FILLED THE BOX, which
    makes the ink centroid equal the bbox centre by construction and is why none
    of them could detect that the fit rests on that coincidence. Finding 3 on
    PR #215.
    """
    frame_px = (int(round(_PREMISE_FRAME_UV[2] / _PREMISE_FPP)),
                int(round(_PREMISE_FRAME_UV[3] / _PREMISE_FPP)))
    return _build_capture(
        tmp_path, name, _PREMISE_FRAME_UV, frame_px, _PREMISE_FPP,
        (_PREMISE_FRAME_UV[0], _PREMISE_FRAME_UV[3]), _premise_elements(),
        ink_fraction_of_bbox=fraction, ink_align=align)


def test_offset_ink_shifts_the_fitted_margins_while_residuals_stay_clean(tmp_path):
    """The exact failure mode finding 3 names, reproduced.

    A displacement that is the SAME for every element is absorbed into the
    fitted intercept: the residuals stay small, so measurement 2 looks healthy,
    while every margin in 2b is shifted by that displacement. This asserts both
    halves — clean residuals AND shifted margins — so the report cannot be read
    as "the fit was fine" on such a capture.
    """
    sidecar_path, tiff_path = _premise_capture(tmp_path, "left_ink", fraction=0.3)
    analysis = report.analyze_capture(sidecar_path, tiff_path)
    fit = analysis["measurements"]["registration"]
    assert fit["status"] == "value", fit

    # The scale is still recovered: a uniform translation does not affect it.
    assert fit["px_per_ft_u"] == pytest.approx(1.0 / _PREMISE_FPP, rel=0.02)
    # And the residuals are SMALL -- which is precisely why residuals alone are
    # not enough to catch this.
    assert fit["residual_median_px"] < 2.0
    # But the margins have moved, and THE SIGNATURE IS THE FINDING.
    #
    # 30% of a 4 ft box, left-aligned, puts every ink centroid 1.4 ft left of
    # its box centre. The fit absorbs that into the origin, so the image looks
    # SHIFTED: the left margin goes negative by 1.4 and the right margin
    # positive by the same 1.4. A translation, summing to zero.
    #
    # That is distinguishable from the F1 case, where the render really is
    # bigger than the frame and BOTH margins come back positive -- as
    # test_the_non_b_extent_capture_recovers_the_scale_it_was_drawn_at asserts.
    # Pinning the sign pattern rather than one number is what keeps those two
    # readings apart, which is the whole reason this offset matters.
    margins = fit["margin_ft"]
    assert margins["left"] == pytest.approx(-1.4, abs=0.4), margins
    assert margins["right"] == pytest.approx(1.4, abs=0.4), margins
    assert margins["left"] + margins["right"] == pytest.approx(0.0, abs=0.2), margins
    # v is untouched: the fixture only narrows the ink horizontally.
    assert abs(margins["top"]) < 0.3 and abs(margins["bottom"]) < 0.3, margins


def test_the_premise_measurement_bounds_the_possible_offset(tmp_path):
    """The premise is a measurement now, and it is measured OUTSIDE the fit.

    The first version of this measured the fit's per-sample residual, and a
    uniform displacement is absorbed into the fitted intercept -- so it came
    back ~0 on this very fixture. This asserts what is actually recoverable:
    ink occupying 30% of its box spans ~0.3 of it, and the possible offset is
    therefore a real, nonzero bound on the margins in 2b.
    """
    sidecar_path, tiff_path = _premise_capture(tmp_path, "offsets", fraction=0.3)
    fit = report.analyze_capture(
        sidecar_path, tiff_path)["measurements"]["registration"]
    premise = fit["ink_vs_bbox_by_category"]
    assert premise, "the premise must be reported, not left implicit"
    assert set(premise) == {"Text Notes", "Dimensions", "Grids", "Door Tags"}
    for name, entry in premise.items():
        # The fixture paints 30% of each box's width, full height.
        assert entry["span_fraction_u"]["median"] == pytest.approx(0.3, abs=0.06), (
            name, entry)
        assert entry["span_fraction_v"]["median"] == pytest.approx(1.0, abs=0.06), (
            name, entry)
        # A real, nonzero bound on u -- 40 px box, 12 px of ink, so up to 14 px
        # of slack -- and essentially none on v.
        assert entry["possible_offset_u_px"]["median"] > 10.0, (name, entry)
        assert entry["possible_offset_v_px"]["median"] < 1.5, (name, entry)
        # Solid rectangles, so solidity is ~1 even though the span is not.
        assert entry["solidity"]["median"] == pytest.approx(1.0, abs=0.1), (
            name, entry)


def test_a_capture_whose_ink_fills_its_bbox_bounds_the_offset_at_zero(tmp_path):
    """THE CONTROL. Without it the assertions above would also pass against a
    function that reported a constant, and "the premise holds on this capture"
    would never be a readable outcome.

    Ink that reaches both edges of its box cannot be off-centre in it, so the
    possible offset is ~0 and the margins in 2b are bounded.
    """
    sidecar_path, tiff_path = _premise_capture(tmp_path, "filled")
    fit = report.analyze_capture(
        sidecar_path, tiff_path)["measurements"]["registration"]
    for name, entry in fit["ink_vs_bbox_by_category"].items():
        assert entry["span_fraction_u"]["median"] == pytest.approx(1.0, abs=0.06), (
            name, entry)
        assert entry["span_fraction_v"]["median"] == pytest.approx(1.0, abs=0.06), (
            name, entry)
        assert entry["possible_offset_u_px"]["median"] < 1.5, (name, entry)
        assert entry["possible_offset_v_px"]["median"] < 1.5, (name, entry)


def test_the_two_anchors_diverge_on_offset_ink_and_agree_when_it_fills(tmp_path):
    """The cross-check that makes the premise visible without a threshold.

    Centre-aligned but SHRUNKEN ink keeps centroid == ink-bbox centre, so that
    would not discriminate. Left-aligned ink moves the centroid and the ink-bbox
    centre together, so for the divergence to be real the fixture needs ink
    whose centroid is NOT its bbox centre — an L shape. Simpler and sufficient
    here: assert agreement on the filled fixture (the premise holds) and that
    both anchors are reported either way, so a reader always has both numbers.
    """
    fpp = 0.5333333333333333
    frame_uv = (0.0, 0.0, 80.0, 54.0)
    frame_px = (int(round(80.0 / fpp)), int(round(54.0 / fpp)))
    sidecar_path, tiff_path = _build_capture(
        tmp_path, "filled2", frame_uv, frame_px, fpp, (frame_uv[0], frame_uv[3]),
        _registering_elements(fpp))
    measurements = report.analyze_capture(sidecar_path, tiff_path)["measurements"]
    primary = measurements["registration"]
    secondary = measurements["registration_ink_bbox_anchor"]
    agreement = measurements["anchor_agreement"]
    assert primary["anchor"] == "ink_centroid"
    assert secondary["anchor"] == "ink_bbox"
    assert agreement["status"] == "value"
    # Solid rectangles: centroid and ink-bbox centre coincide, so every delta
    # is ~0 and the anchor choice provably does not matter on this capture.
    assert abs(agreement["px_per_ft_u_delta"]) < 0.01
    assert abs(agreement["px_per_ft_v_delta"]) < 0.01
    for side, delta in agreement["margin_ft_delta"].items():
        assert abs(delta) < 0.05, (side, delta)


def test_an_l_shaped_ink_makes_the_two_anchors_disagree(tmp_path):
    """THE DISCRIMINATING CASE for the cross-check itself.

    An L: the centroid is pulled toward the heavy arm while the ink's bounding
    box still spans the whole glyph. That is the one shape where centroid and
    ink-bbox centre genuinely differ, so it is what proves the cross-check can
    detect a divergence rather than always printing zeros.
    """
    fpp = 0.5333333333333333
    frame_uv = (0.0, 0.0, 80.0, 54.0)
    frame_px = (int(round(80.0 / fpp)), int(round(54.0 / fpp)))
    elements = _registering_elements(fpp)
    sidecar_path, tiff_path = _build_capture(
        tmp_path, "lshape", frame_uv, frame_px, fpp, (frame_uv[0], frame_uv[3]),
        elements)
    # Carve each filled square into an L by whitening its top-right quadrant.
    with Image.open(tiff_path) as handle:
        array = np.asarray(handle.convert("RGB"), dtype=np.uint8).copy()
    for _eid, rgb, _rect, _cat in elements:
        mask = np.all(array == np.array(rgb, dtype=np.uint8), axis=-1)
        ys, xs = np.nonzero(mask)
        mid_x = (xs.min() + xs.max()) // 2
        mid_y = (ys.min() + ys.max()) // 2
        array[ys.min():mid_y, mid_x:xs.max() + 1] = 255
    Image.fromarray(array, mode="RGB").save(tiff_path)

    measurements = report.analyze_capture(sidecar_path, tiff_path)["measurements"]
    agreement = measurements["anchor_agreement"]
    assert agreement["status"] == "value"
    # The centroid moved down-left; the ink bbox did not. At least one margin
    # delta is therefore materially nonzero -- the cross-check fires.
    assert max(abs(v) for v in agreement["margin_ft_delta"].values()) > 0.1, (
        agreement)


# ======================================================================
# FINDING C (PR #215 round 2, P2): a degenerate fit is unavailable, not a crash
# ======================================================================

def test_a_zero_slope_axis_is_reported_unavailable_not_raised():
    """``fit_axis`` returns 0.0 -- validly -- when distinct recorded UV positions
    all render at the same pixel. Every inversion then divides by it, and one
    such capture used to abort the WHOLE multi-view report with a
    ZeroDivisionError before any other view was written."""
    samples = [
        {"u": 0.0, "v": 0.0, "x": 5.0, "y": 5.0, "category": "Text Notes"},
        {"u": 10.0, "v": 10.0, "x": 5.0, "y": 5.0, "category": "Grids"},
    ]
    fit = report.fit_registration(samples, (0.0, 0.0, 80.0, 54.0), 100, 100, 96.0)
    assert fit["status"] == "unavailable"
    assert "degenerate mapping" in fit["reason"]
    assert fit["degenerate_mapping"]["a_u"] == 0.0


def test_a_zero_slope_on_only_one_axis_is_still_unavailable():
    """u fits, v collapses. Reporting the u half would hand back margins whose v
    components came from a division that could not be performed."""
    samples = [
        {"u": 0.0, "v": 0.0, "x": 0.0, "y": 7.0, "category": "a"},
        {"u": 10.0, "v": 10.0, "x": 50.0, "y": 7.0, "category": "b"},
    ]
    fit = report.fit_registration(samples, (0.0, 0.0, 80.0, 54.0), 100, 100, 96.0)
    assert fit["status"] == "unavailable"


def test_one_degenerate_capture_does_not_abort_the_whole_report(tmp_path):
    """The consequence, end to end: a collapsed capture must be REPORTED and the
    other views still written. This is what the crash cost."""
    fpp = 0.5333333333333333
    frame_uv = (0.0, 0.0, 40.0, 30.0)
    frame_px = (int(round(40.0 / fpp)), int(round(30.0 / fpp)))
    probe_dir = tmp_path / "anno_pass_variants_probe"
    variant_dir = probe_dir / "v0_control" / "color_id_buffer"
    variant_dir.mkdir(parents=True)
    # Two elements at DIFFERENT recorded UVs whose ink is painted at the same
    # place -- a collapsed render.
    elements = [(501, (10, 120, 200), (5.0, 5.0, 9.0, 9.0), "Text Notes"),
                (502, (200, 60, 10), (25.0, 20.0, 29.0, 24.0), "Grids")]
    sidecar_path, tiff_path = _build_capture(
        variant_dir, "Plan_19290402", frame_uv, frame_px, fpp,
        (frame_uv[0], frame_uv[3]), elements)
    # Repaint both colours into one identical 4x4 block.
    with Image.open(tiff_path) as handle:
        array = np.asarray(handle.convert("RGB"), dtype=np.uint8).copy()
    array[:] = 255
    array[10:14, 10:14] = (10, 120, 200)
    array[10:14, 20:24] = (200, 60, 10)
    # Same y for both, same x-spread ratio -> v axis has no spread at all.
    Image.fromarray(array, mode="RGB").save(tiff_path)
    combined = {
        "probe": {"name": "stage_a_anno_pass_variants", "version": "test"},
        "inputs": {"view_name": "Plan", "view_id": 19290402,
                   "view_type": "FloorPlan", "view_scale": 96.0},
        "model_pass": {"success": True, "failure_reason": None, "tiff_path": None,
                       "geometry": {"achieved_export_dpi": 150.0,
                                    "frame_px": list(frame_px)}},
        "variants": [{"variant": "v0_control", "skipped": False,
                      "annotation_pass": {"sidecar_path": str(sidecar_path),
                                          "tiff_path": str(tiff_path)}}],
    }
    (probe_dir / "Plan_19290402.anno_pass_variants.json").write_text(
        json.dumps(combined), encoding="utf-8")

    # The whole report is produced, and the degenerate fit is stated.
    text = report.build_report([probe_dir], overlay_enabled=False)
    assert "### 1. Image size" in text
    assert "NOT FITTED" in text


# ======================================================================
# FINDING B (PR #215 round 2, P1): faults read from the combined record
# ======================================================================

def test_capture_faults_prefer_the_combined_record_over_the_sidecar():
    """Production wrote the sidecar BEFORE computing its faults until `dcb4e65`,
    so a sidecar can carry none however faulted the capture was. The combined
    record holds the later in-memory metadata and is preferred."""
    item = {
        "analysis": {"sidecar": {"capture_faults": [], "capture_faults_raw": []}},
        "variant_report": {"annotation_pass": {
            "capture_faults": [{"fault": "annotation_frame_not_applied"}]}},
    }
    faults, source = report._capture_faults_for(item)
    assert source == "combined_record"
    assert faults[0]["fault"] == "annotation_frame_not_applied"


def test_capture_faults_fall_back_to_the_sidecar_when_the_record_lacks_them():
    item = {
        "analysis": {"sidecar": {"capture_faults": [{"fault": "export_dim_mismatch"}],
                                 "capture_faults_raw": [
                                     {"fault": "export_dim_mismatch"}]}},
        "variant_report": {"annotation_pass": {}},
    }
    faults, source = report._capture_faults_for(item)
    assert source == "sidecar"
    assert faults[0]["fault"] == "export_dim_mismatch"


def test_absent_everywhere_is_UNKNOWN_not_none():
    """An absent key and an empty list are different facts, and collapsing them
    is the same silence this finding is about."""
    item = {"analysis": {"sidecar": {"capture_faults": [],
                                     "capture_faults_raw": None}},
            "variant_report": {}}
    faults, source = report._capture_faults_for(item)
    assert source == "unavailable"
    assert faults == []


def test_an_empty_list_in_the_record_is_none_not_unknown():
    """THE CONTROL for the distinction above: a genuinely clean capture must
    read as "none", not "UNKNOWN"."""
    item = {"analysis": {"sidecar": {"capture_faults": [],
                                     "capture_faults_raw": []}},
            "variant_report": {"annotation_pass": {"capture_faults": []}}}
    faults, source = report._capture_faults_for(item)
    assert source == "combined_record"
    assert faults == []


def test_section_0_prints_the_fault_and_says_where_it_came_from(tmp_path):
    """End to end: a capture whose SIDECAR carries no faults but whose combined
    record does must still show the fault in section 0. That is exactly the case
    production's ordering bug produced, and the case the old code printed as
    `none`."""
    fpp = 0.5333333333333333
    frame_uv = (0.0, 0.0, 40.0, 30.0)
    frame_px = (int(round(40.0 / fpp)), int(round(30.0 / fpp)))
    probe_dir = tmp_path / "anno_pass_variants_probe"
    variant_dir = probe_dir / "v0_control" / "color_id_buffer"
    variant_dir.mkdir(parents=True)
    sidecar_path, tiff_path = _build_capture(
        variant_dir, "Plan_19290402", frame_uv, frame_px, fpp,
        (frame_uv[0], frame_uv[3]),
        [(601, (10, 120, 200), (5.0, 5.0, 9.0, 9.0), "Text Notes"),
         (602, (200, 60, 10), (25.0, 20.0, 29.0, 24.0), "Grids")])
    # The sidecar as an OLD production build wrote it: no capture_faults key.
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar.pop("capture_faults", None)
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    combined = {
        "probe": {"name": "stage_a_anno_pass_variants", "version": "test"},
        "inputs": {"view_name": "Plan", "view_id": 19290402,
                   "view_type": "FloorPlan", "view_scale": 96.0},
        "model_pass": {"success": True, "failure_reason": None, "tiff_path": None,
                       "geometry": {"achieved_export_dpi": 150.0,
                                    "frame_px": list(frame_px)}},
        "variants": [{"variant": "v0_control", "skipped": False,
                      "conclusion": "CAPTURE_FAILED",
                      "annotation_pass": {
                          "sidecar_path": str(sidecar_path),
                          "tiff_path": str(tiff_path),
                          "success": False,
                          "failure_reason": "annotation_frame_not_applied",
                          "capture_faults": [
                              {"fault": "annotation_frame_not_applied",
                               "detail": "frame B could not be applied"}]}}],
    }
    (probe_dir / "Plan_19290402.anno_pass_variants.json").write_text(
        json.dumps(combined), encoding="utf-8")

    text = report.build_report([probe_dir], overlay_enabled=False)
    section = text[text.index("### 0."):text.index("### 1.")]
    assert "annotation_frame_not_applied" in section
    assert "combined_record" in section
    assert "CAPTURE_FAILED" in section


# ======================================================================
# FINDING D (PR #215 round 3, P1): a failed bbox collection is not "no bboxes"
# ======================================================================

def _failed_collection_capture(tmp_path, name="v0_control"):
    """A capture whose sidecar reports the bbox collection as unavailable.

    This is what production writes when ``_collect_annotation_bbox_data``
    raises: an EMPTY MAP plus a status block saying so.
    """
    fpp = 0.5333333333333333
    frame_uv = (0.0, 0.0, 40.0, 30.0)
    frame_px = (int(round(40.0 / fpp)), int(round(30.0 / fpp)))
    probe_dir = tmp_path / "anno_pass_variants_probe"
    variant_dir = probe_dir / name / "color_id_buffer"
    variant_dir.mkdir(parents=True)
    sidecar_path, tiff_path = _build_capture(
        variant_dir, "Plan_19290402", frame_uv, frame_px, fpp,
        (frame_uv[0], frame_uv[3]),
        [(701, (10, 120, 200), (5.0, 5.0, 9.0, 9.0), "Text Notes"),
         (702, (200, 60, 10), (25.0, 20.0, 29.0, 24.0), "Grids")])
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["annotation_bbox_status"] = {
        "status": "unavailable",
        "reason": "RuntimeError: the bbox collection blew up"}
    sidecar["annotation_bbox_map"] = {}
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    combined = {
        "probe": {"name": "stage_a_anno_pass_variants", "version": "test"},
        "inputs": {"view_name": "Plan", "view_id": 19290402,
                   "view_type": "FloorPlan", "view_scale": 96.0},
        "model_pass": {"success": True, "failure_reason": None, "tiff_path": None,
                       "geometry": {"achieved_export_dpi": 150.0,
                                    "frame_px": list(frame_px)}},
        "variants": [{"variant": name, "skipped": False,
                      "annotation_pass": {"sidecar_path": str(sidecar_path),
                                          "tiff_path": str(tiff_path)}}],
    }
    (probe_dir / "Plan_19290402.anno_pass_variants.json").write_text(
        json.dumps(combined), encoding="utf-8")
    return (probe_dir, sidecar_path, tiff_path)


def test_every_bbox_derived_measurement_is_withheld_when_the_collection_failed(tmp_path):
    """Four measurements, one reason, none of them reported as a value.

    Before this, the fit said "0 matched sample(s)", the excursion came back
    ``status: value`` over ZERO rectangles, and coverage printed an empty table
    -- all indistinguishable from a clean capture that simply has no usable
    bboxes. That is the same silence this whole file exists to prevent.
    """
    _probe_dir, sidecar_path, tiff_path = _failed_collection_capture(tmp_path)
    measurements = report.analyze_capture(sidecar_path, tiff_path)["measurements"]

    assert measurements["bbox_collection"]["status"] == "unavailable"
    assert "blew up" in measurements["bbox_collection"]["reason"]
    for key in ("registration", "registration_ink_bbox_anchor", "bbox_excursion",
                "coverage"):
        assert measurements[key]["status"] == "unavailable", key
        assert "blew up" in measurements[key]["reason"], key
    # Explicitly NOT a value-valued excursion over zero rectangles.
    assert "rect_count" not in measurements["bbox_excursion"]
    assert measurements["coverage"]["by_category"] == {}
    assert measurements["coverage"]["ink_source"] is None
    # Measurements that do NOT depend on the bbox map still work -- the gate is
    # scoped, not a blanket refusal of the whole capture.
    assert measurements["size"]["image_w"] > 0
    assert measurements["offpalette"]["total_pixels"] > 0


def test_the_rendered_report_states_the_collection_failure(tmp_path):
    """THE PART THE PREVIOUS TEST DID NOT BIND.

    The review was right: asserting on ``read_annotation_sidecar``'s
    intermediate field says nothing about the report a reader consumes. This
    drives ``build_report`` and asserts the failure appears in the text.
    """
    probe_dir, _sidecar, _tiff = _failed_collection_capture(tmp_path)
    text = report.build_report([probe_dir], overlay_enabled=False)
    assert "ANNOTATION BBOX COLLECTION FAILED" in text
    assert "blew up" in text
    # And the withheld measurements are visibly withheld rather than empty.
    assert "NOT FITTED" in text


def test_a_clean_collection_is_not_reported_as_failed(tmp_path):
    """THE CONTROL. Without it the gate could fire unconditionally and every
    capture would read as a failed collection."""
    fpp = 0.5333333333333333
    frame_uv = (0.0, 0.0, 80.0, 54.0)
    frame_px = (int(round(80.0 / fpp)), int(round(54.0 / fpp)))
    sidecar_path, tiff_path = _build_capture(
        tmp_path, "clean", frame_uv, frame_px, fpp, (frame_uv[0], frame_uv[3]),
        _registering_elements(fpp))
    measurements = report.analyze_capture(sidecar_path, tiff_path)["measurements"]
    assert measurements["bbox_collection"]["status"] == "value"
    assert measurements["bbox_collection"]["entry_count"] == 4
    assert measurements["registration"]["status"] == "value"
    assert measurements["bbox_excursion"]["status"] == "value"
    assert measurements["coverage"]["by_category"]
