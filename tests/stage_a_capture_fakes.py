"""Shared fake Autodesk.Revit.DB surface for Stage A capture tests.

Same technique as tests/test_color_id_buffer_shadow_suppression.py and
tests/test_color_id_buffer_link_category_filters.py -- install a fake
``Autodesk.Revit.DB`` into ``sys.modules`` so
``color_id_buffer.export_color_id_buffer_view`` can run its whole
suppress/export/restore lifecycle outside Revit.

It lives in its own module, rather than being copied into each test file,
because two separate concerns now need the SAME view surface: the
neutral-phase-swap gate and the per-view graphics-state record. A second copy
would drift, and a fake that drifts from the one production is tested against
is worse than no fake at all.

Deliberately richer than the shadow-suppression fake in three places, each of
which a test here actually reaches:

  - ``XYZ``/``BoundingBoxXYZ`` are present, so ``crop_box_from_uv_bounds()``
    resolves and the crop path is REACHED rather than raising ImportError and
    silently leaving every test running with ``bounds_xy=None``. That exact
    gap is recorded in CLAUDE.md as a test that could not reach the code it
    named; ``test_fake_surface_reaches_the_crop_path`` pins it.
  - ``ViewPlan`` exists as a real class, so a plan-view fake takes the
    underlay branch and a non-plan fake takes "not_applicable".
  - Views carry readable phase, phase-filter, filter, category-override,
    template, detail-level and display-style state.
"""
import contextlib
import os
import sys
import types


class FakeElementId(object):
    def __init__(self, v):
        self.IntegerValue = int(v)

    def __eq__(self, other):
        return isinstance(other, FakeElementId) and other.IntegerValue == self.IntegerValue

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.IntegerValue)

    def __repr__(self):
        return "ElementId({0})".format(self.IntegerValue)


INVALID_ELEMENT_ID = FakeElementId(-1)
FakeElementId.InvalidElementId = INVALID_ELEMENT_ID


class FakeCategory(object):
    def __init__(self, name, cat_id, cat_type="Model"):
        self.Name = name
        self.Id = FakeElementId(cat_id)
        self.CategoryType = cat_type


class FakeElement(object):
    def __init__(self, elem_id, category=None, name=None):
        self.Id = FakeElementId(elem_id)
        self.Category = category
        if name is not None:
            self.Name = name

    def __repr__(self):
        return "FakeElement({0})".format(self.Id.IntegerValue)


class FakeColor(object):
    def __init__(self, r, g, b):
        self.r, self.g, self.b = r, g, b


class FakeOGS(object):
    def __init__(self):
        self.Halftone = False
        self.calls = {}

    def SetHalftone(self, value):
        self.Halftone = value
        self.calls["SetHalftone"] = (value,)

    def __getattr__(self, name):
        def _record(*args):
            self.calls[name] = args
        return _record


class FakeTransaction(object):
    def __init__(self, doc, name):
        self.doc = doc
        self.name = name

    def Start(self):
        pass

    def Commit(self):
        pass

    def RollBack(self):
        pass


class FakeTransform(object):
    Identity = object()


class FakeFillPattern(object):
    def __init__(self, is_solid, target):
        self.IsSolidFill = is_solid
        self.Target = target


class FakeFillPatternElement(object):
    def __init__(self, pattern_id, pattern):
        self.Id = FakeElementId(pattern_id)
        self._pattern = pattern

    def GetFillPattern(self):
        return self._pattern


class FakeFillPatternTarget(object):
    Drafting = "Drafting"
    Model = "Model"


class FakePhaseFilter(object):
    def __init__(self, name, filter_id):
        self.Name = name
        self.Id = FakeElementId(filter_id)
        self.status_presentation = {}

    def SetPhaseStatusPresentation(self, status, presentation):
        self.status_presentation[status] = presentation

    def GetPhaseStatusPresentation(self, status):
        return self.status_presentation.get(status, "ByCategory")

    @classmethod
    def Create(cls, doc, name):
        pf = cls(name, 5000 + len(doc.phase_filters))
        doc.phase_filters.append(pf)
        doc.register(pf)
        return pf


class FakeElementOnPhaseStatus(object):
    New = "New"
    Existing = "Existing"
    Demolished = "Demolished"
    Temporary = "Temporary"


class FakePhaseStatusPresentation(object):
    ShowByCategory = "ShowByCategory"


class FakeCategoryType(object):
    Model = "Model"
    Annotation = "Annotation"


class FakeBuiltInParameter(object):
    VIEW_PHASE = "VIEW_PHASE"
    VIEW_PHASE_FILTER = "VIEW_PHASE_FILTER"


class FakeBuiltInCategory(object):
    pass


class FakeImageFileType(object):
    TIFF = "TIFF"


class FakeImageExportOptions(object):
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


class FakeExportRange(object):
    SetOfViews = "SetOfViews"


class FakeZoomFitType(object):
    FitToPage = "FitToPage"


class FakeFitDirectionType(object):
    Horizontal = "Horizontal"
    Vertical = "Vertical"


class FakeDisplayStyle(object):
    FlatColors = "FlatColors"
    Shading = "Shading"


class FakeGenericList(list):
    def Add(self, item):
        self.append(item)


class FakeListFactory(object):
    def __getitem__(self, _item_type):
        return FakeGenericList


class FakeXYZ(object):
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = float(x), float(y), float(z)


class FakeBoundingBoxXYZ(object):
    def __init__(self):
        self.Min = FakeXYZ(0, 0, 0)
        self.Max = FakeXYZ(1, 1, 1)
        self.Transform = None


class FakeRevitLinkInstance(object):
    def __init__(self, link_id, name):
        self.Id = FakeElementId(link_id)
        self.Name = name


class FakeParameterFilterElement(object):
    pass


class FakeGroup(object):
    pass


class FakeFamilyInstance(object):
    pass


class FakeRevitLinkGraphicsSettings(object):
    def __init__(self, halftone):
        self.Halftone = halftone


class FakeParameter(object):
    def __init__(self, element_id, read_only=False):
        self._element_id = element_id
        self.IsReadOnly = read_only
        self.set_calls = []

    def AsElementId(self):
        return self._element_id

    def Set(self, value):
        self.set_calls.append(value)
        self._element_id = value
        return True


class FakeCollector(object):
    def __init__(self, source, _view_id=None):
        self._source = source

    def OfClass(self, cls):
        if cls is FakePhaseFilter:
            return list(getattr(self._source, "phase_filters", []))
        if cls is FakeFillPatternElement:
            return list(getattr(self._source, "fill_patterns", []))
        if cls is FakeRevitLinkInstance:
            return list(getattr(self._source, "link_instances", []))
        return []

    def WhereElementIsNotElementType(self):
        return []


class FakeDoc(object):
    def __init__(self, elements=None, link_instances=None, categories=None):
        self.phase_filters = []
        self.fill_patterns = [
            FakeFillPatternElement(700, FakeFillPattern(True, FakeFillPatternTarget.Drafting)),
        ]
        self.link_instances = list(link_instances or [])
        self.deleted_ids = []
        self.export_image_calls = []
        self.on_export_image = None
        self._by_id = {}
        for elem in (elements or []):
            self.register(elem)
        for link in self.link_instances:
            self.register(link)

        settings = types.SimpleNamespace()
        settings.Categories = list(categories or [])
        self.Settings = settings

    def register(self, element):
        self._by_id[int(element.Id.IntegerValue)] = element
        return element

    def GetElement(self, eid):
        return self._by_id.get(int(eid.IntegerValue))

    def Delete(self, eid):
        self.deleted_ids.append(int(eid.IntegerValue))

    def ExportImage(self, opts):
        self.export_image_calls.append(opts)
        if self.on_export_image is not None:
            self.on_export_image(opts)
        out_dir = os.path.dirname(opts.FilePath)
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        with open(opts.FilePath + ".tiff", "wb") as f:
            f.write(b"FAKE_TIFF")


class FakeDisplayModel(object):
    def __init__(self, smooth_edges, show_shadows):
        self.SmoothEdges = smooth_edges
        self.ShowShadows = show_shadows

    def Dispose(self):
        pass


class FakeView(object):
    """A view with readable graphics state.

    Everything the graphics-state record reads is a plain attribute here, so a
    test can make any single read FAIL (assign a property that raises) without
    disturbing the others.
    """

    def __init__(self, view_id, name="TestView"):
        self.Id = FakeElementId(view_id)
        self.Name = name
        self.ViewTemplateId = INVALID_ELEMENT_ID
        self.DetailLevel = "Fine"
        self.DisplayStyle = "Wireframe"
        self.CropBox = FakeBoundingBoxXYZ()
        self.CropBoxActive = True
        self._smooth_edges = False
        self._show_shadows = False

        self.params = {}
        self.filters = []
        self.filter_enabled = {}
        self.filter_visibility = {}
        self.filter_overrides = {}
        self.category_overrides = {}
        self.category_hidden = {}
        self.element_overrides = {}
        self.link_overrides = {}

        self.crop_box_writes = []
        self.set_category_hidden_calls = []

    # --- parameters ---
    def get_Parameter(self, bip):
        return self.params.get(bip)

    # --- filters ---
    def GetFilters(self):
        return list(self.filters)

    def GetIsFilterEnabled(self, fid):
        return self.filter_enabled.get(int(fid.IntegerValue), True)

    def SetIsFilterEnabled(self, fid, value):
        self.filter_enabled[int(fid.IntegerValue)] = value

    def GetFilterVisibility(self, fid):
        return self.filter_visibility.get(int(fid.IntegerValue), True)

    def SetFilterVisibility(self, fid, value):
        self.filter_visibility[int(fid.IntegerValue)] = value

    def GetFilterOverrides(self, fid):
        return self.filter_overrides.setdefault(int(fid.IntegerValue), FakeOGS())

    # --- categories ---
    def GetCategoryOverrides(self, cat_id):
        return self.category_overrides.setdefault(int(cat_id.IntegerValue), FakeOGS())

    def SetCategoryOverrides(self, cat_id, ogs):
        self.category_overrides[int(cat_id.IntegerValue)] = ogs

    def CanCategoryBeHidden(self, cat_id):
        return True

    def GetCategoryHidden(self, cat_id):
        return self.category_hidden.get(int(cat_id.IntegerValue), False)

    def SetCategoryHidden(self, cat_id, value):
        self.set_category_hidden_calls.append((int(cat_id.IntegerValue), value))
        self.category_hidden[int(cat_id.IntegerValue)] = value

    # --- element / link overrides ---
    def GetElementOverrides(self, eid):
        return self.element_overrides.setdefault(int(eid.IntegerValue), FakeOGS())

    def SetElementOverrides(self, eid, ogs):
        self.element_overrides[int(eid.IntegerValue)] = ogs

    def GetLinkOverrides(self, link_id):
        return self.link_overrides.get(int(link_id.IntegerValue))

    # --- display model ---
    def GetViewDisplayModel(self):
        return FakeDisplayModel(self._smooth_edges, self._show_shadows)

    def SetViewDisplayModel(self, dm):
        self._smooth_edges = dm.SmoothEdges
        self._show_shadows = dm.ShowShadows


class FakeViewPlan(FakeView):
    """A plan view, so the underlay branch is reachable."""

    def __init__(self, view_id, name="TestPlan"):
        FakeView.__init__(self, view_id, name)
        self.underlay_base = INVALID_ELEMENT_ID
        self.underlay_top = INVALID_ELEMENT_ID

    def GetUnderlayBaseLevel(self):
        return self.underlay_base

    def GetUnderlayTopLevel(self):
        return self.underlay_top


class FakeDiag(object):
    def __init__(self):
        self.warnings = []
        self.errors = []
        self.infos = []

    def warn(self, **kwargs):
        self.warnings.append(kwargs)

    def error(self, **kwargs):
        self.errors.append(kwargs)

    def info(self, **kwargs):
        self.infos.append(kwargs)


@contextlib.contextmanager
def install_fake_revit_db():
    fake_db = types.ModuleType("Autodesk.Revit.DB")
    fake_db.ElementId = FakeElementId
    fake_db.Color = FakeColor
    fake_db.OverrideGraphicSettings = FakeOGS
    fake_db.Transaction = FakeTransaction
    fake_db.Transform = FakeTransform
    fake_db.FilteredElementCollector = FakeCollector
    fake_db.FillPatternElement = FakeFillPatternElement
    fake_db.FillPatternTarget = FakeFillPatternTarget
    fake_db.PhaseFilter = FakePhaseFilter
    fake_db.ElementOnPhaseStatus = FakeElementOnPhaseStatus
    fake_db.PhaseStatusPresentation = FakePhaseStatusPresentation
    fake_db.CategoryType = FakeCategoryType
    fake_db.BuiltInParameter = FakeBuiltInParameter
    fake_db.BuiltInCategory = FakeBuiltInCategory
    fake_db.ImageFileType = FakeImageFileType
    fake_db.ImageExportOptions = FakeImageExportOptions
    fake_db.ExportRange = FakeExportRange
    fake_db.ZoomFitType = FakeZoomFitType
    fake_db.FitDirectionType = FakeFitDirectionType
    fake_db.DisplayStyle = FakeDisplayStyle
    fake_db.RevitLinkInstance = FakeRevitLinkInstance
    fake_db.ParameterFilterElement = FakeParameterFilterElement
    fake_db.Group = FakeGroup
    fake_db.FamilyInstance = FakeFamilyInstance
    fake_db.ViewPlan = FakeViewPlan
    fake_db.XYZ = FakeXYZ
    fake_db.BoundingBoxXYZ = FakeBoundingBoxXYZ

    fake_system = types.ModuleType("System")
    fake_system_collections = types.ModuleType("System.Collections")
    fake_scg = types.ModuleType("System.Collections.Generic")
    fake_scg.List = FakeListFactory()

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
