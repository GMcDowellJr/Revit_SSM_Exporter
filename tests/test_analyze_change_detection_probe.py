"""The change-detection probe's pure parts and its offline analyzer
(tests/dynamo/probe_change_detection.py, tools/analyze_change_detection_probe.py).

The probe needs Revit; what is tested here is the arithmetic it does with
what Revit returns (the sample, the signature check, the per-view signals)
and the analyzer end to end on synthetic runs: TIFFs drawn here, sidecars in
the shape the capture writes, a probe JSON in the shape the probe writes.
Every analyzer assertion reads the analysis JSON the tool WROTE (CLAUDE.md,
defect class 4).
"""
import json

import numpy as np
import pytest
from PIL import Image

from tests.dynamo import probe_change_detection as probe
from tools import analyze_change_detection_probe as acd

WHITE = (255, 255, 255)
W, H = 40, 30


# --- the probe's pure parts --------------------------------------------------------

def test_sample_is_seeded_round_robin_across_types_and_skips_templates():
    views = ([(100 + i, "FloorPlan", False) for i in range(10)]
             + [(200 + i, "Section", False) for i in range(3)]
             + [(300, "FloorPlan", True), (400, "ThreeD", False)])
    ids, rec = probe.sample_view_ids(views, ("FloorPlan", "Section"), 8, 7)
    again, _ = probe.sample_view_ids(list(reversed(views)), ("FloorPlan", "Section"), 8, 7)
    assert ids == again                       # population order does not matter
    assert len(ids) == 8 and len(set(ids)) == 8
    assert 300 not in ids and 400 not in ids  # template, and a type not asked for
    assert rec["drawn_per_type"] == {"FloorPlan": 5, "Section": 3}
    # Round robin: the first two draws are one of each type.
    assert {ids[0] // 100, ids[1] // 100} == {1, 2}
    other, _ = probe.sample_view_ids(views, ("FloorPlan", "Section"), 8, 8)
    assert other != ids                       # the seed is what fixes it


def test_sample_smaller_population_than_size_takes_everything():
    ids, rec = probe.sample_view_ids([(1, "Legend", False), (2, "Legend", False)],
                                     ("Legend",), 50, 1)
    assert sorted(ids) == [1, 2] and rec["size_drawn"] == 2


def _m(name, static, ret, params):
    return probe.method_record(name, static, ret, params)


def test_expected_shape_accepts_the_two_known_shapes_and_names_the_rest():
    static = _m("GetDocumentVersion", True, "Autodesk.Revit.DB.DocumentVersion",
                [("Autodesk.Revit.DB.Document", "doc")])
    changed = _m("GetChangedElements", False, "Autodesk.Revit.DB.ChangedElements",
                 [("System.Guid", "versionGuid")])
    assert probe.expected_shape([static, changed], "GetDocumentVersion")[1] == "ok"
    assert probe.expected_shape([static, changed], "GetChangedElements")[1] == "ok"
    assert probe.expected_shape([static], "GetChangedElements") == (None, "absent")
    odd = _m("GetChangedElements", False, "X", [("System.String", "s")])
    chosen, why = probe.expected_shape([odd], "GetChangedElements")
    assert chosen is None and "System.String" in why


def test_view_signals_or_of_api_signals_and_membership_diff_beside_it():
    rec = probe.view_signals(10, members_b=[1, 2, 3], members_a=[1, 2, 4], template_id=50,
                             filter_ids=[60], type_ids=[70, 71], created=[], modified=[71],
                             deleted=[])
    assert rec["fired"] == ["member_types"] and rec["api_verdict"] == "changed"
    assert rec["signals"]["member_types"]["ids"] == [71]
    assert rec["membership_diff"]["added"] == 1 and rec["membership_diff"]["removed"] == 1
    # A membership change alone is NOT the API saying changed.
    quiet = probe.view_signals(10, [1, 2, 3], [1, 2, 4], -1, [], [], [], [], [])
    assert quiet["api_verdict"] == "unchanged" and quiet["membership_diff"]["added"] == 1


def test_view_signals_deleted_is_read_against_a_membership_and_unread_is_unknown():
    rec = probe.view_signals(10, [1], [1, 9], None, [], [], [], [], [9])
    assert rec["fired"] == ["a_members_deleted"]
    unknown = probe.view_signals(10, [1], None, -1, [], [], [], [], [])
    assert unknown["api_verdict"] == "unknown"            # A membership unread
    assert unknown["signals"]["a_members_deleted"] is None
    itself = probe.view_signals(10, [1], [1], -1, [], [], [], [10], [])
    assert itself["fired"] == ["view_itself"]


def test_options_refuse_unknown_keys_and_bad_values():
    # Run 20261026T084811: empty IN[4] returned {} and the open raised
    # KeyError 'worksets'. Every default is filled in, empty or not.
    for empty in (None, "", {}):
        opts = probe.parse_options(empty)
        assert opts["worksets"] == "all" and opts["control"] == "reopen"
        assert opts["seed"] == probe.DEFAULT_SEED
        assert opts["sample_size"] == probe.DEFAULT_SAMPLE_SIZE
    opts = probe.parse_options('{"seed": 3, "control": "none"}')
    assert opts["seed"] == 3 and opts["control"] == "none" and opts["worksets"] == "all"
    with pytest.raises(ValueError):
        probe.parse_options({"sed": 1})
    with pytest.raises(ValueError):
        probe.parse_options({"control": "attached"})
    assert probe.parse_view_ids("5, 6;6") == [5, 6]


def test_file_fingerprint_sees_a_one_byte_change(tmp_path):
    p = tmp_path / "m.rvt"
    p.write_bytes(b"abc")
    before = probe.file_fingerprint(str(p))
    assert probe.fingerprints_changed(before, probe.file_fingerprint(str(p))) == []
    p.write_bytes(b"abd")
    assert "sha256" in probe.fingerprints_changed(before, probe.file_fingerprint(str(p)))


# --- the analyzer on synthetic runs ---------------------------------------------------

def _tiff(path, img):
    Image.fromarray(np.asarray(img, dtype=np.uint8), mode="RGB").save(str(path), format="TIFF")


def _canvas():
    return np.full((H, W, 3), WHITE, dtype=np.uint8)


def _view(run, vid, rects, colours, links=(), frame=None, bboxes=None, anno=True):
    """One captured view in ``run``: ``rects`` {element id: (x0, y0, x1, y1)}
    painted in ``colours`` {element id: rgb}; ``links`` [(category, rgb, rect)]."""
    cap = run / "color_id"
    cap.mkdir(parents=True, exist_ok=True)
    img = _canvas()
    for eid, (x0, y0, x1, y1) in rects.items():
        img[y0:y1, x0:x1] = colours[eid]
    for _name, rgb, (x0, y0, x1, y1) in links:
        img[y0:y1, x0:x1] = rgb
    stem = "V_{0}".format(vid)
    _tiff(cap / (stem + ".tiff"), img)
    host = dict((str(e), {"bbox_3d": {"min": [0, 0, 0], "max": [1, 1, 1]},
                          "uv_rect": [0, 0, 1, 1], "category": "Walls"}) for e in rects)
    host.update(bboxes or {})
    side = {"view_id": vid, "pass": "model", "tiff_path": str(cap / (stem + ".tiff")),
            "color_assignment_map": dict((str(e), list(colours[e])) for e in rects),
            "link_category_color_map": dict((n, list(c)) for n, c, _r in links),
            "near_face_w_map": {"host": host},
            "frame": frame or {"view_scale": 96, "fit_direction": "horizontal",
                               "pixel_size": W, "actual_w": W, "actual_h": H,
                               "crop_uv": [0, 0, 10, 7.5]}}
    (cap / (stem + ".json")).write_text(json.dumps(side))
    if anno:
        aimg = _canvas()
        aimg[2:4, 2:20] = (10, 20, 30)
        _tiff(cap / (stem + "_anno.tiff"), aimg)
        (cap / (stem + "_anno.json")).write_text(json.dumps({
            "view_id": vid, "pass": "annotation",
            "tiff_path": str(cap / (stem + "_anno.tiff")),
            "color_assignment_map": {"900": [10, 20, 30]},
            "annotation_bbox_map": {"900": {"bbox_uv": {"state": "value",
                                                        "value": [[0, 0], [1, 0], [1, 1], [0, 1]]},
                                            "category": "Text Notes"}}}))


def _probe(folder, views, finalized=True):
    folder.mkdir(parents=True, exist_ok=True)
    rows = [dict({"view_id": vid, "present_in_b": True, "name": {"state": "value",
                                                                 "value": "V{0}".format(vid)},
                  "fired": [] if verdict == "unchanged" else ["member_types"],
                  "unread": []}, api_verdict=verdict) for vid, verdict in views]
    path = folder / "probe_change_detection_20261024T000000.json"
    path.write_text(json.dumps({"schema": probe.SCHEMA, "finalized": finalized,
                                "per_view_against": "b_detached__vs__a_detached",
                                "views": rows}))
    return folder


RED, GREEN, BLUE = (200, 0, 0), (0, 200, 0), (0, 0, 200)


@pytest.fixture
def runs(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    # 1: identical content, DIFFERENT colour assignment: same once decoded.
    _view(a, 1, {11: (0, 0, 10, 10)}, {11: RED})
    _view(b, 1, {11: (0, 0, 10, 10)}, {11: GREEN})
    # 2: API says unchanged, element 21's pixels moved, its bbox did not.
    _view(a, 2, {21: (0, 0, 10, 10)}, {21: RED})
    _view(b, 2, {21: (5, 0, 15, 10)}, {21: RED})
    # 3: API says unchanged, the frame (scale) changed.
    _view(a, 3, {31: (0, 0, 10, 10)}, {31: RED})
    _view(b, 3, {31: (0, 0, 10, 10)}, {31: RED},
          frame={"view_scale": 48, "fit_direction": "horizontal", "pixel_size": W,
                 "actual_w": W, "actual_h": H, "crop_uv": [0, 0, 10, 7.5]})
    # 4: a linked category in a different colour per run, unchanged; then
    #    a second category grows in B: the change is link pixels only.
    _view(a, 4, {41: (0, 0, 5, 5)}, {41: RED},
          links=[("Walls", BLUE, (10, 10, 20, 20)), ("Doors", GREEN, (25, 0, 30, 5))])
    _view(b, 4, {41: (0, 0, 5, 5)}, {41: RED},
          links=[("Walls", (1, 2, 3), (10, 10, 20, 20)), ("Doors", (4, 5, 6), (25, 0, 32, 5))])
    # 5: API says changed and nothing differs.
    _view(a, 5, {51: (0, 0, 10, 10)}, {51: RED})
    _view(b, 5, {51: (0, 0, 10, 10)}, {51: RED})
    # 6: captured in A only.
    _view(a, 6, {61: (0, 0, 10, 10)}, {61: RED})
    pdir = _probe(tmp_path / "probe", [(1, "unchanged"), (2, "unchanged"), (3, "unchanged"),
                                       (4, "unchanged"), (5, "changed"), (6, "unchanged")])
    assert acd.main([str(pdir), "--run-a", str(a), "--run-b", str(b)]) == 0
    record = json.loads((pdir / acd.ANALYSIS_NAME).read_text())
    return dict((r["view_id"], r) for r in record["views"]), record


def test_recoloured_but_identical_capture_is_the_same(runs):
    rows, _ = runs
    assert rows[1]["capture_differs"] is False
    assert rows[1]["model_pixels"]["differing_px"] == 0


def test_false_unchanged_is_listed_with_what_differs(runs):
    rows, record = runs
    assert rows[2]["capture_differs"] is True
    assert rows[2]["model_pixels"]["differing_px"] == 2 * 5 * 10   # 50 px lost, 50 gained
    assert rows[2]["cause_inference"] == ["type_or_graphics"]
    assert rows[3]["cause_inference"] == ["view_setting"]
    assert rows[4]["cause_inference"] == ["link"]
    assert rows[4]["model_pixels"]["link_px"] == 2 * 5             # Doors grew 2 x 5
    assert sorted(f["view_id"] for f in record["false_unchanged"]) == [2, 3, 4]
    assert record["false_unchanged_count"] == 3


def test_table_crosses_api_with_capture_and_keeps_the_not_comparable(runs):
    rows, record = runs
    assert record["table"]["unchanged"] == {"differs": 3, "same": 1, "not_comparable": 1}
    assert record["table"]["changed"] == {"differs": 0, "same": 1, "not_comparable": 0}
    assert record["api_changed_capture_same"] == [5]
    assert "run B" in rows[6]["not_comparable"]
    assert record["status"] == "value" and len(record["probe_json_sha256"]) == 64


def test_bbox_move_is_an_element_change(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    _view(a, 7, {71: (0, 0, 10, 10)}, {71: RED})
    _view(b, 7, {71: (0, 0, 10, 10)}, {71: RED},
          bboxes={"71": {"bbox_3d": {"min": [0, 0, 0], "max": [2, 1, 1]},
                         "uv_rect": [0, 0, 1, 1]}})
    pdir = _probe(tmp_path / "probe", [(7, "unchanged")])
    assert acd.main([str(pdir), "--run-a", str(a), "--run-b", str(b)]) == 0
    row = json.loads((pdir / acd.ANALYSIS_NAME).read_text())["views"][0]
    assert row["model_bbox"]["moved_ids"] == ["71"] and row["cause_inference"] == ["element"]


def test_unfinished_probe_is_refused(tmp_path):
    pdir = _probe(tmp_path / "probe", [(1, "unchanged")], finalized=False)
    assert acd.main([str(pdir), "--run-a", str(tmp_path), "--run-b", str(tmp_path)]) == 2
    assert not (pdir / acd.ANALYSIS_NAME).exists()


def test_cloud_central_is_read_from_basic_file_info():
    def info(central):
        return {"state": "value", "value": {"CentralPath": {"state": "value",
                                                            "value": central}}}
    # The archives of run 20261026T084944.
    path = "Autodesk Docs://000000000_Kaizen_Standard_Resources/KSRF_V56.rvt"
    assert probe.cloud_central(info(path)) == path
    assert probe.cloud_central(info("\\\\server\\share\\central.rvt")) is None
    assert probe.cloud_central(info("")) is None
    assert probe.cloud_central({"state": "raised", "value": None}) is None


class _NumberViewType(object):
    """A ViewType as Dynamo's Python engine handed it over in run
    20261026T095313: its ToString() gives the NUMBER, so production's
    batch_capture._view_type_name returned "1", "117", ... for every view."""
    def __init__(self, number):
        self.number = number

    def ToString(self):
        return str(self.number)


class _Id(object):
    def __init__(self, value):
        self.Value = value


class _View(object):
    def __init__(self, vid, number, template=False):
        self.Id, self.ViewType, self.IsTemplate = _Id(vid), _NumberViewType(number), template


NAMES = {1: "FloorPlan", 117: "Section", 4: "ThreeD"}


def test_sample_draws_when_the_production_reader_returns_numbers():
    from vop_interwoven import batch_capture as bc
    views = [_View(1, 1), _View(2, 1), _View(3, 117), _View(4, 1, template=True),
             _View(5, 4)]
    # The failure being fixed: production's selection keeps nothing here.
    assert bc.select_views(views)[1]["count"] == 0

    def type_name(v):
        return probe.resolve_view_type_name(bc._view_type_name(v), NAMES.get)
    ids, rec = probe.sample_view_ids(probe.sample_population(views, type_name),
                                     bc.DEFAULT_VIEW_TYPES, 50, 1)
    assert sorted(ids) == [1, 2, 3]
    assert rec["population"] == {"FloorPlan": 2, "Section": 1}


def test_resolve_view_type_name_prefers_a_real_name_and_says_how():
    assert probe.resolve_view_type_name("FloorPlan", NAMES.get) == (
        "FloorPlan", "production_reader")
    assert probe.resolve_view_type_name("117", NAMES.get) == ("Section", "enum_lookup")
    assert probe.resolve_view_type_name("999", NAMES.get)[0] is None
    assert probe.resolve_view_type_name(None, NAMES.get) == (None, "unreadable")


def _inputs(members, template=-1, filters=(), types=()):
    return {"members": list(members), "template_id": template,
            "filter_ids": list(filters), "type_ids": list(types)}


def test_version_guid_signals_name_what_changed():
    a = _inputs([1, 2, 3], template=50, filters=[60], types=[70])
    ga = {10: "v", 1: "x1", 2: "x2", 3: "x3", 50: "t", 60: "f", 70: "y"}
    same = probe.version_guid_signals(10, a, dict(a), ga, dict(ga))
    assert same["vg_verdict"] == "unchanged" and same["fingerprints_equal"]
    gb = dict(ga)
    gb.update({2: "x2b", 70: "y2"})
    b = _inputs([1, 2, 4], template=50, filters=[60], types=[70])
    gb[4] = "x4"
    rec = probe.version_guid_signals(10, a, b, ga, gb)
    sig = rec["signals"]
    assert rec["vg_verdict"] == "changed" and not rec["fingerprints_equal"]
    assert sig["members_added"] == [4] and sig["members_removed"] == [3]
    assert sig["members_changed"] == [2] and sig["member_types_changed"] == [70]
    assert not sig["view_itself"] and not sig["template"] and not sig["filters"]
    # A template's own VersionGuid change is a template change.
    gt = dict(ga)
    gt[50] = "t2"
    assert probe.version_guid_signals(10, a, dict(a), ga, gt)["fired"] == ["template"]


def test_version_guid_unread_input_is_unknown_never_unchanged():
    a = _inputs([1])
    assert probe.version_guid_signals(10, a, None, {}, {})["vg_verdict"] == "unknown"
    b = dict(a, type_ids=None)
    assert probe.version_guid_signals(10, a, b, {}, {})["vg_verdict"] == "unknown"
    assert probe.version_guid_signals(10, a, a, None, {})["vg_verdict"] == "unknown"


def test_unchanged_is_exactly_equal_fingerprints_over_generated_inputs():
    # The docstring's claim, asserted (CLAUDE.md, defect class 2): no signal
    # fires if and only if the two fingerprints are equal.
    import random
    rng = random.Random(7)
    seen = set()
    for _ in range(2000):
        pool = list(range(1, 12))
        def pick():
            return _inputs(rng.sample(pool, rng.randint(0, 5)),
                           template=rng.choice([-1, 50, 51]),
                           filters=rng.sample([60, 61], rng.randint(0, 2)),
                           types=rng.sample([70, 71], rng.randint(0, 2)))
        a = pick()
        b = a if rng.random() < 0.4 else pick()
        ids = pool + [10, 50, 51, 60, 61, 70, 71]
        ga = dict((i, rng.choice("pq")) for i in ids)
        gb = ga if rng.random() < 0.5 else dict(
            (i, ga[i] if rng.random() < 0.9 else rng.choice("pq")) for i in ids)
        rec = probe.version_guid_signals(10, a, b, ga, gb)
        assert (rec["vg_verdict"] == "unchanged") == rec["fingerprints_equal"]
        seen.add(rec["vg_verdict"])
    assert seen == {"changed", "unchanged"}       # both sides were exercised


def test_compare_guid_maps_counts():
    rec = probe.compare_guid_maps({1: "a", 2: "b", 3: "c"}, {2: "b", 3: "z", 4: "d"})
    assert (rec["common"], rec["same"], rec["differ"], rec["only_a"], rec["only_b"]) == (
        2, 1, 1, 1, 1)
    assert probe.compare_guid_maps(None, {}) is None


def test_options_version_guid_flags():
    assert probe.parse_options(None)["version_guid"] is True
    assert probe.parse_options({"version_guid": "false"})["version_guid"] is False
    assert probe.parse_options({"write_guid_maps": True})["write_guid_maps"] is True
    with pytest.raises(ValueError):
        probe.parse_options({"version_guid": "maybe"})


def test_version_guid_verdict_gets_its_own_table_and_false_unchanged(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    _view(a, 2, {21: (0, 0, 10, 10)}, {21: RED})
    _view(b, 2, {21: (5, 0, 15, 10)}, {21: RED})        # differs
    _view(a, 5, {51: (0, 0, 10, 10)}, {51: RED})
    _view(b, 5, {51: (0, 0, 10, 10)}, {51: RED})        # same
    pdir = _probe(tmp_path / "probe", [(2, "unknown"), (5, "unknown")])
    data = json.loads(next(pdir.glob("probe_change_detection_*.json")).read_text())
    data["views"][0]["vg_verdict"] = "unchanged"         # wrong: capture differs
    data["views"][1]["vg_verdict"] = "changed"           # over-cautious
    next(pdir.glob("probe_change_detection_*.json")).write_text(json.dumps(data))
    assert acd.main([str(pdir), "--run-a", str(a), "--run-b", str(b)]) == 0
    record = json.loads((pdir / acd.ANALYSIS_NAME).read_text())
    assert record["table_version_guid"] == {
        "unchanged": {"differs": 1, "same": 0, "not_comparable": 0},
        "changed": {"differs": 0, "same": 1, "not_comparable": 0}}
    assert [f["view_id"] for f in record["false_unchanged_version_guid"]] == [2]
    assert record["version_guid_changed_capture_same"] == [5]
    assert record["false_unchanged_count"] == 0           # the API table is apart


def test_the_captures_own_tick_and_frame_lines_are_not_a_change(tmp_path):
    # Run 20261028T111442: 15 identical drafting views and legends read as
    # differing because their 12 ticks and 2 frame-bound lines -- drawn and
    # rolled back by every capture -- had new ids in run B.
    a, b = tmp_path / "A", tmp_path / "B"
    _view(a, 8, {81: (0, 0, 10, 10)}, {81: RED})
    _view(b, 8, {81: (0, 0, 10, 10)}, {81: RED})
    for run, base in ((a, 1000), (b, 2000)):
        path = run / "color_id" / "V_8_anno.json"
        side = json.loads(path.read_text())
        lines = [str(base + i) for i in range(14)]
        for lid in lines:
            side["annotation_bbox_map"][lid] = {"bbox_uv": {"state": "value",
                                                            "value": [[0, 0]]}}
        side["registration_marks"] = {
            "marks": [{"id": int(x)} for x in lines[:12]],
            "frame_bounds": {"created": [{"id": int(x)} for x in lines[12:]]}}
        path.write_text(json.dumps(side))
    pdir = _probe(tmp_path / "probe", [(8, "unchanged")])
    assert acd.main([str(pdir), "--run-a", str(a), "--run-b", str(b)]) == 0
    row = json.loads((pdir / acd.ANALYSIS_NAME).read_text())["views"][0]
    assert row["anno_bbox"]["added"] == 0 and row["anno_bbox"]["removed"] == 0
    assert row["anno_bbox"]["excluded_capture_lines"] == [14, 14]
    assert row["capture_differs"] is False


def test_capture_line_ids_reads_ticks_and_frame_bounds_and_tolerates_absence():
    side = {"registration_marks": {"marks": [{"id": 5}, {"id": None}, {}],
                                   "frame_bounds": {"created": [{"id": 7}]}}}
    assert acd.capture_line_ids(side) == {"5", "7"}
    assert acd.capture_line_ids({}) == set()
    assert acd.capture_line_ids({"registration_marks": {"frame_bounds": None}}) == set()


def test_volatile_ids_are_not_compared_but_membership_still_is():
    a = _inputs([1, 2], template=50)
    ga = {10: "v", 1: "x1", 2: "x2", 50: "t"}
    gb = dict(ga)
    gb.update({2: "x2b", 10: "v2", 50: "t2"})
    plain = probe.version_guid_signals(10, a, dict(a), ga, gb)
    assert plain["vg_verdict"] == "changed"
    stable = probe.version_guid_signals(10, a, dict(a), ga, gb, volatile={2, 10, 50})
    assert stable["vg_verdict"] == "unchanged" and stable["fingerprints_equal"]
    b = _inputs([1, 2, 3], template=50)
    gb[3] = "x3"
    assert probe.version_guid_signals(10, a, b, ga, gb, volatile={2, 10, 50})["fired"] == [
        "members_added_count"]
    assert probe.volatile_ids({1: "a", 2: "b"}, {1: "a", 2: "c", 3: "d"}) == [2]


def test_stable_unchanged_is_exactly_equal_stable_fingerprints():
    import random
    rng = random.Random(11)
    seen = set()
    for _ in range(2000):
        ids = list(range(1, 12)) + [10, 50, 51, 60, 70]
        a = _inputs(rng.sample(range(1, 12), rng.randint(0, 5)),
                    template=rng.choice([-1, 50, 51]), filters=[60], types=[70])
        b = a if rng.random() < 0.5 else dict(a, members=rng.sample(range(1, 12), 3))
        ga = dict((i, rng.choice("pq")) for i in ids)
        gb = dict((i, ga[i] if rng.random() < 0.85 else rng.choice("pq")) for i in ids)
        vol = set(rng.sample(ids, rng.randint(0, 6)))
        rec = probe.version_guid_signals(10, a, b, ga, gb, volatile=vol)
        assert (rec["vg_verdict"] == "unchanged") == rec["fingerprints_equal"]
        seen.add(rec["vg_verdict"])
    assert seen == {"changed", "unchanged"}
