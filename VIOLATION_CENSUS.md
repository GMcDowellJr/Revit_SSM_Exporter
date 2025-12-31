# Violation Census — Contract-Gated Convergence

**Status:** ACTIVE  
**Authority:** `docs/correctness_contract.md` (authoritative)  
**Purpose:**  
This document enumerates all known and suspected violations of the correctness contract in executable code paths.  
Each item here gates BEHAVIOR CHANGE pull requests. No BEHAVIOR CHANGE may proceed without explicitly addressing (fixing or scoping) the relevant violations listed below.

---

## How This Document Is Used

- Each BEHAVIOR CHANGE PR MUST:
  - Cite one or more **contract clauses** listed here
  - Reference the corresponding **Violation ID**
  - Declare expected output deltas **before code changes**
- Items marked **FAIL** must be fixed or explicitly deferred.
- Items marked **UNKNOWN** must be audited before dependent behavior changes stack.
- Items marked **PASS** are included for completeness and regression detection.

---

## Legend

- **Status**
  - PASS — Verified compliant
  - FAIL — Verified non-compliant
  - UNKNOWN — Not yet fully audited
- **Gate**
  - YES — Blocks BEHAVIOR CHANGE PRs
  - NO — Informational only

---

## 2. Locked Invariants

### VC-2.1 — Supported View Scope
- **Contract Clause:** §2.1
- **Requirement:**  
  Unsupported views produce **zero output rows** (not partial exports).
- **Evidence:**  
  Supported view checks exist in `geometry/grid.py`.
- **Status:** PASS
- **Gate:** NO

---

### VC-2.2 — Grid Definition & Membership
- **Contract Clause:** §2.2
- **Requirement:**  
  - Cell size defined in **paper inches**, scaled by view scale  
  - No rounding until final rasterization  
  - Cell membership requires center ∈ XY domain and ∈ clip volume
- **Evidence:**  
  Grid sizing, scale conversion, and membership logic not yet audited end-to-end.
- **Status:** UNKNOWN
- **Gate:** YES

---

### VC-2.3 — UVW Depth Semantics
- **Contract Clause:** §2.3
- **Requirement:**  
  - UV plane derived strictly from **crop box**
  - **W = 0 at crop UV plane**
  - W increases inward, clamped to `[0, ViewDepth]`
  - **No use of ViewRange or cut plane**
- **Evidence (Violation):**
  - `processing/projection.py:366–408`  
    Uses ViewRange / cut plane as W=0 reference for plan/RCP views.
- **Status:** FAIL
- **Gate:** YES

---

### VC-2.4 — Caching Policy
- **Contract Clause:** §2.4
- **Requirement:**  
  - Allowed: view-skipping cache only  
  - Disallowed: extractor caches, projection caches, cross-view reuse
- **Evidence (Violation):**
  - `processing/projection.py:596–655`  
    `_extractor_cache` keyed by scale/grid size  
  - `ssm_exporter_main.py:3142–3146` confirms cross-run cache lifecycle
- **Status:** FAIL
- **Gate:** YES

---

### VC-2.5 — Occupancy State Exclusivity
- **Contract Clause:** §2.5
- **Requirement:**  
  Each cell must be exactly one of:
  `{0=3D-only, 1=2D-only, 2=2D-over-3D}`
- **Evidence:**  
  Rasterization and aggregation path not yet audited for exclusivity enforcement.
- **Status:** UNKNOWN
- **Gate:** YES

---

## 3. Geometry Contribution Rules

### VC-3.1 — 3D Geometry Is Boundary-Only
- **Contract Clause:** §3.1
- **Requirement:**  
  - 3D contributes **boundary cells only**
  - **No interior fill** for 3D
  - Bounding-box projection allowed only for **TINY / LINEAR**
- **Evidence (Violation):**
  - `ssm_exporter_main.py:1601–1609`  
    Parity-filled rasterization applied to 3D via `is_floor_like` / `is_likely_areal`
- **Status:** FAIL
- **Gate:** YES

---

### VC-3.2 — 2D Geometry Whitelist
- **Contract Clause:** §3.2
- **Requirement:**  
  Only explicitly whitelisted 2D elements contribute.
- **Evidence:**  
  2D collectors and filters not fully audited against whitelist.
- **Status:** UNKNOWN
- **Gate:** YES

---

## 4. Region Classification

### VC-4.1 — Deterministic Region Classification
- **Contract Clause:** §4
- **Requirement:**  
  Strategy selection driven **only** by grid-based classification:
  - TINY, LINEAR, AREAL
  - No category or view-type influence
- **Evidence (Violation):**
  - `ssm_exporter_main.py:1581, 1601–1609`  
    Category-based `is_floor_like` alters rasterization behavior.
- **Status:** FAIL
- **Gate:** YES

---

## 5. Occlusion Model (3D Only)

### VC-5.1 — Occlusion Mask Construction
- **Contract Clause:** §5.1
- **Requirement:**  
  - Occlusion mask built **only** from parity-filled interiors of **AREAL 3D**
  - LINEAR / TINY do not write occlusion
  - 2D never occluded
- **Evidence (Violation):**
  - `ssm_exporter_main.py:1651–1663`  
    Uses depth buffer (`w_nearest`) instead of mask accumulation.
- **Status:** FAIL
- **Gate:** YES

---

### VC-5.2 — Occlusion Culling Rule
- **Contract Clause:** §5.2
- **Requirement:**  
  A 3D element is excluded **iff** its **UV-aligned AABB** is fully contained in the occlusion mask.  
  Fail-open if containment is uncertain.
- **Evidence (Violation):**
  - `processing/projection.py:464–466`  
    Depth-based bbox skipping logic
- **Status:** FAIL
- **Gate:** YES

---

## 6. Linked Models

### VC-6.1 — Linked Geometry Parity
- **Contract Clause:** §6
- **Requirement:**  
  Linked geometry obeys **identical rules** as host geometry.
- **Evidence:**  
  Transform application and clip-volume parity not fully audited.
- **Status:** UNKNOWN
- **Gate:** YES

---

## 7. CSV Outputs (Authoritative)

### VC-7.1 — CSV Governance
- **Contract Clause:** §7.1
- **Requirement:**  
  - CSVs are authoritative
  - Headers append-only
  - Required fields always present
- **Evidence:**  
  Export header and schema enforcement not yet audited.
- **Status:** UNKNOWN
- **Gate:** YES

---

### VC-7.2 — Per-View Totals Reconciliation
- **Contract Clause:** §7.2
- **Requirement:**  
  `TotalCells = Empty + ModelOnly + AnnoOnly + Overlap`  
  Violation is a **hard correctness failure**.
- **Evidence:**  
  Validation logic not yet confirmed.
- **Status:** UNKNOWN
- **Gate:** YES

---

## Gating Summary

### Known FAILS (Must Be Addressed First)
- VC-2.3 UVW depth semantics
- VC-2.4 Caching policy
- VC-3.1 3D boundary-only rule
- VC-4.1 Deterministic region classification
- VC-5.1 Occlusion mask construction
- VC-5.2 Occlusion culling rule

### UNKNOWNs Requiring Audit
- VC-2.2 Grid definition
- VC-2.5 Occupancy exclusivity
- VC-3.2 2D whitelist
- VC-6.1 Linked model parity
- VC-7.1–7.2 CSV governance and reconciliation

---

## Convergence Principle

This repository is no longer in design discovery.  
All remaining work is **contract-driven convergence**.

Each BEHAVIOR CHANGE:
- Must cite this census
- Must narrow the FAIL surface
- Must declare its baseline delta up front