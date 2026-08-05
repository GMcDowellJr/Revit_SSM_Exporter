import json
import pytest

from tests.dynamo.resolution_contract import (
    apply_resolution_cap,
    build_resolution_report,
    calculate_canvas_placement,
    calculate_paper_space_resolution,
    round_half_up_positive,
)


def test_required_widths_at_scale_96():
    assert calculate_paper_space_resolution(276, 100, 96, 150, "active_model_crop")["requested_width_px"] == 5175
    assert calculate_paper_space_resolution(276, 100, 96, 75, "active_model_crop")["requested_width_px"] == 2588
    assert calculate_paper_space_resolution(276, 100, 96, 300, "active_model_crop")["requested_width_px"] == 10350


def test_contract_values_150_dpi():
    r = calculate_paper_space_resolution(276, 100, 96, 150, "active_model_crop")
    assert r["paper_width_in"] == 34.5
    assert r["target_pixels_per_model_foot"] == 18.75
    assert r["target_model_inches_per_pixel"] == 0.64


def test_bounds_sources_allowed():
    for source in ["active_model_crop", "resolved_model_bounds"]:
        assert calculate_paper_space_resolution(10, 5, 100, 150, source)["bounds_source"] == source


@pytest.mark.parametrize("kwargs", [
    {"view_scale": 0}, {"target_dpi": 0}, {"model_width_ft": 0},
])
def test_invalid_inputs(kwargs):
    args = dict(model_width_ft=10, model_height_ft=5, view_scale=100, target_dpi=150, bounds_source="active_model_crop")
    args.update(kwargs)
    with pytest.raises(ValueError):
        calculate_paper_space_resolution(**args)


def test_width_only_cap_and_effective_dpi():
    r = calculate_paper_space_resolution(276, 100, 96, 150, "active_model_crop")
    capped = apply_resolution_cap(r, 4000)
    assert capped["accepted_width_px"] == 4000
    assert capped["capped"] is True
    assert capped["effective_dpi"] == pytest.approx((4000 / 276) * 96 / 12)


def test_height_driven_cap():
    r = calculate_paper_space_resolution(100, 300, 100, 150, "resolved_model_bounds")
    capped = apply_resolution_cap(r, 1000)
    assert capped["predicted_height_px"] == 1000
    assert capped["accepted_width_px"] < r["requested_width_px"]


def test_canvas_offsets_and_round_half_up():
    assert round_half_up_positive(2.5) == 3
    placement = calculate_canvas_placement((10, 10, 20, 20), (0, 0, 30, 30), 2.5)
    assert placement["canvas_width_px"] == 75
    assert placement["model_offset_px"] == [25, 25]
    assert placement["lossless_padding_possible"] is True


def test_complete_report_serializes():
    report = build_resolution_report(276, 100, 96, 150, "active_model_crop", actual_width_px=5175, actual_height_px=1875)
    text = json.dumps({"resolution": report})
    assert '"resolution"' in text
