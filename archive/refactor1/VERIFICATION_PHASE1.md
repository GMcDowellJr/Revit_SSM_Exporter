# Phase 1 VOP Cache Signature Enhancement - Verification Guide

This guide explains how to verify that the VOP cache signature enhancement is working correctly.

## Quick Verification Steps

### 1. Unit Test (Revit Python Shell)

Run the test script in Revit's Python Shell or pyRevit:

```python
# Load the test script
exec(open(r'/home/user/Revit_SSM_Exporter/test_vop_cache_signature.py').read())

# Run verification
test_signature_collection()
```

**Expected output:**
- ✓ Element IDs collected successfully
- ✓ Schema version is 2
- ✓ Signature hashes differ when element list changes

---

## 2. Integration Test (Full Pipeline Run)

### Test A: Cache MISS → Cache HIT (No Changes)

This verifies that cache works when nothing changes.

```python
from vop_interwoven import pipeline, config

# Configure cache
cfg = config.Config()
cfg.view_cache_enabled = True
cfg.view_cache_dir = r"C:\Temp\vop_cache_test"
cfg.view_cache_require_doc_unmodified = True

# Get document and view
doc = __revit__.ActiveUIDocument.Document
view_id = doc.ActiveView.Id.IntegerValue

# First run - should be MISS (cache cold)
print("\n=== RUN 1: Cache should be MISS ===")
results1 = pipeline.process_document_views(doc, [view_id], cfg)
cache_status_1 = results1[0].get('cache', {}).get('view_cache', 'N/A')
print(f"Cache status: {cache_status_1}")

# Second run - should be HIT (nothing changed)
print("\n=== RUN 2: Cache should be HIT ===")
results2 = pipeline.process_document_views(doc, [view_id], cfg)
cache_status_2 = results2[0].get('cache', {}).get('view_cache', 'N/A')
print(f"Cache status: {cache_status_2}")

# Verify
assert cache_status_1 == "MISS_SAVED", f"Expected MISS_SAVED, got {cache_status_1}"
assert cache_status_2 == "HIT", f"Expected HIT, got {cache_status_2}"
print("\n✓ Cache HIT verification passed!")
```

### Test B: Cache Invalidation (Element Added)

This verifies that adding an element invalidates the cache.

**Steps:**
1. Run pipeline with cache enabled (cache MISS → MISS_SAVED)
2. **Manually add a model element** to the view in Revit (e.g., place a wall)
3. Run pipeline again
4. **Expected:** Cache MISS (signature changed due to new element ID)

```python
# Run 1: Initial cache
results1 = pipeline.process_document_views(doc, [view_id], cfg)
cache_status_1 = results1[0].get('cache', {}).get('view_cache')
print(f"Run 1 cache status: {cache_status_1}")

# --- PAUSE HERE: Add an element in Revit UI ---
input("Press Enter after adding an element to the view...")

# Run 2: Should invalidate cache
results2 = pipeline.process_document_views(doc, [view_id], cfg)
cache_status_2 = results2[0].get('cache', {}).get('view_cache')
print(f"Run 2 cache status: {cache_status_2}")

if cache_status_2 == "MISS_SAVED":
    print("✓ Cache correctly invalidated after element addition!")
else:
    print(f"✗ Expected MISS_SAVED, got {cache_status_2}")
```

### Test C: Cache Invalidation (Element Removed)

This verifies that removing an element invalidates the cache.

**Steps:**
1. Run pipeline (cache HIT if nothing changed)
2. **Delete a model element** from the view in Revit
3. Run pipeline again
4. **Expected:** Cache MISS (signature changed due to removed element ID)

---

## 3. Manual Cache File Inspection

### Check Schema Version in Signature

Add debug logging to see the actual signature during generation:

```python
# Temporarily add this at line 317 in pipeline.py (after signature generation)
import sys
print(f"[DEBUG] View signature schema: {sig.get('schema')}", file=sys.stderr)
print(f"[DEBUG] Element IDs count: {len(elem_ids)}", file=sys.stderr)
print(f"[DEBUG] Signature hash: {hashlib.sha1(blob).hexdigest()[:16]}...", file=sys.stderr)
```

Then run your pipeline and check the console output.

### Inspect Cache Directory

```python
# List cache files and check timestamps
import os
import json
from datetime import datetime

cache_dir = r"C:\Temp\vop_cache_test"

for filename in os.listdir(cache_dir):
    if filename.endswith('.json'):
        filepath = os.path.join(cache_dir, filename)
        with open(filepath, 'r') as f:
            data = json.load(f)

        saved_time = datetime.fromtimestamp(data['saved_utc'])
        sig_hash = data['signature'][:16]

        print(f"{filename}:")
        print(f"  Saved: {saved_time}")
        print(f"  Signature: {sig_hash}...")
        print()
```

---

## 4. Performance Verification

Verify that element collection doesn't degrade performance:

```python
import time

# Time the signature generation
start = time.time()
results = pipeline.process_document_views(doc, [view_id], cfg)
elapsed = time.time() - start

# Check timing breakdown
timings = results[0].get('timings', {})
mode_ms = timings.get('mode_ms', 0)
total_ms = timings.get('total_ms', 0)

print(f"Total processing time: {elapsed:.3f}s")
print(f"Mode resolution: {mode_ms}ms")
print(f"Total (from timings): {total_ms}ms")

# Element collection happens during signature generation (part of cache check)
# Should be negligible (<10ms for typical views)
```

---

## 5. Edge Case Testing

### Test with Empty View (No Elements)

```python
# Create or use a drafting view with no elements
empty_view = # ... get empty view reference
results = pipeline.process_document_views(doc, [empty_view.Id.IntegerValue], cfg)

# Should work without errors, elem_ids should be empty string
print("✓ Empty view handled correctly")
```

### Test with Large View (Many Elements)

```python
# Use a view with 1000+ elements
large_view = # ... get view with many elements

start = time.time()
results = pipeline.process_document_views(doc, [large_view.Id.IntegerValue], cfg)
elapsed = time.time() - start

print(f"Large view processing time: {elapsed:.3f}s")
# Should still be fast (element enumeration is O(n))
```

### Test Exception Handling

Verify the function never raises:

```python
# Test with None inputs (should return [])
from vop_interwoven.pipeline import process_document_views

# The function is nested, so we need to test via the pipeline
# Errors should be caught and return [] for elem_ids

# Best way: Set a breakpoint or add logging in the catch block
# to verify exceptions are being caught
```

---

## Expected Results Summary

| Test Scenario | Expected Behavior | Verification Method |
|--------------|-------------------|---------------------|
| Schema version | `"schema": 2` | Check signature dict |
| Element IDs included | `"elem_ids": "123,456,789"` | Check signature dict |
| Cache HIT (no changes) | `view_cache: "HIT"` | Integration test A |
| Cache MISS (element added) | `view_cache: "MISS_SAVED"` | Integration test B |
| Cache MISS (element removed) | `view_cache: "MISS_SAVED"` | Integration test C |
| Performance | No degradation | Timing comparison |
| Error handling | Never raises | Edge case tests |

---

## Troubleshooting

### Cache Always Misses

**Possible causes:**
1. `view_cache_require_doc_unmodified = True` and document is modified
   - Solution: Save document or set to `False`

2. Config changes between runs
   - Solution: Use identical config for both runs

3. Signature includes timestamp (it shouldn't)
   - Solution: Verify signature dict doesn't include time-based fields

### Performance Issues

**Symptoms:** Slow cache check
**Diagnosis:** Check element count in view
**Solution:** Element enumeration is O(n), expected for large views

### Cache Not Invalidating

**Possible causes:**
1. Element change wasn't visible in the view
   - Solution: Verify element is actually in view's element set

2. Old schema v1 cache file exists
   - Solution: Clear cache directory and regenerate

---

## Automated Test Suite

For CI/CD, consider adding pytest tests:

```python
# tests/test_vop_cache_signature.py
def test_collect_element_ids_for_view(mock_doc, mock_view):
    """Test element ID collection returns sorted list."""
    # ... mock setup
    ids = _collect_element_ids_for_view(mock_doc, mock_view)
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)  # No duplicates

def test_view_signature_schema_v2(mock_doc, mock_view):
    """Test signature uses schema version 2."""
    sig_hash, sig_dict = _view_signature(mock_doc, mock_view, "model")
    assert sig_dict["schema"] == 2
    assert "elem_ids" in sig_dict
```

---

## Success Criteria

✅ Phase 1 is working correctly if:

1. ✓ Schema version is 2
2. ✓ Element IDs are included in signature
3. ✓ Cache HITs when view content unchanged
4. ✓ Cache MISSes when elements added/removed
5. ✓ No exceptions raised during normal operation
6. ✓ No performance degradation
7. ✓ Signature is deterministic (same inputs → same hash)
