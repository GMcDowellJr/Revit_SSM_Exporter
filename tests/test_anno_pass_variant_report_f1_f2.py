"""Round 2 (revised) of tools/notes/anno_pass_variant_report.py: F1 and F2.

THE FIXTURES CARRY THE ANSWER, computed by hand, as in the round-1 file: every
image is drawn at a known px/ft and origin, so the mapping the analyzer
RECOVERS is compared against the one the fixture was DRAWN with -- never
against a re-run of the analyzer's own arithmetic.

The discriminating fixtures:
  * the boundary is drawn at a DIFFERENT scale from the lattice, so a decoder
    that returned the lattice instead of measuring would fail;
  * a long stray line sits beside the boundary, so "the longest band is the
    boundary" is not enough -- the four must CLOSE into one rectangle;
  * an L-shaped crop, so a decoder assuming four edges fails;
  * a fiducial image whose AABB is deliberately looser than its ink, so the
    residual has something to report.
"""
from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image

from tools.notes import anno_pass_variant_report as report

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
FID_A = (251, 11, 139)
FID_B = (11, 139, 251)
GRID = (248, 0, 0)

# The drawing: 12 px/ft, u origin at x = 30, v origin (v = 0) at y = 330.
A_U, B_U = 12.0, 30.0
A_V, B_V = -12.0, 330.0
W, H = 480, 360
CROP_UV = (2.0, 3.0, 32.0, 24.0)


def _x(u):
    return A_U * u + B_U


def _y(v):
    return A_V * v + B_V


def _canvas():
    return np.full((H, W, 3), 255, dtype=np.uint8)


def _outline(img, x0, y0, x1, y1, thickness=2, colour=BLACK):
    """A rectangle outline whose line CENTRES sit on x0/x1/y0/y1."""
    half = thickness // 2
    img[int(y0) - half:int(y0) - half + thickness, int(x0):int(x1)] = colour
    img[int(y1) - half:int(y1) - half + thickness, int(x0):int(x1)] = colour
    img[int(y0):int(y1), int(x0) - half:int(x0) - half + thickness] = colour
    img[int(y0):int(y1), int(x1) - half:int(x1) - half + thickness] = colour


def _save(img, path):
    Image.fromarray(img).save(str(path))
    return str(path)


def _crop_image(tmp_path, stray=False, name="crop.tiff"):
    img = _canvas()
    _outline(img, _x(CROP_UV[0]), _y(CROP_UV[3]), _x(CROP_UV[2]), _y(CROP_UV[1]))
    if stray:
        # A long straight line that closes no rectangle: model ink, say.
        img[15, 40:420] = (60, 60, 60)
    return _save(img, tmp_path / name)


_RECT_SHAPE = {"is_rectangle": True, "distinct_u_levels": [2.0, 32.0],
               "distinct_v_levels": [3.0, 24.0]}


# ======================================================================
# F1 -- recovering the boundary
# ======================================================================

def test_the_boundary_recovers_the_scale_it_was_DRAWN_at(tmp_path):
    path = _crop_image(tmp_path)
    boundary = report.recover_crop_boundary(
        report.scan_axis_lines(path, []), _RECT_SHAPE, CROP_UV)
    assert boundary["status"] == "value", boundary
    assert boundary["px_per_ft_u"] == pytest.approx(A_U, rel=1e-3)
    assert boundary["px_per_ft_v"] == pytest.approx(abs(A_V), rel=1e-3)
    assert boundary["isotropy_u_over_v"] == pytest.approx(1.0, rel=1e-3)
    assert boundary["v_axis_sign"] == "negative (expected)"
    assert boundary["mapping"]["b_u"] == pytest.approx(B_U, abs=0.01)
    assert boundary["mapping"]["b_v"] == pytest.approx(B_V, abs=0.01)


def test_the_boundary_rects_cover_exactly_the_drawn_line_pixels(tmp_path):
    """What a consumer subtracts must be the boundary and nothing else."""
    path = _crop_image(tmp_path)
    boundary = report.recover_crop_boundary(
        report.scan_axis_lines(path, []), _RECT_SHAPE, CROP_UV)
    img = np.asarray(Image.open(path).convert("RGB"))
    mask = np.zeros(img.shape[:2], dtype=bool)
    for x0, y0, x1, y1 in boundary["boundary_px_rect"]:
        mask[y0:y1, x0:x1] = True
    dark = np.all(img == 0, axis=-1)
    assert not (dark & ~mask).any(), "boundary pixels left outside the rects"
    assert dark[mask].mean() > 0.95, "the rects swallow non-boundary pixels"


def test_a_stray_long_line_is_not_mistaken_for_the_boundary(tmp_path):
    """The longest band alone is not the boundary: the four must close."""
    path = _crop_image(tmp_path, stray=True)
    boundary = report.recover_crop_boundary(
        report.scan_axis_lines(path, []), _RECT_SHAPE, CROP_UV)
    assert boundary["status"] == "value"
    assert boundary["px_per_ft_v"] == pytest.approx(abs(A_V), rel=1e-3)
    assert boundary["bands"]["top"]["centre"] == pytest.approx(_y(CROP_UV[3]), abs=0.6)


def test_a_capture_with_no_boundary_says_NOT_FOUND_with_the_reason(tmp_path):
    img = _canvas()
    img[50:60, 50:60] = GRID
    path = _save(img, tmp_path / "none.tiff")
    boundary = report.recover_crop_boundary(
        report.scan_axis_lines(path, [GRID]), _RECT_SHAPE, CROP_UV)
    assert boundary["status"] == "not_found"
    assert "NOT in this capture" in boundary["reason"]


def test_lines_that_do_not_close_are_not_found_rather_than_forced(tmp_path):
    img = _canvas()
    img[100, 40:420] = BLACK
    img[20:340, 200] = BLACK   # crosses, but no four close
    path = _save(img, tmp_path / "cross.tiff")
    boundary = report.recover_crop_boundary(
        report.scan_axis_lines(path, []), _RECT_SHAPE, CROP_UV)
    assert boundary["status"] == "not_found"
    assert "close" in boundary["reason"]


def test_palette_and_fiducial_colours_are_never_boundary_candidates(tmp_path):
    """A long GRID line in a palette colour is annotation, not the boundary."""
    img = _canvas()
    _outline(img, 50, 40, 400, 300, colour=GRID)
    path = _save(img, tmp_path / "grid.tiff")
    boundary = report.recover_crop_boundary(
        report.scan_axis_lines(path, [GRID]), _RECT_SHAPE, CROP_UV)
    assert boundary["status"] == "not_found"


def test_an_L_shaped_crop_is_matched_level_by_level_not_as_four_edges(tmp_path):
    img = _canvas()
    # L: (2,3)-(32,3)-(32,12)-(20,12)-(20,24)-(2,24)
    pts = [(2, 3), (32, 3), (32, 12), (20, 12), (20, 24), (2, 24)]
    for (u_a, v_a), (u_b, v_b) in zip(pts, pts[1:] + pts[:1]):
        x_a, x_b = sorted((int(_x(u_a)), int(_x(u_b))))
        y_a, y_b = sorted((int(_y(v_a)), int(_y(v_b))))
        img[y_a:y_b + 1, x_a:x_b + 1] = BLACK
    path = _save(img, tmp_path / "L.tiff")
    shape = {"is_rectangle": False, "distinct_u_levels": [2.0, 20.0, 32.0],
             "distinct_v_levels": [3.0, 12.0, 24.0]}
    boundary = report.recover_crop_boundary(
        report.scan_axis_lines(path, []), shape, None)
    assert boundary["status"] == "value", boundary
    assert boundary["px_per_ft_u"] == pytest.approx(A_U, rel=0.01)
    assert boundary["px_per_ft_v"] == pytest.approx(abs(A_V), rel=0.01)
    assert "RANK" in boundary["match"]


def test_a_missing_shape_falls_back_to_the_crop_box_and_SAYS_so(tmp_path):
    path = _crop_image(tmp_path)
    boundary = report.recover_crop_boundary(
        report.scan_axis_lines(path, []), None, CROP_UV)
    assert boundary["status"] == "value"
    assert "ASSUMED rectangular" in boundary["levels_source"]


# ======================================================================
# F2 -- the fiducials
# ======================================================================

def _fiducial_image(tmp_path, clip_second=False):
    img = _canvas()
    # A: UV (5,6)-(7,8); B: UV (26,19)-(29,21). Drawn exactly.
    img[int(_y(8)):int(_y(6)), int(_x(5)):int(_x(7))] = FID_A
    if clip_second:
        img[0:20, 0:30] = FID_B
    else:
        img[int(_y(21)):int(_y(19)), int(_x(26)):int(_x(29))] = FID_B
    return _save(img, tmp_path / "fid.tiff")


_FIDUCIALS = [{"id": 1, "rgb": list(FID_A), "rect_uv": [5.0, 6.0, 7.0, 8.0]},
              {"id": 2, "rgb": list(FID_B), "rect_uv": [26.0, 19.0, 29.0, 21.0]}]


def test_f2_recovers_the_drawn_mapping_from_the_ink_edges(tmp_path):
    path = _fiducial_image(tmp_path)
    scan = report.scan_image(path, [], [FID_A, FID_B])
    f2 = report.fiducial_fit(_FIDUCIALS, scan["blobs"], scan["image_w"],
                             scan["image_h"])
    assert f2["status"] == "value", f2
    assert f2["px_per_ft_u"] == pytest.approx(A_U, rel=1e-6)
    assert f2["px_per_ft_v"] == pytest.approx(abs(A_V), rel=1e-6)
    assert f2["residual_max_px"]["u"] == pytest.approx(0.0, abs=1e-6)


def test_a_looser_recorded_box_shows_up_as_a_residual_not_a_clean_fit(tmp_path):
    """A projected AABB can be looser than the ink. That must be VISIBLE."""
    path = _fiducial_image(tmp_path)
    scan = report.scan_image(path, [], [FID_A, FID_B])
    loose = [dict(_FIDUCIALS[0], rect_uv=[4.0, 6.0, 7.0, 8.0]), _FIDUCIALS[1]]
    f2 = report.fiducial_fit(loose, scan["blobs"], scan["image_w"], scan["image_h"])
    assert f2["residual_max_px"]["u"] > 1.0


def test_a_clipped_fiducial_is_excluded_and_f2_is_unavailable(tmp_path):
    path = _fiducial_image(tmp_path, clip_second=True)
    scan = report.scan_image(path, [], [FID_A, FID_B])
    f2 = report.fiducial_fit(_FIDUCIALS, scan["blobs"], scan["image_w"],
                             scan["image_h"])
    assert f2["status"] == "unavailable"
    assert "1 usable" in f2["reason"]


MODEL_A = (10, 20, 30)
MODEL_B = (40, 50, 60)


def _model_anchored(tmp_path, model_draws_second):
    """Drive _model_anchored_fiducials: the model capture is the same drawing
    in the model pass's own colours, on a lattice whose bounds_xy is exact."""
    anno = _fiducial_image(tmp_path)
    scan = report.scan_image(anno, [], [FID_A, FID_B])
    model = _canvas()
    model[int(_y(8)):int(_y(6)), int(_x(5)):int(_x(7))] = MODEL_A
    if model_draws_second:
        model[int(_y(21)):int(_y(19)), int(_x(26)):int(_x(29))] = MODEL_B
    model_tiff = _save(model, tmp_path / "model.tiff")
    sidecar = tmp_path / "model.json"
    sidecar.write_text(json.dumps({
        "color_assignment_map": {"1": list(MODEL_A), "2": list(MODEL_B)},
        "bounds_xy": [(0 - B_U) / A_U, (H - B_V) / A_V,
                      (W - B_U) / A_U, (0 - B_V) / A_V],
    }), encoding="utf-8")
    return report._model_anchored_fiducials(
        _FIDUCIALS, scan["blobs"], scan["image_w"], scan["image_h"],
        {"model_sidecar": str(sidecar), "model_tiff": str(model_tiff)})


def test_model_anchored_f2_recovers_the_mapping_when_both_draw(tmp_path):
    """Control: the refusal below must not be what every case returns."""
    f2 = _model_anchored(tmp_path, model_draws_second=True)
    assert f2["status"] == "value", f2
    assert f2["px_per_ft_u"] == pytest.approx(A_U, rel=1e-6)
    assert f2["px_per_ft_v"] == pytest.approx(abs(A_V), rel=1e-6)


def test_a_fiducial_the_model_capture_did_not_draw_makes_f2_unavailable(tmp_path):
    """Round 2 plan, probe .4: the wall had a model colour but drew nothing in
    the model capture, so it had no UV extent -- and the fit ran on the other
    fiducial alone, two points per axis, zero residual, reported as a value
    (11.72 / 18.74 px/ft). One fiducial is not F2."""
    f2 = _model_anchored(tmp_path, model_draws_second=False)
    assert f2["status"] == "unavailable", f2
    assert "1 usable" in f2["reason"]
    assert f2["ink_pixels_annotation_vs_model"][1]["model_px"] is None


def test_fiducial_pixels_are_neither_palette_nor_offpalette(tmp_path):
    """They are there on purpose; in the off-palette tally they would read as
    model ink the suppression missed."""
    path = _fiducial_image(tmp_path)
    scan = report.scan_image(path, [], [FID_A, FID_B])
    assert scan["fiducial_pixels"] == 24 * 24 + 36 * 24
    assert scan["offpalette_pixels"] == 0
    assert scan["palette_pixels"] == 0


# ======================================================================
# agreement, datums, residue
# ======================================================================

def test_mapping_agreement_reports_the_corner_disagreement_in_pixels():
    first = {"a_u": 12.0, "b_u": 30.0, "a_v": -12.0, "b_v": 330.0}
    second = {"a_u": 12.0, "b_u": 32.0, "a_v": -12.0, "b_v": 327.0}
    agreement = report.mapping_agreement(first, second, CROP_UV)
    assert agreement["px_per_ft_u_delta"] == pytest.approx(0.0)
    assert agreement["worst_corner_px"] == pytest.approx(3.0)
    assert report.mapping_agreement(None, second, CROP_UV)["status"] == "unavailable"


def test_a_lengthened_datum_reads_longer_than_its_recorded_bbox(tmp_path):
    """The V0 defect, in miniature: the grid's ink runs 5 ft past the bbox that
    was recorded against the authored crop."""
    img = _canvas()
    img[int(_y(10)) - 1:int(_y(10)) + 1, int(_x(4)):int(_x(30))] = GRID
    path = _save(img, tmp_path / "datum.tiff")
    scan = report.scan_image(path, [GRID])
    record = {"bbox_entries": {7: {"membership_basis": "datum_category",
                                   "category": "Grids",
                                   "rect_uv": (4.0, 9.9, 25.0, 10.1)}},
              "color_map": {7: GRID}}
    mapping = {"a_u": A_U, "b_u": B_U, "a_v": A_V, "b_v": B_V}
    datums = report.datum_extents(record, scan["blobs"], mapping, 96.0)
    row = datums["datums"][0]
    assert row["status"] == "value"
    assert row["long_axis"] == "u"
    assert row["right_ft"] == pytest.approx(5.0, abs=0.1)
    assert row["left_ft"] == pytest.approx(0.0, abs=0.1)
    assert row["length_delta_ft"] == pytest.approx(5.0, abs=0.1)
    assert row["length_delta_paper_in"] == pytest.approx(5.0 * 12 / 96, abs=0.02)


def test_datums_without_a_mapping_are_unavailable_not_zero():
    datums = report.datum_extents({"bbox_entries": {}, "color_map": {}}, {}, None, 96)
    assert datums["status"] == "unavailable"


def test_residue_excludes_only_the_boundary_rectangles(tmp_path):
    img = _canvas()
    img[10:12, 10:100] = BLACK          # boundary band
    img[200:203, 200:203] = (40, 40, 40)  # 9 px of residue
    path = _save(img, tmp_path / "residue.tiff")
    assert report.count_offpalette_outside(path, [], [[10, 10, 100, 12]]) == 9
    assert report.count_offpalette_outside(path, [], []) == 9 + 180


# ======================================================================
# the frame an UNTOUCHED capture is measured against
# ======================================================================

def test_an_untouched_capture_is_framed_by_the_AUTHORED_crop():
    rect, reason = report.frame_rect_for(
        {"rendered_uv": None, "crop_mode": "untouched"}, list(CROP_UV))
    assert rect == CROP_UV and reason is None


def test_an_untouched_crop_less_capture_has_no_frame_and_says_why():
    rect, reason = report.frame_rect_for(
        {"rendered_uv": None, "crop_mode": "untouched"}, None)
    assert rect is None
    assert "no ACTIVE authored crop" in reason


def test_a_frame_b_capture_without_rendered_uv_is_still_refused():
    """The control: the untouched branch must not swallow a frame_b capture
    whose crop failed to apply."""
    rect, reason = report.frame_rect_for(
        {"rendered_uv": None, "crop_mode": "frame_b"}, list(CROP_UV))
    assert rect is None
    assert "frame B was not applied" in reason


def test_a_frameless_fit_still_measures_the_scale_and_withholds_margins():
    samples = [{"u": u, "v": v, "x": A_U * u + B_U, "y": A_V * v + B_V,
                "bbox_x": A_U * u + B_U, "bbox_y": A_V * v + B_V}
               for u, v in ((3, 4), (20, 9), (11, 22))]
    fit = report.fit_registration(samples, None, W, H, 96.0)
    assert fit["status"] == "value"
    assert fit["px_per_ft_u"] == pytest.approx(A_U)
    assert fit["margin_ft"] is None
    assert fit["mapping"]["b_v"] == pytest.approx(B_V)


# ======================================================================
# end to end: a probe directory -> the report and the JSON
# ======================================================================

def _write_capture(root, variant, image, colour_map, bbox_map, crop_mode,
                   rendered_uv, extra=None):
    out = root / variant / "color_id_buffer"
    out.mkdir(parents=True)
    tiff = _save(image, out / "V_1_anno.tiff")
    sidecar = {
        "registration": {"frame_px": [W, H], "rendered_uv": rendered_uv,
                         "frame_snapped_uv": [0.0, 0.0, 40.0, 30.0],
                         "achieved_fpp_ft": 1.0 / 12.0, "crop_mode": crop_mode},
        "resolution": {"view_scale": 96.0, "actual_w": W, "actual_h": H,
                       "dim_check": "pass"},
        "color_assignment_map": {str(k): list(v) for k, v in colour_map.items()},
        "annotation_bbox_map": bbox_map,
        "annotation_bbox_status": {"status": "value"},
        "model_suppression_mode": "external",
        "capture_faults": [],
        "tiff_path": tiff,
    }
    sidecar.update(extra or {})
    path = out / "V_1_anno.json"
    path.write_text(json.dumps(sidecar))
    return str(path), tiff


def _probe_dir(tmp_path, with_tiffs=True):
    root = tmp_path / "anno_pass_variants_probe"
    root.mkdir()
    colour = {7: GRID}
    bbox = {"7": {"bbox_uv": {"state": "value",
                              "value": [[4.0, 9.9], [25.0, 9.9], [25.0, 10.1],
                                        [4.0, 10.1]]},
                  "category": "Grids", "membership_basis": "datum_category"}}
    img = _canvas()
    _outline(img, _x(CROP_UV[0]), _y(CROP_UV[3]), _x(CROP_UV[2]), _y(CROP_UV[1]))
    img[int(_y(10)) - 1:int(_y(10)) + 1, int(_x(4)):int(_x(25))] = GRID
    img[int(_y(8)):int(_y(6)), int(_x(5)):int(_x(7))] = FID_A
    img[int(_y(21)):int(_y(19)), int(_x(26)):int(_x(29))] = FID_B
    variants = []
    if with_tiffs:
        side, tiff = _write_capture(root, "v8_no_crop_fiducials", img, colour, bbox,
                                    "untouched", None,
                                    {"probe_crop_boundary": {"present": True}})
    else:
        side, tiff = "/nowhere/V_1_anno.json", "/nowhere/V_1_anno.tiff"
    variants.append({
        "variant": "v8_no_crop_fiducials", "skipped": False, "conclusion": "RAN",
        "measurement": {"measured": True, "unmet": []},
        "annotation_pass": {"sidecar_path": side, "tiff_path": tiff,
                            "success": True, "capture_faults": []},
        "pre_state": {"fiducials": {"painted": _FIDUCIALS, "painted_count": 2}},
    })
    combined = {
        "probe": {"name": "stage_a_anno_pass_variants", "version": "2026-09-22.2"},
        "inputs": {"view_id": 1, "view_name": "V", "view_scale": 96.0},
        "model_pass": {"success": True,
                       "geometry": {"achieved_fpp_ft": 1.0 / 12.0}},
        "authored_crop": {
            "crop_box_active": {"state": "value", "value": True},
            "crop_box_uv": {"state": "value", "value": list(CROP_UV)},
            "shape": {"state": "value", "value": dict(_RECT_SHAPE, loop_count=1,
                                                      edge_count=4,
                                                      oblique_edge_count=0)}},
        "crop_relationships": {
            "authored_crop_active": True, "authored_crop_uv": list(CROP_UV),
            "v0_widens_crop_by_ft": {"left": 2, "right": 8, "top": 6, "bottom": 3},
            "model_pass_crop_a_minus_authored_ft": {"left": 0.01, "right": 0.02,
                                                    "top": 0.0, "bottom": 0.0},
            "model_pass_activates_a_crop": False},
        "suppression_cost": {"suppression_ms": 120.0, "element_override_count": 50,
                             "model_pass_total_ms": 900.0,
                             "ratio_to_model_pass_total": 0.1333,
                             "by_mechanism_ms": {"element_overrides_ms": 90.0,
                                                 "category_override_reads_ms": 20.0},
                             "api_call_counts": {"element_override_writes": 50}},
        "fiducial_choice": {"state": "value", "pair": _FIDUCIALS,
                            "separation_u_ft": 21.5, "separation_v_ft": 13.0,
                            "kept_count": 2, "candidate_count": 3,
                            "reference_source": "authored_crop"},
        "variants": variants,
    }
    (root / "V_1.anno_pass_variants.json").write_text(json.dumps(combined))
    return root


def test_end_to_end_the_report_carries_F1_F2_and_their_agreement(tmp_path):
    root = _probe_dir(tmp_path)
    records = []
    text = report.build_report([str(root)], overlay_enabled=False,
                               json_records=records)
    for heading in ("### 7. F1", "### 8. F2", "### 9. F1 vs F2",
                    "### 10. Datum extents", "### 11. Model-ink residue",
                    "white-suppression cost", "V0 widens it to frame B",
                    "by mechanism (ms): element_overrides 90",
                    "API calls: element_override_writes 50"):
        assert heading in text, heading
    [record] = records
    assert record["crop_boundary"]["status"] == "value"
    assert record["crop_boundary"]["must_be_subtracted"] is True
    assert record["fiducials"]["status"] == "value"
    agreement = record["mapping_agreement"]["F1_vs_F2"]
    assert agreement["status"] == "value"
    # Both were drawn on the same map, so they agree to the line-centre rounding.
    assert agreement["worst_corner_px"] < 1.5


def test_end_to_end_the_untouched_capture_is_measured_against_the_authored_crop(
        tmp_path):
    root = _probe_dir(tmp_path)
    [run] = report.discover_runs(root)
    [capture] = run["captures"]
    analysis = report.analyze_capture(capture["sidecar"], capture["tiff"],
                                      context=report.capture_context(run, capture))
    boundary = analysis["measurements"]["crop_boundary"]
    assert boundary["lattice_px_per_ft"] == pytest.approx(12.0)
    assert boundary["probe_turned_it_on"] is True
    # The boundary and fiducials are NOT residue; the datum is palette.
    assert analysis["measurements"]["model_ink_residue"][
        "offpalette_outside_boundary"] == 0


def test_a_view_with_no_analysable_capture_prints_no_empty_tables(tmp_path):
    """A heading over zero rows reads as 'measured, nothing found'."""
    root = _probe_dir(tmp_path, with_tiffs=False)
    text = report.build_report([str(root)], overlay_enabled=False)
    assert "No capture of this view could be analysed" in text
    assert "### 1. Image size" not in text
    assert "could not locate the sidecar or the TIFF" in text


def test_a_boundary_on_the_image_edge_is_flagged_half_clipped(tmp_path):
    """A model capture renders exactly its crop, so the boundary sits on the
    border with half the stroke clipped. The fit must say so rather than
    present a biased centre as whole."""
    img = _canvas()
    img[0, :] = BLACK
    img[H - 1, :] = BLACK
    img[:, 0] = BLACK
    img[:, W - 1] = BLACK
    path = _save(img, tmp_path / "edge.tiff")
    boundary = report.recover_crop_boundary(
        report.scan_axis_lines(path, []), None, CROP_UV)
    assert boundary["status"] == "value"
    assert boundary["border_clipped_band_count"] == 4
    assert "half-clipped" in boundary["border_clipped_note"]
    inside = report.recover_crop_boundary(
        report.scan_axis_lines(_crop_image(tmp_path), []), _RECT_SHAPE, CROP_UV)
    assert inside["border_clipped_band_count"] == 0


def _plan_like_image(tmp_path):
    """The round-2 Plan_CropActive failure, in miniature.

    The crop's right edge is crossed every 20 px by painted annotation (a
    palette colour), which cuts it into runs too short to qualify; an interior
    black line runs PAST the top and bottom edges. Without gap bridging and
    span-end checks the detector closed the rectangle on the interior line
    (round 2: u/v isotropy 0.49, datum extents off by ~300 ft).
    """
    img = _canvas()
    _outline(img, _x(CROP_UV[0]), _y(CROP_UV[3]), _x(CROP_UV[2]), _y(CROP_UV[1]))
    x_right = int(_x(CROP_UV[2]))
    for y in range(int(_y(CROP_UV[3])) + 5, int(_y(CROP_UV[1])) - 5, 20):
        img[y:y + 3, x_right - 4:x_right + 4] = GRID
    img[10:350, 250] = BLACK
    return _save(img, tmp_path / "plan_like.tiff")


def test_a_crossed_edge_is_bridged_and_an_overshooting_line_is_refused(tmp_path):
    path = _plan_like_image(tmp_path)
    boundary = report.recover_crop_boundary(
        report.scan_axis_lines(path, [GRID]), _RECT_SHAPE, CROP_UV)
    assert boundary["status"] == "value", boundary
    assert boundary["bands"]["right"]["centre"] == pytest.approx(_x(CROP_UV[2]), abs=0.6)
    assert boundary["isotropy_u_over_v"] == pytest.approx(1.0, rel=1e-3)
    assert boundary["px_per_ft_u"] == pytest.approx(A_U, rel=1e-3)


def test_without_bridging_the_crossed_edge_would_not_qualify(tmp_path):
    """The control for the test above: the crossings really do break the edge
    into pieces shorter than the qualifying run, so the bridging is what
    rescues it rather than the fixture being easy."""
    path = _plan_like_image(tmp_path)
    img = np.asarray(Image.open(path).convert("RGB"))
    column = np.all(img[:, int(_x(CROP_UV[2]))] == 0, axis=-1)
    assert report._row_longest_run(column, 0)[0] < report.BOUNDARY_MIN_RUN_FRACTION * H
    assert report._row_longest_run(column, 8)[0] >= report.BOUNDARY_MIN_RUN_FRACTION * H


def test_a_blend_fringe_is_not_a_boundary_candidate(tmp_path):
    """A painted grid's anti-aliased fringe is off-palette, straight and long,
    but coloured. Only grey (or a known crop-element colour) is a candidate."""
    img = _canvas()
    img[100, 20:460] = (253, 128, 128)      # GRID blended toward white
    img[20:340, 100] = (253, 128, 128)
    path = _save(img, tmp_path / "fringe.tiff")
    scan = report.scan_axis_lines(path, [GRID])
    assert max(r[0] for r in scan["rows"]) == 0


def test_a_crop_element_painted_in_a_palette_colour_is_still_found(tmp_path):
    """The model pass paints every model member, the crop-region element
    included, so its boundary can be a PALETTE colour -- recoverable only when
    the caller names that colour."""
    img = _canvas()
    _outline(img, _x(CROP_UV[0]), _y(CROP_UV[3]), _x(CROP_UV[2]), _y(CROP_UV[1]),
             colour=GRID)
    path = _save(img, tmp_path / "painted.tiff")
    hidden = report.recover_crop_boundary(
        report.scan_axis_lines(path, [GRID]), _RECT_SHAPE, CROP_UV)
    assert hidden["status"] == "not_found"
    found = report.recover_crop_boundary(
        report.scan_axis_lines(path, [GRID], boundary_rgbs=[GRID]), _RECT_SHAPE, CROP_UV)
    assert found["status"] == "value"


def test_a_rectangle_is_never_closed_on_a_line_that_runs_past_the_corners(tmp_path):
    """The true right edge is GONE; an interior line runs past top and bottom.
    Coverage alone accepts it and reports a rectangle at the wrong scale (the
    round-2 plan's 0.49 isotropy). Requiring both span ends at the corners
    refuses, which is the honest answer."""
    img = _canvas()
    x0, x1 = int(_x(CROP_UV[0])), int(_x(CROP_UV[2]))
    y0, y1 = int(_y(CROP_UV[3])), int(_y(CROP_UV[1]))
    img[y0 - 1:y0 + 1, x0:x1] = BLACK
    img[y1 - 1:y1 + 1, x0:x1] = BLACK
    img[y0:y1, x0 - 1:x0 + 1] = BLACK
    img[10:350, 250] = BLACK
    path = _save(img, tmp_path / "no_right_edge.tiff")
    boundary = report.recover_crop_boundary(
        report.scan_axis_lines(path, []), _RECT_SHAPE, CROP_UV)
    assert boundary["status"] == "not_found", boundary.get("bands")


def test_a_crossed_HORIZONTAL_edge_is_bridged_too(tmp_path):
    """Rows and columns are scanned by different code; each needs its own
    known-positive."""
    img = _canvas()
    _outline(img, _x(CROP_UV[0]), _y(CROP_UV[3]), _x(CROP_UV[2]), _y(CROP_UV[1]))
    y_bottom = int(_y(CROP_UV[1]))
    for x in range(int(_x(CROP_UV[0])) + 5, int(_x(CROP_UV[2])) - 5, 20):
        img[y_bottom - 4:y_bottom + 4, x:x + 3] = GRID
    path = _save(img, tmp_path / "crossed_bottom.tiff")
    boundary = report.recover_crop_boundary(
        report.scan_axis_lines(path, [GRID]), _RECT_SHAPE, CROP_UV)
    assert boundary["status"] == "value", boundary
    assert boundary["bands"]["bottom"]["centre"] == pytest.approx(y_bottom, abs=0.6)


def test_the_capture_context_carries_the_crop_element_ids(tmp_path):
    """Wiring, not arithmetic: the ids the probe found must reach the scan, or
    a palette-painted boundary stays invisible (round 2's elevation model
    capture)."""
    run = {"combined": {"crop_region_elements": {"crop_element_ids": [42]}}}
    assert report.capture_context(run, {})["crop_element_ids"] == [42]
    assert report.capture_context({"combined": {}}, {})["crop_element_ids"] == []


# ======================================================================
# F3 (round 3) -- registration marks, in BOTH captures
# ======================================================================
#
# The layout is the PROBE's own registration_mark_segments, not a copy: the
# analyzer measures what the probe would draw, so a change to either side that
# breaks the other turns these red (CLAUDE.md, defect class 1).

from tests.dynamo import probe_stage_a_anno_pass_variants as probe  # noqa: E402
import vop_interwoven.stage_a_registration as registration  # noqa: E402

MARK = registration.MARK_COLOUR
# The model capture's lattice: a DIFFERENT scale and origin from the
# annotation drawing, so "the two maps agree" cannot pass by accident.
M_U, M_V = 18.0, -18.0
MW, MH = 700, 520
MODEL_BOUNDS = (-1.0, -2.0, -1.0 + MW / M_U, -2.0 + MH / abs(M_V))


def _mark_layout():
    layout = probe.registration_mark_segments(CROP_UV, 1.0 / A_U)
    assert layout["state"] == "value", layout
    return [dict(seg, id=9000 + i) for i, seg in enumerate(layout["segments"])]


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


def _anno_colours(marks):
    return {m["id"]: (200, 8 * (i + 1), 16) for i, m in enumerate(marks)}


def _mark_images(tmp_path, marks, drop=(), cross=False):
    colours = _anno_colours(marks)
    anno = _canvas()
    for m in marks:
        if m["key"] not in drop:
            _draw_tick(anno, m, colours[m["id"]], _x, _y)
    model = np.full((MH, MW, 3), 255, dtype=np.uint8)
    mx = lambda u: M_U * (u - MODEL_BOUNDS[0])            # noqa: E731
    my = lambda v: M_V * (v - MODEL_BOUNDS[3])            # noqa: E731
    for m in marks:
        if m["key"] not in drop:
            _draw_tick(model, m, MARK, mx, my)
    if cross:
        # Other ink crossing every horizontal tick: each splits in two.
        for m in marks:
            if m["orientation"] == "horizontal":
                x_mid = int(round(mx(sum(m["span_uv"]) / 2.0)))
                model[:, x_mid:x_mid + 3] = (0, 0, 0)
    anno_path = _save(anno, tmp_path / "anno.tiff")
    model_path = _save(model, tmp_path / "model.tiff")
    sidecar = tmp_path / "model.json"
    sidecar.write_text(json.dumps({"bounds_xy": list(MODEL_BOUNDS),
                                   "model_lines_visible": True}))
    return anno_path, model_path, str(sidecar), colours


def _context(model_path, sidecar):
    return {"model_tiff": model_path, "model_sidecar": sidecar,
            "registration_mark_colour": list(MARK)}


def test_f3_recovers_the_annotation_mapping_from_the_tick_centre_lines(tmp_path):
    marks = _mark_layout()
    anno, model, sidecar, colours = _mark_images(tmp_path, marks)
    f3 = report.registration_mark_fit(anno, marks, colour_by_id=colours)
    assert f3["status"] == "value", f3
    assert f3["found_count"] == 12
    assert f3["points"] == {"u": 6, "v": 6}
    assert f3["px_per_ft_u"] == pytest.approx(A_U, abs=1e-6)
    assert f3["px_per_ft_v"] == pytest.approx(abs(A_V), abs=1e-6)
    assert f3["mapping"]["b_u"] == pytest.approx(B_U, abs=0.05)
    assert f3["mapping"]["b_v"] == pytest.approx(B_V, abs=0.05)


def test_f3_in_the_model_capture_meets_the_recorded_lattice(tmp_path):
    """The one place the method meets a KNOWN answer: the model lattice."""
    marks = _mark_layout()
    anno, model, sidecar, colours = _mark_images(tmp_path, marks)
    fit = report._model_registration_marks(marks, _context(model, sidecar),
                                           list(CROP_UV))
    assert fit["status"] == "value", fit
    assert fit["found_count"] == 12
    assert fit["px_per_ft_u"] == pytest.approx(M_U, abs=0.01)
    assert fit["vs_lattice"]["status"] == "value"
    assert fit["vs_lattice"]["worst_corner_px"] < 0.6


def test_f3_composes_annotation_pixels_onto_the_model_lattice(tmp_path):
    marks = _mark_layout()
    anno, model, sidecar, colours = _mark_images(tmp_path, marks)
    anno_fit = report.registration_mark_fit(anno, marks, colour_by_id=colours)
    model_fit = report._model_registration_marks(marks, _context(model, sidecar),
                                                 list(CROP_UV))
    transform = report.compose_pixel_transform(anno_fit["mapping"],
                                               model_fit["mapping"])
    assert transform["scale_x"] == pytest.approx(M_U / A_U, rel=1e-4)
    # A point of known UV lands where the model lattice puts it.
    u, v = 10.0, 12.0
    x_model = transform["scale_x"] * _x(u) + transform["offset_x"]
    y_model = transform["scale_y"] * _y(v) + transform["offset_y"]
    assert x_model == pytest.approx(M_U * (u - MODEL_BOUNDS[0]), abs=0.6)
    assert y_model == pytest.approx(M_V * (v - MODEL_BOUNDS[3]), abs=0.6)


def test_f3_crossed_ticks_are_merged_not_miscounted(tmp_path):
    marks = _mark_layout()
    anno, model, sidecar, colours = _mark_images(tmp_path, marks, cross=True)
    fit = report._model_registration_marks(marks, _context(model, sidecar),
                                           list(CROP_UV))
    assert fit["status"] == "value", fit
    # Six horizontal ticks, each split in two by the crossing line.
    assert fit["components"]["components"] == 18
    assert fit["components"]["merged_into_one_tick"] == 6
    assert fit["components"]["unassigned_components"] == []
    assert fit["vs_lattice"]["worst_corner_px"] < 0.6
    # MERGED, not dropped: each crossed tick still spans its full length.
    # Keeping only the first half would leave the centre right and the tick
    # short, which only the extent shows.
    clean_dir = tmp_path / "clean"
    clean_dir.mkdir()
    _a, clean_model, clean_side, _c = _mark_images(clean_dir, marks)
    clean = report._model_registration_marks(marks, _context(clean_model, clean_side),
                                             list(CROP_UV))
    spans = dict((t["key"], t["end_px"]) for t in clean["ticks"])
    for tick in fit["ticks"]:
        if tick["orientation"] == "horizontal":
            assert tick["end_px"] == spans[tick["key"]], tick["key"]


def test_f3_with_one_level_left_on_an_axis_is_unavailable(tmp_path):
    """Left AND mid vertical ticks missing: u has one level left, which fixes no
    scale. Refused, not fitted through one level."""
    marks = _mark_layout()
    gone = ("left_bottom_v", "left_top_v", "mid_bottom_v", "mid_top_v")
    anno, model, sidecar, colours = _mark_images(tmp_path, marks, drop=gone)
    f3 = report.registration_mark_fit(anno, marks, colour_by_id=colours)
    assert f3["status"] == "unavailable"
    assert {m["key"] for m in f3["missing"]} == set(gone)


def test_a_misplaced_mid_tick_shows_up_as_a_residual(tmp_path):
    """THE POINT OF THE THIRD LEVEL: a level that disagrees with the other two
    now moves the residual. Round 3's two-level fit would read 0.00 here."""
    marks = _mark_layout()
    shifted = [dict(m, level_uv=m["level_uv"] + 0.5)
               if m["key"] in ("mid_bottom_v", "mid_top_v") else m for m in marks]
    anno, model, sidecar, colours = _mark_images(tmp_path, shifted)
    # Measured against where the ticks were SUPPOSED to be.
    f3 = report.registration_mark_fit(anno, marks, colour_by_id=colours)
    assert f3["status"] == "value"
    assert f3["residual_max_px"]["u"] > 2.0
    assert f3["residual_max_px"]["v"] < 0.5


def test_one_missing_tick_still_fits_and_names_the_gap(tmp_path):
    """The CONTROL for the refusal above: one tick short is three points on an
    axis, still two levels."""
    marks = _mark_layout()
    anno, model, sidecar, colours = _mark_images(tmp_path, marks,
                                                 drop=("left_top_v",))
    f3 = report.registration_mark_fit(anno, marks, colour_by_id=colours)
    assert f3["status"] == "value"
    assert f3["points"]["u"] == 5
    assert [m["key"] for m in f3["missing"]] == ["left_top_v"]


def test_end_to_end_v9_reports_section_12_and_the_json_carries_the_transform(
        tmp_path):
    marks = _mark_layout()
    root = tmp_path / "anno_pass_variants_probe"
    root.mkdir()
    anno_img_dir = tmp_path / "src"
    anno_img_dir.mkdir()
    anno, model, model_sidecar, colours = _mark_images(anno_img_dir, marks)
    anno_pixels = np.array(Image.open(anno).convert("RGB"))
    # The crop boundary too, so F1 is ALSO available and the datum map is a
    # choice between rulers rather than the only one present.
    _outline(anno_pixels, _x(CROP_UV[0]), _y(CROP_UV[3]), _x(CROP_UV[2]),
             _y(CROP_UV[1]))
    side, tiff = _write_capture(root, "v9_registration_marks", anno_pixels,
                                colours, {}, "untouched", None)
    own_dir = root / "v9_registration_marks" / "model" / "color_id_buffer"
    own_dir.mkdir(parents=True)
    own_tiff = own_dir / "V_1.tiff"
    own_tiff.write_bytes(open(model, "rb").read())
    own_side = own_dir / "V_1.json"
    own_side.write_text(open(model_sidecar).read())
    combined = {
        "probe": {"name": "stage_a_anno_pass_variants", "version": "2026-09-23.1"},
        "inputs": {"view_id": 1, "view_name": "V", "view_scale": 96.0},
        "model_pass": {"success": True, "geometry": {"achieved_fpp_ft": 1.0 / 12.0}},
        "authored_crop": {"crop_box_active": {"state": "value", "value": True},
                          "crop_box_uv": {"state": "value", "value": list(CROP_UV)}},
        "crop_relationships": {"authored_crop_active": True,
                               "authored_crop_uv": list(CROP_UV)},
        "variants": [{
            "variant": "v9_registration_marks", "skipped": False, "conclusion": "RAN",
            "measurement": {"measured": True, "unmet": []},
            "annotation_pass": {"sidecar_path": side, "tiff_path": tiff,
                                "success": True, "capture_faults": []},
            "own_model_pass": {"sidecar_path": str(own_side),
                               "tiff_path": str(own_tiff)},
            "pre_state": {"registration_marks": {
                "created": marks, "colour": list(MARK)}},
            "restore": {"mode": "transaction_group_rollback", "rollback_ms": 12.0,
                        "element_overrides_after_rollback": {
                            "status": "restored", "elapsed_ms": 3.0}},
            "transaction_group": {"pre_state_commit_ms": 5.0},
        }],
    }
    (root / "V_1.anno_pass_variants.json").write_text(json.dumps(combined))
    records = []
    text = report.build_report([str(root)], overlay_enabled=False,
                               json_records=records)
    assert "### 12. F3 -- registration marks" in text
    assert "restore = group rollback, 12 ms" in text
    [record] = records
    marks_json = record["registration_marks"]
    assert marks_json["status"] == "value"
    assert marks_json["model"]["vs_lattice"]["worst_corner_px"] < 0.6
    assert marks_json["annotation_to_model_px"]["via_model_marks"][
        "scale_x"] == pytest.approx(M_U / A_U, rel=1e-4)
    assert len(marks_json["mark_pixel_rects"]) == 12
    assert "F3_vs_bbox_fit" in record["mapping_agreement"]
    # The marks, now the best ruler present, are the map the datums use.
    [run] = report.discover_runs(root)
    [capture] = run["captures"]
    analysis = report.analyze_capture(capture["sidecar"], capture["tiff"],
                                      context=report.capture_context(run, capture))
    assert analysis["measurements"]["crop_boundary"]["status"] == "value"
    assert analysis["measurements"]["datum_extents"]["mapping_used"] == "F3"


def test_a_capture_without_marks_reports_f3_not_applicable(tmp_path):
    """The CONTROL for the end-to-end test: no marks, no section 12."""
    root = _probe_dir(tmp_path)
    records = []
    text = report.build_report([str(root)], overlay_enabled=False,
                               json_records=records)
    assert "### 12." not in text
    assert records[0]["registration_marks"] == {"status": "not_applicable"}
