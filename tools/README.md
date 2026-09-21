# SSM/VOP Exporter Tools

Quality assurance and testing utilities for the SSM/VOP exporter.

## Scripts


### `verify_invariant_core.py` - Path-independent verification invariant core

Reads a run bundle (`views_core` / `views_vop` / `views_perf` /
`views_occlusion`) and emits ONE JSON verification bundle carrying eight
numbered invariants, the L0-L4 layering, and a violation register scored
against a known-open baseline.

The invariants read only quantities BOTH extraction paths emit, so they
survive the classification-surface deletion untouched. That is the point: it
is the instrument that makes the post-deletion read "new violations only".

**It is not a gate.** The output is a register. The exit code reports whether
the tool could DECIDE, not whether the data was good:

| exit | meaning |
|---|---|
| 0 | a bundle was produced |
| 1 | `--fail-on-new` only, and only for a violation the baseline does not carry |
| 2 | REFUSAL - it could not identify something it needs |

Exit 1 is opt-in so the default can never be silently read as pass/fail.

**What it refuses** (rather than guessing): a missing or ambiguous CSV role, a
missing directory, an empty scan, a boolean token outside the declared
vocabulary (`bool("False")` is `True`, and coercing it would invert invariant
3 with no symptom), an unparseable number, a row with no `ViewId`, a bundle
carrying two `ConfigHash` values (`CONFIGURATION_DRIFT`, reusing the existing
campaign-fingerprint semantics), and an unsupported bundle schema version on
read.

**Declared, not defaulted.** `--path` and `--cache-mode` have no defaults. No
CSV column discriminates the extraction path - `RowSource` is a hardcoded
constant written by both - so the operator declares it and L0 records that it
was declared. No cache-row count is expected in either direction.

`N_MIN_ORDER_STATISTIC = 8` is the only threshold in the file. It governs
medians and quantiles only; counts, sums, ratios, min and max are reported at
any `n`, always with `n` alongside. Invariant 7 compares two cell totals and
asserts NOTHING about their agreement - they count different populations by
construction, and a tolerance would launder a definitional gap.

**Usage:**
```bash
python tools/verify_invariant_core.py ~/Documents/_metrics \
    --path geometry --cache-mode unknown --out bundle.json

# CI, once a capture is expected to stay clean:
python tools/verify_invariant_core.py <dir> --path color-id \
    --cache-mode enabled --fail-on-new
```

Tests: `tests/test_verify_invariant_core.py` (80 cases). Every scenario has a
control asserting the unmutated fixture is clean, because otherwise each one
would also pass against a tool that refused, or violated, everything.

---


### `stage_a_cycle.py` - One-command Stage A campaign advance

A thin, idempotent wrapper that composes `campaign_planner.py` and
`analyze_stage_a_probe.py` so normal Stage A operation is one command
between Dynamo runs: discover manifests, ingest execution evidence, analyze
outstanding evidence, ingest normalized acceptance, and prepare the next
batch. It runs outside Revit, imports no Revit API modules, and makes no
acceptance/gating/fallback decision of its own - see
`docs/CAMPAIGN_PLANNER.md` ("`stage_a_cycle.py` - one command between Dynamo
runs") for the full contract, path defaults, and exit codes.

**Usage:**
```bash
python tools/stage_a_cycle.py advance campaign/campaign.json campaign/campaign_state.json
python tools/stage_a_cycle.py advance campaign/campaign.json campaign/campaign_state.json --dry-run --json
```

---

### `analyze_stage_a_probe.py` - External Stage A Probe Analysis

Analyzes extraction-only Stage A Dynamo probe outputs using standalone Python,
Pillow, and NumPy. It recognizes graphics-semantics, model-linework, and
image-alignment probe JSON files, loads the referenced TIFFs, and writes
`<original>.analyzed.json` companion files with the pixel-analysis schema that
used to be produced inside Dynamo, including rebuilt graphics recommendations,
linework classifications/rankings, sequential export repeatability, guarded diff images, and image-alignment
model-to-canvas placement, and alignment evidence status after pixel dimensions are known.

**Usage:**
```bash
python tools/analyze_stage_a_probe.py path/to/probe_output_dir
python tools/analyze_stage_a_probe.py path/to/probe.json another/probe.json
```

**Output:** a short terminal summary plus analyzed JSON sidecars.

---

### `capture_overlay.py` - Stage A review overlay

Draws a Stage A capture's TIFF with its own sidecar's bboxes, source classes
and category labels on top, so the capture can be read against the drawing.
Offline; standard library + Pillow + `clamp_pad_geometry.py`. Handles both
passes, reading each one's own rendered rectangle (`bounds_xy` for the model
pass, `registration.rendered_uv` for the annotation pass).

**It emits pictures only** - no score, no tolerance, no pass/fail, no
aggregate verdict. Validating a capture is a human read of the TIFF and the
sidecar against the drawing, and a tool that rated the result would replace
that read rather than support it. The only number it prints is a truncation
notice, so a capped panel cannot hide records.

Records that reach no pixels are listed under the image with the reason -
an unavailable bbox, a rectangle wholly off the crop, or a class excluded by
`--only`. It **refuses** to place anything, and says which, when the pass
recorded no crop rectangle, when the crop is degenerate, or when the
producer's recorded export size disagrees with the file being read (decode's
Guard 1 in a different costume: the clamp pads would go negative and every
box would land somewhere plausible and wrong).

**Usage:**
```bash
python tools/capture_overlay.py path/to/sidecar.json
python tools/capture_overlay.py path/to/capture_dir/
python tools/capture_overlay.py sidecar.json --labels id --only host --only dwg
```

**Output:** `<sidecar-stem>.overlay.png` beside the sidecar (or in
`--out-dir`). Never overwrites the sidecar or the TIFF.

---

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

## Stage A external acceptance records

`analyze_stage_a_probe.py` accepts a raw probe execution envelope or a legacy
probe-native report and writes a sibling `*.analyzed.json`; raw evidence is never
overwritten. The acceptance schema is version `1.0` and dispatch is explicit for
image alignment, minimum-ID mutations, model linework, external sources, and
graphics semantics. Envelope `probe_schema_version` currently supports `1.0`.
Unknown probe identities and schema versions are errors rather than filename
inferences.

Each record separates `analysis_status` (`COMPLETED`, `LIMITED`, or `ERROR`),
the imported `execution_status`, and deterministic `acceptance_status` (`PASS`,
`FAIL`, or `INCONCLUSIVE`). It includes campaign/run/job identifiers when
provided, source and artifact references, stable reason codes, checks,
limitations, errors, warnings, timestamps, and the enriched native report under
`metrics.probe_specific`. Existing enriched native fields are also mirrored at
the top level during the version-1 compatibility period.

Gate precedence is deterministic: failed execution, rollback, restoration,
dimensions, repeatability, attestation, and probe-specific contamination are
failures; a failure dominates inconclusive checks. Missing execution state,
TIFFs, image dependencies, cases, calibration, or visual/semantic evidence are
inconclusive unless the probe contract explicitly makes them failures. Failed
mutation attestation excludes that variant from pixel fidelity analysis.
Rollback is aggregated from every executed variant when an envelope aggregate
is unavailable; requested case coverage is based on executed cases, never a
static requested list.

The automated checks are intentionally bounded:

* alignment checks actual/requested dimensions, exact calibration evidence,
  repeatability, and model-to-canvas offset. Resolution-qualified export keys
  such as `original.dpi_150` count toward the requested `original` mode, and
  every requested mode/resolution-case combination must be present, so one DPI
  export cannot satisfy a multi-DPI request. The
  placement enrichment uses those qualified exports without discarding an
  already-recorded native placement. Dimension acceptance compares both the
  accepted width and predicted height from the preserved resolution report.
  Repeatability passes only when the producer records an explicit successful
  sequential comparison; a single export remains inconclusive;
* minimum-ID checks attestation, TIFF dimensions, palette contamination,
  requested coverage, rollback, restoration, and separately reported semantic
  preservation;
* linework checks ID-reference dimensional agreement, contamination and
  repeatability, while internal/hidden edges remain manual evidence. Each
  linework mode and generated difference image is compared only within its
  matching resolution case;
* external-source checks HOST/LINK/DWG variant coverage, artifacts,
  attestation, rollback and restoration, while linked-source visual handling
  remains inconclusive unless explicit evidence establishes it. Source-family
  status is recalculated as a tri-state from externally refreshed per-variant
  pixels; a stale raw `passed: false` caused by unavailable in-Revit Pillow is
  not treated as a visual failure.

Thresholds used by legacy pixel calculations remain the documented probe
contract thresholds (`DARK_THRESHOLD`, `WHITE_THRESHOLD`, reserved near-white,
and the linework 0.1%/10-pixel tolerance). Acceptance adds no hidden residual
threshold. Install external-test dependencies with:

```bash
python -m pip install -r requirements-test.txt
python -m pytest tests/dynamo/test_analyze_stage_a_acceptance.py \
  tests/dynamo/test_analyze_stage_a_minimum_id_mutations.py
```

## Stage A campaigns: core host color-ID feasibility vs. non-blocking follow-up

`examples/stage_a_campaign.json` is the core campaign, scoped to exactly one
question: can Stage A reliably assign unique colors to visible host Revit
elements and export a raster from which those elements can be recovered,
across elevation/plan(active)/plan(inactive)/RCP/section/callout?
`tools/campaign_planner.py feasibility campaign.json campaign_state.json` (or
`status`'s `host_color_id_feasibility` field) reports the aggregate
`HOST_COLOR_ID_FEASIBILITY_PASS|FAIL|MIXED|INCONCLUSIVE` conclusion, derived
per-view from the terminal status of each view's mutation-recipe closure -
see `docs/CAMPAIGN_PLANNER.md` ("Aggregate host-element color-ID
feasibility") for the full contract.

External-source capability testing, model-linework diagnostics, the full
mutation-variant factorial, and resolution-sensitivity experiments live in a
separate, independent `examples/stage_a_followup_campaign.json` with its own
campaign ID and state file. None of that work blocks the core campaign's
completion or its feasibility conclusion.
