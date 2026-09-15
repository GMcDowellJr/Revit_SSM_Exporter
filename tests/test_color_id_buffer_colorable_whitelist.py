"""Unit tests for Stage A's vetted-colorable category whitelist.

Covers vop_interwoven/color_id_buffer.py's
_resolve_colorable_category_predicate: the single "can Stage A give this
category a real color?" answer, resolved once per capture and handed to LINK
category-filter discovery.

Same fake-Autodesk.Revit.DB technique as
tests/test_color_id_buffer_link_category_filters.py: the function under test
does a local ``from Autodesk.Revit.DB import ...`` inside its body, so only a
fake module installed into sys.modules can intercept it.
"""
import contextlib
import sys
import types

import vop_interwoven.color_id_buffer as color_id_buffer


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


class _FakeParameterFilterUtilities(object):
    def __init__(self, filterable_ids):
        self._filterable_ids = filterable_ids

    def GetAllFilterableCategories(self):
        return [_FakeElementId(v) for v in self._filterable_ids]


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
    if with_filter_utilities:
        fake_db.ParameterFilterUtilities = _FakeParameterFilterUtilities(filterable_category_ids)

    original = sys.modules.get("Autodesk.Revit.DB")
    sys.modules["Autodesk.Revit.DB"] = fake_db
    try:
        yield fake_db
    finally:
        if original is None:
            sys.modules.pop("Autodesk.Revit.DB", None)
        else:
            sys.modules["Autodesk.Revit.DB"] = original


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
    """The pre-freeze state has to keep working -- dropping every LINK
    category filter because a constant has not been populated yet would be a
    far worse regression -- but it must never be silent, or a capture that is
    not reproducible from the source tree looks identical to one that is."""
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


def test_unresolvable_colorability_resolves_to_nothing_colorable():
    """With no colorability answer at all, nothing is treated as colorable:
    no LINK category gets a filter, so LINK content renders with its native
    color and carries no identity in this capture.

    That is the same outcome any non-whitelisted content already has (see
    _model_categories_in_linked_doc's ACCEPTED GAP note) rather than a new
    class of failure -- but it is total rather than partial, which is why it
    is recorded as an error."""
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
