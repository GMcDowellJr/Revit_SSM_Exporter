"""Hash-and-skip phase 1 (docs/DESIGN_CHANGE_SKIP.md): the fingerprint, the
skip verdict, the batch's fingerprint backfill mode and the validation tool.

Revit is not here. What is asserted is the pure arithmetic production runs
(vop_interwoven.view_fingerprint), the plan and loop with Revit injected (as
tests/test_batch_capture.py does), and the validation tool end to end on
synthetic runs: run_meta, fingerprints files and captures written here, the
record read from the FILE the tool wrote (CLAUDE.md, defect class 4).
"""
import json
import random
from datetime import datetime

import pytest

from tests import test_analyze_change_detection_probe as cap
from tests import test_batch_capture as tb
from tests.layout_helpers import run_meta_file
from tools import validate_change_skip as vcs
from vop_interwoven import batch_capture as bc
from vop_interwoven import output_layout
from vop_interwoven import view_fingerprint as vf


WALLS = (-2000011, "Walls", 3, 5, 90, -1)        # projection pattern 90, cut solid


def _inputs(members=(1, 2), template=-1, filters=(), types=(), refs=(), rows=(WALLS,)):
    return {"members": list(members), "template_id": template, "filter_ids": list(filters),
            "type_ids": list(types), "reference_ids": list(refs), "graphics_rows": list(rows)}


GUIDS = dict((i, "g{0}".format(i)) for i in (1, 2, 3, 10, 50, 60, 70, 80, 90))


# --- the fingerprint -------------------------------------------------------------------

def test_each_component_moves_only_its_own_hash():
    base = vf.view_fingerprint(10, _inputs(template=50, filters=[60], types=[70], refs=[80]),
                               GUIDS)
    for name, guid_id in (("view", 10), ("template", 50), ("filters", 60),
                          ("members", 1), ("types", 70), ("references", 80),
                          ("graphics", 90)):
        guids = dict(GUIDS)
        guids[guid_id] = "changed"
        moved = vf.view_fingerprint(10, _inputs(template=50, filters=[60], types=[70],
                                                refs=[80]), guids)
        assert moved["fingerprint"] != base["fingerprint"]
        assert vf.changed_components(base, moved) == [name]


def test_order_and_duplicates_do_not_matter_and_membership_does():
    a = vf.view_fingerprint(10, _inputs(members=[2, 1, 1]), GUIDS)
    b = vf.view_fingerprint(10, _inputs(members=[1, 2]), GUIDS)
    c = vf.view_fingerprint(10, _inputs(members=[1, 2, 3]), GUIDS)
    assert a["fingerprint"] == b["fingerprint"] != c["fingerprint"]
    assert vf.changed_components(b, c) == ["members"]


def test_an_unread_input_is_an_unknown_fingerprint():
    for key in ("members", "template_id", "filter_ids", "type_ids", "reference_ids",
                "graphics_rows"):
        rec = vf.view_fingerprint(10, dict(_inputs(), **{key: None}), GUIDS)
        assert rec["fingerprint"] is None and rec["components"] is None
    assert vf.view_fingerprint(10, _inputs(), None)["fingerprint"] is None


DOORS = (-2000023, "Doors", 1, 1, -1, -1)


def test_the_graphics_component_sees_weights_and_patterns_not_row_order():
    rows = [WALLS, DOORS]
    g, _ = vf.graphics_component(rows, GUIDS)
    assert g == vf.graphics_component(list(reversed(rows)), GUIDS)[0]
    for edited in ((-2000011, "Walls", 4, 5, 90, -1),     # projection weight
                   (-2000011, "Walls", 3, 6, 90, -1),     # cut weight
                   (-2000011, "Walls", 3, 5, 91, -1),     # projection pattern
                   (-2000011, "Walls", 3, 5, 90, 90),     # cut pattern
                   (-2000011, "Walls", 3, 5, 90, "?")):   # now unreadable
        assert vf.graphics_component([edited, DOORS], GUIDS)[0] != g, edited
    guids = dict(GUIDS)
    guids[90] = "pattern edited"
    assert vf.graphics_component(rows, guids)[0] != g
    # Only patterns that are elements carry a VersionGuid: the solid / no
    # pattern ids and unreadable fields are not looked up.
    assert vf.graphics_component([DOORS, (1, "x", 1, 1, "?", 0)], {})[1] == 0
    assert vf.graphics_component([WALLS], {})[1] == 1


def test_a_style_edit_moves_only_the_graphics_component():
    base = vf.view_fingerprint(10, _inputs(), GUIDS)
    heavier = vf.view_fingerprint(10, _inputs(rows=[(-2000011, "Walls", 4, 5, 90, -1)]), GUIDS)
    assert vf.changed_components(base, heavier) == ["graphics"]
    assert base["counts"]["graphics"] == 1


def test_a_category_the_view_does_not_draw_cannot_move_its_fingerprint():
    # Basis 1 hashed every category in the document: a Doors edit blocked a
    # view that shows only walls. The rows are the view's own categories.
    walls_only = vf.view_fingerprint(10, _inputs(rows=[WALLS]), GUIDS)
    edited_doors = (-2000023, "Doors", 9, 9, -1, -1)
    assert walls_only == vf.view_fingerprint(10, _inputs(rows=[WALLS]), GUIDS)
    with_doors = vf.view_fingerprint(10, _inputs(rows=[WALLS, DOORS]), GUIDS)
    assert vf.view_fingerprint(10, _inputs(rows=[WALLS, edited_doors]), GUIDS)[
        "fingerprint"] != with_doors["fingerprint"]
    assert "graphics_differs" not in vf.RULES and vf.BASIS_VERSION == 2


def test_resolve_type_name_takes_a_name_or_looks_a_number_up():
    names = {1: "FloorPlan"}
    assert vf.resolve_type_name("Section", names.get) == "Section"
    assert vf.resolve_type_name("1", names.get) == "FloorPlan"
    assert vf.resolve_type_name("9", names.get) is None
    assert vf.resolve_type_name(None, names.get) is None


# --- the verdict -------------------------------------------------------------------------

FACTS = {"config_hash": "9400eaef", "exporter_major": 4, "revit_build": "25.4.41.14",
         "open_mode": {"detached": True, "workshared": True, "closed_user_worksets": 0},
         "basis_version": 2}


def _view(fp="f1", links=False):
    return {"fingerprint": fp, "components": {"members": fp}, "shows_links_or_imports": links}


def test_every_rule_holds_and_the_view_would_skip():
    assert vf.skip_verdict(FACTS, dict(FACTS), _view(), _view(), "success") == {
        "would_skip": True, "rule": None}


@pytest.mark.parametrize("change, rule", [
    (dict(base=None), "no_baseline_fingerprint"),
    (dict(status="failed"), "baseline_capture_not_success"),
    (dict(status="unchanged"), "baseline_capture_not_success"),
    (dict(cur_facts={"config_hash": "other"}), "config_hash_differs"),
    (dict(cur_facts={"exporter_major": 5}), "exporter_major_differs"),
    (dict(cur_facts={"revit_build": "26.0"}), "revit_build_differs"),
    (dict(cur_facts={"open_mode": {"detached": False}}), "open_mode_differs"),
    (dict(cur_facts={"open_mode": None}), "open_mode_differs"),
    (dict(cur_facts={"basis_version": 1}), "basis_version_differs"),
    (dict(cur=_view(fp=None)), "fingerprint_unknown"),
    (dict(base=_view(fp=None)), "fingerprint_unknown"),
    (dict(cur=None), "fingerprint_unknown"),
    (dict(cur=_view(links=True)), "shows_links_or_imports"),
    (dict(base=_view(links=None)), "shows_links_or_imports"),
    (dict(cur=_view(fp="f2")), "fingerprint_differs"),
])
def test_each_rule_alone_refuses_the_skip_and_is_named(change, rule):
    cur_facts = dict(FACTS, **change.get("cur_facts", {}))
    base = change["base"] if "base" in change else _view()
    cur = change["cur"] if "cur" in change else _view()
    out = vf.skip_verdict(FACTS, cur_facts, base, cur, change.get("status", "success"))
    assert out == {"would_skip": False, "rule": rule}
    assert rule in vf.RULES


def test_unknown_never_skips_over_generated_records():
    rng = random.Random(3)
    for _ in range(3000):
        base = rng.choice([None, _view(), _view(fp=None), _view(links=None)])
        cur = rng.choice([None, _view(), _view(fp=None), _view(links=None)])
        facts = dict(FACTS, **dict((k, None) for k in FACTS if rng.random() < 0.1))
        out = vf.skip_verdict(FACTS, facts, base, cur, rng.choice(["success", None]))
        if out["would_skip"]:
            assert base and cur and base["fingerprint"] == cur["fingerprint"]
            assert base["shows_links_or_imports"] is False is cur["shows_links_or_imports"]
            assert all(facts[k] is not None for k in FACTS)


def test_run_facts_read_run_meta_and_the_fingerprints_file():
    meta = {"config_hash": "9400eaef", "exporter_version": "4.0.0+src.abc",
            "revit_version": {"state": "value", "value": {"version_build": "25.4.41.14"}}}
    fp = {"basis_version": 2, "document": {"open_mode": FACTS["open_mode"]}}
    assert vf.run_facts(meta, fp) == FACTS
    assert vf.exporter_major("garbage") is None


# --- the batch's fingerprint backfill --------------------------------------------------

def test_the_fingerprint_plan_lists_each_files_runs_and_skips_the_rest():
    index = [{"run_dir": "E/2025-12-13__r1", "doc_title": "A_2025-12-13", "finalized": True},
             {"run_dir": "E/2025-12-13__r2", "doc_title": "A_2025-12-13", "finalized": True},
             {"run_dir": "E/2025-12-20__r3", "doc_title": "A_2025-12-20", "finalized": False}]
    plan = bc.build_plan(tb._entries("A_2025-12-13", "A_2025-12-20"), "E",
                         run_index=index, mode="fingerprint")
    (job,) = plan["jobs"]
    assert job["kind"] == "fingerprint" and job["stem"] == "A_2025-12-13"
    assert job["fingerprint_runs"] == ["E/2025-12-13__r1", "E/2025-12-13__r2"]
    assert plan["skipped"] == [{"key": "KSRF/A_2025-12-20.rvt",
                                "reason": "no run to fingerprint"}]


def test_a_fingerprint_job_is_done_only_with_a_current_basis_and_no_error(tmp_path):
    run = tb._run_dir(tmp_path, "A_2025-12-13")
    job = {"kind": "fingerprint", "fingerprint_runs": [run]}
    assert bc.job_done(job) == (False, None)
    for stamp, done in (({"error": "x"}, False), ({"basis_version": 0}, False),
                        ({"basis_version": vf.BASIS_VERSION}, True)):
        meta = json.loads(run_meta_file(run).read_text())
        meta["view_fingerprints"] = stamp
        run_meta_file(run).write_text(json.dumps(meta))
        assert bc.job_done(job)[0] is done


def test_the_loop_runs_a_fingerprint_job_and_never_overwrites_opened_from(tmp_path):
    root = tmp_path / "Exports"
    run = tb._run_dir(root, "A_2025-12-13")
    meta = json.loads(run_meta_file(run).read_text())
    meta["opened_from"] = {"source": "batch_capture", "title": "the capture's open"}
    run_meta_file(run).write_text(json.dumps(meta))
    plan = {"schema": bc.PLAN_SCHEMA, "mode": "fingerprint", "output_root": str(root),
            "jobs": [{"kind": "fingerprint", "key": "KSRF/A_2025-12-13.rvt",
                      "stem": "A_2025-12-13", "full_path": "C:\\ACC\\A_2025-12-13.rvt",
                      "display_name": "KSRF/A_2025-12-13.rvt", "fingerprint_runs": [run]}]}
    events, open_doc, close_doc, capture, recapture = tb._harness(tmp_path)
    seen = []

    def fingerprint(doc, job):
        seen.append(job["fingerprint_runs"])
        m = json.loads(run_meta_file(run).read_text())
        m["view_fingerprints"] = {"basis_version": vf.BASIS_VERSION}
        run_meta_file(run).write_text(json.dumps(m))
        return {"run_dirs": [run]}
    record = bc.run_batch(plan, open_doc, close_doc, capture, recapture, log=lambda s: None,
                          now=datetime(2026, 10, 28, 9, 0, 0), fingerprint=fingerprint)
    assert record["ok"] == 1 and seen == [[run]]
    assert ("capture", "A_2025-12-13") not in events
    after = json.loads(run_meta_file(run).read_text())
    assert after["opened_from"]["title"] == "the capture's open"
    assert after["fingerprint_opened_from"]["title"] == "A_2025-12-13_detached"
    # Without the callable the job fails loudly, never as a capture.
    plan2 = dict(plan, jobs=[dict(plan["jobs"][0], fingerprint_runs=[run + "_x"])])
    rec2 = bc.run_batch(plan2, open_doc, close_doc, capture, recapture, log=lambda s: None,
                        now=datetime(2026, 10, 28, 9, 5, 0), write_log=False)
    assert rec2["failed"] == 1 and "no fingerprint callable" in rec2["jobs"][0]["error"]


def test_a_saved_fingerprint_plan_loads():
    path_free = {"schema": bc.PLAN_SCHEMA, "jobs": [{"kind": "fingerprint", "key": "k"}]}
    import tempfile
    import os
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "plan.json")
        with open(p, "w") as fh:
            json.dump(path_free, fh)
        assert bc.load_plan(p)["jobs"][0]["kind"] == "fingerprint"


# --- the validation tool ------------------------------------------------------------------

RED = (200, 0, 0)


def _run(root, name, date, views, status="success", facts=FACTS, anno_rects=None):
    """A run with run_meta, captures (cap._view) and a fingerprints file.
    ``views``: {view_id: (fingerprint, rects)}; ``anno_rects`` {view_id: the
    text note's rect} where it is not the default."""
    run = root / name
    for vid, (_fp, rects) in views.items():
        extra = {"anno_rect": anno_rects[vid]} if vid in (anno_rects or {}) else {}
        cap._view(run, vid, rects, dict((e, RED) for e in rects), **extra)
    meta = {"schema": "vop.run_meta.v1", "run_id": name, "date": date, "finalized": True,
            "config_hash": facts["config_hash"], "exporter_version": "4.0.0+src.x",
            "revit_version": {"state": "value", "value": {"version_build": facts["revit_build"]}},
            "views": [{"view_id": v, "capture_status": status} for v in views]}
    (run / "run_meta_{0}.json".format(date)).write_text(json.dumps(meta))
    fp = {"schema": vf.SCHEMA, "basis_version": facts["basis_version"],
          "document": {"open_mode": facts["open_mode"]},
          "views": dict((str(v), {"view_id": v, "view_type": "DraftingView" if v < 100 else
                                  "FloorPlan", "fingerprint": f, "components": {"members": f},
                                  "shows_links_or_imports": False})
                        for v, (f, _r) in views.items())}
    (run / output_layout.fingerprints_name(date)).write_text(json.dumps(fp))
    return str(run)


def test_validation_counts_true_and_false_unchanged_per_type(tmp_path):
    a = _run(tmp_path, "2025-12-13__r1", "2025-12-13", {
        1: ("same", {11: (0, 0, 10, 10)}),         # unchanged, capture same
        2: ("same", {21: (0, 0, 10, 10)}),         # unchanged by hash, capture differs
        101: ("old", {31: (0, 0, 10, 10)})})       # hash changed
    b = _run(tmp_path, "2025-12-20__r2", "2025-12-20", {
        1: ("same", {11: (0, 0, 10, 10)}),
        2: ("same", {21: (5, 0, 15, 10)}),
        101: ("new", {31: (0, 0, 10, 10)})})
    out = tmp_path / "v.json"
    assert vcs.main([a, b, "--out", str(out), "--min-pairs", "2", "--min-per-type", "1"]) == 0
    rec = json.loads(out.read_text())
    (pair,) = rec["pairs"]
    assert pair["rules"] == {"would_skip": 2, "fingerprint_differs": 1}
    assert pair["changed_components"] == {"members": 1}
    assert [f["view_id"] for f in pair["false_unchanged"]] == [2]
    acc = rec["acceptance"]
    assert acc["by_type"]["DraftingView"]["true_unchanged"] == 1
    assert acc["by_type"]["DraftingView"]["false_unchanged"] == 1
    assert acc["false_unchanged"] == 1 and acc["overall_meets_bar"] is False
    assert acc["types_meeting_bar"] == []
    assert pair["over_cautious"] == []         # not measured without --compare-all


def test_compare_all_finds_over_caution_and_the_bar_is_met_when_clean(tmp_path):
    a = _run(tmp_path, "2025-12-13__r1", "2025-12-13", {
        1: ("same", {11: (0, 0, 10, 10)}), 101: ("old", {31: (0, 0, 10, 10)})})
    b = _run(tmp_path, "2025-12-20__r2", "2025-12-20", {
        1: ("same", {11: (0, 0, 10, 10)}), 101: ("new", {31: (0, 0, 10, 10)})})
    out = tmp_path / "v.json"
    assert vcs.main([a, b, "--compare-all", "--out", str(out), "--min-pairs", "1",
                     "--min-per-type", "1"]) == 0
    rec = json.loads(out.read_text())
    assert rec["pairs"][0]["over_cautious"] == [
        {"view_id": 101, "view_type": "FloorPlan", "rule": "fingerprint_differs"}]
    acc = rec["acceptance"]
    assert acc["overall_meets_bar"] is True and acc["types_meeting_bar"] == ["DraftingView"]
    assert acc["upper_bound_95"] == pytest.approx(3.0)


def test_annotation_fringe_is_tolerated_unless_exact_and_a_move_is_not(tmp_path):
    # View 1: the text note one pixel wider in B (anti-aliasing fringe).
    # View 2: the text note moved 6 px (a real change).
    views = {1: ("same", {11: (0, 10, 10, 20)}), 2: ("same", {21: (0, 10, 10, 20)})}
    a = _run(tmp_path, "2025-12-13__r1", "2025-12-13", views)
    b = _run(tmp_path, "2025-12-20__r2", "2025-12-20", views,
             anno_rects={1: (2, 2, 21, 4), 2: (8, 2, 26, 4)})
    tolerant, exact = tmp_path / "t.json", tmp_path / "e.json"
    assert vcs.main([a, b, "--out", str(tolerant)]) == 0
    assert vcs.main([a, b, "--out", str(exact), "--exact"]) == 0
    rec = json.loads(tolerant.read_text())
    row = rec["acceptance"]["by_type"]["DraftingView"]
    assert (row["true_unchanged"], row["fringe_tolerated"], row["false_unchanged"]) == (1, 1, 1)
    (fu,) = rec["pairs"][0]["false_unchanged"]
    assert fu["view_id"] == 2 and fu["anno_fringe_px"] < fu["anno_differing_px"]
    assert rec["exact"] is False and rec["fringe_radius_px"] == 2
    rec = json.loads(exact.read_text())
    row = rec["acceptance"]["by_type"]["DraftingView"]
    assert (row["true_unchanged"], row["fringe_tolerated"], row["false_unchanged"]) == (0, 0, 2)
    assert rec["fringe_radius_px"] is None


def test_a_run_without_fingerprints_is_named_not_scored(tmp_path):
    a = _run(tmp_path, "2025-12-13__r1", "2025-12-13", {1: ("same", {11: (0, 0, 10, 10)})})
    b = _run(tmp_path, "2025-12-20__r2", "2025-12-20", {1: ("same", {11: (0, 0, 10, 10)})})
    import os
    os.remove(os.path.join(b, output_layout.fingerprints_name("2025-12-20")))
    out = tmp_path / "v.json"
    assert vcs.main([a, b, "--out", str(out)]) == 0
    pair = json.loads(out.read_text())["pairs"][0]
    assert "fingerprints missing" in pair["skipped"] and pair["by_type"] == {}


def test_acceptance_is_per_type_and_uses_the_rule_of_three():
    pairs = [{"by_type": {"DraftingView": {"views": 40, "would_skip": 40, "true_unchanged": 40,
                                           "false_unchanged": 0, "not_comparable": 0},
                          "Section": {"views": 5, "would_skip": 5, "true_unchanged": 5,
                                      "false_unchanged": 0, "not_comparable": 0}}}]
    acc = vcs.acceptance(pairs, min_pairs=1000, min_per_type=30)
    assert acc["types_meeting_bar"] == ["DraftingView"]
    assert acc["by_type"]["Section"]["meets_bar"] is False
    assert acc["overall_meets_bar"] is False and acc["compared_would_skip"] == 45
    assert acc["by_type"]["DraftingView"]["upper_bound_95"] == pytest.approx(3 / 40.0)


# --- the capture-run hook -----------------------------------------------------------------

def _fake_capture_run(monkeypatch, tmp_path, fingerprint_views):
    """run_capture with the capture itself replaced by a run directory holding
    a finalized run_meta, and the Revit reads by ``fingerprint_views``."""
    from vop_interwoven import capture_run
    run = tmp_path / "2025-12-20__20261028T1200"
    order = []

    def fake_run(doc, view_ids, cfg, output_dir, **kw):
        order.append("capture")
        run.mkdir()
        (run / "run_meta_2025-12-20.json").write_text(json.dumps(
            {"schema": "vop.run_meta.v1", "run_id": "20261028T1200", "date": "2025-12-20",
             "finalized": True}))
        return {}, str(run), "20261028T1200"

    def fake_fp(doc, view_ids, mode, opened=None):
        order.append("fingerprint")
        return fingerprint_views(doc, view_ids, mode, opened)
    monkeypatch.setattr(capture_run, "_run_capture", fake_run)
    monkeypatch.setattr(vf, "fingerprint_views", fake_fp)
    return capture_run, run, order


def test_run_capture_fingerprints_before_capturing_and_writes_after(monkeypatch, tmp_path):
    def fp(doc, view_ids, mode, opened):
        return {"schema": vf.SCHEMA, "basis_version": vf.BASIS_VERSION, "mode": mode,
                "views": {"1": {"fingerprint": "f"}}, "summary": vf.summarize(
                    {"1": {"fingerprint": "f"}}), "ms": 5.0}
    capture_run, run, order = _fake_capture_run(monkeypatch, tmp_path, fp)
    capture_run.run_capture(object(), [1], object(), str(tmp_path), record_fingerprints=True)
    assert order == ["fingerprint", "capture"]
    written = json.loads((run / "view_fingerprints_2025-12-20.json").read_text())
    assert written["mode"] == "capture" and written["run_id"] == "20261028T1200"
    stamp = json.loads((run / "run_meta_2025-12-20.json").read_text())["view_fingerprints"]
    assert stamp["file"] == "view_fingerprints_2025-12-20.json"
    assert stamp["fingerprinted"] == 1 and stamp["basis_version"] == vf.BASIS_VERSION


def test_a_fingerprint_failure_is_stamped_and_never_fails_the_capture(monkeypatch, tmp_path):
    def fp(doc, view_ids, mode, opened):
        raise RuntimeError("VersionGuid unreadable")
    capture_run, run, order = _fake_capture_run(monkeypatch, tmp_path, fp)
    out = capture_run.run_capture(object(), [1], object(), str(tmp_path),
                                  record_fingerprints=True)
    assert out[1] == str(run) and order == ["fingerprint", "capture"]
    stamp = json.loads((run / "run_meta_2025-12-20.json").read_text())["view_fingerprints"]
    assert stamp == {"error": "RuntimeError: VersionGuid unreadable"}
    assert not (run / "view_fingerprints_2025-12-20.json").exists()


def test_off_by_default_nothing_is_read_or_written(monkeypatch, tmp_path):
    def fp(doc, view_ids, mode, opened):
        raise AssertionError("must not be called")
    capture_run, run, order = _fake_capture_run(monkeypatch, tmp_path, fp)
    capture_run.run_capture(object(), [1], object(), str(tmp_path))
    assert order == ["capture"]
    assert "view_fingerprints" not in json.loads((run / "run_meta_2025-12-20.json").read_text())


# --- the Revit reads, on a fake API ------------------------------------------------------

class _Id(object):
    def __init__(self, value):
        self.Value = value


class _Cat(object):
    def __init__(self, cid, name, weight=1, pattern=-1, parent=None, broken=False):
        self.Id, self.Name, self.Parent = _Id(cid), name, parent
        self.SubCategories = []
        self._w, self._p, self._broken = weight, pattern, broken
        if parent is not None:
            parent.SubCategories.append(self)

    def GetLineWeight(self, kind):
        if self._broken:
            raise RuntimeError("unreadable")
        return self._w

    def GetLinePatternId(self, kind):
        return _Id(self._p)


class _Elem(object):
    def __init__(self, category, type_id=-1):
        self.Category, self._t = category, type_id

    def GetTypeId(self):
        return _Id(self._t)


class _View(object):
    ViewTemplateId = _Id(-1)
    Parameters = []

    def AreGraphicsOverridesAllowed(self):
        return False


class _Doc(object):
    def __init__(self, elems):
        self._e = elems

    def GetElement(self, eid):
        return self._e.get(eid.Value)


def _read(monkeypatch, elems, cache=None):
    import sys
    import types
    from vop_interwoven import stage_a_registered_capture as rc
    fake = types.ModuleType("Autodesk.Revit.DB")
    fake.ElementId = _Id
    fake.ImportInstance = type("ImportInstance", (), {})
    fake.RevitLinkInstance = type("RevitLinkInstance", (), {})
    fake.StorageType = types.SimpleNamespace(ElementId="ElementId")
    fake.GraphicsStyleType = types.SimpleNamespace(Projection="P", Cut="C")
    monkeypatch.setitem(sys.modules, "Autodesk.Revit.DB", fake)
    monkeypatch.setattr(rc, "view_membership_ids", lambda doc, view: sorted(elems))
    return vf.read_view_inputs(_Doc(elems), _View(), cache)


def test_graphics_rows_are_the_member_categories_roots_and_all_their_subcategories(
        monkeypatch):
    walls = _Cat(-2000011, "Walls", 3, 90)
    _Cat(-2000012, "Common Edges", 1, parent=walls)
    doors = _Cat(-2000023, "Doors")
    frame = _Cat(-2000024, "Frame/Mullion", 2, parent=doors)
    _Cat(-2000099, "Furniture")                    # in the document, not in the view
    cache = {}
    inputs, links, unread = _read(monkeypatch, {1: _Elem(walls, 70), 2: _Elem(frame)}, cache)
    assert unread == {} and links is False and inputs["type_ids"] == [70]
    ids = sorted(r[0] for r in inputs["graphics_rows"])
    assert ids == [-2000024, -2000023, -2000012, -2000011]   # roots + subs, no furniture
    assert (-2000011, "Walls", 3, 3, 90, 90) in inputs["graphics_rows"]
    assert sorted(cache) == [-2000023, -2000011]             # read once per root
    # A view of the same categories reads the same rows from the cache.
    again, _l, _u = _read(monkeypatch, {1: _Elem(walls, 70), 2: _Elem(frame)}, cache)
    assert again["graphics_rows"] == inputs["graphics_rows"]


def test_an_unreadable_weight_is_a_stable_marker_and_a_failed_read_is_unknown(monkeypatch):
    broken = _Cat(-2000011, "Walls", broken=True)
    inputs, _links, unread = _read(monkeypatch, {1: _Elem(broken)})
    assert inputs["graphics_rows"] == [(-2000011, "Walls", "?", "?", -1, -1)]
    assert unread == {}

    class _NoCategory(_Elem):
        @property
        def Category(self):
            raise RuntimeError("category unreadable")

        @Category.setter
        def Category(self, value):
            pass
    inputs, _links, unread = _read(monkeypatch, {1: _NoCategory(None)})
    assert inputs["graphics_rows"] is None and "graphics_rows" in unread
    assert vf.view_fingerprint(10, inputs, GUIDS)["fingerprint"] is None


# --- the grid basis: real grids from tools/stage_a_grid ---------------------------------

def _grid_run(root, name, link=True, whiten=None):
    """A run whose one view (id 1) was captured and gridded by the real tools
    (tests/test_stage_a_grid_element_cell._rich_view). ``whiten``: model
    pixels (y, x) set white in the TIFF before the grid is made again."""
    import shutil
    from tests.test_stage_a_grid_element_cell import _rich_view
    from PIL import Image
    import numpy as np
    from tools import stage_a_grid as grid
    run = root / name
    cap_dir = run / "color_id"
    rec, _arr = _rich_view(cap_dir, link=link)
    assert rec["status"] == "value", rec.get("reason")
    shutil.move(str(cap_dir / "out"), str(run / "grid"))
    if whiten:
        tiff = cap_dir / "V_1.tiff"
        img = np.asarray(Image.open(str(tiff)).convert("RGB")).copy()
        for y, x in whiten:
            img[y, x] = (255, 255, 255)
        Image.fromarray(img).save(str(tiff), format="TIFF")
        from tools import register_stage_a_annotation as reg
        reg.register(cap_dir / "V_1_anno.json")    # the model TIFF it registered to moved
        assert grid.grid_view(cap_dir / "V_1.json", run / "grid")["status"] == "value"
    fp = {"basis_version": vf.BASIS_VERSION, "document": {"open_mode": FACTS["open_mode"]},
          "views": {"1": {"view_id": 1, "view_type": "FloorPlan", "fingerprint": "f",
                          "components": {"members": "f"}, "shows_links_or_imports": False}}}
    meta = {"config_hash": FACTS["config_hash"], "exporter_version": "4.0.0",
            "revit_version": {"value": {"version_build": FACTS["revit_build"]}}}
    return {"run_dir": str(run), "meta": meta, "fingerprints": fp,
            "statuses": {"1": "success"}, "reason": None}


def _wall_edge_px():
    """A pixel on the wall's top edge, mid-cell: whitening it moves no cell."""
    from tests.test_stage_a_grid_element_cell import WALL, _model_px
    y0, _y1, x0, x1 = _model_px(WALL)
    return [(y0, (x0 + x1) // 2 + 9)]


def _outcome(pair):
    row = pair["by_type"]["FloorPlan"]
    return dict((k, row[k]) for k in ("true_unchanged", "false_unchanged",
                                      "grid_tolerated", "fringe_tolerated"))


@pytest.fixture(scope="module")
def grid_runs(tmp_path_factory):
    root = tmp_path_factory.mktemp("grid_basis")
    return {"a": _grid_run(root, "a"),
            "noise": _grid_run(root, "noise", whiten=_wall_edge_px()),
            "nolink": _grid_run(root, "nolink", link=False)}


def test_a_pixel_change_that_moves_no_cell_is_the_same_on_the_grid(grid_runs):
    pair = vcs.score_pair(grid_runs["a"], grid_runs["noise"])
    assert _outcome(pair) == {"true_unchanged": 1, "false_unchanged": 0,
                              "grid_tolerated": 1, "fringe_tolerated": 0}
    # The control: the pixels really differ, so on pixels it is a false unchanged.
    pair = vcs.score_pair(grid_runs["a"], grid_runs["noise"], basis="pixels")
    assert _outcome(pair)["false_unchanged"] == 1
    assert pair["false_unchanged"][0]["basis"] == "pixels"


def test_a_change_the_grid_sees_is_a_false_unchanged_on_the_grid_basis(grid_runs):
    pair = vcs.score_pair(grid_runs["a"], grid_runs["nolink"])
    assert _outcome(pair)["false_unchanged"] == 1
    (fu,) = pair["false_unchanged"]
    assert fu["basis"] == "grid" and fu["grid"]["rows_removed"] > 0
    assert fu["grid"]["differs"] is True


def test_identical_captures_need_no_grid(grid_runs, tmp_path):
    import shutil
    copy = tmp_path / "copy"
    shutil.copytree(grid_runs["a"]["run_dir"], str(copy))
    shutil.rmtree(str(copy / "grid"))
    pair = vcs.score_pair(grid_runs["a"], dict(grid_runs["a"], run_dir=str(copy)))
    assert _outcome(pair) == {"true_unchanged": 1, "false_unchanged": 0,
                              "grid_tolerated": 0, "fringe_tolerated": 0}


def test_without_a_comparable_grid_the_pixels_decide(grid_runs, tmp_path):
    import shutil
    for name, damage in (("missing", "remove"), ("version", "version")):
        copy = tmp_path / name
        shutil.copytree(grid_runs["noise"]["run_dir"], str(copy))
        rec_path = copy / "grid" / "V_1.grid.json"
        if damage == "remove":
            rec_path.unlink()
        else:
            rec = json.loads(rec_path.read_text())
            rec["tool_version"] = "0.0.1"
            rec_path.write_text(json.dumps(rec))
        pair = vcs.score_pair(grid_runs["a"], dict(grid_runs["noise"], run_dir=str(copy)))
        (fu,) = pair["false_unchanged"]
        assert fu["basis"] == "pixels", name
        assert ("no grid record in run B" if damage == "remove"
                else "grid versions differ") in fu["grid"]["reason"]


def test_grid_diff_compares_presence_not_counts():
    def facts(present=(("model", 7, None, "ec_px", 1, 1),), occupied=None, lattice=(1.0, (0, 0))):
        return {"lattice": lattice, "present": set(present),
                "occupied": occupied if occupied is not None else {(1, 1): 1},
                "tool_version": "2.1.0"}
    assert vcs.grid_diff(facts(), facts())["differs"] is False
    moved = facts(present=(("model", 7, None, "ec_px", 2, 1),))
    d = vcs.grid_diff(facts(), moved)
    assert (d["rows_added"], d["rows_removed"], d["differs"]) == (1, 1, True)
    assert vcs.grid_diff(facts(), facts(occupied={(1, 1): 3}))["cells_differ"] == 1
    assert vcs.grid_diff(facts(), facts(occupied={}))["cells_differ"] == 1
    assert vcs.grid_diff(facts(), facts(lattice=(1.0, (0.5, 0))))["differs"] is True


def test_grid_facts_reads_presence_per_field_and_places_occupancy_by_range():
    import numpy as np
    rec = {"grid": {"cell_ft": 1.0, "origin_uv": [0, 0], "i_range": [-2, 1], "j_range": [5, 7]},
           "ec_keys": [{"layer": "model", "element_id": 7, "link_category": None},
                       {"layer": "annotation", "element_id": 9, "link_category": None}],
           "tool_version": "2.1.0"}
    occ = np.zeros((2, 3), dtype=np.uint8)
    occ[1, 0] = 2                                   # row 1 = j 6, column 0 = i -2
    arrays = {"ec_key": np.array([0, 1]), "ec_i": np.array([-2, 0]), "ec_j": np.array([5, 6]),
              "ec_px": np.array([3, 0]), "ec_ink_px": np.array([1, 0]),
              "ec_black_px": np.array([0, 4]), "occupancy": occ}
    f = vcs.grid_facts(rec, arrays)
    assert f["present"] == {("model", 7, None, "ec_px", -2, 5),
                            ("model", 7, None, "ec_ink_px", -2, 5),
                            ("annotation", 9, None, "ec_black_px", 0, 6)}
    assert f["occupied"] == {(-2, 6): 2}
