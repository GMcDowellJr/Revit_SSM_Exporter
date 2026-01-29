# VOP Interwoven Pipeline - Progressive Implementation Plan

## Overview

This plan breaks down the Revit API integration into testable increments, allowing you to verify each module works before moving to the next. Each phase has clear success criteria and minimal Revit dependencies.

---

## Phase 0: Foundation ✅ COMPLETE

**Status**: All core logic implemented and tested

**Components**:
- ✅ `config.py` - Configuration with adaptive tile sizing
- ✅ `core/geometry.py` - UV classification and proxy generation
- ✅ `core/raster.py` - ViewRaster and TileMap structures
- ✅ `core/math_utils.py` - Geometric utilities
- ✅ `entry_dynamo.py` - CPython3-compatible Dynamo entry point

**Test Coverage**: 29/29 unit tests passing

**Next**: Integrate Revit API for coordinate transforms and element collection

---

## Phase 1: View Basis & Coordinate System

**Goal**: Get view coordinate transforms working in Revit context

**File**: `revit/view_basis.py`

### Implementation Tasks

1. **Implement `make_view_basis(view)`**
   ```python
   def make_view_basis(view):
       """Extract view basis from Revit View."""
       from Autodesk.Revit.DB import View

       origin = view.Origin
       right = view.RightDirection
       up = view.UpDirection
       forward = right.CrossProduct(up).Normalize()

       return ViewBasis(
           origin=(origin.X, origin.Y, origin.Z),
           right=(right.X, right.Y, right.Z),
           up=(up.X, up.Y, up.Z),
           forward=(forward.X, forward.Y, forward.Z)
       )
   ```

2. **Implement `world_to_view(pt, vb)`**
   ```python
   def world_to_view(pt, vb):
       """Transform world point to view coordinates."""
       # Vector from origin to point
       dx = pt[0] - vb.origin[0]
       dy = pt[1] - vb.origin[1]
       dz = pt[2] - vb.origin[2]

       # Project onto view axes
       x = dx*vb.right[0] + dy*vb.right[1] + dz*vb.right[2]
       y = dx*vb.up[0] + dy*vb.up[1] + dz*vb.up[2]
       z = dx*vb.forward[0] + dy*vb.forward[1] + dz*vb.forward[2]

       return (x, y, z)
   ```

### Testing in Dynamo

**Test Script** (`test_phase1_view_basis.py`):
```python
import sys
sys.path.append(r'C:\path\to\Revit_SSM_Exporter')

from vop_interwoven.entry_dynamo import get_current_document, get_current_view
from vop_interwoven.revit.view_basis import make_view_basis, world_to_view

doc = get_current_document()
view = get_current_view()

# Test 1: Extract view basis
vb = make_view_basis(view)
print("View Origin:", vb.origin)
print("Right:", vb.right)
print("Up:", vb.up)
print("Forward:", vb.forward)

# Test 2: Transform a known point
world_pt = (0, 0, 0)  # Revit origin
view_pt = world_to_view(world_pt, vb)
print("World (0,0,0) -> View:", view_pt)

# Test 3: Transform view origin (should be ~(0,0,0) in view space)
view_pt_origin = world_to_view(vb.origin, vb)
print("View origin in view space:", view_pt_origin)

OUT = "✅ Phase 1 tests complete"
```

### Success Criteria

- [ ] View basis extracted without errors
- [ ] Right, Up, Forward vectors are orthonormal (dot products correct)
- [ ] View origin transforms to ~(0,0,0) in view space
- [ ] Known world points transform consistently

**Estimated Revit API calls**: `View.Origin`, `View.RightDirection`, `View.UpDirection`

---

## Phase 2: Element Collection & Bounding Boxes

**Goal**: Collect 3D elements and get their bounding boxes in view space

**Files**: `revit/collection.py`, `revit/view_basis.py`

### Implementation Tasks

1. **Implement `collect_view_elements(doc, view, raster)`**
   ```python
   from Autodesk.Revit.DB import FilteredElementCollector, View3D, ViewType
   from Autodesk.Revit.DB import BuiltInCategory, ElementId

   def collect_view_elements(doc, view, raster):
       """Collect 3D model elements visible in view."""
       collector = FilteredElementCollector(doc, view.Id)

       # Filter to 3D model categories
       model_categories = [
           BuiltInCategory.OST_Walls,
           BuiltInCategory.OST_Floors,
           BuiltInCategory.OST_Roofs,
           BuiltInCategory.OST_Doors,
           BuiltInCategory.OST_Windows,
           BuiltInCategory.OST_Columns,
           BuiltInCategory.OST_StructuralFraming,
           # Add more as needed
       ]

       elements = []
       for cat in model_categories:
           cat_elements = collector.OfCategory(cat).WhereElementIsNotElementType()
           elements.extend(cat_elements)

       return elements
   ```

2. **Implement `_project_element_bbox_to_cell_rect(elem, vb, raster)`**
   ```python
   def _project_element_bbox_to_cell_rect(elem, vb, raster):
       """Project element bounding box to cell rectangle."""
       bbox = elem.get_BoundingBox(None)  # World coordinates
       if bbox is None:
           return None

       # Transform bbox corners to view space
       min_pt = world_to_view((bbox.Min.X, bbox.Min.Y, bbox.Min.Z), vb)
       max_pt = world_to_view((bbox.Max.X, bbox.Max.Y, bbox.Max.Z), vb)

       # Project to UV cells
       u_min = int((min_pt[0] - raster.bounds.x_min) / raster.cell_size)
       v_min = int((min_pt[1] - raster.bounds.y_min) / raster.cell_size)
       u_max = int((max_pt[0] - raster.bounds.x_min) / raster.cell_size)
       v_max = int((max_pt[1] - raster.bounds.y_min) / raster.cell_size)

       from vop_interwoven.core.math_utils import CellRect
       return CellRect(u_min, v_min, u_max, v_max)
   ```

### Testing in Dynamo

**Test Script** (`test_phase2_collection.py`):
```python
import sys
sys.path.append(r'C:\path\to\Revit_SSM_Exporter')

from vop_interwoven.entry_dynamo import get_current_document, get_current_view
from vop_interwoven.config import Config
from vop_interwoven.pipeline import init_view_raster
from vop_interwoven.revit.collection import collect_view_elements
from vop_interwoven.revit.view_basis import make_view_basis

doc = get_current_document()
view = get_current_view()
cfg = Config()

# Test 1: Initialize raster
raster = init_view_raster(doc, view, cfg)
print(f"Raster: {raster.width}x{raster.height} cells")

# Test 2: Collect elements
vb = make_view_basis(view)
elements = collect_view_elements(doc, view, raster)
print(f"Found {len(elements)} elements")

# Test 3: Show element categories
from collections import Counter
categories = Counter([elem.Category.Name for elem in elements if elem.Category])
for cat, count in categories.most_common(10):
    print(f"  {cat}: {count}")

OUT = f"✅ Phase 2: {len(elements)} elements collected"
```

### Success Criteria

- [ ] FilteredElementCollector returns elements without errors
- [ ] Element count matches expected model size
- [ ] Element categories are correct (Walls, Floors, etc.)
- [ ] Bounding boxes transform to valid cell rectangles

**Estimated Revit API calls**: `FilteredElementCollector`, `Element.get_BoundingBox`, `Element.Category`

---

## Phase 3: UV Classification & Proxy Generation

**Goal**: Classify collected elements and generate proxies for TINY/LINEAR

**Files**: `core/geometry.py`, `pipeline.py`

### Implementation Tasks

1. **Integrate classification into pipeline**
   ```python
   def classify_and_proxy_elements(elements, vb, raster, cfg):
       """Classify elements and generate proxies."""
       from vop_interwoven.core.geometry import classify_by_uv, make_uv_aabb
       from vop_interwoven.revit.collection import _project_element_bbox_to_cell_rect

       classified = {
           "TINY": [],
           "LINEAR": [],
           "AREAL": []
       }

       for elem in elements:
           rect = _project_element_bbox_to_cell_rect(elem, vb, raster)
           if rect is None or rect.empty:
               continue

           mode = classify_by_uv(rect.width_cells, rect.height_cells, cfg)

           if mode == Mode.TINY:
               proxy = make_uv_aabb(rect)
               classified["TINY"].append((elem, proxy))
           elif mode == Mode.LINEAR:
               # OBB generation placeholder
               classified["LINEAR"].append((elem, None))
           else:
               classified["AREAL"].append((elem, None))

       return classified
   ```

### Testing in Dynamo

**Test Script** (`test_phase3_classification.py`):
```python
import sys
sys.path.append(r'C:\path\to\Revit_SSM_Exporter')

from vop_interwoven.entry_dynamo import get_current_document, get_current_view
from vop_interwoven.config import Config
from vop_interwoven.pipeline import init_view_raster
from vop_interwoven.revit.collection import collect_view_elements
from vop_interwoven.revit.view_basis import make_view_basis
from vop_interwoven.core.geometry import classify_by_uv, make_uv_aabb, Mode
from vop_interwoven.revit.collection import _project_element_bbox_to_cell_rect

doc = get_current_document()
view = get_current_view()
cfg = Config(tiny_max=2, thin_max=2)

raster = init_view_raster(doc, view, cfg)
vb = make_view_basis(view)
elements = collect_view_elements(doc, view, raster)

# Classify elements
tiny_count = 0
linear_count = 0
areal_count = 0

for elem in elements[:100]:  # Test first 100
    rect = _project_element_bbox_to_cell_rect(elem, vb, raster)
    if rect is None or rect.empty:
        continue

    mode = classify_by_uv(rect.width_cells, rect.height_cells, cfg)

    if mode == Mode.TINY:
        tiny_count += 1
    elif mode == Mode.LINEAR:
        linear_count += 1
    else:
        areal_count += 1

print(f"TINY: {tiny_count}")
print(f"LINEAR: {linear_count}")
print(f"AREAL: {areal_count}")

OUT = f"✅ Phase 3: {tiny_count}T/{linear_count}L/{areal_count}A"
```

### Success Criteria

- [ ] Elements classify into TINY/LINEAR/AREAL categories
- [ ] Classification ratios seem reasonable for model
- [ ] UV_AABB proxies generate without errors
- [ ] Proxy bounds match element cell rectangles

**New dependencies**: None (uses existing core logic)

---

## Phase 4: Geometry Tessellation & Rasterization

**Goal**: Tessellate AREAL elements and rasterize to depth buffer

**Files**: `revit/geometry.py` (new), `pipeline.py`

### Implementation Tasks

1. **Create `revit/geometry.py`**
   ```python
   """Revit geometry extraction and tessellation."""

   from Autodesk.Revit.DB import Options, GeometryInstance

   def extract_triangles(elem, vb):
       """Extract triangulated geometry from element."""
       options = Options()
       options.DetailLevel = ViewDetailLevel.Fine
       options.IncludeNonVisibleObjects = False

       geom = elem.get_Geometry(options)
       if geom is None:
           return []

       triangles = []
       for obj in geom:
           if isinstance(obj, GeometryInstance):
               # Handle instances (families, groups)
               inst_geom = obj.GetInstanceGeometry()
               triangles.extend(_extract_from_geom(inst_geom, vb))
           else:
               triangles.extend(_extract_from_geom([obj], vb))

       return triangles

   def _extract_from_geom(geom, vb):
       """Extract triangles from geometry objects."""
       from Autodesk.Revit.DB import Solid, Face, Mesh
       from vop_interwoven.revit.view_basis import world_to_view

       triangles = []
       for obj in geom:
           if isinstance(obj, Solid):
               for face in obj.Faces:
                   mesh = face.Triangulate()
                   for i in range(mesh.NumTriangles):
                       tri = mesh.get_Triangle(i)
                       v0 = world_to_view((tri.get_Vertex(0).X, tri.get_Vertex(0).Y, tri.get_Vertex(0).Z), vb)
                       v1 = world_to_view((tri.get_Vertex(1).X, tri.get_Vertex(1).Y, tri.get_Vertex(1).Z), vb)
                       v2 = world_to_view((tri.get_Vertex(2).X, tri.get_Vertex(2).Y, tri.get_Vertex(2).Z), vb)
                       triangles.append((v0, v1, v2))

       return triangles
   ```

2. **Implement rasterization**
   ```python
   def rasterize_triangle(tri, raster):
       """Rasterize triangle to depth buffer (scanline)."""
       v0, v1, v2 = tri

       # Project to UV cells
       u0 = int((v0[0] - raster.bounds.x_min) / raster.cell_size)
       v0_y = int((v0[1] - raster.bounds.y_min) / raster.cell_size)
       # ... similar for v1, v2

       # Simple scanline rasterization
       # (Full implementation would use efficient scanline algorithm)
       # For now, just mark bounding box cells
       u_min = min(u0, u1, u2)
       u_max = max(u0, u1, u2)
       v_min = min(v0_y, v1_y, v2_y)
       v_max = max(v0_y, v1_y, v2_y)

       for u in range(u_min, u_max + 1):
           for v in range(v_min, v_max + 1):
               depth = min(v0[2], v1[2], v2[2])  # Conservative depth
               raster.set_cell_filled(u, v, depth)
   ```

### Testing in Dynamo

**Test Script** (`test_phase4_geometry.py`):
```python
import sys
sys.path.append(r'C:\path\to\Revit_SSM_Exporter')

from vop_interwoven.entry_dynamo import get_current_document, get_current_view
from vop_interwoven.config import Config
from vop_interwoven.pipeline import init_view_raster
from vop_interwoven.revit.view_basis import make_view_basis
from vop_interwoven.revit.geometry import extract_triangles

doc = get_current_document()
view = get_current_view()
cfg = Config()

raster = init_view_raster(doc, view, cfg)
vb = make_view_basis(view)

# Test on a single wall
from Autodesk.Revit.DB import FilteredElementCollector, BuiltInCategory
walls = FilteredElementCollector(doc, view.Id).OfCategory(BuiltInCategory.OST_Walls).WhereElementIsNotElementType()
wall = next(iter(walls), None)

if wall:
    triangles = extract_triangles(wall, vb)
    print(f"Wall ID {wall.Id}: {len(triangles)} triangles")

    # Show first triangle
    if triangles:
        t0 = triangles[0]
        print(f"  Triangle 0:")
        print(f"    v0: {t0[0]}")
        print(f"    v1: {t0[1]}")
        print(f"    v2: {t0[2]}")

    OUT = f"✅ Phase 4: {len(triangles)} triangles extracted"
else:
    OUT = "⚠ No walls found"
```

### Success Criteria

- [ ] Triangles extract without errors
- [ ] Triangle count reasonable for element complexity
- [ ] Triangle vertices in view space (not world)
- [ ] Depth values increase away from camera

**New Revit API calls**: `Element.get_Geometry`, `Options`, `Solid.Faces`, `Face.Triangulate`

---

## Phase 5: Interwoven Pass Integration

**Goal**: Combine all phases into full interwoven pipeline

**Files**: `pipeline.py`

### Implementation Tasks

1. **Complete `run_interwoven_pass(doc, view, cfg)`**
   - Sort elements front-to-back by depth
   - Iterate through elements
   - Apply early-out tests per mode
   - Rasterize/stamp visible elements
   - Track metadata

2. **Add progress reporting**
   ```python
   def run_interwoven_pass(doc, view, cfg):
       elements = collect_view_elements(doc, view, raster)

       total = len(elements)
       processed = 0

       for elem in sorted_elements:
           processed += 1
           if processed % 100 == 0:
               print(f"Progress: {processed}/{total} ({100*processed//total}%)")

           # ... process element ...
   ```

### Testing in Dynamo

**Full Pipeline Test** (`test_phase5_full.py`):
```python
import sys
sys.path.append(r'C:\path\to\Revit_SSM_Exporter')

from vop_interwoven.entry_dynamo import run_vop_pipeline, get_current_document, get_current_view
from vop_interwoven.config import Config

doc = get_current_document()
view = get_current_view()

cfg = Config(
    cell_size_paper_in=0.125,          # Cell size in paper inches
    adaptive_tile_size=True,
    tiny_max=2,
    thin_max=2,
    over_model_includes_proxies=False  # Whether proxies affect "over model"
)

result = run_vop_pipeline(doc, [view.Id], cfg)

# Inspect result
view_result = result["views"][0]
print(f"View: {view_result['view_name']}")
print(f"Grid: {view_result['width']}x{view_result['height']}")
print(f"Tile size: {view_result['tile_size']}")
print(f"Elements processed: {view_result['total_elements']}")
print(f"Model filled cells: {view_result['filled_cells']}")

OUT = result
```

### Success Criteria

- [ ] Full pipeline runs without errors
- [ ] Reasonable processing time (< 30s for small model)
- [ ] Filled cells count matches expected coverage
- [ ] Output format matches specification

---

## Phase 6: Edge Rasterization & Polish

**Goal**: Add edge detection and finalize output

**Files**: `revit/geometry.py`, `pipeline.py`

### Implementation Tasks

1. **Implement edge detection**
   - Detect boundary edges from triangulation
   - Project to 2D view space
   - Bresenham line rasterization
   - Mark edge layer in raster

2. **Add annotation collection**
   - Filter 2D annotation elements
   - Project to view space
   - Mark in raster

3. **Finalize output format**
   - Export model_mask as binary array
   - Export edge layer
   - Export anno_over_model
   - Include all metadata

### Success Criteria

- [ ] Edge layer populated for model elements
- [ ] Annotations collected and rasterized
- [ ] `anno_over_model` correctly computed
- [ ] Output matches SSM format expectations

---

## Testing Workflow

### For Each Phase

1. **Read existing tests** in `tests/` to understand expected behavior
2. **Write Dynamo test script** from phase template
3. **Run in Dynamo**, capture output
4. **Fix errors** reported by Dynamo
5. **Verify success criteria** before moving to next phase
6. **Commit working code** after each phase

### Example Iteration

```
User: "Phase 1 test fails: AttributeError: 'View3D' object has no attribute 'Origin'"
Claude: *checks Revit API docs* "View3D doesn't expose Origin directly. Need to use view.GetOrientation().EyePosition instead"
Claude: *edits view_basis.py*
User: "Now it works! ✅"
Claude: *moves to Phase 2*
```

### Git Workflow Per Phase

```bash
# After each phase success
git add vop_interwoven/
git commit -m "Phase N: [description]"
git push -u origin claude/vop-interwoven-pipeline-Dmhju
```

---

## Dependency Graph

```
Phase 1 (View Basis)
  ↓
Phase 2 (Element Collection)
  ↓
Phase 3 (Classification) ← Uses Phase 1+2
  ↓
Phase 4 (Geometry) ← Uses Phase 1+2
  ↓
Phase 5 (Integration) ← Uses all above
  ↓
Phase 6 (Polish) ← Uses Phase 5
```

---

## Quick Reference: Test All Phases

Create `test_all_phases.py`:

```python
import sys
sys.path.append(r'C:\path\to\Revit_SSM_Exporter')

from vop_interwoven.entry_dynamo import get_current_document, get_current_view
from vop_interwoven.config import Config

doc = get_current_document()
view = get_current_view()
cfg = Config()

results = []

# Phase 1: View Basis
try:
    from vop_interwoven.revit.view_basis import make_view_basis
    vb = make_view_basis(view)
    results.append("✅ Phase 1: View Basis")
except Exception as e:
    results.append(f"❌ Phase 1: {e}")

# Phase 2: Collection
try:
    from vop_interwoven.pipeline import init_view_raster
    from vop_interwoven.revit.collection import collect_view_elements
    raster = init_view_raster(doc, view, cfg)
    elements = collect_view_elements(doc, view, raster)
    results.append(f"✅ Phase 2: {len(elements)} elements")
except Exception as e:
    results.append(f"❌ Phase 2: {e}")

# Phase 3: Classification
try:
    from vop_interwoven.core.geometry import classify_by_uv, Mode
    from vop_interwoven.revit.collection import _project_element_bbox_to_cell_rect

    elem = elements[0]
    rect = _project_element_bbox_to_cell_rect(elem, vb, raster)
    mode = classify_by_uv(rect.width_cells, rect.height_cells, cfg)
    results.append(f"✅ Phase 3: Classification works")
except Exception as e:
    results.append(f"❌ Phase 3: {e}")

# ... continue for other phases

OUT = "\n".join(results)
```

---

## Phase 7: CSV Export

**Goal**: Add CSV export matching SSM exporter format for analytics integration

**Status**: ✅ Complete (implemented in `csv_export.py`)

### Why This Phase?

Enables integration with existing SSM analytics workflows and provides:
- Per-view occupancy metrics
- Element type breakdowns
- Comparison with legacy SSM outputs
- Data validation via CSV invariants

### Implementation Tasks

**File**: `vop_interwoven/csv_export.py` (NEW)

1. **Cell Metrics Computation**
   ```python
   def compute_cell_metrics(raster):
       """Compute Empty/ModelOnly/AnnoOnly/Overlap from raster arrays.

       CRITICAL: Must validate invariant:
           TotalCells = Empty + ModelOnly + AnnoOnly + Overlap
       """
   ```

2. **Annotation Type Metrics**
   ```python
   def compute_annotation_type_metrics(raster):
       """Count annotation cells by type (TEXT/TAG/DIM/DETAIL/LINES/REGION/OTHER)."""
   ```

3. **View Metadata Extraction**
   ```python
   def extract_view_metadata(view, doc):
       """Extract Scale, Discipline, Phase, Sheet info, ViewType, etc."""
   ```

4. **Config & Frame Hashing**
   ```python
   def compute_config_hash(config):
       """Stable 8-char hash for reproducibility tracking."""

   def compute_view_frame_hash(view):
       """Hash of ViewType, Scale, Sheet, Discipline."""
   ```

5. **CSV Row Building**
   ```python
   def build_core_csv_row(view, doc, metrics, config, run_info):
       """Build 18-column core CSV row."""

   def build_vop_csv_row(view, metrics, anno_metrics, config, run_info):
       """Build 27-column VOP CSV row."""
   ```

6. **Export Function**
   ```python
   def export_pipeline_to_csv(pipeline_result, output_dir, config):
       """Export to views_core_YYYY-MM-DD.csv and views_vop_YYYY-MM-DD.csv."""
   ```

### CSV Output Files

**1. Core Metrics** (`views_core_YYYY-MM-DD.csv` - 18 columns):
```
Date, RunId, ViewId, ViewUniqueId, ViewName, ViewType, SheetNumber, IsOnSheet,
Scale, Discipline, Phase, ViewTemplate_Name, IsTemplate, ExporterVersion,
ConfigHash, ViewFrameHash, FromCache, ElapsedSec
```

**2. VOP Extended** (`views_vop_YYYY-MM-DD.csv` - 27 columns):
```
Date, RunId, ViewId, ViewName, ViewType, TotalCells, Empty, ModelOnly, AnnoOnly,
Overlap, Ext_Cells_Any, Ext_Cells_Only, Ext_Cells_DWG, Ext_Cells_RVT,
AnnoCells_TEXT, AnnoCells_TAG, AnnoCells_DIM, AnnoCells_DETAIL, AnnoCells_LINES,
AnnoCells_REGION, AnnoCells_OTHER, CellSize_ft, RowSource, ExporterVersion,
ConfigHash, FromCache, ElapsedSec
```

### Testing

**Test Script** (`tests/dynamo/test_phase7_csv_export.py`):
```python
import sys
sys.path.append(r'C:\Users\gmcdowell\Documents\Revit_SSM_Exporter')

from vop_interwoven.dynamo_helpers import run_pipeline_from_dynamo_input
import csv

result = run_pipeline_from_dynamo_input(
    views_input=IN[0] if len(IN) > 0 else None,
    export_csv=True
)

# Validate invariant
with open(result['vop_csv_path'], 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        total = int(row['TotalCells'])
        empty = int(row['Empty'])
        model = int(row['ModelOnly'])
        anno = int(row['AnnoOnly'])
        overlap = int(row['Overlap'])

        assert total == empty + model + anno + overlap, \
            f"Invariant failed: {total} != {empty + model + anno + overlap}"

print("✅ CSV export validated!")
OUT = result
```

### Success Criteria

- [ ] Core CSV exported with correct 18 columns
- [ ] VOP CSV exported with correct 27 columns
- [ ] CSV invariant validated: `TotalCells = Empty + ModelOnly + AnnoOnly + Overlap`
- [ ] Date-based filenames work correctly
- [ ] CSVs append (header written only once)
- [ ] Integration with `run_vop_pipeline_with_png()`
- [ ] Unit tests pass for metric computation
- [ ] Dynamo integration test validates output

**Dependencies**: Phases 0-5 (working pipeline with model & annotation rasters)

**Estimated Time**: 5-7 hours

---

## Phase 8a: Annotation Collection & Rasterization

**Goal**: Collect 2D annotation elements and rasterize to anno_key layer

**Status**: ✅ Complete (implemented in `revit/annotation.py`)

### Why This Phase?

Currently `anno_key` and `anno_over_model` are empty. Need to:
- Collect 2D view-specific elements
- Classify by type (TEXT/TAG/DIM/DETAIL/LINES/REGION)
- Rasterize bounding boxes to cells
- Populate `anno_key` and `anno_meta` arrays

### Implementation Tasks

**File**: `vop_interwoven/revit/annotation.py` (NEW)

1. **Annotation Collection**
   ```python
   def collect_2d_annotations(doc, view):
       """Collect view-specific 2D elements by whitelist.

       Categories:
           - TextNote (TEXT)
           - User Keynotes (TEXT)
           - Dimension (DIM)
           - IndependentTag, RoomTag (TAG)
           - Material Element Keynotes (TAG)
           - FilledRegion (REGION)
           - DetailCurve, CurveElement (LINES)
           - FamilyInstance (view-specific) (DETAIL)

       Returns:
           List of tuples: [(element, anno_type), ...]
       """
   ```

2. **Annotation Classification**
   ```python
   def classify_annotation(elem):
       """Classify annotation element into type.

       Keynote handling:
           - Material Element Keynote → TAG
           - User Keynote → TEXT

       Returns:
           "TEXT" | "TAG" | "DIM" | "DETAIL" | "LINES" | "REGION" | "OTHER"
       """
   ```

3. **Bounding Box Extraction**
   ```python
   def get_annotation_bbox(elem, view):
       """Get annotation bounding box in view coordinates.

       Handles:
           - Text with rotation
           - Dimensions (linear, radial, angular)
           - Tags with leader lines
           - Detail components
       """
   ```

4. **Rasterization**
   ```python
   def rasterize_annotations(doc, view, raster, cfg):
       """Rasterize 2D annotations to anno_key layer.

       For each annotation:
           1. Get bounding box
           2. Project to cell rect
           3. Fill cells in anno_key with metadata index
           4. Track in anno_meta
       """
   ```

**File**: `vop_interwoven/pipeline.py` (UPDATE)

Uncomment and implement line 81-82:
```python
# 4) ANNO PASS (2D only, no occlusion effect)
rasterize_2d_annotations(doc, view, raster, cfg)
```

### Testing

**Test Script** (`tests/dynamo/test_phase8a_annotations.py`):
```python
from vop_interwoven.revit.annotation import collect_2d_annotations
from vop_interwoven.entry_dynamo import get_current_document, get_current_view

doc = get_current_document()
view = get_current_view()

annotations = collect_2d_annotations(doc, view)

# Group by type
from collections import Counter
type_counts = Counter(anno_type for _, anno_type in annotations)

print(f"Total annotations: {len(annotations)}")
for atype, count in type_counts.most_common():
    print(f"  {atype}: {count}")

OUT = f"✅ Phase 8a: {len(annotations)} annotations collected"
```

### Success Criteria

- [ ] Annotations collected by category whitelist
- [ ] Classification logic matches SSM (_classify_2d_annotation)
- [ ] Bounding boxes extracted correctly
- [ ] `anno_key` array populated (not all -1)
- [ ] `anno_meta` list has correct type classifications
- [ ] `anno_over_model` derived correctly after finalization
- [ ] CSV export shows non-zero AnnoCells_* counts

**Dependencies**: Phase 5 (working pipeline), Phase 7 (CSV to verify metrics)

**Estimated Time**: 4-6 hours

---

## Phase 8b: Revit Link Support (RVT Links)

**Goal**: Process elements from linked Revit models

**Status**: ✅ Complete (implemented in `revit/linked_documents.py`)

### Why This Phase?

Many projects use Revit links for:
- Architectural model in structural view
- MEP overlays
- Multi-building coordination

Currently VOP Interwoven ignores linked models.

### Implementation Tasks

**File**: `vop_interwoven/revit/links.py` (NEW)

1. **Link Discovery**
   ```python
   def get_revit_link_instances(doc, view):
       """Find all RevitLinkInstance elements visible in view."""
   ```

2. **Link Transform**
   ```python
   def get_link_transform(link_instance):
       """Get transformation from link space to host space."""
   ```

3. **Link Element Collection**
   ```python
   def collect_link_elements(view, link_instance, config):
       """Collect 3D elements from linked model.

       Strategy:
           1. Get host view clip volume
           2. Transform to link space
           3. Collect elements in link doc by AABB
           4. Filter by host VG category visibility
           5. Create LinkElementProxy wrappers
       """
   ```

4. **Element Proxy**
   ```python
   class LinkElementProxy:
       """Wraps linked element with transform."""
       def __init__(self, element, link_inst, transform):
           self.element = element
           self.link_inst = link_inst
           self.transform = transform

       def get_BoundingBox(self, view):
           """Get bbox transformed to host space."""
   ```

**File**: `vop_interwoven/revit/collection.py` (UPDATE)

Add to `collect_view_elements()`:
```python
# Collect linked model elements
from .links import get_revit_link_instances, collect_link_elements

link_instances = get_revit_link_instances(doc, view)
for link_inst in link_instances:
    link_elements = collect_link_elements(view, link_inst, cfg)
    elements.extend(link_elements)
```

### Testing

**Test Script** (`tests/dynamo/test_phase8b_links.py`):
```python
from vop_interwoven.revit.links import get_revit_link_instances
from vop_interwoven.entry_dynamo import get_current_document, get_current_view

doc = get_current_document()
view = get_current_view()

links = get_revit_link_instances(doc, view)

print(f"Found {len(links)} Revit link instances")
for link in links:
    link_doc = link.GetLinkDocument()
    link_name = link_doc.Title if link_doc else "<unloaded>"
    print(f"  Link: {link_name}")

OUT = f"✅ Phase 8b: {len(links)} links found"
```

### Success Criteria

- [ ] RevitLinkInstance elements discovered
- [ ] Link transforms applied correctly
- [ ] Elements collected from linked docs
- [ ] Linked elements rendered to raster
- [ ] CSV shows Ext_Cells_RVT counts
- [ ] Source="RVT_LINK" in element_meta

**Dependencies**: Phase 5 (working pipeline), Phase 7 (CSV metrics)

**Estimated Time**: 6-8 hours

---

## Phase 8c: DWG Import Support

**Goal**: Process DWG underlay geometry

**Status**: ⏸️ Not Started

### Why This Phase?

DWG files are commonly used for:
- Site plans
- Survey data
- Consultant backgrounds

Currently ignored by VOP Interwoven.

### Implementation Tasks

**File**: `vop_interwoven/revit/dwg_import.py` (NEW)

1. **Import Discovery**
   ```python
   def get_dwg_import_instances(doc, view):
       """Find all ImportInstance elements (DWG) visible in view."""
   ```

2. **Geometry Extraction**
   ```python
   def extract_dwg_loops(import_inst, view):
       """Extract 2D line loops from DWG import.

       DWG imports expose GeometryObject with:
           - GeometryInstance (flattened)
           - Curve loops (polylines, arcs, circles)
       """
   ```

3. **Bounding Box Approximation**
   ```python
   def get_dwg_bbox_loops(import_inst, view):
       """Get bounding rectangles for DWG geometry bands.

       Strategy (from SSM):
           - Extract geometry loops
           - Compute bounding box per loop
           - Classify as TINY/LINEAR/AREAL
           - Return as synthetic regions
       """
   ```

**File**: `vop_interwoven/revit/collection.py` (UPDATE)

Add to `collect_view_elements()`:
```python
# Collect DWG import geometry
from .dwg_import import get_dwg_import_instances, get_dwg_bbox_loops

dwg_instances = get_dwg_import_instances(doc, view)
for dwg_inst in dwg_instances:
    dwg_regions = get_dwg_bbox_loops(dwg_inst, view)
    # Create synthetic elements for DWG regions
    elements.extend(dwg_regions)
```

### Success Criteria

- [ ] ImportInstance elements discovered
- [ ] DWG geometry extracted
- [ ] Bounding boxes computed
- [ ] DWG regions rasterized
- [ ] CSV shows Ext_Cells_DWG counts
- [ ] Source="DWG_IMPORT" in element_meta

**Dependencies**: Phase 5 (working pipeline), Phase 7 (CSV metrics)

**Estimated Time**: 4-6 hours

---

## Phase 8d: View-Level Caching

**Goal**: Add frame-based caching to skip unchanged views

**Status**: ⏸️ Not Started

### Why This Phase?

In large models, re-processing all views on every run is slow. SSM exporter has view-level caching that:
- Computes ViewFrameHash (hash of view settings)
- Checks if cached result exists
- Skips processing if cache valid
- Speeds up iterative runs by 10-100x

### Implementation Tasks

**File**: `vop_interwoven/cache.py` (NEW)

1. **Cache Directory Management**
   ```python
   def get_cache_dir(config):
       """Get cache directory path (e.g., ~/Documents/_vop_cache)."""

   def ensure_cache_dir(config):
       """Create cache directory if it doesn't exist."""
   ```

2. **ViewFrameHash Computation**
   ```python
   def compute_view_frame_hash(view):
       """Compute stable hash of view frame properties.

       Includes:
           - ViewType
           - Scale
           - Discipline
           - Crop box bounds
           - Detail level
           - View template ID

       Returns:
           8-character hex hash
       """
   ```

3. **Cache Key**
   ```python
   def get_cache_key(view, config):
       """Get cache key for view.

       Format: <ViewId>_<ViewFrameHash>_<ConfigHash>.json
       """
   ```

4. **Cache Read/Write**
   ```python
   def read_cached_result(view, config):
       """Read cached result if exists and valid."""

   def write_cached_result(view, result, config):
       """Write result to cache."""
   ```

**File**: `vop_interwoven/pipeline.py` (UPDATE)

Add caching to `run_vop_pipeline()`:
```python
for view_id in view_ids:
    view = doc.GetElement(view_id)

    # Check cache
    cached = read_cached_result(view, cfg) if cfg.enable_cache else None
    if cached:
        results.append(cached)
        continue

    # Process view...
    result = export_view_raster(view, raster, cfg)

    # Write to cache
    if cfg.enable_cache:
        write_cached_result(view, result, cfg)

    results.append(result)
```

### Testing

**Test Script** (`tests/dynamo/test_phase8d_cache.py`):
```python
from vop_interwoven.config import Config
from vop_interwoven.dynamo_helpers import run_pipeline_from_dynamo_input
import time

cfg = Config(enable_cache=True)

# First run (cold cache)
start = time.time()
result1 = run_pipeline_from_dynamo_input(export_csv=False, config=cfg)
elapsed1 = time.time() - start

# Second run (warm cache)
start = time.time()
result2 = run_pipeline_from_dynamo_input(export_csv=False, config=cfg)
elapsed2 = time.time() - start

print(f"First run: {elapsed1:.2f}s")
print(f"Second run: {elapsed2:.2f}s (cached)")
print(f"Speedup: {elapsed1/elapsed2:.1f}x")

OUT = f"✅ Phase 8d: Caching working ({elapsed1/elapsed2:.1f}x speedup)"
```

### Success Criteria

- [ ] Cache directory created
- [ ] ViewFrameHash computed correctly
- [ ] Cache files written after processing
- [ ] Cache files read on subsequent runs
- [ ] FromCache=True in CSV for cached views
- [ ] Cached results identical to fresh results
- [ ] 10x+ speedup on cache hits

**Dependencies**: Phase 7 (CSV export for FromCache field)

**Estimated Time**: 3-5 hours

---

## Phase 8e: Adaptive Thresholds

**Goal**: Auto-compute TINY/LINEAR thresholds per view

**Status**: ⏸️ Not Started

### Why This Phase?

Fixed thresholds (2x2, thin_max=2) don't work well across different view scales:
- 1/16" detail: 2x2 cells too large, captures too much as TINY
- 1/4" plan: 2x2 cells too small, misses small elements

SSM exporter computes adaptive thresholds based on percentile of element sizes in each view.

### Implementation Tasks

**File**: `vop_interwoven/adaptive.py` (NEW)

1. **Element Size Collection**
   ```python
   def collect_element_sizes(elements, view, raster):
       """Get projected sizes of all elements.

       Returns:
           List of (width_cells, height_cells) tuples
       """
   ```

2. **Percentile Thresholds**
   ```python
   def compute_adaptive_thresholds(element_sizes, config):
       """Compute TINY and LINEAR thresholds from size distribution.

       Strategy (from SSM):
           - Sort sizes by area (width * height)
           - tiny_threshold = percentile(areas, 10)  # Bottom 10%
           - linear_thin = percentile(min_dims, 20)  # Bottom 20% of min(w,h)

       Returns:
           Updated Config with adaptive thresholds
       """
   ```

**File**: `vop_interwoven/pipeline.py` (UPDATE)

Add adaptive threshold computation:
```python
def render_model_front_to_back(doc, view, raster, elements, cfg):
    # Compute adaptive thresholds if enabled
    if cfg.adaptive_thresholds:
        sizes = collect_element_sizes(elements, view, raster)
        cfg = compute_adaptive_thresholds(sizes, cfg)

    # Continue with rendering...
```

### Success Criteria

- [ ] Element sizes collected correctly
- [ ] Percentile computation working
- [ ] Thresholds adapt to view scale
- [ ] Classification ratios improve across scales
- [ ] Config option `adaptive_thresholds=True/False`

**Dependencies**: Phase 5 (working pipeline)

**Estimated Time**: 2-4 hours

---

## Phase 8f: Silhouette Extraction Strategies

**Goal**: Replace bbox projection with proper silhouette extraction

**Status**: ⏸️ Not Started (advanced geometry handling)

### Why This Phase?

Currently using bounding boxes for all elements. This:
- Over-estimates occupancy for rotated/angled elements
- Doesn't capture actual visible geometry
- Produces "boxy" rasterization

SSM exporter has multi-strategy silhouette extraction:
1. Category API shortcuts (Floor.GetExtendedBottomFace)
2. Geometry hull extraction (mesh/solid faces)
3. Oriented bounding box (OBB) fitting
4. Coarse tessellation fallback

### Implementation Tasks

**File**: `vop_interwoven/geometry/silhouette.py` (NEW)

1. **Silhouette Extractor Class**
   ```python
   class SilhouetteExtractor:
       """Multi-strategy 3D→2D silhouette extraction."""

       def __init__(self, view, config):
           self.view = view
           self.config = config

       def extract_silhouette(self, elem):
           """Extract 2D silhouette loops for element.

           Tries strategies in order:
               1. Category shortcuts
               2. Geometry hull
               3. OBB fitting
               4. Coarse tessellation
           """
   ```

2. **Strategy: Category Shortcuts**
   ```python
   def _category_api_shortcuts(self, elem):
       """Use category-specific Revit API shortcuts.

       Examples:
           - Floor: GetExtendedBottomFace()
           - Wall: GetDefinitionPolygon()
           - Ceiling: GetExtendedTopFace()
       """
   ```

3. **Strategy: Geometry Hull**
   ```python
   def _geometry_hull_extraction(self, elem):
       """Extract outline from element geometry.

       1. Get elem.get_Geometry()
       2. Flatten to meshes/solids
       3. Extract faces
       4. Project to view plane
       5. Compute convex hull or boundary loop
       """
   ```

4. **Strategy: OBB Fitting**
   ```python
   def _oriented_bbox_fitting(self, elem):
       """Fit oriented bounding box to element geometry.

       Better than AABB for rotated elements.
       """
   ```

**File**: `vop_interwoven/pipeline.py` (UPDATE)

Replace bbox projection with silhouette extraction:
```python
from .geometry.silhouette import SilhouetteExtractor

def render_model_front_to_back(doc, view, raster, elements, cfg):
    extractor = SilhouetteExtractor(view, cfg)

    for elem in elements:
        # Extract silhouette loops
        loops = extractor.extract_silhouette(elem)

        # Convert loops to regions
        regions = loops_to_regions(loops, raster)

        # Classify and rasterize
        for region in regions:
            mode = classify_region(region, cfg)
            rasterize_region(region, mode, raster)
```

### Success Criteria

- [ ] Category shortcuts working for Floors, Walls, Ceilings
- [ ] Geometry hull extraction working
- [ ] OBB fitting reduces over-estimation
- [ ] Strategy fallback chain robust
- [ ] Rasterization matches actual geometry better
- [ ] Visual comparison with SSM exporter shows parity

**Dependencies**: Phase 4 (triangle rasterization)

**Estimated Time**: 8-12 hours (complex)

---

## Updated Dependency Graph

```
Phase 0 (Foundation) ✅
  ↓
Phase 1 (View Basis) ✅
  ↓
Phase 2 (Element Collection) ✅
  ↓
Phase 3 (Classification) ✅
  ↓
Phase 4 (Geometry Tessellation) ⏸️ Deferred
  ↓
Phase 5 (Simplified Pipeline) ✅
  ├─→ Phase 6 (Edge Rasterization) ⏸️ Deferred
  ├─→ Phase 7 (CSV Export) 📋 Planned
  ├─→ Phase 8a (Annotations) ⏸️
  ├─→ Phase 8b (RVT Links) ⏸️
  ├─→ Phase 8c (DWG Imports) ⏸️
  ├─→ Phase 8d (Caching) ⏸️
  ├─→ Phase 8e (Adaptive Thresholds) ⏸️
  └─→ Phase 8f (Silhouette Extraction) ⏸️
```

---

## Implementation Priority

### Tier 1: Essential (Complete First)
1. **Phase 7**: CSV Export - Enables analytics and comparison with SSM
2. **Phase 8a**: Annotations - Completes occupancy model (model + anno)
3. **Phase 8d**: Caching - Huge performance boost for iterative workflows

### Tier 2: Important (Add Next)
4. **Phase 8b**: RVT Links - Common in multi-discipline projects
5. **Phase 8e**: Adaptive Thresholds - Improves classification across scales

### Tier 3: Advanced (Future Work)
6. **Phase 4**: Triangle Rasterization - Accurate depth values
7. **Phase 6**: Edge Detection - Visual detail
8. **Phase 8c**: DWG Imports - Less common than RVT links
9. **Phase 8f**: Silhouette Extraction - Complex geometry handling

---

## Summary

### Current Status (Phases 0-3, 5 Complete)
✅ **Working end-to-end pipeline**:
- View coordinate extraction
- 3D element collection
- UV classification
- Bbox-based rasterization
- JSON + PNG export
- Dynamo integration

### Phase 7: Next Immediate Goal
📋 **CSV Export** - Match SSM format, enable analytics

### Phases 8a-8f: Future Enhancements
⏸️ **Missing SSM Features**:
- Annotations (8a)
- RVT Links (8b)
- DWG Imports (8c)
- Caching (8d)
- Adaptive Thresholds (8e)
- Silhouette Extraction (8f)

### Total Estimated Effort
- Phase 7: 5-7 hours
- Tier 1 (7, 8a, 8d): 12-18 hours
- Tier 2 (8b, 8e): 8-12 hours
- Tier 3 (4, 6, 8c, 8f): 18-28 hours
- **Grand Total**: ~38-58 hours for full SSM parity

Start with **Phase 7** for immediate analytics value!
