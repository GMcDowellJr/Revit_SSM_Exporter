"""Stage A step 3 -- the pipeline actually runs the annotation pass, and
hands it the MODEL pass's geometry.

WHY A SEPARATE FILE FROM test_stage_a_annotation_pass.py. That file proves
the annotation pass registers against the model pass when the two are called
correctly. This one proves the PIPELINE calls them that way -- which is a
different claim, and the one CLAUDE.md's "exercise the call site" corollary
is about: extracting the lattice binds the lattice, not the argument the
pipeline passes. The discriminating detail is that the object handed to the
annotation pass must be the SAME dict the model pass filled, not an equal
one and not a fresh one.

CONTROL: ``test_the_flag_off_runs_only_the_model_pass`` pins that the flag
gates anything at all. Without it, an implementation that always ran the
annotation pass would satisfy the positive tests.
"""
import contextlib
import sys
import types

import pytest

from vop_interwoven import pipeline
from vop_interwoven.config import Config

from tests.test_stage_a_skips_the_model_pass import (
    VIEW_ID,
    _fake_doc,
    _fake_revit_db,
    _fake_view,
)

GEOM = {
    "frame_snapped_uv": (0.0, 0.0, 120.0, 90.0),
    "frame_px": (1200, 900),
    "crop_snapped_uv": (20.0, 15.0, 80.0, 60.0),
    "crop_px": (600, 450),
    "crop_offset_px": (200, 150),
    "crop_is_frame": False,
    "achieved_fpp_ft": 0.1,
    "min_axis_px": 64,
    "requested_export_dpi": 150.0,
    "achieved_export_dpi": 150.0,
}


def _run_one_view(monkeypatch, tmp_path, annotation_pass):
    """Drive one Stage A view through the real per-view loop.

    Returns (model_calls, anno_calls, results).
    """
    view = _fake_view()
    doc = _fake_doc(view)

    model_calls = []
    anno_calls = []

    def _model(doc_, view_, elements_, cfg_, **kwargs):
        # Fill the caller's out-parameter exactly as production does, so the
        # object identity below is the real thing being checked.
        geometry_out = kwargs.get("geometry_out")
        model_calls.append({"geometry_out": geometry_out})
        if geometry_out is not None:
            geometry_out.update(GEOM)
        return {
            "view_id": VIEW_ID,
            "view_name": "L1 Plan",
            "success": True,
            "stage": "color_id_buffer_stage_a",
        }

    def _anno(doc_, view_, cfg_, geom_, **kwargs):
        anno_calls.append({"geom": geom_})
        return {
            "view_id": VIEW_ID,
            "view_name": "L1 Plan",
            "success": True,
            "stage": "color_id_buffer_stage_a_annotation",
        }

    # raising=True (the default): a rename in production errors here rather
    # than silently testing nothing.
    monkeypatch.setattr(
        "vop_interwoven.color_id_buffer.export_color_id_buffer_view", _model)
    monkeypatch.setattr(
        "vop_interwoven.color_id_buffer.export_annotation_color_id_buffer_view", _anno)
    monkeypatch.setattr(pipeline, "render_model_front_to_back",
                        lambda *a, **k: {"timings": {}})
    monkeypatch.setattr(
        pipeline, "init_view_raster",
        lambda doc_, view_, cfg_, diag=None: types.SimpleNamespace(
            W=8, H=8, cell_size_ft=1.0, bounds_xy=None, view_basis=None,
        ),
    )
    monkeypatch.setattr(pipeline, "collect_view_elements", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "rasterize_annotations", lambda *a, **k: {})

    cfg = Config(
        enable_color_id_buffer_stage_a=True,
        color_id_buffer_annotation_pass=annotation_pass,
        view_cache_enabled=False,
        perf_collect_timings=False,
    )
    cfg.output_dir = str(tmp_path)
    with _fake_revit_db():
        results = pipeline.process_document_views(doc, [VIEW_ID], cfg)

    return model_calls, anno_calls, results


def test_the_flag_off_runs_only_the_model_pass(monkeypatch, tmp_path):
    """CONTROL. If this fails the flag gates nothing and the tests below are
    vacuous."""
    model_calls, anno_calls, results = _run_one_view(
        monkeypatch, tmp_path, annotation_pass=False)

    assert len(model_calls) == 1
    assert anno_calls == []
    assert len(results) == 1
    assert results[0]["stage"] == "color_id_buffer_stage_a"


def test_the_flag_on_runs_both_passes(monkeypatch, tmp_path):
    model_calls, anno_calls, results = _run_one_view(
        monkeypatch, tmp_path, annotation_pass=True)

    assert len(model_calls) == 1
    assert len(anno_calls) == 1
    # ONE entry per view, with the annotation result nested. See
    # test_the_annotation_result_stays_inside_one_per_view_entry for why.
    assert len(results) == 1
    assert results[0]["stage"] == "color_id_buffer_stage_a"
    assert results[0]["annotation_pass"]["stage"] == (
        "color_id_buffer_stage_a_annotation")


def test_the_annotation_result_stays_inside_one_per_view_entry(
        monkeypatch, tmp_path):
    """REGRESSION (PR #211 review, chatgpt-codex-connector P1).

    The first shape appended the annotation result as a SECOND entry. Both
    streaming loops take results[0] only, so that entry reached no consumer
    at all: not on_view_complete, not full_results, not failure accounting,
    not the Stage A summary. An annotation export could fail while the run
    reported success.

    The invariant is therefore about LENGTH, not just content: one entry per
    view, whatever the annotation pass did.
    """
    _m, _a, results_on = _run_one_view(monkeypatch, tmp_path, annotation_pass=True)
    _m2, _a2, results_off = _run_one_view(monkeypatch, tmp_path, annotation_pass=False)

    assert len(results_on) == len(results_off) == 1
    assert "annotation_pass" not in results_off[0]
    assert results_on[0]["annotation_pass_success"] is True
    assert results_on[0]["annotation_pass_failure_reason"] is None


def test_the_annotation_pass_receives_the_model_passs_own_geometry_object(
        monkeypatch, tmp_path):
    """IDENTITY, not equality.

    An equal-but-separate dict would pass an equality check while coming from
    a second, independent computation -- which is exactly the drift decision
    A exists to rule out. So this asserts the annotation pass got the SAME
    object the model pass filled.
    """
    model_calls, anno_calls, results = _run_one_view(
        monkeypatch, tmp_path, annotation_pass=True)

    published = model_calls[0]["geometry_out"]
    received = anno_calls[0]["geom"]

    assert published is not None
    assert received is published
    assert received["frame_px"] == GEOM["frame_px"]


def test_the_model_pass_is_always_given_an_out_parameter(monkeypatch, tmp_path):
    """Even with the annotation pass off.

    Passing it conditionally would make the model pass's own behaviour depend
    on a flag about a different capture, and the two would then diverge only
    on the path nobody runs.
    """
    model_calls, _anno, _results = _run_one_view(
        monkeypatch, tmp_path, annotation_pass=False)
    assert isinstance(model_calls[0]["geometry_out"], dict)


def test_a_failed_annotation_pass_does_not_fail_the_model_capture(
        monkeypatch, tmp_path):
    """The two results are separate entries for this reason.

    A view whose annotation capture refused must still report its model
    capture as the success it was.
    """
    view = _fake_view()
    doc = _fake_doc(view)

    def _model(doc_, view_, elements_, cfg_, **kwargs):
        out = kwargs.get("geometry_out")
        if out is not None:
            out.update(GEOM)
        return {"view_id": VIEW_ID, "view_name": "L1 Plan", "success": True,
                "stage": "color_id_buffer_stage_a"}

    def _anno(doc_, view_, cfg_, geom_, **kwargs):
        return {"view_id": VIEW_ID, "view_name": "L1 Plan", "success": False,
                "failure_reason": "annotation_pass_no_export_geometry",
                "stage": "color_id_buffer_stage_a_annotation"}

    monkeypatch.setattr(
        "vop_interwoven.color_id_buffer.export_color_id_buffer_view", _model)
    monkeypatch.setattr(
        "vop_interwoven.color_id_buffer.export_annotation_color_id_buffer_view", _anno)
    monkeypatch.setattr(pipeline, "render_model_front_to_back",
                        lambda *a, **k: {"timings": {}})
    monkeypatch.setattr(
        pipeline, "init_view_raster",
        lambda doc_, view_, cfg_, diag=None: types.SimpleNamespace(
            W=8, H=8, cell_size_ft=1.0, bounds_xy=None, view_basis=None,
        ),
    )
    monkeypatch.setattr(pipeline, "collect_view_elements", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "rasterize_annotations", lambda *a, **k: {})

    cfg = Config(
        enable_color_id_buffer_stage_a=True,
        color_id_buffer_annotation_pass=True,
        view_cache_enabled=False,
        perf_collect_timings=False,
    )
    cfg.output_dir = str(tmp_path)
    with _fake_revit_db():
        results = pipeline.process_document_views(doc, [VIEW_ID], cfg)

    assert len(results) == 1
    entry = results[0]
    # The MODEL capture either worked or it did not, independently -- an
    # annotation failure does not retroactively fail it.
    assert entry["success"] is True
    # And the annotation failure is visible beside it rather than swallowed.
    assert entry["annotation_pass_success"] is False
    assert entry["annotation_pass_failure_reason"] == (
        "annotation_pass_no_export_geometry")
    assert entry["annotation_pass"]["success"] is False


def test_the_flag_defaults_off_and_survives_a_dict_round_trip():
    cfg = Config()
    assert cfg.color_id_buffer_annotation_pass is False
    assert cfg.to_dict()["color_id_buffer_annotation_pass"] is False
    on = Config(color_id_buffer_annotation_pass=True)
    assert Config.from_dict(on.to_dict()).color_id_buffer_annotation_pass is True
