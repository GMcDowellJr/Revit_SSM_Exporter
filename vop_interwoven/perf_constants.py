"""Performance/memory diagnostics key constants."""

TIMING_KEYS = {
    "MODE_MS": "mode_ms",
    "COLLECT_MS": "collect_ms",
    "BBOX_MS": "bbox_ms",
    "CLASSIFY_MS": "classify_ms",
    "GEOM_EXTRACT_MS": "geom_extract_ms",
    "GEOM_SILHOUETTE_MS": "geom_silhouette_ms",
    "GEOM_OBB_MS": "geom_obb_ms",
    "GEOM_BBOX_MS": "geom_bbox_ms",
    "RASTER_MODEL_MS": "raster_model_ms",
    "RASTER_ANNO_MS": "raster_anno_ms",
    "PNG_MS": "png_ms",
    "CSV_MS": "csv_ms",
    "CACHE_READ_MS": "cache_read_ms",
    "CACHE_WRITE_MS": "cache_write_ms",
    "ELEM_CACHE_MS": "elem_cache_ms",
    "GC_MS": "gc_ms",
    "TOTAL_MS": "total_ms",
}

METRIC_KEYS = {
    "ELEMENT_COUNT": "element_count",
    "AREAL_COUNT": "areal_count",
    "LINEAR_COUNT": "linear_count",
    "TINY_COUNT": "tiny_count",
    "MS_PER_ELEMENT": "ms_per_element",
    "MS_PER_AREAL": "ms_per_areal",
    "RASTER_CELLS": "raster_cells",
    "MS_PER_1K_CELLS": "ms_per_1k_cells",
    "FALLBACK_RATE_PCT": "fallback_rate_pct",
}
