"""Phase 7: CSV Export Test

Test CSV export functionality with invariant validation.

Usage in Dynamo Python node:
    1. Set path: sys.path.append(r'C:\Users\gmcdowell\Documents\Revit_SSM_Exporter')
    2. Run this script
    3. Check output for CSV paths and validation results

Expected output:
    - views_core_YYYY-MM-DD.csv created
    - views_vop_YYYY-MM-DD.csv created
    - CSV invariant validated for all views
"""

import sys
sys.path.append(r'C:\Users\gmcdowell\Documents\Revit_SSM_Exporter')

from vop_interwoven.dynamo_helpers import run_pipeline_from_dynamo_input
import csv
import os

# Run pipeline with CSV export
print("Running VOP pipeline with CSV export...")

result = run_pipeline_from_dynamo_input(
    views_input=IN[0] if len(IN) > 0 else None,
    output_dir=r'C:\temp\vop_output',
    export_csv=True,
    export_json=True,
    export_png=True,
    verbose=True
)

# Display results
print("\n=== Export Complete ===")
print(f"Core CSV: {result.get('core_csv_path', 'N/A')}")
print(f"VOP CSV: {result.get('vop_csv_path', 'N/A')}")
print(f"JSON: {result.get('json_path', 'N/A')}")
print(f"PNGs: {len(result.get('png_files', []))} files")
print(f"Rows exported: {result.get('rows_exported', 0)}")

# Validate VOP CSV if it exists
vop_csv_path = result.get('vop_csv_path')
if vop_csv_path and os.path.exists(vop_csv_path):
    print("\n=== Validating CSV Invariant ===")

    with open(vop_csv_path, 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Total rows in CSV: {len(rows)}")

    all_valid = True
    for i, row in enumerate(rows):
        try:
            total = int(row['TotalCells'])
            empty = int(row['Empty'])
            model = int(row['ModelOnly'])
            anno = int(row['AnnoOnly'])
            overlap = int(row['Overlap'])

            computed = empty + model + anno + overlap

            if total != computed:
                print(f"❌ Row {i+1}: Invariant FAILED")
                print(f"   TotalCells={total}, but Empty+ModelOnly+AnnoOnly+Overlap={computed}")
                all_valid = False
            else:
                view_name = row.get('ViewName', f'Row {i+1}')
                print(f"✅ {view_name}: {total} cells ({empty} empty, {model} model, {anno} anno, {overlap} overlap)")

        except Exception as e:
            print(f"❌ Row {i+1}: Error validating - {e}")
            all_valid = False

    if all_valid:
        print("\n✅ CSV INVARIANT VALIDATED FOR ALL VIEWS!")
    else:
        print("\n❌ CSV INVARIANT FAILED FOR SOME VIEWS")

    # Show annotation metrics
    print("\n=== Annotation Metrics ===")
    for i, row in enumerate(rows):
        view_name = row.get('ViewName', f'Row {i+1}')
        text = int(row.get('AnnoCells_TEXT', 0))
        tag = int(row.get('AnnoCells_TAG', 0))
        dim = int(row.get('AnnoCells_DIM', 0))
        detail = int(row.get('AnnoCells_DETAIL', 0))
        lines = int(row.get('AnnoCells_LINES', 0))
        region = int(row.get('AnnoCells_REGION', 0))
        other = int(row.get('AnnoCells_OTHER', 0))

        total_anno = text + tag + dim + detail + lines + region + other

        if total_anno > 0:
            print(f"{view_name}: {total_anno} anno cells (TEXT:{text} TAG:{tag} DIM:{dim} DETAIL:{detail} LINES:{lines} REGION:{region} OTHER:{other})")
        else:
            print(f"{view_name}: No annotations")

# Show view filtering info if verbose
if result.get('filter_info'):
    filter_info = result['filter_info']
    print("\n=== View Filtering ===")
    print(f"Total views input: {filter_info['total']}")
    print(f"Supported views: {filter_info['supported']}")
    print(f"Skipped views: {filter_info['skipped']}")

    if filter_info['skipped'] > 0:
        print("\nSkipped view types:")
        for view_name, view_type in filter_info['skipped_types']:
            print(f"  - {view_name} ({view_type})")

# Output for Dynamo
OUT = f"✅ Phase 7 Complete!\n" \
      f"Core CSV: {result.get('core_csv_path', 'N/A')}\n" \
      f"VOP CSV: {result.get('vop_csv_path', 'N/A')}\n" \
      f"Rows: {result.get('rows_exported', 0)}\n" \
      f"PNGs: {len(result.get('png_files', []))}"
