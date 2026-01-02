# Dynamo Test Scripts

This directory contains Dynamo Python node test scripts for progressive testing of the VOP interwoven pipeline implementation.

## Usage

1. **Open Dynamo** in Revit with your test model loaded
2. **Create a Python Script node** in Dynamo
3. **Copy the test script** content into the Python node
4. **Run the script** - all paths are pre-configured for `C:\Users\gmcdowell\Documents\Revit_SSM_Exporter`
5. Check the output

## Test Scripts

### `test_all_phases.py` - Quick Validation
**Use this first** to check which phases are implemented and working.

- Tests all phases at once
- Shows ✅/❌/⚠/⏸ status for each phase
- Good for sanity checking after changes

### `test_phase1_view_basis.py` - Phase 1
Tests view coordinate system extraction and transforms.

**Prerequisites**: Phase 1 implementation complete

**Checks**:
- View basis extraction
- Vector orthonormality
- Coordinate transforms

### `test_phase2_collection.py` - Phase 2
Tests element collection and bounding box projection.

**Prerequisites**: Phases 1-2 implementation complete

**Checks**:
- Raster initialization
- Element collection
- Category counts
- Bounding box projection

### `test_phase3_classification.py` - Phase 3
Tests UV classification and proxy generation.

**Prerequisites**: Phases 1-3 implementation complete

**Checks**:
- TINY/LINEAR/AREAL classification
- Classification distribution
- UV_AABB proxy generation
- Proxy dimensions

### `test_phase7_csv_export.py` - Phase 7
Tests CSV export with invariant validation.

**Prerequisites**: Phases 1-7 implementation complete

**Checks**:
- Core CSV creation (views_core_YYYY-MM-DD.csv)
- VOP CSV creation (views_vop_YYYY-MM-DD.csv)
- CSV invariant validation (ModelCells + AnnoCells ≤ TotalCells)
- Export to `C:\temp\vop_output`

### `test_phase8a_annotations.py` - Phase 8a
Tests annotation collection, classification, and rasterization.

**Prerequisites**: Phase 8a implementation complete

**Checks**:
- Annotation collection by category whitelist
- Classification into 7 types (TEXT/TAG/DIM/DETAIL/LINES/REGION/OTHER)
- Keynote handling (Material Element→TAG, User→TEXT)
- ViewSpecific filter (detail lines vs model lines)
- Anno_key array population
- CSV export with AnnoCells_* metrics

### `thinrunner.py` - Quick Iteration Runner
**Use this for rapid development iteration** with automatic module reloading.

**Features**:
- Accepts IN[0] as list of views (or uses current view)
- Automatic module reloading (no need to restart Dynamo)
- Concise output summary
- CSV and JSON export
- Error handling with full traceback

**Perfect for**: Active development, testing code changes without restarting Dynamo

## Recommended Testing Workflow

1. Start with `test_all_phases.py` to see current status
2. Implement next phase based on IMPLEMENTATION_PLAN.md
3. Run phase-specific test to verify implementation
4. Fix any errors reported
5. Re-run `test_all_phases.py` to confirm nothing broke
6. Move to next phase

## Example Dynamo Python Node Setup

```python
# Dynamo Python Script node

import sys
sys.path.append(r'C:\Users\gmcdowell\Documents\Revit_SSM_Exporter')  # ✅ Pre-configured

# Paste test script content here
# ...

# Output goes to OUT variable
OUT = results
```

**Note**: All test scripts are pre-configured with the correct path. No manual updates needed!

## Troubleshooting

### "ImportError: No module named vop_interwoven"
- Check that `sys.path.append()` points to the correct directory
- Path should be the parent directory containing `vop_interwoven/`

### "RuntimeError: Not running in Revit/Dynamo context"
- Make sure you're running inside Dynamo, not standalone Python
- Use `get_current_document()` and `get_current_view()` helpers

### "AttributeError: 'View3D' object has no attribute..."
- This means the Revit API integration needs updating
- Report the full error back for fixing
- Check IMPLEMENTATION_PLAN.md for correct API usage

### "No elements found"
- Check that your view isn't empty
- Try a different view with visible model elements
- Check category filters in `collect_view_elements()`

### "0 views processed"
- **FIXED**: DraftingViews now supported (commit 16eb3a7)
- Supported view types: FloorPlan, CeilingPlan, Elevation, Section, AreaPlan, EngineeringPlan, Detail, DraftingView
- Not supported: 3D views, schedules, sheets, legends

### AttributeError: 'Bounds2D' object has no attribute 'min_x'
- **FIXED**: Bounds2D attribute names corrected (commit c0f55af)
- If still occurring, ensure you have the latest code
- Use `thinrunner.py` with module reloading to pick up fixes

## Output Format

Test scripts output multi-line strings showing:
- ✅ Success indicators
- ❌ Error indicators
- ⚠ Warnings
- Detailed results (counts, dimensions, etc.)
- Stack traces on errors

## Next Steps

After all phase tests pass, move to full pipeline testing with `run_vop_pipeline()` from `entry_dynamo.py`.
