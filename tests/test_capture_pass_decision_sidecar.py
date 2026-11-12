"""P2 (2026-11-10): ``capture_pass_decision`` is durable -- in every sidecar the
registered capture declares its passes in -- and has ONE reader,
``capture_passes.declared_pass_decision``.

Why: a continue of an interrupted run rebuilds run_meta ``views[]`` from the
files on disk, and the decision was the one per-view field (loop_timing
aside) that existed only in memory.

Fixtures are the single-pass capture's (tests/stage_a_single_pass_fixtures.py),
each a REAL registered capture on the fake Revit harness:

  * ``model``           -- two passes, ``both_have_members``;
  * ``drafting``        -- annotation only (no crop region, model 0),
    ``membership_empty``;
  * ``unresolved_only`` -- two passes for another reason,
    ``membership_unresolved`` (an element whose OwnerViewId will not read);
  * ``model_only``      -- model only, ``membership_empty``.

Every value is read back from the FILES (CLAUDE.md, defect class 4).
"""
import json

import pytest

from vop_interwoven import capture_passes as cp
from vop_interwoven import run_meta

from tests import stage_a_single_pass_fixtures as fx

# (fixture, decision, the sidecars it writes)
CASES = (
    ("model", "both_have_members", ("model", "annotation")),
    ("drafting", "membership_empty", ("annotation",)),
    ("unresolved_only", "membership_unresolved", ("model", "annotation")),
    ("model_only", "membership_empty", ("model",)),
)


# ---- gate 1: the file carries it -------------------------------------------------

@pytest.mark.parametrize("name,decision,written", CASES)
def test_every_declared_sidecar_file_carries_the_decision(tmp_path, name, decision, written):
    out = fx.run_fixture(name, tmp_path)[0]
    files = fx.sidecars(out)                     # read from disk
    assert sorted(k for k, v in files.items() if v is not None) == sorted(written)
    for which in written:
        side = files[which]
        # Reachability control: _declare_passes ran on this file.
        assert side["capture_passes"] == out["capture_passes"], which
        assert side["capture_pass_decision"] == decision, which
        assert cp.declared_pass_decision(side) == (decision, None), which


# ---- gate 2: composed with run_meta (class 1) -----------------------------------

def test_the_sidecar_decision_is_run_metas_for_the_same_view(tmp_path):
    """The run_meta entry comes from view_outcomes over the summaries a real
    StreamingExporter built from each capture's result, read back from the
    run_meta FILE it wrote. The fixtures all have view id 77, so a summary
    and its run_meta entry are paired by position (view_outcomes keeps the
    summaries' order); the decisions differ, so any mix-up is red."""
    from vop_interwoven.config import Config
    from vop_interwoven.streaming import StreamingExporter
    outs = [fx.run_fixture(name, tmp_path / name)[0] for name, _d, _w in CASES]
    exp = StreamingExporter(str(tmp_path / "run"), Config(enable_color_id_buffer_stage_a=True),
                            doc=None, export_png=False, export_csv=True,
                            run_id="20261110T1200", date_override="2026-11-10")
    for out in outs:
        exp.on_view_complete(out)
    summaries = list(exp.view_summaries)
    result = exp.finalize()
    with open(result["run_meta_path"]) as handle:
        views = json.load(handle)["views"]
    assert views == json.loads(json.dumps(run_meta.view_outcomes(summaries)))
    assert len(views) == len(summaries) == len(CASES)
    assert len(set(d for _n, d, _w in CASES[:3])) == 3   # discriminating
    for (name, _decision, written), summary, entry in zip(CASES, summaries, views):
        keys = {"model": "sidecar_path", "annotation": "annotation_sidecar_path"}
        for which in written:
            with open(summary[keys[which]]) as handle:
                side = json.load(handle)
            assert cp.declared_pass_decision(side) == (entry["capture_pass_decision"], None), (
                name, which)


# ---- gate 3: the reader refuses ------------------------------------------------

def test_a_sidecar_that_declares_nothing_reads_as_none():
    assert cp.declared_pass_decision({"capture_passes": ["model", "annotation"]}) == (None, None)
    assert cp.declared_pass_decision({}) == (None, None)
    assert cp.declared_pass_decision(None) == (None, None)


@pytest.mark.parametrize("value", ["both_have_member", "", None, 3, ["both_have_members"]])
def test_an_unknown_decision_is_an_error(value):
    decision, error = cp.declared_pass_decision(
        {"capture_passes": ["model", "annotation"], "capture_pass_decision": value})
    assert decision is None and "not a known" in error


def test_a_skip_decision_on_a_two_pass_sidecar_is_an_error():
    decision, error = cp.declared_pass_decision(
        {"capture_passes": ["model", "annotation"], "capture_pass_decision": "membership_empty"})
    assert decision is None and "disagrees" in error


@pytest.mark.parametrize("reason", cp.BOTH_REASONS)
def test_a_both_passes_decision_on_a_single_pass_sidecar_is_an_error(reason):
    side = {"capture_passes": ["annotation"], "capture_pass_decision": reason,
            "skipped_pass": cp.skip_record("model", {
                "model_count": 0, "annotation_count": 3, "unresolved_count": 0,
                "pipeline_model_elements": 0})}
    decision, error = cp.declared_pass_decision(side)
    assert decision is None and "disagrees" in error


def test_control_each_consistent_declaration_reads():
    single = {"capture_passes": ["model"], "capture_pass_decision": "membership_empty",
              "skipped_pass": cp.skip_record("annotation", {
                  "model_count": 3, "annotation_count": 0, "unresolved_count": 0,
                  "pipeline_model_elements": 3})}
    assert cp.declared_pass_decision(single) == ("membership_empty", None)
    for reason in cp.BOTH_REASONS:
        assert cp.declared_pass_decision(
            {"capture_passes": ["model", "annotation"], "capture_pass_decision": reason}) == (
            reason, None)


def test_a_decision_with_malformed_or_absent_passes_is_an_error():
    for side in ({"capture_pass_decision": "both_have_members"},
                 {"capture_passes": ["model"], "capture_pass_decision": "membership_empty"},
                 {"capture_passes": "model", "capture_pass_decision": "membership_empty"}):
        decision, error = cp.declared_pass_decision(side)
        assert decision is None and error, side
