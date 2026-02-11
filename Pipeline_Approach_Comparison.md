# Exporter Pipeline Comparison: Proposed Contract-Aligned Flow vs Current VOP Interwoven Code

## What the current code is doing conceptually

The current implementation is an **interwoven render pipeline** that prioritizes performance by blending classification, geometry extraction, depth ordering, occlusion, and raster writes into one model pass.

Conceptually, it does this:

1. Resolves per-view mode (`MODEL_AND_ANNOTATION`, `ANNOTATION_ONLY`, or `REJECTED`) and only runs model processing in model-capable views.
2. Collects broad-phase elements once per view using a Revit view-scoped collector and category policy.
3. Expands host elements with linked/imported proxies.
4. Sorts expanded elements front-to-back by estimated depth.
5. Processes each element in one integrated loop:
   - computes envelope/depth hints,
   - classifies footprint as `TINY`, `LINEAR`, or `AREAL`,
   - attempts silhouette/areal extraction,
   - falls back to proxy geometry when needed,
   - writes raster/occlusion channels directly.
6. Runs annotation pass separately and derives `anno_over_model`.
7. Exports outputs and diagnostics.

This means extraction/projection/raster policy decisions are intentionally interwoven for throughput, not staged as strict contracts.

## Reusable with minimal effort

These areas are already close to the pseudocode contracts and can be reused/adapted quickly:

### 1) View capability split (model vs annotation-only)
- `resolve_view_mode` already forces drafting/legend views to annotation-only and rejects template/non-model-capable views.
- `process_document_views` branches pipeline execution by that mode.

**Reuse value:** High. This can serve as your Stage 2 view-kind/depth-mode gate with minimal restructuring.

### 2) View basis + bounds/crop primitives
- `make_view_basis` and `resolve_view_w_volume` already expose a view-local UVW basis and depth volume semantics.
- `resolve_view_bounds` exists for raster grid bounds/cell-size calculations.

**Reuse value:** High. These are close to `resolve_view_basis`, `resolve_crop_volume`, and `make_raster_spec` concepts.

### 3) Candidate collection infrastructure
- `collect_view_elements` already does broad-phase collection (single view-scoped collector, optional coarse filters, allowlist policy).
- `expand_host_link_import_model_elements` already adds link/import wrappers with transform + bbox provenance.

**Reuse value:** High. This is nearly your Stage 1 collector + envelope acquisition.

### 4) Front-to-back ordering and depth hints
- `sort_front_to_back` and bbox-based depth estimation are already used before render.

**Reuse value:** Medium-high. Can be adapted for your Stage 7 ordering in true-depth mode.

### 5) Existing cache building blocks
- `core/cache.py` has bounded LRU machinery.
- `core/element_cache.py` has cross-view fingerprint caching (bbox-based signatures).
- `process_document_views` has root-cache/view-signature pathways for skipping unchanged view work.

**Reuse value:** Medium. Good scaffolding exists, but not yet your explicit Tier-1 (world primitive) and Tier-2 (projected primitive) cache contracts.

### 6) Annotation/model non-occlusion layering intent
- The pipeline documents and implements the rule that 2D annotation does not occlude model geometry, with annotation rasterized in a separate pass.

**Reuse value:** High for B1 semantics, with some refactoring needed to make B1/B2 tier objects explicit.

## Needs substantial new implementation (essentially from scratch)

These are the largest gaps vs your pseudocode and would require real new architecture, not just small edits:

### A) Strict non-interwoven stage boundaries with explicit artifacts
Your pseudocode expects explicit handoff objects per stage (`PrimitiveWorld`, `PrimitiveView`, `visible_by_elem`, tiered outputs). Current code mostly computes and consumes these concerns inside one rendering loop.

**Why this is near-scratch:** Requires introducing stable intermediate data contracts and refactoring loop internals around them.

### B) Tier-1/Tier-2 cache contract separation
Your approach defines:
- Tier-1 cache keyed by extraction-affecting inputs, storing **world primitives** and geometry fingerprint.
- Tier-2 cache keyed by geometry fingerprint + view basis/crop/raster settings, storing **projected/clipped primitives**.

Current code has generic LRU and element fingerprint caches, plus view-level cache skip, but no formal two-tier primitive cache split.

**Why this is near-scratch:** New key schemas, cache records, and invalidation boundaries must be designed/implemented.

### C) Authoritative Stage-5 visibility gating pass over already-projected primitives
Your contract requires a dedicated visibility pass that must enforce category/subcategory/filter/manual-hide without re-extraction.

Current `is_element_visible_in_view` is permissive (always `True`) and relies mostly on collector behavior, not an explicit post-projection gating pass.

**Why this is near-scratch:** Requires introducing a new authoritative filter pass and richer visibility metadata checks.

### D) Explicit B1/B2 model-hosted-2D split with allowlisted occluder upgrade path
Current semantics are AREAL/TINY/LINEAR-centric. Your approach needs explicit context/tier classification:
- B1 view-context 2D (never occludes),
- B2 model-hosted 2D (default non-occluding),
- optional B2->A upgrade by allowlist + valid depth.

**Why this is near-scratch:** Current class/strategy/confidence model is different from this tier model.

### E) Primitive context isolation in cache policy
Your contract prohibits cross-view Tier-1 caching of `context=view` primitives.

Current code does not expose this explicit world-primitive context segregation in cache APIs.

**Why this is near-scratch:** Needs new primitive schema + cache admission rules.

## What current code does that your proposed approach does **not** explicitly cover

These are significant behaviors in the existing system that your pseudocode does not currently encode:

1. **UV size-classification strategy (`TINY`, `LINEAR`, `AREAL`) drives cost control and rendering strategy.**
2. **Safe early-out skipping using tile/depth occupancy heuristics** to avoid expensive work for elements likely fully occluded.
3. **Proxy ink semantics** (`model_proxy_key` / proxy presence) for lightweight but counted model presence, separate from precise model edge channels.
4. **Confidence/strategy fallback accounting** (e.g., silhouette success/fallback) and strategy diagnostics export.
5. **Root/view cache short-circuit of full view processing** based on view signatures and cached metrics/payloads.
6. **Detailed source handling across host/link/import wrappers** with transform/bbox provenance and diagnostics.

If you adopt the new staged contract, you likely want to preserve these behaviors explicitly as optional policies so performance characteristics do not regress.

## Practical migration path (minimal-risk order)

1. **Introduce stage data contracts first** (world primitives, projected primitives, visibility-gated primitives) while still calling existing internals.
2. **Implement Tier-2 cache first** around existing projection/clip outputs (lower API blast radius).
3. **Add dedicated Stage-5 visibility gate** and wire diagnostics.
4. **Implement explicit B1/B2 split and allowlist upgrade path** while preserving current annotation non-occlusion rule.
5. **Then implement Tier-1 world primitive cache + context isolation**.
6. **Finally de-interweave model loop internals** once parity tests pass.

This sequence maximizes reuse and keeps behavior drift measurable.
