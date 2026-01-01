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
    cell_size_ft=1.0,
    adaptive_tile_size=True,
    tiny_max=2,
    thin_max=2,
    anno_proxies_in_overmodel=False
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

## Summary

This plan provides:
- ✅ **Clear progression**: 6 phases, each building on previous
- ✅ **Testable increments**: Dynamo test scripts for each phase
- ✅ **Success criteria**: Know when to move forward
- ✅ **Minimal dependencies**: Small Revit API surface per phase
- ✅ **Error recovery**: Iterative workflow for fixing issues

Start with **Phase 1** and work through systematically. Report errors at each phase for fixes before continuing!
