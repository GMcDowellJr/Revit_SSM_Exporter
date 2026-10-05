"""Under Stage A, drafting views and legends are captured by the colour-ID process.

``resolve_view_mode`` classifies a DraftingView or a Legend ANNOTATION_ONLY.
Before this change, ``process_document_views`` gave those views the Stage A
branch only when they were MODEL_AND_ANNOTATION, so under Stage A they fell
through to the geometry annotation raster (``rasterize_annotations`` +
``export_view_raster``) and produced no TIFF or sidecar at all.

Now they take the Stage A capture with NO model elements: nothing is collected
for the model pass, and the capture is handed an empty element list.

Both directions are pinned over the same fixture (the shape
test_stage_a_skips_the_model_pass.py explains): with Stage A off, the view
must still reach the geometry annotation raster and never the capture --
the CONTROL, which fails if the fixture stops reaching the loop body at all.

Mutation: restoring the old condition (``if view_mode ==
VIEW_MODE_MODEL_AND_ANNOTATION:``) turns every Stage A case red and leaves
the controls green.
"""
import types

import pytest

from vop_interwoven import pipeline

from tests.test_stage_a_skips_the_model_pass import (
    VIEW_ID, _FakeElementId, _FakeViewType, _fake_doc, _fake_revit_db, _make_cfg,
)


def _annotation_only_view(view_type):
    return types.SimpleNamespace(
        Id=_FakeElementId(VIEW_ID),
        Name="Detail {0}".format(view_type),
        IsTemplate=False,
        ViewType=_FakeViewType(view_type),
        Scale=12,
    )


def _run(monkeypatch, tmp_path, view_type, stage_a, registered=True):
    view = _annotation_only_view(view_type)
    doc = _fake_doc(view)
    calls = {"capture": [], "fallback_capture": [], "collect": 0,
             "anno_raster": 0, "model_pass": 0}

    def _capture(key):
        def _fn(doc_, view_, elements_, cfg_, **kwargs):
            calls[key].append(list(elements_))
            return {"view_id": VIEW_ID, "view_name": view_.Name, "success": True,
                    "stage": "color_id_buffer_stage_a"}
        return _fn

    def _count(key, ret=None):
        def _fn(*a, **k):
            calls[key] += 1
            return ret
        return _fn

    monkeypatch.setattr(
        "vop_interwoven.stage_a_registered_capture.export_registered_stage_a_view",
        _capture("capture"))
    monkeypatch.setattr(
        "vop_interwoven.color_id_buffer.export_color_id_buffer_view",
        _capture("fallback_capture"))
    monkeypatch.setattr(pipeline, "render_model_front_to_back",
                        _count("model_pass", {"timings": {}}))
    monkeypatch.setattr(pipeline, "collect_view_elements", _count("collect", []))
    monkeypatch.setattr(pipeline, "rasterize_annotations", _count("anno_raster", {}))
    monkeypatch.setattr(
        pipeline, "init_view_raster",
        lambda doc_, view_, cfg_, diag=None: types.SimpleNamespace(
            W=8, H=8, cell_size_ft=1.0, bounds_xy=None, view_basis=None,
            finalize_anno_over_model=lambda cfg: None,
        ),
    )

    cfg = _make_cfg(tmp_path, stage_a)
    cfg.color_id_buffer_registered_capture = registered
    with _fake_revit_db():
        results = pipeline.process_document_views(doc, [VIEW_ID], cfg)
    return calls, results


@pytest.mark.parametrize("view_type", ["DraftingView", "Legend"])
def test_control_without_stage_a_the_geometry_annotation_raster_runs(
        monkeypatch, tmp_path, view_type):
    """THE CONTROL: this fixture reaches the loop body and is ANNOTATION_ONLY."""
    calls, results = _run(monkeypatch, tmp_path, view_type, stage_a=False)
    assert calls["anno_raster"] == 1
    assert calls["capture"] == [] and calls["fallback_capture"] == []
    assert calls["model_pass"] == 0 and calls["collect"] == 0
    assert len(results) == 1


@pytest.mark.parametrize("view_type", ["DraftingView", "Legend"])
def test_under_stage_a_the_view_is_captured_with_no_model_elements(
        monkeypatch, tmp_path, view_type):
    calls, results = _run(monkeypatch, tmp_path, view_type, stage_a=True)
    assert calls["capture"] == [[]], "the registered Stage A capture was not run"
    # Not the geometry path in any part, and no model collection.
    assert calls["anno_raster"] == 0
    assert calls["model_pass"] == 0
    assert calls["collect"] == 0
    assert len(results) == 1
    assert results[0]["stage"] == "color_id_buffer_stage_a"
    assert results[0]["view_mode"] == "ANNOTATION_ONLY"


def test_the_two_pass_fallback_capture_is_taken_too(monkeypatch, tmp_path):
    calls, results = _run(monkeypatch, tmp_path, "DraftingView", stage_a=True,
                          registered=False)
    assert calls["fallback_capture"] == [[]]
    assert calls["capture"] == [] and calls["anno_raster"] == 0
    assert len(results) == 1


# --- legend components: hidden in the model pass, kept in the annotation pass

def test_legend_components_are_hidden_for_the_model_pass_only():
    """OST_LegendComponents reports CategoryType.Model. Without the
    VIEW_ONLY_MODEL_BIC_NAMES entry the model pass of a legend leaves every
    legend component visible and unpainted, and the annotation pass's
    hide_categories mode hides the very content it is there to paint.

    Control: an ordinary Model category (Walls) is still hidden for the
    annotation pass and visible in the model pass, so the assertion is not
    satisfied by a scan that classifies nothing.

    Mutation: removing "OST_LegendComponents" from VIEW_ONLY_MODEL_BIC_NAMES
    turns this red."""
    from vop_interwoven import color_id_buffer
    from tests.stage_a_capture_fakes import (
        FakeBuiltInCategory, FakeCategory, FakeDoc, FakeViewPlan,
        install_fake_revit_db,
    )
    legend = FakeCategory("Legend Components",
                          FakeBuiltInCategory.OST_LegendComponents, cat_type="Model")
    walls = FakeCategory("Walls", -2000011, cat_type="Model")
    text = FakeCategory("Text Notes", -2000300, cat_type="Annotation")
    doc = FakeDoc(categories=[legend, walls, text])
    view = FakeViewPlan(VIEW_ID)
    with install_fake_revit_db():
        model_pass_hides = set(color_id_buffer._hidden_category_state(doc, view))
        anno_pass_hides = set(color_id_buffer._model_category_hidden_state(doc, view))
    legend_id = FakeBuiltInCategory.OST_LegendComponents
    assert legend_id in model_pass_hides
    assert legend_id not in anno_pass_hides
    # Control.
    assert -2000011 in anno_pass_hides and -2000011 not in model_pass_hides
    assert -2000300 in model_pass_hides
