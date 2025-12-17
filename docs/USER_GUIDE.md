# SSM Exporter User Guide

## Table of Contents
1. [Overview](#overview)
2. [Installation](#installation)
3. [Quick Start](#quick-start)
4. [How It Works](#how-it-works)
5. [Running the Exporter](#running-the-exporter)
6. [Understanding the Output](#understanding-the-output)
7. [Configuration](#configuration)
8. [Troubleshooting](#troubleshooting)

---

## Overview

The **SSM/VOP (Space/View Occupancy Planner) Exporter** is a specialized tool for Autodesk Revit that analyzes 2D orthographic views and generates occupancy maps. It identifies where:
- **3D geometry** (model elements) appears
- **2D annotations** (text, tags, dimensions, detail items) appear
- **Both overlap** on the view

The exporter processes views on a configurable grid and outputs CSV files with detailed metrics and statistics.

### Supported View Types
- Floor Plans
- Reflected Ceiling Plans (RCPs)
- Sections
- Elevations
- Drafting Views
- Detail Views
- Legend Views

### Key Features
- Grid-based occupancy analysis (configurable cell size)
- Handles linked Revit models and DWG imports
- View-level caching for performance
- PNG visualization output (optional)
- Comprehensive CSV metrics export
- Adaptive element size thresholds

---

## Installation

### Requirements
- **Autodesk Revit** 2020 or later
- **Dynamo** (comes with Revit) or IronPython environment
- **Python 3.x** compatible environment

### Files Needed
```
ssm_exporter/
├── SSM_Exporter_v4_A21.py    # Main exporter script
├── export_csv.py              # CSV helper module
└── docs/                      # Documentation (optional)
```

### Setup Steps
1. Copy the exporter files to a known location on your machine
2. Note the file path to `SSM_Exporter_v4_A21.py` - you'll need it in Dynamo
3. Ensure you have write permissions to your Documents folder (default output location)

---

## Quick Start

### Basic Workflow
1. **Open your Revit model**
2. **Open Dynamo** (Manage → Dynamo)
3. **Create a Python Script node** in Dynamo
4. **Load the exporter script**:
   ```python
   import sys
   sys.path.append(r"C:\path\to\ssm_exporter")  # Adjust path

   import SSM_Exporter_v4_A21
   OUT = SSM_Exporter_v4_A21._safe_main()
   ```
5. **Run the graph**
6. **Check the output** in `~/Documents/_metrics/`

### First Run
On your first run, the exporter will:
- Process all valid orthographic 2D views in the active document
- Generate baseline cache for future runs
- Create CSV files with occupancy data
- Log progress to the Dynamo console

**Expected output files:**
```
~/Documents/_metrics/
├── views_core_2025-12-17.csv    # Core metrics
├── views_vop_2025-12-17.csv     # Extended VOP metrics
└── grid_cache.json               # Performance cache
```

---

## How It Works

### Processing Pipeline

```
1. View Collection
   ↓
2. Grid Generation (paper-space based)
   ↓
3. 3D Element Collection & Projection
   ↓
4. 2D Annotation Collection
   ↓
5. Region Building & Classification
   ↓
6. Rasterization to Grid Cells
   ↓
7. Occupancy Computation
   ↓
8. CSV Export
```

### Grid System
- **Grid cells** are sized in **paper space inches** (default: 0.125" = 1/8")
- The grid is converted to model space using the view's scale
- Example: At 1/4" = 1'-0" scale (1:48), a 1/8" paper cell = 6" in model space

### Occupancy States
Each grid cell is assigned one of three states:

| Code | State | Meaning |
|------|-------|---------|
| 0 | Model-Only | Only 3D geometry appears |
| 1 | Anno-Only | Only 2D annotations appear |
| 2 | Overlap | Both 3D and 2D overlap |

Empty cells (neither 3D nor 2D) are counted separately.

### Element Classification

**3D Elements (Model Geometry):**
- Walls, floors, roofs, ceilings
- Structural elements (beams, columns, foundations)
- MEP components (ducts, pipes, conduits)
- Furniture, casework, equipment
- Linked Revit models and DWG imports

**2D Elements (Annotations):**
- Text notes
- Tags (room, door, window, etc.)
- Dimensions
- Detail items and lines
- Filled regions
- Symbols and detail components

### Occlusion Handling
- The exporter implements **Z-buffer occlusion**
- Only **AREAL** (area-filling) 3D regions participate in occlusion
- TINY and LINEAR regions are non-occluding (considered as edges/outlines)
- This prevents thin elements from incorrectly blocking larger objects behind them

---

## Running the Exporter

### Method 1: Dynamo Python Script (Recommended)

Create a Python Script node with:

```python
import sys
import clr

# Add path to exporter
sys.path.append(r"C:\path\to\ssm_exporter")

# Import the exporter
import SSM_Exporter_v4_A21

# Run with default configuration
OUT = SSM_Exporter_v4_A21._safe_main()
```

### Method 2: With Custom Configuration

```python
import sys
sys.path.append(r"C:\path\to\ssm_exporter")

import SSM_Exporter_v4_A21

# Modify configuration before running
SSM_Exporter_v4_A21.CONFIG["grid"]["cell_size_paper_in"] = 0.25  # Larger cells
SSM_Exporter_v4_A21.CONFIG["export"]["output_dir"] = r"C:\MyOutputFolder"
SSM_Exporter_v4_A21.CONFIG["cache"]["enabled"] = False  # Disable caching

# Run
OUT = SSM_Exporter_v4_A21._safe_main()
```

### Method 3: Process Specific Views

```python
import sys
sys.path.append(r"C:\path\to\ssm_exporter")

import SSM_Exporter_v4_A21
from Autodesk.Revit.DB import FilteredElementCollector, View

# Get specific views
doc = __revit__.ActiveUIDocument.Document
collector = FilteredElementCollector(doc).OfClass(View)
my_views = [v for v in collector if "Level 1" in v.Name]

# Set the global document
SSM_Exporter_v4_A21.DOC = doc

# Process
results = []
for view in my_views:
    result = SSM_Exporter_v4_A21.process_view(
        view,
        SSM_Exporter_v4_A21.CONFIG,
        SSM_Exporter_v4_A21.LOGGER
    )
    results.append(result)

OUT = results
```

### Force Recompute (Ignore Cache)

To force recomputation of all views (ignore cache):

```python
import SSM_Exporter_v4_A21

# Set the reset flag before running
# (This is typically passed as IN[1] from a Dynamo boolean node)
SSM_Exporter_v4_A21.CONFIG["cache"]["enabled"] = False

OUT = SSM_Exporter_v4_A21._safe_main()
```

---

## Understanding the Output

### CSV Structure

#### Core Metrics File (`views_core_YYYY-MM-DD.csv`)
Basic occupancy metrics for each view:

| Column | Description |
|--------|-------------|
| `RunDate` | Timestamp of the export run |
| `RunID` | Unique identifier for this run |
| `ViewID` | Revit element ID of the view |
| `ViewName` | Name of the view |
| `ViewType` | Type (FloorPlan, Elevation, etc.) |
| `TotalCells` | Total grid cells in the view |
| `EmptyCells` | Cells with neither 3D nor 2D |
| `ModelOnlyCells` | Cells with only 3D geometry (code 0) |
| `AnnoOnlyCells` | Cells with only 2D annotations (code 1) |
| `OverlapCells` | Cells with both 3D and 2D (code 2) |
| `CellSize_ft` | Cell size in model feet |
| `ExporterVersion` | Version identifier |
| `ConfigHash` | Hash of configuration used |
| `FromCache` | Whether result was cached |
| `ElapsedSec` | Processing time in seconds |

#### VOP Extended File (`views_vop_YYYY-MM-DD.csv`)
Additional breakdown of element types:

Includes all core columns plus:
- `ExtCells_Any` - Cells with external references (links/imports)
- `ExtCells_OnlyExt` - Cells with ONLY external elements
- `ExtCells_DWG` - Cells with DWG import elements
- `ExtCells_RVT` - Cells with linked Revit elements
- `AnnoCells_TEXT` - Cells with text annotations
- `AnnoCells_TAG` - Cells with tags
- `AnnoCells_DIM` - Cells with dimensions
- `AnnoCells_DETAIL` - Cells with detail items
- `AnnoCells_LINES` - Cells with detail lines
- `AnnoCells_REGION` - Cells with filled regions
- `AnnoCells_OTHER` - Cells with other annotations

### CSV Validation Rule

All CSVs must satisfy this invariant:
```
TotalCells = EmptyCells + ModelOnlyCells + AnnoOnlyCells + OverlapCells
```

This ensures complete accounting of all grid cells.

### PNG Visualization (Optional)

When enabled via `CONFIG["occupancy_png"]["enabled"] = True`:
- PNG images are generated in the output directory
- Each cell is colored based on occupancy:
  - **White**: Empty
  - **Blue**: Model-only (3D)
  - **Yellow**: Anno-only (2D)
  - **Red**: Overlap (both)
- Useful for visual debugging and presentations

---

## Configuration

See the separate [CONFIGURATION.md](CONFIGURATION.md) document for detailed configuration options.

### Quick Configuration Tips

**Adjust Grid Resolution:**
```python
# Finer grid (smaller cells, more detail, slower)
CONFIG["grid"]["cell_size_paper_in"] = 0.0625  # 1/16"

# Coarser grid (larger cells, less detail, faster)
CONFIG["grid"]["cell_size_paper_in"] = 0.5     # 1/2"
```

**Change Output Location:**
```python
CONFIG["export"]["output_dir"] = r"C:\MyProject\Metrics"
```

**Enable PNG Output:**
```python
CONFIG["occupancy_png"]["enabled"] = True
CONFIG["occupancy_png"]["pixels_per_cell"] = 10
```

**Disable Linked Models:**
```python
CONFIG["projection"]["include_link_3d"] = False
```

**Limit View Processing (for testing):**
```python
CONFIG["run"]["max_views"] = 5  # Only process first 5 views
```

---

## Troubleshooting

### Problem: "No output generated"

**Possible Causes:**
- No valid orthographic 2D views in the model
- Views are excluded by type (templates, schedules, etc.)
- Output directory lacks write permissions

**Solution:**
- Check that you have Floor Plans, Sections, or Elevations in the model
- Verify the output directory exists and is writable
- Check Dynamo console for error messages

---

### Problem: "Processing is very slow"

**Possible Causes:**
- Very large views with small grid cells
- Many linked models
- Cache is disabled

**Solutions:**
- Increase cell size: `CONFIG["grid"]["cell_size_paper_in"] = 0.25`
- Enable caching: `CONFIG["cache"]["enabled"] = True`
- Exclude links: `CONFIG["projection"]["include_link_3d"] = False`
- Limit max cells: `CONFIG["grid"]["max_cells"] = 100000`

---

### Problem: "Cache not working"

**Possible Causes:**
- Cache file corrupted or from incompatible version
- Configuration changed (different config hash)
- Project GUID changed

**Solutions:**
- Delete `grid_cache.json` file to regenerate
- Ensure configuration is consistent between runs
- Use force recompute flag to bypass cache once

---

### Problem: "Results don't match expectations"

**Possible Causes:**
- View range settings affecting 3D visibility
- Detail level affecting geometry representation
- Linked models not loading

**Solutions:**
- Review view range settings (View → View Range)
- Check detail level (Fine/Medium/Coarse)
- Verify linked models are loaded
- Enable debug JSON output to inspect element lists

---

### Problem: "Missing annotations in output"

**Possible Causes:**
- Annotations outside view crop region
- Annotation categories not in whitelist
- View scale affecting visibility

**Solutions:**
- Verify annotations are visible in Revit view
- Check `correctness_contract.md` for annotation category whitelist
- Review view crop region boundaries

---

### Debug Mode

Enable detailed logging:

```python
CONFIG["debug"]["enable"] = True
CONFIG["debug"]["write_debug_json"] = True
CONFIG["debug"]["include_run_log_in_out"] = True
```

This will:
- Write detailed JSON file with element lists
- Include full processing log in output
- Help diagnose processing issues

---

## Advanced Usage

### Batch Processing Multiple Projects

Create a Dynamo graph that:
1. Opens each Revit file in sequence
2. Runs the exporter
3. Closes and moves to next file

(Requires Dynamo Player or custom automation)

### Integration with Other Tools

The CSV output can be:
- Imported into Excel/Power BI for analysis
- Processed with Python pandas for custom metrics
- Visualized in Tableau or similar BI tools
- Used for quality control dashboards

### Custom Metrics

Extend the exporter by:
1. Adding custom fields to the output dictionary
2. Modifying `_export_view_level_csvs()` to include new columns
3. Following the BEHAVIOR CHANGE commit pattern

---

## Getting Help

### Resources
- Review [correctness_contract.md](correctness_contract.md) for behavior specification
- Check [CONFIGURATION.md](CONFIGURATION.md) for all config options
- See [ARCHITECTURE.md](ARCHITECTURE.md) for design decisions
- Review [REFRACTOR_PLAN.md](REFRACTOR_PLAN.md) for planned improvements

### Common Issues
See the [Troubleshooting](#troubleshooting) section above.

### Reporting Bugs
When reporting issues, include:
1. Revit and Dynamo version
2. Sample view that reproduces the issue
3. Configuration used
4. Error messages from Dynamo console
5. Debug JSON output (if available)

---

## Best Practices

1. **Start with defaults** - Run with default configuration first
2. **Test on small views** - Validate results on simple views before batch processing
3. **Enable caching** - Speeds up iterative runs significantly
4. **Back up cache** - Save `grid_cache.json` before major changes
5. **Validate outputs** - Verify the TotalCells reconciliation formula
6. **Use PNG preview** - Enable for visual confirmation during development
7. **Monitor performance** - Check `ElapsedSec` column to identify slow views
8. **Document configuration** - Save your CONFIG dictionary for reproducibility

---

## Appendix: Typical Workflows

### Workflow 1: Initial Project Analysis
1. Open project in Revit
2. Run exporter with default settings
3. Review core metrics CSV
4. Identify views with high overlap
5. Generate PNG visualizations for problem views

### Workflow 2: Quality Control
1. Run exporter on current model
2. Compare with baseline metrics from previous phase
3. Flag views with significant changes
4. Investigate views with unexpected occupancy patterns

### Workflow 3: Optimization
1. Run with fine grid (0.0625")
2. Export to Power BI
3. Analyze annotation density by floor/zone
4. Identify areas needing annotation cleanup

---

**Document Version:** 1.0
**Last Updated:** 2025-12-17
**Compatible with:** SSM_Exporter_v4_A21
