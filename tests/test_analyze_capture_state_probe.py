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


def test_q3_steps_after_an_unrestored_rollback_are_refused_not_mismatches(tmp_path):
    """Codex, PR #225: S2's rollback left the scope box changed, so the probe
    refused S3 and S4. Their rows are refused with no numbers -- never a
    mismatch, never a match -- and S2's row says its rollback did not
    restore. S1, restored and run, is the control."""
    e1, e2 = _export("Q3_S1", 11, w=200), _export("Q3_S2", 11, w=200)
    s1 = _q3_step("S1", e1, written=_box(20, 10), read_back=_box(20, 10))
    s2 = _q3_step("S2", e2, written=_box(20, 10), read_back=_box(20, 10))
    reason = "state not restored after S2: changed ['scope_box']"
    refused = [{"step": name, "refused": "state_not_restored",
                "refused_reason": reason, "blocked_by": "S2"} for name in ("S3", "S4")]
    q3 = [{"view_id": 11, "role": "test", "steps": [s1, s2] + refused,
           "restore_checks": {"question_start": {"restored": True},
                              "S1": {"restored": True}, "S2": {"restored": False}}}]
    _probe(tmp_path, [e1, e2], {"Q3_S1_11.tiff": _flat(200, 150),
                                "Q3_S2_11.tiff": _flat(200, 150)}, q3=q3)
    rows = _run(tmp_path)["q3"][0]["rows"]
    assert [r["step"] for r in rows] == ["S1", "S2", "S3", "S4"]
    r1, r2, r3, r4 = rows
    assert (r1["restored_after"], r1["non_fit_mismatch"]) == (True, True)
    assert (r2["restored_after"], r2["box_source"], r2["non_fit_delta_px"]) == (False, "written", 50)
    for row in (r3, r4):
        assert row["box_source"] == "refused"
        assert row["refused"] == "state_not_restored"
        assert row["refused_reason"] == reason and row["blocked_by"] == "S2"
        assert row["restored_after"] is None
        assert (row["exported_px"], row["implied_px"], row["non_fit_delta_px"],
                row["non_fit_mismatch"]) == (None, None, None, None)


def test_q3_a_view_refused_at_question_start_has_only_refused_rows(tmp_path):
    """An earlier question left the view changed: the probe refused the whole
    question for it, S0 included."""
    reason = "state not restored after question_start: changed ['display_style']"
    q3 = [{"view_id": 11, "role": "test", "refused": reason,
           "steps": [{"step": s, "refused": "state_not_restored", "refused_reason": reason,
                      "blocked_by": "question_start"} for s in ("S0", "S1", "S2", "S3", "S4")],
           "restore_checks": {"question_start": {"restored": False}}}]
    _probe(tmp_path, [], {}, q3=q3)
    view = _run(tmp_path)["q3"][0]
    assert view["refused"] == reason
    assert [r["box_source"] for r in view["rows"]] == ["refused"] * 5
    assert all(r["non_fit_mismatch"] is None for r in view["rows"])


def test_q3_a_discriminator_that_did_not_apply_is_refused_not_a_mismatch(tmp_path):
    """Codex, PR #225: S2's scope-box clear did not apply (Set returned
    False, the read-back still shows the scope box), so the probe wrote no
    crop and exported nothing. The row is refused with the reason -- not a
    mismatch that would read as "clearing the scope box did not help" --
    while its rollback still restored the view, so later steps ran."""
    e1 = _export("Q3_S1", 11, w=200)
    s1 = _q3_step("S1", e1, written=_box(20, 10), read_back=_box(20, 10))
    reason = "the scope box was not cleared: write value (value False), read-back 900"
    s2 = {"step": "S2", "refused": "discriminator_not_applied", "refused_reason": reason,
          "writes": {"scope_box_cleared": {"state": "value", "value": False}}}
    q3 = [{"view_id": 11, "role": "test", "steps": [s1, s2],
           "restore_checks": {"S1": {"restored": True}, "S2": {"restored": True}}}]
    _probe(tmp_path, [e1], {"Q3_S1_11.tiff": _flat(200, 150)}, q3=q3)
    r1, r2 = _run(tmp_path)["q3"][0]["rows"]
    assert r1["non_fit_mismatch"] is True
    assert (r2["box_source"], r2["refused"], r2["refused_reason"]) == (
        "refused", "discriminator_not_applied", reason)
    assert r2["restored_after"] is True
    assert (r2["exported_px"], r2["implied_px"], r2["non_fit_mismatch"]) == (None, None, None)


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


# --- round 2 (analyzer 1.1.0) ------------------------------------------------

def _round2_probe(folder, questions, exports=(), images=None, version="2026-10-02.1"):
    folder.mkdir(parents=True, exist_ok=True)
    for export in exports:
        if export["file"] in (images or {}):
            _tiff(folder, export["file"], images[export["file"]])
    report = {"schema": "vop.probe.capture_state.v1", "probe": {"version": version},
              "exports": list(exports), "questions": questions,
              "verify_changed": [], "VERIFY_SUMMARY": "ALL PROBED VIEWS RESTORED"}
    (folder / "probe_capture_state_20261002T000000.json").write_text(json.dumps(report))


def _write(attempted, read_back, commit_status="Committed", state="value", differs=True):
    return {"attempted": attempted, "commit_status": commit_status, "state": state,
            "value": True, "error": None,
            "read_back": {"state": "value", "value": read_back, "error": None},
            "took_effect": attempted == read_back, "attempted_differs_from_before": differs}


def test_a_write_that_commits_without_effect_is_listed_as_a_finding(tmp_path):
    """The round-1 shape: CropBox under a scope box commits (status "3" in
    probe 2026-10-01.x, "Committed" since) and reads back unchanged. Both
    forms are listed; a write that took effect, and one that did not commit,
    are not."""
    steps = [{"step": "S1", "writes": {
        "silent_digit": _write([1, 2], [0, 0], commit_status="3"),
        "silent_name": _write([1, 2], [0, 0], commit_status="Committed"),
        "worked": _write([1, 2], [1, 2]),
        "rolled_back": _write([1, 2], [0, 0], commit_status="RolledBack", state="raised")}}]
    _round2_probe(tmp_path, {"q3": {"views": [{"view_id": 11, "steps": steps}]}})
    rec = _run(tmp_path)
    assert rec["status"] == "value" and len(rec["writes"]) == 4
    silent = sorted(w["path"].rsplit("/", 1)[1] for w in rec["commit_without_effect"])
    assert silent == ["silent_digit", "silent_name"]
    by_name = dict((w["path"].rsplit("/", 1)[1], w) for w in rec["writes"])
    assert by_name["silent_digit"]["commit_status_name"] == "Committed"
    assert by_name["worked"]["took_effect"] is True


def test_q1b_lists_every_member_change_with_its_category_and_owner(tmp_path):
    details = {"5": {"state": "value", "error": None, "value": {
                   "exists": True, "class": "Wall",
                   "category": {"state": "value", "value": "Walls", "error": None},
                   "owner_view_id": {"state": "value", "value": -1, "error": None}}},
               "15846537": {"state": "value", "error": None, "value": {
                   "exists": True, "class": "TextNote",
                   "category": {"state": "value", "value": "Text Notes", "error": None},
                   "owner_view_id": {"state": "value", "value": 6112047, "error": None}}},
               "9": {"state": "raised", "value": None, "error": "InvalidOperationException: x"}}
    step = {"step": "S1",
            "writes": {"crop_box_identity": _write([0], [0], differs=False)},
            "membership": {"added": [5, 15846537], "removed": [9], "added_count": 2,
                           "removed_count": 1, "truncated": False, "details": details},
            "watch_ids": {"15846537": {"in_before": False, "in_after": True}},
            "parameter_diff": {"changed": [{"name": "Crop Region Visible", "before": 0, "after": 1}],
                               "unreadable": []}}
    _round2_probe(tmp_path, {"q1b": {"views": [{"view_id": 6112047, "steps": [step]}]}})
    v = _run(tmp_path)["q1b"][0]
    assert (v["added_count"], v["removed_count"], v["truncated"]) == (2, 1, False)
    rows = dict((r["id"], r) for r in v["members"])
    assert (rows[5]["category"], rows[5]["owner_view_id"]) == ("Walls", -1)
    assert (rows[15846537]["category"], rows[15846537]["owner_view_id"]) == ("Text Notes", 6112047)
    # An unreadable detail is reported unavailable, never filled with a default.
    assert rows[9]["category"] == {"unavailable": "raised: InvalidOperationException: x"}
    assert v["by_change_and_category"] == {"added:Walls": 1, "added:Text Notes": 1,
                                           "removed:unavailable": 1}
    assert v["watch_ids"]["15846537"]["in_after"] is True
    assert v["parameters_changed"][0]["name"] == "Crop Region Visible"
    assert v["identity_write"]["attempted_differs_from_before"] is False


def test_q3b_reports_non_white_and_changed_pixels_against_s0(tmp_path):
    e0, e1 = _export("Q3B_S0", 11), _export("Q3B_S1", 11)
    s1_img = _flat()
    s1_img[0:10, 0:10] = (0, 0, 0)                               # 100 more non-white
    t0 = {"origin": [0, 0, 0], "basis_x": [-1, 0, 0], "basis_y": [0, -1, 0], "basis_z": [0, 0, 1]}
    steps = [{"step": "S0", "export": e0,
              "at_export": {"scope_box": {"state": "value", "value": 4672540, "error": None}}},
             {"step": "S1", "export": e1,
              "writes": {"scope_box_cleared": _write(-1, -1)},
              "at_export": {"scope_box": {"state": "value", "value": -1, "error": None}},
              "read_back_transform": t0, "s0_transform": t0}]
    _round2_probe(tmp_path, {"q3b": {"views": [{"view_id": 11, "role": "test", "steps": steps}]}},
                  exports=[e0, e1], images={"Q3B_S0_11.tiff": _flat(), "Q3B_S1_11.tiff": s1_img})
    rows = _run(tmp_path)["q3b"][0]["rows"]
    assert rows[1]["scope_box_at_export"] == -1 and rows[0]["scope_box_at_export"] == 4672540
    assert rows[1]["non_white_pixels"] == 100 and rows[1]["non_white_vs_s0"] == 100
    assert rows[1]["changed_fraction_vs_s0"] == 100 / float(W * H)
    assert rows[1]["transform_kept"] is True
    assert rows[1]["writes"]["scope_box_cleared"]["took_effect"] is True


def test_q3b_a_transform_that_changed_is_not_kept(tmp_path):
    t0 = {"origin": [0, 0, 0], "basis_x": [-1, 0, 0], "basis_y": [0, -1, 0], "basis_z": [0, 0, 1]}
    t1 = dict(t0, basis_x=[1, 0, 0], basis_y=[0, 1, 0])
    steps = [{"step": "S1", "read_back_transform": t1, "s0_transform": t0}]
    _round2_probe(tmp_path, {"q3b": {"views": [{"view_id": 11, "steps": steps}]}})
    assert _run(tmp_path)["q3b"][0]["rows"][0]["transform_kept"] is False


def test_q5b_summarises_each_variant_and_refuses_a_non_dependent_view(tmp_path):
    a = {"step": "A", "detach_primary_template": _write(-1, 2812885),
         "dependent_template_id_after": {"state": "value", "value": 7249248, "error": None},
         "dependent_flat_colors": {"outcome": "raised"}}
    b = {"step": "B", "detach_primary_template": _write(-1, -1),
         "primary_flat_colors": {"outcome": "took_effect"},
         "dependent_display_style_before": {"state": "value", "value": "HLR", "error": None},
         "dependent_display_style_after": {"state": "value", "value": "FlatColors", "error": None},
         "dependent_followed_primary": True}
    views = [{"view_id": 13663964, "primary_view_id": 12172722, "steps": [a, b]},
             {"view_id": 5, "primary_view_id": None, "refused": "not a dependent view",
              "steps": [{"step": "A", "refused": "not_dependent"}]}]
    _round2_probe(tmp_path, {"q5b": {"views": views}})
    dep, plain = _run(tmp_path)["q5b"]
    assert dep["variants"]["A"]["detach_primary_template"]["took_effect"] is False
    assert dep["variants"]["A"]["dependent_template_id_after"] == 7249248
    assert dep["variants"]["A"]["dependent_flat_colors"] == "raised"
    assert dep["variants"]["B"]["dependent_followed_primary"] is True
    assert dep["variants"]["B"]["dependent_display_style_after"] == "FlatColors"
    assert plain["refused"] == "not a dependent view"
    assert plain["variants"]["A"]["refused"] == "not_dependent"


MARK = [139, 251, 11]
# crop [0, 0, 40, 30] ft exported 400 x 300 px: 10 px per ft, v up. The mark
# from (2, 1.5) to (4, 1.5) is x 20..40, y 300 - 15 = 285; padded by 3 px
# into [x0, y0, x1, y1) -- floor(20) - 3, floor(285) - 3, ceil(40) + 3,
# ceil(285) + 3:
WINDOW = [17, 282, 43, 288]


def _q6_case(tmp_path, draw=True, twin=True, crop_active=True, crop_uv=(0, 0, 40, 30),
             uv=((2.0, 1.5), (4.0, 1.5)), marked_export_ok=True, twin_size=(W * 4, H * 6),
             twin_paint=()):
    w, h = W * 4, H * 6
    e0, e1 = _export("Q6_S0", 9948, w=w), _export("Q6_S1", 9948, w=w,
                                                   state="value" if marked_export_ok else "raised")
    marked = _flat(w, h)
    if draw:
        marked[285, 20:41] = MARK
    images = {"Q6_S1_9948.tiff": marked}
    if twin:
        twin_img = _flat(*twin_size)
        for y, x, rgb in twin_paint:
            twin_img[y, x] = rgb
        images["Q6_S0_9948.tiff"] = twin_img
    row = {"id": 900001, "key": "left_bottom_h", "orientation": "horizontal",
           "uv0": list(uv[0]), "uv1": list(uv[1]), "uv_source": "readback", "painted": True,
           "line_style_id": {"state": "value", "value": 7001, "error": None},
           "line_style_name": {"state": "value", "value": "Thin Lines", "error": None},
           "line_style_category_id": {"state": "value", "value": 7002, "error": None},
           "line_style_category_name": {"state": "value", "value": "Thin Lines", "error": None},
           "lines_category_hidden": {"state": "value", "value": False, "error": None},
           "line_style_subcategory_hidden": {"state": "value", "value": not draw, "error": None},
           "element_hidden": {"state": "raised", "value": None, "error": "X: y"},
           "in_view_collector": {"state": "value", "value": True, "error": None}}
    s1 = {"step": "S1", "export": e1, "mark_rows": [row], "marks_record": {"colour": MARK},
          "crop_uv_at_export": ({"state": "value", "value": list(crop_uv), "error": None}
                                if crop_uv else {"state": "raised", "value": None, "error": "E: no crop"}),
          "crop_box_active_at_export": {"state": "value", "value": crop_active, "error": None},
          "view_filters": {"state": "value", "error": None, "value": [
              {"name": {"state": "value", "value": "Hide lines", "error": None}, "kind": "ParameterFilterElement",
               "enabled": {"state": "value", "value": True, "error": None},
               "visible": {"state": "value", "value": False, "error": None},
               "includes_ost_lines": {"state": "value", "value": True, "error": None}}]},
          "template_vg_control": {"state": "value", "error": None, "value": {
              "state": "value", "template_id": 5823949,
              "model_categories": {"state": "value", "value": True, "error": None},
              "annotation_categories": {"state": "value", "value": True, "error": None},
              "filters": {"state": "value", "value": False, "error": None}}}}
    s0 = {"step": "S0", "export": e0}
    views = [{"view_id": 9948, "steps": [s0, s1, {"step": "S2", "refused": "state_not_restored"},
                                         {"step": "S3", "refused": "state_not_restored",
                                          "refused_reason": "state not restored after no_crop_write"}]}]
    exports = [e0, e1] if twin else [e1]
    _round2_probe(tmp_path, {"q6": {"views": views}}, exports=exports, images=images)
    return _run(tmp_path)["q6"]


def test_q6_a_mark_that_drew_is_rendered_by_its_changed_pixels(tmp_path):
    rows = _q6_case(tmp_path)
    r = rows[0]
    assert r["changed_anywhere_px"] == 21 and r["export_vs_crop"]["matches"] is True
    assert (r["view_id"], r["step"], r["variant"], r["key"]) == (9948, "S1", "no_crop_write", "left_bottom_h")
    assert r["window_px"] == WINDOW
    assert (r["changed_vs_unmarked_px"], r["mark_colour_px"], r["non_white_px"]) == (21, 21, 21)
    assert (r["rendered"], r["rendered_basis"]) == (True, "changed_vs_unmarked")
    assert r["line_style_name"] == "Thin Lines" and r["line_style_subcategory_hidden"] is False
    # An unreadable field is reported unavailable, not as False.
    assert r["element_hidden"] == {"unavailable": "raised: X: y"}
    assert r["view_filters"][0] == {"name": "Hide lines", "kind": "ParameterFilterElement",
                                    "enabled": True, "visible": False, "includes_ost_lines": True}
    assert r["template_vg_control"]["filters"] is False
    # The refused crop-write variant is a row with its reason, no numbers.
    s3 = [x for x in rows if x["step"] == "S3"][0]
    assert s3["mark_id"] is None and s3["refused"] == "state_not_restored"


def test_q6_a_pixel_that_differs_in_one_channel_counts_as_changed(tmp_path):
    """The unmarked twin already had a near-mark pixel at (285, 20): only
    its blue channel differs from the mark. It still changed."""
    r = _q6_case(tmp_path, twin_paint=[(285, 20, [139, 251, 12])])[0]
    assert r["changed_vs_unmarked_px"] == 21


def test_q6_a_mark_that_did_not_draw_is_not_rendered(tmp_path):
    """No pixel changed anywhere against the unmarked twin: false, without
    needing a window."""
    r = _q6_case(tmp_path, draw=False)[0]
    assert (r["changed_anywhere_px"], r["rendered"], r["rendered_basis"]) == (
        0, False, "no_pixel_changed_anywhere")
    assert r["line_style_subcategory_hidden"] is True


def test_q6_an_export_that_is_not_the_crop_is_unmeasured_not_false(tmp_path):
    """Round 2, 9948: the plan exported taller than its crop, so windows
    placed through the crop missed the 137 px the marks changed and read
    false. Crop [0, 0, 40, 25] implies 400 x 250; the image is 400 x 300."""
    r = _q6_case(tmp_path, crop_uv=(0, 0, 40, 25))[0]
    assert r["rendered"] == "unmeasured"
    assert "export is not the crop" in r["unmeasured_reason"]
    assert r["changed_anywhere_px"] == 21
    assert r["export_vs_crop"]["matches"] is False
    assert (r["export_vs_crop"]["implied_px"], r["export_vs_crop"]["non_fit_delta_px"]) == (
        [400, 250], 50)
    assert r["window_px"] is None


def test_q6_nothing_changed_anywhere_is_false_even_when_the_export_is_not_the_crop(tmp_path):
    """Round 2, 5823803 and 11999340: no pixel changed anywhere, so the marks
    did not render -- the mapping does not matter."""
    r = _q6_case(tmp_path, draw=False, crop_uv=(0, 0, 40, 25))[0]
    assert (r["rendered"], r["rendered_basis"]) == (False, "no_pixel_changed_anywhere")
    assert r["export_vs_crop"]["matches"] is False


def test_q6_without_a_same_size_twin_falls_back_to_the_mark_colour(tmp_path):
    r = _q6_case(tmp_path, twin_size=(W, H))[0]
    assert (r["rendered"], r["rendered_basis"], r["changed_vs_unmarked_px"]) == (
        True, "mark_colour", None)


def test_q6_windows_that_cannot_be_measured_say_why(tmp_path):
    cases = {
        "crop_inactive": dict(crop_active=False),
        "crop_unavailable": dict(crop_uv=None),
        "outside": dict(uv=((500.0, 500.0), (510.0, 500.0))),
        "export_failed": dict(marked_export_ok=False),
    }
    expect = {"crop_inactive": "not active at export", "crop_unavailable": "E: no crop",
              "outside": "outside the", "export_failed": "marked export is missing"}
    for name, kw in cases.items():
        r = _q6_case(tmp_path / name, **kw)[0]
        assert r["rendered"] == "unmeasured", name
        assert expect[name] in r["unmeasured_reason"], (name, r["unmeasured_reason"])
        assert (r["window_px"], r["non_white_px"]) == (None, None), name


def test_q6_content_with_no_colour_and_no_twin_is_unmeasured(tmp_path):
    rendered, basis = acs.rendered_verdict(
        {"non_white": 5, "mark_colour": 0, "changed_vs_unmarked": None}, False, True)
    assert rendered == "unmeasured" and "no unmarked export" in basis
    assert acs.rendered_verdict({"non_white": 0, "mark_colour": 0,
                                 "changed_vs_unmarked": None}, False, True) == (
        False, "no_non_white_pixels")


def test_a_round_1_probe_json_still_analyzes_with_empty_round_2_sections(tmp_path):
    """Probe 2026-10-01.x: no write invariant, statuses as digits, no round-2
    questions. 1.1.0 analyzes it; the round-2 sections are empty, not
    errors, and the round-1 Q3 table is as before."""
    e1 = _export("Q3_S1", 11, w=200)
    s1 = _q3_step("S1", e1, written=_box(20, 10), read_back=_box(20, 10))
    s1["writes"] = {"crop_box": {"state": "value", "value": True, "commit_status": "3"}}
    q3 = [{"view_id": 11, "role": "test", "steps": [s1]}]
    _probe(tmp_path, [e1], {"Q3_S1_11.tiff": _flat(200, 100)}, q3=q3)
    rec = _run(tmp_path)
    assert rec["status"] == "value" and rec["tool_version"] == "1.2.1"
    assert (rec["writes"], rec["commit_without_effect"]) == ([], [])
    assert (rec["q1b"], rec["q3b"], rec["q5b"], rec["q6"]) == ([], [], [], [])
    assert rec["q6_authored"] == []
    assert rec["q3"][0]["rows"][0]["non_fit_delta_px"] == 0
    assert rec["images"][0]["non_white_pixels"] == 0


# --- Q6b (probe 2026-10-02.2): the tick-style fix (A) and Lines unhidden (B) --

def _q6b_case(tmp_path, s8_paint=(), s9_draw=True):
    """S6 (detached twin), S8 (Lines unhidden, unmarked), S9 (marked)."""
    w, h = W * 4, H * 6
    e6, e8, e9 = (_export("Q6_S6", 9948, w=w), _export("Q6_S8", 9948, w=w),
                  _export("Q6_S9", 9948, w=w))
    s6_img, s8_img, s9_img = _flat(w, h), _flat(w, h), _flat(w, h)
    for y, x in s8_paint:
        s8_img[y, x] = [0, 0, 0]
        s9_img[y, x] = [0, 0, 0]
    if s9_draw:
        s9_img[285, 20:41] = MARK
    row = {"id": 900001, "key": "left_bottom_h", "orientation": "horizontal",
           "uv0": [2.0, 1.5], "uv1": [4.0, 1.5], "uv_source": "readback", "painted": True}
    crop = {"state": "value", "value": [0, 0, 40, 30], "error": None}
    active = {"state": "value", "value": True, "error": None}
    steps = [
        {"step": "S6", "export": e6},
        {"step": "S8", "export": e8, "writes": {
            "detach": {"state": "value", "took_effect": True},
            "unhide_lines": {"state": "raised", "took_effect": True},
            "hide_existing_lines": {"state": "value", "took_effect": True}}},
        {"step": "S9", "export": e9, "mark_rows": [row], "marks_record": {"colour": MARK},
         "crop_uv_at_export": crop, "crop_box_active_at_export": active}]
    images = {"Q6_S6_9948.tiff": s6_img, "Q6_S8_9948.tiff": s8_img,
              "Q6_S9_9948.tiff": s9_img}
    _round2_probe(tmp_path, {"q6": {"views": [{"view_id": 9948, "steps": steps}]}},
                  exports=[e6, e8, e9], images=images)
    return _run(tmp_path)


def test_q6b_lines_unhidden_ticks_are_judged_against_S8(tmp_path):
    rec = _q6b_case(tmp_path)
    rows = [r for r in rec["q6"] if r["step"] == "S9"]
    assert len(rows) == 1 and rows[0]["variant"] == "lines_unhidden"
    assert rows[0]["rendered"] is True and rows[0]["changed_anywhere_px"] == 21
    # Round 2's S1/S3 are still reported (as not recorded); absent Q6b steps
    # other than these are not.
    assert sorted(r["step"] for r in rec["q6"]) == ["S1", "S3", "S9"]


def test_q6b_a_view_that_still_shows_as_authored_changes_no_pixel(tmp_path):
    rec = _q6b_case(tmp_path)
    (row,) = rec["q6_authored"]
    assert (row["step"], row["twin"], row["changed_px"]) == ("S8", "S6", 0)
    # A write that RAISED is shown raised, even when its read-back matched.
    assert row["writes"] == {
        "detach": {"state": "value", "took_effect": True},
        "unhide_lines": {"state": "raised", "took_effect": True},
        "hide_existing_lines": {"state": "value", "took_effect": True}}


def test_q6b_lines_that_reappear_are_counted(tmp_path):
    """Control: unhiding Lines brought 2 px of linework back."""
    rec = _q6b_case(tmp_path, s8_paint=[(10, 10), (11, 10)])
    assert rec["q6_authored"][0]["changed_px"] == 2



# --- probe 2026-10-02.3: Q6 refuses a production checkout without A and C --

def test_q6_names_the_production_symbols_a_checkout_lacks():
    """Run 20261001T175557 imported a checkout without A and C: the Q6b steps
    raised and S1/S3 measured the OLD tick style. The probe now refuses Q6 on
    such a checkout, naming what is missing. Control: this checkout has all."""
    import types
    from tests.dynamo import probe_capture_state as probe
    from vop_interwoven import color_id_buffer, stage_a_registration
    modules = {"registration": stage_a_registration, "color_id_buffer": color_id_buffer}
    assert probe.q6_missing_symbols(modules) == []
    old = {"registration": types.SimpleNamespace(),
           "color_id_buffer": types.SimpleNamespace()}
    assert probe.q6_missing_symbols(old) == [
        "registration.tick_line_style", "registration._temporary_tick_style",
        "registration.TEMPORARY_TICK_SUBCATEGORY", "color_id_buffer._detach_view_template"]
    ctx = types.SimpleNamespace(production_record={
        "root": "C:/repo", "missing_for_q6": ["registration.tick_line_style"]})
    reason = probe._q6_production_mismatch(ctx)
    assert "C:/repo" in reason and "registration.tick_line_style" in reason
    assert probe._q6_production_mismatch(types.SimpleNamespace(
        production_record={"root": "x", "missing_for_q6": []})) is None
