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
from datetime import datetime


class StreamingExporter:
    """Manages incremental export of pipeline results."""
    
    def __init__(self, output_dir, cfg, doc, 
                 export_png=True, 
                 export_csv=True,
                 export_json=False,
                 pixels_per_cell=4,
                 date_override=None):
        """Initialize streaming exporter.
        
        Args:
            output_dir: Base output directory
            cfg: Config object
            doc: Revit Document
            export_png: Write PNGs as views complete
            export_csv: Write CSV rows incrementally
            export_json: Write full JSON at end (memory-heavy, discouraged)
            pixels_per_cell: PNG resolution
            date_override: Optional date for CSV export
        """
        self.output_dir = output_dir
        self.cfg = cfg
        self.doc = doc
        self.export_png = export_png
        self.export_csv = export_csv
        self.export_json = export_json
        self.pixels_per_cell = pixels_per_cell
        self.date_override = date_override
        
        # Stats
        self.views_processed = 0
        self.views_failed = 0
        self.png_files = []
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
                except Exception:
                    tag = s
            else:
                tag = str(date_override)

        base_run_id = run_dt.strftime("%Y%m%dT%H%M%S")
        self.run_id = f"{base_run_id}_{tag}" if tag else base_run_id

        # Setup
        os.makedirs(output_dir, exist_ok=True)
        if export_png:
            self.png_dir = os.path.join(output_dir, "png")
            os.makedirs(self.png_dir, exist_ok=True)
        
        if export_csv:
            self._init_csv_writers()
    
    def _init_csv_writers(self):
        """Initialize CSV writers for incremental writing."""
        import csv
        from vop_interwoven.csv_export import (
            get_core_csv_header, 
            get_vop_csv_header,
            get_perf_csv_header
        )
        
        # Core CSV
        date_str = (self.date_override or datetime.now()).strftime("%Y-%m-%d")
        core_filename = f"views_core_{date_str}.csv"
        self.core_csv_path = os.path.join(self.output_dir, core_filename)
        self.csv_core_file = open(self.core_csv_path, 'w', newline='', encoding='utf-8')
        self.csv_core_writer = csv.DictWriter(
            self.csv_core_file, 
            fieldnames=get_core_csv_header(),
            extrasaction='ignore'
        )
        self.csv_core_writer.writeheader()
        
        # VOP CSV
        vop_filename = f"views_vop_{date_str}.csv"
        self.vop_csv_path = os.path.join(self.output_dir, vop_filename)
        self.csv_vop_file = open(self.vop_csv_path, 'w', newline='', encoding='utf-8')
        self.csv_vop_writer = csv.DictWriter(
            self.csv_vop_file,
            fieldnames=get_vop_csv_header(), 
            extrasaction='ignore'
        )
        self.csv_vop_writer.writeheader()
        
        # Perf CSV
        perf_filename = f"views_perf_{date_str}.csv"
        self.perf_csv_path = os.path.join(self.output_dir, perf_filename)
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
        
        # Check if this is a failure stub
        is_success = view_result.get("success", True)
        has_raster = "raster" in view_result
        
        if not is_success or not has_raster:
            self.views_failed += 1
            # Store lightweight summary
            self.view_summaries.append({
                "view_id": view_result.get("view_id"),
                "view_name": view_result.get("view_name"),
                "success": False
            })
            return
        
        # Export PNG immediately
        if self.export_png:
            t0 = time.perf_counter()
            png_path = self._write_png(view_result)
            t1 = time.perf_counter()
            
            # Add timing to view result
            timings = view_result.setdefault("timings", {})
            timings["png_ms"] = (t1 - t0) * 1000.0
            
            if png_path:
                self.png_files.append(png_path)
        
        # Write CSV rows immediately with flush
        if self.export_csv:
            self._write_csv_rows(view_result)
        
        # Store lightweight summary (no raster arrays)
        summary = self._extract_summary(view_result)
        self.view_summaries.append(summary)
        
        # Store full result if JSON export requested (memory-heavy)
        if self.full_results is not None:
            self.full_results.append(view_result)
        
        # Note: View-level caching is disabled for streaming mode to avoid
        # holding full raster data in memory. The cache would need to store
        # the entire view_result dict, defeating streaming's memory benefits.
    
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
    
    def _write_csv_rows(self, view_result):
        """Write CSV rows for a single view result."""
        from vop_interwoven.csv_export import (
            view_result_to_core_row,
            view_result_to_vop_row,
            view_result_to_perf_row
        )
        
        # Core row
        core_row = view_result_to_core_row(
            view_result,
            self.cfg,
            self.doc,
            date_override=self.date_override,
            run_id=self.run_id
        )
        if core_row:
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
            self.csv_vop_writer.writerow(vop_row)
            self.csv_vop_file.flush()
        
        # Perf row
        perf_row = view_result_to_perf_row(
            view_result,
            date_override=self.date_override,
            run_id=self.run_id
        )
        if perf_row:
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
            "core_csv_path": getattr(self, 'core_csv_path', None),
            "vop_csv_path": getattr(self, 'vop_csv_path', None),
            "perf_csv_path": getattr(self, 'perf_csv_path', None),
            "csv_rows_written": self.csv_rows_written,
            "json_path": json_path,
            "view_summaries": self.view_summaries
        }


def process_document_views_streaming(doc, view_ids, cfg, on_view_complete=None):
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
    
    # Process views one at a time with callback
    summaries = []
    
    for view_id in view_ids:
        try:
            # Process single view
            results = process_document_views(doc, [view_id], cfg)
            
            if results and len(results) > 0:
                view_result = results[0]
                
                # Call user callback
                on_view_complete(view_result)
                
                # Retain only lightweight summary
                summary = {
                    "view_id": view_result.get("view_id"),
                    "view_name": view_result.get("view_name"),
                    "width": view_result.get("width"),
                    "height": view_result.get("height"),
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
    
    return summaries


def run_vop_pipeline_streaming(doc, view_ids, cfg=None, output_dir=None, 
                                export_png=True, export_csv=True, export_json=False,
                                pixels_per_cell=4, date_override=None):
    """Run VOP pipeline with streaming export to minimize memory usage.
    
    This is the recommended entry point for large view sets where memory
    accumulation of raster data would be problematic.
    
    Args:
        doc: Revit Document
        view_ids: List of view IDs to process
        cfg: Config object (optional)
        output_dir: Output directory (default: C:\\temp\\vop_output)
        export_png: Export PNGs as views complete (default: True)
        export_csv: Export CSV rows incrementally (default: True)
        export_json: Export full JSON at end (default: False - memory-heavy)
        pixels_per_cell: PNG resolution (default: 4)
        date_override: Optional date for CSV export
        
    Returns:
        Dict with export summary:
        {
            'views_processed': int,
            'views_failed': int,
            'png_files': [paths],
            'core_csv_path': str,
            'vop_csv_path': str,
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
    
    # Defaults
    if cfg is None:
        cfg = Config()
    
    if output_dir is None:
        output_dir = r"C:\temp\vop_output"
    
    # Disable view-level caching for streaming mode
    # The cache stores the full view_result dict (including all raster arrays),
    # which defeats streaming's memory optimization.
    #
    # CRITICAL: Must set BOTH view_cache_enabled=False AND view_cache_dir=None
    # because pipeline checks both conditions:
    #   - If view_cache_dir exists, pipeline may still write cache
    #   - Config default is view_cache_enabled=True
    #
    # Save original setting in case user wants to check it
    original_cache_enabled = getattr(cfg, 'view_cache_enabled', False)
    original_cache_dir = getattr(cfg, 'view_cache_dir', None)
    
    cfg.view_cache_enabled = False
    cfg.view_cache_dir = None
    
    if original_cache_enabled or original_cache_dir:
        print("[Streaming] Note: View-level caching disabled for streaming mode")
        print(f"[Streaming]   Original: enabled={original_cache_enabled}, dir={original_cache_dir}")
        print(f"[Streaming]   Use batch mode if you need caching for iterative development")
    
    # Initialize streaming exporter
    exporter = StreamingExporter(
        output_dir=output_dir,
        cfg=cfg,
        doc=doc,
        export_png=export_png,
        export_csv=export_csv,
        export_json=export_json,
        pixels_per_cell=pixels_per_cell,
        date_override=date_override
    )
    
    # Process with streaming callback
    t0 = time.perf_counter()
    
    process_document_views_streaming(
        doc, 
        view_ids, 
        cfg,
        on_view_complete=exporter.on_view_complete
    )
    
    t1 = time.perf_counter()
    
    # Finalize and get results
    result = exporter.finalize()
    result["total_time_sec"] = t1 - t0
    
    print(f"\n[Streaming] Complete:")
    print(f"  Processed: {result['views_processed']} views")
    print(f"  Failed: {result['views_failed']} views")
    print(f"  PNGs: {len(result['png_files'])} files")
    print(f"  CSVs: {result['csv_rows_written']} rows")
    print(f"  Time: {result['total_time_sec']:.1f}s")
    
    return result