# Phase 7: CSV Export Implementation Plan

## Overview

Add CSV export functionality to VOP Interwoven pipeline to match the existing SSM exporter's output format. This enables integration with existing analytics workflows and comparison tools.

---

## Current Output Format (VOP Interwoven)

### JSON Structure (per view)

```json
{
  "view_id": 123456,
  "view_name": "Level 1",
  "width": 640,
  "height": 480,
  "cell_size": 1.0,
  "tile_size": 16,
  "total_elements": 150,
  "filled_cells": 7197,
  "raster": {
    "width": 640,
    "height": 480,
    "cell_size_ft": 1.0,
    "bounds_xy": {
      "xmin": 0.0,
      "ymin": 0.0,
      "xmax": 640.0,
      "ymax": 480.0
    },
    "model_mask": [false, true, true, ...],
    "z_min": [null, 5.2, 3.1, ...],
    "model_edge_key": [-1, 0, 0, ...],
    "model_proxy_key": [-1, -1, 3, ...],
    "model_proxy_mask": [false, false, true, ...],
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
  "config": {...},
  "diagnostics": {
    "num_elements": 150,
    "num_annotations": 25,
    "num_filled_cells": 7197
  }
}
```

### Current Outputs Generated
- ✅ JSON file: `vop_export_<timestamp>.json` (full pipeline result)
- ✅ PNG files: `<ViewName>_vop.png` (color-coded occupancy visualization)

---

## Target CSV Format (SSM Exporter)

### Two CSV Files

#### 1. Core Metrics (`views_core_YYYY-MM-DD.csv`)

**Purpose**: View metadata and processing info

**Headers**:
```
Date, RunId, ViewId, ViewUniqueId, ViewName, ViewType, SheetNumber, IsOnSheet,
Scale, Discipline, Phase, ViewTemplate_Name, IsTemplate, ExporterVersion,
ConfigHash, ViewFrameHash, FromCache, ElapsedSec
```

**Example Row**:
```
2025-12-17, 20251217T143022, 123456, abc-def-123, "Level 1", FloorPlan, A101,
True, 96, Architectural, New Construction, "Default Floor Plan", False,
VOP_v1.0, a1b2c3d4, e5f6g7h8, False, 2.34
```

#### 2. VOP Extended Metrics (`views_vop_YYYY-MM-DD.csv`)

**Purpose**: Occupancy metrics and element breakdowns

**Headers**:
```
Date, RunId, ViewId, ViewName, ViewType, TotalCells, Empty, ModelOnly, AnnoOnly,
Overlap, Ext_Cells_Any, Ext_Cells_Only, Ext_Cells_DWG, Ext_Cells_RVT,
AnnoCells_TEXT, AnnoCells_TAG, AnnoCells_DIM, AnnoCells_DETAIL, AnnoCells_LINES,
AnnoCells_REGION, AnnoCells_OTHER, CellSize_ft, RowSource, ExporterVersion,
ConfigHash, FromCache, ElapsedSec
```

**Example Row**:
```
2025-12-17, 20251217T143022, 123456, "Level 1", FloorPlan, 307200, 280000,
25000, 1500, 700, 0, 0, 0, 0, 500, 200, 800, 100, 50, 50, 500, 1.0, VOP_v1.0,
VOP_v1.0, a1b2c3d4, False, 2.34
```

### CSV Validation Invariant

**CRITICAL**: All CSV files must satisfy:
```
TotalCells = Empty + ModelOnly + AnnoOnly + Overlap
```

Where:
- **TotalCells**: `width * height`
- **Empty**: Cells with neither model nor annotation
- **ModelOnly**: Cells with model but no annotation
- **AnnoOnly**: Cells with annotation but no model
- **Overlap**: Cells with both model and annotation

---

## Gap Analysis: Current vs. Target

### What We Have (VOP Interwoven)

✅ **Raster Data**:
- `model_mask` (Boolean array) - Model occupancy
- `anno_over_model` (Boolean array) - Annotation presence
- `element_meta` - Element metadata list
- `anno_meta` - Annotation metadata list

✅ **View Metadata**:
- `view_id`, `view_name`, `width`, `height`, `cell_size`

✅ **Processing Info**:
- `total_elements`, `filled_cells`

### What We Need to Add

❌ **Core CSV Fields**:
- Date (run date string)
- RunId (timestamp-based run identifier)
- ViewUniqueId (Revit UniqueId)
- ViewType (FloorPlan, Section, etc.)
- SheetNumber, IsOnSheet (sheet placement info)
- Scale (view scale)
- Discipline (Architectural, Structural, etc.)
- Phase (Revit phase name)
- ViewTemplate_Name
- IsTemplate (boolean)
- ExporterVersion (version string)
- ConfigHash (hash of config for reproducibility)
- ViewFrameHash (hash of view frame properties)
- FromCache (boolean, always False for now)
- ElapsedSec (processing time)

❌ **VOP CSV Metrics**:
- TotalCells (W * H)
- Empty (cells with no content)
- ModelOnly (model without annotation)
- AnnoOnly (annotation without model)
- Overlap (model + annotation)
- Ext_Cells_Any, Ext_Cells_Only, Ext_Cells_DWG, Ext_Cells_RVT (external refs - 0 for now)
- AnnoCells_TEXT, AnnoCells_TAG, AnnoCells_DIM, AnnoCells_DETAIL, AnnoCells_LINES, AnnoCells_REGION, AnnoCells_OTHER
- RowSource (identifier like "VOP_Interwoven_v1")

❌ **Computation Functions**:
- Cell classification (Empty, ModelOnly, AnnoOnly, Overlap)
- Annotation type counting by category
- View metadata extraction helpers
- Config hashing
- ViewFrameHash computation

---

## Implementation Plan

### File: `vop_interwoven/csv_export.py` (NEW)

**Purpose**: CSV export logic for VOP Interwoven pipeline

#### Functions to Implement

##### 1. `compute_cell_metrics(raster)`

```python
def compute_cell_metrics(raster):
    """Compute occupancy metrics from raster arrays.

    Args:
        raster: ViewRaster

    Returns:
        Dict with:
            - TotalCells: int (W * H)
            - Empty: int (neither model nor anno)
            - ModelOnly: int (model but no anno)
            - AnnoOnly: int (anno but no model)
            - Overlap: int (both model and anno)

    Validates: TotalCells == Empty + ModelOnly + AnnoOnly + Overlap
    """
```

Logic:
```python
total = raster.W * raster.H
empty = 0
model_only = 0
anno_only = 0
overlap = 0

for i in range(total):
    has_model = raster.model_mask[i]
    has_anno = raster.anno_over_model[i]

    if has_model and has_anno:
        overlap += 1
    elif has_model:
        model_only += 1
    elif has_anno:
        anno_only += 1
    else:
        empty += 1

# Validate invariant
assert total == empty + model_only + anno_only + overlap

return {
    "TotalCells": total,
    "Empty": empty,
    "ModelOnly": model_only,
    "AnnoOnly": anno_only,
    "Overlap": overlap
}
```

##### 2. `compute_annotation_type_metrics(raster)`

```python
def compute_annotation_type_metrics(raster):
    """Count annotation cells by type.

    Args:
        raster: ViewRaster

    Returns:
        Dict with:
            - AnnoCells_TEXT: int
            - AnnoCells_TAG: int
            - AnnoCells_DIM: int
            - AnnoCells_DETAIL: int
            - AnnoCells_LINES: int
            - AnnoCells_REGION: int
            - AnnoCells_OTHER: int
    """
```

Logic:
```python
counts = {
    "TEXT": 0,
    "TAG": 0,
    "DIM": 0,
    "DETAIL": 0,
    "LINES": 0,
    "REGION": 0,
    "OTHER": 0
}

for i, anno_idx in enumerate(raster.anno_key):
    if anno_idx >= 0:  # Cell has annotation
        meta = raster.anno_meta[anno_idx]
        anno_type = meta.get("type", "OTHER").upper()

        if anno_type in counts:
            counts[anno_type] += 1
        else:
            counts["OTHER"] += 1

return {f"AnnoCells_{k}": v for k, v in counts.items()}
```

##### 3. `extract_view_metadata(view, doc)`

```python
def extract_view_metadata(view, doc):
    """Extract view metadata for CSV export.

    Args:
        view: Revit View
        doc: Revit Document

    Returns:
        Dict with:
            - ViewId: int
            - ViewUniqueId: str
            - ViewName: str
            - ViewType: str (FloorPlan, Section, etc.)
            - SheetNumber: str (if on sheet)
            - IsOnSheet: bool
            - Scale: int
            - Discipline: str
            - Phase: str
            - ViewTemplate_Name: str
            - IsTemplate: bool
    """
```

##### 4. `compute_config_hash(config)`

```python
def compute_config_hash(config):
    """Compute stable hash of config for reproducibility tracking.

    Args:
        config: Config object

    Returns:
        8-character hex hash (stable across runs)
    """
```

##### 5. `compute_view_frame_hash(view)`

```python
def compute_view_frame_hash(view):
    """Compute hash of view frame properties.

    Args:
        view: Revit View

    Returns:
        8-character hex hash based on ViewType, Scale, Sheet, Discipline
    """
```

##### 6. `build_core_csv_row(view, doc, metrics, config, run_info)`

```python
def build_core_csv_row(view, doc, metrics, config, run_info):
    """Build row for core CSV.

    Args:
        view: Revit View
        doc: Revit Document
        metrics: Dict from compute_cell_metrics()
        config: Config object
        run_info: Dict with date, run_id, exporter_version, elapsed_sec

    Returns:
        List of values matching core_headers order
    """
```

##### 7. `build_vop_csv_row(view, metrics, anno_metrics, config, run_info)`

```python
def build_vop_csv_row(view, metrics, anno_metrics, config, run_info):
    """Build row for VOP extended CSV.

    Args:
        view: Revit View
        metrics: Dict from compute_cell_metrics()
        anno_metrics: Dict from compute_annotation_type_metrics()
        config: Config object
        run_info: Dict with date, run_id, exporter_version, elapsed_sec

    Returns:
        List of values matching vop_headers order
    """
```

##### 8. `export_pipeline_to_csv(pipeline_result, output_dir, config)`

```python
def export_pipeline_to_csv(pipeline_result, output_dir, config):
    """Export VOP pipeline results to CSV files.

    Args:
        pipeline_result: Result from run_vop_pipeline()
        output_dir: Output directory path
        config: Config object

    Returns:
        Dict with:
            - core_csv_path: str
            - vop_csv_path: str
            - rows_exported: int

    Creates:
        - views_core_YYYY-MM-DD.csv
        - views_vop_YYYY-MM-DD.csv
    """
```

---

### File: `vop_interwoven/entry_dynamo.py` (UPDATE)

Add new function:

```python
def run_vop_pipeline_with_csv(doc, view_ids, cfg=None, output_dir=None,
                               pixels_per_cell=4, export_csv=True):
    """Run VOP pipeline and export JSON, PNG, and CSV files.

    Args:
        doc: Revit Document
        view_ids: List of View ElementIds
        cfg: Config (optional)
        output_dir: Output directory (default: C:\\temp\\vop_output)
        pixels_per_cell: PNG resolution (default: 4)
        export_csv: Enable CSV export (default: True)

    Returns:
        Dict with:
            - pipeline_result: Full pipeline result dict
            - json_path: Path to JSON file
            - png_files: List of PNG file paths
            - core_csv_path: Path to core CSV (if export_csv=True)
            - vop_csv_path: Path to VOP CSV (if export_csv=True)
    """
```

---

### File: `vop_interwoven/dynamo_helpers.py` (UPDATE)

Update `run_pipeline_from_dynamo_input()` to add CSV export option:

```python
def run_pipeline_from_dynamo_input(
    views_input=None,
    output_dir=None,
    pixels_per_cell=4,
    config=None,
    verbose=False,
    export_csv=True  # NEW PARAMETER
):
```

---

## Testing Strategy

### Unit Tests (`vop_interwoven/tests/test_csv_export.py`)

1. **Test `compute_cell_metrics()`**
   - Verify invariant: TotalCells == Empty + ModelOnly + AnnoOnly + Overlap
   - Test empty raster (all Empty)
   - Test fully filled raster (all ModelOnly)
   - Test mixed content

2. **Test `compute_annotation_type_metrics()`**
   - Count TEXT, TAG, DIM correctly
   - Handle unknown types → OTHER
   - Handle empty anno_meta

3. **Test `compute_config_hash()`**
   - Same config → same hash
   - Different config → different hash
   - Stable across runs

4. **Test CSV row building**
   - Headers match row length
   - Values in correct positions
   - Types are correct (int, str, bool, float)

### Integration Test (Dynamo)

**Test Script** (`tests/dynamo/test_phase7_csv_export.py`):

```python
import sys
sys.path.append(r'C:\Users\gmcdowell\Documents\Revit_SSM_Exporter')

from vop_interwoven.dynamo_helpers import run_pipeline_from_dynamo_input
from vop_interwoven.config import Config
import os
import csv

# Run pipeline with CSV export
result = run_pipeline_from_dynamo_input(
    views_input=IN[0] if len(IN) > 0 else None,
    output_dir=r'C:\temp\vop_output',
    export_csv=True,
    verbose=True
)

# Verify CSV files exist
core_csv = result.get('core_csv_path')
vop_csv = result.get('vop_csv_path')

print(f"Core CSV: {core_csv}")
print(f"VOP CSV: {vop_csv}")

# Validate core CSV
if core_csv and os.path.exists(core_csv):
    with open(core_csv, 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        print(f"Core rows: {len(rows)}")
        if rows:
            print(f"Sample: {rows[0]}")

# Validate VOP CSV
if vop_csv and os.path.exists(vop_csv):
    with open(vop_csv, 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        print(f"VOP rows: {len(rows)}")
        if rows:
            # Verify invariant
            row = rows[0]
            total = int(row['TotalCells'])
            empty = int(row['Empty'])
            model = int(row['ModelOnly'])
            anno = int(row['AnnoOnly'])
            overlap = int(row['Overlap'])

            if total == empty + model + anno + overlap:
                print("✅ CSV invariant validated!")
            else:
                print(f"❌ Invariant failed: {total} != {empty + model + anno + overlap}")

OUT = result
```

---

## Success Criteria

- [ ] `csv_export.py` module created with all helper functions
- [ ] Core CSV exported with correct headers and data
- [ ] VOP CSV exported with correct headers and data
- [ ] CSV invariant validated: `TotalCells = Empty + ModelOnly + AnnoOnly + Overlap`
- [ ] Date-based filenames: `views_core_2025-12-17.csv`, `views_vop_2025-12-17.csv`
- [ ] CSV files append correctly (header written only if file doesn't exist)
- [ ] Integration with existing `run_vop_pipeline_with_png()` function
- [ ] Dynamo helper updated to support CSV export
- [ ] Unit tests pass for all metric computation functions
- [ ] Integration test validates CSV output in Dynamo

---

## CSV Headers Reference

### Core CSV Headers (18 columns)

```python
CORE_HEADERS = [
    "Date",
    "RunId",
    "ViewId",
    "ViewUniqueId",
    "ViewName",
    "ViewType",
    "SheetNumber",
    "IsOnSheet",
    "Scale",
    "Discipline",
    "Phase",
    "ViewTemplate_Name",
    "IsTemplate",
    "ExporterVersion",
    "ConfigHash",
    "ViewFrameHash",
    "FromCache",
    "ElapsedSec",
]
```

### VOP CSV Headers (27 columns)

```python
VOP_HEADERS = [
    "Date",
    "RunId",
    "ViewId",
    "ViewName",
    "ViewType",
    "TotalCells",
    "Empty",
    "ModelOnly",
    "AnnoOnly",
    "Overlap",
    "Ext_Cells_Any",
    "Ext_Cells_Only",
    "Ext_Cells_DWG",
    "Ext_Cells_RVT",
    "AnnoCells_TEXT",
    "AnnoCells_TAG",
    "AnnoCells_DIM",
    "AnnoCells_DETAIL",
    "AnnoCells_LINES",
    "AnnoCells_REGION",
    "AnnoCells_OTHER",
    "CellSize_ft",
    "RowSource",
    "ExporterVersion",
    "ConfigHash",
    "FromCache",
    "ElapsedSec",
]
```

---

## Notes

1. **FromCache**: Always `False` for VOP Interwoven (no caching yet)
2. **External Refs**: Ext_Cells_* fields all 0 for now (no RVT link support yet)
3. **RowSource**: Use identifier like `"VOP_Interwoven_v1"` to distinguish from old SSM exports
4. **ExporterVersion**: Use version string like `"VOP_v1.0.0"` or git commit hash
5. **Date Format**: YYYY-MM-DD (e.g., "2025-12-17")
6. **RunId Format**: YYYYMMDDTHHMMSS (e.g., "20251217T143022")
7. **Appending**: CSVs must append, not overwrite (multiple runs same day → same CSV)
8. **Header**: Write header only if file doesn't exist or is empty

---

## Dependencies

- Existing `export/csv.py` module (has `_append_csv_rows()` helper)
- `vop_interwoven.core.raster.ViewRaster` structure
- `vop_interwoven.config.Config` object
- Revit API for view metadata extraction

---

## Timeline Estimate

- **csv_export.py core logic**: 2-3 hours
- **Integration with entry_dynamo.py**: 1 hour
- **Unit tests**: 1-2 hours
- **Integration testing in Dynamo**: 1 hour
- **Documentation**: 30 minutes

**Total**: ~5-7 hours

---

## Future Enhancements (Post-Phase 7)

- RVT link support (populate Ext_Cells_RVT)
- DWG underlay support (populate Ext_Cells_DWG)
- Caching support (set FromCache=True when using cache)
- Element category breakdowns (ModelCells_Walls, ModelCells_Doors, etc.)
- Compression for large CSVs (optional gzip output)
