"""
Test Linked Documents Collection - Debug Script

Paste this into a Dynamo Python node to debug linked document collection.

Expected behavior:
- Should find RVT links and DWG imports in the view
- Should report how many elements were collected from each
- Should show any errors in the collection process
"""

import sys
sys.path.append(r'C:\Users\gmcdowell\Documents\Revit_SSM_Exporter')

from vop_interwoven.entry_dynamo import get_current_document, get_current_view
from vop_interwoven.config import Config
from vop_interwoven.revit.linked_documents import collect_all_linked_elements

doc = get_current_document()
view = get_current_view()

# Create config with linked docs enabled (default)
cfg = Config(
    include_linked_rvt=True,
    include_dwg_imports=True
)

results = []
results.append("=" * 60)
results.append("LINKED DOCUMENTS COLLECTION DEBUG")
results.append("=" * 60)
results.append("")

# Check config
results.append("Config:")
results.append("  include_linked_rvt: {0}".format(cfg.include_linked_rvt))
results.append("  include_dwg_imports: {0}".format(cfg.include_dwg_imports))
results.append("")

# Try to collect linked elements
try:
    results.append("Collecting linked elements...")
    linked_elements = collect_all_linked_elements(doc, view, cfg)

    results.append("SUCCESS: Collected {0} linked element(s)".format(len(linked_elements)))
    results.append("")

    # Group by doc_key
    by_source = {}
    for proxy in linked_elements:
        doc_key = proxy.doc_key
        if doc_key not in by_source:
            by_source[doc_key] = []
        by_source[doc_key].append(proxy)

    results.append("Breakdown by source:")
    for doc_key, proxies in by_source.items():
        results.append("  {0}: {1} element(s)".format(doc_key, len(proxies)))

        # Show first few element IDs
        if len(proxies) > 0:
            sample_ids = [str(p.Id.IntegerValue) for p in proxies[:3]]
            results.append("    Sample IDs: {0}".format(", ".join(sample_ids)))

    results.append("")

    # Check bounding boxes
    if linked_elements:
        first_elem = linked_elements[0]
        bbox = first_elem.get_BoundingBox(None)
        if bbox:
            results.append("First element bbox (host space):")
            results.append("  Min: ({0:.2f}, {1:.2f}, {2:.2f})".format(
                bbox.Min.X, bbox.Min.Y, bbox.Min.Z
            ))
            results.append("  Max: ({0:.2f}, {1:.2f}, {2:.2f})".format(
                bbox.Max.X, bbox.Max.Y, bbox.Max.Z
            ))
        else:
            results.append("WARNING: First element has no bbox!")

except Exception as e:
    results.append("ERROR: {0}".format(str(e)))

    # Try to get more details
    import traceback
    results.append("")
    results.append("Traceback:")
    results.append(traceback.format_exc())

results.append("")
results.append("=" * 60)

OUT = "\n".join(results)
