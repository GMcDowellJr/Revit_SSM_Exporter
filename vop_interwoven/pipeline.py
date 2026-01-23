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


import math
import time

from .config import Config
from .core.raster import ViewRaster, TileMap
from .core.geometry import Mode, classify_by_uv, make_uv_aabb, make_obb_or_skinny_aabb
from .core.math_utils import Bounds2D, CellRect
from .core.silhouette import get_element_silhouette
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
    except:
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
    except Exception:
        return None


def _safe_bool(v):
    try:
        return bool(v)
    except Exception:
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
    except Exception:
        pass
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
    except Exception:
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
        for elem in col:
            try:
                elem_id = getattr(getattr(elem, "Id", None), "IntegerValue", None)
                if elem_id is None:
                    continue

                # Track for CSV export
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
            except Exception:
                continue
    except Exception:
        pass  # Empty list on failure

    # Store element-view relationship for CSV export
    if track_elements is not None:
        try:
            view_id_int = _safe_int(getattr(getattr(view_obj, "Id", None), "IntegerValue", None))
            if view_id_int is not None:
                track_elements[view_id_int] = elem_ids_for_tracking
        except Exception:
            pass

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
        except Exception:
            return None

    sig = {
        "schema": 4,
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
        "discipline": "",
        "phase": "",
        "sheet_number": "",
        "view_template_name": "",
    }

    if view is None:
        return out

    # view_type (readable)
    try:
        vt = getattr(view, "ViewType", None)
        out["view_type"] = "" if vt is None else str(vt)
    except Exception:
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
            except Exception:
                s = None
            if not s:
                try:
                    s = p.AsString()
                except Exception:
                    s = None
            if s:
                out["discipline"] = str(s)

        # Fallback: enum-ish string
        if not out["discipline"]:
            d = getattr(view, "Discipline", None)
            out["discipline"] = "" if d is None else str(d)
    except Exception:
        try:
            d = getattr(view, "Discipline", None)
            out["discipline"] = "" if d is None else str(d)
        except Exception:
            pass

    # phase (readable NAME only)
    try:
        from Autodesk.Revit.DB import BuiltInParameter  # type: ignore
        p = view.get_Parameter(BuiltInParameter.VIEW_PHASE)
        if p is not None:
            eid = None
            try:
                eid = p.AsElementId()
            except Exception:
                eid = None

            if eid is not None and doc is not None:
                try:
                    ph = doc.GetElement(eid)
                    name = getattr(ph, "Name", None)
                    if name:
                        out["phase"] = str(name)
                except Exception:
                    pass
    except Exception:
        pass

    # sheet_number (readable)
    try:
        # Some view types expose SheetNumber directly when placed; else keep blank
        sn = getattr(view, "SheetNumber", None)
        out["sheet_number"] = "" if sn is None else str(sn)
    except Exception:
        pass

    # view_template_name (readable)
    try:
        vtid = getattr(view, "ViewTemplateId", None)
        if vtid is not None and doc is not None:
            try:
                vt_elem = doc.GetElement(vtid)
                name = getattr(vt_elem, "Name", None)
                out["view_template_name"] = "" if name is None else str(name)
            except Exception:
                out["view_template_name"] = ""
    except Exception:
        pass

    return out

def process_document_views(doc, view_ids, cfg, diag=None, root_cache=None):
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
    from .core.diagnostics import Diagnostics

    results = []

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
        except Exception:
            view_cache_dir = None

    # Streaming-only policy: root_cache (single JSON) is the authoritative cache.
    # Disable per-view disk cache to avoid duplicate cache systems and confusion.
    view_cache_enabled = False

    if view_cache_enabled:
        try:
            os.makedirs(view_cache_dir, exist_ok=True)
        except Exception:
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
        except Exception:
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
                except Exception:
                    pass
        except Exception:
            pass

    # Guardrail: cfg must be vop_interwoven.config.Config (attribute-based), not a dict.
    # This prevents silent drift when new code accidentally uses cfg.get(...).
    if isinstance(cfg, dict):
        raise TypeError("cfg must be vop_interwoven.config.Config (not dict)")

    # PR12: bounded geometry cache shared across all views in this call.
    # Scoped to this run to avoid cross-run semantic drift.
    try:
        from .core.cache import LRUCache
        geometry_cache = LRUCache(max_items=getattr(cfg, "geometry_cache_max_items", 0))
    except Exception:
        geometry_cache = None

    # PR13: Document-scoped element cache for bbox reuse across views
    elem_cache = None
    elem_cache_prev = None  # Previous run cache (for change detection)
    elem_cache_path = None
    if getattr(cfg, "use_element_cache", True):
        try:
            from .core.element_cache import ElementCache
            max_items = int(getattr(cfg, "element_cache_max_items", 10000))

            # Determine cache file path (stored with output files)
            if output_dir is not None:
                elem_cache_path = os.path.join(output_dir, ".vop_element_cache.json")
            else:
                elem_cache_path = None

            # Load previous cache if persistence enabled
            if getattr(cfg, "element_cache_persist", True) and elem_cache_path is not None:
                try:
                    elem_cache_prev = ElementCache.load_from_json(elem_cache_path, max_elements=max_items)
                    # Start with previous cache (pre-populated)
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
                        except Exception:
                            pass
                except Exception:
                    # Failed to load - start fresh
                    elem_cache = ElementCache(max_elements=max_items)
            else:
                # No persistence - start fresh
                elem_cache = ElementCache(max_elements=max_items)

        except Exception:
            elem_cache = None  # Graceful degradation

    # Track element-view relationships for CSV export
    view_elements = {}  # view_id -> list of (elem_id, source_id)

    for view_id in view_ids:
        diag = Diagnostics()  # per-view diag
        timings = {}
        t_view0 = _perf_now()

        def _tmark(name, t0, t1):
            if getattr(cfg, "perf_collect_timings", True):
                timings[name] = round(_perf_ms(t0, t1), 3)

        view = None

        try:
            # Convert int to ElementId if needed
            from Autodesk.Revit.DB import ElementId
            elem_id = ElementId(view_id) if isinstance(view_id, int) else view_id

            view = doc.GetElement(elem_id)

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
                except Exception:
                    # Never allow diagnostics logging to fail the pipeline
                    pass

            if view_mode == VIEW_MODE_REJECTED:
                
                t_view1 = _perf_now()
                _tmark("total_ms", t_view0, t_view1)

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
                    except Exception:
                        # Diagnostics must never break the pipeline
                        pass

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
                except Exception:
                    pass

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

                if cached:
                    cached_meta = cached.get("metadata") or {}
                    cached_metrics = cached.get("metrics") or {}

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
                        "view_type": cached_meta.get("view_type", "") or ident.get("view_type", ""),
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
            except Exception:
                pass

            # Create strategy diagnostics tracker if enabled (used by render and CSV export)
            strategy_diag = None
            if getattr(cfg, "export_strategy_diagnostics", False):
                try:
                    from .diagnostics import StrategyDiagnostics
                    strategy_diag = StrategyDiagnostics()
                except Exception:
                    # Graceful degradation: continue without diagnostics
                    pass

            if view_mode == VIEW_MODE_MODEL_AND_ANNOTATION:
                # 2) Broad-phase visible elements
                t0 = _perf_now()
                elements = collect_view_elements(doc, view, raster, diag=diag, cfg=cfg)
                t1 = _perf_now()
                _tmark("collect_ms", t0, t1)
                
                # 3) MODEL PASS
                t0 = _perf_now()
                render_model_front_to_back(doc, view, raster, elements, cfg, diag=diag, geometry_cache=geometry_cache, elem_cache=elem_cache, strategy_diag=strategy_diag)
                t1 = _perf_now()
                _tmark("model_ms", t0, t1)

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
            _tmark("anno_ms", t0, t1)

            # 5) Derive annoOverModel (safe even if model is empty)
            t0 = _perf_now()
            raster.finalize_anno_over_model(cfg)
            t1 = _perf_now()
            _tmark("finalize_ms", t0, t1)

            # 6) Export
            t0 = _perf_now()
            out = export_view_raster(view, raster, cfg, diag=diag, timings=timings, strategy_diag=strategy_diag)

            # Ensure identity fields exist on first-run results so CSV + cache row_payload are complete
            try:
                if isinstance(out, dict):
                    if out.get("view_type") in (None, ""):
                        out["view_type"] = ident.get("view_type", "")
                    if out.get("discipline") in (None, ""):
                        out["discipline"] = ident.get("discipline", "")
                    if out.get("phase") in (None, ""):
                        out["phase"] = ident.get("phase", "")
                    if out.get("sheet_number") in (None, ""):
                        out["sheet_number"] = ident.get("sheet_number", "")
                    if out.get("view_template_name") in (None, ""):
                        out["view_template_name"] = ident.get("view_template_name", "")
            except Exception:
                pass

            t1 = _perf_now()
            _tmark("export_ms", t0, t1)

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
                        _save_cached_view(vid, sig_hex, out)
                        try:
                            out["cache"] = {"view_cache": "MISS_SAVED", "signature": sig_hex, "dir": view_cache_dir}
                            # Attach canonical signature for downstream consumers (streaming/CSV/debug).
                            # Never recompute signature outside this function.
                            try:
                                if out is not None:
                                    c = out.setdefault("cache", {})
                                    if isinstance(c, dict):
                                        c.setdefault("signature", sig_hex)
                            except Exception:
                                pass

                        except Exception:
                            pass
            except Exception:
                pass
            
            t_view1 = _perf_now()
            _tmark("total_ms", t_view0, t_view1)

            # Always expose a wall-clock elapsed seconds for this view, even if timing collection is disabled
            try:
                out["elapsed_sec"] = round((_perf_ms(t_view0, t_view1) / 1000.0), 3)
            except Exception:
                pass

            # Convenience mirror at top-level for callers that don't dive into diagnostics
            try:
                out["timings"] = dict(timings)
            except Exception:
                pass

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
        except Exception:
            pass

    # Phase 2.5: Persistent element cache - save/export/detect changes
    if elem_cache is not None and getattr(cfg, "element_cache_persist", True):
        try:
            # Save cache to JSON for next run
            if elem_cache_path is not None:
                try:
                    metadata = {
                        "timestamp": time.time(),
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
                except Exception:
                    pass

            # Export analysis CSV
            if getattr(cfg, "element_cache_export_csv", True) and output_dir is not None:
                try:
                    csv_path = os.path.join(output_dir, "element_cache_analysis.csv")
                    exported = elem_cache.export_analysis_csv(csv_path, view_elements=view_elements)
                    if exported and diag is not None:
                        diag.info(
                            phase="pipeline",
                            callsite="process_document_views.element_cache_export_csv",
                            message="Exported element cache analysis CSV",
                            extra={"csv_path": csv_path, "elements": len(elem_cache.cache), "views": len(view_elements)}
                        )
                except Exception:
                    pass

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
                        except Exception:
                            pass

                except Exception:
                    pass
        except Exception:
            pass

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
        except Exception:
            pass

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
    except Exception:
        pass

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
        "diagnostics": {
            # Keep only numeric stats, not full metadata lists
            "num_elements": view_result.get("diagnostics", {}).get("num_elements"),
            "num_annotations": view_result.get("diagnostics", {}).get("num_annotations"),
            "num_filled_cells": view_result.get("diagnostics", {}).get("num_filled_cells"),
            "occlusion_cells": view_result.get("diagnostics", {}).get("occlusion_cells"),
            "model_ink_edge_cells": view_result.get("diagnostics", {}).get("model_ink_edge_cells"),
            "proxy_edge_cells": view_result.get("diagnostics", {}).get("proxy_edge_cells"),
            "timings": view_result.get("diagnostics", {}).get("timings"),
        },
        "cache": view_result.get("cache"),
        "config": view_result.get("config"),
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
            # Rasterize closed loops (fills + occlusion)
            if closed_loops:
                try:
                    filled += raster.rasterize_silhouette_loops(
                        closed_loops, key_index, depth=elem_depth, source=source_type
                    )
                except Exception:
                    pass

            # Rasterize open polylines (edges)
            if open_loops:
                try:
                    filled += raster.rasterize_open_polylines(
                        open_loops, key_index, depth=elem_depth, source=source_type
                    )
                    if len(open_loops) > 0:
                        open_polyline_success = True
                except Exception:
                    pass

        # MEDIUM/LOW confidence: Rasterize to proxy layer only (no occlusion)
        else:
            # For MEDIUM/LOW, we still want to show the element but NOT occlude
            # Phase 2.3: Use rasterize_polygon_to_proxy (writes to model_proxy_key, NOT w_occ)
            if closed_loops:
                try:
                    # Rasterize to proxy layer without updating occlusion buffer
                    # This makes the element visible but doesn't block later elements
                    filled += raster.rasterize_polygon_to_proxy(
                        closed_loops, key_index, depth=elem_depth, source=source_type
                    )
                except Exception:
                    # Fallback: if new method doesn't exist, try old method
                    try:
                        filled += raster.rasterize_proxy_loops(
                            closed_loops, key_index, depth=elem_depth, source=source_type
                        )
                    except Exception:
                        # Last resort: regular rasterization but mark as non-occluding
                        try:
                            filled += raster.rasterize_silhouette_loops(
                                closed_loops, key_index, depth=elem_depth, source=source_type
                            )
                            # Explicitly mark as non-occluding
                            if key_index < len(raster.element_meta):
                                raster.element_meta[key_index]["occluder"] = False
                        except Exception:
                            pass

            if open_loops:
                try:
                    filled += raster.rasterize_open_polylines(
                        open_loops, key_index, depth=elem_depth, source=source_type
                    )
                    if len(open_loops) > 0:
                        open_polyline_success = True
                except Exception:
                    pass

        # Mark open-polyline-only rendering in metadata
        if open_polyline_success and filled == 0:
            if key_index < len(raster.element_meta):
                raster.element_meta[key_index]["open_polyline_only"] = True

        # Success if we filled cells OR drew open polylines
        success = (filled > 0) or open_polyline_success
        return (success, filled)

    except Exception:
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

    # Get view basis for transformations
    vb = make_view_basis(view, diag=diag)

    # Resolve explicit view-space W volume once per view (shared by host + link).
    from .revit.view_basis import resolve_view_w_volume
    W0, Wmax, _wvol_meta = resolve_view_w_volume(view, vb, cfg, diag=diag)

    # Persist for export/diagnostics (safe: optional fields)
    try:
        raster.view_w0 = W0
        raster.view_wmax = Wmax
        raster.view_wvol_meta = _wvol_meta
    except Exception:
        pass

    # Expand to include linked/imported elements
    expanded_elements = expand_host_link_import_model_elements(doc, view, elements, cfg, diag=diag, elem_cache=elem_cache)

    # Sort elements front-to-back by depth for proper occlusion
    expanded_elements = sort_front_to_back(expanded_elements, view, raster)

    # Enrich elements with depth range and bbox for ambiguity detection
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
        except Exception:
            wrapper["depth_range"] = (0.0, 0.0)
            wrapper["uv_bbox_rect"] = None

    # Process each element (host + linked)
    processed = 0
    skipped_outside_view_volume = 0
    skipped = 0
    silhouette_success = 0
    bbox_fallback = 0

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
        except Exception:
            pass

        # Fallback: AABB in cell units
        try:
            return (float(rect.width()), float(rect.height()))
        except Exception:
            return (0.0, 0.0)

    def _occlusion_allowed(elem_class, confidence):
        return (elem_class == "AREAL") and (confidence == CONF_HIGH)

    for elem_wrapper in expanded_elements:
        elem = elem_wrapper["element"]
        source_type = elem_wrapper.get("source_type", "HOST")
        source_id = elem_wrapper.get("source_id", source_type)
        source_label = elem_wrapper.get("source_label", source_id)
        world_transform = elem_wrapper["world_transform"]

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
                    if (dmax < W0) or (dmin > Wmax):
                        skipped_outside_view_volume += 1
                        try:
                            # Minimal, auditable tag (no spam)
                            # key_index may not exist yet here; only write later if available
                            elem_wrapper["_skipped_outside_view_volume"] = True
                            elem_wrapper["_skip_w_range"] = (dmin, dmax)
                        except Exception:
                            pass
                        continue
            except Exception:
                # Conservative: do not gate if we cannot determine depth range
                pass

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
        except Exception:
            pass

        # PR9: persist bbox provenance into element meta (auditable)
        try:
            if 0 <= key_index < len(raster.element_meta):
                raster.element_meta[key_index]["bbox_source"] = elem_wrapper.get("bbox_source")
        except Exception:
            pass

        if source_type not in ("HOST", "LINK", "DWG"):
            raise ValueError("Invalid source_type from wrapper: {0} (source_id={1})".format(source_type, source_id))

        # Optional targeted diagnostics for LINK transform issues.
        # If cfg.diag_link_elem_ids is a non-empty iterable of int element ids, only those ids are traced.
        diag_link_ids = set()
        try:
            v = getattr(cfg, "diag_link_elem_ids", None)
            if v:
                diag_link_ids = set(int(x) for x in v)
        except Exception:
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
            except Exception:
                rect = None

        # Classify element based on rect dimensions
        elem_class = "AREAL"  # Default classification
        if rect and not rect.empty:
            try:
                cls_w_cells, cls_h_cells = _rect_dims_for_classification(rect, raster)
                elem_class = _classify_uv_rect(cls_w_cells, cls_h_cells)
            except Exception:
                elem_class = "AREAL"  # Safe default on classification failure

        # Extract geometry using appropriate strategy based on classification
        loops = None
        confidence = None
        strategy = None
        silhouette_error = None

        if elem_class == "AREAL":
            # AREAL: Use unified extraction with confidence-based fallback
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
                    except Exception:
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

                # Assign confidence for TINY/LINEAR (simple model)
                confidence = CONF_HIGH if loops else CONF_LOW

                # Extract strategy from loops if available
                if loops and len(loops) > 0:
                    strategy = loops[0].get('strategy', 'silhouette')
                else:
                    strategy = 'failed'

            except Exception as e:
                # Silhouette extraction failed, loops will be None
                loops = None
                confidence = CONF_LOW
                strategy = 'failed'
                silhouette_error = str(e)
                if processed < 10:
                    print("[DEBUG] Silhouette extraction failed for element {0} ({1}): {2}".format(
                        elem_id, category, silhouette_error))

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
            except Exception:
                elem_depth = 0.0

            try:
                if key_index < len(raster.element_meta):
                    raster.element_meta[key_index]["depth_invalid"] = True
            except Exception:
                pass

        # Clamp depth used for early-out comparisons to the view volume (min depth >= W0).
        # Do NOT change silhouette strategy; this only prevents out-of-volume depths from driving occlusion logic.
        if (W0 is not None) and isinstance(elem_depth, (int, float)) and math.isfinite(elem_depth):
            if elem_depth < W0:
                try:
                    if key_index < len(raster.element_meta):
                        raster.element_meta[key_index]["depth_clamped_to_w0"] = True
                except Exception:
                    pass
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

                # Defaults must be defined on the non-ambiguous path
                uvw_pts = None
                footprint = fp
                elem_min_w = elem_depth  # conservative: element depth from loops-or-bbox

                # Stage 1: tile-level conservative occlusion against bbox footprint
                if _tiles_fully_covered_and_nearer(raster.tile, fp, elem_min_w):
                    skipped += 1
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
                    except Exception:
                        pass  # Diagnostic failures must not crash pipeline

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
                        except Exception:
                            pass
            
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

                        # Minimum sampled W becomes the conservative depth for early-out + stamping
                        elem_min_w = min(w for (_, _, w) in uvw_pts)

                # Stage 2: depth-aware early-out using chosen footprint (bbox or hull)
                if _tiles_fully_covered_and_nearer(raster.tile, footprint, elem_min_w):
                    skipped += 1
                    continue

                # Stage 3: conservative stamping
                # PR8 occlusion contract:
                #   - ONLY AREAL elements contribute to occlusion depth buffer (w_occ)
                #   - TINY/LINEAR: skip conservative stamping entirely
                #     * Will be rendered via silhouette extraction or fallback
                #     * Prevents double-rendering and ghost pixels
                if occlusion_allowed:
                    depth_by_cell = None
                    if uvw_pts:
                        depth_by_cell = {}
                        for (u, v, w) in uvw_pts:
                            i = int(round(u))
                            j = int(round(v))
                            key = (i, j)
                            prev = depth_by_cell.get(key)
                            if prev is None or w < prev:
                                depth_by_cell[key] = w

                    for (i, j) in footprint.cells():
                        w_depth = depth_by_cell.get((i, j), elem_min_w) if depth_by_cell else elem_min_w
                        raster.try_write_cell(i, j, w_depth=w_depth, source=source_type, key_index=key_index)

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
                except Exception:
                    # Diagnostics must never throw.
                    pass

        # Rasterize silhouette loops if we have them
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
                        except Exception:
                            pass

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
                    # Fill axis-aligned rect with depth-tested occlusion and occupancy
                    filled_count = 0
                    for i, j in rect.cells():
                        # Occlusion authority is classification + confidence gated.
                        # If we are in AABB fallback, confidence is LOW by definition (no silhouette fill),
                        # so we should not write occlusion here.
                        meta = raster.element_meta[key_index] if (key_index < len(raster.element_meta)) else {}
                        occlusion_allowed = _occlusion_allowed(
                            meta.get("class", elem_class if "elem_class" in locals() else "AREAL"),
                            meta.get("confidence", CONF_LOW),
                        )

                        if occlusion_allowed:
                            if raster.try_write_cell(i, j, w_depth=elem_depth, source=source_type, key_index=key_index):
                                filled_count += 1

                        # Check if element already rendered successfully via silhouette
                        strategy_used = None
                        if key_index < len(raster.element_meta):
                            strategy_used = raster.element_meta[key_index].get('strategy')

                            raster.element_meta[key_index]["confidence"] = CONF_LOW
                            raster.element_meta[key_index]["occluder"] = False

                        # Only stamp proxy edges if no successful silhouette exists
                        # (Prevents double-rendering: silhouette geometry + bbox edges)
                        if not strategy_used or strategy_used == 'unknown' or 'fallback' in str(strategy_used):
                            # No silhouette or fallback path - stamp proxy edges
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
            except Exception:
                pass

            skipped += 1

            if skipped <= 10:
                try:
                    safe_elem_id = getattr(getattr(elem, "Id", None), "IntegerValue", None)
                except Exception:
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
            except Exception:
                pass

            # Continue with remaining elements
            continue

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

    # Persist view-volume metric for export/diagnostics
    try:
        raster.skipped_outside_view_volume = int(skipped_outside_view_volume)
    except Exception:
        pass

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
                except Exception:
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
                    except Exception:
                        pass

                    csv_filename = "strategy_diagnostics_{0}_{1}.csv".format(view_name, view_id)
                    csv_path = os.path.join(dump_dir, csv_filename)
                else:
                    csv_path = "strategy_diagnostics_{0}_{1}.csv".format(view_name, view_id)

                # Export CSV
                strategy_diag.export_to_csv(csv_path)
                print("\nStrategy diagnostics exported to: {}".format(csv_path))

        except Exception as e:
            print("[WARN] vop.pipeline: Failed to export strategy diagnostics: {0}".format(e))

    return processed


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
        except Exception:
            pass
        return False

        return False

def _should_skip_outside_view_volume(depth_range, W0, Wmax):
    """Pure predicate: True iff element depth_range does NOT overlap [W0, Wmax].

    depth_range: (dmin, dmax) in view-space W.
    W0/Wmax: view-space W interval, both finite numbers.
    """
    if depth_range is None:
        return False

    try:
        dmin, dmax = depth_range
    except Exception:
        return False

    if (dmin is None) or (dmax is None):
        return False

    try:
        dmin = float(dmin)
        dmax = float(dmax)
        W0 = float(W0)
        Wmax = float(Wmax)
    except Exception:
        return False

    if not (math.isfinite(dmin) and math.isfinite(dmax) and math.isfinite(W0) and math.isfinite(Wmax)):
        return False

    if dmin > dmax:
        dmin, dmax = dmax, dmin

    if W0 > Wmax:
        W0, Wmax = Wmax, W0

    return (dmax < W0) or (dmin > Wmax)

def _tiles_fully_covered_and_nearer(tile_map, footprint, elem_min_w):
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
    tiles = footprint.tiles(tile_map)

    for t in tiles:
        # Check if tile is fully filled
        if not tile_map.is_tile_full(t):
            return False

        # SAFE occlusion: ALL cells in tile must be nearer => tile max depth < elem_min_w
        if tile_map.w_max_tile[t] >= elem_min_w:
            return False

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


def _render_areal_element(elem, transform, view, raster, rect, key_index, cfg):
    """Render AREAL element: triangles + z-buffer + depth-tested edges.

    Commentary:
        ✔ Fast conservative interior fill by tiles
        ✔ Refine boundaries using triangle z-buffer
        ✔ Edges depth-tested vs zMin
        ⚠ Placeholder implementation - requires geometry API access
    """
    raise NotImplementedError(
        "Areal rasterization must route all writes through ViewRaster.try_write_cell() "
        "with view-space W-depth per cell (no set_cell_filled fallback)."
    )


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

    Args:
        proxy: UV_AABB or OBB
        key_index: Element metadata index
        raster: ViewRaster (modified in-place)
    """
    # TODO: Implement edge rasterization
    # Placeholder: mark center cell
    pass


def _mark_rect_center_cell(rect, raster):
    """Mark center cell of rect in model_proxy_mask."""
    i_center, j_center = rect.center_cell()
    idx = raster.get_cell_index(i_center, j_center)
    if idx is not None:
        raster.model_proxy_mask[idx] = True


def _mark_thin_band_along_long_axis(rect, raster):
    """Mark thin band along long axis of rect in model_proxy_mask."""
    # TODO: Implement thin band marking
    # Placeholder: mark center row or column
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


def export_view_raster(view, raster, cfg, diag=None, timings=None, strategy_diag=None):
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
    except Exception:
        model_ink_edge_cells = 0

    proxy_edge_cells = 0
    try:
        proxy_edge_cells = sum(1 for k in raster.model_proxy_key if k != -1)
    except Exception:
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
    except Exception:
        # Never allow diagnostics to break export
        pass

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
        "raster": raster.to_dict(),
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
        "strategy_diag": strategy_diag,  # StrategyDiagnostics instance for CSV export
    }
