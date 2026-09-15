"""Category policy (single source of truth).

Notes
-----
- Must be importable under pytest (outside Revit). Do not import Autodesk at module import time.
- Revit-only resolution happens inside functions.

This policy implements exclude-by-default for model geometry: all CategoryType.Model categories
are included unless explicitly excluded.  Annotation, Analytical, and Internal categories are
excluded by virtue of not being CategoryType.Model.

Decision order in should_include_element():
  1. Exclude elements with no category.
  2. Apply source-specific line rules:
       HOST  – exclude view-specific lines (OST_Lines with ViewSpecific=True).
       LINK/DWG – exclude all lines.
  3. Apply explicit model exclusion list (_EXCLUDED_BIC_NAMES_GLOBAL).
  4. Include if category.CategoryType == CategoryType.Model.
  5. Exclude as non_model_category otherwise.

Per-source behavior
-------------------
HOST:
  - OST_Lines are passed through unless they are view-specific.
LINK/DWG:
  - Lines are excluded entirely (historical behavior, avoids counting graphics-only content).
"""

from typing import Dict, Iterable, Optional, Set, Tuple


class PolicyStats(object):
    """Aggregated counters for policy filtering (runtime-safe)."""

    def __init__(self):
        self.seen_total = 0
        self.included_total = 0
        self.excluded_total = 0
        self.excluded_by_reason = {}
        self.excluded_by_category = {}

    def mark_excluded(self, reason, category_name):
        self.excluded_total += 1
        self.excluded_by_reason[reason] = self.excluded_by_reason.get(reason, 0) + 1
        self.excluded_by_category[category_name] = (
            self.excluded_by_category.get(category_name, 0) + 1
        )

    def mark_included(self):
        self.included_total += 1


# Cache: (id(doc), bic_names_tuple) -> set(category_ids).
_CATEGORY_ID_CACHE = {}

# Lines are special-cased by source.
_BIC_LINES = "OST_Lines"

# Integer value of CategoryType.Model in the Revit API enum.
# Used for cross-env comparison (int mocks work in pytest without importing Revit).
_CATEGORY_TYPE_MODEL_INT = 1

# Explicit model exclusion list.  These categories are excluded from model occupancy
# collection regardless of their CategoryType, to avoid bounds blowouts and semantic
# contamination.  Some may be reintroduced in a later PR with TRACK_ONLY or
# REFERENCE_OVERLAY roles.
_EXCLUDED_BIC_NAMES_GLOBAL: Tuple[str, ...] = (
    # Navigation / view mechanics
    "OST_Grids",
    "OST_GridHeads",
    "OST_Levels",
    "OST_LevelHeads",
    "OST_SectionHeads",
    "OST_SectionMarks",
    "OST_ElevationMarks",
    "OST_CalloutHeads",
    "OST_ReferenceViewer",
    "OST_Viewers",
    "OST_Cameras",
    "OST_SunPath",
    "OST_SectionBox",
    "OST_AdaptivePoints",
    "OST_Reveals",
    # Non-physical / analysis
    "OST_Rooms",
    "OST_Areas",
    "OST_MEPSpaces",
    # Explicitly non-target
    "OST_DetailComponents",
    "OST_PointClouds",
    # Document-envelope elements — bounds span entire linked/imported document.
    # Actual geometry is already expanded by linked_documents.py; admitting these
    # from the HOST pass causes full-grid bbox proxy floods.
    "OST_RVT_Links",
    "OST_ImportObjectStyles",
)

# Human-readable category names excluded by should_include_element's name check.
# That check runs unconditionally and FIRST (inside and outside Revit alike), so
# this set is not merely a pytest fallback for _EXCLUDED_BIC_NAMES_GLOBAL: the
# first block below mirrors that tuple's names one-for-one and must stay in sync
# with it, while the second block holds names with no BuiltInCategory counterpart
# in that tuple, excluded by name only.
_FALLBACK_EXCLUDED_CATEGORY_NAMES = {
    "Grids",
    "Grid Heads",
    "Levels",
    "Level Heads",
    "Section Heads",
    "Section Marks",
    "Elevation Marks",
    "Callout Heads",
    "Reference Viewers",
    "Viewers",
    "Cameras",
    "Sun Path",
    "Section Boxes",
    "Adaptive Points",
    "Reveals",
    "Rooms",
    "Areas",
    "MEP Spaces",
    "Detail Items",
    "Point Clouds",
    "RVT Links",
    "Imports",
    # Non-physical reference/metadata categories confirmed (empirically, via a
    # real LINK uncolorable-category diagnostic, not assumed) to report
    # CategoryType.Model in Revit's API despite carrying no visible model
    # geometry.  Each one previously reached color_id_buffer.py's
    # _model_categories_in_linked_doc "uncolorable" branch and could trigger
    # whole-link-instance hiding for an unrelated, genuinely colorable
    # category sharing the same instance -- the second half of that failure
    # mode is fixed in color_id_buffer.py by scoping the hide decision to
    # categories actually present in the view.
    "Internal Origin",
    "Project Base Point",
    "Survey Point",
    "Sheets",
    "Project Information",
    "Material Assets",
    "Materials",
    "Legend Components",
}


# Annotation categories collected by the VOP annotation pass (collect_2d_annotations).
# These must remain visible in view_raster even though some overlap with the model
# exclude list (e.g. OST_DetailComponents).
_ANNOTATION_INCLUDED_BIC_NAMES: Tuple[str, ...] = (
    "OST_TextNotes",
    "OST_Dimensions",
    "OST_RoomTags",
    "OST_SpaceTags",
    "OST_AreaTags",
    "OST_DoorTags",
    "OST_WindowTags",
    "OST_WallTags",
    "OST_MEPSpaceTags",
    "OST_GenericAnnotation",
    "OST_FilledRegion",
    "OST_Lines",
    "OST_DetailComponents",
    "OST_KeynoteTags",
)


def annotation_included_bic_names() -> Tuple[str, ...]:
    """Category names collected by the VOP annotation pass.

    Used by view_raster to ensure these categories are not hidden — the
    effective hide set for view_raster is excluded_bic_names_global()
    minus annotation_included_bic_names().
    """
    return _ANNOTATION_INCLUDED_BIC_NAMES


def excluded_bic_names_global() -> Tuple[str, ...]:
    return _EXCLUDED_BIC_NAMES_GLOBAL


def _try_import_bic():
    """Import BuiltInCategory lazily (Revit-only)."""
    from Autodesk.Revit.DB import BuiltInCategory  # type: ignore
    return BuiltInCategory


def _try_get_category_id(doc, bic_name: str) -> Optional[int]:
    """Resolve a BuiltInCategory name to a Category integer id for a given doc."""
    try:
        BuiltInCategory = _try_import_bic()
        bic = getattr(BuiltInCategory, bic_name, None)
        if bic is None:
            return None
        cat = doc.Settings.Categories.get_Item(bic)
        if cat is None or cat.Id is None:
            return None
        return int(cat.Id.IntegerValue)
    except Exception:
        return None


def resolve_category_ids(doc, bic_names: Iterable[str]) -> Set[int]:
    """Resolve BuiltInCategory names to integer category ids for this doc (cached)."""
    try:
        key = (id(doc), tuple(bic_names))
        cached = _CATEGORY_ID_CACHE.get(key)
        if cached is not None:
            return set(cached)
    except Exception:
        key = None

    out: Set[int] = set()
    for n in bic_names:
        cid = _try_get_category_id(doc, n)
        if cid is not None:
            out.add(cid)

    if key is not None:
        try:
            _CATEGORY_ID_CACHE[key] = set(out)
        except Exception:
            pass
    return out


def _try_get_category_type_model():
    """Return CategoryType.Model from the Revit API, or None if outside Revit."""
    try:
        from Autodesk.Revit.DB import CategoryType  # type: ignore
        return CategoryType.Model
    except (ImportError, AttributeError):
        return None


def should_include_element(
    *,
    elem,
    doc,
    source_type: str,
    stats: Optional[PolicyStats] = None,
) -> Tuple[bool, str, str]:
    """Apply category policy to an element.

    Decision order:
      1. Exclude elements with no category.
      2. Apply source-specific line rules.
      3. Apply explicit model exclusion list.
      4. Include if category.CategoryType == CategoryType.Model.
      5. Exclude as non_model_category otherwise.

    Returns:
        (include, reason, category_name)

    reason is one of:
      - "included"
      - "no_category"
      - "excluded_global"
      - "non_model_category"
      - "view_specific_line"
      - "lines_excluded_for_source"
    """
    if stats is not None:
        stats.seen_total += 1

    cat = getattr(elem, "Category", None)
    if cat is None:
        if stats is not None:
            stats.mark_excluded("no_category", "<NO_CATEGORY>")
        return False, "no_category", "<NO_CATEGORY>"

    try:
        cname = getattr(cat, "Name", None) or "<UNKNOWN_CATEGORY>"
    except Exception:
        cname = "<UNKNOWN_CATEGORY>"

    cat_id_val = None
    try:
        cat_id_val = int(cat.Id.IntegerValue)
    except Exception:
        cat_id_val = None

    st = (source_type or "HOST").upper()

    # Step 2: Source-specific line rules
    if st == "HOST":
        is_lines = (cname == "Lines")
        if cat_id_val is not None:
            lines_id = _try_get_category_id(doc, _BIC_LINES)
            if lines_id is not None:
                is_lines = is_lines or (cat_id_val == lines_id)
        if is_lines:
            try:
                if bool(getattr(elem, "ViewSpecific", False)):
                    if stats is not None:
                        stats.mark_excluded("view_specific_line", cname)
                    return False, "view_specific_line", cname
            except Exception:
                # Preserve legacy behavior: if ViewSpecific probe fails, do not exclude.
                pass
    else:
        # LINK/DWG: exclude lines altogether (explicit per-source override).
        is_lines = (cname == "Lines")
        if cat_id_val is not None:
            lines_id = _try_get_category_id(doc, _BIC_LINES)
            if lines_id is not None:
                is_lines = is_lines or (cat_id_val == lines_id)
        if is_lines:
            if stats is not None:
                stats.mark_excluded("lines_excluded_for_source", cname)
            return False, "lines_excluded_for_source", cname

    # Step 3: Explicit global exclusion list.
    # Name check runs unconditionally first: it catches categories whose BIC name
    # may not resolve in the current Revit version (e.g. OST_RVT_Links).
    # ID check runs as supplemental coverage for categories not in the name set.
    if cname in _FALLBACK_EXCLUDED_CATEGORY_NAMES:
        if stats is not None:
            stats.mark_excluded("excluded_global", cname)
        return False, "excluded_global", cname

    # Subcategories of excluded categories also inherit the exclusion.
    # DWG layer elements (e.g. "A-WALL") appear as subcategories of
    # OST_ImportObjectStyles ("Imports") and would otherwise pass step 4
    # as CategoryType.Model elements.
    try:
        parent_cat = getattr(cat, "Parent", None)
        if parent_cat is not None:
            parent_name = getattr(parent_cat, "Name", None) or ""
            if parent_name in _FALLBACK_EXCLUDED_CATEGORY_NAMES:
                if stats is not None:
                    stats.mark_excluded("excluded_global", cname)
                return False, "excluded_global", cname
    except Exception:
        pass

    if cat_id_val is not None:
        excluded_ids = resolve_category_ids(doc, excluded_bic_names_global())
        if cat_id_val in excluded_ids:
            if stats is not None:
                stats.mark_excluded("excluded_global", cname)
            return False, "excluded_global", cname

    # Belt-and-suspenders: exclude ImportInstance by type name regardless of
    # category name localization.  DWG/DXF imports are handled (when enabled)
    # exclusively through _collect_from_dwg_imports, not the HOST pass.
    if type(elem).__name__ == "ImportInstance":
        if stats is not None:
            stats.mark_excluded("excluded_global", cname)
        return False, "excluded_global", cname

    # Step 4: Include CategoryType.Model; exclude all other category types.
    cat_type = getattr(cat, "CategoryType", None)
    if cat_type is not None:
        model_type = _try_get_category_type_model()
        if model_type is not None:
            # Full Revit runtime: exact enum comparison
            if cat_type == model_type:
                if stats is not None:
                    stats.mark_included()
                return True, "included", cname
            else:
                if stats is not None:
                    stats.mark_excluded("non_model_category", cname)
                return False, "non_model_category", cname

        # Outside Revit: compare via integer value (CategoryType.Model == 1)
        try:
            if int(cat_type) == _CATEGORY_TYPE_MODEL_INT:
                if stats is not None:
                    stats.mark_included()
                return True, "included", cname
            else:
                if stats is not None:
                    stats.mark_excluded("non_model_category", cname)
                return False, "non_model_category", cname
        except (TypeError, ValueError):
            pass

        # Final fallback: string representation comparison
        cat_type_str = str(cat_type)
        if cat_type_str == "Model":
            if stats is not None:
                stats.mark_included()
            return True, "included", cname
        if cat_type_str in {"Annotation", "AnalyticalModel", "Internal"}:
            if stats is not None:
                stats.mark_excluded("non_model_category", cname)
            return False, "non_model_category", cname

    # Default: include when CategoryType is unavailable (safe; avoids silent omission).
    if stats is not None:
        stats.mark_included()
    return True, "included", cname
