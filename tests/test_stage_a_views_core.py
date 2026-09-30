"""C9: under Stage A the run emits views_core (view metadata + capture
status + elapsed + RunId/ConfigHash), views_diagnostics.views, and ONE
run_meta.json holding everything that is not per-view -- and no views_vop /
views_occlusion / views_perf.

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


class _Doc:
    Title = "Tower.rvt"
    PathName = r"C:\\models\\Tower.rvt"
    Application = types.SimpleNamespace(VersionNumber="2024", VersionBuild="24.1.0.66",
                                        VersionName="Autodesk Revit 2024")

    def GetElement(self, _eid):
        raise AssertionError("views_core must use view_result['view'] here")


def _exporter(tmp_path, stage_a=True, perf=True, run_id=None):
    cfg = Config(enable_color_id_buffer_stage_a=stage_a, export_perf_csv=perf)
    return StreamingExporter(str(tmp_path), cfg, doc=_Doc(), export_png=False,
                             export_csv=True, date_override="2026-09-29",
                             view_ids=[4242, 7], run_id=run_id), cfg


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
    assert out["run_meta_path"] == str(tmp_path / "run_meta.json")

    snap = json.loads((tmp_path / "run_meta.json").read_text())
    assert snap["finalized"] is True
    assert snap["config"] == cfg.to_dict()
    assert snap["config_hash"] == compute_config_hash(cfg) == out["config_hash"]
    assert snap["run_id"] == exp.run_id and snap["date"] == "2026-09-29"
    assert snap["doc_title"] == {"state": "value", "value": "Tower.rvt"}
    assert snap["revit_version"]["value"]["version_number"] == "2024"
    assert snap["exporter_version"] == "vop_interwoven"
    assert snap["git_commit"]["state"] == "value" and len(snap["git_commit"]["value"]) == 40
    assert snap["views_requested"] == [4242, 7]
    assert [(v["view_id"], v["capture_status"]) for v in snap["views"]] == [
        (4242, "success"), (7, "failed")]

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
    assert out["run_meta_path"] is None
    assert "run_meta.json" not in names


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


def test_run_meta_is_written_unfinalized_at_the_start(tmp_path):
    """An interrupted run leaves run_meta saying so, not a clean-looking file."""
    _exporter(tmp_path)
    snap = json.loads((tmp_path / "run_meta.json").read_text())
    assert snap["finalized"] is False and snap["views"] == []


def test_batches_share_one_run_id_and_merge_into_one_run_meta(tmp_path):
    from vop_interwoven.run_meta import merge_run_metas
    a, _ = _exporter(tmp_path / "b1")
    b, _ = _exporter(tmp_path / "b2", run_id=a.run_id)
    assert b.run_id == a.run_id
    metas = [json.loads((p / "run_meta.json").read_text())
             for p in (tmp_path / "b1", tmp_path / "b2")]
    merged = merge_run_metas(metas)
    assert merged["run_id"] == a.run_id and merged["batches"] == 2
    assert merged["views_requested"] == [4242, 7, 4242, 7]
    assert merged["finalized"] is False
    metas[1]["run_id"] = "other"
    import pytest
    with pytest.raises(ValueError):
        merge_run_metas(metas)


def test_a_deployed_copy_without_git_says_so(tmp_path):
    from vop_interwoven.run_meta import git_commit, revit_version
    got = git_commit(str(tmp_path))
    assert got["state"] in ("unavailable", "value")   # tmp may sit under a repo
    assert revit_version(None) == {"state": "unavailable", "reason": "no document"}


def test_a_tag_override_is_recorded_as_the_tag_not_as_the_date(tmp_path):
    """pipeline_0930_0739 wrote run_meta.date = "PR_221" (thinrunner's tag)."""
    import datetime as _dt
    cfg = Config(enable_color_id_buffer_stage_a=True)
    exp = StreamingExporter(str(tmp_path), cfg, doc=_Doc(), export_png=False,
                            export_csv=False, date_override="PR_221", view_ids=[1])
    meta = json.loads((tmp_path / "run_meta.json").read_text())
    _dt.datetime.strptime(meta["date"], "%Y-%m-%d")     # a real date
    assert meta["run_tag"] == "PR_221"
    assert exp.run_id.endswith("_PR_221")


def test_control_a_date_override_is_the_date_and_no_tag(tmp_path):
    _exporter(tmp_path)          # date_override="2026-09-29"
    meta = json.loads((tmp_path / "run_meta.json").read_text())
    assert meta["date"] == "2026-09-29" and meta["run_tag"] is None


def test_run_meta_is_replaced_atomically_and_survives_a_failed_write(tmp_path, monkeypatch):
    """review, PR #221: writing with "w" truncated the finalized:false record
    first. A serialisation that fails mid-way must leave the PREVIOUS record
    readable and no temporary file behind."""
    from vop_interwoven import run_meta as rm
    _exporter(tmp_path)                                    # finalized:false on disk
    before = (tmp_path / "run_meta.json").read_text()

    real_dump = json.dump

    def _dies(obj, handle, **kw):
        handle.write('{"partial": ')
        raise RuntimeError("process killed mid-write")
    monkeypatch.setattr(rm.json, "dump", _dies)
    import pytest
    with pytest.raises(RuntimeError):
        rm.write_run_meta({"finalized": True}, str(tmp_path))
    monkeypatch.setattr(rm.json, "dump", real_dump)

    assert (tmp_path / "run_meta.json").read_text() == before
    assert json.loads(before)["finalized"] is False
    assert not [p for p in os.listdir(str(tmp_path)) if p.endswith(".tmp")]


def test_merged_run_meta_is_written_per_batch_and_unfinalized_until_the_end(tmp_path):
    """Codex, PR #221: the root run_meta.json was written only after the last
    batch, so captures already moved into <output>/color_id_buffer by an
    earlier batch had none if a later batch raised -- and find_run_meta()
    then decoded crop-less sidecars at the default dpi."""
    from tools.decode_stage_a_color_id import find_run_meta
    from vop_interwoven.run_meta import write_merged_run_meta
    a, _ = _exporter(tmp_path / "b1")
    b, _ = _exporter(tmp_path / "b2", run_id=a.run_id)
    paths = [str(tmp_path / d / "run_meta.json") for d in ("b1", "b2")]
    for p in paths:     # each batch finished cleanly on its own
        meta = json.loads(open(p).read())
        meta["finalized"] = True
        open(p, "w").write(json.dumps(meta))
    out = tmp_path / "out"
    (out / "color_id_buffer").mkdir(parents=True)

    # After batch 1, before its captures move: the root record exists, and
    # says the run is NOT finished although batch 1 is.
    assert write_merged_run_meta(paths[:1], str(out), run_complete=False)
    sidecar = out / "color_id_buffer" / "V_1.json"
    found = find_run_meta(sidecar)
    assert found is not None and found["finalized"] is False
    assert found["run_id"] == a.run_id and found["batches"] == 1

    write_merged_run_meta(paths, str(out), run_complete=True)
    final = find_run_meta(sidecar)
    assert final["finalized"] is True and final["batches"] == 2
    assert write_merged_run_meta([], str(out), run_complete=True) is None


def test_thinrunner_writes_the_root_run_meta_before_relocating_a_batch():
    """The batch loop is top-level Dynamo script code, so its ORDER is pinned
    on the source: the per-batch write must precede the relocation call."""
    # Read as text: importing the module RUNS the Dynamo script.
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(
        __file__))), "vop_interwoven", "thinrunner_streaming.py")).read()
    write = src.index("write_merged_run_meta(batch_meta_paths, output_dir, run_complete=False)")
    move = src.index("_relocate_batch_stage_a_outputs(\n", src.index("for batch_index"))
    assert write < move


def test_a_failed_stage_a_view_reaches_views_core_through_the_real_loop(tmp_path, monkeypatch):
    """Codex, PR #221: process_document_views_streaming `continue`d on a
    success-False result before on_view_complete, so a failed capture never
    reached the exporter -- no views_core row, no run_meta entry, and
    views_failed 0. Driven through the REAL loop, not on_view_complete."""
    from vop_interwoven import streaming
    results = {
        4242: {"view_id": 4242, "view_name": "L1 Plan", "success": True,
               "stage": "color_id_buffer_stage_a", "elapsed_sec": 1.0, "view": _view()},
        # A capture failure the capture itself stamped...
        7: {"view_id": 7, "view_name": "Sec", "success": False,
            "failure_reason": "export_dim_mismatch",
            "stage": "color_id_buffer_stage_a", "view": _view(7, "Sec")},
        # ...and a pipeline exception stub, which carries no stage at all.
        8: {"view_id": 8, "view_name": "Elev", "success": False,
            "error": "RuntimeError: boom"},
    }
    monkeypatch.setattr(
        "vop_interwoven.pipeline.process_document_views",
        lambda doc, ids, cfg, **k: [dict(results[ids[0]])])
    exp, cfg = _exporter(tmp_path)
    streaming.process_document_views_streaming(
        exp.doc, [4242, 7, 8], cfg, on_view_complete=exp.on_view_complete)
    out = exp.finalize()

    assert out["views_failed"] == 2
    core = [f for f in os.listdir(str(tmp_path)) if f.startswith("views_core_")]
    rows = {r["ViewId"]: r for r in _rows(str(tmp_path / core[0]))}
    assert rows["7"]["CaptureStatus"] == "failed"
    assert rows["7"]["CaptureFailureReason"] == "export_dim_mismatch"
    assert rows["8"]["CaptureStatus"] == "failed"
    assert "boom" in rows["8"]["CaptureFailureReason"]
    assert rows["4242"]["CaptureStatus"] == "success"
    meta = json.loads((tmp_path / "run_meta.json").read_text())
    assert sorted(v["view_id"] for v in meta["views"]) == [7, 8, 4242]
