# Metrics Totals Relationships (Set/Superset/Subset Cheat Sheet)

This document defines which metric totals are exact equalities vs subset/superset constraints for the locked manifest output.

## 1) Exact equalities (must always hold)

- **8-state partition must total all cells**
  - `Cells_Empty + Cells_ModelOnly + Cells_AnnoOnly + Cells_ExtOnly + Cells_ModelAnno + Cells_ModelExt + Cells_AnnoExt + Cells_All3 == TotalCells`
- **Annotation type bins must total anno-present cells**
  - `AnnoFinalCells_TEXT + AnnoFinalCells_TAG + AnnoFinalCells_DIM + AnnoFinalCells_DETAIL + AnnoFinalCells_LINES + AnnoFinalCells_REGION + AnnoFinalCells_OTHER == AnnoPresentFinal`
- **External inclusion/exclusion identity must hold**
  - `ExtFinalCells_Any == ExtFinalCells_DWG + ExtFinalCells_RVT - ExtFinalCells_DWG_RVT`

## 2) Subset/superset inequalities (must always hold)

- `ExtFinalCells_Only <= ExtFinalCells_Any`
  - `ExtFinalCells_Only` means external-present cells with no host-present ink or occupancy.
- `ExtFinalCells_DWG <= ExtFinalCells_Any`
- `ExtFinalCells_RVT <= ExtFinalCells_Any`
- `ExtFinalCells_DWG_RVT <= ExtFinalCells_DWG`
- `ExtFinalCells_DWG_RVT <= ExtFinalCells_RVT`

Interpretation:

- `ExtFinalCells_Any` is the external-presence union set.
- `ExtFinalCells_DWG_RVT` is the overlap/intersection subset.

## 3) Useful derived groups from the 8-state partition

These are not separate manifest invariants, but they are useful diagnostics:

- **Model-present cells**
  - `Cells_ModelOnly + Cells_ModelAnno + Cells_ModelExt + Cells_All3`
  - Model presence includes final occupancy (`model_mask` / `occ_*`) as well as edge/proxy ink.
- **Annotation-present cells**
  - `Cells_AnnoOnly + Cells_ModelAnno + Cells_AnnoExt + Cells_All3`
- **External-present-by-partition cells**
  - `Cells_ExtOnly + Cells_ModelExt + Cells_AnnoExt + Cells_All3`


## 4) Legacy `views_vop` 4-way presence buckets

`views_vop_*.csv` preserves the older four mutually-exclusive presence columns:

- `Empty`: `Cells_Empty`
- `ModelOnly`: `Cells_ModelOnly + Cells_ModelExt + Cells_ExtOnly`
- `AnnoOnly`: `Cells_AnnoOnly`
- `Overlap`: `Cells_ModelAnno + Cells_AnnoExt + Cells_All3`

In the locked 8-state manifest, `E` means external model content (RVT link or
DWG).  Therefore external-only cells are still model-present cells in the legacy
CSV partition, while `Ext_Cells_*` columns provide the source overlay needed to
distinguish host-vs-linked/DWG contribution.  For example, linked-only cells
contribute to both `ModelOnly` and `Ext_Cells_RVT`; host-only cells contribute to
`ModelOnly` but not `Ext_Cells_*`.

## 5) What is allowed to exceed what

### Model classes are multihot (not disjoint)

- `ModelClassCells_WALL`, `..._DOOR`, `..._STAIR`, `..._COLUMN`, `..._LIGHT`, `..._OTHER` are **multihot** counts.
- A single cell can increment multiple model class columns.
- Therefore:
  - `sum(ModelClassCells_*)` is **not required** to equal `Cells_ModelOnly`.
  - `sum(ModelClassCells_*)` is **not required** to equal any single partition bucket.
  - `sum(ModelClassCells_*)` may exceed model-present cell counts when multi-class cells exist.

## 6) Quick triage sequence when numbers look wrong

1. Check partition identity first (`sum partition == TotalCells`).
2. Check annotation identity (`sum AnnoFinalCells_* == AnnoPresentFinal`).
3. Check external inequalities and inclusion/exclusion identity.
4. If class totals seem high, verify whether cells have multiple model keys/classes (expected in multihot mode).
