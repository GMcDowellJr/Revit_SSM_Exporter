"""Per-run performance CSV export."""

import csv
import os
from datetime import datetime


def export_perf_csv(output_dir, view_results, run_id=None, date_value=None):
    try:
        if run_id is None:
            run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
        if date_value is None:
            date_value = datetime.now().strftime("%Y-%m-%d")

        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, "perf_{}.csv".format(ts))

        cols = [
            "RunId", "Date", "ViewId", "ViewName", "ElementCount", "ArealCount", "LinearCount", "TinyCount",
            "FallbackRatePct", "TotalMs", "CollectMs", "GeomExtractMs", "GeomSilhouetteMs", "GeomObbMs", "GeomBboxMs",
            "RasterModelMs", "RasterAnnoMs", "PngMs", "CsvMs", "CacheReadMs", "CacheWriteMs", "GcMs",
            "MsPerElement", "MsPerAreal", "MsPer1kCells", "RasterCells",
            "MemStartPrivMb", "MemEndPrivMb", "MemDeltaPrivMb",
        ]

        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for vr in view_results or []:
                t = vr.get("timings", {}) if isinstance(vr, dict) else {}
                mem = vr.get("memory", {}) if isinstance(vr, dict) else {}
                row = {
                    "RunId": run_id,
                    "Date": date_value,
                    "ViewId": vr.get("view_id", "") if isinstance(vr, dict) else "",
                    "ViewName": vr.get("view_name", "") if isinstance(vr, dict) else "",
                    "ElementCount": t.get("element_count"),
                    "ArealCount": t.get("areal_count"),
                    "LinearCount": t.get("linear_count"),
                    "TinyCount": t.get("tiny_count"),
                    "FallbackRatePct": t.get("fallback_rate_pct"),
                    "TotalMs": t.get("total_ms"),
                    "CollectMs": t.get("collect_ms"),
                    "GeomExtractMs": t.get("geom_extract_ms"),
                    "GeomSilhouetteMs": t.get("geom_silhouette_ms"),
                    "GeomObbMs": t.get("geom_obb_ms"),
                    "GeomBboxMs": t.get("geom_bbox_ms"),
                    "RasterModelMs": t.get("raster_model_ms"),
                    "RasterAnnoMs": t.get("raster_anno_ms"),
                    "PngMs": t.get("png_ms"),
                    "CsvMs": t.get("csv_ms"),
                    "CacheReadMs": t.get("cache_read_ms"),
                    "CacheWriteMs": t.get("cache_write_ms"),
                    "GcMs": t.get("gc_ms"),
                    "MsPerElement": t.get("ms_per_element"),
                    "MsPerAreal": t.get("ms_per_areal"),
                    "MsPer1kCells": t.get("ms_per_1k_cells"),
                    "RasterCells": t.get("raster_cells"),
                    "MemStartPrivMb": mem.get("start_priv_mb"),
                    "MemEndPrivMb": mem.get("end_priv_mb"),
                    "MemDeltaPrivMb": mem.get("delta_priv_mb"),
                }
                w.writerow(row)
        return path
    except Exception as e:
        print("[perf_export] export_perf_csv failed: {}".format(e))
        return None
