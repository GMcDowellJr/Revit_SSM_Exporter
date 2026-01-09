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
#    - ONLY AREAL elements contribute to occlusion (w_occ).
#    - AREAL elements ALWAYS occlude, even if they fall back to
#      coarse geometry (OBB / AABB).
#    - TINY and LINEAR elements NEVER write occlusion, even though
#      they may technically hide things at sub-cell scale.
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

def _perf_now():
    # perf_counter is monotonic and high-resolution where available.
    return time.perf_counter()


def _perf_ms(t0, t1):
    return (float(t1) - float(t0)) * 1000.0

def process_document_views(doc, view_ids, cfg):
    """Process multiple views through the VOP interwoven pipeline.

    Args:
        doc: Revit Document
        view_ids: List of Revit View ElementIds (or ints) to process
        cfg: Config object

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

    view_cache_enabled = bool(getattr(cfg, "view_cache_enabled", False))
    view_cache_dir = getattr(cfg, "view_cache_dir", None)
    require_doc_clean = bool(getattr(cfg, "view_cache_require_doc_unmodified", True))

    if not view_cache_dir:
        view_cache_enabled = False

    if view_cache_enabled:
        try:
            os.makedirs(view_cache_dir, exist_ok=True)
        except Exception:
            # If cache dir can't be created, disable caching (must never break pipeline)
            view_cache_enabled = False

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

    def _cfg_hash(cfg_obj):
        try:
            d = cfg_obj.to_dict() if cfg_obj is not None else {}
            blob = json.dumps(d, sort_keys=True, separators=(",", ":")).encode("utf-8")
            return hashlib.sha1(blob).hexdigest()
        except Exception:
            return None

    def _view_signature(doc_obj, view_obj, view_mode_val):
        """
        Conservative signature: view state + config + basic doc identity.
        This intentionally does NOT attempt to prove model hasn't changed
        when the document is dirty (unsaved changes).
        """
        sig = {
            "schema": 1,
            "doc_path": getattr(doc_obj, "PathName", None),
            "doc_title": getattr(doc_obj, "Title", None),
            "view_id": _safe_int(getattr(getattr(view_obj, "Id", None), "IntegerValue", None)),
            "view_uid": getattr(view_obj, "UniqueId", None),
            "view_name": getattr(view_obj, "Name", None),
            "view_mode": view_mode_val,
            "view_type": str(getattr(view_obj, "ViewType", None)),
            "view_template_id": _safe_int(getattr(getattr(view_obj, "ViewTemplateId", None), "IntegerValue", None)),
            "scale": _safe_int(getattr(view_obj, "Scale", None)),
            "detail_level": str(getattr(view_obj, "DetailLevel", None)),
            "discipline": str(getattr(view_obj, "Discipline", None)),
            "display_style": str(getattr(view_obj, "DisplayStyle", None)),
            "crop": _cropbox_fingerprint(view_obj),
            "cfg_sha1": _cfg_hash(cfg),
        }
        blob = json.dumps(sig, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha1(blob).hexdigest(), sig

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

            if can_use_cache and view_id_int is not None:
                sig_hex, sig_obj = _view_signature(doc, view, view_mode)
                cached = _load_cached_view(view_id_int, sig_hex)
                if cached is not None:
                    try:
                        cached["cache"] = {
                            "view_cache": "HIT",
                            "signature": sig_hex,
                            "dir": view_cache_dir,
                        }
                    except Exception:
                        pass

                    try:
                        if hasattr(diag, "info"):
                            diag.info(
                                phase="pipeline",
                                callsite="process_document_views.view_cache",
                                message="View cache hit: skipping view processing",
                                view_id=int(view_id_int),
                                extra={"signature": sig_hex},
                            )
                    except Exception:
                        pass

                    results.append(cached)
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

            if view_mode == VIEW_MODE_MODEL_AND_ANNOTATION:
                # 2) Broad-phase visible elements
                t0 = _perf_now()
                elements = collect_view_elements(doc, view, raster, diag=diag, cfg=cfg)
                t1 = _perf_now()
                _tmark("collect_ms", t0, t1)
                
                # 3) MODEL PASS
                t0 = _perf_now()
                render_model_front_to_back(doc, view, raster, elements, cfg, diag=diag, geometry_cache=geometry_cache)
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
            out = export_view_raster(view, raster, cfg, diag=diag, timings=timings)
            t1 = _perf_now()
            _tmark("export_ms", t0, t1)

            # Write-through persistent cache on successful export
            try:
                if view_cache_enabled:
                    vid = getattr(getattr(view, "Id", None), "IntegerValue", None)
                    if vid is not None:
                        sig_hex, _ = _view_signature(doc, view, view_mode)
                        _save_cached_view(vid, sig_hex, out)
                        try:
                            out["cache"] = {"view_cache": "MISS_SAVED", "signature": sig_hex, "dir": view_cache_dir}
                        except Exception:
                            pass
            except Exception:
                pass
            
            t_view1 = _perf_now()
            _tmark("total_ms", t_view0, t_view1)

            # Convenience mirror at top-level for callers that don't dive into diagnostics
            try:
                out["timings"] = dict(timings)
            except Exception:
                pass

            results.append(out)

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

    return results



def init_view_raster(doc, view, cfg, diag=None):
    """Initialize ViewRaster for a view.

    Centralizes bounds resolution through resolve_view_bounds() so bounds behavior is auditable.
    """
    # Cell size: 1/8" on sheet -> model feet
    scale = view.Scale  # e.g., 96 for 1/8" = 1'-0"
    cell_size_ft = (0.125 * scale) / 12.0  # inches -> feet

    # View basis
    basis = make_view_basis(view, diag=diag)

    # Resolve bounds centrally
    # NOTE: Drafting/annotation-only views require annotation-only bounds; otherwise fallback base bounds dominate.
    from .revit.view_basis import resolve_view_mode, VIEW_MODE_ANNOTATION_ONLY, resolve_annotation_only_bounds

    view_mode, _mode_reason = resolve_view_mode(view, diag=diag)

    if view_mode == VIEW_MODE_ANNOTATION_ONLY:
        anno_bounds = resolve_annotation_only_bounds(doc, view, basis, cell_size_ft, cfg=cfg, diag=diag)

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
            "cap_before": None,
            "cap_after": None,
            "grid_W": int(max(1, math.ceil(float(anno_bounds.width()) / cell_size_ft))),
            "grid_H": int(max(1, math.ceil(float(anno_bounds.height()) / cell_size_ft))),
            "buffer_ft": 0.0,
            "cell_size_ft": cell_size_ft,
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
                "cell_size_ft": cell_size_ft,
                "max_W": cfg.max_grid_cells_width,
                "max_H": cfg.max_grid_cells_height,
            },
        )

    bounds_xy = bounds_result["bounds_uv"]
    W = int(bounds_result.get("grid_W", 1) or 1)
    H = int(bounds_result.get("grid_H", 1) or 1)

    # Compute adaptive tile size based on grid dimensions
    tile_size = cfg.compute_adaptive_tile_size(W, H)

    raster = ViewRaster(
        width=W, height=H, cell_size=cell_size_ft, bounds=bounds_xy, tile_size=tile_size, cfg=cfg
    )

    # Persist bounds metadata for export diagnostics (never silent)
    raster.bounds_meta = {
        "reason": bounds_result.get("reason"),
        "confidence": bounds_result.get("confidence"),
        "buffer_ft": bounds_result.get("buffer_ft"),
        "anno_expanded": bounds_result.get("anno_expanded"),
        "capped": bounds_result.get("capped"),
        "cap_before": bounds_result.get("cap_before"),
        "cap_after": bounds_result.get("cap_after"),
        "grid_W": W,
        "grid_H": H,
    }

    # Store view basis for annotation rasterization
    raster.view_basis = basis

    return raster


def render_model_front_to_back(doc, view, raster, elements, cfg, diag=None, geometry_cache=None):
    """Render 3D model elements front-to-back with interwoven AreaL/Tiny/Linear handling.

    Args:
        doc: Revit Document
        view: Revit View
        raster: ViewRaster (modified in-place)
        elements: List of Revit elements (from collect_view_elements)
        cfg: Config

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

    # Expand to include linked/imported elements
    expanded_elements = expand_host_link_import_model_elements(doc, view, elements, cfg, diag=diag)

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

    for elem_wrapper in expanded_elements:
        elem = elem_wrapper["element"]
        source_type = elem_wrapper.get("source_type", "HOST")
        source_id = elem_wrapper.get("source_id", source_type)
        source_label = elem_wrapper.get("source_label", source_id)
        world_transform = elem_wrapper["world_transform"]

        # Get element metadata
        try:
            elem_id = elem.Id.IntegerValue
            category = elem.Category.Name if elem.Category else "Unknown"
        except Exception as e:
            # Log the error but continue processing other elements
            skipped += 1
            if skipped <= 5:  # Log first 5 errors to avoid spam
                print("[WARN] vop.pipeline: Skipping element from {0}: {1}".format(doc_label, e))
            continue

        key_index = raster.get_or_create_element_meta_index(
            elem_id, category,
            source_id=source_id,
            source_type=source_type,
            source_label=source_label
        )

        # PR9: persist bbox provenance into element meta (auditable)
        try:
            if 0 <= key_index < len(raster.element_meta):
                raster.element_meta[key_index]["bbox_source"] = elem_wrapper.get("bbox_source")
        except Exception:
            pass

        if source_type not in ("HOST", "LINK", "DWG"):
            raise ValueError("Invalid source_type from wrapper: {0} (source_id={1})".format(source_type, source_id))

        # CRITICAL FIX: Extract silhouette FIRST to get accurate geometry
        # (depth calculation moved AFTER silhouette extraction)
        loops = None
        silhouette_error = None
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

            loops = get_element_silhouette(elem, view, vb, raster, cfg, cache=geometry_cache, cache_key=cache_key, diag=diag)
        except Exception as e:
            # Silhouette extraction failed, loops will be None
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

        # DEBUG: Log depth values and silhouette status for first few elements
        if processed < 10:
            depth_source = "geometry" if loops else "bbox"
            silhouette_status = "SUCCESS ({0} loops)".format(len(loops)) if loops else "FAILED (bbox fallback)"
            print("[DEBUG] Element {0} ({1}): silhouette={2}, depth={3} (from {4}), source={5}".format(
                elem_id, category, silhouette_status, elem_depth, depth_source, source_type))

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
                width_cells, height_cells = cellrect_dims(rect)
                minor_cells = min(width_cells, height_cells)

                elem_class = _classify_uv_rect(width_cells, height_cells)
                if key_index < len(raster.element_meta):
                    raster.element_meta[key_index]["class"] = elem_class
                    raster.element_meta[key_index]["occluder"] = (elem_class == "AREAL")

                aabb_area_cells = width_cells * height_cells
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
                # PR8 semantics:
                #   - ONLY AREAL elements contribute to occlusion (w_occ)
                #   - TINY/LINEAR never write occlusion here
                if elem_class == "AREAL":
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
            try:
                # Get strategy used from first loop
                strategy = loops[0].get('strategy', 'unknown')

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
                    except Exception:
                        pass

                # Second: rasterize open polylines (edges)
                if open_loops:
                    try:
                        filled += raster.rasterize_open_polylines(
                            open_loops, key_index, depth=elem_depth, source=source_type
                        )
                    except Exception:
                        pass

                if filled > 0:
                    # Tag element metadata with strategy used
                    if key_index < len(raster.element_meta):
                        raster.element_meta[key_index]['strategy'] = strategy

                    silhouette_success += 1
                    processed += 1
                    continue

            except Exception as e:
                # Rasterization failed, fall through to bbox fallback
                pass

        # Fallback: Use OBB polygon (oriented bounds, not axis-aligned rect)
        obb_success = False
        obb_error = None
        aabb_success = False
        aabb_error = None

        try:
            from .revit.collection import get_element_obb_loops
            obb_loops = get_element_obb_loops(
                elem,
                vb,
                raster,
                bbox=elem_wrapper.get("bbox"),
                diag=diag,
                view=view,
            )

            if obb_loops:
                try:
                    # Rasterize OBB polygon (same as silhouette loops)
                    write_proxy_edges = bool(getattr(cfg, "proxy_edges_to_occupancy", True))
                    filled = raster.rasterize_proxy_loops(
                        obb_loops,
                        key_index,
                        depth=elem_depth,
                        source=source_type,
                        write_proxy_edges=write_proxy_edges,
                    )

                    if filled > 0:
                        # Tag with OBB strategy
                        if key_index < len(raster.element_meta):
                            raster.element_meta[key_index]['strategy'] = 'uv_obb'
                            raster.element_meta[key_index]['filled_cells'] = filled

                        bbox_fallback += 1
                        processed += 1
                        obb_success = True
                    else:
                        obb_error = "OBB rasterization returned 0 filled cells"
                except Exception as e:
                    obb_error = "OBB rasterization failed: {0}".format(e)
            else:
                obb_error = "OBB loop generation returned None (no bbox?)"

            if obb_success:
                continue

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
                        # Occlusion authority remains classification-based
                        if elem_class == "AREAL":
                            if raster.try_write_cell(i, j, w_depth=elem_depth, source=source_type, key_index=key_index):
                                filled_count += 1

                        # Always attempt proxy-ink edges on boundary (visibility > nothing)
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
                    raster.element_meta[key_index]['strategy'] = 'FAILED'
                    raster.element_meta[key_index]['obb_error'] = obb_error
                    raster.element_meta[key_index]['aabb_error'] = aabb_error

                skipped += 1
                if skipped <= 10:
                    print("[ERROR] Element {0} ({1}) from {2} completely failed:".format(
                        elem_id, category, doc_label))
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


def export_view_raster(view, raster, cfg, diag=None, timings=None):
    """Export view raster to dictionary for JSON serialization.

    Args:
        view: Revit View
        raster: ViewRaster
        cfg: Config

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
            "timings": (dict(timings) if timings is not None else None),
        },
    }
