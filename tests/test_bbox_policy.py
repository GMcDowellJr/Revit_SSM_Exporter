import pytest

from vop_interwoven.revit.collection import resolve_element_bbox


class _BBox:
    class _P:
        def __init__(self, x, y, z):
            self.X, self.Y, self.Z = x, y, z

    def __init__(self):
        self.Min = self._P(0, 0, 0)
        self.Max = self._P(1, 1, 1)


class ElemViewWins:
    def get_BoundingBox(self, view):
        if view is None:
            return None
        return _BBox()


class ElemModelOnly:
    def get_BoundingBox(self, view):
        if view is None:
            return _BBox()
        return None


class ElemThrows:
    def get_BoundingBox(self, view):
        raise RuntimeError("boom")


class FakeView:
    class _Id:
        IntegerValue = 123
    Id = _Id()


def test_resolve_element_bbox_prefers_view_bbox():
    bbox, src = resolve_element_bbox(ElemViewWins(), view=FakeView(), diag=None, context={"t": 1})
    assert bbox is not None
    assert src == "view"


def test_resolve_element_bbox_falls_back_to_model():
    bbox, src = resolve_element_bbox(ElemModelOnly(), view=FakeView(), diag=None, context={"t": 1})
    assert bbox is not None
    assert src == "model"


def test_resolve_element_bbox_returns_none_when_unavailable():
    bbox, src = resolve_element_bbox(ElemThrows(), view=FakeView(), diag=None, context={"t": 1})
    assert bbox is None
    assert src == "none"
