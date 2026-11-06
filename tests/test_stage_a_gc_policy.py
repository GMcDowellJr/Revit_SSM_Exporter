"""Config.stage_a_gc_policy: how many forced CLR collections a Stage A view pays.

Before 2026-11-06 every Stage A view paid two full blocking collections,
about 1 s each on a ~1 GB managed heap (two 60-view KSRF runs): the
pipeline's after its one-view loop (``mark_and_gc("after_run_gc")``, the
view's result still referenced) and the streaming loop's after the exporter
callback (``after_clr_gc_<id>``, the result released). ``"per_view_once"``
(the default) keeps the second only; ``"per_view_twice"`` is the old
behaviour, kept for A/B runs.

GATE 1 -- ``test_output_is_byte_identical_under_every_policy`` runs the
synthetic run of tests/stage_a_loop_fixture.py under each policy through the
REAL loop and compares every file with tools/compare_stage_a_runs.py, the
tool Greg runs on real runs. It is what licenses CONFIG_HASH_EXEMPT_KEYS.

GATE 2 -- the collection count, with a counting ``_force_clr_gc``, through
the real streaming loop: one per view under "per_view_once", two under
"per_view_twice". Labels are asserted too, so a mutation that keeps the
count but moves the collection is also red.

GATE 3 -- the C2 identity (total = sum(steps) + unaccounted) for every view
under every policy, and a skipped collection is recorded with its reason.

The run-end collection (streaming: two ``GC.Collect()`` and NO
``WaitForPendingFinalizers``, the 36-hour hang) is pinned BY BEHAVIOUR: a
fake ``System`` module counts the calls the whole run makes.

MUTATIONS (gate 4), applied to production and reverted, recorded in the PR:
(a) ignore the flag (after_run_gc always runs) -> gate 2 RED;
(b) remove the streaming per-view collection -> gate 2 RED;
(c) add WaitForPendingFinalizers to the run-end collection -> the run-end
    test RED.
"""
import json
import os
import sys
import types

import pytest

from tests import stage_a_loop_fixture as fx
from tests.layout_helpers import run_meta_file
from tools import compare_stage_a_runs
from vop_interwoven import loop_timing, memory_telemetry, pipeline
from vop_interwoven.config import (CONFIG_HASH_EXEMPT_KEYS, STAGE_A_GC_POLICIES,
                                   Config, stage_a_gc_policy_from)

N_VIEWS = len(fx.VIEWS)


def _views(run_dir):
    return json.loads(run_meta_file(run_dir).read_text())["views"]


def _run(monkeypatch, root, policy, **overrides):
    return fx.run(monkeypatch, root, cfg=fx.make_cfg(stage_a_gc_policy=policy, **overrides))


def _pair(monkeypatch, tmp_path):
    """``(twice_run_dir, once_run_dir)``."""
    return tuple(_run(monkeypatch, tmp_path / p, p)[1]
                 for p in ("per_view_twice", "per_view_once"))


# ---- gate 1: output unchanged ------------------------------------------------

def test_output_is_byte_identical_under_every_policy(monkeypatch, tmp_path):
    twice, once = _pair(monkeypatch, tmp_path)
    report = compare_stage_a_runs.compare(twice, once)
    assert report["unexpected"] == {}, report["unexpected"]
    rules = set(r for by_rule in report["allowed"].values() for r in by_rule)
    # Every difference there is, by name: the flag, the policy recorded in
    # the loop record and the skipped collection's reason, timing, clocks.
    assert rules <= {"flag", "gc_policy", "timing", "clock"}, report["allowed"]
    assert "flag" in rules and "gc_policy" in rules
    # Not vacuous: both runs wrote the captures, identical byte for byte.
    identical = report["identical"]
    assert len([p for p in identical if p.endswith(".tif")]) == 2 * N_VIEWS, identical
    assert len([p for p in identical if p.startswith("color_id/") and p.endswith(".json")]) \
        == 2 * N_VIEWS
    every = identical + list(report["allowed"])
    for name in ("run_meta_", "views_core_", "views_diagnostics_", "vop_view_element_map_"):
        assert any(os.path.basename(p).startswith(name) for p in every), name


def test_control_the_identity_test_sees_a_change_in_what_reaches_the_capture(
        monkeypatch, tmp_path):
    """THE CONTROL: a policy that changed the capture's input is reported."""
    real = fx.fake_collect_view_elements

    def _drop_one_under_once(doc, view, raster, diag=None, cfg=None):
        out = real(doc, view, raster, diag=diag, cfg=cfg)
        return out[1:] if getattr(cfg, "stage_a_gc_policy", "") == "per_view_once" else out
    monkeypatch.setattr(fx, "fake_collect_view_elements", _drop_one_under_once)
    twice, once = _pair(monkeypatch, tmp_path)
    report = compare_stage_a_runs.compare(twice, once)
    assert any(p.endswith(".tif") for p in report["unexpected"]), report["unexpected"]


def test_the_config_hash_is_the_same_under_every_policy(monkeypatch, tmp_path):
    runs = dict((p, _run(monkeypatch, tmp_path / p, p)[0]) for p in STAGE_A_GC_POLICIES)
    # The hash each RUN recorded (views_core ConfigHash, run_meta config_hash).
    assert len(set(r["config_hash"] for r in runs.values())) == 1
    from vop_interwoven.csv_export import compute_config_hash
    from vop_interwoven.view_fingerprint import portable_config_hash
    cfgs = [Config(stage_a_gc_policy=p) for p in STAGE_A_GC_POLICIES]
    assert len(set(compute_config_hash(c) for c in cfgs)) == 1
    assert len(set(pipeline._cfg_hash(c) for c in cfgs)) == 1
    assert len(set(portable_config_hash(c.to_dict()) for c in cfgs)) == 1
    # A run recorded before the flag existed has no key: still comparable.
    older = cfgs[0].to_dict()
    older.pop("stage_a_gc_policy")
    assert portable_config_hash(older) == portable_config_hash(cfgs[1].to_dict())
    assert "stage_a_gc_policy" in CONFIG_HASH_EXEMPT_KEYS


def test_the_policy_defaults_round_trips_and_refuses_an_unknown_value():
    assert Config().stage_a_gc_policy == "per_view_once"
    assert Config().to_dict()["stage_a_gc_policy"] == "per_view_once"
    assert Config.from_dict(Config(stage_a_gc_policy="per_view_twice").to_dict()) \
        .stage_a_gc_policy == "per_view_twice"
    for blank in (None, "", "  "):
        assert stage_a_gc_policy_from(blank) == "per_view_once"
    assert stage_a_gc_policy_from(" Per_View_Twice ") == "per_view_twice"
    with pytest.raises(ValueError):
        stage_a_gc_policy_from("per_view_none")
    with pytest.raises(ValueError):
        Config(stage_a_gc_policy="threshold")   # not implemented (G2 not done)


@pytest.mark.parametrize("value, expected", [
    (None, "per_view_once"), ("", "per_view_once"),
    ("per_view_once", "per_view_once"), ("per_view_twice", "per_view_twice")])
def test_the_batch_config_reads_the_policy(value, expected):
    from vop_interwoven.batch_capture import capture_config
    cfg = capture_config({} if value is None else {"stage_a_gc_policy": value})
    assert cfg.stage_a_gc_policy == expected
    assert cfg.to_dict()["stage_a_gc_policy"] == expected


def test_the_batch_config_refuses_an_unknown_policy():
    from vop_interwoven.batch_capture import capture_config
    with pytest.raises(ValueError):
        capture_config({"stage_a_gc_policy": "twice"})


def test_the_button_defaults_the_policy_to_blank():
    """Blank in the button's defaults is the Config default, so a
    batch_settings.json without the key runs "per_view_once"."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pyrevit",
                        "VOP.extension", "VOP.tab", "StageA.panel", "BatchCapture.pushbutton",
                        "script.py")
    src = open(path, encoding="utf-8").read()
    assert '"stage_a_gc_policy": ""' in src


# ---- gate 2: the collection count, through the real streaming loop -----------

def _count_collections(monkeypatch):
    """Every forced collection MemoryTracker.mark_and_gc makes, by label."""
    labels = []
    real_mark_and_gc = memory_telemetry.MemoryTracker.mark_and_gc
    calls = {"n": 0}

    def _counting_gc():
        calls["n"] += 1
        return {"pre_priv_mb": None, "post_priv_mb": None, "freed_mb": None, "gc_ms": 0.0}

    def _mark_and_gc(self, label):
        before = calls["n"]
        out = real_mark_and_gc(self, label)
        labels.extend([label] * (calls["n"] - before))
        return out
    monkeypatch.setattr(memory_telemetry, "_force_clr_gc", _counting_gc)
    monkeypatch.setattr(memory_telemetry.MemoryTracker, "mark_and_gc", _mark_and_gc)
    return labels


def _kinds(labels):
    return sorted("after_run_gc" if l == "after_run_gc" else
                  "after_clr_gc" if l.startswith("after_clr_gc_") else l for l in labels)


def test_per_view_once_forces_exactly_one_collection_per_view(monkeypatch, tmp_path):
    labels = _count_collections(monkeypatch)
    _run(monkeypatch, tmp_path, "per_view_once")
    assert len(labels) == N_VIEWS, labels
    assert _kinds(labels) == ["after_clr_gc"] * N_VIEWS, labels


def test_per_view_twice_forces_two_collections_per_view(monkeypatch, tmp_path):
    labels = _count_collections(monkeypatch)
    _run(monkeypatch, tmp_path, "per_view_twice")
    assert len(labels) == 2 * N_VIEWS, labels
    assert _kinds(labels) == sorted(["after_clr_gc"] * N_VIEWS + ["after_run_gc"] * N_VIEWS)


def test_control_the_counter_sees_a_collection_at_all(monkeypatch):
    """THE CONTROL: the counter is wired to the call mark_and_gc makes, so a
    count above is not the counter failing to see one."""
    labels = _count_collections(monkeypatch)
    memory_telemetry.MemoryTracker().mark_and_gc("probe")
    assert labels == ["probe"]


def test_outside_stage_a_the_policy_is_ignored(monkeypatch):
    labels = _count_collections(monkeypatch)
    fx.install(monkeypatch)
    monkeypatch.setattr(pipeline, "render_model_front_to_back", lambda *a, **k: {})
    monkeypatch.setattr(pipeline, "rasterize_annotations", lambda *a, **k: {})
    monkeypatch.setattr(pipeline, "export_view_raster", lambda *a, **k: {"success": True})
    cfg = fx.make_cfg(enable_color_id_buffer_stage_a=False, stage_a_gc_policy="per_view_once")
    cfg.output_dir = None
    with fx.fake_revit_db():
        pipeline.process_document_views(fx.FakeDoc(), [fx.VIEWS[0][0]], cfg)
    assert "after_run_gc" in labels, labels


# ---- gate 3: the C2 record closes cleanly under every policy -----------------

def _sum_holds(rec, partition):
    steps = rec["steps_ms"]
    assert set(steps) <= set(partition)
    assert rec["unaccounted_ms"] == pytest.approx(
        rec["total_ms"] - sum(steps.values()), abs=0.01)


@pytest.mark.parametrize("policy", STAGE_A_GC_POLICIES)
def test_the_loop_record_closes_under_every_policy(monkeypatch, tmp_path, policy):
    _result, run_dir = _run(monkeypatch, tmp_path, policy)
    views = _views(run_dir)
    assert len(views) == N_VIEWS
    for v in views:
        rec = v["loop_timing"]
        _sum_holds(rec, loop_timing.STREAMING_STEPS)
        _sum_holds(rec["pipeline"], loop_timing.PIPELINE_STEPS)
        pipe = rec["pipeline"]
        # The step is timed either way (the mark, plus the collection or not).
        assert "gc_after_run" in pipe["steps_ms"]
        assert "gc_after_view" in rec["steps_ms"]
        assert pipe["gc_policy"] == policy
        assert "gc_ms" in rec["gc"]["after_view"]
        if policy == "per_view_once":
            assert pipe["gc_after_run"] == {"skipped": "per_view_once",
                                            "gc_ms": None, "freed_mb": None}
        else:
            assert "skipped" not in pipe["gc_after_run"]
            assert "gc_ms" in pipe["gc_after_run"]


def test_the_timing_report_leaves_the_skipped_collection_blank(monkeypatch, tmp_path):
    from tools import stage_a_timing_report as report
    _result, run_dir = _run(monkeypatch, tmp_path, "per_view_once")
    rows = report.timing_rows(run_dir)
    assert len(rows) == N_VIEWS
    for row in rows:
        cells = report.loop_cells(row["loop_timing"])
        assert cells["gc_after_run_gc_ms"] is None
        assert cells["loop_total_ms"] is not None
        assert cells["pipeline_gc_after_run_ms"] is not None  # the mark, still timed


# ---- the run-end collection, pinned by behaviour -----------------------------

class _FakeGC(object):
    def __init__(self, calls):
        self._calls = calls

    def Collect(self):
        self._calls.append("Collect")

    def WaitForPendingFinalizers(self):
        self._calls.append("WaitForPendingFinalizers")


@pytest.mark.parametrize("policy, per_view", [("per_view_once", 1), ("per_view_twice", 2)])
def test_the_run_end_collection_never_waits_for_finalizers(monkeypatch, tmp_path,
                                                          policy, per_view):
    """Every forced collection on the per-view path is Collect, Wait,
    Collect (memory_telemetry._force_clr_gc). The run-end one in streaming
    is Collect, Collect with NO WaitForPendingFinalizers: waiting there hung
    Revit for 36 hours. So over a whole run the waits equal the per-view
    collections exactly, and the run adds two plain Collects."""
    calls = []
    fake_system = types.ModuleType("System")
    fake_system.GC = _FakeGC(calls)
    monkeypatch.setitem(sys.modules, "System", fake_system)
    _run(monkeypatch, tmp_path, policy)
    forced = per_view * N_VIEWS
    assert calls.count("WaitForPendingFinalizers") == forced, calls
    assert calls.count("Collect") == 2 * forced + 2, calls
    # The run ends on the last view's trailing Collect, then the run-end pair.
    assert calls[-3:] == ["Collect", "Collect", "Collect"], calls
