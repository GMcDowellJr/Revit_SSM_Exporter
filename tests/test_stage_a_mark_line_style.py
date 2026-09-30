"""T1: the registration ticks take the THINNEST line style their detail curve
allows. No override in either pass sets a weight, so the style's projection
weight is what the ticks draw at; before T1 it was whatever the document's
default detail line style happened to be, and nothing recorded it."""
import contextlib
import sys
import types

from vop_interwoven.stage_a_registration import _thinnest_line_style


class _Id:
    def __init__(self, v):
        self.IntegerValue = v


class _Style:
    def __init__(self, sid, name, weight):
        self.Id, self.Name = _Id(sid), name
        self._w = weight
        self.GraphicsStyleCategory = self

    def GetLineWeight(self, _kind):
        if isinstance(self._w, Exception):
            raise self._w
        return self._w


class _Doc:
    def __init__(self, styles):
        self._s = {s.Id.IntegerValue: s for s in styles}

    def GetElement(self, eid):
        return self._s[eid.IntegerValue]


class _Curve:
    def __init__(self, styles, current):
        self._ids = [s.Id for s in styles]
        self.LineStyle = current

    def GetLineStyleIds(self):
        return self._ids


@contextlib.contextmanager
def _db():
    fake = types.ModuleType("Autodesk.Revit.DB")
    fake.GraphicsStyleType = types.SimpleNamespace(Projection="Projection")
    saved = sys.modules.get("Autodesk.Revit.DB")
    sys.modules["Autodesk.Revit.DB"] = fake
    try:
        yield
    finally:
        if saved is None:
            sys.modules.pop("Autodesk.Revit.DB", None)
        else:
            sys.modules["Autodesk.Revit.DB"] = saved


def _pick(styles, current):
    with _db():
        return _thinnest_line_style(_Doc(styles), _Curve(styles, current))


def test_the_thinnest_style_is_chosen_and_the_default_is_recorded():
    lines, thin, wide = _Style(1, "Lines", 3), _Style(2, "Thin Lines", 1), _Style(3, "Wide", 6)
    style, rec = _pick([lines, thin, wide], current=lines)
    assert style is thin
    assert rec["projection_line_weight"] == 1 and rec["name"] == "Thin Lines"
    assert rec["default_style"] == {"id": 1, "name": "Lines", "projection_line_weight": 3}


def test_a_tie_keeps_the_current_style():
    a, b = _Style(1, "A", 1), _Style(2, "B", 1)
    style, _rec = _pick([a, b], current=b)
    assert style is b


def test_an_unreadable_weight_is_named_not_assumed_thin():
    bad, ok = _Style(1, "Bad", RuntimeError("no weight")), _Style(2, "Ok", 2)
    style, rec = _pick([bad, ok], current=ok)
    assert style is ok
    assert rec["unreadable"][0]["id"] == 1 and "no weight" in rec["unreadable"][0]["error"]


def test_nothing_readable_is_unavailable():
    bad = _Style(1, "Bad", RuntimeError("no weight"))
    style, rec = _pick([bad], current=bad)
    assert style is None and rec["state"] == "unavailable" and rec["reason"]
