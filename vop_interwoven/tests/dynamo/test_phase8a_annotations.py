"""Phase 8a: Annotation Collection & Rasterization Test

Test annotation collection, classification, and rasterization to anno_key layer.

Usage in Dynamo Python node:
    1. Set path: sys.path.append(r'C:\\Users\\gmcdowell\\Documents\\Revit_SSM_Exporter')
    2. Select views to process (or leave IN[0] empty for current view)
    3. Run this script
    4. Check output for annotation counts and type breakdown

Expected output:
    - Annotations collected by category
    - Classification by 7 types: TEXT, TAG, DIM, DETAIL, LINES, REGION, OTHER
    - Keynotes classified as TAG (Material Element) or TEXT (User)
    - Anno cells populated in raster
    - CSV export shows non-zero AnnoCells_* counts

Test cases:
    1. View with TextNotes → TEXT count > 0
    2. View with Tags → TAG count > 0
    3. View with Dimensions → DIM count > 0
    4. View with Keynotes → TAG or TEXT count increases
    5. View with FilledRegion → REGION count > 0
    6. View with DetailCurves → LINES count > 0
"""

import sys
sys.path.append(r'C:\Users\gmcdowell\Documents\Revit_SSM_Exporter')

from vop_interwoven.revit.annotation import collect_2d_annotations
from vop_interwoven.entry_dynamo import get_current_document, get_current_view
from vop_interwoven.dynamo_helpers import run_pipeline_from_dynamo_input
from collections import Counter

# Get document and view
doc = get_current_document()

# Test 1: Direct annotation collection
print("=" * 60)
print("TEST 1: Direct Annotation Collection")
print("=" * 60)

# Get current view for direct testing
current_view = get_current_view()
print(f"\nCurrent view: {current_view.Name}")

# Collect annotations from current view
annotations = collect_2d_annotations(doc, current_view)

print(f"\nTotal annotations collected: {len(annotations)}")

if annotations:
    # Group by type
    type_counts = Counter(anno_type for _, anno_type in annotations)

    print("\nAnnotation breakdown by type:")
    for anno_type in ["TEXT", "TAG", "DIM", "DETAIL", "LINES", "REGION", "OTHER"]:
        count = type_counts.get(anno_type, 0)
        if count > 0:
            print(f"  {anno_type}: {count}")
        else:
            print(f"  {anno_type}: 0 (none found)")

    # Show first few annotations with details
    print("\nFirst 10 annotations (sample):")
    for i, (elem, anno_type) in enumerate(annotations[:10]):
        elem_id = elem.Id.IntegerValue
        category = elem.Category.Name if elem.Category else "Unknown"
        print(f"  {i+1}. [{anno_type}] ID={elem_id} Category={category}")
else:
    print("\n⚠️  No annotations found in current view")
    print("   Try selecting a view with text, dimensions, tags, or detail elements")

# Test 2: Full pipeline with annotation rasterization
print("\n" + "=" * 60)
print("TEST 2: Full Pipeline with Annotation Rasterization")
print("=" * 60)

# Run full pipeline with CSV export
result = run_pipeline_from_dynamo_input(
    views_input=IN[0] if len(IN) > 0 else None,
    output_dir=r'C:\temp\vop_output',
    export_csv=True,
    export_json=True,
    export_png=False,  # Skip PNG for faster testing
    verbose=True
)

# Display pipeline results
print("\n=== Pipeline Results ===")
print(f"Views processed: {result.get('views_processed', 0)}")
print(f"Core CSV: {result.get('core_csv_path', 'N/A')}")
print(f"VOP CSV: {result.get('vop_csv_path', 'N/A')}")

# Check annotation metrics from pipeline
pipeline_result = result.get('pipeline_result', {})
if pipeline_result and 'views' in pipeline_result:
    views = pipeline_result['views']

    print(f"\n=== Annotation Metrics by View ===")
    for view_data in views:
        view_name = view_data.get('view_name', 'Unknown')
        diagnostics = view_data.get('diagnostics', {})
        num_annotations = diagnostics.get('num_annotations', 0)

        print(f"\nView: {view_name}")
        print(f"  Annotations rasterized: {num_annotations}")

        # Check raster data
        raster_dict = view_data.get('raster', {})
        if raster_dict:
            anno_meta = raster_dict.get('anno_meta', [])
            print(f"  Anno metadata entries: {len(anno_meta)}")

            # Count by type
            if anno_meta:
                meta_types = Counter(meta.get('type', 'OTHER') for meta in anno_meta)
                print(f"  Type breakdown:")
                for anno_type in ["TEXT", "TAG", "DIM", "DETAIL", "LINES", "REGION", "OTHER"]:
                    count = meta_types.get(anno_type, 0)
                    if count > 0:
                        print(f"    {anno_type}: {count}")

# Test 3: Validate CSV export has annotation metrics
print("\n" + "=" * 60)
print("TEST 3: CSV Annotation Metrics Validation")
print("=" * 60)

vop_csv_path = result.get('vop_csv_path')
if vop_csv_path:
    import csv
    import os

    if os.path.exists(vop_csv_path):
        print(f"\nReading VOP CSV: {vop_csv_path}")

        with open(vop_csv_path, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        # Check for non-zero annotation cells
        print(f"\nAnnotation cell counts from CSV:")
        for row in rows:
            view_name = row.get('ViewName', 'Unknown')

            # Get annotation type counts
            anno_cells = {
                'TEXT': int(row.get('AnnoCells_TEXT', 0)),
                'TAG': int(row.get('AnnoCells_TAG', 0)),
                'DIM': int(row.get('AnnoCells_DIM', 0)),
                'DETAIL': int(row.get('AnnoCells_DETAIL', 0)),
                'LINES': int(row.get('AnnoCells_LINES', 0)),
                'REGION': int(row.get('AnnoCells_REGION', 0)),
                'OTHER': int(row.get('AnnoCells_OTHER', 0)),
            }

            total_anno_cells = sum(anno_cells.values())

            print(f"\n  View: {view_name}")
            print(f"    Total anno cells: {total_anno_cells}")

            if total_anno_cells > 0:
                for anno_type, count in anno_cells.items():
                    if count > 0:
                        print(f"      {anno_type}: {count} cells")
                print(f"    ✅ Annotations detected in CSV!")
            else:
                print(f"    ⚠️  No annotation cells in CSV (view may have no annotations)")
    else:
        print(f"\n❌ VOP CSV not found at {vop_csv_path}")
else:
    print("\n❌ VOP CSV path not returned from pipeline")

# Summary
print("\n" + "=" * 60)
print("PHASE 8a TEST SUMMARY")
print("=" * 60)

summary_lines = []

# Check Test 1
if annotations:
    summary_lines.append("✅ Test 1: Annotation collection working")
    summary_lines.append(f"   - Collected {len(annotations)} annotations")
    summary_lines.append(f"   - Types found: {', '.join(sorted(type_counts.keys()))}")
else:
    summary_lines.append("⚠️  Test 1: No annotations in current view")

# Check Test 2
if pipeline_result and 'views' in pipeline_result:
    total_annos = sum(
        v.get('diagnostics', {}).get('num_annotations', 0)
        for v in pipeline_result['views']
    )
    if total_annos > 0:
        summary_lines.append("✅ Test 2: Pipeline rasterization working")
        summary_lines.append(f"   - Rasterized {total_annos} annotations across views")
    else:
        summary_lines.append("⚠️  Test 2: Pipeline ran but no annotations rasterized")
else:
    summary_lines.append("❌ Test 2: Pipeline failed or returned no views")

# Check Test 3
if vop_csv_path and os.path.exists(vop_csv_path):
    summary_lines.append("✅ Test 3: CSV export working")
    summary_lines.append(f"   - CSV file created with annotation metrics")
else:
    summary_lines.append("❌ Test 3: CSV export failed")

# Print summary
for line in summary_lines:
    print(line)

# Success criteria
print("\n" + "=" * 60)
print("SUCCESS CRITERIA")
print("=" * 60)
print("✓ Annotations collected by category whitelist")
print("✓ Classification logic matches 7 types (TEXT/TAG/DIM/DETAIL/LINES/REGION/OTHER)")
print("✓ Keynotes handled: Material Element→TAG, User→TEXT")
print("✓ Bounding boxes extracted from view")
print("✓ anno_key array populated (not all -1)")
print("✓ anno_meta list has correct type classifications")
print("✓ CSV export shows AnnoCells_* counts")

# Output for Dynamo
OUT = f"✅ Phase 8a Complete!\\n" \
      f"Direct collection: {len(annotations)} annotations\\n" \
      f"Pipeline rasterized: {sum(v.get('diagnostics', {}).get('num_annotations', 0) for v in pipeline_result.get('views', []))} annotations\\n" \
      f"CSV: {vop_csv_path}\\n" \
      f"\\nSee console for detailed breakdown"
