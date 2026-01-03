"""
Diagnostic script to test silhouette strategy selection and occlusion vs occupancy.

USAGE: Copy this entire script into a Dynamo Python Script node and run.
"""

# Minimal imports - no sys.path manipulation needed when run from Dynamo
import clr
clr.AddReference('RevitAPI')
from Autodesk.Revit.DB import FilteredElementCollector, ViewType

# Get document and view from Dynamo/Revit context
doc = IN[0] if IN and len(IN) > 0 else __revit__.ActiveUIDocument.Document
active_view = __revit__.ActiveUIDocument.ActiveView

print("=" * 80)
print("OCCLUSION VS OCCUPANCY LAYER CHECK")
print("=" * 80)
print("")
print("QUESTION: Which layer is being exported to PNG/CSV?")
print("")
print("TWO LAYERS:")
print("  1. model_mask (occlusion)  - Interior + boundary (should be filled blob)")
print("  2. model_edge_key (occupancy) - Boundary only (should be outline)")
print("")
print("PNG/CSV SHOULD export: model_edge_key (boundary only)")
print("")
print("=" * 80)
print("")

OUT = "Run the full pipeline to check which layer is exported"
