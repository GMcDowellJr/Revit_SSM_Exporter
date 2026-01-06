"""
Minimal Single View Test - Step-by-step diagnostic

This will show exactly where the pipeline is failing.
Paste into Dynamo Python node.
"""

import sys
sys.path.append(r'C:\Users\gmcdowell\Documents\Revit_SSM_Exporter')

results = []
results.append("=" * 70)
results.append("MINIMAL SINGLE VIEW DIAGNOSTIC")
results.append("=" * 70)
results.append("")

# Step 1: Test imports
results.append("STEP 1: Test imports")
results.append("-" * 70)
try:
    from vop_interwoven.entry_dynamo import get_current_document, get_current_view
    results.append("OK: entry_dynamo imports")
except Exception as e:
    results.append("FAIL: entry_dynamo import - {0}".format(e))
    OUT = "\n".join(results)
    raise

try:
    from vop_interwoven.config import Config
    results.append("OK: Config imports")
except Exception as e:
    results.append("FAIL: Config import - {0}".format(e))
    OUT = "\n".join(results)
    raise

try:
    from vop_interwoven.entry_dynamo import run_vop_pipeline
    results.append("OK: run_vop_pipeline imports")
except Exception as e:
    results.append("FAIL: run_vop_pipeline import - {0}".format(e))
    OUT = "\n".join(results)
    raise

results.append("")

# Step 2: Get doc and view
results.append("STEP 2: Get document and view")
results.append("-" * 70)
try:
    doc = get_current_document()
    results.append("OK: Got document - {0}".format(doc.Title))
except Exception as e:
    results.append("FAIL: get_current_document - {0}".format(e))
    OUT = "\n".join(results)
    raise

try:
    view = get_current_view()
    results.append("OK: Got view - {0} (ID: {1})".format(view.Name, view.Id.IntegerValue))
except Exception as e:
    results.append("FAIL: get_current_view - {0}".format(e))
    OUT = "\n".join(results)
    raise

results.append("")

# Step 3: Create config
results.append("STEP 3: Create config")
results.append("-" * 70)
try:
    cfg = Config(
        include_linked_rvt=True,
        include_dwg_imports=True
    )
    results.append("OK: Config created")
    results.append("  include_linked_rvt: {0}".format(cfg.include_linked_rvt))
    results.append("  include_dwg_imports: {0}".format(cfg.include_dwg_imports))
except Exception as e:
    results.append("FAIL: Config creation - {0}".format(e))
    import traceback
    results.append(traceback.format_exc())
    OUT = "\n".join(results)
    raise

results.append("")

# Step 4: Run pipeline
results.append("STEP 4: Run pipeline on single view")
results.append("-" * 70)
try:
    result = run_vop_pipeline(doc, [view.Id], cfg)

    results.append("Pipeline completed!")
    results.append("  Success: {0}".format(result.get('success', False)))
    results.append("  Views processed: {0}".format(len(result.get('views', []))))
    results.append("  Errors: {0}".format(len(result.get('errors', []))))

    if result.get('errors'):
        results.append("")
        results.append("ERRORS:")
        for err in result['errors']:
            results.append("  - {0}".format(err))

    if result.get('views'):
        view_data = result['views'][0]
        results.append("")
        results.append("View result:")
        results.append("  View ID: {0}".format(view_data.get('view_id', '?')))
        results.append("  View name: {0}".format(view_data.get('view_name', '?')))
        results.append("  Grid: {0}x{1}".format(
            view_data.get('width', '?'),
            view_data.get('height', '?')
        ))

        diag = view_data.get('diagnostics', {})
        results.append("  Elements: {0}".format(diag.get('num_elements', '?')))
        results.append("  Filled cells: {0}".format(diag.get('num_filled_cells', '?')))

except Exception as e:
    results.append("FAIL: Pipeline execution - {0}".format(e))
    import traceback
    results.append("")
    results.append("Full traceback:")
    results.append(traceback.format_exc())

results.append("")
results.append("=" * 70)
results.append("END DIAGNOSTIC")
results.append("=" * 70)

OUT = "\n".join(results)
