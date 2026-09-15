"""
Unit test for VOP Stage A shadow suppression
(vop_interwoven/color_id_buffer.py's export_color_id_buffer_view).

Exercises the full suppress/export/restore transaction lifecycle by
installing a fake Autodesk.Revit.DB + System.Collections.Generic surface
into sys.modules -- the same technique
tests/test_color_id_buffer_link_category_filters.py uses for Revit-adjacent
code that has no other way to run outside Revit. The view/elements/links
inputs are kept minimal (no HOST elements, LINK RVT expansion disabled) so
this test is scoped to the shadow-suppression mechanics themselves rather
than re-testing collection, painting, or LINK category-filter behavior
already covered elsewhere.

Locks in:
  - ShowShadows is captured before suppression, forced False during the
    suppress/export window (asserted from inside the fake ExportImage
    call, i.e. mid-transaction), and restored to its original value once
    the restore transaction runs -- mirroring SmoothEdges's existing
    capture/suppress/restore pattern exactly.
  - "applied_show_shadows" is reported in the returned metadata dict.
"""
import contextlib
import os
import sys
import types

import vop_interwoven.color_id_buffer as color_id_buffer
from vop_interwoven.config import Config


# --- Fake Autodesk.Revit.DB surface -----------------------------------------

class _FakeElementId(object):
    def __init__(self, v):
        self.IntegerValue = int(v)

    def __eq__(self, other):
        return isinstance(other, _FakeElementId) and other.IntegerValue == self.IntegerValue

    def __hash__(self):
        return hash(self.IntegerValue)

    def __repr__(self):
        return "ElementId({0})".format(self.IntegerValue)


_INVALID_ELEMENT_ID = _FakeElementId(-1)
_FakeElementId.InvalidElementId = _INVALID_ELEMENT_ID


class _FakeColor(object):
    def __init__(self, r, g, b):
        self.r, self.g, self.b = r, g, b


class _FakeOGS(object):
    def __init__(self):
        self.calls = {}

    def __getattr__(self, name):
        def _record(*args):
            self.calls[name] = args
        return _record


class _FakeTransaction(object):
    def __init__(self, doc, name):
        self.doc = doc
        self.name = name
        self.started = False
        self.committed = False
        self.rolled_back = False

    def Start(self):
        self.started = True

    def Commit(self):
        self.committed = True

    def RollBack(self):
        self.rolled_back = True


class _FakeTransformMarker(object):
    pass


class _FakeTransform(object):
    Identity = _FakeTransformMarker()


class _FakeFillPattern(object):
    def __init__(self, is_solid, target):
        self.IsSolidFill = is_solid
        self.Target = target


class _FakeFillPatternElement(object):
    def __init__(self, pattern_id, pattern):
        self.Id = _FakeElementId(pattern_id)
        self._pattern = pattern

    def GetFillPattern(self):
        return self._pattern


class _FakeFillPatternTarget(object):
    Drafting = "Drafting"
    Model = "Model"


class _FakePhaseFilter(object):
    def __init__(self, name, filter_id):
        self.Name = name
        self.Id = _FakeElementId(filter_id)
        self.status_presentation_calls = []

    def SetPhaseStatusPresentation(self, status, presentation):
        self.status_presentation_calls.append((status, presentation))

    @classmethod
    def Create(cls, doc, name):
        pf = cls(name, 5000 + len(doc.phase_filters))
        doc.phase_filters.append(pf)
        return pf


class _FakeElementOnPhaseStatus(object):
    New = "New"
    Existing = "Existing"
    Demolished = "Demolished"
    Temporary = "Temporary"


class _FakePhaseStatusPresentation(object):
    ShowByCategory = "ShowByCategory"


class _FakeCategoryType(object):
    Model = "Model"
    Annotation = "Annotation"


class _FakeBuiltInParameter(object):
    VIEW_PHASE_FILTER = "VIEW_PHASE_FILTER"


class _FakeBuiltInCategory(object):
    # Deliberately empty: doc.Settings.Categories is empty in this test, so
    # VIEW_ONLY_MODEL_BIC_NAMES lookups (getattr(..., None)) never resolve
    # to anything a hidden-category pass would act on.
    pass


class _FakeImageFileType(object):
    TIFF = "TIFF"


class _FakeImageExportOptions(object):
    def __init__(self):
        self.ExportRange = None
        self.ZoomType = None
        self.FitDirection = None
        self.PixelSize = None
        self.FilePath = None
        self.HLRandWFViewsFileType = None
        self.ShadowViewsFileType = None

    def SetViewsAndSheets(self, ids):
        self.view_ids = ids


class _FakeExportRange(object):
    SetOfViews = "SetOfViews"


class _FakeZoomFitType(object):
    FitToPage = "FitToPage"


class _FakeFitDirectionType(object):
    Horizontal = "Horizontal"


class _FakeGenericList(list):
    def Add(self, item):
        self.append(item)


class _FakeListFactory(object):
    def __getitem__(self, _item_type):
        return _FakeGenericList


class _FakeCollector(object):
    def __init__(self, source, _view_id=None):
        self._source = source

    def OfClass(self, cls):
        if cls is _FakePhaseFilter:
            return list(getattr(self._source, "phase_filters", []))
        if cls is _FakeFillPatternElement:
            return list(getattr(self._source, "fill_patterns", []))
        return []

    def WhereElementIsNotElementType(self):
        return []


class _FakeDoc(object):
    def __init__(self):
        self.phase_filters = []
        self.fill_patterns = [
            _FakeFillPatternElement(700, _FakeFillPattern(True, _FakeFillPatternTarget.Drafting)),
        ]
        self.deleted_ids = []
        self.export_image_calls = []
        # Assigned by the test after the fake DB module is installed, so
        # ExportImage can inspect/assert on live view state mid-transaction.
        self.on_export_image = None

        class _Settings(object):
            Categories = []
        self.Settings = _Settings()

    def GetElement(self, eid):
        raise AssertionError("doc.GetElement should not be called with no resolved elements")

    def Delete(self, eid):
        self.deleted_ids.append(eid)

    def ExportImage(self, opts):
        self.export_image_calls.append(opts)
        if self.on_export_image is not None:
            self.on_export_image(opts)
        out_dir = os.path.dirname(opts.FilePath)
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        with open(opts.FilePath + ".tiff", "wb") as f:
            f.write(b"FAKE_TIFF")


class _FakeDisplayModel(object):
    def __init__(self, smooth_edges, show_shadows):
        self.SmoothEdges = smooth_edges
        self.ShowShadows = show_shadows
        self.disposed = False

    def Dispose(self):
        self.disposed = True


class _FakeView(object):
    def __init__(self, view_id):
        self.Id = _FakeElementId(view_id)
        self.Name = "TestView"
        self.ViewTemplateId = _INVALID_ELEMENT_ID
        self.CropBox = object()
        self.CropBoxActive = True
        self._smooth_edges = True
        self._show_shadows = True
        self.display_model_writes = []

    def get_Parameter(self, bip):
        return None

    def GetFilters(self):
        return []

    def GetViewDisplayModel(self):
        return _FakeDisplayModel(self._smooth_edges, self._show_shadows)

    def SetViewDisplayModel(self, dm):
        self._smooth_edges = dm.SmoothEdges
        self._show_shadows = dm.ShowShadows
        self.display_model_writes.append((dm.SmoothEdges, dm.ShowShadows))


class _FakeDiag(object):
    def __init__(self):
        self.warnings = []
        self.errors = []

    def warn(self, **kwargs):
        self.warnings.append(kwargs)

    def error(self, **kwargs):
        self.errors.append(kwargs)


@contextlib.contextmanager
def _install_fake_revit_db():
    fake_db = types.ModuleType("Autodesk.Revit.DB")
    fake_db.ElementId = _FakeElementId
    fake_db.Color = _FakeColor
    fake_db.OverrideGraphicSettings = _FakeOGS
    fake_db.Transaction = _FakeTransaction
    fake_db.Transform = _FakeTransform
    fake_db.FilteredElementCollector = _FakeCollector
    fake_db.FillPatternElement = _FakeFillPatternElement
    fake_db.FillPatternTarget = _FakeFillPatternTarget
    fake_db.PhaseFilter = _FakePhaseFilter
    fake_db.ElementOnPhaseStatus = _FakeElementOnPhaseStatus
    fake_db.PhaseStatusPresentation = _FakePhaseStatusPresentation
    fake_db.CategoryType = _FakeCategoryType
    fake_db.BuiltInParameter = _FakeBuiltInParameter
    fake_db.BuiltInCategory = _FakeBuiltInCategory
    fake_db.ImageFileType = _FakeImageFileType
    fake_db.ImageExportOptions = _FakeImageExportOptions
    fake_db.ExportRange = _FakeExportRange
    fake_db.ZoomFitType = _FakeZoomFitType
    fake_db.FitDirectionType = _FakeFitDirectionType
    fake_db.RevitLinkInstance = type("RevitLinkInstance", (), {})
    fake_db.ParameterFilterElement = type("ParameterFilterElement", (), {})

    fake_system = types.ModuleType("System")
    fake_system_collections = types.ModuleType("System.Collections")
    fake_scg = types.ModuleType("System.Collections.Generic")
    fake_scg.List = _FakeListFactory()

    module_names = (
        "Autodesk.Revit.DB", "System", "System.Collections", "System.Collections.Generic",
    )
    fakes = (fake_db, fake_system, fake_system_collections, fake_scg)
    originals = {name: sys.modules.get(name) for name in module_names}
    for name, fake in zip(module_names, fakes):
        sys.modules[name] = fake
    try:
        yield fake_db
    finally:
        for name in module_names:
            orig = originals[name]
            if orig is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = orig


def test_shadows_suppressed_during_export_and_restored_after(tmp_path):
    with _install_fake_revit_db():
        doc = _FakeDoc()
        view = _FakeView(view_id=42)
        cfg = Config()
        cfg.include_linked_rvt = False
        cfg.debug_dump_path = str(tmp_path)
        diag = _FakeDiag()

        captured = {}

        def _on_export_image(opts):
            # Mid-transaction: suppression must already be in effect here,
            # before restore has run.
            captured["smooth_edges_during_export"] = view._smooth_edges
            captured["show_shadows_during_export"] = view._show_shadows

        doc.on_export_image = _on_export_image

        result = color_id_buffer.export_color_id_buffer_view(
            doc, view, elements=[], cfg=cfg, diag=diag, raster=None, elem_cache=None,
        )

    assert captured["show_shadows_during_export"] is False
    assert captured["smooth_edges_during_export"] is False

    assert result["metadata"]["applied_show_shadows"] is False
    assert result["metadata"]["applied_smooth_edges"] is False

    # Restored to the original True afterward.
    assert view._show_shadows is True
    assert view._smooth_edges is True


def test_show_shadows_suppression_skipped_when_already_off(tmp_path):
    with _install_fake_revit_db():
        doc = _FakeDoc()
        view = _FakeView(view_id=43)
        view._show_shadows = False
        cfg = Config()
        cfg.include_linked_rvt = False
        cfg.debug_dump_path = str(tmp_path)
        diag = _FakeDiag()

        result = color_id_buffer.export_color_id_buffer_view(
            doc, view, elements=[], cfg=cfg, diag=diag, raster=None, elem_cache=None,
        )

    # Already off: no mutation attempted, reported as "unchanged".
    assert result["metadata"]["applied_show_shadows"] == "unchanged"
    assert view._show_shadows is False


class _FakeDisplayModelNoShadows(object):
    """A ViewDisplayModel with SmoothEdges but no ShowShadows attribute at
    all, standing in for an older Revit host where that member doesn't
    exist yet."""
    def __init__(self, smooth_edges):
        self.SmoothEdges = smooth_edges
        self.disposed = False

    def Dispose(self):
        self.disposed = True


class _FakeViewNoShadowsAttr(_FakeView):
    def GetViewDisplayModel(self):
        return _FakeDisplayModelNoShadows(self._smooth_edges)

    def SetViewDisplayModel(self, dm):
        self._smooth_edges = dm.SmoothEdges
        self.display_model_writes.append((dm.SmoothEdges, None))


def test_show_shadows_capture_preserves_unsupported_state_as_none(tmp_path):
    with _install_fake_revit_db():
        doc = _FakeDoc()
        view = _FakeViewNoShadowsAttr(view_id=44)
        cfg = Config()
        cfg.include_linked_rvt = False
        cfg.debug_dump_path = str(tmp_path)
        diag = _FakeDiag()

        result = color_id_buffer.export_color_id_buffer_view(
            doc, view, elements=[], cfg=cfg, diag=diag, raster=None, elem_cache=None,
        )

    # Unsupported (attribute missing entirely), not coerced to "already
    # off" -- must not be reported as an inspected, confirmed "unchanged"
    # the same way a real False would be silently indistinguishable from.
    assert result["metadata"]["applied_show_shadows"] == "unchanged"
    assert any(w.get("callsite") == "show_shadows_capture" for w in diag.warnings)
