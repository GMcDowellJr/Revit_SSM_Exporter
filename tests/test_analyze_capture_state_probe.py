"""The capture-state probe's offline analyzer (tools/analyze_capture_state_probe.py).

The probe itself needs Revit; these tests cover the analyzer only. Every
fixture is a synthetic TIFF drawn here from known constants plus a probe
JSON in the shape the probe writes, and every assertion reads the analysis
JSON the tool WROTE (CLAUDE.md, defect class 4). Expected numbers are
arithmetic on the fixture, not calls into the tool.
"""
import hashlib
import json
import math

import numpy as np
from PIL import Image

from tools import analyze_capture_state_probe as acs

WHITE = (255, 255, 255)
W, H = 100, 50
# The 2 % band: ceil(0.02 * 50) = 1 row top and bottom, ceil(0.02 * 100) = 2
# columns left and right. Pixels in it: 2 rows * 100 + 2 * 2 cols * (50 - 2).
BAND_ROWS, BAND_COLS = 1, 2
BAND_PX = 2 * BAND_ROWS * W + 2 * BAND_COLS * (H - 2 * BAND_ROWS)


def _tiff(folder, name, img):
    Image.fromarray(np.asarray(img, dtype=np.uint8), mode="RGB").save(
        str(folder / name), format="TIFF")


def _flat(w=W, h=H, colour=WHITE):
    return np.full((h, w, 3), colour, dtype=np.uint8)


def _export(step, view_id, w=W, fit="horizontal", state="value"):
    rec = {"step": step, "view_id": view_id, "file": "{0}_{1}.tiff".format(step, view_id),
           "state": state, "requested_pixel_size": w, "fit_direction": fit,
           "dims_px": {"state": "value", "value": None, "error": None}}
    if state != "value":
        rec["error"] = "RuntimeError: export failed"
    return rec


def _probe(folder, exports, images, q3=None, name="probe_capture_state_20261001T000000.json"):
    folder.mkdir(parents=True, exist_ok=True)
    for export in exports:
        if export["file"] in images:
            _tiff(folder, export["file"], images[export["file"]])
    report = {"schema": "vop.probe.capture_state.v1", "exports": exports,
              "questions": {"q3": {"views": q3}} if q3 is not None else {},
              "verify_changed": [], "VERIFY_SUMMARY": "ALL PROBED VIEWS RESTORED"}
    path = folder / name
    path.write_text(json.dumps(report))
    return path


def _run(folder):
    record, path = acs.run(str(folder))
    on_disk = json.loads((folder / acs.ANALYSIS_NAME).read_text())
    assert path == folder / acs.ANALYSIS_NAME
    return on_disk


def _image(rec, file_name):
    return next(i for i in rec["images"] if i["file"] == file_name)


def _pair(rec, file_name):
    return next(p for p in rec["pairs_vs_s0"] if p["file"] == file_name)


# --- per image -----------------------------------------------------------------

def test_a_flat_white_image_has_no_border_no_grey_and_one_colour(tmp_path):
    exports = [_export("Q4_S0", 7)]
    probe = _probe(tmp_path, exports, {"Q4_S0_7.tiff": _flat()})
    rec = _run(tmp_path)
    assert rec["status"] == "value"
    assert rec["probe_json_sha256"] == hashlib.sha256(probe.read_bytes()).hexdigest()
    img = _image(rec, "Q4_S0_7.tiff")
    assert img["size_px"] == [W, H]
    assert img["distinct_colours"] == 1
    assert img["border_pixels"] == BAND_PX
    assert img["border_non_white_fraction"] == 0.0
    assert img["grey_pixels"] == 0
    assert img["top_colours"] == [{"rgb": list(WHITE), "count": W * H}]


def test_a_gradient_border_is_counted_by_the_2_percent_band(tmp_path):
    """A gradient fills the band exactly; one more ring just inside it is
    coloured too and must NOT count. Then the top row alone."""
    full = _flat()
    for x in range(W):
        colour = (x * 2, 100, 255 - x * 2)
        full[:BAND_ROWS + 1, x] = colour
        full[H - BAND_ROWS - 1:, x] = colour
    for y in range(H):
        colour = (200, y * 4, 50)
        full[y, :BAND_COLS + 1] = colour
        full[y, W - BAND_COLS - 1:] = colour
    top_only = _flat()
    top_only[0, :] = (10, 20, 30)
    exports = [_export("Q4_S0", 7), _export("Q4_S1", 7)]
    _probe(tmp_path, exports, {"Q4_S0_7.tiff": full, "Q4_S1_7.tiff": top_only})
    rec = _run(tmp_path)
    band = _image(rec, "Q4_S0_7.tiff")
    assert band["border_band_px"] == [BAND_ROWS, BAND_COLS]
    assert band["border_non_white_fraction"] == 1.0
    assert band["border_non_white_pixels"] == BAND_PX
    assert band["distinct_colours"] > 50
    top = _image(rec, "Q4_S1_7.tiff")
    assert top["border_non_white_pixels"] == W
    assert top["border_non_white_fraction"] == W / float(BAND_PX)


def test_grey_shadow_blocks_are_counted_and_white_black_and_near_grey_are_not(tmp_path):
    img = _flat()
    img[10:20, 10:20] = (128, 128, 128)     # 100 grey
    img[30:35, 50:60] = (64, 64, 64)        # 50 grey
    img[0:5, 80:90] = (0, 0, 0)             # black: not grey
    img[40:45, 80:90] = (128, 128, 129)     # near grey: not grey
    exports = [_export("Q4_S0", 7)]
    _probe(tmp_path, exports, {"Q4_S0_7.tiff": img})
    rec = _run(tmp_path)
    grey = _image(rec, "Q4_S0_7.tiff")
    assert grey["grey_pixels"] == 150
    assert grey["distinct_colours"] == 5
    assert grey["top_colours"][0] == {"rgb": list(WHITE), "count": W * H - 100 - 50 - 50 - 50}
    assert grey["top_colours"][1] == {"rgb": [128, 128, 128], "count": 100}


# --- pairs ---------------------------------------------------------------------

def test_a_same_size_step_counts_the_pixels_that_changed(tmp_path):
    changed = _flat()
    changed[5:10, 3:10] = (1, 2, 3)          # 35 pixels
    changed[40, 40] = (254, 255, 255)        # 1 pixel, one channel
    exports = [_export("Q1_S0", 5), _export("Q1_S1", 5), _export("Q1_S2", 5)]
    _probe(tmp_path, exports, {"Q1_S0_5.tiff": _flat(), "Q1_S1_5.tiff": changed,
                               "Q1_S2_5.tiff": _flat()})
    rec = _run(tmp_path)
    s1 = _pair(rec, "Q1_S1_5.tiff")
    assert (s1["state"], s1["baseline"]) == ("value", "Q1_S0_5.tiff")
    assert s1["changed_pixels"] == 36
    assert s1["changed_fraction"] == 36 / float(W * H)
    s2 = _pair(rec, "Q1_S2_5.tiff")
    assert (s2["state"], s2["changed_pixels"]) == ("value", 0)
    assert all(p["file"] != "Q1_S0_5.tiff" for p in rec["pairs_vs_s0"])


def test_a_different_size_step_is_size_differs_never_resized(tmp_path):
    exports = [_export("Q1_S0", 5), _export("Q1_S1", 5)]
    _probe(tmp_path, exports, {"Q1_S0_5.tiff": _flat(), "Q1_S1_5.tiff": _flat(h=H - 3)})
    rec = _run(tmp_path)
    s1 = _pair(rec, "Q1_S1_5.tiff")
    assert s1["state"] == "size_differs"
    assert s1["baseline_size_px"] == [W, H] and s1["size_px"] == [W, H - 3]
    assert s1["changed_pixels"] is None and s1["changed_fraction"] is None


def test_the_baseline_is_the_same_question_and_view(tmp_path):
    """Q3 S1 of view 9 against Q3 S0 of view 9: not Q1's S0, not view 5's."""
    other = _flat()
    other[:, :] = (9, 9, 9)
    exports = [_export("Q1_S0", 9), _export("Q3_S0", 5), _export("Q3_S0", 9),
               _export("Q3_S1", 9)]
    _probe(tmp_path, exports, {"Q1_S0_9.tiff": other, "Q3_S0_5.tiff": other,
                               "Q3_S0_9.tiff": _flat(), "Q3_S1_9.tiff": _flat()})
    rec = _run(tmp_path)
    pair = _pair(rec, "Q3_S1_9.tiff")
    assert (pair["baseline"], pair["changed_pixels"]) == ("Q3_S0_9.tiff", 0)


def test_a_step_without_its_S0_is_baseline_unavailable(tmp_path):
    exports = [_export("Q1_S0", 5, state="raised"), _export("Q1_S1", 5)]
    _probe(tmp_path, exports, {"Q1_S1_5.tiff": _flat()})
    rec = _run(tmp_path)
    assert rec["status"] == "value"
    assert _pair(rec, "Q1_S1_5.tiff")["state"] == "baseline_unavailable"
    assert rec["failed_exports"] == [{"step": "Q1_S0", "view_id": 5, "file": "Q1_S0_5.tiff",
                                      "error": "RuntimeError: export failed"}]


# --- Q3 ------------------------------------------------------------------------

def _box(w_ft, h_ft):
    return {"min": [0.0, 0.0, 0.0], "max": [float(w_ft), float(h_ft), 0.0],
            "transform": None}


def _q3_step(step, export, written=None, read_back=None):
    rec = {"step": step, "export": export,
           "common": {"crop_box": {"state": "value", "value": read_back, "error": None}},
           "at_export": {"shape_set": {"state": "value", "value": True}}}
    if written is not None:
        rec.update(written_box=written, read_back_box=read_back,
                   read_back_equal={"local": True, "world": True})
    return rec


def test_q3_rows_report_the_non_fit_axis_mismatch(tmp_path):
    """MOHAVE's shape: a box of 846 : 729 aspect exported at 846 px wide
    implies 729 px tall; the export came back 639. The control's export
    matches its box."""
    e0, e1 = _export("Q3_S0", 11, w=846), _export("Q3_S1", 11, w=846)
    c1 = _export("Q3_S1", 22, w=846)
    q3 = [{"view_id": 11, "role": "test",
           "steps": [_q3_step("S0", e0, read_back=_box(84.6, 72.9)),
                     _q3_step("S1", e1, written=_box(84.6, 72.9),
                              read_back=_box(84.6, 72.9))]},
          {"view_id": 22, "role": "control",
           "steps": [_q3_step("S1", c1, written=_box(84.6, 72.9),
                              read_back=_box(84.6, 72.9))]},
          {"role": "test_slab", "refused": "not_provided"}]
    _probe(tmp_path, [e0, e1, c1], {"Q3_S0_11.tiff": _flat(846, 639),
                                    "Q3_S1_11.tiff": _flat(846, 639),
                                    "Q3_S1_22.tiff": _flat(846, 730)}, q3=q3)
    rec = _run(tmp_path)
    test, control, slab = rec["q3"]
    s0, s1 = test["rows"]
    assert s0["box_source"] == "s0_read" and s1["box_source"] == "written"
    assert s1["written_extent_ft"] == [84.6, 72.9]
    assert s1["read_back_extent_ft"] == [84.6, 72.9]
    assert s1["exported_px"] == [846, 639] and s1["implied_px"] == [846, 729]
    assert (s1["non_fit_axis"], s1["non_fit_delta_px"], s1["non_fit_mismatch"]) == (
        "height", -90, True)
    assert s0["implied_px"] == [846, 729] and s0["non_fit_mismatch"] is True
    # 1 px is within tolerance; the control is not a mismatch.
    row = control["rows"][0]
    assert (row["non_fit_delta_px"], row["non_fit_mismatch"]) == (1, False)
    assert slab["refused"] == "not_provided" and slab["rows"] == []


def test_q3_implied_is_from_the_WRITTEN_box_when_the_read_back_differs(tmp_path):
    """The scope-box hypothesis: the write is silently overridden, so the
    read-back (20 x 15) is not what was written (20 x 10) and the export
    follows the read-back. The row says so: implied from the written box,
    a 50 px non-fit mismatch, and the read-back extent beside it."""
    e1 = _export("Q3_S1", 11, w=200)
    step = _q3_step("S1", e1, written=_box(20, 10), read_back=_box(20, 15))
    step["read_back_equal"] = {"local": False, "world": False}
    q3 = [{"view_id": 11, "role": "test", "steps": [step]}]
    _probe(tmp_path, [e1], {"Q3_S1_11.tiff": _flat(200, 150)}, q3=q3)
    row = _run(tmp_path)["q3"][0]["rows"][0]
    assert row["written_extent_ft"] == [20.0, 10.0]
    assert row["read_back_extent_ft"] == [20.0, 15.0]
    assert row["read_back_equal"] == {"local": False, "world": False}
    assert (row["implied_px"], row["exported_px"]) == ([200, 100], [200, 150])
    assert (row["non_fit_delta_px"], row["non_fit_mismatch"]) == (50, True)


def test_q3_a_rejected_crop_write_implies_nothing(tmp_path):
    """Codex, PR #225: a CropBox write Revit rejected leaves written_box
    None and crop_write_state "raised". The row must not compute an implied
    size from the attempted box or from the read-back, so a rejection is
    never read as Revit overriding an accepted write. The S1 beside it,
    whose write succeeded, is the control."""
    e1, e2 = _export("Q3_S1", 11, w=200), _export("Q3_S2", 11, w=200)
    ok = _q3_step("S1", e1, written=_box(20, 10), read_back=_box(20, 10))
    ok["crop_write_state"] = "value"
    rejected = _q3_step("S2", e2, read_back=_box(20, 15))
    rejected.update(crop_write_state="raised", written_box=None,
                    attempted_box=_box(20, 10), read_back_box=_box(20, 15))
    q3 = [{"view_id": 11, "role": "test", "steps": [ok, rejected]}]
    _probe(tmp_path, [e1, e2], {"Q3_S1_11.tiff": _flat(200, 150),
                                "Q3_S2_11.tiff": _flat(200, 150)}, q3=q3)
    good, bad = _run(tmp_path)["q3"][0]["rows"]
    assert (good["box_source"], good["non_fit_delta_px"], good["non_fit_mismatch"]) == (
        "written", 50, True)
    assert bad["box_source"] == "write_failed"
    assert bad["crop_write_state"] == "raised"
    assert bad["written_extent_ft"] is None
    assert bad["read_back_extent_ft"] == [20.0, 15.0]
    assert bad["exported_px"] == [200, 150]
    assert (bad["implied_px"], bad["non_fit_delta_px"], bad["non_fit_mismatch"]) == (
        None, None, None)


def test_q3_two_pixels_off_is_a_mismatch(tmp_path):
    e1 = _export("Q3_S1", 11, w=200)
    q3 = [{"view_id": 11, "role": "test",
           "steps": [_q3_step("S1", e1, written=_box(20, 10), read_back=_box(20, 10))]}]
    _probe(tmp_path, [e1], {"Q3_S1_11.tiff": _flat(200, 102)}, q3=q3)
    row = _run(tmp_path)["q3"][0]["rows"][0]
    assert (row["implied_px"], row["non_fit_delta_px"], row["non_fit_mismatch"]) == (
        [200, 100], 2, True)


def test_q3_a_vertical_fit_compares_the_width(tmp_path):
    e1 = _export("Q3_S1", 11, w=300, fit="vertical")
    q3 = [{"view_id": 11, "role": "test",
           "steps": [_q3_step("S1", e1, written=_box(20, 10), read_back=_box(20, 10))]}]
    _probe(tmp_path, [e1], {"Q3_S1_11.tiff": _flat(500, 300)}, q3=q3)
    row = _run(tmp_path)["q3"][0]["rows"][0]
    assert row["implied_px"] == [600, 300]
    assert (row["non_fit_axis"], row["non_fit_delta_px"], row["non_fit_mismatch"]) == (
        "width", -100, True)


# --- refusals ------------------------------------------------------------------

def test_a_missing_probe_json_is_refused(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    rec = _run(tmp_path)
    assert rec["status"] == "refused"
    assert "probe JSON is missing" in rec["reasons"][0]
    assert rec["probe_json_sha256"] is None
    assert "images" not in rec


def test_two_probe_jsons_are_refused_not_guessed_between(tmp_path):
    _probe(tmp_path, [], {})
    _probe(tmp_path, [], {}, name="probe_capture_state_20261001T000001.json")
    rec = _run(tmp_path)
    assert rec["status"] == "refused" and "2 probe JSONs" in rec["reasons"][0]


def test_an_unparseable_probe_json_is_refused(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "probe_capture_state_20261001T000000.json").write_text("{not json")
    rec = _run(tmp_path)
    assert rec["status"] == "refused" and "cannot be read" in rec["reasons"][0]


def test_a_missing_tiff_is_refused_naming_every_one(tmp_path):
    exports = [_export("Q1_S0", 5), _export("Q1_S1", 5), _export("Q1_S2", 5)]
    _probe(tmp_path, exports, {"Q1_S0_5.tiff": _flat()})
    rec = _run(tmp_path)
    assert rec["status"] == "refused"
    assert rec["reasons"] == ["TIFF Q1_S1_5.tiff named in the probe JSON is missing",
                              "TIFF Q1_S2_5.tiff named in the probe JSON is missing"]
    assert "images" not in rec


def test_an_unreadable_tiff_is_refused(tmp_path):
    exports = [_export("Q1_S0", 5), _export("Q1_S1", 5)]
    _probe(tmp_path, exports, {"Q1_S0_5.tiff": _flat()})
    (tmp_path / "Q1_S1_5.tiff").write_bytes(b"II*\x00garbage")
    rec = _run(tmp_path)
    assert rec["status"] == "refused"
    assert len(rec["reasons"]) == 1 and "Q1_S1_5.tiff cannot be read" in rec["reasons"][0]


def test_the_exit_code_is_2_on_refusal_and_0_on_a_value(tmp_path):
    good, bad = tmp_path / "good", tmp_path / "bad"
    _probe(good, [_export("Q4_S0", 7)], {"Q4_S0_7.tiff": _flat()})
    _probe(bad, [_export("Q4_S0", 7)], {})
    assert acs.main([str(good)]) == 0
    assert acs.main([str(bad)]) == 2
    # A second run beside its own analysis file still finds one probe JSON.
    assert acs.main([str(good)]) == 0


def test_the_band_is_at_least_one_pixel():
    assert acs.border_band_px(10, 10) == (1, 1)
    assert acs.border_band_px(1000, 3000) == (20, 60)
    assert acs.border_band_px(1001, 3001) == (math.ceil(20.02), math.ceil(60.02))
