"""
Diagnostic script to test silhouette strategy selection and occlusion vs occupancy.

Run this in Dynamo to see:
1. Which elements use which strategy (bbox, obb, silhouette_edges)
2. Strategy success rates
3. Visual comparison of occlusion (model_mask) vs occupancy (model_edge_key)
"""

import sys
import os

# Add parent directories to path
script_dir = os.path.dirname(__file__)
vop_root = os.path.abspath(os.path.join(script_dir, "..", ".."))
repo_root = os.path.abspath(os.path.join(vop_root, ".."))
sys.path.insert(0, vop_root)
sys.path.insert(0, repo_root)

from vop_interwoven.pipeline import process_document_views
from vop_interwoven.config import Config

# Revit API imports
import clr
clr.AddReference('RevitAPI')
from Autodesk.Revit.DB import FilteredElementCollector, ViewType

# Get current document and active view
doc = __revit__.ActiveUIDocument.Document
active_view = __revit__.ActiveUIDocument.ActiveView

print("=" * 80)
print("SILHOUETTE STRATEGY DIAGNOSTICS")
print("=" * 80)
print("View: {0}".format(active_view.Name))
print("")

# Create config
cfg = Config()

# Process the active view
results = process_document_views(doc, [active_view.Id], cfg)

if not results:
    print("ERROR: No results returned from pipeline")
    OUT = None
else:
    result = results[0]
    raster_dict = result.get('raster', {})

    print("\n" + "=" * 80)
    print("RASTER STATISTICS")
    print("=" * 80)
    print("Grid size: {0}x{1}".format(result['width'], result['height']))
    print("Total elements: {0}".format(result['total_elements']))
    print("Filled cells (occlusion): {0}".format(result['filled_cells']))
    print("")

    # Analyze element metadata to count strategies
    element_meta = raster_dict.get('element_meta', [])

    print("\n" + "=" * 80)
    print("STRATEGY BREAKDOWN")
    print("=" * 80)

    # Count strategies
    strategy_counts = {}
    category_strategy = {}

    for elem_data in element_meta:
        strategy = elem_data.get('strategy', 'unknown')
        category = elem_data.get('category', 'Unknown')

        # Count overall strategies
        strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1

        # Count strategies per category
        if category not in category_strategy:
            category_strategy[category] = {}
        category_strategy[category][strategy] = category_strategy[category].get(strategy, 0) + 1

    print("Overall strategy usage:")
    for strategy, count in sorted(strategy_counts.items(), key=lambda x: x[1], reverse=True):
        pct = 100.0 * count / len(element_meta) if element_meta else 0
        print("  {0}: {1} elements ({2:.1f}%)".format(strategy, count, pct))

    print("\nStrategy usage by category:")
    for category in sorted(category_strategy.keys()):
        strategies = category_strategy[category]
        total = sum(strategies.values())
        print("  {0} ({1} elements):".format(category, total))
        for strategy, count in sorted(strategies.items(), key=lambda x: x[1], reverse=True):
            pct = 100.0 * count / total
            print("    {0}: {1} ({2:.1f}%)".format(strategy, count, pct))

    print("\n" + "=" * 80)
    print("ELEMENT DETAILS")
    print("=" * 80)
    print("Total elements in raster: {0}".format(len(element_meta)))

    # Show first 20 elements with strategy
    print("\nFirst 20 elements:")
    for i, elem_data in enumerate(element_meta[:20]):
        elem_id = elem_data.get('elem_id', '?')
        category = elem_data.get('category', '?')
        source = elem_data.get('source', 'HOST')
        strategy = elem_data.get('strategy', 'unknown')
        print("  [{0}] ID={1}, Category={2}, Strategy={3}, Source={4}".format(
            i, elem_id, category, strategy, source))

    if len(element_meta) > 20:
        print("  ... and {0} more".format(len(element_meta) - 20))

    # Analyze occlusion vs occupancy
    print("\n" + "=" * 80)
    print("OCCLUSION VS OCCUPANCY ANALYSIS")
    print("=" * 80)

    model_mask = raster_dict.get('model_mask', [])
    model_edge_key = raster_dict.get('model_edge_key', [])

    if model_mask and model_edge_key:
        total_cells = len(model_mask)
        occluded_cells = sum(1 for m in model_mask if m)
        occupied_cells = sum(1 for k in model_edge_key if k != -1)

        print("Total grid cells: {0}".format(total_cells))
        print("Occluded cells (model_mask=True): {0} ({1:.1f}%)".format(
            occluded_cells, 100.0 * occluded_cells / total_cells))
        print("Occupied cells (model_edge_key != -1): {0} ({1:.1f}%)".format(
            occupied_cells, 100.0 * occupied_cells / total_cells))
        print("")

        # Check if there's inappropriate overlap
        interior_marked_as_occupied = 0
        for i in range(total_cells):
            # If cell is occluded but NOT on boundary, it shouldn't be occupied
            # This is a simplified check - just count cells that are both
            if model_mask[i] and model_edge_key[i] != -1:
                interior_marked_as_occupied += 1

        print("Cells marked as both occluded AND occupied: {0}".format(interior_marked_as_occupied))
        print("  (This should be small - only boundaries should be both)")
        print("")

        # Calculate boundary-to-interior ratio
        if occluded_cells > 0:
            boundary_ratio = float(occupied_cells) / float(occluded_cells)
            print("Boundary-to-occlusion ratio: {0:.3f}".format(boundary_ratio))
            print("  (Lower is better - means less interior marked as occupied)")
            print("  (Expected: ~0.1-0.3 for large elements with small perimeter-to-area)")

    else:
        print("ERROR: model_mask or model_edge_key not found in raster")

    # Export visualization data
    print("\n" + "=" * 80)
    print("EXPORT INSTRUCTIONS")
    print("=" * 80)
    print("To visualize occlusion vs occupancy:")
    print("1. Export 'model_mask' - shows interior + boundary (occlusion)")
    print("2. Export 'model_edge_key' - shows boundary only (occupancy)")
    print("3. Compare the two - interior should NOT appear in model_edge_key")
    print("")

    OUT = {
        'success': True,
        'view_name': active_view.Name,
        'grid_size': (result['width'], result['height']),
        'total_elements': len(element_meta),
        'occluded_cells': occluded_cells if model_mask else 0,
        'occupied_cells': occupied_cells if model_edge_key else 0,
        'raster': raster_dict
    }

    print("\nDiagnostics complete. Check output for raster data.")
