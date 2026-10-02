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
    fake.LinePatternElement = types.SimpleNamespace(GetSolidPatternId=lambda: _Id(SOLID))
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


# --- capture-state probe Q6: the style's OWN subcategory must be visible -----
#
# In 5823803 and 11999340 the view template hides "<Thin Lines>" -- the
# weight-1 style T1 always picked -- while OST_Lines stays visible. No tick
# drew in 31 views, and nothing said so: the only check was the parent.

class _View:
    def __init__(self, hidden=(), unreadable=()):
        self._hidden, self._unreadable = set(hidden), set(unreadable)

    def GetCategoryHidden(self, cat_id):
        if cat_id.IntegerValue in self._unreadable:
            raise RuntimeError("cannot read")
        return cat_id.IntegerValue in self._hidden


SOLID, DASH = -3000010, 7900


class _SubStyle(_Style):
    """A style whose GraphicsStyleCategory has its OWN id (sid + 100), and a
    projection line pattern (solid unless ``pattern`` says otherwise)."""

    def __init__(self, sid, name, weight, pattern=SOLID):
        _Style.__init__(self, sid, name, weight)
        self.GraphicsStyleCategory = types.SimpleNamespace(
            Id=_Id(sid + 100), GetLineWeight=self.GetLineWeight,
            GetLinePatternId=lambda _kind: _Id(pattern))


def _pick_in(view, styles, current):
    with _db():
        return _thinnest_line_style(_Doc(styles), _Curve(styles, current), view=view)


def test_a_hidden_thinnest_style_is_passed_over_for_the_thinnest_VISIBLE_one():
    lines, thin, wide = (_SubStyle(1, "Lines", 3), _SubStyle(2, "<Thin Lines>", 1),
                         _SubStyle(3, "Wide", 6))
    style, rec = _pick_in(_View(hidden={102}), [lines, thin, wide], current=lines)
    assert style is lines and rec["name"] == "Lines"
    assert rec["hidden"] == [{"id": 2, "name": "<Thin Lines>", "projection_line_weight": 1}]


def test_control_nothing_hidden_still_picks_thin_lines():
    lines, thin = _SubStyle(1, "Lines", 3), _SubStyle(2, "<Thin Lines>", 1)
    style, rec = _pick_in(_View(), [lines, thin], current=lines)
    assert style is thin and rec["hidden"] == []


def test_a_style_whose_hidden_state_will_not_read_is_not_a_candidate():
    lines, thin = _SubStyle(1, "Lines", 3), _SubStyle(2, "<Thin Lines>", 1)
    style, rec = _pick_in(_View(unreadable={102}), [lines, thin], current=lines)
    assert style is lines
    assert rec["hidden_unreadable"][0]["id"] == 2 and "cannot read" in rec[
        "hidden_unreadable"][0]["error"]


def test_every_style_hidden_is_unavailable_and_says_so():
    lines, thin = _SubStyle(1, "Lines", 3), _SubStyle(2, "<Thin Lines>", 1)
    style, rec = _pick_in(_View(hidden={101, 102}), [lines, thin], current=lines)
    assert style is None and rec["state"] == "unavailable"
    assert "hidden" in rec["reason"]



def test_a_dashed_style_is_passed_over_for_a_solid_one():
    """Q6 run 20261001T183711: A picked <Overhead> (dashed, weight 1) on
    5823803; its ticks changed 92 px where the solid temporary one changed
    141. Control: the same weight, solid, is chosen."""
    lines = _SubStyle(1, "Lines", 3)
    overhead = _SubStyle(2, "<Overhead>", 1, pattern=DASH)
    style, rec = _pick_in(_View(), [lines, overhead], current=lines)
    assert style is lines
    assert [e["name"] for e in rec["not_solid"]] == ["<Overhead>"]
    solid = _SubStyle(2, "<Overhead>", 1)
    style, _rec = _pick_in(_View(), [lines, solid], current=lines)
    assert style is solid


# --- Codex, PR #226: a failed read reaches Diagnostics -----------------------

class _Diag:
    def __init__(self):
        self.warnings = []

    def warn(self, **kw):
        self.warnings.append(kw)


def test_each_failed_style_read_is_one_warning_and_a_clean_read_none():
    """Mutation: dropping a _warn call turns this red."""
    lines = _SubStyle(1, "Lines", 3)
    bad_weight = _Style(4, "BadWeight", RuntimeError("no weight"))
    bad_pattern = _SubStyle(5, "BadPattern", 1)

    def _no_pattern(_kind):
        raise RuntimeError("no pattern")
    bad_pattern.GraphicsStyleCategory.GetLinePatternId = _no_pattern
    styles = [lines, bad_weight, _SubStyle(2, "Thin", 1), bad_pattern]
    diag = _Diag()
    with _db():
        _thinnest_line_style(_Doc(styles), _Curve(styles, lines),
                             view=_View(unreadable={102}), diag=diag, view_id=9)
    assert sorted(w["message"].split("its ")[1].split(" would")[0]
                  for w in diag.warnings) == ["hidden state", "line pattern", "weight"]
    assert all(w["view_id"] == 9 and w["callsite"] == "tick_line_style"
               for w in diag.warnings)
    clean = _Diag()
    with _db():
        _thinnest_line_style(_Doc([lines]), _Curve([lines], lines), view=_View(),
                             diag=clean, view_id=9)
    assert clean.warnings == []
