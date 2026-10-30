"""The export-cap probe's pure parts and its offline analyzer
(tests/dynamo/probe_export_cap.py, tools/analyze_export_cap_probe.py).

The probe needs Revit. Tested here: the plan, the job summary read from
sidecars, the document check, the statistics, and the analyzer end to end on
synthetic runs built by tests/stage_a_bench.py (real TIFFs, real sidecars,
real run_meta), registered and gridded by the UNCHANGED production tools.
Analyzer assertions read the analysis JSON the tool WROTE (CLAUDE.md, defect
class 4). Expected numbers are arithmetic on the fixture.
"""
import json
import shutil

import numpy as np
import pytest

from tests import stage_a_bench
from tests.dynamo import probe_export_cap as probe
from tools import analyze_export_cap_probe as aec


# --- the probe's pure parts ------------------------------------------------------

def test_plan_runs_every_view_cap_repeat_once_shuffled_per_view_and_seeded():
    plan = probe.build_plan([1, 2], [10000, 7000, 5000, 3500], 3, 5)
    assert len(plan) == 2 * 4 * 3
    assert len(set((j["view_id"], j["cap"], j["repeat"]) for j in plan)) == len(plan)
    assert [j["index"] for j in plan] == list(range(len(plan)))
    # Each view's block is contiguous and holds all four caps.
    blocks = [plan[i:i + 4] for i in range(0, len(plan), 4)]
    assert all(len(set(j["view_id"] for j in b)) == 1 for b in blocks)
    assert all(sorted(j["cap"] for j in b) == [3500, 5000, 7000, 10000] for b in blocks)
    assert all([j["position_in_view_block"] for j in b] == [0, 1, 2, 3] for b in blocks)
    orders = [tuple(j["cap"] for j in b) for b in blocks]
    assert len(set(orders)) > 1                       # not one fixed order
    assert plan == probe.build_plan([1, 2], [10000, 7000, 5000, 3500], 3, 5)
    assert plan != probe.build_plan([1, 2], [10000, 7000, 5000, 3500], 3, 6)


def test_caps_are_bounded_by_the_ceiling():
    assert probe.parse_caps(None, 10000) == [10000, 7000, 5000, 3500]
    assert probe.parse_caps("5000, 7000", 10000) == [7000, 5000]
    with pytest.raises(ValueError):
        probe.parse_caps([12000], 10000)
    with pytest.raises(ValueError):
        probe.parse_options({"bound": 1})


def test_summarize_job_reads_the_capture_timings_and_restore_faults():
    model = {"frame": {"actual_w": 9151, "actual_h": 9999, "max_axis_px": 10000,
                       "cap_applied": True, "dim_check_attempts": [{}, {}]},
             "registration_marks": {"timings_ms": {"model_export": 812.5,
                                                   "annotation_export": 400.0,
                                                   "total": 9000.0},
                                    "faults": [{"fault": "view_state_not_restored"},
                                               {"fault": "annotation_lattice_mismatch"}]},
             "capture_integrity": {"rolled_back": True, "marks_still_in_project": 0}}
    s = probe.summarize_job(model, None, {"capture_status": "success"})
    assert s["megapixels"] == round(9151 * 9999 / 1e6, 6)
    assert s["model_export_ms"] == 812.5 and s["attempts"] == 2
    assert s["restore_faults"] == ["view_state_not_restored"]
    assert s["faults"] == ["annotation_lattice_mismatch", "view_state_not_restored"]
    empty = probe.summarize_job(None, None)
    assert empty["megapixels"] is None and empty["model_export_ms"] is None


def test_document_check_names_what_moved():
    a = {"element_count": 10, "type_count": 5, "is_modified": False}
    assert probe.document_check_verdict(a, dict(a))["unchanged"] is True
    assert probe.document_check_verdict(a, dict(a, element_count=11))["changed"] == [
        "element_count"]


# --- the analyzer's statistics -------------------------------------------------

def test_ols_recovers_a_line_and_reports_residuals():
    fit = aec.ols([(1, 5, "a"), (2, 8, "b"), (3, 11, "c"), (4, 15, "d")])
    # Least squares: mean x 2.5, mean y 9.75, Sxy 16.5, Sxx 5 -> slope 3.3,
    # intercept 9.75 - 3.3 * 2.5 = 1.5.
    assert fit["fit"]["slope_per_x"] == pytest.approx(3.3)
    assert fit["fit"]["intercept"] == pytest.approx(1.5)
    assert fit["residuals"]["d"] == pytest.approx(15 - (1.5 + 3.3 * 4), abs=1e-3)
    assert aec.ols([(1, 5, "a"), (1, 6, "b")])["fit"] is None


def test_smallest_cap_within_stops_at_the_first_cap_over_the_bound():
    curve = [{"cap": 10000, "max_abs_delta_fill": 0.0},
             {"cap": 7000, "max_abs_delta_fill": 0.004},
             {"cap": 5000, "max_abs_delta_fill": 0.02},
             {"cap": 3500, "max_abs_delta_fill": 0.001}]   # back inside: not counted
    assert aec.smallest_cap_within(curve, 0.01) == 7000
    assert aec.smallest_cap_within(curve, 0.05) == 3500
    assert aec.smallest_cap_within([{"cap": 10000, "max_abs_delta_fill": 0.1}], 0.01) is None


def test_steps_rising_and_cell_change():
    assert aec.steps_rising([0.10, 0.12, 0.11, 0.15]) == (2, 3)
    assert aec.cell_change({1, 2, 3}, {2, 3, 4, 5}) == {
        "gained": 2, "lost": 1, "jaccard": 2 / 5.0}


def test_grid_metrics_counts_inside_crop_a_and_per_category():
    record = {"grid": {"px_per_cell_u": 4.0, "px_per_cell_v": 3.0, "cells_w": 3,
                       "cells_h": 2, "i_range": [-1, 2], "j_range": [0, 2]},
              "ec_keys": [{"layer": "model", "category": "Walls"},
                          {"layer": "annotation", "category": "Text Notes"},
                          {"layer": "model", "category": "Doors"}],
              "npz_bytes": 9}
    occ = np.array([[1, 3, 0], [2, 1, 1]], dtype=np.uint8)
    inside = np.array([[True, True, True], [True, True, False]])
    npz = {"occupancy": occ, "inside_crop_a": inside,
           "model_host": np.array([[4, 2, 0], [0, 6, 6]]),
           "crop_a_px": np.full((2, 3), 12),
           "ec_key": np.array([0, 0, 1, 2, 2]), "ec_i": np.array([-1, 0, 0, 1, 0]),
           "ec_j": np.array([0, 0, 0, 1, 1]), "ec_ink_px": np.array([3, 1, 5, 2, 0])}
    m, cells = aec.grid_metrics(record, npz)
    # Model ink inside: (0,0)=1, (0,1)=3, (1,1)=1; (1,2)=1 is outside.
    assert m["model_ink_cells"] == 3 and m["inside_cells"] == 5
    assert m["fill_fraction"] == pytest.approx(3 / 5.0)
    assert m["anno_ink_cells"] == 2                    # (0,1)=3 and (1,0)=2
    assert m["px_per_cell"] == 3.0
    assert m["model_px_fraction"] == pytest.approx((4 + 2 + 0 + 0 + 6) / (12.0 * 5))
    # Walls: cells (j0,i-1) and (j0,i0). Doors: its only ink cell (j1,i1) is
    # OUTSIDE crop A, so it is reported with 0 cells rather than dropped.
    assert m["per_category_ink_cells"] == {"Walls": 2, "Doors": 0}
    # Signed (i, j): column 0 is i = -1, row 0 is j = 0.
    assert cells == {(-1, 0), (0, 0), (0, 1)}


# --- end to end on synthetic runs --------------------------------------------------

# The same MODEL scene at two resolutions: write_view draws its rectangles in
# feet from (seed, view), its extent in feet is size / px_per_ft, and with the
# element counts fixed (they default to a function of the pixel area) scaling
# size and px_per_ft together keeps the model scene and halves the pixels.
# (The annotation scene still differs: its speckle draws per pixel.) Cells are
# 1 ft (1/8" at 1/96), so 8 and 4 px per cell -- both above the grid's 2.
FULL = (800, 600, 8.0)
HALF = (400, 300, 4.0)


def _write_run(run, size):
    """stage_a_bench.write_run for one view, with the element counts fixed."""
    cap = run / "color_id_buffer"
    cap.mkdir(parents=True)
    stage_a_bench.write_view(cap, 1, size[:2], seed=3, model_elements=30,
                             anno_elements=15, px_per_ft=size[2])
    (run / "run_meta.json").write_text(json.dumps({
        "schema": "vop.run_meta.v1", "run_id": run.name, "finalized": True,
        "views_requested": [1], "date": "2026-10-24",
        "views": [{"view_id": 1, "capture_status": "success"}],
        "config": {"cell_size_paper_in": stage_a_bench.CELL_IN}}))


def _job(probe_dir, label, view, cap, repeat, size, ms, position):
    run = probe_dir / "jobs" / label / "2026-10-24__RUN_{0}".format(label)
    shutil.rmtree(run, ignore_errors=True)
    _write_run(run, size)
    w, h = size[:2]
    return {"index": 0, "label": label, "view_id": view, "cap": cap, "repeat": repeat,
            "position_in_view_block": position, "run_dir": "C:\\elsewhere\\" + run.name,
            "actual_w": w, "actual_h": h, "megapixels": w * h / 1e6, "attempts": 1,
            "model_export_ms": ms, "annotation_export_ms": ms / 2.0,
            "capture_total_ms": 10 * ms, "model_tiff_bytes": 100}


@pytest.fixture
def analysis(tmp_path):
    pdir = tmp_path / "probe_export_cap_X"
    pdir.mkdir()
    # export ms = 100 + 200 x Mpx exactly, except the second repeat at 10000
    # is 30 ms slower and came first in its block (the warm-up).
    jobs = [_job(pdir, "v1_c10000_r1", 1, 10000, 1, FULL, 100 + 200 * 0.48, 1),
            _job(pdir, "v1_c5000_r1", 1, 5000, 1, HALF, 100 + 200 * 0.12, 0),
            _job(pdir, "v1_c10000_r2", 1, 10000, 2, FULL, 100 + 200 * 0.48 + 30, 0),
            _job(pdir, "v1_c5000_r2", 1, 5000, 2, HALF, 100 + 200 * 0.12, 1)]
    for i, j in enumerate(jobs):
        j["index"] = i
    (pdir / "probe_export_cap_20261024T000000.json").write_text(json.dumps({
        "schema": probe.SCHEMA, "finalized": True, "RESTORE_SUMMARY": "DOCUMENT UNCHANGED",
        "inputs": {"capped_view_ids": [1], "control_view_ids": [], "caps": [10000, 5000]},
        "jobs": jobs}))
    assert aec.main([str(pdir)]) == 0
    return json.loads((pdir / aec.ANALYSIS_NAME).read_text())


def test_end_to_end_grids_the_first_repeat_with_the_production_tools(analysis):
    grid = analysis["grid"]
    assert sorted(grid) == ["v1_c10000_r1", "v1_c5000_r1"]      # first repeats only
    full, half = grid["v1_c10000_r1"], grid["v1_c5000_r1"]
    assert full["grid_status"] == "value" and half["grid_status"] == "value"
    assert full["register_status"] == "registered"
    assert full["px_per_cell"] == pytest.approx(8.0, rel=0.02)
    assert half["px_per_cell"] == pytest.approx(4.0, rel=0.02)
    assert full["inside_cells"] == half["inside_cells"]          # cells are in feet
    assert full["model_ink_cells"] > 0


def test_end_to_end_same_scene_overlaps_at_half_resolution(analysis):
    # A control on the fixture: the two resolutions draw ONE model scene, so
    # most model-ink cells are shared. (Different scenes overlap ~0.04.)
    low = [e for e in analysis["grid_vs_reference"] if e["cap"] == 5000][0]
    assert low["cells"]["jaccard"] > 0.7


def test_end_to_end_reference_curve_and_bias(analysis):
    ref = [e for e in analysis["grid_vs_reference"] if e["cap"] == 10000][0]
    assert ref["delta_fill"] == 0 and ref["cells"]["jaccard"] == 1.0
    low = [e for e in analysis["grid_vs_reference"] if e["cap"] == 5000][0]
    full, half = analysis["grid"]["v1_c10000_r1"], analysis["grid"]["v1_c5000_r1"]
    assert low["delta_fill"] == pytest.approx(half["fill_fraction"] - full["fill_fraction"])
    assert [r["cap"] for r in analysis["fill_curve"]] == [10000, 5000]
    assert analysis["fill_curve"][1]["max_abs_delta_fill"] == pytest.approx(
        abs(low["delta_fill"]))
    bias = analysis["thin_line_bias"]["1"]
    assert bias["steps_down_in_resolution"] == 1
    assert bias["steps_raising_fill"] == (1 if low["delta_fill"] > 0 else 0)


def test_end_to_end_export_fit_timing_and_repeat_hashes(analysis):
    fit = analysis["export_fit"]
    assert fit["n"] == 4
    # Least squares through 0.48 -> {196, 226} and 0.12 -> {124, 124}.
    assert fit["fit"]["slope_per_x"] == pytest.approx((211 - 124) / (0.48 - 0.12))
    t = dict(((r["view_id"], r["cap"]), r) for r in analysis["timing"])
    assert t[(1, 10000)]["model_export_ms"]["spread"] == pytest.approx(30)
    # The two repeats of each cap are byte-identical files (same seed).
    assert t[(1, 10000)]["repeats_hashed"] == 2
    assert t[(1, 10000)]["distinct_model_tiff_hashes"] == 1
    assert analysis["warmup_by_position"]["0"]["mean_ms_above_median"] == pytest.approx(7.5)
    assert len(analysis["probe_json_sha256"]) == 64


def test_bound_is_only_answered_when_asked(tmp_path, analysis):
    assert "bound_query" not in analysis


def test_unfinished_probe_is_refused(tmp_path):
    (tmp_path / "probe_export_cap_1.json").write_text(json.dumps({"finalized": False}))
    assert aec.main([str(tmp_path)]) == 2
    assert not (tmp_path / aec.ANALYSIS_NAME).exists()


def test_a_retried_export_is_left_out_of_the_fit_and_named(tmp_path):
    # Grid and hash stubbed out (both are injectable): this is the fit alone.
    jobs = [{"index": i, "label": "v1_c{0}_r1".format(cap), "view_id": 1, "cap": cap,
             "repeat": 1, "position_in_view_block": i, "megapixels": mpx,
             "attempts": attempts, "model_export_ms": ms}
            for i, (cap, mpx, attempts, ms) in enumerate(
                [(10000, 1.0, 1, 300.0), (7000, 0.5, 1, 200.0), (5000, 0.25, 2, 9999.0)])]
    (tmp_path / "probe_export_cap_1.json").write_text(json.dumps({
        "finalized": True, "inputs": {"capped_view_ids": [1]}, "jobs": jobs}))
    _path, record = aec.analyze(tmp_path, gridder=lambda d, j: ({"label": j["label"]}, None),
                                hasher=lambda d, j: None)
    assert record["export_fit"]["excluded_retried"] == ["v1_c5000_r1"]
    assert record["export_fit"]["n"] == 2
    assert record["export_fit"]["fit"]["slope_per_x"] == pytest.approx(200.0)
    # Nothing gridded: every cap is listed, and none claims a measurement.
    assert [r["cap"] for r in record["fill_curve"]] == [10000, 7000, 5000]
    assert all(r["views"] == 0 and r["max_abs_delta_fill"] is None
               for r in record["fill_curve"])


def test_repeats_are_compared_by_the_probes_recorded_hash_when_images_were_deleted(tmp_path):
    # The probe hashes every image, then deletes a later repeat's TIFFs: no
    # run directory exists here, so only the recorded hashes can be read.
    jobs = [{"index": r, "label": "v1_c10000_r{0}".format(r + 1), "view_id": 1,
             "cap": 10000, "repeat": r + 1, "position_in_view_block": 0, "attempts": 1,
             "megapixels": 1.0, "model_export_ms": 100.0, "model_tiff_sha256": h}
            for r, h in enumerate(["aa", "aa", "bb"])]
    (tmp_path / "probe_export_cap_1.json").write_text(json.dumps({
        "finalized": True, "inputs": {"capped_view_ids": [1]}, "jobs": jobs}))
    _path, record = aec.analyze(tmp_path, gridder=lambda d, j: ({"label": j["label"]}, None))
    row = record["timing"][0]
    assert row["repeats_hashed"] == 3 and row["distinct_model_tiff_hashes"] == 2


def test_a_grid_refusal_is_the_caps_result_and_is_not_retried(tmp_path):
    # Run 20261026T101435: the first repeat at 7000 px was refused (1.93 px per
    # cell), and the analyzer tried repeats 2 and 3, whose TIFFs the probe had
    # deleted. A refusal settles the cap; the curve counts it.
    jobs = [{"index": i, "label": "v1_c{0}_r{1}".format(cap, r), "view_id": 1, "cap": cap,
             "repeat": r, "position_in_view_block": 0, "attempts": 1, "megapixels": 1.0,
             "model_export_ms": 1.0}
            for i, (cap, r) in enumerate([(10000, 1), (7000, 1), (10000, 2), (7000, 2)])]
    (tmp_path / "probe_export_cap_1.json").write_text(json.dumps({
        "finalized": True, "inputs": {"capped_view_ids": [1]}, "jobs": jobs}))
    called = []

    def gridder(_d, job):
        called.append(job["label"])
        if job["cap"] == 7000:
            return {"label": job["label"], "grid_status": "refused",
                    "grid_refusal": "1.93 px per cell is below the 2.0 px floor"}, None
        return {"label": job["label"], "grid_status": "value", "fill_fraction": 0.1,
                "px_per_cell": 2.75, "model_px_fraction": 0.1, "anno_ink_cells": 0,
                "per_category_fill": {}}, {(0, 0)}
    _path, record = aec.analyze(tmp_path, gridder=gridder, hasher=lambda d, j: None)
    assert called == ["v1_c10000_r1", "v1_c7000_r1"]
    curve = dict((r["cap"], r) for r in record["fill_curve"])
    assert curve[7000]["views"] == 0 and curve[7000]["views_refused"] == 1
    assert curve[7000]["max_abs_delta_fill"] is None
    assert curve[10000]["views_refused"] == 0
    assert aec.smallest_cap_within(record["fill_curve"], 1.0) == 10000


def test_smallest_cap_within_stops_at_a_cap_with_a_refused_view():
    curve = [{"cap": 10000, "max_abs_delta_fill": 0.0, "views_refused": 0},
             {"cap": 7000, "max_abs_delta_fill": 0.0, "views_refused": 3}]
    assert aec.smallest_cap_within(curve, 0.01) == 10000
