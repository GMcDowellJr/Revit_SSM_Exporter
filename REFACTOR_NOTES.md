# VOP Interwoven Pipeline - Refactor Notes

## Overview

This document describes the core architectural changes made to enforce correct depth-based occlusion and per-source occupancy tracking in the VOP interwoven pipeline.

## 1. W-Depth Definition

### What is W-Depth?

**W-depth** is the view-space depth coordinate computed from the view's local coordinate system (UVW).

```
Given:
- View origin: O
- View basis vectors: (right, up, forward)
- World point: P

Transform to view space:
U = dot(P - O, right)   # Horizontal position in view
V = dot(P - O, up)      # Vertical position in view
W = dot(P - O, forward) # DEPTH into view (toward viewer)
```

**Critical invariant**: W-depth is the ONLY depth metric used throughout the pipeline.

### Why W (not Z)?

- **Z** refers to world-space vertical coordinate (gravity-aligned)
- **W** refers to view-space depth (view-direction-aligned)

For non-plan views (sections, elevations, 3D views), Z ≠ W. Using Z for depth in these views produces incorrect occlusion.

### Implementation

- `ViewBasis.transform_to_view_uvw(point_xyz)` returns `(u, v, w)`
- **All depth comparisons use W-coordinate**
- **All depth storage uses W-coordinate**

## 2. Global Per-Cell Occlusion Depth Buffer

### Structure

**`ViewRaster.w_occ[u,v]`**: Nearest W-depth per cell (initialized to `+∞`)

```python
class ViewRaster:
    w_occ: Float array [W*H]  # Nearest W-depth per cell
    occ_host: Boolean array [W*H]  # Host element occupancy (depth-tested)
    occ_link: Boolean array [W*H]  # Linked RVT occupancy (depth-tested)
    occ_dwg: Boolean array [W*H]   # DWG/DXF occupancy (depth-tested)
```

### Tile Acceleration

**`TileMap.w_min_tile[tile_idx]`**: Minimum W-depth per tile (for early-out)

```python
class TileMap:
    w_min_tile: Float array [tiles_x * tiles_y]  # Per-tile min W-depth
    filled_count: Int array [tiles_x * tiles_y]  # Per-tile fill count
```

## 3. TryWriteCell Contract (MANDATORY)

### The Contract

**ALL rasterization MUST route through `try_write_cell()`**:

```python
def try_write_cell(u, v, w_depth, source):
    """
    Args:
        u, v: Cell coordinates
        w_depth: View-space W-depth
        source: "HOST", "LINK", or "DWG"

    Returns:
        True if depth won, False if rejected

    Behavior:
        IF w_depth < w_occ[u,v]:
            w_occ[u,v] = w_depth
            Mark EXACTLY ONE occupancy layer: occ_host OR occ_link OR occ_dwg
            Update tile acceleration
            Return True
        ELSE:
            Return False (element is behind existing geometry)
    """
```

### Guarantees

1. **Behind geometry never marks occupancy**: Elements failing depth test are rejected
2. **Per-source layers only mark winning depth**: Whichever source is nearest wins
3. **All sources share same w_occ buffer**: Unified occlusion across host/link/DWG

### Implementation Points

- **Silhouette fill**: `_scanline_fill()` calls `try_write_cell()` for interior
- **Open polylines**: `rasterize_open_polylines()` calls `try_write_cell()` for curves
- **Bbox fallback**: Direct `try_write_cell()` calls for simple rects

### Debugging

Depth test statistics available:
- `raster.depth_test_attempted`: Total write attempts
- `raster.depth_test_wins`: Writes that won depth test
- `raster.depth_test_rejects`: Writes rejected by depth test

Printed in pipeline summary for monitoring.

## 4. Tier-A vs Tier-B Decision Logic

### Current State: Tier-A Only (with PCA)

The current implementation uses **Tier-A classification with PCA-based oriented extents**:

```python
def _determine_uv_mode(elem, view, view_basis, raster, cfg):
    # 1. Get bbox corners in world space
    # 2. Transform to UVW
    # 3. Project to UV plane
    # 4. Run PCA to find oriented extents (lu_ft, lv_ft)
    # 5. Convert to cells: U = lu_ft / cell_size, V = lv_ft / cell_size
    # 6. Classify:
    if U <= tiny_max and V <= tiny_max:
        return 'TINY'
    elif min(U, V) <= thin_max:
        return 'LINEAR'  # Diagonal beams correctly classify!
    else:
        return 'AREAL'
```

**Key fix**: PCA orientation handles diagonal elements correctly. A 12" wide beam rotated 45° will have:
- AABB: ~17" x ~17" (AREAL - wrong!)
- OBB via PCA: ~12" x ~100' (LINEAR - correct!)

### Future: Tier-B Geometry Sampling (Adaptive)

Tier-B would add geometry-based fast approximation for ambiguous cases:

**Ambiguity conditions (configurable)**:
1. **Thickness ambiguity**: Element is "near" the LINEAR threshold
   ```
   margin_cells = clamp(1, round(cell_size / cell_size_ref), 4)
   Ambiguous if: thin_max < minor_cells <= thin_max + margin_cells
   ```

2. **Large area ambiguity**: Element is very large relative to grid
   ```
   area_thresh = clamp(50, round(f * grid_area), 2000)
   Ambiguous if: aabb_area_cells >= area_thresh
   ```

**Tier-B process** (when triggered):
1. Sample geometry: mesh vertices, curve tessellation
2. Transform to UVW
3. Extract UV point set
4. Run PCA + convex hull for footprint
5. Use footprint for classification and rasterization

**Configuration**:
```python
cfg.tierb_cell_size_ref_ft = 1.0     # Reference cell size for scaling
cfg.tierb_area_fraction = 0.005      # Grid area fraction for large element detection
cfg.tierb_margin_cells_min = 1       # Min margin for thickness ambiguity
cfg.tierb_margin_cells_max = 4       # Max margin for thickness ambiguity
cfg.tierb_area_thresh_min = 50       # Min area threshold (cells)
cfg.tierb_area_thresh_max = 2000     # Max area threshold (cells)
```

**Current status**: Tier-B configuration added to `Config`, but full implementation deferred. Silhouette extraction serves as a form of geometry-based fallback.

## 5. Early-Out Occlusion Testing

### Depth-Aware Early-Out

```python
def _tiles_fully_covered_and_nearer(tile_map, rect, elem_min_w):
    """
    Element is guaranteed occluded if ALL tiles in its footprint are:
    1. Fully filled (no empty cells)
    2. Nearer than element (w_min_tile < elem_min_w)
    """
    for tile in tiles_overlapping(rect):
        if not tile.is_full():
            return False  # Gaps = not fully occluded
        if tile.w_min_tile >= elem_min_w:
            return False  # Not nearer = not occluded
    return True  # Safe to skip element
```

**Conservative correctness**: Only skips when provably behind existing geometry.

## 6. Debug Dump Functionality

### Enabling Debug Dumps

```python
cfg = Config(
    debug_dump_occlusion=True,
    debug_dump_path="/path/to/output/prefix"  # Optional, defaults to /tmp/vop_debug
)
```

### Output Files

For each view, generates:
- `{prefix}_{view}_w_occ.csv` - W-depth buffer (u, v, w_depth)
- `{prefix}_{view}_occ_host.csv` - Host occupancy mask (u, v, 1)
- `{prefix}_{view}_occ_link.csv` - Linked RVT occupancy (u, v, 1)
- `{prefix}_{view}_occ_dwg.csv` - DWG/DXF occupancy (u, v, 1)

### CSV Format

```csv
u,v,w_depth
0,0,12.500000
1,0,12.500000
```

Sparse format: only occupied cells included.

### Visualization

Load CSVs into spreadsheet or plotting tool:
- **w_occ**: Visualize as heatmap (near = dark, far = light)
- **occupancy layers**: Visualize as binary masks (1 = occupied, 0 = empty)

Useful for:
- Debugging occlusion ordering
- Verifying depth testing correctness
- Regression testing occupancy changes

## 7. Source Type Extraction

### Source Key Format

Elements are tagged with unique source keys:
- `"HOST"` - Host document elements
- `"RVT_LINK:{UniqueId}:{InstanceId}"` - Linked RVT elements (unique per instance)
- `"DWG_IMPORT:{Name}:{InstanceId}"` - DWG/DXF imports

### Extraction

```python
def _extract_source_type(doc_key):
    """
    "HOST" → "HOST"
    "RVT_LINK:abc-123:456" → "LINK"
    "DWG_IMPORT:Site:789" → "DWG"
    """
```

Used to route writes to correct occupancy layer in `try_write_cell()`.

## 8. Revit 2024+ Linked Collector Detection

### Improved Detection

```python
def _has_revit_2024_link_collector(doc, view):
    # 1. Find a real RevitLinkInstance (if available)
    # 2. Test with real link ID (more reliable)
    # 3. Distinguish TypeError (signature not found) vs other errors (signature exists, runtime issue)
    # 4. Log explicitly which path is active
```

**Key improvements**:
- Uses real `RevitLinkInstance.Id` instead of dummy ID
- Distinguishes missing signature from runtime errors
- Clear logging of detection results

## 9. Pipeline Stages

The refactored pipeline follows clear stages:

```
1. World → View (UVW) transform
   ↓
2. Tier A proxy (UV AABB + elem_min_w from bbox corners)
   ↓
3. [Tier B proxy - if ambiguous] (geometry sampling → UV points + hull)
   ↓
4. Classification (TINY / LINEAR / AREAL) via PCA-based oriented extents
   ↓
5. Early-out using w_occ + footprint (tile-based acceleration)
   ↓
6. Rasterization via try_write_cell() (depth-tested, per-source occupancy)
```

## 10. Migration Guide

### For Developers

**Old code**:
```python
raster.z_min[idx] = depth
raster.model_mask[idx] = True
```

**New code**:
```python
source = _extract_source_type(elem.doc_key)
raster.try_write_cell(i, j, w_depth=depth, source=source)
```

### For Users

**Enable debug dumps**:
```python
from vop_interwoven.config import Config

cfg = Config(debug_dump_occlusion=True, debug_dump_path="/tmp/debug")
# Run pipeline with cfg
```

**Tune Tier-B thresholds** (future):
```python
cfg = Config(
    tierb_cell_size_ref_ft=0.5,  # Finer reference cell size
    tierb_area_fraction=0.01,    # Larger area threshold
)
```

## References

- **ViewBasis**: `vop_interwoven/revit/view_basis.py`
- **ViewRaster**: `vop_interwoven/core/raster.py`
- **Pipeline**: `vop_interwoven/pipeline.py`
- **Config**: `vop_interwoven/config.py`
- **Silhouette (PCA)**: `vop_interwoven/core/silhouette.py`
