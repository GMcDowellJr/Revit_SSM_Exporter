"""
View Type Check - See why view is being skipped

This will show the exact view type and whether it's supported.
Paste into Dynamo Python node.
"""

import sys
sys.path.append(r'C:\Users\gmcdowell\Documents\Revit_SSM_Exporter')

from vop_interwoven.entry_dynamo import get_current_document, get_current_view
from Autodesk.Revit.DB import ViewType

doc = get_current_document()
view = get_current_view()

results = []
results.append("=" * 70)
results.append("VIEW TYPE CHECK")
results.append("=" * 70)
results.append("")

results.append("View: {0}".format(view.Name))
results.append("View ID: {0}".format(view.Id.IntegerValue))
results.append("View Type: {0}".format(view.ViewType))
results.append("")

# Show all supported types
supported_types = [
    ViewType.FloorPlan,
    ViewType.CeilingPlan,
    ViewType.Elevation,
    ViewType.Section,
    ViewType.AreaPlan,
    ViewType.EngineeringPlan,
    ViewType.Detail,
    ViewType.DraftingView,
]

results.append("Supported view types:")
for vtype in supported_types:
    is_current = "  <-- CURRENT VIEW" if view.ViewType == vtype else ""
    results.append("  - {0}{1}".format(vtype, is_current))

results.append("")

# Check if current view is supported
if view.ViewType in supported_types:
    results.append("RESULT: View IS supported - should be processed")
else:
    results.append("RESULT: View is NOT supported - will be skipped!")
    results.append("")
    results.append("This view type is not in the supported list.")
    results.append("You need to use a Floor Plan, Section, or other model view.")

results.append("")
results.append("=" * 70)

OUT = "\n".join(results)
