import json
import pytest

from tests.dynamo.resolution_contract import (
    apply_resolution_cap,
    build_resolution_report,
    calculate_canvas_placement,
    calculate_paper_space_resolution,
    choose_resolution_bounds,
    resolution_report_for_accepted_width,
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
    """The HEIGHT is what the cap binds on, and it lands at or under it.

    This asserted ``== 1000`` and had been failing since the fitted axis
    started flooring: 1800 px scaled by 1000/5400 floors to 333, and 333 at
    this view's 3.0 aspect derives exactly 999.0, not 1000. The test was
    pinning a height that only ever arose from rounding the request UP to
    reach the cap -- the behaviour that floor was introduced to stop. It is
    not a D5 regression; it fails identically with D5 reverted.

    It stayed invisible because tests/dynamo/ is collected only under
    VOP_RUN_DYNAMO_TESTS=1.

    Intent preserved: the cap must be BINDING on the height (within one
    pixel of it, not slack) and must not be exceeded.
    """
    r = calculate_paper_space_resolution(100, 300, 100, 150, "resolved_model_bounds")
    capped = apply_resolution_cap(r, 1000)
    assert capped["predicted_height_px"] <= 1000
    assert 1000 - capped["predicted_height_px"] <= 1
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


def test_inactive_crop_uses_resolved_model_bounds():
    bounds, source = choose_resolution_bounds(active_crop_bounds=None, resolved_model_bounds=(0, 0, 276, 100))
    assert bounds == (0, 0, 276, 100)
    assert source == "resolved_model_bounds"


def test_resolution_recomputed_after_pixel_size_backoff():
    report = calculate_paper_space_resolution(276, 100, 96, 150, "active_model_crop")
    accepted = resolution_report_for_accepted_width(report, 4000)
    assert accepted["accepted_width_px"] == 4000
    assert accepted["accepted_pixels_per_model_foot"] == pytest.approx(4000 / 276)
    assert accepted["effective_dpi"] == pytest.approx((4000 / 276) * 96 / 12)
    assert accepted["actual_model_inches_per_pixel"] == pytest.approx(12 / (4000 / 276))
    assert accepted["pixel_size_backoff"] is True
