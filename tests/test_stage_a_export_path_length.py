"""ExportImage is staged in a short temp directory, so run depth cannot lose a view.

Revit's ExportImage names its file ``<FilePath> - <view type> - <view name>.tif``
and, when that path reaches the Windows limit, writes nothing and raises
nothing. The 2025-07-02 rerun (run 20261016T143141, a batch output root 159
characters deep) lost exactly the 24 views whose export path came to 256-281
characters; every view at 255 or fewer captured, and the original run, 106
characters deep, had captured all 24 (views_diagnostics_2025-07-02.json:
"ExportImage did not produce a TIFF in ...").

The fake below is Revit's naming and its silent refusal, nothing else. The
capture runs through production's ``export_color_id_buffer_view``; the
discriminating case is a run directory deep enough that exporting beside the
output would cross the limit while the staged path does not. Mutation:
pointing FilePath back at the output directory turns it red.
"""
import os

from vop_interwoven import color_id_buffer as cib
from vop_interwoven.config import Config

from tests.test_color_id_buffer_shadow_suppression import (
    _FakeDiag, _FakeDoc, _FakeView, _install_fake_revit_db,
)
from tests.test_stage_a_export_dimension_contract import write_tiff_header

LIMIT = 256          # the 2025-07-02 rerun: 255 captured, 256 did not
LONG_NAME = "SITE PLAN - LEVEL 3 - NEW SITEWORK 22X34 - EXISTING HOSPITAL TO REMAIN-ELEV study_er"


class _RevitNamingDoc(_FakeDoc):
    """ExportImage as Revit names its output; nothing written at the limit."""

    def __init__(self, view_name):
        _FakeDoc.__init__(self)
        self.view_name = view_name
        self.attempted = []

    def ExportImage(self, opts):
        self.export_image_calls.append(opts)
        path = "{0} - Floor Plan - {1}.tif".format(opts.FilePath, self.view_name)
        self.attempted.append(path)
        if len(path) >= LIMIT:
            return                      # Revit: no file, no exception
        px = int(opts.PixelSize)
        write_tiff_header(path, px, px)


def _named_view(name):
    view = _FakeView(view_id=3969026)
    view.Name = name
    return view


def _capture(dump_dir, name):
    cfg = Config()
    cfg.include_linked_rvt = False
    cfg.debug_dump_path = str(dump_dir)
    doc = _RevitNamingDoc(name)
    with _install_fake_revit_db():
        result = cib.export_color_id_buffer_view(
            doc, _named_view(name), elements=[], cfg=cfg, diag=_FakeDiag(), raster=None,
            elem_cache=None)
    return result, doc


def _deep(tmp_path):
    """A run directory whose color_id_buffer/ makes the in-place export path
    for LONG_NAME cross the limit."""
    deep = tmp_path / ("Exports_rerun_" + "k" * 60) / ("Hosp_Interior_AR_" + "v" * 40)
    in_place = "{0} - Floor Plan - {1}.tif".format(
        os.path.join(str(deep), "color_id_buffer", "_vop_color_id_tmp"), LONG_NAME)
    assert len(in_place) >= LIMIT, len(in_place)      # the fixture discriminates
    return deep


def test_a_deep_run_directory_no_longer_loses_a_long_named_view(tmp_path):
    result, doc = _capture(_deep(tmp_path), LONG_NAME)
    assert result["success"] is True, result.get("failure_reason")
    assert os.path.isfile(result["tiff_path"])
    assert all(len(p) < LIMIT for p in doc.attempted)
    # Staged: the export request never pointed into the run directory.
    assert all(not p.startswith(str(tmp_path)) for p in doc.attempted)


def test_control_a_short_run_directory_captures_either_way(tmp_path):
    result, _doc = _capture(tmp_path / "run", "LEVEL 1")
    assert result["success"] is True
    assert os.path.isfile(result["tiff_path"])


def test_the_staging_directory_is_removed(tmp_path):
    result, doc = _capture(_deep(tmp_path), LONG_NAME)
    assert result["success"] is True
    assert not any(os.path.exists(os.path.dirname(p)) for p in doc.attempted)


def test_a_name_too_long_even_staged_names_the_path_limit(tmp_path):
    """The registered capture records this as registered_capture_raised with
    the message (views_diagnostics); here it propagates."""
    import pytest
    name = "X" * 300
    with pytest.raises(RuntimeError) as ex:
        _capture(tmp_path / "run", name)
    text = str(ex.value)
    assert "did not produce a TIFF" in text and "Windows limit" in text, text
    staged = text.split("'")[1]
    assert not os.path.exists(staged)                                  # still cleaned up
