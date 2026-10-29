"""Hash-and-skip phase 2 (vop_interwoven/change_skip.py, docs/DESIGN_CHANGE_SKIP.md).

The planner's rules one at a time, then ``capture_run.run_capture(skip=...)``
end to end on a real baseline run on disk, with the capture itself faked:
every assertion reads the FILES the run leaves (run_meta, views_core, the
copied captures), never the return value (CLAUDE.md, defect class 4).
"""
import csv
import hashlib
import json
import random

import pytest

from tests import test_analyze_change_detection_probe as cap
from vop_interwoven import change_skip as cs
from vop_interwoven import output_layout
from vop_interwoven import view_fingerprint as vf

SETTINGS = dict(cs.DEFAULTS, skip_unchanged=True, skip_audit_fraction=0.0,
                skip_audit_min_per_type=0)


# --- settings, series, refresh, audit -------------------------------------------------

def test_settings_default_off_for_drafting_views_and_legends_only():
    s = cs.parse_settings(None)
    assert s["skip_unchanged"] is False
    assert s["skip_view_types"] == ["DraftingView", "Legend"]
    assert (s["skip_full_refresh_every_n_runs"], s["skip_audit_fraction"],
            s["skip_audit_min_per_type"]) == (7, 0.10, 3)


@pytest.mark.parametrize("raw", [
    {"skip_unchnaged": True}, {"skip_unchanged": "yes"}, {"skip_view_types": "Legend"},
    {"skip_full_refresh_every_n_runs": -1}, {"skip_full_refresh_every_n_runs": True},
    {"skip_audit_fraction": 1.5}, {"skip_audit_min_per_type": 2.0}])
def test_a_bad_setting_is_refused_not_guessed(raw):
    with pytest.raises(ValueError):
        cs.parse_settings(raw)


@pytest.mark.parametrize("title, key", [
    ("KSRF_Hosp_Interior_AR_V57_3.8 GB_2025-12-20_detached", "KSRF_Hosp_Interior_AR"),
    ("KSRF_Hosp_Interior_AR_V22_2.4 GB_2025-05-22", "KSRF_Hosp_Interior_AR"),
    ({"state": "value", "value": "A_2025-12-13"}, "A"),
    ("Interactive Model", None), (None, None), ({"state": "unavailable"}, None)])
def test_series_key(title, key):
    assert cs.series_key(title) == key


def test_series_runs_are_the_series_earlier_finalized_runs_oldest_first():
    index = [{"run_dir": "r3", "date": "2025-12-13", "doc_title": "M_V3_2025-12-13", "finalized": True},
             {"run_dir": "r1", "date": "2025-12-06", "doc_title": "M_V2_2025-12-06", "finalized": True},
             {"run_dir": "x", "date": "2025-12-06", "doc_title": "N_V2_2025-12-06", "finalized": True},
             {"run_dir": "u", "date": "2025-12-10", "doc_title": "M_V9_2025-12-10", "finalized": False},
             {"run_dir": "later", "date": "2025-12-20", "doc_title": "M_V4_2025-12-20", "finalized": True},
             {"run_dir": "e", "error": "unreadable"}]
    assert [r["run_dir"] for r in cs.series_runs(index, "M", "2025-12-20")] == ["r1", "r3"]


def _skipped(n=1):
    return {"enabled": True, "full_refresh": False, "carried_view_ids": list(range(n))}


@pytest.mark.parametrize("prior, every_n, due, since", [
    ([], 7, False, 0),
    ([None] + [_skipped()] * 5, 7, False, 5),
    ([None] + [_skipped()] * 6, 7, True, 6),       # the 7th run refreshes
    ([_skipped()] * 3 + [dict(_skipped(), full_refresh=True)] + [_skipped()] * 2, 7, False, 2),
    ([None, {"enabled": False}, _skipped()], 2, True, 1),
    ([None] + [_skipped()] * 30, 0, False, 30),    # 0 = never
    ([None, _skipped(0)], 2, False, 0),            # carried nothing: a full capture
])
def test_full_refresh_every_nth_run(prior, every_n, due, since):
    assert cs.full_refresh_due(prior, every_n) == (due, since)


def test_audit_is_reproducible_proportional_and_floored_per_type():
    pop = {"DraftingView": list(range(100, 200)), "Legend": [1, 2], "FloorPlan": [7, 8, 9, 10]}
    a = cs.audit_sample(pop, 0.10, 3, "20261029T1200")
    assert a == cs.audit_sample(dict(reversed(list(pop.items()))), 0.10, 3, "20261029T1200")
    assert a != cs.audit_sample(pop, 0.10, 3, "20261029T1300")
    by = dict((t, len(a & set(ids))) for t, ids in pop.items())
    assert by == {"DraftingView": 10, "Legend": 2, "FloorPlan": 3}
    assert cs.audit_sample(pop, 0.0, 0, "s") == set()


# --- the plan ------------------------------------------------------------------------

FACTS = {"config_hash": "c", "exporter_major": 4, "revit_build": "b",
         "open_mode": {"detached": True}, "basis_version": vf.BASIS_VERSION}


def _fp(vtype, fp="f"):
    return {"view_type": vtype, "fingerprint": fp, "components": {"members": fp},
            "shows_links_or_imports": False}


def _base(views, statuses=None):
    return {"facts": dict(FACTS), "views": views,
            "statuses": statuses or dict((k, "success") for k in views)}


def test_only_enabled_types_skip_and_every_skip_is_a_would_skip_verdict():
    cur = {"1": _fp("DraftingView"), "2": _fp("DraftingView", "new"), "3": _fp("FloorPlan"),
           "4": _fp("Legend"), "5": _fp("Legend")}
    base = _base({"1": _fp("DraftingView"), "2": _fp("DraftingView"), "3": _fp("FloorPlan"),
                  "4": _fp("Legend")})
    plan = cs.plan_skips(dict(FACTS), cur, base, SETTINGS, seed="s")
    assert plan["skip"] == [1, 4] and plan["reason"] is None
    assert plan["verdicts"] == {"1": "would_skip", "2": "fingerprint_differs",
                                "3": "type_not_enabled", "4": "would_skip",
                                "5": "no_baseline_fingerprint"}


def test_the_plan_never_skips_what_skip_verdict_would_capture():
    rng = random.Random(7)
    types = ["DraftingView", "Legend", "FloorPlan", "Section"]
    for _ in range(300):
        cur, basev, statuses = {}, {}, {}
        for vid in range(1, 12):
            cur[str(vid)] = _fp(rng.choice(types), rng.choice(["a", "b"]))
            if rng.random() < 0.8:
                basev[str(vid)] = _fp(cur[str(vid)]["view_type"], rng.choice(["a", "b"]))
                statuses[str(vid)] = rng.choice(["success", "unchanged", "failed"])
        settings = dict(SETTINGS, skip_audit_fraction=rng.random(),
                        skip_audit_min_per_type=rng.randint(0, 3))
        plan = cs.plan_skips(dict(FACTS), cur, _base(basev, statuses), settings, seed=rng.random())
        for vid in plan["skip"]:
            v = str(vid)
            assert cur[v]["view_type"] in ("DraftingView", "Legend")
            assert vf.skip_verdict(FACTS, FACTS, basev.get(v), cur[v], statuses.get(v))[
                "would_skip"]
            assert vid not in plan["audit"]


@pytest.mark.parametrize("kw, reason", [
    (dict(suspended={"view_id": 9}), "suspended"),
    (dict(full_refresh=True), "full_refresh"),
])
def test_suspension_and_full_refresh_skip_nothing(kw, reason):
    cur = {"1": _fp("DraftingView")}
    plan = cs.plan_skips(dict(FACTS), cur, _base({"1": _fp("DraftingView")}), SETTINGS,
                         seed="s", **kw)
    assert plan["skip"] == [] and plan["reason"] == reason
    assert cs.plan_skips(dict(FACTS), cur, None, SETTINGS, seed="s")["reason"] == "no_baseline"


def test_the_source_is_one_hop_back():
    run = {"run_dir": "B", "run_id": "b", "date": "2025-12-13"}
    assert cs.source_of(run, {"capture_status": "success"}) == {
        "run_dir": "B", "run_id": "b", "as_of_date": "2025-12-13"}
    older = {"run_dir": "A", "run_id": "a", "as_of_date": "2025-12-06"}
    assert cs.source_of(run, {"capture_status": "unchanged", "carried_from": older}) == older


# --- end to end: run_capture(skip=...) on disk ----------------------------------------

class _App(object):
    VersionNumber, VersionBuild, VersionName = "2025", "25.4.41.14", "Revit 2025"


class _Doc(object):
    def __init__(self, title):
        self.Title = title
        self.Application = _App()
        self.PathName = "C:/m.rvt"

    def GetElement(self, _eid):
        return None


RED = (200, 0, 0)
VIEWS = {1: "DraftingView", 2: "DraftingView", 3: "FloorPlan", 4: "Legend"}


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _baseline(root, name, date, fps, statuses=None, carried=None, captures=True):
    """A finalized run with captures (cap._view) and a fingerprints file."""
    from vop_interwoven.config import Config
    from vop_interwoven.csv_export import compute_config_hash
    from vop_interwoven.run_meta import provenance_fields
    run = root / name
    run.mkdir(parents=True)
    views = []
    for vid in fps:
        status = (statuses or {}).get(vid, "success")
        if captures and status == "success":
            cap._view(run, vid, {vid * 10: (0, 0, 10, 10)}, {vid * 10: RED})
        entry = {"view_id": vid, "view_name": "V{0}".format(vid), "capture_status": status}
        if status == "unchanged":
            entry["carried_from"] = carried
        views.append(entry)
    meta = {"schema": "vop.run_meta.v1", "run_id": name.split("__")[1], "date": date,
            "finalized": True, "doc_title": {"state": "value",
                                             "value": "KSRF_M_V1_1 GB_{0}_detached".format(date)},
            "config_hash": compute_config_hash(Config()),
            "exporter_version": provenance_fields()["exporter_version"],
            "revit_version": {"state": "value", "value": {"version_build": _App.VersionBuild}},
            "views": views, "views_requested": sorted(fps)}
    (run / "run_meta_{0}.json".format(date)).write_text(json.dumps(meta))
    fp = {"schema": vf.SCHEMA, "basis_version": vf.BASIS_VERSION,
          "document": {"open_mode": {"detached": True, "workshared": True,
                                     "closed_user_worksets": 0}},
          "views": dict((str(v), dict(_fp(VIEWS[v], f), view_id=v)) for v, f in fps.items())}
    (run / output_layout.fingerprints_name(date)).write_text(json.dumps(fp))
    return run


def _capture(monkeypatch, root, fps, settings, date="2025-12-20"):
    """run_capture with the capture and the fingerprint read faked; returns
    (run_dir, captured view ids)."""
    from vop_interwoven import capture_run
    from vop_interwoven.config import Config
    from vop_interwoven.csv_export import compute_config_hash
    captured = []

    def fake_run(doc, view_ids, cfg, output_dir, identity=None, **kw):
        captured.extend(view_ids)
        run = root / "{0}__{1}".format(identity["as_of_date"], identity["run_id"])
        run.mkdir()
        for vid in view_ids:
            cap._view(run, vid, {vid * 10 + 1: (0, 0, 5, 5)}, {vid * 10 + 1: RED})
        (run / "run_meta_{0}.json".format(identity["as_of_date"])).write_text(json.dumps(
            {"schema": "vop.run_meta.v1", "run_id": identity["run_id"],
             "date": identity["as_of_date"], "finalized": True,
             "config_hash": compute_config_hash(cfg),
             "views": [{"view_id": v, "capture_status": "success"} for v in view_ids],
             "views_requested": list(view_ids)}))
        return {}, str(run), identity["run_id"]

    def fake_fp(doc, view_ids, mode, opened=None):
        views = dict((str(v), dict(_fp(VIEWS[v], fps[v]), view_id=v)) for v in view_ids)
        return {"schema": vf.SCHEMA, "basis_version": vf.BASIS_VERSION, "mode": mode,
                "document": {"open_mode": {"detached": True, "workshared": True,
                                           "closed_user_worksets": 0}},
                "views": views, "summary": vf.summarize(views)}
    monkeypatch.setattr(capture_run, "_run_capture", fake_run)
    monkeypatch.setattr(vf, "fingerprint_views", fake_fp)
    doc = _Doc("KSRF_M_V2_1 GB_{0}_detached".format(date))
    _res, run_dir, _rid = capture_run.run_capture(doc, sorted(fps), Config(), str(root),
                                                  date_override=date, skip=settings)
    from pathlib import Path
    return Path(run_dir), captured


def _meta(run):
    path, _ = output_layout.find_run_meta(str(run))
    return json.loads(open(path).read())


def _core_rows(run):
    (path,) = list(run.glob("views_core_*.csv"))
    with open(str(path), newline="") as handle:
        return dict((r["ViewId"], r) for r in csv.DictReader(handle))


def test_unchanged_drafting_views_and_legends_are_carried_and_declared(monkeypatch, tmp_path):
    base = _baseline(tmp_path, "2025-12-13__20261021T1658", "2025-12-13",
                     {1: "f", 2: "f", 3: "f", 4: "f"})
    run, captured = _capture(monkeypatch, tmp_path, {1: "f", 2: "new", 3: "f", 4: "f"},
                             SETTINGS)
    assert captured == [2, 3]                       # 2 changed; 3 is a floor plan
    meta = _meta(run)
    skip = meta["skip"]
    assert skip["skip_view_ids"] == [1, 4] and skip["carried_view_ids"] == [1, 4]
    assert skip["baseline"]["run_dir"] == str(base) and skip["reason"] is None
    assert skip["summary"]["by_type"]["FloorPlan"] == {"type_not_enabled": 1}
    entries = dict((e["view_id"], e) for e in meta["views"])
    assert entries[1]["capture_status"] == "unchanged"
    assert entries[1]["carried_from"]["run_dir"] == str(base)
    assert entries[1]["fingerprint"] == "f" and entries[1]["view_name"] == "V1"
    assert entries[2]["capture_status"] == "success"
    assert sorted(meta["views_requested"]) == [1, 2, 3, 4]
    for name in entries[1]["carried_files"]:
        assert _sha(run / "color_id" / name) == _sha(base / "color_id" / name)
    assert sorted(entries[1]["carried_files"]) == ["V_1.json", "V_1.tiff", "V_1_anno.json",
                                                   "V_1_anno.tiff"]
    rows = _core_rows(run)
    assert rows["1"]["CaptureStatus"] == "unchanged" and rows["4"]["CaptureStatus"] == "unchanged"
    assert rows["1"]["RunId"] == meta["run_id"]
    # The fingerprints of EVERY view, skipped ones included, are recorded.
    fp = json.loads((run / output_layout.fingerprints_name("2025-12-20")).read_text())
    assert sorted(fp["views"]) == ["1", "2", "3", "4"]


def test_a_view_the_baseline_carried_is_copied_from_its_source(monkeypatch, tmp_path):
    src = _baseline(tmp_path, "2025-12-06__20261021T1513", "2025-12-06", {1: "f", 2: "f"})
    carried = {"run_dir": str(src), "run_id": "20261021T1513", "as_of_date": "2025-12-06"}
    _baseline(tmp_path, "2025-12-13__20261021T1658", "2025-12-13", {1: "f", 2: "f"},
              statuses={1: "unchanged"}, carried=carried)
    run, captured = _capture(monkeypatch, tmp_path, {1: "f", 2: "new"}, SETTINGS)
    entry = dict((e["view_id"], e) for e in _meta(run)["views"])[1]
    assert captured == [2]
    assert entry["capture_status"] == "unchanged" and entry["carried_from"] == carried
    assert _sha(run / "color_id" / "V_1.tiff") == _sha(src / "color_id" / "V_1.tiff")


def test_a_carry_that_cannot_be_made_is_declared_failed_not_unchanged(monkeypatch, tmp_path):
    _baseline(tmp_path, "2025-12-13__20261021T1658", "2025-12-13", {1: "f", 2: "f"},
              captures=False)
    run, captured = _capture(monkeypatch, tmp_path, {1: "f", 2: "new"}, SETTINGS)
    meta = _meta(run)
    entry = dict((e["view_id"], e) for e in meta["views"])[1]
    assert entry["capture_status"] == "failed"
    assert entry["capture_failure_reason"].startswith("carry_failed: ")
    assert meta["skip"]["carried_view_ids"] == [] and meta["skip"]["carry_failed"][0]["view_id"] == 1
    assert _core_rows(run)["1"]["CaptureStatus"] == "failed"
    assert not list((run / "color_id").glob("V_1.*"))


def test_the_audit_captures_some_would_skip_views_anyway(monkeypatch, tmp_path):
    fps = dict((v, "f") for v in VIEWS)
    _baseline(tmp_path, "2025-12-13__20261021T1658", "2025-12-13", fps)
    run, captured = _capture(monkeypatch, tmp_path, fps,
                             dict(SETTINGS, skip_audit_min_per_type=1))
    skip = _meta(run)["skip"]
    assert sorted(skip["audit_view_ids"]) == sorted(set(captured) - {3})
    assert len(skip["audit_view_ids"]) == 2         # one drafting view, one legend
    assert sorted(skip["carried_view_ids"] + skip["audit_view_ids"]) == [1, 2, 4]


@pytest.mark.parametrize("setup, reason", [
    ("off", None), ("suspended", "suspended"), ("no_baseline", "no_baseline"),
    ("other_series", "no_baseline")])
def test_nothing_skips_off_suspended_or_without_a_baseline(monkeypatch, tmp_path, setup, reason):
    fps = {1: "f", 4: "f"}
    if setup != "no_baseline":
        base = _baseline(tmp_path, "2025-12-13__20261021T1658", "2025-12-13", fps)
        if setup == "other_series":
            p = base / "run_meta_2025-12-13.json"
            m = json.loads(p.read_text())
            m["doc_title"]["value"] = "OTHER_V1_2025-12-13"
            p.write_text(json.dumps(m))
    if setup == "suspended":
        (tmp_path / cs.SUSPENSION_FILE).write_text(json.dumps({"KSRF_M": {"view_id": 1}}))
    settings = dict(SETTINGS, skip_unchanged=(setup != "off"))
    run, captured = _capture(monkeypatch, tmp_path, fps, settings)
    assert captured == [1, 4]
    skip = _meta(run)["skip"]
    if setup == "off":
        assert skip == {"schema": cs.SCHEMA, "enabled": False}
    else:
        assert skip["enabled"] is True and skip["carried_view_ids"] == []
        assert skip["reason"].startswith(reason)


def test_without_skip_settings_run_meta_has_no_skip_record(monkeypatch, tmp_path):
    run, captured = _capture(monkeypatch, tmp_path, {1: "f"}, None)
    assert captured == [1] and "skip" not in _meta(run)


def test_an_unreadable_suspension_file_suspends(tmp_path):
    (tmp_path / cs.SUSPENSION_FILE).write_text("{ not json")
    assert "unreadable" in cs.read_suspension(str(tmp_path), "KSRF_M")["reason"]
    (tmp_path / cs.SUSPENSION_FILE).write_text(json.dumps({"*": {"by": "greg"}}))
    assert cs.read_suspension(str(tmp_path), "ANY") == {"by": "greg"}


def test_when_every_view_would_skip_one_is_audited_so_the_run_captures(monkeypatch, tmp_path):
    _baseline(tmp_path, "2025-12-13__20261021T1658", "2025-12-13", {1: "f", 4: "f"})
    run, captured = _capture(monkeypatch, tmp_path, {1: "f", 4: "f"}, SETTINGS)
    skip = _meta(run)["skip"]
    assert captured == [1] and skip["audit_forced"] == 1
    assert skip["audit_view_ids"] == [1] and skip["carried_view_ids"] == [4]


def test_a_copy_that_does_not_match_its_source_is_removed_and_declared_failed(monkeypatch, tmp_path):
    _baseline(tmp_path, "2025-12-13__20261021T1658", "2025-12-13", {1: "f", 2: "f"})
    real = cs.shutil.copy2

    def corrupt(src, dst):
        real(src, dst)
        if str(dst).endswith(".tiff"):
            with open(dst, "ab") as handle:
                handle.write(b"x")
    monkeypatch.setattr(cs.shutil, "copy2", corrupt)
    run, _captured = _capture(monkeypatch, tmp_path, {1: "f", 2: "new"}, SETTINGS)
    entry = dict((e["view_id"], e) for e in _meta(run)["views"])[1]
    assert entry["capture_status"] == "failed"
    assert "does not match its source" in entry["capture_failure_reason"]
    assert not list((run / "color_id").glob("V_1.*"))


def test_a_full_refresh_run_skips_nothing_on_disk(monkeypatch, tmp_path):
    fps = {1: "f", 2: "f"}
    for n, date in enumerate(("2025-11-29", "2025-12-06", "2025-12-13")):
        run = _baseline(tmp_path, "{0}__2026102{1}T1000".format(date, n), date, fps)
        if n:
            p = run / "run_meta_{0}.json".format(date)
            m = json.loads(p.read_text())
            m["skip"] = {"enabled": True, "full_refresh": False, "carried_view_ids": [1]}
            p.write_text(json.dumps(m))
    run, captured = _capture(monkeypatch, tmp_path, fps,
                             dict(SETTINGS, skip_full_refresh_every_n_runs=3))
    skip = _meta(run)["skip"]
    assert captured == [1, 2] and skip["full_refresh"] is True and skip["runs_since_full"] == 2
    assert skip["reason"] == "full_refresh"


def test_carry_never_overwrites_a_file_the_run_already_holds(tmp_path):
    src = _baseline(tmp_path, "2025-12-13__20261021T1658", "2025-12-13", {1: "f"})
    dest = tmp_path / "dest"
    (dest / "color_id").mkdir(parents=True)
    mine = dest / "color_id" / "V_1.tiff"
    mine.write_bytes(b"this run's own capture")
    with pytest.raises(ValueError, match="already exists"):
        cs.carry_view(str(src), str(dest), 1)
    assert mine.read_bytes() == b"this run's own capture"
    assert sorted(p.name for p in (dest / "color_id").iterdir()) == ["V_1.tiff"]


def test_a_carry_that_raises_is_recorded_and_fails_the_run(monkeypatch, tmp_path):
    _baseline(tmp_path, "2025-12-13__20261021T1658", "2025-12-13", {1: "f", 2: "f"})

    def boom(*a, **k):
        raise RuntimeError("disk gone")
    monkeypatch.setattr(cs, "carry_into_run", boom)
    with pytest.raises(RuntimeError, match="disk gone"):
        _capture(monkeypatch, tmp_path, {1: "f", 2: "new"}, SETTINGS)
    (run,) = [p for p in tmp_path.iterdir() if p.name.startswith("2025-12-20__")]
    skip = _meta(run)["skip"]
    assert skip["skip_view_ids"] == [1] and skip["carry_error"] == "RuntimeError: disk gone"
