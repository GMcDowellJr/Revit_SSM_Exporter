"""Per-run performance CSV export."""

from __future__ import annotations

import csv
import os
from datetime import datetime


PERF_EXPORT_COLUMNS = [
    "RunId", "Date", "ViewId", "ViewName", "ElementCount", "ArealCount", "LinearCount", "TinyCount",
    "FallbackRatePct", "TotalMs", "CollectMs", "GeomExtractMs", "GeomSilhouetteMs", "GeomObbMs", "GeomBboxMs",
    "RasterModelMs", "RasterAnnoMs", "PngMs", "CsvMs", "CacheReadMs", "CacheWriteMs", "GcMs",
    "MsPerElement", "MsPerAreal", "MsPer1kCells", "RasterCells",
    "MemStartPrivMb", "MemEndPrivMb", "MemDeltaPrivMb",
]


def _find_view_mem(records, view_id):
    start = None
    end = None
    try:
        for rec in records or []:
            label = str(rec.get("label", ""))
            if label == "view_start_{}".format(view_id):
                start = rec.get("priv_mb")
            if label == "after_clr_gc_{} [post-GC]".format(view_id):
                end = rec.get("priv_mb")
    except Exception as e:
        print("[WARN] perf_export._find_view_mem failed: {}".format(e))
    return start, end


def export_perf_csv(results, output_dir=None, run_id=None, date_str=None, memory_records=None):
    now = datetime.now()
    rid = run_id or now.strftime("%Y%m%dT%H%M%S")
    dstr = date_str or now.strftime("%Y-%m-%d")

    out_dir = output_dir or os.getcwd()
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "perf_{}.csv".format(now.strftime("%Y-%m-%d_%H%M%S")))

    try:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=PERF_EXPORT_COLUMNS)
            w.writeheader()
            for r in results or []:
                timings = r.get("timings", {}) if isinstance(r, dict) else {}
                view_id = r.get("view_id") if isinstance(r, dict) else None
                mem_start, mem_end = _find_view_mem(memory_records, view_id)
                row = {
                    "RunId": rid,
                    "Date": dstr,
                    "ViewId": view_id,
                    "ViewName": r.get("view_name") if isinstance(r, dict) else "",
                    "ElementCount": timings.get("element_count"),
                    "ArealCount": timings.get("areal_count"),
                    "LinearCount": timings.get("linear_count"),
                    "TinyCount": timings.get("tiny_count"),
                    "FallbackRatePct": timings.get("fallback_rate_pct"),
                    "TotalMs": timings.get("total_ms"),
                    "CollectMs": timings.get("collect_ms"),
                    "GeomExtractMs": timings.get("geom_extract_ms"),
                    "GeomSilhouetteMs": timings.get("geom_silhouette_ms"),
                    "GeomObbMs": timings.get("geom_obb_ms"),
                    "GeomBboxMs": timings.get("geom_bbox_ms"),
                    "RasterModelMs": timings.get("raster_model_ms"),
                    "RasterAnnoMs": timings.get("raster_anno_ms"),
                    "PngMs": timings.get("png_ms"),
                    "CsvMs": timings.get("csv_ms"),
                    "CacheReadMs": timings.get("cache_read_ms"),
                    "CacheWriteMs": timings.get("cache_write_ms"),
                    "GcMs": timings.get("gc_ms"),
                    "MsPerElement": timings.get("ms_per_element"),
                    "MsPerAreal": timings.get("ms_per_areal"),
                    "MsPer1kCells": timings.get("ms_per_1k_cells"),
                    "RasterCells": timings.get("raster_cells"),
                    "MemStartPrivMb": mem_start,
                    "MemEndPrivMb": mem_end,
                    "MemDeltaPrivMb": None if (mem_start is None or mem_end is None) else float(mem_end) - float(mem_start),
                }
                w.writerow(row)
    except Exception as e:
        print("[WARN] perf_export.export_perf_csv failed: {}".format(e))
    return path
