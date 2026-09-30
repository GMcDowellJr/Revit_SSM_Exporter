"""T2: registration-tick avoidance ignores imports, in the sidecar FILE.

pipeline_0930_1133 Plan_DWG: the view-specific DWG 19296946's bbox spans the
plan, so seven ticks were marked "blocked" -- and all twelve still registered.
An import is not something a tick must clear; a tag still is (the control).
"""
import json

from vop_interwoven.stage_a_registered_capture import annotation_avoid_rects

from tests.stage_a_capture_fakes import FakeCategory, FakeElement
from tests.test_stage_a_annotation_pass_probe_switches import ANNO_CAT, VIEW_ID, _BBox
from tests.test_stage_a_registered_capture import _run

DWG_ID = 19296946


class ImportInstance(FakeElement):
    """Named as the Revit type is: production matches it by type NAME."""

    def __init__(self, elem_id, owner_view_id, bbox):
        FakeElement.__init__(self, elem_id, FakeCategory("plan.dwg", 70),
                             owner_view_id=owner_view_id, bbox=bbox)
        self.ViewSpecific = True


def _spanning_import():
    return ImportInstance(DWG_ID, VIEW_ID, _BBox((-1e4, -1e4, 0), (1e4, 1e4, 0)))


def _marks(path):
    with open(path) as handle:
        return json.load(handle)["registration_marks"]


def test_t2_a_plan_spanning_import_moves_and_blocks_no_tick_in_the_FILE(tmp_path):
    base, _v, _d, _e, _diag = _run(tmp_path / "base")
    base_rects = _marks(base["sidecar_path"])["mark_avoidance"]["avoid_rects"]
    out, _v, _d, _e, _diag = _run(tmp_path / "dwg", extra_elements=[_spanning_import()])
    for path in (out["sidecar_path"], out["annotation_sidecar_path"]):
        marks = _marks(path)
        assert [m["placement"] for m in marks["marks"]
                if m.get("placement") not in (None, "original")] == []
        assert marks["mark_avoidance"]["excluded_imports"] == 1
        # The import adds no rectangle; the view's other members still do.
        assert marks["mark_avoidance"]["avoid_rects"] == base_rects
    assert out["registration"]["marks"]["created_count"] == 12


def test_t2_control_a_tag_over_a_tick_is_still_avoided(tmp_path):
    out, _v, _d, _e, _diag = _run(tmp_path / "a")
    target = [m for m in _marks(out["sidecar_path"])["marks"]
              if m["key"] == "left_bottom_h"][0]
    (ua, va), (ub, _vb) = target["uv0"], target["uv1"]
    tag = FakeElement(2009, ANNO_CAT, owner_view_id=VIEW_ID,
                      bbox=_BBox((min(ua, ub) - 0.5, va - 0.5, 0),
                                 (max(ua, ub) + 0.5, va + 0.5, 0)))
    base_rects = _marks(out["sidecar_path"])["mark_avoidance"]["avoid_rects"]
    out2, _v, _d, _e, _diag = _run(tmp_path / "b",
                                   extra_elements=[tag, _spanning_import()])
    marks = _marks(out2["sidecar_path"])
    moved = [m for m in marks["marks"] if m["key"] == "left_bottom_h"][0]
    assert moved["placement"] == "moved" and moved["was_covered_by"] == [2009]
    assert marks["mark_avoidance"]["excluded_imports"] == 1
    assert marks["mark_avoidance"]["avoid_rects"] == base_rects + 1


def test_t2_only_imports_are_excluded():
    tag = FakeElement(7, ANNO_CAT, owner_view_id=VIEW_ID, bbox=None)
    avoid, record = annotation_avoid_rects(None, [tag, _spanning_import()], None)
    assert record["excluded_imports"] == 1
    assert record["annotation_elements"] == 2
    assert record["no_bbox"] == 1
