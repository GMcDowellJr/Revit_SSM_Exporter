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

        # Build fallback memory map from run-level memory_tracker marks if present.
        mem_by_view = {}
        try:
            tracker_marks = []
            for vr0 in view_results or []:
                if isinstance(vr0, dict) and isinstance(vr0.get("memory_tracker"), list):
                    tracker_marks = vr0.get("memory_tracker") or []
                    if tracker_marks:
                        break
            for m in tracker_marks:
                if not isinstance(m, dict):
                    continue
                lbl = str(m.get("label") or "")
                priv = m.get("priv_mb")
                if "view_start_" in lbl:
                    try:
                        vid = int(lbl.split("view_start_")[1].split()[0])
                        mem_by_view.setdefault(vid, {})["start_priv_mb"] = priv
                    except Exception as e:
                        print("[perf_export] parse view_start label failed: {}".format(e))
                elif "after_clr_gc_" in lbl and "[post-GC]" in lbl:
                    try:
                        vid = int(lbl.split("after_clr_gc_")[1].split()[0])
                        mem_by_view.setdefault(vid, {})["end_priv_mb"] = priv
                    except Exception as e:
                        print("[perf_export] parse after_clr_gc label failed: {}".format(e))
            for vid, mm in mem_by_view.items():
                s = mm.get("start_priv_mb")
                e = mm.get("end_priv_mb")
                mm["delta_priv_mb"] = (None if s is None or e is None else (float(e) - float(s)))
        except Exception as e:
            print("[perf_export] memory fallback map failed: {}".format(e))

        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for vr in view_results or []:
                t = vr.get("timings", {}) if isinstance(vr, dict) else {}
                mem = vr.get("memory", {}) if isinstance(vr, dict) else {}
                vid = vr.get("view_id", "") if isinstance(vr, dict) else ""
                if (not mem or mem.get("start_priv_mb") is None) and isinstance(vid, int) and vid in mem_by_view:
                    mem = mem_by_view.get(vid, {})
                row = {
                    "RunId": run_id,
                    "Date": date_value,
                    "ViewId": vid,
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
