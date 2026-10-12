"""tools/census_registration.py on the shared registration fixture: one
registered view and one whose sidecars record twelve painted ticks that
drew in neither image (2025-06-21 view 3939979's shape). The tool must put
the two stories side by side in one row each -- the sidecar's record and
what the TIFFs hold -- and name every file it could not find."""

import csv
import json
import math

import numpy as np

from tests.test_stage_a_grid_rollup import _capture, _outcome, _run
from tools import census_registration as census


def _rows(out):
    with open(str(out / census.CSV_NAME), encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    summary = json.loads((out / census.SUMMARY_NAME).read_text())
    return dict((r["view_stem"], r) for r in rows), summary


def test_recorded_ticks_and_drawn_ticks_are_one_row(tmp_path):
    run = _run(tmp_path)
    # shared_anno: the reserved tick colour in both captures, as production
    # paints them since 2026-10-02.
    _capture(run, 1, gridded=False, pair_kw={"shared_anno": True})
    every_tick = tuple(m["key"] for m in
                       json.loads((run / "color_id_buffer" / "V_1.json").read_text())
                       ["registration_marks"]["marks"])
    _capture(run, 2, gridded=False,
             pair_kw={"drop_anno": every_tick, "drop_model": every_tick,
                      "shared_anno": True})
    _capture(run, 3, gridded=False, register=False, pair_kw={"shared_anno": True})
    # The older annotation shape: each tick its own palette colour, read from
    # the sidecar, never assumed to be the reserved colour.
    _capture(run, 4, gridded=False)
    out = tmp_path / "census"
    assert census.main([str(run), "--out", str(out)]) == 0
    by_stem, summary = _rows(out)
    assert summary["denominator"] == 4
    assert set(by_stem) == {"V_1", "V_2", "V_3", "V_4"}
    r1, r2, r3, r4 = by_stem["V_1"], by_stem["V_2"], by_stem["V_3"], by_stem["V_4"]
    # The sidecars tell the same story for both views...
    # The shared fixture's annotation image is on its own (frame-B-era)
    # lattice, which the union tilt declines by name; nothing else is noted.
    lattice_note = "content_tilt_union_deg: the annotation TIFF is 480x360, the model TIFF " \
                   "700x520; not on one lattice"
    for r in (r1, r2):
        assert r["marks_expected"] == "12"
        assert r["notes"] == lattice_note
    # ...the registration and the images do not.
    assert (r1["registration_state"], r1["anno_found_count"],
            r1["model_found_count"]) == ("registered", "12", "12")
    assert (r2["registration_state"], r2["anno_found_count"],
            r2["model_found_count"]) == ("refused", "0", "0")
    assert "0 of 12 ticks found" in r2["registration_reason"]
    assert int(r1["model_tick_px"]) > 0 and int(r1["anno_tick_px"]) > 0
    assert (r2["model_tick_px"], r2["anno_tick_px"]) == ("0", "0")
    # The model capture is on crop A's lattice, where the windows are placed.
    # (The shared fixture's annotation image is on its own, frame-B-era
    # lattice, so its windows are not asserted for the registered view.)
    assert r1["model_tick_windows_white"] == "0"
    assert (r2["anno_tick_windows_white"], r2["model_tick_windows_white"]) == ("12", "12")
    # Every window lay on the image: the off-image count is 0, and reported.
    for r in (r1, r2):
        assert (r["anno_tick_windows_off_image"], r["model_tick_windows_off_image"]) == ("0", "0")
    # Per-tick colours (the older shape) are counted from the sidecar's record.
    assert r4["registration_state"] == "registered"
    assert int(r4["anno_tick_px"]) > 0 and int(r4["model_tick_px"]) > 0
    assert r4["notes"] == lattice_note
    # The ink rectangle is measured on both (the fixture's ink is two small
    # rectangles, whose hull's minimum-area box is tilted: the measure says
    # something about a crop only when ink reaches the crop's edges).
    for r in (r1, r2):
        assert 0.0 <= float(r["anno_ink_rect_angle_deg"]) < 90.0
        assert 0.0 < float(r["anno_ink_rect_fill"]) < 1.0
    assert r1["anno_ink_rect_angle_deg"] == r2["anno_ink_rect_angle_deg"]  # same ink
    assert int(r1["anno_palette_px"]) > 0 and r1["anno_assigned_colours"] == "2"
    # An absence is named by file, never blank.
    assert r3["registration_state"] == "absent"
    assert "no V_3_anno.registered.json" in r3["notes"]
    assert "ticks_found_anno: no usable annotation_fit" in r3["notes"]
    assert r3["anno_found_count"] == ""
    assert summary["partitions"]["model_tick_windows_white"] == {
        "0": {"registered": 2, "absent": 1}, "12": {"refused": 1}}
    assert all(r["provenance"] == "this_run" for r in (r1, r2, r3, r4))


def test_a_registration_record_of_other_files_is_stale_not_reported(tmp_path):
    """A capture rerun in place without re-registering: the record's source
    hashes no longer match the files beside it. Its state is stale, its
    reason names the hash, and its fit counts are not set beside pixels it
    was never fitted on. Control: an untouched record stays registered."""
    run = _run(tmp_path)
    _capture(run, 1, gridded=False, pair_kw={"shared_anno": True})
    _capture(run, 2, gridded=False, pair_kw={"shared_anno": True})
    tiff = run / "color_id_buffer" / "V_2_anno.tiff"
    tiff.write_bytes(tiff.read_bytes() + b"\0")
    out = tmp_path / "census"
    census.main([str(run), "--out", str(out)])
    by_stem, summary = _rows(out)
    assert by_stem["V_1"]["registration_state"] == "registered"
    r2 = by_stem["V_2"]
    assert r2["registration_state"] == "stale"
    assert "source_annotation_tiff_sha256 does not match" in r2["registration_reason"]
    assert (r2["anno_found_count"], r2["model_found_count"]) == ("", "")
    assert (r2["ticks_found_model"], r2["ticks_predicted_vs_found_model"]) == ("", "")
    assert "ticks_found_model: no usable model_fit" in r2["notes"]
    assert r2["anno_nonwhite_px"] != ""           # the pixels ARE measured, on the file as is
    assert summary["registration_state"] == {"registered": {"registered": 1},
                                             "stale": {"stale": 1}}


def test_a_sidecar_of_a_failed_capture_is_not_this_runs(tmp_path):
    """run_meta.json records the view's capture as failed; the sidecar in the
    folder is a previous capture's. The row says so and is left out of the
    partitions. Control: a successful outcome is this_run."""
    run = _run(tmp_path)
    _capture(run, 1, gridded=False, pair_kw={"shared_anno": True})
    _capture(run, 2, gridded=False, pair_kw={"shared_anno": True})
    _outcome(run, 2, status="failed", reason="export raised")
    out = tmp_path / "census"
    census.main([str(run), "--out", str(out)])
    by_stem, summary = _rows(out)
    assert (by_stem["V_1"]["provenance"], by_stem["V_1"]["run_meta_capture_status"]) == (
        "this_run", "success")
    assert (by_stem["V_2"]["provenance"], by_stem["V_2"]["run_meta_capture_status"]) == (
        "not_this_run", "failed")
    assert summary["provenance"] == {"this_run": {"registered": 1},
                                     "not_this_run": {"registered": 1}}
    assert summary["registration_state"] == {"registered": {"registered": 1}}
    assert summary["partitions"]["model_tick_windows_white"] == {"0": {"registered": 1}}


def test_a_window_off_the_image_is_counted_as_off_image_not_only_white():
    nonwhite = np.zeros((40, 40), dtype=bool)
    mapping = {"a_u": 1.0, "b_u": 0.0, "a_v": -1.0, "b_v": 40.0}
    marks = [{"uv0": [5.0, 20.0], "uv1": [15.0, 20.0]},     # on the image, white
             {"uv0": [100.0, 20.0], "uv1": [110.0, 20.0]}]  # entirely off it
    assert census.tick_windows_white(nonwhite, marks, mapping) == (2, 1)
    nonwhite[20, 10] = True
    assert census.tick_windows_white(nonwhite, marks, mapping) == (1, 1)


def test_no_pixels_leaves_every_image_column_blank(tmp_path):
    run = _run(tmp_path)
    _capture(run, 1, gridded=False)
    out = tmp_path / "census"
    assert census.main([str(run), "--out", str(out), "--no-pixels"]) == 0
    by_stem, _summary = _rows(out)
    r = by_stem["V_1"]
    for column in ("model_tick_px", "anno_tick_px", "anno_palette_px",
                   "anno_tick_windows_white", "anno_ink_rect_angle_deg"):
        assert r[column] == ""
    assert r["registration_state"] == "registered"


# --- the rendered region (2026-10-12 follow-up) ---------------------------------

def test_the_inscribed_polygon_has_the_box_as_its_bounding_box():
    """Run 2025-06-21 view 3939979: a 4157 x 3715 box at 12.91 deg holds a
    2991 x 3268 px rectangle (the 06-13 crop, 3579 x 2991, rotated)."""
    for sign in (1, -1):
        poly = census.inscribed_polygon(4157, 3715, 12.91, sign)
        xs, ys = [x for x, _y in poly], [y for _x, y in poly]
        assert abs(min(xs)) < 1e-6 and abs(max(xs) - 4157) < 1e-6
        assert abs(min(ys)) < 1e-6 and abs(max(ys) - 3715) < 1e-6
        sides = sorted(round(math.hypot(poly[i][0] - poly[(i + 1) % 4][0],
                                        poly[i][1] - poly[(i + 1) % 4][1]))
                       for i in range(4))
        assert sides == [2991, 2991, 3579, 3579]
    assert census.inscribed_polygon(100, 50, 0) == [(0.0, 0.0), (100.0, 0.0),
                                                    (100.0, 50.0), (0.0, 50.0)]


def test_ticks_on_a_rotated_crops_box_are_predicted_outside_and_on_its_rectangle_inside():
    """The twelve nominal ticks of a 4157 x 3715 box: all inside at tilt 0,
    none inside at 12.91 deg (3939979, 06-21: 0 of 12 found)."""
    from vop_interwoven.stage_a_registration import registration_mark_segments
    w, h, fpp = 4157, 3715, 0.33330989910698183
    crop = [0.0, 0.0, w * fpp, h * fpp]
    marks = registration_mark_segments(crop, fpp)["segments"]
    mapping = {"a_u": 1.0 / fpp, "b_u": 0.0, "a_v": -1.0 / fpp, "b_v": float(h)}
    flat = census.inscribed_polygon(w, h, 0)
    assert len(census.ticks_inside(flat, marks, mapping)) == 12
    for sign in (1, -1):
        tilted = census.inscribed_polygon(w, h, 12.91, sign)
        assert census.ticks_inside(tilted, marks, mapping) == []


def test_found_ticks_and_confusion_read_the_fits_missing_list():
    marks = [{"key": "a"}, {"key": "b"}, {"key": "c"}]
    assert census.found_ticks({"missing": [{"key": "b"}]}, marks) == ["a", "c"]
    assert census.found_ticks({"found_count": 2}, marks) is None
    assert census.confusion(["a", "b"], ["a", "c"], ["a", "b", "c"]) == "tp=1,fp=1,fn=1,tn=0"


def test_rendered_region_columns_on_the_fixture(tmp_path):
    """Axis-aligned fixture: tilt 0.00, every tick predicted inside; the
    registered view finds all 12 (tp 12), the tickless one none (fp 12: the
    prediction says where a tick WOULD render, not whether it drew)."""
    run = _run(tmp_path)
    _capture(run, 1, gridded=False, pair_kw={"shared_anno": True})
    every_tick = tuple(m["key"] for m in
                       json.loads((run / "color_id_buffer" / "V_1.json").read_text())
                       ["registration_marks"]["marks"])
    _capture(run, 2, gridded=False,
             pair_kw={"drop_anno": every_tick, "drop_model": every_tick,
                      "shared_anno": True})
    out = tmp_path / "census"
    census.main([str(run), "--out", str(out)])
    by_stem, summary = _rows(out)
    r1, r2 = by_stem["V_1"], by_stem["V_2"]
    for r in (r1, r2):
        # The fixture's two images are on different lattices, so the model
        # TIFF alone is the tilt source and the union column is blank, by name.
        assert r["content_tilt_deg"] == "0.0" and r["polygon_tilt_source"] == "model"
        assert r["content_tilt_union_deg"] == ""
        assert "not on one lattice" in r["notes"]
        assert r["ticks_inside_polygon_predicted"] == "12"
        assert r["aabb_corner_white"].count(":") == 4
    assert r1["ticks_found_model"] == "|".join(every_tick)
    assert r1["ticks_predicted_vs_found_model"] == "tp=12,fp=0,fn=0,tn=0"
    assert r2["ticks_found_model"] == "none" and r2["ticks_found_anno"] == "none"
    assert r2["ticks_predicted_vs_found_model"] == "tp=0,fp=12,fn=0,tn=0"
    assert summary["partitions"]["ticks_inside_polygon_predicted"] == {
        "12": {"registered": 1, "refused": 1}}
