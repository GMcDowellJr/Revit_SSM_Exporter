"""
Streaming pipeline processing for VOP Interwoven.

Enables incremental processing and export to avoid memory accumulation:
- PNGs written as each view completes
- CSV rows appended incrementally  
- Element metadata streamed to JSON if needed
- Minimal memory footprint for large view sets

Key principles:
1. Results are processed via callbacks as they become available
2. Heavy data (rasters) can be discarded after export
3. Lightweight metadata retained for final summary
"""

import os
import time
import json
import gc
from datetime import datetime
from vop_interwoven.memory_telemetry import MemoryTracker

MAX_SAFE_GRID_CELLS = 500000

def process_with_streaming(doc, view_ids, cfg, on_view_complete, root_cache=None):
    """Process views with per-view callback and cache support."""
    
    from vop_interwoven.pipeline import process_document_views
    from vop_interwoven.root_cache import RootStyleCache, compute_config_hash
    
    # Initialize cache
    project_guid = doc.ProjectInformation.UniqueId if doc.ProjectInformation else "unknown"
    config_hash = compute_config_hash(cfg)
    
    root_cache = RootStyleCache(
        output_dir=getattr(cfg, "output_dir", "C:\\temp\\vop_output"),
        project_guid=project_guid,
        exporter_version="VOP_v2.0",
        config_hash=config_hash
    )
    root_cache.load()
    
    try:
        from vop_interwoven.core.silhouette import reset_family_region_caches
        reset_family_region_caches()
    except Exception:
        pass

    summaries = []
    mem_tracker = MemoryTracker()
    try:
        mem_tracker.mark("run_start")
    except Exception as e:
        print("[Streaming] memory mark run_start failed: {}".format(e))
    
    for view_id in view_ids:
        try:
            # Compute signature FIRST (before processing)
            from vop_interwoven.pipeline import _view_signature  # Need to expose this
            view = doc.GetElement(view_id)
            
            # TODO: Compute signature with element IDs
            # sig_hex, sig_obj = _view_signature(doc, view, "MODEL_AND_ANNOTATION")
            
            # Check cache
            # cached = root_cache.get_view(view_id, sig_hex)
            # if cached:
            #     # Reconstruct minimal view_result from cached metrics
            #     view_result = {
            #         "view_id": view_id,
            #         "from_cache": True,
            #         **cached["metadata"],
            #         "metrics": cached["metrics"]
            #     }
            #     on_view_complete(view_result)
            #     continue
            
            # Process view (cache miss)
            results = process_document_views(doc, [view_id], cfg, reset_family_caches=False)
            
            if results and len(results) > 0:
                view_result = results[0]
                on_view_complete(view_result)
                
                # Lightweight summary
                summary = {
                    "view_id": view_result.get("view_id"),
                    "view_name": view_result.get("view_name"),
                    "success": view_result.get("success", True)
                }
                summaries.append(summary)
                
        except Exception as e:
            print(f"[Streaming] Error processing view {view_id}: {e}")
            summaries.append({
                "view_id": view_id,
                "success": False,
                "error": str(e)
            })
    
    # Save cache at end
    root_cache.save()
    
    return summaries
    
class StreamingExporter:
    """Manages incremental export of pipeline results."""
    
    def __init__(self, output_dir, cfg, doc,
                 export_png=True,
                 export_csv=True,
                 export_json=False,
                 export_view_raster=True,
                 pixels_per_cell=4,
                 date_override=None,
                 root_cache=None):
        """Initialize streaming exporter.

        Args:
            output_dir: Base output directory
            cfg: Config object
            doc: Revit Document
            export_png: Write VOP raster PNGs as views complete (output: vop_raster/)
            export_csv: Write CSV rows incrementally
            export_json: Write full JSON at end (memory-heavy, discouraged)
            export_view_raster: Write raw Revit view PNGs for comparison (output: view_raster/)
            pixels_per_cell: PNG resolution
            date_override: Optional date for CSV export
        """
        self.root_cache = root_cache

        self.output_dir = output_dir
        self.cfg = cfg
        self.doc = doc
        self.export_png = export_png
        self.export_csv = export_csv
        self.export_json = export_json
        self.export_view_raster = export_view_raster
        self.export_perf_csv = bool(getattr(cfg, "export_perf_csv", False))
        self.pixels_per_cell = pixels_per_cell
        self.date_override = date_override

        # Stats
        self.views_processed = 0
        self.views_failed = 0
        self.png_files = []
        self.view_raster_files = []
        self.csv_rows_written = 0
        
        # CSV state
        self.csv_core_writer = None
        self.csv_core_file = None
        self.csv_vop_writer = None
        self.csv_vop_file = None
        self.perf_writer = None
        self.perf_file = None
        
        # Lightweight view summaries (no raster data)
        self.view_summaries = []

        # Full results if JSON export requested (memory-heavy)
        self.full_results = [] if export_json else None

        # Generate run_id once for consistency across all views
        from datetime import datetime
        run_dt = datetime.now()
        tag = None

        if date_override:
            if isinstance(date_override, str):
                s = date_override.strip()
                try:
                    if len(s) == 10:
                        run_dt = datetime.strptime(s, "%Y-%m-%d")
                    else:
                        run_dt = datetime.fromisoformat(s)
                except Exception as e:
                    tag = s
            else:
                tag = str(date_override)

        base_run_id = run_dt.strftime("%Y%m%dT%H%M%S")
        self.run_id = f"{base_run_id}_{tag}" if tag else base_run_id

        # Setup
        os.makedirs(output_dir, exist_ok=True)
        if export_png:
            self.png_dir = os.path.join(output_dir, "vop_raster")
            os.makedirs(self.png_dir, exist_ok=True)
        if export_view_raster:
            self.view_raster_dir = os.path.join(output_dir, "view_raster")
            os.makedirs(self.view_raster_dir, exist_ok=True)
        
        if export_csv:
            self._init_csv_writers()
    
    def _init_csv_writers(self):
        """Initialize CSV writers for incremental writing."""
        import csv
        from vop_interwoven.csv_export import (
            get_core_csv_header,
            get_vop_csv_header,
            get_occlusion_csv_header,
            get_perf_csv_header,
        )
        
        # Output dirs (default perf CSV colocated with other CSVs)
        csv_output_dir = self.output_dir
        perf_output_dir = getattr(self.cfg, "perf_csv_output_dir", None) or csv_output_dir
        os.makedirs(csv_output_dir, exist_ok=True)
        os.makedirs(perf_output_dir, exist_ok=True)

        # Core CSV
        if isinstance(self.date_override, datetime):
            date_str = self.date_override.strftime("%Y-%m-%d")
        elif isinstance(self.date_override, str):
            date_str = self.date_override
        else:
            date_str = datetime.now().strftime("%Y-%m-%d")
            
        core_filename = f"views_core_{date_str}.csv"
        self.core_csv_path = os.path.join(csv_output_dir, core_filename)
        self.csv_core_file = open(self.core_csv_path, 'w', newline='', encoding='utf-8')
        self.csv_core_writer = csv.DictWriter(
            self.csv_core_file, 
            fieldnames=get_core_csv_header(),
            extrasaction='ignore'
        )
        self.csv_core_writer.writeheader()
        
        # VOP CSV
        vop_filename = f"views_vop_{date_str}.csv"
        self.vop_csv_path = os.path.join(csv_output_dir, vop_filename)
        self.csv_vop_file = open(self.vop_csv_path, 'w', newline='', encoding='utf-8')
        self.csv_vop_writer = csv.DictWriter(
            self.csv_vop_file,
            fieldnames=get_vop_csv_header(self.cfg), 
            extrasaction='ignore'
        )
        self.csv_vop_writer.writeheader()
        
        # Occlusion diagnostics CSV
        occlusion_filename = f"views_occlusion_{date_str}.csv"
        self.occlusion_csv_path = os.path.join(csv_output_dir, occlusion_filename)
        self.csv_occlusion_file = open(self.occlusion_csv_path, 'w', newline='', encoding='utf-8')
        self.csv_occlusion_writer = csv.DictWriter(
            self.csv_occlusion_file,
            fieldnames=get_occlusion_csv_header(),
            extrasaction='ignore'
        )
        self.csv_occlusion_writer.writeheader()

        # Perf CSV (optional via config)
        self.perf_csv_path = None
        if self.export_perf_csv:
            perf_filename = f"views_perf_{date_str}.csv"
            self.perf_csv_path = os.path.join(perf_output_dir, perf_filename)
            self.perf_file = open(self.perf_csv_path, 'w', newline='', encoding='utf-8')
            self.perf_writer = csv.DictWriter(
                self.perf_file,
                fieldnames=get_perf_csv_header(),
                extrasaction='ignore'
            )
            self.perf_writer.writeheader()
    
    def on_view_complete(self, view_result):
        """Callback when a view completes processing.
        
        Args:
            view_result: Full view result dict with raster data
        """
        self.views_processed += 1
        
        # Check success
        is_success = view_result.get("success", True)
        has_raster = "raster" in view_result
        is_cache_hit = bool(view_result.get("from_cache"))
        has_metrics = isinstance(view_result.get("metrics"), dict) and bool(view_result.get("metrics"))

        # Accept raster-bearing, metrics-only, or cache-hit payloads.
        # Cache-hit payloads may be rehydrated in _write_csv_rows.
        if not is_success:
            self.views_failed += 1
            self.view_summaries.append({
                "view_id": view_result.get("view_id"),
                "view_name": view_result.get("view_name"),
                "success": False
            })
            return
                
        # Export PNG immediately (if enabled) — skip on cache hits (root or legacy)
        try:
            c = view_result.get("cache", {})
            if isinstance(c, dict) and "HIT" in str(c.get("view_cache", "")).upper():
                is_cache_hit = True
            if isinstance(c, dict) and str(c.get("cache_type", "")).lower() == "root":
                is_cache_hit = True
        except Exception as e:
            # Exception in on_view_complete - no diag in scope
            pass  # TODO: Add diagnostics when diag becomes available
        if self.export_png and has_raster and not is_cache_hit:
            t0 = time.perf_counter()
            png_path = self._write_png(view_result)
            t1 = time.perf_counter()

            timings = view_result.setdefault("timings", {})
            timings["png_ms"] = (t1 - t0) * 1000.0

            if png_path:
                self.png_files.append(png_path)

        # Export raw Revit view image for comparison (if enabled).
        # Not gated on is_cache_hit: only needs doc + view_id + dimensions,
        # all of which are present on cache-hit payloads too.
        if self.export_view_raster:
            t0 = time.perf_counter()
            vr_path = self._write_view_raster(view_result)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            timings = view_result.setdefault("timings", {})
            timings["view_raster_ms"] = elapsed_ms

            if vr_path:
                self.view_raster_files.append(vr_path)

        # Write CSV rows immediately (if enabled)
        if self.export_csv:
            self._write_csv_rows(view_result)
        
        # Root cache write-through is owned by pipeline.process_document_views().
        # Streaming must NOT recompute signatures or call root_cache.set_view(),
        # otherwise it can overwrite valid entries with signature="" on import failures.
        pass
        
        # Free heavy raster payload as soon as PNG/CSV export is complete.
        if "raster" in view_result:
            try:
                del view_result["raster"]
            finally:
                gc.collect()

        # Store lightweight summary (no raster)
        summary = self._extract_summary(view_result)
        self.view_summaries.append(summary)

        # Optionally store full result if JSON export requested
        if self.full_results is not None:
            # Store metrics and identity only — never raster arrays.
            self.full_results.append({
                k: v for k, v in view_result.items()
                if k not in ("raster", "diag")
            })
            
            
    def _write_png(self, view_result):
        """Write PNG for a single view result.
        
        Returns:
            Path to written PNG, or None on error
        """
        from vop_interwoven.png_export import export_raster_to_png
        
        view_name = view_result.get("view_name", "unknown")
        view_id = view_result.get("view_id", 0)
        
        # Sanitize filename
        safe_name = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in view_name)
        filename = f"{safe_name}_{view_id}.png"
        output_path = os.path.join(self.png_dir, filename)
        
        png_path = export_raster_to_png(
            view_result,
            output_path,
            self.pixels_per_cell,
            cut_vs_projection=True
        )
        
        if png_path:
            print(f"[Streaming] Wrote PNG: {os.path.basename(png_path)}")

        return png_path

    def _write_view_raster(self, view_result):
        """Export a raw Revit view image to view_raster/ for comparison.

        Returns:
            Path to written PNG, or None on error
        """
        from vop_interwoven.view_raster_export import export_view_image

        view_name = view_result.get("view_name", "unknown")
        view_id = view_result.get("view_id", 0)
        width = int(view_result.get("width", 0) or 0)
        height = int(view_result.get("height", 0) or 0)

        if width <= 0 or height <= 0:
            return None

        safe_name = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in view_name)
        filename = f"{safe_name}_{view_id}.png"
        output_path = os.path.join(self.view_raster_dir, filename)

        # Coerce int view_id to ElementId when Revit API is available.
        eid = view_id
        try:
            from Autodesk.Revit.DB import ElementId
            if not hasattr(view_id, 'IntegerValue'):
                eid = ElementId(int(view_id))
        except Exception:
            pass

        png_path = export_view_image(
            self.doc, eid, output_path,
            width_px=width * self.pixels_per_cell,
            height_px=height * self.pixels_per_cell,
        )

        if png_path:
            print(f"[Streaming] Wrote view raster: {os.path.basename(png_path)}")

        return png_path

    def _write_csv_rows(self, view_result):
        """Write CSV rows for a single view result."""

        # If this is a cache hit, attempt to rehydrate missing CSV fields from root cache.
        # This prevents partial rows when process_document_views() short-circuits and returns
        # metrics-only view_result dicts.
        try:
            is_cache_hit = bool(view_result.get("from_cache"))
            c = view_result.get("cache", {})
            if isinstance(c, dict) and "HIT" in str(c.get("view_cache", "")).upper():
                is_cache_hit = True
            if isinstance(c, dict) and str(c.get("cache_type", "")).lower() == "root":
                is_cache_hit = True

            if is_cache_hit and self.root_cache is not None:
                vid = view_result.get("view_id")
                if vid is not None:
                    cached = None
                    # Prefer signature-agnostic fetch: pipeline already validated signature on hit.
                    try:
                        cached = self.root_cache.get_view_any(vid)
                    except Exception as e:
                        cached = None

                    if isinstance(cached, dict):
                        payload = cached.get("row_payload")
                        if isinstance(payload, dict) and payload:
                            # Merge cached flat payload into view_result for downstream row builders.
                            # Do not overwrite explicit fields already present in view_result.
                            for k, v in payload.items():
                                if k not in view_result or view_result.get(k) in (None, "", []):
                                    view_result[k] = v

                            # Ensure metrics is present as dict if cached has it
                            if not isinstance(view_result.get("metrics"), dict) or not view_result.get("metrics"):
                                m = cached.get("metrics")
                                if isinstance(m, dict):
                                    view_result["metrics"] = m

                            # Ensure timings is present
                            if not isinstance(view_result.get("timings"), dict) or not view_result.get("timings"):
                                t = cached.get("timings")
                                if isinstance(t, dict):
                                    view_result["timings"] = t
        except Exception as e:
            # Never block export due to cache rehydration issues; downstream will fill sentinels.
            pass

        from vop_interwoven.csv_export import (
            view_result_to_core_row,
            view_result_to_vop_row,
            view_result_to_occlusion_row,
            view_result_to_perf_row,
        )
        
        # Helper: ensure all required header columns exist (no blanks on cache hits).
        def _fill_missing(row_dict, fieldnames, sentinel):
            if not isinstance(row_dict, dict):
                return None
            for f in fieldnames:
                if f not in row_dict or row_dict[f] in (None, ""):
                    row_dict[f] = sentinel
            return row_dict

        # IMPORTANT: For DAX slicing, cache-hit blanks should remain blanks.
        # Do not inject "<MISSING_FROM_CACHE>" into empty-but-valid slicer fields.
        sentinel = ""

        # Core row
        core_row = view_result_to_core_row(
            view_result,
            self.cfg,
            self.doc,
            date_override=self.date_override,
            run_id=self.run_id
        )
        if core_row:
            core_row = _fill_missing(core_row, self.csv_core_writer.fieldnames, sentinel)
            self.csv_core_writer.writerow(core_row)
            self.csv_core_file.flush()  # Ensure written to disk
            self.csv_rows_written += 1

        # VOP row
        vop_row = view_result_to_vop_row(
            view_result,
            self.cfg,
            self.doc,
            date_override=self.date_override,
            run_id=self.run_id
        )
        if vop_row:
            vop_row = _fill_missing(vop_row, self.csv_vop_writer.fieldnames, sentinel)
            self.csv_vop_writer.writerow(vop_row)
            self.csv_vop_file.flush()

        # Occlusion diagnostics row (may be empty on cache/annotation-only paths)
        occ_row = view_result_to_occlusion_row(
            view_result,
            date_override=self.date_override,
            run_id=self.run_id,
        )
        if occ_row:
            occ_row = _fill_missing(occ_row, self.csv_occlusion_writer.fieldnames, sentinel)
            self.csv_occlusion_writer.writerow(occ_row)
            self.csv_occlusion_file.flush()

        # Perf row
        if self.export_perf_csv and self.perf_writer is not None:
            perf_row = view_result_to_perf_row(
                view_result,
                date_override=self.date_override,
                run_id=self.run_id
            )
            if perf_row:
                perf_row = _fill_missing(perf_row, self.perf_writer.fieldnames, sentinel)
                self.perf_writer.writerow(perf_row)
                self.perf_file.flush()

        print(f"[Streaming] Wrote CSV rows for view: {view_result.get('view_name')}")
    
    def _extract_summary(self, view_result):
        """Extract lightweight summary from view result (no raster arrays)."""
        return {
            "view_id": view_result.get("view_id"),
            "view_name": view_result.get("view_name"),
            "width": view_result.get("width"),
            "height": view_result.get("height"),
            "total_elements": view_result.get("total_elements"),
            "filled_cells": view_result.get("filled_cells"),
            "success": True,
            "timings": view_result.get("timings")
        }
    
    def finalize(self):
        """Finalize export and return results.
        
        Returns:
            Dict with export summary and file paths
        """
        # Close CSV files
        if self.csv_core_file:
            self.csv_core_file.close()
        if self.csv_vop_file:
            self.csv_vop_file.close()
        if getattr(self, 'csv_occlusion_file', None):
            self.csv_occlusion_file.close()
        if self.perf_file:
            self.perf_file.close()
        
        # Write JSON if requested
        json_path = None
        if self.export_json and self.full_results:
            json_path = os.path.join(self.output_dir, "vop_export.json")
            
            from vop_interwoven.entry_dynamo import _pipeline_result_for_json
            pipeline_result = {
                "success": self.views_failed == 0,
                "views": self.full_results,
                "config": self.cfg.to_dict(),
                "errors": [],
                "summary": {
                    "num_views_requested": len(self.view_summaries),
                    "num_views_processed": self.views_processed,
                    "num_views_failed": self.views_failed
                }
            }
            
            json_payload = _pipeline_result_for_json(pipeline_result, self.cfg)
            with open(json_path, 'w') as f:
                json.dump(json_payload, f, indent=2, default=str)
            
            print(f"[Streaming] Wrote JSON: {json_path}")
        
        return {
            "views_processed": self.views_processed,
            "views_failed": self.views_failed,
            "png_files": self.png_files,
            "view_raster_files": self.view_raster_files,
            "core_csv_path": getattr(self, 'core_csv_path', None),
            "vop_csv_path": getattr(self, 'vop_csv_path', None),
            "occlusion_csv_path": getattr(self, 'occlusion_csv_path', None),
            "perf_csv_path": getattr(self, 'perf_csv_path', None),
            "csv_rows_written": self.csv_rows_written,
            "json_path": json_path,
            "view_summaries": self.view_summaries
        }


def process_document_views_streaming(doc, view_ids, cfg, on_view_complete=None, root_cache=None):
    """Process views with streaming callback support.
    
    Modified version of process_document_views() that calls a callback
    for each completed view, allowing incremental export.
    
    Args:
        doc: Revit Document
        view_ids: List of view IDs to process
        cfg: Config object
        on_view_complete: Callback function(view_result) called for each view
        
    Returns:
        List of lightweight view summaries (no raster data retained)
    """
    from vop_interwoven.pipeline import process_document_views
    
    # If no callback, fall back to standard behavior
    if on_view_complete is None:
        return process_document_views(doc, view_ids, cfg)

    # CRITICAL: Ensure rasters are retained for streaming exports
    # Override any user setting to prevent export failures
    original_retain = getattr(cfg, 'retain_rasters_in_memory', True)
    cfg._is_streaming_mode = True
    cfg.retain_rasters_in_memory = True
    
    # Reset family-level silhouette caches once per exporter run.
    # Keep per-view process_document_views() calls cache-hot within this run.
    try:
        from vop_interwoven.core.silhouette import reset_family_region_caches
        reset_family_region_caches()
    except Exception:
        pass

    geometry_cache = None
    try:
        from vop_interwoven.core.cache import LRUCache
        geometry_cache = LRUCache(max_items=getattr(cfg, "geometry_cache_max_items", 0))
    except Exception:
        geometry_cache = None

    elem_cache = None
    if getattr(cfg, "use_element_cache", True):
        try:
            from vop_interwoven.core.element_cache import ElementCache
            max_items = int(getattr(cfg, "element_cache_max_items", 10000))
            elem_cache = ElementCache(max_elements=max_items)
        except Exception:
            elem_cache = None

    # Process views one at a time with callback
    summaries = []
    mem_tracker = MemoryTracker()
    try:
        mem_tracker.mark("run_start")
    except Exception as e:
        print("[Streaming] memory mark run_start failed: {}".format(e))
    
    for view_id in view_ids:
        try:
            try:
                mem_tracker.mark("view_start_{}".format(view_id))
            except Exception as e:
                print("[Streaming] memory mark view_start failed: {}".format(e))
            # Process single view (cache miss)
            results = process_document_views(
                doc,
                [view_id],
                cfg,
                root_cache=root_cache,
                reset_family_caches=False,
                geometry_cache=geometry_cache,
                elem_cache=elem_cache,
            )

            if results and len(results) > 0:
                view_result = results[0]

                # If pipeline returned a per-view failure stub, surface it directly.
                if view_result.get("success") is False:
                    err = view_result.get("error")
                    if not err:
                        try:
                            d = view_result.get("diag", {}) or {}
                            if isinstance(d, dict):
                                # Diagnostics.to_dict() stores entries in "events".
                                events = d.get("events", []) or []
                                if events:
                                    ev = events[-1] if isinstance(events[-1], dict) else {}
                                    msg = ev.get("message")
                                    exc_type = ev.get("exc_type")
                                    exc_msg = ev.get("exc_message")
                                    phase = ev.get("phase")
                                    callsite = ev.get("callsite")
                                    parts = []
                                    if phase or callsite:
                                        parts.append("{}/{}".format(phase or "?", callsite or "?"))
                                    if msg:
                                        parts.append(str(msg))
                                    if exc_type or exc_msg:
                                        parts.append("{}{}".format(exc_type or "Exception", ": " + str(exc_msg) if exc_msg else ""))
                                    if parts:
                                        err = " | ".join(parts)
                        except Exception:
                            err = None
                    print(f"[Streaming] WARNING: View {view_id} failed in pipeline; skipping exports. error={err}")
                    summaries.append({
                        "view_id": view_id,
                        "view_name": view_result.get("view_name"),
                        "success": False,
                        "error": err or "Pipeline view failure",
                    })
                    continue
                
                # Allow three valid payload shapes:
                #   1) raster-bearing (normal miss path),
                #   2) metrics-only (lightweight/cache materialized metrics),
                #   3) cache-hit without metrics (rehydrated later from root cache in _write_csv_rows).
                has_raster = ("raster" in view_result) and (view_result.get("raster") is not None)
                has_metrics = isinstance(view_result.get("metrics"), dict) and bool(view_result.get("metrics"))
                is_cache_hit = bool(view_result.get("from_cache"))
                try:
                    c = view_result.get("cache", {})
                    if isinstance(c, dict) and "HIT" in str(c.get("view_cache", "")).upper():
                        is_cache_hit = True
                    if isinstance(c, dict) and str(c.get("cache_type", "")).lower() == "root":
                        is_cache_hit = True
                except Exception:
                    pass

                if (not has_raster) and (not has_metrics) and (not is_cache_hit):
                    print(f"[Streaming] WARNING: No raster/metrics/cache-hit in view_result for view {view_id}")
                    summaries.append({
                        "view_id": view_id,
                        "success": False,
                        "error": "Missing raster, metrics, and cache-hit marker"
                    })
                    continue
                    
                width = int(view_result.get("width", 0) or 0)
                height = int(view_result.get("height", 0) or 0)
                filled_cells = int(view_result.get("filled_cells", 0) or 0)
                grid_cells = width * height
                if grid_cells > MAX_SAFE_GRID_CELLS and filled_cells < 10:
                    print(
                        "[Streaming] WARNING: Grid explosion guard tripped for view {0} "
                        "(width={1}, height={2}, grid_cells={3}, filled_cells={4})".format(
                            view_result.get("view_id"), width, height, grid_cells, filled_cells
                        )
                    )
                    summaries.append({
                        "view_id": view_result.get("view_id"),
                        "view_name": view_result.get("view_name"),
                        "success": False,
                        "error": "Grid explosion guard tripped",
                        "width": width,
                        "height": height,
                        "filled_cells": filled_cells,
                    })
                    continue

                try:
                    mem_tracker.mark("after_raster_{}".format(view_result.get("view_id")))
                except Exception as e:
                    print("[Streaming] memory mark after_raster failed: {}".format(e))

                # Call user callback
                on_view_complete(view_result)

                # Retain only lightweight summary
                summary = {
                    "view_id": view_result.get("view_id"),
                    "view_name": view_result.get("view_name"),
                    "width": view_result.get("width"),
                    "height": view_result.get("height"),
                    "success": view_result.get("success", True),
                    "timings": view_result.get("timings")
                }
                summaries.append(summary)

                # Drop Python refs before CLR GC so Python.NET wrappers can release handles first.
                view_result = None
                results = None

                # Force CLR GC to release Revit geometry objects from get_Geometry(opts.View=view).
                # These accumulate on the .NET heap and are not freed by CPython refcounting alone.
                try:
                    mem_tracker.mark_and_gc("after_clr_gc_{}".format(view_id))
                except Exception as e:
                    print("[Streaming] CLR GC mark failed: {}".format(e))

        except Exception as e:
            print(f"[Streaming] Error processing view {view_id}: {e}")
            summaries.append({
                "view_id": view_id,
                "success": False,
                "error": str(e)
            })
    
    try:
        mem_tracker.mark("run_end")
        mem_tracker.mark_and_gc("after_run_gc")
    except Exception as e:
        print("[Streaming] run-end memory marks failed: {}".format(e))

    # Restore original setting (though caller usually doesn't reuse cfg)
    cfg.retain_rasters_in_memory = original_retain
    
    if summaries:
        try:
            summaries[0]["memory_tracker"] = mem_tracker.to_dict()
        except Exception as e:
            print("[Streaming] failed attaching memory tracker: {}".format(e))
    return summaries


def run_vop_pipeline_streaming(doc, view_ids, cfg=None, output_dir=None,
                                export_png=True, export_csv=True, export_json=False,
                                export_view_raster=True,
                                pixels_per_cell=4, date_override=None):
    """Run VOP pipeline with streaming export to minimize memory usage.
    
    This is the recommended entry point for large view sets where memory
    accumulation of raster data would be problematic.
    
    Args:
        doc: Revit Document
        view_ids: List of view IDs to process
        cfg: Config object (optional)
        output_dir: Output directory (default: C:\\temp\\vop_output)
        export_png: Export VOP raster PNGs as views complete (output: vop_raster/)
        export_csv: Export CSV rows incrementally (default: True)
        export_json: Export full JSON at end (default: False - memory-heavy)
        export_view_raster: Export raw Revit view PNGs for comparison (output: view_raster/)
        pixels_per_cell: PNG resolution (default: 4)
        date_override: Optional date for CSV export

    Returns:
        Dict with export summary:
        {
            'views_processed': int,
            'views_failed': int,
            'png_files': [paths],            # vop_raster/ files
            'view_raster_files': [paths],    # view_raster/ files
            'core_csv_path': str,
            'vop_csv_path': str,
            'occlusion_csv_path': str,
            'perf_csv_path': str,
            'csv_rows_written': int,
            'json_path': str | None,
            'view_summaries': [lightweight summaries]
        }
    
    Example:
        >>> from vop_interwoven.streaming import run_vop_pipeline_streaming
        >>> result = run_vop_pipeline_streaming(doc, view_ids, cfg)
        >>> print(f"Processed {result['views_processed']} views")
        >>> print(f"CSVs: {result['core_csv_path']}")
    """
    from vop_interwoven.config import Config
    from vop_interwoven.root_cache import RootStyleCache, compute_config_hash
  
    # Defaults
    if cfg is None:
        cfg = Config()
    
    if output_dir is None:
        output_dir = r"C:\temp\vop_output"

    # Keep pipeline-side exports aligned with streaming output directory by default
    try:
        cfg.output_dir = output_dir
    except Exception:
        pass
    try:
        cfg.date_override = date_override
    except Exception:
        pass

    # CRITICAL: Force raster retention for streaming exports
    # This ensures PNGs and CSVs can be exported before memory is discarded
    cfg.retain_rasters_in_memory = True
    
    # Initialize root-style cache (works with streaming!)
    project_guid = doc.ProjectInformation.UniqueId if doc.ProjectInformation else "unknown"
    exporter_version = "VOP_v2.0"
    config_hash = compute_config_hash(cfg)
    
    root_cache = RootStyleCache(
        output_dir=output_dir,
        project_guid=project_guid,
        exporter_version=exporter_version,
        config_hash=config_hash
    )
    
    # Load existing cache
    root_cache.load()
    
    # Initialize streaming exporter
    exporter = StreamingExporter(
        output_dir=output_dir,
        cfg=cfg,
        doc=doc,
        export_png=export_png,
        export_csv=export_csv,
        export_json=export_json,
        export_view_raster=export_view_raster,
        pixels_per_cell=pixels_per_cell,
        date_override=date_override,
        root_cache=root_cache
    )
    
    # Process with streaming callback
    t0 = time.perf_counter()
    
    process_document_views_streaming(
        doc, 
        view_ids, 
        cfg,
        on_view_complete=exporter.on_view_complete,
        root_cache=root_cache
    )
    
    t1 = time.perf_counter()
    
    # Finalize and get results
    result = exporter.finalize()
    result["total_time_sec"] = t1 - t0
    
    # Persist root cache
    try:
        ok = root_cache.save()
        try:
            print(f"[Streaming] Root cache stats: {root_cache.stats()}")
        except Exception as e:
            # Exception in run_vop_pipeline_streaming - no diag in scope
            pass  # TODO: Add diagnostics when diag becomes available
        if not ok:
            print("[Streaming] Root cache save returned False")
    except Exception as e:
        print(f"[Streaming] Root cache save failed: {e}")
    
    print(f"\n[Streaming] Complete:")
    print(f"  Processed: {result['views_processed']} views")
    print(f"  Failed: {result['views_failed']} views")
    print(f"  VOP raster PNGs: {len(result['png_files'])} files")
    print(f"  View raster PNGs: {len(result['view_raster_files'])} files")
    print(f"  CSVs: {result['csv_rows_written']} rows")
    print(f"  Time: {result['total_time_sec']:.1f}s")
    
    return result
