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


# --- Codex, PR #226: a marker whose Name will not read ----------------------
#
# It may be the capture view's own crop region, and painting that colours the
# whole crop. It is left in the model pass under its own basis, counted, and
# warned once per split. Mutation: falling through to annotation (the old
# behaviour) turns the first two red; dropping the warn turns the third red.

class _NamelessElem(_Elem):
    @property
    def Name(self):
        raise RuntimeError("name unavailable")

    @Name.setter
    def Name(self, _value):
        pass


class _Diag(object):
    def __init__(self):
        self.warnings = []

    def warn(self, **kw):
        self.warnings.append(kw)


def test_a_marker_whose_name_will_not_read_is_not_painted():
    rec = _place(_NamelessElem(4, VIEWERS, None))
    assert (rec["pass"], rec["basis"]) == (STAGE_A_PASS_MODEL,
                                           "view_reference_name_unreadable")
    assert "name unavailable" in rec["reason"]
    # Control: with no capture view name to compare, the name decides
    # nothing, and the marker is painted.
    rec = _place(_NamelessElem(4, VIEWERS, None), view_name=None)
    assert (rec["pass"], rec["basis"]) == (STAGE_A_PASS_ANNOTATION,
                                           "view_reference_category")


def test_the_split_counts_and_warns_once_for_unreadable_names():
    elems = [_NamelessElem(4, VIEWERS, None), _NamelessElem(5, VIEWERS, None),
             _Elem(1, VIEWERS, "Section 3")]
    diag = _Diag()
    model, anno, _u, basis = split_stage_a_pass_membership(
        elems, capture_view_id_int=CAPTURE_VIEW_ID, diag=diag,
        datum_category_ids=set(), view_reference_category_ids={VIEWERS},
        capture_view_name="Level 1")
    assert sorted(e.Id.IntegerValue for e in model) == [4, 5]
    assert [e.Id.IntegerValue for e in anno] == [1]
    assert basis["view_reference_name_unreadable"] == 2
    warned = [w for w in diag.warnings if w["callsite"].endswith(
        "view_reference_name_unreadable")]
    assert len(warned) == 1 and warned[0]["view_id"] == CAPTURE_VIEW_ID
    assert warned[0]["message"].startswith("2 view marker(s)")


def test_control_readable_names_warn_nothing():
    diag = _Diag()
    split_stage_a_pass_membership(
        [_Elem(1, VIEWERS, "Section 3"), _Elem(2, VIEWERS, "Level 1")],
        capture_view_id_int=CAPTURE_VIEW_ID, diag=diag, datum_category_ids=set(),
        view_reference_category_ids={VIEWERS}, capture_view_name="Level 1")
    assert diag.warnings == []


# --- probe Q7 (run 20261005T084311): the ElevationMarker body ----------------
#
# The marker body is category -2000535 "Elevations", BuiltInCategory OST_Elev,
# not OST_ElevationMarks; on CABINET TYPES all 15 were placed no_owner_view
# (model pass) and their text came out black. Resolved the way production
# resolves the list -- by name, off the live enum -- with the enum values the
# host REPORTED (probe 2026-10-05.1, run 20261005T091441), which has no
# OST_Elevations: the first fix used that name and resolved to nothing.
# Mutations: removing "OST_Elev" from STAGE_A_VIEW_REFERENCE_BIC_NAMES, or
# naming it OST_Elevations again, turns this red.

import sys  # noqa: E402
import types  # noqa: E402

from vop_interwoven.revit.annotation import stage_a_view_reference_category_ids  # noqa: E402

ELEVATIONS = -2000535


def test_an_elevation_marker_body_is_annotation_and_counted_by_name(monkeypatch):
    fake = types.ModuleType("Autodesk.Revit.DB")
    fake.BuiltInCategory = types.SimpleNamespace(
        OST_Viewers=VIEWERS, OST_Elev=ELEVATIONS, OST_ElevationMarks=-2006045,
        OST_SectionHeads=-2000400, OST_CalloutHeads=-2000538,
        OST_ReferenceViewer=-2000198)
    monkeypatch.setitem(sys.modules, "Autodesk.Revit.DB", fake)
    names = {}
    ids, error = stage_a_view_reference_category_ids(names_out=names)
    assert error is None and ELEVATIONS in ids
    marker = _Elem(31, ELEVATIONS, "Elevation 1 - a")
    # No view_reference_category_ids: the split resolves them itself, as the
    # capture does, so the count is keyed by the resolved NAME.
    _model, anno, _u, basis = split_stage_a_pass_membership(
        [marker, _Elem(3, 10, "Wall")], capture_view_id_int=CAPTURE_VIEW_ID,
        datum_category_ids=set(), capture_view_name="CABINET TYPES")
    assert [e.Id.IntegerValue for e in anno] == [31]
    assert basis["view_reference_category_counts"]["OST_Elev"] == 1
