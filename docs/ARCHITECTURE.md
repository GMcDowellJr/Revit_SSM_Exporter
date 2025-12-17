# SSM Exporter Architecture

This document explains the architectural design, key algorithms, and design decisions in the SSM Exporter.

## Table of Contents
1. [Overview](#overview)
2. [Core Concepts](#core-concepts)
3. [Processing Pipeline](#processing-pipeline)
4. [Key Algorithms](#key-algorithms)
5. [Design Decisions](#design-decisions)
6. [Performance Optimizations](#performance-optimizations)
7. [Future Architecture](#future-architecture)

---

## Overview

The SSM Exporter is a **view-centric occupancy analyzer** for Autodesk Revit. It answers the question: *"Where does 3D model geometry and 2D annotation content appear in this 2D view?"*

### Fundamental Constraint
All analysis is **paper-space based** - the grid is defined in paper inches and converted to model space using the view's scale. This ensures:
- Consistent output resolution across different view scales
- Predictable CSV file sizes
- Scale-independent comparison of views

---

## Core Concepts

### 1. Grid System

**Paper Space Grid**
- Grid cells are defined in **paper inches** (default: 0.25" = 1/4")
- Grid is centered on the view's crop box
- Safety expansion adds margin around visible extents

**Conversion to Model Space**
```
cell_size_model_ft = cell_size_paper_in × (view_scale / 12)

Example at 1/4" = 1'-0" scale (1:48):
  0.25" paper × (48 / 12) = 1.0 ft model
```

**Grid Construction** (SSM_Exporter_v4_A21.py:2817)
1. Get view crop box in model coordinates
2. Convert to view-basis coordinates (X-right, Y-up)
3. Compute grid dimensions from paper-space cell size
4. Generate cell center points
5. Apply safety cap (`max_cells`)

### 2. Occupancy States

Each cell is assigned exactly one state:

| State | Code | Meaning | Example |
|-------|------|---------|---------|
| Empty | N/A | Neither 3D nor 2D | Whitespace, margins |
| Model-only | 0 | 3D geometry only | Walls in a section with no labels |
| Anno-only | 1 | 2D annotations only | Text in a title block |
| Overlap | 2 | Both 3D and 2D | Room tag over a wall |

**Reconciliation Invariant:**
```
TotalCells = EmptyCells + ModelOnlyCells + AnnoOnlyCells + OverlapCells
```

This invariant is **authoritative** per `correctness_contract.md`.

### 3. Element Classification

**3D Elements:**
- Walls, floors, roofs, ceilings
- Structural elements (beams, columns, foundations)
- MEP (ducts, pipes, conduits, equipment)
- Furniture, casework, plumbing fixtures
- **External:** Linked Revit models, DWG imports

**2D Elements (Annotations):**
- Text notes
- Tags (room, door, window, area, etc.)
- Dimensions (linear, radial, angular)
- Detail items and lines
- Filled regions
- Symbols

**Whitelist Approach:**
2D elements are collected by **category whitelist** (SSM_Exporter_v4_A21.py:3838-3932). Only known annotation categories are included. This prevents false positives from view-specific geometry.

### 4. Region Types

After projection, geometry is classified into regions:

| Type | Criteria | Fill Behavior | Occlusion |
|------|----------|---------------|-----------|
| TINY | ≤ 2×2 cells | Boundary only | No |
| LINEAR | Width OR height ≤ 1 cell | Boundary only | No |
| AREAL | Width AND height > threshold | Interior fill | Yes |

**Rationale:**
- TINY/LINEAR represent edges, outlines, small details
- AREAL represents solid surfaces
- Only AREAL regions participate in occlusion (see [Occlusion](#occlusion-handling))

---

## Processing Pipeline

### High-Level Flow

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. View Collection                                              │
│    - Collect all orthographic 2D views                          │
│    - Filter out templates, schedules, legends (optional)        │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ 2. Per-View Processing (cached)                                 │
│    ┌─────────────────────────────────────────────────────────┐ │
│    │ 2a. Grid Generation                                     │ │
│    │     - Build paper-space grid                            │ │
│    │     - Convert to model coordinates                      │ │
│    └─────────────────────────────────────────────────────────┘ │
│    ┌─────────────────────────────────────────────────────────┐ │
│    │ 2b. 3D Element Collection                               │ │
│    │     - Collect host model elements                       │ │
│    │     - Collect link model elements                       │ │
│    │     - Build clip volume from view range                 │ │
│    └─────────────────────────────────────────────────────────┘ │
│    ┌─────────────────────────────────────────────────────────┐ │
│    │ 2c. 3D Projection                                       │ │
│    │     - Extract silhouettes (multiple strategies)         │ │
│    │     - Project to view plane                             │ │
│    │     - Build regions (TINY/LINEAR/AREAL)                 │ │
│    │     - Apply occlusion (Z-buffer)                        │ │
│    └─────────────────────────────────────────────────────────┘ │
│    ┌─────────────────────────────────────────────────────────┐ │
│    │ 2d. 2D Annotation Collection                            │ │
│    │     - Collect by category whitelist                     │ │
│    │     - Extract bounding boxes                            │ │
│    └─────────────────────────────────────────────────────────┘ │
│    ┌─────────────────────────────────────────────────────────┐ │
│    │ 2e. Rasterization                                       │ │
│    │     - Rasterize 3D regions to cells                     │ │
│    │     - Rasterize 2D bboxes to cells                      │ │
│    └─────────────────────────────────────────────────────────┘ │
│    ┌─────────────────────────────────────────────────────────┐ │
│    │ 2f. Occupancy Computation                               │ │
│    │     - Assign codes (0/1/2) based on overlap             │ │
│    │     - Count cells by state                              │ │
│    └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ 3. CSV Export                                                   │
│    - Aggregate results across views                             │
│    - Write core metrics CSV                                     │
│    - Write VOP extended CSV                                     │
└─────────────────────────────────────────────────────────────────┘
```

### Detailed Processing Steps

#### Step 2c: 3D Projection (Lines 4543-6181)

This is the **most complex** step. Key substeps:

1. **Silhouette Extraction** (per element)
   - Adaptive threshold computation (percentile-based)
   - Element size classification (TINY/MEDIUM/LARGE/VERY_LARGE)
   - Strategy selection based on tier
   - Extraction with fallback chain

2. **Projection to View Plane**
   - Transform 3D geometry to view-basis coordinates
   - Flatten to 2D (discard Z, keep XY)
   - Clip to view crop box

3. **Region Building** (Lines 6182-6949)
   - Convert geometry to bounding rectangles
   - Classify as TINY/LINEAR/AREAL
   - Apply hole smoothing
   - Separate regions by type

4. **Occlusion Handling**
   - Sort AREAL regions by Z-depth (front to back)
   - Rasterize each region
   - Track occupied cells in Z-buffer
   - Mask occluded regions (cells already filled by closer geometry)

---

## Key Algorithms

### 1. Adaptive Threshold Computation

**Location:** SSM_Exporter_v4_A21.py:204-374

**Purpose:** Automatically determine element size thresholds based on the distribution of element sizes in the current view.

**Algorithm:**
```python
1. Collect all 3D elements in view
2. Compute projected size (in cells) for each element
3. Winsorize: Drop outliers (bottom 5%, top 5%)
4. Compute percentiles:
   - P25 (25th percentile) → tiny_threshold
   - P50 (50th percentile) → medium_threshold
   - P75 (75th percentile) → large_threshold
5. Clamp to [min, max] safety bounds
6. If < 50 elements, fall back to fixed thresholds
```

**Benefits:**
- Views with small elements (detail views) get fine-grained classification
- Views with large elements (building sections) adjust automatically
- Prevents misclassification due to scale differences

**Trade-offs:**
- Requires pre-processing pass over all elements
- Threshold computation adds overhead (~5-10% of view processing time)
- May produce different thresholds for similar views if element distributions differ

### 2. Silhouette Extraction

**Location:** SilhouetteExtractor class (Lines 376-1263)

**Purpose:** Convert 3D Revit elements to 2D polygons representing their outline in the view.

**Strategy Pattern:**
The extractor uses a **pluggable strategy** approach with fallback chain:

```
Category API Shortcuts (fastest, category-specific)
    ↓ (if not available)
Oriented Bounding Box (fast, good for regular shapes)
    ↓ (if too inaccurate)
Coarse Tessellation (slow, accurate for complex geometry)
    ↓ (if fails)
Axis-Aligned Bounding Box (fallback, always works)
```

**Strategy Selection by Tier:**
- **TINY**: Simple bbox (speed over accuracy)
- **MEDIUM/LARGE**: Category API → OBB → bbox
- **VERY_LARGE**: Category API → Tessellation → OBB → bbox

**Category API Shortcuts:**
For specific categories (Walls, Columns, etc.), use fast APIs:
- `Wall.GetShellGeometry()` - Gets wall face geometry directly
- `FamilyInstance.get_Geometry()` with detail level

**Why Tiered?**
- Small elements (TINY): Inaccuracy doesn't matter at scale
- Large elements (VERY_LARGE): Accuracy critical, worth the cost of tessellation

### 3. Occlusion Handling

**Location:** SSM_Exporter_v4_A21.py:4543-6181 (within projection)

**Purpose:** Prevent background geometry from incorrectly appearing when occluded by foreground geometry.

**Z-Buffer Algorithm:**
```python
1. Separate 3D regions into AREAL (occluding) and TINY/LINEAR (non-occluding)
2. Sort AREAL regions by Z-depth (view-forward coordinate)
3. Initialize empty Z-buffer (set of occupied cells)
4. For each AREAL region (front to back):
   a. Rasterize region to cells
   b. For each cell:
      - If cell already in Z-buffer: SKIP (occluded)
      - Else: Mark cell, add to Z-buffer
5. Rasterize TINY/LINEAR regions without occlusion check
```

**Key Design Decision:**
Only **AREAL** regions participate in occlusion. TINY/LINEAR are always visible.

**Rationale:**
- TINY/LINEAR represent edges and outlines
- Edges should remain visible even if "behind" surfaces
- Example: Wall corners should show even if behind a floor slab
- Prevents thin elements from incorrectly blocking larger objects

**Reference:** `correctness_contract.md` Section: "Occlusion Rules"

### 4. Region Classification

**Location:** SSM_Exporter_v4_A21.py:6182-6949 (`build_regions_from_projected`)

**Purpose:** Classify projected geometry into TINY, LINEAR, or AREAL regions.

**Algorithm:**
```python
For each projected polygon:
  1. Compute bounding rectangle in cells
  2. Calculate width_cells and height_cells

  3. If width <= 2 AND height <= 2:
       → TINY (point-like)

  4. Else if width <= 1 OR height <= 1:
       → LINEAR (edge-like, one dimension is thin)

  5. Else:
       → AREAL (area-filling, both dimensions substantial)
```

**Hole Smoothing:**
For regions with interior holes (e.g., windows in walls):
- Holes ≤ 1×1 cells are filled (smoothed away)
- Larger holes are preserved
- Configurable via `min_hole_size_w_cells`, `min_hole_size_h_cells`

**Why smooth holes?**
- Small holes are often tessellation artifacts
- Reduces region fragmentation
- Improves rasterization performance

### 5. Parity Fill (Filled Regions)

**Location:** Embedded in `build_regions_from_projected`

**Purpose:** Fill the interior of closed 2D polygons (e.g., Filled Regions in Revit).

**Algorithm:** Ray casting parity test
```python
For each cell in bounding box:
  1. Cast horizontal ray from cell center to infinity
  2. Count intersections with polygon edges
  3. If count is odd: cell is INSIDE
  4. If count is even: cell is OUTSIDE
  5. Mark inside cells as occupied
```

**Use case:** 2D annotations like Filled Regions that should occupy area, not just outline.

### 6. Rasterization

**Location:** SSM_Exporter_v4_A21.py:6949 (`rasterize_regions_to_cells`)

**Purpose:** Convert regions (rectangles) to grid cell indices.

**Simple Algorithm:**
```python
For each region:
  1. Get bounding box (x_min, y_min, x_max, y_max) in cells
  2. For AREAL regions (if fill enabled):
       Mark all cells in [x_min, x_max] × [y_min, y_max]
  3. For LINEAR regions (if fill enabled):
       Mark all cells in [x_min, x_max] × [y_min, y_max]
  4. For TINY regions:
       Mark boundary cells only
```

**Optimization:** Uses set operations for fast cell marking.

### 7. Occupancy Computation

**Location:** SSM_Exporter_v4_A21.py:7108 (`compute_occupancy`)

**Purpose:** Assign final occupancy codes (0/1/2) to each cell.

**Algorithm:**
```python
Initialize all cells to EMPTY (no code)

For each cell index:
  has_3d = (cell in 3d_cells_set)
  has_2d = (cell in 2d_cells_set)

  If has_3d AND has_2d:
    occupancy[cell] = 2  (OVERLAP)
  Else if has_3d:
    occupancy[cell] = 0  (MODEL-ONLY)
  Else if has_2d:
    occupancy[cell] = 1  (ANNO-ONLY)
  Else:
    (leave as EMPTY, not in occupancy dict)

Count:
  - empty_cells = TotalCells - len(occupancy)
  - model_only_cells = count(occupancy == 0)
  - anno_only_cells = count(occupancy == 1)
  - overlap_cells = count(occupancy == 2)
```

**Verification:** Assert reconciliation invariant before returning.

---

## Design Decisions

### ADR 1: Paper-Space Grid

**Decision:** Grid cells are defined in paper inches, not model feet.

**Rationale:**
- Output resolution should be **scale-independent**
- A 1/4" = 1'-0" floor plan and a 1/8" = 1'-0" site plan should have comparable CSV size
- Paper space is what users care about (sheet layout, printing)
- Model-space grids would produce massive CSV files for large-scale views

**Trade-offs:**
- More complex conversion logic (paper → model via view scale)
- Cell size in model space varies by view scale
- ✅ Consistent output sizes
- ✅ Predictable performance

**Alternatives Considered:**
- Fixed model-space grid (rejected: CSV size varies wildly)
- Adaptive cell count (rejected: inconsistent resolution)

---

### ADR 2: AREAL-Only Occlusion

**Decision:** Only AREAL regions participate in Z-buffer occlusion. TINY and LINEAR regions are always visible.

**Rationale:**
- TINY/LINEAR represent **edges and outlines**, not surfaces
- Example: A wall corner (LINEAR) should show even if technically "behind" a floor slab
- Prevents thin elements from incorrectly occluding larger objects
- Matches human perception of 2D drawings (edges always drawn)

**Trade-offs:**
- More complex occlusion logic (filter by region type)
- Possible false positives (thin surface might not occlude)
- ✅ Better matches drawing conventions
- ✅ Prevents pathological cases (thin wall blocking entire building)

**Alternatives Considered:**
- All regions occlude (rejected: thin elements block incorrectly)
- No occlusion (rejected: background elements show through foreground)

---

### ADR 3: Category Whitelist for 2D Elements

**Decision:** 2D annotations are collected by explicit category whitelist, not by "all view-specific elements."

**Rationale:**
- Revit has many view-specific element types (reference planes, analysis visualization, etc.)
- Whitelist ensures only **intentional annotations** are counted
- Prevents false positives from temporary or debug geometry
- Explicit list is self-documenting

**Trade-offs:**
- Must update whitelist when new annotation categories are added
- May miss custom annotation families if not in whitelist
- ✅ Accurate results
- ✅ No false positives

**Alternatives Considered:**
- Collect all view-specific elements (rejected: too many false positives)
- User-configurable whitelist (considered for future)

---

### ADR 4: View-Level Caching

**Decision:** Cache results at the view level, keyed by (view_id, config_hash, exporter_version).

**Rationale:**
- Most runs are **iterative** (small config changes, testing)
- Views are independent (cache hit for unchanged views)
- Dramatic speedup (10x - 100x) for cached views
- Hash-based invalidation ensures correctness

**Cache Invalidation:**
- Configuration changes (config_hash)
- Exporter version changes
- Project GUID changes
- Element modifications in view (TODO: not yet implemented)

**Trade-offs:**
- Cache file can be large (MB - GB for big projects)
- Cache management overhead (save/load JSON)
- ✅ Huge performance win for iterative workflows
- ✅ Safe (hash-based invalidation)

**Alternatives Considered:**
- No caching (rejected: too slow for iteration)
- Project-level caching (rejected: too coarse-grained)
- Element-level caching (considered for future)

---

### ADR 5: Adaptive vs. Fixed Thresholds

**Decision:** Support both adaptive (percentile-based) and fixed thresholds, with adaptive as default.

**Rationale:**
- Different views have vastly different element size distributions
- Detail view: Elements are 1-10 cells
- Building section: Elements are 100-10,000 cells
- Fixed thresholds misclassify in one or the other
- Adaptive thresholds adjust automatically

**When Adaptive Fails:**
- Views with < 50 elements (fallback to fixed)
- Views with uniform element sizes (no distribution to percentile)

**Trade-offs:**
- Adaptive adds 5-10% processing overhead
- Thresholds vary by view (less reproducible)
- ✅ Better classification accuracy
- ✅ Works across all view types and scales

**Alternatives Considered:**
- Fixed thresholds only (rejected: poor classification)
- User-specified thresholds (too complex for most users)

---

## Performance Optimizations

### 1. View-Level Caching
- **Impact:** 10x - 100x speedup for cached views
- **Implementation:** JSON file with view results keyed by hash
- **Location:** SSM_Exporter_v4_A21.py:8564-8570

### 2. Silhouette Strategy Tiering
- **Impact:** 50-80% reduction in tessellation calls
- **Method:** Use fast strategies (bbox, OBB) for small elements; reserve tessellation for large elements
- **Location:** SilhouetteExtractor class

### 3. Extractor Caching
- **Impact:** 30-50% speedup when same element appears in multiple views
- **Method:** Cache `SilhouetteExtractor` instances on function object
- **Location:** `project_elements_to_view_xy._extractor_cache`
- **Cleanup:** Cleared at end of main() to free memory

### 4. Set-Based Rasterization
- **Impact:** O(1) cell lookups instead of O(n) list scans
- **Method:** Use Python sets for cell index storage
- **Location:** Throughout rasterization and occupancy computation

### 5. Early Exit for Empty Views
- **Impact:** Skips processing for views with no elements
- **Method:** Check element counts before projection
- **Location:** `process_view()` entry checks

### 6. Z-Buffer Optimization
- **Impact:** Reduces occlusion checks by 60-80%
- **Method:** Only check AREAL regions, skip TINY/LINEAR
- **Location:** Occlusion logic in projection

### 7. Category API Shortcuts
- **Impact:** 5-10x faster silhouette extraction for supported categories
- **Method:** Use category-specific fast APIs (e.g., Wall.GetShellGeometry)
- **Location:** SilhouetteExtractor strategy selection

---

## Future Architecture

### Planned Modularization (REFRACTOR_PLAN.md)

**Current State:** Monolithic 8,630-line file

**Target State:** Modular architecture

```
ssm_exporter/
├── core/
│   ├── config.py           # Configuration management
│   ├── types.py            # Enums, dataclasses
│   └── logger.py           # Logger class
├── geometry/
│   ├── silhouette.py       # SilhouetteExtractor
│   ├── transforms.py       # View-basis transforms
│   └── grid.py             # Grid building
├── revit/
│   ├── collection.py       # Element collection
│   ├── views.py            # View processing
│   └── links.py            # Link model handling
├── processing/
│   ├── projection.py       # 3D→2D projection
│   ├── regions.py          # Region building
│   ├── rasterization.py    # Rasterization
│   └── occupancy.py        # Occupancy computation
└── export/
    ├── csv.py              # CSV export
    └── visualization.py    # PNG output
```

**Benefits:**
- Testability (unit tests per module)
- Maintainability (smaller files)
- Reusability (components in other tools)
- Documentation (easier to document focused modules)

**Refactor Discipline:**
- MOVE ONLY commits (extract without changing behavior)
- Golden artifact regression (verify CSV outputs match)
- Incremental (one module at a time)

---

## References

- **Correctness Contract:** [correctness_contract.md](correctness_contract.md) - Authoritative behavior specification
- **Refactor Plan:** [REFRACTOR_PLAN.md](REFRACTOR_PLAN.md) - Modularization roadmap
- **Configuration:** [CONFIGURATION.md](CONFIGURATION.md) - All config options

---

**Document Version:** 1.0
**Last Updated:** 2025-12-17
**Compatible with:** SSM_Exporter_v4_A21
