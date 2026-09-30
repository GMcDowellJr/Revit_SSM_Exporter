"""G2: the TINY/LINEAR/AREAL classifiers (#203) take no part in colour-ID capture.

Greg (2026-09-30): the three classifiers stay in the tree until the geometry
path is deleted, but must not run under Stage A. Proven two ways, each with
the control that makes it discriminate:

1. STATIC, over every call path: tools/check_stage_a_no_geometry.py's own
   call-graph walk (name-resolved, over-approximating -- callbacks and aliases
   are edges) from the Stage A roots reaches none of them. Control: the same
   walk from render_model_front_to_back DOES reach _classify_uv_rect, and all
   three names exist, so "not reached" is not "not found".
2. RECORDED, at runtime: the registered capture, driven end to end on the fake
   Revit, with recorders installed over the classifiers and over
   render_model_front_to_back (the only scope _classify_uv_rect lives in).
   Control: each recorder fires when its name is invoked.

What DOES run per Stage A view is pinned too (the G2 inventory), so the list
cannot shrink or grow unnoticed: init_view_raster and collect_view_elements
are Stage A roots, and compute_annotation_extents is reached from them.
"""
import os
import sys

import pytest

import vop_interwoven.core.geometry as geometry
import vop_interwoven.pipeline as pipeline

from tests.test_stage_a_registered_capture import _run

_TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
CLASSIFIERS = ("_classify_uv_rect", "classify_by_uv", "classify_by_uv_pca")


def _walk():
    sys.path.insert(0, _TOOLS)
    try:
        import check_stage_a_no_geometry as checker
    finally:
        sys.path.remove(_TOOLS)
    root = os.path.join(os.path.dirname(_TOOLS), "vop_interwoven")
    _n, functions, callees, _g, _u = checker._scan(list(checker._iter_py_files([root])))
    return checker, functions, callees


def _names(keys):
    return set(name for _path, name in keys)


def test_g2_no_classifier_is_reachable_from_the_stage_a_roots():
    checker, functions, callees = _walk()
    reached = _names(checker._reachable(checker.DEFAULT_ROOTS, functions, callees))
    assert not (set(CLASSIFIERS) & reached)
    assert "render_model_front_to_back" not in reached


def test_g2_control_the_walk_finds_the_classifier_from_the_geometry_path():
    checker, functions, callees = _walk()
    assert all(name in functions for name in CLASSIFIERS)
    reached = _names(checker._reachable(("render_model_front_to_back",),
                                        functions, callees))
    assert "_classify_uv_rect" in reached


def test_g2_inventory_what_the_geometry_path_still_runs_under_stage_a():
    checker, functions, callees = _walk()
    assert {"init_view_raster", "collect_view_elements"} <= set(checker.DEFAULT_ROOTS)
    reached = _names(checker._reachable(("init_view_raster",), functions, callees))
    assert {"make_view_basis", "resolve_view_bounds",
            "compute_annotation_extents", "collect_2d_annotations"} <= reached


def _recorders(monkeypatch):
    calls = []

    def _rec(name):
        def _stub(*a, **k):
            calls.append(name)
            raise AssertionError("{0} called under Stage A".format(name))
        return _stub
    # raising=True (the default): a rename fails this file rather than
    # silencing it.
    monkeypatch.setattr(geometry, "classify_by_uv", _rec("classify_by_uv"))
    monkeypatch.setattr(geometry, "classify_by_uv_pca", _rec("classify_by_uv_pca"))
    monkeypatch.setattr(pipeline, "classify_by_uv", _rec("pipeline.classify_by_uv"))
    monkeypatch.setattr(pipeline, "render_model_front_to_back",
                        _rec("render_model_front_to_back"))
    return calls


def test_g2_the_registered_capture_calls_no_classifier(tmp_path, monkeypatch):
    calls = _recorders(monkeypatch)
    out, _v, _d, _e, _diag = _run(tmp_path)
    assert out["success"] and out["annotation_pass_success"]
    assert calls == []


def test_g2_control_the_recorders_fire(monkeypatch):
    calls = _recorders(monkeypatch)
    for fn in (geometry.classify_by_uv, geometry.classify_by_uv_pca,
               pipeline.classify_by_uv, pipeline.render_model_front_to_back):
        with pytest.raises(AssertionError):
            fn()
    assert len(calls) == 4
