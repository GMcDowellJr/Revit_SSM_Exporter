"""
VOP Interwoven Pipeline for Revit SSM/VOP Export.

This module implements the interwoven occlusion-aware rasterization pipeline
described in the VOP specification. Core principles:

1. 3D model geometry is the ONLY occlusion truth
2. 2D annotation NEVER occludes model
3. Heavy work (triangles, depth-buffer) reserved for AreaL elements
4. Tiny/Linear elements emit proxies (UV_AABB/OBB) to avoid heavy geometry
5. Early-out is safe only against depth-aware occlusion buffers

Modules:
- config: Configuration for tile size, proxy modes, depth tolerance
- core.raster: ViewRaster and TileMap data structures
- core.geometry: UV classification and proxy generation
- core.math_utils: Geometric utilities for bounds and rectangles
- revit: Revit-specific element collection and view basis extraction
- pipeline: Main interwoven model pass (ProcessDocumentViews)
- entry_dynamo: Dynamo entry point for testing

"""

__version__ = "1.0.0"
__author__ = "Claude Code"

from .config import Config

__all__ = ["Config"]
