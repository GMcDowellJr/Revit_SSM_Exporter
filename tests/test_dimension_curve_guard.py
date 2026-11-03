"""C4: compute_annotation_extents' Dimension.Curve guard and log summary, and
the anno_expand_cap_in=None TypeError.

compute_annotation_extents is CONSUMED under Stage A (its result is
raster.anno_frame_bounds, which crop A is resolved inside), so it keeps
running and these fixes must leave its result unchanged:

* An unbound Dimension.Curve raised in GetEndPoint, was caught, and
  contributed no point. Skipping it on IsBound contributes no point either:
  ``test_an_unbound_curve_gives_the_same_bounds_guarded_or_not`` COMPOSES
  the two paths (a curve that has IsBound and one that does not) through the
  real function rather than restating either.
* The per-dimension print (pythonnet folds the .NET stack trace into str(e))
  is one summary line per view.
* cfg.anno_expand_cap_in defaults to None; float(None) raised a TypeError,
  diag.error'd on every view, and fell back to anno_expand_cap_cells. The
  fallback is kept and the error is gone.
"""
import contextlib
import sys
import types

from vop_interwoven.core.diagnostics import Diagnostics
from vop_interwoven.core.math_utils import Bounds2D
from vop_interwoven.revit import annotation


class _XYZ(object):
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = x, y, z


class _Dimension(object):
    def __init__(self, eid, curve, bbox=(0.0, 0.0, 1.0, 1.0)):
        self.Id = types.SimpleNamespace(IntegerValue=eid)
        self.Curve = curve
        self.TextPosition = None
        self._bbox = bbox

    def get_BoundingBox(self, _view):
        x0, y0, x1, y1 = self._bbox
        return types.SimpleNamespace(Min=_XYZ(x0, y0, 0.0), Max=_XYZ(x1, y1, 0.0),
                                     Transform=None)


class _UnboundWithFlag(object):
    """Revit's unbound Line: IsBound False, GetEndPoint raises."""
    IsBound = False

    def GetEndPoint(self, _i):
        raise ValueError("The input curve is not bound.\n   at Autodesk.Revit.DB...")


class _UnboundNoFlag(object):
    """The same curve as the unguarded code saw it: no IsBound to consult."""

    def GetEndPoint(self, _i):
        raise ValueError("The input curve is not bound.\n   at Autodesk.Revit.DB...")


class _Bound(object):
    IsBound = True

    def __init__(self, p0, p1):
        self._pts = (_XYZ(p0[0], p0[1], 0.0), _XYZ(p1[0], p1[1], 0.0))

    def GetEndPoint(self, i):
        return self._pts[i]


@contextlib.contextmanager
def _fake_db():
    db = types.ModuleType("Autodesk.Revit.DB")
    db.Dimension = _Dimension
    db.XYZ = _XYZ
    saved = sys.modules.get("Autodesk.Revit.DB")
    sys.modules["Autodesk.Revit.DB"] = db
    try:
        yield
    finally:
        if saved is None:
            sys.modules.pop("Autodesk.Revit.DB", None)
        else:
            sys.modules["Autodesk.Revit.DB"] = saved


def _extents(monkeypatch, dims, cfg=None, crop_active=False, diag=None):
    monkeypatch.setattr(annotation, "collect_2d_annotations",
                        lambda doc, view, diag=None: [(d, "DIM") for d in dims])
    monkeypatch.setattr(annotation, "is_extent_driver_annotation", lambda e: True)
    view = types.SimpleNamespace(Id=types.SimpleNamespace(IntegerValue=77), Scale=96,
                                 AnnotationCropActive=crop_active)
    basis = types.SimpleNamespace(transform_to_view_uv=lambda p: (p[0], p[1]))
    cfg = cfg or types.SimpleNamespace(anno_crop_margin_in=0.0, anno_expand_cap_cells=0,
                                       anno_expand_cap_in=None)
    with _fake_db():
        b = annotation.compute_annotation_extents(
            None, view, basis, Bounds2D(0.0, 0.0, 10.0, 10.0), 1.0, cfg=cfg, diag=diag)
    return None if b is None else (b.xmin, b.ymin, b.xmax, b.ymax)


def test_an_unbound_curve_gives_the_same_bounds_guarded_or_not(monkeypatch):
    bbox = (-5.0, -3.0, 12.0, 4.0)
    guarded = _extents(monkeypatch, [_Dimension(1, _UnboundWithFlag(), bbox)])
    unguarded = _extents(monkeypatch, [_Dimension(1, _UnboundNoFlag(), bbox)])
    assert guarded == unguarded == (-5.0, -3.0, 12.0, 10.0)


def test_control_a_bound_curve_still_contributes_its_end_points(monkeypatch):
    """The guard must not skip bound curves: their end points widen the frame."""
    got = _extents(monkeypatch, [_Dimension(1, _Bound((-20.0, 2.0), (30.0, 2.0)))])
    assert got == (-20.0, 0.0, 30.0, 10.0)


def test_one_summary_line_per_view_and_no_stack_trace(monkeypatch, capsys):
    dims = [_Dimension(i, _UnboundWithFlag()) for i in (11, 12, 13)]
    dims.append(_Dimension(14, _UnboundNoFlag()))
    _extents(monkeypatch, dims)
    lines = [l for l in capsys.readouterr().out.splitlines() if "Dimension.Curve" in l]
    assert len(lines) == 1, lines
    assert "view=77" in lines[0]
    assert "3 unbound" in lines[0] and "1 read failed" in lines[0]
    assert "first elem_id=11" in lines[0]
    assert "at Autodesk" not in lines[0]


def test_control_no_summary_when_every_curve_is_bound(monkeypatch, capsys):
    _extents(monkeypatch, [_Dimension(1, _Bound((1.0, 1.0), (2.0, 2.0)))])
    assert "Dimension.Curve" not in capsys.readouterr().out


def test_an_unset_cap_records_no_type_error_and_keeps_the_cells_fallback(monkeypatch):
    far = [_Dimension(1, _Bound((50.0, 50.0), (60.0, 60.0)), bbox=(50.0, 50.0, 60.0, 60.0))]
    unset = types.SimpleNamespace(anno_crop_margin_in=0.0, anno_expand_cap_cells=2,
                                  anno_expand_cap_in=None)
    diag = Diagnostics()
    got = _extents(monkeypatch, far, cfg=unset, crop_active=True, diag=diag)
    # cap = 2 printed inches at 1/96 = 16 ft beyond the base crop: the
    # fallback anno_expand_cap_cells, as before the fix.
    assert got == (0.0, 0.0, 26.0, 26.0)
    assert not [e for e in diag.to_dict().get("events", [])
                if "float()" in str(e.get("message", ""))], diag.to_dict()
    # The explicit value still wins over the fallback.
    explicit = types.SimpleNamespace(anno_crop_margin_in=0.0, anno_expand_cap_cells=2,
                                     anno_expand_cap_in=1.0)
    assert _extents(monkeypatch, far, cfg=explicit, crop_active=True) == (0.0, 0.0, 18.0, 18.0)
