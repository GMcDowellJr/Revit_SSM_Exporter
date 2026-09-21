"""Both directions of one claim: a Stage A view does not enter the geometry model pass.

WHAT THIS PINS
--------------
``pipeline.process_document_views``' per-view loop, when
``cfg.enable_color_id_buffer_stage_a`` is set, takes the color-ID branch and
``continue``s -- so ``render_model_front_to_back`` is never called, and with it
neither ``_classify_uv_rect`` (N1) nor ``_scene_occ_rects`` (N3), both of which
are nested inside that function. Everything after the branch in that loop body
is skipped too, which is why docs/CARRY_FORWARD_LEDGER.md marks most of its R/N/A
rows "arbiter path only".

Before this file that claim was read off control flow and asserted nowhere.

WHY IT IS TWO TESTS AND NOT ONE, AND WHY THE ORDER MATTERS
----------------------------------------------------------
The tempting version of this test is one assertion: "no geometry work happens on
a Stage A run". Built on absence -- a missing timing key, an empty recorder -- it
is satisfied by *unreached*, by *renamed*, and by *never-emitted-at-all*, alike.
All three look identical from inside the assertion, so it passes forever and
proves nothing the day the thing it watches stops existing. That is the same
uniformity trap CLAUDE.md records defeating three successive versions of
``--reconcile``, this time inside a test instead of a validator.

So the empty direction is worthless without its opposite. ``test_control_*``
below asserts the recorder is NON-empty on the geometry path over the same
fixture, and it is the one that fails when the Stage A direction is lying: if the
fake never gets far enough to reach either call, the Stage A assertion passes
vacuously and the control does not. Read a failure in the control as "this file
has stopped testing anything", not as "the geometry path regressed".

Two further properties make the pair fail rather than pass quietly:

- **Rename.** The recorder is installed with ``monkeypatch.setattr(pipeline,
  "render_model_front_to_back", ...)``, which raises ``AttributeError`` when the
  name is gone. The recorder therefore watches *entry into the call site of a
  named function*, not the presence of a telemetry key a rename would orphan.
- **Same input.** ``test_the_two_runs_differ_only_in_the_flag`` diffs the two
  ``Config.to_dict()``s and requires the single differing key to be
  ``enable_color_id_buffer_stage_a``. Without it, "over the same fixture" is a
  claim about how the helper below was written, which nothing checks.

PROVEN AGAINST, by mutating production rather than the test
----------------------------------------------------------
Both mutations were applied to ``vop_interwoven/pipeline.py`` at ``d05038a``, run,
and reverted. Neither was reasoned about.

1. **Delete the ``continue``** at the tail of the Stage A branch, so it falls
   through into the model pass -- the exact defect this file exists to catch.
   ``test_stage_a_view_does_not_enter_the_model_pass`` goes RED with
   ``assert [4242] == []``; the control and the config differ stay GREEN. This is
   the shape a failure should have: one test, naming the branch.
2. **Rename ``render_model_front_to_back``** (definition and call site together,
   as a refactor would). BOTH runtime tests go RED with ``AttributeError`` from
   ``monkeypatch.setattr``. That is the property the "absence" version of this
   test lacks: when the watched thing stops existing, this file fails instead of
   passing greenly over nothing.

Unmutated, all three pass, and the suite goes 1111 -> 1114.

WHAT IS FAKED, AND WHAT IS NOT
------------------------------
Not faked, and this is the point: the branch, its condition, the ``continue``,
and the ``render_model_front_to_back`` call site are all production code, and
``resolve_view_mode`` is the real capability-based one -- a plain namespace view
satisfies it, so the mode decision is not stubbed into agreeing.

Faked: ``Autodesk.Revit.DB.ElementId`` (the loop's only Revit API use before the
branch), and the three heavy steps on either side of it -- ``init_view_raster``,
``collect_view_elements``, ``rasterize_annotations`` -- plus
``export_color_id_buffer_view``, which would otherwise drive a real Revit export.
Stubbing those cannot make this test pass wrongly: they sit upstream of and
around the branch, and if they stubbed it into never being reached, the control
fails.
"""
import contextlib
import sys
import types

import pytest

from vop_interwoven import pipeline
from vop_interwoven.config import Config


class _FakeElementId:
    def __init__(self, v):
        self.IntegerValue = int(v)

    def __eq__(self, other):
        return isinstance(other, _FakeElementId) and other.IntegerValue == self.IntegerValue

    def __hash__(self):
        return hash(self.IntegerValue)


class _FakeViewType:
    """Stands in for Revit's ViewType enum member well enough for _view_type_name."""

    def __init__(self, name):
        self._name = name

    def __str__(self):
        return self._name

    def ToString(self):
        return self._name


VIEW_ID = 4242


def _fake_view():
    """A view the REAL resolve_view_mode() classifies MODEL_AND_ANNOTATION."""
    return types.SimpleNamespace(
        Id=_FakeElementId(VIEW_ID),
        Name="L1 Plan",
        IsTemplate=False,
        ViewType=_FakeViewType("FloorPlan"),
        Scale=96,
        CropBoxActive=True,
        CropBoxVisible=True,
    )


def _fake_doc(view):
    return types.SimpleNamespace(
        GetElement=lambda eid: view,
        ProjectInformation=None,
        IsModified=False,
        Title="fake",
    )


@contextlib.contextmanager
def _fake_revit_db():
    """The loop does `from Autodesk.Revit.DB import ElementId` before the branch."""
    fake = types.ModuleType("Autodesk.Revit.DB")
    fake.ElementId = _FakeElementId
    saved = sys.modules.get("Autodesk.Revit.DB")
    sys.modules["Autodesk.Revit.DB"] = fake
    try:
        yield fake
    finally:
        if saved is None:
            sys.modules.pop("Autodesk.Revit.DB", None)
        else:
            sys.modules["Autodesk.Revit.DB"] = saved


def _make_cfg(tmp_path, stage_a):
    # output_dir is not a Config kwarg -- the pipeline reads it with getattr --
    # so it is set as an attribute, and deliberately AFTER construction so
    # to_dict() (which the differ below reads) is unaffected by the tmp path.
    cfg = Config(
        enable_color_id_buffer_stage_a=stage_a,
        view_cache_enabled=False,
        perf_collect_timings=False,
    )
    cfg.output_dir = str(tmp_path)
    return cfg


def _run_one_view(monkeypatch, tmp_path, stage_a):
    """Drive one view through the real per-view loop.

    Returns (model_pass_entries, capture_entries, results).
    """
    view = _fake_view()
    doc = _fake_doc(view)

    model_pass_entries = []
    capture_entries = []

    def _recorder(doc_, view_, raster_, elements_, cfg_, **kwargs):
        # Records ENTRY into the model pass. Substituting for the function is
        # what makes this runnable outside Revit; setattr below is what makes a
        # rename of the production name fail this file instead of silencing it.
        model_pass_entries.append(getattr(getattr(view_, "Id", None), "IntegerValue", None))
        return {"timings": {}}

    def _capture(doc_, view_, elements_, cfg_, **kwargs):
        capture_entries.append(getattr(getattr(view_, "Id", None), "IntegerValue", None))
        return {
            "view_id": VIEW_ID,
            "view_name": "L1 Plan",
            "success": True,
            "stage": "color_id_buffer_stage_a",
        }

    # raising=True (the default) is deliberate on all four: if production renames
    # or drops any of these, this file errors rather than quietly testing nothing.
    monkeypatch.setattr(pipeline, "render_model_front_to_back", _recorder)
    monkeypatch.setattr(
        "vop_interwoven.color_id_buffer.export_color_id_buffer_view", _capture
    )
    monkeypatch.setattr(
        pipeline, "init_view_raster",
        lambda doc_, view_, cfg_, diag=None: types.SimpleNamespace(
            W=8, H=8, cell_size_ft=1.0, bounds_xy=None, view_basis=None,
        ),
    )
    monkeypatch.setattr(pipeline, "collect_view_elements", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "rasterize_annotations", lambda *a, **k: {})

    cfg = _make_cfg(tmp_path, stage_a)
    with _fake_revit_db():
        results = pipeline.process_document_views(doc, [VIEW_ID], cfg)

    return model_pass_entries, capture_entries, results


def test_control_geometry_path_does_enter_the_model_pass(monkeypatch, tmp_path):
    """THE CONTROL. Read a failure here as "this file tests nothing any more"."""
    model_pass, capture, results = _run_one_view(monkeypatch, tmp_path, stage_a=False)

    assert model_pass == [VIEW_ID], (
        "the geometry path must reach render_model_front_to_back for this fixture; "
        "if it does not, the Stage A assertion in this file is vacuous"
    )
    assert capture == [], "the geometry path must not run the color-ID capture"
    assert len(results) == 1


def test_stage_a_view_does_not_enter_the_model_pass(monkeypatch, tmp_path):
    """The claim. Meaningless without the control above."""
    model_pass, capture, results = _run_one_view(monkeypatch, tmp_path, stage_a=True)

    # Positive evidence that the Stage A branch was actually taken -- so an empty
    # recorder cannot be explained by the loop bailing out before the branch.
    assert capture == [VIEW_ID], "the Stage A branch was not taken at all"

    assert model_pass == [], (
        "a Stage A view entered render_model_front_to_back: the branch's "
        "`continue` is gone, or the model pass moved above the branch. "
        "N1's _classify_uv_rect and N3's _scene_occ_rects are nested inside "
        "that function, so this is the ledger's 'arbiter path only' claim failing."
    )
    assert len(results) == 1


def test_the_two_runs_differ_only_in_the_flag(tmp_path):
    """Pins "over the same fixture" mechanically, not by inspection of the helper."""
    off = _make_cfg(tmp_path, False).to_dict()
    on = _make_cfg(tmp_path, True).to_dict()

    differing = {k for k in set(off) | set(on) if off.get(k) != on.get(k)}
    assert differing == {"enable_color_id_buffer_stage_a"}, (
        "the two runs must differ in exactly one config key, or 'same input' is "
        "not what this file is testing"
    )
