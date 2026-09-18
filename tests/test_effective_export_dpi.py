"""A10: the producer's achieved dpi and a decoder's must be the SAME number.

`effective_export_dpi` promises to be `view_scale / (12 * feet_per_pixel)`
for the feet_per_pixel a decoder derives from the same sidecar. Nothing
composed the two, which is how the first version came to divide by
`paper_fit_in` -- the GRID's extent, `raster.W * cell_size_ft`, where
`raster.W` is `ceil(extent / cell)` (view_basis.py:1183-1184) -- instead of
by the rectangle the TIFF actually spans.

That is the same substitution the two-axis cap already refuses at
color_id_buffer.py:1694-1703, for the same stated reason.

These tests bind the two sides the way tests/test_uv_pixel_round_trip.py
binds the forward and inverse mappings: the producer's arithmetic is
replicated here, the decoder's feet_per_pixel comes from the live
_clamp_pad_geometry, and the identity is asserted across them. Everything is
synthetic -- no Revit, no capture.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import decode_stage_a_color_id as dsc  # noqa: E402
from vop_interwoven.resolution_contract import (  # noqa: E402
    effective_export_dpi as _production_effective_export_dpi,
)


def producer_effective_dpi(crop, actual, view_scale):
    """Adapter onto the PRODUCTION function -- signature only, no arithmetic.

    This file used to carry its own copy of the formula. Review on PR #202
    pointed out what that costs: a test that reimplements the producer binds
    the decoder to the test's copy, so production could regress to the
    fitted-axis or grid-extent form with every assertion here still green.
    The copy is gone; only the call shape remains.
    """
    aw, ah = actual
    return _production_effective_export_dpi(crop, aw, ah, view_scale)


# (label, crop rect, actual px, fitted axis, view scale)
#
# grid_W/cell are given per case only to show what paper_fit_in WOULD have
# been; nothing under test reads them.
CASES = [
    # Elev 5's recorded shape: decode reports the grid at 97.0 ft against a
    # 96.38 ft crop, 0.64% over (decode_stage_a_color_id.py:828-831).
    ("elev5_cell_slack", (0.0, 0.0, 96.38, 96.38), (4000, 4000), "width", 96.0, 97.0),
    # A narrowed capture: model_clip_bounds pulls the crop well inside the grid.
    ("narrowed_crop", (0.0, 0.0, 80.0, 50.0), (4000, 2500), "width", 96.0, 100.0),
    # Vertical fit: the fitted axis is v, and it is the SHORT one here.
    ("vertical_fit", (0.0, 0.0, 300.0, 100.0), (12000, 4000), "height", 48.0, 101.0),
    # No slack at all: grid extent and crop agree exactly.
    ("exact_cells", (0.0, 0.0, 100.0, 100.0), (5000, 5000), "width", 96.0, 100.0),
    # THE FITTED AXIS IS THE PADDED ONE. A wide crop under VERTICAL fit: the
    # 40 px height is both what PixelSize set and the axis carrying 10 px of
    # clamp pad each side, so the dimension check passes on a padded axis and
    # cannot be used to argue the fitted axis is clean. Dividing by the fitted
    # axis reports 80 dpi where the truth is 40.
    ("fitted_axis_is_padded", (0.0, 0.0, 80.0, 4.0), (400, 40), "height", 96.0, 4.0),
    # The same defect mirrored: a TALL crop under horizontal fit pads width.
    ("fitted_axis_is_padded_h", (0.0, 0.0, 4.0, 80.0), (40, 400), "width", 96.0, 4.0),
]


@pytest.mark.parametrize("label,crop,actual,axis,scale,grid_ft",
                         CASES, ids=[c[0] for c in CASES])
def test_effective_dpi_equals_view_scale_over_12_fpp(label, crop, actual, axis, scale, grid_ft):
    """The promise in the field's own comment, checked against the decoder."""
    aw, ah = actual
    effective = producer_effective_dpi(crop, actual, scale)

    fpp, _pad_x, _pad_y = dsc._clamp_pad_geometry(crop, aw, ah, measured_w=aw, measured_h=ah)
    assert effective == pytest.approx(scale / (12.0 * fpp), rel=1e-12)


UNPADDED_CASES = [c for c in CASES if not c[0].startswith("fitted_axis_is_padded")]


@pytest.mark.parametrize("label,crop,actual,axis,scale,grid_ft",
                         UNPADDED_CASES, ids=[c[0] for c in UNPADDED_CASES])
def test_dividing_by_the_grid_extent_under_reports(label, crop, actual, axis, scale, grid_ft):
    """The FIRST defect, pinned so it cannot come back.

    paper_fit_in is the grid rounded up to whole cells, so it is >= the
    rendered extent and the quotient is <= the truth -- never above it, which
    is why the error read as a plausible dpi rather than as nonsense.

    Scoped to the unpadded fixtures on purpose. On a capture whose fitted axis
    is padded, the grid's over-large denominator (under-reports) and the
    padded axis's excess pixels (over-report) push in OPPOSITE directions, so
    those cases cannot isolate this effect -- the padded-axis error dominates
    and the sign flips. Asserting a bound across both would be asserting the
    net of two mistakes. The padded case has its own test below.
    """
    aw, ah = actual
    crop_fit_ft = (crop[3] - crop[1]) if axis == "height" else (crop[2] - crop[0])
    actual_fit_px = ah if axis == "height" else aw

    truth = producer_effective_dpi(crop, actual, scale)
    from_grid = float(actual_fit_px) / (float(grid_ft) * 12.0 / float(scale))

    assert from_grid <= truth + 1e-9
    if grid_ft > crop_fit_ft:
        assert from_grid < truth, "a case with slack must actually discriminate"


def test_color_id_buffer_calls_the_shared_function_and_does_not_recompute():
    """The achieved dpi must be measured against the RENDERED CROP, and there
    must be exactly one implementation of that.

    This replaces a source-text scan that asserted `crop_bounds_xy` appeared
    near an inline `effective_export_dpi = None`. That scan was brittle -- it
    broke the moment the arithmetic was extracted, which is the improvement it
    was meant to protect -- and it approximated the real guarantee instead of
    stating it.

    The real guarantee is structural, and the extraction is what makes it
    checkable: the production function takes `crop_bounds_xy` and nothing
    grid-shaped, so a grid extent cannot reach it without changing a
    signature; and color_id_buffer must CALL it rather than recompute dpi
    locally, or the binding in this file is against a copy again.
    """
    import inspect
    from vop_interwoven import resolution_contract

    params = list(inspect.signature(
        resolution_contract.effective_export_dpi).parameters)
    assert params[0] == "crop_bounds_xy", (
        "the denominator must be the rendered crop; a grid extent must not be "
        "expressible as the first argument")
    assert not any("paper" in p or "grid" in p or "raster" in p for p in params)

    src = (Path(__file__).resolve().parent.parent
           / "vop_interwoven" / "color_id_buffer.py").read_text(encoding="utf-8")
    assert "_effective_export_dpi(" in src, (
        "color_id_buffer must call resolution_contract.effective_export_dpi")
    assert "effective_export_dpi = min(" not in src, (
        "dpi recomputed inline; there must be one implementation, not two")


@pytest.mark.parametrize("label,crop,actual,axis,scale,grid_ft",
                         CASES, ids=[c[0] for c in CASES])
def test_the_fitted_axis_alone_is_not_safe_to_divide(label, crop, actual, axis, scale, grid_ft):
    """The SECOND defect, pinned.

    The first fix divided by the FITTED axis, arguing that PixelSize sets it
    and the dimension check verifies it, so it could not be padded. That is
    false: ExportImage pads the SHORT axis, the fitted axis can BE the short
    one, and the padded dimension then EQUALS PixelSize -- so the dimension
    check passes on a padded axis and proves nothing about it.

    The fitted-axis answer must be >= the truth on every case, and strictly
    greater on the two where the fitted axis is padded, since a padded axis
    carries pixels its extent does not cover.
    """
    aw, ah = actual
    crop_fit_ft = (crop[3] - crop[1]) if axis == "height" else (crop[2] - crop[0])
    actual_fit_px = ah if axis == "height" else aw

    truth = producer_effective_dpi(crop, actual, scale)
    fitted_only = float(actual_fit_px) / (crop_fit_ft * 12.0 / float(scale))

    assert fitted_only >= truth - 1e-9
    if label.startswith("fitted_axis_is_padded"):
        assert fitted_only == pytest.approx(2.0 * truth, rel=1e-12), (
            "this fixture's clamp doubles the pixel count on the fitted axis")


def test_pad_free_captures_are_unaffected_by_the_axis_question():
    """Where no pad exists both axes agree, so min() is inert and the earlier
    fitted-axis form was already right. That is why this defect showed up on
    no real capture: every view in the reference run is horizontal fit with a
    wide crop, and all six record a zero pad on the fitted axis."""
    crop, actual, scale = (0.0, 0.0, 100.0, 50.0), (4000, 2000), 96.0
    aw, ah = actual
    fpp, pad_x, pad_y = dsc._clamp_pad_geometry(crop, aw, ah, measured_w=aw, measured_h=ah)
    assert (pad_x, pad_y) == (0.0, 0.0)
    dpi_u = aw / ((crop[2] - crop[0]) * 12.0 / scale)
    dpi_v = ah / ((crop[3] - crop[1]) * 12.0 / scale)
    assert dpi_u == pytest.approx(dpi_v, rel=1e-12)
    assert producer_effective_dpi(crop, actual, scale) == pytest.approx(
        scale / (12.0 * fpp), rel=1e-12)
