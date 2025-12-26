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
            exporter_types.TIER_LARGE: [exporter_types.STRATEGY_CATEGORY_API, exporter_types.STRATEGY_SILHOUETTE_EDGES, exporter_types.STRATEGY_OBB, exporter_types.STRATEGY_BBOX],
            exporter_types.TIER_VERY_LARGE: [exporter_types.STRATEGY_CATEGORY_API, exporter_types.STRATEGY_SILHOUETTE_EDGES, exporter_types.STRATEGY_COARSE_TESS, exporter_types.STRATEGY_OBB, exporter_types.STRATEGY_BBOX],

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
        # Master switch
        "enable": True,

        # === DIAGNOSTIC LOGGING ===
        "logging": {
            # Exception/error tracking
            "grid_exceptions": False,          # Grid geometry collection failures
            "bbox_fallbacks": False,           # Elements falling back to bbox (with reasons)

            # Performance warnings
            "error_budget_warnings": True,     # Extraction failure rate warnings
            "extraction_failure_threshold": 0.1,  # 10% failure threshold
            "large_regions": False,            # Regions > X% of grid
            "large_region_threshold": 0.8,     # 80% threshold

            # Element-level detail logging
            "filled_region_loops": False,
            "filled_region_loops_max": 5,
            "floor_loops": False,
            "floor_loops_max": 5,
            "driver2d_debug": True,            # 2D annotation extent drivers
            "driver2d_log_once_per_signature": False,  # Dedupe by view signature
        },

        # === GEOMETRY PREVIEWS (Dynamo output) ===
        "previews": {
            "enable_polys": False,
            "max_projected_2d": 32,
            "max_projected_3d": 64,
            "enable_regions": False,
            "max_region_cells": 2048,
        },

        # === FILE EXPORTS ===
        "exports": {
            "debug_json": False,               # Export debug.json with view details
            "max_debug_views": 10,             # Cap on debug JSON entries
            "min_elapsed_sec": 999.0,          # Only include slow views
            "debug_view_ids": [],              # Explicit view IDs to include
            "include_cached_views": False,     # Include cached views in debug
            "include_run_log": False,          # Add log to output dict
        },

        # === BACKWARD COMPATIBILITY (deprecated, will be removed) ===
        # Old flat keys still work but map to new structure
        "write_debug_json": False,
        "max_debug_views": 10,
        "min_elapsed_for_debug_sec": 999.0,
        "debug_view_ids": [],
        "include_cached_views": False,
        "log_bbox_fallbacks": False,
        "log_grid_exceptions": False,
        "enable_error_budget_warnings": True,
        "extraction_failure_threshold": 0.1,
        "log_large_3d_regions": False,
        "large_region_fraction": 0.8,
        "enable_preview_polys": False,
        "max_preview_projected_2d": 32,
        "max_preview_projected_3d": 64,
        "enable_region_previews": False,
        "max_region_areal_cells": 2048,
        "include_run_log_in_out": False,
        "filled_region_loops": False,
        "filled_region_loops_max": 5,
        "floor_loops": False,
        "floor_loops_max": 5,
        "enable_driver2d_debug": True,
        "driver2d_log_once_per_signature": False,
    },
}


def validate_config(config, logger=None):
    """
    Validate configuration dictionary for type correctness and reasonable ranges.

    Args:
        config: Configuration dictionary to validate
        logger: Optional logger for warnings (if None, prints to console)

    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []

    def log_error(msg):
        errors.append(msg)
        if logger:
            logger.warn("Config validation: {0}".format(msg))
        else:
            print("WARNING: Config validation: {0}".format(msg))

    # Grid validation
    grid_cfg = config.get("grid", {})

    cell_size = grid_cfg.get("cell_size_paper_in")
    if cell_size is not None:
        try:
            cell_size_float = float(cell_size)
            if cell_size_float <= 0.0:
                log_error("grid.cell_size_paper_in must be > 0, got {0}".format(cell_size))
            elif cell_size_float > 2.0:
                log_error("grid.cell_size_paper_in unusually large: {0} inches".format(cell_size))
        except (TypeError, ValueError):
            log_error("grid.cell_size_paper_in must be numeric, got {0}".format(type(cell_size).__name__))

    max_cells = grid_cfg.get("max_cells")
    if max_cells is not None:
        try:
            max_cells_int = int(max_cells)
            if max_cells_int <= 0:
                log_error("grid.max_cells must be > 0, got {0}".format(max_cells))
            elif max_cells_int > 10000000:
                log_error("grid.max_cells extremely large: {0} (risk of memory issues)".format(max_cells))
        except (TypeError, ValueError):
            log_error("grid.max_cells must be numeric, got {0}".format(type(max_cells).__name__))

    # Projection validation
    proj_cfg = config.get("projection", {})

    for key in ["include_3d", "include_2d", "include_link_3d"]:
        value = proj_cfg.get(key)
        if value is not None and not isinstance(value, bool):
            log_error("projection.{0} must be boolean, got {1}".format(key, type(value).__name__))

    # Silhouette validation
    sil_cfg = proj_cfg.get("silhouette", {})

    for key in ["enabled", "enable_obb", "enable_silhouette_edges", "enable_coarse_tessellation", "enable_category_api_shortcuts"]:
        value = sil_cfg.get(key)
        if value is not None and not isinstance(value, bool):
            log_error("projection.silhouette.{0} must be boolean, got {1}".format(key, type(value).__name__))

    # Threshold validation
    for key in ["tiny_linear_threshold_cells", "medium_threshold_cells", "large_threshold_cells"]:
        value = sil_cfg.get(key)
        if value is not None:
            try:
                val_int = int(value)
                if val_int < 0:
                    log_error("projection.silhouette.{0} must be >= 0, got {1}".format(key, value))
            except (TypeError, ValueError):
                log_error("projection.silhouette.{0} must be numeric, got {1}".format(key, type(value).__name__))

    # Occupancy PNG validation
    png_cfg = config.get("occupancy_png", {})

    pixels_per_cell = png_cfg.get("pixels_per_cell")
    if pixels_per_cell is not None:
        try:
            ppc_int = int(pixels_per_cell)
            if ppc_int <= 0:
                log_error("occupancy_png.pixels_per_cell must be > 0, got {0}".format(pixels_per_cell))
            elif ppc_int > 100:
                log_error("occupancy_png.pixels_per_cell very large: {0} (may create huge PNGs)".format(pixels_per_cell))
        except (TypeError, ValueError):
            log_error("occupancy_png.pixels_per_cell must be numeric, got {0}".format(type(pixels_per_cell).__name__))

    # Regions validation
    regions_cfg = config.get("regions", {})

    for key in ["tiny_max_w", "tiny_max_h", "linear_band_thickness_cells"]:
        value = regions_cfg.get(key)
        if value is not None:
            try:
                val_num = float(value)
                if val_num < 0:
                    log_error("regions.{0} must be >= 0, got {1}".format(key, value))
            except (TypeError, ValueError):
                log_error("regions.{0} must be numeric, got {1}".format(key, type(value).__name__))

    # Export validation
    export_cfg = config.get("export", {})

    output_dir = export_cfg.get("output_dir")
    if output_dir is not None and not isinstance(output_dir, str):
        log_error("export.output_dir must be string, got {0}".format(type(output_dir).__name__))

    # Debug validation
    debug_cfg = config.get("debug", {})

    max_debug_views = debug_cfg.get("max_debug_views")
    if max_debug_views is not None:
        try:
            mdv_int = int(max_debug_views)
            if mdv_int < 0:
                log_error("debug.max_debug_views must be >= 0, got {0}".format(max_debug_views))
        except (TypeError, ValueError):
            log_error("debug.max_debug_views must be numeric, got {0}".format(type(max_debug_views).__name__))

    return errors

