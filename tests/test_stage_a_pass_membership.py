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
    stage_a_datum_category_ids,
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

    model, annotation, unresolved, _basis = split_stage_a_pass_membership(
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

    model, annotation, unresolved, _basis = split_stage_a_pass_membership(
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

    model, annotation, unresolved, _basis = split_stage_a_pass_membership(
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
    # datum_category_ids given explicitly: this test counts diagnostics, and
    # outside Revit the default resolution ALSO warns (BuiltInCategory is not
    # importable). An empty set is ownership-only placement.
    split_stage_a_pass_membership(
        [_model_elem(50), _ElemOwnerRaises(51, RuntimeError("boom"))],
        capture_view_id_int=CAPTURE_VIEW_ID, diag=diag,
        datum_category_ids=set())

    assert len(diag.warnings) == 1
    warned = diag.warnings[0]
    assert warned["phase"] == "annotation"
    assert warned["callsite"] == "split_stage_a_pass_membership"
    assert "NEITHER" in warned["message"]
    assert warned["view_id"] == CAPTURE_VIEW_ID


# --- CONTROL ---------------------------------------------------------------

def test_a_plain_model_element_is_not_reported_unresolved():
    """Without this, an all-unresolved implementation passes every test above."""
    model, annotation, unresolved, _basis = split_stage_a_pass_membership(
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
        capture_view_id_int=CAPTURE_VIEW_ID, diag=diag,
        datum_category_ids=set())
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
    model, annotation, unresolved, _basis = split_stage_a_pass_membership(
        [_model_elem(100), _anno_elem(101), _ElemOwnerRaises(102, RuntimeError("x"))],
        capture_view_id_int=CAPTURE_VIEW_ID)
    summary = stage_a_pass_membership_summary(model, annotation, unresolved)

    assert summary["membership_rule"] == "OwnerViewId+datum_category"
    assert summary["model_count"] == 1
    assert summary["annotation_count"] == 1
    assert summary["unresolved_count"] == 1
    assert summary["unresolved"][0]["element_id"] == 102
    assert summary["unresolved"][0]["state"] == "unavailable"
    assert summary["unresolved"][0]["reason"]


def test_summary_zero_means_none_of_these_because_the_split_ran():
    model, annotation, unresolved, _basis = split_stage_a_pass_membership(
        [_model_elem(110)], capture_view_id_int=CAPTURE_VIEW_ID)
    summary = stage_a_pass_membership_summary(model, annotation, unresolved)
    assert summary["annotation_count"] == 0
    assert summary["unresolved_count"] == 0
    assert summary["unresolved"] == []


def test_an_unreadable_element_id_is_none_not_zero():
    class _NoId(object):
        OwnerViewId = None

    model, annotation, unresolved, _basis = split_stage_a_pass_membership(
        [_NoId()], capture_view_id_int=CAPTURE_VIEW_ID)
    summary = stage_a_pass_membership_summary(model, annotation, unresolved)
    assert summary["unresolved"][0]["element_id"] is None


# ==========================================================================
# Datums join the annotation pass on CATEGORY (Greg, 2026-09-21)
#
# Found by review on PR #211: grids and levels are ownerless, so ownership
# alone put them in the model bucket -- where collection_policy excludes them
# outright, so nothing painted them, while the annotation pass left them
# visible. They rendered as unassigned native-colour pixels on nearly every
# plan and section. Greg's call: keep them in the annotation pass, paint them.
# ==========================================================================

# Category ids only need to be internally consistent here: production
# resolves them off the live BuiltInCategory enum, so these stand in for
# Revit's values rather than asserting them.
GRID_CAT_ID = -2000220
LEVEL_CAT_ID = -2000240
GRID_HEAD_CAT_ID = -2000221
LEVEL_HEAD_CAT_ID = -2000241
WALL_CAT_ID = 10
DATUMS = {GRID_CAT_ID, LEVEL_CAT_ID, GRID_HEAD_CAT_ID, LEVEL_HEAD_CAT_ID}


class _Cat(object):
    def __init__(self, cat_id):
        self.Id = _Id(cat_id)


class _CategorisedElem(object):
    """An element with a category AND an owner view, both settable."""

    def __init__(self, elem_id, cat_id, owner_view_id=-1):
        self.Id = _Id(elem_id)
        self.Category = _Cat(cat_id)
        self.OwnerViewId = _Id(owner_view_id)


def _grid(elem_id=200):
    return _CategorisedElem(elem_id, GRID_CAT_ID)


def _level(elem_id=201):
    return _CategorisedElem(elem_id, LEVEL_CAT_ID)


def _wall(elem_id=202):
    return _CategorisedElem(elem_id, WALL_CAT_ID)


@pytest.mark.parametrize("make", [_grid, _level])
def test_an_ownerless_datum_joins_the_annotation_pass(make):
    record = stage_a_pass_membership(
        make(), capture_view_id_int=CAPTURE_VIEW_ID, datum_category_ids=DATUMS)

    assert record["pass"] == STAGE_A_PASS_ANNOTATION
    # Placed by CATEGORY, and the record says so rather than leaving a reader
    # to infer ownership from the pass name.
    assert record["basis"] == "datum_category"
    assert record["owner_view_id"] == -1
    # It has no owning view at all, so "does it match this view" has no
    # answer -- explicitly not a False one.
    assert record["owner_view_matches_capture_view"] == "not_applicable"


def test_an_ownerless_non_datum_still_joins_the_model_pass():
    """CONTROL. Without it, an implementation that sent every ownerless
    element to the annotation pass would pass the test above -- and would
    put the whole model into the annotation capture."""
    record = stage_a_pass_membership(
        _wall(), capture_view_id_int=CAPTURE_VIEW_ID, datum_category_ids=DATUMS)

    assert record["pass"] == STAGE_A_PASS_MODEL
    assert record["basis"] == "no_owner_view"


def test_ownership_still_wins_where_it_can_answer():
    """The category is consulted ONLY on elements ownership failed to place.
    A view-owned element is an annotation on ownership, not on category."""
    owned = _CategorisedElem(203, WALL_CAT_ID, owner_view_id=CAPTURE_VIEW_ID)
    record = stage_a_pass_membership(
        owned, capture_view_id_int=CAPTURE_VIEW_ID, datum_category_ids=DATUMS)

    assert record["pass"] == STAGE_A_PASS_ANNOTATION
    assert record["basis"] == "owner_view"


def test_the_split_sends_datums_to_the_annotation_bucket():
    grid, level, wall = _grid(), _level(), _wall()
    anno = _anno_elem(210)

    model, annotation, unresolved, basis = split_stage_a_pass_membership(
        [wall, grid, anno, level], capture_view_id_int=CAPTURE_VIEW_ID,
        datum_category_ids=DATUMS)

    assert model == [wall]
    assert annotation == [grid, anno, level]
    assert unresolved == []
    assert basis["datum_category"] == 2
    assert basis["owner_view"] == 1
    assert basis["no_owner_view"] == 1


def test_an_empty_datum_set_is_the_pre_decision_behaviour():
    """Pins what the decision actually changed.

    With no datum categories, a grid is ownerless model content again -- the
    exact state review found. Asserted so the fix cannot be reduced to a
    no-op without a test saying so.
    """
    model, annotation, unresolved, basis = split_stage_a_pass_membership(
        [_grid()], capture_view_id_int=CAPTURE_VIEW_ID, datum_category_ids=set())

    assert len(model) == 1
    assert annotation == []
    assert basis["datum_category"] == 0


def test_an_element_with_no_readable_category_is_not_guessed_into_a_datum():
    """A category that cannot be read is not a datum category.

    It stays model content on ownership, which is the answer ownership
    actually gave -- rather than being swept into the annotation pass by a
    failed lookup.
    """
    class _NoCategory(object):
        def __init__(self):
            self.Id = _Id(220)
            self.OwnerViewId = _Id(-1)

        @property
        def Category(self):
            raise RuntimeError("category unavailable")

    record = stage_a_pass_membership(
        _NoCategory(), capture_view_id_int=CAPTURE_VIEW_ID,
        datum_category_ids=DATUMS)
    assert record["pass"] == STAGE_A_PASS_MODEL
    assert record["basis"] == "no_owner_view"


def test_a_failed_datum_resolution_is_reported_not_silently_empty():
    """An unresolvable datum set restores the gap, so it must be loud.

    Outside Revit BuiltInCategory is not importable, which is exactly the
    failure this reports -- so the default path here IS the failure path.
    """
    class _Diag2(object):
        def __init__(self):
            self.warnings = []

        def warn(self, **kw):
            self.warnings.append(kw)

    diag = _Diag2()
    _m, _a, _u, basis = split_stage_a_pass_membership(
        [_grid()], capture_view_id_int=CAPTURE_VIEW_ID, diag=diag)

    assert basis["datum_resolution_error"]
    assert any(w["callsite"] == "stage_a_datum_category_ids" for w in diag.warnings)
    # And it says what the consequence is, not just that a lookup failed.
    assert "nothing paints them" in diag.warnings[0]["message"]


def test_the_datum_set_resolves_off_the_live_enum():
    """Not hardcoded ints. A wrong constant would leave datums unpainted --
    silently, which is the failure this decision exists to fix."""
    import sys
    import types as _types

    fake = _types.ModuleType("Autodesk.Revit.DB")

    class _BIC(object):
        OST_Grids = GRID_CAT_ID
        OST_Levels = LEVEL_CAT_ID
        OST_GridHeads = GRID_HEAD_CAT_ID
        OST_LevelHeads = LEVEL_HEAD_CAT_ID

    fake.BuiltInCategory = _BIC
    saved = sys.modules.get("Autodesk.Revit.DB")
    sys.modules["Autodesk.Revit.DB"] = fake
    try:
        ids, error = stage_a_datum_category_ids()
    finally:
        if saved is None:
            sys.modules.pop("Autodesk.Revit.DB", None)
        else:
            sys.modules["Autodesk.Revit.DB"] = saved

    assert error is None
    assert ids == DATUMS


def test_a_host_missing_a_datum_category_reports_which_one():
    import sys
    import types as _types

    fake = _types.ModuleType("Autodesk.Revit.DB")

    class _BIC(object):
        OST_Grids = GRID_CAT_ID
        OST_GridHeads = GRID_HEAD_CAT_ID
        OST_LevelHeads = LEVEL_HEAD_CAT_ID
        # OST_Levels absent on this host.

    fake.BuiltInCategory = _BIC
    saved = sys.modules.get("Autodesk.Revit.DB")
    sys.modules["Autodesk.Revit.DB"] = fake
    try:
        ids, error = stage_a_datum_category_ids()
    finally:
        if saved is None:
            sys.modules.pop("Autodesk.Revit.DB", None)
        else:
            sys.modules["Autodesk.Revit.DB"] = saved

    # Partial, and named. Not an empty set, and not a silent success.
    assert ids == {GRID_CAT_ID, GRID_HEAD_CAT_ID, LEVEL_HEAD_CAT_ID}
    assert "OST_Levels" in error


# ==========================================================================
# Heads: painted IF Revit returns them as separate elements (Greg, follow-up)
#
# Whether a grid/level head is a separately placed element or just graphics
# the datum's TYPE draws is a Revit API fact this session could not verify.
# Listing the head categories makes REVIT decide it at runtime, and the
# per-category counts make the answer readable from one capture.
# ==========================================================================

def _grid_head(elem_id=300):
    return _CategorisedElem(elem_id, GRID_HEAD_CAT_ID)


def _level_head(elem_id=301):
    return _CategorisedElem(elem_id, LEVEL_HEAD_CAT_ID)


@pytest.mark.parametrize("make", [_grid_head, _level_head])
def test_a_head_that_is_a_separate_element_joins_the_annotation_pass(make):
    """The case where Greg's condition is TRUE."""
    record = stage_a_pass_membership(
        make(), capture_view_id_int=CAPTURE_VIEW_ID, datum_category_ids=DATUMS)

    assert record["pass"] == STAGE_A_PASS_ANNOTATION
    assert record["basis"] == "datum_category"


def test_heads_absent_from_the_collection_change_nothing():
    """The case where Greg's condition is FALSE, and why listing them is safe.

    If Revit never returns a head as an element, the head categories match
    nothing: the split is byte-for-byte what it was with only grids and
    levels listed. Being wrong about the API costs nothing.
    """
    grid, wall, anno = _grid(), _wall(), _anno_elem(310)
    elements = [grid, wall, anno]

    with_heads = split_stage_a_pass_membership(
        elements, capture_view_id_int=CAPTURE_VIEW_ID, datum_category_ids=DATUMS)
    without_heads = split_stage_a_pass_membership(
        elements, capture_view_id_int=CAPTURE_VIEW_ID,
        datum_category_ids={GRID_CAT_ID, LEVEL_CAT_ID})

    assert with_heads[0] == without_heads[0]      # model
    assert with_heads[1] == without_heads[1]      # annotation
    assert with_heads[2] == without_heads[2]      # unresolved


def test_the_per_category_counts_answer_the_question():
    """A zero beside a non-zero is the measurement.

    "OST_GridHeads": 0 next to "OST_Grids": 2 says heads are not separate
    elements on this host. A MISSING key would say only that nobody looked,
    which is why every resolved category is seeded to zero.
    """
    names = {}
    import sys
    import types as _types

    fake = _types.ModuleType("Autodesk.Revit.DB")

    class _BIC(object):
        OST_Grids = GRID_CAT_ID
        OST_Levels = LEVEL_CAT_ID
        OST_GridHeads = GRID_HEAD_CAT_ID
        OST_LevelHeads = LEVEL_HEAD_CAT_ID

    fake.BuiltInCategory = _BIC
    fake.ElementId = type("EId", (), {"InvalidElementId": _Id(-1)})
    saved = sys.modules.get("Autodesk.Revit.DB")
    sys.modules["Autodesk.Revit.DB"] = fake
    try:
        _m, _a, _u, basis = split_stage_a_pass_membership(
            [_grid(400), _grid(401), _wall(402)],
            capture_view_id_int=CAPTURE_VIEW_ID)
    finally:
        if saved is None:
            sys.modules.pop("Autodesk.Revit.DB", None)
        else:
            sys.modules["Autodesk.Revit.DB"] = saved

    counts = basis["datum_category_counts"]
    assert counts["OST_Grids"] == 2
    # Present and zero -- not absent.
    assert counts["OST_GridHeads"] == 0
    assert counts["OST_LevelHeads"] == 0
    assert counts["OST_Levels"] == 0
    assert basis["datum_resolution_error"] is None


def test_a_present_head_is_counted_under_its_own_category():
    """CONTROL for the test above.

    Without it, an implementation that always reported zero for heads would
    pass -- and would be indistinguishable from the answer Greg is waiting
    for.
    """
    import sys
    import types as _types

    fake = _types.ModuleType("Autodesk.Revit.DB")

    class _BIC(object):
        OST_Grids = GRID_CAT_ID
        OST_Levels = LEVEL_CAT_ID
        OST_GridHeads = GRID_HEAD_CAT_ID
        OST_LevelHeads = LEVEL_HEAD_CAT_ID

    fake.BuiltInCategory = _BIC
    fake.ElementId = type("EId", (), {"InvalidElementId": _Id(-1)})
    saved = sys.modules.get("Autodesk.Revit.DB")
    sys.modules["Autodesk.Revit.DB"] = fake
    try:
        _m, annotation, _u, basis = split_stage_a_pass_membership(
            [_grid(410), _grid_head(411), _grid_head(412), _level_head(413)],
            capture_view_id_int=CAPTURE_VIEW_ID)
    finally:
        if saved is None:
            sys.modules.pop("Autodesk.Revit.DB", None)
        else:
            sys.modules["Autodesk.Revit.DB"] = saved

    counts = basis["datum_category_counts"]
    assert counts["OST_Grids"] == 1
    assert counts["OST_GridHeads"] == 2
    assert counts["OST_LevelHeads"] == 1
    # And they are in the paint set, not merely counted.
    assert len(annotation) == 4
