"""Category policy (single source of truth).

Notes
-----
- Must be importable under pytest (outside Revit). Do not import Autodesk at module import time.
- Revit-only resolution happens inside functions.

This policy intentionally describes what we mean by "model geometry" for VOP.
Collectors are expected to:
  1) restrict candidates to CategoryType.Model (when available), and
  2) apply this allowlist + global exclusions + per-source overrides.

Per-source behavior
-------------------
HOST:
  - allowlist includes OST_Lines, but view-specific lines are excluded.
LINK/DWG:
  - exclude lines entirely by default (historical behavior), to avoid counting graphics-only content.
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
# This avoids repeated doc.Settings lookups when filtering many elements.
_CATEGORY_ID_CACHE = {}

# Allowlist: categories we intend to treat as "model geometry" for occupancy/edges.
# Stored as BuiltInCategory names (strings) so this module can import outside Revit.
_INCLUDED_BIC_NAMES_BASE: Tuple[str, ...] = (
    "OST_Walls",
    "OST_Floors",
    "OST_Roofs",
    "OST_Doors",
    "OST_Windows",
    "OST_Columns",
    "OST_StructuralFraming",
    "OST_StructuralColumns",
    "OST_Stairs",
    "OST_Railings",
    "OST_Ceilings",
    "OST_GenericModel",
    "OST_Furniture",
    "OST_Casework",
    "OST_MechanicalEquipment",
    "OST_ElectricalEquipment",
    "OST_PlumbingFixtures",
    "OST_DuctCurves",
    "OST_PipeCurves",
)

# Lines are special-cased by source.
_BIC_LINES = "OST_Lines"

# Excludelist: categories to exclude even if a future caller attempts broad collection.
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
)

# Fallback category NAME allowlist/excludelist used only when doc.Settings resolution
# is unavailable (pytest fakes). Real Revit runs should resolve by CategoryId.
_FALLBACK_INCLUDED_CATEGORY_NAMES = set([
    "Walls",
    "Floors",
    "Roofs",
    "Doors",
    "Windows",
    "Columns",
    "Structural Framing",
    "Structural Columns",
    "Stairs",
    "Railings",
    "Ceilings",
    "Generic Models",
    "Furniture",
    "Casework",
    "Mechanical Equipment",
    "Electrical Equipment",
    "Plumbing Fixtures",
    "Ducts",
    "Pipes",
    "Lines",
])

_FALLBACK_EXCLUDED_CATEGORY_NAMES = set([
    "Rooms",
    "Areas",
    "Grids",
    "Levels",
    "Point Clouds",
    "Detail Items",
])

def included_bic_names_for_source(source_type: str) -> Tuple[str, ...]:
    """Return allowlist BuiltInCategory *names* for a given source."""
    st = (source_type or "HOST").upper()
    if st == "HOST":
        return _INCLUDED_BIC_NAMES_BASE + (_BIC_LINES,)
    # LINK / DWG: match historical behavior (exclude lines).
    return _INCLUDED_BIC_NAMES_BASE

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

def should_include_element(
    *,
    elem,
    doc,
    source_type: str,
    stats: Optional[PolicyStats] = None,
) -> Tuple[bool, str, str]:
    """Apply category policy to an element.

    Returns:
        (include, reason, category_name)

    reason is one of:
      - "included"
      - "no_category"
      - "excluded_global"
      - "not_in_allowlist"
      - "view_specific_line"
      - "lines_excluded_for_source"
      - "category_id_unresolved"
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

    # Resolve id via cat.Id if available.
    cat_id_val = None
    try:
        cat_id_val = int(cat.Id.IntegerValue)
    except Exception:
        cat_id_val = None

    st = (source_type or "HOST").upper()

    # Lines special case
    if st == "HOST":
        is_lines = False
        if cname == "Lines":
            is_lines = True
        if cat_id_val is not None:
            lines_id = _try_get_category_id(doc, _BIC_LINES)
            if lines_id is not None:
                is_lines = (is_lines or (cat_id_val == lines_id))
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
        # pytest/fake-doc fallback: enforce by name if ids cannot be resolved.
        if cname == "Lines":
            if stats is not None:
                stats.mark_excluded("lines_excluded_for_source", cname)
            return False, "lines_excluded_for_source", cname

        if cat_id_val is not None:
            lines_id = _try_get_category_id(doc, _BIC_LINES)
            if lines_id is not None and cat_id_val == lines_id:
                if stats is not None:
                    stats.mark_excluded("lines_excluded_for_source", cname)
                return False, "lines_excluded_for_source", cname

    # Global exclusions
    if cat_id_val is not None:
        excluded_ids = resolve_category_ids(doc, excluded_bic_names_global())
        if excluded_ids:
            if cat_id_val in excluded_ids:
                if stats is not None:
                    stats.mark_excluded("excluded_global", cname)
                return False, "excluded_global", cname
        else:
            # pytest/fake-doc fallback
            if cname in _FALLBACK_EXCLUDED_CATEGORY_NAMES:
                if stats is not None:
                    stats.mark_excluded("excluded_global", cname)
                return False, "excluded_global", cname

    # Allowlist
    if cat_id_val is None:
        if stats is not None:
            stats.mark_excluded("category_id_unresolved", cname)
        return False, "category_id_unresolved", cname

    included_ids = resolve_category_ids(doc, included_bic_names_for_source(st))
    if included_ids:
        if cat_id_val not in included_ids:
            if stats is not None:
                stats.mark_excluded("not_in_allowlist", cname)
            return False, "not_in_allowlist", cname
    else:
        # pytest/fake-doc fallback
        if cname not in _FALLBACK_INCLUDED_CATEGORY_NAMES:
            if stats is not None:
                stats.mark_excluded("not_in_allowlist", cname)
            return False, "not_in_allowlist", cname

    if stats is not None:
        stats.mark_included()

    return True, "included", cname
