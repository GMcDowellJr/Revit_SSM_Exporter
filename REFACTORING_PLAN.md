# Refactoring Plan — Violation-Driven Convergence

**Status:** ACTIVE
**Authority:** `VIOLATION_CENSUS.md` (gating document), `docs/correctness_contract.md` (specification)
**Purpose:**
Phased resolution of contract violations in dependency order to minimize baseline churn and maintain interpretable deltas.

---

## Strategic Principle

**Minimize baseline churn and avoid entanglement.**

Do not change 5 things at once because they're coupled. Each phase isolates a single dominant cause for baseline changes, making deltas interpretable and reviewable.

---

## Dependency Chain

```
Cache removal → UVW semantics → Strategy switch unification → 3D boundary-only → Occlusion model rewrite → audits/unknowns
```

Each phase must complete before the next begins. Violations are resolved in an order that prevents cascading re-work.

---

## Phase 1 — Remove Global State (VC-2.4)

**Fix:** Prohibited caching (`_extractor_cache`, any module/function caches beyond view-skipping).

**Why First:**
- Changes *when* work happens, but shouldn't change semantics if done carefully
- Removes major source of "why did this view behave differently this run?" confusion
- Reduces chance that later semantic fixes appear inconsistent due to cached state

**Expected Delta:**
Mostly runtime + debug counters; ideally **no CSV/PNG deltas** (but you'll detect if cache was masking nondeterminism).

**Targets:**
- `processing/projection.py:596–655` — `_extractor_cache` keyed by scale/grid size
- `ssm_exporter_main.py:3142–3146` — cache lifecycle management

**Acceptance:**
- All extractor/projection caches removed
- View-skipping cache retained (allowed by contract)
- Golden baseline delta review confirms no semantic changes

---

## Phase 2 — Lock UVW Definition (VC-2.3)

**Fix:** W=0 at crop UV plane; eliminate ViewRange/cut-plane as depth reference.

**Why Now:**
- Everything downstream (occlusion, culling, linked geometry depth placement) depends on W
- If you change occlusion first while W is wrong, you'll get deltas you can't interpret

**Expected Delta:**
Potentially **widespread** (depth-related ordering/culling/visibility), but it becomes the foundation for all later changes.

**Targets:**
- `processing/projection.py:366–408` — uses ViewRange/cut plane as W=0 reference for plan/RCP views

**Acceptance:**
- UV plane derived strictly from crop box
- W = 0 at crop UV plane, increases inward
- No ViewRange or cut plane references in depth calculation
- Golden baseline delta review confirms new depth model is consistent

---

## Phase 3 — Make Region Classification the Only Strategy Switch (VC-4.1)

**Fix:** Remove category/view-type branching (e.g., `is_floor_like`) and ensure decisions flow only from TINY/LINEAR/AREAL rules.

**Why Before Occlusion and Boundary-Only:**
- You want a single deterministic switchboard before you change the behaviors behind each switch
- Otherwise you can't tell whether a delta came from "classification" or "occlusion"

**Expected Delta:**
**Moderate**; mostly elements that previously got special handling.

**Targets:**
- `ssm_exporter_main.py:1581, 1601–1609` — category-based `is_floor_like` alters rasterization behavior

**Acceptance:**
- All strategy selection driven by grid-based classification (TINY/LINEAR/AREAL)
- No category or view-type influence on rasterization
- Golden baseline delta review confirms classification-driven behavior changes only

---

## Phase 4 — Bring 3D Contribution Into Compliance (VC-3.1)

**Fix:** 3D is boundary-only; no parity-filled interior for 3D occupancy.

**Why Before Occlusion Rewrite:**
- Occlusion mask semantics rely on AREAL regions, but occupancy semantics need to be correct first
- If you rewrite occlusion while still filling 3D interiors, you'll conflate two independent model changes

**Expected Delta:**
**High** in occupancy grids/CSVs (less "solid fill" in 3D), but it will be interpretable because Phase 2/3 are already locked.

**Targets:**
- `ssm_exporter_main.py:1601–1609` — parity-filled rasterization applied to 3D via `is_floor_like`/`is_likely_areal`

**Acceptance:**
- 3D contributes boundary cells only
- No interior fill for 3D
- Bounding-box projection allowed only for TINY/LINEAR
- Golden baseline delta review confirms boundary-only occupancy for 3D

---

## Phase 5 — Replace Depth-Buffer Occlusion with Mask + AABB Containment (VC-5.1, VC-5.2)

**Fix:**
- Build occlusion mask only from parity-filled interiors of AREAL 3D
- Cull only when element UV-AABB fully contained in mask
- Fail-open on uncertainty

**Why Last Among Core FAILs:**
- This is the **biggest behavior model swap**
- Doing it last means earlier deltas are already "settled", and remaining changes can be attributed to occlusion only

**Expected Delta:**
**High** (culling and performance variability). This is also where you'll see the largest runtime shifts (some views faster, some slower).

**Targets:**
- `ssm_exporter_main.py:1651–1663` — uses depth buffer (`w_nearest`) instead of mask accumulation
- `processing/projection.py:464–466` — depth-based bbox skipping logic

**Acceptance:**
- Occlusion mask built only from parity-filled interiors of AREAL 3D
- LINEAR/TINY do not write occlusion
- 2D never occluded
- Culling based on UV-AABB containment in mask
- Fail-open logic implemented
- Golden baseline delta review confirms occlusion-driven changes only

---

## Phase 6 — Audit/Repair UNKNOWNs (Gated by Risk)

These can be interleaved earlier if they block interpretation, but the clean order is:

### 6A — VC-7.2: Totals Reconciliation Hard-Fail (Audit First, Then Enforce)

**Why First:**
It's your correctness tripwire. If it's wrong, every baseline comparison is suspect.

**Target:**
`TotalCells = Empty + ModelOnly + AnnoOnly + Overlap`

**Acceptance:**
- Validation logic confirmed in code
- Hard failure on reconciliation mismatch
- All existing golden baselines pass reconciliation

### 6B — VC-2.2: Grid Definition & Membership

**Why Careful:**
If grid semantics are wrong, everything is wrong — but don't change it casually because it explodes baselines.

**Acceptance:**
- Cell size defined in paper inches, scaled by view scale
- No rounding until final rasterization
- Cell membership requires center ∈ XY domain and ∈ clip volume

### 6C — VC-2.5: Occupancy Exclusivity

**Why Later:**
Often a downstream effect; fix once the occupancy logic has settled.

**Acceptance:**
- Each cell is exactly one of: `{0=3D-only, 1=2D-only, 2=2D-over-3D}`
- Enforcement in rasterization and aggregation paths

### 6D — VC-3.2: 2D Whitelist

**Why Careful:**
Needs careful scoping; can create broad deltas.

**Acceptance:**
- Only explicitly whitelisted 2D elements contribute
- 2D collectors and filters audited against whitelist

### 6E — VC-6.1: Linked Model Parity

**Why Last:**
Do after UVW + occlusion are stable, otherwise you get "double deltas" (link transform + depth semantics + occlusion).

**Acceptance:**
- Linked geometry obeys identical rules as host geometry
- Transform application and clip-volume parity verified

---

## PR Requirements for Each Phase

Every BEHAVIOR CHANGE PR must:

1. **Cite** the VIOLATION_CENSUS.md items being addressed
2. **Declare** expected output deltas before code changes
3. **Scope** the change to a single phase
4. **Include** golden baseline comparison showing predicted vs actual deltas
5. **Document** any deviations from expected behavior

---

## Status Tracking

| Phase | Status | PR | Notes |
|-------|--------|-----|-------|
| Phase 1: Cache Removal | NOT STARTED | — | — |
| Phase 2: UVW Semantics | NOT STARTED | — | Blocked by Phase 1 |
| Phase 3: Classification | NOT STARTED | — | Blocked by Phase 2 |
| Phase 4: 3D Boundary-Only | NOT STARTED | — | Blocked by Phase 3 |
| Phase 5: Occlusion Rewrite | NOT STARTED | — | Blocked by Phase 4 |
| Phase 6A: Totals Reconciliation | NOT STARTED | — | Can run earlier |
| Phase 6B: Grid Definition | NOT STARTED | — | Risk-gated |
| Phase 6C: Occupancy Exclusivity | NOT STARTED | — | Risk-gated |
| Phase 6D: 2D Whitelist | NOT STARTED | — | Risk-gated |
| Phase 6E: Linked Model Parity | NOT STARTED | — | Blocked by Phase 5 |

---

## Convergence Checkpoint

At the completion of all phases:

- All FAIL violations resolved
- All UNKNOWN violations audited and addressed or explicitly scoped
- Golden baselines updated to reflect new contract-compliant behavior
- Runtime characteristics documented (performance shifts expected in Phase 5)

This repository will then be fully contract-compliant.
