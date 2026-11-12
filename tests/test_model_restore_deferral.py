"""R1 (2026-11-11): the registered capture leaves the model members'
element-override restore to the attempt's TransactionGroup rollback.

Driven through production's ``export_registered_stage_a_view`` on the fake DB
whose groups genuinely roll view and document state back
(tests/test_probe_anno_pass_run_variant.py). The comparison is against the
SAME capture with the deferral switched off (the probe-only
``color_id_buffer_model_restore_deferral``, getattr, absent from Config): the
in-pass restore every capture ran before R1.

What is asserted, and why each is the gate it stands for:

* the WHOLE view state each export saw -- every element and category
  override, hidden category, hidden element, crop, display style, template --
  is identical with and without the deferral (the fake-world half of gate 1:
  a pixel can only differ if the state an export renders differs);
* the post-rollback view equals the pre-capture view, an AUTHORED override on
  a model member included (gate 2);
* the restore transaction writes no model member's override (the saving
  itself; a deferral that still wrote them would pass every equality above);
* a caller outside the registered capture still restores in the pass
  (gate 3), and so does a painted element that is not a model member.

Mutations wired to these tests (gate 7a): dropping the category-hidden
restore under the deferral turns the export-state test red (the annotation
pass's M1 gate then sees every annotation category hidden); deferring every
painted id rather than the model members turns the non-member test red.
"""
import copy
import json

import pytest

from vop_interwoven import color_id_buffer
from vop_interwoven.config import Config

from tests import test_probe_anno_pass_run_variant as world
from tests import test_stage_a_registered_capture as rc
from tests.stage_a_capture_fakes import (
    FakeDiag, FakeViewPlan, install_fake_revit_db,
)
from tests.test_stage_a_annotation_pass_probe_switches import (
    MODEL_CAT, VIEW_ID, _SizedDoc, _elements, _raster,
)

RESTORE_TX = "VOP Stage A RESTORE color ID buffer"
AUTHORED_ID = 1003        # a model member the fixture gives an authored override


def _plain(value, depth=0, seen=None):
    """A comparable, json-able copy of fake Revit state, by value."""
    seen = set() if seen is None else seen
    if depth > 6:
        return "<deep>"
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return dict((str(k), _plain(v, depth + 1, seen)) for k, v in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_plain(v, depth + 1, seen) for v in value]
        return sorted(items, key=repr) if isinstance(value, (set, frozenset)) else items
    if id(value) in seen:
        return "<cycle>"
    if isinstance(getattr(value, "IntegerValue", None), int):
        return {"IntegerValue": int(value.IntegerValue)}
    if callable(value):
        return "<callable>"
    if hasattr(value, "__dict__"):
        seen = seen | {id(value)}
        return {"__type__": type(value).__name__,
                "fields": dict((k, _plain(v, depth + 1, seen))
                               for k, v in sorted(vars(value).items())
                               # FakeOGS's call recorder is history,
                               # not state: Revit has no such field.
                               if not k.startswith("_") and k != "calls")}
    return repr(value)


def _view_state(view):
    return _plain(dict((k, v) for k, v in vars(view).items() if not k.startswith("_")))


def _capture(tmp_path, monkeypatch, deferral=True, authored=True):
    """One registered capture; returns (out, view, per-export view states)."""
    states = []
    holder = {}

    def _view_setup(view):
        holder["view"] = view
        if authored:
            # An AUTHORED override on a model member: the rollback, not the
            # pass, must put it back.
            from tests.stage_a_capture_fakes import FakeColor, FakeOGS
            authored_ogs = FakeOGS()
            authored_ogs.SetProjectionLineColor(FakeColor(10, 20, 30))
            view.element_overrides[AUTHORED_ID] = authored_ogs

    real_export = _SizedDoc.ExportImage

    def _export(doc, opts):
        states.append(copy.deepcopy(_view_state(holder["view"])))
        return real_export(doc, opts)

    monkeypatch.setattr(_SizedDoc, "ExportImage", _export)

    def _cfg(cfg):
        if not deferral:
            cfg.color_id_buffer_model_restore_deferral = False

    out, view, doc, exports, diag = rc._run(
        tmp_path, view_setup=_view_setup, cfg_setup=_cfg)
    return out, view, states, holder


def _sidecar(path):
    with open(path) as handle:
        return json.load(handle)


def _without(record, *keys):
    return dict((k, v) for k, v in record.items() if k not in keys)


def test_every_export_sees_the_same_view_with_and_without_the_deferral(
        tmp_path, monkeypatch):
    out_on, _v1, on, _h1 = _capture(tmp_path / "on", monkeypatch, deferral=True)
    monkeypatch.undo()
    out_off, _v2, off, _h2 = _capture(tmp_path / "off", monkeypatch, deferral=False)
    assert out_on["success"] is True and out_off["success"] is True
    assert len(on) == len(off) == 2          # the model export, the annotation export
    assert on == off


def test_the_sidecars_differ_only_in_the_restore_bookkeeping(tmp_path, monkeypatch):
    out_on, _v1, _on, _h1 = _capture(tmp_path / "on", monkeypatch, deferral=True)
    monkeypatch.undo()
    out_off, _v2, _off, _h2 = _capture(tmp_path / "off", monkeypatch, deferral=False)
    volatile = ("timings", "timings_ms", "captured_at", "process_memory", "paths",
                "tiff_path", "sidecar_path", "json_path")

    def _scrub(value):
        if isinstance(value, dict):
            return dict((k, _scrub(v)) for k, v in value.items()
                        if k not in volatile and k != "ms" and "_ms" not in k
                        and "path" not in k)
        if isinstance(value, list):
            return [_scrub(v) for v in value]
        return value

    for key in ("sidecar_path", "annotation_sidecar_path"):
        a = _scrub(_sidecar(out_on[key]))
        b = _scrub(_sidecar(out_off[key]))
        if key == "sidecar_path":
            assert a["model_restore"]["state"] == "deferred_to_group_rollback"
            assert b["model_restore"]["state"] == "in_pass"
            a, b = _without(a, "model_restore"), _without(b, "model_restore")
        assert a == b, key


def test_the_restore_transaction_writes_no_model_members_override(tmp_path, monkeypatch):
    out, view, _states, _h = _capture(tmp_path, monkeypatch, deferral=True)
    members = set(_model_member_ids(out))
    in_restore = [eid for entry in world._LOG
                  if entry[0] == "override" and entry[2] == RESTORE_TX
                  for eid in [entry[1]]]
    assert members and not (set(in_restore) & members), in_restore
    restore = _sidecar(out["sidecar_path"])["model_restore"]
    assert restore["deferred"] == ["element_overrides"]
    assert restore["deferred_element_overrides"] >= 1
    # CONTROL: without the deferral the same transaction resets them.
    monkeypatch.undo()
    out_off, _v, _s, _h2 = _capture(tmp_path / "off", monkeypatch, deferral=False)
    in_restore_off = [entry[1] for entry in world._LOG
                      if entry[0] == "override" and entry[2] == RESTORE_TX]
    assert set(in_restore_off) & members
    assert _sidecar(out_off["sidecar_path"])["model_restore"][
        "deferred_element_overrides"] == 0


def _model_member_ids(out):
    # What the model pass actually painted, read from its FILE rather than
    # restating the membership split here.
    model = _sidecar(out["sidecar_path"])
    return [int(k) for k in (model.get("color_assignment_map") or {})]


def test_the_rollback_puts_back_the_view_authored_override_included(tmp_path, monkeypatch):
    out, view, _states, holder = _capture(tmp_path, monkeypatch, deferral=True)
    reg = out["registration"]
    assert all(v["status"] == "restored" for v in reg["restore"]["view_state"].values())
    assert reg["restore"]["element_overrides"]["left_behind"] == []
    assert world._colour_of(view.element_overrides[AUTHORED_ID]) == (10, 20, 30)
    # Nothing else carries an override after the capture.
    assert [i for i, ogs in view.element_overrides.items()
            if i != AUTHORED_ID and world._colour_of(ogs) is not None] == []


def _direct_model_pass(tmp_path, restore_under_rollback=None, extra=()):
    elements = _elements() + list(extra)
    doc = _SizedDoc(elements=elements, link_instances=[],
                    categories=[MODEL_CAT])
    view = FakeViewPlan(view_id=VIEW_ID)
    cfg = Config()
    cfg.include_linked_rvt = False
    cfg.debug_dump_path = str(tmp_path)
    with install_fake_revit_db():
        result = color_id_buffer.export_color_id_buffer_view(
            doc, view, elements=elements, cfg=cfg, diag=FakeDiag(),
            raster=_raster(), elem_cache=None,
            restore_under_rollback=restore_under_rollback)
    return result, view, elements


def test_a_caller_outside_a_rolled_back_group_still_restores_in_the_pass(tmp_path):
    """Gate 3: no group, no deferral -- every painted override is reset."""
    result, view, elements = _direct_model_pass(tmp_path)
    assert result["success"] is True
    painted = [int(k) for k in _sidecar(result["sidecar_path"])["color_assignment_map"]]
    assert painted
    assert [i for i in painted if world._colour_of(view.element_overrides.get(i)) is not None] == []
    restore = _sidecar(result["sidecar_path"])["model_restore"]
    assert restore == {"state": "in_pass", "deferred": [],
                       "restored_element_overrides": len(painted),
                       "deferred_element_overrides": 0}


def test_a_painted_element_outside_the_deferred_set_is_still_reset(tmp_path):
    result, view, elements = _direct_model_pass(tmp_path / "a")
    painted = sorted(int(k) for k in
                     _sidecar(result["sidecar_path"])["color_assignment_map"])
    assert len(painted) >= 2
    deferred, kept = painted[:1], painted[1:]
    result, view, elements = _direct_model_pass(tmp_path / "b",
                                                restore_under_rollback=deferred)
    # The deferred member keeps its paint (its group's rollback is its
    # restore); every other painted element is reset here.
    assert world._colour_of(view.element_overrides.get(deferred[0])) is not None
    assert [i for i in kept
            if world._colour_of(view.element_overrides.get(i)) is not None] == []
    restore = _sidecar(result["sidecar_path"])["model_restore"]
    assert restore["deferred_element_overrides"] == 1
    assert restore["restored_element_overrides"] == len(kept)
