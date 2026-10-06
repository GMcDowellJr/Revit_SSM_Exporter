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

### `analyze_capture_state_probe.py` - Capture-state probe analysis

Measures the TIFFs `tests/dynamo/probe_capture_state.py` exported. Per image:
size, distinct colours, the non-white fraction of a 2 % border band, the grey
count and the top 10 colours. Per step against the S0 of the same question and
view: the changed pixels, or `size_differs` (never resized). For Q3, a per-view
table of written box, read-back box, exported px, implied px and the non-fit
axis difference. Writes `probe_capture_state_analysis.json` last, carrying
the probe JSON's sha256. Refuses (exit 2) on a missing, ambiguous or
unreadable probe JSON, or on a TIFF that is missing or unreadable. See
`tests/dynamo/PROBE_CAPTURE_STATE.md`.

```bash
python tools/analyze_capture_state_probe.py path/to/capture_state_<timestamp>
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

### `register_stage_a_annotation.py` - Registered capture onto the model lattice

For captures made with `Config.color_id_buffer_registered_capture` on. Fits
the registration ticks recorded under `registration_marks` in BOTH sidecars
(`registration_marks.py`, the one fit, shared with the decoder and the probe
analyzer), composes annotation pixel -> model pixel, removes the ticks from the
annotation capture and resamples it onto the model image's pixel grid,
nearest neighbour (colour IDs are never blended).

It **refuses**, and writes the reason instead of a TIFF, when a sidecar has no
usable `registration_marks`, when the two sidecars' marks are not the same
ticks, when either capture has ticks at fewer than two levels on an axis, or
when the transform would mirror an axis. It never falls back to the model's
recorded lattice. Losses (ink off the model image, uncovered model pixels,
colours that did not survive the resample) are counted in the record.

**Usage:**
```bash
python tools/register_stage_a_annotation.py path/to/View_123_anno.json
python tools/register_stage_a_annotation.py path/to/color_id_buffer/
```

**Output:** `<anno-stem>.registered.tiff` and `<anno-stem>.registered.json`
beside the annotation sidecar; the JSON is written last and names the TIFF's
hash. Exit 0 all registered, 1 any refused, 2 an input unreadable.
`decode_stage_a_color_id.py` removes the marks from its own decode of either
capture (`registration_marks` in its output).

---

### `stage_a_grid_rollup.py` - Every view's grid result across Stage A runs

Collects what `register_stage_a_annotation.py` and `stage_a_grid.py` already
wrote into one CSV and one summary JSON, so a project-wide run can be shared
without its images. **Read-only:** it never registers, never grids, and
refuses an `--out` inside a capture folder.

**Run order** (per run):
```bash
python tools/register_stage_a_annotation.py <run>/color_id_buffer
python tools/stage_a_grid.py <run>
python tools/stage_a_grid_rollup.py <run>
```

**The denominator is what the run requested:** one row per unique view id in
`run_meta.json`'s `views_requested`, matched to its `views` outcome and to its
model sidecar by view id, plus one row per model sidecar the run did not
request. A view that left no Stage A sidecar (a drafting view or legend that
went the geometry path, a 3D view rejected by capability gating) is a row,
never an absence. Model sidecars are found with `stage_a_grid.model_sidecars`
(the grid's own selector); grid JSONs are never counted.

**A run is refused**, with no rows and its reason under `refused_runs`, when
`run_meta.json` is absent or unreadable, is not `finalized: true`, carries no
`run_id`, requests no views, or when the view-id sets of `views_requested` and
`views` differ. Duplicate ids in `views_requested` give one row and are listed
under `duplicate_requested_ids`. The summary records the count invariant (the
row-status counts sum to `requested_count` plus the orphan sidecars); if it
fails, the tool exits 2.

What was NOT gridded is a row like any other:

| `row_status` | meaning |
|---|---|
| `no_stage_a_capture` | requested, but no model sidecar has its view id; carries run_meta's `capture_status` and failure reason |
| `orphan_sidecar` | a model sidecar whose view id the run did not request (or cannot be read) |
| `sidecar_ambiguous` | more than one model sidecar claims the view id; not guessed between |
| `not_this_run` | the sidecar cannot be tied to the run: `run_meta.json` (finalized) does not record this view's capture as `success`, so the sidecar may be a previous run's |
| `gridded` | `<view>.grid.json` with status `value`, still matching the files on disk |
| `grid_refused` | the grid's own refusal, with its reason |
| `not_gridded` | no `<view>.grid.json` in `<run>/analysis_grid/` |
| `grid_stale` | a hash the grid recorded (model sidecar, model TIFF, registration record, registered TIFF) no longer matches, the grid was made under another run id, or a registration appeared or became usable after the grid was made |
| `unreadable` | the grid JSON cannot be parsed; the exception |

`registration_state` is `registered`, `refused` (its refusals), `absent` or
`unusable`; a gridded row that is not `registered` is counted as model-only
and keeps the grid's `no_registered_annotation` flag. `frame_predicted_px`,
`frame_actual_px` and `frame_delta_px` are the grid's non-fit-axis frame
check (blank when unmeasured); `frame_mismatch` and `frame_unmeasured`
arrive through `flags` and are counted in `flag_counts`. View name, type, scale
and on-sheet come from the run's `views_core_*.csv` on (`RunId`, `ViewId`), with
`views_core_state` saying how the join went. A view with more than one
views_core row is reported, not hidden: `duplicate_rows` when the rows agree
on ViewType, `conflicting` when they do not; each field is filled only where
every row agrees. The summary counts these views, counts rows by run_meta
capture status, and crosstabs ViewType by `row_status`, which is where
drafting views and legends show up. An empty cell means "not applicable for
this row", never zero.

**It reports states, not a verdict:** no ok/pass/clean field, and every count
in the summary sits beside its denominator.

**Usage:**
```bash
python tools/stage_a_grid_rollup.py path/to/run
python tools/stage_a_grid_rollup.py run_a run_b/color_id_buffer --out rollup/
```

**Output:** `grid_rollup.csv`, then `grid_rollup.summary.json` (written last,
naming the CSV's sha256), in `<run>/analysis_grid/` for one run; `--out` is
required for more than one. Exit 0 when no run is refused and every row is
`gridded` and `registered`; 1 for any other row or a refused run; 2 when every
run is refused, the count invariant fails, or on bad arguments.

---

### `stage_a_runs_index.py` - One fact row per Stage A run under an export root

Lists every run folder under an export root
(`<root>/<project>/<model>/<as_of>__<run_id>/`) as one row of facts read from
that run's own files. A downstream summarizer (Power Query / Power BI) reads
this index plus the files it names and never has to list the export tree.
**Facts only:** no "good", "latest" or "included" column; selection belongs
to the consumer. **Read-only on run folders**: an `--out` at or under any folder
holding `run_meta.json` is refused, including run folders the scan skipped. Standard library only.

```bash
python tools/stage_a_runs_index.py --root <Exports> [--out <dir>] [--max-depth 4]
```

**`--root` is the export root** (`…\VOP\Exports`), not a project or model
folder. `model_key` is the path from the root to each run folder's parent, so
pointed at a model folder (`…\Exports\KSRF\Hops_Interior_AR`) every run sits
directly under the root and its `model_key` is empty. Such rows are still
indexed, and each is listed under `scan.warnings` ("run folder parent is
root; model_key undefined") and printed as a `WARNING` line. `model_key` is
never invented from `doc_title`.

**Discovery.** A run folder is any folder that directly holds `run_meta.json`.
The walk lists one directory at a time (never a recursive glob), stops at a
run folder, never enters `color_id_buffer/` or `analysis_grid/`, does not
follow symlinks, and stops at `--max-depth` (root = 0). Every folder it skips
is listed under `scan.skipped` with its reason: an unparsable `run_meta.json`,
a run folder not named `<YYYY-MM-DD>__<run_id>`, a reserved name, a symlink,
the depth cap, or a folder that cannot be listed.

**Output**, in `--out` (default `<root>`): `runs_index.csv`, then
`runs_index.json` (written last, naming the CSV's sha256). Each is written to a
temporary name and atomically replaced, rebuilt from scratch every time. Rows
are sorted by (`model_key`, `as_of_date`, `run_id`). The CSV is UTF-8 with
RFC 4180 quoting and every value is a string. **An empty cell means absent or
not applicable, never zero.** Two runs over an unchanged tree give a
byte-identical CSV; the JSON differs only in `generated_utc`. The JSON
carries `schema` (`vop.stage_a.runs_index.v1`), `tool_version`,
`generated_utc`, `root`, `columns`, `rows` (the JSON-valued columns as real
objects), `scan` (folders visited, run folders found and indexed, skipped
folders with reasons, max depth reached) and `csv_sha256`.

**Column contract.** Every `*_path_rel` and `run_dir_rel` is relative to
`--root` and uses `/`. Each file-derived group has a `*_state` (`value`,
`absent`, `unreadable`, plus `ambiguous` for a dated file matched more than
once) and a `*_reason` that is always filled when the state is not `value`. It
can also annotate a `value`, for example a summary whose CSV is missing. The
CSV a summary or inventory names is used only if it is a bare file name beside
the record; an absolute or `..` path is reported in `*_reason`, never followed.
A summary whose `runs` carry a non-string `run_id` leaves `rollup_run_ids` and
`chk_rollup_run_id_eq_run_meta` empty, with the reason.

| group | columns | source |
|---|---|---|
| identity | `model_key` (root to the run's parent; never `doc_title`), `run_dir_rel`, `run_dir_name`, `run_id` (opaque, never parsed), `run_tag`, `as_of_date` (run_meta `date`), `doc_title`, `doc_path`, `config_hash`, `git_commit`, `exporter_version`, `revit_version_number`, `run_meta_schema`, `finalized` | `run_meta.json`, keys below |
| views | `views_requested_count`, `views_count`, `capture_status_counts` (JSON) | `run_meta.json` |
| roll-up | `rollup_state`, `rollup_reason`, `rollup_location` (`top` \| `analysis_grid`), `rollup_csv_path_rel`, `rollup_summary_path_rel`, `rollup_schema`, `rollup_tool_version`, `rollup_csv_sha256` (re-hashed), `rollup_csv_sha256_match`, `rollup_class_map_sha256`, `rollup_class_map_version`, `rollup_refused_runs_count`, `rollup_count_invariant_difference`, `rollup_row_status_counts`, `rollup_registration_state_counts`, `rollup_flag_counts`, `rollup_run_ids` (JSON) | `grid_rollup.summary.json`, at the run's top level first, then `analysis_grid/` |
| kinds | `kinds_state`, `kinds_reason`, `kinds_location`, `kinds_csv_path_rel`, `kinds_schema`, `kinds_tool_version`, `kinds_class_map_sha256`, `kinds_csv_sha256`, `kinds_csv_sha256_match`, `kinds_grid_used`, `kinds_grid_denominator` | `kinds_inventory.json`, same search order |
| per-run files | `views_core_state` / `_reason` / `_path_rel` / `_rows` (data rows), `element_map_*`, `diagnostics_*` | `views_core_<d>.csv`, `vop_view_element_map_<d>.json`, `views_diagnostics_<d>.json` at the run's top level; presence only |
| consistency | `chk_folder_date_eq_as_of`, `chk_folder_run_id_eq_run_meta`, `chk_rollup_run_id_eq_run_meta` (the summary's run ids are exactly this run's), `chk_kinds_class_map_eq_rollup` | `true` / `false`, empty when not computable |
| group | `n_runs_same_model_as_of`, `n_config_hash_in_model` (distinct non-empty hashes) | across rows |
| run_meta | `run_meta_unavailable` (JSON: field -> reason; empty when none) | `run_meta.json` |

The run_meta keys each identity column is read from:

| column | run_meta.json key | shape written by `run_meta.build_run_meta` |
|---|---|---|
| `run_id`, `run_tag`, `as_of_date`, `config_hash`, `exporter_version`, `run_meta_schema`, `finalized` | `run_id`, `run_tag`, `date`, `config_hash`, `exporter_version`, `schema`, `finalized` | plain value (`run_tag` is null unless the run had a non-date override such as `PR_221`) |
| `doc_title`, `doc_path`, `git_commit` | `doc_title.value`, `doc_path.value`, `git_commit.value` | three-valued: `{"state": "value", "value": …}` or `{"state": "unavailable", "reason": …}` |
| `revit_version_number` | `revit_version.value.version_number` | three-valued, the value an object |

A three-valued field that is `unavailable` gives an empty cell, with its reason
in `run_meta_unavailable`. A bare value (an older, flat record) is read as the
value. `git_commit` is the full 40-character SHA that `run_meta.git_commit()`
reads from `.git`. `exporter_version` is copied as written, and the exporter
writes the constant `run_meta.EXPORTER_VERSION`, which is the package name
`vop_interwoven`, not a version. Use `git_commit` to identify the code.

The roll-up and kinds file names are read from those tools' own sources, not
copied, and a test asserts they agree with the imported modules.

Exit 0 when at least one run is indexed and every run is `finalized` with
`rollup_state` `value`; 1 for anything else indexed; 2 when no run is indexed
(the index is still written), on bad arguments, or when the output cannot be
written.

Tests: `tests/test_stage_a_runs_index.py` (fixtures A-I; the roll-up and kinds
outputs are written by the real producers).

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
