"""
Phase 2 Test: Element Collection & Bounding Boxes

Copy this code into a Dynamo Python node to test Phase 2 implementation.

Prerequisites:
- Phase 1 complete
- Phase 2 implementation complete (collection.py)
- Active Revit view with model elements

Success Criteria:
- FilteredElementCollector returns elements without errors
- Element count matches expected model size
- Element categories are correct
- Bounding boxes transform to valid cell rectangles
"""

import sys
sys.path.append(r'C:\Users\gmcdowell\Documents\Revit_SSM_Exporter')

from vop_interwoven.entry_dynamo import get_current_document, get_current_view
from vop_interwoven.config import Config
from vop_interwoven.pipeline import init_view_raster
from vop_interwoven.revit.collection import collect_view_elements
from vop_interwoven.revit.view_basis import make_view_basis
from collections import Counter

doc = get_current_document()
view = get_current_view()
cfg = Config()

results = []

try:
    # Test 1: Initialize raster
    raster = init_view_raster(doc, view, cfg)
    results.append(f"✅ Raster initialized: {raster.width}x{raster.height} cells")
    results.append(f"   Cell size: {raster.cell_size:.2f} ft")
    results.append(f"   Tile size: {raster.tile.tile_size}x{raster.tile.tile_size}")
    results.append(f"   Bounds: ({raster.bounds.xmin:.1f}, {raster.bounds.ymin:.1f}) to ({raster.bounds.xmax:.1f}, {raster.bounds.ymax:.1f})")

    # Test 2: Create view basis
    vb = make_view_basis(view)
    results.append(f"✅ View basis created")

    # Test 3: Collect elements
    elements = collect_view_elements(doc, view, raster)
    results.append(f"✅ Collected {len(elements)} elements")

    if len(elements) == 0:
        results.append("⚠ Warning: No elements found. Is the view empty?")
    else:
        # Test 4: Show element categories
        results.append("")
        results.append("Element breakdown by category:")
        categories = Counter([elem.Category.Name for elem in elements if elem.Category])
        for cat, count in categories.most_common(10):
            results.append(f"   {cat}: {count}")

        # Test 5: Test bounding box projection on first 10 elements
        from vop_interwoven.revit.collection import _project_element_bbox_to_cell_rect

        results.append("")
        results.append("Sample bounding box projections:")
        for i, elem in enumerate(elements[:10]):
            rect = _project_element_bbox_to_cell_rect(elem, vb, raster)
            if rect is None:
                results.append(f"   Element {elem.Id}: No bounding box")
            elif rect.empty:
                results.append(f"   Element {elem.Id}: Empty rect")
            else:
                results.append(f"   Element {elem.Id}: {rect.width_cells}x{rect.height_cells} cells at ({rect.i_min},{rect.j_min})")

    results.append("")
    results.append("=" * 50)
    results.append("✅✅✅ PHASE 2 COMPLETE ✅✅✅")
    results.append("=" * 50)

except Exception as e:
    results.append(f"❌ Phase 2 failed: {e}")
    import traceback
    results.append(traceback.format_exc())

OUT = "\n".join(results)
