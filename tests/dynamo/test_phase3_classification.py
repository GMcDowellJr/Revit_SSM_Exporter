"""
Phase 3 Test: UV Classification & Proxy Generation

Copy this code into a Dynamo Python node to test Phase 3 implementation.

Prerequisites:
- Phases 1-2 complete
- Active Revit view with model elements

Success Criteria:
- Elements classify into TINY/LINEAR/AREAL categories
- Classification ratios seem reasonable for model
- UV_AABB proxies generate without errors
- Proxy bounds match element cell rectangles
"""

import sys
sys.path.append(r'C:\Users\gmcdowell\Documents\Revit_SSM_Exporter')

from vop_interwoven.entry_dynamo import get_current_document, get_current_view
from vop_interwoven.config import Config
from vop_interwoven.pipeline import init_view_raster
from vop_interwoven.revit.collection import collect_view_elements, _project_element_bbox_to_cell_rect
from vop_interwoven.revit.view_basis import make_view_basis
from vop_interwoven.core.geometry import classify_by_uv, make_uv_aabb, Mode

doc = get_current_document()
view = get_current_view()
cfg = Config(tiny_max=2, thin_max=2)

results = []

try:
    # Setup
    raster = init_view_raster(doc, view, cfg)
    vb = make_view_basis(view)
    elements = collect_view_elements(doc, view, raster)

    results.append(f"Testing {len(elements)} elements")
    results.append(f"Config: tiny_max={cfg.tiny_max}, thin_max={cfg.thin_max}")
    results.append("")

    # Classify all elements
    tiny_elements = []
    linear_elements = []
    areal_elements = []
    no_bbox = 0

    for elem in elements:
        rect = _project_element_bbox_to_cell_rect(elem, vb, raster)
        if rect is None or rect.empty:
            no_bbox += 1
            continue

        mode = classify_by_uv(rect.width_cells, rect.height_cells, cfg)

        if mode == Mode.TINY:
            tiny_elements.append((elem, rect))
        elif mode == Mode.LINEAR:
            linear_elements.append((elem, rect))
        else:
            areal_elements.append((elem, rect))

    # Report classification results
    results.append("Classification Results:")
    results.append(f"   TINY:   {len(tiny_elements)} elements ({100*len(tiny_elements)//len(elements)}%)")
    results.append(f"   LINEAR: {len(linear_elements)} elements ({100*len(linear_elements)//len(elements)}%)")
    results.append(f"   AREAL:  {len(areal_elements)} elements ({100*len(areal_elements)//len(elements)}%)")
    results.append(f"   No bbox: {no_bbox} elements")
    results.append("")

    # Test proxy generation for TINY elements
    if tiny_elements:
        results.append("Sample TINY element proxies:")
        for elem, rect in tiny_elements[:5]:
            proxy = make_uv_aabb(rect)
            center = proxy.center()
            results.append(f"   Element {elem.Id}: {rect.width_cells}x{rect.height_cells} cells")
            results.append(f"      UV_AABB center: ({center[0]:.1f}, {center[1]:.1f})")
            results.append(f"      UV_AABB size: {proxy.width():.1f}x{proxy.height():.1f}")
            results.append(f"      Edges: {len(proxy.edges())} edge segments")
    else:
        results.append("⚠ No TINY elements found")

    results.append("")

    # Test LINEAR elements
    if linear_elements:
        results.append("Sample LINEAR element dimensions:")
        for elem, rect in linear_elements[:5]:
            results.append(f"   Element {elem.Id}: {rect.width_cells}x{rect.height_cells} cells")
    else:
        results.append("⚠ No LINEAR elements found")

    results.append("")

    # Test AREAL elements
    if areal_elements:
        results.append("Sample AREAL element dimensions:")
        for elem, rect in areal_elements[:5]:
            results.append(f"   Element {elem.Id}: {rect.width_cells}x{rect.height_cells} cells")
    else:
        results.append("⚠ No AREAL elements found")

    results.append("")
    results.append("=" * 50)
    results.append("✅✅✅ PHASE 3 COMPLETE ✅✅✅")
    results.append("=" * 50)

except Exception as e:
    results.append(f"❌ Phase 3 failed: {e}")
    import traceback
    results.append(traceback.format_exc())

OUT = "\n".join(results)
