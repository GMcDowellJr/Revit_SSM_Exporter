"""
All Phases Test: Quick validation of all implemented phases

Copy this code into a Dynamo Python node to test all phases at once.
Use this for quick validation that nothing broke during development.

This will run tests for all phases and report which are working.
"""

import sys
sys.path.append(r'C:\path\to\Revit_SSM_Exporter')  # UPDATE THIS PATH

from vop_interwoven.entry_dynamo import get_current_document, get_current_view
from vop_interwoven.config import Config

doc = get_current_document()
view = get_current_view()
cfg = Config()

results = []
results.append("=" * 60)
results.append("VOP INTERWOVEN PIPELINE - PHASE VALIDATION")
results.append("=" * 60)
results.append("")

# Phase 0: Core Logic (already tested in unit tests)
results.append("Phase 0: Core Logic")
try:
    from vop_interwoven.core.geometry import classify_by_uv, make_uv_aabb, Mode
    from vop_interwoven.core.raster import ViewRaster, TileMap
    from vop_interwoven.core.math_utils import Bounds2D, CellRect
    results.append("   ✅ Core modules import successfully")
except Exception as e:
    results.append(f"   ❌ Core modules failed: {e}")

results.append("")

# Phase 1: View Basis
results.append("Phase 1: View Basis & Coordinate System")
try:
    from vop_interwoven.revit.view_basis import make_view_basis, world_to_view
    vb = make_view_basis(view)
    test_pt = world_to_view((0, 0, 0), vb)
    results.append(f"   ✅ View basis working (origin at {vb.origin[0]:.1f}, {vb.origin[1]:.1f}, {vb.origin[2]:.1f})")
except Exception as e:
    results.append(f"   ❌ View basis failed: {e}")

results.append("")

# Phase 2: Element Collection
results.append("Phase 2: Element Collection & Bounding Boxes")
try:
    from vop_interwoven.pipeline import init_view_raster
    from vop_interwoven.revit.collection import collect_view_elements

    raster = init_view_raster(doc, view, cfg)
    elements = collect_view_elements(doc, view, raster)
    results.append(f"   ✅ Element collection working ({len(elements)} elements, {raster.width}x{raster.height} grid)")
except Exception as e:
    results.append(f"   ❌ Element collection failed: {e}")

results.append("")

# Phase 3: Classification
results.append("Phase 3: UV Classification & Proxy Generation")
try:
    from vop_interwoven.revit.collection import _project_element_bbox_to_cell_rect

    if elements:
        elem = elements[0]
        rect = _project_element_bbox_to_cell_rect(elem, vb, raster)
        if rect and not rect.empty:
            mode = classify_by_uv(rect.width_cells, rect.height_cells, cfg)
            proxy = make_uv_aabb(rect) if mode == Mode.TINY else None
            results.append(f"   ✅ Classification working (first element: {mode.name})")
        else:
            results.append(f"   ⚠ Classification working but first element has no bbox")
    else:
        results.append(f"   ⚠ Classification code OK but no elements to test")
except Exception as e:
    results.append(f"   ❌ Classification failed: {e}")

results.append("")

# Phase 4: Geometry Tessellation (if implemented)
results.append("Phase 4: Geometry Tessellation & Rasterization")
try:
    from vop_interwoven.revit.geometry import extract_triangles
    if elements:
        triangles = extract_triangles(elements[0], vb)
        results.append(f"   ✅ Geometry extraction working ({len(triangles)} triangles from first element)")
    else:
        results.append(f"   ⚠ Geometry code OK but no elements to test")
except ImportError:
    results.append(f"   ⏸ Not yet implemented (expected)")
except Exception as e:
    results.append(f"   ❌ Geometry extraction failed: {e}")

results.append("")

# Phase 5: Full Pipeline (if implemented)
results.append("Phase 5: Full Interwoven Pipeline")
try:
    from vop_interwoven.entry_dynamo import run_vop_pipeline
    result = run_vop_pipeline(doc, [view.Id], cfg)
    view_result = result["views"][0]
    results.append(f"   ✅ Full pipeline working!")
    results.append(f"      Grid: {view_result['width']}x{view_result['height']}")
    results.append(f"      Elements: {view_result.get('total_elements', 'N/A')}")
    results.append(f"      Filled cells: {view_result.get('filled_cells', 'N/A')}")
except NotImplementedError:
    results.append(f"   ⏸ Not yet fully implemented (expected)")
except Exception as e:
    results.append(f"   ❌ Pipeline failed: {e}")

results.append("")
results.append("=" * 60)
results.append("VALIDATION COMPLETE")
results.append("=" * 60)
results.append("")
results.append("Legend:")
results.append("   ✅ = Working correctly")
results.append("   ❌ = Error (needs fixing)")
results.append("   ⚠  = Warning (may be OK)")
results.append("   ⏸  = Not yet implemented")

OUT = "\n".join(results)
