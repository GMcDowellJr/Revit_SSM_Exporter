"""Unit tests for Stage A's category-level force-white safety net.

Covers vop_interwoven/color_id_buffer.py's
_resolve_colorable_category_predicate, _category_force_white_targets,
_force_white_category and the category-override snapshot/restore pair, plus
the override-precedence layering the whole mechanism depends on.

Same fake-Autodesk.Revit.DB technique as
tests/test_color_id_buffer_link_category_filters.py: the functions under test
do local ``from Autodesk.Revit.DB import ...`` inside their bodies, so only a
fake module installed into sys.modules can intercept them. The REAL
vop_interwoven.revit.collection_policy is used throughout (it is designed to
be importable outside Revit), so these exercise the project's actual category
policy rather than a stand-in for it -- which matters, because the whole
point of the force-white rule is which side of that policy a category falls
on.
"""
import contextlib
import sys
import types

import vop_interwoven.color_id_buffer as color_id_buffer
from vop_interwoven.color_id_buffer import (
    UNCOLORABLE_SENTINEL_RGB,
)


# --- Fake Autodesk.Revit.DB surface -----------------------------------------

class _FakeElementId(object):
    def __init__(self, v):
        self.IntegerValue = int(v)

    def __eq__(self, other):
        return isinstance(other, _FakeElementId) and other.IntegerValue == self.IntegerValue

    def __hash__(self):
        return hash(self.IntegerValue)


class _FakeCategoryType(object):
    Model = "Model"
    Annotation = "Annotation"


class _FakeCategory(object):
    def __init__(self, name, cat_id, cat_type=_FakeCategoryType.Model):
        self.Name = name
        self.Id = _FakeElementId(cat_id)
        self.CategoryType = cat_type
        self.Parent = None


class _FakeColor(object):
    """Mirrors the Revit Color surface the snapshot helpers actually read."""

    def __init__(self, r, g, b, valid=True):
        self.Red, self.Green, self.Blue = int(r), int(g), int(b)
        self.IsValid = valid

    def as_tuple(self):
        return (self.Red, self.Green, self.Blue)

    def __eq__(self, other):
        return isinstance(other, _FakeColor) and (
            self.IsValid == other.IsValid
            and (not self.IsValid or self.as_tuple() == other.as_tuple())
        )

    def __repr__(self):
        return "Color{0}".format(self.as_tuple()) if self.IsValid else "Color(invalid)"


_FakeColor.InvalidColorValue = _FakeColor(0, 0, 0, valid=False)


class _FakeOGS(object):
    """An OverrideGraphicSettings with the real property names the snapshot
    helpers read and the real setter names they write."""

    def __init__(self):
        self.SurfaceForegroundPatternId = _FakeElementId(-1)
        self.SurfaceForegroundPatternColor = _FakeColor(0, 0, 0, valid=False)
        self.IsSurfaceForegroundPatternVisible = True
        self.CutForegroundPatternId = _FakeElementId(-1)
        self.CutForegroundPatternColor = _FakeColor(0, 0, 0, valid=False)
        self.IsCutForegroundPatternVisible = True
        self.ProjectionLineColor = _FakeColor(0, 0, 0, valid=False)
        self.CutLineColor = _FakeColor(0, 0, 0, valid=False)
        self.Transparency = 0
        self.Halftone = False
        # Never touched by the flat-color paint; proves restore leaves the
        # rest of a category's overrides alone.
        self.ProjectionLineWeight = 7

    def SetSurfaceForegroundPatternId(self, v):
        self.SurfaceForegroundPatternId = v

    def SetSurfaceForegroundPatternColor(self, v):
        self.SurfaceForegroundPatternColor = v

    def SetSurfaceForegroundPatternVisible(self, v):
        self.IsSurfaceForegroundPatternVisible = v

    def SetCutForegroundPatternId(self, v):
        self.CutForegroundPatternId = v

    def SetCutForegroundPatternColor(self, v):
        self.CutForegroundPatternColor = v

    def SetCutForegroundPatternVisible(self, v):
        self.IsCutForegroundPatternVisible = v

    def SetProjectionLineColor(self, v):
        self.ProjectionLineColor = v

    def SetCutLineColor(self, v):
        self.CutLineColor = v

    def SetSurfaceTransparency(self, v):
        self.Transparency = v

    def SetHalftone(self, v):
        self.Halftone = v

    def snapshot_tuple(self):
        return (
            self.SurfaceForegroundPatternId.IntegerValue,
            self.SurfaceForegroundPatternColor,
            self.IsSurfaceForegroundPatternVisible,
            self.CutForegroundPatternId.IntegerValue,
            self.CutForegroundPatternColor,
            self.IsCutForegroundPatternVisible,
            self.ProjectionLineColor,
            self.CutLineColor,
            self.Transparency,
            self.Halftone,
            self.ProjectionLineWeight,
        )


class _FakeParameterFilterUtilities(object):
    def __init__(self, filterable_ids):
        self._filterable_ids = filterable_ids

    def GetAllFilterableCategories(self):
        return [_FakeElementId(v) for v in self._filterable_ids]


class _FakeSettings(object):
    def __init__(self, categories):
        self.Categories = categories


class _FakeDoc(object):
    def __init__(self, categories):
        self.Settings = _FakeSettings(categories)


class _FakeView(object):
    """Records the three override layers separately, so a test can ask which
    layer a given element's color would actually come from."""

    def __init__(self):
        self.category_overrides = {}
        self.filter_overrides = {}
        self.element_overrides = {}
        self.set_category_override_calls = []

    def GetCategoryOverrides(self, cat_id):
        return self.category_overrides.setdefault(cat_id.IntegerValue, _FakeOGS())

    def SetCategoryOverrides(self, cat_id, ogs):
        self.category_overrides[cat_id.IntegerValue] = ogs
        self.set_category_override_calls.append(cat_id.IntegerValue)

    def SetFilterOverrides(self, filter_id, ogs):
        self.filter_overrides[filter_id.IntegerValue] = ogs

    def SetElementOverrides(self, elem_id, ogs):
        self.element_overrides[elem_id.IntegerValue] = ogs


class _FakeDiag(object):
    def __init__(self):
        self.warnings = []
        self.errors = []

    def warn(self, **kwargs):
        self.warnings.append(kwargs)

    def error(self, **kwargs):
        self.errors.append(kwargs)


@contextlib.contextmanager
def _install_fake_revit_db(filterable_category_ids=(), with_filter_utilities=True):
    fake_db = types.ModuleType("Autodesk.Revit.DB")
    fake_db.CategoryType = _FakeCategoryType
    fake_db.ElementId = _FakeElementId
    fake_db.Color = _FakeColor
    fake_db.OverrideGraphicSettings = _FakeOGS
    if with_filter_utilities:
        fake_db.ParameterFilterUtilities = _FakeParameterFilterUtilities(filterable_category_ids)

    originals = {"Autodesk.Revit.DB": sys.modules.get("Autodesk.Revit.DB")}
    sys.modules["Autodesk.Revit.DB"] = fake_db
    try:
        yield fake_db
    finally:
        orig = originals["Autodesk.Revit.DB"]
        if orig is None:
            sys.modules.pop("Autodesk.Revit.DB", None)
        else:
            sys.modules["Autodesk.Revit.DB"] = orig


@contextlib.contextmanager
def _frozen_whitelist(*names):
    """Temporarily populate VETTED_COLORABLE_CATEGORY_NAMES.

    The shipped constant is empty until the live Dynamo capture is frozen
    into it, so a test that wants the post-freeze behavior has to supply one.
    """
    original = color_id_buffer.VETTED_COLORABLE_CATEGORY_NAMES
    color_id_buffer.VETTED_COLORABLE_CATEGORY_NAMES = frozenset(names)
    try:
        yield
    finally:
        color_id_buffer.VETTED_COLORABLE_CATEGORY_NAMES = original


# --- _resolve_colorable_category_predicate ----------------------------------

def test_frozen_whitelist_is_preferred_over_the_live_lookup():
    cat_walls = _FakeCategory("Walls", 10)
    cat_odd = _FakeCategory("OddModelCategory", 15)
    diag = _FakeDiag()

    # The live lookup would call BOTH colorable; the frozen whitelist must win.
    with _install_fake_revit_db({10, 15}), _frozen_whitelist("Walls"):
        is_colorable, source = color_id_buffer._resolve_colorable_category_predicate(
            diag=diag, view_id=1
        )

    assert source == "frozen_whitelist"
    assert is_colorable(cat_walls) is True
    assert is_colorable(cat_odd) is False
    assert diag.warnings == [], "the frozen path is the intended one and is not noisy"


def test_empty_whitelist_falls_back_to_the_live_lookup_and_says_so():
    """The pre-freeze state has to keep working -- force-whiting every
    category in the model because a constant has not been populated yet would
    be a far worse regression -- but it must never be silent, or a capture
    that is not reproducible from the source tree looks identical to one that
    is."""
    cat_walls = _FakeCategory("Walls", 10)
    cat_odd = _FakeCategory("OddModelCategory", 15)
    diag = _FakeDiag()

    with _install_fake_revit_db({10}):
        is_colorable, source = color_id_buffer._resolve_colorable_category_predicate(
            diag=diag, view_id=1
        )

    assert source == "live_filterable_lookup"
    assert is_colorable(cat_walls) is True
    assert is_colorable(cat_odd) is False
    assert any(w["callsite"] == "colorable_category_whitelist" for w in diag.warnings)


def test_unresolvable_colorability_fails_closed_not_open():
    """With no colorability answer at all, nothing may be treated as
    colorable. That degrades the capture (no LINK identity, everything
    force-whited) but keeps it correct; failing open would let uncontrolled
    native colors alias assigned palette IDs, which is the one outcome this
    whole mechanism exists to prevent."""
    diag = _FakeDiag()
    with _install_fake_revit_db(with_filter_utilities=False):
        is_colorable, source = color_id_buffer._resolve_colorable_category_predicate(
            diag=diag, view_id=1
        )

    assert source == "unavailable"
    assert is_colorable(_FakeCategory("Walls", 10)) is False
    assert any(e["callsite"] == "colorable_category_whitelist" for e in diag.errors), (
        "an unresolvable whitelist is an error, not a warning -- the capture "
        "needs the whitelist frozen, not a retry"
    )


# --- _category_force_white_targets ------------------------------------------

def test_policy_included_but_unwhitelisted_category_is_force_whited():
    cat_walls = _FakeCategory("Walls", 10)      # policy-included + whitelisted
    cat_odd = _FakeCategory("OddModelCategory", 15)  # policy-included, NOT whitelisted
    doc = _FakeDoc([cat_walls, cat_odd])

    with _install_fake_revit_db(), _frozen_whitelist("Walls"):
        is_colorable, _src = color_id_buffer._resolve_colorable_category_predicate()
        targets = color_id_buffer._category_force_white_targets(doc, is_colorable)

    assert targets == [15]


def test_policy_excluded_model_categories_are_force_whited():
    """The HOST-side gap this closes, asserted against the real policy.

    Rooms, Areas, MEP Spaces and Point Clouds are all in
    collection_policy._EXCLUDED_BIC_NAMES_GLOBAL and all report
    CategoryType.Model. _hidden_category_state() hides only
    CategoryType.Annotation plus VIEW_ONLY_MODEL_BIC_NAMES, so nothing hid
    them, nothing collected them, and nothing painted them -- they rendered
    with native, uncontrolled color. A "policy-included but not whitelisted"
    rule would skip exactly these, since they are policy-EXCLUDED.
    """
    excluded_model_cats = [
        _FakeCategory("Rooms", 20),
        _FakeCategory("Areas", 21),
        _FakeCategory("MEP Spaces", 22),
        _FakeCategory("Point Clouds", 23),
    ]
    doc = _FakeDoc(excluded_model_cats)

    # Deliberately whitelist them all: being colorable must not rescue a
    # category the policy excludes, because nothing will ever paint it.
    with _install_fake_revit_db(), _frozen_whitelist(*[c.Name for c in excluded_model_cats]):
        is_colorable, _src = color_id_buffer._resolve_colorable_category_predicate()
        targets = color_id_buffer._category_force_white_targets(doc, is_colorable)

    assert targets == [20, 21, 22, 23]


def test_colored_categories_are_never_force_whited():
    cat_walls = _FakeCategory("Walls", 10)
    cat_doors = _FakeCategory("Doors", 11)
    doc = _FakeDoc([cat_walls, cat_doors])

    with _install_fake_revit_db(), _frozen_whitelist("Walls", "Doors"):
        is_colorable, _src = color_id_buffer._resolve_colorable_category_predicate()
        targets = color_id_buffer._category_force_white_targets(doc, is_colorable)

    assert targets == [], (
        "a policy-included, whitelisted category gets a real assigned color; "
        "force-whiting it underneath would be pointless work and would make "
        "the capture's force-white set meaningless as a diagnostic"
    )


def test_annotation_categories_are_not_force_white_targets():
    doc = _FakeDoc([_FakeCategory("Text Notes", 30, cat_type=_FakeCategoryType.Annotation)])
    with _install_fake_revit_db(), _frozen_whitelist():
        is_colorable, _src = color_id_buffer._resolve_colorable_category_predicate()
        targets = color_id_buffer._category_force_white_targets(doc, is_colorable)
    assert targets == [], "annotation is hidden by _hidden_category_state, not force-whited"


def test_already_hidden_categories_are_skipped():
    """VIEW_ONLY_MODEL_BIC_NAMES (Detail Items, Lines) is hidden for the
    capture and stays untouched by this pass, as the annotation-layer
    migration expects."""
    cat_detail = _FakeCategory("Detail Items", 40)
    cat_odd = _FakeCategory("OddModelCategory", 15)
    doc = _FakeDoc([cat_detail, cat_odd])

    with _install_fake_revit_db(), _frozen_whitelist():
        is_colorable, _src = color_id_buffer._resolve_colorable_category_predicate()
        targets = color_id_buffer._category_force_white_targets(
            doc, is_colorable, already_hidden_ids=[40]
        )

    assert targets == [15]


def test_force_white_targets_are_sorted_for_a_reproducible_capture():
    cats = [_FakeCategory("C", 33), _FakeCategory("A", 11), _FakeCategory("B", 22)]
    doc = _FakeDoc(cats)
    with _install_fake_revit_db(), _frozen_whitelist():
        is_colorable, _src = color_id_buffer._resolve_colorable_category_predicate()
        targets = color_id_buffer._category_force_white_targets(doc, is_colorable)
    assert targets == [11, 22, 33]


def test_a_category_that_raises_is_reported_not_silently_dropped():
    class _ExplodingCategory(object):
        Name = "Boom"

        @property
        def CategoryType(self):
            raise RuntimeError("category is in a bad state")

    doc = _FakeDoc([_ExplodingCategory(), _FakeCategory("OddModelCategory", 15)])
    diag = _FakeDiag()
    with _install_fake_revit_db(), _frozen_whitelist():
        is_colorable, _src = color_id_buffer._resolve_colorable_category_predicate()
        targets = color_id_buffer._category_force_white_targets(doc, is_colorable, diag=diag)

    assert targets == [15], "one bad category must not abort the scan"
    assert any(w["callsite"] == "force_white_target_scan" for w in diag.warnings)


# --- _force_white_category: apply + snapshot --------------------------------

def test_force_white_paints_the_uncolorable_sentinel():
    view = _FakeView()
    snapshots = {}
    solid = _FakeElementId(777)

    with _install_fake_revit_db():
        applied = color_id_buffer._force_white_category(view, 15, solid, snapshots)

    assert applied is True
    ogs = view.category_overrides[15]
    assert ogs.ProjectionLineColor.as_tuple() == tuple(UNCOLORABLE_SENTINEL_RGB)
    assert ogs.SurfaceForegroundPatternColor.as_tuple() == tuple(UNCOLORABLE_SENTINEL_RGB)
    assert ogs.CutForegroundPatternColor.as_tuple() == tuple(UNCOLORABLE_SENTINEL_RGB)
    assert ogs.CutLineColor.as_tuple() == tuple(UNCOLORABLE_SENTINEL_RGB)
    assert ogs.SurfaceForegroundPatternId is solid
    assert ogs.Halftone is False
    assert 15 in snapshots


def test_force_white_keeps_the_first_snapshot_per_category():
    """A LINK category whose filter fails arrives here AFTER the halftone
    pass has already rewritten its overrides. Re-snapshotting then would
    capture Stage A's own mutation as if it were the user's original state,
    and restore would write Stage A's values back permanently."""
    view = _FakeView()
    snapshots = {}
    solid = _FakeElementId(777)

    with _install_fake_revit_db():
        original = view.GetCategoryOverrides(_FakeElementId(15))
        original.Halftone = True  # the user's real state
        color_id_buffer._force_white_category(view, 15, solid, snapshots)
        first_snapshot = dict(snapshots[15])
        # Second pass over the same category (the failed-filter case).
        color_id_buffer._force_white_category(view, 15, solid, snapshots)

    assert snapshots[15] == first_snapshot
    assert snapshots[15]["Halftone"] is True, (
        "the snapshot must hold the user's halftone, not the False the "
        "flat-color paint just wrote"
    )


def test_force_white_failure_is_reported_and_returns_false():
    class _RefusingView(_FakeView):
        def SetCategoryOverrides(self, cat_id, ogs):
            raise RuntimeError("category does not accept view overrides")

    view = _RefusingView()
    diag = _FakeDiag()
    with _install_fake_revit_db():
        applied = color_id_buffer._force_white_category(
            view, 15, _FakeElementId(777), {}, diag=diag, view_id=1
        )

    assert applied is False
    assert any(w["callsite"] == "force_white_category" for w in diag.warnings), (
        "a category that cannot be force-whited may render an uncontrolled "
        "native color -- CLAUDE.md's no-silent-failure rule"
    )


# --- category override snapshot / restore round trip ------------------------

def test_snapshot_restore_round_trips_every_field_the_paint_overwrites():
    view = _FakeView()
    snapshots = {}
    with _install_fake_revit_db():
        ogs = view.GetCategoryOverrides(_FakeElementId(15))
        # A category carrying real user overrides, not a blank one.
        ogs.SetSurfaceForegroundPatternId(_FakeElementId(501))
        ogs.SetSurfaceForegroundPatternColor(_FakeColor(1, 2, 3))
        ogs.SetSurfaceForegroundPatternVisible(False)
        ogs.SetCutForegroundPatternId(_FakeElementId(502))
        ogs.SetCutForegroundPatternColor(_FakeColor(4, 5, 6))
        ogs.SetCutForegroundPatternVisible(False)
        ogs.SetProjectionLineColor(_FakeColor(7, 8, 9))
        ogs.SetCutLineColor(_FakeColor(10, 11, 12))
        ogs.SetSurfaceTransparency(35)
        ogs.SetHalftone(True)
        before = ogs.snapshot_tuple()

        color_id_buffer._force_white_category(view, 15, _FakeElementId(777), snapshots)
        painted = view.category_overrides[15]
        assert painted.snapshot_tuple() != before, "the paint must actually change something"

        unrestored = color_id_buffer._restore_category_ogs_fields(painted, snapshots[15])

    assert unrestored == []
    assert painted.snapshot_tuple() == before


def test_restore_preserves_fields_the_paint_never_touched():
    view = _FakeView()
    snapshots = {}
    with _install_fake_revit_db():
        ogs = view.GetCategoryOverrides(_FakeElementId(15))
        ogs.ProjectionLineWeight = 12  # not in _CATEGORY_OGS_FIELDS
        color_id_buffer._force_white_category(view, 15, _FakeElementId(777), snapshots)
        painted = view.category_overrides[15]
        color_id_buffer._restore_category_ogs_fields(painted, snapshots[15])

    assert painted.ProjectionLineWeight == 12, (
        "force-white is applied on top of a category's existing overrides and "
        "restores only what it overwrote; unrelated override state is never "
        "captured, never changed, and never needs restoring"
    )


def test_an_unset_color_round_trips_as_unset_not_as_black():
    view = _FakeView()
    snapshots = {}
    with _install_fake_revit_db():
        ogs = view.GetCategoryOverrides(_FakeElementId(15))
        assert ogs.ProjectionLineColor.IsValid is False  # no color set
        color_id_buffer._force_white_category(view, 15, _FakeElementId(777), snapshots)
        painted = view.category_overrides[15]
        color_id_buffer._restore_category_ogs_fields(painted, snapshots[15])

    assert painted.ProjectionLineColor.IsValid is False, (
        "'no color set' must restore as no color, not as the (0,0,0) a naive "
        "Red/Green/Blue read would produce"
    )


def test_a_field_missing_on_this_revit_host_is_reported_not_guessed():
    """Property names have shifted across Revit versions. A field that cannot
    be read must come back as unrestorable rather than being invented, or
    restore would write a guessed value over a user's category graphics."""
    class _OGSMissingTransparency(_FakeOGS):
        def __init__(self):
            super(_OGSMissingTransparency, self).__init__()
            del self.Transparency

    with _install_fake_revit_db():
        ogs = _OGSMissingTransparency()
        snapshot = color_id_buffer._capture_category_ogs_fields(ogs)
        assert snapshot["Transparency"] is color_id_buffer._MISSING_OGS_FIELD
        unrestored = color_id_buffer._restore_category_ogs_fields(_FakeOGS(), snapshot)

    assert unrestored == ["Transparency"]


# --- Override precedence: Instance/Element > Filter > Category --------------

def _resolve_rendered_color(view, cat_id_int, filter_id_int=None, elem_id_int=None):
    """Resolve which override layer supplies an element's color, following
    Revit's documented precedence: Instance/Element > Filter > Category.

    Revit's renderer cannot be invoked from pytest, so this models the
    documented rule and applies it to the layers the production code actually
    recorded on the view. What it therefore proves is the part that is ours to
    get wrong: that force-white is applied at the CATEGORY layer only, never
    at the filter or element layer, and never to a category that has a real
    color coming. Whether Revit itself honors the documented order on a live
    model is the one thing this cannot establish -- that needs a real capture.
    """
    if elem_id_int is not None and elem_id_int in view.element_overrides:
        return view.element_overrides[elem_id_int].ProjectionLineColor.as_tuple()
    if filter_id_int is not None and filter_id_int in view.filter_overrides:
        return view.filter_overrides[filter_id_int].ProjectionLineColor.as_tuple()
    if cat_id_int in view.category_overrides:
        return view.category_overrides[cat_id_int].ProjectionLineColor.as_tuple()
    return None


def test_force_white_never_masks_a_host_element_or_link_filter_color():
    """Both real-color layers, over one force-whited category, at once.

    A HOST element with its own individual override and a LINK element under a
    colored category filter must each render their real assigned color; only
    content with nothing above the category layer renders the sentinel.
    """
    cat_id, filter_id, host_elem_id = 15, 900, 555
    host_color = (10, 20, 30)
    link_filter_color = (40, 50, 60)
    view = _FakeView()
    solid = _FakeElementId(777)

    with _install_fake_revit_db():
        # Category layer: the force-white safety net.
        color_id_buffer._force_white_category(view, cat_id, solid, {})
        # Filter layer: a colored LINK category filter.
        view.SetFilterOverrides(
            _FakeElementId(filter_id),
            color_id_buffer._build_flat_color_ogs(solid, _FakeColor(*link_filter_color)),
        )
        # Element layer: the HOST per-element paint.
        view.SetElementOverrides(
            _FakeElementId(host_elem_id),
            color_id_buffer._build_flat_color_ogs(solid, _FakeColor(*host_color)),
        )

    assert _resolve_rendered_color(
        view, cat_id, filter_id_int=filter_id, elem_id_int=host_elem_id
    ) == host_color, "a HOST element's own override outranks both layers beneath it"

    assert _resolve_rendered_color(
        view, cat_id, filter_id_int=filter_id
    ) == link_filter_color, (
        "a LINK element under a colored filter outranks the category-level "
        "force-white sitting beneath it"
    )

    assert _resolve_rendered_color(view, cat_id) == tuple(UNCOLORABLE_SENTINEL_RGB), (
        "content with no override above the category layer -- the actually "
        "uncontrolled content -- is what renders the sentinel"
    )


def test_force_white_is_applied_at_the_category_layer_only():
    """The structural half of the precedence claim: if force-white ever leaked
    into the filter or element layer it would outrank the real colors instead
    of sitting under them, and the precedence rule above could not save it."""
    view = _FakeView()
    with _install_fake_revit_db():
        color_id_buffer._force_white_category(view, 15, _FakeElementId(777), {})

    assert view.set_category_override_calls == [15]
    assert view.filter_overrides == {}
    assert view.element_overrides == {}


# --- Halftone / force-white ownership handoff -------------------------------

def test_force_white_after_halftone_restores_the_users_halftone_not_stage_as():
    """The overlap case: a colorable LINK category whose filter later fails.

    The halftone pass neutralized it first, so a snapshot taken afterward
    would record Stage A's own Halftone=False as the user's original state
    and restore would make the neutralization permanent.
    """
    view = _FakeView()
    snapshots = {}
    halftone_state = {}

    with _install_fake_revit_db():
        ogs = view.GetCategoryOverrides(_FakeElementId(15))
        ogs.SetHalftone(True)  # the user's real state

        # The halftone neutralization pass, as export_color_id_buffer_view runs it.
        halftone_state[15] = ogs.Halftone
        ogs.SetHalftone(False)
        view.SetCategoryOverrides(_FakeElementId(15), ogs)

        # ...then the category's filter fails and it needs the safety net.
        applied = color_id_buffer._force_white_after_halftone(
            view, 15, _FakeElementId(777), snapshots, halftone_state
        )

        assert applied is True
        assert snapshots[15]["Halftone"] is True, (
            "the snapshot must carry the user's halftone, not the False the "
            "halftone pass wrote just before it"
        )
        assert 15 not in halftone_state, (
            "exactly one restore path may own a category -- two that both "
            "write it would have to be correctly ordered to agree"
        )

        color_id_buffer._restore_category_ogs_fields(view.category_overrides[15], snapshots[15])

    assert view.category_overrides[15].Halftone is True


def test_force_white_after_halftone_leaves_an_untouched_category_alone():
    view = _FakeView()
    snapshots = {}
    halftone_state = {}
    with _install_fake_revit_db():
        view.GetCategoryOverrides(_FakeElementId(15)).SetHalftone(True)
        color_id_buffer._force_white_after_halftone(
            view, 15, _FakeElementId(777), snapshots, halftone_state
        )
    assert snapshots[15]["Halftone"] is True, (
        "with no halftone entry to adopt, the snapshot's own read is already "
        "the user's original state"
    )
    assert halftone_state == {}


def test_halftone_entry_is_handed_back_when_no_snapshot_could_be_taken():
    """If reading the category's overrides fails outright there is no
    force-white snapshot to own the restore, so the halftone entry must go
    back rather than being dropped -- otherwise Stage A's neutralization
    would be left in place permanently."""
    class _UnreadableView(_FakeView):
        def GetCategoryOverrides(self, cat_id):
            raise RuntimeError("cannot read category overrides")

    view = _UnreadableView()
    snapshots = {}
    halftone_state = {15: True}
    diag = _FakeDiag()

    with _install_fake_revit_db():
        applied = color_id_buffer._force_white_after_halftone(
            view, 15, _FakeElementId(777), snapshots, halftone_state, diag=diag
        )

    assert applied is False
    assert snapshots == {}
    assert halftone_state == {15: True}
    assert diag.warnings


# --- Document-envelope carve-out --------------------------------------------

def test_document_envelope_categories_are_exempt_from_force_white():
    """RVT Links and Imports are CategoryType.Model and policy-excluded, so
    the plain rule would force-white both. Both are carved out: an override
    on the link-instance category risks tinting the very LINK geometry the
    category filters exist to color, and Imports is the parent of every DWG
    layer subcategory, which DWG-out-of-scope forbids reaching into. See
    FORCE_WHITE_EXEMPT_BIC_NAMES.
    """
    cat_rvt_links = _FakeCategory("RVT Links", 60)
    cat_imports = _FakeCategory("Imports", 61)
    cat_rooms = _FakeCategory("Rooms", 20)  # a normal policy-excluded Model category
    doc = _FakeDoc([cat_rvt_links, cat_imports, cat_rooms])

    # resolve_category_ids() needs a Revit BuiltInCategory to resolve ids; it
    # returns an empty set outside Revit, so the NAME path in the real policy
    # is what has to carry the exemption here -- which is exactly the
    # situation on any Revit where a BIC name fails to resolve.
    with _install_fake_revit_db(), _frozen_whitelist():
        is_colorable, _src = color_id_buffer._resolve_colorable_category_predicate()
        targets = color_id_buffer._category_force_white_targets(doc, is_colorable)

    assert 20 in targets, "a normal policy-excluded Model category is still force-whited"
    assert 60 not in targets, "RVT Links must stay exempt"
    assert 61 not in targets, "Imports must stay exempt"
