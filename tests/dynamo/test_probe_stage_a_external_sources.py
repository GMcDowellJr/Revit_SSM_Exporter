"""Pure-Python coverage for probe_stage_a_external_sources.py helpers that
don't require Revit. The probe module itself imports cleanly without Revit
(Revit API imports are deferred inside function bodies), but most of its
logic can only run inside Revit/Dynamo - this covers the one exception:
_win_long_path(), the MAX_PATH workaround _export_tiff() relies on.
"""
import os

import tests.dynamo.probe_stage_a_external_sources as probe


def test_win_long_path_prefixes_on_windows(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(os.path, "abspath", lambda p: p)
    assert probe._win_long_path(r"C:\short\path.tiff") == r"\\?\C:\short\path.tiff"


def test_win_long_path_is_noop_on_posix(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    assert probe._win_long_path("/already/short") == "/already/short"


def test_win_long_path_does_not_double_prefix(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    already = r"\\?\C:\already\prefixed.tiff"
    monkeypatch.setattr(os.path, "abspath", lambda p: p)
    assert probe._win_long_path(already) == already


def test_win_long_path_leaves_unc_paths_alone(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    unc = r"\\server\share\file.tiff"
    monkeypatch.setattr(os.path, "abspath", lambda p: p)
    assert probe._win_long_path(unc) == unc


def test_win_long_path_normalizes_the_reported_failure_case_under_the_limit(monkeypatch):
    """The exact destination path from the reported bug (286 chars, over the
    260-char MAX_PATH) must come back under a Win32 API's practical limit
    once prefixed - the \\?\\ prefix itself raises the limit to ~32,767."""
    monkeypatch.setattr(os, "name", "nt")
    dest = (r"C:\Users\gmcdowell\Documents\Revit_SSM_Exporter\campaign\raw\\"
            r"stage-a-initial.07_external_sources.rvt_link.stage_a_external_sources."
            r"capability.fixed_1600.1.8af2c3804b3e\external_sources_probe\\"
            r"__Plan_RVTLink_19293485.dpi_150.linked_per_element_linkelementid_coloring."
            r"external_sources.tiff")
    assert len(dest) > 260
    monkeypatch.setattr(os.path, "abspath", lambda p: p)
    prefixed = probe._win_long_path(dest)
    assert prefixed == "\\\\?\\" + dest
