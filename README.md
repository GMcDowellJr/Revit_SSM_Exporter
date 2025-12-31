# SSM Exporter

**SSM/VOP (Space/View Occupancy Planner) Exporter for Autodesk Revit**

A specialized tool for analyzing 2D orthographic views in Revit and generating occupancy maps that show where 3D model geometry and 2D annotations appear on a configurable grid.

## Design Principle

- All views share the same UVW depth semantics (W=0 at crop UV plane).
- All elements are processed through a uniform pipeline.
- The only permitted cache is view-skipping.
## Quick Start

```python
# In Dynamo Python Script node:
import sys
sys.path.append(r"C:\path\to\ssm_exporter")

import SSM_Exporter_v4_A21
OUT = SSM_Exporter_v4_A21._safe_main()
```

Check output in: `~/Documents/_metrics/`

## Features

- **Grid-based occupancy analysis** - Configurable cell size in paper space
- **Multi-view support** - Floor Plans, RCPs, Sections, Elevations, Drafting, Detail, Legend
- **Linked model handling** - Processes Revit links and DWG imports
- **Performance caching** - View-level caching for fast iterative runs
- **Adaptive thresholds** - Automatic element size classification per view
- **CSV export** - Comprehensive metrics with view-level and element-level breakdowns
- **PNG visualization** - Optional color-coded occupancy maps
- **Occlusion handling** - AREAL 3D occlusion mask with AABB containment culling

## Output

### CSV Files
- `views_core_YYYY-MM-DD.csv` - Core occupancy metrics (empty, model-only, anno-only, overlap)
- `views_vop_YYYY-MM-DD.csv` - Extended metrics with element type breakdowns

### Occupancy States
- **Code 0** (Model-only): Only 3D geometry
- **Code 1** (Anno-only): Only 2D annotations
- **Code 2** (Overlap): Both 3D and 2D

### Invariant
All CSV outputs satisfy: `TotalCells = EmptyCells + ModelOnlyCells + AnnoOnlyCells + OverlapCells`

## Documentation

- **[USER_GUIDE.md](docs/USER_GUIDE.md)** - Installation, usage, troubleshooting
- **[CONFIGURATION.md](docs/CONFIGURATION.md)** - Complete configuration reference
- **[ARCHITECTURE.md](docs/ARCHITECTURE.md)** - Design decisions and algorithms
- **[correctness_contract.md](docs/correctness_contract.md)** - Authoritative behavior specification
- **[REFRACTOR_PLAN.md](docs/REFRACTOR_PLAN.md)** - Modularization roadmap

## Requirements

- Autodesk Revit 2020+
- Dynamo (bundled with Revit)
- Python 3.x compatible environment

## Configuration

Modify settings before running:

```python
import SSM_Exporter_v4_A21
CONFIG = SSM_Exporter_v4_A21.CONFIG

# Adjust grid resolution
CONFIG["grid"]["cell_size_paper_in"] = 0.25  # 1/4" paper space

# Change output location
CONFIG["export"]["output_dir"] = r"C:\MyProject\Metrics"

# Enable PNG visualization
CONFIG["occupancy_png"]["enabled"] = True

# Run
OUT = SSM_Exporter_v4_A21._safe_main()
```

See [CONFIGURATION.md](docs/CONFIGURATION.md) for all options.

## Repository Structure

```
ssm_exporter/
├── SSM_Exporter_v4_A21.py    # Main exporter (8,630 lines, being modularized)
├── export_csv.py              # CSV export helpers
├── README.md                  # This file
├── docs/
│   ├── USER_GUIDE.md         # User documentation
│   ├── CONFIGURATION.md      # Configuration reference
│   ├── ARCHITECTURE.md       # Design documentation
│   ├── correctness_contract.md   # Behavior specification
│   ├── REFRACTOR_PLAN.md     # Modularization plan
│   ├── config_used.json      # Example configuration
│   └── ...
└── tests/
    └── golden/               # Golden artifact regression tests
```

## Development Status

**Current Phase:** Refactoring from monolithic to modular architecture

- ✅ Phase 0: Baseline established
- 🔄 Phase 1: Extracting types, CSV, debug utilities (in progress)
- ⏳ Phase 2: View-basis transforms
- ⏳ Phase 3: Grid and counting
- ⏳ Phase 4: Projection and silhouette
- ⏳ Phase 5: Revit collection and link policy

See [REFRACTOR_PLAN.md](docs/REFRACTOR_PLAN.md) for details.

---

## Ground rules
- PATCH-only by default.
- One intent per commit (MOVE ONLY vs BEHAVIOR CHANGE).
- Baseline commit is sacred.

## Collaboration rule
AI-assisted analysis is allowed on this repository.
No automated code execution or data extraction occurs.

## Golden runs
Golden artifact test sets are defined in [docs/view_sets.md](docs/view_sets.md).
Baseline CSV outputs will be stored in `tests/golden/`.
Regression testing follows [docs/golden_artifacts_rules.md](docs/golden_artifacts_rules.md).

---

## Contributing

When making changes:

1. **Read the contract** - [correctness_contract.md](docs/correctness_contract.md) defines authoritative behavior
2. **Follow commit discipline** - Use `MOVE ONLY`, `MECHANICAL ADAPTATION`, or `BEHAVIOR CHANGE` markers
3. **Test against golden artifacts** - Ensure CSV outputs match baseline (when implemented)
4. **Document configuration changes** - Update [CONFIGURATION.md](docs/CONFIGURATION.md)
5. **One intent per commit** - Don't mix refactoring with behavior changes

## License

Internal tool - not for public distribution.

## Support

For issues or questions:
1. Check [USER_GUIDE.md](docs/USER_GUIDE.md) troubleshooting section
2. Review [correctness_contract.md](docs/correctness_contract.md) for expected behavior
3. Enable debug mode and inspect output

---

**Version:** A21 (v4)
**Last Updated:** 2025-12-17
