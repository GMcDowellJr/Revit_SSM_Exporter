"""
Diagnostic script to test silhouette strategy selection and occlusion vs occupancy.

USAGE: Copy this entire script into a Dynamo Python Script node and run.
"""

# Minimal imports for Dynamo Python 3 compatibility
import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitServices')
from Autodesk.Revit.DB import FilteredElementCollector, ViewType
from RevitServices.Persistence import DocumentManager

# Get document and view from Dynamo context (Python 3 compatible)
doc = DocumentManager.Instance.CurrentDBDocument
uiapp = DocumentManager.Instance.CurrentUIApplication
active_view = doc.ActiveView

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
