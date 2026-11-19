"""Export-floor diagnosis: the export timer split into its parts.

docs/notes/export_floor_20261118T1002.md: ``<pass>_export_ms`` spans
ExportImage, the staging listing, the move, the dimension read and the
staging cleanup together, so a ~21 s floor in it could not be placed. Each
part is now a sub-key ``<pass>_export.<part>`` nested in its parent (never in
the partition), and every ExportImage leaves one ``export_telemetry`` entry.

Asserted on the sidecar FILES (CLAUDE.md, defect class 4). Each part is shown
to receive a known injected delay while its neighbours do not, so a part that
was timed in the wrong place turns a test red.
"""
import json
import os
import shutil
import time

import pytest

from vop_interwoven import color_id_buffer as cib
from vop_interwoven import stage_a_registered_capture as rc
from tests.test_stage_a_registered_capture import _SizedDoc, _run
from tests.test_stage_a_timing_subkeys import C0_PARTITION

DELAY_MS = 30.0
PASSES = ("model", "annotation")


@pytest.fixture(autouse=True)
def _fresh_ledger(monkeypatch):
    # The ledger is process-wide by design (it outlives a run in a Dynamo
    # session); each test starts from an empty one.
    monkeypatch.setattr(cib, "_EXPORT_IMAGE_LEDGER", {"counts": {}, "last": None, "seq": 0})


def _run_keyed(tmp_path, key="run-A"):
    def _setup(cfg):
        cfg._stage_a_run_dir = key
    return _run(tmp_path, cfg_setup=_setup)


def _marks(path):
    with open(path) as handle:
        return json.load(handle)["registration_marks"]


def _files(out):
    return (out["sidecar_path"], out["annotation_sidecar_path"])


def test_the_partition_is_unchanged():
    assert rc.TIMING_PARTITION == C0_PARTITION
    assert not any("export" in k for k in rc.TIMING_PARTITION)


def test_every_export_part_is_in_both_FILES_nested_in_its_parent(tmp_path):
    out, _v, _d, _e, _diag = _run_keyed(tmp_path)
    for path in _files(out):
        timings = _marks(path)["timings_ms"]
        for name in PASSES:
            parent = timings["{0}_export".format(name)]
            parts = [timings["{0}_export.{1}".format(name, p)] for p in cib.EXPORT_SUBKEYS]
            assert all(ms >= 0 for ms in parts)
            assert sum(parts) <= parent + 1e-2, (name, parts, parent)
        partition = sum(timings[k] for k in rc.TIMING_PARTITION)
        assert abs(timings["unaccounted"] - (timings["total"] - partition)) < 1e-2


def test_export_telemetry_names_the_first_export_of_the_view_in_both_FILES(tmp_path):
    out, _v, _d, _e, _diag = _run_keyed(tmp_path)
    for path in _files(out):
        tel = _marks(path)["export_telemetry"]
        assert [t["pass"] for t in tel] == ["model", "annotation"]
        model, anno = tel
        assert model["export_image_ordinal"] == 1 and model["first_export_image_for_view"] is True
        assert anno["export_image_ordinal"] == 2 and anno["first_export_image_for_view"] is False
        assert anno["previous_export_view_id"] == out["view_id"]
        assert model["previous_export_view_id"] is None
        assert model["gap_since_previous_export_ms"] is None
        assert anno["gap_since_previous_export_ms"] >= 0
        assert anno["process_export_seq"] == model["process_export_seq"] + 1
        for t in tel:
            assert t["capture_attempt"] == 1
            assert t["listed_to_moved_ms"] >= 0
            for part in cib.EXPORT_SUBKEYS:
                assert t[part + "_ms"] >= 0, part
        # file_bytes is the staged TIFF's size, i.e. the moved file's.
        assert model["file_bytes"] == os.path.getsize(out["tiff_path"])
        assert anno["file_bytes"] == os.path.getsize(out["annotation_tiff_path"])


def test_a_second_run_key_starts_the_count_again(tmp_path):
    _run_keyed(tmp_path / "a", key="run-A")
    out, _v, _d, _e, _diag = _run_keyed(tmp_path / "b", key="run-B")
    tel = _marks(out["sidecar_path"])["export_telemetry"]
    assert tel[0]["export_image_ordinal"] == 1
    # ...while the process-wide view of "what came just before" carries over.
    assert tel[0]["previous_export_view_id"] == out["view_id"]
    assert tel[0]["gap_since_previous_export_ms"] >= 0


def test_the_same_run_key_continues_the_count(tmp_path):
    _run_keyed(tmp_path / "a", key="run-A")
    out, _v, _d, _e, _diag = _run_keyed(tmp_path / "b", key="run-A")
    tel = _marks(out["sidecar_path"])["export_telemetry"]
    assert [t["export_image_ordinal"] for t in tel] == [3, 4]
    assert not any(t["first_export_image_for_view"] for t in tel)


def _slow(fn):
    def wrapped(*a, **k):
        time.sleep(DELAY_MS / 1000.0)
        return fn(*a, **k)
    return wrapped


def _assert_only(path, part):
    timings = _marks(path)["timings_ms"]
    for name in PASSES:
        assert timings["{0}_export.{1}".format(name, part)] >= DELAY_MS, (name, part)
        for other in cib.EXPORT_SUBKEYS:
            if other != part:
                assert timings["{0}_export.{1}".format(name, other)] < DELAY_MS, (
                    name, part, other)
        assert timings["{0}_export".format(name)] >= DELAY_MS


def test_a_slow_ExportImage_lands_in_export_image(tmp_path, monkeypatch):
    monkeypatch.setattr(_SizedDoc, "ExportImage", _slow(_SizedDoc.ExportImage))
    out, _v, _d, _e, _diag = _run_keyed(tmp_path)
    for path in _files(out):
        _assert_only(path, "export_image")


def test_a_slow_listing_lands_in_list_stage(tmp_path, monkeypatch):
    real = os.listdir

    def listdir(path):
        if os.path.basename(str(path)).startswith("vop_"):
            time.sleep(DELAY_MS / 1000.0)
        return real(path)
    monkeypatch.setattr(cib.os, "listdir", listdir)
    out, _v, _d, _e, _diag = _run_keyed(tmp_path)
    for path in _files(out):
        _assert_only(path, "list_stage")


def test_a_slow_move_lands_in_move_and_in_listed_to_moved(tmp_path, monkeypatch):
    monkeypatch.setattr(cib.shutil, "move", _slow(shutil.move))
    out, _v, _d, _e, _diag = _run_keyed(tmp_path)
    for path in _files(out):
        _assert_only(path, "move")
        for t in _marks(path)["export_telemetry"]:
            assert t["listed_to_moved_ms"] >= DELAY_MS


def test_a_slow_dimension_read_lands_in_read_dims(tmp_path, monkeypatch):
    monkeypatch.setattr(cib, "read_image_dimensions", _slow(cib.read_image_dimensions))
    out, _v, _d, _e, _diag = _run_keyed(tmp_path)
    for path in _files(out):
        _assert_only(path, "read_dims")
        for t in _marks(path)["export_telemetry"]:
            assert t["listed_to_moved_ms"] < DELAY_MS


def test_a_slow_cleanup_lands_in_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(cib.shutil, "rmtree", _slow(shutil.rmtree))
    out, _v, _d, _e, _diag = _run_keyed(tmp_path)
    for path in _files(out):
        _assert_only(path, "cleanup")


def test_a_failed_ExportImage_keeps_the_parts_it_reached(tmp_path):
    class _Boom(object):
        def ExportImage(self, opts):
            raise RuntimeError("revit said no")

    class _View(object):
        Id = type("I", (), {"IntegerValue": 7})()
        Name = "v"

    tel = {}
    with pytest.raises(Exception):
        from tests.stage_a_capture_fakes import install_fake_revit_db
        with install_fake_revit_db():
            cib._export_one_tiff(_Boom(), _View(), str(tmp_path),
                                 str(tmp_path / "x.tiff"), 100, view_id=7,
                                 timing_out=tel, run_key="k")
    assert tel["export_image_ms"] >= 0 and tel["cleanup_ms"] >= 0
    # Parts never reached are absent, not zero.
    assert "list_stage_ms" not in tel and "move_ms" not in tel
    assert tel["file_bytes"] is None
    assert cib.export_subkeys_ms([tel]) == {
        "export.export_image": tel["export_image_ms"], "export.cleanup": tel["cleanup_ms"]}


def test_export_subkeys_sum_over_attempts():
    attempts = [{"export_image_ms": 1.0, "list_stage_ms": 0.5, "move_ms": 0.25,
                 "read_dims_ms": 0.125, "cleanup_ms": 0.5},
                {"export_image_ms": 2.0, "list_stage_ms": 0.5, "move_ms": 0.25,
                 "read_dims_ms": 0.125, "cleanup_ms": 0.25, "file_bytes": 9}]
    assert cib.export_subkeys_ms(attempts) == {
        "export.export_image": 3.0, "export.list_stage": 1.0, "export.move": 0.5,
        "export.read_dims": 0.25, "export.cleanup": 0.75}
    assert cib.export_subkeys_ms([]) == {}


def test_compare_stage_a_runs_reads_export_telemetry_as_timing():
    """Two runs of one view differ in every export_telemetry field (the
    ordinal depends on what the session exported before); the run
    comparison must call that timing, not an unexpected difference."""
    from tools import compare_stage_a_runs as csr
    for leaf in ("export_image_ordinal", "process_export_seq", "file_bytes",
                 "first_export_image_for_view", "previous_export_view_id", "pass"):
        path = "/color_id/v_1.json/registration_marks/export_telemetry[]/" + leaf
        assert csr._rule_for(path) == "timing", leaf
    # Control: a neighbouring capture field is still compared.
    assert csr._rule_for("/color_id/v_1.json/registration_marks/co_sizing/state") is None
