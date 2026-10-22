"""C1: run_meta records the source that EXECUTED, read once when the session
starts, not the disk at write time.

Batch 2026-10-21: one pyRevit session, modules loaded once per button press,
yet runs 6 and 7 recorded newer commits than runs 1-5 -- a ``git pull``
between runs changed the record, not the code. These tests change a file on
disk INSIDE a session and assert the FILE write_run_meta writes (CLAUDE.md,
defect class 4), with a control on an unchanged disk.
"""
import json
import shutil
from pathlib import Path

import pytest

import vop_interwoven
from vop_interwoven import run_meta
from vop_interwoven.config import Config

PACKAGE = Path(vop_interwoven.__file__).resolve().parent


@pytest.fixture
def pkg(tmp_path, monkeypatch):
    """A copy of the package that EVERY default read sees (PACKAGE_DIR), so a
    write-time read anywhere -- not only through the pin's own directory --
    sees the edit the test makes."""
    dst = tmp_path / "pkg"
    shutil.copytree(str(PACKAGE), str(dst),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    monkeypatch.setattr(run_meta, "PACKAGE_DIR", str(dst))
    return dst


@pytest.fixture(autouse=True)
def _no_pin_leaks():
    assert run_meta.executed_provenance() is None
    yield
    run_meta.release_executed_provenance(True)


def _written(out_dir):
    meta = run_meta.build_run_meta(Config(), None, "20261022T0900", "2025-11-08",
                                   [1], "abcd1234", run_dir=str(out_dir))
    with open(run_meta.write_run_meta(meta, str(out_dir))) as handle:
        return json.load(handle)


def _sha(record):
    return record["exporter_source"]["value"]["sha256"]


def test_a_file_changed_on_disk_mid_session_is_flagged_and_the_executed_source_kept(
        pkg, tmp_path):
    """Mutation (a): read the fingerprint at write time and this turns red."""
    with run_meta.provenance_session("batch_capture") as pin:
        executed_sha = _sha(pin)
        with open(str(pkg / "pipeline.py"), "a") as handle:
            handle.write("\n# a git pull landed here\n")
        on_disk = _written(tmp_path)
    assert _sha(on_disk) == executed_sha
    assert on_disk["exporter_version"] == pin["exporter_version"]
    block = on_disk["provenance"]
    assert block["basis"] == "pinned_at_session_start"
    assert block["pinned_by"] == "batch_capture"
    assert block["source_changed_on_disk"] is True
    moved = block["source_on_disk"]
    assert _sha(moved) != executed_sha
    assert moved["exporter_version"] != on_disk["exporter_version"]


def test_control_an_unchanged_disk_says_so_and_carries_no_disk_copy(pkg, tmp_path):
    with run_meta.provenance_session("run_capture") as pin:
        on_disk = _written(tmp_path)
    assert _sha(on_disk) == _sha(pin)
    assert on_disk["provenance"]["source_changed_on_disk"] is False
    assert "source_on_disk" not in on_disk["provenance"]


def test_outside_a_session_the_disk_is_read_at_write_and_the_flag_is_unknown(tmp_path):
    on_disk = _written(tmp_path)
    assert on_disk["provenance"] == {"schema": run_meta.PROVENANCE_SCHEMA,
                                     "basis": "read_at_write",
                                     "source_changed_on_disk": None}
    assert on_disk["exporter_version"] == run_meta.exporter_version()[0]


def test_a_run_inside_a_batch_keeps_the_batch_pin_and_only_the_batch_releases_it(pkg):
    with run_meta.provenance_session("batch_capture") as outer:
        with run_meta.provenance_session("run_capture") as inner:
            assert inner is outer
        # The inner session did not own the pin, so it is still there.
        assert run_meta.executed_provenance() is outer
    assert run_meta.executed_provenance() is None


def test_run_capture_and_run_recapture_open_a_session(monkeypatch):
    """Both entry points the Dynamo thinrunner and the batch call pin on
    entry, so a Dynamo run is covered without a batch around it."""
    from vop_interwoven import capture_run, recapture
    seen = []

    def _capture(*a, **k):
        seen.append(run_meta.executed_provenance()["pinned_by"])
        return {}, "dir", "id"

    def _recapture(*a, **k):
        seen.append(run_meta.executed_provenance()["pinned_by"])
        return {}
    monkeypatch.setattr(capture_run, "_run_capture", _capture)
    monkeypatch.setattr(recapture, "_run_recapture", _recapture)
    capture_run.run_capture(None, [1], Config(), "out")
    recapture.run_recapture(None, {"runs": []}, Config())
    assert seen == ["run_capture", "run_recapture"]
    assert run_meta.executed_provenance() is None


def test_the_recapture_stamp_carries_the_executed_source_too(pkg):
    from vop_interwoven.recapture import recapture_record
    with run_meta.provenance_session("batch_capture") as pin:
        with open(str(pkg / "config.py"), "a") as handle:
            handle.write("\n# moved\n")
        record = recapture_record({"exporter_version": "old"}, [1])
    assert _sha(record) == _sha(pin)
    assert record["provenance"]["source_changed_on_disk"] is True


def test_the_batch_log_records_what_the_batch_executed(tmp_path):
    from datetime import datetime
    from vop_interwoven import batch_capture as bc
    plan = {"schema": bc.PLAN_SCHEMA, "mode": "capture", "output_root": str(tmp_path),
            "jobs": []}
    record = bc.run_batch(plan, None, None, None, None, log=lambda s: None,
                          now=datetime(2026, 10, 22, 9, 0, 0))
    on_disk = json.loads(Path(record["log_path"]).read_text())
    assert on_disk["executed_provenance"]["pinned_by"] == "batch_capture"
    assert on_disk["executed_provenance"]["pinned_at"] == "20261022T090000"
    assert "package_dir" not in on_disk["executed_provenance"]
    assert run_meta.executed_provenance() is None
