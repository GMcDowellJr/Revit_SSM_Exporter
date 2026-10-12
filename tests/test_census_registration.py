"""tools/census_registration.py on the shared registration fixture: one
registered view and one whose sidecars record twelve painted ticks that
drew in neither image (2025-06-21 view 3939979's shape). The tool must put
the two stories side by side in one row each -- the sidecar's record and
what the TIFFs hold -- and name every file it could not find."""

import csv
import json

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
    for r in (r1, r2):
        assert r["marks_expected"] == "12"
        assert r["notes"] == ""
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
    assert r4["notes"] == ""
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
