#!/usr/bin/env python
"""
Static code verification for Phase 1 VOP cache signature enhancement.

This script verifies the code changes are present without running Revit.
"""

import re
import sys
from pathlib import Path


def verify_implementation():
    """Verify Phase 1 implementation in pipeline.py"""

    print("="*70)
    print("Phase 1 VOP Cache Signature Enhancement - Static Verification")
    print("="*70)

    # Find pipeline.py
    pipeline_path = Path(__file__).parent / "vop_interwoven" / "pipeline.py"

    if not pipeline_path.exists():
        print(f"\n✗ ERROR: Cannot find {pipeline_path}")
        return False

    print(f"\n✓ Found: {pipeline_path}")

    # Read file
    with open(pipeline_path, 'r', encoding='utf-8') as f:
        content = f.read()

    all_passed = True

    # Test 1: Check for _collect_element_ids_for_view function
    print("\n1. Checking for _collect_element_ids_for_view() function...")
    if re.search(r'def _collect_element_ids_for_view\(doc, view\):', content):
        print("   ✓ Function definition found")

        # Check key implementation details
        if 'FilteredElementCollector' in content:
            print("   ✓ Uses FilteredElementCollector")
        else:
            print("   ✗ Missing FilteredElementCollector")
            all_passed = False

        if 'WhereElementIsNotElementType()' in content:
            print("   ✓ Filters out element types")
        else:
            print("   ✗ Missing WhereElementIsNotElementType()")
            all_passed = False

        if re.search(r'return sorted\(set\(ids\)\)', content):
            print("   ✓ Returns sorted list")
        else:
            print("   ✗ Missing sorted list return")
            all_passed = False

    else:
        print("   ✗ Function _collect_element_ids_for_view() not found")
        all_passed = False

    # Test 2: Check function placement (before _view_signature)
    print("\n2. Checking function placement...")
    collect_match = re.search(r'def _collect_element_ids_for_view', content)
    signature_match = re.search(r'def _view_signature\(doc_obj, view_obj, view_mode_val\)', content)

    if collect_match and signature_match:
        if collect_match.start() < signature_match.start():
            print("   ✓ _collect_element_ids_for_view() defined before _view_signature()")
        else:
            print("   ✗ Function order incorrect")
            all_passed = False
    else:
        print("   ✗ Cannot verify function placement")
        all_passed = False

    # Test 3: Check _view_signature modifications
    print("\n3. Checking _view_signature() modifications...")

    # Check for element ID collection call
    if re.search(r'elem_ids = _collect_element_ids_for_view\(doc_obj, view_obj\)', content):
        print("   ✓ Calls _collect_element_ids_for_view()")
    else:
        print("   ✗ Missing call to _collect_element_ids_for_view()")
        all_passed = False

    # Check for elem_ids_str conversion
    if re.search(r'elem_ids_str = .*\.join\(str\(eid\) for eid in elem_ids\)', content):
        print("   ✓ Converts to comma-separated string")
    else:
        print("   ✗ Missing elem_ids string conversion")
        all_passed = False

    # Check for schema version 2
    if re.search(r'"schema":\s*2', content):
        print("   ✓ Schema bumped to version 2")
    else:
        print("   ✗ Schema not updated to version 2")
        all_passed = False

    # Check for elem_ids in signature dict
    if re.search(r'"elem_ids":\s*elem_ids_str', content):
        print("   ✓ Element IDs added to signature dict")
    else:
        print("   ✗ Element IDs not added to signature dict")
        all_passed = False

    # Test 4: Check docstring updates
    print("\n4. Checking documentation...")

    # Check for docstring mentioning element IDs
    if re.search(r'Schema v2 adds element ID tracking', content):
        print("   ✓ Docstring updated to explain element ID tracking")
    else:
        print("   ⚠ Docstring may not explain element ID tracking")

    # Test 5: Check error handling
    print("\n5. Checking error handling...")

    # Count exception handlers in _collect_element_ids_for_view
    collect_func_match = re.search(
        r'def _collect_element_ids_for_view\(doc, view\):.*?(?=\n    def |\nclass |\Z)',
        content,
        re.DOTALL
    )

    if collect_func_match:
        func_body = collect_func_match.group(0)
        exception_count = func_body.count('except Exception')

        if exception_count >= 2:
            print(f"   ✓ Comprehensive error handling ({exception_count} exception handlers)")
        elif exception_count == 1:
            print(f"   ⚠ Basic error handling (only {exception_count} exception handler)")
        else:
            print(f"   ✗ Insufficient error handling")
            all_passed = False
    else:
        print("   ⚠ Cannot analyze error handling")

    # Test 6: Check for common issues
    print("\n6. Checking for common issues...")

    # Check that function doesn't raise
    if 'raise' not in collect_func_match.group(0):
        print("   ✓ Function never raises exceptions")
    else:
        print("   ✗ Function may raise exceptions")
        all_passed = False

    # Final summary
    print("\n" + "="*70)
    if all_passed:
        print("✓ All static verification checks PASSED!")
        print("="*70)
        print("\nNext steps:")
        print("1. Run integration tests in Revit (see VERIFICATION_PHASE1.md)")
        print("2. Verify cache invalidation with element add/remove")
        print("3. Check performance with large views")
        return True
    else:
        print("✗ Some verification checks FAILED")
        print("="*70)
        print("\nPlease review the implementation and fix the issues above.")
        return False


if __name__ == "__main__":
    success = verify_implementation()
    sys.exit(0 if success else 1)
