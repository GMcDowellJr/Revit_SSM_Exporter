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


def producer_effective_dpi(crop, actual, view_scale):
    """color_id_buffer.py's rule, replicated.

    Paper inches along an axis are model feet * 12 / view scale, and the
    achieved dpi is that axis's measured pixel count over it -- computed for
    BOTH axes, smaller wins. The padded axis carries more pixels than its
    extent warrants and so always reports the larger figure; the minimum
    selects the unpadded one without needing the aspect limit or the pad.
    """
    aw, ah = actual
    crop_u_ft = float(crop[2]) - float(crop[0])
    crop_v_ft = float(crop[3]) - float(crop[1])
    dpi_u = float(aw) / (crop_u_ft * 12.0 / float(view_scale))
    dpi_v = float(ah) / (crop_v_ft * 12.0 / float(view_scale))
    return min(dpi_u, dpi_v)


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


def test_the_two_axis_cap_makes_the_same_choice():
    """Not a new rule: color_id_buffer.py:1694-1703 already resolves
    compute_model_crop() rather than using the grid's paper extents, because
    the grid 'would understate the derived axis by exactly the amount the
    crop narrows'. This pins that the same source is used for both, so the
    cap and the reported dpi cannot disagree about what was rendered."""
    src = (Path(__file__).resolve().parent.parent
           / "vop_interwoven" / "color_id_buffer.py").read_text(encoding="utf-8")
    i = src.index("effective_export_dpi = None")
    window = src[i - 2000:i + 500]
    assert "crop_bounds_xy" in window, (
        "the achieved dpi must be measured against the rendered crop, not "
        "paper_fit_in / the grid extent")
    assert "paper_fit_in" not in window.split("_crop_fit_ft = None")[-1]


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
