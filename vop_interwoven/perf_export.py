"""Per-run performance CSV export."""

from __future__ import annotations

import csv
import os
from datetime import datetime

from .csv_export import get_perf_csv_header, view_result_to_perf_row


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
    path = os.path.join(out_dir, "views_perf_{}.csv".format(dstr))

    try:
        file_exists = os.path.exists(path)
        file_empty = (not file_exists) or (os.path.getsize(path) == 0)
        with open(path, "a", newline="", encoding="utf-8") as f:
            header = get_perf_csv_header()
            w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
            if file_empty:
                w.writeheader()

            for r in results or []:
                if not isinstance(r, dict):
                    continue
                row = view_result_to_perf_row(r, date_override=dstr, run_id=rid) or {}
                view_id = r.get("view_id")
                mem_start, mem_end = _find_view_mem(memory_records, view_id)
                row.setdefault("MemStartPrivMb", mem_start)
                row.setdefault("MemEndPrivMb", mem_end)
                if row.get("MemDeltaPrivMb") in (None, ""):
                    if (mem_start is not None) and (mem_end is not None):
                        row["MemDeltaPrivMb"] = float(mem_end) - float(mem_start)
                w.writerow(row)
    except Exception as e:
        print("[WARN] perf_export.export_perf_csv failed: {}".format(e))
    return path
