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


def test_the_registered_capture_is_used_even_when_the_fallback_is_configured(
        monkeypatch, tmp_path):
    """The two-pass fallback writes a crop and has no ticks, so it cannot frame
    a view with no crop region; such a view takes the registered capture."""
    calls, results = _run(monkeypatch, tmp_path, "DraftingView", stage_a=True,
                          registered=False)
    assert calls["capture"] == [[]]
    assert calls["fallback_capture"] == [] and calls["anno_raster"] == 0
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


# --- end to end: the REGISTERED capture of a view with no crop region --------

from tests import test_probe_anno_pass_run_variant as world  # noqa: E402

CROP_NAMES = ("CropBox", "CropBoxActive", "CropBoxVisible")


class _NoCropView(world._ProbeView):
    """A view with no crop region: once armed, reading or writing any crop
    property -- or asking for the split manager -- raises and is logged."""

    def __init__(self, view_id, view_type):
        object.__setattr__(self, "_armed", False)
        object.__setattr__(self, "crop_access", [])
        world._ProbeView.__init__(self, view_id)
        object.__setattr__(self, "ViewType", _FakeViewType(view_type))
        object.__setattr__(self, "_armed", True)

    def __getattribute__(self, name):
        if name in CROP_NAMES and object.__getattribute__(self, "_armed"):
            object.__getattribute__(self, "crop_access").append(("read", name))
            raise RuntimeError("this view has no crop region (fake): " + name)
        return object.__getattribute__(self, name)

    def __setattr__(self, name, value):
        if name in CROP_NAMES and object.__getattribute__(self, "_armed"):
            object.__getattribute__(self, "crop_access").append(("write", name))
            raise RuntimeError("this view has no crop region (fake): " + name)
        world._ProbeView.__setattr__(self, name, value)

    def GetCropRegionShapeManager(self):
        self.crop_access.append(("read", "GetCropRegionShapeManager"))
        raise RuntimeError("this view has no crop region (fake)")


def _run_registered_no_crop(tmp_path, view_type, refuse_white=False):
    from vop_interwoven.config import Config
    from vop_interwoven.core.math_utils import Bounds2D
    from vop_interwoven.stage_a_registered_capture import export_registered_stage_a_view
    from tests.stage_a_capture_fakes import FakeDiag, FakeElement, install_fake_revit_db
    from tests.test_stage_a_annotation_pass_probe_switches import (
        ANNO_CAT, _BBox, _PLAN_BASIS, _SizedDoc,
    )
    del world._LOG[:]
    view_id = 77
    # Everything a drafting view holds is owned by it.
    elements = [
        FakeElement(2001, ANNO_CAT, owner_view_id=view_id,
                    bbox=_BBox((25, 20, 0), (31, 23, 0))),
        FakeElement(2002, ANNO_CAT, owner_view_id=view_id,
                    bbox=_BBox((40, 30, 0), (47, 34, 0))),
        FakeElement(3001, world.LINES_CAT, owner_view_id=view_id,
                    bbox=_BBox((22, 18, 0), (70, 18, 0))),
    ]
    view = _NoCropView(view_id, view_type)
    view.Scale = 96
    doc = _SizedDoc(elements=elements, link_instances=[],
                    categories=[ANNO_CAT, world.LINES_CAT])
    doc.IsModifiable = False
    doc.Create = world._Create(doc)
    # The frame init_view_raster builds for it: every element's bbox + pad.
    frame = Bounds2D(20.0, 15.0, 50.0, 37.0)
    raster = types.SimpleNamespace(
        W=30, H=22, cell_size_ft=1.0, bounds_xy=frame, model_clip_bounds=None,
        anno_frame_bounds=None, anno_cap_envelope_applied=False,
        view_basis=_PLAN_BASIS)
    if refuse_white:
        # The white override does not take on the capture's own lines: every
        # other override still does.
        real_set = view.SetElementOverrides

        def _set(eid, ogs):
            if world._colour_of(ogs) == (255, 255, 255) and int(eid.IntegerValue) > 5000:
                raise RuntimeError("override refused (fake)")
            return real_set(eid, ogs)
        object.__setattr__(view, "SetElementOverrides", _set)
    exports = []
    doc.on_export_image = lambda opts: exports.append(dict(
        (eid, world._colour_of(ogs)) for eid, ogs in view.element_overrides.items()))
    cfg = Config(enable_color_id_buffer_stage_a=True,
                 color_id_buffer_registered_capture=True,
                 color_id_buffer_export_dpi=150.0)
    cfg.include_linked_rvt = False
    cfg.debug_dump_path = str(tmp_path)
    diag = FakeDiag()
    with install_fake_revit_db() as fake_db:
        fake_db.Transaction = world._Tx
        fake_db.TransactionGroup = world._Group
        fake_db.TransactionStatus = world._STATUS
        fake_db.Line = world._Line
        fake_db.FilteredElementCollector = world._ViewCollector
        world._ROLLBACK_TARGETS[:] = [view, doc]
        out = export_registered_stage_a_view(doc, view, [], cfg, diag=diag,
                                             raster=raster)
    return out, view, frame, exports


@pytest.mark.parametrize("view_type", ["DraftingView", "Legend"])
def test_a_view_with_no_crop_region_is_captured_without_touching_a_crop(
        tmp_path, view_type):
    import json
    out, view, frame, exports = _run_registered_no_crop(tmp_path, view_type)
    reg = out["registration"]
    assert view.crop_access == []
    assert reg["faults"] == [], reg["faults"]
    assert out["success"] is True and out["annotation_pass_success"] is True
    assert reg["split_crop"] == {"state": "no_crop_region"}
    assert reg["mark_reference_source"] == "no_crop_region_frame"
    assert not any(k.startswith("crop_box") or k == "split_crop"
                   for k in reg["restore"]["view_state"])
    # The FILES: no crop written, and crop A is the element frame.
    with open(out["sidecar_path"]) as f:
        model = json.load(f)
    with open(out["annotation_sidecar_path"]) as f:
        anno = json.load(f)
    assert model["frame"]["crop_write"]["source"] == "no_crop_region"
    assert model["frame"]["crop_write"]["written"] is False
    assert model["frame"]["crop_uv"] == [frame.xmin, frame.ymin, frame.xmax, frame.ymax]
    assert anno["registration"]["crop_applied"] == "none"
    assert "no crop region" in anno["registration"]["rendered_uv_reason"]
    # Run 1005_0947: ticks ON the frame edge were lost to the derived axis's
    # rounding. So the ticks keep their ordinary inset, INSIDE crop A ...
    ends = [p for m in reg["marks"]["created"] for p in m["readback_uv"]]
    assert reg["marks"]["created_count"] == 12
    assert min(p[0] for p in ends) > frame.xmin and max(p[0] for p in ends) < frame.xmax
    assert min(p[1] for p in ends) > frame.ymin and max(p[1] for p in ends) < frame.ymax
    # ... and two frame-bound lines, read back from what was drawn, bound
    # exactly crop A -- which is what sets the uncropped export's extent.
    bounds = reg["frame_bounds"]
    assert bounds["created_count"] == 2
    b_ends = [p for b in bounds["created"] for p in b["readback_uv"]]
    assert (min(p[0] for p in b_ends), min(p[1] for p in b_ends),
            max(p[0] for p in b_ends), max(p[1] for p in b_ends)) == pytest.approx(
        (frame.xmin, frame.ymin, frame.xmax, frame.ymax))
    # WHITE in both exports: never ink. Not in the annotation palette or its
    # color_assignment_map, and in the FILES' registration record.
    bound_ids = [b["id"] for b in bounds["created"]]
    assert len(exports) == 2
    for overrides in exports:
        assert all(overrides[i] == (255, 255, 255) for i in bound_ids)
    assert not set(str(i) for i in bound_ids) & set(anno["color_assignment_map"])
    assert sorted(anno["palette_reservation"]["blank_ids_painted"]) == sorted(bound_ids)
    for sidecar in (model, anno):
        assert sidecar["registration_marks"]["frame_bounds"]["created_count"] == 2
    # Rolled back with the ticks.
    assert reg["restore"]["marks_still_in_project"] == []
    # The analysis grid reads the FILE's split record as unsplit, not as a
    # split the capture failed to remove (run 1005_0947 refused both views).
    from tools import stage_a_grid as grid
    assert grid.split_crop_bands(model) is None


def test_control_the_same_view_as_a_floor_plan_reaches_the_crop_and_fails(tmp_path):
    """THE CONTROL: the fixture does reach the crop code -- on a view that
    claims a crop region, every access raises and the capture faults."""
    out, view, _frame, _exports = _run_registered_no_crop(tmp_path, "FloorPlan")
    assert view.crop_access != []
    assert out["registration"]["success"] is False
    # A view with a crop region gets no frame-bound lines.
    assert out["registration"].get("frame_bounds") is None


# --- the frame: the bbox of EVERY element in the view, plus a pad -----------

class _Box(object):
    def __init__(self, mn, mx):
        self.Min = types.SimpleNamespace(X=mn[0], Y=mn[1], Z=0.0)
        self.Max = types.SimpleNamespace(X=mx[0], Y=mx[1], Z=0.0)


class _Elem(object):
    def __init__(self, box=None, raises=False):
        self._box, self._raises = box, raises

    def get_BoundingBox(self, view):
        if self._raises:
            raise RuntimeError("bbox unreadable (fake)")
        return self._box


def _with_collector(elems):
    import contextlib
    import sys

    class _FEC(object):
        def __init__(self, doc, view_id):
            pass

        def WhereElementIsNotElementType(self):
            return list(elems)

    @contextlib.contextmanager
    def _cm():
        fake = types.ModuleType("Autodesk.Revit.DB")
        fake.FilteredElementCollector = _FEC
        saved = sys.modules.get("Autodesk.Revit.DB")
        sys.modules["Autodesk.Revit.DB"] = fake
        try:
            yield
        finally:
            if saved is None:
                sys.modules.pop("Autodesk.Revit.DB", None)
            else:
                sys.modules["Autodesk.Revit.DB"] = saved
    return _cm()


def _identity_basis():
    return types.SimpleNamespace(transform_to_view_uv=lambda p: (p[0], p[1]),
                                 transform_to_view_uvw=lambda p: (p[0], p[1], p[2]))


class _Rot90(object):
    """A bbox Transform: a 90-degree rotation about Z, then a move to (100, 0)."""
    def OfPoint(self, p):
        return (100.0 - p[1], p[0], p[2])


def test_a_rotated_elements_bbox_is_framed_where_it_is_drawn():
    """BoundingBoxXYZ.Min/Max are bbox-LOCAL: an element with a Transform (a
    rotated family instance) is framed through it, not at its local corners
    (Codex, PR #227). Mutation: project Min/Max without the Transform ->
    the frame lands at (0..10, 0..2), red."""
    from vop_interwoven.revit.view_basis import resolve_view_element_bounds
    box = _Box((0, 0), (10, 2))
    box.Transform = _Rot90()
    view = types.SimpleNamespace(Id=_FakeElementId(VIEW_ID))
    with _with_collector([_Elem(box)]):
        b = resolve_view_element_bounds(None, view, _identity_basis(), 0.0)
    assert (b.xmin, b.ymin, b.xmax, b.ymax) == (98.0, 0.0, 100.0, 10.0)


def test_the_frame_covers_every_element_not_only_the_extent_drivers():
    """A detail line or filled region far from any text draws in an uncropped
    export, so it is inside the frame. An element with no bbox adds nothing;
    one whose bbox will not read is warned, not guessed."""
    from vop_interwoven.revit.view_basis import resolve_view_element_bounds
    from tests.stage_a_capture_fakes import FakeDiag
    elems = [_Elem(_Box((10, 10), (12, 11))),      # a text note
             _Elem(_Box((-30, 5), (40, 5))),       # a long detail line
             _Elem(None),                          # no bbox
             _Elem(raises=True)]                   # unreadable
    diag = FakeDiag()
    view = types.SimpleNamespace(Id=_FakeElementId(VIEW_ID))
    with _with_collector(elems):
        b = resolve_view_element_bounds(None, view, _identity_basis(), 0.5, diag=diag)
    assert (b.xmin, b.ymin, b.xmax, b.ymax) == (-30.5, 4.5, 40.5, 11.5)
    assert [w["callsite"] for w in diag.warnings] == ["resolve_view_element_bounds"]
    with _with_collector([_Elem(None)]):
        assert resolve_view_element_bounds(None, view, _identity_basis(), 0.5) is None


@pytest.mark.parametrize("stage_a", [True, False])
def test_init_view_raster_frames_by_every_element_under_stage_a_only(
        monkeypatch, tmp_path, stage_a):
    """Control is stage_a=False: the geometry path keeps its extent-driver
    bounds, unchanged."""
    from vop_interwoven.core.math_utils import Bounds2D
    import vop_interwoven.revit.view_basis as vb
    monkeypatch.setattr(vb, "resolve_view_element_bounds",
                        lambda *a, **k: Bounds2D(0, 0, 30, 20))
    monkeypatch.setattr(vb, "resolve_annotation_only_bounds",
                        lambda *a, **k: Bounds2D(5, 5, 6, 6))
    monkeypatch.setattr(pipeline, "make_view_basis",
                        lambda view, diag=None: _identity_basis())
    view = _annotation_only_view("DraftingView")
    raster = pipeline.init_view_raster(None, view, _make_cfg(tmp_path, stage_a))
    b = raster.bounds_xy
    expected = (0, 0, 30, 20) if stage_a else (5, 5, 6, 6)
    assert (b.xmin, b.ymin, b.xmax, b.ymax) == expected


def test_a_frame_bound_that_will_not_paint_white_is_a_fault_in_the_FILE(tmp_path):
    """Codex, PR #227: a bound whose white override fails draws in its native
    colour, and the capture used to report success. Control: the clean
    capture above has no faults. Mutation: drop the painted check -> red."""
    import json
    out, _view, _frame, _exports = _run_registered_no_crop(
        tmp_path, "DraftingView", refuse_white=True)
    faults = [f["fault"] for f in out["registration"]["faults"]]
    assert "frame_bounds_not_white" in faults
    assert out["registration"]["success"] is False
    with open(out["sidecar_path"]) as f:
        model = json.load(f)
    assert "frame_bounds_not_white" in [
        f["fault"] for f in model["registration_marks"]["faults"]]
