"""View markers (elevation, section, callout, reference viewers) are annotation.

Greg (2026-10-02): they do not "belong" to the view they are drawn in -- their
OwnerViewId is invalid -- so ownership placed them in the MODEL pass, where
nothing paints them and the annotation pass suppresses them to white. They now
join the annotation pass on category (basis "view_reference_category"), as the
datums do. The capture view's OWN crop-region element (an OST_Viewers element
named like the view) is not a marker drawn in it, and stays in the model pass
(basis "own_view_reference").

Mutations: deleting the view-marker branch -> the first test and the FILE test
red; dropping the own-name exclusion -> the own-crop tests red.
"""
import json

from vop_interwoven.revit.annotation import (
    STAGE_A_PASS_ANNOTATION, STAGE_A_PASS_MODEL, split_stage_a_pass_membership,
    stage_a_pass_membership,
)

from tests import test_probe_anno_pass_run_variant as world
from tests.stage_a_capture_fakes import FakeElement
from tests.test_stage_a_registered_capture import _run

VIEWERS = world.VIEWERS_CAT.Id.IntegerValue
CAPTURE_VIEW_ID = 77


class _Id(object):
    def __init__(self, value):
        self.IntegerValue = int(value)


class _Elem(object):
    def __init__(self, elem_id, cat_id, name, owner=-1):
        self.Id = _Id(elem_id)
        self.OwnerViewId = _Id(owner)
        self.Category = type("C", (object,), {"Id": _Id(cat_id)})()
        self.Name = name


def _place(elem, ids=frozenset([VIEWERS]), view_name="Level 1"):
    return stage_a_pass_membership(elem, capture_view_id_int=CAPTURE_VIEW_ID,
                                   datum_category_ids=set(),
                                   view_reference_category_ids=set(ids),
                                   capture_view_name=view_name)


def test_a_section_marker_drawn_in_the_view_is_annotation():
    rec = _place(_Elem(1, VIEWERS, "Section 3"))
    assert rec["pass"] == STAGE_A_PASS_ANNOTATION
    assert rec["basis"] == "view_reference_category"


def test_the_views_own_crop_region_element_stays_in_the_model_pass():
    rec = _place(_Elem(2, VIEWERS, "Level 1"))
    assert rec["pass"] == STAGE_A_PASS_MODEL and rec["basis"] == "own_view_reference"


def test_controls_ownerless_model_content_and_no_marker_categories():
    wall = _place(_Elem(3, 10, "Wall"))
    assert (wall["pass"], wall["basis"]) == (STAGE_A_PASS_MODEL, "no_owner_view")
    # With no marker categories resolved, the pre-2026-10-02 placement.
    marker = _place(_Elem(1, VIEWERS, "Section 3"), ids=())
    assert (marker["pass"], marker["basis"]) == (STAGE_A_PASS_MODEL, "no_owner_view")


def test_the_split_counts_markers_per_category():
    elems = [_Elem(1, VIEWERS, "Section 3"), _Elem(2, VIEWERS, "Level 1"),
             _Elem(3, 10, "Wall")]
    model, anno, unresolved, basis = split_stage_a_pass_membership(
        elems, capture_view_id_int=CAPTURE_VIEW_ID, datum_category_ids=set(),
        view_reference_category_ids={VIEWERS}, capture_view_name="Level 1")
    assert [e.Id.IntegerValue for e in anno] == [1]
    assert sorted(e.Id.IntegerValue for e in model) == [2, 3]
    assert (basis["view_reference_category"], basis["own_view_reference"]) == (1, 1)


def _sidecar(path):
    with open(path) as handle:
        return json.load(handle)


def test_a_marker_is_painted_in_the_annotation_FILE_and_the_crop_element_is_not(tmp_path):
    """End to end: the annotation pass paints the marker (in its colour map,
    not suppressed white); the view's own crop-region element (1005, named
    like the view) is neither painted nor marker-placed."""
    marker = FakeElement(1006, world.VIEWERS_CAT, name="Section 3")
    own = FakeElement(1005, world.VIEWERS_CAT, name="TestPlan")
    out, view, _doc, exports, _diag = _run(tmp_path, extra_elements=[marker, own])
    assert view.Name == "TestPlan"
    anno = _sidecar(out["annotation_sidecar_path"])
    assert "1006" in anno["color_assignment_map"]
    assert "1005" not in anno["color_assignment_map"]
    basis = anno["membership"]["basis_counts"]
    assert basis["view_reference_category"] == 1 and basis["own_view_reference"] == 1
    # The annotation export painted the marker in its own colour, not white.
    assert exports[1]["overrides"].get(1006) not in (None, (255, 255, 255))
    assert out["registration"]["success"] is True
