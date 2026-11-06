"""C3: Config.stage_a_legacy_prepass gates the legacy steps Stage A does not use.

The gated steps (C1 audit, PR body): the view signature's hash and the config
hash it embeds (read only by the root cache, which Stage A bypasses), the CSV
identity read ``_extract_view_identity_for_csv`` (read only by the root cache
and the geometry export), and the StrategyDiagnostics tracker (read only by
the geometry model pass). Everything else the loop runs still runs.

GATE 1 -- ``test_output_is_byte_identical_with_the_flag_on_and_off`` runs the
synthetic run of tests/stage_a_loop_fixture.py twice through the REAL loop
and compares EVERY file of the two runs with tools/compare_stage_a_runs.py
(the comparison run on real runs too): binaries byte for byte, JSON and CSV
field by field, allowing only the named rules there. Its fixture's capture writes bytes that
are a pure function of what the pipeline hands it, so a gated step whose
output reached the capture would change them. It is also what licenses
CONFIG_HASH_EXEMPT_KEYS: the flag is out of the config hash because this test
shows the two runs are the same capture.

MUTATIONS (gate 3), applied to production and reverted, recorded in the PR:
(a) ignore the flag (legacy steps always run) -> the not-invoked tests here
    turn RED; (b) gate off a step the audit found CONSUMED
    (collect_view_elements, or the membership tracking in _view_signature)
    -> the byte-identity test turns RED.
"""
import json
import os

from tests import stage_a_loop_fixture as fx
from tools import compare_stage_a_runs
from vop_interwoven import pipeline
from vop_interwoven.config import CONFIG_HASH_EXEMPT_KEYS, Config

def _run_pair(monkeypatch, tmp_path, **cfg_overrides):
    """``{flag: (run_dir, result)}`` for a flag-off and a flag-on run."""
    runs = {}
    for flag in (False, True):
        root = tmp_path / ("on" if flag else "off")
        result, run_dir = fx.run(monkeypatch, root, cfg=fx.make_cfg(
            stage_a_legacy_prepass=flag, **cfg_overrides))
        runs[flag] = (run_dir, result)
    return runs


def output_differences(monkeypatch, tmp_path, **cfg_overrides):
    """tools/compare_stage_a_runs.compare over the pair -- the comparison
    Greg runs on two real runs, so the test and the tool cannot disagree."""
    runs = _run_pair(monkeypatch, tmp_path, **cfg_overrides)
    return compare_stage_a_runs.compare(runs[False][0], runs[True][0]), runs


# ---- gate 1 ------------------------------------------------------------------

def test_output_is_byte_identical_with_the_flag_on_and_off(monkeypatch, tmp_path):
    report, runs = output_differences(monkeypatch, tmp_path)
    assert report["unexpected"] == {}, report["unexpected"]
    # Every difference there IS, by rule: the flag, the gated-steps list and
    # timing/clock fields -- nothing else.
    rules = set(r for by_rule in report["allowed"].values() for r in by_rule)
    assert rules <= {"flag", "gated_steps", "timing", "clock"}, report["allowed"]
    assert "flag" in rules  # the comparison saw the two runs differ at all
    # Not vacuous: both runs wrote every kind of file, and the captures are
    # the same files byte for byte (not merely both empty).
    identical = report["identical"]
    tiffs = [p for p in identical if p.endswith(".tif")]
    assert len(tiffs) == 2 * len(fx.VIEWS), identical
    assert len([p for p in identical if p.startswith("color_id/") and p.endswith(".json")]) \
        == 2 * len(fx.VIEWS)
    every = identical + list(report["allowed"])
    for name in ("run_meta_", "views_core_", "views_diagnostics_", "vop_view_element_map_"):
        assert any(os.path.basename(p).startswith(name) for p in every), name


def test_control_the_identity_test_sees_a_change_in_what_reaches_the_capture(
        monkeypatch, tmp_path):
    """THE CONTROL. A difference in the capture's input must be reported,
    or the empty diff above proves nothing."""
    real = fx.fake_collect_view_elements

    def _drop_one_when_flag_on(doc, view, raster, diag=None, cfg=None):
        out = real(doc, view, raster, diag=diag, cfg=cfg)
        return out[1:] if getattr(cfg, "stage_a_legacy_prepass", False) else out
    monkeypatch.setattr(fx, "fake_collect_view_elements", _drop_one_when_flag_on)
    report, _runs = output_differences(monkeypatch, tmp_path)
    assert any(p.endswith(".tif") for p in report["unexpected"]), report["unexpected"]


def test_the_config_hash_is_the_same_with_the_flag_on_and_off(monkeypatch, tmp_path):
    runs = _run_pair(monkeypatch, tmp_path)
    # The hash each RUN recorded (views_core ConfigHash, run_meta config_hash).
    assert runs[False][1]["config_hash"] == runs[True][1]["config_hash"]
    from vop_interwoven.csv_export import compute_config_hash
    from vop_interwoven.view_fingerprint import portable_config_hash
    off, on = Config(stage_a_legacy_prepass=False), Config(stage_a_legacy_prepass=True)
    assert compute_config_hash(off) == compute_config_hash(on)
    assert pipeline._cfg_hash(off) == pipeline._cfg_hash(on)
    assert portable_config_hash(off.to_dict()) == portable_config_hash(on.to_dict())
    # A run recorded before the flag existed has no key: still comparable.
    older = off.to_dict()
    older.pop("stage_a_legacy_prepass")
    assert portable_config_hash(older) == portable_config_hash(on.to_dict())


def test_control_a_hashed_key_still_changes_the_hash():
    from vop_interwoven.csv_export import compute_config_hash
    assert compute_config_hash(Config(tiny_max=2)) != compute_config_hash(Config(tiny_max=3))


def test_the_flag_is_recorded_in_the_config_and_round_trips():
    assert "stage_a_legacy_prepass" in CONFIG_HASH_EXEMPT_KEYS
    assert Config().to_dict()["stage_a_legacy_prepass"] is False
    assert Config.from_dict(Config(stage_a_legacy_prepass=True).to_dict()) \
        .stage_a_legacy_prepass is True


# ---- gate 3a: the gated call sites are not invoked -------------------------------

def _recorders(monkeypatch):
    calls = {"view_identity": 0, "cfg_hash": 0, "strategy_diag": 0, "signature": []}
    real_ident = pipeline._extract_view_identity_for_csv
    real_cfg_hash = pipeline._cfg_hash
    real_sig = pipeline._view_signature

    def _ident(*a, **k):
        calls["view_identity"] += 1
        return real_ident(*a, **k)

    def _cfg_hash(*a, **k):
        calls["cfg_hash"] += 1
        return real_cfg_hash(*a, **k)

    def _sig(*a, **k):
        calls["signature"].append(k.get("compute_signature", True))
        return real_sig(*a, **k)

    class _Strategy(object):
        def __init__(self):
            calls["strategy_diag"] += 1
    monkeypatch.setattr(pipeline, "_extract_view_identity_for_csv", _ident)
    monkeypatch.setattr(pipeline, "_cfg_hash", _cfg_hash)
    monkeypatch.setattr(pipeline, "_view_signature", _sig)
    monkeypatch.setattr("vop_interwoven.diagnostics.StrategyDiagnostics", _Strategy)
    return calls


def test_stage_a_does_not_invoke_the_gated_steps_by_default(monkeypatch, tmp_path):
    calls = _recorders(monkeypatch)
    fx.run(monkeypatch, tmp_path, cfg=fx.make_cfg(export_strategy_diagnostics=True))
    assert calls["view_identity"] == 0
    assert calls["cfg_hash"] == 0
    assert calls["strategy_diag"] == 0
    # The membership read still runs, once per view, without the hash.
    assert calls["signature"] == [False] * len(fx.VIEWS)


def test_control_the_flag_runs_the_gated_steps(monkeypatch, tmp_path):
    """THE CONTROL: the recorders above can see the calls at all."""
    calls = _recorders(monkeypatch)
    fx.run(monkeypatch, tmp_path, cfg=fx.make_cfg(export_strategy_diagnostics=True,
                                                  stage_a_legacy_prepass=True))
    assert calls["view_identity"] == len(fx.VIEWS)
    assert calls["cfg_hash"] == len(fx.VIEWS)
    assert calls["strategy_diag"] == len(fx.VIEWS)
    assert calls["signature"] == [True] * len(fx.VIEWS)


def test_outside_stage_a_the_flag_is_ignored_and_every_step_runs(monkeypatch):
    calls = _recorders(monkeypatch)
    fx.install(monkeypatch)
    monkeypatch.setattr(pipeline, "render_model_front_to_back", lambda *a, **k: {})
    monkeypatch.setattr(pipeline, "rasterize_annotations", lambda *a, **k: {})
    monkeypatch.setattr(pipeline, "export_view_raster", lambda *a, **k: {"success": True})
    cfg = fx.make_cfg(enable_color_id_buffer_stage_a=False, stage_a_legacy_prepass=False)
    cfg.output_dir = None
    with fx.fake_revit_db():
        pipeline.process_document_views(fx.FakeDoc(), [fx.VIEWS[0][0]], cfg)
    assert calls["view_identity"] == 1
    assert calls["signature"] == [True]


def test_the_gated_steps_are_named_in_the_loop_record(monkeypatch, tmp_path):
    from tests.layout_helpers import run_meta_file
    _result, run_dir = fx.run(monkeypatch, tmp_path)
    views = json.loads(run_meta_file(run_dir).read_text())["views"]
    assert all(v["loop_timing"]["pipeline"]["gated_steps"]
               == ["signature_hash", "view_identity", "strategy_diag"] for v in views)


def test_the_membership_map_is_unchanged_by_the_gate(monkeypatch, tmp_path):
    """The element map is _view_signature's consumed half: it must list the
    same members with the hash skipped."""
    runs = _run_pair(monkeypatch, tmp_path)
    for flag in (False, True):
        run_dir = runs[flag][0]
        (name,) = [p for p in os.listdir(run_dir) if p.startswith("vop_view_element_map_")]
        with open(os.path.join(run_dir, name)) as handle:
            views = json.load(handle)["views"]
        assert dict((v["view_id"], v["element_ids"]) for v in views) == {
            13521188: [501, 502, 503], 13521190: [501, 505], 13521300: []}


def test_the_comparison_tool_refuses_a_directory_that_is_not_a_run(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert compare_stage_a_runs.main([str(tmp_path / "a"), str(tmp_path / "b")]) == 2


def test_the_comparison_tool_exits_1_on_an_unexpected_difference(monkeypatch, tmp_path):
    runs = _run_pair(monkeypatch, tmp_path)
    assert compare_stage_a_runs.main([runs[False][0], runs[True][0]]) == 0
    tiff = [p for p in os.listdir(os.path.join(runs[True][0], "color_id"))
            if p.endswith(".tif")][0]
    with open(os.path.join(runs[True][0], "color_id", tiff), "ab") as handle:
        handle.write(b"\x00")
    assert compare_stage_a_runs.main([runs[False][0], runs[True][0]]) == 1


# ---- the comparison's rules against the shapes a real pair showed (2026-11-06) ---

def _write_json(path, payload):
    with open(path, "w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)


def _real_shapes(run_dir, which):
    """What two real runs of one archive differed in, flag on vs off
    (compare_on_vs_off.txt, 2026-11-06): the tick-placement slowest list,
    view_fingerprints.ms in run_meta, the timing report written into the run
    folder, and the document-open fingerprints."""
    k = 1 if which == "on" else 2
    cap = os.path.join(run_dir, "color_id")
    sidecar = os.path.join(cap, sorted(n for n in os.listdir(cap) if n.endswith(".json"))[0])
    with open(sidecar) as handle:
        data = json.load(handle)
    data["registration_marks"]["mark_avoidance"] = {"slowest": [
        {"id": 100 + k, "category_id": -2000000 - k, "ms": 3.0 * k}]}
    _write_json(sidecar, data)
    (name,) = [n for n in os.listdir(run_dir) if n.startswith("run_meta_")]
    with open(os.path.join(run_dir, name)) as handle:
        meta = json.load(handle)
    meta["view_fingerprints"] = {"ms": 1000.0 * k, "views": 3}
    _write_json(os.path.join(run_dir, name), meta)
    with open(os.path.join(run_dir, "views_timing_2026-11-02.csv"), "w") as handle:
        handle.write("view_id,elapsed_ms,mem_view_start_gc2\n1,{0},{0}\n".format(k))
    _write_json(os.path.join(run_dir, "views_timing_2026-11-02.json"),
                {"views": [{"phases_ms": {"total": k}}]})
    _write_json(os.path.join(run_dir, "view_fingerprints_2026-11-02.json"),
                {"computed_at": "t{0}".format(k), "ms": k,
                 "views": {"13521188": {"fingerprint": "f{0}".format(k), "ms": k,
                                        "components": {"members": "m{0}".format(k)}},
                           "13521190": {"fingerprint": "same", "ms": k}}})


def test_the_shapes_of_a_real_pair_are_not_unexpected(monkeypatch, tmp_path):
    runs = _run_pair(monkeypatch, tmp_path)
    _real_shapes(runs[False][0], "off")
    _real_shapes(runs[True][0], "on")
    report = compare_stage_a_runs.compare(runs[False][0], runs[True][0])
    assert report["unexpected"] == {}, report["unexpected"]
    assert sorted(report["skipped"]) == ["views_timing_2026-11-02.csv",
                                         "views_timing_2026-11-02.json"]
    (fp,) = report["document_open"]
    assert fp == "view_fingerprints_2026-11-02.json"
    assert "/views/13521188/fingerprint" in report["document_open"][fp]
    assert "/views/13521190/fingerprint" not in report["document_open"][fp]
    assert compare_stage_a_runs.main([runs[False][0], runs[True][0]]) == 0


def test_control_a_non_timing_sidecar_field_is_still_unexpected(monkeypatch, tmp_path):
    runs = _run_pair(monkeypatch, tmp_path)
    for flag, value in ((False, 1), (True, 2)):
        cap = os.path.join(runs[flag][0], "color_id")
        sidecar = os.path.join(cap, sorted(n for n in os.listdir(cap) if n.endswith(".json"))[0])
        with open(sidecar) as handle:
            data = json.load(handle)
        data["registration_marks"]["mark_avoidance"] = {"placed": value}
        _write_json(sidecar, data)
    report = compare_stage_a_runs.compare(runs[False][0], runs[True][0])
    assert any("/mark_avoidance/placed" in f for fs in report["unexpected"].values() for f in fs)
