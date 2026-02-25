"""
VOP Interwoven Pipeline - Main Processing Logic.

Implements the interwoven model pass (Pass A+B merged) with:
- Front-to-back element streaming
- UV classification (TINY/LINEAR/AREAL)
- AreaL gets full triangles + depth buffer
- Tiny/Linear get UV_AABB/OBB proxies
- Depth-aware early-out occlusion testing

Core principles:
1. 3D model geometry is the ONLY occlusion truth
2. 2D annotation NEVER occludes model
3. Heavy work reserved for AreaL elements
4. Safe early-out only against depth-aware tile buffers
"""

# ────────────────────────────────────────────────────────────────────────────
# PR8 — MODEL / PROXY / OCCLUSION SEMANTICS (AUTHORITATIVE SUMMARY)
#
# Elements are processed FRONT → BACK by view-space depth (w).
#
# 1) CLASSIFICATION (semantic, NOT strategy-driven)
#    Each element is classified as one of:
#      - TINY    : very small (≤ ~2x2 cells)
#      - LINEAR  : thin / elongated (angle matters)
#      - AREAL   : meaningful area (floors, walls, slabs, etc.)
#
#    Classification determines *occlusion authority*.
#    Rasterization strategy (silhouette vs OBB vs AABB) does NOT.
#
# 2) OCCLUSION (depth masking / early-out)
#    - ONLY AREAL elements with HIGH confidence contribute to occlusion (w_occ).
#    - If silhouette/geometry fails and we fall back to OBB / AABB, confidence is LOW:
#         • we still write PROXY INK (visibility + metrics)
#         • we do NOT write occlusion (avoid false skipping + bbox-box artifacts)
#    - TINY and LINEAR elements NEVER write occlusion.
#
#    Rationale:
#      • Occlusion is a high-impact decision (skips later elements).
#      • We reserve it for elements we are confident dominate space.
#      • This is a deliberate performance + correctness tradeoff.
#
# 3) CELL OCCUPANCY ("INK ON SCREEN")
#    What appears in PNGs and contributes to cell metrics.
#
#    There are TWO kinds of occupancy ink:
#
#    a) MODEL INK (precise)
#       - Written ONLY by AREAL elements when real geometry succeeds.
#       - Comes from silhouette edges (with holes).
#       - Stored in model_edge_key.
#       - High spatial certainty.
#
#    b) PROXY INK (imprecise but real)
#       - Written by TINY / LINEAR elements.
#       - Also written by AREAL elements *when they fall back*
#         to OBB / AABB instead of true silhouettes.
#       - Stored in model_proxy_key.
#       - Means: "this element occupies *somewhere* in these cells".
#
#    Proxy ink IS included in:
#       ✔ PNG output
#       ✔ Cell occupancy counts / metrics
#
#    The difference from model ink is *certainty*, not visibility.
#
# 4) FALLBACK RULE (critical)
#    If an element is classified AREAL but must fall back to
#    OBB or AABB:
#      - It STILL occludes (writes w_occ).
#      - It writes PROXY INK, not MODEL INK.
#
#    Strategy failure must NOT downgrade occlusion authority.
#
# 5) EARLY-OUT / SKIP LOGIC
#    - Early-out tests consult ONLY existing occlusion (from AREAL).
#    - Proxy ink alone never causes skipping.
#
# In short:
#   • Classification controls occlusion.
#   • Strategy controls ink precision.
#   • Proxy ink counts, but only areal occludes.
#
# If behavior here looks "wrong", check:
#   (1) element classification
#   (2) fallback path taken
#   (3) which channel was written (model_edge vs model_proxy vs w_occ)
# ────────────────────────────────────────────────────────────────────────────


from datetime import datetime
import math
import time

# Boundary tolerance for crop volume intersection tests.
# Prevents exclusion of elements coplanar with crop boundaries.
# Value smaller than typical cell size to maintain accuracy.
BOUNDARY_TOLERANCE = 1e-6  # feet

from .config import Config
from .core.raster import ViewRaster, TileMap
from .core.geometry import Mode, classify_by_uv, make_uv_aabb, make_obb_or_skinny_aabb
from .core.math_utils import Bounds2D, CellRect
from .core.silhouette import get_element_silhouette, reset_family_region_caches
from .core.areal_extraction import extract_areal_geometry
from .revit.view_basis import make_view_basis, resolve_view_bounds
from .revit.collection import (
    collect_view_elements,
    expand_host_link_import_model_elements,
    sort_front_to_back,
    is_element_visible_in_view,
    estimate_nearest_depth_from_bbox,
)
from .revit.annotation import rasterize_annotations
from .revit.safe_api import safe_call
from .diagnostics import OcclusionTracker
from .memory_telemetry import MemoryTracker
from .perf_constants import TIMING_KEYS
from .perf_export import export_perf_csv


def _diagnose_link_geometry_transform(elem, link_trf, basis, stage_name):
    """Trace where transforms are applied to link geometry.

    Call this at different stages of geometry processing to see
    if transforms are being applied correctly and consistently.
    """
    try:
        elem_id = getattr(elem, 'Id', None)
        if elem_id:
            elem_id = elem_id.IntegerValue
        else:
            elem_id = "?"
    except Exception as e:
        elem_id = "?"

    print("\n" + "="*80)
    print("LINK GEOM TRANSFORM - Stage: {} - Element: {}".format(stage_name, elem_id))
    print("="*80)

    # Get first geometry vertex/point to trace coordinate space
    try:
        from Autodesk.Revit.DB import Options
        opts = Options()
        opts.ComputeReferences = False
        opts.DetailLevel = 0

        geom = elem.get_Geometry(opts)
        if not geom:
            print("No geometry available")
            return

        sample_pt = None
        for item in geom:
            # Try vertices
            if hasattr(item, 'Vertices') and item.Vertices.Size > 0:
                sample_pt = item.Vertices[0]
                break
            # Try curve endpoints
            if hasattr(item, 'GetEndPoint'):
                sample_pt = item.GetEndPoint(0)
                break
            # Try tessellated geometry
            if hasattr(item, 'GetTriangles'):
                tri = item.GetTriangles()
                if tri and tri.Count > 0:
                    sample_pt = tri[0].get_Vertex(0)
                    break

        if sample_pt is None:
            print("No sample point found in geometry")
            return

        print("Sample point (raw from get_Geometry): ({:.3f}, {:.3f}, {:.3f})".format(
            sample_pt.X, sample_pt.Y, sample_pt.Z))

        if link_trf:
            transformed = link_trf.OfPoint(sample_pt)
            print("After link transform: ({:.3f}, {:.3f}, {:.3f})".format(
                transformed.X, transformed.Y, transformed.Z))

            if basis:
                uv = basis.transform_to_view_uv((transformed.X, transformed.Y, transformed.Z))
                print("After view basis transform (UV): ({:.3f}, {:.3f})".format(uv[0], uv[1]))

        # Also check bbox to see if it matches
        bbox_link = elem.get_BoundingBox(None)
        if bbox_link:
            print("BBox Min (link space): ({:.3f}, {:.3f}, {:.3f})".format(
                bbox_link.Min.X, bbox_link.Min.Y, bbox_link.Min.Z))

            if link_trf:
                bbox_host_min = link_trf.OfPoint(bbox_link.Min)
                print("BBox Min (after transform): ({:.3f}, {:.3f}, {:.3f})".format(
                    bbox_host_min.X, bbox_host_min.Y, bbox_host_min.Z))

    except Exception as e:
        print("ERROR in diagnostic: {}".format(e))
        import traceback
        traceback.print_exc()

    print("="*80 + "\n")


def _perf_now():
    # perf_counter is monotonic and high-resolution where available.
    return time.perf_counter()


def _perf_ms(t0, t1):
    return (float(t1) - float(t0)) * 1000.0

def _safe_int(v):
    try:
        return int(v)
    except Exception as e:
        return None


def _safe_bool(v):
    try:
        return bool(v)
    except Exception as e:
        return None


def _cropbox_fingerprint(view_obj):
    """
    Returns a small, stable fingerprint of crop settings and extents.
    We avoid returning heavy objects; only primitives.
    """
    fp = {
        "crop_active": _safe_bool(getattr(view_obj, "CropBoxActive", None)),
        "crop_visible": _safe_bool(getattr(view_obj, "CropBoxVisible", None)),
    }
    try:
        cb = getattr(view_obj, "CropBox", None)
        if cb is not None:
            mn = getattr(cb, "Min", None)
            mx = getattr(cb, "Max", None)
            fp["crop_min"] = (
                round(float(getattr(mn, "X", 0.0)), 6),
                round(float(getattr(mn, "Y", 0.0)), 6),
                round(float(getattr(mn, "Z", 0.0)), 6),
            )
            fp["crop_max"] = (
                round(float(getattr(mx, "X", 0.0)), 6),
                round(float(getattr(mx, "Y", 0.0)), 6),
                round(float(getattr(mx, "Z", 0.0)), 6),
            )
    except Exception as e:
        # Exception in _cropbox_fingerprint - no diag in scope
        pass  # TODO: Add diagnostics when diag becomes available
    return fp

def _cfg_hash(cfg_obj, exclude_cache_wiring=False):
    try:
        import json
        import hashlib

        d = cfg_obj.to_dict() if cfg_obj is not None else {}
        if exclude_cache_wiring:
            d.pop("view_cache_enabled", None)
            d.pop("view_cache_dir", None)
            d.pop("view_cache_require_doc_unmodified", None)

        blob = json.dumps(d, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha1(blob).hexdigest()
    except Exception as e:
        return None

def _view_signature(doc_obj, view_obj, view_mode_val, cfg_obj=None, elem_cache=None, track_elements=None):
    """Enhanced signature with element fingerprints for position/size tracking.

    Must be module-level: imported by vop_interwoven.streaming.
    """
    import json
    import hashlib

    # Collect elements with fingerprints (position + size) or IDs (fallback)
    elem_fps = []
    elem_ids_for_tracking = []  # For view_elements tracking
    try:
        from Autodesk.Revit.DB import FilteredElementCollector

        col = FilteredElementCollector(doc_obj, view_obj.Id).WhereElementIsNotElementType()

        # Apply the same inclusion policy used by the main collectors so that
        # view_elements (and the exported vop_view_element_map_*.json) does not
        # include categories like Cameras, Section Boxes, etc.
        try:
            from .revit.collection_policy import should_include_element  # local import (Revit runtime)
        except Exception:
            should_include_element = None

        for elem in col:
            try:
                elem_id = getattr(getattr(elem, "Id", None), "IntegerValue", None)
                if elem_id is None:
                    continue

                # Policy gating (HOST source)
                if should_include_element is not None:
                    include, reason, category_name = should_include_element(
                        elem=elem,
                        doc=doc_obj,
                        source_type="HOST",
                        stats=None,
                    )
                    if not include:
                        continue

                # Track for view-element map export
                elem_ids_for_tracking.append((elem_id, "HOST"))

                if elem_cache is not None:
                    fp = elem_cache.get_or_create_fingerprint(
                        elem=elem,
                        elem_id=elem_id,
                        source_id="HOST",
                        view=None,  # Use model bbox for cross-view reuse
                        extract_params=None,
                    )
                    if fp is not None:
                        precision = int(getattr(cfg_obj, "signature_bbox_precision", 2)) if cfg_obj is not None else 2
                        elem_fps.append(fp.to_signature_string(precision=precision))
                    else:
                        elem_fps.append(str(elem_id))
                else:
                    elem_fps.append(str(elem_id))
            except Exception as e:
                continue

    except Exception as e:
        # Exception in _view_signature - no diag in scope
        pass  # TODO: Add diagnostics when diag becomes available
    # Store element-view relationship for CSV export
    if track_elements is not None:
        try:
            view_id_int = _safe_int(getattr(getattr(view_obj, "Id", None), "IntegerValue", None))
            if view_id_int is not None:
                track_elements[view_id_int] = elem_ids_for_tracking
        except Exception as e:
            # Exception in _view_signature - no diag in scope
            pass  # TODO: Add diagnostics when diag becomes available
    # Sort for deterministic signature
    elem_fps_str = "|".join(sorted(elem_fps))

    def _safe_prop_str(getter):
        try:
            v = getter()
            return None if v is None else str(v)
        except Exception as e:
            return "__ERR__:{0}".format(type(e).__name__)

    def _safe_prop_int(getter):
        try:
            v = getter()
            return None if v is None else int(v)
        except Exception as e:
            return None

    sig = {
        # Bump when signature-affecting pipeline behavior changes to force safe cache invalidation.
        "schema": 5,
        "view_id": _safe_int(getattr(getattr(view_obj, "Id", None), "IntegerValue", None)),
        "view_uid": getattr(view_obj, "UniqueId", None),
        "view_name": getattr(view_obj, "Name", None),
        "view_mode": view_mode_val,
        "view_type": _safe_prop_str(lambda: getattr(view_obj, "ViewType", None)),
        "view_template_id": _safe_int(getattr(getattr(view_obj, "ViewTemplateId", None), "IntegerValue", None)),
        "scale": _safe_prop_int(lambda: getattr(view_obj, "Scale", None)),
        "detail_level": _safe_prop_str(lambda: getattr(view_obj, "DetailLevel", None)),
        "discipline": _safe_prop_str(lambda: view_obj.Discipline),
        "display_style": _safe_prop_str(lambda: view_obj.DisplayStyle),
        "crop": _cropbox_fingerprint(view_obj),
        "elem_fps": elem_fps_str,
        "cfg_sha1": _cfg_hash(cfg_obj, exclude_cache_wiring=True),
    }

    blob = json.dumps(sig, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(blob).hexdigest(), sig

def _extract_view_identity_for_csv(doc, view):
    """
    Best-effort extraction of view identity fields needed for CSV slicing (DAX).
    Readable names only (never ids). Missing/unknown -> "".
    """
    out = {
        "view_type": "",
        "view_unique_id": "",
        "discipline": "",
        "phase": "",
        "sheet_number": "",
        "view_template_name": "",
        "view_unique_id": "",
    }

    if view is None:
        return out

    # view unique id
    try:
        out["view_unique_id"] = getattr(view, "UniqueId", "") or ""
    except Exception:
        pass

    # view_type (readable)
    try:
        vt = getattr(view, "ViewType", None)
        out["view_type"] = "" if vt is None else str(vt)
    except Exception as e:
        # Exception in _extract_view_identity_for_csv - no diag in scope
        pass  # TODO: Add diagnostics when diag becomes available

    # unique id (stable identity)
    try:
        out["view_unique_id"] = getattr(view, "UniqueId", "") or ""
    except Exception as e:
        pass
    # discipline (readable)
    try:
        # Prefer parameter value string if available (more "UI-like" than enum)
        from Autodesk.Revit.DB import BuiltInParameter  # type: ignore
        p = view.get_Parameter(BuiltInParameter.VIEW_DISCIPLINE)
        if p is not None:
            s = None
            try:
                s = p.AsValueString()
            except Exception as e:
                s = None
            if not s:
                try:
                    s = p.AsString()
                except Exception as e:
                    s = None
            if s:
                out["discipline"] = str(s)

        # Fallback: enum-ish string
        if not out["discipline"]:
            d = getattr(view, "Discipline", None)
            out["discipline"] = "" if d is None else str(d)
    except Exception as e:
        try:
            d = getattr(view, "Discipline", None)
            out["discipline"] = "" if d is None else str(d)
        except Exception as e2:
            # Safe fallback: discipline extraction completely failed
            pass

    # phase (readable NAME only)
    try:
        from Autodesk.Revit.DB import BuiltInParameter  # type: ignore
        p = view.get_Parameter(BuiltInParameter.VIEW_PHASE)
        if p is not None:
            eid = None
            try:
                eid = p.AsElementId()
            except Exception as e:
                eid = None

            if eid is not None and doc is not None:
                try:
                    ph = doc.GetElement(eid)
                    name = getattr(ph, "Name", None)
                    if name:
                        out["phase"] = str(name)
                except Exception as e:
                    # Exception in _extract_view_identity_for_csv - no diag in scope
                    pass  # TODO: Add diagnostics when diag becomes available
    except Exception as e:
        # Exception in _extract_view_identity_for_csv - no diag in scope
        pass  # TODO: Add diagnostics when diag becomes available
    # sheet_number (readable)
    try:
        # Some view types expose SheetNumber directly when placed; else keep blank
        sn = getattr(view, "SheetNumber", None)
        out["sheet_number"] = "" if sn is None else str(sn)
    except Exception as e:
        # Exception in _extract_view_identity_for_csv - no diag in scope
        pass  # TODO: Add diagnostics when diag becomes available
    # view_template_name (readable)
    try:
        vtid = getattr(view, "ViewTemplateId", None)
        if vtid is not None and doc is not None:
            try:
                vt_elem = doc.GetElement(vtid)
                name = getattr(vt_elem, "Name", None)
                out["view_template_name"] = "" if name is None else str(name)
            except Exception as e:
                out["view_template_name"] = ""
    except Exception as e:
        # Exception in _extract_view_identity_for_csv - no diag in scope
        pass  # TODO: Add diagnostics when diag becomes available
    return out

def _compute_manifest_metrics_payload(raster, cfg):
    """Compute manifest-scanned metrics totals and validation payload."""
    from .metrics.final_state_scanner import scan_final_state_totals
    from .metrics.manifest_evaluator import evaluate_metrics_manifest
    from .metrics_manifest import load_manifest_json

    manifest_obj = load_manifest_json(getattr(cfg, "metrics_manifest_path", None))

    totals = scan_final_state_totals(
        raster,
        manifest_obj.data,
        model_presence_mode="any",
    )

    available_primitives = ["M", "A", "E", "AnnoType", "ExtType", "ModelClasses"]
    available_capabilities = [
        "source_partition_8",
        "count_by_anno_type_final",
        "count_by_ext_type_final_flags",
        "count_by_ext_type_final_intersection",
        "count_model_class_multihot",
    ]

    validation = evaluate_metrics_manifest(
        totals,
        manifest_obj.data,
        manifest_sha256=manifest_obj.sha256,
        manifest_file_name=manifest_obj.file_name,
        available_primitives=available_primitives,
        available_capabilities=available_capabilities,
        mode=getattr(cfg, "metrics_validation_mode", "warn"),
    )

    return {
        "metrics": totals,
        "metrics_validation": validation,
        "metrics_version": manifest_obj.data.get("metrics_version"),
        "metrics_manifest_file": manifest_obj.file_name,
        "metrics_manifest_sha256": manifest_obj.sha256,
        "metrics_validation_mode": getattr(cfg, "metrics_validation_mode", "warn"),
    }


def process_document_views(doc, view_ids, cfg, diag=None, root_cache=None, reset_family_caches=True):
    """Process multiple views through the VOP interwoven pipeline.

    Args:
        doc: Revit Document
        view_ids: List of Revit View ElementIds (or ints) to process
        cfg: Config object
        root_cache: Optional RootStyleCache instance for metrics caching

    Returns:
        List of results (one per view), each containing:
        {
            'view_id': int,
            'view_name': str,
            'raster': ViewRaster (or dict from raster.to_dict()),
            'diagnostics': dict with processing stats
        }

    Example:
        >>> cfg = Config()
        >>> results = process_document_views(doc, [view_id1, view_id2], cfg)
        >>> len(results)
        2
    """
    # Revit hosts can keep the Python runtime alive across document sessions.
    # Reset module-level family caches so geometry from prior documents does not
    # accumulate in memory over repeated exporter runs.
    #
    # NOTE: Streaming mode processes one view per invocation and intentionally
    # disables this per-call reset so caches can be reused across views.
    if reset_family_caches:
        try:
            reset_family_region_caches()
        except Exception:
            pass

    from .core.diagnostics import Diagnostics

    results = []

    # Run/date identity for dated exports and metadata
    # Keep this aligned with CSV/PERF naming date semantics.
    date_override = getattr(cfg, "date_override", None)
    run_dt = datetime.now()
    if date_override:
        try:
            if isinstance(date_override, datetime):
                run_dt = date_override
            elif isinstance(date_override, str):
                ds = date_override.strip()
                if len(ds) == 10:
                    run_dt = datetime.strptime(ds, "%Y-%m-%d")
                elif len(ds) == 8 and ds.isdigit():
                    run_dt = datetime.strptime(ds, "%Y%m%d")
                else:
                    run_dt = datetime.fromisoformat(ds)
            else:
                run_dt = datetime.fromisoformat(str(date_override))
        except Exception:
            pass

    date_str = run_dt.strftime("%Y-%m-%d")
    run_id = run_dt.strftime("%Y%m%dT%H%M%S")
    run_t0 = _perf_now()
    memory_tracker = None
    try:
        memory_tracker = MemoryTracker()
    except Exception as e:
        print("[WARN] pipeline MemoryTracker init failed: {}".format(e))
    if memory_tracker is not None:
        try:
            memory_tracker.mark("run_start")
        except Exception as e:
            print("[WARN] pipeline run_start memory mark failed: {}".format(e))

    # ────────────────────────────────────────────────────────────────────
    # Persistent view-level cache (disk-backed)
    # Skip entire views when their signature matches a stored result.
    import os
    import json
    import hashlib
    import time
    import tempfile

    # Output directory (used for element cache persistence/export). Must be defined here.
    output_dir = getattr(cfg, "output_dir", None)

    view_cache_enabled = bool(getattr(cfg, "view_cache_enabled", False))
    view_cache_dir = getattr(cfg, "view_cache_dir", None)
    require_doc_clean = bool(getattr(cfg, "view_cache_require_doc_unmodified", True))

    # If view caching is enabled but no explicit directory is provided,
    # default to the run output directory (so cache co-locates with CSV/PNG exports).
    if view_cache_enabled and not view_cache_dir:
        try:
            view_cache_dir = getattr(cfg, "output_dir", None)
        except Exception as e:
            if diag is not None:
                diag.error(
                    phase="pipeline",
                    callsite="process_document_views",
                    message="Exception in process_document_views: {}".format(e),
                    exc=e,
                )
            view_cache_dir = None

    # Streaming-only policy: root_cache (single JSON) is the authoritative cache.
    # Disable per-view disk cache to avoid duplicate cache systems and confusion.
    view_cache_enabled = False

    if view_cache_enabled:
        try:
            os.makedirs(view_cache_dir, exist_ok=True)
        except Exception as e:
            if diag is not None:
                diag.error(
                    phase="pipeline",
                    callsite="process_document_views",
                    message="Exception in process_document_views: {}".format(e),
                    exc=e,
                )
            # If cache dir can't be created, disable caching (must never break pipeline)
            view_cache_enabled = False

    def _cache_path_for_view(view_id_int):
        return os.path.join(view_cache_dir, f"view_{int(view_id_int)}.json")

    def _load_cached_view(view_id_int, signature_hex):
        try:
            p = _cache_path_for_view(view_id_int)
            if not os.path.exists(p):
                return None
            with open(p, "r") as f:
                payload = json.load(f)
            if payload.get("signature") != signature_hex:
                return None
            return payload.get("result")
        except Exception as e:
            if diag is not None:
                diag.error(
                    phase="pipeline",
                    callsite="_load_cached_view",
                    message="Exception in _load_cached_view: {}".format(e),
                    exc=e,
                )
            return None

    def _save_cached_view(view_id_int, signature_hex, result_obj):
        try:
            p = _cache_path_for_view(view_id_int)
            payload = {
                "signature": signature_hex,
                "saved_utc": time.time(),
                "result": result_obj,
            }
            # Atomic write
            tmp_fd, tmp_path = tempfile.mkstemp(prefix="vop_viewcache_", suffix=".json", dir=view_cache_dir)
            try:
                with os.fdopen(tmp_fd, "w") as f:
                    json.dump(payload, f)
                os.replace(tmp_path, p)
            finally:
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception as e:
                    if diag is not None:
                        diag.error(
                            phase="pipeline",
                            callsite="_save_cached_view",
                            message="Exception in _save_cached_view: {}".format(e),
                            exc=e,
                        )
        except Exception as e:
            if diag is not None:
                diag.error(
                    phase="pipeline",
                    callsite="_save_cached_view",
                    message="Exception in _save_cached_view: {}".format(e),
                    exc=e,
                )
    # Guardrail: cfg must be vop_interwoven.config.Config (attribute-based), not a dict.
    # This prevents silent drift when new code accidentally uses cfg.get(...).
    if isinstance(cfg, dict):
        raise TypeError("cfg must be vop_interwoven.config.Config (not dict)")

    # PR12: bounded geometry cache shared across all views in this call.
    # Scoped to this run to avoid cross-run semantic drift.
    try:
        from .core.cache import LRUCache
        geometry_cache = LRUCache(max_items=getattr(cfg, "geometry_cache_max_items", 0))
    except Exception as e:
        if diag is not None:
            diag.error(
                phase="pipeline",
                callsite="_save_cached_view",
                message="Exception in _save_cached_view: {}".format(e),
                exc=e,
            )
        geometry_cache = None

    # PR13: Document-scoped element cache for bbox reuse across views
    elem_cache = None
    elem_cache_prev = None  # Previous run cache (for change detection)
    elem_cache_path = None
    if getattr(cfg, "use_element_cache", True):
        try:
            from .core.element_cache import ElementCache
            max_items = int(getattr(cfg, "element_cache_max_items", 10000))

            # Determine cache file path (dated for tracking changes over time)
            if output_dir is not None:
                cache_date = date_str
                elem_cache_path = os.path.join(output_dir, f"vop_element_cache_{cache_date}.json")
            else:
                elem_cache_path = None

            # Load previous cache if persistence enabled
            if getattr(cfg, "element_cache_persist", True) and elem_cache_path is not None:
                try:
                    elem_cache_prev = ElementCache.load_from_json(elem_cache_path, max_elements=max_items)
                    # Start with previous cache (pre-populated) and keep prev ref for change detection.
                    elem_cache = elem_cache_prev
                    if diag is not None:
                        try:
                            prev_size = len(elem_cache.cache)
                            diag.info(
                                phase="pipeline",
                                callsite="process_document_views.element_cache_load",
                                message=f"Loaded element cache from previous run ({prev_size} elements)",
                                extra={"cache_path": elem_cache_path, "prev_size": prev_size}
                            )
                        except Exception as e:
                            if diag is not None:
                                diag.error(
                                    phase="pipeline",
                                    callsite="_save_cached_view",
                                    message="Exception in _save_cached_view: {}".format(e),
                                    exc=e,
                                )
                except Exception as e:
                    if diag is not None:
                        diag.error(
                            phase="pipeline",
                            callsite="_save_cached_view",
                            message="Exception in _save_cached_view: {}".format(e),
                            exc=e,
                        )
                    # Failed to load - start fresh
                    elem_cache = ElementCache(max_elements=max_items)
            else:
                # No persistence - start fresh
                elem_cache = ElementCache(max_elements=max_items)

        except Exception as e:
            if diag is not None:
                diag.error(
                    phase="pipeline",
                    callsite="_save_cached_view",
                    message="Exception in _save_cached_view: {}".format(e),
                    exc=e,
                )
            elem_cache = None  # Graceful degradation

    # Track element-view relationships for export (view_id -> list of (elem_id, source_id))
    view_elements = {}
    try:
        for requested_view_id in view_ids:
            view_id_seed = _safe_int(getattr(getattr(requested_view_id, "Id", None), "IntegerValue", requested_view_id))
            if view_id_seed is not None:
                view_elements.setdefault(view_id_seed, [])
    except Exception as e:
        if diag is not None:
            diag.error(
                phase="pipeline",
                callsite="_tmark",
                message="Exception in _tmark: {}".format(e),
                exc=e,
            )

    if memory_tracker is not None:
        try:
            memory_tracker.mark("run_start")
        except Exception as e:
            print("[WARN] pipeline run_start memory mark failed: {}".format(e))

    for view_id in view_ids:
        diag = Diagnostics()  # per-view diag
        timings = {}
        t_view0 = _perf_now()
        gc_elapsed_ms = 0.0

        def _tmark(name, t0, t1):
            if getattr(cfg, "perf_collect_timings", True):
                timings[name] = round(_perf_ms(t0, t1), 3)

        view = None
        elem_hits_before = int(getattr(elem_cache, "hits", 0) or 0) if elem_cache is not None else 0
        elem_misses_before = int(getattr(elem_cache, "misses", 0) or 0) if elem_cache is not None else 0

        try:
            # Convert int to ElementId if needed
            from Autodesk.Revit.DB import ElementId
            elem_id = ElementId(view_id) if isinstance(view_id, int) else view_id

            view = doc.GetElement(elem_id)
            if getattr(cfg, "perf_collect_timings", True) and memory_tracker is not None:
                try:
                    memory_tracker.mark("view_start_{}".format(getattr(getattr(view, "Id", None), "IntegerValue", view_id)))
                except Exception as e:
                    print("[WARN] pipeline view_start mark failed: {}".format(e))

            # 0) Capability gating / mode selection (PR6)
            from .revit.view_basis import resolve_view_mode, VIEW_MODE_MODEL_AND_ANNOTATION, VIEW_MODE_ANNOTATION_ONLY, VIEW_MODE_REJECTED

            t0 = _perf_now()
            view_mode, mode_reason = resolve_view_mode(view, diag=diag)
            t1 = _perf_now()
            _tmark("mode_ms", t0, t1)

            if diag is not None:
                # Diagnostics implementations differ (some do not implement .info()).
                # This must never crash the view loop.
                payload = {
                    "mode": view_mode,
                    "reason": mode_reason,
                    "view_name": getattr(view, "Name", None),
                }
                try:
                    if hasattr(diag, "debug"):
                        diag.debug(
                            phase="pipeline",
                            callsite="process_document_views.mode",
                            message="Resolved view processing mode",
                            view_id=getattr(getattr(view, "Id", None), "IntegerValue", None),
                            extra=payload,
                        )
                    elif hasattr(diag, "warn"):
                        diag.warn(
                            phase="pipeline",
                            callsite="process_document_views.mode",
                            message="Resolved view processing mode",
                            view_id=getattr(getattr(view, "Id", None), "IntegerValue", None),
                            extra=payload,
                        )
                except Exception as e:
                    if diag is not None:
                        diag.error(
                            phase="pipeline",
                            callsite="_tmark",
                            message="Exception in _tmark: {}".format(e),
                            exc=e,
                        )
                    # Never allow diagnostics logging to fail the pipeline

            if view_mode == VIEW_MODE_REJECTED:
                
                t_view1 = _perf_now()
                _tmark(TIMING_KEYS["TOTAL_MS"], t_view0, t_view1)

                # Do not silently drop rejected views — always emit a per-view result
                if diag is not None:
                    try:
                        diag.warn(
                            phase="pipeline",
                            callsite="process_document_views",
                            message="View rejected by capability gating",
                            view_id=getattr(getattr(view, "Id", None), "IntegerValue", None),
                            extra={
                                "view_name": getattr(view, "Name", None),
                                "mode_reason": mode_reason,
                            },
                        )
                    except Exception as e:
                        if diag is not None:
                            diag.error(
                                phase="pipeline",
                                callsite="_tmark",
                                message="Exception in _tmark: {}".format(e),
                                exc=e,
                            )
                        # Diagnostics must never break the pipeline

                results.append(
                    {
                        "view_id": getattr(getattr(view, "Id", None), "IntegerValue", None),
                        "view_name": getattr(view, "Name", None),
                        "success": False,
                        "view_mode": view_mode,
                        "view_mode_reason": mode_reason,
                        "diag": diag.to_dict() if diag is not None else None,
                        "timings": dict(timings),
                    }
                )
                continue

            # Persistent view-cache: skip whole view if unchanged (disk-backed)
            view_id_int = getattr(getattr(view, "Id", None), "IntegerValue", None)

            can_use_cache = view_cache_enabled
            if can_use_cache and require_doc_clean:
                try:
                    if bool(getattr(doc, "IsModified", False)):
                        can_use_cache = False
                except Exception as e:
                    if diag is not None:
                        diag.error(
                            phase="pipeline",
                            callsite="_tmark",
                            message="Exception in _tmark: {}".format(e),
                            exc=e,
                        )
            # Compute signature ONCE (and populate view_elements consistently)
            sig_hex, sig_obj = _view_signature(
                doc, view, view_mode,
                cfg_obj=cfg,
                elem_cache=elem_cache,
                track_elements=view_elements
            )

            # Compute identity fields once for CSV slicing and cache row_payload completeness
            ident = _extract_view_identity_for_csv(doc, view)

            # Check root cache first (metrics-only hit; valid in streaming too)
            if root_cache:
                t_cache0 = _perf_now()
                cached = root_cache.get_view(view_id_int, sig_hex)
                t_cache1 = _perf_now()
                _tmark(TIMING_KEYS["CACHE_READ_MS"], t_cache0, t_cache1)

                if cached:
                    cached_meta = cached.get("metadata") or {}
                    cached_metrics = cached.get("metrics") or {}

                    # Backfill legacy cache entries missing UID; persist repaired metadata.
                    try:
                        cached_uid = cached_meta.get("view_unique_id", "")
                        ident_uid = ident.get("view_unique_id", "")
                        if (not cached_uid) and ident_uid:
                            patched_meta = dict(cached_meta)
                            patched_meta["view_unique_id"] = ident_uid
                            root_cache.set_view(
                                view_id=view_id_int,
                                signature=sig_hex,
                                metadata=patched_meta,
                                metrics=cached_metrics,
                                element_summary=cached.get("element_summary") or {},
                                timings=cached.get("timings") or {},
                            )
                            cached_meta = patched_meta
                    except Exception:
                        pass

                    # Minimal raster stub so CSV export can populate bounds_meta-driven fields on metrics-only hits.
                    cell_size_ft = cached_meta.get("CellSize_ft", cached_metrics.get("CellSize_ft", 0.0))
                    raster_stub = {
                        "cell_size_ft": cell_size_ft,
                        "bounds_meta": {
                            "cell_size_ft_requested": cached_meta.get("CellSizeRequested_ft", cell_size_ft),
                            "cell_size_ft_effective": cached_meta.get("CellSizeEffective_ft", cell_size_ft),
                            "resolution_mode": cached_meta.get("ResolutionMode", "canonical"),
                            "cap_triggered": bool(cached_meta.get("CapTriggered", False)),
                        },
                    }

                    # Cache-hit timing: how long it took to determine the hit (lookup + minimal assembly)
                    cache_ms = _perf_ms(t_cache0, t_cache1)

                    result = dict(cached_meta)
                    result.update({
                        # Ensure identity fields are always present for CSV slicing + doc lookups
                        "view_id": cached_meta.get("view_id", view_id_int),
                        "view_name": cached_meta.get("view_name", ""),
                        "view_unique_id": cached_meta.get("view_unique_id", "") or ident.get("view_unique_id", ""),
                        "view_type": cached_meta.get("view_type", "") or ident.get("view_type", ""),
                        "view_unique_id": cached_meta.get("view_unique_id", "") or ident.get("view_unique_id", ""),
                        "discipline": cached_meta.get("discipline", "") or ident.get("discipline", ""),
                        "phase": cached_meta.get("phase", "") or ident.get("phase", ""),

                        "success": True,
                        "from_cache": True,
                        "metrics": cached_metrics,
                        "raster": raster_stub,

                        # Preserve cached outputs for reuse by CSV exporters
                        "row_payload": cached.get("row_payload") or {},
                        "timings": cached.get("timings") or {},

                        # If you still want lookup cost, store it separately (do NOT overwrite timings)
                        "cache_lookup_ms": cache_ms,

                        "cache": {
                            "cache_type": "root",
                            "signature": sig_hex,
                        },
                    })

                    results.append(result)
                    continue

            # 1) Init raster bounds/resolution
            t0 = _perf_now()
            raster = init_view_raster(doc, view, cfg, diag=diag)
            t1 = _perf_now()
            _tmark("raster_init_ms", t0, t1)       
            
            # Persist view mode for downstream exports/diagnostics
            try:
                raster.view_mode = view_mode
                raster.view_mode_reason = mode_reason
            except Exception as e:
                if diag is not None:
                    diag.error(
                        phase="pipeline",
                        callsite="_tmark",
                        message="Exception in _tmark: {}".format(e),
                        exc=e,
                    )
            # Create strategy diagnostics tracker if enabled (used by render and CSV export)
            strategy_diag = None
            if getattr(cfg, "export_strategy_diagnostics", False):
                try:
                    from .diagnostics import StrategyDiagnostics
                    strategy_diag = StrategyDiagnostics()
                except Exception as e:
                    if diag is not None:
                        diag.error(
                            phase="pipeline",
                            callsite="_tmark",
                            message="Exception in _tmark: {}".format(e),
                            exc=e,
                        )
                    # Graceful degradation: continue without diagnostics

            render_result = {}

            if view_mode == VIEW_MODE_MODEL_AND_ANNOTATION:
                # 2) Broad-phase visible elements
                t0 = _perf_now()
                elements = collect_view_elements(doc, view, raster, diag=diag, cfg=cfg)
                t1 = _perf_now()
                _tmark(TIMING_KEYS["COLLECT_MS"], t0, t1)
                
                # 3) MODEL PASS
                t0 = _perf_now()
                render_result = render_model_front_to_back(doc, view, raster, elements, cfg, diag=diag, geometry_cache=geometry_cache, elem_cache=elem_cache, strategy_diag=strategy_diag)
                t1 = _perf_now()
                _tmark(TIMING_KEYS["RASTER_MODEL_MS"], t0, t1)

                # Merge rasterization sub-timings into view timings
                _raster_sub_timings = render_result.get("timings", {}) if isinstance(render_result, dict) else {}
                if _raster_sub_timings and isinstance(_raster_sub_timings, dict):
                    if getattr(cfg, "perf_collect_timings", True):
                        for _sk, _sv in _raster_sub_timings.items():
                            timings[_sk] = round(float(_sv), 3)
                        timings[TIMING_KEYS["GEOM_EXTRACT_MS"]] = round(float(_raster_sub_timings.get(TIMING_KEYS["GEOM_EXTRACT_MS"], _raster_sub_timings.get("raster_geom_extract_ms", 0.0))), 3)
                        if getattr(cfg, "perf_subtimings_geometry", True):
                            for _gk in (TIMING_KEYS["GEOM_SILHOUETTE_MS"], TIMING_KEYS["GEOM_OBB_MS"], TIMING_KEYS["GEOM_BBOX_MS"]):
                                if _gk in _raster_sub_timings:
                                    timings[_gk] = round(float(_raster_sub_timings.get(_gk, 0.0)), 3)
                if getattr(cfg, "perf_collect_timings", True) and memory_tracker is not None:
                    try:
                        memory_tracker.mark("after_geom_extract_{}".format(view_id_int))
                    except Exception as e:
                        print("[WARN] pipeline after_geom_extract mark failed: {}".format(e))
                if getattr(cfg, "perf_collect_timings", True) and memory_tracker is not None:
                    try:
                        memory_tracker.mark("after_raster_{}".format(view_id_int))
                    except Exception as e:
                        print("[WARN] pipeline after_raster mark failed: {}".format(e))

            elif view_mode == VIEW_MODE_ANNOTATION_ONLY:
                # Annotation-only: do NOT attempt model collection, depth sorting, or link expansion
                if diag is not None:
                    diag.warn(
                        phase="pipeline",
                        callsite="process_document_views",
                        message="Annotation-only mode: skipping model pipeline phases",
                        view_id=getattr(getattr(view, "Id", None), "IntegerValue", None),
                        extra={"view_name": getattr(view, "Name", None), "mode_reason": mode_reason},
                    )
        
            # 4) ANNO PASS (always allowed)
            t0 = _perf_now()
            rasterize_annotations(doc, view, raster, cfg, diag=diag)
            t1 = _perf_now()
            _tmark(TIMING_KEYS["RASTER_ANNO_MS"], t0, t1)

            # 5) Derive annoOverModel (safe even if model is empty)
            t0 = _perf_now()
            raster.finalize_anno_over_model(cfg)
            t1 = _perf_now()
            _tmark("finalize_ms", t0, t1)

            metrics_payload = _compute_manifest_metrics_payload(raster, cfg)

            # 6) Export
            t0 = _perf_now()
            out = export_view_raster(view, raster, cfg, diag=diag, timings=timings, strategy_diag=strategy_diag, metrics_payload=metrics_payload)
            if isinstance(render_result, dict) and "diagnostics" in render_result:
                out["diagnostics"] = render_result["diagnostics"]
                tracker_obj = render_result.get("occlusion_tracker")
                if tracker_obj is not None:
                    try:
                        out["occlusion_tracker"] = tracker_obj.as_dict()
                    except Exception:
                        out["occlusion_tracker"] = None

            # Ensure identity fields exist on first-run results so CSV + cache row_payload are complete
            try:
                if isinstance(out, dict):
                    if out.get("view_unique_id") in (None, ""):
                        out["view_unique_id"] = ident.get("view_unique_id", "")
                    if out.get("view_type") in (None, ""):
                        out["view_type"] = ident.get("view_type", "")
                    if out.get("view_unique_id") in (None, ""):
                        out["view_unique_id"] = ident.get("view_unique_id", "")
                    if out.get("discipline") in (None, ""):
                        out["discipline"] = ident.get("discipline", "")
                    if out.get("phase") in (None, ""):
                        out["phase"] = ident.get("phase", "")
                    if out.get("sheet_number") in (None, ""):
                        out["sheet_number"] = ident.get("sheet_number", "")
                    if out.get("view_template_name") in (None, ""):
                        out["view_template_name"] = ident.get("view_template_name", "")
            except Exception as e:
                if diag is not None:
                    diag.error(
                        phase="pipeline",
                        callsite="_tmark",
                        message="Exception in _tmark: {}".format(e),
                        exc=e,
                    )
            t1 = _perf_now()
            _tmark(TIMING_KEYS["CSV_MS"], t0, t1)

            # Root cache write-through (metrics only; requires out+raster)
            if root_cache and out and out.get("success", True) and ("raster" in out):
                try:
                    from .root_cache import extract_metrics_from_view_result

                    metadata, metrics, elem_summary, timings2 = extract_metrics_from_view_result(out, cfg)

                    root_cache.set_view(
                        view_id=view_id_int,
                        signature=sig_hex,
                        metadata=metadata,
                        metrics=metrics,
                        element_summary=elem_summary,
                        timings=timings2,
                    )
                except Exception as e:
                    print(f"[Pipeline] Root cache save failed: {e}")
        
            # Write-through persistent cache on successful export
            try:
                if view_cache_enabled:
                    vid = getattr(getattr(view, "Id", None), "IntegerValue", None)
                    if vid is not None:
                        _t_cw0 = _perf_now()
                        _save_cached_view(vid, sig_hex, out)
                        _tmark(TIMING_KEYS["CACHE_WRITE_MS"], _t_cw0, _perf_now())
                        try:
                            out["cache"] = {"view_cache": "MISS_SAVED", "signature": sig_hex, "dir": view_cache_dir}
                            # Attach canonical signature for downstream consumers (streaming/CSV/debug).
                            # Never recompute signature outside this function.
                            try:
                                if out is not None:
                                    c = out.setdefault("cache", {})
                                    if isinstance(c, dict):
                                        c.setdefault("signature", sig_hex)
                            except Exception as e:
                                if diag is not None:
                                    diag.error(
                                        phase="pipeline",
                                        callsite="_tmark",
                                        message="Exception in _tmark: {}".format(e),
                                        exc=e,
                                    )
                        except Exception as e:
                            if diag is not None:
                                diag.error(
                                    phase="pipeline",
                                    callsite="_tmark",
                                    message="Exception in _tmark: {}".format(e),
                                    exc=e,
                                )
            except Exception as e:
                if diag is not None:
                    diag.error(
                        phase="pipeline",
                        callsite="_tmark",
                        message="Exception in _tmark: {}".format(e),
                        exc=e,
                    )
            t_view1 = _perf_now()
            _tmark(TIMING_KEYS["TOTAL_MS"], t_view0, t_view1)

            # Always expose a wall-clock elapsed seconds for this view, even if timing collection is disabled
            try:
                out["elapsed_sec"] = round((_perf_ms(t_view0, t_view1) / 1000.0), 3)
            except Exception as e:
                if diag is not None:
                    diag.error(
                        phase="pipeline",
                        callsite="_tmark",
                        message="Exception in _tmark: {}".format(e),
                        exc=e,
                    )
            # Normalized metrics
            try:
                if isinstance(out, dict):
                    tdict = dict(timings)
                    element_count = int(tdict.get("element_count", 0) or 0)
                    areal_count = int(tdict.get("areal_count", 0) or 0)
                    linear_count = int(tdict.get("linear_count", 0) or 0)
                    tiny_count = int(tdict.get("tiny_count", 0) or 0)
                    raster_cells = int((out.get("width") or 0) * (out.get("height") or 0))
                    geom_ms = float(tdict.get(TIMING_KEYS["GEOM_EXTRACT_MS"], 0.0) or 0.0)
                    raster_model_ms = float(tdict.get(TIMING_KEYS["RASTER_MODEL_MS"], 0.0) or 0.0)
                    fallback_hits = float(tdict.get("fallback_bbox_hits", 0) or 0)
                    tdict["element_count"] = element_count
                    tdict["areal_count"] = areal_count
                    tdict["linear_count"] = linear_count
                    tdict["tiny_count"] = tiny_count
                    tdict["ms_per_element"] = (geom_ms / element_count) if element_count else None
                    tdict["ms_per_areal"] = (geom_ms / areal_count) if areal_count else None
                    tdict["raster_cells"] = raster_cells
                    tdict["ms_per_1k_cells"] = (raster_model_ms / (raster_cells / 1000.0)) if raster_cells else None
                    tdict["fallback_rate_pct"] = (fallback_hits / element_count * 100.0) if element_count else 0.0
                    timings = tdict
            except Exception as e:
                print("[WARN] pipeline normalized timing metrics failed: {}".format(e))

            # Convenience mirror at top-level for callers that don't dive into diagnostics
            try:
                out["timings"] = dict(timings)
            except Exception as e:
                if diag is not None:
                    diag.error(
                        phase="pipeline",
                        callsite="_tmark",
                        message="Exception in _tmark: {}".format(e),
                        exc=e,
                    )
            # Per-view element-cache hit rate (delta over this view only)
            try:
                if isinstance(out, dict):
                    elem_hits_after = int(getattr(elem_cache, "hits", 0) or 0) if elem_cache is not None else elem_hits_before
                    elem_misses_after = int(getattr(elem_cache, "misses", 0) or 0) if elem_cache is not None else elem_misses_before
                    delta_hits = max(0, elem_hits_after - elem_hits_before)
                    delta_misses = max(0, elem_misses_after - elem_misses_before)
                    delta_total = delta_hits + delta_misses
                    out["elem_cache_hit_rate"] = (float(delta_hits) / float(delta_total)) if delta_total > 0 else 0.0
                    if getattr(cfg, "perf_collect_timings", True) and getattr(cfg, "perf_subtimings_cache", True):
                        timings[TIMING_KEYS["ELEM_CACHE_MS"]] = round(float(timings.get(TIMING_KEYS["ELEM_CACHE_MS"], 0.0) or 0.0), 3)
            except Exception as e:
                if diag is not None:
                    diag.error(
                        phase="pipeline",
                        callsite="process_document_views.elem_cache_hit_rate",
                        message="Failed to compute per-view element cache hit rate",
                        exc=e,
                    )
                if isinstance(out, dict):
                    out["elem_cache_hit_rate"] = 0.0

            # Force CLR GC between views and capture memory mark
            try:
                t_gc0 = _perf_now()
                if memory_tracker is not None:
                    memory_tracker.mark_and_gc("after_clr_gc_{}".format(view_id_int))
                t_gc1 = _perf_now()
                gc_elapsed_ms = _perf_ms(t_gc0, t_gc1)
                if getattr(cfg, "perf_collect_timings", True):
                    timings[TIMING_KEYS["GC_MS"]] = round(float(gc_elapsed_ms), 3)
            except Exception as e:
                print("[WARN] pipeline per-view CLR GC failed: {}".format(e))

            # Memory management: conditionally retain or discard raster data
            if getattr(cfg, 'retain_rasters_in_memory', True):
                # Keep full raster (needed for streaming exports or debug)
                results.append(out)
            else:
                # Discard raster, keep only lightweight summary
                summary = _extract_view_summary(out)
                results.append(summary)

        except Exception as e:
            # Never silent: record + continue
            if diag is not None:
                diag.error(
                    phase="pipeline",
                    callsite="process_document_views",
                    message="Failed to process view",
                    exc=e,
                    view_id=getattr(getattr(view, "Id", None), "IntegerValue", None),
                    extra={"view_name": getattr(view, "Name", None)},
                )

            # Keep legacy behavior (continue)
            results.append(
                {
                    "view_id": getattr(getattr(view, "Id", None), "IntegerValue", 0),
                    "view_name": getattr(view, "Name", "Unknown") if view is not None else "Unknown",
                    "success": False,
                    "diag": diag.to_dict(),
                }
            )
            continue

    # Log element cache statistics
    if elem_cache is not None and diag is not None:
        try:
            diag.info(
                phase="pipeline",
                callsite="process_document_views.element_cache_stats",
                message="Element cache statistics for this run",
                extra=elem_cache.stats()
            )
        except Exception as e:
            if diag is not None:
                diag.error(
                    phase="pipeline",
                    callsite="_tmark",
                    message="Exception in _tmark: {}".format(e),
                    exc=e,
                )
    # Phase 2.5: Persistent element cache - save/export/detect changes
    if elem_cache is not None and getattr(cfg, "element_cache_persist", True):
        try:
            # Save cache to JSON for next run
            if elem_cache_path is not None:
                try:
                    metadata = {
                        "timestamp": time.time(),
                        "date": cache_date,
                        "doc_path": getattr(doc, "PathName", None),
                        "doc_title": getattr(doc, "Title", None),
                    }
                    saved = elem_cache.save_to_json(elem_cache_path, metadata=metadata)
                    if saved and diag is not None:
                        diag.info(
                            phase="pipeline",
                            callsite="process_document_views.element_cache_save",
                            message="Saved element cache for next run",
                            extra={"cache_path": elem_cache_path, "size": len(elem_cache.cache)}
                        )
                except Exception as e:
                    if diag is not None:
                        diag.error(
                            phase="pipeline",
                            callsite="_tmark",
                            message="Exception in _tmark: {}".format(e),
                            exc=e,
                        )
            # Export view-element map JSON (view -> element ids)
            if getattr(cfg, "element_cache_export_csv", True) and output_dir is not None:
                try:
                    analysis_path = os.path.join(output_dir, f"vop_view_element_map_{date_str}.json")
                    exported = elem_cache.export_view_element_map_json(
                        analysis_path,
                        view_elements=view_elements,
                        merge_existing=True,
                    )
                    if exported and diag is not None:
                        diag.info(
                            phase="pipeline",
                            callsite="process_document_views.element_cache_export_view_element_map_json",
                            message="Exported view-element map JSON",
                            extra={"analysis_path": analysis_path, "elements": len(elem_cache.cache), "views": len(view_elements)}
                        )
                except Exception as e:
                    if diag is not None:
                        diag.error(
                            phase="pipeline",
                            callsite="_tmark",
                            message="Exception in _tmark: {}".format(e),
                            exc=e,
                        )
            # Detect changes from previous run
            if getattr(cfg, "element_cache_detect_changes", True) and elem_cache_prev is not None:
                try:
                    tolerance = float(getattr(cfg, "element_cache_change_tolerance", 0.01))
                    changes = elem_cache.detect_changes(elem_cache_prev, tolerance=tolerance)

                    if diag is not None:
                        diag.info(
                            phase="pipeline",
                            callsite="process_document_views.element_cache_changes",
                            message="Element changes detected since last run",
                            extra=changes
                        )

                    # Also export changes CSV if significant changes detected
                    if output_dir is not None and (changes["added"] or changes["moved"] or changes["resized"]):
                        try:
                            import csv as csv_module
                            changes_csv_path = os.path.join(output_dir, "element_changes.csv")
                            with open(changes_csv_path, "w", newline="") as f:
                                writer = csv_module.writer(f)
                                writer.writerow(["change_type", "elem_id", "source_id", "distance_or_size_change"])

                                for elem_id, source_id in changes["added"]:
                                    writer.writerow(["ADDED", elem_id, source_id, ""])

                                for elem_id, source_id in changes["removed"]:
                                    writer.writerow(["REMOVED", elem_id, source_id, ""])

                                for elem_id, source_id, distance in changes["moved"]:
                                    writer.writerow(["MOVED", elem_id, source_id, f"{distance:.3f}"])

                                for elem_id, source_id, size_change in changes["resized"]:
                                    writer.writerow(["RESIZED", elem_id, source_id, f"{size_change:.3f}"])

                            if diag is not None:
                                diag.info(
                                    phase="pipeline",
                                    callsite="process_document_views.element_changes_export",
                                    message="Exported element changes CSV",
                                    extra={"csv_path": changes_csv_path}
                                )
                        except Exception as e:
                            if diag is not None:
                                diag.error(
                                    phase="pipeline",
                                    callsite="_tmark",
                                    message="Exception in _tmark: {}".format(e),
                                    exc=e,
                                )
                except Exception as e:
                    if diag is not None:
                        diag.error(
                            phase="pipeline",
                            callsite="_tmark",
                            message="Exception in _tmark: {}".format(e),
                            exc=e,
                        )
            # Release prev cache — no longer needed after change detection.
            elem_cache_prev = None
        except Exception as e:
            if diag is not None:
                diag.error(
                    phase="pipeline",
                    callsite="_tmark",
                    message="Exception in _tmark: {}".format(e),
                    exc=e,
                )

    # Export per-view diagnostics JSON
    if getattr(cfg, "export_view_diagnostics", True):
        try:
            diagnostics_output_dir = getattr(cfg, "view_diagnostics_output_dir", None) or output_dir
            if diagnostics_output_dir:
                os.makedirs(diagnostics_output_dir, exist_ok=True)

            all_view_diags = {}
            for view_result in results:
                view_diag = view_result.get("diagnostics") if isinstance(view_result, dict) else None
                if view_diag and view_diag.get("view_id"):
                    all_view_diags[str(view_diag["view_id"])] = view_diag

            if not diagnostics_output_dir:
                raise ValueError("No diagnostics output directory configured (cfg.output_dir / view_diagnostics_output_dir)")

            diag_filename = f"views_diagnostics_{date_str}.json"
            diag_path = os.path.join(diagnostics_output_dir, diag_filename)

            payload = {
                "metadata": {
                    "date": date_str,
                    "run_id": run_id,
                    "doc_title": getattr(doc, "Title", "Unknown"),
                    "doc_path": getattr(doc, "PathName", None),
                    "exporter_version": "vop_interwoven",
                },
                "views": all_view_diags,
            }

            # Append behavior across multiple process_document_views() calls in the same run date:
            # if diagnostics already exists for this day, merge prior views so entries are not lost.
            if os.path.exists(diag_path):
                try:
                    with open(diag_path, "r") as f:
                        existing_payload = json.load(f)
                    existing_views = existing_payload.get("views", {}) if isinstance(existing_payload, dict) else {}
                    if isinstance(existing_views, dict):
                        existing_views.update(payload["views"])
                        payload["views"] = existing_views
                except Exception:
                    # Best-effort merge only; fall back to writing current payload.
                    pass

            with open(diag_path, "w") as f:
                json.dump(payload, f, indent=2)

            if diag is not None:
                diag.info(
                    phase="pipeline",
                    callsite="process_document_views.export_diagnostics",
                    message=f"Exported view diagnostics: {len(payload.get('views', {}))} views",
                    extra={"path": diag_path},
                )
        except Exception as e:
            if diag is not None:
                diag.error(
                    phase="pipeline",
                    callsite="process_document_views.export_diagnostics",
                    message=f"Failed to export view diagnostics: {e}",
                    exc=e,
                )
    if memory_tracker is not None:
        try:
            memory_tracker.mark("run_end")
            memory_tracker.mark_and_gc("after_run_gc")
        except Exception as e:
            print("[WARN] pipeline run-end memory marks failed: {}".format(e))

    # Run-level summary
    try:
        phase_keys = [TIMING_KEYS["COLLECT_MS"], TIMING_KEYS["GEOM_EXTRACT_MS"], TIMING_KEYS["RASTER_MODEL_MS"], TIMING_KEYS["RASTER_ANNO_MS"], TIMING_KEYS["PNG_MS"], TIMING_KEYS["CSV_MS"]]
        phase_totals = {k: 0.0 for k in phase_keys}
        for _r in results:
            _t = (_r.get("timings") or {}) if isinstance(_r, dict) else {}
            for _k in phase_keys:
                phase_totals[_k] += float(_t.get(_k, 0.0) or 0.0)
        phase_sum = sum(phase_totals.values()) or 1.0
        slow = sorted([r for r in results if isinstance(r, dict)], key=lambda x: float((x.get("timings") or {}).get(TIMING_KEYS["TOTAL_MS"], 0.0) or 0.0), reverse=True)[:5]
        mem_records = memory_tracker.to_dict() if memory_tracker is not None else []
        priv_vals = [float(m.get("priv_mb")) for m in mem_records if m.get("priv_mb") is not None]
        run_summary = {
            "view_count": len(results),
            "total_elapsed_s": round(_perf_ms(run_t0, _perf_now()) / 1000.0, 3),
            "slowest_views": [{"view_name": s.get("view_name"), "view_id": s.get("view_id"), "total_ms": float((s.get("timings") or {}).get(TIMING_KEYS["TOTAL_MS"], 0.0) or 0.0), "element_count": int((s.get("timings") or {}).get("element_count", 0) or 0)} for s in slow],
            "phase_totals_ms": {k: round(v, 3) for k, v in phase_totals.items()},
            "phase_pct": {k: round((v / phase_sum) * 100.0, 2) for k, v in phase_totals.items()},
            "memory_start_priv_mb": priv_vals[0] if priv_vals else None,
            "memory_end_priv_mb": priv_vals[-1] if priv_vals else None,
            "memory_peak_priv_mb": max(priv_vals) if priv_vals else None,
            "memory_freed_by_gc_mb": round(float(getattr(memory_tracker, "gc_freed_total_mb", 0.0) or 0.0), 3) if memory_tracker is not None else 0.0,
            "clr_gc_call_count": int(getattr(memory_tracker, "gc_call_count", 0) or 0) if memory_tracker is not None else 0,
        }
        print("\n[RUN SUMMARY] {}".format(run_summary))
        for _r in results:
            if isinstance(_r, dict):
                _r["run_summary"] = run_summary
                _r["memory_marks"] = mem_records
        if bool(getattr(cfg, "export_perf_csv", False)):
            try:
                export_perf_csv(results, output_dir=getattr(cfg, "perf_csv_output_dir", None) or output_dir, run_id=run_id, date_str=date_str, memory_records=mem_records)
            except Exception as e:
                print("[WARN] pipeline perf CSV export failed: {}".format(e))
    except Exception as e:
        print("[WARN] pipeline run summary failed: {}".format(e))

    return results



def init_view_raster(doc, view, cfg, diag=None):
    """Initialize ViewRaster for a view.

    Centralizes bounds resolution through resolve_view_bounds() so bounds behavior is auditable.
    """
    # Cell size: 1/8" on sheet -> model feet (REQUESTED resolution)
    scale = view.Scale  # e.g., 96 for 1/8" = 1'-0"
    cell_size_paper_in = float(getattr(cfg, "cell_size_paper_in", None))
    if cell_size_paper_in <= 0:
        raise ValueError("cfg.cell_size_paper_in must be > 0 (paper-space resolution)")

    cell_size_ft_requested = (cell_size_paper_in * scale) / 12.0  # inches -> feet

    # View basis
    basis = make_view_basis(view, diag=diag)

    # Resolve bounds centrally
    # NOTE: Drafting/annotation-only views require annotation-only bounds; otherwise fallback base bounds dominate.
    from .revit.view_basis import resolve_view_mode, VIEW_MODE_ANNOTATION_ONLY, resolve_annotation_only_bounds

    view_mode, _mode_reason = resolve_view_mode(view, diag=diag)

    if view_mode == VIEW_MODE_ANNOTATION_ONLY:
        anno_bounds = resolve_annotation_only_bounds(doc, view, basis, cell_size_ft_requested, cfg=cfg, diag=diag)

        if anno_bounds is None:
            # No driver annotations → deterministic small fallback to avoid huge grids
            from .core.math_utils import Bounds2D
            anno_bounds = Bounds2D(-10.0, -10.0, 10.0, 10.0)

        bounds_result = {
            "bounds_uv": anno_bounds,
            "reason": "annotation_only",
            "confidence": "med",
            "anno_expanded": True,
            "capped": False,
            "cap_triggered": False,
            "cap_before": None,
            "cap_after": None,
            "resolution_mode": "canonical",
            "cell_size_ft_requested": float(cell_size_ft_requested),
            "cell_size_ft_effective": float(cell_size_ft_requested),
            "grid_W": int(max(1, math.ceil(float(anno_bounds.width()) / cell_size_ft_requested))),
            "grid_H": int(max(1, math.ceil(float(anno_bounds.height()) / cell_size_ft_requested))),
            "buffer_ft": 0.0,
            "cell_size_ft": float(cell_size_ft_requested),
        }
    else:
        bounds_result = resolve_view_bounds(
            view,
            diag=diag,
            policy={
                "doc": doc,
                "basis": basis,
                "cfg": cfg,
                "buffer_ft": cfg.bounds_buffer_ft,
                "cell_size_ft": float(cell_size_ft_requested),
                "max_W": cfg.max_grid_cells_width,
                "max_H": cfg.max_grid_cells_height,
            },
        )

    bounds_xy = bounds_result["bounds_uv"]

    # Effective resolution (may differ if cap triggers)
    cell_size_ft_effective = float(bounds_result.get("cell_size_ft_effective", cell_size_ft_requested))

    W = int(bounds_result.get("grid_W", 1) or 1)
    H = int(bounds_result.get("grid_H", 1) or 1)

    if diag is not None:
        try:
            b = bounds_xy
            diag.info(
                phase="pipeline",
                callsite="init_view_raster.bounds_used",
                message="Bounds used to construct ViewRaster",
                view_id=getattr(getattr(view, "Id", None), "IntegerValue", None),
                extra={
                    "anno_expanded": bool(bounds_result.get("anno_expanded")),
                    "reason": bounds_result.get("reason"),
                    "confidence": bounds_result.get("confidence"),
                    "grid_W": W,
                    "grid_H": H,
                    "cell_size_ft_requested": float(cell_size_ft_requested),
                    "cell_size_ft_effective": float(cell_size_ft_effective),
                    "resolution_mode": bounds_result.get("resolution_mode"),
                    "cap_triggered": bool(bounds_result.get("cap_triggered", bounds_result.get("capped"))),
                    "bounds_xy": (b.xmin, b.ymin, b.xmax, b.ymax),
                    "cap_before": bounds_result.get("cap_before"),
                    "cap_after": bounds_result.get("cap_after"),
                },
            )
        except Exception as e:
            if diag is not None:
                diag.error(
                    phase="pipeline",
                    callsite="init_view_raster",
                    message="Exception in init_view_raster: {}".format(e),
                    exc=e,
                )
    # Compute adaptive tile size based on grid dimensions
    tile_size = cfg.compute_adaptive_tile_size(W, H)

    raster = ViewRaster(
        width=W, height=H, cell_size=cell_size_ft_effective, bounds=bounds_xy, tile_size=tile_size, cfg=cfg
    )

    # Log raster bounds for floater diagnostics
    print("[RASTER] Grid: {}x{}, Cell: {:.3f}ft, UV bounds: ({:.1f},{:.1f}) to ({:.1f},{:.1f})".format(
        W, H, cell_size_ft_effective,
        bounds_xy.xmin, bounds_xy.ymin, bounds_xy.xmax, bounds_xy.ymax))

    # If raster bounds were expanded for annotations, preserve the pre-annotation bounds
    # as a model-only clip region. Model writes consult this; annotation writes do not.
    try:
        raster.model_clip_bounds = bounds_result.get("model_bounds_uv", None)
    except Exception as e:
        if diag is not None:
            diag.error(
                phase="pipeline",
                callsite="init_view_raster",
                message="Exception in init_view_raster: {}".format(e),
                exc=e,
            )
    # Persist bounds/resolution metadata for export diagnostics (never silent)
    raster.bounds_meta = {
        "reason": bounds_result.get("reason"),
        "confidence": bounds_result.get("confidence"),
        "buffer_ft": bounds_result.get("buffer_ft"),
        "anno_expanded": bounds_result.get("anno_expanded"),

        "capped": bounds_result.get("capped"),
        "cap_triggered": bool(bounds_result.get("cap_triggered", bounds_result.get("capped"))),
        "cap_before": bounds_result.get("cap_before"),
        "cap_after": bounds_result.get("cap_after"),

        "grid_W": W,
        "grid_H": H,

        # Option 2 contract fields
        "resolution_mode": bounds_result.get("resolution_mode", "canonical"),
        "cell_size_ft_requested": float(bounds_result.get("cell_size_ft_requested", cell_size_ft_requested)),
        "cell_size_ft_effective": float(bounds_result.get("cell_size_ft_effective", cell_size_ft_effective)),
    }


    # Store view basis for annotation rasterization
    raster.view_basis = basis

    return raster

def _extract_view_summary(view_result):
    """Extract lightweight summary from full view result.
    
    Discards heavy raster data while retaining essential metadata for
    summary statistics and reporting. Used when retain_rasters_in_memory=False.
    
    Args:
        view_result: Full view result dict with raster data
        
    Returns:
        Lightweight summary dict without raster arrays
        
    Memory impact:
        - Full result: ~2-20 MB per view (depends on grid size)
        - Summary: ~1-2 KB per view
        - Savings: ~99.9% memory reduction per view
    """
    return {
        "view_id": view_result.get("view_id"),
        "view_name": view_result.get("view_name"),
        "success": view_result.get("success", True),
        "view_mode": view_result.get("view_mode"),
        "view_mode_reason": view_result.get("view_mode_reason"),
        "width": view_result.get("width"),
        "height": view_result.get("height"),
        "cell_size": view_result.get("cell_size"),
        "tile_size": view_result.get("tile_size"),
        "total_elements": view_result.get("total_elements"),
        "filled_cells": view_result.get("filled_cells"),
        "timings": view_result.get("timings"),
        "diagnostics": view_result.get("diagnostics", {}),
        "cache": view_result.get("cache"),
        "config": view_result.get("config"),
        "occlusion_tracker": view_result.get("occlusion_tracker"),
        # Explicitly omit "raster" key to free memory
    }


def rasterize_areal_loops(loops, raster, key_index, elem_depth, source_type, confidence, strategy, elem_id=None, category=None):
    """Rasterize AREAL element loops with confidence-based occlusion handling.

    Args:
        loops: List of loop dicts [{'points': [...], 'is_hole': bool}]
        raster: ViewRaster instance
        key_index: Element metadata index
        elem_depth: Element depth (W coordinate)
        source_type: Source type ('HOST' or 'LINK')
        confidence: Confidence level ('HIGH', 'MEDIUM', 'LOW')
        strategy: Strategy name used for extraction
        elem_id: Optional element ID for debugging
        category: Optional category name for debugging

    Returns:
        Tuple of (success, filled_cells):
          - success: True if rasterization succeeded
          - filled_cells: Number of cells filled (0 for edges-only)

    Commentary:
        HIGH confidence:
          - Rasterizes filled polygons and edges
          - Updates occlusion buffer (allows early-out for later elements)
          - Uses actual extracted geometry (planar_face_loops, silhouette_edges)

        MEDIUM confidence:
          - Rasterizes to proxy layer ONLY (no occlusion)
          - Uses geometry_polygon extraction (actual footprint, not bbox)
          - Visible in output but doesn't block later elements

        LOW confidence:
          - Rasterizes to proxy layer ONLY (no occlusion)
          - Uses OBB/AABB fallback (approximate shape)
          - Visible in output but doesn't block later elements
    """
    if not loops or len(loops) == 0:
        return (False, 0)

    try:
        # Store strategy and confidence in element metadata
        if key_index < len(raster.element_meta):
            raster.element_meta[key_index]["strategy"] = strategy
            raster.element_meta[key_index]["confidence"] = confidence
            raster.element_meta[key_index]["occluder"] = (confidence == "HIGH")

        # Separate open and closed loops
        open_loops = []
        closed_loops = []
        for lp in loops:
            if lp.get("open", False):
                open_loops.append(lp)
            else:
                closed_loops.append(lp)

        filled = 0
        open_polyline_success = False

        # HIGH confidence: Rasterize with occlusion
        if confidence == "HIGH":
            # DEBUG: Log HIGH confidence rasterization
            print("[DEBUG RASTER] Element {} ({}): HIGH confidence - rasterizing to model_edge + w_occ".format(
                elem_id, category))

            # Rasterize closed loops (fills + occlusion)
            if closed_loops:
                try:
                    filled += raster.rasterize_silhouette_loops(
                        closed_loops, key_index, depth=elem_depth, source=source_type, occlude_edges=True
                    )
                except Exception as e:
                    # Exception in rasterize_areal_loops - no diag in scope
                    pass  # TODO: Add diagnostics when diag becomes available
            # Rasterize open polylines (edges)
            if open_loops:
                try:
                    filled += raster.rasterize_open_polylines(
                        open_loops, key_index, depth=elem_depth, source=source_type
                    )
                    if len(open_loops) > 0:
                        open_polyline_success = True
                except Exception as e:
                    # Exception in rasterize_areal_loops - no diag in scope
                    pass  # TODO: Add diagnostics when diag becomes available
            # For MEDIUM/LOW, show boundary ink via proxy edges ONLY, with NO occlusion.
            if closed_loops:
                try:
                    filled += raster.rasterize_closed_loops_to_proxy_edges(
                        closed_loops, key_index, depth=elem_depth, source=source_type
                    )
                except Exception as e:
                    # Exception in rasterize_areal_loops - no diag in scope
                    pass  # TODO: Add diagnostics when diag becomes available
            if open_loops:
                try:
                    filled += raster.rasterize_open_polylines_to_proxy_edges(
                        open_loops, key_index, depth=elem_depth, source=source_type
                    )
                    if len(open_loops) > 0:
                        open_polyline_success = True
                except Exception as e:
                    # Exception in rasterize_areal_loops - no diag in scope
                    pass  # TODO: Add diagnostics when diag becomes available
        # Mark open-polyline-only rendering in metadata
        if open_polyline_success and filled == 0:
            if key_index < len(raster.element_meta):
                raster.element_meta[key_index]["open_polyline_only"] = True

        # Success if we filled cells OR drew open polylines
        success = (filled > 0) or open_polyline_success
        return (success, filled)

    except Exception as e:
        return (False, 0)


def render_model_front_to_back(doc, view, raster, elements, cfg, diag=None, geometry_cache=None, elem_cache=None, strategy_diag=None):
    """Render 3D model elements front-to-back with interwoven AreaL/Tiny/Linear handling.

    Args:
        doc: Revit Document
        view: Revit View
        raster: ViewRaster (modified in-place)
        elements: List of Revit elements (from collect_view_elements)
        cfg: Config
        diag: Optional diagnostics
        geometry_cache: Optional geometry cache for silhouettes
        elem_cache: Optional element cache for bbox fingerprints (Phase 2)
        strategy_diag: Optional StrategyDiagnostics instance

    Returns:
        None (modifies raster in-place)

    Commentary:
        ✔ Uses silhouette extraction for accurate element boundaries
        ✔ Falls back to bbox if silhouette extraction fails
        ✔ Classifies elements as TINY/LINEAR/AREAL
        ✔ Rasterizes silhouette loops with edge tracking
        ✔ Handles linked/imported elements with transforms
    """
    from .revit.collection import _project_element_bbox_to_cell_rect, expand_host_link_import_model_elements

    # ── Sub-timing accumulators (milliseconds) ──
    _sub_t = {
        "raster_expand_ms": 0.0,
        "raster_sorting_ms": 0.0,
        "raster_enrich_ms": 0.0,
        "raster_geom_extract_ms": 0.0,
        "raster_depth_test_ms": 0.0,
        "raster_cell_write_ms": 0.0,
        "raster_element_iter_ms": 0.0,
        TIMING_KEYS["BBOX_MS"]: 0.0,
        TIMING_KEYS["CLASSIFY_MS"]: 0.0,
        TIMING_KEYS["GEOM_SILHOUETTE_MS"]: 0.0,
        TIMING_KEYS["GEOM_OBB_MS"]: 0.0,
        TIMING_KEYS["GEOM_BBOX_MS"]: 0.0,
    }

    # Get view basis for transformations
    vb = make_view_basis(view, diag=diag)

    # Resolve explicit view-space W volume once per view (shared by host + link).
    from .revit.view_basis import resolve_view_w_volume
    W0, Wmax, _wvol_meta = resolve_view_w_volume(view, vb, cfg, diag=diag)

    # DIAGNOSTICS: Initialize view-level diagnostic collection
    view_diag = {
        "view_id": None,
        "view_name": None,
        "total_elements": 0,
        "classification_counts": {"TINY": 0, "LINEAR": 0, "AREAL": 0},
        "strategy_matrix": {"TINY": {}, "LINEAR": {}, "AREAL": {}},
        "confidence_counts": {"HIGH": 0, "MEDIUM": 0, "LOW": 0},
        "fallback_elements": [],
    }
    try:
        view_diag["view_id"] = int(getattr(getattr(view, "Id", None), "IntegerValue", 0))
        view_diag["view_name"] = str(getattr(view, "Name", "Unknown"))
    except Exception as e:
        if diag is not None:
            diag.error(
                phase="pipeline",
                callsite="render_model_front_to_back",
                message=f"Failed to extract view identity: {e}",
                exc=e,
            )

    # Persist for export/diagnostics (safe: optional fields)
    try:
        raster.view_w0 = W0
        raster.view_wmax = Wmax
        raster.view_wvol_meta = _wvol_meta
    except Exception as e:
        if diag is not None:
            diag.error(
                phase="pipeline",
                callsite="render_model_front_to_back",
                message="Exception in render_model_front_to_back: {}".format(e),
                exc=e,
            )
    # Expand to include linked/imported elements
    _t0_expand = _perf_now()
    expanded_elements = expand_host_link_import_model_elements(doc, view, elements, cfg, diag=diag, elem_cache=elem_cache)
    _sub_t["raster_expand_ms"] = _perf_ms(_t0_expand, _perf_now())

    # Occlusion diagnostics tracker (lightweight counters + aggregate timings)
    occlusion_tracker = OcclusionTracker(
        extraction_est_ms=getattr(cfg, "occlusion_extraction_est_ms", 5.0),
        raster_tile_est_ms=getattr(cfg, "occlusion_raster_tile_est_ms", 0.1),
    )

    # Sort elements front-to-back by depth for proper occlusion
    _t0_sort = _perf_now()
    expanded_elements = sort_front_to_back(expanded_elements, view, raster)
    _t1_sort = _perf_now()
    _sub_t["raster_sorting_ms"] = _perf_ms(_t0_sort, _t1_sort)
    occlusion_tracker.time_sorting_ms = _perf_ms(_t0_sort, _t1_sort)

    # Enrich elements with depth range and bbox for ambiguity detection
    _t0_enrich = _perf_now()
    from .revit.collection import estimate_depth_range_from_bbox
    for wrapper in expanded_elements:
        try:
            elem = wrapper["element"]
            world_transform = wrapper["world_transform"]
            bbox = wrapper.get("bbox")

            depth_range = estimate_depth_range_from_bbox(
                elem,
                world_transform,
                view,
                raster,
                bbox=bbox,
                diag=diag,
                bbox_is_link_space=bool(wrapper.get("bbox_is_link_space", False)),
            )

            wrapper["depth_range"] = depth_range

            rect = _project_element_bbox_to_cell_rect(
                elem,
                vb,
                raster,
                bbox=bbox,
                diag=diag,
                view=view,
            )
            wrapper["uv_bbox_rect"] = rect
        except Exception as e:
            if diag is not None:
                diag.error(
                    phase="pipeline",
                    callsite="render_model_front_to_back",
                    message="Exception in render_model_front_to_back: {}".format(e),
                    exc=e,
                )
            wrapper["depth_range"] = (0.0, 0.0)
            wrapper["uv_bbox_rect"] = None
    _sub_t["raster_enrich_ms"] = _perf_ms(_t0_enrich, _perf_now())
    _sub_t[TIMING_KEYS["BBOX_MS"]] = _sub_t["raster_enrich_ms"]

    # Process each element (host + linked)
    processed = 0
    skipped_outside_view_volume = 0
    skipped = 0
    silhouette_success = 0
    bbox_fallback = 0
    tiny_count = 0
    linear_count = 0
    areal_count = 0
    fallback_bbox_hits = 0

    def _classify_uv_rect(width_cells, height_cells):
        # Local, explicit classification to avoid dependency on classify_by_uv signature.
        # Semantics:
        #   - TINY   : <= 2x2 cells
        #   - LINEAR : thin in one dimension (<=2) and longer in the other
        #   - AREAL  : everything else (occlusion-authoritative)
    
        minor = min(width_cells, height_cells)
        major = max(width_cells, height_cells)

        if major <= 2 and minor <= 2:
            return "TINY"
        if minor <= 2 and major > 2:
            return "LINEAR"
        return "AREAL"

    # Confidence levels for geometry extraction (match areal_extraction.py output)
    CONF_HIGH = "HIGH"      # Tier 1: planar_face_loops, silhouette_edges
    CONF_MEDIUM = "MEDIUM"  # Tier 2: geometry_polygon extraction
    CONF_LOW = "LOW"        # Tier 2/3: OBB/AABB fallback

    def _rect_dims_for_classification(rect, raster):
        """
        Prefer OBB dimensions when available (diagonals), else fall back to AABB cell rect.
        Returns (width_cells, height_cells) as floats.
        """
        try:
            obb = getattr(rect, "obb_data", None)
            if obb and isinstance(obb, dict):
                # Stored in world/uv units; convert to cells using raster cell size.
                cell = float(getattr(raster, "cell_size", 1.0) or 1.0)
                if cell <= 0:
                    cell = 1.0
                len_u = float(obb.get("len_u", 0.0) or 0.0)
                len_v = float(obb.get("len_v", 0.0) or 0.0)
                return (abs(len_u) / cell, abs(len_v) / cell)
        except Exception as e:
            if diag is not None:
                diag.error(
                    phase="pipeline",
                    callsite="_rect_dims_for_classification",
                    message="Exception in _rect_dims_for_classification: {}".format(e),
                    exc=e,
                )
        # Fallback: AABB in cell units
        try:
            return (float(rect.width()), float(rect.height()))
        except Exception as e:
            if diag is not None:
                diag.error(
                    phase="pipeline",
                    callsite="_rect_dims_for_classification",
                    message="Exception in _rect_dims_for_classification: {}".format(e),
                    exc=e,
                )
            return (0.0, 0.0)

    def _occlusion_allowed(elem_class, confidence):
        return (elem_class == "AREAL") and (confidence == CONF_HIGH)

    _t0_elem_iter = _perf_now()
    for element_idx, elem_wrapper in enumerate(expanded_elements):
        elem = elem_wrapper["element"]
        source_type = elem_wrapper.get("source_type", "HOST")
        source_id = elem_wrapper.get("source_id", source_type)
        source_label = elem_wrapper.get("source_label", source_id)
        world_transform = elem_wrapper["world_transform"]
        occlusion_tracker.record_element()

        # View-volume gating: skip elements whose bbox W-range does not overlap [W0, Wmax].
        # This is the ONLY intended semantic change: exclude truly-outside elements.
        if (W0 is not None) and (Wmax is not None):
            try:
                dmin, dmax = elem_wrapper.get("depth_range", (None, None))
                if (dmin is None) or (dmax is None):
                    dmin, dmax = (None, None)

                # Normalize range if present
                if (dmin is not None) and (dmax is not None) and (dmin > dmax):
                    dmin, dmax = dmax, dmin

                # Non-overlap => skip
                if (dmin is not None) and (dmax is not None):
                    if (dmax < W0 - BOUNDARY_TOLERANCE) or (dmin > Wmax + BOUNDARY_TOLERANCE):
                        skipped_outside_view_volume += 1
                        try:
                            # Minimal, auditable tag (no spam)
                            # key_index may not exist yet here; only write later if available
                            elem_wrapper["_skipped_outside_view_volume"] = True
                            elem_wrapper["_skip_w_range"] = (dmin, dmax)
                        except Exception as e:
                            if diag is not None:
                                diag.error(
                                    phase="pipeline",
                                    callsite="_occlusion_allowed",
                                    message="Exception in _occlusion_allowed: {}".format(e),
                                    exc=e,
                                )
                        continue
            except Exception as e:
                if diag is not None:
                    diag.error(
                        phase="pipeline",
                        callsite="_occlusion_allowed",
                        message="Exception in _occlusion_allowed: {}".format(e),
                        exc=e,
                    )
                # Conservative: do not gate if we cannot determine depth range

        # Get element metadata
        try:
            elem_id = elem.Id.IntegerValue
            category = elem.Category.Name if elem.Category else "Unknown"
        except Exception as e:
            # Log the error but continue processing other elements
            skipped += 1
            if skipped <= 5:  # Log first 5 errors to avoid spam
                print("[WARN] vop.pipeline: Skipping element from {0}: {1}".format(source_type, e))
            continue

        key_index = raster.get_or_create_element_meta_index(
            elem_id, category,
            source_id=source_id,
            source_type=source_type,
            source_label=source_label
        )

        # If wrapper was volume-skipped before meta existed, record it now (auditable).
        try:
            if elem_wrapper.get("_skipped_outside_view_volume", False):
                if 0 <= key_index < len(raster.element_meta):
                    raster.element_meta[key_index]["skipped_outside_view_volume"] = True
                    raster.element_meta[key_index]["skip_w_range"] = elem_wrapper.get("_skip_w_range")
        except Exception as e:
            if diag is not None:
                diag.error(
                    phase="pipeline",
                    callsite="_occlusion_allowed",
                    message="Exception in _occlusion_allowed: {}".format(e),
                    exc=e,
                )
        # PR9: persist bbox provenance into element meta (auditable)
        try:
            if 0 <= key_index < len(raster.element_meta):
                raster.element_meta[key_index]["bbox_source"] = elem_wrapper.get("bbox_source")
        except Exception as e:
            if diag is not None:
                diag.error(
                    phase="pipeline",
                    callsite="_occlusion_allowed",
                    message="Exception in _occlusion_allowed: {}".format(e),
                    exc=e,
                )
        if source_type not in ("HOST", "LINK", "DWG"):
            raise ValueError("Invalid source_type from wrapper: {0} (source_id={1})".format(source_type, source_id))

        # Optional targeted diagnostics for LINK transform issues.
        # If cfg.diag_link_elem_ids is a non-empty iterable of int element ids, only those ids are traced.
        diag_link_ids = set()
        try:
            v = getattr(cfg, "diag_link_elem_ids", None)
            if v:
                diag_link_ids = set(int(x) for x in v)
        except Exception as e:
            if diag is not None:
                diag.error(
                    phase="pipeline",
                    callsite="_occlusion_allowed",
                    message="Exception in _occlusion_allowed: {}".format(e),
                    exc=e,
                )
            diag_link_ids = set()

        # DIAGNOSTIC: Stage 1 - Right after extracting wrapper data
        if source_type == "LINK" and ((diag_link_ids and elem_id in diag_link_ids) or ((not diag_link_ids) and processed < 3)):
            try:
                _diagnose_link_geometry_transform(elem, world_transform, vb, "STAGE1_WRAPPER_EXTRACTED")
            except Exception as diag_e:
                print("[DEBUG] Diagnostic failed at stage 1: {}".format(diag_e))

        # PHASE 2.2: Classify element FIRST, then use appropriate extraction strategy
        # AREAL elements use unified extract_areal_geometry() with confidence levels
        # TINY/LINEAR elements use get_element_silhouette() as before

        # Get rect for classification (required before extraction)
        rect = elem_wrapper.get("uv_bbox_rect")
        if rect is None:
            try:
                rect = _project_element_bbox_to_cell_rect(
                    elem,
                    vb,
                    raster,
                    bbox=elem_wrapper.get("bbox"),
                    diag=diag,
                    view=view,
                )
            except Exception as e:
                if diag is not None:
                    diag.error(
                        phase="pipeline",
                        callsite="_occlusion_allowed",
                        message="Exception in _occlusion_allowed: {}".format(e),
                        exc=e,
                    )
                rect = None

        # Classify element based on rect dimensions
        _t0_class = _perf_now()
        elem_class = "AREAL"  # Default classification
        if rect and not rect.empty:
            try:
                cls_w_cells, cls_h_cells = _rect_dims_for_classification(rect, raster)
                elem_class = _classify_uv_rect(cls_w_cells, cls_h_cells)
            except Exception as e:
                if diag is not None:
                    diag.error(
                        phase="pipeline",
                        callsite="_occlusion_allowed",
                        message="Exception in _occlusion_allowed: {}".format(e),
                        exc=e,
                    )
                elem_class = "AREAL"  # Safe default on classification failure
        _sub_t[TIMING_KEYS["CLASSIFY_MS"]] += _perf_ms(_t0_class, _perf_now())
        if elem_class == "TINY":
            tiny_count += 1
        elif elem_class == "LINEAR":
            linear_count += 1
        else:
            areal_count += 1

        # Extract geometry using appropriate strategy based on classification
        _t0_geom = _perf_now()
        loops = None
        confidence = None
        strategy = None
        silhouette_error = None
        geom_extract_ms = 0.0

        if elem_class == "AREAL":
            # AREAL: Use unified extraction with confidence-based fallback
            t_geom_start = time.perf_counter()
            try:
                loops, confidence, strategy = extract_areal_geometry(
                    elem=elem,
                    view=view,
                    view_basis=vb,
                    raster=raster,
                    cfg=cfg,
                    diag=diag,
                    strategy_diag=strategy_diag
                )

                geom_extract_ms = (time.perf_counter() - t_geom_start) * 1000.0

                # Normalize confidence to uppercase (extract_areal_geometry returns 'HIGH', 'MEDIUM', 'LOW')
                if confidence is None:
                    confidence = CONF_LOW  # Failed extraction

            except Exception as e:
                # Extraction failed completely
                loops = None
                confidence = CONF_LOW
                strategy = 'failed'
                silhouette_error = str(e)
                if processed < 10:
                    print("[DEBUG] AREAL extraction failed for element {0} ({1}): {2}".format(
                        elem_id, category, silhouette_error))
        else:
            # TINY/LINEAR: Use traditional silhouette extraction (no confidence levels)
            try:
                # PR12: bounded LRU cache for expensive silhouette/triangulation calls.
                cache_key = None
                if geometry_cache is not None:
                    try:
                        view_id_int = getattr(getattr(view, "Id", None), "IntegerValue", None)
                    except Exception as e:
                        if diag is not None:
                            diag.error(
                                phase="pipeline",
                                callsite="_occlusion_allowed",
                                message="Exception in _occlusion_allowed: {}".format(e),
                                exc=e,
                            )
                        view_id_int = None
                    cache_key = (
                        source_id,
                        elem_id,
                        view_id_int,
                        getattr(cfg, "proxy_mask_mode", None),
                        "silhouette_v1",
                    )

                # DIAGNOSTIC: Stage 2 - Right before calling get_element_silhouette
                if source_type == "LINK" and processed < 3:  # Only first 3 LINK elements
                    try:
                        _diagnose_link_geometry_transform(elem, world_transform, vb, "STAGE2_BEFORE_SILHOUETTE")
                    except Exception as diag_e:
                        print("[DEBUG] Diagnostic failed at stage 2: {}".format(diag_e))

                loops = get_element_silhouette(elem, view, vb, raster, cfg, cache=geometry_cache, cache_key=cache_key, diag=diag)

                # =====================================================================
                # DIAGNOSTIC: Coordinate space check for element 987587
                # =====================================================================
                if elem_id == 987587 and loops and len(loops) > 0:
                    print(f"\n{'='*80}")
                    print(f"SILHOUETTE COORDINATE DIAGNOSTIC - Element {elem_id}")
                    print(f"{'='*80}")
                    print(f"Category: {category}")
                    print(f"Source: {source_type}")
                    print(f"Number of loops returned: {len(loops)}")
                    
                    # Analyze ALL loops
                    for loop_idx, loop in enumerate(loops):
                        points = loop.get('points', [])
                        strategy = loop.get('strategy', 'unknown')
                        is_hole = loop.get('is_hole', False)
                        is_open = loop.get('open', False)
                        
                        print(f"\n  Loop {loop_idx}:")
                        print(f"    Strategy: {strategy}")
                        print(f"    Point count: {len(points)}")
                        print(f"    Is hole: {is_hole}")
                        print(f"    Is open: {is_open}")
                        
                        if len(points) > 0:
                            # Show all points for small loops, first/last 3 for large loops
                            if len(points) <= 10:
                                print(f"    All points:")
                                for i, pt in enumerate(points):
                                    if len(pt) >= 2:
                                        print(f"      [{i}] U={pt[0]:10.2f}, V={pt[1]:10.2f}", end="")
                                    if len(pt) >= 3:
                                        print(f", W={pt[2]:10.2f}")
                                    else:
                                        print()
                            else:
                                print(f"    First 3 points:")
                                for i, pt in enumerate(points[:3]):
                                    if len(pt) >= 2:
                                        print(f"      [{i}] U={pt[0]:10.2f}, V={pt[1]:10.2f}", end="")
                                    if len(pt) >= 3:
                                        print(f", W={pt[2]:10.2f}")
                                    else:
                                        print()
                                print(f"    Last 3 points:")
                                for i, pt in enumerate(points[-3:], start=len(points)-3):
                                    if len(pt) >= 2:
                                        print(f"      [{i}] U={pt[0]:10.2f}, V={pt[1]:10.2f}", end="")
                                    if len(pt) >= 3:
                                        print(f", W={pt[2]:10.2f}")
                                    else:
                                        print()
                        
                        # UV bounds for this loop
                        u_coords = [pt[0] for pt in points if len(pt) >= 2]
                        v_coords = [pt[1] for pt in points if len(pt) >= 2]
                        
                        if u_coords and v_coords:
                            u_min, u_max = min(u_coords), max(u_coords)
                            v_min, v_max = min(v_coords), max(v_coords)
                            u_center = (u_min + u_max) / 2.0
                            v_center = (v_min + v_max) / 2.0
                            u_span = u_max - u_min
                            v_span = v_max - v_min
                            
                            print(f"    UV Bounds: U=[{u_min:7.2f}, {u_max:7.2f}] V=[{v_min:7.2f}, {v_max:7.2f}]")
                            print(f"    Span: U={u_span:7.2f}, V={v_span:7.2f}")
                            print(f"    Center: U={u_center:7.2f}, V={v_center:7.2f}")
                    
                    # Check for overlapping loops
                    print(f"\n  Overlap Analysis:")
                    for i in range(len(loops)):
                        for j in range(i+1, len(loops)):
                            loop_i_points = loops[i].get('points', [])
                            loop_j_points = loops[j].get('points', [])
                            
                            if loop_i_points and loop_j_points:
                                # Get bounds
                                u_i = [pt[0] for pt in loop_i_points if len(pt) >= 2]
                                v_i = [pt[1] for pt in loop_i_points if len(pt) >= 2]
                                u_j = [pt[0] for pt in loop_j_points if len(pt) >= 2]
                                v_j = [pt[1] for pt in loop_j_points if len(pt) >= 2]
                                
                                if u_i and v_i and u_j and v_j:
                                    # Check for overlap
                                    u_overlap = not (max(u_i) < min(u_j) or max(u_j) < min(u_i))
                                    v_overlap = not (max(v_i) < min(v_j) or max(v_j) < min(v_i))
                                    
                                    if u_overlap and v_overlap:
                                        print(f"    ⚠️  Loop {i} and Loop {j} OVERLAP")
                                        print(f"        Loop {i}: U=[{min(u_i):7.2f}, {max(u_i):7.2f}] V=[{min(v_i):7.2f}, {max(v_i):7.2f}]")
                                        print(f"        Loop {j}: U=[{min(u_j):7.2f}, {max(u_j):7.2f}] V=[{min(v_j):7.2f}, {max(v_j):7.2f}]")
                    
                    # Check bbox for comparison
                    try:
                        test_bbox = elem.get_BoundingBox(view)
                        if not test_bbox:
                            test_bbox = elem.get_BoundingBox(None)
                        
                        if test_bbox:
                            print(f"\n  BBox Info:")
                            bbox_tf = getattr(test_bbox, "Transform", None)
                            print(f"    BBox.Transform.IsIdentity: {getattr(bbox_tf, 'IsIdentity', True) if bbox_tf else True}")
                            print(f"    BBox.Min (local): ({test_bbox.Min.X:.2f}, {test_bbox.Min.Y:.2f}, {test_bbox.Min.Z:.2f})")
                            print(f"    BBox.Max (local): ({test_bbox.Max.X:.2f}, {test_bbox.Max.Y:.2f}, {test_bbox.Max.Z:.2f})")
                    except Exception as e:
                        print(f"  Could not analyze bbox: {e}")
                    
                    print(f"{'='*80}\n")

                # Assign confidence for TINY/LINEAR (simple model)
                confidence = CONF_HIGH if loops else CONF_LOW

                # Extract strategy from loops if available
                if loops and len(loops) > 0:
                    strategy = loops[0].get('strategy', 'silhouette')
                else:
                    strategy = 'failed'

                if isinstance(strategy, str):
                    _st = strategy.lower()
                    if "silhouette" in _st or "planar" in _st or "geometry_polygon" in _st:
                        _sub_t[TIMING_KEYS["GEOM_SILHOUETTE_MS"]] += float(geom_extract_ms or 0.0)
                    elif "obb" in _st:
                        _sub_t[TIMING_KEYS["GEOM_OBB_MS"]] += float(geom_extract_ms or 0.0)
                    elif "bbox" in _st or "aabb" in _st:
                        _sub_t[TIMING_KEYS["GEOM_BBOX_MS"]] += float(geom_extract_ms or 0.0)

            except Exception as e:
                # Silhouette extraction failed, loops will be None
                loops = None
                confidence = CONF_LOW
                strategy = 'failed'
                silhouette_error = str(e)
                if processed < 10:
                    print("[DEBUG] Silhouette extraction failed for element {0} ({1}): {2}".format(
                        elem_id, category, silhouette_error))

        _sub_t["raster_geom_extract_ms"] += _perf_ms(_t0_geom, _perf_now())

        # DIAGNOSTICS: track per-element classification/strategy/confidence and fallback details
        try:
            view_diag["total_elements"] += 1

            if elem_class in view_diag["classification_counts"]:
                view_diag["classification_counts"][elem_class] += 1

            if elem_class in view_diag["strategy_matrix"]:
                strategy_key = strategy if strategy else "unknown"
                view_diag["strategy_matrix"][elem_class][strategy_key] = view_diag["strategy_matrix"][elem_class].get(strategy_key, 0) + 1

            conf_key = confidence if isinstance(confidence, str) else None
            if conf_key in view_diag["confidence_counts"]:
                view_diag["confidence_counts"][conf_key] += 1

            if confidence == CONF_LOW and elem_class == "AREAL":
                view_diag["fallback_elements"].append({
                    "elem_id": elem_id,
                    "category": category,
                    "classification": elem_class,
                    "final_strategy": strategy if strategy else "unknown",
                    "confidence": confidence,
                    "geom_extract_ms": round(geom_extract_ms, 2),
                })
        except Exception as e:
            if diag is not None:
                diag.error(
                    phase="pipeline",
                    callsite="render_model_front_to_back.diagnostics",
                    message=f"Diagnostics collection failed: {e}",
                    exc=e,
                )

        bbox_link = elem_wrapper.get("bbox_link")
        bbox_for_metrics = bbox_link if bbox_link is not None else elem_wrapper.get("bbox")
        bbox_is_link_space = bbox_link is not None

        # Calculate element depth from silhouette geometry OR bbox fallback
        # CRITICAL FIX: Use accurate geometry depth instead of bbox-only depth
        from .revit.collection import estimate_depth_from_loops_or_bbox

        elem_depth = estimate_depth_from_loops_or_bbox(
            elem=elem,
            loops=loops,
            bbox=bbox_for_metrics,
            transform=world_transform,
            view=view,
            raster=raster,
            bbox_is_link_space=bbox_is_link_space,
        )

        # Depth must be finite. NaN causes all depth tests to reject (NaN < inf is False),
        # which yields exactly: filled_cells=0, occlusion_cells=0, proxy_edge_cells=0.
        if (elem_depth is None) or (not isinstance(elem_depth, (int, float))) or (not math.isfinite(elem_depth)):
            # Fall back to a conservative nearest-depth estimate from bbox.
            try:
                elem_depth = estimate_nearest_depth_from_bbox(
                    elem,
                    world_transform,
                    view,
                    raster,
                    bbox=elem_wrapper.get("bbox"),
                    diag=diag,
                )
            except Exception as e:
                if diag is not None:
                    diag.error(
                        phase="pipeline",
                        callsite="_occlusion_allowed",
                        message="Exception in _occlusion_allowed: {}".format(e),
                        exc=e,
                    )
                elem_depth = 0.0

            try:
                if key_index < len(raster.element_meta):
                    raster.element_meta[key_index]["depth_invalid"] = True
            except Exception as e:
                if diag is not None:
                    diag.error(
                        phase="pipeline",
                        callsite="_occlusion_allowed",
                        message="Exception in _occlusion_allowed: {}".format(e),
                        exc=e,
                    )
        # Clamp depth used for early-out comparisons to the view volume (min depth >= W0).
        # Do NOT change silhouette strategy; this only prevents out-of-volume depths from driving occlusion logic.
        if (W0 is not None) and isinstance(elem_depth, (int, float)) and math.isfinite(elem_depth):
            if elem_depth < W0:
                try:
                    if key_index < len(raster.element_meta):
                        raster.element_meta[key_index]["depth_clamped_to_w0"] = True
                except Exception as e:
                    if diag is not None:
                        diag.error(
                            phase="pipeline",
                            callsite="_occlusion_allowed",
                            message="Exception in _occlusion_allowed: {}".format(e),
                            exc=e,
                        )
                elem_depth = W0

        # DEBUG: Log depth values and silhouette status for first few elements
        if processed < 10:
            depth_source = "geometry" if loops else "bbox"
            silhouette_status = "SUCCESS ({0} loops)".format(len(loops)) if loops else "FAILED (bbox fallback)"
            
            # Get classification from wrapper if available
            rect = elem_wrapper.get("uv_bbox_rect")
            classification = "?"
            if rect and not rect.empty:
                w_cells, h_cells = _rect_dims_for_classification(rect, raster)
                classification = _classify_uv_rect(w_cells, h_cells)
            
            print("[DEBUG] Element {0} ({1}): silhouette={2}, depth={3} (from {4}), source={5}, class={6}".format(
                elem_id, category, silhouette_status, elem_depth, depth_source, source_type, classification))

        # Safe early-out occlusion using bbox footprint + tile depth (front-to-back streaming)
        _t0_depth = _perf_now()
        try:
            rect = elem_wrapper.get("uv_bbox_rect")
            if rect is None:
                rect = _project_element_bbox_to_cell_rect(
                    elem,
                    vb,
                    raster,
                    bbox=elem_wrapper.get("bbox"),
                    diag=diag,
                    view=view,
                )
            if rect and (not rect.empty):
                from .core.footprint import CellRectFootprint
                fp = CellRectFootprint(rect)
                fp_tile_count = len(fp.tiles(raster.tile))

                # Defaults must be defined on the non-ambiguous path
                uvw_pts = None
                footprint = fp
                footprint_tile_count = fp_tile_count
                elem_min_w = elem_depth  # conservative: element depth from loops-or-bbox

                # Stage 1: tile-level conservative occlusion against bbox footprint
                if _tiles_fully_covered_and_nearer(raster.tile, fp, elem_min_w, tracker=occlusion_tracker):
                    skipped += 1
                    occlusion_tracker.record_bbox_rejection()
                    _sub_t["raster_depth_test_ms"] += _perf_ms(_t0_depth, _perf_now())
                    continue

                # Tier-A ambiguity trigger (selectively enable Tier-B proxy)
                # NOTE: multiple CellRect implementations exist; derive dimensions via helper.
                from .core.math_utils import cellrect_dims
                aabb_w_cells, aabb_h_cells = cellrect_dims(rect)

                # Classification and confidence already set above (Phase 2.2)
                # elem_class: set before extraction (line ~1620)
                # confidence: returned by extract_areal_geometry() or assigned for TINY/LINEAR

                # Get dimensions for Tier-A ambiguity check
                cls_w_cells, cls_h_cells = _rect_dims_for_classification(rect, raster)
                minor_cells = min(cls_w_cells, cls_h_cells)

                # Use existing confidence (don't overwrite what extraction set)
                if confidence is None:
                    confidence = CONF_LOW  # Safety fallback

                occlusion_allowed = _occlusion_allowed(elem_class, confidence)

                if key_index < len(raster.element_meta):
                    raster.element_meta[key_index]["class"] = elem_class
                    raster.element_meta[key_index]["confidence"] = confidence
                    raster.element_meta[key_index]["occluder"] = occlusion_allowed

                # Track element classification and confidence in strategy diagnostics
                if strategy_diag is not None:
                    try:
                        strategy_diag.record_element_classification(
                            elem_id=elem_id,
                            elem_class=elem_class,
                            category=category
                        )

                        # Phase 2.2: Track confidence level (HIGH, MEDIUM, LOW)
                        if confidence is not None:
                            strategy_diag.record_confidence(
                                elem_id=elem_id,
                                confidence=confidence,
                                category=category
                            )
                    except Exception as e:
                        if diag is not None:
                            diag.error(
                                phase="pipeline",
                                callsite="_occlusion_allowed",
                                message="Exception in _occlusion_allowed: {}".format(e),
                                exc=e,
                            )
                # DEBUG: Log classification for diagonal-looking elements (first 10)
                if not hasattr(render_model_front_to_back, '_classify_debug_count'):
                    render_model_front_to_back._classify_debug_count = 0
                if render_model_front_to_back._classify_debug_count < 10:
                    # Diagonal elements likely have similar width/height
                    ratio = max(cls_w_cells, cls_h_cells) / max(min(cls_w_cells, cls_h_cells), 0.001)
                    if 5 < ratio < 50:  # Likely LINEAR diagonal
                        try:
                            print("[DEBUG classify] Elem {}: {}x{} cells → class={}, category='{}'".format(
                                elem_id, width_cells, height_cells, elem_class, category))
                            render_model_front_to_back._classify_debug_count += 1
                        except Exception as e:
                            if diag is not None:
                                diag.error(
                                    phase="pipeline",
                                    callsite="_occlusion_allowed",
                                    message="Exception in _occlusion_allowed: {}".format(e),
                                    exc=e,
                                )
                aabb_area_cells = aabb_w_cells * aabb_h_cells
                grid_area = raster.W * raster.H

                # World-units-per-cell (ft). Prefer cfg override if present.
                cell_size_world = getattr(cfg, "cell_size_world_ft", None)
                if cell_size_world is None:
                    cell_size_world = getattr(raster, "cell_size", 1.0)

                from .core.geometry import tier_a_is_ambiguous
                tier_a_ambig = tier_a_is_ambiguous(
                    minor_cells, aabb_area_cells, grid_area, cell_size_world, cfg
                )

                # Tier-B proxy path (geometry-based sampling)
                if tier_a_ambig:
                    from .revit.tierb_proxy import sample_element_uvw_points
                    uvw_pts = sample_element_uvw_points(elem, view, vb, cfg)

                    if uvw_pts:
                        points_uv = [(u, v) for (u, v, w) in uvw_pts]

                        from .core.hull import convex_hull_uv
                        hull_uv = convex_hull_uv(points_uv)

                        from .core.footprint import HullFootprint
                        footprint = HullFootprint(hull_uv, raster)
                        footprint_tile_count = len(footprint.tiles(raster.tile))

                        # Minimum sampled W becomes the conservative depth for early-out + stamping
                        elem_min_w = min(w for (_, _, w) in uvw_pts)

                # Stage 2: depth-aware early-out using chosen footprint (bbox or hull)
                rejected_tiles = max(0, fp_tile_count - footprint_tile_count)

                if _tiles_fully_covered_and_nearer(raster.tile, footprint, elem_min_w, tracker=occlusion_tracker):
                    skipped += 1
                    occlusion_tracker.record_bbox_rejection()
                    _sub_t["raster_depth_test_ms"] += _perf_ms(_t0_depth, _perf_now())
                    continue

                if rejected_tiles > 0:
                    occlusion_tracker.record_partial_occlusion(rejected_tiles)

                # Stage 3: conservative stamping
                #
                # IMPORTANT: Do NOT write occlusion here based on a bbox footprint.
                # Bbox-footprint occlusion creates false rectangular masks for diagonal/rotated geometry.
                #
                # Occlusion truth is written only by the actual rasterization path
                # (e.g., rasterize_areal_loops -> raster.rasterize_silhouette_loops).
                #
                # This block intentionally performs no stamping.
                pass

                # Note: TINY/LINEAR elements intentionally NOT stamped here
                # They will be rendered via:
                #   1. Silhouette extraction (preferred)
                #   2. OBB fallback (if silhouette fails)
                #   3. AABB fallback (if OBB fails)

        except Exception as e:
            # Must be observable, and must remain conservative (do not skip element).
            # Early-out is an optimization; failures must not change raster results.
            if diag is not None:
                try:
                    view_id = getattr(getattr(view, "Id", None), "IntegerValue", None)
                    elem_id = getattr(getattr(elem, "Id", None), "IntegerValue", None)
                    dedupe_key = "early_out_failed|{}".format(view_id)
                    diag.debug_dedupe(
                        dedupe_key=dedupe_key,
                        phase="pipeline",
                        callsite="render_model_front_to_back.early_out",
                        message="Early-out/stamp block failed; continuing without early-out",
                        view_id=view_id,
                        elem_id=elem_id,
                        extra={
                            # doc_key is not defined in this scope; never allow diagnostics to raise
                            "doc_key": None,
                            "exc": str(e),
                        },
                    )
                except Exception as e:
                    if diag is not None:
                        diag.error(
                            phase="pipeline",
                            callsite="_occlusion_allowed",
                            message="Exception in _occlusion_allowed: {}".format(e),
                            exc=e,
                        )
                    # Diagnostics must never throw.
        _sub_t["raster_depth_test_ms"] += _perf_ms(_t0_depth, _perf_now())

        # Rasterize silhouette loops if we have them
        _t0_cell = _perf_now()
        if loops:
            # DIAGNOSTIC: Stage 3 - Right before rasterization
            if source_type == "LINK" and processed < 3:  # Only first 3 LINK elements
                try:
                    _diagnose_link_geometry_transform(elem, world_transform, vb, "STAGE3_BEFORE_RASTER")
                    # Also print first few loop points to see if they're in correct space
                    if loops and len(loops) > 0:
                        first_loop = loops[0]
                        pts = first_loop.get('points', [])
                        if pts and len(pts) > 0:
                            print("First loop point (UV): ({:.3f}, {:.3f})".format(pts[0][0], pts[0][1]))
                except Exception as diag_e:
                    print("[DEBUG] Diagnostic failed at stage 3: {}".format(diag_e))

            # Get strategy from extraction (already set above) or from loop metadata
            if strategy is None and len(loops) > 0:
                strategy = loops[0].get('strategy', 'unknown')

            # PHASE 2.2: Use rasterize_areal_loops() for AREAL elements
            # This handles confidence-based occlusion (HIGH occludes, MEDIUM/LOW don't)
            if elem_class == "AREAL":
                try:
                    success, filled = rasterize_areal_loops(
                        loops=loops,
                        raster=raster,
                        key_index=key_index,
                        elem_depth=elem_depth,
                        source_type=source_type,
                        confidence=confidence,
                        strategy=strategy,
                        elem_id=elem_id,
                        category=category
                    )

                    if success:
                        silhouette_success += 1
                        processed += 1
                        _sub_t["raster_cell_write_ms"] += _perf_ms(_t0_cell, _perf_now())
                        occlusion_tracker.check_saturation(raster.tile, processed)
                        continue
                    else:
                        # Rasterization failed, fall through to bbox fallback
                        if processed < 10:
                            print("[DEBUG] AREAL rasterization failed for element {} ({}), falling through to bbox".format(elem_id, category))

                except Exception as e:
                    # Rasterization failed, fall through to bbox fallback
                    if processed < 10:
                        print("[DEBUG] AREAL rasterization exception for element {} ({}): {}".format(elem_id, category, e))
                    pass

            # TINY/LINEAR: Use traditional rasterization (no confidence-based occlusion)
            else:
                try:
                    open_loops = []
                    closed_loops = []
                    for lp in loops:
                        if lp.get("open", False):
                            open_loops.append(lp)
                        else:
                            closed_loops.append(lp)

                    filled = 0

                    # First: rasterize closed loops (fills/occlusion)
                    if closed_loops:
                        try:
                            filled += raster.rasterize_silhouette_loops(
                                closed_loops, key_index, depth=elem_depth, source=source_type
                            )

                            if filled == 0 and processed < 10:
                                print("[DEBUG RASTER FAIL] Element {} closed loops returned 0 filled (loops={}, source={})".format(
                                    elem_id, len(closed_loops), source_type))
                        except Exception as e:
                            if processed < 10:
                                print("[DEBUG RASTER EXCEPT] Element {} rasterization exception: {}".format(elem_id, e))
                            pass

                    # Second: rasterize open polylines (edges)
                    open_polyline_success = False
                    if open_loops:
                        try:
                            filled += raster.rasterize_open_polylines(
                                open_loops, key_index, depth=elem_depth, source=source_type
                            )
                            # CRITICAL: Open polylines succeed even if filled=0
                            # (Bresenham draws edges, doesn't "fill" cells like closed loops)
                            if len(open_loops) > 0:
                                open_polyline_success = True
                        except Exception as e:
                            if diag is not None:
                                diag.error(
                                    phase="pipeline",
                                    callsite="_occlusion_allowed",
                                    message="Exception in _occlusion_allowed: {}".format(e),
                                    exc=e,
                                )
                    # Check for any successful rendering (filled cells OR open polylines drawn)
                    if filled > 0 or open_polyline_success:
                        # Update confidence if needed (TINY/LINEAR use simple model)
                        if confidence is None or confidence == CONF_LOW:
                            confidence = CONF_HIGH if filled > 0 else CONF_LOW

                        if key_index < len(raster.element_meta):
                            raster.element_meta[key_index]["strategy"] = strategy
                            raster.element_meta[key_index]["confidence"] = confidence
                            raster.element_meta[key_index]["occluder"] = _occlusion_allowed(
                                elem_class,
                                confidence,
                            )
                            if open_polyline_success and filled == 0:
                                raster.element_meta[key_index]["open_polyline_only"] = True

                        silhouette_success += 1
                        processed += 1
                        _sub_t["raster_cell_write_ms"] += _perf_ms(_t0_cell, _perf_now())
                        occlusion_tracker.check_saturation(raster.tile, processed)
                        continue

                    else:
                        if processed < 10:
                            print("[DEBUG] Element {} loops exist but no successful rendering, falling through to bbox".format(elem_id))

                except Exception as e:
                    # Rasterization failed, fall through to bbox fallback
                    if processed < 10:
                        print("[DEBUG] Rasterization exception for element {} ({}): {}".format(elem_id, category, e))
                    pass

        # CRITICAL: Check if silhouette rendering already succeeded
        # This section is ONLY for elements that failed silhouette extraction
        # (most successful cases already hit 'continue' above, this is defensive)
        if key_index < len(raster.element_meta):
            strategy_used = raster.element_meta[key_index].get('strategy')
            if strategy_used and strategy_used != 'unknown':
                # Element already successfully rendered via silhouette
                processed += 1
                _sub_t["raster_cell_write_ms"] += _perf_ms(_t0_cell, _perf_now())
                occlusion_tracker.check_saturation(raster.tile, processed)
                continue

        # Note: AREAL diagnostic tracking is now handled inside extract_areal_geometry()
        # No need for additional tracking here

        # Fallback: AABB-only proxy (skip OBB polygon generation entirely)
        obb_success = False
        obb_error = "skipped (proxy-only AABB)"
        aabb_success = False
        aabb_error = None

        # Ultimate fallback: axis-aligned rect (AABB)
        try:
            rect = elem_wrapper.get("uv_bbox_rect")

            # Force a rect attempt here (AABB last resort depends on it).
            if rect is None:
                rect = _project_element_bbox_to_cell_rect(
                    elem,
                    vb,
                    raster,
                    bbox=elem_wrapper.get("bbox"),
                    diag=diag,
                    view=view,
                )

            if rect is None:
                aabb_error = "CellRect unavailable (bbox missing/unprojectable)"
            elif rect.empty:
                aabb_error = "CellRect is empty (element outside bounds?)"

            # Ultimate fallback: axis-aligned rect (if OBB fails)
            try:
                rect = elem_wrapper.get("uv_bbox_rect")

                # Force a rect attempt here (AABB last resort depends on it).
                if rect is None:
                    rect = _project_element_bbox_to_cell_rect(
                        elem,
                        vb,
                        raster,
                        bbox=elem_wrapper.get("bbox"),
                        diag=diag,
                        view=view,
                    )

                if rect is None:
                    aabb_error = "CellRect unavailable (bbox missing/unprojectable)"
                elif rect.empty:
                    aabb_error = "CellRect is empty (element outside bounds?)"
                else:
                    # AABB fallback: proxy edges only (NO occlusion, NO filled mask)
                    filled_count = 0

                    # AABB fallback is LOW confidence by definition; it must never write occlusion.
                    if key_index < len(raster.element_meta):
                        raster.element_meta[key_index]["confidence"] = CONF_LOW
                        raster.element_meta[key_index]["occluder"] = False

                    # Stamp boundary proxy edges
                    for i, j in rect.cells():
                        is_boundary = (
                            i == rect.i_min or i == rect.i_max or
                            j == rect.j_min or j == rect.j_max
                        )
                        if is_boundary:
                            idx = raster.get_cell_index(i, j)
                            if idx is not None:
                                raster.stamp_proxy_edge_idx(idx, key_index, depth=elem_depth)

                    # Tag element metadata with axis-aligned fallback strategy
                    if key_index < len(raster.element_meta):
                        raster.element_meta[key_index]["strategy"] = "aabb_fallback"
                        raster.element_meta[key_index]["filled_cells"] = filled_count
                        raster.element_meta[key_index]["aabb_used"] = True
                        if obb_error:
                            raster.element_meta[key_index]["obb_error"] = obb_error

                    bbox_fallback += 1
                    fallback_bbox_hits += 1
                    processed += 1
                    aabb_success = True


            except Exception as e:
                aabb_error = "AABB fallback failed: {0}".format(e)

            if not aabb_success:
                # Complete failure - tag element with error info
                if key_index < len(raster.element_meta):
                    raster.element_meta[key_index]['obb_error'] = obb_error
                    raster.element_meta[key_index]['strategy'] = 'FAILED'
                    raster.element_meta[key_index]['aabb_error'] = aabb_error

                skipped += 1
                if skipped <= 10:
                    print("[ERROR] Element {0} ({1}) from {2} completely failed:".format(
                        elem_id, category, source_type))
                    print("  OBB error: {0}".format(obb_error))
                    print("  AABB error: {0}".format(aabb_error))

        except Exception as e:
            # Catastrophic failure
            try:
                if key_index < len(raster.element_meta):
                    raster.element_meta[key_index]['strategy'] = 'CATASTROPHIC_FAILURE'
                    raster.element_meta[key_index]['error'] = str(e)
            except Exception as e:
                if diag is not None:
                    diag.error(
                        phase="pipeline",
                        callsite="_occlusion_allowed",
                        message="Exception in _occlusion_allowed: {}".format(e),
                        exc=e,
                    )
            skipped += 1

            if skipped <= 10:
                try:
                    safe_elem_id = getattr(getattr(elem, "Id", None), "IntegerValue", None)
                except Exception as e:
                    if diag is not None:
                        diag.error(
                            phase="pipeline",
                            callsite="_occlusion_allowed",
                            message="Exception in _occlusion_allowed: {}".format(e),
                            exc=e,
                        )
                    safe_elem_id = None
                print("[ERROR] vop.pipeline: Catastrophic failure for element {0}: {1}".format(safe_elem_id, e))

            # Record structured diagnostic if available
            try:
                if diag is not None:
                    diag.error(
                        phase="pipeline",
                        callsite="render_model_front_to_back",
                        message="Catastrophic failure processing element",
                        exc=e,
                        view_id=getattr(getattr(view, "Id", None), "IntegerValue", None),
                        elem_id=getattr(getattr(elem, "Id", None), "IntegerValue", None),
                        extra={"doc_key": doc_key if "doc_key" in locals() else None},
                    )
            except Exception as e:
                if diag is not None:
                    diag.error(
                        phase="pipeline",
                        callsite="_occlusion_allowed",
                        message="Exception in _occlusion_allowed: {}".format(e),
                        exc=e,
                    )
            # Continue with remaining elements
            _sub_t["raster_cell_write_ms"] += _perf_ms(_t0_cell, _perf_now())
            occlusion_tracker.check_saturation(raster.tile, processed)
            continue

        # Normal path (AABB fallback completed without catastrophic exception)
        _sub_t["raster_cell_write_ms"] += _perf_ms(_t0_cell, _perf_now())
        occlusion_tracker.check_saturation(raster.tile, processed)

    _sub_t["raster_element_iter_ms"] = _perf_ms(_t0_elem_iter, _perf_now())

    # Phase 4.5: Ambiguity detection (selective z-buffer prep)
    # Build tile bins and detect ambiguous tiles where depth conflicts exist
    if getattr(cfg, 'enable_ambiguity_detection', True):
        try:
            tile_bins = _bin_elements_to_tiles(expanded_elements, raster)
            ambiguous_tiles = _get_ambiguous_tiles(tile_bins, cfg)

            # TODO: Phase 4.5 triangle resolution will go here
            # For now, just log ambiguous tile count
            if getattr(cfg, 'debug_ambiguous_tiles', False) and ambiguous_tiles:
                print("[DEBUG] Ambiguous tiles detected: {0}".format(len(ambiguous_tiles)))
                print("[DEBUG] These tiles have depth conflicts and may need triangle resolution")

        except Exception as e:
            print("[WARN] vop.pipeline: Ambiguity detection failed: {0}".format(e))

    # Log summary
    if processed > 0:
        print("[INFO] vop.pipeline: Processed {0} elements ({1} silhouette, {2} bbox fallback)".format(
            processed, silhouette_success, bbox_fallback))

    # Finalize occlusion diagnostics (coverage + ROI)
    try:
        occlusion_tracker.finalize(raster.tile)
    except Exception as e:
        if diag is not None:
            diag.error(
                phase="pipeline",
                callsite="render_model_front_to_back",
                message="Exception finalizing occlusion diagnostics: {}".format(e),
                exc=e,
            )

    # Persist view-volume metric for export/diagnostics
    try:
        raster.skipped_outside_view_volume = int(skipped_outside_view_volume)
    except Exception as e:
        if diag is not None:
            diag.error(
                phase="pipeline",
                callsite="_occlusion_allowed",
                message="Exception in _occlusion_allowed: {}".format(e),
                exc=e,
            )
    if skipped > 0:
        print("[WARN] vop.pipeline: Skipped {0} elements due to errors".format(skipped))

    # Print depth test statistics
    if raster.depth_test_attempted > 0:
        win_rate = 100.0 * raster.depth_test_wins / raster.depth_test_attempted
        reject_rate = 100.0 * raster.depth_test_rejects / raster.depth_test_attempted
        print("[INFO] vop.pipeline: Depth tests: {0} attempted, {1} wins ({2:.1f}%), {3} rejects ({4:.1f}%)".format(
            raster.depth_test_attempted, raster.depth_test_wins, win_rate,
            raster.depth_test_rejects, reject_rate))

    # Debug dump occlusion layers if requested
    if cfg.debug_dump_occlusion:
        try:
            import os
            import re

            def _makedirs(path):
                if not path:
                    return
                if os.path.isdir(path):
                    return
                try:
                    os.makedirs(path)
                except Exception as e:
                    if diag is not None:
                        diag.error(
                            phase="pipeline",
                            callsite="_makedirs",
                            message="Exception in _makedirs: {}".format(e),
                            exc=e,
                        )
                    # If it already exists due to race/permissions quirks, ignore.
                    if not os.path.isdir(path):
                        raise

            view_name = re.sub(r'[<>:"/\\|?*]', "_", getattr(view, "Name", "view"))
            view_id = getattr(getattr(view, "Id", None), "IntegerValue", 0)

            if getattr(cfg, "debug_dump_prefix", None):
                prefix = cfg.debug_dump_prefix
                dump_dir = os.path.dirname(prefix)
                _makedirs(dump_dir)
            else:
                dump_dir = getattr(cfg, "debug_dump_path", None)
                base_name = "occlusion_{0}_{1}".format(view_name, view_id)

                if dump_dir:
                    _makedirs(dump_dir)
                    prefix = os.path.join(dump_dir, base_name)
                else:
                    prefix = base_name  # explicit CWD fallback

            raster.dump_occlusion_debug(prefix)

        except Exception as e:
            print("[WARN] vop.pipeline: Failed to dump occlusion debug: {0}".format(e))

    # Export strategy diagnostics if enabled
    if strategy_diag is not None:
        try:
            import os
            import re

            # Print summary to console
            print("\n" + "=" * 80)
            print("STRATEGY DIAGNOSTICS SUMMARY (View: {})".format(
                getattr(view, "Name", "Unknown")))
            print("=" * 80)
            strategy_diag.print_summary()

            # Export CSV if requested
            if getattr(cfg, "export_strategy_diagnostics", False):
                # Build output path
                view_name = re.sub(r'[<>:"/\\|?*]', "_", getattr(view, "Name", "view"))
                view_id = getattr(getattr(view, "Id", None), "IntegerValue", 0)

                dump_dir = getattr(cfg, "debug_dump_path", None)
                if dump_dir:
                    try:
                        if not os.path.isdir(dump_dir):
                            os.makedirs(dump_dir)
                    except Exception as e:
                        if diag is not None:
                            diag.error(
                                phase="pipeline",
                                callsite="_makedirs",
                                message="Exception in _makedirs: {}".format(e),
                                exc=e,
                            )
                    # Per-element CSV (existing)
                    csv_filename = "strategy_diagnostics_{0}_{1}.csv".format(view_name, view_id)
                    csv_path = os.path.join(dump_dir, csv_filename)

                    # Category summary CSV (Phase 3.1)
                    cat_csv_filename = "category_summary_{0}_{1}.csv".format(view_name, view_id)
                    cat_csv_path = os.path.join(dump_dir, cat_csv_filename)
                else:
                    csv_path = "strategy_diagnostics_{0}_{1}.csv".format(view_name, view_id)
                    cat_csv_path = "category_summary_{0}_{1}.csv".format(view_name, view_id)

                # Export per-element CSV
                strategy_diag.export_to_csv(csv_path)
                print("\nDiagnostics exported:")
                print("  Elements: {}".format(csv_path))

                # Export category summary CSV (Phase 3.1)
                try:
                    strategy_diag.export_category_summary_csv(cat_csv_path)
                    print("  Categories: {}".format(cat_csv_path))
                except Exception as cat_e:
                    print("  [WARN] Failed to export category summary: {}".format(cat_e))

        except Exception as e:
            print("[WARN] vop.pipeline: Failed to export strategy diagnostics: {0}".format(e))

    # Round sub-timings for consistency
    for _k in _sub_t:
        _sub_t[_k] = round(_sub_t[_k], 3)

    _sub_t[TIMING_KEYS["GEOM_EXTRACT_MS"]] = round(float(_sub_t.get("raster_geom_extract_ms", 0.0)), 3)
    _sub_t["element_count"] = int(view_diag.get("total_elements", 0) or 0)
    _sub_t["areal_count"] = int(areal_count)
    _sub_t["linear_count"] = int(linear_count)
    _sub_t["tiny_count"] = int(tiny_count)
    _sub_t["fallback_bbox_hits"] = int(fallback_bbox_hits)
    return {"timings": _sub_t, "diagnostics": view_diag, "occlusion_tracker": occlusion_tracker}


def _is_supported_2d_view(view, diag=None):
    """Check if view type is supported (2D-ish views only).

    Args:
        view: Revit View

    Returns:
        True if view is supported (2D orthographic), False otherwise

    Commentary:
        ✔ Supports: Floor plans, ceiling plans, sections, elevations, area plans, drafting views
        ✘ Rejects: 3D views, schedules, sheets, legends
    """
    from Autodesk.Revit.DB import ViewType

    # Supported 2D view types
    supported_types = [
        ViewType.FloorPlan,
        ViewType.CeilingPlan,
        ViewType.Elevation,
        ViewType.Section,
        ViewType.AreaPlan,
        ViewType.EngineeringPlan,
        ViewType.Detail,
        ViewType.DraftingView,  # Drafting views (detail sheets, assembly drawings)
    ]

    try:
        view_type = view.ViewType
        return view_type in supported_types
    except Exception as e:
        # If we can't determine type, reject it. Record diagnostic to avoid silent misclassification.
        try:
            if diag is not None:
                diag.warn(
                    phase="pipeline",
                    callsite="_is_supported_2d_view",
                    message="Failed to read view.ViewType; rejecting view",
                    view_id=getattr(getattr(view, "Id", None), "IntegerValue", None),
                    extra={"view_name": getattr(view, "Name", None), "exc": str(e)},
                )
        except Exception as e:
            if diag is not None:
                diag.error(
                    phase="pipeline",
                    callsite="_is_supported_2d_view",
                    message="Exception in _is_supported_2d_view: {}".format(e),
                    exc=e,
                )
        return False

        return False

def _intersects_crop_volume(aabb_min, aabb_max, crop_min, crop_max):
    """Test if AABB intersects crop volume with boundary tolerance.

    Applies small epsilon to boundaries to include coplanar geometry,
    matching Revit's display pipeline behavior where elements exactly
    at crop boundaries are visible.

    Args:
        aabb_min: (w_min, x_min, y_min) tuple of element AABB minimum coords
        aabb_max: (w_max, x_max, y_max) tuple of element AABB maximum coords
        crop_min: (w_min, x_min, y_min) tuple of crop volume minimum
        crop_max: (w_max, x_max, y_max) tuple of crop volume maximum

    Returns:
        True if AABB intersects crop volume (within tolerance), False otherwise
    """
    return not (
        aabb_max[0] < crop_min[0] - BOUNDARY_TOLERANCE or
        aabb_min[0] > crop_max[0] + BOUNDARY_TOLERANCE or
        aabb_max[1] < crop_min[1] - BOUNDARY_TOLERANCE or
        aabb_min[1] > crop_max[1] + BOUNDARY_TOLERANCE or
        aabb_max[2] < crop_min[2] - BOUNDARY_TOLERANCE or
        aabb_min[2] > crop_max[2] + BOUNDARY_TOLERANCE
    )


def _should_skip_outside_view_volume(depth_range, W0, Wmax):
    """Pure predicate: True iff element depth_range does NOT overlap [W0, Wmax].

    Applies BOUNDARY_TOLERANCE to prevent exclusion of elements coplanar
    with crop boundaries, matching Revit's display behavior.

    depth_range: (dmin, dmax) in view-space W.
    W0/Wmax: view-space W interval, both finite numbers.
    """
    if depth_range is None:
        return False

    try:
        dmin, dmax = depth_range
    except Exception as e:
        return False

    if (dmin is None) or (dmax is None):
        return False

    try:
        dmin = float(dmin)
        dmax = float(dmax)
        W0 = float(W0)
        Wmax = float(Wmax)
    except Exception as e:
        return False

    if not (math.isfinite(dmin) and math.isfinite(dmax) and math.isfinite(W0) and math.isfinite(Wmax)):
        return False

    if dmin > dmax:
        dmin, dmax = dmax, dmin

    if W0 > Wmax:
        W0, Wmax = Wmax, W0

    return (dmax < W0 - BOUNDARY_TOLERANCE) or (dmin > Wmax + BOUNDARY_TOLERANCE)

def _tiles_fully_covered_and_nearer(tile_map, footprint, elem_min_w, tracker=None):
    """Check if all tiles overlapping rect are fully covered AND nearer than element.

    Args:
        tile_map: TileMap acceleration structure
        footprint: footprint object with tiles(tile_map)
        elem_min_w: Element's minimum W-depth (view-space depth)

    Returns:
        True if element is guaranteed occluded (safe to skip)

    Commentary:
        Early-out occlusion test using tile-level W-depth buffer.
        Element is occluded if ALL tiles overlapping its footprint are:
        1. Fully filled (no empty cells)
        2. Nearer than element's minimum W-depth (w_min_tile < elem_min_w)
    """
    t0 = _perf_now()
    tiles = footprint.tiles(tile_map)

    for t in tiles:
        # Check if tile is fully filled
        if not tile_map.is_tile_full(t):
            if tracker is not None:
                tracker.record_occlusion_test_ms(_perf_ms(t0, _perf_now()))
            return False

        # SAFE occlusion: ALL cells in tile must be nearer => tile max depth < elem_min_w
        if tile_map.w_max_tile[t] >= elem_min_w:
            if tracker is not None:
                tracker.record_occlusion_test_ms(_perf_ms(t0, _perf_now()))
            return False

    if tracker is not None:
        tracker.record_occlusion_test_ms(_perf_ms(t0, _perf_now()))
    return True


def _bin_elements_to_tiles(elem_wrappers, raster):
    """Bin elements to tiles based on their projected bbox.

    Args:
        elem_wrappers: List of element wrapper dicts with enriched data
        raster: ViewRaster with tile map

    Returns:
        Dict {tile_id: [elem_wrappers]} mapping tiles to elements

    Commentary:
        Each element is added to all tiles its uv_bbox_px intersects.
        Used for ambiguity detection and selective z-buffer resolution.
    """
    tile_bins = {}

    for wrapper in elem_wrappers:
        rect = wrapper.get('uv_bbox_rect')
        if rect is None or rect.empty:
            continue

        # Get all tiles overlapping this element's bbox
        tiles = raster.tile.get_tiles_for_rect(rect.i_min, rect.j_min, rect.i_max, rect.j_max)

        for tile_id in tiles:
            if tile_id not in tile_bins:
                tile_bins[tile_id] = []
            tile_bins[tile_id].append(wrapper)

    return tile_bins


def _tile_has_depth_conflict(elem_wrappers, cfg=None):
    """Check if tile has depth range conflicts (ambiguity) using a sweep (O(k log k)).

    Returns True if any overlapping depth ranges exist.
    """
    if len(elem_wrappers) < 2:
        return False

    cap = int(getattr(cfg, "tile_wrapper_cap", 250)) if cfg is not None else 250
    if len(elem_wrappers) > cap:
        return True

    ranges = []
    for w in elem_wrappers:
        dmin, dmax = w.get("depth_range", (float("inf"), float("inf")))
        # Skip invalid ranges (conservative): treat as ambiguous
        if dmin == 0 and dmax == 0:
            return True
        if dmin > dmax:
            dmin, dmax = dmax, dmin
        ranges.append((dmin, dmax))

    ranges.sort(key=lambda t: t[0])

    max_dmax = ranges[0][1]
    for dmin, dmax in ranges[1:]:
        if dmin < max_dmax:
            return True
        if dmax > max_dmax:
            max_dmax = dmax

    return False


def _get_ambiguous_tiles(tile_bins, cfg):
    """Identify tiles with depth conflicts that need triangle resolution.

    Args:
        tile_bins: Dict {tile_id: [elem_wrappers]}
        cfg: Config object with debug flags

    Returns:
        List of tile_ids that are ambiguous

    Commentary:
        Ambiguous tiles are those where depth-based ordering is insufficient.
        These tiles will use triangle z-buffer resolution in Phase 4.5.
    """
    ambiguous = []

    for tile_id, elems in tile_bins.items():
        if _tile_has_depth_conflict(elems, cfg=cfg):
            ambiguous.append(tile_id)

    # Debug logging
    if getattr(cfg, 'debug_ambiguous_tiles', False):
        print("[DEBUG] Ambiguous tiles: {0} / {1} total tiles ({2:.1f}%)".format(
            len(ambiguous), len(tile_bins),
            100.0 * len(ambiguous) / len(tile_bins) if tile_bins else 0
        ))

    return ambiguous


def _render_proxy_element(elem, transform, view, raster, rect, mode, key_index, cfg):
    """Render TINY/LINEAR element: proxy edges + optional minimal mask.

    Commentary:
        ✔ Save heavy work; no triangle raster
        ✔ Do not write broad proxy fill into modelMask/zMin (avoids false occlusion)
        ✔ Proxy edges go to separate layer
        ✔ Optional minimal proxy mask for "Over any model presence"
    """
    if mode == Mode.TINY:
        proxy = make_uv_aabb(rect)
    else:  # Mode.LINEAR
        proxy = make_obb_or_skinny_aabb(elem, transform, rect, view, raster)

    # Stamp proxy edges
    _stamp_proxy_edges(proxy, key_index, raster)

    # Optional minimal proxy mask
    if cfg.over_model_includes_proxies and cfg.proxy_mask_mode == "minmask":
        if mode == Mode.TINY:
            _mark_rect_center_cell(rect, raster)
        else:  # LINEAR
            _mark_thin_band_along_long_axis(rect, raster)


def _stamp_proxy_edges(proxy, key_index, raster):
    """Stamp proxy edges into model_proxy_key layer.

    NOTE: Edge rasterization is not currently implemented.
    This function is only called when cfg.proxy_mask_mode="edges" (non-default).
    The default "minmask" mode works correctly and does not use this function.

    Args:
        proxy: UV_AABB or OBB
        key_index: Element metadata index
        raster: ViewRaster (modified in-place)
    """
    # Edge mode not implemented - only minmask mode is supported
    # Default config uses minmask, so this is rarely reached in production
    pass


def _mark_rect_center_cell(rect, raster):
    """Mark center cell of rect in model_proxy_mask."""
    i_center, j_center = rect.center_cell()
    idx = raster.get_cell_index(i_center, j_center)
    if idx is not None:
        raster.model_proxy_mask[idx] = True


def _mark_thin_band_along_long_axis(rect, raster):
    """Mark thin band along long axis of rect in model_proxy_mask."""
    if rect.width_cells > rect.height_cells:
        # Horizontal band
        j_center = (rect.j_min + rect.j_max) // 2
        for i in range(rect.i_min, rect.i_max + 1):
            idx = raster.get_cell_index(i, j_center)
            if idx is not None:
                raster.model_proxy_mask[idx] = True
    else:
        # Vertical band
        i_center = (rect.i_min + rect.i_max) // 2
        for j in range(rect.j_min, rect.j_max + 1):
            idx = raster.get_cell_index(i_center, j)
            if idx is not None:
                raster.model_proxy_mask[idx] = True


def export_view_raster(view, raster, cfg, diag=None, timings=None, strategy_diag=None, metrics_payload=None):
    """Export view raster to dictionary for JSON serialization.

    Args:
        view: Revit View
        raster: ViewRaster
        cfg: Config
        diag: Optional diagnostics
        timings: Optional timings dict
        strategy_diag: Optional StrategyDiagnostics instance

    Returns:
        Dictionary with all view data
    """
    num_filled = sum(1 for m in raster.model_mask if m)

    # PR8: channel summary (auditable, low-noise)
    occlusion_cells = int(num_filled)

    model_ink_edge_cells = 0
    try:
        model_ink_edge_cells = sum(1 for k in raster.model_edge_key if k != -1)
    except Exception as e:
        if diag is not None:
            diag.error(
                phase="pipeline",
                callsite="export_view_raster",
                message="Exception in export_view_raster: {}".format(e),
                exc=e,
            )
        model_ink_edge_cells = 0

    proxy_edge_cells = 0
    try:
        proxy_edge_cells = sum(1 for k in raster.model_proxy_key if k != -1)
    except Exception as e:
        if diag is not None:
            diag.error(
                phase="pipeline",
                callsite="export_view_raster",
                message="Exception in export_view_raster: {}".format(e),
                exc=e,
            )
        proxy_edge_cells = 0

    # PR8: dominance detector (once per view)
    try:
        thr_ink = float(getattr(cfg, "dominance_threshold_model_ink", 0.75))
        thr_occ = float(getattr(cfg, "dominance_threshold_occlusion", 0.90))

        # Find max per-element contributions (counters are updated where we have key_index attribution)
        max_ink = 0
        max_occ = 0
        max_ink_meta = None
        max_occ_meta = None
        for meta in getattr(raster, "element_meta", []) or []:
            ink = int(meta.get("model_edge_cells", 0) or 0)
            occ = int(meta.get("occlusion_cells", 0) or 0)
            if ink > max_ink:
                max_ink = ink
                max_ink_meta = meta
            if occ > max_occ:
                max_occ = occ
                max_occ_meta = meta

        if diag is not None:
            diag.debug(
                phase="pipeline",
                callsite="export_view_raster.channel_summary",
                message="Per-view channel write summary",
                view_id=view.Id.IntegerValue,
                extra={
                    "occlusion_cells": occlusion_cells,
                    "model_ink_edge_cells": model_ink_edge_cells,
                    "proxy_edge_cells": proxy_edge_cells,
                    "timings": (dict(timings) if timings is not None else None),
                },
            )

            # Warn once per view on dominance
            if model_ink_edge_cells > 0 and (max_ink / float(model_ink_edge_cells)) >= thr_ink:
                diag.warn(
                    phase="pipeline",
                    callsite="export_view_raster.dominance",
                    message="Single element dominates model ink edges (possible regression)",
                    view_id=view.Id.IntegerValue,
                    extra={
                        "threshold": thr_ink,
                        "max_fraction": (max_ink / float(model_ink_edge_cells)),
                        "elem_id": (max_ink_meta or {}).get("elem_id", None),
                        "category": (max_ink_meta or {}).get("category", None),
                        "source_type": (max_ink_meta or {}).get("source_type", None),
                    },
                )

            if occlusion_cells > 0 and (max_occ / float(occlusion_cells)) >= thr_occ:
                diag.warn(
                    phase="pipeline",
                    callsite="export_view_raster.dominance",
                    message="Single element dominates occlusion coverage (possible proxy fill / huge footprint)",
                    view_id=view.Id.IntegerValue,
                    extra={
                        "threshold": thr_occ,
                        "max_fraction": (max_occ / float(occlusion_cells)),
                        "elem_id": (max_occ_meta or {}).get("elem_id", None),
                        "category": (max_occ_meta or {}).get("category", None),
                        "source_type": (max_occ_meta or {}).get("source_type", None),
                    },
                )
    except Exception as e:
        if diag is not None:
            diag.error(
                phase="pipeline",
                callsite="export_view_raster",
                message="Exception in export_view_raster: {}".format(e),
                exc=e,
            )
        # Never allow diagnostics to break export

    return {
        "view_id": view.Id.IntegerValue,
        "view_name": view.Name,
        "view_mode": getattr(raster, "view_mode", None),
        "view_mode_reason": getattr(raster, "view_mode_reason", None),
        "width": int(getattr(raster, "W", 0) or 0),
        "height": int(getattr(raster, "H", 0) or 0),
        "cell_size": raster.cell_size_ft,
        "tile_size": raster.tile.tile_size,
        "total_elements": len(raster.element_meta),
        "filled_cells": num_filled,
        "raster": raster if bool(getattr(cfg, "_is_streaming_mode", False)) else raster.to_dict(),
        "config": cfg.to_dict(),
        "timings": (dict(timings) if timings is not None else None),
        "diagnostics": {
            "diag": (diag.to_dict() if diag is not None else None),
            "bounds": getattr(raster, "bounds_meta", None),
            "num_elements": len(raster.element_meta),
            "num_annotations": len(raster.anno_meta),
            "num_filled_cells": num_filled,
            "occlusion_cells": occlusion_cells,
            "model_ink_edge_cells": model_ink_edge_cells,
            "proxy_edge_cells": proxy_edge_cells,
            "skipped_outside_view_volume": int(getattr(raster, "skipped_outside_view_volume", 0) or 0),
            "timings": (dict(timings) if timings is not None else None),
        },
        "metrics": (metrics_payload or {}).get("metrics"),
        "metrics_validation": (metrics_payload or {}).get("metrics_validation"),
        "metrics_version": (metrics_payload or {}).get("metrics_version"),
        "metrics_manifest_file": (metrics_payload or {}).get("metrics_manifest_file"),
        "metrics_manifest_sha256": (metrics_payload or {}).get("metrics_manifest_sha256"),
        "metrics_validation_mode": (metrics_payload or {}).get("metrics_validation_mode"),
        "strategy_diag": strategy_diag,  # StrategyDiagnostics instance for CSV export
    }
