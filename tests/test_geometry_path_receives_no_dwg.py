"""G1: the geometry path receives no DWG; Stage A's import enumeration does.

Greg (2026-09-30): DWG line work moves to the colour analysis layer. The
geometry path's one entry for imports is render_model_front_to_back's call to
expand_host_link_import_model_elements -- the same expansion Stage A's model
pass enumerates imports through, so the expansion is left whole and the
geometry path filters after it.

The render test drives the REAL render_model_front_to_back up to its
front-to-back sort and records what reaches it; nothing downstream of the sort
runs. The control drives the real expansion and shows the DWG entry is still
there for Stage A.
"""
import pytest

import vop_interwoven.pipeline as pipeline
import vop_interwoven.revit.collection as revit_collection
import vop_interwoven.revit.linked_documents as linked_documents
import vop_interwoven.revit.view_basis as view_basis
from vop_interwoven.config import Config

from tests.stage_a_capture_fakes import FakeDiag, FakeElement, install_fake_revit_db


class _Stop(Exception):
    pass


class _Proxy(object):
    def __init__(self, elem_id, source_type):
        from tests.stage_a_capture_fakes import FakeElementId
        self.Id = FakeElementId(elem_id)
        self.source_type = source_type
        self.source_id = source_type
        self.LinkInstanceId = None


def _entries():
    return [{"element": FakeElement(1001), "source_type": "HOST"},
            {"element": _Proxy(3001, "LINK"), "source_type": "LINK"},
            {"element": _Proxy(2002, "DWG"), "source_type": "DWG"}]


def test_g1_render_model_front_to_back_receives_no_dwg(monkeypatch):
    seen = []

    def _sort(expanded, view, raster):
        seen.extend(expanded)
        raise _Stop()

    monkeypatch.setattr(pipeline, "make_view_basis", lambda view, diag=None: object())
    monkeypatch.setattr(view_basis, "resolve_view_w_volume",
                        lambda view, vb, cfg, diag=None: (0.0, 1.0, {}))
    monkeypatch.setattr(revit_collection, "expand_host_link_import_model_elements",
                        lambda doc, view, elements, cfg, diag=None, elem_cache=None: _entries())
    monkeypatch.setattr(pipeline, "sort_front_to_back", _sort)
    diag = FakeDiag()
    with pytest.raises(_Stop):
        pipeline.render_model_front_to_back(None, FakeElement(42), object(), [], Config(),
                                            diag=diag)
    assert [w["source_type"] for w in seen] == ["HOST", "LINK"]
    [info] = [e for e in getattr(diag, "infos", [])
              if e.get("callsite") == "render_model_front_to_back.dwg_excluded"]
    assert info["extra"] == {"dwg_entries_excluded": 1}


def test_g1_control_stage_As_expansion_still_enumerates_the_dwg(monkeypatch):
    """Stage A's model pass reads imports through this same expansion; it
    must still return the DWG entry (and the collector must still be called)."""
    calls = []

    def _collect(doc, view, cfg, diag=None, dwg_omitted_out=None, **kw):
        calls.append(cfg)
        return [_Proxy(2002, "DWG")]
    monkeypatch.setattr(linked_documents, "collect_all_linked_elements", _collect)
    with install_fake_revit_db():
        out = revit_collection.expand_host_link_import_model_elements(
            None, FakeElement(42), [], Config(), diag=FakeDiag())
    assert calls and [w["source_type"] for w in out] == ["DWG"]


def test_g1_the_filter_is_by_source_type_only():
    kept, dropped = pipeline.exclude_dwg_from_geometry_path(_entries())
    assert dropped == 1 and [w["source_type"] for w in kept] == ["HOST", "LINK"]
    assert pipeline.exclude_dwg_from_geometry_path([]) == ([], 0)
