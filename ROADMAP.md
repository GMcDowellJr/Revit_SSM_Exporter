# ROADMAP

This roadmap is derived from explicit stubs/TODOs and contract gaps present in the current repository state.

## P0

### 1) Gate or implement proxy `edges` mode
- **Type:** Bug/Defect
- **Description:** `proxy_mask_mode="edges"` routes into `_stamp_proxy_edges`, which is a stub (`pass`).
- **Rationale:** This exposes a selectable mode that does not perform advertised behavior.
- **Evidence:** `vop_interwoven.pipeline._stamp_proxy_edges`.【F:vop_interwoven/pipeline.py†L3333-L3347】
- **Acceptance criteria:**
  - Either: implement edge stamping with deterministic tests; or
  - Explicitly reject/disable `edges` mode at config validation with a clear diagnostic message.

### 2) Contract contradiction requiring code decision: AREAL fallback occlusion semantics
- **Type:** Debt/Clarification
- **Description:** The top-level semantic comment states AREAL fallback to OBB/AABB still writes occlusion, while nearby principles also say only high-confidence AREAL contributes to occlusion.
- **Rationale:** This is an architecture-contract contradiction that can affect correctness/performance decisions and should be settled explicitly.
- **Evidence:** PR8 semantic block in `pipeline.py` (classification/occlusion/fallback statements).【F:vop_interwoven/pipeline.py†L33-L42】【F:vop_interwoven/pipeline.py†L70-L77】
- **Acceptance criteria:**
  - Decide and document one canonical rule in code comments/docs.
  - Add regression tests that assert chosen behavior under extraction fallback paths.

## P1

### 3) Complete diagnostics wiring debt in exception handlers
- **Type:** Validation/Tooling
- **Description:** Multiple handlers still include placeholders such as `TODO: Add diagnostics when diag becomes available`.
- **Rationale:** Diagnostics contract says diagnostics are always enabled and errors should be structured.
- **Evidence:** `entry_dynamo.py`, `csv_export.py`, `root_cache.py`, `streaming.py` placeholders; contract in `docs/diagnostics_contract.md`.【F:vop_interwoven/entry_dynamo.py†L399-L402】【F:vop_interwoven/csv_export.py†L34-L37】【F:vop_interwoven/root_cache.py†L186-L214】【F:vop_interwoven/streaming.py†L742-L744】【F:vop_interwoven/docs/diagnostics_contract.md†L1-L18】
- **Acceptance criteria:**
  - No TODO placeholders about missing diagnostics in actively used runtime paths.
  - Structured `diag.error/warn/info` coverage for recoverable exceptions in entry/export/cache streaming paths.

### 4) Remove or finalize dormant per-view disk cache path
- **Type:** Debt/Clarification
- **Description:** `process_document_views` defines per-view cache plumbing but force-disables it via `view_cache_enabled = False`.
- **Rationale:** Dormant logic increases maintenance burden and can mislead readers about active cache behavior.
- **Evidence:** cache config capture plus hard disable in pipeline.。【F:vop_interwoven/pipeline.py†L571-L594】
- **Acceptance criteria:**
  - Either remove dormant branches and related dead helpers; or
  - Introduce explicit feature flag path with tests proving enabled behavior.

### 5) Implement explicit visibility-gating pass or narrow the contract
- **Type:** Feature
- **Description:** `is_element_visible_in_view` is permissive and always returns `True`.
- **Rationale:** Current behavior relies primarily on collector semantics and does not provide explicit post-collection visibility gate.
- **Evidence:** `is_element_visible_in_view` implementation.。【F:vop_interwoven/revit/collection.py†L324-L345】
- **Acceptance criteria:**
  - Either implement explicit checks (hidden/category/template overrides) with tests; or
  - Update docs/contracts to state collector-only visibility policy as intentional and final.

## P2

### 6) Streaming signature-first TODO cleanup
- **Type:** Debt/Clarification
- **Description:** Streaming scaffolding still contains TODO comments and commented code around signature-first cache flow.
- **Rationale:** Creates uncertainty about intended streaming cache flow ownership.
- **Evidence:** TODO/comment block in `process_document_views_streaming`.【F:vop_interwoven/streaming.py†L49-L67】
- **Acceptance criteria:**
  - Remove stale commented TODO block and align function narrative with actual implementation.

### 7) Align historical documentation footprint with current architecture doc
- **Type:** Debt/Clarification
- **Description:** Multiple top-level and module-level docs were written as plans/reviews and can be mistaken for current truth.
- **Rationale:** Reduces reader confusion and maintenance drift by making `CURRENT_ARCHITECTURE.md` canonical.
- **Evidence:** historical plan/review docs now bannered as superseded.。【F:vop_interwoven/IMPLEMENTATION_PLAN.md†L1-L11】【F:vop_interwoven/PHASE7_CSV_EXPORT_PLAN.md†L1-L11】【F:Pipeline_Approach_Comparison.md†L1-L9】【F:Repository_Operational_Review.md†L1-L9】
- **Acceptance criteria:**
  - All historical docs include explicit superseded banner and pointer to canonical current-state doc.
