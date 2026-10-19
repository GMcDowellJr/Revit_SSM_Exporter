"""Recapture in place (vop_interwoven/recapture.py): the listed views of an
EXISTING run are captured again into that run's directory, their files and
rows replaced and stamped, and nothing of any other view touched.

Driven at three levels, because each one can fail alone: the pure plan /
merge functions; the real StreamingExporter and the real entry point
(run_vop_pipeline_streaming, the thinrunner's IN[7] branch executed from its
own source) with the per-view capture faked; and the real registered
capture on the fake Revit world, which must REFUSE a view whose ticks would
still lie outside its crop loop before it exports anything.

What is asserted is the FILES after the recapture (CLAUDE.md, defect class
4), with a byte-for-byte control on every file the recapture must not touch.
"""
import csv
import ctypes
import hashlib
import json
import os
import types
from pathlib import Path

import pytest

import vop_interwoven.stage_a_registration as registration
from vop_interwoven import recapture as rc
from vop_interwoven.config import Config
from vop_interwoven.recapture import (
    RecaptureRefused, load_recapture_lists, merge_recapture_into_run_meta,
    merge_views_core_rows, recapture_plan, recapture_record, run_recapture,
)
from vop_interwoven.streaming import StreamingExporter, run_vop_pipeline_streaming

from tests import test_run_directory as rd
from tests import test_stage_a_registered_capture as cap
from tests import test_stage_a_views_core as vc
from tests.layout_helpers import run_meta_file

META = {
    "schema": "vop.run_meta.v1", "finalized": True, "run_id": "20261008T093539",
    "date": "2025-06-21", "exporter_version": "1.1.0+src.70a96ed2b83c",
    "doc_title": {"state": "value", "value": "KSRF.rvt"},
    "views_requested": [930011, 3939979, 7035379],
    "views": [
        {"view_id": 930011, "view_name": "(N) LOWER LEVEL", "capture_status": "success",
         "capture_failure_reason": ""},
        {"view_id": 3939979, "view_name": "LEVEL 2", "capture_status": "success",
         "capture_failure_reason": ""},
        {"view_id": 7035379, "view_name": "LEVEL 3", "capture_status": "failed",
         "capture_failure_reason": "export_dim_mismatch"},
    ],
}


def _meta(**over):
    meta = json.loads(json.dumps(META))
    meta.update(over)
    return meta


def _summary(view_id, success=True, reason=None):
    out = {"view_id": view_id, "view_name": "V {0}".format(view_id), "success": success,
           "stage": "color_id_buffer_stage_a", "elapsed_sec": 1.0}
    if reason:
        out["failure_reason"] = reason
    return out


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --- the plan ---------------------------------------------------------------------

def test_a_plan_continues_a_finished_run_of_the_same_document_on_requested_ids():
    plan = recapture_plan(_meta(), ["3939979", 7035379], "KSRF.rvt")
    assert plan == {"run_id": "20261008T093539", "as_of_date": "2025-06-21",
                    "view_ids": [3939979, 7035379]}


@pytest.mark.parametrize("over, ids, title, words", [
    ({"finalized": False}, [3939979], "KSRF.rvt", "not finalized"),
    ({"run_id": None}, [3939979], "KSRF.rvt", "no run_id"),
    ({"doc_title": {"state": "unavailable"}}, [3939979], "KSRF.rvt", "no document title"),
    ({}, [3939979], "Other.rvt", "captured from 'KSRF.rvt'"),
    ({}, [3939979, 4], "KSRF.rvt", "never requested: [4]"),
    ({}, [], "KSRF.rvt", "no view ids"),
])
def test_a_plan_is_refused_for_what_it_cannot_show_to_be_this_runs(over, ids, title, words):
    with pytest.raises(RecaptureRefused) as err:
        recapture_plan(_meta(**over), ids, title)
    assert words in str(err.value)


def test_the_record_says_when_which_build_why_and_what_it_replaces():
    from datetime import datetime
    import vop_interwoven
    record = recapture_record(_meta(), ["3939979"], now=datetime(2026, 10, 12, 15, 4, 5))
    assert record["schema"] == rc.RECAPTURE_SCHEMA
    assert record["captured_at"] == "20261012T150405"
    assert record["exporter_version"].startswith(vop_interwoven.__version__)
    assert record["reason"] == rc.RECAPTURE_REASON
    assert record["view_ids"] == [3939979]
    assert record["replaces_exporter_version"] == "1.1.0+src.70a96ed2b83c"


# --- run_meta merge -----------------------------------------------------------------

def _record():
    from datetime import datetime
    return recapture_record(_meta(), [3939979, 7035379], now=datetime(2026, 10, 12, 15, 0, 0))


def test_the_merge_replaces_only_the_recaptured_entries_and_stamps_them():
    meta = _meta()
    before = json.loads(json.dumps(meta["views"]))
    out = merge_recapture_into_run_meta(
        meta, _record(), [_summary(3939979), _summary(7035379, False, "boom"),
                          _summary(424242)])
    assert out is meta and meta["finalized"] is True
    assert meta["views"][0] == before[0]                      # untouched, same place
    assert meta["views"][1]["capture_status"] == "success"
    assert meta["views"][1]["recapture"] == {
        "captured_at": "20261012T150000", "exporter_version": _record()["exporter_version"],
        "reason": "ticks_outside_crop_loop"}
    assert meta["views"][2]["capture_status"] == "failed"
    assert meta["views"][2]["capture_failure_reason"] == "boom"
    assert "recapture" in meta["views"][2]
    # An outcome for an id the run holds no entry for is appended, not lost.
    assert meta["views"][3]["view_id"] == 424242 and "recapture" in meta["views"][3]
    assert "recapture" not in meta["views"][0]
    (entry,) = meta["recaptures"]
    assert entry["captured_at"] == "20261012T150000"
    assert entry["outcomes"] == {"3939979": "success", "7035379": "failed",
                                 "424242": "success"}
    assert entry["refused_view_ids"] == []
    assert meta["exporter_version"] == "1.1.0+src.70a96ed2b83c"   # the run's, still


def test_a_refused_outcome_replaces_nothing_and_is_listed_as_refused():
    meta = _meta()
    before = json.loads(json.dumps(meta["views"]))
    merge_recapture_into_run_meta(
        meta, _record(),
        [_summary(3939979, False, "recapture_refused"), _summary(7035379)])
    assert meta["views"][1] == before[1]            # no stamp, old outcome
    assert "recapture" in meta["views"][2]
    (entry,) = meta["recaptures"]
    assert entry["refused_view_ids"] == [3939979]
    assert entry["outcomes"]["3939979"] == "failed"


def test_one_recapture_written_in_chunks_is_one_entry_and_a_later_one_a_second():
    """The thinrunner's batches each finalize under the SAME record (found
    by the first version of this test: batch_size 1 over two views wrote
    two entries for one recapture)."""
    from datetime import datetime
    meta = _meta()
    merge_recapture_into_run_meta(meta, _record(), [_summary(3939979)])
    merge_recapture_into_run_meta(meta, _record(),
                                  [_summary(7035379, False, "recapture_refused")])
    (entry,) = meta["recaptures"]
    assert entry["outcomes"] == {"3939979": "success", "7035379": "failed"}
    assert entry["refused_view_ids"] == [7035379]
    later = recapture_record(_meta(), [930011], now=datetime(2026, 10, 13, 9, 0, 0))
    merge_recapture_into_run_meta(meta, later, [_summary(930011)])
    assert [e["captured_at"] for e in meta["recaptures"]] == [
        "20261012T150000", "20261013T090000"]
    assert [v["view_id"] for v in meta["views"]] == [930011, 3939979, 7035379]
    assert all("recapture" in v for v in meta["views"] if v["view_id"] != 7035379)
    assert "recapture" not in meta["views"][2]


# --- views_core merge ---------------------------------------------------------------

HEADER = ["RunId", "ConfigHash", "ViewId", "ViewName", "CaptureStatus", "CaptureFailureReason"]


def _write_rows(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER)
        writer.writeheader()
        writer.writerows(rows)


def _row(run_id, vid, status="success", reason="", name=None):
    return {"RunId": run_id, "ConfigHash": "9400eaef", "ViewId": str(vid),
            "ViewName": name or "V {0}".format(vid), "CaptureStatus": status,
            "CaptureFailureReason": reason}


def test_rows_are_replaced_by_run_and_view_and_every_other_row_is_kept_byte_for_byte(tmp_path):
    path = str(tmp_path / "views_core_2025-06-21.csv")
    _write_rows(path, [_row("R1", 930011), _row("R1", 3939979, name="LEVEL 2, old"),
                       _row("R0", 3939979, name="another run's"), _row("R1", 7035379)])
    lines_before = Path(path).read_text(encoding="utf-8").splitlines()

    replaced, appended = merge_views_core_rows(
        path, [_row("R1", 3939979, name="LEVEL 2, new"), _row("R1", 424242)], HEADER)

    assert (replaced, appended) == (1, 1)
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    assert lines[0] == lines_before[0]
    assert lines[1] == lines_before[1]                    # 930011 byte-identical
    assert "LEVEL 2, new" in lines[2] and lines[2].startswith("R1,")
    assert lines[3] == lines_before[3]                    # R0's 3939979 is not this run's
    assert lines[4] == lines_before[4]
    assert lines[5].startswith("R1,9400eaef,424242,")
    assert len(lines) == 6
    assert not os.path.exists(path + ".recapture.tmp")


def test_a_missing_views_core_is_created_from_the_header(tmp_path):
    path = str(tmp_path / "views_core_2025-06-21.csv")
    assert merge_views_core_rows(path, [_row("R1", 1)], HEADER) == (0, 1)
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [r["ViewId"] for r in rows] == ["1"] and list(rows[0].keys()) == HEADER


def test_the_files_header_wins_over_the_callers(tmp_path):
    """A run written by an older build keeps its column set; a new column
    the recapture's rows carry is dropped rather than the file re-shaped."""
    path = str(tmp_path / "views_core.csv")
    _write_rows(path, [_row("R1", 1)])
    new = dict(_row("R1", 1, name="new"), Extra="x")
    merge_views_core_rows(path, [new], HEADER + ["Extra"])
    text = Path(path).read_text(encoding="utf-8")
    assert text.splitlines()[0] == ",".join(HEADER) and "Extra" not in text and "new" in text


# --- the list ----------------------------------------------------------------------

def test_the_list_is_read_from_a_path_a_json_text_or_a_dict(tmp_path):
    payload = {"runs": [{"run_dir": "x", "run_id": "r", "recapture": [1]}]}
    path = tmp_path / "recapture_lists.json"
    path.write_text(json.dumps(payload))
    assert load_recapture_lists(str(path)) == payload["runs"]
    assert load_recapture_lists(json.dumps(payload)) == payload["runs"]
    assert load_recapture_lists(payload) == payload["runs"]
    with pytest.raises(RecaptureRefused):
        load_recapture_lists({"views": []})


# --- the exporter -----------------------------------------------------------------------

def _original_run(tmp_path):
    """A finished Stage A run in tmp_path: 4242 success, 7 failed."""
    exp, cfg = vc._exporter(tmp_path)
    exp.on_view_complete(dict(_summary(4242), view=vc._view()))
    exp.on_view_complete(dict(_summary(7, False, "export_dim_mismatch"), view=vc._view(7, "Sec")))
    exp.finalize()
    return exp.run_id, cfg


def _recapture_exporter(tmp_path, run_id, cfg, view_ids=(7,), **over):
    record = recapture_record(json.loads(run_meta_file(tmp_path).read_text()),
                              list(view_ids))
    kwargs = dict(doc=vc._Doc(), export_png=False, export_csv=True,
                  date_override="2026-09-29", view_ids=list(view_ids), run_id=run_id,
                  recapture=record)
    kwargs.update(over)
    return StreamingExporter(str(tmp_path), cfg, **kwargs), record


def _core(tmp_path):
    (path,) = [p for p in tmp_path.iterdir() if p.name.startswith("views_core_")]
    return path


def test_an_exporter_recapture_merges_run_meta_and_views_core_in_place(tmp_path):
    run_id, cfg = _original_run(tmp_path)
    core = _core(tmp_path)
    meta_before = json.loads(run_meta_file(tmp_path).read_text())
    core_before = core.read_text(encoding="utf-8").splitlines()

    exp, record = _recapture_exporter(tmp_path, run_id, cfg)
    assert cfg._recapture_record is record
    exp.on_view_complete(dict(_summary(7), view=vc._view(7, "Sec")))
    out = exp.finalize()

    assert out["views_failed"] == 0 and exp.recapture_rows_merged == (1, 0)
    assert not list(tmp_path.glob("*.tmp"))
    meta = json.loads(run_meta_file(tmp_path).read_text())
    assert meta["finalized"] is True and meta["run_id"] == run_id
    assert meta["views_requested"] == meta_before["views_requested"] == [4242, 7]
    assert meta["exporter_version"] == meta_before["exporter_version"]
    assert meta["config_hash"] == meta_before["config_hash"]
    assert meta["views"][0] == meta_before["views"][0]
    assert meta["views"][1]["capture_status"] == "success"
    assert meta["views"][1]["recapture"]["captured_at"] == record["captured_at"]
    assert len(meta["views"]) == 2
    (entry,) = meta["recaptures"]
    assert entry["view_ids"] == [7] and entry["outcomes"] == {"7": "success"}
    lines = core.read_text(encoding="utf-8").splitlines()
    assert lines[0] == core_before[0] and lines[1] == core_before[1]
    assert len(lines) == 3
    rows = vc._rows(str(core))
    assert rows[1]["ViewId"] == "7" and rows[1]["CaptureStatus"] == "success"
    assert rows[1]["RunId"] == run_id


def test_a_recapture_that_reports_nothing_backfills_its_own_ids_only(tmp_path):
    run_id, cfg = _original_run(tmp_path)
    core = _core(tmp_path)
    before = core.read_text(encoding="utf-8").splitlines()
    exp, _record = _recapture_exporter(tmp_path, run_id, cfg)
    out = exp.finalize()
    assert out["views_failed"] == 1
    meta = json.loads(run_meta_file(tmp_path).read_text())
    by_id = {v["view_id"]: v for v in meta["views"]}
    assert by_id[7]["capture_failure_reason"].startswith("no_outcome_reported")
    assert "recapture" in by_id[7] and "recapture" not in by_id[4242]
    lines = core.read_text(encoding="utf-8").splitlines()
    assert lines[1] == before[1] and len(lines) == 3
    assert "no_outcome_reported" in lines[2]


def test_a_refused_recapture_leaves_the_views_row_and_entry_as_they_were(tmp_path):
    run_id, cfg = _original_run(tmp_path)
    core = _core(tmp_path)
    before = core.read_text(encoding="utf-8")
    meta_before = json.loads(run_meta_file(tmp_path).read_text())
    exp, _record = _recapture_exporter(tmp_path, run_id, cfg)
    exp.on_view_complete(dict(_summary(7, False, "recapture_refused"), view=vc._view(7, "Sec")))
    exp.finalize()
    assert core.read_text(encoding="utf-8") == before
    assert exp.recapture_rows_merged == (0, 0)
    meta = json.loads(run_meta_file(tmp_path).read_text())
    assert meta["views"] == meta_before["views"]
    assert meta["recaptures"][0]["refused_view_ids"] == [7]


def test_an_exporter_recapture_is_refused_outside_its_run(tmp_path):
    run_id, cfg = _original_run(tmp_path)
    with pytest.raises(RecaptureRefused, match="holds run"):
        _recapture_exporter(tmp_path, "20991231T000000", cfg)
    with pytest.raises(RecaptureRefused, match="Stage A is off"):
        _recapture_exporter(tmp_path, run_id, Config(enable_color_id_buffer_stage_a=False))
    # Codex, PR #236: the frame-B fallback would reproduce the failure.
    with pytest.raises(RecaptureRefused, match="REGISTERED capture"):
        _recapture_exporter(tmp_path, run_id, Config(enable_color_id_buffer_stage_a=True,
                                                     color_id_buffer_registered_capture=False))
    other = tmp_path / "empty"
    other.mkdir()
    with pytest.raises(RecaptureRefused, match="no run_meta file"):
        StreamingExporter(str(other), cfg, doc=vc._Doc(), export_png=False,
                          export_csv=True, date_override="2026-09-29", view_ids=[7],
                          run_id=run_id, recapture={"captured_at": "x"})
    # Nothing was written into either directory by a refused construction.
    assert sorted(p.name for p in other.iterdir()) == []
    with pytest.raises(ValueError, match="names the run it continues"):
        run_vop_pipeline_streaming(vc._Doc(), [7], cfg, output_dir=str(tmp_path),
                                   recapture={"captured_at": "x"})


# --- run_recapture with an injected runner ---------------------------------------------

def _write_meta(run_dir, **over):
    os.makedirs(run_dir, exist_ok=True)
    meta = _meta(**over)
    with open(str(run_meta_file(run_dir)), "w") as handle:
        json.dump(meta, handle)
    return meta


def test_run_recapture_continues_each_runs_identity_in_chunks(tmp_path):
    root = tmp_path / "KSRF"
    run_a = str(root / "2025-06-21__20261008T093539")
    run_b = str(root / "2025-06-13__20261001T084840")
    _write_meta(run_a)
    _write_meta(run_b, run_id="20261001T084840", date="2025-06-13",
                doc_title={"state": "value", "value": "Other.rvt"})
    calls = []

    def runner(**kw):
        calls.append(kw)
        return {"views_processed": len(kw["view_ids"]), "views_failed": 0,
                "annotation_passes_failed": 0, "csv_rows_written": len(kw["view_ids"]),
                "view_summaries": [_summary(v) for v in kw["view_ids"]]}

    lists = {"runs": [
        {"run_dir": run_a, "run_id": "20261008T093539",
         "recapture": [930011, 3939979, 7035379], "review": [], "keep": 0},
        {"run_dir": run_b, "run_id": "20261001T084840", "recapture": [930011]},
        {"run_dir": str(root / "none"), "run_id": "x", "recapture": []},
    ]}
    doc = types.SimpleNamespace(Title="KSRF.rvt")
    cfg = Config(enable_color_id_buffer_stage_a=True)
    out = run_recapture(doc, lists, cfg, batch_size=2, runner=runner)

    assert [c["view_ids"] for c in calls] == [[930011, 3939979], [7035379]]
    for call in calls:
        assert call["doc"] is doc and call["cfg"] is cfg
        assert call["output_dir"] == str(root)            # the ROOT: the run dir is derived
        assert call["date_override"] == "2025-06-21"
        assert call["run_id"] == "20261008T093539"
        assert call["recapture"]["view_ids"] == [930011, 3939979, 7035379]
        assert call["recapture"]["replaces_exporter_version"] == "1.1.0+src.70a96ed2b83c"
    assert calls[0]["recapture"] is calls[1]["recapture"]
    assert out["views_processed"] == 3 and out["views_failed"] == 0
    assert [r.get("run_id") for r in out["runs"]] == ["20261008T093539", "x"]
    assert out["runs"][0]["chunks"] == 2 and out["runs"][0]["views"] == 3
    assert out["runs"][1]["skipped"] == "nothing to recapture"
    (refused,) = out["refused"]
    assert refused["run_id"] == "20261001T084840" and "Other.rvt" in refused["reason"]


def test_run_recapture_refuses_a_run_dir_without_run_meta(tmp_path):
    out = run_recapture(types.SimpleNamespace(Title="KSRF.rvt"),
                        {"runs": [{"run_dir": str(tmp_path), "run_id": "r", "recapture": [1]}]},
                        Config(enable_color_id_buffer_stage_a=True),
                        runner=lambda **kw: pytest.fail("must not run"))
    assert out["runs"] == [] and "no run_meta file" in out["refused"][0]["reason"]


# --- the real entry point, capture faked ------------------------------------------------

def _tree_hashes(root):
    return {str(p.relative_to(root)): _sha(p) for p in sorted(Path(root).rglob("*")) if p.is_file()}


def test_a_recapture_through_the_real_entry_point_rewrites_only_the_listed_views(
        tmp_path, monkeypatch):
    rd._fake_capture(monkeypatch)
    cfg = Config(enable_color_id_buffer_stage_a=True)
    first = rd._run(tmp_path, "Tower.rvt", view_ids=(4242, 7), date_override="2026-09-29",
                    cfg=cfg)
    run_dir = Path(first["run_dir"])
    assert run_dir.name == "2026-09-29__" + first["run_id"]
    core = run_dir / "views_core_2026-09-29.csv"
    # The run's row for 4242 as an older build left it (the fake's rows are
    # otherwise identical before and after, so the replacement must show).
    lines = core.read_text(encoding="utf-8").splitlines()
    assert ",4242," in lines[1]
    lines[1] = lines[1].replace(",success,", ",registration_failed,old_build_reason", 1)
    assert "old_build_reason" in lines[1]
    core.write_text("\n".join(lines) + "\n", encoding="utf-8")
    before = _tree_hashes(run_dir)
    core_before = core.read_text(encoding="utf-8").splitlines()

    calls = []

    def writer(stem, vid):
        Path(stem + ".tiff").write_text("RECAPTURED pixels of view {0}".format(vid))
        Path(stem + ".json").write_text(json.dumps({"view_id": vid, "recaptured": True}))
    rd._fake_capture(monkeypatch, writer=writer, calls=calls)
    lists = tmp_path / "recapture_lists.json"
    lists.write_text(json.dumps({"runs": [{"run_dir": str(run_dir), "run_id": first["run_id"],
                                           "recapture": [4242], "review": [], "keep": 1}]}))
    out = run_recapture(rd._Doc("Tower.rvt"), str(lists), cfg)

    assert calls == [4242] and out["views_processed"] == 1 and out["refused"] == []
    after = _tree_hashes(run_dir)
    assert sorted(after) == sorted(before), "no file appears or disappears"
    changed = sorted(k for k in before if before[k] != after[k])
    # A v2 run (output_layout): color_id/ and run_meta_<d>.json.
    assert changed == ["color_id/V_4242.json", "color_id/V_4242.tiff",
                       "run_meta_2026-09-29.json", "views_core_2026-09-29.csv",
                       "views_diagnostics_2026-09-29.json"], changed
    assert "RECAPTURED" in (run_dir / "color_id" / "V_4242.tiff").read_text()
    assert "RECAPTURED" not in (run_dir / "color_id" / "V_7.tiff").read_text()
    meta = rd._meta(run_dir)
    assert meta["run_id"] == first["run_id"] and meta["finalized"] is True
    assert meta["views_requested"] == [4242, 7]
    assert [(v["view_id"], "recapture" in v) for v in meta["views"]] == [(4242, True), (7, False)]
    assert meta["recaptures"][0]["view_ids"] == [4242]
    lines = core.read_text(encoding="utf-8").splitlines()
    assert lines[0] == core_before[0] and lines[2] == core_before[2] and len(lines) == 3
    assert lines[1].startswith(first["run_id"] + ",") and ",4242," in lines[1]
    assert "old_build_reason" not in lines[1] and ",success," in lines[1]
    # No second run directory was minted.
    assert sorted(p.name for p in tmp_path.iterdir()) == [run_dir.name, "recapture_lists.json"]


def _thinrunner(monkeypatch, root, lists, stage_a=True):
    import vop_interwoven.entry_dynamo as entry
    doc = rd._Doc("Tower.rvt")
    monkeypatch.setattr(entry, "get_current_document", lambda: doc)
    monkeypatch.setattr(entry, "get_current_view", lambda: None)
    kernel = types.SimpleNamespace(SetThreadExecutionState=lambda flags: 0)
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(kernel32=kernel),
                        raising=False)
    src = rd.THINRUNNER.read_text(encoding="utf-8")
    assert src.count("RELOAD_MODULES = True") == 1
    src = src.replace("RELOAD_MODULES = True", "RELOAD_MODULES = False")
    ns = {"IN": [None, None, str(root), 1, False, stage_a, True, lists]}
    exec(compile(src, str(rd.THINRUNNER), "exec"), ns)
    return ns["OUT"]


def test_the_thinrunner_recaptures_from_a_list_and_reports_it(tmp_path, monkeypatch):
    rd._fake_capture(monkeypatch)
    first = rd._run(tmp_path, "Tower.rvt", view_ids=(4242, 7, 9), date_override="2026-09-29")
    run_dir = Path(first["run_dir"])
    calls = []
    rd._fake_capture(monkeypatch, calls=calls)
    lists = tmp_path / "recapture_lists.json"
    lists.write_text(json.dumps({"runs": [{"run_dir": str(run_dir), "run_id": first["run_id"],
                                           "recapture": [7, 9]}]}))
    out = _thinrunner(monkeypatch, tmp_path, str(lists))
    assert "STATUS: SUCCESS" in out, out
    assert "RECAPTURE IN PLACE" in out and "STREAMING MODE" not in out
    assert "RUN {0}: 2 view(s), 2 chunk(s), 2 processed, 0 failed".format(first["run_id"]) in out
    assert "Next: register_stage_a_annotation.py" in out
    assert "Run directory: {0}".format(run_dir) in out
    assert calls == [7, 9]
    meta = rd._meta(run_dir)
    assert len(meta["recaptures"]) == 1 and meta["recaptures"][0]["view_ids"] == [7, 9]
    assert sorted(p.name for p in tmp_path.iterdir()) == [run_dir.name, "recapture_lists.json"]


def test_the_thinrunner_refuses_a_recapture_without_stage_a(tmp_path, monkeypatch):
    calls = []
    rd._fake_capture(monkeypatch, calls=calls)
    out = _thinrunner(monkeypatch, tmp_path, '{"runs": []}', stage_a=False)
    assert "ERROR" in out and "IN[7] (recapture) needs IN[5]" in out
    assert calls == [] and list(tmp_path.iterdir()) == []


# --- the registered capture on the fake world --------------------------------------------

RECORD = {"schema": rc.RECAPTURE_SCHEMA, "captured_at": "20261012T150000",
          "exporter_version": "1.2.0+src.abc", "reason": rc.RECAPTURE_REASON,
          "view_ids": [cap.VIEW_ID], "replaces_exporter_version": "1.1.0+src.70a96ed2b83c"}


def _in_recapture(cfg):
    cfg._recapture_record = dict(RECORD)


def test_the_capture_refuses_a_recapture_whose_ticks_would_still_lie_outside(
        tmp_path, monkeypatch):
    """The shipped placement (loop reference refused) in recapture mode:
    nothing is exported, no sidecar is written, the view is rolled back,
    and the outcome names the refusal."""
    monkeypatch.setattr(registration, "mark_reference_from_loop",
                        lambda loop, crop, tol: (None, {"reference": "none",
                                                        "classification": {},
                                                        "reason": "forced"}))
    out, view, doc, exports, diag = cap._run(
        tmp_path, view_setup=lambda v: cap._rotated_loop(v, 12.91), cfg_setup=_in_recapture)
    assert out["success"] is False
    assert out["failure_reason"] == "recapture_refused"
    assert exports == []                                   # nothing exported
    assert out["sidecar_path"] is None and out["tiff_path"] is None
    assert out.get("annotation_pass") is None
    reg = out["registration"]
    names = [f["fault"] for f in reg["faults"]]
    assert "recapture_refused" in names and "registration_marks_outside_crop_loop" in names
    assert len(reg["ticks_outside_crop_loop"]) >= 11
    assert reg["restore"]["rolled_back"] is True
    assert not list((tmp_path / "color_id_buffer").glob("*")) if (tmp_path / "color_id_buffer").exists() else True


def test_control_the_same_placement_outside_recapture_mode_exports_as_before(
        tmp_path, monkeypatch):
    monkeypatch.setattr(registration, "mark_reference_from_loop",
                        lambda loop, crop, tol: (None, {"reference": "none",
                                                        "classification": {},
                                                        "reason": "forced"}))
    out, view, doc, exports, diag = cap._run(
        tmp_path, view_setup=lambda v: cap._rotated_loop(v, 12.91))
    assert out["success"] is True and len(exports) == 2
    assert out["failure_reason"] is None if "failure_reason" in out else True
    assert "recapture_refused" not in [f["fault"] for f in out["registration"]["faults"]]
    for path in (out["sidecar_path"], out["annotation_pass"]["sidecar_path"]):
        assert cap._sidecar(path)["registration_marks"]["recapture"] is None


def test_a_recaptured_views_sidecars_carry_the_recapture_record_in_the_FILES(tmp_path):
    out, view, doc, exports, diag = cap._run(
        tmp_path, view_setup=lambda v: cap._rotated_loop(v, 12.91), cfg_setup=_in_recapture)
    assert out["success"] is True and len(exports) == 2
    assert out["registration"]["ticks_outside_crop_loop"] == []
    for path in (out["sidecar_path"], out["annotation_pass"]["sidecar_path"]):
        assert cap._sidecar(path)["registration_marks"]["recapture"] == RECORD


def test_a_views_core_merge_that_fails_leaves_run_meta_untouched(tmp_path):
    """Codex, PR #236: run_meta is written AFTER the CSV merge, so a merge that
    fails cannot leave a run_meta recording outcomes the CSV does not."""
    run_id, cfg = _original_run(tmp_path)
    core = _core(tmp_path)
    meta_before = run_meta_file(tmp_path).read_bytes()
    exp, _record = _recapture_exporter(tmp_path, run_id, cfg)
    exp.on_view_complete(dict(_summary(7), view=vc._view(7, "Sec")))
    # The run's CSV becomes unreadable as a file before finalize.
    core_text = core.read_text(encoding="utf-8")
    core.unlink()
    core.mkdir()
    with pytest.raises(OSError):
        exp.finalize()
    assert run_meta_file(tmp_path).read_bytes() == meta_before
    assert "recaptures" not in json.loads(meta_before)
    # Control: with the CSV back, the same recapture finalizes and both change.
    core.rmdir()
    core.write_text(core_text, encoding="utf-8")
    exp2, _record = _recapture_exporter(tmp_path, run_id, cfg)
    exp2.on_view_complete(dict(_summary(7), view=vc._view(7, "Sec")))
    exp2.finalize()
    assert "recaptures" in json.loads(run_meta_file(tmp_path).read_text())
    assert vc._rows(str(core))[1]["CaptureStatus"] == "success"
