"""The analysis tools read a declared single-pass view as its own state (S3).

The fixture is ONE drafting view drawn twice, from the same constants:

  * S0 SHAPE -- what the two-pass registered capture wrote before this
    change: a model image that is the twelve ticks on white, and an
    annotation image holding the view's elements AND the ticks, both on crop
    A's lattice (a view with no crop region: the frame-bound lines fix both
    exports to crop A), each sidecar carrying its ``registration_marks``;
  * SINGLE-PASS SHAPE -- what the capture writes now: the annotation image
    alone, the same elements and NO ticks, its sidecar declaring
    ``capture_passes: ["annotation"]``, the skip and ``single_pass_frame``.

So the single-pass annotation image is the S0 annotation image minus exactly
its tick pixels. The ticks are drawn clear of every element (production
moves them clear of annotation bboxes; ``test_control_no_tick_covers_an_element``
pins that here), so "minus the tick pixels" loses no element pixel.

GATE 3: the grid's annotation cell counts for the single-pass view equal the
S0 grid's, after S0's registration subtracted the ticks.

Every assertion reads the FILES the tools wrote (CLAUDE.md, defect class 4).
"""
import csv
import json

import numpy as np
import pytest
from PIL import Image

from tools import register_stage_a_annotation as reg
from tools import stage_a_grid as grid
from vop_interwoven import capture_passes as cp
from vop_interwoven.stage_a_registered_capture import (
    _no_ticks, _registration_payload, single_pass_frame)
from vop_interwoven.stage_a_registration import MARK_COLOUR, registration_mark_segments

PX_PER_FT = 12.0
CROP = (2.0, 3.0, 32.0, 24.0)               # crop A, the frame-bound rectangle
W, H = int(30 * PX_PER_FT), int(21 * PX_PER_FT)
CELL_IN = 0.125                             # 1/8" at 1:96 = 1 ft = 12 px
SCALE = 96.0
ELEMENTS = {50: ((40, 8, 16), (10.0, 10.0, 14.0, 12.0)),
            51: ((80, 8, 16), (20.0, 15.0, 21.0, 20.0)),
            52: ((120, 8, 16), (5.0, 18.0, 9.0, 18.25))}
STEM = "Drafting_77"


def px(u):
    return PX_PER_FT * (u - CROP[0])


def py(v):
    return -PX_PER_FT * (v - CROP[3])


def _fill(img, rect, colour):
    u0, v0, u1, v1 = rect
    img[int(round(py(v1))):int(round(py(v0))), int(round(px(u0))):int(round(px(u1)))] = colour


def _tick(img, seg):
    if seg["orientation"] == "horizontal":
        yc = py(seg["level_uv"])
        xs = sorted(px(u) for u in seg["span_uv"])
        img[int(round(yc - 1)):int(round(yc + 1)), int(round(xs[0])):int(round(xs[1]))] = \
            MARK_COLOUR
    else:
        xc = px(seg["level_uv"])
        ys = sorted(py(v) for v in seg["span_uv"])
        img[int(round(ys[0])):int(round(ys[1])), int(round(xc - 1)):int(round(xc + 1))] = \
            MARK_COLOUR


def _marks():
    layout = registration_mark_segments(CROP, 1.0 / PX_PER_FT)
    assert layout["state"] == "value", layout
    return [dict(seg, id=9000 + i) for i, seg in enumerate(layout["segments"])]


def _integrity():
    return {"status": "value", "capture_faults": [], "rolled_back": True,
            "restore_failures": 0, "marks_still_in_project": 0, "paint_failures": 0}


def _bbox_map():
    return dict((str(eid), {"bbox_uv": {"state": "value", "value": [
        [r[0], r[1]], [r[2], r[1]], [r[2], r[3]], [r[0], r[3]]]},
        "category": "Text Notes", "membership_basis": "owner_view",
        "element_class": "TextNote"}) for eid, (_c, r) in ELEMENTS.items())


def _frame():
    return {"view_scale": SCALE, "fit_direction": "horizontal", "pixel_size": W,
            "actual_w": W, "actual_h": H, "dim_check": "pass"}


def _anno_image(with_ticks, marks):
    img = np.full((H, W, 3), 255, dtype=np.uint8)
    for _eid, (colour, rect) in ELEMENTS.items():
        _fill(img, rect, colour)
    if with_ticks:
        for m in marks:
            _tick(img, m)
    return img


def _anno_sidecar(tiff, marks_record, view_id=77):
    return {"view_id": view_id, "pass": "annotation", "tiff_path": str(tiff),
            "frame": _frame(),
            "color_assignment_map": dict((str(e), list(c)) for e, (c, _r) in ELEMENTS.items()),
            "annotation_bbox_map": _bbox_map(),
            "annotation_bbox_status": {"status": "value"},
            "capture_integrity": _integrity(),
            "applied_display_style": "FlatColors", "applied_smooth_edges": False,
            "registration_marks": _registration_payload(
                "annotation", marks_record, shared_colour=MARK_COLOUR)}


def _run_meta(folder):
    (folder / "run_meta.json").write_text(json.dumps(
        {"run_id": "RUN_T", "config": {"cell_size_paper_in": CELL_IN}}))


def write_s0_view(folder, stem=STEM, view_id=77, run_meta=True):
    """The two-pass shape: model = ticks on white, annotation = elements +
    ticks, both on crop A at 12 px/ft."""
    folder.mkdir(parents=True, exist_ok=True)
    marks = _marks()
    record = {"marks": {"created": marks, "layout": {"arm_px": 32}},
              "mark_reference_source": "no_crop_region_frame", "faults": [],
              "restore": {"rolled_back": True}}
    model = np.full((H, W, 3), 255, dtype=np.uint8)
    for m in marks:
        _tick(model, m)
    model_tiff, anno_tiff = folder / (stem + ".tiff"), folder / (stem + "_anno.tiff")
    Image.fromarray(model).save(str(model_tiff), format="TIFF")
    Image.fromarray(_anno_image(True, marks)).save(str(anno_tiff), format="TIFF")
    model_side = {"view_id": view_id, "tiff_path": str(model_tiff),
                  "frame": dict(_frame(), crop_uv=list(CROP), sizing_frame="crop_a",
                                crop_px=[W, H], status="value"),
                  "color_assignment_map": {}, "link_category_color_map": {},
                  "near_face_w_map": {"host": {}, "link": {}},
                  "capture_integrity": _integrity(),
                  "applied_display_style": "FlatColors", "applied_smooth_edges": False,
                  "registration_marks": _registration_payload(
                      "model", record, shared_colour=MARK_COLOUR)}
    anno_side = _anno_sidecar(anno_tiff, record, view_id)
    anno_side["model_pass_tiff_path"] = str(model_tiff)
    (folder / (stem + ".json")).write_text(json.dumps(model_side))
    (folder / (stem + "_anno.json")).write_text(json.dumps(anno_side))
    if run_meta:
        _run_meta(folder)
    return folder / (stem + ".json"), folder / (stem + "_anno.json"), marks


def write_single_pass_view(folder, declare=True, stem=STEM, view_id=77, run_meta=True):
    """The single-pass shape: the annotation image alone, no ticks, declared."""
    folder.mkdir(parents=True, exist_ok=True)
    anno_tiff = folder / (stem + "_anno.tiff")
    Image.fromarray(_anno_image(False, [])).save(str(anno_tiff), format="TIFF")
    side = _anno_sidecar(anno_tiff, {"marks": _no_ticks(), "faults": [],
                                     "mark_reference_source": "no_crop_region_frame",
                                     "restore": {"rolled_back": True}}, view_id)
    if declare:
        plan = cp.plan_capture_passes({
            "model_count": 0, "annotation_count": len(ELEMENTS), "unresolved_count": 0,
            "pipeline_model_elements": 0, "has_crop_region": False})
        side["capture_passes"] = plan["passes"]
        side["skipped_pass"] = plan["skipped_pass"]
        geom = {"crop_px": [W, H], "achieved_fpp_ft": 1.0 / PX_PER_FT,
                "achieved_export_dpi": 150.0}
        side["single_pass_frame"] = single_pass_frame(
            CROP, "no_crop_region_frame", geom, type("C", (), {})())
    path = folder / (stem + "_anno.json")
    path.write_text(json.dumps(side))
    if run_meta:
        _run_meta(folder)
    return path


def _grid_npz(folder, rec):
    return dict(np.load(str(folder / "out" / rec["npz"])))


# --- registration ---------------------------------------------------------------

def test_registration_records_a_declared_single_pass_and_writes_no_tiff(tmp_path):
    anno = write_single_pass_view(tmp_path)
    rec = reg.register(anno)
    tiff_out, json_out = reg.output_paths(anno)
    on_disk = json.loads(json_out.read_text())
    assert on_disk["status"] == "single_pass"
    assert on_disk["capture_passes"] == ["annotation"]
    assert on_disk["skipped_pass"]["skipped"] == "model"
    assert on_disk["registered_tiff"] is None and not tiff_out.exists()
    assert on_disk["refusals"] == []
    assert rec == on_disk
    code, line, _ = reg.register_job(anno)
    assert code == 0 and line.startswith("single_pass")


def test_a_missing_model_sidecar_WITHOUT_the_declaration_is_not_single_pass(tmp_path):
    """Mutation (b): reading a missing model sidecar as single_pass -> red."""
    anno = write_single_pass_view(tmp_path, declare=False)
    with pytest.raises(FileNotFoundError):
        reg.register(anno)
    code, line, _ = reg.register_job(anno)
    assert code == 2 and line.startswith("unreadable")
    assert not reg.output_paths(anno)[1].exists()


def test_a_malformed_declaration_is_refused_not_single_pass(tmp_path):
    anno = write_single_pass_view(tmp_path)
    side = json.loads(anno.read_text())
    del side["skipped_pass"]
    anno.write_text(json.dumps(side))
    rec = reg.register(anno)
    assert rec["status"] == "refused"
    assert "declaration" in rec["refusals"][0]


def test_registration_of_an_S0_view_is_unchanged(tmp_path):
    _model, anno, _m = write_s0_view(tmp_path)
    rec = reg.register(anno)
    assert rec["status"] == "registered"
    assert "capture_passes" not in rec


# --- the grid --------------------------------------------------------------------

def test_control_no_tick_covers_an_element():
    marks = _marks()
    with_ticks, without = _anno_image(True, marks), _anno_image(False, marks)
    changed = np.any(with_ticks != without, axis=2)
    assert changed.sum() > 0
    assert np.all(np.all(without[changed] == 255, axis=1)), \
        "a tick was drawn over an element pixel"


def test_gate3_single_pass_annotation_cells_equal_S0s_after_tick_subtraction(tmp_path):
    s0_model, s0_anno, _m = write_s0_view(tmp_path / "s0")
    reg.register(s0_anno)
    s0 = grid.grid_view(s0_model, tmp_path / "s0" / "out")
    assert s0["status"] == "value", s0.get("reason")
    one = write_single_pass_view(tmp_path / "one")
    paths, _folder = grid.view_sidecars(tmp_path / "one")
    assert paths == [one]
    rec = grid.grid_view(one, tmp_path / "one" / "out")
    assert rec["status"] == "value", rec.get("reason")
    on_disk = json.loads((tmp_path / "one" / "out" / (STEM + ".grid.json")).read_text())
    a0, a1 = _grid_npz(tmp_path / "s0", s0), _grid_npz(tmp_path / "one", rec)
    # The same lattice: the same cells.
    for k in ("i_range", "j_range", "origin_uv", "cell_ft", "crop_a_cells"):
        assert on_disk["grid"][k] == s0["grid"][k], k
    anno_keys = sorted(k for k in a0 if k.startswith("anno_"))
    assert anno_keys and anno_keys == sorted(k for k in a1 if k.startswith("anno_"))
    for k in anno_keys:
        assert np.array_equal(a0[k], a1[k]), k
    assert np.array_equal(a0["inside_crop_a"], a1["inside_crop_a"])
    # The ink the elements put in the grid, both ways, and S0's ticks are
    # not in it: S0 registered them away.
    assert int(a1["anno_element_ink"].sum()) > 0
    assert on_disk["occupancy"]["all_cells"]["anno_only"] == \
        s0["occupancy"]["all_cells"]["anno_only"]
    # The annotation keys are the same elements with the same rows.
    assert [k for k in on_disk["ec_keys"] if k["layer"] == "annotation"] == \
        [k for k in s0["ec_keys"] if k["layer"] == "annotation"]


def test_a_declared_annotation_only_view_is_its_own_state_in_the_FILE(tmp_path):
    one = write_single_pass_view(tmp_path)
    rec = grid.grid_view(one, tmp_path / "out")
    on_disk = json.loads((tmp_path / "out" / (STEM + ".grid.json")).read_text())
    assert on_disk == json.loads(json.dumps(rec, default=str))
    assert on_disk["capture_passes"] == ["annotation"]
    assert on_disk["skipped_pass"]["skipped"] == "model"
    flags = on_disk["grid"]["flags"]
    assert "single_pass" in flags and "no_registered_annotation" not in flags
    assert on_disk["registered_annotation"]["status"] == "single_pass"
    assert on_disk["grid"]["frame_check"]["state"] == "value"
    assert on_disk["grid"]["frame_check"]["delta_px"] == 0
    # ZERO model counts, not null: the model layer is the white image an
    # empty model pass renders.
    arr = _grid_npz(tmp_path, rec)
    for k in ("model_host", "model_dwg", "model_link", "model_host_ink",
              "model_dwg_ink", "model_link_ink", "model_black", "model_residual"):
        assert k in arr and int(arr[k].sum()) == 0, k
    assert int(arr["model_white"].sum()) == W * H == int(arr["model_total"].sum())
    totals = on_disk["image_totals"]
    assert totals["model_host_ink"] == 0 and totals["anno_element_ink"] > 0
    assert on_disk["occupancy"]["all_cells"]["model_only"] == 0
    assert on_disk["occupancy"]["all_cells"]["overlap"] == 0
    assert "model" not in on_disk["capture_faults"]


def test_without_the_declaration_an_annotation_sidecar_is_not_gridded(tmp_path):
    """The control for the selector: an orphaned annotation sidecar (no model
    sidecar, no declaration) is what a failed model pass left before this
    change, and is not picked up."""
    write_single_pass_view(tmp_path, declare=False)
    assert grid.view_sidecars(tmp_path)[0] == []


def _model_only_view(folder, declare=True, stem=STEM, view_id=77, run_meta=True):
    """A cropped plan whose annotation membership was empty: the S0 model
    image without ticks and a model element, sidecar declaring the skip."""
    folder.mkdir(parents=True, exist_ok=True)
    model = np.full((H, W, 3), 255, dtype=np.uint8)
    _fill(model, (8.0, 6.0, 12.0, 9.0), (1, 2, 3))
    tiff = folder / (stem + ".tiff")
    Image.fromarray(model).save(str(tiff), format="TIFF")
    side = {"view_id": view_id, "tiff_path": str(tiff),
            "frame": dict(_frame(), crop_uv=list(CROP), sizing_frame="crop_a",
                          crop_px=[W, H], status="value"),
            "color_assignment_map": {"7": [1, 2, 3]}, "link_category_color_map": {},
            "near_face_w_map": {"host": {"7": {"source": "HOST", "category": "Walls"}},
                                "link": {}},
            "capture_integrity": _integrity(),
            "applied_display_style": "FlatColors", "applied_smooth_edges": False,
            "registration_marks": _registration_payload(
                "model", {"marks": _no_ticks(), "faults": [], "restore": {}},
                shared_colour=MARK_COLOUR)}
    if declare:
        plan = cp.plan_capture_passes({
            "model_count": 1, "annotation_count": 0, "unresolved_count": 0,
            "pipeline_model_elements": 1, "has_crop_region": True})
        side["capture_passes"], side["skipped_pass"] = plan["passes"], plan["skipped_pass"]
    path = folder / (stem + ".json")
    path.write_text(json.dumps(side))
    if run_meta:
        _run_meta(folder)
    return path


def test_a_declared_model_only_view_has_zero_annotation_channels(tmp_path):
    path = _model_only_view(tmp_path)
    rec = grid.grid_view(path, tmp_path / "out")
    assert rec["status"] == "value", rec.get("reason")
    on_disk = json.loads((tmp_path / "out" / (STEM + ".grid.json")).read_text())
    flags = on_disk["grid"]["flags"]
    assert "single_pass" in flags and "no_registered_annotation" not in flags
    assert on_disk["capture_passes"] == ["model"]
    assert on_disk["skipped_pass"]["skipped"] == "annotation"
    arr = _grid_npz(tmp_path, rec)
    for k in ("anno_element", "anno_element_ink", "anno_filled_region",
              "anno_black_assigned", "model_ink_under_anno"):
        assert k in arr and int(arr[k].sum()) == 0, k
    assert on_disk["image_totals"]["anno_element_ink"] == 0
    assert on_disk["image_totals"]["model_host_ink"] > 0
    assert on_disk["black"]["total"] == 0
    assert on_disk["model_ink_under_annotation"]["under_annotation_px"] == 0


def test_control_the_same_model_view_undeclared_is_no_registered_annotation(tmp_path):
    path = _model_only_view(tmp_path, declare=False)
    rec = grid.grid_view(path, tmp_path / "out")
    assert rec["status"] == "value"
    assert "no_registered_annotation" in rec["grid"]["flags"]
    assert "single_pass" not in rec["grid"]["flags"]
    assert "anno_element" not in _grid_npz(tmp_path, rec)


def test_a_model_sidecar_beside_a_declared_annotation_only_capture_is_refused(tmp_path):
    one = write_single_pass_view(tmp_path)
    stale = _model_only_view(tmp_path / "x", declare=False)
    (tmp_path / (STEM + ".json")).write_text(stale.read_text())
    rec = grid.grid_view(tmp_path / (STEM + ".json"), tmp_path / "out")
    assert rec["status"] == "refused" and "another capture" in rec["reason"]
    # And the annotation sidecar is no longer selected on its own.
    assert grid.view_sidecars(tmp_path)[0] == [tmp_path / (STEM + ".json")]
    assert one.exists()


# --- the roll-up -----------------------------------------------------------------

def _rollup_run(tmp_path):
    """One v1 run: a two-pass view (77), an annotation-only view (78) and a
    model-only view (79), registered and gridded by the real tools."""
    from tools import stage_a_grid_rollup as rollup
    run = tmp_path / "run"
    cap = run / "color_id_buffer"
    write_s0_view(cap, "Two_77", 77, run_meta=False)
    write_single_pass_view(cap, stem="Drafting_78", view_id=78, run_meta=False)
    _model_only_view(cap, stem="Plan_79", view_id=79, run_meta=False)
    (run / "run_meta.json").write_text(json.dumps({
        "schema": "vop.run_meta.v1", "run_id": "RUN_T", "finalized": True,
        "views_requested": [77, 78, 79],
        "views": [{"view_id": v, "view_name": "V", "capture_status": "success",
                   "capture_failure_reason": ""} for v in (77, 78, 79)],
        "config": {"cell_size_paper_in": CELL_IN}}))
    assert reg.main([str(cap)]) == 0
    assert grid.main([str(run)]) == 0
    assert sorted(p.name for p in (run / "analysis_grid").glob("*.grid.json")) == [
        "Drafting_78.grid.json", "Plan_79.grid.json", "Two_77.grid.json"]
    return run, rollup.main([str(run)])


def _rollup_files(run):
    rows = list(csv.DictReader(open(str(run / "analysis_grid" / "grid_rollup.csv"))))
    summary = json.loads((run / "analysis_grid" / "grid_rollup.summary.json").read_text())
    return dict((r["view_id"], r) for r in rows), summary


def test_the_rollup_counts_single_pass_views_as_their_own_category(tmp_path):
    run, code = _rollup_run(tmp_path)
    assert code == 0
    rows, summary = _rollup_files(run)
    assert summary["count_invariant"]["difference"] == 0
    assert summary["count_invariant"]["row_status_total"] == 3
    assert all(r["row_status"] == "gridded" for r in rows.values()), rows
    assert rows["77"]["registration_state"] == "registered"
    assert rows["77"]["capture_passes"] == ""          # S0 shape: undeclared
    assert rows["78"]["registration_state"] == "single_pass"
    assert rows["78"]["capture_passes"] == "annotation"
    assert rows["78"]["skipped_pass"] == "model"
    assert rows["78"]["skipped_pass_reason"] == "membership_empty"
    assert rows["79"]["registration_state"] == "single_pass"
    assert rows["79"]["skipped_pass"] == "annotation"
    # Zero, not blank, for the skipped pass.
    assert rows["78"]["model_host_ink"] == "0" and int(rows["78"]["anno_element_ink"]) > 0
    assert rows["79"]["anno_element_ink"] == "0" and int(rows["79"]["model_host_ink"]) > 0
    assert rows["79"]["inside_crop_a_anno_only"] == "0"
    sp = summary["single_pass_count"]
    assert sp["count"] == 2 and sp["by_skipped_pass"] == {"model": 1, "annotation": 1}
    assert sp["by_reason"] == {"membership_empty": 2}
    assert summary["model_only_count"]["count"] == 0
    assert summary["registration_state"]["counts"]["single_pass"] == 2
    assert "single_pass" in summary["flag_counts"]["counts"]
    assert "no_registered_annotation" not in summary["flag_counts"]["counts"]


def test_a_single_pass_grid_goes_stale_when_its_sidecar_changes(tmp_path):
    from tools import stage_a_grid_rollup as rollup
    run, _code = _rollup_run(tmp_path)
    anno = run / "color_id_buffer" / "Drafting_78_anno.json"
    side = json.loads(anno.read_text())
    side["note"] = "recaptured"
    anno.write_text(json.dumps(side))
    assert rollup.main([str(run)]) == 1
    rows, _summary = _rollup_files(run)
    assert rows["78"]["row_status"] == "grid_stale"
    assert "annotation_sidecar_sha256" in rows["78"]["reason"]
    assert rows["77"]["row_status"] == "gridded"


def test_kinds_derive_a_single_pass_view_and_count_it_in_the_inventory(tmp_path):
    from tools import stage_a_kinds as kinds
    run, _code = _rollup_run(tmp_path)
    gdir = run / "analysis_grid"
    cmap_info = kinds.load_class_map()
    for stem, skipped in (("Drafting_78", "model"), ("Plan_79", "annotation"),
                          ("Two_77", None)):
        rec = kinds.derive_view(gdir / (stem + ".grid.json"), cmap_info)
        assert rec["status"] == "value", (stem, rec.get("reason"))
        on_disk = json.loads((gdir / (stem + ".kinds.json")).read_text())
        assert (on_disk.get("skipped_pass") or {}).get("skipped") == skipped
        # Every family is derived, from real or zero channels: none is
        # "not_applicable" for want of a layer.
        assert all(f["state"] == "value" for f in on_disk["families"].values()), stem
    rows, views = kinds.inventory([gdir], cmap_info[0])
    record = kinds.write_inventory(rows, views, cmap_info, tmp_path / "inv")
    assert record["grid_records"]["used"] == 3
    assert record["grid_records"]["single_pass"] == {"model": 1, "annotation": 1}


def test_the_census_measures_a_single_pass_run(tmp_path):
    from tools import stage_a_element_census as census
    run, _code = _rollup_run(tmp_path)
    meta = json.loads((run / "run_meta.json").read_text())
    meta["date"] = "2025-12-20"
    (run / "run_meta.json").write_text(json.dumps(meta))
    result = census.census_run(run)
    assert result["refused"] is None, result["refused"]
    assert sorted(result["measured"]) == ["77", "78", "79"]
    # The model-only view's element is measured; the annotation-only view
    # holds no model element and is still a measured view.
    assert "host:7" in result["elements"]
    assert result["views"]["78"]["model_cells"] == 0
    assert result["views"]["78"]["anno_cells"] > 0
    assert result["views"]["79"]["anno_cells"] == 0
    # The census's inputs: the annotation-only view's sidecar, TIFF and grid
    # files are hashed, and its single_pass registration record (no TIFF by
    # design) is not an unresolved input.
    inputs, _stats = census.census_inputs(census.RunFiles(run))
    assert not [k for k in inputs if k.startswith("unresolved")], inputs
    for name in ("Drafting_78_anno.json", "Drafting_78_anno.tiff",
                 "Drafting_78_anno.registered.json", "Drafting_78.grid.json",
                 "Drafting_78.grid.npz"):
        assert any(k.endswith(name) for k in inputs), name


def test_the_timing_report_reads_an_annotation_only_view_from_its_sidecar(tmp_path):
    """Composed with a sidecar the drafting-view capture really wrote."""
    import shutil
    from tests import stage_a_single_pass_fixtures as fx
    from tools.stage_a_timing_report import timing_rows
    out = fx.run_fixture("drafting", tmp_path / "cap")[0]
    assert out["capture_passes"] == ["annotation"]
    run = tmp_path / "run"
    (run / "color_id_buffer").mkdir(parents=True)
    vid = out["view_id"]
    shutil.copy(out["annotation_sidecar_path"],
                str(run / "color_id_buffer" / "D_{0}_anno.json".format(vid)))
    # A two-pass view's ANNOTATION sidecar (model sibling present) is never read.
    (run / "color_id_buffer" / "P_5_anno.json").write_text(json.dumps({"view_id": 5}))
    (run / "color_id_buffer" / "P_5.json").write_text(json.dumps({"view_id": 5}))
    (run / "run_meta.json").write_text(json.dumps({"run_id": "RUN_B", "views": [
        {"view_id": vid, "capture_status": "success"}]}))
    (run / "views_core_x.csv").write_text(
        "RunId,ViewId,ViewName,ElapsedSec\nRUN_B,{0},d,3.0\n".format(vid))
    rows = dict((r["view_name"], r) for r in timing_rows(str(run)))
    with open(out["annotation_sidecar_path"]) as handle:
        total = json.load(handle)["registration_marks"]["timings_ms"]["total"]
    assert rows["d"]["capture_total_ms"] == total, rows["d"]
    assert rows["d"]["reason"] in (None, "")
