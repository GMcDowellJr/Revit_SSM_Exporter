# SSM Exporter Configuration Reference

This document provides comprehensive documentation for all configuration options in the SSM Exporter.

## Table of Contents
1. [Configuration Structure](#configuration-structure)
2. [Grid Configuration](#grid-configuration)
3. [Clip Volume Configuration](#clip-volume-configuration)
4. [Projection Configuration](#projection-configuration)
5. [Silhouette Extraction](#silhouette-extraction)
6. [Occupancy PNG Output](#occupancy-png-output)
7. [Region Configuration](#region-configuration)
8. [Rasterization Configuration](#rasterization-configuration)
9. [Occupancy Codes](#occupancy-codes)
10. [Run Configuration](#run-configuration)
11. [Cache Configuration](#cache-configuration)
12. [Export Configuration](#export-configuration)
13. [Debug Configuration](#debug-configuration)

---

## Configuration Structure

The exporter uses a single nested dictionary called `CONFIG` for all settings. Access it via:

```python
import SSM_Exporter_v4_A21
CONFIG = SSM_Exporter_v4_A21.CONFIG
```

You can modify settings before running:

```python
CONFIG["grid"]["cell_size_paper_in"] = 0.25
CONFIG["export"]["output_dir"] = r"C:\MyOutputs"
```

---

## Grid Configuration

**Location:** `CONFIG["grid"]`

Controls the grid system that overlays each view.

### `cell_size_paper_in`
- **Type:** `float`
- **Default:** `0.25`
- **Units:** Inches (paper space)
- **Description:** Size of each grid cell measured in paper space inches. This is converted to model space using the view's scale.

**Example calculations:**
- At 1/4" = 1'-0" scale (1:48):
  - 0.125" paper → 6" model
  - 0.25" paper → 12" (1 ft) model
- At 1/8" = 1'-0" scale (1:96):
  - 0.125" paper → 12" (1 ft) model
  - 0.25" paper → 24" (2 ft) model

**Recommendations:**
- **Fine detail:** 0.0625" - 0.125" (1/16" - 1/8")
- **Standard:** 0.25" (1/4")
- **Coarse/fast:** 0.5" - 1.0"

### `max_cells`
- **Type:** `int`
- **Default:** `200000`
- **Description:** Hard safety limit on the maximum number of cells per view. Prevents memory issues with very large views.
- **Behavior:** If a view would exceed this limit, processing is skipped and a warning is logged.

**When to adjust:**
- Increase for very large site plans or building sections
- Decrease to catch unexpectedly large views early

---

## Clip Volume Configuration

**Location:** `CONFIG["clip_volume"]`

Controls how 3D geometry is clipped to the view's depth range.

### `enable_view_range`
- **Type:** `bool`
- **Default:** `True`
- **Description:** Use the view's View Range settings (Top/Cut/Bottom offsets) to clip 3D geometry.
- **Applies to:** Floor Plans, RCPs

**Effect:**
- `True`: Only geometry within the view range appears
- `False`: All geometry in the model projects to the view

### `enable_far_clip`
- **Type:** `bool`
- **Default:** `True`
- **Description:** Use the view's Far Clip Plane setting to limit depth.
- **Applies to:** Sections, Elevations

**Effect:**
- `True`: Geometry beyond far clip is excluded
- `False`: All geometry along the view direction projects

### `use_cut_slab`
- **Type:** `bool`
- **Default:** `False`
- **Description:** Experimental refinement for view range interpretation.

**Values:**
- `True`: Use directional slab (Plans: cut-down, RCPs: cut-up)
- `False`: Use conservative full-range slab

**Recommendation:** Keep at `False` unless testing specific edge cases.

---

## Projection Configuration

**Location:** `CONFIG["projection"]`

Controls which elements are included in the analysis.

### `include_3d`
- **Type:** `bool`
- **Default:** `True`
- **Description:** Include 3D model geometry in the analysis.

**Use case for `False`:**
- Annotation-only analysis (measure annotation density without 3D context)

### `include_2d`
- **Type:** `bool`
- **Default:** `True`
- **Description:** Include 2D annotations in the analysis.

**Use case for `False`:**
- Model geometry coverage only (ignore annotations)

### `include_link_3d`
- **Type:** `bool`
- **Default:** `True`
- **Description:** Include 3D geometry from linked Revit models and DWG imports.

**Use cases for `False`:**
- Analyze host model only
- Debugging link-related issues
- Faster processing when links are not relevant

---

## Silhouette Extraction

**Location:** `CONFIG["projection"]["silhouette"]`

Advanced settings for 3D geometry outline extraction. The exporter uses multiple strategies to extract 2D silhouettes from 3D geometry efficiently.

### Core Settings

#### `enabled`
- **Type:** `bool`
- **Default:** `True`
- **Description:** Master switch for silhouette extraction.
- **Warning:** Setting to `False` will break projection. Do not disable.

#### `enable_obb`
- **Type:** `bool`
- **Default:** `True`
- **Description:** Use Oriented Bounding Box strategy for medium/large elements.
- **Performance:** Fast, good accuracy for regular shapes.

#### `enable_silhouette_edges`
- **Type:** `bool`
- **Default:** `True`
- **Description:** Use Revit's native silhouette edge API.
- **Quality:** High accuracy but slower.

#### `enable_coarse_tessellation`
- **Type:** `bool`
- **Default:** `True`
- **Description:** Use coarse mesh tessellation for very large elements.
- **Performance:** Balanced approach for complex geometry.

#### `enable_category_api_shortcuts`
- **Type:** `bool`
- **Default:** `True`
- **Description:** Use fast category-specific APIs (e.g., Wall.GetShellGeometry) when available.
- **Performance:** Fastest method for supported categories.

---

### Adaptive Thresholds

The exporter can automatically compute size thresholds based on the actual element distribution in each view.

#### `use_adaptive_thresholds`
- **Type:** `bool`
- **Default:** `True`
- **Description:** Enable adaptive threshold computation based on percentiles.

**How it works:**
1. Collect all 3D elements in the view
2. Compute their projected sizes (in cells)
3. Calculate percentile-based thresholds
4. Use these to classify elements as TINY/MEDIUM/LARGE/VERY_LARGE

#### `adaptive_percentile_tiny`
- **Type:** `int`
- **Default:** `25`
- **Range:** 0-100
- **Description:** Percentile for TINY/LINEAR threshold (25th percentile = lower quartile).

#### `adaptive_percentile_medium`
- **Type:** `int`
- **Default:** `50`
- **Description:** Percentile for MEDIUM threshold (median).

#### `adaptive_percentile_large`
- **Type:** `int`
- **Default:** `75`
- **Description:** Percentile for LARGE threshold (75th percentile = upper quartile).

#### `adaptive_winsorize`
- **Type:** `bool`
- **Default:** `True`
- **Description:** Remove outliers before computing percentiles.

#### `adaptive_winsorize_lower` / `adaptive_winsorize_upper`
- **Type:** `int`
- **Default:** `5` / `95`
- **Description:** Drop elements below 5th percentile and above 95th percentile before threshold calculation.
- **Purpose:** Prevents extreme outliers from skewing thresholds.

#### `adaptive_min_tiny` / `adaptive_min_medium` / `adaptive_min_large`
- **Type:** `int`
- **Default:** `1` / `3` / `10`
- **Description:** Minimum threshold values (safety floor).
- **Purpose:** Ensures thresholds don't become too small even for small-element views.

#### `adaptive_max_tiny` / `adaptive_max_medium` / `adaptive_max_large`
- **Type:** `int`
- **Default:** `5` / `20` / `100`
- **Description:** Maximum threshold values (safety ceiling).
- **Purpose:** Prevents thresholds from becoming too large in views with few large elements.

#### `adaptive_min_elements`
- **Type:** `int`
- **Default:** `50`
- **Description:** Minimum number of elements required to use adaptive thresholds.
- **Behavior:** If fewer elements, fall back to fixed thresholds.

---

### Fixed Thresholds (Fallback)

Used when adaptive thresholds are disabled or insufficient elements.

#### `tiny_linear_threshold_cells`
- **Type:** `int`
- **Default:** `2`
- **Description:** Max cells for TINY classification. Elements ≤ this are TINY (treated as points/small linear).

#### `medium_threshold_cells`
- **Type:** `int`
- **Default:** `10`
- **Description:** Threshold between MEDIUM and LARGE.

#### `large_threshold_cells`
- **Type:** `int`
- **Default:** `50`
- **Description:** Threshold between LARGE and VERY_LARGE.

**Classification logic:**
```
If cells <= tiny_linear_threshold_cells:      → TIER_TINY_LINEAR
Else if cells <= medium_threshold_cells:      → TIER_MEDIUM
Else if cells <= large_threshold_cells:       → TIER_LARGE
Else:                                          → TIER_VERY_LARGE
```

---

### Scale-Aware Thresholds (Experimental)

Alternative to cell-based thresholds: use absolute model dimensions (feet).

#### `use_absolute_thresholds`
- **Type:** `bool`
- **Default:** `False`
- **Description:** Use feet-based thresholds instead of cell-based.

#### `auto_adjust_for_scale`
- **Type:** `bool`
- **Default:** `False`
- **Description:** Automatically adjust thresholds based on view scale.

#### `tiny_linear_threshold_ft`
- **Type:** `float`
- **Default:** `2.0`
- **Description:** TINY threshold in model feet.

#### `medium_threshold_ft`
- **Type:** `float`
- **Default:** `5.0`

#### `large_threshold_ft`
- **Type:** `float`
- **Default:** `10.0`

**Recommendation:** Keep these `False` unless you have specific needs for scale-independent thresholds.

---

### Tessellation Settings

#### `coarse_tess_max_verts_per_face`
- **Type:** `int`
- **Default:** `20`
- **Description:** Maximum vertices per face when tessellating geometry.
- **Trade-off:** Higher = more accurate but slower.

#### `coarse_tess_triangulate_param`
- **Type:** `float`
- **Default:** `0.5`
- **Range:** 0.0 - 1.0
- **Description:** Triangulation quality parameter for Revit tessellation API.

---

### Category Shortcuts

#### `simple_categories`
- **Type:** `list[str]`
- **Default:** `["Walls", "Structural Framing", "Structural Columns", "Columns", "Beams", "Doors", "Windows"]`
- **Description:** Categories that support fast API shortcuts (e.g., Wall.GetShellGeometry).
- **When to modify:** Add categories if you've identified fast extraction methods.

---

### Tier Strategies

Define the extraction strategy sequence for each size tier.

#### `tier_tiny_linear`
- **Type:** `list[str]`
- **Default:** `["bbox"]`
- **Description:** Strategies for TINY elements. Uses simple bounding box (fastest).

#### `tier_medium`
- **Type:** `list[str]`
- **Default:** `["category_api_shortcuts", "obb", "bbox"]`
- **Description:** Try category API first, then OBB, then bbox fallback.

#### `tier_large`
- **Type:** `list[str]`
- **Default:** `["category_api_shortcuts", "obb", "bbox"]`

#### `tier_very_large`
- **Type:** `list[str]`
- **Default:** `["category_api_shortcuts", "coarse_tessellation", "obb", "bbox"]`
- **Description:** For very large elements, add coarse tessellation before OBB.

**Available strategies:**
- `"category_api_shortcuts"` - Fast category-specific APIs
- `"obb"` - Oriented bounding box
- `"coarse_tessellation"` - Mesh-based extraction
- `"bbox"` - Axis-aligned bounding box (fastest fallback)

#### `category_first`
- **Type:** `bool`
- **Default:** `True`
- **Description:** Always try category shortcuts first regardless of tier.

#### `track_strategy_usage`
- **Type:** `bool`
- **Default:** `True`
- **Description:** Log which strategies are used for debugging/optimization.

---

## Occupancy PNG Output

**Location:** `CONFIG["occupancy_png"]`

Optional PNG visualization of occupancy grids.

### `enabled`
- **Type:** `bool`
- **Default:** `False`
- **Description:** Generate PNG image for each view.

**Output location:** Same as CSV output directory, named `<ViewName>_occupancy.png`

### `pixels_per_cell`
- **Type:** `int`
- **Default:** `10`
- **Description:** Size of each grid cell in the PNG image (both width and height in pixels).

**Color scheme:**
- White: Empty cell
- Blue: Model-only (3D)
- Yellow: Anno-only (2D)
- Red: Overlap (both)

**Recommendations:**
- Use 5-10 pixels for large views
- Use 10-20 pixels for detailed inspection
- Larger values = larger file size

---

## Region Configuration

**Location:** `CONFIG["regions"]`

Controls how projected geometries are classified into regions.

### `tiny_max_w` / `tiny_max_h`
- **Type:** `int`
- **Default:** `2` / `2`
- **Units:** Cells
- **Description:** Maximum width/height for TINY region classification.
- **Effect:** Regions ≤ 2×2 cells are TINY (non-filling).

### `linear_band_thickness_cells`
- **Type:** `int`
- **Default:** `1`
- **Description:** Maximum thickness for LINEAR region classification.
- **Effect:** Regions with one dimension ≤ 1 cell are LINEAR (edges).

### `min_hole_size_w_cells` / `min_hole_size_h_cells`
- **Type:** `int`
- **Default:** `1` / `1`
- **Description:** Minimum hole size to preserve during region building.
- **Effect:** Holes ≤ 1×1 cells are filled (smoothing).

**Use cases:**
- Set to 0 to preserve all holes (no smoothing)
- Increase to 2-3 for more aggressive smoothing

### `suppress_floor_roof_ceiling_3d`
- **Type:** `bool`
- **Default:** `False`
- **Description:** Exclude floors, roofs, ceilings, and structural foundations from 3D regions.

**Use case for `True`:**
- Floor plan analysis where you don't want the floor slab itself to count as "model"
- RCP analysis excluding ceiling elements

---

## Rasterization Configuration

**Location:** `CONFIG["raster"]`

Controls how regions are filled to grid cells.

### `enable_areal_fill`
- **Type:** `bool`
- **Default:** `True`
- **Description:** Fill the interior of AREAL regions (not just their boundary).

**Effect of `False`:**
- Only boundary cells are marked
- Interiors remain empty
- Useful for outline-only analysis

### `enable_linear_fill`
- **Type:** `bool`
- **Default:** `True`
- **Description:** Fill LINEAR regions (thin elements like walls in section).

**Recommendation:** Keep both `True` for standard occupancy analysis.

---

## Occupancy Codes

**Location:** `CONFIG["occupancy"]`

Defines the numeric codes for occupancy states. **Do not modify** unless you have a specific reason (e.g., custom downstream tooling).

### `code_3d_only`
- **Type:** `int`
- **Default:** `0`
- **Description:** Code for cells with only 3D geometry.

### `code_2d_only`
- **Type:** `int`
- **Default:** `1`
- **Description:** Code for cells with only 2D annotations.

### `code_2d_over_3d`
- **Type:** `int`
- **Default:** `2`
- **Description:** Code for cells with both 3D and 2D (overlap).

**Note:** These codes appear in CSV outputs and PNG colors.

---

## Run Configuration

**Location:** `CONFIG["run"]`

Controls the main processing loop.

### `max_views`
- **Type:** `int` or `None`
- **Default:** `None`
- **Description:** Limit the number of views processed (for testing/debugging).
- **Values:**
  - `None`: Process all valid views
  - `5`: Process only first 5 views

**Use cases:**
- Testing configuration changes on a small set
- Debugging specific views
- Quick validation runs

---

## Cache Configuration

**Location:** `CONFIG["cache"]`

Performance optimization via view-level caching.

### `enabled`
- **Type:** `bool`
- **Default:** `True`
- **Description:** Enable caching of view results.

**How it works:**
1. After processing a view, results are cached with hash of (view_id, config, exporter_version)
2. On subsequent runs, if hash matches, cached result is used
3. Dramatically speeds up iterative runs

**When cache is invalidated:**
- Configuration changes
- Exporter version changes
- Project GUID changes
- Elements in view are modified

### `file_name`
- **Type:** `str`
- **Default:** `"grid_cache.json"`
- **Description:** Name of the cache file (stored in output directory).

**Management:**
- Delete the cache file to force full recompute
- Back up cache file before major changes
- Cache files can be large (MB - GB for big projects)

---

## Export Configuration

**Location:** `CONFIG["export"]`

Controls CSV output generation.

### `output_dir`
- **Type:** `str`
- **Default:** `os.path.join(os.path.expanduser("~"), "Documents", "_metrics")`
- **Description:** Directory where all output files are written.

**Defaults to:**
- Windows: `C:\Users\<username>\Documents\_metrics`
- Mac/Linux: `~/Documents/_metrics`

**Behavior:**
- Directory is created if it doesn't exist
- Must have write permissions

### `core_filename`
- **Type:** `str`
- **Default:** `"views_core.csv"`
- **Description:** Base name for core metrics CSV (date is appended).

**Actual file:** `views_core_2025-12-17.csv`

### `vop_filename`
- **Type:** `str`
- **Default:** `"views_vop.csv"`
- **Description:** Base name for extended VOP metrics CSV.

**Actual file:** `views_vop_2025-12-17.csv`

### `csv_timings`
- **Type:** `str`
- **Default:** `"timings.csv"`
- **Description:** Filename for processing timing logs (not currently used).

### `enable_rows_csv`
- **Type:** `bool`
- **Default:** `True`
- **Description:** Master switch for CSV export.

**Use case for `False`:**
- Running for PNG output only
- Custom export logic

---

## Debug Configuration

**Location:** `CONFIG["debug"]`

Comprehensive debugging and diagnostic options.

### Core Debug Settings

#### `enable`
- **Type:** `bool`
- **Default:** `True`
- **Description:** Master debug switch. Enables verbose logging.

#### `write_debug_json`
- **Type:** `bool`
- **Default:** `False`
- **Description:** Write detailed debug JSON file with element lists, processing steps, etc.

**Output:** `debug_<RunID>.json` in output directory.

**Warning:** Can produce very large files (MB - GB).

#### `max_debug_views`
- **Type:** `int`
- **Default:** `10`
- **Description:** Maximum number of views to include in debug JSON.

#### `min_elapsed_for_debug_sec`
- **Type:** `float`
- **Default:** `999.0`
- **Description:** Only include views in debug JSON if processing time ≥ this value.
- **Purpose:** Focus debug output on slow views.

#### `debug_view_ids`
- **Type:** `list[int]`
- **Default:** `[]`
- **Description:** Always include these view IDs in debug output regardless of timing.

**Usage:**
```python
CONFIG["debug"]["debug_view_ids"] = [123456, 789012]  # Revit element IDs
```

#### `include_cached_views`
- **Type:** `bool`
- **Default:** `False`
- **Description:** Include cached views in debug output.
- **Recommendation:** Keep `False` to focus on newly processed views.

---

### Specialized Debug Options

#### `enable_driver2d_debug`
- **Type:** `bool`
- **Default:** `False`
- **Description:** Enable detailed logging of 2D annotation processing.

#### `driver2d_log_once_per_signature`
- **Type:** `bool`
- **Default:** `False`
- **Description:** Log each unique 2D element type only once (reduces log spam).

#### `log_large_3d_regions`
- **Type:** `bool`
- **Default:** `False`
- **Description:** Log warnings for 3D regions that occupy a large fraction of the view.

#### `large_region_fraction`
- **Type:** `float`
- **Default:** `0.8`
- **Range:** 0.0 - 1.0
- **Description:** Threshold fraction for large region warning (0.8 = 80% of view).

---

### Preview Geometry Options

#### `enable_preview_polys`
- **Type:** `bool`
- **Default:** `False`
- **Description:** Generate Dynamo geometry preview of projected polygons.

**Warning:** Very slow, only for debugging specific views.

#### `max_preview_projected_2d`
- **Type:** `int`
- **Default:** `32`
- **Description:** Maximum 2D elements to preview.

#### `max_preview_projected_3d`
- **Type:** `int`
- **Default:** `64`
- **Description:** Maximum 3D elements to preview.

---

### Region Preview Options

#### `enable_region_previews`
- **Type:** `bool`
- **Default:** `False`
- **Description:** Generate geometry preview of classified regions.

#### `max_region_areal_cells`
- **Type:** `int`
- **Default:** `2048`
- **Description:** Maximum cells in AREAL regions to preview (prevents memory issues).

---

### Run Log Options

#### `include_run_log_in_out`
- **Type:** `bool`
- **Default:** `False`
- **Description:** Include full processing log in Dynamo output dictionary.

**Effect:** Adds `"log"` key to output with all log lines.

---

### Loop Debug Options

#### `filled_region_loops`
- **Type:** `bool`
- **Default:** `False`
- **Description:** Log detailed loop information for filled regions (2D).

#### `filled_region_loops_max`
- **Type:** `int`
- **Default:** `5`
- **Description:** Maximum filled regions to log.

#### `floor_loops`
- **Type:** `bool`
- **Default:** `False`
- **Description:** Log detailed loop information for floor-like elements (3D).

#### `floor_loops_max`
- **Type:** `int`
- **Default:** `5`
- **Description:** Maximum floor elements to log.

---

## Example Configurations

### Fast Processing (Coarse Grid)
```python
CONFIG["grid"]["cell_size_paper_in"] = 0.5
CONFIG["projection"]["include_link_3d"] = False
CONFIG["cache"]["enabled"] = True
CONFIG["occupancy_png"]["enabled"] = False
CONFIG["debug"]["enable"] = False
```

### High Detail Analysis
```python
CONFIG["grid"]["cell_size_paper_in"] = 0.0625  # 1/16"
CONFIG["projection"]["silhouette"]["use_adaptive_thresholds"] = True
CONFIG["occupancy_png"]["enabled"] = True
CONFIG["occupancy_png"]["pixels_per_cell"] = 15
```

### Debug Slow Views
```python
CONFIG["debug"]["enable"] = True
CONFIG["debug"]["write_debug_json"] = True
CONFIG["debug"]["min_elapsed_for_debug_sec"] = 5.0  # Views taking > 5 sec
CONFIG["debug"]["max_debug_views"] = 20
```

### Annotation-Only Analysis
```python
CONFIG["projection"]["include_3d"] = False
CONFIG["projection"]["include_2d"] = True
CONFIG["projection"]["include_link_3d"] = False
```

---

## Configuration Validation

The exporter does **not** validate configuration at startup. Invalid settings may cause:
- Runtime errors
- Unexpected behavior
- Silent failures

**Best practices:**
1. Start with default configuration
2. Change one setting at a time
3. Test on small views
4. Document your changes

---

## Configuration Hashing

The exporter computes a hash of the CONFIG dictionary to detect changes. This hash is:
- Stored in CSV output (`ConfigHash` column)
- Used for cache invalidation
- Useful for tracking which configuration produced which results

**Note:** Changing any CONFIG value will generate a new hash and invalidate the cache.

---

**Document Version:** 1.0
**Last Updated:** 2025-12-17
**Compatible with:** SSM_Exporter_v4_A21
