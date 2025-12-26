# types.py
# Phase 1, Sprint 1: MOVE ONLY (type definitions and constants)
#
# Extracted constants for SSM Exporter occupancy states, tier names, and other
# shared type definitions.

"""
Type definitions and constants for SSM Exporter.

This module contains:
- Occupancy state codes (3D-only, 2D-only, overlap)
- Element size tier names
- Other shared constants
"""

# ============================================================
# OCCUPANCY STATE CODES
# ============================================================
# These codes are assigned to each grid cell based on the presence
# of 3D geometry and/or 2D annotations.

# Cell contains only 3D model geometry
OCCUPANCY_CODE_3D_ONLY = 0

# Cell contains only 2D annotations
OCCUPANCY_CODE_2D_ONLY = 1

# Cell contains both 3D geometry and 2D annotations (overlap)
OCCUPANCY_CODE_2D_OVER_3D = 2

# ============================================================
# ELEMENT SIZE TIER NAMES
# ============================================================
# Tier names used in silhouette extraction strategy selection.
# Elements are classified by projected size (in cells) and assigned
# a tier, which determines which extraction strategies are attempted.

TIER_TINY_LINEAR = "tier_tiny_linear"
TIER_MEDIUM = "tier_medium"
TIER_LARGE = "tier_large"
TIER_VERY_LARGE = "tier_very_large"

# ============================================================
# EXTRACTION STRATEGY NAMES
# ============================================================
# Strategy identifiers for silhouette extraction

STRATEGY_BBOX = "bbox"
STRATEGY_OBB = "obb"
STRATEGY_SILHOUETTE_EDGES = "silhouette_edges"
STRATEGY_CATEGORY_API = "category_api_shortcuts"
STRATEGY_COARSE_TESS = "coarse_tessellation"

# ============================================================
# SOURCE IDENTIFIERS
# ============================================================
# Element source types

SOURCE_HOST = "HOST"
SOURCE_RVT_LINK = "RVT_LINK"
SOURCE_DWG_IMPORT = "DWG_IMPORT"
