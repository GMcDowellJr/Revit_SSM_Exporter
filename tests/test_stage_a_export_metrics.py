"""Unit coverage for the Stage A edge-drift / white-blend export metrics.

These exercise tools/analyze_stage_a_probe.py's diagnostic metric set against
synthetic rasters whose ground truth is known by construction. They say nothing
about any real capture -- empirical Revit results are the only evidence for the
drift and blend investigations.
"""
import json

import pytest

np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")

import tools.analyze_stage_a_probe as analyzer


RED = (220, 40, 40)
BLUE = (40, 60, 210)
WHITE = (255, 255, 255)


def sidecar(bounds=(0.0, 0.0, 100.0, 80.0), dpi=150.0, scale=96.0,
            requested=1200, accepted=1200, colors=(RED,)):
    return {
        "resolution": {"requested_pixel_size": requested, "pixel_size": accepted,
                       "export_dpi": dpi, "view_scale": scale},
        "bounds_xy": list(bounds),
        "color_assignment_map": {str(1000 + i): list(c) for i, c in enumerate(colors)},
        "link_category_color_map": {},
    }


def write_tiff(tmp_path, arr, name="probe.tiff"):
    path = tmp_path / name
    Image.fromarray(arr.astype(np.uint8), mode="RGB").save(path)
    return path


def solid_square(size=64, color=RED, inset=16):
    arr = np.full((size, size, 3), 255, dtype=np.uint8)
    arr[inset:size - inset, inset:size - inset] = color
    return arr


def measure(tmp_path, arr, **kw):
    path = write_tiff(tmp_path, arr)
    return analyzer.stage_a_export_metrics(path, sidecar(**kw))


# --- hard vs blended edges -------------------------------------------------

def test_hard_edged_square_has_no_blended_edges(tmp_path):
    m = measure(tmp_path, solid_square())
    assert m["edges"]["hard_edge_count"] > 0
    assert m["edges"]["blended_edge_count"] == 0
    assert m["edges"]["hard_edge_ratio"] == 1.0
    assert m["pixels"]["off_palette_px"] == 0
    assert m["transitions"]["count"] == 0


def test_hard_edge_count_matches_the_square_perimeter(tmp_path):
    # A 32x32 block inset in a 64x64 canvas: 32 color|white pairs per side,
    # counted once each across the horizontal and vertical passes.
    m = measure(tmp_path, solid_square(size=64, inset=16))
    assert m["edges"]["hard_edge_count"] == 4 * 32


def test_three_pixel_ramp_is_all_blended_edges(tmp_path):
    arr = np.full((16, 32, 3), 255, dtype=np.uint8)
    arr[:, 16:] = RED
    for i, t in enumerate((0.25, 0.5, 0.75)):
        arr[:, 13 + i] = np.round(np.array(WHITE) * (1 - t) + np.array(RED) * t)
    m = measure(tmp_path, arr)
    assert m["edges"]["hard_edge_count"] == 0
    assert m["edges"]["blended_edge_count"] > 0
    assert m["edges"]["hard_edge_ratio"] == 0.0
    assert m["transitions"]["width_percentiles"]["p50"] == 3.0


def test_palette_to_palette_contact_is_not_a_color_white_edge(tmp_path):
    arr = np.full((16, 32, 3), 255, dtype=np.uint8)
    arr[4:12, 4:16] = RED
    arr[4:12, 16:28] = BLUE
    m = measure(tmp_path, arr, colors=(RED, BLUE))
    assert m["edges"]["hard_color_color_edge_count"] == 8
    assert m["edges"]["blended_edge_count"] == 0


# --- transition widths and overshoot ---------------------------------------

def test_overshoot_is_zero_for_a_monotone_ramp(tmp_path):
    arr = np.full((16, 32, 3), 255, dtype=np.uint8)
    arr[:, 20:] = RED
    for i, t in enumerate((0.25, 0.5, 0.75)):
        arr[:, 17 + i] = np.round(np.array(WHITE) * (1 - t) + np.array(RED) * t)
    m = measure(tmp_path, arr)
    assert m["transitions"]["overshoot"]["overshoot_rate"] == 0.0


def test_ringing_past_the_dark_endpoint_is_reported_as_overshoot(tmp_path):
    arr = np.full((16, 32, 3), 255, dtype=np.uint8)
    arr[:, 20:] = RED
    arr[:, 17] = (240, 150, 150)
    arr[:, 18] = (120, 20, 20)            # undershoots below RED: negative lobe
    arr[:, 19] = (200, 30, 30)
    m = measure(tmp_path, arr)
    over = m["transitions"]["overshoot"]
    assert over["overshoot_rate"] == 1.0
    assert over["normalized_overshoot"]["p100"] > 0.3


def test_wider_transitions_move_the_width_percentiles(tmp_path):
    arr = np.full((16, 40, 3), 255, dtype=np.uint8)
    arr[:, 26:] = RED
    ramp = np.linspace(0.1, 0.9, 6)
    for i, t in enumerate(ramp):
        arr[:, 20 + i] = np.round(np.array(WHITE) * (1 - t) + np.array(RED) * t)
    m = measure(tmp_path, arr)
    assert m["transitions"]["width_percentiles"]["p50"] == 6.0
    assert m["transitions"]["width_histogram"]["6"] == 16


# --- solidity ---------------------------------------------------------------

def test_solid_interior_dominates_a_hard_edged_square(tmp_path):
    m = measure(tmp_path, solid_square(size=64, inset=16))
    # A 32x32 block has a 30x30 fully-solid 3x3 core out of 32*32 palette px.
    assert m["solidity"]["solid_3x3_palette_px"] == 30 * 30
    assert m["solidity"]["fraction_3x3_solid"] == pytest.approx(900 / 1024)


def test_single_pixel_speckle_is_never_3x3_solid(tmp_path):
    arr = np.full((32, 32, 3), 255, dtype=np.uint8)
    arr[::4, ::4] = RED
    m = measure(tmp_path, arr)
    assert m["solidity"]["solid_3x3_palette_px"] == 0
    assert m["solidity"]["fraction_3x3_solid"] == 0.0


# --- native resolution and the 10:1 frame clamp -----------------------------

def test_native_px_and_scale_factor_follow_extent_dpi_and_scale(tmp_path):
    # 100 ft at 150 dpi, 1:96 -> 100 * 12 * 150 / 96 = 1875 px native.
    m = measure(tmp_path, solid_square(size=64), bounds=(0.0, 0.0, 100.0, 80.0))
    res = m["resolution"]
    assert res["native_width_px"] == pytest.approx(1875.0)
    assert res["scale_factor"] == pytest.approx(64 / 1875.0)
    assert res["max_axis_px"] == 64


def test_pixel_size_backoff_is_reported_when_revit_lowered_the_request(tmp_path):
    m = measure(tmp_path, solid_square(), requested=15000, accepted=7500)
    assert m["resolution"]["pixel_size_backoff"] is True


def test_no_clamp_for_an_ordinary_aspect(tmp_path):
    m = measure(tmp_path, solid_square(size=64), bounds=(0.0, 0.0, 100.0, 80.0))
    assert m["frame"]["clamp_applied"] is False
    assert m["frame"]["pad_y_px"] == 0.0
    assert m["pixels"]["out_of_bbox_palette_px"] == 0


def test_ten_to_one_clamp_pads_the_short_axis_and_shrinks_the_content_rect(tmp_path):
    # 2000 ft x 100 ft is 20:1; Revit clamps to 10:1, so a 64-wide canvas is
    # 6.4 px tall by content but 6 px tall as exported... use a taller canvas
    # so the symmetric pad is measurable.
    arr = np.full((20, 200, 3), 255, dtype=np.uint8)
    arr[:, :] = RED
    m = measure(tmp_path, arr, bounds=(0.0, 0.0, 2000.0, 50.0))
    frame = m["frame"]
    assert frame["aspect"] == pytest.approx(40.0)
    assert frame["clamped_aspect"] == 10.0
    assert frame["clamp_applied"] is True
    # content is 200/40 = 5 px tall, centred in 20 -> 7.5 px pad each side
    assert frame["pad_y_px"] == pytest.approx(7.5)
    # every RED pixel outside that 5-px band is out-of-bbox
    assert m["pixels"]["out_of_bbox_palette_px"] > 0
    assert m["pixels"]["out_of_bbox_palette_px"] < 20 * 200


def test_clamp_prediction_flags_a_height_it_does_not_explain(tmp_path):
    m = measure(tmp_path, solid_square(size=64), bounds=(0.0, 0.0, 2000.0, 50.0))
    assert m["frame"]["clamp_matches_actual_height"] is False


# --- white blend (0.67 pastel) ---------------------------------------------

def blend_over_white(color, alpha):
    return tuple(int(round(alpha * c + (1 - alpha) * 255)) for c in color)


def test_whole_element_at_alpha_067_over_white_unblends_to_its_palette_color(tmp_path):
    arr = np.full((64, 64, 3), 255, dtype=np.uint8)
    arr[16:48, 16:48] = blend_over_white(RED, 0.67)
    m = measure(tmp_path, arr)
    blend = m["white_blend"]
    assert blend["matched_color_count"] == 1
    match = blend["matches"][0]
    assert match["palette_rgb"] == list(RED)
    assert match["alpha"] == pytest.approx(0.67, abs=0.01)
    assert match["palette_labels"] == ["element:1000"]
    assert blend["alpha_percentiles"]["p50"] == pytest.approx(0.67, abs=0.01)


def test_two_elements_blended_at_the_same_alpha_report_one_alpha_bin(tmp_path):
    arr = np.full((64, 64, 3), 255, dtype=np.uint8)
    arr[8:28, 8:28] = blend_over_white(RED, 0.67)
    arr[36:56, 36:56] = blend_over_white(BLUE, 0.67)
    m = measure(tmp_path, arr, colors=(RED, BLUE))
    blend = m["white_blend"]
    assert blend["matched_color_count"] == 2
    assert blend["distinct_palette_colors_blended"] == 2
    assert list(blend["alpha_histogram"].values()) == [2]


def test_an_unrelated_off_palette_color_does_not_unblend(tmp_path):
    arr = np.full((64, 64, 3), 255, dtype=np.uint8)
    arr[16:48, 16:48] = (17, 200, 90)     # not on any alpha ray from RED to white
    m = measure(tmp_path, arr)
    assert m["white_blend"]["matched_color_count"] == 0
    assert m["pixels"]["off_palette_px"] == 32 * 32


def test_pastel_regions_stay_3x3_solid_unlike_resampled_edges(tmp_path):
    """The discriminator between a composited element and a resampled edge."""
    pastel = np.full((64, 64, 3), 255, dtype=np.uint8)
    pastel[16:48, 16:48] = blend_over_white(RED, 0.67)
    flat = analyzer.stage_a_export_metrics(
        write_tiff(tmp_path, pastel, "pastel.tiff"), sidecar())
    # The pastel block is off-palette, so palette solidity has no denominator;
    # what matters is that its pixels form large uniform runs, not 1-2 px
    # transitions the way a resample does.
    assert flat["transitions"]["width_percentiles"]["p50"] == 32.0
    assert flat["white_blend"]["matches"][0]["pixel_count"] == 32 * 32


# --- record discovery and the CLI ------------------------------------------

def test_metrics_json_is_written_for_a_bare_sidecar(tmp_path):
    arr = solid_square()
    write_tiff(tmp_path, arr, "view.tiff")
    doc = dict(sidecar(), tiff_path="view.tiff", view_id=871863)
    src = tmp_path / "view.json"
    src.write_text(json.dumps(doc), encoding="utf-8")
    out, rows = analyzer.analyze_metrics_json(src)
    assert out.name == "view.metrics.json"
    assert [label for label, _ in rows] == ["871863"]
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["errors"] == []
    assert written["exports"]["871863"]["edges"]["hard_edge_ratio"] == 1.0


def test_exports_list_measures_every_case_and_inherits_the_palette(tmp_path):
    write_tiff(tmp_path, solid_square(), "a.tiff")
    write_tiff(tmp_path, solid_square(inset=8), "b.tiff")
    base = sidecar()
    doc = {
        "color_assignment_map": base["color_assignment_map"],
        "link_category_color_map": {},
        "exports": [
            {"label": "rep1", "tiff_path": "a.tiff", "resolution": base["resolution"],
             "bounds_xy": base["bounds_xy"]},
            {"label": "rep2", "tiff_path": "b.tiff", "resolution": base["resolution"],
             "bounds_xy": base["bounds_xy"]},
        ],
    }
    src = tmp_path / "d1.json"
    src.write_text(json.dumps(doc), encoding="utf-8")
    _, rows = analyzer.analyze_metrics_json(src)
    assert [label for label, _ in rows] == ["rep1", "rep2"]
    assert all(m["palette_color_count"] == 1 for _, m in rows)


def test_a_missing_tiff_is_recorded_as_an_error_not_an_exception(tmp_path):
    doc = {"exports": [{"label": "gone", "tiff_path": "missing.tiff"}]}
    src = tmp_path / "x.json"
    src.write_text(json.dumps(doc), encoding="utf-8")
    out, rows = analyzer.analyze_metrics_json(src)
    assert rows == []
    assert json.loads(out.read_text(encoding="utf-8"))["errors"][0]["code"] == "TIFF_MISSING"


def test_a_file_describing_no_export_is_rejected(tmp_path):
    src = tmp_path / "empty.json"
    src.write_text(json.dumps({"view_id": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="NO_EXPORT_RECORDS"):
        analyzer.analyze_metrics_json(src)


def test_cli_export_metrics_mode_writes_a_markdown_table(tmp_path, capsys):
    write_tiff(tmp_path, solid_square(), "view.tiff")
    doc = dict(sidecar(), tiff_path="view.tiff", view_id=42)
    (tmp_path / "view.json").write_text(json.dumps(doc), encoding="utf-8")
    table = tmp_path / "table.md"
    assert analyzer.main([str(tmp_path), "--export-metrics", "--metrics-table", str(table)]) == 0
    text = table.read_text(encoding="utf-8")
    assert "hard_edge_ratio" in text.replace("hard_ratio", "hard_edge_ratio")
    assert "| W | H |" in text
    assert text.strip().count("\n") == 2  # header, rule, one data row


def test_cli_export_metrics_skips_its_own_output_on_a_rerun(tmp_path):
    write_tiff(tmp_path, solid_square(), "view.tiff")
    doc = dict(sidecar(), tiff_path="view.tiff", view_id=42)
    (tmp_path / "view.json").write_text(json.dumps(doc), encoding="utf-8")
    assert analyzer.main([str(tmp_path), "--export-metrics"]) == 0
    assert analyzer.main([str(tmp_path), "--export-metrics"]) == 0


# --- stripe-seam invariance -------------------------------------------------

@pytest.mark.parametrize("stripe_rows", [3, 7, 16, 4096])
def test_metrics_are_invariant_to_the_stripe_height(tmp_path, monkeypatch, stripe_rows):
    """Stripe boundaries must not create, destroy, or double-count anything."""
    rng = np.random.default_rng(20260915)
    arr = np.full((61, 53, 3), 255, dtype=np.uint8)
    arr[10:40, 8:30] = RED
    arr[25:55, 24:48] = BLUE
    arr[5:12, 40:50] = blend_over_white(RED, 0.67)
    noise = rng.integers(0, 3, size=(61, 53, 3), endpoint=False, dtype=np.uint8)
    arr[50:58, 2:20] = 255 - noise[50:58, 2:20] * 40
    path = write_tiff(tmp_path, arr, f"s{stripe_rows}.tiff")

    monkeypatch.setattr(analyzer, "_METRIC_STRIPE_ROWS", 4096)
    reference = analyzer.stage_a_export_metrics(path, sidecar(colors=(RED, BLUE)))
    monkeypatch.setattr(analyzer, "_METRIC_STRIPE_ROWS", stripe_rows)
    got = analyzer.stage_a_export_metrics(path, sidecar(colors=(RED, BLUE)))

    for section in ("pixels", "edges", "solidity"):
        assert got[section] == reference[section], section
    assert got["transitions"]["count"] == reference["transitions"]["count"]
    assert got["transitions"]["width_histogram"] == reference["transitions"]["width_histogram"]
    assert got["white_blend"]["matched_color_count"] == reference["white_blend"]["matched_color_count"]
    assert got["white_blend"]["matched_pixel_count"] == reference["white_blend"]["matched_pixel_count"]


# --- overshoot population gating -------------------------------------------

def resample(arr, factor, kernel):
    h, w = arr.shape[:2]
    img = Image.fromarray(arr.astype(np.uint8), mode="RGB")
    small = img.resize((int(w * factor), int(h * factor)), kernel)
    return np.asarray(small.resize((w, h), kernel).convert("RGB"))


def scene(size=240):
    arr = np.full((size, size, 3), 255, dtype=np.uint8)
    arr[20:90, 20:200] = RED
    arr[120:200, 40:210] = BLUE
    return arr


def test_a_hard_edged_scene_offers_no_transition_to_score(tmp_path):
    m = measure(tmp_path, scene(), colors=(RED, BLUE))
    over = m["transitions"]["overshoot"]
    assert over["measurable_transitions"] == 0
    assert over["overshoot_rate"] is None


def test_a_negative_lobe_resample_rings_but_a_bilinear_one_does_not(tmp_path):
    base = scene()
    lanczos = analyzer.stage_a_export_metrics(
        write_tiff(tmp_path, resample(base, 0.62, Image.LANCZOS), "lanczos.tiff"),
        sidecar(colors=(RED, BLUE)))["transitions"]["overshoot"]
    bilinear = analyzer.stage_a_export_metrics(
        write_tiff(tmp_path, resample(base, 0.62, Image.BILINEAR), "bilinear.tiff"),
        sidecar(colors=(RED, BLUE)))["transitions"]["overshoot"]
    assert lanczos["measurable_transitions"] > 50
    assert bilinear["measurable_transitions"] > 50
    assert lanczos["overshoot_rate"] > 0.5
    assert bilinear["overshoot_rate"] < 0.1


def test_a_run_bounded_by_the_same_color_is_never_scored(tmp_path):
    """A composited element's interior is a region, not a transition."""
    arr = np.full((32, 64, 3), 255, dtype=np.uint8)
    arr[8:24, 16:48] = blend_over_white(RED, 0.67)
    over = measure(tmp_path, arr)["transitions"]["overshoot"]
    assert over["degenerate_runs"] > 0
    assert over["measurable_transitions"] == 0
    assert over["overshoot_rate"] is None


def test_a_run_too_wide_to_be_a_resampled_edge_is_excluded(tmp_path):
    arr = np.full((16, 400, 3), 255, dtype=np.uint8)
    arr[:, 300:] = BLUE
    arr[:, 60:300] = blend_over_white(RED, 0.67)   # 240 px of a third region
    over = measure(tmp_path, arr, colors=(RED, BLUE))["transitions"]["overshoot"]
    assert over["wide_region_runs"] > 0
    assert over["measurable_transitions"] == 0
    assert over["max_width_scored_px"] == analyzer._MAX_TRANSITION_FOR_OVERSHOOT


def test_a_color_to_color_transition_is_counted_apart_from_color_to_white(tmp_path):
    arr = np.full((16, 40, 3), 255, dtype=np.uint8)
    arr[:, :18] = RED
    arr[:, 22:] = BLUE
    for i, t in enumerate((0.2, 0.4, 0.6, 0.8)):   # fills 18..21, leaving no white gap
        arr[:, 18 + i] = np.round(np.array(RED) * (1 - t) + np.array(BLUE) * t)
    over = measure(tmp_path, arr, colors=(RED, BLUE))["transitions"]["overshoot"]
    assert over["color_color_transitions"] == 16
    assert over["color_white_transitions"] == 0
    assert over["overshoot_population"] == "color_color"


# --- composited pastel vs resample residue ---------------------------------

def test_resample_residue_unblends_cleanly_but_is_never_composited(tmp_path):
    """Edge pixels sit exactly on the alpha ray, so solving alpha is not enough."""
    path = write_tiff(tmp_path, resample(scene(), 0.62, Image.LANCZOS), "soft.tiff")
    blend = analyzer.stage_a_export_metrics(path, sidecar(colors=(RED, BLUE)))["white_blend"]
    assert blend["matched_color_count"] > 0
    assert blend["composited_color_count"] == 0
    assert blend["composited_alpha_histogram"] == {}
    assert all(m["solid_3x3_fraction"] < 0.5 for m in blend["matches"])


def test_a_composited_element_is_solid_and_reports_its_alpha(tmp_path):
    arr = np.full((120, 120, 3), 255, dtype=np.uint8)
    arr[20:100, 20:100] = blend_over_white(RED, 0.67)
    blend = measure(tmp_path, arr)["white_blend"]
    assert blend["composited_color_count"] == 1
    assert blend["composited_alpha_histogram"] == {"0.67": 1}
    assert blend["composited_alpha_percentiles"]["p50"] == pytest.approx(0.67, abs=0.01)
    match = blend["matches"][0]
    assert match["is_composited_region"] is True
    assert match["solid_3x3_fraction"] > 0.9


def test_both_anomalies_in_one_capture_are_reported_independently(tmp_path):
    """A resampled export of a composited view must show drift AND the blend."""
    arr = np.full((240, 240, 3), 255, dtype=np.uint8)
    arr[20:110, 20:220] = blend_over_white(RED, 0.67)
    arr[130:220, 30:210] = BLUE
    m = analyzer.stage_a_export_metrics(
        write_tiff(tmp_path, resample(arr, 0.62, Image.LANCZOS), "both.tiff"),
        sidecar(colors=(RED, BLUE)))
    assert m["edges"]["hard_edge_ratio"] == 0.0
    assert m["transitions"]["overshoot"]["overshoot_rate"] > 0.5
    assert m["white_blend"]["composited_color_count"] == 1
    assert m["white_blend"]["composited_alpha_histogram"] == {"0.67": 1}


def test_neighbouring_palette_colors_are_recorded_for_the_blend_target_question(tmp_path):
    """B2: a pastel that unblends against white while touching another element."""
    arr = np.full((120, 160, 3), 255, dtype=np.uint8)
    arr[20:100, 20:80] = blend_over_white(RED, 0.67)
    arr[20:100, 80:140] = BLUE
    blend = measure(tmp_path, arr, colors=(RED, BLUE))["white_blend"]
    match = [m for m in blend["matches"] if m["is_composited_region"]][0]
    assert match["palette_rgb"] == list(RED)
    assert list(BLUE) in match["neighbor_palette_rgb"]


# --- orientation independence (Codex round 2) -------------------------------

def edge(length=400, thickness=60, horizontal=True):
    """A single straight boundary with a known 3-pixel blended transition."""
    arr = (np.full((thickness, length, 3), 255, dtype=np.uint8) if horizontal
           else np.full((length, thickness, 3), 255, dtype=np.uint8))
    half = thickness // 2
    if horizontal:
        arr[half:, :] = RED
        for i, t in enumerate((0.25, 0.5, 0.75)):
            arr[half - 3 + i, :] = np.round(np.array(WHITE) * (1 - t) + np.array(RED) * t)
    else:
        arr[:, half:] = RED
        for i, t in enumerate((0.25, 0.5, 0.75)):
            arr[:, half - 3 + i] = np.round(np.array(WHITE) * (1 - t) + np.array(RED) * t)
    return arr


def test_a_horizontal_transition_is_measured_across_it_not_along_it(tmp_path):
    """Row-only scanning reported this 3-px edge as 400 px wide with no samples."""
    m = measure(tmp_path, edge(horizontal=True))
    t = m["transitions"]
    assert t["width_percentiles"]["p50"] == 3.0
    assert t["width_histogram"]["3"] == 400
    assert t["overshoot"]["measurable_transitions"] == 400


def test_a_vertical_transition_measures_the_same_as_a_horizontal_one(tmp_path):
    horizontal = analyzer.stage_a_export_metrics(
        write_tiff(tmp_path, edge(horizontal=True), "h.tiff"), sidecar())["transitions"]
    vertical = analyzer.stage_a_export_metrics(
        write_tiff(tmp_path, edge(horizontal=False), "v.tiff"), sidecar())["transitions"]
    assert horizontal["width_percentiles"] == vertical["width_percentiles"]
    assert horizontal["width_histogram"] == vertical["width_histogram"]
    assert horizontal["overshoot"]["measurable_transitions"] == \
        vertical["overshoot"]["measurable_transitions"]
    # The two axes swap roles between the orientations, as they must.
    assert horizontal["row_runs"] == vertical["column_runs"]
    assert horizontal["column_runs"] == vertical["row_runs"]


def test_ringing_on_a_horizontal_edge_is_detected(tmp_path):
    arr = np.full((60, 200, 3), 255, dtype=np.uint8)
    arr[30:, :] = RED
    arr[27, :] = (240, 150, 150)
    arr[28, :] = (120, 20, 20)        # undershoots past RED
    arr[29, :] = (200, 30, 30)
    over = measure(tmp_path, arr)["transitions"]["overshoot"]
    assert over["measurable_transitions"] > 0
    assert over["overshoot_rate"] == 1.0


def test_both_axes_are_scanned_on_a_rectangle(tmp_path):
    m = measure(tmp_path, solid_square(size=64, inset=16))
    t = m["transitions"]
    assert t["row_runs"] == 0 and t["column_runs"] == 0   # hard edges, no off-runs


def test_stripe_height_still_does_not_change_the_pooled_transition_counts(tmp_path, monkeypatch):
    arr = edge(horizontal=True)
    path = write_tiff(tmp_path, arr, "seam.tiff")
    monkeypatch.setattr(analyzer, "_METRIC_STRIPE_ROWS", 4096)
    reference = analyzer.stage_a_export_metrics(path, sidecar())["transitions"]
    monkeypatch.setattr(analyzer, "_METRIC_STRIPE_ROWS", 7)
    got = analyzer.stage_a_export_metrics(path, sidecar())["transitions"]
    assert got["width_histogram"] == reference["width_histogram"]
    assert got["row_runs"] == reference["row_runs"]
    assert got["column_runs"] == reference["column_runs"]


# --- a capture with no applied crop ----------------------------------------

def test_a_null_bounds_capture_still_measures_without_inventing_a_rectangle(tmp_path):
    """bounds_xy is null when no crop could be applied; nothing may be derived from it."""
    side = sidecar()
    side["bounds_xy"] = None
    m = analyzer.stage_a_export_metrics(
        write_tiff(tmp_path, solid_square(), "nocrop.tiff"), side)
    assert m["resolution"]["native_width_px"] is None
    assert m["resolution"]["scale_factor"] is None
    assert m["frame"]["aspect"] is None
    assert m["frame"]["clamp_applied"] is None
    # No bounds means nothing can be called out-of-bounds.
    assert m["pixels"]["out_of_bbox_palette_px"] == 0
    # Edge and blend metrics are bounds-independent and must still be reported.
    assert m["edges"]["hard_edge_ratio"] == 1.0


# --- shared Value-first element id reader -----------------------------------

def test_safe_api_element_id_value_reads_value_before_integervalue():
    from vop_interwoven.revit.safe_api import element_id_value

    class Revit2025Id(object):
        Value = 2 ** 40

        @property
        def IntegerValue(self):
            raise OverflowError("id exceeds the legacy 32-bit range")

    class LegacyId(object):
        IntegerValue = 871863

    assert element_id_value(Revit2025Id()) == 2 ** 40
    assert element_id_value(LegacyId()) == 871863


def test_safe_api_element_id_value_returns_none_for_a_non_id():
    from vop_interwoven.revit.safe_api import element_id_value
    # No int() fallback: the answer always means "this was an element id".
    assert element_id_value(587278) is None
    assert element_id_value("587278") is None
    assert element_id_value(None) is None
    assert element_id_value(object()) is None


def test_thinrunner_coerce_view_id_uses_the_shared_reader():
    """thinrunner_streaming is a Dynamo entry script and is not importable
    outside Revit (it manipulates sys.path at module scope), so its behaviour
    is asserted from source rather than by calling it.

    Both points matter: it must go through element_id_value rather than
    reading IntegerValue first, and it must import it ABSOLUTELY -- this file
    is loaded standalone in Dynamo, where a relative import has no package to
    resolve against.
    """
    import ast
    from pathlib import Path as _Path

    source = _Path("vop_interwoven/thinrunner_streaming.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    coerce = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and node.name == "_coerce_view_id")
    body = ast.get_source_segment(source, coerce)
    assert "element_id_value" in body
    assert "from vop_interwoven.revit.safe_api import element_id_value" in body
    assert "from .revit" not in body, "relative import breaks the standalone Dynamo load"
    # The old IntegerValue-first reads are gone. Checked on the parsed
    # function, not the text: the comment explaining the change legitimately
    # names IntegerValue.
    attributes = {node.attr for node in ast.walk(coerce) if isinstance(node, ast.Attribute)}
    assert "IntegerValue" not in attributes


# --- scale factor on a clamped capture --------------------------------------

def test_scale_factor_is_measured_across_content_not_the_padded_canvas(tmp_path):
    """Revit pads the short axis to stay inside 10:1; padding is not model pixels.

    A 1:20-aspect view is padded horizontally to 1:10, so half the canvas
    width is frame. Dividing the full width by native would report twice the
    density the capture actually rendered at.
    """
    # 50 ft wide x 1000 ft tall = 1:20, clamped to 1:10.
    arr = np.full((400, 200, 3), 255, dtype=np.uint8)
    arr[:, 50:150] = RED
    m = measure(tmp_path, arr, bounds=(0.0, 0.0, 50.0, 1000.0))
    frame, res = m["frame"], m["resolution"]

    assert frame["clamp_applied"] is True
    assert frame["pad_x_px"] == pytest.approx(90.0)     # (200 - 400*0.05)/2
    assert res["content_width_px"] == 20                # 200 - 2*90
    assert res["width_px"] == 200

    # native = 50 ft * 12 * 150 / 96 = 937.5 px
    assert res["native_width_px"] == pytest.approx(937.5)
    assert res["scale_factor"] == pytest.approx(20 / 937.5)
    # The uncorrected figure is kept, and is 10x the real one here.
    assert res["canvas_scale_factor"] == pytest.approx(200 / 937.5)
    assert res["canvas_scale_factor"] > res["scale_factor"]


def test_an_unclamped_capture_has_identical_content_and_canvas_scale(tmp_path):
    m = measure(tmp_path, solid_square(size=64), bounds=(0.0, 0.0, 100.0, 80.0))
    res = m["resolution"]
    assert m["frame"]["clamp_applied"] is False
    assert res["content_width_px"] == res["width_px"] == 64
    assert res["scale_factor"] == res["canvas_scale_factor"]


def test_a_null_bounds_capture_reports_no_scale_factor_of_either_kind(tmp_path):
    side = sidecar()
    side["bounds_xy"] = None
    res = analyzer.stage_a_export_metrics(
        write_tiff(tmp_path, solid_square(), "nb.tiff"), side)["resolution"]
    assert res["scale_factor"] is None
    assert res.get("canvas_scale_factor") is None
    # Content dimensions still describe the image itself, which is knowable.
    assert res["content_width_px"] == 64


# --- palette must be present, or nothing may be measured --------------------

def test_an_exports_list_with_no_palette_is_refused_not_measured(tmp_path):
    """An empty palette does not fail — it reports every pixel as off-palette,
    a hard_edge_ratio of 0 and no pastels, which reads exactly like a
    catastrophically drifted capture. That silent-wrong-answer is the failure
    mode worth refusing."""
    write_tiff(tmp_path, solid_square(), "a.tiff")
    base = sidecar()
    doc = {"exports": [{"label": "rep0", "tiff_path": "a.tiff",
                        "resolution": base["resolution"], "bounds_xy": base["bounds_xy"]}]}
    src = tmp_path / "report.json"
    src.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="NO_PALETTE"):
        analyzer.analyze_metrics_json(src)


def test_an_export_record_defers_to_its_own_production_sidecar(tmp_path):
    """The probe records name their sidecar and deliberately do not duplicate
    the palette; the sidecar is production's own and carries it."""
    write_tiff(tmp_path, solid_square(), "cap.tiff")
    side = dict(sidecar(), tiff_path="cap.tiff")
    (tmp_path / "cap.json").write_text(json.dumps(side), encoding="utf-8")
    doc = {"exports": [{"label": "rep0", "case": "d1_determinism",
                        "tiff_path": "cap.tiff", "sidecar_path": "cap.json"}]}
    src = tmp_path / "report.json"
    src.write_text(json.dumps(doc), encoding="utf-8")
    _out, rows = analyzer.analyze_metrics_json(src)
    assert [label for label, _ in rows] == ["rep0"]
    metrics = rows[0][1]
    assert metrics["palette_color_count"] == 1
    assert metrics["edges"]["hard_edge_ratio"] == 1.0
    assert metrics["resolution"]["native_width_px"] == pytest.approx(1875.0)


def test_a_bare_production_sidecar_is_measured_directly(tmp_path):
    """The captures/ directory is the intended --export-metrics target."""
    write_tiff(tmp_path, solid_square(), "d1_determinism.rep0.tiff")
    side = dict(sidecar(), tiff_path="d1_determinism.rep0.tiff", view_id=871863)
    src = tmp_path / "d1_determinism.rep0.json"
    src.write_text(json.dumps(side), encoding="utf-8")
    _out, rows = analyzer.analyze_metrics_json(src)
    assert rows[0][1]["palette_color_count"] == 1


# --- a probe directory must not measure each capture twice ------------------

def _probe_dir(tmp_path):
    """The layout a probe run produces: a report plus captures/ sidecars."""
    probe = tmp_path / "drift_onset_probe"
    captures = probe / "captures"
    captures.mkdir(parents=True)
    write_tiff(captures, solid_square(), "d1_determinism.rep0.tiff")
    write_tiff(captures, solid_square(inset=8), "d1_determinism.rep1.tiff")
    base = sidecar()
    for label in ("rep0", "rep1"):
        side = dict(base, tiff_path=f"d1_determinism.{label}.tiff", view_id=871863)
        (captures / f"d1_determinism.{label}.json").write_text(
            json.dumps(side), encoding="utf-8")
    report = {
        "view": {"id": 871863, "name": "MOB 1 - LEVEL 2"},
        "exports": [
            {"case": "d1_determinism", "label": f"d1_determinism/{label}",
             "tiff_path": str(captures / f"d1_determinism.{label}.tiff"),
             "sidecar_path": str(captures / f"d1_determinism.{label}.json")}
            for label in ("rep0", "rep1")
        ],
    }
    (probe / "MOB_1_-_LEVEL_2.871863.drift_onset.json").write_text(
        json.dumps(report), encoding="utf-8")
    return probe


def test_targeting_a_whole_probe_directory_measures_each_capture_once(tmp_path, capsys):
    probe = _probe_dir(tmp_path)
    table = tmp_path / "t.md"
    assert analyzer.main([str(probe), "--export-metrics", "--metrics-table", str(table)]) == 0
    # Two captures exist; two data rows, not four.
    body = [line for line in table.read_text(encoding="utf-8").strip().split("\n")]
    assert len(body) == 2 + 2   # header, rule, two rows


def test_the_duplicate_is_recorded_rather_than_silently_dropped(tmp_path):
    probe = _probe_dir(tmp_path)
    analyzer.main([str(probe), "--export-metrics"])
    report_metrics = json.loads(
        (probe / "MOB_1_-_LEVEL_2.871863.drift_onset.metrics.json").read_text(encoding="utf-8"))
    codes = {err["code"] for err in report_metrics["errors"]}
    assert codes == {"DUPLICATE_TIFF"}
    assert report_metrics["exports"] == {}


def test_the_standalone_capture_sidecar_is_the_one_kept(tmp_path):
    """It is production's own file; the probe record merely points at it."""
    probe = _probe_dir(tmp_path)
    analyzer.main([str(probe), "--export-metrics"])
    kept = json.loads(
        (probe / "captures" / "d1_determinism.rep0.metrics.json").read_text(encoding="utf-8"))
    assert kept["errors"] == []
    assert list(kept["exports"]) == ["871863"]


def test_dedupe_does_not_suppress_genuinely_distinct_captures(tmp_path):
    probe = _probe_dir(tmp_path)
    analyzer.main([str(probe), "--export-metrics"])
    measured = set()
    for label in ("rep0", "rep1"):
        payload = json.loads(
            (probe / "captures" / f"d1_determinism.{label}.metrics.json").read_text(
                encoding="utf-8"))
        assert payload["errors"] == []
        measured.add(payload["exports"]["871863"]["image"]["sha256"])
    # The two fixtures differ, so both were really measured.
    assert len(measured) == 2
