"""
Zero Occupancy Debug Script

Comprehensive diagnostic for linked RVT/DWG zero occupancy issue.

Paste this into Dynamo Python node to diagnose:
1. Are linked elements being collected?
2. Are they being passed to the pipeline?
3. Are their bboxes valid and in view bounds?
4. Are they being projected to cells correctly?
"""

import sys
sys.path.append(r'C:\Users\gmcdowell\Documents\Revit_SSM_Exporter')

from vop_interwoven.entry_dynamo import get_current_document, get_current_view
from vop_interwoven.config import Config
from vop_interwoven.pipeline import init_view_raster
from vop_interwoven.revit.collection import (
    collect_view_elements,
    expand_host_link_import_model_elements,
    _project_element_bbox_to_cell_rect
)
from vop_interwoven.revit.view_basis import make_view_basis

doc = get_current_document()
view = get_current_view()

cfg = Config(
    include_linked_rvt=True,
    include_dwg_imports=True
)

results = []
results.append("=" * 70)
results.append("ZERO OCCUPANCY DEBUG - Linked Documents")
results.append("=" * 70)
results.append("")

# Step 1: Check view and config
results.append("STEP 1: Configuration")
results.append("-" * 70)
results.append("View: {0} (ID: {1})".format(view.Name, view.Id.IntegerValue))
results.append("View Type: {0}".format(view.ViewType))
results.append("Config flags:")
results.append("  include_linked_rvt: {0}".format(cfg.include_linked_rvt))
results.append("  include_dwg_imports: {0}".format(cfg.include_dwg_imports))
results.append("")

# Step 2: Initialize raster and view basis
results.append("STEP 2: Initialize Raster and View Basis")
results.append("-" * 70)
try:
    raster = init_view_raster(doc, view, cfg)
    vb = make_view_basis(view)

    results.append("Raster initialized:")
    results.append("  Grid: {0}x{1} cells".format(raster.W, raster.H))
    results.append("  Cell size: {0:.4f} ft".format(raster.cell_size_ft))
    results.append("  Bounds: ({0:.2f}, {1:.2f}) to ({2:.2f}, {3:.2f})".format(
        raster.bounds.xmin, raster.bounds.ymin,
        raster.bounds.xmax, raster.bounds.ymax
    ))

    results.append("")
    results.append("View basis:")
    results.append("  Origin: ({0:.2f}, {1:.2f}, {2:.2f})".format(
        vb.origin[0], vb.origin[1], vb.origin[2]
    ))
except Exception as e:
    results.append("ERROR initializing raster/basis: {0}".format(e))
    OUT = "\n".join(results)
    raise

results.append("")

# Step 3: Collect host elements
results.append("STEP 3: Collect Host Elements")
results.append("-" * 70)
try:
    host_elements = collect_view_elements(doc, view, raster)
    results.append("Host elements collected: {0}".format(len(host_elements)))
except Exception as e:
    results.append("ERROR collecting host elements: {0}".format(e))
    host_elements = []

results.append("")

# Step 4: Expand to include linked elements
results.append("STEP 4: Expand to Include Linked/Imported Elements")
results.append("-" * 70)
try:
    expanded_elements = expand_host_link_import_model_elements(doc, view, host_elements, cfg)
    results.append("Total elements after expansion: {0}".format(len(expanded_elements)))

    # Count by source
    by_source = {}
    for elem_wrapper in expanded_elements:
        source = elem_wrapper["doc_key"]
        by_source[source] = by_source.get(source, 0) + 1

    results.append("")
    results.append("Breakdown by source:")
    for source, count in sorted(by_source.items()):
        results.append("  {0}: {1} element(s)".format(source, count))

    # Check for linked elements
    linked_count = len(expanded_elements) - len(host_elements)
    if linked_count == 0:
        results.append("")
        results.append("WARNING: No linked elements found!")
        results.append("Possible reasons:")
        results.append("  - No RVT links or DWG imports in this view")
        results.append("  - Links are unloaded")
        results.append("  - Elements filtered out by category/visibility")
        results.append("  - Elements outside view clip volume")
    else:
        results.append("")
        results.append("Found {0} linked/imported element(s)".format(linked_count))

except Exception as e:
    results.append("ERROR expanding elements: {0}".format(e))
    import traceback
    results.append(traceback.format_exc())
    expanded_elements = []

results.append("")

# Step 5: Test bbox projection for a few elements
results.append("STEP 5: Test BBox Projection (Sample)")
results.append("-" * 70)

sample_count = min(5, len(expanded_elements))
projected_count = 0

for i, elem_wrapper in enumerate(expanded_elements[:sample_count]):
    elem = elem_wrapper["element"]
    source = elem_wrapper["doc_key"]

    try:
        elem_id = elem.Id.IntegerValue
        category = elem.Category.Name if elem.Category else "Unknown"

        # Get bbox
        bbox = elem.get_BoundingBox(None)
        if bbox is None:
            results.append("Element {0} ({1}/{2}): NO BBOX".format(
                elem_id, category, source
            ))
            continue

        # Project to cell rect
        rect = _project_element_bbox_to_cell_rect(elem, vb, raster)

        if rect is None or rect.empty:
            results.append("Element {0} ({1}/{2}): bbox OK but rect EMPTY/NONE".format(
                elem_id, category, source
            ))
            results.append("  BBox: ({0:.2f}, {1:.2f}, {2:.2f}) to ({3:.2f}, {4:.2f}, {5:.2f})".format(
                bbox.Min.X, bbox.Min.Y, bbox.Min.Z,
                bbox.Max.X, bbox.Max.Y, bbox.Max.Z
            ))
        else:
            results.append("Element {0} ({1}/{2}): OK - {3}x{4} cells".format(
                elem_id, category, source,
                rect.width_cells, rect.height_cells
            ))
            projected_count += 1

    except Exception as e:
        results.append("Element {0}: ERROR - {1}".format(i, e))

results.append("")
results.append("Projection summary: {0}/{1} elements projected to non-empty rects".format(
    projected_count, sample_count
))

if projected_count == 0 and sample_count > 0:
    results.append("")
    results.append("WARNING: No elements projected to grid cells!")
    results.append("Possible reasons:")
    results.append("  - Elements outside view bounds")
    results.append("  - View basis transformation issue")
    results.append("  - Incorrect coordinate space for linked elements")

results.append("")
results.append("=" * 70)
results.append("END DIAGNOSTIC")
results.append("=" * 70)

OUT = "\n".join(results)
