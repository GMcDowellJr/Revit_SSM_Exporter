"""GATE: the capture frame is never re-centred by the cap envelope.

THE DEFECT THIS PINS. view_basis.resolve_view_bounds expands the view's
bounds to hold annotation, then -- if the expansion exceeds a sheet-sized
envelope -- clips it and RE-CENTRES the remainder on the pre-annotation
(model) bounds. After that, raster.bounds_xy is a model-centred window, not
the annotation extent it was expanded to hold. Sizing a capture against it
puts annotation outside the image on exactly the views that needed the
expansion most.

TWO CAPS, AND ONLY ONE OF THEM MOVES BOUNDS. This was easy to get wrong and
was gotten wrong once here: the *grid resolution* cap further down that
function is adaptive-cell-size and leaves bounds UNCHANGED ("CRITICAL: Bounds
stay UNCHANGED" in its own comment). The cap that moves the frame is the
earlier *annotation cap envelope*. Only the second one is what this file is
about, and the first one is pinned separately below so the two cannot be
conflated again.

ADDITIVE BY CONSTRUCTION. raster.bounds_xy is untouched, so the analysis grid
and the geometry path keep the exact frame they have always had; the capture
reads a separate rectangle. The geometry path predates the color-ID capture
and the envelope is its own historical behaviour, so moving the arbiter's
frame is not this step's to do.
"""
import math
import types

import pytest

import vop_interwoven.color_id_buffer as color_id_buffer
from vop_interwoven.config import Config
from vop_interwoven.core.math_utils import Bounds2D
from vop_interwoven.revit.view_basis import ViewBasis, resolve_view_bounds

from tests.stage_a_capture_fakes import (
    FakeCategory,
    FakeDiag,
    FakeDoc,
    FakeElement,
    FakeViewPlan,
    install_fake_revit_db,
)

_PLAN_BASIS = ViewBasis(origin=(0, 0, 0), right=(1, 0, 0), up=(0, 1, 0), forward=(0, 0, -1))

# The model sits in one corner; annotation pushes the frame far past it. With
# an envelope smaller than the expansion, the shipped code clips to the
# envelope and re-centres on the MODEL -- so the annotation end of the frame
# is what gets thrown away.
MODEL_BOUNDS = Bounds2D(0.0, 0.0, 40.0, 30.0)
ANNO_BOUNDS = Bounds2D(0.0, 0.0, 400.0, 300.0)
CELL = 1.0
MAX_W, MAX_H = 100, 80          # envelope: 100 x 80 ft, well inside ANNO_BOUNDS


def _bounds_result(max_W=MAX_W, max_H=MAX_H):
    """Drive resolve_view_bounds through its injectable policy hooks."""
    return resolve_view_bounds(
        view=FakeViewPlan(view_id=7),
        diag=FakeDiag(),
        policy={
            "cell_size_ft": CELL,
            "max_W": max_W,
            "max_H": max_H,
            "buffer_ft": 0.0,
            "bounds_default_fn": lambda view, policy: MODEL_BOUNDS,
            "crop_bounds_fn": lambda view, policy: MODEL_BOUNDS,
            "anno_expand_fn": lambda base, view, policy: ANNO_BOUNDS,
        },
    )


def test_the_envelope_really_does_re_centre_the_grid_bounds():
    """Known-positive. If the shipped envelope ever stops firing on this
    fixture, every assertion below would pass for a reason that says nothing
    about the frame, so the defect is pinned before the fix is."""
    r = _bounds_result()
    assert r["anno_cap_envelope_applied"] is True
    b = r["bounds_uv"]
    # clipped to the envelope ...
    assert b.width() == pytest.approx(MAX_W * CELL)
    assert b.height() == pytest.approx(MAX_H * CELL)
    # ... and re-centred on the MODEL, so it no longer starts where the
    # annotation frame starts, and no longer reaches its far corner.
    assert b.xmin != pytest.approx(ANNO_BOUNDS.xmin)
    assert b.xmax < ANNO_BOUNDS.xmax


def test_the_uncapped_frame_survives_the_envelope():
    """The fix, at the source: the annotation frame as computed is returned
    alongside the clipped one rather than being overwritten by it."""
    r = _bounds_result()
    u = r["anno_bounds_uncapped_uv"]
    assert u is not None
    assert (u.xmin, u.ymin, u.xmax, u.ymax) == pytest.approx(
        (ANNO_BOUNDS.xmin, ANNO_BOUNDS.ymin, ANNO_BOUNDS.xmax, ANNO_BOUNDS.ymax))


def test_bounds_uv_is_unchanged_so_the_geometry_path_is_untouched():
    """The additive claim. bounds_uv is what the analysis grid and the
    geometry path render into; this step must not move it."""
    r = _bounds_result()
    b = r["bounds_uv"]
    assert b.width() == pytest.approx(MAX_W * CELL)
    assert b.height() == pytest.approx(MAX_H * CELL)
    assert r["grid_W"] <= MAX_W and r["grid_H"] <= MAX_H


def test_no_expansion_means_no_uncapped_frame_and_no_envelope():
    """Control for the three above. Without an annotation expansion there is
    nothing for the envelope to move, and the new keys must say so rather
    than reporting a frame that was never computed."""
    r = resolve_view_bounds(
        view=FakeViewPlan(view_id=7),
        diag=FakeDiag(),
        policy={
            "cell_size_ft": CELL, "max_W": MAX_W, "max_H": MAX_H, "buffer_ft": 0.0,
            "bounds_default_fn": lambda view, policy: MODEL_BOUNDS,
            "crop_bounds_fn": lambda view, policy: MODEL_BOUNDS,
            "anno_expand_fn": lambda base, view, policy: None,
        },
    )
    assert r["anno_bounds_uncapped_uv"] is None
    assert r["anno_cap_envelope_applied"] is False


def test_the_grid_resolution_cap_does_not_move_bounds():
    """The OTHER cap, pinned so the two are not conflated again. Its own
    comment says "CRITICAL: Bounds stay UNCHANGED" -- it caps by growing the
    cell size, not by clipping. A frame well inside the envelope but far too
    fine for the cell count exercises it alone."""
    r = resolve_view_bounds(
        view=FakeViewPlan(view_id=7),
        diag=FakeDiag(),
        policy={
            "cell_size_ft": 0.01, "max_W": 50, "max_H": 50, "buffer_ft": 0.0,
            "bounds_default_fn": lambda view, policy: MODEL_BOUNDS,
            "crop_bounds_fn": lambda view, policy: MODEL_BOUNDS,
            "anno_expand_fn": lambda base, view, policy: None,
        },
    )
    assert r["cap_triggered"] is True
    assert r["resolution_mode"] == "adaptive"
    # resolution absorbed it; the rectangle did not move
    assert r["cell_size_ft_effective"] > r["cell_size_ft_requested"]
    assert r["bounds_uv"].width() == pytest.approx(MODEL_BOUNDS.width())
    assert r["bounds_uv"].height() == pytest.approx(MODEL_BOUNDS.height())
    assert r["anno_cap_envelope_applied"] is False


# --- the capture itself ---------------------------------------------------

def _raster_from(bounds_result, cell=CELL, model_clip_bounds=MODEL_BOUNDS):
    """A raster shaped the way pipeline.init_view_raster builds one.

    model_clip_bounds is supplied EXPLICITLY rather than read from
    bounds_result. resolve_view_bounds only populates "model_bounds_uv" on
    its crop-box branch (view_basis.py, xy_bounds_from_crop_box_all_corners),
    which needs a real Revit CropBox; driven through the injectable policy
    hooks this fixture lands on the fallback branch and that key comes back
    None. Reading it here would leave A equal to B and silently retire the
    narrowing case -- CLAUDE.md's "know what the fake harness does NOT
    provide", which cost this repo an entire untested crop path once already.
    """
    b = bounds_result["bounds_uv"]
    return types.SimpleNamespace(
        W=max(1, int(math.ceil(b.width() / cell))),
        H=max(1, int(math.ceil(b.height() / cell))),
        cell_size_ft=cell,
        bounds_xy=b,
        model_clip_bounds=model_clip_bounds,
        anno_frame_bounds=bounds_result.get("anno_bounds_uncapped_uv"),
        anno_cap_envelope_applied=bounds_result.get("anno_cap_envelope_applied"),
        view_basis=_PLAN_BASIS,
    )


def _capture(tmp_path, raster):
    walls = FakeCategory("Walls", 10)
    elements = [FakeElement(1001, walls)]
    doc = FakeDoc(elements=elements, link_instances=[], categories=[walls])
    cfg = Config()
    cfg.include_linked_rvt = False
    cfg.debug_dump_path = str(tmp_path)
    with install_fake_revit_db():
        return color_id_buffer.export_color_id_buffer_view(
            doc, FakeViewPlan(view_id=7), elements=elements, cfg=cfg,
            diag=FakeDiag(), raster=raster, elem_cache=None,
        )["metadata"]


def test_a_capped_view_is_captured_against_the_annotation_frame(tmp_path):
    """GATE: on a view whose annotation expansion exceeded the envelope, the
    capture frame is the annotation extent -- not the model-centred window
    the grid was left with."""
    md = _capture(tmp_path, _raster_from(_bounds_result()))
    ef = md["export_frame"]

    assert ef["status"] == "value"
    assert ef["frame_source"] == "anno_uncapped"
    assert ef["anno_cap_envelope_applied"] is True
    assert ef["frame_uv"] == pytest.approx(
        [ANNO_BOUNDS.xmin, ANNO_BOUNDS.ymin, ANNO_BOUNDS.xmax, ANNO_BOUNDS.ymax])
    # and it is genuinely wider than the grid's own rectangle, or the
    # envelope did not move anything and this proves nothing
    assert ef["raster_bounds_uv"] is not None
    assert (ef["frame_uv"][2] - ef["frame_uv"][0]) > (
        ef["raster_bounds_uv"][2] - ef["raster_bounds_uv"][0])


def test_the_annotation_extent_is_inside_the_captured_image(tmp_path):
    """The gate stated as the thing that actually matters: every corner of
    the annotation frame falls within the rectangle the capture spans.

    Against the shipped code the far corner falls outside, because the frame
    was re-centred on the model."""
    md = _capture(tmp_path, _raster_from(_bounds_result()))
    ef = md["export_frame"]
    fs = ef["frame_snapped_uv"]
    assert fs[0] <= ANNO_BOUNDS.xmin + 1e-9
    assert fs[1] <= ANNO_BOUNDS.ymin + 1e-9
    assert fs[2] >= ANNO_BOUNDS.xmax - 1e-9
    assert fs[3] >= ANNO_BOUNDS.ymax - 1e-9


def test_the_model_crop_is_not_narrowed_to_the_capped_window(tmp_path):
    """compute_model_crop clamps A into whatever rectangle it is handed.
    Handing it the capped window while B is the uncapped frame would narrow A
    back to that window -- reintroducing the re-centred rectangle through the
    back door on the exact views this fixes."""
    md = _capture(tmp_path, _raster_from(_bounds_result()))
    ef = md["export_frame"]
    # A is the model rectangle, intact, not the envelope window.
    assert ef["crop_snapped_uv"][0] <= MODEL_BOUNDS.xmin + 1e-9
    assert ef["crop_snapped_uv"][1] <= MODEL_BOUNDS.ymin + 1e-9
    assert ef["crop_snapped_uv"][2] >= MODEL_BOUNDS.xmax - 1e-9
    assert ef["crop_snapped_uv"][3] >= MODEL_BOUNDS.ymax - 1e-9
    assert ef["crop_is_frame"] is False


def test_the_narrowing_case_is_actually_reached(tmp_path):
    """Reachability control for the test above. If model_clip_bounds ever
    stops arriving, A collapses to B, crop_is_frame goes True and the
    narrowing assertions pass by testing nothing."""
    md = _capture(tmp_path, _raster_from(_bounds_result()))
    ef = md["export_frame"]
    assert ef["crop_is_frame"] is False
    assert ef["crop_px"][0] < ef["frame_px"][0]
    assert ef["crop_px"][1] < ef["frame_px"][1]


def test_without_a_model_clip_the_capture_spans_the_whole_frame(tmp_path):
    """The other half: a view with no model clip rectangle renders the frame
    itself. This is production-reachable -- model_bounds_uv is None whenever
    the crop-box branch did not run -- so it is pinned rather than assumed."""
    md = _capture(tmp_path, _raster_from(_bounds_result(), model_clip_bounds=None))
    ef = md["export_frame"]
    assert ef["crop_is_frame"] is True
    assert ef["crop_px"] == ef["frame_px"]
    assert ef["crop_offset_px"] == [0, 0]


def test_an_uncapped_view_still_reports_its_own_frame(tmp_path):
    """Control. On a view the envelope never touched, the capture must still
    be frame-sized and say which rectangle it used -- otherwise the assertions
    above would hold for a capture that always used the annotation frame
    whether or not one existed."""
    r = _bounds_result(max_W=10_000, max_H=10_000)
    assert r["anno_cap_envelope_applied"] is False
    md = _capture(tmp_path, _raster_from(r))
    ef = md["export_frame"]
    assert ef["anno_cap_envelope_applied"] is False
    assert ef["frame_uv"] == pytest.approx(
        [ANNO_BOUNDS.xmin, ANNO_BOUNDS.ymin, ANNO_BOUNDS.xmax, ANNO_BOUNDS.ymax])
