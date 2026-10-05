"""One run, one date in every dated filename.

views_core_<d>.csv is named by the StreamingExporter; views_diagnostics_<d>.json
and vop_view_element_map_<d>.json by the pipeline, from the same cfg. The
pipeline used to parse the override and fall back to TODAY when it was not a
date, so a thinrunner tag ("PR_221") named views_core by the tag and the other
two by the wall clock. This composes the two writers rather than checking
either alone (CLAUDE.md defect class 1).

Also: Stage A writes no PNGs, so it creates no vop_raster/ or view_raster/.
"""
import os

import pytest

from vop_interwoven.config import Config
from vop_interwoven.run_meta import output_date_str
from vop_interwoven.streaming import StreamingExporter
from tests import test_stage_a_skips_the_model_pass as loop
from tests.test_stage_a_views_core import _Doc


def _pipeline_names(monkeypatch, tmp_path, date_override):
    make_cfg = loop._make_cfg

    def _cfg(tmp_path_, stage_a):
        cfg = make_cfg(tmp_path_, stage_a)
        cfg.date_override = date_override
        cfg._view_element_map_run_id = "RUN_X"
        return cfg

    monkeypatch.setattr(loop, "_make_cfg", _cfg)
    loop._run_one_view(monkeypatch, tmp_path, stage_a=True)
    names = os.listdir(str(tmp_path))
    diag = [n for n in names if n.startswith("views_diagnostics_")]
    emap = [n for n in names if n.startswith("vop_view_element_map_")]
    return diag, emap


def _core_name(tmp_path, date_override):
    cfg = Config(enable_color_id_buffer_stage_a=True)
    exp = StreamingExporter(str(tmp_path), cfg, doc=_Doc(), export_png=False,
                            export_csv=True, date_override=date_override, view_ids=[1])
    name = os.path.basename(exp.core_csv_path)
    exp.finalize()
    return name


# "PR_221" is the case that diverged; "20261001" parsed to 2026-10-01 in the
# pipeline while views_core kept it raw; an ISO datetime must not put its
# colons into a filename. "2026-09-29" is the control: it agreed before the
# fix too, so it shows the fixture is not vacuous.
@pytest.mark.parametrize("override,expected", [
    ("PR_221", "PR_221"),
    ("20261001", "2026-10-01"),
    ("2026-10-01T12:34:56", "2026-10-01"),
    ("2026-09-29", "2026-09-29"),
])
def test_pipeline_files_carry_the_same_date_as_views_core(monkeypatch, tmp_path,
                                                          override, expected):
    core = _core_name(tmp_path / "core", override)
    diag, emap = _pipeline_names(monkeypatch, tmp_path / "pipe", override)
    suffix = core[len("views_core_"):-len(".csv")]
    assert suffix == expected
    assert diag == ["views_diagnostics_%s.json" % suffix], diag
    # The fixture's element cache may not export a map; when it does, it must agree.
    assert emap in ([], ["vop_view_element_map_%s.json" % suffix]), emap


def test_output_date_str_shapes():
    from datetime import datetime
    assert output_date_str(datetime(2026, 1, 2, 3, 4)) == "2026-01-02"
    assert output_date_str(" PR_221 ") == "PR_221"
    assert output_date_str("20261001") == "2026-10-01"
    assert output_date_str("2026-10-01T12:34:56") == "2026-10-01"
    assert output_date_str("2026-13-45") == "2026-13-45"   # not a date: a tag
    assert output_date_str(None, now=datetime(2026, 10, 5)) == "2026-10-05"
    assert output_date_str("", now=datetime(2026, 10, 5)) == "2026-10-05"


@pytest.mark.parametrize("stage_a", [True, False])
def test_png_folders_only_off_stage_a(tmp_path, stage_a):
    """stage_a=False is the control: the raster path still makes both."""
    cfg = Config(enable_color_id_buffer_stage_a=stage_a)
    StreamingExporter(str(tmp_path), cfg, doc=_Doc(), export_png=True,
                      export_view_raster=True, export_csv=False, view_ids=[1])
    names = os.listdir(str(tmp_path))
    for folder in ("vop_raster", "view_raster"):
        assert (folder in names) is (not stage_a), names
