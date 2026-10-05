"""B: a view that hides OST_Lines draws no tick in any line style; the
registered capture unhides Lines for the capture and hides, one by one, every
line that unhiding REVEALED -- so both captures show the view as authored.

Q6 run 20261001T183711, view 4284900: its template hides OST_Lines; the ticks
changed 0 px in every variant (template attached or detached); with the
template detached, Lines unhidden and the revealed lines hidden, 138 px.

Driven end to end through the registered capture; asserted at each export and
on the FILES. The fake view collector returns every element whatever the view
hides, so "which lines the view draws" is stood in for by a line_ids_in_view
that honours the Lines category, as Revit's collector does.

Mutations: skipping the unhide -> the first test red (lines hidden at export,
may_not_draw fault); hiding ALL lines rather than the revealed ones -> the
authored-line test red; dropping the revealed hide -> the first test red.
"""
import json

import pytest

import vop_interwoven.stage_a_registration as registration
from tests import test_probe_anno_pass_run_variant as world
from tests.stage_a_capture_fakes import FakeElement, FakeElementId
from tests.test_stage_a_annotation_pass_probe_switches import VIEW_ID
from tests.test_stage_a_registered_capture import _run

LINES = world.LINES_CAT.Id.IntegerValue
HIDDEN_LINE, SHOWN_LINE = 3101, 3102
TEMPLATE = 4284901


def _sidecar(path):
    with open(path) as handle:
        return json.load(handle)


def _faults(path):
    return [f["fault"] for f in _sidecar(path)["capture_integrity"]["capture_faults"]]


@pytest.fixture
def lines_world(monkeypatch):
    """A view whose template hides Lines, holding one line the category
    hides and, with ``shown``, one the view draws anyway (e.g. a line in a
    category-independent state). line_ids_in_view returns the lines the view
    draws: none while Lines is hidden, except SHOWN_LINE."""
    state = {"views": []}

    def _line_ids(doc, view):
        ids = [SHOWN_LINE] if state.get("shown") else []
        if not view.category_hidden.get(LINES, False):
            ids.append(HIDDEN_LINE)
        return sorted(set(ids))
    monkeypatch.setattr(registration, "line_ids_in_view", _line_ids)

    def _setup(view):
        view.category_hidden[LINES] = True
        object.__setattr__(view, "ViewTemplateId", FakeElementId(TEMPLATE))
        state["views"].append(view)
    state["setup"] = _setup
    return state


def _lines():
    return [FakeElement(HIDDEN_LINE, world.LINES_CAT, owner_view_id=VIEW_ID),
            FakeElement(SHOWN_LINE, world.LINES_CAT, owner_view_id=VIEW_ID)]


def test_a_view_hiding_lines_gets_its_ticks_and_keeps_its_lines_hidden(tmp_path, lines_world):
    out, view, _doc, exports, _diag = _run(tmp_path, view_setup=lines_world["setup"],
                                           extra_elements=_lines())
    for exp in exports:
        assert exp["lines_hidden"] is False, exp            # the ticks can draw
        assert HIDDEN_LINE in exp["hidden_elements"], exp   # the author's hidden line is not
    rec = _sidecar(out["sidecar_path"])["registration_marks"]["lines_unhidden"]
    assert rec["state"] == "value" and rec["lines_category_hidden_before"] is True
    assert rec["lines_category_hidden_after"] is False
    assert rec["template_detach"]["orig_view_template_id"] == TEMPLATE
    assert rec["template_detach"]["detached"] is True
    assert (rec["lines_before"], rec["revealed"], rec["revealed_hidden"]) == (0, 1, 1)
    for path in (out["sidecar_path"], out["annotation_sidecar_path"]):
        assert "registration_marks_may_not_draw" not in _faults(path), path
    assert out["registration"]["success"] is True
    # The rollback puts it all back.
    assert view.category_hidden[LINES] is True
    assert int(view.ViewTemplateId.IntegerValue) == TEMPLATE
    assert HIDDEN_LINE not in view.hidden_elements


def test_a_line_the_view_already_drew_is_not_hidden(tmp_path, lines_world):
    lines_world["shown"] = True
    out, _view, _doc, exports, _diag = _run(tmp_path, view_setup=lines_world["setup"],
                                            extra_elements=_lines())
    rec = out["registration"]["lines_unhidden"]
    assert (rec["lines_before"], rec["revealed"]) == (1, 1)
    # The model pass hides the view's detail lines itself; the ANNOTATION
    # capture, where they belong, must still draw the one the author showed.
    assert SHOWN_LINE not in exports[1]["hidden_elements"]
    assert HIDDEN_LINE in exports[1]["hidden_elements"]


def test_control_a_view_that_draws_lines_is_not_touched(tmp_path):
    out, _view, _doc, _exports, _diag = _run(tmp_path)
    rec = out["registration"]["lines_unhidden"]
    assert rec == {"lines_category_hidden_before": False, "state": "not_needed"}
    assert not [e for e in world._LOG if e[0] == "tx_start" and "unhide Lines" in e[1]]


def test_lines_that_will_not_unhide_are_a_fault_in_the_FILES(tmp_path, lines_world, monkeypatch):
    monkeypatch.setattr(registration, "set_lines_category_hidden",
                        lambda view, hidden: (_ for _ in ()).throw(
                            RuntimeError("Category cannot be hidden.")))
    out, _view, _doc, _exports, _diag = _run(tmp_path, view_setup=lines_world["setup"],
                                             extra_elements=_lines())
    assert out["registration"]["lines_unhidden"]["state"] == "failed"
    for path in (out["sidecar_path"], out["annotation_sidecar_path"]):
        faults = _faults(path)
        assert "lines_unhide_failed" in faults and "registration_marks_may_not_draw" in faults
    assert out["registration"]["success"] is False


def test_a_revealed_line_that_will_not_hide_is_a_fault(tmp_path, lines_world, monkeypatch):
    real = registration.hide_in_view

    def _refuse(doc, view, ids):
        if HIDDEN_LINE in ids:
            rec = real(doc, view, [])
            rec["error"] = "HideElements raised RuntimeError: refused"
            return rec
        return real(doc, view, ids)
    monkeypatch.setattr(registration, "hide_in_view", _refuse)
    out, _view, _doc, _exports, _diag = _run(tmp_path, view_setup=lines_world["setup"],
                                             extra_elements=_lines())
    assert "revealed_lines_not_hidden" in _faults(out["sidecar_path"])
    assert out["registration"]["success"] is False
