"""One run, one directory.

Observed 2026-10-05: two models captured on the same day into the same output
folder shared run_meta.json (the second, interrupted run overwrote the first's,
so the roll-up refused the first: finalized false, 0 rows) and color_id_buffer/
(view stems end in an ElementId, unique only within one document). And with a
date override the run id was ``<override>T000000``, so two re-runs of one
archive snapshot were the same run id.

Every test here drives the REAL entry point (run_vop_pipeline_streaming, and
the thinrunner's batch loop executed from its own source); only the per-view
capture is faked, and the fake writes its files where the real capture does --
``cfg.output_dir/color_id_buffer/<name>_<view id>.*`` -- so a collision the
real capture would cause, the fake causes too.
"""
import csv
import ctypes
import hashlib
import json
import os
import shutil
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Imported HERE, before any test patches vop_interwoven.pipeline: entry_dynamo
# binds process_document_views at import, so a first import made while a
# test's fake capture is patched in would keep that fake for the session.
import vop_interwoven.entry_dynamo  # noqa: F401
from vop_interwoven import run_meta
from vop_interwoven.config import Config
from vop_interwoven.run_meta import (
    RunDirectoryConflict, check_run_directory, run_directory, run_identity,
)
from vop_interwoven.streaming import run_vop_pipeline_streaming

REPO = Path(__file__).resolve().parent.parent
THINRUNNER = REPO / "vop_interwoven" / "thinrunner_streaming.py"
T0 = datetime(2026, 10, 5, 10, 36, 0)


# --- fixtures --------------------------------------------------------------------

@pytest.fixture
def clock(monkeypatch):
    """The execution clock, one second further on at every read."""
    state = {"t": T0}

    def _tick():
        now = state["t"]
        state["t"] = now + timedelta(seconds=1)
        return now
    monkeypatch.setattr(run_meta, "_clock", _tick)
    return state


def _view(view_id, name):
    return types.SimpleNamespace(
        Id=types.SimpleNamespace(IntegerValue=view_id, Value=view_id),
        UniqueId="uid-%d" % view_id, Name=name, Scale=96, IsTemplate=False,
        ViewType=types.SimpleNamespace(ToString=lambda: "FloorPlan"))


class _Doc:
    def __init__(self, title):
        self.Title = title
        self.PathName = "C:\\models\\" + title
        self.ProjectInformation = types.SimpleNamespace(UniqueId="pi-" + title)
        self.Application = types.SimpleNamespace(
            VersionNumber="2024", VersionBuild="24.1", VersionName="Revit 2024")

    def GetElement(self, _eid):
        raise AssertionError("the run must not resolve elements here")


def _fake_capture(monkeypatch, writer=None, calls=None):
    """process_document_views for one view: writes the view's capture where
    the real one does and returns a successful Stage A result. ``writer``
    replaces the file contents (the registration fixture, for the tools)."""
    def _process(doc, ids, cfg, **_k):
        vid = int(ids[0])
        if calls is not None:
            calls.append(vid)
        cap = os.path.join(cfg.output_dir, "color_id_buffer")
        os.makedirs(cap, exist_ok=True)
        stem = os.path.join(cap, "V_{0}".format(vid))
        if writer is not None:
            writer(stem, vid)
        else:
            with open(stem + ".tiff", "w") as handle:
                handle.write("pixels of {0} view {1}".format(doc.Title, vid))
            with open(stem + ".json", "w") as handle:
                json.dump({"doc": doc.Title, "view_id": vid,
                           "tiff_path": stem + ".tiff"}, handle)
        # The pipeline's own dated per-run file, as it names it.
        diag = os.path.join(cfg.output_dir, "views_diagnostics_{0}.json".format(
            run_meta.output_date_str(getattr(cfg, "date_override", None))))
        with open(diag, "w") as handle:
            json.dump({"doc": doc.Title, "last_view": vid}, handle)
        return [{"view_id": vid, "view_name": "V {0}".format(vid), "success": True,
                 "stage": "color_id_buffer_stage_a", "elapsed_sec": 1.0,
                 "view": _view(vid, "V {0}".format(vid)),
                 "tiff_path": stem + ".tiff", "sidecar_path": stem + ".json"}]
    monkeypatch.setattr("vop_interwoven.pipeline.process_document_views", _process)


def _run(root, title, view_ids=(4242, 7), date_override=None, run_id=None, cfg=None):
    cfg = cfg or Config(enable_color_id_buffer_stage_a=True)
    return run_vop_pipeline_streaming(
        _Doc(title), list(view_ids), cfg, output_dir=str(root), export_png=False,
        export_csv=True, date_override=date_override, run_id=run_id)


def _hashes(folder):
    out = {}
    for path in sorted(Path(folder).rglob("*")):
        if path.is_file():
            out[str(path.relative_to(folder))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _tree(folder):
    return sorted(str(p.relative_to(folder)) for p in Path(folder).rglob("*"))


def _meta(run_dir):
    return json.loads((Path(run_dir) / "run_meta.json").read_text())


# --- run_identity ------------------------------------------------------------------

def test_a_date_override_is_the_as_of_date_and_never_the_run_id():
    now = datetime(2026, 10, 5, 10, 36, 1)
    ident = run_identity("2025-02-03", now=now)
    assert ident == {"run_id": "20261005T103601", "as_of_date": "2025-02-03",
                     "run_tag": None}
    # A datetime override is a date too: no tag, and none of its colons in
    # the id (the id now names a directory).
    ident = run_identity(datetime(2025, 2, 3, 8, 0), now=now)
    assert ident == {"run_id": "20261005T103601", "as_of_date": "2025-02-03",
                     "run_tag": None}


def test_a_tag_is_appended_to_the_clock_and_the_date_is_today():
    now = datetime(2026, 10, 5, 10, 36, 1)
    assert run_identity("PR_999", now=now) == {
        "run_id": "20261005T103601_PR_999", "as_of_date": "2026-10-05",
        "run_tag": "PR_999"}
    assert run_identity(None, now=now) == {
        "run_id": "20261005T103601", "as_of_date": "2026-10-05", "run_tag": None}


def test_a_passed_run_id_is_not_reminted_and_keeps_its_own_date():
    """A later batch passes the run's id: it is returned as given, and a run
    whose batches cross midnight keeps the date it was minted on -- so every
    batch resolves the SAME run directory."""
    after_midnight = datetime(2026, 10, 6, 0, 0, 5)
    ident = run_identity("PR_999", now=after_midnight, run_id="20261005T235959_PR_999")
    assert ident == {"run_id": "20261005T235959_PR_999", "as_of_date": "2026-10-05",
                     "run_tag": "PR_999"}
    assert run_identity("2025-02-03", now=after_midnight,
                        run_id="20261005T235959")["as_of_date"] == "2025-02-03"


def test_identity_and_file_names_agree_on_which_overrides_are_dates():
    """ONE parser: a date for one is a date for the other (CLAUDE.md class 1)."""
    now = datetime(2026, 10, 5, 10, 36, 1)
    for override in ("2025-02-03", "20250203", "2025-02-03T12:34:56",
                     datetime(2025, 2, 3), "PR_999", None, "  "):
        ident = run_identity(override, now=now)
        named = run_meta.output_date_str(override, now=now)
        if ident["run_tag"] is None:
            assert named == ident["as_of_date"], override
        else:
            assert named == ident["run_tag"], override


# --- A-G1 two models, same day, same root -------------------------------------------

def test_two_models_same_day_same_root_never_touch_each_others_files(
        tmp_path, monkeypatch, clock):
    _fake_capture(monkeypatch)
    first = _run(tmp_path, "Tower.rvt")
    before = _hashes(first["run_dir"])
    second = _run(tmp_path, "Annex.rvt")    # same view ids, same day
    after = _hashes(first["run_dir"])

    assert first["run_dir"] != second["run_dir"]
    assert sorted(os.listdir(str(tmp_path))) == sorted(
        os.path.basename(r["run_dir"]) for r in (first, second))
    changed = [k for k in before if after.get(k) != before[k]]
    print("A-G1: {0} files compared in the first run dir, {1} changed".format(
        len(before), len(changed)))
    assert len(before) >= 6 and set(after) == set(before) and changed == []
    assert _meta(first["run_dir"])["finalized"] is True
    assert _meta(first["run_dir"])["doc_title"]["value"] == "Tower.rvt"
    assert _meta(second["run_dir"])["doc_title"]["value"] == "Annex.rvt"
    # The capture landed in its own run's directory, not the root.
    assert json.loads((Path(first["run_dir"]) / "color_id_buffer" / "V_4242.json")
                      .read_text())["doc"] == "Tower.rvt"


# --- A-G2 one archive snapshot, run twice ------------------------------------------

def test_an_archive_snapshot_run_twice_is_two_runs(tmp_path, monkeypatch, clock):
    _fake_capture(monkeypatch)
    a = _run(tmp_path, "Archive.rvt", date_override="2025-02-03")
    b = _run(tmp_path, "Archive.rvt", date_override="2025-02-03")
    ma, mb = _meta(a["run_dir"]), _meta(b["run_dir"])
    print("A-G2: run dirs {0} / {1}".format(os.path.basename(a["run_dir"]),
                                            os.path.basename(b["run_dir"])))
    assert a["run_dir"] != b["run_dir"]
    assert ma["date"] == mb["date"] == "2025-02-03"
    assert ma["run_id"] != mb["run_id"]
    assert not ma["run_id"].startswith("20250203T000000")
    assert not mb["run_id"].startswith("20250203T000000")
    assert os.path.basename(a["run_dir"]) == "2025-02-03__" + ma["run_id"]
    assert ma["run_dir"] == a["run_dir"]


# --- A-G5 overrides --------------------------------------------------------------------

@pytest.mark.parametrize("override, date, tag", [
    (None, "2026-10-05", None),
    ("2025-02-03", "2025-02-03", None),
    ("PR_999", "2026-10-05", "PR_999"),
])
def test_overrides_set_the_date_and_tag_and_the_clock_sets_the_id(
        tmp_path, monkeypatch, clock, override, date, tag):
    _fake_capture(monkeypatch)
    out = _run(tmp_path, "Tower.rvt", date_override=override)
    meta = _meta(out["run_dir"])
    assert meta["date"] == date and meta["run_tag"] == tag
    assert meta["run_id"].startswith("20261005T1036")
    if tag:
        assert meta["run_id"].endswith("_PR_999")
    # views_core rows key into this run_meta.
    core = list(Path(out["run_dir"]).glob("views_core_*.csv"))
    assert len(core) == 1
    with open(str(core[0]), newline="", encoding="utf-8") as handle:
        assert {r["RunId"] for r in csv.DictReader(handle)} == {meta["run_id"]}


# --- A-G4 the guard ------------------------------------------------------------------

def _occupy(run_dir, run_id):
    os.makedirs(run_dir)
    with open(os.path.join(run_dir, "run_meta.json"), "w") as handle:
        json.dump({"run_id": run_id}, handle)


def test_a_run_dir_holding_another_run_is_refused_before_any_view(
        tmp_path, monkeypatch, clock):
    calls = []
    _fake_capture(monkeypatch, calls=calls)
    # The id this run will mint (the clock fixture is deterministic).
    run_dir = run_directory(str(tmp_path), run_identity(None, now=clock["t"]))
    _occupy(run_dir, "20991231T000000")
    tree = _tree(tmp_path)
    with pytest.raises(RunDirectoryConflict) as err:
        _run(tmp_path, "Tower.rvt")
    assert "20991231T000000" in str(err.value) and "20261005T103600" in str(err.value)
    assert calls == [] and _tree(tmp_path) == tree


def test_a_run_dir_with_no_run_meta_is_refused(tmp_path, monkeypatch, clock):
    calls = []
    _fake_capture(monkeypatch, calls=calls)
    os.makedirs(run_directory(str(tmp_path), run_identity(None, now=clock["t"])))
    tree = _tree(tmp_path)
    with pytest.raises(RunDirectoryConflict):
        _run(tmp_path, "Tower.rvt")
    assert calls == [] and _tree(tmp_path) == tree


def test_the_same_run_id_passed_in_continues(tmp_path, monkeypatch, clock):
    calls = []
    _fake_capture(monkeypatch, calls=calls)
    run_id = "20261005T090000"
    run_dir = run_directory(str(tmp_path), run_identity(None, run_id=run_id))
    _occupy(run_dir, run_id)
    out = _run(tmp_path, "Tower.rvt", run_id=run_id)
    assert out["run_dir"] == run_dir and calls == [4242, 7]


def test_a_minted_id_that_finds_its_own_dir_is_a_second_run_not_a_batch(tmp_path):
    """Two runs started in the same second with the same override mint the
    same id. Only a caller that PASSED the id in is continuing a run."""
    run_dir = str(tmp_path / "2026-10-05__20261005T103600")
    _occupy(run_dir, "20261005T103600")
    with pytest.raises(RunDirectoryConflict):
        check_run_directory(run_dir, "20261005T103600")
    check_run_directory(run_dir, "20261005T103600", continuing=True)
    check_run_directory(str(tmp_path / "absent"), "20261005T103600")


# --- A-G3 thinrunner batches ---------------------------------------------------------

def _thinrunner(monkeypatch, root, views, batch_size, tag=None):
    """Execute the thinrunner script from its own source, as Dynamo does,
    with its development module reloader switched off (it would discard the
    faked capture by re-importing the package)."""
    import vop_interwoven.entry_dynamo as entry
    doc = _Doc("Tower.rvt")
    monkeypatch.setattr(entry, "get_current_document", lambda: doc)
    monkeypatch.setattr(entry, "get_current_view", lambda: None)
    kernel = types.SimpleNamespace(SetThreadExecutionState=lambda flags: 0)
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(kernel32=kernel),
                        raising=False)
    src = THINRUNNER.read_text(encoding="utf-8")
    assert src.count("RELOAD_MODULES = True") == 1
    src = src.replace("RELOAD_MODULES = True", "RELOAD_MODULES = False")
    ns = {"IN": [views, tag, str(root), batch_size, False, True, True]}
    exec(compile(src, str(THINRUNNER), "exec"), ns)
    return ns["OUT"]


def test_a_batched_thinrunner_run_is_one_run_dir_with_one_run_id(
        tmp_path, monkeypatch, clock):
    from tools import verify_invariant_core as vic
    _fake_capture(monkeypatch)
    views = [_view(n, "V {0}".format(n)) for n in (11, 12, 13)]
    out = _thinrunner(monkeypatch, tmp_path, views, batch_size=1)
    assert "STATUS: SUCCESS" in out, out

    run_dirs = os.listdir(str(tmp_path))
    assert len(run_dirs) == 1
    run_dir = tmp_path / run_dirs[0]
    meta = _meta(run_dir)
    assert meta["finalized"] is True and meta["batches"] == 3
    assert run_dirs[0] == "{0}__{1}".format(meta["date"], meta["run_id"])
    assert meta["run_dir"] == str(run_dir)
    assert sorted(meta["views_requested"]) == [11, 12, 13]
    # One run_meta.json at the run directory's top; the batch records stay
    # in their own _batch_tmp_<n>/ (unchanged batch mechanics).
    assert sorted(p.name for p in run_dir.glob("_batch_tmp_*")) == [
        "_batch_tmp_1", "_batch_tmp_2", "_batch_tmp_3"]
    # Every capture moved into the run's one color_id_buffer/.
    assert sorted(p.name for p in (run_dir / "color_id_buffer").iterdir()) == [
        "V_11.json", "V_11.tiff", "V_12.json", "V_12.tiff", "V_13.json", "V_13.tiff"]

    # views_core: one file, every row the one RunId -- by the tool's own I1.
    core_files = list(run_dir.glob("views_core_*.csv"))
    assert len(core_files) == 1
    core = vic.load_bundle({"views_core": str(core_files[0])})
    keyed = {"views_core": vic.key_rows(core["views_core"])}
    assert len(keyed["views_core"]) == 3
    i1, findings = vic.invariant_1_single_run_id(keyed)
    print("A-G3: 1 run dir, {0} views_core rows, I1 {1}".format(
        len(keyed["views_core"]), i1["status"]))
    assert i1["status"] == vic.STATUS_HOLDS and findings == []
    assert i1["run_id"] == meta["run_id"]


def test_a_thinrunner_run_into_another_runs_dir_captures_nothing(
        tmp_path, monkeypatch, clock):
    calls = []
    _fake_capture(monkeypatch, calls=calls)
    _occupy(run_directory(str(tmp_path), run_identity(None, now=clock["t"])),
            "20991231T000000")
    tree = _tree(tmp_path)
    out = _thinrunner(monkeypatch, tmp_path, [_view(11, "V 11"), _view(12, "V 12")],
                      batch_size=1)
    assert "RunDirectoryConflict" in out
    assert calls == [] and _tree(tmp_path) == tree


# --- A-G6 the downstream tools, unchanged ---------------------------------------------

def test_register_grid_rollup_run_unchanged_on_a_new_run_dir(tmp_path, monkeypatch, clock):
    from tests.test_stage_a_grid import _write_pair
    from tools import register_stage_a_annotation as reg
    from tools import stage_a_grid as grid
    from tools import stage_a_grid_rollup as rollup

    stage = tmp_path / "fixture"
    stage.mkdir()
    anno_src, model_src, _c = _write_pair(stage)

    def _writer(stem, vid):
        shutil.copy(str(stage / "V_1.tiff"), stem + ".tiff")
        shutil.copy(str(stage / "V_1_anno.tiff"), stem + "_anno.tiff")
        model = json.loads(model_src.read_text())
        model.update(view_id=vid, tiff_path=stem + ".tiff")
        anno = json.loads(anno_src.read_text())
        anno.update(view_id=vid, tiff_path=stem + "_anno.tiff",
                    model_pass_tiff_path=stem + ".tiff")
        Path(stem + ".json").write_text(json.dumps(model))
        Path(stem + "_anno.json").write_text(json.dumps(anno))

    _fake_capture(monkeypatch, writer=_writer)
    root = tmp_path / "root"
    out = _run(root, "Tower.rvt", view_ids=(5, 6))
    run_dir = out["run_dir"]

    assert reg.main([run_dir]) == 0
    assert grid.main([run_dir]) == 0
    assert rollup.main([run_dir]) == 0
    summary = json.loads((Path(run_dir) / "analysis_grid" / rollup.SUMMARY_NAME).read_text())
    print("A-G6: difference {0}, refused runs {1}, rows {2}".format(
        summary["count_invariant"]["difference"], summary["refused_runs"]["count"],
        summary["denominator"]))
    assert summary["count_invariant"]["difference"] == 0
    assert summary["refused_runs"]["count"] == 0
    assert summary["denominator"] == 2


# --- PR #230 review: one identity per run, read once --------------------------------

def _views_payload(n=3):
    return {"views": [{"view_id": i, "view_name": "V {0}".format(i), "success": True,
                       "timings": {"total_ms": 1.0}} for i in range(1, n + 1)]}


def test_perf_rows_carry_the_runs_one_run_id(tmp_path, monkeypatch, clock):
    """Codex, PR #230: run_vop_pipeline_with_csv minted a fresh id for every
    perf row once a date override stopped fixing the id. Driven through the
    entry point, with a clock that moves one second per read."""
    import vop_interwoven.entry_dynamo as entry
    monkeypatch.setattr(entry, "run_vop_pipeline", lambda doc, ids, cfg: _views_payload())
    out = entry.run_vop_pipeline_with_csv(
        None, [1, 2, 3], cfg=Config(), output_dir=str(tmp_path), export_png=False,
        export_perf_csv=True, date_override="2025-02-03")
    with open(out["perf_csv_path"], newline="", encoding="utf-8") as handle:
        perf = list(csv.DictReader(handle))
    assert len(perf) == 3
    assert {r["RunId"] for r in perf} == {"20261005T103600"}
    assert {r["Date"] for r in perf} == {"2025-02-03"}
    assert os.path.basename(out["perf_csv_path"]) == "views_perf_2025-02-03.csv"


def test_a_run_straddling_midnight_names_its_files_by_its_own_date(
        tmp_path, monkeypatch):
    """Codex, PR #230: the id and the filename read the clock separately, so a
    run minted at 23:59:59 could name its files for the next day."""
    ticks = iter([datetime(2026, 10, 5, 23, 59, 59) + timedelta(seconds=s)
                  for s in range(100)])
    monkeypatch.setattr(run_meta, "_clock", lambda: next(ticks))
    from vop_interwoven.streaming import StreamingExporter
    exp = StreamingExporter(str(tmp_path), Config(enable_color_id_buffer_stage_a=True),
                            _Doc("Tower.rvt"), export_png=False, export_csv=True,
                            view_ids=[1])
    assert exp.run_id == "20261005T235959" and exp.run_meta["date"] == "2026-10-05"
    assert os.path.basename(exp.core_csv_path) == "views_core_2026-10-05.csv"
    exp.finalize()


def test_the_pipelines_files_join_the_exporters_run_after_midnight(tmp_path, monkeypatch):
    """The pipeline runs once per view and merges each into the run's
    views_diagnostics / view-element map files. A view processed after
    midnight writes into the run's files, named by the exporter's run id --
    not into a new day's. Driven through the real process_document_views."""
    from vop_interwoven.pipeline import process_document_views
    ticks = iter([datetime(2026, 10, 6, 0, 0, 1) + timedelta(seconds=s)
                  for s in range(1000)])
    monkeypatch.setattr(run_meta, "_clock", lambda: next(ticks))
    cfg = Config(enable_color_id_buffer_stage_a=True)
    cfg.output_dir = str(tmp_path)
    cfg._view_element_map_run_id = "20261005T235959"
    process_document_views(types.SimpleNamespace(Title="T", PathName="p"), [], cfg)
    assert sorted(os.listdir(str(tmp_path))) == [
        "views_diagnostics_2026-10-05.json", "vop_view_element_map_2026-10-05.json"]


def test_diagnostics_and_csv_rows_carry_one_run_id(tmp_path, monkeypatch, clock):
    """Codex, PR #230 (second review): run_vop_pipeline_with_csv let the
    pipeline mint the views_diagnostics id before processing and the CSV
    export mint its own after, seconds apart. Driven through the entry point
    and the REAL pipeline, with a clock that moves one second per read."""
    import vop_interwoven.entry_dynamo as entry
    seen = {}
    from vop_interwoven import csv_export
    real_export = csv_export.export_pipeline_to_csv

    def _export(*a, **k):
        out = real_export(*a, **k)
        seen.update(out)
        return out
    monkeypatch.setattr(csv_export, "export_pipeline_to_csv", _export)
    entry.run_vop_pipeline_with_csv(
        types.SimpleNamespace(Title="T", PathName="p"), [],
        cfg=Config(enable_color_id_buffer_stage_a=True),
        output_dir=str(tmp_path), export_png=False, export_perf_csv=False,
        date_override="2025-02-03")
    # Control: the pipeline really ran (a raise inside it is swallowed by
    # run_vop_pipeline, and would leave this test asserting on nothing).
    diag = json.loads((tmp_path / "views_diagnostics_2025-02-03.json").read_text())
    assert seen["run_id"] == "20261005T103600"
    assert diag["metadata"]["run_id"] == diag["metadata"]["exporter_run_id"] == seen["run_id"]
    assert diag["metadata"]["date"] == "2025-02-03"
