"""M1: the annotation pass does not paint what the view does not show.

pipeline_0928_0953: Plan_DWG 19294180 and Plan_RVTLink 19293485 have "Show
annotation categories in this view" OFF, yet each carried 71 tags/dims in the
annotation color_assignment_map -- membership_basis null (so they arrived by
resolve_all's expansion, not as top-level members), no bbox, 0 px.

Gate: a hidden annotation category yields NO color_assignment_map entries,
plus a per-category not_visible_in_view COUNT (never per-element entries).
"""
import json

from tests.stage_a_capture_fakes import FakeCategory, FakeElement, FakeGroup, FakeViewPlan
from tests.test_stage_a_annotation_pass import (
    ANNO_CAT, VIEW_ID, _BBox, _elements, _run_both_passes,
)

DIM_CAT = FakeCategory("Dimensions", -2000260, cat_type="Annotation")
GROUP_CAT = FakeCategory("Detail Groups", -2000095, cat_type="Annotation")


class _DetailGroup(FakeGroup, FakeElement):
    """A view-owned group whose MEMBERS reach the pass only through
    resolve_all's expansion -- the route the evidence points at."""
    def __init__(self, elem_id, member_ids):
        FakeElement.__init__(self, elem_id, GROUP_CAT, owner_view_id=VIEW_ID)
        self._members = member_ids

    def GetMemberIds(self):
        from tests.stage_a_capture_fakes import FakeElementId
        return [FakeElementId(i) for i in self._members]


def _with_group():
    members = [FakeElement(2101, DIM_CAT, owner_view_id=VIEW_ID,
                           bbox=_BBox((60, 50, 0), (66, 52, 0))),
               FakeElement(2102, DIM_CAT, owner_view_id=VIEW_ID)]
    return _elements() + [_DetailGroup(2100, [2101, 2102])] + members


def _painted(anno):
    with open(anno["sidecar_path"]) as handle:
        side = json.load(handle)
    return set(int(k) for k in side["color_assignment_map"]), side["not_painted"]


def test_a_hidden_category_is_not_painted_and_is_counted(tmp_path):
    view = FakeViewPlan(view_id=VIEW_ID)
    view.category_hidden[DIM_CAT.Id.IntegerValue] = True
    _m, anno, _g, _d, _v, _diag = _run_both_passes(tmp_path, elements=_with_group(), view=view)
    painted, not_painted = _painted(anno)
    assert not ({2101, 2102} & painted), painted
    assert not_painted["not_visible_in_view"] == {"Dimensions": 2}
    assert not_painted["not_visible_by_rule"] == {"category_hidden": 2}
    # Everything the view DOES show is still painted.
    assert {2001, 2002, 2100} <= painted


def test_annotation_categories_hidden_drops_every_annotation_category(tmp_path):
    view = FakeViewPlan(view_id=VIEW_ID)
    view.AreAnnotationCategoriesHidden = True
    _m, anno, _g, _d, _v, _diag = _run_both_passes(tmp_path, elements=_with_group(), view=view)
    painted, not_painted = _painted(anno)
    assert not ({2001, 2002, 2100, 2101, 2102, 3001, 3002} & painted), painted
    assert not_painted["annotation_categories_hidden"] is True
    assert not_painted["not_visible_in_view"]["Dimensions"] == 2
    assert not_painted["not_visible_in_view"][ANNO_CAT.Name] == 2
    # The detail line is a MODEL-typed category (Lines): still shown, still painted.
    assert 2003 in painted


def test_an_element_hidden_in_the_view_is_not_painted(tmp_path):
    view = FakeViewPlan(view_id=VIEW_ID)
    view.hidden_elements.add(2002)
    _m, anno, _g, _d, _v, _diag = _run_both_passes(tmp_path, view=view)
    painted, not_painted = _painted(anno)
    assert 2002 not in painted and 2001 in painted
    assert not_painted["not_visible_by_rule"] == {"element_hidden": 1}


def test_control_a_view_that_shows_everything_paints_everything(tmp_path):
    view = FakeViewPlan(view_id=VIEW_ID)
    view.AreAnnotationCategoriesHidden = False
    _m, anno, _g, _d, _v, _diag = _run_both_passes(tmp_path, elements=_with_group(), view=view)
    painted, not_painted = _painted(anno)
    assert {2101, 2102, 2001, 2002, 2100} <= painted
    assert not_painted["not_visible_in_view"] == {}
    assert not_painted["annotation_categories_hidden"] is False


def test_an_element_whose_reads_raise_is_kept_and_counted_not_fatal():
    """Codex, PR #221: GetElement/Category/Name ran OUTSIDE the guarded
    visibility block, so a stale element that raised aborted the whole
    annotation pass instead of being kept and counted under
    visibility_unreadable, as documented."""
    from tests.stage_a_capture_fakes import FakeElementId, install_fake_revit_db
    from vop_interwoven.color_id_buffer import _drop_not_visible_in_view

    class _StaleCategory(FakeElement):
        @property
        def Category(self):
            raise RuntimeError("InvalidObjectException")

    good = FakeElement(2001, ANNO_CAT, owner_view_id=VIEW_ID)
    stale = _StaleCategory.__new__(_StaleCategory)
    stale.Id = FakeElementId(2005)

    class _Doc:
        def GetElement(self, eid):
            if eid.IntegerValue == 2009:
                raise RuntimeError("element deleted")
            return {2001: good, 2005: stale}[eid.IntegerValue]

    ids = [FakeElementId(i) for i in (2001, 2005, 2009)]
    with install_fake_revit_db():
        shown, record = _drop_not_visible_in_view(_Doc(), FakeViewPlan(view_id=VIEW_ID), ids)
    assert [e.IntegerValue for e in shown] == [2001, 2005, 2009]
    assert record["visibility_unreadable"] == {"<unreadable category>": 2}
    assert record["not_visible_in_view"] == {}


def test_n1_by_rule_and_by_category_are_one_set_with_a_total_in_the_FILE(tmp_path):
    """N1 (pipeline_0930_1133 Plan_DWG/RVTLink: by_rule 71, by_category
    40+14+17 = 71). The same elements counted two ways -- the file says so
    and carries the total both sum to."""
    view = FakeViewPlan(view_id=VIEW_ID)
    view.AreAnnotationCategoriesHidden = True
    view.hidden_elements.add(2003)  # a MODEL-typed line, dropped by another rule
    _m, anno, _g, _d, _v, _diag = _run_both_passes(tmp_path, elements=_with_group(), view=view)
    _painted_ids, not_painted = _painted(anno)
    total = not_painted["not_visible_total"]
    assert total == sum(not_painted["not_visible_by_rule"].values())
    assert total == sum(not_painted["not_visible_in_view"].values())
    assert set(not_painted["not_visible_by_rule"]) == {
        "annotation_categories_hidden", "element_hidden"}
    assert "SAME" in not_painted["count_relationship"]


def test_n1_control_nothing_hidden_totals_zero_in_the_FILE(tmp_path):
    view = FakeViewPlan(view_id=VIEW_ID)
    _m, anno, _g, _d, _v, _diag = _run_both_passes(tmp_path, view=view)
    _painted_ids, not_painted = _painted(anno)
    assert not_painted["not_visible_total"] == 0
    assert not_painted["not_visible_by_rule"] == {}
