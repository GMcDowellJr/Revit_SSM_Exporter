"""Group A of RUN_..._SMALL_VIEWS.md: the ``annotation_lattice_mismatch``
arithmetic, COMPOSED from production rather than restated.

Six "(N) LEVEL x - Wall Types BP" FloorPlans at 1/48 (ids 4148625, 9468987,
9560929, 9594635, 9595813, 9596211) captured cleanly on 2025-08-23..09-06
and on the 11-01 batch run and failed every interactive run 09-13..10-25
with this fault. The capture raises it in two places, now one predicate
(``color_id_buffer.annotation_lattice_mismatch_faults``):

* the ANNOTATION export was accepted at a PixelSize other than the model
  pass's crop_px on the fitted axis -- which ``_export_tiff``'s dimension
  backoff produces when the annotation export's DERIVED axis comes back
  over the per-axis ceiling (FitToPage fits the drawn extent, annotation
  past the crop included), so it halves and re-exports;
* the MODEL export was accepted off its own request -- which
  ``_set_pixel_size_with_backoff`` produces when Revit rejects the
  PixelSize outright.

Each branch is run here through the real functions in the order the
capture runs them: ``resolution_contract.frame_export_geometry`` sizes
crop A, the annotation pass requests ``crop_px`` on the fitted axis,
``_export_tiff`` measures what a fake Revit rendered and backs off, and
the predicate decides. The numbers are a 1/48 plan at 150 dpi whose crop
A is at the 10000 px cap -- the SHAPE the six plans are inferred to have.
THE SIDECARS' OWN NUMBERS ARE NOT IN THIS FILE: the session had no access to
the run folders. When they arrive, ``RECORDED`` takes one entry per view
from ``frame.dim_check_attempts`` of both sidecars and the parametrised
test below runs them unchanged; until then it holds the inferred shape and
says so.

Mutations that must turn this red: making the backoff halve the request
instead of what Revit accepted (test_backoff_...); dropping either branch
of the predicate; comparing the file against the REQUEST rather than what
was accepted in ``_export_tiff``.
"""
import pytest

from vop_interwoven import color_id_buffer as cib
from vop_interwoven.resolution_contract import (
    DEFAULT_COLOR_ID_EXPORT_DPI, MAX_STAGE_A_AXIS_PX, frame_export_geometry,
)
from vop_interwoven.stage_a_registration import annotation_extent_prediction

from tests.test_stage_a_export_dimension_contract import (
    _DownshiftingExporter, _FakeExporter, _run_export,
)

SCALE = 48.0
DPI = DEFAULT_COLOR_ID_EXPORT_DPI                 # 150: 0.026667 ft/px requested
# Crop A of a 1/48 plan that the cap just reaches: 300 ft fitted horizontally
# would be 11250 px, so the cap holds it at 10000 and the lattice is 0.03 ft/px.
CROP_A = (0.0, 0.0, 300.0, 200.0)
# The annotation pass renders the DRAWN extent: here crop A plus annotation
# 60 ft above and below it (grid heads, elevation markers), 320 ft tall.
ANNO_EXTENT_FT = (300.0, 320.0)


def _model_lattice():
    geom = frame_export_geometry(CROP_A, CROP_A, SCALE, DPI, fit_direction="horizontal",
                                 max_axis_px=MAX_STAGE_A_AXIS_PX)
    assert geom["cap_applied"] is True and geom["requested_px"] == 10000
    assert geom["crop_px"] == (10000, 6667) or list(geom["crop_px"]) == [10000, 6667]
    assert abs(geom["achieved_fpp_ft"] - 0.03) < 1e-12
    return geom


def _anno_render(extent_ft):
    """What FitToPage hands back for the annotation pass at a fitted width
    ``px``: the drawn extent's aspect, whole pixels."""
    w_ft, h_ft = extent_ft
    return lambda px: (px, int(round(px * h_ft / w_ft)))


def test_branch_1_the_annotation_derived_axis_over_the_ceiling_halves_the_export_off_lattice(
        monkeypatch, tmp_path):
    geom = _model_lattice()
    required = int(geom["crop_px"][0])                 # 10000: the shared lattice
    exporter = _FakeExporter(_anno_render(ANNO_EXTENT_FT))
    accepted, report, diag = _run_export(monkeypatch, tmp_path, exporter, required,
                                         grid_axis_px=geom["min_axis_px"],
                                         max_axis_px=MAX_STAGE_A_AXIS_PX)
    # The first export is 10000 x 10667: the fitted axis is right and the
    # derived one is over the ceiling, so the check is a mismatch and the
    # request is halved; 5000 x 5333 passes and is ACCEPTED.
    assert [a["actual_h"] for a in report["attempts"]] == [10667, 5333]
    assert [a["dim_check"] for a in report["attempts"]] == ["mismatch", "pass"]
    assert accepted == 5000 and report["dim_check"] == "pass"
    faults = cib.annotation_lattice_mismatch_faults(
        accepted, required, "width", geom.get("model_accepted_px"), geom["requested_px"])
    assert [f for f, _ in faults] == ["annotation_lattice_mismatch"]
    assert "accepted at 5000 px on the width axis but the shared lattice requires 10000" in (
        faults[0][1])
    # The bbox union the capture now records BEFORE the model pass would
    # exceed the cap here too -- but the union is NOT the export's extent
    # (run 2025-09-06: unions 1.05-1.80x crop A, exports at crop A's size),
    # so this is a number to compare with the measured extent, not a cause.
    pred = annotation_extent_prediction(
        CROP_A, [(1, [0.0, -60.0, 300.0, 260.0])], geom["achieved_fpp_ft"],
        MAX_STAGE_A_AXIS_PX)
    assert pred["union_px"] == [10000.0, 10666.7] and pred["exceeds_cap"] is True


def test_branch_2_revit_rejecting_the_model_request_puts_the_model_off_lattice(
        monkeypatch, tmp_path):
    geom = _model_lattice()
    exporter = _DownshiftingExporter(lambda px: (px, int(round(px * 200.0 / 300.0))))
    accepted, report, _diag = _run_export(monkeypatch, tmp_path, exporter,
                                          geom["requested_px"],
                                          grid_axis_px=geom["min_axis_px"],
                                          max_axis_px=MAX_STAGE_A_AXIS_PX)
    # Revit took 5000 for the 10000 asked; the file is 5000 x 3333, which
    # matches what was ACCEPTED, so the model pass's own check PASSES.
    assert accepted == 5000 and report["dim_check"] == "pass"
    assert report["attempts"][0]["requested_px"] == 10000
    # ... and only the annotation pass, holding both halves, sees it.
    faults = cib.annotation_lattice_mismatch_faults(
        int(geom["crop_px"][0]), int(geom["crop_px"][0]), "width",
        model_accepted_px=accepted, model_requested_px=geom["requested_px"])
    assert [f for f, _ in faults] == ["annotation_lattice_mismatch"]
    assert "MODEL export was accepted at 5000 px where the lattice requires 10000" in (
        faults[0][1])


def test_control_an_annotation_extent_within_the_ceiling_stays_on_lattice(monkeypatch, tmp_path):
    geom = _model_lattice()
    required = int(geom["crop_px"][0])
    exporter = _FakeExporter(_anno_render((300.0, 290.0)))      # 10000 x 9667: under
    accepted, report, _diag = _run_export(monkeypatch, tmp_path, exporter, required,
                                          grid_axis_px=geom["min_axis_px"],
                                          max_axis_px=MAX_STAGE_A_AXIS_PX)
    assert accepted == required and len(report["attempts"]) == 1
    assert cib.annotation_lattice_mismatch_faults(accepted, required, "width",
                                                  required, required) == []


def test_sizing_crop_a_against_the_bbox_union_would_change_passing_views_and_is_not_the_fix():
    """Question (3) of group A, the arithmetic of the first proposal: size
    crop A inside the annotation bbox union so the UNION fits the cap. Run
    2025-09-06 showed the union is not the export's extent (five passing
    plans had unions over the cap and exported at crop A's size), so this
    sizing would have coarsened their lattice by 7-50 % while they needed
    nothing: it is NOT production and must not become it on that premise.
    Kept as the numbers of a rejected proposal."""
    union = (0.0, -60.0, 300.0, 260.0)
    geom = frame_export_geometry(union, CROP_A, SCALE, DPI, fit_direction="horizontal",
                                 max_axis_px=MAX_STAGE_A_AXIS_PX)
    # 300 x 320 ft under the cap: 9375 x 10000 px, 0.032 ft/px, and crop A
    # is 9375 x 6250 px on that lattice.
    assert geom["frame_px"] == (9375, 10000) or list(geom["frame_px"]) == [9375, 10000]
    assert list(geom["crop_px"]) == [9375, 6250]
    assert abs(geom["achieved_fpp_ft"] - 0.032) < 1e-12
    # The annotation pass would then request 9375 and FitToPage's 320 ft
    # derived axis is exactly 10000: no halving, no mismatch.
    anno_w, anno_h = _anno_render(ANNO_EXTENT_FT)(int(geom["crop_px"][0]))
    assert (anno_w, anno_h) == (9375, 10000) and max(anno_w, anno_h) <= MAX_STAGE_A_AXIS_PX
    assert cib.annotation_lattice_mismatch_faults(anno_w, int(geom["crop_px"][0]), "width",
                                                  9375, 9375) == []
    # The cost: 0.032 against 0.03 ft/px, a 6.7 % coarser lattice than crop A
    # alone -- on a view that, per 2025-09-06, exports at crop A's size
    # anyway. Breaking, for nothing.
    assert abs(geom["achieved_fpp_ft"] / 0.03 - 1.0667) < 1e-3


# One entry per view and run, from the sidecars: the model sidecar's
# frame.dim_check_attempts[0] gives (model_requested, model_accepted) and
# frame.crop_px the annotation's REQUIRED figure on the fitted axis; the
# annotation sidecar's frame.dim_check_attempts gives the annotation attempts
# (accepted_px, actual_w, actual_h). ``fault`` is what the run recorded.
#
# OBSERVED, run 2025-09-06__20261011T094136 (passing; exporter
# 1.2.0+src.d3ccc3e116cb; opened_from batch_capture, detached, all
# worksets): every plan accepted at its request, the annotation export at
# the model's size to the pixel, no fault. Five of the six are capped with
# the DERIVED axis at 9998-9999 of 10000 px: two pixels from the ceiling.
# LEVEL 5 is at scale 128 in that archive (48 on 2025-10-25).
# A FAILING run's entries are still to come (none received).
PASS_0906 = "2025-09-06__20261011T094136"
FAIL_1025 = "2025-10-25__20261012T122748"
PASS_1101 = "2025-11-01__20261013T111808"
# The plans the cap bound on the DERIVED axis, and how many px under the
# ceiling their annotation export came back (OBSERVED, frame.actual_h).
DERIVED_AXIS_HEADROOM_0906 = {4148625: 1, 9468987: 2, 9560929: 1, 9595813: 1}
RECORDED = [
    # view_id, run, model (requested, accepted), anno required, anno attempts, fault expected
    pytest.param(4148625, PASS_0906, (7476, 7476), 7476, [(7476, 7476, 9999)], False,
                 id="4148625-0906-pass"),
    pytest.param(9468987, PASS_0906, (9555, 9555), 9555, [(9555, 9555, 9998)], False,
                 id="9468987-0906-pass"),
    pytest.param(9560929, PASS_0906, (9340, 9340), 9340, [(9340, 9340, 9999)], False,
                 id="9560929-0906-pass"),
    pytest.param(9594635, PASS_0906, (10000, 10000), 10000, [(10000, 10000, 8323)], False,
                 id="9594635-0906-pass"),
    pytest.param(9595813, PASS_0906, (9050, 9050), 9050, [(9050, 9050, 9999)], False,
                 id="9595813-0906-pass"),
    pytest.param(9596211, PASS_0906, (2842, 2842), 2842, [(2842, 2842, 4988)], False,
                 id="9596211-0906-pass-scale-128"),
    # OBSERVED, run 2025-10-25__20261012T122748 (FAILING; exporter 1.2.0, the
    # same build and the same batch-opened detached environment as 09-06):
    # every plan's annotation export came back TALLER than the model's by
    # 1.2 % to 61 % -- section lines of the "Views" category past the crop
    # (absent from the 09-06 and 11-01 memberships) -- over the ceiling,
    # halved, accepted at half: branch 1, each one.
    pytest.param(4148625, FAIL_1025, (7476, 7476), 7476,
                 [(7476, 7476, 10512), (3738, 3738, 5256)], True, id="4148625-1025-fail"),
    pytest.param(9468987, FAIL_1025, (9555, 9555), 9555,
                 [(9555, 9555, 10345), (4777, 4777, 5172)], True, id="9468987-1025-fail"),
    pytest.param(9560929, FAIL_1025, (9340, 9340), 9340,
                 [(9340, 9340, 10121), (4670, 4670, 5060)], True, id="9560929-1025-fail"),
    pytest.param(9594635, FAIL_1025, (10000, 10000), 10000,
                 [(10000, 10000, 13575), (5000, 5000, 6787)], True, id="9594635-1025-fail"),
    pytest.param(9595813, FAIL_1025, (9050, 9050), 9050,
                 [(9050, 9050, 10305), (4525, 4525, 5152)], True, id="9595813-1025-fail"),
    pytest.param(9596211, FAIL_1025, (5697, 5697), 5697,
                 [(5697, 5697, 10273), (2848, 2848, 5135)], True, id="9596211-1025-fail-scale-48"),
    # OBSERVED, run 2025-11-01__20261013T111808 (passing; exporter 1.3.0,
    # opened_from null: the INTERACTIVE one). LEVEL 3 and LEVEL 4 passed
    # with the annotation's derived axis OFF the model's (8448 vs 8425,
    # 9852 vs 9999): under the ceiling, so no halving and no fault; the
    # registration tool measures that scale from the ticks.
    pytest.param(4148625, PASS_1101, (7476, 7476), 7476, [(7476, 7476, 9999)], False,
                 id="4148625-1101-pass"),
    pytest.param(9468987, PASS_1101, (9555, 9555), 9555, [(9555, 9555, 9998)], False,
                 id="9468987-1101-pass"),
    pytest.param(9560929, PASS_1101, (9340, 9340), 9340, [(9340, 9340, 9999)], False,
                 id="9560929-1101-pass"),
    pytest.param(9594635, PASS_1101, (10000, 10000), 10000, [(10000, 10000, 8448)], False,
                 id="9594635-1101-pass-anno-off-lattice-0.3pct"),
    pytest.param(9595813, PASS_1101, (9050, 9050), 9050, [(9050, 9050, 9852)], False,
                 id="9595813-1101-pass-anno-off-lattice-1.5pct"),
    pytest.param(9596211, PASS_1101, (5697, 5697), 5697, [(5697, 5697, 9999)], False,
                 id="9596211-1101-pass"),
]
# The model's derived axis in the two 2025-10/11 runs (same crops), to say
# by how much the failing annotation export overran it.
MODEL_DERIVED_1025 = {4148625: 9999, 9468987: 9998, 9560929: 9999, 9594635: 8425,
                      9595813: 9999, 9596211: 9999}


@pytest.mark.parametrize("view_id,run,model,anno_required,anno_attempts,fault", RECORDED)
def test_recorded_numbers_reproduce_the_outcome(view_id, run, model, anno_required,
                                                anno_attempts, fault):
    anno_accepted = anno_attempts[-1][0]
    faults = cib.annotation_lattice_mismatch_faults(
        anno_accepted, anno_required, "width", model[1], model[0])
    assert bool(faults) is fault, (view_id, run, faults)
    over = [a for a in anno_attempts if max(a[1], a[2]) > MAX_STAGE_A_AXIS_PX]
    assert bool(over) is fault, (view_id, run, "an attempt over the ceiling is branch 1")
    # Four of the 2025-09-06 plans sit 1-2 px under the ceiling on the
    # derived axis: 0.03-0.06 ft of model space from a dimension mismatch.
    if run == PASS_0906 and view_id in DERIVED_AXIS_HEADROOM_0906:
        assert MAX_STAGE_A_AXIS_PX - anno_attempts[0][2] == DERIVED_AXIS_HEADROOM_0906[view_id]
    if run == FAIL_1025:
        # The failing export is the model's request with a taller derived
        # axis: the halving then lands exactly at half the lattice.
        first, second = anno_attempts
        assert first[0] == model[0] and second[0] == model[0] // 2
        assert first[2] > MODEL_DERIVED_1025[view_id] and first[2] > MAX_STAGE_A_AXIS_PX
        assert second[2] <= MAX_STAGE_A_AXIS_PX


@pytest.mark.parametrize("view_id,run,model,anno_required,anno_attempts,fault", RECORDED)
def test_one_lattice_for_both_passes_is_cap_axes_on_the_measured_annotation_extent(
        view_id, run, model, anno_required, anno_attempts, fault):
    """Question (3), the fix that holds: when the annotation export's first
    attempt overruns the ceiling, re-size BOTH passes with cap_axes on the
    model's request and the annotation's MEASURED derived axis, and
    re-export both at that request. The lattice coarsens by the overrun
    (3.5 % for LEVEL 1, 61 % for LEVEL 3) and the two passes share it; a
    view whose annotation export fits the cap (every passing entry) keeps
    its request unchanged, so nothing that captures today changes. The
    arithmetic only: the re-export loop is not production (note §1.4)."""
    from vop_interwoven.resolution_contract import cap_axes
    first = anno_attempts[0]
    resized = cap_axes(model[0], first[2], MAX_STAGE_A_AXIS_PX)
    if not fault:
        assert resized["accepted_px"] == model[0] and resized["cap_applied"] is False
        return
    assert resized["cap_applied"] is True and resized["accepted_px"] < model[0]
    # Both passes at the resized request: the annotation's derived axis
    # scales with its fitted one and lands at the ceiling.
    # cap_axes floors the request, so the derived axis lands 1-2 px under.
    anno_h = int(resized["accepted_px"] * first[2] / float(first[1]))
    assert anno_h <= MAX_STAGE_A_AXIS_PX and MAX_STAGE_A_AXIS_PX - anno_h <= 2
    assert cib.annotation_lattice_mismatch_faults(
        resized["accepted_px"], resized["accepted_px"], "width",
        resized["accepted_px"], resized["accepted_px"]) == []
    coarsening = model[0] / float(resized["accepted_px"])
    assert 1.0 < coarsening < 1.7, (view_id, coarsening)
