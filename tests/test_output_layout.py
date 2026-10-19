"""The run file layout and the view-name cap (vop_interwoven/output_layout.py).

The 2025-07-02 rerun (20261016T143141) lost 24 views in a run directory 159
characters deep, all with long names; every view at 255 characters or fewer
captured. The cap is checked against the real capture: a long-named view in a
deep run directory is captured through ``export_color_id_buffer_view`` and every
path a writer could make from its stem stays under the limit, with a control
that a short name is left whole. The file date is composed with the writer
that names the run's views_core file (CLAUDE.md defect class 1).
"""
import os
import re
from pathlib import Path

import pytest

from vop_interwoven import output_layout as ol
from vop_interwoven.config import Config

REPO = Path(__file__).resolve().parent.parent
LONG = "SITE PLAN - LEVEL 3 - NEW SITEWORK 22X34 - EXISTING HOSPITAL TO REMAIN-ELEV study_er"


def _deep_run(tmp_path, depth=159):
    """A run directory whose absolute path is ``depth`` characters."""
    base = str(tmp_path)
    pad = depth - len(base) - len(os.sep) - len("2025-07-02__20261016T1431") - len(os.sep)
    assert pad > 0
    return os.path.join(base, "x" * pad, "2025-07-02__20261016T1431")


# --- the cap --------------------------------------------------------------------

def test_a_long_name_is_cut_to_fit_and_every_suffix_stays_under_the_limit(tmp_path):
    run = _deep_run(tmp_path)
    assert len(os.path.abspath(run)) == 159
    cap = os.path.join(run, ol.CAPTURE_DIR[ol.V2])
    stem = ol.view_file_stem(LONG, 3969026, cap, ol.V2)
    assert stem.endswith("_3969026") and len(stem) < len(LONG) + len("_3969026")
    assert ol.safe_view_name(LONG).startswith(stem[:-len("_3969026")])
    for suffix in ol.STEM_SUFFIXES[ol.V2]:
        assert len(os.path.join(os.path.abspath(cap), stem + suffix)) <= ol.PATH_LIMIT
    # ... and in a batch's temporary folder, which is deeper.
    batch_cap = os.path.join(run, "_batch_tmp_999", ol.CAPTURE_DIR[ol.V2])
    for suffix in ol.CAPTURE_SUFFIXES:
        assert len(os.path.join(os.path.abspath(batch_cap), stem + suffix)) <= ol.PATH_LIMIT


def test_control_a_name_that_fits_is_unchanged(tmp_path):
    cap = os.path.join(str(tmp_path), "2025-07-02__20261016T1431", "color_id")
    assert ol.view_file_stem("(N) LEVEL 3", 929475, cap) == "_N_ LEVEL 3_929475"
    assert ol.view_file_stem(LONG, 3969026, cap) == ol.safe_view_name(LONG) + "_3969026"


def test_a_view_already_in_the_run_keeps_its_file_name(tmp_path):
    """A recapture replaces the view's files: its stem must not move, even
    when the view was renamed or the cap would now cut differently."""
    cap = Path(tmp_path) / "color_id"
    cap.mkdir()
    (cap / "OLD NAME_42.json").write_text("{}")
    (cap / "OLD NAME_42_anno.json").write_text("{}")
    assert ol.view_file_stem("NEW NAME", 42, str(cap)) == "OLD NAME_42"
    (cap / "OTHER_42.json").write_text("{}")             # two: not guessed
    assert ol.view_file_stem("NEW NAME", 42, str(cap)) == "NEW NAME_42"


def test_a_folder_too_deep_for_even_the_id_is_refused(tmp_path):
    run = _deep_run(tmp_path, depth=230)
    with pytest.raises(ol.PathBudgetExceeded, match="no room"):
        ol.view_file_stem("A", 3969026, os.path.join(run, "color_id"), ol.V2)
    with pytest.raises(ol.PathBudgetExceeded, match="shorter output folder"):
        ol.check_run_budget(run)
    assert ol.check_run_budget(_deep_run(tmp_path, depth=159)) > 0      # control


def test_the_capture_writes_the_capped_name_in_a_deep_run(tmp_path):
    """Through production's export_color_id_buffer_view, with the run's
    layout set as the run sets it."""
    from tests.test_color_id_buffer_shadow_suppression import (
        _FakeDiag, _FakeDoc, _FakeView, _install_fake_revit_db)
    from vop_interwoven import color_id_buffer as cib
    run = _deep_run(tmp_path)
    cfg = Config()
    cfg.include_linked_rvt = False
    cfg.output_dir = run
    cfg._stage_a_layout = ol.V2
    cfg._stage_a_run_dir = run
    view = _FakeView(view_id=3969026)
    view.Name = LONG
    with _install_fake_revit_db():
        result = cib.export_color_id_buffer_view(
            _FakeDoc(), view, elements=[], cfg=cfg, diag=_FakeDiag(), raster=None,
            elem_cache=None)
    tiff = Path(result["tiff_path"])
    assert tiff.parent.name == "color_id" and tiff.exists()
    assert tiff.stem == ol.view_file_stem(LONG, 3969026, str(tiff.parent))
    assert len(tiff.stem) < len(ol.safe_view_name(LONG)) + len("_3969026")


# --- every suffix a tool appends is in the registry --------------------------------

def _appended_suffixes():
    """Each literal a tool or the capture appends to a stem: ``stem + "..."``
    and ``p.stem + "..."`` in tools/ and vop_interwoven/."""
    found = set()
    for folder in ("tools", "vop_interwoven"):
        for path in (REPO / folder).rglob("*.py"):
            if "notes" in path.parts:
                continue
            for m in re.finditer(r'stem \+ "([_.][A-Za-z0-9_.]+)"', path.read_text()):
                found.add(m.group(1))
    return found


def test_every_suffix_the_tools_append_fits_the_reserve():
    found = _appended_suffixes()
    assert {".grid.json", ".kinds.npz", "_anno.json"} <= found      # the scan sees them
    longest = max(len(s) for s in ol.STEM_SUFFIXES[ol.V2])
    for suffix in found:
        worst = suffix if suffix.startswith("_anno") else "_anno" + suffix
        worst = worst.replace(".registered", ol.REGISTERED_INFIX[ol.V2])
        assert len(worst) <= longest, (suffix, longest)


# --- layouts and file dates ---------------------------------------------------------

def test_layout_of_a_run(tmp_path):
    assert ol.layout_of_run(tmp_path / "absent") is None
    v1 = tmp_path / "v1"
    (v1 / "color_id_buffer").mkdir(parents=True)
    assert ol.layout_of_run(v1) == ol.V1
    v2 = tmp_path / "v2"
    v2.mkdir()
    (v2 / "run_meta_2025-07-02.json").write_text("{}")
    assert ol.layout_of_run(v2) == ol.V2
    assert ol.layout_of_capture_dir(v2 / "color_id") == ol.V2
    assert ol.layout_of_capture_dir(v1 / "color_id_buffer") == ol.V1
    assert ol.layout_of_capture_dir(tmp_path / "anything") == ol.V1


def test_two_run_meta_files_are_never_guessed_between(tmp_path):
    (tmp_path / "run_meta.json").write_text("{}")
    assert ol.find_run_meta(tmp_path)[0] == str(tmp_path / "run_meta.json")
    (tmp_path / "run_meta_2025-07-02.json").write_text("{}")
    path, why = ol.find_run_meta(tmp_path)
    assert path is None and "2 run_meta files" in why
    (tmp_path / ".run_meta_x.json.tmp").write_text("{}")             # never a record
    assert len(ol.run_meta_candidates(tmp_path)) == 2


def test_registered_paths_follow_the_capture_folder(tmp_path):
    assert ol.registered_paths(tmp_path / "color_id" / "V_1_anno.json")[1].endswith(
        os.path.join("color_id", "V_1_anno.reg.json"))
    assert ol.registered_paths(tmp_path / "color_id_buffer" / "V_1_anno.json")[0].endswith(
        "V_1_anno.registered.tiff")


@pytest.mark.parametrize("override", ["2025-07-02", "PR_221", None])
def test_the_file_date_is_the_one_views_core_is_named_by(tmp_path, override):
    """Composed: the run's own views_core file, named by the streaming
    exporter, and file_date() of the run_meta it wrote."""
    import json
    from vop_interwoven.streaming import StreamingExporter
    from tests.test_stage_a_views_core import _Doc
    exp = StreamingExporter(str(tmp_path), Config(enable_color_id_buffer_stage_a=True),
                            doc=_Doc(), export_png=False, export_csv=True,
                            date_override=override, view_ids=[4242])
    exp.finalize()
    meta_path, why = ol.find_run_meta(tmp_path)
    assert meta_path is not None, why
    meta = json.loads(Path(meta_path).read_text())
    d = ol.file_date(meta)
    assert os.path.basename(exp.core_csv_path) == "views_core_{0}.csv".format(d)
    assert os.path.basename(meta_path) == "run_meta_{0}.json".format(d)
    assert meta["output_layout"] == ol.V2
