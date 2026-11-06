"""A synthetic Stage A RUN through the real loop: run_vop_pipeline_streaming ->
process_document_views_streaming -> pipeline.process_document_views, with a
fake Revit document, for the loop timing (C2) and legacy pre-pass gate (C3)
tests. Not a test module.

WHAT IS REAL. The streaming run (run directory, StreamingExporter, views_core,
run_meta, root cache), the per-view loop of process_document_views and every
step it runs before and after the capture -- resolve_view_mode,
_view_signature (with the real ElementCache and the real
collection_policy.should_include_element), _extract_view_identity_for_csv,
the strategy-diagnostics construction, the element-map and views_diagnostics
writes, the memory marks and collections.

WHAT IS FAKED, AND WHY THAT DOES NOT MAKE A GATE PASS WRONGLY.
* Autodesk.Revit.DB: ElementId, FilteredElementCollector, Viewport,
  BuiltInParameter, CategoryType -- the API surface those steps touch.
* init_view_raster and collect_view_elements: they need a real Revit view.
  Each is replaced by a fake that derives its output from the VIEW (bounds
  from the view id, the elements from the document), so its call site still
  runs and what it returns still reaches the capture.
* resolve_element_bbox: a deterministic box per element id.
* export_registered_stage_a_view: replaced by a capture that WRITES files
  (a model and an annotation "TIFF", their sidecars) whose bytes are a pure
  function of everything the pipeline hands the capture: the view, the
  elements (ids, in order) and the raster (bounds, model crop, annotation
  frame, basis). So a pipeline step whose output reaches the capture changes
  these bytes when it is gated off, and the byte-identity test sees it.
"""
import contextlib
import hashlib
import json
import os
import sys
import types

from vop_interwoven import pipeline
from vop_interwoven.config import Config


class FakeElementId(object):
    def __init__(self, v):
        self.IntegerValue = int(v)

    def __eq__(self, other):
        return getattr(other, "IntegerValue", None) == self.IntegerValue

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.IntegerValue)


class _ViewType(object):
    def __init__(self, name):
        self._name = name

    def __str__(self):
        return self._name

    def ToString(self):
        return self._name


class _CategoryType(object):
    Model = "Model"
    Annotation = "Annotation"


class _Category(object):
    def __init__(self, cid, name, ctype):
        self.Id = FakeElementId(cid)
        self.Name = name
        self.CategoryType = ctype


WALLS = _Category(-2000011, "Walls", _CategoryType.Model)
DOORS = _Category(-2000023, "Doors", _CategoryType.Model)
TEXT = _Category(-2000300, "Text Notes", _CategoryType.Annotation)


class FakeElement(object):
    def __init__(self, eid, category):
        self.Id = FakeElementId(eid)
        self.Category = category


# (view id, name, view type, element ids with their categories)
VIEWS = (
    (13521188, "P_OVERALL - LEVEL 1", "FloorPlan",
     ((501, WALLS), (502, WALLS), (503, DOORS), (504, TEXT))),
    (13521190, "P_OVERALL - LEVEL 2", "FloorPlan",
     ((501, WALLS), (505, DOORS), (506, TEXT))),
    (13521300, "GENERAL NOTES", "DraftingView", ((601, TEXT),)),
)


def _make_view(view_id, name, vtype):
    return types.SimpleNamespace(
        Id=FakeElementId(view_id), UniqueId="uid-{0}".format(view_id), Name=name,
        IsTemplate=False, ViewType=_ViewType(vtype), Scale=96, CropBoxActive=True,
        CropBoxVisible=True, ViewTemplateId=FakeElementId(-1),
        get_Parameter=lambda _bip: None)


class FakeDoc(object):
    Title = "Synthetic.rvt"
    PathName = r"C:\models\Synthetic.rvt"
    IsModified = False
    ProjectInformation = types.SimpleNamespace(UniqueId="project-guid")
    Application = types.SimpleNamespace(VersionNumber="2024", VersionBuild="24.1.0.66",
                                        VersionName="Autodesk Revit 2024")

    def __init__(self):
        self.views = {}
        self.members = {}
        for view_id, name, vtype, members in VIEWS:
            self.views[view_id] = _make_view(view_id, name, vtype)
            self.members[view_id] = [FakeElement(e, c) for e, c in members]

    def GetElement(self, eid):
        return self.views.get(getattr(eid, "IntegerValue", eid))


class _Collector(object):
    def __init__(self, doc, view_id=None):
        self._items = []
        if view_id is not None:
            self._items = list(doc.members.get(getattr(view_id, "IntegerValue", view_id), []))

    def WhereElementIsNotElementType(self):
        return self

    def OfClass(self, _cls):
        return self

    def __iter__(self):
        return iter(list(self._items))


def _fake_db():
    db = types.ModuleType("Autodesk.Revit.DB")
    db.ElementId = FakeElementId
    db.FilteredElementCollector = _Collector
    db.Viewport = type("Viewport", (), {})
    db.CategoryType = _CategoryType
    db.BuiltInParameter = types.SimpleNamespace(VIEW_DISCIPLINE=1, VIEW_PHASE=2)
    return db


@contextlib.contextmanager
def fake_revit_db():
    names = ("Autodesk", "Autodesk.Revit", "Autodesk.Revit.DB")
    saved = dict((n, sys.modules.get(n)) for n in names)
    sys.modules["Autodesk"] = types.ModuleType("Autodesk")
    sys.modules["Autodesk.Revit"] = types.ModuleType("Autodesk.Revit")
    sys.modules["Autodesk.Revit.DB"] = _fake_db()
    try:
        yield
    finally:
        for n, mod in saved.items():
            if mod is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = mod


class _Bounds(object):
    def __init__(self, xmin, ymin, xmax, ymax):
        self.xmin, self.ymin, self.xmax, self.ymax = xmin, ymin, xmax, ymax

    def as_list(self):
        return [self.xmin, self.ymin, self.xmax, self.ymax]


def fake_init_view_raster(doc, view, cfg, diag=None):
    vid = view.Id.IntegerValue % 1000
    return types.SimpleNamespace(
        W=10, H=8, cell_size_ft=1.0,
        bounds_xy=_Bounds(0.0, 0.0, 10.0 + vid, 8.0),
        model_clip_bounds=_Bounds(1.0, 1.0, 9.0 + vid, 7.0),
        anno_frame_bounds=_Bounds(-1.0, -1.0, 11.0 + vid, 9.0),
        view_basis=("basis", vid))


def fake_collect_view_elements(doc, view, raster, diag=None, cfg=None):
    return [e for e in doc.members[view.Id.IntegerValue]
            if e.Category.CategoryType == _CategoryType.Model]


def fake_resolve_element_bbox(elem, view=None, diag=None, context=None):
    i = float(elem.Id.IntegerValue)
    return types.SimpleNamespace(Min=types.SimpleNamespace(X=i, Y=0.0, Z=0.0),
                                 Max=types.SimpleNamespace(X=i + 1.0, Y=1.0, Z=1.0)), "model"


def _capture_bytes(view, elements, raster, which):
    """What a capture would export, as a pure function of its inputs."""
    payload = {"which": which, "view_id": view.Id.IntegerValue, "view_name": view.Name,
               "elements": [e.Id.IntegerValue for e in elements or []]}
    if raster is not None:
        for attr in ("bounds_xy", "model_clip_bounds", "anno_frame_bounds"):
            b = getattr(raster, attr, None)
            payload[attr] = None if b is None else b.as_list()
        payload["view_basis"] = list(getattr(raster, "view_basis", None) or [])
    blob = json.dumps(payload, sort_keys=True).encode("utf-8")
    return b"II*\x00" + hashlib.sha256(blob).digest() + blob


def fake_registered_capture(doc, view, elements, cfg, diag=None, raster=None,
                            elem_cache=None, view_start_memory=None):
    from vop_interwoven.output_layout import CAPTURE_DIR
    folder = os.path.join(cfg.output_dir, CAPTURE_DIR[getattr(cfg, "_stage_a_layout", "v2")])
    if not os.path.isdir(folder):
        os.makedirs(folder)
    stem = os.path.join(folder, "{0}_{1}".format(view.Name, view.Id.IntegerValue))
    paths = {}
    for which, suffix in (("model", ""), ("annotation", "_anno")):
        data = _capture_bytes(view, elements, raster, which)
        with open(stem + suffix + ".tif", "wb") as handle:
            handle.write(data)
        sidecar = {"view_id": view.Id.IntegerValue, "pass": which,
                   "color_assignment_map": {str(e.Id.IntegerValue): i + 1
                                            for i, e in enumerate(elements or [])},
                   "tiff_sha256": hashlib.sha256(data).hexdigest(),
                   "registration_marks": {"timings_ms": {"total": 12.5,
                                                         "model_pass": 5.0,
                                                         "annotation_pass": 4.0,
                                                         "unaccounted": 3.5}}}
        with open(stem + suffix + ".json", "w") as handle:
            json.dump(sidecar, handle, indent=2, sort_keys=True)
        paths[which] = (stem + suffix + ".tif", stem + suffix + ".json")
    registration = {"success": True, "faults": [],
                    "timings_ms": {"total": 12.5, "model_pass": 5.0,
                                   "annotation_pass": 4.0, "unaccounted": 3.5}}
    anno = {"view_id": view.Id.IntegerValue, "view_name": view.Name, "success": True,
            "stage": "color_id_buffer_stage_a_annotation",
            "tiff_path": paths["annotation"][0], "sidecar_path": paths["annotation"][1]}
    return {"view_id": view.Id.IntegerValue, "view_name": view.Name, "success": True,
            "stage": "color_id_buffer_stage_a", "view": view,
            "tiff_path": paths["model"][0], "sidecar_path": paths["model"][1],
            "annotation_pass": anno, "annotation_pass_success": True,
            "annotation_pass_failure_reason": None,
            "annotation_tiff_path": paths["annotation"][0],
            "annotation_sidecar_path": paths["annotation"][1],
            "registration": registration, "registration_success": True}


def install(monkeypatch):
    """Install every fake above (raising=True: a renamed production name
    fails the caller instead of silently testing nothing)."""
    monkeypatch.setattr(pipeline, "init_view_raster", fake_init_view_raster)
    monkeypatch.setattr(pipeline, "collect_view_elements", fake_collect_view_elements)
    monkeypatch.setattr("vop_interwoven.revit.collection.resolve_element_bbox",
                        fake_resolve_element_bbox)
    monkeypatch.setattr(
        "vop_interwoven.stage_a_registered_capture.export_registered_stage_a_view",
        fake_registered_capture)
    # The views_core row's metadata (doc.GetElement(ElementId(view_id))).
    monkeypatch.setattr("vop_interwoven.revit.collection_policy.CategoryType",
                        _CategoryType, raising=False)


def make_cfg(**overrides):
    kwargs = dict(enable_color_id_buffer_stage_a=True)
    kwargs.update(overrides)
    return Config(**kwargs)


def run(monkeypatch, root, cfg=None, view_ids=None, batch_size=None):
    """One synthetic run under ``root``; returns (result, run_dir)."""
    from vop_interwoven.capture_run import _run_capture
    install(monkeypatch)
    cfg = cfg if cfg is not None else make_cfg()
    ids = list(view_ids or [v[0] for v in VIEWS])
    with fake_revit_db():
        result, run_dir, _run_id = _run_capture(
            FakeDoc(), ids, cfg, str(root), batch_size=batch_size,
            date_override="2026-11-02", export_view_raster=False)
    return result, run_dir
