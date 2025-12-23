# config.py
# Phase 1, Sprint 3: MOVE ONLY (configuration dictionary)
#
# Extracted CONFIG dictionary defining all exporter behavior and settings.

"""
Configuration for SSM Exporter.

This module defines the CONFIG dictionary which controls all aspects of the
exporter's behavior including grid sizing, projection settings, silhouette
extraction strategies, occupancy codes, export paths, and debug options.
"""

import os
from . import types as exporter_types


CONFIG = {
    "grid": {
        # Paper size of a cell (inches) -> converted to model via view.Scale
        "cell_size_paper_in": 0.125,    # 1/8" paper
        "max_cells": 200000,           # hard safety cap per view
    },
    "clip_volume": {
        # Optional refinement: use cut-plane slab instead of full conservative slab
        # True  = Plans: CUT_DOWN, RCPs: CUT_UP
        "use_cut_slab": False,
    },
    "projection": {
        "include_3d": True,
        "include_2d": True,
        "include_link_3d": True,

        "silhouette": {
            "enabled": True,
            "enable_obb": True,
            "enable_silhouette_edges": True,
            "enable_coarse_tessellation": True,
            "enable_category_api_shortcuts": True,

            # === ADAPTIVE THRESHOLDS (NEW!) ===
            "use_adaptive_thresholds": True,     # ← Set to True to enable

            # Percentile settings
            "adaptive_percentile_tiny": 25,       # 25th percentile
            "adaptive_percentile_medium": 50,     # 50th percentile (median)
            "adaptive_percentile_large": 75,      # 75th percentile

            # Winsorization (outlier removal)
            "adaptive_winsorize": True,           # Remove outliers
            "adaptive_winsorize_lower": 5,        # Drop bottom 5%
            "adaptive_winsorize_upper": 95,       # Drop top 5%

            # Minimum thresholds (safety floor)
            "adaptive_min_tiny": 1,               # Never lower than 1 cell
            "adaptive_min_medium": 3,             # Never lower than 3 cells
            "adaptive_min_large": 10,             # Never lower than 10 cells

            # Maximum thresholds (safety ceiling)
            "adaptive_max_tiny": 5,               # Never higher than 5 cells
            "adaptive_max_medium": 20,            # Never higher than 20 cells
            "adaptive_max_large": 100,            # Never higher than 100 cells

            # Fallback to fixed if too few elements
            "adaptive_min_elements": 50,          # Need 50+ elements to use adaptive

            # Fixed thresholds (used as fallback)
            "tiny_linear_threshold_cells": 2,
            "medium_threshold_cells": 10,
            "large_threshold_cells": 50,

            # === SCALE-AWARE: ABSOLUTE THRESHOLDS ===
            "use_absolute_thresholds": False,
            "auto_adjust_for_scale": False,
            "tiny_linear_threshold_ft": 2.0,
            "medium_threshold_ft": 5.0,
            "large_threshold_ft": 10.0,

            "coarse_tess_max_verts_per_face": 20,
            "coarse_tess_triangulate_param": 0.5,

            "simple_categories": [
                "Walls", "Structural Framing", "Structural Columns",
                "Columns", "Beams", "Doors", "Windows"
            ],

            exporter_types.TIER_TINY_LINEAR: [exporter_types.STRATEGY_BBOX],
            exporter_types.TIER_MEDIUM: [exporter_types.STRATEGY_CATEGORY_API, exporter_types.STRATEGY_OBB, exporter_types.STRATEGY_BBOX],
            exporter_types.TIER_LARGE: [exporter_types.STRATEGY_CATEGORY_API, exporter_types.STRATEGY_OBB, exporter_types.STRATEGY_BBOX],
            exporter_types.TIER_VERY_LARGE: [exporter_types.STRATEGY_CATEGORY_API, exporter_types.STRATEGY_COARSE_TESS, exporter_types.STRATEGY_OBB, exporter_types.STRATEGY_BBOX],

            "category_first": True,
            "track_strategy_usage": True,
        },
    },
    "occupancy_png": {
        # turn on/off PNG export
        "enabled": False,
        # how many pixels each grid cell should occupy (both width & height)
        "pixels_per_cell": 10
    },
    "regions": {
        "tiny_max_w": 2,
        "tiny_max_h": 2,
        "linear_band_thickness_cells": 1,

        # Default = 1x1 cells → holes <= 1 cell in both directions are
        # smoothed away.
        "min_hole_size_w_cells": 1,
        "min_hole_size_h_cells": 1,

        # (Floors / Roofs / Ceilings / Structural Foundations).
        # If True, those elements are completely excluded from 3D regions.
        "suppress_floor_roof_ceiling_3d": False,
    },

    "raster": {
        "enable_areal_fill": True,
        "enable_linear_fill": True,
    },
    "occupancy": {
        "code_3d_only": exporter_types.OCCUPANCY_CODE_3D_ONLY,
        "code_2d_only": exporter_types.OCCUPANCY_CODE_2D_ONLY,
        "code_2d_over_3d": exporter_types.OCCUPANCY_CODE_2D_OVER_3D,
    },
    "run": {
        "max_views": None,             # optional clamp for debug
    },
    "cache": {
        # exporter + config + project are unchanged.
        "enabled": True,
        "file_name": "grid_cache.json",
    },

    "export": {
        # Root folder – default to ~/Documents/_metrics
        "output_dir": os.path.join(os.path.expanduser("~"), "Documents", "_metrics"),

        # Base filenames (we'll append _YYYY-MM-DD.csv)
        # → views_core_2025-10-25.csv
        # → views_vop_2025-10-25.csv
        "core_filename": "views_core.csv",
        "vop_filename": "views_vop.csv",
        "csv_timings": "timings.csv",
        # Toggle for CSV export
        "enable_rows_csv": True,
    },

    "debug": {
        "enable": True,
        "write_debug_json": False,

        "max_debug_views": 10,              # hard cap on views in debug JSON
        "min_elapsed_for_debug_sec": 999.0,   # only keep debug if view took >= this
        "debug_view_ids": [],               # explicit view ids to always include
        "include_cached_views": False,      # usually we only care about non-cached

        # Silhouette strategy fallback logging
        "log_bbox_fallbacks": False,        # log elements that fall back to bbox with reasons

        "log_large_3d_regions": False,
        "large_region_fraction": 0.8,

        # projection preview polys
        "enable_preview_polys": False,      # <- turn ON when you want geometry
        "max_preview_projected_2d": 32,
        "max_preview_projected_3d": 64,

        # region preview controls (False later; harmless now)
        "enable_region_previews": False,
        "max_region_areal_cells": 2048,

        "include_run_log_in_out": False,
        "filled_region_loops": False,       # enable/disable this debug
        "filled_region_loops_max": 5,      # max filled regions to log per run
        "floor_loops": False,          # enable per-floor loop debug
        "floor_loops_max": 5,          # max 3D floor-like elems to log

    },
}
