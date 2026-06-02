"""
Root-style cache: Single file per project, metrics only.

Compatible with streaming mode - stores only computed metrics and metadata,
never the full raster arrays.
"""

import os
import json
import time
import tempfile
import hashlib

def _safe_seq(value):
    if value is None:
        return []
    return value

def _round6(x):
    try:
        return round(float(x), 6)
    except Exception as e:
        return x

class RootStyleCache:
    """Single-file cache storing metrics only (no raster data)."""
    
    def __init__(self, output_dir, project_guid, exporter_version, config_hash):
        """Initialize cache.
        
        Args:
            output_dir: Base output directory
            project_guid: Revit project GUID for cache filename
            exporter_version: Version string (invalidates on mismatch)
            config_hash: Config hash (invalidates on mismatch)
        """
        self.output_dir = output_dir
        self.project_guid = project_guid
        self.exporter_version = exporter_version
        self.config_hash = config_hash
        
        # Cache file path
        self.cache_path = os.path.join(
            output_dir, 
            f"vop_view_cache.json"
        )
        
        #safe_guid = str(project_guid).replace(":", "_").replace("\\", "_").replace("/", "_")
        #self.cache_path = os.path.join(output_dir, f"vop_cache_{safe_guid}.json")
        
        # In-memory cache state
        self._cache = None
        self._dirty = False
        
        # Stats
        self.hits = 0
        self.misses = 0
        self.invalidations = 0
    
    def load(self):
        """Load cache from disk.
        
        Returns:
            True if cache loaded and valid, False otherwise
        """
        if not os.path.exists(self.cache_path):
            self._cache = self._empty_cache()
            return False
        
        try:
            with open(self.cache_path, 'r') as f:
                data = json.load(f)
            
            # Validate version/config/project
            if data.get("exporter_version") != self.exporter_version:
                print(f"[RootCache] Version mismatch: {data.get('exporter_version')} != {self.exporter_version}")
                self.invalidations += 1
                self._cache = self._empty_cache()
                return False
            
            if data.get("config_hash") != self.config_hash:
                print(f"[RootCache] Config mismatch: {data.get('config_hash')} != {self.config_hash}")
                self.invalidations += 1
                self._cache = self._empty_cache()
                return False
            
            if data.get("project_guid") != self.project_guid:
                print(f"[RootCache] Project mismatch: {data.get('project_guid')} != {self.project_guid}")
                self.invalidations += 1
                self._cache = self._empty_cache()
                return False
            
            # Valid cache
            self._cache = data
            print(f"[RootCache] Loaded cache with {len(data.get('views', {}))} views")
            return True
            
        except Exception as e:
            print(f"[RootCache] Load failed: {e}")
            self._cache = self._empty_cache()
            return False
    
    def get_view(self, view_id, current_signature):
        """Get cached view if signature matches.
        
        Args:
            view_id: View ID (integer)
            current_signature: Current view signature hash
        
        Returns:
            Cached view dict if hit, None if miss
        """
        if self._cache is None:
            self.load()
        
        view_key = str(view_id)
        cached_view = self._cache.get("views", {}).get(view_key)
        
        if cached_view is None:
            self.misses += 1
            print(f"[RootCache] MISS view {view_id} (not present)")
            return None

        cached_sig = cached_view.get("view_signature")
        if cached_sig != current_signature:
            self.misses += 1
            print(f"[RootCache] MISS view {view_id} (signature mismatch)")
            return None

        self.hits += 1
        print(f"[RootCache] HIT view {view_id}")
        return cached_view

    def get_view_any(self, view_id):
        """Get cached view by ID only (no signature check).

        Use ONLY when the caller already knows the view was a cache hit
        (e.g., pipeline performed the signature check).
        """
        if self._cache is None:
            self.load()

        view_key = str(view_id)
        return self._cache.get("views", {}).get(view_key)

    def set_view(self, view_id, signature, metadata, metrics, element_summary=None, timings=None):
        """Cache a view's metrics (not raster data).

        Stores an additional 'row_payload' dict that is sufficient to rehydrate
        CSV rows on cache hits (streaming mode), without requiring raster arrays.
        """
        if self._cache is None:
            self.load()

        element_summary = element_summary or {}
        timings = timings or {}

        # CSV-rehydratable payload: flat dict for easy merge into view_result / row builders.
        row_payload = {}
        if isinstance(metadata, dict):
            row_payload.update(metadata)

        # Warn on collisions: metrics overwriting metadata is almost always unintended.
        if isinstance(metadata, dict) and isinstance(metrics, dict):
            for k in metrics.keys():
                if k in metadata:
                    print(f"[RootCache] WARNING: row_payload key collision (metrics overwrites metadata): {k}")

        if isinstance(metrics, dict):
            row_payload.update(metrics)

        # Keep these nested fields too (some exporters may use them)
        row_payload["timings"] = timings
        row_payload["element_summary"] = element_summary

        # Ensure basic identifiers exist in payload
        if "view_id" not in row_payload:
            row_payload["view_id"] = view_id

        # Normalize view_type to human-readable string before persisting to cache row_payload.
        # This prevents cached VOP CSV ViewType from reverting to enum ints.
        try:
            from vop_interwoven.csv_export import _viewtype_name_from_value
            _vt_raw = row_payload.get("view_type", None)
            _vt_name = _viewtype_name_from_value(_vt_raw)

            # Only replace if we successfully resolved a readable name.
            # (In non-Revit test environments, enum name resolution may be unavailable.)
            if _vt_name:
                row_payload["view_type"] = _vt_name
        except Exception as e:
            # Exception in set_view - no diag in scope
            pass  # TODO: Add diagnostics when diag becomes available
        # CSV invariants for cache-hit rehydration
        # These are safe defaults; streaming layer may overwrite with actual lookup timing.
        row_payload.setdefault("FromCache", "Y")
        row_payload.setdefault("ElapsedSec", 0)

        # Backward-compatible: add CSV-schema identity keys alongside snake_case keys
        # so cached row_payload can be reused verbatim by CSV exporters.
        try:
            if "ViewId" not in row_payload:
                row_payload["ViewId"] = metadata.get("view_id", row_payload.get("view_id"))
            if "ViewName" not in row_payload:
                row_payload["ViewName"] = metadata.get("view_name", row_payload.get("view_name"))
            if "ViewType" not in row_payload:
                row_payload["ViewType"] = row_payload.get("view_type") or metadata.get("view_type", "")

            if "view_unique_id" not in row_payload:
                row_payload["view_unique_id"] = metadata.get("view_unique_id", metadata.get("ViewUniqueId", ""))
            if "ViewUniqueId" not in row_payload:
                row_payload["ViewUniqueId"] = (
                    metadata.get("view_unique_id", "")
                    or metadata.get("ViewUniqueId", "")
                    or row_payload.get("view_unique_id", "")
                )
        except Exception as e:
            # Exception in set_view - no diag in scope
            pass  # TODO: Add diagnostics when diag becomes available
        view_key = str(view_id)
        self._cache.setdefault("views", {})[view_key] = {
            "view_signature": signature,
            "cached_utc": time.time(),
            "metadata": metadata or {},
            "metrics": metrics or {},
            "element_summary": element_summary,
            "timings": timings,
            "row_payload": row_payload,
        }

        self._dirty = True

    def save(self):
        """Save cache to disk atomically."""
        if not self._dirty:
            return True
        
        if self._cache is None:
            return False
        
        try:
            # Ensure directory exists
            os.makedirs(self.output_dir, exist_ok=True)
            
            # Atomic write via temp file
            tmp_fd, tmp_path = tempfile.mkstemp(
                prefix="vop_cache_", 
                suffix=".json", 
                dir=self.output_dir
            )
            
            try:
                with os.fdopen(tmp_fd, 'w') as f:
                    json.dump(self._cache, f, indent=2)
                
                # Atomic replace
                os.replace(tmp_path, self.cache_path)
                
                self._dirty = False
                print(f"[RootCache] Saved cache with {len(self._cache.get('views', {}))} views")
                return True
                
            finally:
                # Cleanup temp file if it still exists
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception as e:
                    # Exception in save - no diag in scope
                    pass  # TODO: Add diagnostics when diag becomes available
        except Exception as e:
            print(f"[RootCache] Save failed: {e}")
            return False
    
    def stats(self):
        """Get cache statistics."""
        total = self.hits + self.misses
        hit_rate = (self.hits / total) if total > 0 else 0.0
        
        return {
            "cache_path": self.cache_path,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": hit_rate,
            "invalidations": self.invalidations,
            "cached_views": len(self._cache.get("views", {})) if self._cache else 0
        }
    
    def _empty_cache(self):
        """Create empty cache structure."""
        return {
            "exporter_version": self.exporter_version,
            "config_hash": self.config_hash,
            "project_guid": self.project_guid,
            "created_utc": time.time(),
            "views": {}
        }


def compute_config_hash(cfg):
    """Compute stable hash of config for cache invalidation.
    
    Args:
        cfg: Config object
    
    Returns:
        Hash string (8 chars)
    """
    try:
        config_dict = cfg.to_dict()
        
        # Exclude cache-related settings from hash
        # (changing cache location shouldn't invalidate cache)
        config_dict.pop("view_cache_enabled", None)
        config_dict.pop("view_cache_dir", None)
        config_dict.pop("view_cache_require_doc_unmodified", None)
        
        payload = json.dumps(config_dict, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:8]
    except Exception as e:
        return "00000000"


def extract_metrics_from_view_result(view_result, cfg):
    """Extract cacheable metrics from view result.
    
    Args:
        view_result: Full view result dict (with raster)
        cfg: Config object
    
    Returns:
        Tuple of (metadata, metrics, element_summary, timings)
    """
    from vop_interwoven.csv_export import (
        compute_cell_metrics,
        compute_external_cell_metrics,
        compute_annotation_type_metrics,
        _normalize_locked_metrics_for_legacy_csv,
        _has_legacy_partition_metrics,
        _has_locked_partition_metrics,
    )
    
    raster_payload = view_result.get("raster", None)
    if isinstance(raster_payload, dict):
        raster_dict = raster_payload
    elif raster_payload is None:
        raster_dict = {}
    else:
        b = getattr(raster_payload, "bounds_xy", None)
        raster_dict = {
            "width": int(getattr(raster_payload, "W", 0) or 0),
            "height": int(getattr(raster_payload, "H", 0) or 0),
            "cell_size_ft": float(getattr(raster_payload, "cell_size_ft", 0.0) or 0.0),
            "bounds_xy": {
                "xmin": getattr(b, "xmin", 0.0) if b is not None else 0.0,
                "ymin": getattr(b, "ymin", 0.0) if b is not None else 0.0,
                "xmax": getattr(b, "xmax", 0.0) if b is not None else 0.0,
                "ymax": getattr(b, "ymax", 0.0) if b is not None else 0.0,
            },
            "model_edge_key": _safe_seq(getattr(raster_payload, "model_edge_key", [])),
            "model_proxy_mask": _safe_seq(getattr(raster_payload, "model_proxy_mask", [])),
            "model_proxy_key": _safe_seq(getattr(raster_payload, "model_proxy_key", [])),
            "model_mask": _safe_seq(getattr(raster_payload, "model_mask", [])),
            "anno_over_model": _safe_seq(getattr(raster_payload, "anno_over_model", [])),
            "anno_key": _safe_seq(getattr(raster_payload, "anno_key", [])),
            "anno_meta": _safe_seq(getattr(raster_payload, "anno_meta", [])),
            "element_meta": _safe_seq(getattr(raster_payload, "element_meta", [])),
        }

    # Prefer precomputed per-view metrics only when they include the partition
    # fields needed by the legacy CSV/cache schema.  Some pipeline payloads carry
    # a metrics stub with only TotalCells; treating that as authoritative exports
    # zeroes for Empty/ModelOnly/AnnoOnly/Overlap.
    precomputed_metrics = view_result.get("metrics")
    use_precomputed = _has_legacy_partition_metrics(precomputed_metrics) or _has_locked_partition_metrics(precomputed_metrics)
    if use_precomputed:
        if _has_locked_partition_metrics(precomputed_metrics) and "Empty" not in precomputed_metrics:
            pre = _normalize_locked_metrics_for_legacy_csv(precomputed_metrics)
        else:
            pre = dict(precomputed_metrics)
        metrics = {
            **pre,
            "CellSize_ft": view_result.get("cell_size")
            or raster_dict.get("cell_size_ft", 0.0),
        }
    else:
        # Reconstruct raster object for metric computation (fallback for legacy/incomplete payloads)
        from vop_interwoven.core.raster import ViewRaster
        from vop_interwoven.core.math_utils import Bounds2D
        bounds_dict = raster_dict.get("bounds_xy", {}) or {}
        bounds = Bounds2D(
            bounds_dict.get("xmin", 0),
            bounds_dict.get("ymin", 0),
            bounds_dict.get("xmax", 100),
            bounds_dict.get("ymax", 100)
        )

        raster = ViewRaster(
            width=raster_dict.get("width", 0),
            height=raster_dict.get("height", 0),
            cell_size=raster_dict.get("cell_size_ft", raster_dict.get("cell_size", 1.0)),
            bounds=bounds,
            tile_size=getattr(cfg, "tile_size", 16) or 16
        )

        raster.model_edge_key = raster_dict.get("model_edge_key", [])
        raster.model_proxy_mask = raster_dict.get("model_proxy_mask", raster_dict.get("model_proxy_presence", []))
        raster.model_proxy_key = raster_dict.get("model_proxy_key", [])
        raster.model_mask = raster_dict.get("model_mask", [])
        raster.anno_over_model = raster_dict.get("anno_over_model", [])
        raster.anno_key = raster_dict.get("anno_key", [])
        raster.anno_meta = raster_dict.get("anno_meta", [])
        raster.element_meta = raster_dict.get("element_meta", raster_dict.get("elements_meta", []))

        # Compute metrics
        model_presence_mode = getattr(cfg, "model_presence_mode", "ink")
        cell_metrics = compute_cell_metrics(raster, model_presence_mode=model_presence_mode)
        external_metrics = compute_external_cell_metrics(raster)
        anno_metrics = compute_annotation_type_metrics(raster)

        metrics = {
            **cell_metrics,
            **external_metrics,
            **anno_metrics,
            "CellSize_ft": raster.cell_size_ft
        }
    
    # Extract metadata
    bounds_meta = raster_dict.get("bounds_meta", {}) or {}
    cell_size_base = float(metrics.get("CellSize_ft", raster_dict.get("cell_size_ft", 0.0)) or 0.0)

    cell_size_req = bounds_meta.get("cell_size_ft_requested", cell_size_base)
    cell_size_eff = bounds_meta.get("cell_size_ft_effective", cell_size_base)

    metadata = {
        "view_id": view_result.get("view_id"),
        "view_name": view_result.get("view_name"),
        "view_unique_id": view_result.get("view_unique_id") or "",

        # IMPORTANT: view_type is a human-readable Revit view type (e.g., FloorPlan),
        # NOT the pipeline view_mode (e.g., ANNOTATION_ONLY).
        "view_type": view_result.get("view_type") or "",

        # Keep processing mode separately (useful for debugging / slicing if desired)
        "view_mode": view_result.get("view_mode") or "",

        # Optional but valuable slicers (expect pipeline to populate these on cache-miss)
        "discipline": view_result.get("discipline") or "",
        "phase": view_result.get("phase") or "",
        "sheet_number": view_result.get("sheet_number") or "",
        "view_template_name": view_result.get("view_template_name") or "",

        "width": view_result.get("width"),
        "height": view_result.get("height"),

        # Back-compat / existing
        "CellSize_ft": _round6(cell_size_base),

        # Option 2 contract fields (persisted for CSV + cache parity)
        "CellSizeRequested_ft": _round6(cell_size_req),
        "CellSizeEffective_ft": _round6(cell_size_eff),
        "ResolutionMode": bounds_meta.get("resolution_mode", "canonical"),
        "CapTriggered": bool(bounds_meta.get("cap_triggered", bounds_meta.get("capped", False))),

        # Keep bounds as before
        "bounds": raster_dict.get("bounds_xy", {}),
    }

    
    # Extract element summary
    element_meta = raster_dict.get("element_meta", [])
    element_summary = {
        "count": len(element_meta),
        "by_category": {}
    }
    
    for meta in element_meta:
        cat = meta.get("category", "Unknown")
        element_summary["by_category"][cat] = element_summary["by_category"].get(cat, 0) + 1
    
    # Extract timings
    timings = view_result.get("timings", {})
    
    return metadata, metrics, element_summary, timings