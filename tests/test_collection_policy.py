# -*- coding: utf-8 -*-
"""
Tests for collection_policy.py exclude-list model.

Coverage:
- Model category not in old allowlist is now included by default.
- Explicitly excluded categories (Rooms, Grids, etc.) remain excluded.
- Non-model categories (Annotation, AnalyticalModel) are excluded.
- Host view-specific lines are excluded; non-view-specific lines pass.
- Link/DWG lines are excluded entirely.
- PolicyStats counters are correct.
"""

import pytest
from vop_interwoven.revit.collection_policy import (
    should_include_element,
    excluded_bic_names_global,
    PolicyStats,
    _FALLBACK_EXCLUDED_CATEGORY_NAMES,
    _CATEGORY_TYPE_MODEL_INT,
)


# ---------------------------------------------------------------------------
# Fake Revit objects (pytest / no Revit runtime)
# ---------------------------------------------------------------------------

class _FakeId:
    def __init__(self, val):
        self.IntegerValue = val


class _FakeCategory:
    """Minimal fake Category.

    category_type:
        1 (or _CATEGORY_TYPE_MODEL_INT) → Model
        2 → Annotation
        None → type unknown (triggers safe-include fallback)
    """
    def __init__(self, name, cat_id=9999, category_type=1):
        self.Name = name
        self.Id = _FakeId(cat_id)
        # Setting CategoryType=None simulates Revit unavailability path
        self.CategoryType = category_type


class _FakeElem:
    def __init__(self, category_name, cat_id=9999, category_type=1, view_specific=False, source_type="HOST"):
        self.Category = _FakeCategory(category_name, cat_id=cat_id, category_type=category_type)
        self.ViewSpecific = view_specific


class _FakeDoc:
    """Minimal fake doc.  resolve_category_ids returns empty set (triggers name fallback)."""
    class Settings:
        class Categories:
            @staticmethod
            def get_Item(bic):
                return None
    Settings = Settings()


_DOC = _FakeDoc()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _include(elem, source_type="HOST"):
    include, reason, cat = should_include_element(
        elem=elem, doc=_DOC, source_type=source_type
    )
    return include, reason, cat


# ---------------------------------------------------------------------------
# 1. Model categories not in old allowlist are included
# ---------------------------------------------------------------------------

class TestNewModelCategoryIncluded:
    """Specialty Equipment and other CategoryType.Model categories that were NOT in
    the old _INCLUDED_BIC_NAMES_BASE allowlist must now be included."""

    def test_specialty_equipment_included(self):
        elem = _FakeElem("Specialty Equipment", cat_id=1001, category_type=_CATEGORY_TYPE_MODEL_INT)
        ok, reason, _ = _include(elem)
        assert ok is True, "Expected included, got reason={}".format(reason)
        assert reason == "included"

    def test_furniture_systems_included(self):
        elem = _FakeElem("Furniture Systems", cat_id=1002, category_type=_CATEGORY_TYPE_MODEL_INT)
        ok, reason, _ = _include(elem)
        assert ok is True
        assert reason == "included"

    def test_site_category_included(self):
        elem = _FakeElem("Site", cat_id=1003, category_type=_CATEGORY_TYPE_MODEL_INT)
        ok, reason, _ = _include(elem)
        assert ok is True
        assert reason == "included"

    def test_topography_category_included(self):
        elem = _FakeElem("Topography", cat_id=1004, category_type=_CATEGORY_TYPE_MODEL_INT)
        ok, reason, _ = _include(elem)
        assert ok is True
        assert reason == "included"

    def test_known_allowlist_category_still_included(self):
        """Walls were in the old allowlist -- verify they still work."""
        elem = _FakeElem("Walls", cat_id=2000, category_type=_CATEGORY_TYPE_MODEL_INT)
        ok, reason, _ = _include(elem)
        assert ok is True
        assert reason == "included"


# ---------------------------------------------------------------------------
# 2. Explicitly excluded categories remain excluded
# ---------------------------------------------------------------------------

class TestExplicitExclusions:

    @pytest.mark.parametrize("cat_name", [
        "Rooms",
        "Areas",
        "MEP Spaces",
        "Grids",
        "Grid Heads",
        "Levels",
        "Level Heads",
        "Cameras",
        "Point Clouds",
        "Section Boxes",
        "Sun Path",
        "Adaptive Points",
        "Detail Items",
        "Elevation Marks",
        "Callout Heads",
        "Section Heads",
        "Section Marks",
        "Reference Viewers",
        "Viewers",
        # Document-envelope elements: bounds blowout risk
        "RVT Links",
        "Imports",
    ])
    def test_excluded_category(self, cat_name):
        # category_type=1 (Model) to prove exclusion happens before CategoryType check
        elem = _FakeElem(cat_name, cat_id=9999, category_type=_CATEGORY_TYPE_MODEL_INT)
        ok, reason, _ = _include(elem)
        assert ok is False, "Expected {!r} to be excluded".format(cat_name)
        assert reason == "excluded_global"

    def test_exclusion_takes_precedence_over_model_type(self):
        """An explicitly excluded name must be rejected even if CategoryType=Model."""
        elem = _FakeElem("Rooms", cat_id=9999, category_type=_CATEGORY_TYPE_MODEL_INT)
        ok, reason, _ = _include(elem)
        assert ok is False
        assert reason == "excluded_global"

    def test_rvt_links_excluded_prevents_bounds_blowout(self):
        """RevitLinkInstance (category 'RVT Links') must be excluded from the HOST pass.

        These are document-envelope elements whose bbox spans the entire linked
        model.  Admitting them causes a full-grid proxy flood (all cells occupied).
        Geometry from the linked document is already expanded by linked_documents.py.
        """
        elem = _FakeElem("RVT Links", cat_id=9998, category_type=_CATEGORY_TYPE_MODEL_INT)
        ok, reason, _ = _include(elem, source_type="HOST")
        assert ok is False
        assert reason == "excluded_global"

    def test_imports_excluded_prevents_double_processing(self):
        """ImportInstance (category 'Imports') must be excluded from the HOST pass.

        DWG imports are already collected by linked_documents.py; admitting them
        from the HOST pass causes double-rasterization with an inflated bbox.
        """
        elem = _FakeElem("Imports", cat_id=9997, category_type=_CATEGORY_TYPE_MODEL_INT)
        ok, reason, _ = _include(elem, source_type="HOST")
        assert ok is False
        assert reason == "excluded_global"


# ---------------------------------------------------------------------------
# 3. Non-model categories are excluded from the model pass
# ---------------------------------------------------------------------------

class TestNonModelCategoryExclusion:

    def test_annotation_category_excluded(self):
        # category_type=2 -> Annotation (int comparison in fallback path)
        elem = _FakeElem("Text Notes", cat_id=5001, category_type=2)
        ok, reason, _ = _include(elem)
        assert ok is False
        assert reason == "non_model_category"

    def test_analytical_model_category_excluded(self):
        elem = _FakeElem("Analytical Beams", cat_id=5002, category_type=4)
        ok, reason, _ = _include(elem)
        assert ok is False
        assert reason == "non_model_category"

    def test_model_type_included(self):
        elem = _FakeElem("Generic Models", cat_id=5003, category_type=_CATEGORY_TYPE_MODEL_INT)
        ok, reason, _ = _include(elem)
        assert ok is True
        assert reason == "included"

    def test_string_annotation_type_excluded(self):
        """String 'Annotation' is used in pytest string-comparison fallback path."""
        elem = _FakeElem("Some Annotation", cat_id=5004)
        elem.Category.CategoryType = "Annotation"
        ok, reason, _ = _include(elem)
        assert ok is False
        assert reason == "non_model_category"

    def test_string_model_type_included(self):
        """String 'Model' is used in pytest string-comparison fallback path."""
        elem = _FakeElem("Some Model", cat_id=5005)
        elem.Category.CategoryType = "Model"
        ok, reason, _ = _include(elem)
        assert ok is True
        assert reason == "included"

    def test_no_category_type_defaults_to_include(self):
        """When CategoryType is entirely absent, include by default (safe)."""
        elem = _FakeElem("Unknown Category", cat_id=5006, category_type=None)
        ok, reason, _ = _include(elem)
        assert ok is True
        assert reason == "included"


# ---------------------------------------------------------------------------
# 4. No-category elements are excluded
# ---------------------------------------------------------------------------

class TestNoCategoryElement:

    def test_no_category_excluded(self):
        class _NoCatElem:
            Category = None

        ok, reason, cat = should_include_element(elem=_NoCatElem(), doc=_DOC, source_type="HOST")
        assert ok is False
        assert reason == "no_category"
        assert cat == "<NO_CATEGORY>"


# ---------------------------------------------------------------------------
# 5. HOST view-specific line rules
# ---------------------------------------------------------------------------

class TestHostLineRules:

    def test_host_view_specific_line_excluded(self):
        elem = _FakeElem("Lines", cat_id=100, category_type=_CATEGORY_TYPE_MODEL_INT, view_specific=True)
        ok, reason, _ = _include(elem, source_type="HOST")
        assert ok is False
        assert reason == "view_specific_line"

    def test_host_non_view_specific_line_included(self):
        elem = _FakeElem("Lines", cat_id=100, category_type=_CATEGORY_TYPE_MODEL_INT, view_specific=False)
        ok, reason, _ = _include(elem, source_type="HOST")
        assert ok is True
        assert reason == "included"


# ---------------------------------------------------------------------------
# 6. LINK/DWG line rules
# ---------------------------------------------------------------------------

class TestLinkDWGLineRules:

    def test_link_line_excluded(self):
        elem = _FakeElem("Lines", cat_id=100, category_type=_CATEGORY_TYPE_MODEL_INT)
        ok, reason, _ = _include(elem, source_type="LINK")
        assert ok is False
        assert reason == "lines_excluded_for_source"

    def test_dwg_line_excluded(self):
        elem = _FakeElem("Lines", cat_id=100, category_type=_CATEGORY_TYPE_MODEL_INT)
        ok, reason, _ = _include(elem, source_type="DWG")
        assert ok is False
        assert reason == "lines_excluded_for_source"

    def test_link_non_line_model_category_included(self):
        elem = _FakeElem("Walls", cat_id=200, category_type=_CATEGORY_TYPE_MODEL_INT)
        ok, reason, _ = _include(elem, source_type="LINK")
        assert ok is True
        assert reason == "included"


# ---------------------------------------------------------------------------
# 7. PolicyStats counters
# ---------------------------------------------------------------------------

class TestPolicyStats:

    def test_included_counter(self):
        stats = PolicyStats()
        elem = _FakeElem("Walls", cat_id=200, category_type=_CATEGORY_TYPE_MODEL_INT)
        ok, _, _ = should_include_element(elem=elem, doc=_DOC, source_type="HOST", stats=stats)
        assert ok is True
        assert stats.seen_total == 1
        assert stats.included_total == 1
        assert stats.excluded_total == 0

    def test_excluded_counter(self):
        stats = PolicyStats()
        elem = _FakeElem("Rooms", cat_id=9999, category_type=_CATEGORY_TYPE_MODEL_INT)
        ok, reason, _ = should_include_element(elem=elem, doc=_DOC, source_type="HOST", stats=stats)
        assert ok is False
        assert stats.seen_total == 1
        assert stats.excluded_total == 1
        assert "excluded_global" in stats.excluded_by_reason

    def test_non_model_category_counter(self):
        stats = PolicyStats()
        elem = _FakeElem("Text Notes", cat_id=5001, category_type=2)
        ok, reason, _ = should_include_element(elem=elem, doc=_DOC, source_type="HOST", stats=stats)
        assert ok is False
        assert "non_model_category" in stats.excluded_by_reason

    def test_no_allowlist_reason_present(self):
        """The old 'not_in_allowlist' reason must never appear."""
        stats = PolicyStats()
        for cat_name, cat_type in [
            ("Specialty Equipment", _CATEGORY_TYPE_MODEL_INT),
            ("Rooms", _CATEGORY_TYPE_MODEL_INT),
            ("Text Notes", 2),
            ("Lines", _CATEGORY_TYPE_MODEL_INT),
        ]:
            elem = _FakeElem(cat_name, cat_id=9001, category_type=cat_type)
            should_include_element(elem=elem, doc=_DOC, source_type="HOST", stats=stats)
        assert "not_in_allowlist" not in stats.excluded_by_reason


# ---------------------------------------------------------------------------
# 8. excluded_bic_names_global() / fallback set consistency
# ---------------------------------------------------------------------------

class TestExclusionLists:

    def test_excluded_bic_names_global_returns_tuple(self):
        names = excluded_bic_names_global()
        assert isinstance(names, tuple)
        assert len(names) > 0

    def test_rooms_in_excluded_global(self):
        assert "OST_Rooms" in excluded_bic_names_global()

    def test_cameras_in_excluded_global(self):
        assert "OST_Cameras" in excluded_bic_names_global()

    def test_point_clouds_in_excluded_global(self):
        assert "OST_PointClouds" in excluded_bic_names_global()

    def test_fallback_excluded_covers_key_names(self):
        required = {"Rooms", "Areas", "Grids", "Levels", "Cameras", "Point Clouds"}
        assert required.issubset(_FALLBACK_EXCLUDED_CATEGORY_NAMES), (
            "Fallback set is missing: {}".format(required - _FALLBACK_EXCLUDED_CATEGORY_NAMES)
        )

    def test_non_physical_reference_categories_excluded_by_both_paths(self):
        """The 8 non-physical reference/metadata categories must be excluded by
        the stable BuiltInCategory id path as well as by name.

        Category.Name is localized: on a non-English Revit the English
        literals in _FALLBACK_EXCLUDED_CATEGORY_NAMES match nothing, and only
        the id path resolved from _EXCLUDED_BIC_NAMES_GLOBAL keeps these
        categories out of LINK category discovery -- where, reporting
        CategoryType.Model with no visible geometry, they land in the
        "uncolorable" branch and can hide a whole link instance.
        """
        by_name = {
            "Internal Origin", "Project Base Point", "Survey Point", "Sheets",
            "Project Information", "Material Assets", "Materials",
            "Legend Components",
        }
        by_bic = {
            "OST_InternalOrigin", "OST_ProjectBasePoint", "OST_SharedBasePoint",
            "OST_Sheets", "OST_ProjectInformation", "OST_MaterialAssets",
            "OST_Materials", "OST_LegendComponents",
        }
        assert by_name.issubset(_FALLBACK_EXCLUDED_CATEGORY_NAMES), (
            "missing name coverage: {}".format(by_name - _FALLBACK_EXCLUDED_CATEGORY_NAMES)
        )
        missing_bic = by_bic - set(excluded_bic_names_global())
        assert not missing_bic, (
            "name-only exclusion is not enough on a localized Revit; "
            "missing stable-id coverage: {}".format(missing_bic)
        )

    def test_fallback_names_and_bic_list_are_the_same_length(self):
        """One human-readable name per BuiltInCategory name -- the two lists
        are the same exclusion set expressed twice, and drift between them is
        how a category ends up excluded in English Revit only."""
        assert len(_FALLBACK_EXCLUDED_CATEGORY_NAMES) == len(excluded_bic_names_global())
