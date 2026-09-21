"""GATE: the Stage A export is decoupled from the analysis grid, AT THE CALL SITE.

WHY THIS FILE EXISTS SEPARATELY FROM tests/test_frame_export_geometry.py.
That file binds the FORMULA: it calls frame_export_geometry directly and can
prove the arithmetic never reads a cell size, because a cell size cannot be
passed to it. It cannot prove anything about the arguments PRODUCTION passes.

CLAUDE.md records that exact escape as a review finding, in the same module:
after effective_export_dpi() was extracted and every property bound to it,
the original grid-extent defect could still be reinstated at the call site --
``_effective_export_dpi(<grid tuple>, ...)`` -- with all 981 tests green,
because the properties supplied their own bounds and never saw production's.
The grid rectangle and the frame rectangle are both four-value tuples, so no
signature check separates them.

So this file runs ``export_color_id_buffer_view`` itself and varies the one
thing that used to couple the two: ``raster.cell_size_ft``, together with the
``W``/``H`` cell counts that follow from it. The frame ``bounds_xy`` is held
fixed across every case, because the frame is what the export is now supposed
to be a function of. If any exported quantity moves, the grid is still
reaching the export.

THE FIXTURE IS BUILT TO DISCRIMINATE. The cell sizes chosen do not divide the
frame evenly, so ``ceil(extent/cell)*cell`` -- the shipped sizing term --
genuinely differs between them and from the frame itself. A fixture whose
cell sizes happened to divide the frame would pass against the defect, which
is the trap the effective-dpi call-site test was written to avoid.
"""
import types

import pytest

import vop_interwoven.color_id_buffer as color_id_buffer
from vop_interwoven.config import Config
from vop_interwoven.core.math_utils import Bounds2D
from vop_interwoven.revit.view_basis import ViewBasis

from tests.stage_a_capture_fakes import (
    FakeCategory,
    FakeDiag,
    FakeDoc,
    FakeElement,
    FakeViewPlan,
    install_fake_revit_db,
)

_PLAN_BASIS = ViewBasis(origin=(0, 0, 0), right=(1, 0, 0), up=(0, 1, 0), forward=(0, 0, -1))

# Deliberately not a whole number of any cell size below, and not square.
FRAME = Bounds2D(0.0, 0.0, 61.7, 43.3)

# None of these divide 61.7 or 43.3 evenly.
CELL_SIZES = [0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 7.0]


def _world():
    walls = FakeCategory("Walls", 10)
    elements = [FakeElement(1001, walls), FakeElement(1002, walls)]
    doc = FakeDoc(elements=elements, link_instances=[], categories=[walls])
    view = FakeViewPlan(view_id=4242)
    return doc, view, elements


def _cfg(tmp_path):
    cfg = Config()
    cfg.include_linked_rvt = False
    cfg.debug_dump_path = str(tmp_path)
    return cfg


def _raster_at(cell_size_ft, model_clip_bounds=None):
    """A raster whose FRAME is fixed and whose GRID varies with cell size.

    This is the production relationship: pipeline.init_view_raster sizes W/H
    as ceil(extent / cell_size_ft) over the same bounds.
    """
    import math
    w = int(math.ceil(FRAME.width() / cell_size_ft))
    h = int(math.ceil(FRAME.height() / cell_size_ft))
    return types.SimpleNamespace(
        W=w, H=h, cell_size_ft=cell_size_ft,
        bounds_xy=FRAME,
        model_clip_bounds=model_clip_bounds,
        view_basis=_PLAN_BASIS,
    )


def _export(tmp_path, raster):
    doc, view, elements = _world()
    with install_fake_revit_db():
        return color_id_buffer.export_color_id_buffer_view(
            doc, view, elements=elements, cfg=_cfg(tmp_path), diag=FakeDiag(),
            raster=raster, elem_cache=None,
        )


def _frames(tmp_path, model_clip_bounds=None):
    out = []
    for cell in CELL_SIZES:
        raster = _raster_at(cell, model_clip_bounds)
        md = _export(tmp_path, raster)["metadata"]
        out.append((cell, raster, md))
    return out


def test_the_fixture_actually_varies_the_grid(tmp_path):
    """Control. Without it every assertion below would pass against a fixture
    whose grid never moved -- proving the export is independent of a constant.
    """
    rows = _frames(tmp_path)
    grids = {(r.W, r.H) for _c, r, _m in rows}
    assert len(grids) == len(CELL_SIZES), grids
    # ... and the grid rectangle really does differ from the frame, which is
    # the term the shipped sizing path used.
    for cell, r, _m in rows:
        assert (r.W * cell, r.H * cell) != (FRAME.width(), FRAME.height())


def test_the_export_record_is_present_for_every_case(tmp_path):
    """Reachability control. A three-valued record that came back
    "unavailable" everywhere would make every comparison below trivially
    true. CLAUDE.md: a test that cannot reach the code it names is worth less
    than no test."""
    for _cell, _r, md in _frames(tmp_path):
        assert md["export_frame"]["status"] == "value", md["export_frame"]


@pytest.mark.parametrize("model_clip_bounds", [
    None,                                   # A is B
    Bounds2D(12.0, 9.0, 40.0, 30.0),        # A genuinely narrower than B
])
def test_export_pixels_and_fpp_do_not_move_with_the_analysis_grid(tmp_path, model_clip_bounds):
    """GATE (Stage A step 2): varying cell_size_ft leaves export px and fpp
    unchanged.

    Run with and without a narrowing model crop, because the two take
    different paths through the sizing block and only the second exercises
    the snapped-crop registration.
    """
    rows = _frames(tmp_path, model_clip_bounds)
    first_cell, _first_raster, first = rows[0]
    ef = first["export_frame"]

    for cell, _raster, md in rows[1:]:
        other = md["export_frame"]
        why = "cell_size_ft {0} vs {1}".format(first_cell, cell)
        # what Revit is asked for
        assert other["requested_px"] == ef["requested_px"], why
        assert md["resolution"]["requested_pixel_size"] == \
            first["resolution"]["requested_pixel_size"], why
        # the resolution the capture carries
        assert other["achieved_fpp_ft"] == pytest.approx(ef["achieved_fpp_ft"], rel=1e-12), why
        assert other["achieved_export_dpi"] == pytest.approx(
            ef["achieved_export_dpi"], rel=1e-12), why
        # the rectangle it spans, and where A sits inside B
        assert other["crop_px"] == ef["crop_px"], why
        assert other["frame_px"] == ef["frame_px"], why
        assert other["crop_offset_px"] == ef["crop_offset_px"], why
        assert other["crop_snapped_uv"] == pytest.approx(ef["crop_snapped_uv"]), why
        # and the rectangle recorded for the decoder
        assert md["bounds_xy"] == pytest.approx(first["bounds_xy"]), why


def test_the_recorded_crop_is_the_rectangle_that_was_rendered(tmp_path):
    """The snapped rectangle must be what the view was actually cropped to,
    not merely computed and discarded. Sizing against a snapped rectangle
    while rendering the unsnapped one would leave every decoded UV off by up
    to a pixel per edge, with every test above still green."""
    md = _export(tmp_path, _raster_at(1.0, Bounds2D(12.0, 9.0, 40.0, 30.0)))["metadata"]
    ef = md["export_frame"]
    assert md["bounds_xy"] == pytest.approx(list(ef["crop_snapped_uv"]))
    # and the recorded offset reconstructs it from the frame's own corners,
    # which is that field's documented contract
    off = md["model_crop_offset_uv"]
    assert FRAME.xmin + off[0] == pytest.approx(ef["crop_snapped_uv"][0])
    assert FRAME.ymin + off[1] == pytest.approx(ef["crop_snapped_uv"][1])
    assert FRAME.xmax + off[2] == pytest.approx(ef["crop_snapped_uv"][2])
    assert FRAME.ymax + off[3] == pytest.approx(ef["crop_snapped_uv"][3])


def test_a_narrowed_crop_is_smaller_than_the_frame_in_pixels(tmp_path):
    """Discrimination check for the parametrised gate above: the narrowed
    case must actually produce a different rectangle from the A-is-B case, or
    both parameters would be testing the same path."""
    wide = _export(tmp_path, _raster_at(1.0, None))["metadata"]["export_frame"]
    narrow = _export(tmp_path, _raster_at(
        1.0, Bounds2D(12.0, 9.0, 40.0, 30.0)))["metadata"]["export_frame"]
    assert narrow["crop_px"][0] < wide["crop_px"][0]
    assert narrow["crop_px"][1] < wide["crop_px"][1]
    assert narrow["crop_offset_px"] != [0, 0]
    assert wide["crop_is_frame"] is True
    assert narrow["crop_is_frame"] is False
    # Same frame, so the same resolution: narrowing the crop must not change
    # feet-per-pixel. That is the registration guarantee decision A asks for.
    assert narrow["achieved_fpp_ft"] == pytest.approx(wide["achieved_fpp_ft"], rel=1e-12)
