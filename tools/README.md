# SSM/VOP Exporter Tools

Quality assurance and testing utilities for the SSM/VOP exporter.

## Scripts

### `compare_golden.py` - Golden Baseline Comparison

Compares current exporter outputs against golden baseline to detect regressions.

**Usage:**
```bash
python tools/compare_golden.py \
    --golden tests/ssm_vop_v1 \
    --current ~/Documents/_metrics \
    --verbose
```

**Arguments:**
- `--golden`: Path to golden baseline directory (contains manifest.sha256)
- `--current`: Path to current output directory
- `--exclude-columns`: CSV columns to exclude from comparison (default: RunId,ElapsedSec,ConfigHash)
- `--verbose`: Show detailed diff output

**Exit Codes:**
- `0`: Outputs match (no regression)
- `1`: Outputs differ (regression detected)
- `2`: Error (missing files, invalid arguments)

**What it compares:**
- CSV files: Content hashes after excluding volatile columns
- PNG files: Exact pixel hashes
- Overall: Bundle hash for quick verification

**Comparison Rules:**
- CSV rows must be in deterministic order (enforced by Tier 1 fixes)
- Excluded columns (RunId, ElapsedSec, ConfigHash) don't affect comparison
- PNG pixels must match exactly

---

### `generate_manifest.py` - Generate Golden Baseline

Creates a manifest.sha256 file from exporter outputs to establish a new golden baseline.

**Usage:**
```bash
# After successful exporter run:
python tools/generate_manifest.py \
    --output-dir ~/Documents/_metrics \
    --manifest tests/ssm_vop_v1/manifest.sha256
```

**Arguments:**
- `--output-dir`: Path to directory containing CSV and PNG outputs
- `--manifest`: Path where manifest.sha256 will be written
- `--exclude-columns`: CSV columns to exclude from hashing (default: RunId,ElapsedSec,ConfigHash)

**Output Format:**
```
CSV  views_core_2024-09-16.csv  <sha256>
CSV  views_vop_2024-09-16.csv  <sha256>
PNG  VOP_occ_1172011_1_-_c.png  <sha256>
...
BUNDLE  manifest  <combined sha256>
```

**Workflow:**
1. Run exporter on reference model using golden Dynamo script
2. Verify outputs manually (spot check CSVs, view PNGs)
3. Run `generate_manifest.py` to create manifest.sha256
4. Commit manifest to repository as golden baseline

---

## Workflow Examples

### Establishing Initial Golden Baseline

```bash
# 1. Run exporter with reference Dynamo script
#    (in Revit/Dynamo: load tests/ssm_vop_v1/ssm_vop_v1.dyn, execute)

# 2. Check outputs look correct
ls ~/Documents/_metrics/
ls ~/Documents/_metrics/occupancy/

# 3. Generate manifest
python tools/generate_manifest.py \
    --output-dir ~/Documents/_metrics \
    --manifest tests/ssm_vop_v1/manifest.sha256

# 4. Commit golden baseline
git add tests/ssm_vop_v1/manifest.sha256
git commit -m "Establish golden baseline for SSM/VOP v1"
```

### Testing for Regressions

```bash
# 1. Make code changes (refactoring, bug fixes, etc.)

# 2. Run exporter with same reference script
#    (in Revit/Dynamo: load tests/ssm_vop_v1/ssm_vop_v1.dyn, execute)

# 3. Compare against golden baseline
python tools/compare_golden.py \
    --golden tests/ssm_vop_v1 \
    --current ~/Documents/_metrics \
    --verbose

# If outputs match (exit code 0):
#   ✓ No regression - changes preserved behavior
#
# If outputs differ (exit code 1):
#   - Review differences
#   - Determine if change is:
#     * Intentional improvement → update golden baseline
#     * Unintentional regression → fix code
```

### Updating Golden Baseline After Improvement

```bash
# If changes intentionally improved output (e.g., Tier 1 determinism fixes):

# 1. Verify improvement is correct
python tools/compare_golden.py \
    --golden tests/ssm_vop_v1 \
    --current ~/Documents/_metrics \
    --verbose

# 2. Review differences to confirm they're expected

# 3. Update golden baseline
python tools/generate_manifest.py \
    --output-dir ~/Documents/_metrics \
    --manifest tests/ssm_vop_v1/manifest.sha256

# 4. Commit updated baseline
git add tests/ssm_vop_v1/manifest.sha256
git commit -m "Update golden baseline: determinism improvements"
```

---

## Integration with CI/CD

These scripts are designed for local developer use but can be integrated into CI:

```yaml
# Example GitHub Actions workflow
steps:
  - name: Run exporter
    run: |
      # Load Revit, run Dynamo script
      # (requires Revit/Dynamo CI setup)

  - name: Compare outputs
    run: |
      python tools/compare_golden.py \
        --golden tests/ssm_vop_v1 \
        --current outputs/

  - name: Report regression
    if: failure()
    run: |
      echo "Regression detected - outputs differ from golden baseline"
```

---

## Notes

**Why hash-based comparison?**
- Saves space (don't need to store full golden CSVs/PNGs in repo)
- Manifest file is small and easy to version control
- Fast comparison (just compute current hash vs stored hash)

**Why exclude columns?**
- `RunId`: Changes every run (timestamp-based)
- `ElapsedSec`: Varies based on hardware/load
- `ConfigHash`: Changes when output_dir or other non-semantic config changes

**Determinism requirements:**
- Tier 1 fixes ensure stable cell/view ordering
- Multiple runs on same model must produce identical artifacts
- If comparison fails, check for non-deterministic dict iteration

---

## See Also

- `tests/ssm_vop_v1/README.md` - Golden baseline specification
- `docs/golden_artifacts_rules.md` - Ordering semantics policy
- GitHub Issues A1, A2 (Tier 2 tooling)
