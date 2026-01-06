"""
Import Check - Verify all modules load correctly

This tests if the linked documents implementation can be imported
without errors. Run this FIRST if the pipeline isn't working.

Paste into Dynamo Python node.
"""

import sys
sys.path.append(r'C:\Users\gmcdowell\Documents\Revit_SSM_Exporter')

results = []
results.append("=" * 70)
results.append("IMPORT CHECK - Linked Documents")
results.append("=" * 70)
results.append("")

# Test 1: Basic imports
results.append("Test 1: Basic VOP imports")
try:
    from vop_interwoven.config import Config
    results.append("  OK: Config")
except Exception as e:
    results.append("  FAIL: Config - {0}".format(e))

try:
    from vop_interwoven.pipeline import process_document_views
    results.append("  OK: pipeline")
except Exception as e:
    results.append("  FAIL: pipeline - {0}".format(e))

try:
    from vop_interwoven.revit.collection import collect_view_elements
    results.append("  OK: revit.collection")
except Exception as e:
    results.append("  FAIL: revit.collection - {0}".format(e))

results.append("")

# Test 2: Linked documents imports
results.append("Test 2: Linked documents imports")
try:
    from vop_interwoven.revit.linked_documents import collect_all_linked_elements
    results.append("  OK: linked_documents module")
except Exception as e:
    results.append("  FAIL: linked_documents - {0}".format(e))
    import traceback
    results.append("")
    results.append("Traceback:")
    results.append(traceback.format_exc())

try:
    from vop_interwoven.revit.linked_documents import LinkedElementProxy
    results.append("  OK: LinkedElementProxy class")
except Exception as e:
    results.append("  FAIL: LinkedElementProxy - {0}".format(e))

results.append("")

# Test 3: Revit API imports
results.append("Test 3: Revit API imports")
try:
    from Autodesk.Revit.DB import Transform
    results.append("  OK: Transform")

    # Test Transform.Identity
    identity = Transform.Identity
    results.append("  OK: Transform.Identity = {0}".format(type(identity).__name__))
except Exception as e:
    results.append("  FAIL: Transform - {0}".format(e))

try:
    from Autodesk.Revit.DB import FilteredElementCollector
    results.append("  OK: FilteredElementCollector")
except Exception as e:
    results.append("  FAIL: FilteredElementCollector - {0}".format(e))

try:
    from Autodesk.Revit.DB import RevitLinkInstance
    results.append("  OK: RevitLinkInstance")
except Exception as e:
    results.append("  FAIL: RevitLinkInstance - {0}".format(e))

try:
    from Autodesk.Revit.DB import ImportInstance
    results.append("  OK: ImportInstance")
except Exception as e:
    results.append("  FAIL: ImportInstance - {0}".format(e))

results.append("")

# Test 4: Config with linked doc flags
results.append("Test 4: Config with linked document flags")
try:
    cfg = Config(
        include_linked_rvt=True,
        include_dwg_imports=True
    )
    results.append("  OK: Config created")
    results.append("    include_linked_rvt: {0}".format(cfg.include_linked_rvt))
    results.append("    include_dwg_imports: {0}".format(cfg.include_dwg_imports))

    # Test to_dict
    cfg_dict = cfg.to_dict()
    results.append("  OK: Config.to_dict()")
    results.append("    'include_linked_rvt' in dict: {0}".format('include_linked_rvt' in cfg_dict))

except Exception as e:
    results.append("  FAIL: Config with flags - {0}".format(e))
    import traceback
    results.append(traceback.format_exc())

results.append("")
results.append("=" * 70)
results.append("If all tests pass, the modules are OK.")
results.append("If imports fail, check Python path and file syntax.")
results.append("=" * 70)

OUT = "\n".join(results)
