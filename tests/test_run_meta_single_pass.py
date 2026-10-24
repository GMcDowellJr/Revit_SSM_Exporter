"""run_meta carries each view's capture passes and the run's single-pass count (S4).

The per-view entry comes from the exporter's view SUMMARY
(``streaming._stage_a_registration_summary``), which is built from the
registered capture's result; both ends are composed here with the result a
real drafting-view capture returned (tests/stage_a_single_pass_fixtures.py).
"""
import json

from vop_interwoven import run_meta
from vop_interwoven.recapture import merge_recapture_into_run_meta
from vop_interwoven.streaming import _stage_a_registration_summary

from tests import stage_a_single_pass_fixtures as fx


def _summary(out):
    s = {"view_id": out["view_id"], "view_name": out.get("view_name"),
         "success": out.get("success"),
         "annotation_pass_success": out.get("annotation_pass_success")}
    s.update(_stage_a_registration_summary(out))
    return s


def test_a_single_pass_view_is_declared_in_run_meta(tmp_path):
    drafting = fx.run_fixture("drafting", tmp_path / "d")[0]
    model = fx.run_fixture("model", tmp_path / "m")[0]
    model_only = fx.run_fixture("model_only", tmp_path / "o")[0]
    meta = run_meta.finalize_run_meta({}, [_summary(drafting), _summary(model),
                                           _summary(model_only)])
    # Through a FILE, as the run writes it.
    path = run_meta.write_run_meta(dict(meta, output_layout="v1"), str(tmp_path))
    with open(path) as handle:
        meta = json.load(handle)
    d = [v for v in meta["views"] if v["capture_passes"] == ["annotation"]]
    assert len(d) == 1 and d[0]["capture_status"] == "success"
    assert d[0]["skipped_pass"]["skipped"] == "model"
    assert d[0]["skipped_pass"]["model_count"] == 0
    assert d[0]["capture_pass_decision"] == "membership_empty"
    both = [v for v in meta["views"] if v["capture_passes"] == ["model", "annotation"]]
    assert len(both) == 1 and both[0]["skipped_pass"] is None
    assert both[0]["capture_pass_decision"] == "both_have_members"
    assert meta["single_pass_views"] == {
        "count": 2, "denominator": 3,
        "denominator_rule": meta["single_pass_views"]["denominator_rule"],
        "by_skipped_pass": {"model": 1, "annotation": 1},
        "by_reason": {"membership_empty": 2},
        "both_passes_by_decision": {"both_have_members": 1}}


def test_a_view_that_never_read_its_membership_has_no_passes_entry():
    """A summary without the keys (a failed capture, an older build) gives an
    entry without them: absent, never a made-up two-pass value."""
    views = run_meta.view_outcomes([{"view_id": 1, "success": False,
                                     "failure_reason": "registered_capture_raised"}])
    assert "capture_passes" not in views[0]
    counts = run_meta.single_pass_views(views)
    assert counts["count"] == 0 and counts["denominator"] == 0


def test_batches_and_a_recapture_recount_the_run():
    a = {"run_id": "R", "config_hash": "h", "finalized": True,
         "views": [{"view_id": 1, "capture_passes": ["annotation"],
                    "skipped_pass": {"skipped": "model", "reason": "membership_empty"}}],
         "single_pass_views": {}}
    b = {"run_id": "R", "config_hash": "h", "finalized": True,
         "views": [{"view_id": 2, "capture_passes": ["model", "annotation"],
                    "skipped_pass": None, "capture_pass_decision": "both_have_members"}]}
    merged = run_meta.merge_run_metas([a, b])
    assert merged["single_pass_views"]["count"] == 1
    assert merged["single_pass_views"]["denominator"] == 2
    # A recapture of view 2 as single pass replaces its entry and recounts.
    record = {"captured_at": "t", "exporter_version": "v", "reason": "r"}
    summary = {"view_id": 2, "success": True, "capture_passes": ["model"],
               "skipped_pass": {"skipped": "annotation", "reason": "membership_empty"}}
    merged = merge_recapture_into_run_meta(merged, record, [summary])
    assert merged["single_pass_views"]["count"] == 2
    assert merged["single_pass_views"]["by_skipped_pass"] == {"model": 1, "annotation": 1}


def test_an_older_run_meta_gains_no_count():
    """Existing runs read exactly as before: a merge of records that carry no
    count writes none."""
    a = {"run_id": "R", "config_hash": "h", "finalized": True,
         "views": [{"view_id": 1, "capture_status": "success"}]}
    assert "single_pass_views" not in run_meta.merge_run_metas([a, dict(a)])
