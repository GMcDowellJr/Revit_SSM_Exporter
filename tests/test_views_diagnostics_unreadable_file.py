"""D0 (2026-11-09): what the per-call views_diagnostics merge does to an
existing file it cannot parse.

The merge in process_document_views reads the existing
views_diagnostics_<d>.json, merges it, and rewrites it. Its read was wrapped
in ``except Exception: pass`` ("best-effort merge only"), so a file that does
not parse -- one torn by an interrupted rewrite, say -- was REPLACED by a
file holding only the current call's views: every earlier view's entry of
the run erased, and nothing recorded.

This test pins that behaviour at baseline (main @ 5112db6). D2 replaces it;
the test then asserts the replacement, and restoring the silent fallback
turns it red (gate 4c).

The caller here has no StreamingExporter (no exporter run id), which is the
caller that keeps the per-call merge after D2.
"""
import json
import os

from tests import stage_a_loop_fixture as fx
from vop_interwoven import pipeline
from vop_interwoven.core.diagnostics import Diagnostics

TORN = '{\n  "metadata": {\n    "date": "2026-11-09",\n  "views": {\n    "1": {"view_'


def _run_one_view(monkeypatch, out_dir, diag):
    fx.install(monkeypatch)
    cfg = fx.make_cfg()
    cfg.output_dir = str(out_dir)
    cfg.date_override = "2026-11-09"
    with fx.fake_revit_db():
        pipeline.process_document_views(fx.FakeDoc(), [fx.VIEWS[0][0]], cfg, diag=diag)
    return os.path.join(str(out_dir), "views_diagnostics_2026-11-09.json")


def _export_counts(diag):
    """``{level: count}`` of what the diagnostics write recorded. From
    Diagnostics.counts, not .events: events are capped (max_events) and a
    view's run fills them, while counts are kept for every record."""
    out = {}
    for key, n in diag.counts.items():
        level, _phase, callsite, _exc = key.split("|", 3)
        if callsite.startswith("process_document_views.export_diagnostics"):
            out[level] = out.get(level, 0) + n
    return out


def test_an_unreadable_file_is_overwritten_with_the_current_view_only(monkeypatch, tmp_path):
    path = os.path.join(str(tmp_path), "views_diagnostics_2026-11-09.json")
    with open(path, "w") as f:
        f.write(TORN)
    diag = Diagnostics()
    assert _run_one_view(monkeypatch, tmp_path, diag) == path
    payload = json.load(open(path))           # the torn file is gone ...
    assert sorted(payload["views"]) == [str(fx.VIEWS[0][0])]   # ... replaced
    # and the caller is told nothing. (The write site records into ``diag``,
    # but inside the loop that name is the LAST view's per-view Diagnostics,
    # already serialised into its entry -- so even its routine "Exported"
    # info never reaches the caller's object.)
    assert _export_counts(diag) == {}, diag.counts
    assert not [n for n in os.listdir(str(tmp_path)) if n.startswith("views_diagnostics_")
                and n != os.path.basename(path)]


def test_control_a_readable_file_of_the_same_caller_is_merged(monkeypatch, tmp_path):
    path = os.path.join(str(tmp_path), "views_diagnostics_2026-11-09.json")
    with open(path, "w") as f:
        json.dump({"metadata": {"exporter_run_id": None},
                   "views": {"1": {"view_id": 1}}}, f, indent=2)
    _run_one_view(monkeypatch, tmp_path, Diagnostics())
    assert sorted(json.load(open(path))["views"]) == ["1", str(fx.VIEWS[0][0])]
