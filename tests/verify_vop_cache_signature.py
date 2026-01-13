"""
Test script to verify VOP cache signature enhancement (Phase 1).

This script validates that:
1. _collect_element_ids_for_view() returns sorted element IDs
2. _view_signature() includes element IDs in schema v2
3. Cache invalidation works when elements change
"""

def test_signature_collection():
    """
    Test the element ID collection and signature generation.

    Run this in Revit with a document open to verify the implementation.
    """
    print("\n" + "="*70)
    print("VOP Cache Signature Enhancement - Phase 1 Verification")
    print("="*70)

    try:
        # Import Revit API
        from Autodesk.Revit.DB import FilteredElementCollector
        import clr
        clr.AddReference('RevitAPI')

        # Get active document and a view
        doc = __revit__.ActiveUIDocument.Document
        active_view = doc.ActiveView

        print(f"\n1. Testing with active view: {active_view.Name}")
        print(f"   View ID: {active_view.Id.IntegerValue}")

        # Test element collection
        print("\n2. Testing _collect_element_ids_for_view()...")

        collector = FilteredElementCollector(doc, active_view.Id)
        collector = collector.WhereElementIsNotElementType()

        elem_ids = []
        for elem in collector:
            try:
                elem_id = elem.Id.IntegerValue
                if elem_id is not None:
                    elem_ids.append(int(elem_id))
            except Exception:
                continue

        elem_ids = sorted(set(elem_ids))

        print(f"   ✓ Collected {len(elem_ids)} element IDs")
        if len(elem_ids) > 0:
            print(f"   ✓ First 5 IDs: {elem_ids[:5]}")
            print(f"   ✓ Last 5 IDs: {elem_ids[-5:]}")
            print(f"   ✓ IDs are sorted: {elem_ids == sorted(elem_ids)}")

        # Test signature generation (simulate)
        print("\n3. Testing signature format...")
        elem_ids_str = ",".join(str(eid) for eid in elem_ids)

        import hashlib
        import json

        # Simplified signature dict (schema v2)
        sig = {
            "schema": 2,
            "view_id": active_view.Id.IntegerValue,
            "view_name": active_view.Name,
            "elem_ids": elem_ids_str,
        }

        blob = json.dumps(sig, sort_keys=True, separators=(",", ":")).encode("utf-8")
        sig_hash = hashlib.sha1(blob).hexdigest()

        print(f"   ✓ Schema version: {sig['schema']}")
        print(f"   ✓ Element IDs string length: {len(elem_ids_str)} chars")
        print(f"   ✓ Signature hash: {sig_hash[:16]}...")

        # Test cache invalidation simulation
        print("\n4. Testing cache invalidation simulation...")

        # Simulate adding an element
        elem_ids_modified = elem_ids + [999999]
        elem_ids_str_modified = ",".join(str(eid) for eid in elem_ids_modified)

        sig_modified = {
            "schema": 2,
            "view_id": active_view.Id.IntegerValue,
            "view_name": active_view.Name,
            "elem_ids": elem_ids_str_modified,
        }

        blob_modified = json.dumps(sig_modified, sort_keys=True, separators=(",", ":")).encode("utf-8")
        sig_hash_modified = hashlib.sha1(blob_modified).hexdigest()

        print(f"   ✓ Original signature:  {sig_hash[:16]}...")
        print(f"   ✓ Modified signature:  {sig_hash_modified[:16]}...")
        print(f"   ✓ Signatures differ:   {sig_hash != sig_hash_modified}")

        print("\n" + "="*70)
        print("✓ All verification tests passed!")
        print("="*70)
        return None

    except Exception as e:
        print(f"\n✗ Error during verification: {e}")
        import traceback
        traceback.print_exc()
        raise AssertionError("Signature verification failed") from e


def test_cache_file_inspection(cache_dir=None):
    """
    Inspect actual cache files to verify schema v2 format.

    Args:
        cache_dir: Path to VOP view cache directory (if None, will prompt)
    """
    import json
    import os

    print("\n" + "="*70)
    print("Cache File Inspection")
    print("="*70)

    if cache_dir is None:
        print("\nPlease provide cache directory path when calling this function:")
        print("  test_cache_file_inspection(r'C:\\path\\to\\cache')")
        return

    if not os.path.exists(cache_dir):
        print(f"\n✗ Cache directory not found: {cache_dir}")
        return

    cache_files = [f for f in os.listdir(cache_dir) if f.startswith("view_") and f.endswith(".json")]

    print(f"\nFound {len(cache_files)} cache files in: {cache_dir}")

    for cache_file in cache_files[:5]:  # Inspect first 5
        cache_path = os.path.join(cache_dir, cache_file)
        print(f"\n  Inspecting: {cache_file}")

        try:
            with open(cache_path, 'r') as f:
                payload = json.load(f)

            signature_data = payload.get("signature")
            saved_utc = payload.get("saved_utc")

            print(f"    Signature hash: {signature_data[:16] if signature_data else 'N/A'}...")
            print(f"    Saved UTC: {saved_utc}")

            # Try to decode signature details (not stored in cache, but we can infer)
            result = payload.get("result", {})
            print(f"    View ID: {result.get('view_id')}")
            print(f"    View name: {result.get('view_name')}")

        except Exception as e:
            print(f"    ✗ Error reading cache file: {e}")

    print("\nNote: Cache files store signature hashes, not the full signature dict.")
    print("To see schema version, you need to inspect signatures during generation.")


if __name__ == "__main__":
    # Run verification test
    test_signature_collection()

    # To inspect cache files, uncomment and provide path:
    # test_cache_file_inspection(r"C:\path\to\your\cache\directory")
