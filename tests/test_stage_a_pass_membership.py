"""Stage A step 3 -- capture-pass membership is read from OwnerViewId.

The gate this file exists for: a split over synthetic elements WITH and
WITHOUT an owning view, plus the case the record shape is really about --
an element whose OwnerViewId cannot be read at all, which must join NEITHER
pass rather than defaulting into one.

Why that third case is the load-bearing one. Defaulting an unreadable
element into the model pass paints an annotation into the model TIFF, which
is the one thing step 3 promises not to do; defaulting it into the
annotation pass drops a model element from the capture. Afterwards neither
is distinguishable from a correct read. So the assertion is not just "the
two good cases land right" -- it is that the partition is exact and the
third bucket is populated.

CONTROL: ``test_a_plain_model_element_is_not_reported_unresolved`` pins that
the unresolved bucket is reachable only by a real read failure. Without it
every assertion below would also pass against an implementation that
reported everything unresolved.
"""
import pytest

from vop_interwoven.revit.annotation import (
    STAGE_A_PASS_ANNOTATION,
    STAGE_A_PASS_MODEL,
    split_stage_a_pass_membership,
    stage_a_pass_membership,
    stage_a_pass_membership_summary,
)

CAPTURE_VIEW_ID = 4242


class _Id(object):
    def __init__(self, value):
        self.IntegerValue = int(value)


class _Elem(object):
    """An element whose OwnerViewId is whatever the test says it is."""

    def __init__(self, elem_id, owner_view_id):
        self.Id = _Id(elem_id)
        self.OwnerViewId = _Id(owner_view_id)


class _ElemOwnerRaises(object):
    """OwnerViewId raises -- a broken/stale element, or a host that refuses."""

    def __init__(self, elem_id, exc):
        self.Id = _Id(elem_id)
        self._exc = exc

    @property
    def OwnerViewId(self):
        raise self._exc


class _ElemOwnerNone(object):
    """OwnerViewId is None: not the same as InvalidElementId."""

    def __init__(self, elem_id):
        self.Id = _Id(elem_id)
        self.OwnerViewId = None


class _ElemOwnerIntegerValueRaises(object):
    def __init__(self, elem_id):
        self.Id = _Id(elem_id)
        self.OwnerViewId = self

    @property
    def IntegerValue(self):
        raise RuntimeError("IntegerValue unavailable")


def _model_elem(elem_id=1):
    # -1 is ElementId.InvalidElementId: Revit's "this element has no owning view".
    return _Elem(elem_id, -1)


def _anno_elem(elem_id=2, owner=CAPTURE_VIEW_ID):
    return _Elem(elem_id, owner)


# --- the two named cases ---------------------------------------------------

def test_element_without_an_owner_view_joins_the_model_pass():
    record = stage_a_pass_membership(_model_elem(), capture_view_id_int=CAPTURE_VIEW_ID)
    assert record["state"] == "value"
    assert record["pass"] == STAGE_A_PASS_MODEL
    assert record["owner_view_id"] == -1
    # A model element has no owning view, so "does it match this view" has no
    # answer -- explicitly not a False one.
    assert record["owner_view_matches_capture_view"] == "not_applicable"


def test_element_with_an_owner_view_joins_the_annotation_pass():
    record = stage_a_pass_membership(_anno_elem(), capture_view_id_int=CAPTURE_VIEW_ID)
    assert record["state"] == "value"
    assert record["pass"] == STAGE_A_PASS_ANNOTATION
    assert record["owner_view_id"] == CAPTURE_VIEW_ID
    assert record["owner_view_matches_capture_view"] is True


def test_the_split_partitions_exactly():
    model_elems = [_model_elem(10), _model_elem(11), _model_elem(12)]
    anno_elems = [_anno_elem(20), _anno_elem(21)]
    elements = [model_elems[0], anno_elems[0], model_elems[1],
                anno_elems[1], model_elems[2]]

    model, annotation, unresolved = split_stage_a_pass_membership(
        elements, capture_view_id_int=CAPTURE_VIEW_ID)

    assert model == model_elems
    assert annotation == anno_elems
    assert unresolved == []
    # Exact partition: nothing dropped, nothing duplicated. Counting is not
    # enough -- two elements could swap passes and the counts would hold.
    assert len(model) + len(annotation) + len(unresolved) == len(elements)
    assert [id(e) for e in model + annotation] == [
        id(e) for e in [model_elems[0], model_elems[1], model_elems[2],
                        anno_elems[0], anno_elems[1]]
    ]


# --- the case the record shape exists for ----------------------------------

@pytest.mark.parametrize("broken", [
    _ElemOwnerRaises(30, RuntimeError("no such property")),
    _ElemOwnerRaises(31, AttributeError("OwnerViewId")),
    _ElemOwnerNone(32),
    _ElemOwnerIntegerValueRaises(33),
])
def test_an_unreadable_owner_view_joins_neither_pass(broken):
    record = stage_a_pass_membership(broken, capture_view_id_int=CAPTURE_VIEW_ID)
    assert record["state"] == "unavailable"
    assert record["pass"] is None
    assert record["reason"]

    model, annotation, unresolved = split_stage_a_pass_membership(
        [broken], capture_view_id_int=CAPTURE_VIEW_ID)
    assert model == []
    assert annotation == []
    assert len(unresolved) == 1
    assert unresolved[0][0] is broken


def test_the_unreadable_element_is_absent_from_both_passes_not_just_flagged():
    """The flag is not the point; the ABSENCE is.

    An implementation that recorded 'unavailable' and then appended the
    element to the model pass anyway would satisfy every record-shape
    assertion above. This is the one that fails it.
    """
    good_model = _model_elem(40)
    good_anno = _anno_elem(41)
    broken = _ElemOwnerRaises(42, RuntimeError("boom"))

    model, annotation, unresolved = split_stage_a_pass_membership(
        [good_model, broken, good_anno], capture_view_id_int=CAPTURE_VIEW_ID)

    assert broken not in model
    assert broken not in annotation
    assert model == [good_model]
    assert annotation == [good_anno]
    assert len(unresolved) == 1


def test_a_read_failure_is_diagnosed_not_swallowed():
    class _Diag(object):
        def __init__(self):
            self.warnings = []

        def warn(self, **kw):
            self.warnings.append(kw)

    diag = _Diag()
    split_stage_a_pass_membership(
        [_model_elem(50), _ElemOwnerRaises(51, RuntimeError("boom"))],
        capture_view_id_int=CAPTURE_VIEW_ID, diag=diag)

    assert len(diag.warnings) == 1
    warned = diag.warnings[0]
    assert warned["phase"] == "annotation"
    assert warned["callsite"] == "split_stage_a_pass_membership"
    assert "NEITHER" in warned["message"]
    assert warned["view_id"] == CAPTURE_VIEW_ID


# --- CONTROL ---------------------------------------------------------------

def test_a_plain_model_element_is_not_reported_unresolved():
    """Without this, an all-unresolved implementation passes every test above."""
    model, annotation, unresolved = split_stage_a_pass_membership(
        [_model_elem(60), _anno_elem(61)], capture_view_id_int=CAPTURE_VIEW_ID)
    assert unresolved == []
    assert len(model) == 1
    assert len(annotation) == 1


def test_no_diagnostic_when_everything_resolved():
    class _Diag(object):
        def __init__(self):
            self.warnings = []

        def warn(self, **kw):
            self.warnings.append(kw)

    diag = _Diag()
    split_stage_a_pass_membership(
        [_model_elem(70), _anno_elem(71)],
        capture_view_id_int=CAPTURE_VIEW_ID, diag=diag)
    assert diag.warnings == []


# --- ownership is recorded, not acted on -----------------------------------

def test_an_element_owned_by_another_view_is_still_annotation_but_flagged():
    foreign = _anno_elem(80, owner=CAPTURE_VIEW_ID + 1)
    record = stage_a_pass_membership(foreign, capture_view_id_int=CAPTURE_VIEW_ID)
    assert record["pass"] == STAGE_A_PASS_ANNOTATION
    assert record["owner_view_matches_capture_view"] is False


def test_without_a_capture_view_id_ownership_is_unavailable_not_false():
    record = stage_a_pass_membership(_anno_elem(90), capture_view_id_int=None)
    assert record["pass"] == STAGE_A_PASS_ANNOTATION
    # "unavailable", never False: not comparing is not the same as not matching.
    assert record["owner_view_matches_capture_view"] == "unavailable"


# --- the sidecar summary ---------------------------------------------------

def test_summary_counts_are_always_present():
    model, annotation, unresolved = split_stage_a_pass_membership(
        [_model_elem(100), _anno_elem(101), _ElemOwnerRaises(102, RuntimeError("x"))],
        capture_view_id_int=CAPTURE_VIEW_ID)
    summary = stage_a_pass_membership_summary(model, annotation, unresolved)

    assert summary["membership_rule"] == "OwnerViewId"
    assert summary["model_count"] == 1
    assert summary["annotation_count"] == 1
    assert summary["unresolved_count"] == 1
    assert summary["unresolved"][0]["element_id"] == 102
    assert summary["unresolved"][0]["state"] == "unavailable"
    assert summary["unresolved"][0]["reason"]


def test_summary_zero_means_none_of_these_because_the_split_ran():
    model, annotation, unresolved = split_stage_a_pass_membership(
        [_model_elem(110)], capture_view_id_int=CAPTURE_VIEW_ID)
    summary = stage_a_pass_membership_summary(model, annotation, unresolved)
    assert summary["annotation_count"] == 0
    assert summary["unresolved_count"] == 0
    assert summary["unresolved"] == []


def test_an_unreadable_element_id_is_none_not_zero():
    class _NoId(object):
        OwnerViewId = None

    model, annotation, unresolved = split_stage_a_pass_membership(
        [_NoId()], capture_view_id_int=CAPTURE_VIEW_ID)
    summary = stage_a_pass_membership_summary(model, annotation, unresolved)
    assert summary["unresolved"][0]["element_id"] is None
