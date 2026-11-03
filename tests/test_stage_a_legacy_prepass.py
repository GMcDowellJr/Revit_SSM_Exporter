"""C3: Config.stage_a_legacy_prepass gates the legacy steps Stage A does not use.

The gated steps (C1 audit, PR body): the view signature's hash and the config
hash it embeds (read only by the root cache, which Stage A bypasses), the CSV
identity read ``_extract_view_identity_for_csv`` (read only by the root cache
and the geometry export), and the StrategyDiagnostics tracker (read only by
the geometry model pass). Everything else the loop runs still runs.

GATE 1 -- ``test_output_is_byte_identical_with_the_flag_on_and_off`` runs the
synthetic run of tests/stage_a_loop_fixture.py twice through the REAL loop
and compares EVERY file of the two runs byte for byte, after removing only the
differences ALLOWED below, each named. Its fixture's capture writes bytes that
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
import re

import pytest

from tests import stage_a_loop_fixture as fx
from vop_interwoven import pipeline
from vop_interwoven.config import CONFIG_HASH_EXEMPT_KEYS, Config

# Every difference the two runs may show, and why. Anything else fails.
ALLOWED_JSON_PATHS = (
    (r"^/config/stage_a_legacy_prepass$", "the flag itself"),
    (r"^/run_dir$", "the two runs are written under two test roots"),
    (r"^/views\[\]/loop_timing(/|$)", "C2 loop timing: timing fields and gated_steps"),
    (r"^/views/\d+/elapsed_sec$", "views_diagnostics: timing"),
    (r"^/generated_utc$", "the element map's write clock"),
)
ALLOWED_CSV_COLUMNS = (("ElapsedSec", "views_core: timing"),)


def _flat(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            for item in _flat(v, path + "/" + str(k)):
                yield item
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            for item in _flat(v, "{0}[{1}]".format(path, i)):
                yield item
    else:
        yield path, obj


def _snapshot(root, run_id):
    """``{path relative to the run dir: bytes}`` with the run id masked (two
    runs a minute apart have two ids; it is the clock, not the capture)."""
    out = {}
    for dirpath, _dirs, files in os.walk(str(root)):
        for name in files:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, str(root)).replace(os.sep, "/")
            rel = rel.split("/", 1)[1] if "/" in rel else rel
            with open(full, "rb") as handle:
                data = handle.read()
            if name.endswith((".json", ".csv")):
                data = data.replace(run_id.encode("utf-8"), b"<RUN_ID>")
            out[rel] = data
    return out


def _json_differences(a, b):
    fa, fb = dict(_flat(json.loads(a))), dict(_flat(json.loads(b)))
    diffs = set()
    for path in set(fa) | set(fb):
        if fa.get(path, KeyError) != fb.get(path, KeyError):
            general = re.sub(r"\[\d+\]", "[]", path)
            if not any(re.match(rx, general) for rx, _why in ALLOWED_JSON_PATHS):
                diffs.add(path)
    return diffs


def _csv_differences(a, b):
    import csv
    import io
    ra = list(csv.DictReader(io.StringIO(a.decode("utf-8"))))
    rb = list(csv.DictReader(io.StringIO(b.decode("utf-8"))))
    if len(ra) != len(rb):
        return {"row count {0} != {1}".format(len(ra), len(rb))}
    skip = set(c for c, _why in ALLOWED_CSV_COLUMNS)
    diffs = set()
    for i, (x, y) in enumerate(zip(ra, rb)):
        if list(x) != list(y):
            diffs.add("header")
        for col in set(x) | set(y):
            if col not in skip and x.get(col) != y.get(col):
                diffs.add("row {0} {1}".format(i, col))
    return diffs


def _run_pair(monkeypatch, tmp_path, **cfg_overrides):
    runs = {}
    for flag in (False, True):
        root = tmp_path / ("on" if flag else "off")
        result, run_dir = fx.run(monkeypatch, root, cfg=fx.make_cfg(
            stage_a_legacy_prepass=flag, **cfg_overrides))
        run_id = os.path.basename(run_dir).split("__", 1)[1]
        runs[flag] = (_snapshot(root, run_id), result)
    return runs


def output_differences(monkeypatch, tmp_path, **cfg_overrides):
    """Every difference between the flag-off and flag-on runs beyond the
    ALLOWED ones: ``{path: description}``. Empty when gate 1 holds."""
    runs = _run_pair(monkeypatch, tmp_path, **cfg_overrides)
    off, on = runs[False][0], runs[True][0]
    diffs = {}
    for path in sorted(set(off) | set(on)):
        if path not in off or path not in on:
            diffs[path] = "present in one run only"
        elif off[path] == on[path]:
            continue
        elif path.endswith(".json"):
            found = _json_differences(off[path], on[path])
            if found:
                diffs[path] = sorted(found)
        elif path.endswith(".csv"):
            found = _csv_differences(off[path], on[path])
            if found:
                diffs[path] = sorted(found)
        else:
            diffs[path] = "bytes differ"
    return diffs, runs


# ---- gate 1 ------------------------------------------------------------------

def test_output_is_byte_identical_with_the_flag_on_and_off(monkeypatch, tmp_path):
    diffs, runs = output_differences(monkeypatch, tmp_path)
    assert diffs == {}, diffs
    # Not vacuous: both runs wrote every kind of file, and the captures are
    # the same files byte for byte (not merely both empty).
    off = runs[False][0]
    kinds = set(p.rsplit(".", 1)[-1] for p in off)
    assert {"tif", "json", "csv"} <= kinds, sorted(off)
    tiffs = [p for p in off if p.endswith(".tif")]
    assert len(tiffs) == 2 * len(fx.VIEWS)
    for name in ("run_meta_", "views_core_", "views_diagnostics_", "vop_view_element_map_"):
        assert any(os.path.basename(p).startswith(name) for p in off), name


def test_control_the_identity_test_sees_a_change_in_what_reaches_the_capture(
        monkeypatch, tmp_path):
    """THE CONTROL. A difference in the capture's input must be reported,
    or the empty diff above proves nothing."""
    real = fx.fake_collect_view_elements

    def _drop_one_when_flag_on(doc, view, raster, diag=None, cfg=None):
        out = real(doc, view, raster, diag=diag, cfg=cfg)
        return out[1:] if getattr(cfg, "stage_a_legacy_prepass", False) else out
    monkeypatch.setattr(fx, "fake_collect_view_elements", _drop_one_when_flag_on)
    diffs, _runs = output_differences(monkeypatch, tmp_path)
    assert any(p.endswith(".tif") for p in diffs), diffs


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
        snap = runs[flag][0]
        (name,) = [p for p in snap if os.path.basename(p).startswith("vop_view_element_map_")]
        views = json.loads(snap[name])["views"]
        assert dict((v["view_id"], v["element_ids"]) for v in views) == {
            13521188: [501, 502, 503], 13521190: [501, 505], 13521300: []}
