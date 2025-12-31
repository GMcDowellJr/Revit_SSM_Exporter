# -*- coding: utf-8 -*-
"""
SSM/VOP occupancy exporter for Revit 2D orthographic views.
Behavior is governed by the project regression contract.
"""

import os
import json
from export import csv as export_csv
from core import types as exporter_types
from core.debug import Logger
from core.config import CONFIG
from geometry import transforms
from geometry import grid
from geometry.silhouette import SilhouetteExtractor
from processing.projection import (
    _compute_adaptive_thresholds,
    _create_silhouette_extractor,
    project_elements_to_view_xy
)
from revit.collection import (
    _build_occupancy_preview_rects,
    _build_occupancy_png,
    build_clip_volume_for_view,
    _summarize_elements_by_type,
    _summarize_elements_by_category,
    collect_3d_elements_for_view,
    collect_2d_elements_for_view
)


def _json_default(o):
    """JSON serializer for Revit API types used in debug payloads."""
    try:
        # ElementId
        iv = getattr(o, "IntegerValue", None)
        if isinstance(iv, int):
            return iv
        # XYZ
        if hasattr(o, "X") and hasattr(o, "Y") and hasattr(o, "Z"):
            return [float(o.X), float(o.Y), float(o.Z)]
        # UV
        if hasattr(o, "U") and hasattr(o, "V"):
            return [float(o.U), float(o.V)]
        # Anything with Id
        oid = getattr(getattr(o, "Id", None), "IntegerValue", None)
        if isinstance(oid, int):
            return oid
    except Exception:
        pass
    return str(o)

import sys
import datetime
import math
import csv
import hashlib

# Exporter version tag for downstream tools
# Base id comes from this script's filename; the run id is appended at runtime.

def _compute_exporter_base_id():
    """
    Example:
        'VOP v47 — Exporter Scaffold_ 251205_A1.py'
        -> 'VOP_v47___Exporter_Scaffold__251205_A1'
    """
    try:
        fname = os.path.basename(__file__)
    except Exception:
        fname = None

    if not fname:
        return "Unified Exporter_v4_A1"

    stem, _ = os.path.splitext(fname)
    clean = []
    for ch in stem:
        if ch.isalnum():
            clean.append(ch)
        elif ch in ("_", "-"):
            clean.append(ch)
        else:
            clean.append("_")
    return "".join(clean)

EXPORTER_BASE_ID = _compute_exporter_base_id()

# Kept for backward compatibility; per-run signature is derived from EXPORTER_BASE_ID + run_id.
EXPORTER_VERSION = EXPORTER_BASE_ID

# ------------------------------------------------------------
# Dynamo / Revit boilerplate
# ------------------------------------------------------------

DOC = None
View = object
ViewType = None
ViewDiscipline = None
CategoryType = None
ImportInstance = None
FilteredElementCollector = None
BuiltInCategory = None
BuiltInParameter = None
RevitLinkInstance = None
VisibleInViewFilter = None
Dimension = None
LinearDimension = None
XYZ = None
PointCloudInstance = None

try:
    import clr
    clr.AddReference("RevitAPI")
    clr.AddReference("RevitServices")

    from Autodesk.Revit.DB import (
        CategoryType as _CategoryType,
        ImportInstance as _ImportInstance,
        View as _View,
        ViewType as _ViewType,
        ViewDiscipline as _ViewDiscipline,
        FilteredElementCollector as _FilteredElementCollector,
        BuiltInCategory as _BuiltInCategory,
        BuiltInParameter as _BuiltInParameter,
        RevitLinkInstance as _RevitLinkInstance,
        VisibleInViewFilter as _VisibleInViewFilter,
        Dimension as _Dimension,
        LinearDimension as _LinearDimension,
        TextNote as _TextNote,
        IndependentTag as _IndependentTag,
        FilledRegion as _FilledRegion,
        DetailCurve as _DetailCurve,
        CurveElement as _CurveElement,
        FamilyInstance as _FamilyInstance,
        XYZ as _XYZ,
        PointCloudInstance as _PointCloudInstance,
        Outline as _Outline,
        BoundingBoxIntersectsFilter as _BoundingBoxIntersectsFilter,
    )
    
    Outline = _Outline
    BoundingBoxIntersectsFilter = _BoundingBoxIntersectsFilter

    from RevitServices.Persistence import DocumentManager

    # RoomTag lives under Autodesk.Revit.DB.Architecture in some Revit versions
    try:
        from Autodesk.Revit.DB.Architecture import RoomTag as _RoomTag
    except Exception:
        _RoomTag = None

    DOC = DocumentManager.Instance.CurrentDBDocument
    View = _View
    ViewType = _ViewType
    ViewDiscipline = _ViewDiscipline
    CategoryType = _CategoryType
    ImportInstance = _ImportInstance
    FilteredElementCollector = _FilteredElementCollector
    BuiltInCategory = _BuiltInCategory
    BuiltInParameter = _BuiltInParameter
    RevitLinkInstance = _RevitLinkInstance
    VisibleInViewFilter = _VisibleInViewFilter
    Dimension = _Dimension
    LinearDimension = _LinearDimension
    TextNote = _TextNote
    IndependentTag = _IndependentTag
    RoomTag = _RoomTag
    FilledRegion = _FilledRegion
    DetailCurve = _DetailCurve
    CurveElement = _CurveElement
    FamilyInstance = _FamilyInstance
    XYZ = _XYZ
    PointCloudInstance = _PointCloudInstance
    RoomTag = _RoomTag if _RoomTag is not None else None

    # 2D whitelist class handles (avoid NameError fallthrough in collectors)
    TextNote_cls = TextNote
    Dimension_cls = Dimension
    IndependentTag_cls = IndependentTag
    RoomTag_cls = RoomTag
    FilledRegion_cls = FilledRegion
    DetailCurve_cls = DetailCurve
    CurveElement_cls = CurveElement
    FamilyInstance_cls = FamilyInstance

    # Try to get Dynamo's Revit wrapper (for UnwrapElement)
    try:
        clr.AddReference("RevitNodes")
        import Revit
        clr.ImportExtensions(Revit.Elements)
    except Exception:
        pass

except Exception:
    pass

# Dynamo geometry (for optional crop/grid preview only)

try:
    import clr
    clr.AddReference("ProtoGeometry")
    from Autodesk.DesignScript.Geometry import Point as DSPoint, PolyCurve as DSPolyCurve
except Exception:
    DSPoint = None
    DSPolyCurve = None

try:
    import System
    import System.IO
    import System.Drawing as Drawing
    from System.Drawing import Bitmap
    from System.Drawing.Imaging import ImageFormat
except Exception:
    System = None
    Drawing = None
    Bitmap = None
    ImageFormat = None

# Initialize grid module with Revit API context
grid.set_revit_context(
    DOC, View, ViewType, CategoryType, ImportInstance,
    FilteredElementCollector, BuiltInCategory, BuiltInParameter,
    XYZ, DSPoint, DSPolyCurve
)

# Initialize silhouette module with Revit API context
from geometry import silhouette
silhouette.set_revit_context(
    View, ViewType, XYZ
)

# Initialize projection module with Revit API context
from processing import projection
projection.set_revit_context(
    DOC, View, ViewType, CategoryType, ImportInstance,
    BuiltInCategory, TextNote, IndependentTag,
    RoomTag, FilledRegion, PointCloudInstance, XYZ
)

# Initialize collection module with Revit API context
from revit import collection
collection.set_revit_context(
    DOC, View, ViewType, CategoryType, ImportInstance,
    FilteredElementCollector, BuiltInCategory, BuiltInParameter,
    RevitLinkInstance, VisibleInViewFilter,
    Dimension, LinearDimension, TextNote, IndependentTag,
    RoomTag, FilledRegion, DetailCurve, CurveElement,
    FamilyInstance, XYZ, Outline, BoundingBoxIntersectsFilter
)

# Initialize collection module with System.Drawing context for PNG export
collection.set_drawing_context(System, Drawing, Bitmap, ImageFormat)


# ------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------

# CONFIG dictionary imported from config.py

# ------------------------------------------------------------
# LOGGER
# ------------------------------------------------------------

# Logger class imported from debug.py
LOGGER = Logger()

# ------------------------------------------------------------
# Small helpers
# ------------------------------------------------------------

def _get_view_crop_fingerprint(view):
    """
    Return a simple crop fingerprint for the view as a 4-tuple
    (minX, minY, maxX, maxY) in model coordinates. Falls back to
    (0,0,0,0) if crop is inactive or unavailable.
    """
    try:
        if not bool(getattr(view, "CropBoxActive", True)):
            return (0.0, 0.0, 0.0, 0.0)
        bbox = getattr(view, "CropBox", None)
        if bbox is None or bbox.Min is None or bbox.Max is None:
            return (0.0, 0.0, 0.0, 0.0)
        return (
            float(bbox.Min.X),
            float(bbox.Min.Y),
            float(bbox.Max.X),
            float(bbox.Max.Y),
        )
    except Exception:
        return (0.0, 0.0, 0.0, 0.0)

def _stable_hex_digest(payload, length=8):
    """
    Compute a deterministic hex digest for the given payload.

    Uses SHA1 to avoid Python's per-process hash randomization so that
    cache keys remain stable across Dynamo/Revit sessions.
    """
    if payload is None:
        payload = ""
    try:
        data = payload.encode("utf-8")
    except Exception:
        data = str(payload).encode("utf-8", errors="ignore")
    digest = hashlib.sha1(data).hexdigest()
    if length and length > 0:
        return digest[:length]
    return digest

def _compute_view_signature(view, elem_ids=None):
    """
    Content-aware per-view signature similar to unified exporter v2:

      - ViewType, Scale, DetailLevel, TemplateId, Discipline, Phase
      - Crop fingerprint
      - Sorted element Ids visible in the view

    Used for cache reuse: if this signature matches, we reuse cached metrics.
    """
    if view is None:
        return "NoView"

    # View type + scale + detail level
    vt_name = _get_view_type_name(view)
    try:
        scale = int(getattr(view, "Scale", 0) or 0)
    except Exception:
        scale = 0

    try:
        detail = getattr(view, "DetailLevel", None)
        detail_str = detail.ToString() if detail is not None else ""
    except Exception:
        detail_str = ""

    # Template id
    tpl_id = -1
    try:
        vt_id = getattr(view, "ViewTemplateId", None)
        if vt_id is not None:
            tpl_id = getattr(vt_id, "IntegerValue", -1)
    except Exception:
        tpl_id = -1

    # Discipline + phase
    disc = _get_view_discipline_name(view)
    phase = _get_view_phase_name(view)

    # Crop fingerprint
    crop_fp = _get_view_crop_fingerprint(view)
    try:
        crop_str = "{0:.2f},{1:.2f},{2:.2f},{3:.2f}".format(
            float(crop_fp[0]),
            float(crop_fp[1]),
            float(crop_fp[2]),
            float(crop_fp[3]),
        )
    except Exception:
        crop_str = "0.00,0.00,0.00,0.00"

    # Element Ids
    if elem_ids is None:
        elem_ids = []
    try:
        elem_ids = sorted(set(int(x) for x in elem_ids if x is not None))
    except Exception:
        elem_ids = list(elem_ids) if elem_ids is not None else []

    elem_ids_str = ",".join(str(i) for i in elem_ids)

    sig_parts = [
        vt_name,
        str(scale),
        detail_str,
        str(tpl_id),
        disc,
        phase,
        crop_str,
        elem_ids_str,
    ]
    sig_str = "|".join(sig_parts)

    return _stable_hex_digest(sig_str, length=8)

def _compute_config_hash(config):
    try:
        payload = json.dumps(config, sort_keys=True)
        return _stable_hex_digest(payload, length=8)
    except Exception:
        return ""

def _json_sanitize_keys(obj):
    """Recursively make JSON-safe structures, especially dict keys (tuple -> string)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            # JSON keys must be basic scalars; convert tuples (e.g. (i,j)) and any non-scalar keys.
            if isinstance(k, tuple):
                k2 = ",".join(str(x) for x in k)  # (i,j) -> "i,j"
            elif isinstance(k, (str, int, float, bool)) or k is None:
                k2 = k
            else:
                k2 = str(k)
            out[k2] = _json_sanitize_keys(v)
        return out

    # Convert iterables that aren't JSON-native
    if isinstance(obj, (list, tuple, set)):
        return [_json_sanitize_keys(x) for x in obj]

    return obj


def _collect_element_ids_for_signature(view, logger):
    """
    Collect a per-view set of element Ids for the cache signature.

    Uses a view-scoped FilteredElementCollector (no geometry),
    so it sees both model and annotation that are visible in the view.
    """
    ids = []
    if view is None or DOC is None or _FilteredElementCollector is None:
        return ids

    try:
        col = _FilteredElementCollector(DOC, view.Id).WhereElementIsNotElementType()
        for e in col:
            try:
                eid = getattr(getattr(e, "Id", None), "IntegerValue", None)
                if eid is not None:
                    ids.append(int(eid))
            except Exception:
                continue
    except Exception as ex:
        vid = getattr(getattr(view, "Id", None), "IntegerValue", None)
        logger.warn(
            "Cache: failed to collect element ids for view Id={0}: {1}".format(vid, ex)
        )
        return []

    if not ids:
        return []

    ids = sorted(set(ids))
    return ids

def _enum_to_name(val):
    """
    Generic enum/string helper.

    - For real .NET enums: ToString()
    - For anything else: str(val)
    """
    if val is None:
        return ""
    try:
        to_str = getattr(val, "ToString", None)
        if callable(to_str) and not isinstance(val, (int, float, str)):
            s = to_str()
            if s:
                return s
    except Exception:
        pass
    try:
        return str(val)
    except Exception:
        return ""

def _enum_name_from_int(enum_type, raw, fallback_map=None):
    """
    Robust name lookup when Pythonnet has already converted enums to ints.

    Tries, in order:
    - System.Enum.GetName(enum_type, raw)
    - enum_type(raw).ToString()
    - fallback_map[int(raw)] if provided
    - str(raw) as last resort
    """
    if raw is None or enum_type is None:
        return ""

    # Avoid double-wrapping a real enum
    try:
        # If this is already an enum instance, just ToString it
        if hasattr(raw, "__class__") and raw.__class__.__name__ == enum_type.__name__:
            return _enum_to_name(raw)
    except Exception:
        pass

    # Try System.Enum.GetName
    if System is not None:
        try:
            name = System.Enum.GetName(enum_type, raw)
            if name:
                return name
        except Exception:
            pass

    # Try constructing enum_type(raw)
    try:
        ev = enum_type(raw)
        return _enum_to_name(ev)
    except Exception:
        pass

    # Fallback map for known numeric values
    if fallback_map is not None:
        try:
            key = int(raw)
            name = fallback_map.get(key)
            if name:
                return name
        except Exception:
            pass

    # Last resort: string
    return str(raw)

# Known mapping for ViewType (from Revit API docs)
# https://www.revitapidocs.com/2026/bf04dabc-05a3-baf0-3564-f96c0bde3400.htm

_VIEWTYPE_FALLBACK = {
    0: "Undefined",
    1: "FloorPlan",
    2: "CeilingPlan",
    3: "Elevation",
    4: "ThreeD",
    5: "Schedule",
    6: "DrawingSheet",
    7: "ProjectBrowser",
    8: "Report",
    10: "DraftingView",
    11: "Legend",
    115: "EngineeringPlan",
    116: "AreaPlan",
    117: "Section",
    118: "Detail",
    119: "CostReport",
    120: "LoadsReport",
    121: "PressureLossReport",
    122: "ColumnSchedule",
    123: "PanelSchedule",
    124: "Walkthrough",
    125: "Rendering",
    126: "SystemsAnalysisReport",
    214: "Internal",
}

# Known mapping for ViewDiscipline (from Revit API docs)
# https://www.revitapidocs.com/2025/94363df8-8e46-3d70-8273-dfa0abaf2c46.htm

_VIEWDISC_FALLBACK = {
    1: "Architectural",
    2: "Structural",
    4: "Mechanical",
    8: "Electrical",
    16: "Plumbing",
    4095: "Coordination",
}

def _get_view_type_name(view):
    """Return a stable string for the view's ViewType."""
    if view is None:
        return ""
    try:
        val = getattr(view, "ViewType", None)
    except Exception:
        val = None

    if val is None:
        return ""

    # If we got an enum instance, ToString should be enough
    if ViewType is not None and not isinstance(val, (int, float, str)):
        return _enum_to_name(val)

    # If we got an int or numeric string, map it
    try:
        raw_int = int(val)
    except Exception:
        # Non-numeric but not an enum? Just ToString.
        return _enum_to_name(val)

    return _enum_name_from_int(ViewType, raw_int, _VIEWTYPE_FALLBACK)

def _get_view_discipline_name(view):
    """Return a stable string for the view's Discipline."""
    if view is None:
        return ""
    try:
        val = getattr(view, "Discipline", None)
    except Exception:
        val = None

    if val is None:
        return ""

    # If we got an enum instance, ToString should be enough
    if ViewDiscipline is not None and not isinstance(val, (int, float, str)):
        return _enum_to_name(val)

    # If we got an int or numeric string, map it
    try:
        raw_int = int(val)
    except Exception:
        return _enum_to_name(val)

    return _enum_name_from_int(ViewDiscipline, raw_int, _VIEWDISC_FALLBACK)

def _get_view_phase_name(view):
    """Best-effort phase name for the view.

    Tries:
    1) view.Phase.Name
    2) VIEW_PHASE built-in parameter → Phase element.Name
    3) VIEW_PHASE text/value as last resort
    """
    # 1) Strongly-typed Phase property
    try:
        phase = getattr(view, "Phase", None)
        if phase is not None:
            name = getattr(phase, "Name", "") or ""
            if name:
                return name
    except Exception:
        pass

    # 2) VIEW_PHASE parameter → Phase element
    if BuiltInParameter is None or DOC is None:
        return ""

    param = None
    try:
        param = view.get_Parameter(BuiltInParameter.VIEW_PHASE)
    except Exception:
        param = None

    if param is None:
        return ""

    # Try AsElementId → Phase element
    elem_id = None
    try:
        as_elem_id = getattr(param, "AsElementId", None)
        if callable(as_elem_id):
            elem_id = as_elem_id()
    except Exception:
        elem_id = None

    if elem_id is not None:
        try:
            phase_elem = DOC.GetElement(elem_id)
            if phase_elem is not None:
                name = getattr(phase_elem, "Name", "") or ""
                if name:
                    return name
        except Exception:
            pass

    # 3) Fallback to string representations
    try:
        as_string = param.AsString()
        if as_string:
            return as_string
    except Exception:
        pass

    try:
        as_val_string = param.AsValueString()
        if as_val_string:
            return as_val_string
    except Exception:
        pass

    return ""

def _get_project_guid(doc):
    """Return a stable identifier for the current Revit project."""
    if doc is None:
        return "UnknownProject"
    try:
        pi = getattr(doc, "ProjectInformation", None)
        guid = getattr(pi, "UniqueId", None)
        if guid:
            return str(guid)
    except Exception:
        pass

    # Fallbacks if GUID isn't available
    try:
        path = getattr(doc, "PathName", "") or ""
        if path:
            return "Path_" + re.sub(r"[^A-Za-z0-9_-]", "_", path)
    except Exception:
        pass
    try:
        title = getattr(doc, "Title", "") or ""
        if title:
            return "Title_" + re.sub(r"[^A-Za-z0-9_-]", "_", title)
    except Exception:
        pass

    return "UnknownProject"

def _get_cache_file_path(config, view_or_doc):
    """
    Compute the cache file path inside the export output folder, using
    the cache base name defined in CONFIG and appending the project GUID.
    """
    if not isinstance(config, dict):
        config = CONFIG

    export_cfg = config.get("export", {}) or {}
    cache_cfg = config.get("cache", {}) or {}

    # The output folder is always the parent directory for the cache file
    root = export_cfg.get("output_dir") or os.path.join(
        os.path.expanduser("~"), "Documents", "_metrics"
    )

    # Accept either a View or a Document
    doc = None
    if view_or_doc is not None:
        try:
            from Autodesk.Revit.DB import Document as RevitDocument
        except Exception:
            RevitDocument = None
        if RevitDocument is not None and isinstance(view_or_doc, RevitDocument):
            doc = view_or_doc
        else:
            doc = getattr(view_or_doc, "Document", None)
    if doc is None:
        doc = DOC

    proj_guid = _get_project_guid(doc)

    # Base name comes strictly from CONFIG; no hardcoded value here
    base_name = cache_cfg.get("file_name")
    if not base_name:
        # Defensive fallback if the config is malformed
        base_name = "grid_cache.json"

    stem, ext = os.path.splitext(base_name)
    if not ext:
        ext = ".json"

    final_name = "{0}_{1}{2}".format(stem, proj_guid, ext)
    return os.path.join(root, final_name)

def _load_view_cache(cache_path, exporter_version, config_hash, project_guid, logger):
    """
    Load cache from disk, validating exporter version + config hash + project.
    Returns a dict with at least: { "views": { ... } }.
    """
    empty = {
        "exporter_version": exporter_version,
        "config_hash": config_hash,
        "project_guid": project_guid,
        "views": {},
    }
    if not cache_path:
        return empty

    if not os.path.isfile(cache_path):
        return empty

    try:
        with open(cache_path, "r") as f:
            data = json.load(f)
    except Exception as ex:
        logger.warn("Cache: could not read '{0}': {1}".format(cache_path, ex))
        return empty

    if not isinstance(data, dict):
        return empty

    if data.get("exporter_version") != exporter_version:
        logger.info("Cache: exporter_version mismatch; ignoring existing cache.")
        return empty
    if data.get("config_hash") != config_hash:
        logger.info("Cache: config_hash mismatch; ignoring existing cache.")
        return empty
    if project_guid and data.get("project_guid") not in (None, project_guid):
        logger.info("Cache: project_guid mismatch; ignoring existing cache.")
        return empty

    views = data.get("views") or {}
    if not isinstance(views, dict):
        views = {}

    logger.info("Cache: loaded {0} cached view(s) from '{1}'".format(len(views), cache_path))
    return {
        "exporter_version": exporter_version,
        "config_hash": config_hash,
        "project_guid": project_guid,
        "views": views,
    }

def _save_view_cache(cache_path, cache_data, logger):
    """
    Persist cache to disk. For each view we store:
        - view_signature
        - row (metrics)
        - elapsed_sec
    """
    if not cache_path:
        return

    try:
        # Ensure parent directory exists
        parent = os.path.dirname(cache_path)
        if not export_csv._ensure_dir(parent, logger):
            return

        views = cache_data.get("views") or {}
        safe_views = {}
        for k, v in views.items():
            if not isinstance(v, dict):
                continue
            row = v.get("row") or {}
            elapsed_sec = float(v.get("elapsed_sec") or 0.0)
            view_sig = v.get("view_signature") or ""
            safe_views[str(k)] = {
                "view_signature": view_sig,
                "row": row,
                "elapsed_sec": elapsed_sec,
            }

        payload = {
            "exporter_version": cache_data.get("exporter_version"),
            "config_hash": cache_data.get("config_hash"),
            "project_guid": cache_data.get("project_guid"),
            "views": safe_views,
        }

        with open(cache_path, "w") as f:
            json.dump(payload, f, indent=2, default=_json_default)

        logger.info(
            "Cache: wrote {0} cached view(s) to '{1}'".format(len(safe_views), cache_path)
        )
    except Exception as ex:
        logger.warn("Cache: could not write '{0}': {1}".format(cache_path, ex))

# ------------------------------------------------------------
# RESET / CACHE FLAGS
# ------------------------------------------------------------

def _get_reset_and_cache_flags():
    """
    IN[1] – UseCache (bool)
    IN[2] – ForceRecompute (bool)

    Semantics:
        - UseCache=False      => no cache read or write.
        - ForceRecompute=True => ignore existing cache this run (but still write if UseCache=True).

    Returns:
        (force_recompute, cache_enabled)
    """
    force_recompute = False
    cache_enabled = True  # default: use cache

    if "IN" in globals():
        # IN[1]: UseCache
        if len(IN) > 1 and IN[1] is not None:
            cache_enabled = bool(IN[1])

        # IN[2]: ForceRecompute
        if len(IN) > 2 and IN[2] is not None:
            force_recompute = bool(IN[2])

    # If cache_enabled is False, we ignore cache entirely.
    # If cache_enabled is True but force_recompute is True,
    # we skip reading cache but will still write a fresh one at the end.
    return force_recompute, cache_enabled

def _apply_runtime_inputs_to_config(config, logger=None):
    """
    Apply Dynamo inputs that override CONFIG:

        IN[3] – OutputFolderOverride
        IN[4] – RenderPNG
    """
    export_cfg = config.setdefault("export", {})
    png_cfg = config.setdefault("occupancy_png", {})

    if "IN" not in globals():
        return

    # IN[3]: output folder override
    if len(IN) > 3 and IN[3] is not None:
        out_dir = str(IN[3]).strip()
        if out_dir:
            export_cfg["output_dir"] = out_dir
            if logger:
                logger.info("Export: output_dir overridden to '{0}' via IN[3]".format(out_dir))

    # IN[4]: render PNG override
    if len(IN) > 4 and IN[4] is not None:
        png_enabled = bool(IN[4])
        png_cfg["enabled"] = png_enabled
        if logger:
            logger.info("Export: occupancy_png.enabled overridden to {0} via IN[4]".format(png_enabled))

def build_regions_from_projected(projected, grid_data, config, logger):
    logger.info("Regions: building regions from projected geometry")

    tiny_regions = []
    linear_regions = []
    areal_regions = []

    diagnostics = {}

    # Debug config (no view dependency)
    debug_cfg = (config or {}).get("debug", {})
    debug_filled_loops = bool(debug_cfg.get("filled_region_loops", False))
    debug_filled_loops_max = int(debug_cfg.get("filled_region_loops_max", 10))
    debug_filled_loops_count = 0

    def _point_in_poly(px, py, pts):
        n = len(pts)
        if n < 3:
            return False

        inside = False
        x0, y0 = pts[0]
        for i in range(1, n + 1):
            x1, y1 = pts[i % n]

            dx = x1 - x0
            dy = y1 - y0
            if abs(dx) < 1e-9 and abs(dy) < 1e-9:
                x0, y0 = x1, y1
                continue

            # Edge test: treat points on edge as inside
            t = ((px - x0) * dy - (py - y0) * dx)
            if abs(t) < 1e-9:
                dot = (px - x0) * (px - x1) + (py - y0) * (py - y1)
                if dot <= 1e-9:
                    return True

            # Even–odd parity
            if ((y0 <= py < y1) or (y1 <= py < y0)):
                try:
                    x_int = x0 + (py - y0) * (x1 - x0) / (y1 - y0)
                except ZeroDivisionError:
                    x_int = x0
                if x_int >= px:
                    inside = not inside

            x0, y0 = x1, y1

        return inside

    if not isinstance(projected, dict):
        logger.warn("Regions: projected input not dict; stub regions")
        return {
            "tiny_regions": tiny_regions,
            "linear_regions": linear_regions,
            "areal_regions": areal_regions,
            "diagnostics": diagnostics,
        }

    proj3d = projected.get("projected_3d") or []
    proj2d = projected.get("projected_2d") or []
    proj_diag = projected.get("diagnostics") or {}

    diagnostics.update(proj_diag)
    diagnostics["num_projected_3d_elems"] = len(proj3d)
    diagnostics["num_projected_2d_elems"] = len(proj2d)
    diagnostics["num_projected_3d_loops"] = sum(len(ep.get("loops") or []) for ep in proj3d)
    diagnostics["num_projected_2d_loops"] = sum(len(ep.get("loops") or []) for ep in proj2d)

    try:
        cell_size = grid_data.get("cell_size_model")
        origin_xy = grid_data.get("origin_model_xy")
        n_i = int(grid_data.get("grid_n_i") or 0)
        n_j = int(grid_data.get("grid_n_j") or 0)
        valid_cells_list = grid_data.get("valid_cells") or []
    except Exception:
        cell_size = None
        origin_xy = None
        n_i = n_j = 0
        valid_cells_list = []

    if not cell_size or not origin_xy or n_i <= 0 or n_j <= 0:
        logger.warn("Regions: grid_data incomplete; stub regions")
        return {
            "tiny_regions": tiny_regions,
            "linear_regions": linear_regions,
            "areal_regions": areal_regions,
            "diagnostics": diagnostics,
        }
        
    # Debug thresholds for "large" regions in grid space
    debug_cfg = (config or {}).get("debug", {})
    large_reg_enable = bool(debug_cfg.get("log_large_3d_regions", False))
    large_reg_frac = float(debug_cfg.get("large_region_fraction", 0.8))  # 80% by default

    # 3D floor/roof loop debug
    floor_debug_enable = bool(debug_cfg.get("floor_loops", False))
    floor_debug_max = int(debug_cfg.get("floor_loops_max", 5))
    floor_debug_count = 0

    origin_x, origin_y = origin_xy
    valid_cells = set((int(i), int(j)) for (i, j) in valid_cells_list)

    reg_cfg = config.get("regions", {}) if config else {}
    tiny_max_w = int(reg_cfg.get("tiny_max_w", 2))
    tiny_max_h = int(reg_cfg.get("tiny_max_h", 2))
    linear_band_thickness = int(reg_cfg.get("linear_band_thickness_cells", 2))

    hole_min_w = float(reg_cfg.get("min_hole_size_w_cells", 1.0))
    hole_min_h = float(reg_cfg.get("min_hole_size_h_cells", 1.0))

    # floor/roof/ceiling suppression flag
    suppress_floor_like_3d = bool(reg_cfg.get("suppress_floor_roof_ceiling_3d", False))

    s = float(cell_size)
    eps = 1e-9

    def _cells_from_loops_boundary_only(loops, debug_label=None, debug_enabled=False):
        """
        Conservative boundary-only rasterization (no interior fill).
        Used for 3D model elements so plans/sections/elevations don't come back
        as solid AREAL blobs. We only mark cells whose area is intersected by
        the polygon edges.
        """
        if not loops:
            return set()

        elem_cells = set()
        loop_count = 0

        for loop in loops:
            pts = loop.get("points") or []
            if not pts or len(pts) < 2:
                continue

            loop_count += 1

            # Use existing SAT-based boundary raster
            b_cells = _get_conservative_boundary_cells(
                pts, origin_x, origin_y, s, n_i, n_j
            )
            elem_cells.update(b_cells)

        if debug_enabled and debug_label:
            try:
                diagnostics.setdefault("loop_debug", []).append(
                    {
                        "label": debug_label,
                        "mode": "boundary_only",
                        "num_loops": loop_count,
                        "num_cells_boundary": len(elem_cells),
                    }
                )
            except Exception:
                pass

        return elem_cells

    def _cells_from_loops_parity(loops, debug_label=None, debug_enabled=False):
        """
        v47 Refined: Conservative Boundary + Parity Interior.

        1. Traces edges to capture thin elements (Conservative Rasterization).
        2. Uses parity check for bulk interior.

        If debug_enabled is True, we also push a loop summary into
        diagnostics["loop_debug"] for this element.
        """
        if not loops:
            return set()

        usable = []

        # --- 1. Precompute geometry + signed area for all loops ---
        loop_infos = []  # (idx, loop, pts, min_x, max_x, min_y, max_y, area, sign)

        # Deduplicate loops coming from opposite faces etc.
        # Use bbox + point-count as a cheap key; good enough for our per-element use.
        seen_keys = set()

        for idx, loop in enumerate(loops):
            pts = loop.get("points") or []
            if len(pts) < 3:
                continue

            xs = [float(p[0]) for p in pts]
            ys = [float(p[1]) for p in pts]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            if max_x < min_x or max_y < min_y:
                continue

            # Cheap geometric key (bbox + vertex count, rounded to tame noise)
            key = (
                round(min_x, 6),
                round(max_x, 6),
                round(min_y, 6),
                round(max_y, 6),
                len(pts),
            )
            if key in seen_keys:
                # Duplicate loop: likely from the opposite face; ignore.
                continue
            seen_keys.add(key)

            # Signed area: orientation + magnitude
            area = 0.0
            n_pts = len(pts)
            for k in range(n_pts):
                x1, y1 = pts[k]
                x2, y2 = pts[(k + 1) % n_pts]
                area += (x1 * y2 - x2 * y1)
            area *= 0.5

            if abs(area) < eps:
                # Degenerate, ignore
                continue

            sign = 1 if area > 0.0 else -1
            loop_infos.append(
                (idx, loop, pts, min_x, max_x, min_y, max_y, area, sign)
            )


        if not loop_infos:
            return set()

        # --- 1a. Determine "solid" orientation from largest-area loop ---
        # The loop with largest |area| is the main outer; its sign is used
        # as the "solid" sign. Any loop with opposite sign is a hole ring.
        outer_idx = None
        outer_sign = None
        max_abs_area = 0.0
        for (idx, loop, pts, min_x, max_x, min_y, max_y, area, sign) in loop_infos:
            a = abs(area)
            if a > max_abs_area:
                max_abs_area = a
                outer_idx = idx
                outer_sign = sign

        if outer_sign is None:
            # Fallback: treat all loops as solid; no hole filtering
            for (idx, loop, pts, min_x, max_x, min_y, max_y, area, sign) in loop_infos:
                usable.append((pts, min_x, max_x, min_y, max_y))
        else:
            # --- 1b. Apply size filter only to hole rings (opposite sign) ---
            for (idx, loop, pts, min_x, max_x, min_y, max_y, area, sign) in loop_infos:
                # Convert bbox size to cell units
                w_cells = (max_x - min_x) / s if s > 0.0 else 0.0
                h_cells = (max_y - min_y) / s if s > 0.0 else 0.0

                is_hole_ring = (sign != outer_sign)

                #  only tiny *hole* rings are dropped; all solid
                # (outer + islands) are retained regardless of size.
                if is_hole_ring and w_cells <= hole_min_w and h_cells <= hole_min_h:
                    continue

                usable.append((pts, min_x, max_x, min_y, max_y))

        if not usable:
            return set()


        # 2. Phase 1: Boundary Trace (Conservative)
        final_cells = set()
        for (pts, _, _, _, _) in usable:
            b_cells = _get_conservative_boundary_cells(
                pts, origin_x, origin_y, s, n_i, n_j
            )
            final_cells.update(b_cells)

        # 3. Phase 2: Interior Fill (Parity)
        all_min_x = min(u[1] for u in usable)
        all_max_x = max(u[2] for u in usable)
        all_min_y = min(u[3] for u in usable)
        all_max_y = max(u[4] for u in usable)

        raw_i_min = int(math.floor((all_min_x - origin_x) / s - eps))
        raw_i_max = int(math.ceil((all_max_x - origin_x) / s + eps))
        raw_j_min = int(math.floor((all_min_y - origin_y) / s - eps))
        raw_j_max = int(math.ceil((all_max_y - origin_y) / s + eps))

        i_min = max(0, raw_i_min)
        i_max = min(n_i - 1, raw_i_max)
        j_min = max(0, raw_j_min)
        j_max = min(n_j - 1, raw_j_max)

        for i in range(i_min, i_max + 1):
            cx = origin_x + i * s
            for j in range(j_min, j_max + 1):
                # If edge trace already caught it, we are good
                if (i, j) in final_cells:
                    continue

                cy = origin_y + j * s

                # Quick BBox reject
                if cx < all_min_x - eps or cx > all_max_x + eps:
                    continue
                if cy < all_min_y - eps or cy > all_max_y + eps:
                    continue

                # Parity check
                inside = False
                for (pts, min_x, max_x, min_y, max_y) in usable:
                    if cx < min_x - eps or cx > max_x + eps:
                        continue
                    if cy < min_y - eps or cy > max_y + eps:
                        continue
                    if _point_in_poly(cx, cy, pts):
                        inside = not inside

                if inside:
                    final_cells.add((i, j))

        return final_cells

    def _segment_and_classify(elem_cells, has_3d, has_2d, elem_meta):
        if not elem_cells:
            return

        neighbors = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        visited = set()

        for seed in elem_cells:
            if seed in visited:
                continue

            stack = [seed]
            region_cells = set()

            while stack:
                ci, cj = stack.pop()
                if (ci, cj) in visited:
                    continue
                if (ci, cj) not in elem_cells:
                    continue

                visited.add((ci, cj))
                region_cells.add((ci, cj))

                for di, dj in neighbors:
                    nbr = (ci + di, cj + dj)
                    if nbr in elem_cells and nbr not in visited:
                        stack.append(nbr)

            if not region_cells:
                continue

            is_ = [c[0] for c in region_cells]
            js_ = [c[1] for c in region_cells]
            min_i = min(is_)
            max_i = max(is_)
            min_j = min(js_)
            max_j = max(js_)

            w = max_i - min_i + 1
            h = max_j - min_j + 1

            region_info = {
                "cells": sorted(region_cells),
                "bbox_ij": (min_i, min_j, max_i, max_j),
                "w": w,
                "h": h,
                "has_3d": has_3d,
                "has_2d": has_2d,
                "elem_id": elem_meta.get("elem_id"),
                "category": elem_meta.get("category", "<Unknown>"),
                "is_2d_element": elem_meta.get("is_2d_element", False),
                "is_filled_region": elem_meta.get("is_filled_region", False),
                "source": elem_meta.get("source", "HOST"),
            }

            
            # Debug: log very large 3D regions that span most of the grid
            if large_reg_enable and has_3d:
                try:
                    if (w >= large_reg_frac * n_i) or (h >= large_reg_frac * n_j):
                        logger.info(
                            "Regions-debug: large 3D region elem_id={0}, cat='{1}', w={2}, h={3}, grid={4}x{5}".format(
                                elem_meta.get("elem_id"),
                                elem_meta.get("category", "<Unknown>"),
                                w, h, n_i, n_j
                            )
                        )
                except Exception:
                    pass

            if w <= tiny_max_w and h <= tiny_max_h:
                tiny_regions.append(region_info)
            elif min(w, h) <= linear_band_thickness:
                linear_regions.append(region_info)
            else:
                areal_regions.append(region_info)

    # ------------------------------------------------------------
    # DEPTH-BASED OCCLUSION (v4 behavior change; 3D-only)
    # ------------------------------------------------------------
    occ_cfg = (config or {}).get("occlusion", {}) or {}
    occlusion_enable = bool(occ_cfg.get("enable", True))

    valid_cells_set = set()
    try:
        valid_cells_set = set(tuple(c) for c in (valid_cells_list or []))
    except Exception:
        valid_cells_set = set()

    occlusion_mask = set()  # set((i,j)) of occluded cells
    diagnostics.setdefault("num_3d_culled_by_occlusion", 0)
    # Per-cell nearest-depth buffer for occlusion (visible-geometry mode).
    # Keys are (i,j) cell indices; values are nearest depth (lower = closer).
    INF = 1.0e30
    w_nearest = {}  # dict((i,j) -> float)

    # Tile-based occlusion acceleration (optional optimization)
    # Tiles group cells to speed up fully-occluded checks for large rectangles
    tile_size = int(occ_cfg.get("tile_size", 16))  # 16x16 cells per tile
    tile_fully_occluded = {}  # dict((ti,tj) -> bool)
    tile_depth_gate = {}  # dict((ti,tj) -> float) MAXIMUM depth for fully occluded tiles

    # Bbox UVW optimization (DISABLED by default due to over-culling bug)
    use_bbox_uvw = bool(occ_cfg.get("use_bbox_uvw", False))  # Changed to False
    # Legacy option - now fixed to compute tight link-space bounds for linked elements
    skip_bbox_uvw_for_links = bool(occ_cfg.get("skip_bbox_uvw_for_links", False))

    diagnostics.setdefault("num_3d_culled_by_occlusion", 0)
    diagnostics.setdefault("num_3d_tested_for_occlusion", 0)
    diagnostics.setdefault("num_3d_tile_check_hits", 0)
    diagnostics.setdefault("num_3d_tile_check_total", 0)


    def _compute_uv_aabb_from_loops(_loops):
        mnx = mny = mxx = mxy = None
        try:
            for _lp in (_loops or []):
                _pts = _lp.get("points") or []
                for (_x, _y) in _pts:
                    if mnx is None:
                        mnx = mxx = float(_x)
                        mny = mxy = float(_y)
                    else:
                        if _x < mnx: mnx = float(_x)
                        if _x > mxx: mxx = float(_x)
                        if _y < mny: mny = float(_y)
                        if _y > mxy: mxy = float(_y)
        except Exception:
            return None
        if mnx is None or mny is None or mxx is None or mxy is None:
            return None
        return (mnx, mny, mxx, mxy)

    def _uv_aabb_to_cell_rect(uv_aabb):
        """Convert UV AABB to inclusive (i0,i1,j0,j1) rect in grid index space."""
        if uv_aabb is None:
            return None
        try:
            mnx, mny, mxx, mxy = uv_aabb
            if mnx is None or mny is None or mxx is None or mxy is None:
                return None
            ox, oy = origin_xy
            s = float(cell_size)
            if s <= 0.0:
                return None
            i0 = int(math.floor((float(mnx) - ox) / s))
            i1 = int(math.floor((float(mxx) - ox) / s))
            j0 = int(math.floor((float(mny) - oy) / s))
            j1 = int(math.floor((float(mxy) - oy) / s))
            if i0 > i1:
                i0, i1 = i1, i0
            if j0 > j1:
                j0, j1 = j1, j0
            # clamp to grid
            if i1 < 0 or j1 < 0 or i0 >= n_i or j0 >= n_j:
                return None
            i0 = 0 if i0 < 0 else i0
            j0 = 0 if j0 < 0 else j0
            i1 = (n_i - 1) if i1 >= n_i else i1
            j1 = (n_j - 1) if j1 >= n_j else j1
            return (i0, i1, j0, j1)
        except Exception:
            return None

    def _rect_fully_occluded(rect, elem_wmin):
        """True iff every valid cell in rect already has nearer-or-equal depth than elem_wmin.

        Uses tile-based acceleration when available for faster large-rectangle checks.
        """
        if rect is None or elem_wmin is None:
            return False  # fail-open
        i0, i1, j0, j1 = rect

        # Tile-based acceleration: check if all covering tiles are fully occluded
        if tile_size > 0:
            ti0 = i0 // tile_size
            ti1 = i1 // tile_size
            tj0 = j0 // tile_size
            tj1 = j1 // tile_size

            diagnostics["num_3d_tile_check_total"] += 1
            all_tiles_occluded = True

            for tjj in range(tj0, tj1 + 1):
                for tii in range(ti0, ti1 + 1):
                    t = (tii, tjj)
                    if not tile_fully_occluded.get(t, False):
                        all_tiles_occluded = False
                        break
                    # Check depth gate: tile occludes only if element is farther than ALL occluders
                    # tile_depth_gate stores MAX depth (farthest occluder in tile)
                    # Element must be farther: elem_wmin >= tile_depth_gate
                    if tile_depth_gate.get(t, 0.0) > float(elem_wmin):
                        # Element is CLOSER than farthest occluder, so not fully occluded
                        all_tiles_occluded = False
                        break
                if not all_tiles_occluded:
                    break

            if all_tiles_occluded and (ti1 >= ti0) and (tj1 >= tj0):
                # All covering tiles are fully occluded with sufficient depth
                diagnostics["num_3d_tile_check_hits"] += 1
                return True

        # Fallback to per-cell check (exact but slower)
        any_valid = False
        for jj in range(j0, j1 + 1):
            for ii in range(i0, i1 + 1):
                c = (ii, jj)
                if valid_cells_set and (c not in valid_cells_set):
                    continue
                any_valid = True
                if w_nearest.get(c, INF) > float(elem_wmin):
                    return False
        # Fail-open: if we can't conclusively evaluate any valid cell, INCLUDE
        if not any_valid:
            return False
        return True

    def _update_tile_occlusion(cells, depth):
        """Update tile state after writing occlusion for areal elements.

        When all cells in a tile are occluded, mark the tile as fully occluded
        with the maximum depth gate. This accelerates future occlusion checks.
        """
        if tile_size <= 0 or not cells:
            return

        # Group cells by tile
        tiles_touched = set()
        for (ii, jj) in cells:
            ti = ii // tile_size
            tj = jj // tile_size
            tiles_touched.add((ti, tj))

        # Check each touched tile to see if it's now fully occluded
        for (ti, tj) in tiles_touched:
            # Get all cells in this tile within grid bounds
            i0 = ti * tile_size
            i1 = min((ti + 1) * tile_size - 1, n_i - 1)
            j0 = tj * tile_size
            j1 = min((tj + 1) * tile_size - 1, n_j - 1)

            # Check if all valid cells in tile are occluded
            all_occluded = True
            max_depth_in_tile = 0.0  # Track MAXIMUM depth (farthest occluder)

            for jj in range(j0, j1 + 1):
                for ii in range(i0, i1 + 1):
                    c = (ii, jj)
                    if valid_cells_set and (c not in valid_cells_set):
                        continue
                    if c not in w_nearest:
                        all_occluded = False
                        break
                    max_depth_in_tile = max(max_depth_in_tile, w_nearest[c])
                if not all_occluded:
                    break

            if all_occluded and max_depth_in_tile > 0.0:
                tile_fully_occluded[(ti, tj)] = True
                tile_depth_gate[(ti, tj)] = max_depth_in_tile  # Store MAX for conservative check

    # Front-to-back ordering (view-space AABB ordering)
    def _depth_sort_key(ep):
        d = ep.get("depth_min", None)
        if d is None:
            return (1, 0.0)
        try:
            return (0, float(d))
        except Exception:
            return (1, 0.0)

    proj3d_ordered = sorted(proj3d, key=_depth_sort_key)

    # ADD LOGGING: Show first 5 and last 5 elements in sort order
    if proj3d_ordered and logger:
        logger.info("Occlusion: Sorted {0} elements, first 5:".format(len(proj3d_ordered)))
        for i, ep in enumerate(proj3d_ordered[:5]):
            cat = ep.get("category", "?")
            d_min = ep.get("depth_min", None)
            d_max = ep.get("depth_max", None)
            logger.info("  [{0}] {1}: depth_min={2:.3f}, depth_max={3:.3f}".format(
                i, cat, d_min if d_min is not None else float('nan'), 
                d_max if d_max is not None else float('nan')
            ))
        
        if len(proj3d_ordered) > 5:
            logger.info("Occlusion: Last 5:")
            for i, ep in enumerate(proj3d_ordered[-5:]):
                cat = ep.get("category", "?")
                d_min = ep.get("depth_min", None)
                d_max = ep.get("depth_max", None)
                idx = len(proj3d_ordered) - 5 + i
                logger.info("  [{0}] {1}: depth_min={2:.3f}, depth_max={3:.3f}".format(
                    idx, cat, d_min if d_min is not None else float('nan'),
                    d_max if d_max is not None else float('nan')
                ))

    total_cells_3d = 0
    for ep in proj3d_ordered:
        elem_id = ep.get("elem_id")
        category = ep.get("category", "<No Category>")
        loops = ep.get("loops") or []
        
        # Early-out occlusion test (3D-only). Fail-open if we can't evaluate.
        # Policy: host + RVT link elements can occlude; externals (DWG/SKP/etc.) do not occlude.
        source = ep.get("source", "HOST")
        can_occlude = (source in ("HOST", "RVT_LINK"))

        # Early-out occlusion test DISABLED - cannot work reliably with bbox-derived depth
        #
        # Problem: Both bbox_uvw w_min and ep["depth_min"] are computed from bbox corners,
        # which don't accurately represent the depth of actual visible surfaces (especially
        # for rotated elements). Using bbox corner depth causes massive over-culling.
        #
        # The fundamental issue: to know if an element is fully occluded, we need accurate
        # depth of its visible surface. But getting that depth requires expensive geometry
        # projection, which defeats the purpose of early culling.
        #
        # Occlusion still works correctly during per-cell rasterization (lines 1650-1690)
        # where we have accurate depth values from actual projected geometry.
        #
        # if occlusion_enable and can_occlude:
        #     diagnostics["num_3d_tested_for_occlusion"] += 1
        #     ... early culling code disabled ...

        # optionally exclude floor-like 3D elements entirely
        if suppress_floor_like_3d and category in (
            "Floors",
            "Roofs",
            "Ceilings",
            "Structural Foundations",
        ):
            # Still counted in projection diagnostics, but contributes
            # no 3D regions / occupancy.
            continue
            
        debug_label = None
        debug_enabled = False

        # Optional: log floor-like elements' loop sizes
        if (
            floor_debug_enable
            and floor_debug_count < floor_debug_max
            and category in ("Floors", "Structural Foundations", "Roofs")
        ):
            debug_label = "3D elem={0}, cat={1}".format(elem_id, category)
            debug_enabled = True
            floor_debug_count += 1

        # Determine if element is likely areal by checking loops bounding box
        # This helps us choose the right rasterization strategy BEFORE creating elem_cells
        is_floor_like = category in ("Floors", "Roofs", "Ceilings", "Structural Foundations")
        is_likely_areal = False
        try:
            # Compute bounding box from loops in grid space
            if loops:
                all_pts = []
                for loop in loops:
                    all_pts.extend(loop)
                if all_pts:
                    min_i = min(pt[0] for pt in all_pts)
                    max_i = max(pt[0] for pt in all_pts)
                    min_j = min(pt[1] for pt in all_pts)
                    max_j = max(pt[1] for pt in all_pts)
                    width = (max_i - min_i + 1)
                    height = (max_j - min_j + 1)
                    if width > 2 and height > 2:
                        is_likely_areal = True
        except Exception:
            pass

        # Use interior-filled rasterization for:
        # - Floor-like elements (floors, roofs, ceilings, foundations)
        # - Elements that project as areal (e.g., walls in section/elevation)
        # This ensures areal elements can properly occlude elements behind them
        if is_floor_like or is_likely_areal:
            elem_cells = _cells_from_loops_parity(loops, debug_label, debug_enabled)
        else:
            elem_cells = _cells_from_loops_boundary_only(loops, debug_label, debug_enabled)

        # Determine whether this element's boundary footprint is AREAL in grid space
        is_areal_3d = False
        try:
            if elem_cells:
                _min_i = min(c[0] for c in elem_cells)
                _max_i = max(c[0] for c in elem_cells)
                _min_j = min(c[1] for c in elem_cells)
                _max_j = max(c[1] for c in elem_cells)
                _w = (_max_i - _min_i + 1)
                _h = (_max_j - _min_j + 1)
                if _w > 2 and _h > 2:
                    is_areal_3d = True
        except Exception:
            is_areal_3d = False

        # total_cells_3d updated after visibility (depth) filtering

        elem_meta = {
            "elem_id": elem_id,
            "category": category,
            "is_2d_element": False,
            "is_filled_region": False,
            "source": ep.get("source", "HOST"),
        }

        # Per-cell visibility test against depth buffer:
        # - All 3D contributors can be hidden by nearer occluders.
        # - Only HOST + RVT_LINK write to depth buffer (occlude).
        w_hit = ep.get("depth_min", None)

        # Filter to valid cells (grid domain)
        if valid_cells_set:
            elem_cells = [c for c in elem_cells if c in valid_cells_set]

        visible_cells = elem_cells

        # Special handling for areal elements (floors in plan, walls in section, etc.):
        # - They update depth buffer for occlusion (w_nearest)
        # - But they don't contribute to occupancy (visible_cells = empty)
        # This prevents areal element interiors from showing as occupied space
        # while still allowing them to occlude elements behind them
        if is_areal_3d and occlusion_enable and can_occlude and (w_hit is not None):
            try:
                w_hit_f = float(w_hit)
                # Update depth buffer for ALL cells (enables occlusion)
                cells_updated = []
                for c in elem_cells:
                    if w_hit_f < w_nearest.get(c, INF):
                        w_nearest[c] = w_hit_f
                        cells_updated.append(c)

                # Update tile occlusion state for acceleration
                _update_tile_occlusion(cells_updated, w_hit_f)

                # But don't add to visible_cells (no occupancy contribution)
                visible_cells = []
            except Exception:
                visible_cells = []
        elif occlusion_enable and (w_hit is not None):
            try:
                w_hit_f = float(w_hit)
                vis = []
                cells_updated = []
                for c in elem_cells:
                    if w_hit_f < w_nearest.get(c, INF):
                        vis.append(c)
                        if can_occlude:
                            w_nearest[c] = w_hit_f
                            cells_updated.append(c)

                # Update tile occlusion state for non-areal occluders
                if can_occlude and cells_updated:
                    _update_tile_occlusion(cells_updated, w_hit_f)

                visible_cells = vis
            except Exception:
                # fail-open
                visible_cells = elem_cells

        total_cells_3d += len(visible_cells)
        _segment_and_classify(visible_cells, has_3d=True, has_2d=False, elem_meta=elem_meta)

    total_cells_2d = 0
    for ep in proj2d:
        elem_id = ep.get("elem_id")
        category = ep.get("category", "<No Category>")
        loops = ep.get("loops") or []
        is_filled_region = bool(ep.get("is_filled_region", False))

        # Debug only for filled regions, capped by config
        debug_label = None
        debug_enabled = False
        if (
            is_filled_region
            and debug_filled_loops
            and debug_filled_loops_count < debug_filled_loops_max
        ):
            debug_label = "elem={0}, cat={1}".format(elem_id, category)
            debug_enabled = True
            debug_filled_loops_count += 1

        elem_cells = _cells_from_loops_parity(loops, debug_label, debug_enabled)
        
        if valid_cells_set:
            elem_cells = [c for c in elem_cells if c in valid_cells_set]

        total_cells_2d += len(elem_cells)
        elem_meta = {
            "elem_id": elem_id,
            "category": category,
            "is_2d_element": True,
            "is_filled_region": is_filled_region,
        }
        _segment_and_classify(elem_cells, has_3d=False, has_2d=True, elem_meta=elem_meta)

    diagnostics["num_region_cells_3d"] = total_cells_3d
    diagnostics["num_region_cells_2d"] = total_cells_2d
    diagnostics["num_region_cells_total"] = total_cells_3d + total_cells_2d
    diagnostics["num_tiny_regions"] = len(tiny_regions)
    diagnostics["num_linear_regions"] = len(linear_regions)
    diagnostics["num_areal_regions"] = len(areal_regions)

    return {
        "tiny_regions": tiny_regions,
        "linear_regions": linear_regions,
        "areal_regions": areal_regions,
        "diagnostics": diagnostics,
    }

def _get_conservative_boundary_cells(pts, origin_x, origin_y, cell_size, grid_n_i, grid_n_j):
    """
    Identify every grid cell intersected by the polygon edges (segments).
    Ensures thin elements (walls/lines) are captured even if they miss cell centers.
    """
    boundary_cells = set()
    n = len(pts)
    if n < 2:
        return boundary_cells

    s = float(cell_size)
    eps = 1e-9
    
    # Helper: Separating Axis Theorem for Segment vs Cell AABB
    def _segment_hits_cell(p0, p1, ci, cj):
        # Cell bounds
        cx_min = origin_x + ci * s
        cx_max = cx_min + s
        cy_min = origin_y + cj * s
        cy_max = cy_min + s
        
        # 1. AABB Reject (Min/Max check)
        seg_min_x, seg_max_x = (p0[0], p1[0]) if p0[0] < p1[0] else (p1[0], p0[0])
        seg_min_y, seg_max_y = (p0[1], p1[1]) if p0[1] < p1[1] else (p1[1], p0[1])
        
        if seg_max_x < cx_min or seg_min_x > cx_max: return False
        if seg_max_y < cy_min or seg_min_y > cy_max: return False
        
        # 2. SAT Cross Product (Line Distance) check
        # Form line direction vector (dx, dy)
        dx = p1[0] - p0[0]
        dy = p1[1] - p0[1]
        
        # Test the 4 corners of the box against the line equation
        # A corner is "outside" if the cross product has a consistent sign relative to the line.
        # However, for rasterization, a simplified check is often enough:
        # Check if the line intersects the diagonals or if any corner is within distance.
        # Strict SAT: We project the box onto the normal of the line.
        
        # Normal to line = (-dy, dx)
        # Dot product with corners relative to p0
        # corners: (min, min), (max, min), (max, max), (min, max)
        corners = [
            (cx_min, cy_min), (cx_max, cy_min), 
            (cx_max, cy_max), (cx_min, cy_max)
        ]
        
        # Project corners onto normal
        projections = []
        for (vx, vy) in corners:
            # dot((vx-p0x, vy-p0y), (-dy, dx))
            val = (vx - p0[0]) * (-dy) + (vy - p0[1]) * (dx)
            projections.append(val)
            
        # If all projections have the same sign (and are not 0), the box is on one side of the line
        if all(p > eps for p in projections): return False
        if all(p < -eps for p in projections): return False
        
        return True

    # Iterate edges
    for k in range(n):
        p0 = pts[k]
        p1 = pts[(k + 1) % n]
        
        # Optimization: Only check cells in the bounding box of the segment
        seg_min_x = min(p0[0], p1[0])
        seg_max_x = max(p0[0], p1[0])
        seg_min_y = min(p0[1], p1[1])
        seg_max_y = max(p0[1], p1[1])
        
        # Convert to grid indices
        i_start = int(math.floor((seg_min_x - origin_x) / s - eps))
        i_end   = int(math.floor((seg_max_x - origin_x) / s + eps))
        j_start = int(math.floor((seg_min_y - origin_y) / s - eps))
        j_end   = int(math.floor((seg_max_y - origin_y) / s + eps))
        
        # Clamp to grid
        i_start = max(0, i_start)
        i_end   = min(grid_n_i - 1, i_end)
        j_start = max(0, j_start)
        j_end   = min(grid_n_j - 1, j_end)
        
        for i in range(i_start, i_end + 1):
            for j in range(j_start, j_end + 1):
                if (i, j) in boundary_cells:
                    continue
                if _segment_hits_cell(p0, p1, i, j):
                    boundary_cells.add((i, j))
                    
    return boundary_cells

# ------------------------------------------------------------
# RASTERIZATION
# ------------------------------------------------------------

def rasterize_regions_to_cells(regions, grid_data, config, logger):
    """
    Convert regions (tiny / linear / areal) into 3D + 2D raster layers.

    This version adds crop-clamping for *model-like* 2D so that:
        - Filled regions / detail components / detail lines & arcs
          are restricted to the model crop in the 2D layer.
        - Pure annotation (text / tags / dimensions) can still
          occupy cells anywhere in the annotation band.

    Inputs
    ------
    regions : dict
        { "tiny_regions": [...], "linear_regions": [...], "areal_regions": [...] }
        Each region_info is a dict with at least:
            - "cells": [(i,j), ...]
            - "has_3d": bool
            - "has_2d": bool
            - "category": str
            - "is_2d_element": bool
            - "is_filled_region": bool
    grid_data : dict
        Must include:
            - "crop_xy_min": (x_min, y_min)  # model crop extents in view XY
            - "crop_xy_max": (x_max, y_max)
            - "origin_model_xy": (origin_x, origin_y)
            - "cell_size_model": float
    """

    logger.info("Raster: rasterizing regions to cells")

    if not isinstance(regions, dict):
        logger.warn("Raster: regions input not dict; empty maps")
        return {"cells_3d": {}, "cells_2d": {}, "diagnostics": {}}

    tiny_regions = regions.get("tiny_regions") or []
    linear_regions = regions.get("linear_regions") or []
    areal_regions = regions.get("areal_regions") or []

    cells_3d = {}
    cells_2d = {}

    # --- crop clamp helpers -------------------------------------------------
    crop_min = grid_data.get("crop_xy_min")
    crop_max = grid_data.get("crop_xy_max")
    origin_xy = grid_data.get("origin_model_xy")
    cell_size = grid_data.get("cell_size_model")

    _crop_clamp_active = (
        isinstance(crop_min, (list, tuple)) and
        isinstance(crop_max, (list, tuple)) and
        isinstance(origin_xy, (list, tuple)) and
        cell_size not in (None, 0)
    )

    if _crop_clamp_active:
        crop_min_x, crop_min_y = crop_min
        crop_max_x, crop_max_y = crop_max
        origin_x, origin_y = origin_xy
        s = float(cell_size)
        eps = 1e-9

        def _cell_inside_crop(i, j):
            """
            Test cell center against model crop in view XY.
            """
            cx = origin_x + int(i) * s
            cy = origin_y + int(j) * s
            if cx < crop_min_x - eps or cx > crop_max_x + eps:
                return False
            if cy < crop_min_y - eps or cy > crop_max_y + eps:
                return False
            return True
    else:
        # No crop info; do not clamp anything.
        def _cell_inside_crop(i, j):
            return True

    # ------------------------------------------------------------------------

    def _accumulate_region_list(region_list):
        for reg in region_list:
            if not isinstance(reg, dict):
                continue

            has_3d = bool(reg.get("has_3d"))
            has_2d = bool(reg.get("has_2d"))
            cell_list = reg.get("cells") or []

            if not (has_3d or has_2d) or not cell_list:
                continue

            # Decide if this region's 2D layer should be clamped to the crop.
            cat_name = (reg.get("category") or "").lower()
            is_2d_elem = bool(reg.get("is_2d_element", False))
            is_filled_region = bool(reg.get("is_filled_region", False))

            # Treat filled regions + "detail-ish" 2D as model-like.
            is_detailish = (
                "detail" in cat_name or
                cat_name == "detail items" or
                cat_name == "lines"
            )

            clamp_2d_to_crop = (
                _crop_clamp_active and
                is_2d_elem and
                (is_filled_region or is_detailish)
            )

            for cell in cell_list:
                try:
                    i, j = cell
                    i = int(i)
                    j = int(j)
                except Exception:
                    continue

                key = (i, j)

                # 3D contribution is never crop-clamped here.
                if has_3d:
                    cells_3d[key] = cells_3d.get(key, 0) + 1

                if has_2d:
                    if (not clamp_2d_to_crop) or _cell_inside_crop(i, j):
                        cells_2d[key] = cells_2d.get(key, 0) + 1

    # Accumulate from all region tiers
    _accumulate_region_list(tiny_regions)
    _accumulate_region_list(linear_regions)
    _accumulate_region_list(areal_regions)

    num_cells_3d = len(cells_3d)
    num_cells_2d = len(cells_2d)
    num_cells_union = len(set(cells_3d.keys()) | set(cells_2d.keys()))

    diagnostics = {
        "num_cells_3d_layer": num_cells_3d,
        "num_cells_2d_layer": num_cells_2d,
        "num_cells_union": num_cells_union,
    }

    logger.info(
        "Raster: {0} unique cells ({1} in 3D layer, {2} in 2D layer)".format(
            num_cells_union, num_cells_3d, num_cells_2d
        )
    )

    return {
        "cells_3d": cells_3d,
        "cells_2d": cells_2d,
        "diagnostics": diagnostics,
    }

# ------------------------------------------------------------
# 2D annotation classification (TEXT / TAG / DIM / DETAIL / REGION / OTHER)
# ------------------------------------------------------------

def _classify_2d_annotation(elem):
    """
    Classify a 2D element into one of:
        TEXT, TAG, DIM, DETAIL, REGION, OTHER

    This drives the AnnoCells_* buckets. We lean on Category.Name first,
    then BuiltInCategory as a fallback.

    Key behavior for your case:
    - Category.Name == "Lines"       -> DETAIL
    - Category.Name == "Detail Items"-> DETAIL
    - Detail components / detail lines / OST_Lines also -> DETAIL
    """
    if elem is None:
        return "OTHER"

    cat = getattr(elem, "Category", None)
    cat_name = ""
    cat_id_int = None

    if cat is not None:
        try:
            cat_name = getattr(cat, "Name", "") or ""
        except Exception:
            cat_name = ""
        try:
            cat_id_int = cat.Id.IntegerValue
        except Exception:
            cat_id_int = None

    name_l = cat_name.lower().strip()

    # --- Category-name driven classification ---------------------------------

    # Regions
    if "region" in name_l:
        return "REGION"

    # Text
    if "text" in name_l:
        return "TEXT"

    # Dimensions
    if "dimension" in name_l:
        return "DIM"

    # Detail ITEMS (Detail Components)
    if name_l in ("detail items", "detail item"):
        return "DETAIL"

    # Drafting / Detail Lines → NEW bucket "LINES"
    # Revit exposes drafting lines simply as Category.Name == "Lines"
    if name_l == "lines":
        return "LINES"

    # Explicit detail-line-like curves (DetailLine, DetailArc, etc.)
    if "detail" in name_l and "line" in name_l:
        return "LINES"
    if "detail" in name_l and "arc" in name_l:
        return "LINES"

    # Generic Annotations → NOT TAG
    if name_l == "generic annotations":
        return "OTHER"

    # True tags only
    if "tag" in name_l:
        return "TAG"


    # --- BuiltInCategory driven classification (fallback) ---------------------

    if BuiltInCategory is not None and cat_id_int is not None:
        # Detail components / detail lines / lines → DETAIL
        try:
            detail_cats = []

            try:
                detail_cats.append(int(BuiltInCategory.OST_DetailComponents))
            except Exception:
                pass
            try:
                # Some Revit versions expose OST_DetailLines; some don't.
                if hasattr(BuiltInCategory, "OST_DetailLines"):
                    detail_cats.append(int(BuiltInCategory.OST_DetailLines))
            except Exception:
                pass
            try:
                # Drafting lines category
                if hasattr(BuiltInCategory, "OST_Lines"):
                    detail_cats.append(int(BuiltInCategory.OST_Lines))
            except Exception:
                pass

            if detail_cats and cat_id_int in detail_cats:
                return "DETAIL"
        except Exception:
            pass

        # Text
        try:
            if cat_id_int == int(BuiltInCategory.OST_TextNotes):
                return "TEXT"
        except Exception:
            pass

        # Dimensions
        try:
            if cat_id_int == int(BuiltInCategory.OST_Dimensions):
                return "DIM"
        except Exception:
            pass

        # Tags / annotations (generic + specific tag types)
        try:
            tag_cats = []
            try:
                tag_cats.append(int(BuiltInCategory.OST_GenericAnnotation))
            except Exception:
                pass
            try:
                tag_cats.append(int(BuiltInCategory.OST_Tags))
            except Exception:
                pass
            try:
                tag_cats.append(int(BuiltInCategory.OST_WallTags))
            except Exception:
                pass

            if tag_cats and cat_id_int in tag_cats:
                return "TAG"
        except Exception:
            pass

        # Regions (catch any remaining region-like built-in cats if needed)
        # Left minimal to avoid overreach.

    # Fallback
    return "OTHER"

# ------------------------------------------------------------
# VIEW RESOLUTION
# ------------------------------------------------------------

def _unwrap_to_views(candidate):
    flat_items = []

    def _flatten(x):
        if isinstance(x, (list, tuple)):
            for sub in x:
                _flatten(sub)
        else:
            flat_items.append(x)

    if candidate is not None:
        _flatten(candidate)

    views = []

    for item in flat_items:
        v = item
        try:
            if "UnwrapElement" in globals():
                v = UnwrapElement(v)
        except Exception:
            pass

        if not isinstance(v, View):
            try:
                v_int = getattr(v, "InternalElement", None)
                if isinstance(v_int, View):
                    v = v_int
            except Exception:
                pass

        if isinstance(v, View):
            views.append(v)

    return views

def _resolve_views_from_input():
    views = []

    if "IN" in globals() and len(IN) > 0 and IN[0]:
        candidate = IN[0]
        views = _unwrap_to_views(candidate)

        if views:
            LOGGER.info(
                "View resolution: using {0} view(s) from IN[0]".format(
                    len(views)
                )
            )
        else:
            LOGGER.warn(
                "View resolution: IN[0] provided but yielded no DB Views; "
                "falling back to document views"
            )

    if not views and DOC is not None and FilteredElementCollector is not None:
        try:
            views = list(
                FilteredElementCollector(DOC)
                .OfClass(View)
                .ToElements()
            )
        except Exception as ex:
            LOGGER.warn("Failed to collect views from document: {0}".format(ex))
            views = []

    all_views_count = len(views)

    non_template_views = []
    template_count = 0
    for v in views:
        is_template = False
        try:
            is_template = bool(getattr(v, "IsTemplate", False))
        except Exception:
            is_template = False
        if is_template:
            template_count += 1
        else:
            non_template_views.append(v)

    filtered_views = [v for v in non_template_views if grid._is_supported_2d_view(v)]

    LOGGER.info(
        "Filtered to {0} supported 2D non-template view(s) from {1} total View elements "
        "(excluded {2} templates)".format(
            len(filtered_views), all_views_count, template_count
        )
    )

    max_views = CONFIG["run"]["max_views"]
    if max_views is not None and len(filtered_views) > max_views:
        filtered_views = filtered_views[:max_views]

    return filtered_views

# ------------------------------------------------------------
# PIPELINE FOR A SINGLE VIEW
# ------------------------------------------------------------

def process_view(view, config, logger, grid_cache, cache_invalidate):
    t0 = datetime.datetime.now()

    view_id_val = getattr(getattr(view, "Id", None), "IntegerValue", "Unknown")
    view_name = getattr(view, "Name", "<no name>")
    logger.info("=== Processing view: Id={0}, Name='{1}' ===".format(
        view_id_val, view_name))

    proj_cfg = config.get("projection", {}) if isinstance(config, dict) else {}
    include_3d = bool(proj_cfg.get("include_3d", True))
    include_2d = bool(proj_cfg.get("include_2d", True))

    elems3d = collect_3d_elements_for_view(view, config, logger) if include_3d else []
    elems2d = collect_2d_elements_for_view(view, config, logger) if include_2d else []

    driver_elems2d = [e for e in elems2d if grid._is_extent_driver_2d(e)]
    logger.info(
        "Projection: view Id={0} has {1} driver 2D element(s) for grid extents".format(
            view_id_val, len(driver_elems2d)
        )
    )

    # Build Stage-2 clip volume ONCE per view; pass downstream.
    clip_data = build_clip_volume_for_view(view, config, logger)

    t_grid0 = datetime.datetime.now()
    grid_data = grid.build_grid_for_view(view, config, logger, driver_elems2d, clip_data=clip_data, build_clip_volume_for_view_fn=build_clip_volume_for_view)
    t_grid1 = datetime.datetime.now()


    type_counts_3d = _summarize_elements_by_type(elems3d)
    type_counts_2d = _summarize_elements_by_type(elems2d)
    cat_counts_3d = _summarize_elements_by_category(elems3d)
    cat_counts_2d = _summarize_elements_by_category(elems2d)

    logger.info("Debug: view Id={0} 3D types: {1}".format(
        view_id_val,
        ", ".join("{0}={1}".format(k, v) for k, v in sorted(type_counts_3d.items()))
    ))
    logger.info("Debug: view Id={0} 2D types: {1}".format(
        view_id_val,
        ", ".join("{0}={1}".format(k, v) for k, v in sorted(type_counts_2d.items()))
    ))
    logger.info("Debug: view Id={0} 3D cats: {1}".format(
        view_id_val,
        ", ".join("{0}={1}".format(k, v) for k, v in sorted(cat_counts_3d.items()))
    ))
    logger.info("Debug: view Id={0} 2D cats: {1}".format(
        view_id_val,
        ", ".join("{0}={1}".format(k, v) for k, v in sorted(cat_counts_2d.items()))
    ))

    t_proj0 = datetime.datetime.now()
    projected = project_elements_to_view_xy(
        view,
        grid_data,
        clip_data,
        elems3d,
        elems2d,
        config,
        logger
    )
    t_proj1 = datetime.datetime.now()

    t_regions0 = datetime.datetime.now()
    regions = build_regions_from_projected(projected, grid_data, config, logger)
    t_regions1 = datetime.datetime.now()

    t_raster0 = datetime.datetime.now()
    raster = rasterize_regions_to_cells(regions, grid_data, config, logger)
    t_raster1 = datetime.datetime.now()

    t_occ0 = datetime.datetime.now()
    occupancy = grid.compute_occupancy(grid_data, raster, config, logger)
    t_occ1 = datetime.datetime.now()

    timings = {
        "grid_clip_sec": (t_grid1 - t_grid0).total_seconds(),
        "projection_sec": (t_proj1 - t_proj0).total_seconds(),
        "regions_sec": (t_regions1 - t_regions0).total_seconds(),
        "raster_sec": (t_raster1 - t_raster0).total_seconds(),
        "occupancy_sec": (t_occ1 - t_occ0).total_seconds(),
    }

    logger.info(
        "Timings: grid+clip={0:.3f}s, proj={1:.3f}s, regions={2:.3f}s, raster={3:.3f}s, occupancy={4:.3f}s".format(
            timings["grid_clip_sec"],
            timings["projection_sec"],
            timings["regions_sec"],
            timings["raster_sec"],
            timings["occupancy_sec"],
        )
    )


    anno_cells = {
        "TEXT": set(),
        "TAG": set(),
        "DIM": set(),
        "DETAIL": set(),
        "LINES": set(),
        "REGION": set(),
        "OTHER": set(),
    }
    ext_cells_any = set()
    ext_cells_dwg = set()
    ext_cells_rvt = set()
    native_cells_any = set()

    tiny_regions = []
    linear_regions = []
    areal_regions = []
    if isinstance(regions, dict):
        tiny_regions = regions.get("tiny_regions") or []
        linear_regions = regions.get("linear_regions") or []
        areal_regions = regions.get("areal_regions") or []
    all_regions = tiny_regions + linear_regions + areal_regions

    ElementId_cls = None
    RevitLinkInstance_cls = None
    ImportInstance_cls = None
    try:
        from Autodesk.Revit.DB import ElementId, RevitLinkInstance, ImportInstance
        ElementId_cls = ElementId
        RevitLinkInstance_cls = RevitLinkInstance
        ImportInstance_cls = ImportInstance
    except Exception:
        ElementId_cls = None
        RevitLinkInstance_cls = None
        ImportInstance_cls = None

    for reg in all_regions:
        if not isinstance(reg, dict):
            continue

        cells = reg.get("cells") or []
        if not cells:
            continue

        cell_set = set()
        for c in cells:
            try:
                i, j = c
                cell_set.add((int(i), int(j)))
            except Exception:
                continue

        if not cell_set:
            continue

        elem_id_val = reg.get("elem_id", None)

        # Always resolve host element for annotation classification only.
        # (For 3D link proxies this will usually be None, which is fine.)
        e_obj = None
        if elem_id_val is not None and DOC is not None and ElementId_cls is not None:
            try:
                e_obj = DOC.GetElement(ElementId_cls(elem_id_val))
            except Exception:
                e_obj = None

        # Ext-cells classification must use region "source" (linked element ids are not host ids)
        src = reg.get("source", None)

        is_rvt_link = (src == "RVT_LINK")
        is_import = (src == "DWG_IMPORT")

        if is_rvt_link or is_import:
            ext_cells_any |= cell_set
            if is_rvt_link:
                ext_cells_rvt |= cell_set
            elif is_import:
                ext_cells_dwg |= cell_set
        else:
            native_cells_any |= cell_set

        if not reg.get("is_2d_element", False):
            continue

        if reg.get("is_filled_region", False):
            ann_bucket = "REGION"
        else:
            try:
                ann_bucket = _classify_2d_annotation(e_obj)
            except Exception:
                ann_bucket = "OTHER"

        if ann_bucket not in anno_cells:
            ann_bucket = "OTHER"

        anno_cells[ann_bucket].update(cell_set)

    ext_cells_only = ext_cells_any - native_cells_any

    if isinstance(occupancy, dict):
        occ_diag = occupancy.get("diagnostics", {}) or {}
        occ_map = occupancy.get("occupancy_map", {}) or {}
    else:
        occ_diag = {}
        occ_map = {}

    # Build optional occupancy preview rectangles for Dynamo
    try:
        occ_rects_3d, occ_rects_2d, occ_rects_2d_over_3d = _build_occupancy_preview_rects(
            view, grid_data, occupancy, config, logger
        )
    except Exception as ex:
        logger.warn(
            "Debug: failed to build occupancy preview rects for view Id={0}: {1}".format(
                view_id_val, ex
            )
        )
        occ_rects_3d, occ_rects_2d, occ_rects_2d_over_3d = [], [], []


    total_grid_cells = int(len(grid_data.get("valid_cells") or []))
    num_3d_only = int(occ_diag.get("num_cells_3d_only", 0))
    num_2d_only = int(occ_diag.get("num_cells_2d_only", 0))
    num_2d_over_3d = int(occ_diag.get("num_cells_2d_over_3d", 0))
    num_occ = num_3d_only + num_2d_only + num_2d_over_3d
    empty_cells = max(0, total_grid_cells - num_occ)

    # --- Derive cell size in feet -----------------------------------------
    try:
        cell_size_ft = float(grid_data.get("cell_size_model") or 0.0)
        # Round to 6 decimal places to ensure cache/fresh values match
        # (avoids hash mismatches from floating point precision differences)
        cell_size_ft = round(cell_size_ft, 6)
    except Exception:
        cell_size_ft = 0.0

    # --- Optional debug PNG of occupancy ----------------------------------
    occupancy_png_path = None
    try:
        occupancy_png_path = _build_occupancy_png(
            view,
            grid_data,
            occ_map,   # full occupancy_map from compute_occupancy(...)
            config,
            logger
        )
    except Exception as ex:
        logger.warn(
            "Occupancy: could not build PNG for view Id={0}: {1}"
            .format(view.Id, ex)
        )

    view_type_str = _get_view_type_name(view)

    row = {
        "ViewId": view_id_val,
        "ViewName": view_name,
        "ViewType": view_type_str,
        "TotalCells": total_grid_cells,
        "Empty": empty_cells,
        "ModelOnly": num_3d_only,
        "AnnoOnly": num_2d_only,
        "Overlap": num_2d_over_3d,
        "Ext_Cells_Any": len(ext_cells_any),
        "Ext_Cells_Only": len(ext_cells_only),
        "Ext_Cells_DWG": len(ext_cells_dwg),
        "Ext_Cells_RVT": len(ext_cells_rvt),
        "AnnoCells_TEXT": len(anno_cells.get("TEXT", set())),
        "AnnoCells_TAG": len(anno_cells.get("TAG", set())),
        "AnnoCells_DIM": len(anno_cells.get("DIM", set())),
        "AnnoCells_DETAIL": len(anno_cells.get("DETAIL", set())),
        "AnnoCells_LINES":  len(anno_cells.get("LINES", set())),
        "AnnoCells_REGION": len(anno_cells.get("REGION", set())),
        "AnnoCells_OTHER": len(anno_cells.get("OTHER", set())),
        "CellSize_ft": cell_size_ft,
    }

    debug_cfg = config.get("debug", {}) if isinstance(config, dict) else {}
    enable_preview_polys = bool(debug_cfg.get("enable_preview_polys", False))

    debug = {
        "view": {
            "id": view_id_val,
            "name": view_name,
            "type": view_type_str,
        },
        "grid": grid_data,
        "clip": clip_data,
        "projection": projected,
        "regions": regions,
        "raster": raster,
        "occupancy": occupancy,
        # Preview rectangles for occupancy cells by layer (optional)
        "occupancy_rects_3d_only": occ_rects_3d,
        "occupancy_rects_2d_only": occ_rects_2d,
        "occupancy_rects_2d_over_3d": occ_rects_2d_over_3d,
        # PNG debug path
        "occupancy_png_path": occupancy_png_path,
        "elem3d_type_counts": type_counts_3d,
        "elem2d_type_counts": type_counts_2d,
        "elem3d_cat_counts": cat_counts_3d,
        "elem2d_cat_counts": cat_counts_2d,
        "driver_2d_count": len(driver_elems2d),
        "num_tiny_regions": len(tiny_regions),
        "num_linear_regions": len(linear_regions),
        "num_areal_regions": len(areal_regions),
        "num_occupancy_cells": len(occ_map),
        "occupancy_png_path": occupancy_png_path,
    }

    if not enable_preview_polys:
        debug["grid"]["crop_rect_geom"] = None
        debug["grid"]["grid_rect_geom"] = None
        proj_preview = projected if isinstance(projected, dict) else {}
        proj_preview["preview_2d_rects"] = []
        proj_preview["preview_3d_rects"] = []
        debug["projection"] = proj_preview

    # Strip Revit/geometry objects from debug so Dynamo Watch can display OUT
    grid_dbg = debug.get("grid", {})
    if isinstance(grid_dbg, dict) and "crop_box_model" in grid_dbg:
        grid_dbg["crop_box_model"] = None
        
    logger.info(
        "AnnoCells: TEXT={0}, TAG={1}, DIM={2}, DETAIL={3}, LINES={4}, REGION={5}, OTHER={6}"
        .format(
            len(anno_cells["TEXT"]),
            len(anno_cells["TAG"]),
            len(anno_cells["DIM"]),
            len(anno_cells["DETAIL"]),
            len(anno_cells["LINES"]),
            len(anno_cells["REGION"]),
            len(anno_cells["OTHER"]),
        )
    )

    logger.info("=== Finished view Id={0} ===".format(view_id_val))

    t1 = datetime.datetime.now()
    elapsed = (t1 - t0).total_seconds()
    
    # Per-view summary debug
    logger.info(
        "=== View done: Id={0}, Name='{1}', TotalCells={2}, occ_cells={3} ===".format(
            view_id_val,
            view_name,
            total_grid_cells,
            occ_diag.get("num_cells_total", len(occ_map))
        )
    )

    return {
        "row": row,
        "debug": debug,
        "elapsed_sec": elapsed,
        "timings": timings,
    }

# ------------------------------------------------------------
# CSV EXPORT
# ------------------------------------------------------------

def _export_debug_json(results, config, logger):
    debug_cfg = config.get("debug", {}) or {}
    if not (debug_cfg.get("enable", True) and debug_cfg.get("write_debug_json", False)):
        logger.info("Debug JSON export disabled by config.")
        return

    selected_ids = _select_debug_view_ids(results, config, logger)

    debug_views = []
    for res in results:
        row = res.get("row") or {}
        view_id = row.get("ViewId") or row.get("ViewUniqueId")
        if view_id is None:
            continue
        if view_id not in selected_ids:
            res.pop("debug", None)
            continue

        dbg = res.get("debug") or {}
        debug_views.append(
            {
                "view_id": view_id,
                "row": row,
                "debug": dbg,
            }
        )

    payload = {
        "exporter_version": config.get("exporter_version", ""),
        "view_count": len(debug_views),
        "views": debug_views,
    }

    payload = _json_sanitize_keys(payload)

    path = config.get("paths", {}).get("debug_json", "debug.json")
    try:
        with open(path, "w") as f:
            json.dump(payload, f, indent=2, default=_json_default)
        logger.info("Wrote debug JSON: {0}".format(path))
    except Exception as ex:
        logger.error("Failed to write debug JSON: {0}".format(ex))

def _export_timings_csv(results, config, logger):
    paths = config.get("paths", {}) or {}
    csv_path = paths.get("csv_timings")
    if not csv_path:
        logger.info("No csv_timings path configured; skipping timings export.")
        return
        
    run_date_str = paths.get("run_date_str", "")
    run_id = paths.get("run_id", "")
    
    rows = []

    for res in results:
        row = res.get("row") or {}
        timings = res.get("timings") or {}

        view_id = row.get("ViewId") or row.get("ViewUniqueId")
        view_name = row.get("ViewName") or row.get("ViewTitle") or ""
        view_type = row.get("ViewType") or ""

        elapsed_total = float(res.get("elapsed_sec") or 0.0)

        rows.append(
            {
                "Date": run_date_str,
                "RunId": run_id,
                "ViewId": view_id,
                "ViewName": view_name,
                "ViewType": view_type,
                "GridClipSec": timings.get("grid_clip_sec", 0.0),
                "ProjectionSec": timings.get("projection_sec", 0.0),
                "RegionsSec": timings.get("regions_sec", 0.0),
                "RasterSec": timings.get("raster_sec", 0.0),
                "OccupancySec": timings.get("occupancy_sec", 0.0),
                "TotalElapsedSec": elapsed_total,
            }
        )

    if not rows:
        logger.info("No timing rows to export.")
        return

    # Define a stable column order
    headers = [
        "Date",
        "RunId",
        "ViewId",
        "ViewName",
        "ViewType",
        "GridClipSec",
        "ProjectionSec",
        "RegionsSec",
        "RasterSec",
        "OccupancySec",
        "TotalElapsedSec",
    ]

    matrix_rows = []
    for r in rows:
        matrix_rows.append([r.get(h, "") for h in headers])

    try:
        export_csv._append_csv_rows(csv_path, headers, matrix_rows, logger)
        logger.info("Appended {0} timing rows to {1}".format(len(rows), csv_path))
    except Exception as ex:
        logger.error("Failed to write timings CSV: {0}".format(ex))

def _select_debug_view_ids(results, config, logger):
    """
    Decide which views get full debug payloads.

    Strategy:
    - Always include any ids in debug_view_ids.
    - Then include up to max_debug_views more, chosen from:
        - elapsed_sec >= min_elapsed_for_debug_sec
        - optionally excluding cached views
      sorted by elapsed_sec descending.
    """
    debug_cfg = config.get("debug", {}) or {}
    max_debug = debug_cfg.get("max_debug_views", 20)
    min_elapsed = debug_cfg.get("min_elapsed_for_debug_sec", 0.0)
    explicit_ids = set(debug_cfg.get("debug_view_ids", []) or [])
    include_cached = bool(debug_cfg.get("include_cached_views", False))

    # First pass: collect candidates
    candidates = []
    for res in results:
        row = res.get("row") or {}
        view_id = row.get("ViewId") or row.get("ViewUniqueId") or None
        if view_id is None:
            continue

        elapsed = float(res.get("elapsed_sec") or 0.0)
        from_cache = bool(res.get("from_cache", False))

        # Always keep explicit ids
        if view_id in explicit_ids:
            candidates.append((view_id, elapsed, from_cache, True))
            continue

        # Filter by elapsed + cache flag
        if elapsed < min_elapsed:
            continue
        if (not include_cached) and from_cache:
            continue

        candidates.append((view_id, elapsed, from_cache, False))

    # Build final selection
    selected = set(explicit_ids)

    # Add non-explicit candidates sorted by elapsed descending
    non_explicit = [c for c in candidates if not c[3]]
    non_explicit.sort(key=lambda x: x[1], reverse=True)

    for view_id, elapsed, from_cache, _ in non_explicit:
        if len(selected) >= max_debug:
            break
        selected.add(view_id)

    logger.info(
        "Debug selection: {0} view(s) selected for debug JSON (max {1})".format(
            len(selected), max_debug
        )
    )
    return selected

def _export_view_level_csvs(views, results, run_start, config, logger, exporter_version=None):
    """
    CSV export:

    - views_core_YYYY-MM-DD.csv  (per-view core metadata)
    - views_vop_YYYY-MM-DD.csv   (per-view VOP / occupancy metrics)

    Header structure matches the previous CSVs with the addition of:
        - FromCache (boolean) in both core and vop files.
    """
    if not views or not results:
        return

    export_cfg = (config or {}).get("export") or {}
    out_dir = export_cfg.get("output_dir") or ""
    enable_rows = bool(export_cfg.get("enable_rows_csv", False))

    if not enable_rows:
        return
    if not export_csv._ensure_dir(out_dir, logger):
        return

    # Date string: either override from IN[5] or run_start date.
    run_date_str = None
    try:
        if "IN" in globals() and len(IN) > 5:
            override = IN[5]
            if override is not None:
                override_s = str(override).strip()
                if override_s and override_s.lower() not in ("bydate", "auto", "date"):
                    safe = []
                    for ch in override_s:
                        if ch.isalnum() or ch in ("-", "_"):
                            safe.append(ch)
                    run_date_str = "".join(safe) or None
    except Exception:
        run_date_str = None

    if not run_date_str:
        run_date_str = run_start.strftime("%Y-%m-%d")


    run_id = run_start.strftime("%Y%m%dT%H%M%S")
    config_hash = _compute_config_hash(config)

    paths_cfg = config.setdefault("paths", {})
    paths_cfg["run_date_str"] = run_date_str
    paths_cfg["run_id"] = run_id

    if not exporter_version:
        exporter_version = EXPORTER_BASE_ID

    # ------------------------------------------------------------------
    # Filenames
    # ------------------------------------------------------------------
    core_base = export_cfg.get("core_filename") or "views_core.csv"
    vop_base = export_cfg.get("vop_filename") or "views_vop.csv"

    core_prefix = os.path.splitext(core_base)[0]
    vop_prefix = os.path.splitext(vop_base)[0]

    core_filename = "{0}_{1}.csv".format(core_prefix, run_date_str)
    vop_filename = "{0}_{1}.csv".format(vop_prefix, run_date_str)

    core_path = os.path.join(out_dir, core_filename)
    vop_path = os.path.join(out_dir, vop_filename)
    
    # ------------------------------------------------------------------
    # Save per-run paths for downstream exports (timings, debug JSON)
    # ------------------------------------------------------------------
    paths_cfg = config.setdefault("paths", {})
    paths_cfg["csv_core"] = core_path
    paths_cfg["csv_vop"] = vop_path

    # Timings CSV: same date suffix as core/vop files
    timings_base = export_cfg.get("csv_timings") or "timings.csv"
    timings_prefix = os.path.splitext(timings_base)[0]
    timings_filename = "{0}_{1}.csv".format(timings_prefix, run_date_str)
    timings_path = os.path.join(out_dir, timings_filename)
    paths_cfg["csv_timings"] = timings_path

    # Debug JSON: place in the same output folder
    debug_filename = "debug_{0}.json".format(run_date_str)
    debug_path = os.path.join(out_dir, debug_filename)
    paths_cfg["debug_json"] = debug_path

    # ------------------------------------------------------------------
    # Headers (previous + FromCache)
    # ------------------------------------------------------------------
    core_headers = [
        "Date",
        "RunId",
        "ViewId",
        "ViewUniqueId",
        "ViewName",
        "ViewType",
        "SheetNumber",
        "IsOnSheet",
        "Scale",
        "Discipline",
        "Phase",
        "ViewTemplate_Name",
        "IsTemplate",
        "ExporterVersion",
        "ConfigHash",
        "ViewFrameHash",
        "FromCache",
        "ElapsedSec",
    ]

    vop_headers = [
        "Date",
        "RunId",
        "ViewId",
        "ViewName",
        "ViewType",
        "TotalCells",
        "Empty",
        "ModelOnly",
        "AnnoOnly",
        "Overlap",
        "Ext_Cells_Any",
        "Ext_Cells_Only",
        "Ext_Cells_DWG",
        "Ext_Cells_RVT",
        "AnnoCells_TEXT",
        "AnnoCells_TAG",
        "AnnoCells_DIM",
        "AnnoCells_DETAIL",
        "AnnoCells_LINES",
        "AnnoCells_REGION",
        "AnnoCells_OTHER",
        "CellSize_ft",
        "RowSource",
        "ExporterVersion",
        "ConfigHash",
        "FromCache",
        "ElapsedSec",
    ]


    # ------------------------------------------------------------------
    # Precompute sheet placement map once (ViewId -> (SheetNumber, IsOnSheet))
    # ------------------------------------------------------------------
    sheet_map = {}
    try:
        from Autodesk.Revit.DB import Viewport
        if DOC is not None and FilteredElementCollector is not None:
            vp_col = FilteredElementCollector(DOC).OfClass(Viewport)
            for vp in vp_col:
                try:
                    v_id = vp.ViewId
                    s_id = vp.SheetId
                    view_id_int = v_id.IntegerValue
                    sheet = DOC.GetElement(s_id)
                    sheet_num = getattr(sheet, "SheetNumber", "") if sheet is not None else ""
                    sheet_map[view_id_int] = (sheet_num, True)
                except Exception:
                    continue
    except Exception:
        # If anything goes wrong, we just leave sheet_map empty
        sheet_map = {}

    # ------------------------------------------------------------------
    # Build row lists
    # ------------------------------------------------------------------
    core_rows = []
    vop_rows = []

    for v, res in zip(views, results):
        if not res:
            continue
        row = res.get("row") or {}
        if not isinstance(row, dict):
            row = {}

        # FromCache flag – default to False if not present
        from_cache = bool(row.get("FromCache", False))
        
        # Per-view processing time (seconds)
        try:
            elapsed_sec = float(res.get("elapsed_sec") or 0.0)
        except Exception:
            elapsed_sec = 0.0

        # IDs and names (prefer row values; fall back to view where needed)
        try:
            view_id_val = int(row.get("ViewId", 0) or getattr(getattr(v, "Id", None), "IntegerValue", 0))
        except Exception:
            view_id_val = 0

        view_unique_id = ""
        try:
            view_unique_id = getattr(v, "UniqueId", "") or ""
        except Exception:
            pass

        view_name = row.get("ViewName") or getattr(v, "Name", "<no name>")
        view_type_str = row.get("ViewType") or _get_view_type_name(v)

        # Sheet info
        sheet_number = ""
        is_on_sheet = False
        if view_id_val in sheet_map:
            sheet_number, is_on_sheet = sheet_map[view_id_val]
        else:
            sheet_number = ""
            is_on_sheet = False

        # Scale
        try:
            scale_val = getattr(v, "Scale", None)
            scale = int(scale_val) if isinstance(scale_val, int) else ""
        except Exception:
            scale = ""

        # Discipline (string name)
        discipline = _get_view_discipline_name(v)

        # Phase
        phase_name = _get_view_phase_name(v)

        # View template name
        vt_name = ""
        try:
            vt_id = getattr(v, "ViewTemplateId", None)
            if vt_id is not None and DOC is not None:
                vt_elem = DOC.GetElement(vt_id)
                vt_name = getattr(vt_elem, "Name", "") or ""
        except Exception:
            vt_name = ""

        # IsTemplate
        try:
            is_template = bool(getattr(v, "IsTemplate", False))
        except Exception:
            is_template = False

        # ViewFrameHash – best-effort hash of a few stable properties.
        try:
            frame_payload = "{0}|{1}|{2}|{3}".format(
                view_type_str,
                scale,
                sheet_number,
                discipline,
            )
            view_frame_hash = _stable_hex_digest(frame_payload, length=8)
        except Exception:
            view_frame_hash = ""


        # --------------------
        # Core CSV row
        # --------------------
        core_row = [
            run_date_str,
            run_id,
            view_id_val,
            view_unique_id,
            view_name,
            view_type_str,
            sheet_number,
            is_on_sheet,
            scale,
            discipline,
            phase_name,
            vt_name,
            is_template,
            exporter_version,
            config_hash,
            view_frame_hash,
            from_cache,
            elapsed_sec,
        ]

        core_rows.append(core_row)

        # --------------------
        # VOP CSV row
        # --------------------
        total_cells = row.get("TotalCells", "")
        empty_cells = row.get("Empty", "")
        model_only = row.get("ModelOnly", "")
        anno_only = row.get("AnnoOnly", "")
        overlap = row.get("Overlap", "")
        ext_any = row.get("Ext_Cells_Any", "")
        ext_only = row.get("Ext_Cells_Only", "")
        ext_dwg = row.get("Ext_Cells_DWG", "")
        ext_rvt = row.get("Ext_Cells_RVT", "")
        ann_text = row.get("AnnoCells_TEXT", "")
        ann_tag = row.get("AnnoCells_TAG", "")
        ann_dim = row.get("AnnoCells_DIM", "")
        ann_detail = row.get("AnnoCells_DETAIL", "")
        ann_lines = row.get("AnnoCells_LINES", "")
        ann_region = row.get("AnnoCells_REGION", "")
        ann_other = row.get("AnnoCells_OTHER", "")
        cell_size_ft = row.get("CellSize_ft", "")
        row_source = "VOP_v47"  # Row source marker

        vop_row = [
            run_date_str,
            run_id,
            view_id_val,
            view_name,
            view_type_str,
            total_cells,
            empty_cells,
            model_only,
            anno_only,
            overlap,
            ext_any,
            ext_only,
            ext_dwg,
            ext_rvt,
            ann_text,
            ann_tag,
            ann_dim,
            ann_detail,
            ann_lines,
            ann_region,
            ann_other,
            cell_size_ft,
            row_source,
            exporter_version,
            config_hash,
            from_cache,
            elapsed_sec,
        ]

        vop_rows.append(vop_row)

    # ------------------------------------------------------------------
    # Append to CSVs
    # ------------------------------------------------------------------
    if core_rows:
        export_csv._append_csv_rows(core_path, core_headers, core_rows, logger)
    if vop_rows:
        export_csv._append_csv_rows(vop_path, vop_headers, vop_rows, logger)

def _build_views_out_for_dynamo(results):
    """
    Build a compact views-out structure for Dynamo.

    We don't re-group metrics into pretty nested blocks; we just send out
    the same 'row' dict used for CSV export (plus elapsed_sec), one per view.
    """
    views_out = []
    if not results:
        return views_out

    for res in results:
        if not isinstance(res, dict):
            continue
        row = res.get("row") or {}
        if not isinstance(row, dict):
            row = {}

        elapsed_sec = float(res.get("elapsed_sec") or 0.0)

        # Compact per-view payload: row metrics + elapsed_sec
        view_out = dict(row)  # shallow copy so we don't mutate
        view_out["elapsed_sec"] = elapsed_sec

        views_out.append(view_out)

    return views_out

# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------

def main():
    run_start = datetime.datetime.now()
    
    # === CLEANUP FROM PREVIOUS RUNS ===
    
    # 1. Clear extractor cache (REQUIRED)
    if hasattr(project_elements_to_view_xy, '_extractor_cache'):
        cleared_count = len(project_elements_to_view_xy._extractor_cache)
        project_elements_to_view_xy._extractor_cache.clear()
        if cleared_count > 0:
            LOGGER.info("Cleared {0} cached extractor(s) from previous run".format(cleared_count))
    
    # 2. Clear logger message buffer (OPTIONAL - if logger accumulates)
    if hasattr(LOGGER, 'messages') and isinstance(LOGGER.messages, list):
        old_count = len(LOGGER.messages)
        LOGGER.messages = []
        # Don't log about clearing messages (creates a circular issue!)
    
    # 3. Health check (OPTIONAL - for debugging only)
    # LOGGER.info("=== HEALTH CHECK ===")
    # LOGGER.info("Extractor cache cleared: {0} items".format(cleared_count))
    # LOGGER.info("=== END HEALTH CHECK ===")
    
    # === END CLEANUP ===

    LOGGER.info("Exporter start: {0}".format(run_start))

    # Validate configuration
    from core.config import validate_config
    validation_errors = validate_config(CONFIG, LOGGER)
    if validation_errors:
        LOGGER.warn("Configuration validation found {0} issue(s)".format(len(validation_errors)))

    force_recompute, cache_enabled = _get_reset_and_cache_flags()

    # Apply any Dynamo-driven overrides to CONFIG (output_dir, cache, PNG)
    _apply_runtime_inputs_to_config(CONFIG, LOGGER)

    # Exporter version remains stable across runs
    exporter_version = EXPORTER_BASE_ID

    LOGGER.info("ForceRecompute (IN[2]) = {0}".format(force_recompute))
    LOGGER.info("UseCache (IN[1]) = {0}".format(cache_enabled))

    # Cache controls
    cache_cfg = CONFIG.get("cache", {}) if isinstance(CONFIG, dict) else {}
    cache_enabled = bool(cache_cfg.get("enabled", False))

    views = _resolve_views_from_input()
    LOGGER.info("Found {0} view(s) to process".format(len(views)))
    # === SORT VIEWS BY (SCALE, GRID_SIZE) ===
    def get_cache_key(v):
        try:
            scale = int(getattr(v, "Scale", 96))
            # Estimate grid size (will be computed later, but approximate is fine)
            return (scale, scale * 0.0104167)  # ~1/8" in feet per scale unit
        except Exception:
            return (999, 999)  # Unknowns at end
    
    views_sorted = sorted(views, key=get_cache_key)
    
    LOGGER.info("Sorted {0} views by scale/grid for better cache performance".format(len(views_sorted)))
    # === END SORTING ===
    

        
    

    # Determine project + cache file path (if any)
    cache_path = None
    project_guid = None
    config_hash = _compute_config_hash(CONFIG)
    view_cache = {
        "exporter_version": exporter_version,
        "config_hash": config_hash,
        "project_guid": None,
        "views": {},
    }

    if cache_enabled and views:
        first_view = views[0]
        doc = getattr(first_view, "Document", DOC)
        project_guid = _get_project_guid(doc)
        cache_path = _get_cache_file_path(CONFIG, doc)
        LOGGER.info("Cache: path = '{0}'".format(cache_path))

        if cache_enabled and not force_recompute:
            view_cache = _load_view_cache(
                cache_path,
                exporter_version,
                config_hash,
                project_guid,
                LOGGER,
            )
        else:
            LOGGER.info("Cache: ForceRecompute=True or cache disabled; ignoring existing cache.")
            view_cache = {
                "exporter_version": exporter_version,
                "config_hash": config_hash,
                "project_guid": project_guid,
                "views": {},
            }

    grid_cache = {}  # reserved for future in-memory cache (per run)
    results = []

    cached_views = view_cache.get("views") if (cache_enabled and isinstance(view_cache, dict)) else {}
    if cached_views is None:
        cached_views = {}






    for view in views_sorted:  # Use sorted list
        if view is None:
            continue

        v_id = getattr(getattr(view, "Id", None), "IntegerValue", None)
        v_key = str(v_id)

        elem_ids = _collect_element_ids_for_signature(view, LOGGER)
        current_sig = _compute_view_signature(view, elem_ids)

        res = None
        from_cache = False

        if cache_enabled and not force_recompute:
            cached = cached_views.get(v_key)
            if isinstance(cached, dict) and "row" in cached:
                cached_sig = cached.get("view_signature")
                if cached_sig == current_sig:
                    row = cached.get("row") or {}
                    row["FromCache"] = True
                    res = {
                        "row": row,
                        "debug": {},
                        "elapsed_sec": 0.0,  # cached = free this run
                        "from_cache": True,
                    }
                    from_cache = True
                    LOGGER.info(
                        "Cache: using cached metrics for view Id={0} (signature match)".format(v_id)
                    )

        if res is None:
            # Compute fresh
            res = process_view(view, CONFIG, LOGGER, grid_cache, False)
            if res is None:
                continue

            row = res.get("row") or {}
            if not isinstance(row, dict):
                row = {}
            row["FromCache"] = False
            
            # === PERIODIC GC (CPython3) ===
            view_index = len(results)
            if view_index > 0 and view_index % 10 == 0:
                try:
                    import gc
                    collected = gc.collect()
                    LOGGER.info("GC after view {0}: collected {1} objects".format(
                        view_index, collected
                    ))
                except Exception as ex:
                    LOGGER.info("GC failed: {0}".format(ex))
            # === END PERIODIC GC ===
            
            res["row"] = row
            res["from_cache"] = False

            if cache_enabled:
                elapsed_sec = float(res.get("elapsed_sec") or 0.0)
                res["elapsed_sec"] = elapsed_sec
                cached_views[v_key] = {
                    "view_signature": current_sig,
                    "row": row,
                    "elapsed_sec": elapsed_sec,
                }

        # Safety: guarantee FromCache
        row = res.get("row") or {}
        if "FromCache" not in row:
            row["FromCache"] = from_cache
            res["row"] = row
        res["from_cache"] = bool(row.get("FromCache"))

        results.append(res)

    # CSV export (view-level metrics)
    # Use views_sorted to match the order of results (which were built from views_sorted)
    try:
        _export_view_level_csvs(views_sorted, results, run_start, CONFIG, LOGGER, exporter_version)
    except Exception as ex:
        LOGGER.warn("Export: exception during CSV export: {0}".format(ex))

    # Timings CSV export (new)
    try:
        _export_timings_csv(results, CONFIG, LOGGER)
    except Exception as ex:
        LOGGER.warn("Export: exception during timings CSV export: {0}".format(ex))

    # Debug JSON export (new)
    try:
        _export_debug_json(results, CONFIG, LOGGER)
    except Exception as ex:
        LOGGER.warn("Export: exception during debug JSON export: {0}".format(ex))


    run_end = datetime.datetime.now()
    total_elapsed = (run_end - run_start).total_seconds()

    LOGGER.info("Exporter finished: {0}".format(run_end))
    LOGGER.info("Total elapsed seconds: {0}".format(total_elapsed))
    LOGGER.info("Processed {0} view(s)".format(len(results)))

    # Persist cache (if enabled)
    if cache_enabled and cache_path and not force_recompute:
        view_cache["exporter_version"] = exporter_version
        view_cache["config_hash"] = config_hash
        view_cache["project_guid"] = project_guid
        view_cache["views"] = cached_views
        _save_view_cache(cache_path, view_cache, LOGGER)


    # Build compact views_out for Dynamo
    views_out = _build_views_out_for_dynamo(results)

    include_run_log = bool(CONFIG.get("debug", {}).get("include_run_log_in_out", False))
    run_log = LOGGER.lines if include_run_log else None

    out_dict = {
        "signature": exporter_version,
        "reset_flag": force_recompute,
        "run_start": run_start.strftime("%Y-%m-%d %H:%M:%S"),
        "run_end": run_end.strftime("%Y-%m-%d %H:%M:%S"),
        "total_elapsed_sec": float(total_elapsed),
        "view_count": len(results),
        "views": views_out,
    }

    if include_run_log and run_log is not None:
        out_dict["log"] = run_log
        
    # === FINAL CLEANUP (CPython3) ===
    try:
        import gc
        
        if hasattr(project_elements_to_view_xy, '_extractor_cache'):
            cache_size = len(project_elements_to_view_xy._extractor_cache)
            project_elements_to_view_xy._extractor_cache.clear()
            LOGGER.info("Final cleanup: cleared {0} extractors".format(cache_size))
        
        collected = gc.collect()
        LOGGER.info("Final GC: collected {0} objects".format(collected))
        
    except Exception as ex:
        LOGGER.info("Final cleanup warning: {0}".format(ex))
    # === END FINAL CLEANUP ===

    return [out_dict]

# ------------------------------------------------------------
# DYNAMO OUT (A3 diagnostic)
# ------------------------------------------------------------

def _safe_main():
    try:
        return main()
    except Exception as ex:
        # If we get here, main() threw – surface it as a dict, never leave OUT empty
        return {
            "error": "Exception in main()",
            "message": str(ex),
            "trace_hint": "Check Revit API imports, view collection, or process_view()"
        }

# Only auto-run when executed as the primary Dynamo Python node script.
# When imported from a thin loader, do not execute on import.
if "IN" in globals():
    OUT = _safe_main()
