# CLAUDE.md - AI Assistant Guide for Revit SSM Exporter

This document provides comprehensive guidance for AI assistants working on the Revit SSM Exporter codebase.

## Project Overview

The **Revit SSM Exporter** exports 2D orthographic views from Autodesk Revit to occupancy maps showing where 3D model geometry and 2D annotations appear on a configurable grid. The main active codebase is **VOP Interwoven** (`vop_interwoven/`), which implements an occlusion-aware rasterization pipeline with:

- 3D model geometry occlusion detection with depth-aware tracking
- 2D annotation layering analysis
- Grid-based cell occupancy classification (empty, model-only, anno-only, overlap)
- CSV and PNG output formats

## Directory Structure

```
Revit_SSM_Exporter/
├── vop_interwoven/           # Main active codebase (feature complete)
│   ├── config.py             # Configuration dataclass (40+ parameters)
│   ├── pipeline.py           # Main processing logic
│   ├── entry_dynamo.py       # Dynamo Python node entry point
│   ├── csv_export.py         # CSV export (SSM-compatible format)
│   ├── png_export.py         # PNG visualization export
│   ├── streaming.py          # Data streaming utilities
│   ├── core/                 # Core algorithms
│   │   ├── raster.py         # ViewRaster & TileMap structures
│   │   ├── silhouette.py     # Multi-strategy geometry extraction
│   │   ├── geometry.py       # UV classification & proxy generation
│   │   ├── areal_extraction.py # AREAL element geometry extraction
│   │   ├── element_cache.py  # Element caching (LRU)
│   │   ├── face_selection.py # Front-facing face selection
│   │   ├── math_utils.py     # Geometric utilities
│   │   ├── diagnostics.py    # Diagnostic tracking
│   │   ├── cache.py          # General caching utilities
│   │   ├── footprint.py      # Footprint computation
│   │   ├── hull.py           # Convex hull utilities
│   │   ├── pca2d.py          # 2D PCA for OBB fitting
│   │   └── source_identity.py # Source identity (HOST|LINK|DWG)
│   ├── revit/                # Revit API integration
│   │   ├── collection.py     # Element collection & visibility
│   │   ├── annotation.py     # 2D annotation processing
│   │   ├── view_basis.py     # View coordinate system extraction
│   │   ├── linked_documents.py # Linked RVT/DWG handling
│   │   ├── collection_policy.py # Collection policy configuration
│   │   ├── safe_api.py       # Safe Revit API wrapper
│   │   └── tierb_proxy.py    # Tier B proxy generation
│   ├── diagnostics/          # Diagnostics module
│   │   └── strategy_tracker.py
│   ├── export/               # Export infrastructure
│   │   └── csv.py
│   └── docs/                 # Internal documentation
├── tests/                    # pytest-based unit tests (40+ files)
│   ├── conftest.py           # pytest configuration
│   ├── golden/               # Golden baseline artifacts
│   └── dynamo/               # Dynamo integration tests
├── tools/                    # QA and testing utilities
│   ├── compare_golden.py     # Golden baseline regression detection
│   ├── generate_manifest.py  # Golden manifest generation
│   ├── gen_maps.py           # Auto-generate code navigation maps
│   └── check_no_bare_except.py
├── legacy/                   # Original monolithic code (reference only)
└── archive/                  # Previous refactor attempts
```

> **Note**: Tests are in the repository root `tests/` directory, not inside `vop_interwoven/`.

## Core Architecture Principles

These principles are non-negotiable and must be preserved in all changes:

1. **3D model geometry is the ONLY occlusion truth** - Depth masking reserved for AreaL elements
2. **2D annotation NEVER occludes model geometry** - Annotation layering is one-directional
3. **UV Classification determines occlusion authority**, not rasterization strategy
4. **Confidence-based occlusion semantics** - Failed strategies don't degrade occlusion

### Element Classification System

Elements are classified by projected footprint size (in grid cells):

| Mode | Criteria | Examples | Occlusion Behavior |
|------|----------|----------|-------------------|
| TINY | Both dims ≤2 cells | Door hardware | UV_AABB proxy, no depth writes |
| LINEAR | One dim ≤2, other >2 | Walls, doors | OBB proxy, no depth writes |
| AREAL | Both dims >2 | Floors, roofs | Full tessellation, depth buffer |

> **Terminology**: "AREAL" (all caps) refers to elements with large projected footprints. Use this spelling consistently throughout the codebase.

### Key Configuration Parameters

```python
Config(
    tile_size=16,                    # Tile size for spatial acceleration
    adaptive_tile_size=True,         # Auto-adjust tile size based on grid
    cell_size_paper_in=0.125,        # Cell size in paper inches
    tiny_max=2,                      # Max cells for TINY classification
    thin_max=2,                      # Max cells for LINEAR thin dimension
    over_model_includes_proxies=True, # Whether proxies count as "model presence"
    proxy_mask_mode="minmask",       # "minmask" or "edges"
    depth_eps_ft=0.01,               # Depth epsilon for occlusion tests
)
```

## Refactor Rules

All changes touching pipeline, collection, rasterization, or export must follow these rules (from `vop_interwoven/docs/refactor_rules.md`):

### 1. No Silent Failure
- **Bare `except:` is forbidden**
- All recoverable errors must be recorded in `Diagnostics`
- Categorize errors: collection, bounds, geometry, raster, export
- If recovery is not clearly safe, fail loudly

### 2. Explicit Semantics
- "model present" must specify: triangles/depth truth, proxy presence, or edge presence
- View support must be capability-based, not type-based

### 3. Single Source of Truth
- Category inclusion/exclusion defined in one place only
- View bounds resolution is centralized
- Source identity normalized (`HOST | LINK | DWG`) before rasterization

### 4. Worst-Case First
Code must behave correctly under:
- Null or missing geometry
- Unloaded or partially broken links
- Rotated transforms
- Extreme view scales
- Large models

### 5. Small, Reviewable Changes
Do not mix:
- Refactoring + behavior change
- Performance + semantics
- Cleanup + logic

## Diagnostics Contract

The `Diagnostics` object is always enabled. Required fields for recorded errors:
- phase (collection, bounds, geometry, raster, export)
- view id (if applicable)
- element id (if applicable)
- source type (HOST | LINK | DWG)
- exception type
- message

**Important**: Do not use `print()` for error reporting. Do not swallow errors after recording unless recovery is safe.

## Testing

### Running Tests

```bash
# Run all tests with pytest
cd /home/user/Revit_SSM_Exporter
python -m pytest tests/ -v

# Or use the test runner script
cd vop_interwoven
./run_tests.sh

# Run Dynamo tests (requires environment setup)
VOP_RUN_DYNAMO_TESTS=1 python -m pytest tests/dynamo/
```

### Golden Baseline Testing

For regression testing against known-good outputs:

```bash
# Compare current outputs against golden baseline
python tools/compare_golden.py \
    --golden tests/ssm_vop_v1 \
    --current ~/Documents/_metrics \
    --verbose

# Generate new golden baseline after verified improvements
python tools/generate_manifest.py \
    --output-dir ~/Documents/_metrics \
    --manifest tests/ssm_vop_v1/manifest.sha256
```

### Test Coverage Areas

| Test File | Coverage |
|-----------|----------|
| `test_geometry.py` | UV classification (TINY/LINEAR/AREAL) |
| `test_raster.py` | ViewRaster & TileMap structures |
| `test_csv_export_diagnostics.py` | CSV export correctness |
| `test_areal_extraction.py` | AreaL geometry strategies |
| `test_strategy_tracker.py` | Strategy performance |
| `test_pipeline_diagnostics.py` | Pipeline diagnostics |
| `test_face_selection.py` | Front-facing face detection |

## Git Workflow

### Branch Naming
- Feature branches: `claude/<description>-<session-id>`
- Fix branches: `fix/<description>`
- Feature branches: `feat/<description>`

### Commit Message Convention
Follow conventional commits:
- `fix(scope): description` - Bug fixes
- `feat(scope): description` - New features
- `chore(scope): description` - Maintenance tasks
- `refactor(scope): description` - Code refactoring

Scopes include: `pipeline`, `geometry`, `silhouette`, `areal`, `collection`, `diagnostics`, `csv`, `png`

### Automated Workflows

**Navigation Maps Update** (`.github/workflows/update-maps-on-pr.yml`):
- Runs on PR open/sync/reopen
- Auto-generates code maps via `tools/gen_maps.py`
- Creates commit "chore: update navigation maps" if changes detected
- Generates three map files in `vop_interwoven/`:
  - `vop_interwoven_code_map_authoritative.md` - Per-file imports/definitions
  - `vop_interwoven_trace_map.md` - Call trace mapping
  - `vop_interwoven_symbol_index.md` - Searchable symbol index

## Key Files to Understand First

When starting work, read these files in order:

1. **`vop_interwoven/config.py`** - Configuration options and defaults
2. **`vop_interwoven/pipeline.py`** - Main processing flow
3. **`vop_interwoven/core/raster.py`** - ViewRaster and TileMap structures
4. **`vop_interwoven/core/geometry.py`** - UV classification logic
5. **`vop_interwoven/core/silhouette.py`** - Geometry extraction strategies
6. **`vop_interwoven/docs/refactor_rules.md`** - Coding rules

## Common Development Tasks

### Adding a New Rasterization Strategy

1. Add strategy to `vop_interwoven/core/silhouette.py`
2. Register in strategy tracker (`diagnostics/strategy_tracker.py`)
3. Add unit tests in `tests/test_*_extraction.py`
4. Update documentation if strategy affects occlusion semantics

### Modifying Element Collection

1. Changes go in `vop_interwoven/revit/collection.py`
2. Ensure source identity is normalized (HOST | LINK | DWG)
3. Add diagnostics for collection errors
4. Test with linked documents and DWG imports

### Adding CSV Export Columns

1. Modify `vop_interwoven/csv_export.py`
2. Add to schema in `vop_interwoven/PHASE7_CSV_EXPORT_PLAN.md`
3. Update golden baseline if column is non-volatile
4. Add tests in `tests/test_csv_export_diagnostics.py`

### Debugging Geometry Issues

Use diagnostics to trace geometry extraction:
```python
# Enable debug output in config
cfg = Config(
    dump_occlusion_images=True,  # Outputs PGM files
    diagnostics_enabled=True
)
```

Check the strategy tracker output for extraction method usage and failures.

## Recurring Defect Classes

Six real defects shipped across PRs #200–#202 and were found by review, not by
any check in this repo. They fall into three classes that keep recurring. Read
this before writing a fix, not after.

### 1. A quantity computed in two places, never composed

`feet_per_pixel` existed as five copies; the forward and inverse uv↔pixel
mappings drifted apart because nothing ran them back to back; the achieved-dpi
figure disagreed with the decoder's twice in a row. `TINY/LINEAR/AREAL` is
currently implemented three times and they disagree (issue #203).

**Rule.** When two pieces of code must agree, a test must *compose* them.
Testing each alone binds each to its own copy — see
`tests/test_uv_pixel_round_trip.py` and `tests/test_effective_export_dpi.py`
for the shape.

**Corollary, learned the hard way.** A test that *reimplements* the thing it
checks proves nothing: production can regress and the test stays green because
it only ever ran its own copy. If production's arithmetic is not callable,
**extract it** — that is what `resolution_contract.effective_export_dpi()` and
`view_raster_export._crop_uv_frame()` are, and both extractions are what made
the defect visible.

**Second corollary, because the first one was not enough.** Extracting the
formula binds the *formula*. It does not bind the *arguments* production
passes. After `effective_export_dpi()` was extracted and every property bound
to it, the original grid-extent defect could still be reinstated at the call
site — `_effective_export_dpi(<grid tuple>, ...)` — with all 981 tests green,
because the properties supply their own crop bounds and never see production's.
A signature check cannot close this: the grid rectangle and the render crop are
both four-value tuples. **Exercise the call site**
(`tests/test_effective_dpi_call_site.py`), and make the two candidate
arguments produce visibly different answers so the fixture discriminates.

### 2. An identity claimed in prose and never asserted

Every one of the six violated an invariant already written in a comment above
the code that broke it. Two of those comments (`resolution_contract.py:91`,
`:112`) turned out to be *overstated* — both fail below the one-pixel floor.

**Rule.** If a comment claims an identity, a universal ("always", "never",
"identically", "cannot"), assert it over generated inputs. Property tests live
in `tests/test_invariants_*.py`. A comment that *argues* why something is safe
is an unasserted proof obligation, not documentation.

### 3. Test infrastructure that nothing tests

`tests/conftest.py` decides whether failures are reported at all, so nothing
running inside it can observe it. Two defects shipped there — a quarantine that
absorbed any exception, and a check that made a quarantined file unrunnable by
node id — both invisible to the 959 tests it governed.

**Rule.** Harness behaviour is checked by running pytest as a subprocess and
asserting on its exit code: `tests/test_harness_contract.py`. Any such file
needs a **control** asserting the unmutated copy is green, or every scenario
would also pass against a harness broken outright.

### The discipline that actually caught things

- **Prove a check against a known-positive commit.** A rule you have not
  falsified is a hope. A `semgrep` rule here returned `0` and looked clean; it
  was verified against the wrong commit, then against the right one, where it
  found 4. Record the SHA a check was proven against.
- **Mutate production, not the test.** If reinstating the original defect does
  not turn the suite red, the test is not wired to it.
- **Watch the test count.** Rewriting a test file once truncated it, silently
  deleting 7 cases. The suite went green at 974. Only the total dropping from
  981 caught it.
- **Green means nothing until you know what ran.** `no-bare-except` is scoped
  to `pipeline.py`, `revit/` and `core/`; it passed on a 21-file PR touching
  none of them.
- **Know what the fake harness does NOT provide.** The shared fake
  `Autodesk.Revit.DB` had no `XYZ`/`BoundingBoxXYZ`, so
  `crop_box_from_uv_bounds()` raised `ImportError` and every end-to-end test
  ran with `bounds_xy=None` — the crop path was never exercised at all. A
  test that cannot reach the code it names is worth less than no test, so
  pin reachability with a control case.

### Tooling that found real defects here

Not installed by default; install when reviewing:

```bash
pip install vulture
pip install --ignore-installed PyJWT semgrep   # plain install hits a Debian PyJWT conflict
```

- **`semgrep`** found the hardcoded classification threshold
  (`pipeline.py:2396`, `:2398`) with zero false positives, and 86 instances of
  `except X: pass` — a Refactor Rule #1 violation that
  `check_no_bare_except.py` does not catch, because it only looks for bare
  `except:`. Runtime is 2–3 min over `vop_interwoven/` + `tools/`.
- **`vulture --min-confidence 80`** for dead code. At 60 it false-positives on
  Revit API attributes set dynamically.
- Update these rules when a new defect class appears, and record what each was
  proven against.

## Code Quality Checks

```bash
# Check for bare except (forbidden)
python tools/check_no_bare_except.py vop_interwoven/

# Run linting (if configured)
python -m flake8 vop_interwoven/ --max-line-length=120
```

## Environment Notes

- **Python**: Compatible with both IronPython 2.x and CPython 3.3+
- **Revit API**: Targets Revit 2020+ through Dynamo integration
- **No setup.py**: Deploy by copying to Revit/Dynamo environment
- **External dependencies**: Minimal (Revit API only)

## Commentary Markers in Code

Throughout the codebase, these markers indicate:
- **`✔`** = Hardened choice / recommended default
- **`⚠`** = Known pitfall / assumption boundary
- **`🧩`** = Optional extension knob

## Performance Considerations

- **Tile size 16**: Default balance (256 cells/tile)
- **Early-out**: Skips fully-occluded elements (60-80% savings)
- **Proxy savings**: TINY/LINEAR skip triangle tessellation (10-100x faster)
- **Memory**: ~2-4 bytes/cell for masks, ~4-8 bytes/cell for depth/edges

## Project Status

The VOP Interwoven pipeline is **feature complete** with full SSM parity:
- Core pipeline (view basis, collection, classification, rasterization)
- Multi-strategy silhouette extraction with fallbacks
- 2D annotation collection and classification
- External sources (RVT links, DWG imports)
- CSV and PNG export
- LRU caching and diagnostics

### Recent Development

Recent work has focused on:
- Fixing geometry duplication in family instance extraction
- AABB-based occlusion fixes for rotated geometry
- Confidence-based raster semantics enforcement
- Category statistics in diagnostics

See git log for detailed history:
```bash
git log --oneline -20
```

### Documentation Hierarchy

1. **CLAUDE.md** (this file) - Primary reference for AI assistants
2. **vop_interwoven/README.md** - Architecture overview and API reference
3. **vop_interwoven/IMPLEMENTATION_PLAN.md** - Historical development phases (now complete)
4. **vop_interwoven/docs/refactor_rules.md** - Coding standards (mandatory reading)

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
