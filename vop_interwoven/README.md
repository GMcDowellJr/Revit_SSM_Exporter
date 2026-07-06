# VOP Interwoven Pipeline

**Occlusion-aware rasterization pipeline for Revit SSM/VOP export**

## Overview

The VOP Interwoven Pipeline implements a depth-aware, UV-classified rasterization system for exporting Revit views to structured occupancy data. It combines 3D model geometry occlusion with 2D annotation layering while maintaining precise depth ordering.

### Core Principles

1. **3D model geometry is the ONLY occlusion truth**
2. **2D annotation NEVER occludes model geometry**
3. **Heavy work (triangles, depth-buffer) reserved for AreaL elements**
4. **Tiny/Linear elements emit proxies (UV_AABB/OBB) to avoid expensive geometry**
5. **Early-out is safe only against depth-aware occlusion buffers**

## Architecture

```
vop_interwoven/
├── config.py                # Configuration (tile size, thresholds, proxy modes)
├── pipeline.py              # Main interwoven pass
├── entry_dynamo.py          # Dynamo entry point
├── csv_export.py            # CSV export (SSM-compatible format)
├── png_export.py            # PNG visualization export
├── streaming.py             # Data streaming utilities
├── core/
│   ├── raster.py            # ViewRaster, TileMap (occlusion tracking)
│   ├── geometry.py          # UV classification, proxy generation
│   ├── silhouette.py        # Multi-strategy silhouette extraction
│   ├── areal_extraction.py  # AREAL element geometry extraction
│   ├── face_selection.py    # Front-facing face selection
│   ├── element_cache.py     # LRU element caching
│   ├── cache.py             # General caching utilities
│   ├── diagnostics.py       # Diagnostic tracking
│   ├── math_utils.py        # Bounds, rectangle operations
│   ├── footprint.py         # Footprint computation
│   ├── hull.py              # Convex hull utilities
│   ├── pca2d.py             # 2D PCA for OBB fitting
│   └── source_identity.py   # Source identity (HOST|LINK|DWG)
├── revit/
│   ├── view_basis.py        # View coordinate system extraction
│   ├── collection.py        # Element collection, visibility filtering
│   ├── annotation.py        # 2D annotation processing
│   ├── linked_documents.py  # RVT link and DWG import handling
│   ├── collection_policy.py # Collection policy configuration
│   ├── safe_api.py          # Safe Revit API wrapper
│   └── tierb_proxy.py       # Tier B proxy generation
├── diagnostics/
│   └── strategy_tracker.py  # Strategy performance tracking
└── export/
    └── csv.py               # CSV file writing helpers

tests/                       # Unit tests (at repository root)
├── test_geometry.py         # UV classification tests
├── test_raster.py           # Raster data structure tests
├── test_areal_extraction.py # AREAL extraction tests
└── ...                      # 40+ test files
```

## UV Classification

Elements are classified by their projected footprint size (in grid cells):

- **TINY**: Both U≤2 AND V≤2 (e.g., door hardware, small fixtures)
- **LINEAR**: min(U,V)≤2 AND max(U,V)>2 (e.g., walls, doors, windows)
- **AREAL**: Both dimensions >2 (e.g., floors, roofs, large furniture)

### Classification Examples

| Element Type | Typical Size | Cell Size | UV Dims | Mode |
|---|---|---|---|---|
| Door handle | 0.5ft × 0.5ft | 0.25ft | 2×2 | **TINY** |
| Door | 3ft × 7ft | 1.5ft | 2×5 | **LINEAR** |
| Wall | 20ft × 0.5ft | 1ft | 20×1 | **LINEAR** |
| Floor | 30ft × 40ft | 2ft | 15×20 | **AREAL** |

## Processing Modes

Elements are processed differently based on their UV classification:

### AREAL Elements (Heavy Processing)
- Full triangle tessellation from Revit geometry
- Per-cell depth buffer (z_min tracking)
- Conservative tile-based interior fill
- Boundary refinement via triangle rasterization
- Depth-tested edge stamping
- **Only AREAL elements with HIGH-confidence geometry write to the depth buffer** (occlusion authority)

### TINY Elements (Lightweight Processing)
- Proxy fill stamped to `model_proxy_key` layer via `rasterize_polygon_to_proxy`
- Proxy edges stamped via `rasterize_closed_loops_to_proxy_edges`
- **No depth buffer writes** (avoids false occlusion)

### LINEAR Elements (Medium Processing)
- Proxy fill stamped to `model_proxy_key` layer via `rasterize_polygon_to_proxy`
- Proxy edges stamped via `rasterize_closed_loops_to_proxy_edges` / `rasterize_open_polylines_to_proxy_edges`
- Captures orientation of doors, walls, beams
- **No depth buffer writes**

### Confidence-Based Occlusion Authority (AREAL elements only)

AREAL elements carry a confidence level from geometry extraction:

| Confidence | Source | Writes `w_occ`? | Writes proxy ink? |
|---|---|---|---|
| **HIGH** | planar_face_loops, silhouette_edges | **Yes** | Yes (supplemental edges) |
| **MEDIUM** | geometry_polygon extraction | No | Yes (proxy fill + edges) |
| **LOW** | OBB / AABB fallback | No | Yes (proxy edges only) |

Rationale: occlusion is high-impact — a wrong occlusion skips later elements permanently.
Approximate geometry must err toward doing more work (staying visible) rather than hiding
content based on "close enough" shapes. Proxy ink is acceptable as model presence, but not
as occlusion truth.

TINY and LINEAR elements **always** use the proxy path regardless of whether geometry
extraction succeeds. They set `occluder=False` in element metadata unconditionally.

> **Terminology Note**: "AREAL" refers to elements with both UV dimensions > threshold (large footprint elements like floors, roofs). The term is used consistently throughout the codebase.

## Configuration

```python
from vop_interwoven.config import Config

cfg = Config(
    tile_size=16,                      # Tile size for spatial acceleration
    over_model_includes_proxies=True,  # Include proxy presence in OverModel
    proxy_mask_mode="minmask",         # "minmask" or "edges"
    depth_eps_ft=0.01,                 # Depth tolerance (feet)
    tiny_max=2,                        # TINY threshold (cells)
    thin_max=2                         # LINEAR thin threshold (cells)
)
```

### OverModel Semantics

The `over_model_includes_proxies` flag controls what counts as "model presence" when determining if an annotation is "over model":

- **True** (default): Annotation is "over model" if it overlaps with:
  - AREAL elements (depth-buffered geometry), **OR**
  - TINY/LINEAR proxy presence masks
- **False**: Annotation is "over model" only if it overlaps with AREAL occluders (ignores proxies)

This affects the `anno_over_model` output array and downstream analytics.

### Proxy Mask Modes

The `proxy_mask_mode` controls how TINY/LINEAR elements mark their presence:

- **"minmask"**: Minimal footprint stamping
  - TINY: Center cell only
  - LINEAR: Thin band along long axis
- **"edges"**: Only proxy edges stamped, no interior presence mask (lightest memory footprint)

The proxy mode interacts with `over_model_includes_proxies`: if proxies don't write presence masks ("edges" mode), they won't contribute to "over model" detection even when `over_model_includes_proxies=True`.

## Usage

### From Dynamo Python Node

```python
import sys
sys.path.append(r'C:\path\to\Revit_SSM_Exporter')

from vop_interwoven.entry_dynamo import run_vop_pipeline
from vop_interwoven.config import Config

# Get Revit context
doc = __revit__.ActiveUIDocument.Document
view = __revit__.ActiveUIDocument.ActiveView

# Configure
cfg = Config(
    tiny_max=2,
    thin_max=2,
    over_model_includes_proxies=True
)

# Run pipeline
result = run_vop_pipeline(doc, [view.Id], cfg)

# Output for Dynamo
OUT = result
```

### Quick Test

```python
from vop_interwoven.entry_dynamo import quick_test_current_view

# Test current view with default config
result = quick_test_current_view()
print(result['summary'])
```

## Running Tests

Tests are located in the repository root `tests/` directory (not inside `vop_interwoven/`).

```bash
# From repository root, run all tests with pytest
cd /path/to/Revit_SSM_Exporter
python -m pytest tests/ -v

# Run specific test files
python -m pytest tests/test_geometry.py -v
python -m pytest tests/test_raster.py -v

# Run with coverage
python -m pytest tests/ --cov=vop_interwoven --cov-report=html
```

Expected output:
```
tests/test_geometry.py::test_tiny_classification PASSED
tests/test_geometry.py::test_linear_classification PASSED
tests/test_geometry.py::test_areal_classification PASSED
...
========================= 40+ passed in 2.5s =========================
```

> **Note**: Dynamo integration tests require `VOP_RUN_DYNAMO_TESTS=1` environment variable.

## Data Structures

### ViewRaster

Per-view raster with all occlusion state:

```python
raster = ViewRaster(width=64, height=64, cell_size=1.0, bounds=bounds)

# AreaL truth occlusion
raster.model_mask[idx]      # Boolean: interior coverage
raster.z_min[idx]           # Float: nearest depth (+inf if empty)

# Edge layers
raster.model_edge_key[idx]  # Int: depth-tested AreaL edges
raster.model_proxy_key[idx] # Int: proxy edges (TINY/LINEAR)

# Annotation
raster.anno_key[idx]        # Int: 2D annotation edges
raster.anno_over_model[idx] # Boolean: derived (anno && model presence)

# Metadata
raster.element_meta         # List of element metadata dicts
raster.anno_meta            # List of annotation metadata dicts
```

### TileMap

Tile-based spatial acceleration for early-out occlusion testing:

```python
tile = TileMap(tile_size=16, width=64, height=64)

tile.filled_count[t]  # Count of filled cells in tile t
tile.z_min_tile[t]    # Minimum depth in tile t (+inf if empty)

# Safe early-out: skip element if ALL overlapped tiles are:
#   1. Fully filled (filled_count == tile_area)
#   2. Nearer than element (z_min_tile < elem_near_z)
```

## Output Format

```json
{
  "view_id": 123456,
  "view_name": "Level 1",
  "raster": {
    "width": 64,
    "height": 64,
    "cell_size_ft": 1.0,
    "model_mask": [false, true, ...],
    "z_min": [null, 5.2, ...],
    "model_edge_key": [-1, 0, ...],
    "model_proxy_key": [-1, 3, ...],
    "anno_key": [-1, -1, 5, ...],
    "anno_over_model": [false, false, true, ...],
    "element_meta": [
      {"elem_id": 789, "category": "Walls", "source": "HOST"},
      ...
    ],
    "anno_meta": [
      {"anno_id": 456, "type": "TEXT"},
      ...
    ]
  },
  "diagnostics": {
    "num_elements": 150,
    "num_annotations": 25,
    "num_filled_cells": 2048
  }
}
```

## Development Status

### ✅ Complete (Core Pipeline)
- Config dataclass with validation and adaptive tile sizing
- UV classification (TINY/LINEAR/AREAL)
- ViewRaster and TileMap data structures
- Proxy generation (UV_AABB, OBB)
- View basis extraction from Revit views (`revit/view_basis.py`)
- Element collection and visibility filtering (`revit/collection.py`)
- Multi-strategy silhouette extraction (`core/silhouette.py`)
- AREAL element geometry extraction (`core/areal_extraction.py`)
- Triangle tessellation and rasterization
- Depth buffer with tile-based acceleration
- Edge rasterization (depth-tested)
- 2D annotation collection and classification (`revit/annotation.py`)
- CSV export (SSM-compatible format) (`csv_export.py`)
- PNG visualization export (`png_export.py`)
- LRU element caching (`core/element_cache.py`)
- Diagnostics and strategy tracking
- Comprehensive unit tests (40+ tests)
- Dynamo entry point with CPython3 compatibility

### ✅ Complete (External Sources)
- RVT link document handling (`revit/linked_documents.py`)
- DWG import geometry extraction
- Source identity tracking (HOST | LINK | DWG)

### 🔮 Future Enhancements
- RLE compression for output arrays
- Multi-view parallelization
- Cut plane handling for plan views
- Annotation crop awareness
- Adaptive threshold computation

## Performance Characteristics

- **Tile size 16**: Good balance (256 cells/tile, ~4K tiles for 1024x1024 grid)
- **Early-out**: Skips fully-occluded elements (saves 60-80% geometry work)
- **Proxy savings**: TINY/LINEAR skip triangle tessellation (10-100x faster)
- **Memory**: ~2-4 bytes/cell for masks, ~4-8 bytes/cell for depth/edges

### Scaling
- 64×64 grid (4K cells): <1 MB
- 256×256 grid (65K cells): ~10-20 MB
- 1024×1024 grid (1M cells): ~100-200 MB (with compression: ~10-50 MB)

## Commentary Annotations

Throughout the code, you'll find commentary markers:

- **✔** = Hardened choice / recommended default
- **⚠** = Known pitfall / assumption boundary
- **🧩** = Optional extension knob

## License

Part of the Revit SSM Exporter project.

## Contributors

- Initial implementation: Claude Code (2026-01-01)
- Specification: VOP Interwoven Pipeline Spec

## Performance Setup (Optional)

VOP uses NumPy and Pillow for significantly faster rasterization and PNG export when they are available. The pipeline runs correctly without them — these libraries only enable optional fast code paths.

### One-time installation

From a command prompt (replacing the path with your actual location):
```

python “C:\path\to\vop_interwoven\bootstrap.py”

```
Or from a Dynamo CPython3 Python node:

```python
exec(open(r"C:\path\to\vop_interwoven\bootstrap.py").read())
```

Restart Revit and Dynamo after running.

### Verification

In a Python node or terminal:

```python
from vop_interwoven.np_backend import NUMPY_AVAILABLE, PILLOW_AVAILABLE
print("numpy:", NUMPY_AVAILABLE)    # True = fast path active
print("pillow:", PILLOW_AVAILABLE)  # True = fast PNG export active
```

### Without installation

If NumPy and Pillow are not installed, VOP automatically uses pure Python implementations. No configuration change is required.

## VOP Stage A color ID-buffer extraction

Set `Config(enable_color_id_buffer_stage_a=True)` to bypass the legacy in-memory
occlusion/silhouette model pass for model-capable views and export a Revit
rendered color ID buffer instead.  The Stage A path collects the same visible
model elements, resolves groups and shared nested family subcomponents, applies
a deterministic flat RGB override per resolved element, suppresses active view
filters, swaps to the neutral `VOP_NeutralPhaseFilter`, clears category halftone,
hides annotation/grid/level categories, exports a per-view TIFF, writes a JSON
sidecar, and restores the view state before the next view is processed.  In the
streaming thin runner, pass `True` in `IN[5]`; these files are written under
the `IN[2]` output tree in `color_id_buffer/`. Direct pipeline calls use
`Config(enable_color_id_buffer_stage_a=True)` and write to
`cfg.output_dir/color_id_buffer/`. Stage A bypasses metrics-only root-cache hits
so enabling it always attempts a fresh TIFF/sidecar export for each processed
model-capable view.

Stage A intentionally stops at extraction.  It does not decode colors back into
vectors, trace contours, simplify geometry, join annotations to model elements,
perform bbox pre-filtering, run multi-pass color batching, or derive thresholds
from lineweight.  The only Stage A geometry threshold is the fixed
`color_id_buffer_threshold_source_mm` default of `0.7` mm combined with
`color_id_buffer_min_pixels_across_threshold` default of `2` pixels.
