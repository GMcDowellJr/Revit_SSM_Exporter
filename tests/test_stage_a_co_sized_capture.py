"""fix(capture)!: the registered capture CO-SIZES both passes when the
annotation export overruns the ceiling (group A of RUN_2025-02-03_to_
2025-11-01_SERIES_SMALL_VIEWS.md), DRIVEN through production's
``export_registered_stage_a_view`` on the fake DB.

The fake Revit (tests/test_stage_a_lattice_overrun_probe.py) renders the
annotation export's drawn extent: crop A plus a section marker 21 ft past
it, 10725 px tall at the model's 9750 px request -- over the 10000 ceiling,
as the six Wall Types BP plans were on 09-13..10-25. Asserted on the FILES:
both passes at one request, the annotation under the ceiling, no lattice
fault, the co-sizing record in both sidecars, the ticks sized on the
co-sized lattice, and a view that fits captured once at its own size.

Mutations wired to these tests: dropping the second attempt (the overrun
test goes red: the lattice fault stays); sizing the second attempt's model
pass but not its ticks (``mark_fpp_ft`` without ``fit_px_limit``: the tick
test goes red); re-sizing whenever the annotation failed, not only on an
overrun (the branch-2 and control tests go red); not rolling back the first
attempt (its ticks survive into the second export: marks_in_doc goes red).
"""
import json
import math

import pytest
from hypothesis import given, settings, strategies as st

import vop_interwoven.stage_a_registration as registration
from vop_interwoven.resolution_contract import (
    MAX_STAGE_A_AXIS_PX, co_sized_request, frame_export_geometry,
)

from tests import test_probe_anno_pass_run_variant as world
from tests.test_stage_a_lattice_overrun_probe import run_capture

R = 9750                       # the model's request at 1300 dpi on the fake crop
MODEL_H = 7312
ANNO_H = 10725                 # the first annotation attempt: over the ceiling
CO_SIZED = co_sized_request(R, R, [{"requested_px": R, "accepted_px": R, "actual_w": R,
                                    "actual_h": ANNO_H, "dim_check": "mismatch"}])


def _file(path):
    with open(path) as handle:
        return json.load(handle)


def _faults(path):
    return [f["fault"] for f in _file(path)["capture_integrity"]["capture_faults"]]


def test_the_co_sized_request_is_the_one_the_note_tabled():
    assert CO_SIZED["state"] == "applies"
    assert CO_SIZED["fit_px"] == 9090                    # floor(9750 * 10000 / 10726)
    assert CO_SIZED["predicted_anno_derived_px"] <= MAX_STAGE_A_AXIS_PX


def test_an_overrunning_view_is_captured_again_with_both_passes_on_one_lattice(
        tmp_path, monkeypatch):
    out, view, doc, seen = run_capture(tmp_path, monkeypatch)
    passes = [(s["pass"], s["px"], s["h"]) for s in seen]
    # Attempt 1: the shipped sequence, the overrun and its halving; attempt 2:
    # both passes at the co-sized request, the annotation under the ceiling.
    assert passes[:3] == [("model", R, MODEL_H), ("annotation", R, ANNO_H),
                          ("annotation", R // 2, int(math.floor(R // 2 * 66.0 / 60.0)))]
    fit = CO_SIZED["fit_px"]
    assert passes[3:] == [("model", fit, int(math.floor(fit * 45.0 / 60.0))),
                          ("annotation", fit, int(math.floor(fit * 66.0 / 60.0)))]
    assert max(h for _p, _px, h in passes[3:]) <= MAX_STAGE_A_AXIS_PX
    # The FILES: no lattice fault, both at the co-sized request.
    assert _faults(out["annotation_sidecar_path"]) == []
    model = _file(out["sidecar_path"])
    anno = _file(out["annotation_sidecar_path"])
    assert model["frame"]["pixel_size"] == fit == anno["frame"]["pixel_size"]
    assert model["frame"]["co_sized_fit_px"] == fit and model["frame"]["cap_applied"] is True
    assert out["success"] is True and out["registration"]["faults"] == []
    for path in (out["sidecar_path"], out["annotation_sidecar_path"]):
        co = _file(path)["registration_marks"]["co_sizing"]
        assert co["state"] == "applies" and co["attempts"] == 2 and co["fit_px"] == fit
        assert co["first_attempt"]["annotation_failure_reason"] == "annotation_lattice_mismatch"
        assert co["first_attempt"]["annotation_frame"]["dim_check_attempts"][0]["actual_h"] \
            == ANNO_H
        assert co["second_attempt"]["annotation_failure_reason"] is None
        assert abs(co["coarsening"] - R / float(fit)) < 1e-6


def test_the_second_attempt_starts_from_the_view_the_first_one_found(tmp_path, monkeypatch):
    out, view, doc, seen = run_capture(tmp_path, monkeypatch)
    # Each export sees ITS attempt's twelve ticks only: the first attempt's
    # were rolled back with it before the second drew its own.
    assert [s["marks_in_doc"] for s in seen] == [12] * len(seen)
    reg = out["registration"]
    first_ids = reg["co_sizing"]["first_attempt"]["mark_ids"]
    assert len(first_ids) == 12
    # None is left after the outer rollback, and the read-back looked at both
    # attempts' ticks.
    assert reg["restore"]["marks_still_in_project"] == []
    assert [i for i in doc._by_id if i > world.MARK_ID_BASE] == []
    assert all(v["status"] == "restored" for v in reg["restore"]["view_state"].values())
    assert reg["restore"]["element_overrides"]["left_behind"] == []


def test_the_ticks_are_sized_on_the_co_sized_lattice(tmp_path, monkeypatch):
    out, view, doc, seen = run_capture(tmp_path, monkeypatch)
    frame = _file(out["sidecar_path"])["frame"]
    layout = out["registration"]["marks"]["layout"]
    assert layout["fpp_ft"] == frame["achieved_fpp_ft"]
    fpp = frame["achieved_fpp_ft"]
    arms = []
    for seg in out["registration"]["marks"]["created"]:
        lo, hi = sorted(seg["span_uv"])
        arms.append((hi - lo) / fpp)
    assert min(arms) >= registration.MARK_MIN_ARM_PX - 1e-6, arms


def test_control_a_view_that_fits_is_captured_once_at_its_own_size(tmp_path, monkeypatch):
    out, view, doc, seen = run_capture(tmp_path, monkeypatch, marker=False)
    assert [(s["pass"], s["px"], s["h"]) for s in seen] == [
        ("model", R, MODEL_H), ("annotation", R, MODEL_H)]
    model = _file(out["sidecar_path"])
    assert model["frame"]["pixel_size"] == R and "co_sized_fit_px" not in model["frame"]
    assert model["registration_marks"]["co_sizing"] is None
    assert model["registration_marks"]["timings_ms"]["co_sizing_first_attempt"] == 0.0
    assert out["success"] is True


def test_a_second_overrun_keeps_its_fault_and_there_is_no_third_attempt(tmp_path, monkeypatch):
    # 500 px of the drawn extent do not scale with PixelSize: the co-sized
    # request still overruns, and the capture says so rather than guessing on.
    out, view, doc, seen = run_capture(tmp_path, monkeypatch, extra_h_px=500)
    assert [s["pass"] for s in seen].count("model") == 2
    assert "annotation_lattice_mismatch" in _faults(out["annotation_sidecar_path"])
    co = _file(out["sidecar_path"])["registration_marks"]["co_sizing"]
    assert co["attempts"] == 2
    assert co["second_attempt"]["annotation_failure_reason"] == "annotation_lattice_mismatch"
    assert out["annotation_pass_success"] is False and out["registration_success"] is False


# --- the pure decision -------------------------------------------------------

def _first(w, h, req=R, acc=R, check="mismatch"):
    return [{"requested_px": req, "accepted_px": acc, "actual_w": w, "actual_h": h,
             "dim_check": check}]


def test_only_an_overrun_on_the_derived_axis_is_re_sized():
    assert co_sized_request(R, R, _first(R, MODEL_H, check="pass"))["state"] == "not_needed"
    # Branch 2: Revit accepted the MODEL export off its request.
    assert co_sized_request(R, R // 2, _first(R, ANNO_H))["state"] == "not_applicable"
    # The fitted axis itself came back wrong.
    assert co_sized_request(R, R, _first(R - 7, ANNO_H))["state"] == "not_applicable"
    # A mismatch under the ceiling is another failure.
    assert co_sized_request(R, R, _first(R, 9000))["state"] == "not_applicable"
    assert co_sized_request(R, R, [])["state"] == "not_applicable"
    v = co_sized_request(R, R, [{"requested_px": R, "accepted_px": R, "actual_w": ANNO_H,
                                 "actual_h": R, "dim_check": "mismatch"}],
                         fit_direction="vertical")
    assert v["state"] == "applies" and v["fit_px"] == CO_SIZED["fit_px"]


@settings(max_examples=3000, deadline=None)
@given(requested=st.integers(min_value=2000, max_value=MAX_STAGE_A_AXIS_PX),
       aspect=st.floats(min_value=0.2, max_value=9.9),
       rounding=st.sampled_from(["floor", "half_up"]))
def test_the_co_sized_annotation_export_never_lands_over_the_ceiling(requested, aspect,
                                                                     rounding):
    """Whatever Revit's rounding of the derived axis (floored or half-up,
    cap_axes' D5 note), the annotation export at the co-sized request is
    within the ceiling: the measured axis bounds the true extent by +1."""
    rnd = (lambda x: int(math.floor(x))) if rounding == "floor" else (
        lambda x: int(math.floor(x + 0.5)))
    derived = rnd(requested * aspect)
    co = co_sized_request(requested, requested,
                          _first(requested, derived, req=requested, acc=requested))
    if derived <= MAX_STAGE_A_AXIS_PX:
        assert co["state"] == "not_applicable"
        return
    assert co["state"] == "applies"
    assert rnd(co["fit_px"] * aspect) <= MAX_STAGE_A_AXIS_PX
    # And the model, on crop A's own (shallower) aspect, at that request.
    geom = frame_export_geometry((0.0, 0.0, 100.0, 50.0), (0.0, 0.0, 100.0, 50.0), 96.0,
                                 1.0, fit_px_limit=co["fit_px"])
    assert geom["requested_px"] <= co["fit_px"]


def test_fit_px_limit_lowers_the_lattice_and_says_so_and_none_changes_nothing():
    crop = (0.0, 0.0, 60.0, 45.0)
    shipped = frame_export_geometry(crop, crop, 96.0, 1300.0)
    unlimited = frame_export_geometry(crop, crop, 96.0, 1300.0, fit_px_limit=None)
    assert shipped == unlimited and shipped["requested_px"] == R
    assert shipped["fit_px_limited"] is False and shipped["cap_applied"] is False
    above = frame_export_geometry(crop, crop, 96.0, 1300.0, fit_px_limit=R + 5)
    assert above["requested_px"] == R and above["fit_px_limited"] is False
    limited = frame_export_geometry(crop, crop, 96.0, 1300.0, fit_px_limit=9090)
    assert limited["requested_px"] == 9090 and limited["fit_px_limited"] is True
    assert limited["cap_applied"] is True
    assert limited["achieved_fpp_ft"] == pytest.approx(60.0 / 9090)
