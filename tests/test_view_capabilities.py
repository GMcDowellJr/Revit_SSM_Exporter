import pytest

from vop_interwoven.revit.view_basis import (
    supports_model_geometry,
    supports_crop_bounds,
    supports_depth,
    resolve_view_mode,
    VIEW_MODE_MODEL_AND_ANNOTATION,
    VIEW_MODE_ANNOTATION_ONLY,
    VIEW_MODE_REJECTED,
)


class FakeView:
    def __init__(self, view_type, is_template=False, has_cropbox=True):
        self.ViewType = view_type
        self.IsTemplate = is_template
        if has_cropbox:
            self.CropBox = object()
        # Minimal fields referenced by diagnostics paths (should not be required)
        self.Name = "Fake"
        self.Id = type("Id", (), {"IntegerValue": 123})()


def test_drafting_is_annotation_only():
    v = FakeView("DraftingView", is_template=False, has_cropbox=False)
    mode, reason = resolve_view_mode(v, diag=None)
    assert mode == VIEW_MODE_ANNOTATION_ONLY
    assert reason["view_type"] == "DraftingView"
    assert reason["supports_model_geometry"] is False


def test_floorplan_is_model_and_annotation():
    v = FakeView("FloorPlan", is_template=False, has_cropbox=True)
    mode, reason = resolve_view_mode(v, diag=None)
    assert mode == VIEW_MODE_MODEL_AND_ANNOTATION
    assert reason["supports_model_geometry"] is True
    assert supports_depth(v) is True


def test_legend_view_is_annotation_only():
    v = FakeView("Legend", is_template=False, has_cropbox=True)
    mode, reason = resolve_view_mode(v, diag=None)
    assert mode == VIEW_MODE_ANNOTATION_ONLY
    assert reason["view_type"] == "Legend"
    assert reason["supports_model_geometry"] is False


def test_template_view_is_rejected():
    v = FakeView("FloorPlan", is_template=True, has_cropbox=True)
    mode, reason = resolve_view_mode(v, diag=None)
    assert mode == VIEW_MODE_REJECTED


def test_crop_bounds_capability_requires_model_geometry():
    v = FakeView("DraftingView", is_template=False, has_cropbox=True)
    assert supports_model_geometry(v) is False
    assert supports_crop_bounds(v) is False

def test_numeric_viewtype_floorplan_is_model_capable():
    v = FakeView(1, is_template=False, has_cropbox=True)  # numeric enum form
    mode, reason = resolve_view_mode(v, diag=None)
    assert mode == VIEW_MODE_MODEL_AND_ANNOTATION
