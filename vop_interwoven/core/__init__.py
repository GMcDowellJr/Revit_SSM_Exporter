"""
Core data structures and algorithms for VOP interwoven pipeline.

Modules:
- raster: ViewRaster and TileMap classes for occlusion tracking
- geometry: UV classification and proxy generation (TINY/LINEAR/AREAL)
- math_utils: Bounds, rectangle operations, and geometric primitives
"""

from .raster import ViewRaster, TileMap
from .geometry import Mode, classify_by_uv, make_uv_aabb, make_obb_or_skinny_aabb

__all__ = [
    "ViewRaster",
    "TileMap",
    "Mode",
    "classify_by_uv",
    "make_uv_aabb",
    "make_obb_or_skinny_aabb",
]
