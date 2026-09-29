"""C9: under Stage A the run emits views_core (view metadata + capture
status + elapsed + ConfigHash), views_diagnostics.views, and ONE run-level
config snapshot -- and no views_vop / views_occlusion / views_perf.

Driven through the real StreamingExporter and the real per-view loop.
"""
import csv
import json
import os
import types

from vop_interwoven.config import Config
from vop_interwoven.csv_export import (
    STAGE_A_CORE_CSV_HEADER, compute_config_hash, stage_a_capture_status,
)
from vop_interwoven.streaming import StreamingExporter
from tests import test_stage_a_skips_the_model_pass as loop


def _view(view_id=4242, name="L1 Plan"):
    return types.SimpleNamespace(
        Id=types.SimpleNamespace(IntegerValue=view_id), UniqueId="uid-%d" % view_id,
        Name=name, Scale=96, IsTemplate=False,
        ViewType=types.SimpleNamespace(ToString=lambda: "FloorPlan"),
    )


def _exporter(tmp_path, stage_a=True, perf=True):
    cfg = Config(enable_color_id_buffer_stage_a=stage_a, export_perf_csv=perf)
    return StreamingExporter(str(tmp_path), cfg, doc=None, export_png=False,
                             export_csv=True, date_override="2026-09-29"), cfg


def _rows(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_stage_a_run_writes_views_core_and_one_snapshot_only(tmp_path):
    exp, cfg = _exporter(tmp_path)
    exp.on_view_complete({"view_id": 4242, "view_name": "L1 Plan", "success": True,
                          "stage": "color_id_buffer_stage_a", "elapsed_sec": 1.25,
                          "view": _view()})
    exp.on_view_complete({"view_id": 7, "view_name": "Sec", "success": False,
                          "failure_reason": "export_dim_mismatch",
                          "stage": "color_id_buffer_stage_a", "elapsed_sec": 0.5,
                          "view": _view(7, "Sec")})
    out = exp.finalize()

    names = sorted(os.listdir(str(tmp_path)))
    assert not any(n.startswith(("views_vop_", "views_occlusion_", "views_perf_"))
                   for n in names), names
    snapshots = [n for n in names if n.startswith("vop_run_config_")]
    assert snapshots == [os.path.basename(out["run_config_path"])]

    snap = json.loads((tmp_path / snapshots[0]).read_text())
    assert snap["config"] == cfg.to_dict()
    assert snap["config_hash"] == compute_config_hash(cfg) == out["config_hash"]
    assert snap["run_id"] == exp.run_id

    rows = _rows(out["core_csv_path"])
    assert list(rows[0].keys()) == STAGE_A_CORE_CSV_HEADER
    ok, failed = rows
    assert (ok["ViewId"], ok["ViewName"], ok["Scale"]) == ("4242", "L1 Plan", "96")
    assert ok["ViewUniqueId"] == "uid-4242"
    assert ok["CaptureStatus"] == "success" and ok["CaptureFailureReason"] == ""
    assert ok["ElapsedSec"] == "1.250"
    # A failed capture is still inventoried, with its reason.
    assert failed["CaptureStatus"] == "failed"
    assert failed["CaptureFailureReason"] == "export_dim_mismatch"
    assert {r["ConfigHash"] for r in rows} == {snap["config_hash"]}


def test_control_the_raster_path_still_writes_the_full_csv_set(tmp_path):
    """Without it, a writer that stopped emitting every CSV would pass above."""
    exp, _cfg = _exporter(tmp_path, stage_a=False)
    out = exp.finalize()
    names = os.listdir(str(tmp_path))
    for prefix in ("views_core_", "views_vop_", "views_occlusion_", "views_perf_"):
        assert any(n.startswith(prefix) for n in names), (prefix, names)
    assert out["run_config_path"] is None
    assert not any(n.startswith("vop_run_config_") for n in names)


def test_capture_status_names_each_failure():
    assert stage_a_capture_status({"success": True}) == ("success", "")
    assert stage_a_capture_status({"success": False, "failure_reason": "x"}) == ("failed", "x")
    assert stage_a_capture_status({"success": True, "annotation_pass_success": False,
                                   "annotation_pass_failure_reason": "y"}) == (
        "annotation_failed", "y")
    assert stage_a_capture_status({"success": True, "registration_success": False,
                                   "registration": {"faults": [{"fault": "a"}, {"fault": "b"}]}}
                                  ) == ("registration_failed", "a;b")


def test_the_pipeline_gives_a_stage_a_view_elapsed_and_a_diagnostics_entry(monkeypatch, tmp_path):
    _model, _capture, results = loop._run_one_view(monkeypatch, tmp_path, stage_a=True)
    (out,) = results
    assert isinstance(out["elapsed_sec"], float) and out["elapsed_sec"] >= 0.0
    entry = out["diagnostics"]
    assert entry["view_id"] == loop.VIEW_ID
    assert entry["capture_status"] == "success"
    assert isinstance(entry["diagnostics"], dict)

    diag_files = [p for p in os.listdir(str(tmp_path)) if p.startswith("views_diagnostics_")]
    assert diag_files, os.listdir(str(tmp_path))
    payload = json.loads((tmp_path / diag_files[0]).read_text())
    assert str(loop.VIEW_ID) in payload["views"]
