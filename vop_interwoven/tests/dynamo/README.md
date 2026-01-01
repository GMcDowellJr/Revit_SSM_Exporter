# Dynamo Test Scripts

This directory contains Dynamo Python node test scripts for progressive testing of the VOP interwoven pipeline implementation.

## Usage

1. **Open Dynamo** in Revit with your test model loaded
2. **Create a Python Script node** in Dynamo
3. **Copy the test script** content into the Python node
4. **Update the path** at the top: `sys.path.append(r'C:\path\to\Revit_SSM_Exporter')`
5. **Run the script** and check the output

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
sys.path.append(r'C:\Users\YourName\Projects\Revit_SSM_Exporter')  # ← UPDATE THIS

# Paste test script content here
# ...

# Output goes to OUT variable
OUT = results
```

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

## Output Format

Test scripts output multi-line strings showing:
- ✅ Success indicators
- ❌ Error indicators
- ⚠ Warnings
- Detailed results (counts, dimensions, etc.)
- Stack traces on errors

## Next Steps

After all phase tests pass, move to full pipeline testing with `run_vop_pipeline()` from `entry_dynamo.py`.
