"""views_diagnostics_<date>.json is merged across the per-view calls of ONE
run and never across runs.

Streaming calls process_document_views once per view, so each call merges
into the file. The file is named by date only, and it merged whatever an
earlier run had left there -- the same cross-run leak d57d2a9 closed for the
view-element map. Under Stage A this file's views are populated (C9), so an
earlier run's views read as this run's.
"""
import os

from vop_interwoven.pipeline import _merge_prior_view_diagnostics

_PRIOR = {"metadata": {"exporter_run_id": "RUN_A"},
          "views": {"1": {"view_id": 1}, "2": {"view_id": 2}}}


def test_the_same_run_merges():
    views = _merge_prior_view_diagnostics(_PRIOR, {"3": {"view_id": 3}}, "RUN_A")
    assert sorted(views) == ["1", "2", "3"]


def test_a_prior_or_unstamped_run_is_replaced_not_merged():
    for prior in (_PRIOR, {"metadata": {}, "views": _PRIOR["views"]},
                  {"views": _PRIOR["views"]}):
        views = _merge_prior_view_diagnostics(prior, {"3": {"view_id": 3}}, "RUN_B")
        assert sorted(views) == ["3"], prior


def test_without_a_run_id_the_file_still_merges():
    """A caller with no StreamingExporter: each per-view call must add."""
    views = _merge_prior_view_diagnostics(_PRIOR, {"3": {"view_id": 3}}, None)
    assert sorted(views) == ["1", "2", "3"]


def test_this_call_wins_over_a_stale_entry_for_the_same_view():
    views = _merge_prior_view_diagnostics(_PRIOR, {"2": {"view_id": 2, "new": True}}, "RUN_A")
    assert views["2"] == {"view_id": 2, "new": True}


def test_the_pipeline_stamps_and_scopes_by_the_exporters_run_id():
    """Pinned on the source: the helper is only as good as the id handed to
    it, and the pipeline's own run_id is per CALL (one per view)."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "vop_interwoven", "pipeline.py")).read()
    block = src[src.index('diag_filename = f"views_diagnostics_{date_str}.json"'):]
    block = block[:block.index("json.dump(payload, f, indent=2)")]
    assert 'exporter_run_id = getattr(cfg, "_view_element_map_run_id", None)' in src
    assert '"exporter_run_id": exporter_run_id' in block
    assert "_merge_prior_view_diagnostics(" in block and "exporter_run_id)" in block
