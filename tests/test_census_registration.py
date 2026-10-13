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
    lattice_note = ("content_tilt_union_deg: the annotation TIFF is 480x360, the model TIFF "
                    "700x520; not on one lattice; aabb_corner_white: needs both captures on "
                    "one lattice; ticks_predicted_vs_found_anno: the prediction is on the "
                    "model lattice, which the annotation TIFF is not shown to share")
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
    assert by_stem["V_1"]["registration_sources_verified"] == "true"
    r2 = by_stem["V_2"]
    assert r2["registration_state"] == "stale"
    assert r2["registration_sources_verified"] == "false"
    assert "source_annotation_tiff_sha256 does not match" in r2["registration_reason"]
    assert (r2["anno_found_count"], r2["model_found_count"]) == ("", "")
    assert (r2["ticks_found_model"], r2["ticks_predicted_vs_found_model"]) == ("", "")
    assert "ticks_found_model: no usable model_fit" in r2["notes"]
    assert r2["anno_nonwhite_px"] != ""           # the pixels ARE measured, on the file as is
    assert summary["registration_state"] == {"registered": {"registered": 1},
                                             "stale": {"stale": 1}}


def test_an_absent_source_leaves_the_record_unverified_not_stale(tmp_path):
    """An archive shipped without its TIFFs: the record's hashes cannot be
    checked, which is not a mismatch. Its fit counts are reported, the
    sources are marked unverified, and the absent file is named."""
    run = _run(tmp_path)
    _capture(run, 1, gridded=False, pair_kw={"shared_anno": True})
    (run / "color_id_buffer" / "V_1_anno.tiff").unlink()
    out = tmp_path / "census"
    census.main([str(run), "--out", str(out), "--no-pixels"])
    by_stem, _summary = _rows(out)
    r = by_stem["V_1"]
    assert r["registration_state"] == "registered"
    assert r["registration_sources_verified"] == "false"
    assert (r["anno_found_count"], r["model_found_count"]) == ("12", "12")
    assert "registration sources: the annotation TIFF cannot be hashed now" in r["notes"]


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
                   "anno_tick_windows_white", "anno_ink_rect_angle_deg",
                   "content_tilt_deg", "ticks_inside_polygon_predicted",
                   "ticks_predicted_vs_found_model"):
        assert r[column] == ""
    assert r["registration_state"] == "registered"
    # The found ticks come from the record, so they are reported without images.
    assert r["ticks_found_model"].count("|") == 11 and r["ticks_found_anno"].count("|") == 11


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


def test_edge_tilt_reads_the_long_straight_boundary():
    """A disc of ink cut by one straight boundary at 13 deg: the arc's hull
    edges are short and the cut is the one long edge, so the vote reads 13
    deg; an uncut disc has no edge long enough to vote."""
    h, w = 600, 600
    yy, xx = np.mgrid[0:h, 0:w]
    disc = (xx - 300) ** 2 + (yy - 300) ** 2 < 280 ** 2
    cut = (yy - 300) * math.cos(math.radians(13)) - (xx - 300) * math.sin(math.radians(13)) > -60
    edge, support = census.edge_tilt(disc & cut)
    assert abs(edge - 13.0) <= census.EDGE_VOTE_BIN_DEG
    assert support > 0.5
    assert census.edge_tilt(disc) == (None, 0.0)
    assert census.edge_tilt(np.zeros((50, 50), dtype=bool)) == (None, 0.0)
    # The 06-21 instances that motivate it are in edge_tilt's docstring
    # (4150746, 4610688); they are measured in the run data, not rebuilt here.


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
        assert r["content_tilt_deg"] == "0.0" and r["polygon_tilt_source"] == "model_edges"
        assert r["content_tilt_edge_deg"] == "0.0" and float(r["content_tilt_edge_support"]) > 0
        assert r["content_tilt_union_deg"] == ""
        assert "not on one lattice" in r["notes"]
        assert r["ticks_inside_polygon_predicted"] == "12"
        # Corner whiteness means "no ink in EITHER capture": unmeasured here.
        assert r["aabb_corner_white"] == "" and "aabb_corner_white: needs both" in r["notes"]
        assert r["ticks_predicted_vs_found_anno"] == ""
    assert r1["ticks_found_model"] == "|".join(every_tick)
    assert r1["ticks_predicted_vs_found_model"] == "tp=12,fp=0,fn=0,tn=0"
    assert r2["ticks_found_model"] == "none" and r2["ticks_found_anno"] == "none"
    assert r2["ticks_predicted_vs_found_model"] == "tp=0,fp=12,fn=0,tn=0"
    assert summary["partitions"]["ticks_inside_polygon_predicted"] == {
        "12": {"registered": 1, "refused": 1}}


def test_a_recorded_crop_loop_is_the_polygon_not_the_pixel_tilt():
    """A capture that recorded its loop (2026-10-12 capture change): the
    prediction uses the loop as Revit rendered it. Here the ink is an
    axis-aligned block (tilt 0, which alone would predict all 12 inside) but
    the recorded loop is a rectangle rotated 12.91 deg: no tick inside."""
    import math
    w, h, fpp = 1200, 900, 1.0
    ink = np.zeros((h, w), dtype=bool)
    ink[100:800, 150:1050] = True
    crop = [0.0, 0.0, float(w), float(h)]
    from vop_interwoven.stage_a_registration import registration_mark_segments
    marks = registration_mark_segments(crop, fpp)["segments"]
    c, s = math.cos(math.radians(12.91)), math.sin(math.radians(12.91))
    rw = (w * c - h * s) / math.cos(2 * math.radians(12.91))
    rh = (h * c - w * s) / math.cos(2 * math.radians(12.91))
    loop = [[w / 2 + du * c - dv * s, h / 2 + du * s + dv * c]
            for du, dv in ((-rw / 2, -rh / 2), (rw / 2, -rh / 2), (rw / 2, rh / 2), (-rw / 2, rh / 2))]
    masks = {"model": ink, "anno": ink.copy()}
    one = (True, "test: one lattice")
    plain, _n = census.rendered_region_columns(masks, marks, crop, {}, lattice=one)
    assert plain["ticks_inside_polygon_predicted"] == 12 and plain["polygon_tilt_source"] == "union_edges"
    with_loop, _n = census.rendered_region_columns(masks, marks, crop, {}, loop_uv=loop,
                                                   lattice=one)
    assert with_loop["polygon_tilt_source"] == "recorded_loop"
    # On this 1200 x 900 box one mid tick sits near the loop's tangent point
    # (the run's partial refusals: mid_bottom_v found on 7 of 8); every corner
    # tick and side-mid tick is outside.
    inside = set(with_loop["ticks_inside_polygon_names"].split("|")) - {"none"}
    assert with_loop["ticks_inside_polygon_predicted"] <= 1
    assert inside <= {"mid_bottom_v", "mid_top_v", "left_mid_h", "right_mid_h"}
    sidecar = {"registration_marks": {"crop_shape": {"state": "value", "loops_uv": [loop]}}}
    assert census.recorded_loop(sidecar) == loop
    assert census.recorded_loop({"registration_marks": {"crop_shape": {"state": "unavailable"}}}) is None


# --- Codex, PR #236 -------------------------------------------------------------------------

def test_a_singular_or_impossible_tilt_gives_no_polygon():
    """At 45 deg the inversion divides by cos 90; near it a non-square box
    holds no rectangle at that tilt. Neither is a polygon of 1e18 px."""
    assert census.inscribed_polygon(1200, 900, 45.0) is None
    assert census.inscribed_polygon(1200, 900, 44.0) is None       # a side comes out negative
    assert census.inscribed_polygon(1000, 1000, 44.0) is not None  # a square still holds one
    ink = np.zeros((900, 1200), dtype=bool)
    ink[400:500, 500:700] = True
    poly, reason = census.rendered_polygon(ink, 45.0)
    assert poly is None and "no rectangle" in reason


def test_an_ambiguous_rotation_direction_is_unmeasured_not_the_first():
    """Centred ink lies inside both mirrored polygons: the direction cannot
    be told, and the prediction says so rather than keeping '+'."""
    ink = np.zeros((900, 1200), dtype=bool)
    ink[400:500, 500:700] = True
    poly, reason = census.rendered_polygon(ink, 12.91)
    assert poly is None and reason.startswith("rotation direction ambiguous")
    # Ink hugging one tilted side decides it.
    decided = np.zeros((900, 1200), dtype=bool)
    for x in range(100, 1100):
        y = int(50 + x * 0.2292)      # tan 12.91 deg
        decided[y:y + 3, x] = True
    poly, reason = census.rendered_polygon(decided, 12.91)
    assert poly is not None and reason.startswith("direction")


def test_the_lattice_is_shown_by_the_registration_or_assumed_from_equal_frames():
    model = {"frame": {"crop_uv": [0, 0, 10, 8], "actual_w": 100, "actual_h": 80}}
    anno_same = {"frame": {"crop_uv": [0, 0, 10, 8], "actual_w": 100, "actual_h": 80}}
    anno_other = {"frame": {"crop_uv": [0, 0, 12, 8], "actual_w": 100, "actual_h": 80}}
    identity = {"annotation_to_model_px": {"scale_x": 1.0, "scale_y": 1.0,
                                           "offset_x": 0.0, "offset_y": 0.0}}
    shifted = {"annotation_to_model_px": {"scale_x": 1.0, "scale_y": 1.0,
                                          "offset_x": 3.0, "offset_y": 0.0}}
    assert census.shared_lattice(model, anno_other, identity)[0] is True
    assert census.shared_lattice(model, anno_same, shifted)[0] is False
    shared, why = census.shared_lattice(model, anno_same, None)
    assert shared is True and why.startswith("assumed")
    assert census.shared_lattice(model, anno_other, None) == (
        False, "unregistered, and the frames record different crops")
    assert census.shared_lattice(model, None, None) == (False, "no annotation sidecar")
    # The registered capture's annotation frame records no crop (3939979,
    # 06-21): both passes rendered at crop A, so an equal size is enough.
    reg_model = dict(model, registration_marks={"marks": []})
    reg_anno = {"frame": {"actual_w": 100, "actual_h": 80}, "registration_marks": {"marks": []}}
    shared, why = census.shared_lattice(reg_model, reg_anno, None)
    assert shared is True and why.startswith("assumed: registered capture")
    smaller = {"frame": {"actual_w": 100, "actual_h": 60}, "registration_marks": {"marks": []}}
    assert census.shared_lattice(reg_model, smaller, None) == (
        False, "unregistered, and the frames record different sizes")
    # Equal dimensions alone do not make a union: the assumption is noted.
    ink = np.zeros((80, 100), dtype=bool)
    ink[10:70, 10:90] = True
    cols, notes = census.rendered_region_columns({"model": ink, "anno": ink.copy()}, [], None,
                                                 {}, lattice=(True, "assumed: test"))
    assert cols["polygon_tilt_source"].startswith("union") and any(
        n.startswith("content_tilt_union_deg: lattice assumed") for n in notes)
    cols, notes = census.rendered_region_columns({"model": ink, "anno": ink.copy()}, [], None,
                                                 {}, lattice=(False, "registered: not identity"))
    assert "content_tilt_union_deg" not in cols and "aabb_corner_white" not in cols
    assert any("not shown to be on the model lattice" in n for n in notes)


def test_marks_painted_is_unknown_when_no_mark_records_it():
    cols, _marks = census.marks_columns({"registration_marks": {"marks": [
        {"key": "a"}, {"key": "b"}]}})
    assert cols["marks_expected"] == 2 and cols["marks_painted"] is None
    cols, _marks = census.marks_columns({"registration_marks": {"marks": [
        {"key": "a", "painted": True}, {"key": "b", "painted": False}]}})
    assert cols["marks_painted"] == 1


def test_the_ink_handed_to_the_region_measures_excludes_the_ticks_fringes(tmp_path):
    """The exact-colour mask alone leaves a tick's anti-aliased fringe in the
    ink; the fit's own attribution (mark_ink_mask) removes it. Pinned on the
    fixture: no ink pixel inside any located tick's pixel rect."""
    run = _run(tmp_path)
    _capture(run, 1, gridded=False, pair_kw={"shared_anno": True})
    model_path = run / "color_id_buffer" / "V_1.json"
    model = json.loads(model_path.read_text())
    from tools import decode_stage_a_color_id as dsc
    tiff = dsc._resolve_tiff_path(model_path, model)
    marks = model["registration_marks"]["marks"]
    crop = census.crop_columns(model)[1]
    cols, note, ink = census.image_columns(tiff, "model", model, [], marks, crop)
    reg = json.loads((run / "color_id_buffer" / "V_1_anno.registered.json").read_text())
    rects = reg["model_fit"]["mark_pixel_rects"]
    assert rects, "the fixture's fit locates its ticks"
    for rect in rects:
        x0, y0, x1, y1 = (int(v) for v in (rect if isinstance(rect, list) else rect["rect"]))
        assert not ink[y0:y1 + 1, x0:x1 + 1].any(), rect
    assert cols["model_tick_px"] > 0
