## 1. Purpose (What this baseline is—and is not)

**Is**

* A *golden reference* for validating exporter correctness.
* A contract for **what must be true** after a run, regardless of refactor.
* A gate for PATCH vs BEHAVIOR CHANGE decisions.

**Is not**

* A performance benchmark.
* A code architecture guide.
* A debug artifact spec.

---

## 2. Locked Invariants (Must never change without explicit behavior change)

### 2.1 View Scope

* Only supported 2D views are processed:

  * Floor Plans, RCPs, Sections, Elevations, Drafting, Detail, Legend
* Unsupported views produce **zero rows** (not partial output).

### 2.2 Grid Definition

* Grid cell size is defined in **paper inches**, converted by view scale.
* No rounding until final rasterization.
* Grid domain:

  * Crop active → crop box.
  * Crop inactive → geometry extents (fallback = crop).
* A cell is valid **iff**:

  * Cell center ∈ XY domain **AND**
  * Cell center ∈ clip volume.

### 2.3 Occupancy States (Exhaustive and Exclusive)

Each cell has **exactly one** state:

* `0` = 3D-only
* `1` = 2D-only
* `2` = 2D-over-3D

No additional states, flags, or dual occupancy.

---

## 3. Geometry Contribution Rules (Correctness-critical)

### 3.1 3D Model Geometry

* Contributes **boundary only**.
* **Never** fills interior cells.
* Bounding-box projection allowed **only** for:

  * TINY or LINEAR classified regions.

### 3.2 2D Geometry (Whitelist)

| Type                                      | Contribution                      |
| ----------------------------------------- | --------------------------------- |
| Filled Regions                            | Boundary + parity-filled interior |
| Detail Lines / Curves                     | Linear bands                      |
| Text, Tags, Dimensions, Detail Components | Axis-aligned bounding rectangles  |

**Only Filled Regions may fill interiors.**

---

## 4. Region Classification (Deterministic)

Based on axis-aligned grid-space bounding box:

* **TINY**: width ≤ 2 cells AND height ≤ 2 cells
* **LINEAR**: not TINY AND min(width, height) ≤ 2
* **AREAL**: width > 2 AND height > 2

Inner loops ≤ 1 cell area are ignored.

---

## 5. Depth-Based Occlusion (3D only)

### 5.1 Occlusion Mask

* Built **only** from parity-filled interiors of **AREAL 3D regions**.
* LINEAR and TINY regions **do not** contribute to mask.

### 5.2 Culling Rule

A 3D element is excluded **iff**:

* Its UV-aligned AABB footprint is **fully contained** in the occlusion mask.

Fail-open: if containment cannot be evaluated, **include** the element.

2D geometry is **never occluded**.

---

## 6. Linked Models

* Inclusion governed by:

  * Host view visibility
  * Clip volume
* Geometry rules identical to host.
* No implicit suppression.

---

## 7. CSV Outputs (Authoritative)

### 7.1 Governance

* CSVs are the **only authoritative outputs**.
* Headers are append-only.
* New CSV required if row grain changes.
* Every CSV includes:

  * `Date`
  * `RunId`
  * `ExporterVersion`
  * `ConfigHash`

### 7.2 Required Per-View Totals (Must reconcile)

For each view:

```
TotalCells
Empty
ModelOnly
AnnoOnly
Overlap
```

Constraint:

```
TotalCells = Empty + ModelOnly + AnnoOnly + Overlap
```

If violated → **hard correctness failure**.

---

## 8. Validation Checklist (Git-friendly)

A run is **correct** iff all are true:

* [ ] No unsupported views produce output rows
* [ ] Every cell has exactly one occupancy state
* [ ] No 3D interior fill occurs
* [ ] Only Filled Regions fill interiors
* [ ] Occlusion only removes fully contained 3D footprints
* [ ] Linked model behavior matches host rules
* [ ] Per-view totals reconcile exactly
* [ ] CSV headers unchanged or appended only

---

## 9. Change Classification Rule (Enforcement)

* If this baseline still passes → **PATCH**
* If any clause fails → **BEHAVIOR CHANGE**

  * Instructions must be updated *before* code is merged.
