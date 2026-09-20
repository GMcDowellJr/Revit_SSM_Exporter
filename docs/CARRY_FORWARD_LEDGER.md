# Carry-Forward Ledger — geometry/coarse-grid path → color-ID + vector annotations

Status: **SWEPT.** Every row below has been checked against the tree at
`d05038a0656d8389915e70a45444bb87ca024c0e` (`d05038a`). The seed was populated
from project notes only; the sweep that "Open" demanded has now run, and what it
found is recorded in two places: corrected `State` cells on the seed rows, and a
new **S** section for artifacts that are in the code and were in nobody's notes.

Completeness is **bounded, not proven** — see "What the sweep could and could
not see" at the bottom for the method and its holes.

Baseline tag/release exists, so a NO FORWARD ROLE disposition is recoverable.

## What this is for

The substrate changed: under geometry extraction the pipeline *derived* occupancy
(classify → dispatch → compute occlusion → fill). Under color-ID, Revit rasterizes
and resolves hidden-line, and the pipeline *reads*. Most old abstractions existed to
serve derivation. This ledger forces each one to be dispositioned rather than to
survive by not being asked about.

## The test

An artifact carries forward only if:

    (a) it has a consumer that survives the substrate change, AND
    (b) it cannot be re-derived from what capture persists

Anything re-derivable is not carried forward — it is recomputed at the consumer.

Capture persists two primitives and no more:

    1. frame + pixels   (B, A, model_crop_offset_uv, fpp, dims; color-ID TIFF)
    2. vector records   (channel + category + provenance, frame coordinates)

> **The sweep falsified "and no more."** The sidecar
> `export_color_id_buffer_view` writes carries two further families beside the
> frame: `color_assignment_map` (palette RGB → element id) and `near_face_w_map`
> (per-element `bbox_corners_uv` + `near_face_w` + `category`). The second is a
> **depth** record, persisted per element, on the path whose premise is that
> Revit resolves hidden-line for us. It is not redundant — `resolve_category` in
> `tools/link_identity_resolver.py` ranks blob candidates by projected-footprint
> coverage and tie-breaks on `near_face_w` — but the test above was being applied
> against a two-primitive capture that does not exist. Rows S1 and S2 exist
> because of this, and the stated premise should be corrected rather than the
> rows being treated as exceptions to it.

## Two columns, not one

`Disposition` is a **decision record**: dated, immutable, superseded forward-only by
a new dated line beneath it. Never edited in place.

`State` is **status**: current, replaceable, rewritten freely.

Keeping them in one mutable table either rots the ledger or destroys the reasoning.

## Disposition vocabulary

`Disposition` is a verdict about **forward role**, not about deletion. Code with
NO FORWARD ROLE may stay in the tree indefinitely. Deletion is a separate,
evidence-gated decision (deferred 2026-09-18).

    CARRY           survives unchanged — it is substrate, not method
    CARRY-RESOURCED survives but its input/basis must be re-sourced
    RE-DERIVE       not persisted; recomputed at the consumer
    ARBITER-ONLY    no forward role, retained deliberately as a second method
    NO-ROLE         no consumer under color-ID
    UNDECIDED       raise, do not default

## How to cite a site in this ledger

By the **words in the code**, not by line number. A line number cannot fail, so
it decays into confident misdirection (CLAUDE.md, "the citation that rots"), and
the sweep caught one doing exactly that — see row X1. Every `site` cell below is
a file plus a string that `grep` will find in it.

---

## The structural fact that most State cells depend on

`pipeline.py`'s per-view loop, under `enable_color_id_buffer_stage_a`, takes the
color-ID branch (`Stage A replaces the occlusion/silhouette in-memory model
pass`), appends its result and **`continue`s**. Everything the loop does after
that point is therefore not reached on a Stage A view:

- `render_model_front_to_back` — the whole geometry model pass, and the only
  writer of `geom_silhouette_ms` / `geom_obb_ms` / `classify_ms`. It is one
  function spanning roughly a third of `pipeline.py`, and N1's `_classify_uv_rect`
  and N3's `_scene_occ_rects` are **nested inside it**, so they are not merely
  "after" the branch — they are unreachable without the call
- the annotation rasterization pass
- `_compute_manifest_metrics_payload` — and with it `scan_final_state_totals`,
  so every locked `Cells_*`, `ExtFinalCells_*`, `AnnoFinalCells_*` and
  `ModelClassCells_*` total
- the legacy 4-way projection and the per-view VOP/occlusion CSV rows

So a row whose State says "live" is making a claim about **which run**. Where
that matters the State cells below now say `live (arbiter path only)` rather than
`live`: the artifact is produced when the geometry pipeline runs, and is
produced by nothing at all when Stage A runs. This is not a defect — it is the
substrate change, visible in the control flow — but it means most of the R, N
and A rows are already unproduced under color-ID rather than awaiting a decision
to stop producing them.

---

## Ledger

### C — carries forward

| id | artifact | site | why it survives | disposition | state |
|----|----------|------|-----------------|-------------|-------|
| C1 | Frame reconciliation (B.min origin, `model_crop_offset_uv`, A ⊆ B) | `color_id_buffer.py` `compute_model_crop` / `"model_crop_offset_uv"`; `tools/decode_stage_a_color_id.py` | it *is* primitive 1 | CARRY (2026-09-19) | live, and stronger than the note claimed: `compute_model_crop` **intersects** the candidate crop with `bounds_xy` on all four sides rather than trusting A ⊆ B, because `view_basis.py`'s post-annotation cap envelope (`Apply cap envelope to final annotation-expanded bounds`) can shrink B back below A. Pure Python, no Revit API, directly unit-testable. Its own citation of that envelope still resolves correctly (see X1 for the one that does not) |
| C2 | Clamp / cap / D8 decode math | one derivation: `tools/clamp_pad_geometry.py` (`ONE derivation of Revit ExportImage's aspect-clamp frame geometry`); cap in `vop_interwoven/resolution_contract.py` (`cap_axes`) | models Revit's behaviour, not ours | CARRY (2026-09-19) | **not** duplicated 5× any more — the seed's State was stale. PR #200 extracted it; `decode_stage_a_color_id.py` (as `_shared_clamp_pad_geometry`), `colorid_to_occupancy.py`, `link_identity_resolver.py` and `tools/notes/g2_bbox_excursion_report.py` all delegate, and `analyze_stage_a_probe.py`'s `_frame_geometry()` is a documented deliberate non-caller (it refuses rather than reporting a pad). Bound at **5** test files, not 3: `test_invariants_clamp_pad`, `test_decode_stage_a_clamp_pad`, `test_effective_export_dpi`, `test_effective_dpi_call_site`, `test_uv_pixel_round_trip`. The cap has one production caller and `tests/dynamo/resolution_contract.py` re-exports rather than copying it, with `test_stage_a_export_dimension_contract` asserting `probe_contract.cap_axes is cap_axes` |
| C3 | Link handling: per-category color via `ParameterFilterElement` + bbox disambiguation | `color_id_buffer.py` `_collect_link_category_filters` / `_apply_link_category_filters`; `tools/link_identity_resolver.py` `resolve_category` | a color-ID-path mechanism, not a geometry-path one | CARRY (2026-09-19) | live. Verified end to end: one `ParameterFilterElement` per category paints LINK content, and the resolver disambiguates blobs by `coverage_fraction` over a projected pixel bbox, tie-broken on `near_face_w`. **Its bbox input is produced by the geometry path** — see S1, which the seed had no row for |
| C4 | Export resolution / fpp | `color_id_buffer.py` (`paper_width_in = (float(raster.W) * float(raster.cell_size_ft) * 12.0)`), `resolution_contract.py` | the one irreducible capture-time quantization | CARRY-RESOURCED (2026-09-19) — basis must move off the analysis grid | confirmed, and the contract already argues against the basis production still uses: `effective_export_dpi`'s docstring states `THE DENOMINATOR IS THE RENDERED CROP, not the grid.` — going on to say `raster.W * cell_size_ft` is the extent rounded *up* to whole cells and overstates what the TIFF spans — while the pixel-size request upstream is still sized from exactly that product. The two coexist in one module: the decode-side quantity has been re-sourced, the capture-side request has not |

### R — re-derive at the consumer

| id | artifact | site | what replaces it | disposition | state |
|----|----------|------|------------------|-------------|-------|
| R1 | Coarse analysis grid at capture (384×288 cap, `cell_size_paper_in = 0.125`) | `config.py` (`Grid size cap based on max_sheet_size / cell_size_paper_in (Arch E: 384x288 @ 1/8")`) | grid declared at analysis time | RE-DERIVE (2026-09-19) | live, and C4 is coupled to it. The coupling also reaches **downstream of capture**: with no geometry run to align to, `tools/colorid_to_occupancy.py` recovers W from `resolution.backoff_floor_px` and marks the view `grid_basis="assumed"`. So the capture-time grid is currently load-bearing for a consumer that is supposed to declare its own |
| R2 | Cell taxonomy (`Cells_ModelOnly` … `All3`, `ExtFinalCells_*`, `AnnoFinalCells_*`) | `metrics/final_state_scanner.py` `scan_final_state_totals` | `(channel, category)` as tagged data fields | RE-DERIVE (2026-09-19) | live (arbiter path only) — no producer on a Stage A view. Also **wider than the seed recorded**: the same scan emits `TotalCells`, `AnnoPresentFinal` and a `ModelClassCells_*` family, and all of them are **manifest-locked** in `metrics/manifest/metrics_manifest.v1.json`. `ModelClassCells_*` is already keyed by *category* (`WALL`/`DOOR`/`STAIR`/`COLUMN`/`LIGHT`/`OTHER`, resolved from `element_meta`), i.e. it is closer to R2's own replacement shape than the seed's "what replaces it" implies — but it is keyed off `model_edge_key`/`model_proxy_key`, which the color-ID path never populates. See S3: re-deriving R2 means re-deriving a locked schema, not just a column set |
| R3 | 3-way projection ModelOnly / Overlap / AnnoOnly | `csv_export.py` `_normalize_locked_metrics_for_legacy_csv` | declared mapping at the comparison step only | NO-ROLE as storage (2026-09-19) | live (arbiter path only); lossy exactly as claimed, and the merge is explicit in the code: `ModelOnly ← {Cells_ModelOnly, Cells_ModelExt, Cells_ExtOnly}` and `Overlap ← {Cells_ModelAnno, Cells_AnnoExt, Cells_All3}`, 8 buckets onto 4, with `Ext_Cells_*` retained as the overlay that makes host-vs-external recoverable |

### N — no forward role

| id | artifact | site | what it existed to do | disposition | state |
|----|----------|------|-----------------------|-------------|-------|
| N1 | TINY/LINEAR/AREAL classifier ×3 | `pipeline.py` `_classify_uv_rect` (`Local, explicit classification to avoid dependency on classify_by_uv signature`); `core/geometry.py` `classify_by_uv`; `core/geometry.py` `classify_by_uv_pca` | work-avoidance dispatch for coarse-grid extraction | NO-ROLE (2026-09-18, recorded) | live (arbiter path only); divergence confirmed, not merely latent, and it is **two** divergences: `_classify_uv_rect` hardcodes the literal `2` twice instead of reading `cfg.tiny_max`/`cfg.thin_max` (the subject of U3), and `classify_by_uv_pca` classifies on PCA oriented extents where `classify_by_uv` classifies on AABB cell dims, so the two disagree on any rotated element. `_classify_uv_rect` is defined *inside* `render_model_front_to_back` and has two callers there, so it is unreachable on a Stage A view |
| N2 | Classification telemetry columns (`TinyCount`, `LinearCount`, `ArealCount`, `Areal*Conf`, `FallbackCount`, `FallbackRate`, `AvgFallbackExtractMs`) | `csv_export.py` (`"TinyCount", "LinearCount", "ArealCount",`) | report N1's dispatch | NO-ROLE (2026-09-18, recorded) | live (arbiter path only). All present as claimed, plus `ElemCacheHitRate` in the same header block, and each has a zero-fill branch for the no-class-counts case |
| N3 | Occlusion authority (AREAL@HIGH writes occlusion; TINY/LINEAR never do; `_scene_occ_rects` early-out) | `pipeline.py` `_scene_occ_rects` | derive occlusion where Revit's result was unavailable | NO-ROLE (2026-09-18, recorded) | live (arbiter path only): one initialization, one early-out read, two append sites, all inside the model pass. But depth itself did **not** leave with it — `near_face_w` is still computed per element on the color-ID path and persisted in the sidecar (S1). The disposition stands for *occlusion authority*; it does not cover *depth*, which no row previously claimed |
| N4 | Binary occupancy write ("stop and fill the cells") | dispatch tail | occupancy as a flag | NO-ROLE (2026-09-17, superseded by continuous fill fraction) | live (arbiter path only). The successor exists and is a consumer, which the seed asserted without one: `tools/colorid_to_occupancy.py` takes `--thresholds 0,0.05,0.25,0.5` and occupies a cell when `palette-pixel coverage >= threshold` |
| N5 | Strategy matrix, confidence tiers, fallback telemetry, diagnostics JSON | `revit/collection.py` `strategy_diag.record_geometry_extraction` (5 call sites); `diagnostics/strategy_tracker.py`; `config.py` `export_strategy_diagnostics` | dispatch support | NO-ROLE (2026-09-17, recorded) | live (arbiter path only); `export_strategy_diagnostics` already defaults to `False` |

### A — retained as arbiter

| id | artifact | site | retained until | disposition | state |
|----|----------|------|----------------|-------------|-------|
| A1 | Geometry/silhouette/OBB extraction (front-facing face via `face.EdgeLoops`) | `core/silhouette.py`, `core/areal_extraction.py`, `core/face_selection.py`, `pipeline.py` | annotation capture and links are covered | ARBITER-ONLY (2026-09-17, recorded) | live (arbiter path only), and the isolation is now verifiable rather than assumed: `color_id_buffer.py` imports **nothing** from `core/silhouette.py`, `core/geometry.py`, `core/areal_extraction.py` or `core/face_selection.py`. Its only geometry dependency is bbox-level (S1). The tessellation stack is reachable from the model pass and from nowhere on the capture path |
| A2 | `views_occlusion` CSV | `csv_export.py` (`views_occlusion_`), `streaming.py` | same | ARBITER-ONLY (2026-09-17, recorded) | live (arbiter path only) |
| A3 | `AnnoCells_*` / `AnnoFinalCells_*` emission | `final_state_scanner.py` (`AnnoFinalCells_`) → `csv_export.py` (`"AnnoCells_TEXT"`) | vector bbox is validated against it — **then** retire, not before | ARBITER-ONLY (2026-09-17, recorded) | live (arbiter path only) — and this is the row where "arbiter path only" bites hardest. A3 is named as the *only available validation target* for vector bboxes, but it is produced by the scan the Stage A branch `continue`s past, so validating against it means running both paths over the same views, not reading it off a capture run. Seven buckets, `TEXT/TAG/DIM/DETAIL/LINES/REGION/OTHER`, folded 1:1 from `AnnoFinalCells_*` to `AnnoCells_*` |

### U — undecided, raise do not default

| id | artifact | site | the question | state |
|----|----------|------|--------------|-------|
| U1 | Cache architecture: `geometry_cache`, `elem_cache`, root cache, bbox fingerprints | `pipeline.py`, `root_cache.py`, `core/element_cache.py`, `core/geometry_cache.py`, `core/cache.py` | what, if anything, a cache is *for* when there is no per-element derivation to avoid | **the seed's "read-disabled and inert during Stage A" is true of one cache out of four.** Root cache: reads gated off, explicitly (`if root_cache and not stage_a_color_id_mode:`, `Stage A must always reach the color ID-buffer export branch`). `geometry_cache`/`areal_cache`: unreached, because only `render_model_front_to_back` takes them. `elem_cache`: **live on the Stage A path** — `pipeline.py` constructs it and passes `elem_cache=elem_cache` into `export_color_id_buffer_view`, which forwards it to `expand_host_link_import_model_elements`. So the question is not open uniformly: for `elem_cache` it is "what is this cache doing for the capture path", and it has an answer today |
| U2 | `ViewFrameHash` | `csv_export.py` (`"ViewFrameHash"`) | collides across distinct views (542078/554620; 770742/630733) — a blocker only if U1 re-enables the cache | latent, and the collisions are **recorded data, not a recollection**: `tools/verify_invariant_core.py` carries `{"hash": "1c1b4f43", "view_ids": [542078, 554620]}` and `{"hash": "6147c316", "view_ids": [770742, 630733],`, and `tools/notes/data/README.md`'s I8 gives the dims — `1c1b4f43` at *identical* 350×288, `6147c316` at 97×25. Identical dims means the first pair is not a near-miss: the hash is not discriminating views that differ |
| U3 | semgrep `vop-hardcoded-classification-threshold` (issue #203) | `.semgrep/vop-rules.yml` | an ERROR-severity check whose subject is N1 | live; contradiction voided when deletion was deferred. Confirmed `severity: ERROR`, and its `pattern-either` arms match `_classify_uv_rect`'s two literal-`2` tests exactly, so the finding count is a property of N1's hardcoding and not of the rule's reach. Not re-run here: semgrep is not installed in this environment and the pinned 1.177.0 is what the reconcile job proves against |
| U4 | CreepIndex / DocLoadIndex / Stability | metrics layer | downstream consumers, unvalidated — must not be allowed to constrain the record shape upstream | **no site in the tree.** `CreepIndex` and `DocLoadIndex` appear in no `.py`, `.md` or `.json` at `d05038a`; the only case-insensitive "creep" is the word "creeping" in a probe comment. `Stability` likewise names nothing in the metrics layer. These are notes-only hypotheses, so the row's own warning is already satisfied — there is nothing upstream for them to constrain. Re-raise them when something implements them |
| U5 | `geom_silhouette_ms` / `geom_obb_ms` timing keys | `perf_constants.py` (`"GEOM_SILHOUETTE_MS"`), `pipeline.py` | whether they still fire on Stage A runs (double-processing question) | **answered, and the seed's answer was the wrong mechanism.** They cannot fire on a Stage A view: the only writer is `render_model_front_to_back`, and the Stage A branch `continue`s before it (and before the `perf_subtimings_geometry` block that copies them out). So a `20260917T112500` run showing them is either a geometry-path run or predates the gating. Double processing *is* real and is **collection, not geometry**: `pipeline.py` collects the view's elements, and `export_color_id_buffer_view` collects them again under the neutral phase (`Re-collect under the neutral phase state`) because the original phase filter hides elements the neutral one shows. That second collection is deliberate and documented; the pipeline's own pre-swap collection is what Stage A only uses as a fallback. This wants a disposition as a **cost** (S9), not as a timing-key question |

### S — found by the repo sweep, absent from the notes

New rows. Dispositions dated 2026-09-20; `UNDECIDED` where the evidence
locates the artifact but does not decide it.

| id | artifact | site | why it is here | disposition | state |
|----|----------|------|----------------|-------------|-------|
| S1 | Per-element bbox + projected depth: `resolve_element_bbox`, `project_bbox_uv_and_near_face_w`, and the `near_face_w_map` sidecar section | `revit/collection.py`; `color_id_buffer.py` `_collect_near_face_w_data` | **the geometry path's survivor, and C3 depends on it.** These are the only geometry the capture path runs, and their output is what `link_identity_resolver.py` disambiguates blobs with. No seed row covers them, while A1 (NO forward role) and C3 (CARRY) sit on either side of them | CARRY (2026-09-20) | live on the capture path. The two are deliberately computed together rather than via a separate `estimate_nearest_depth_from_bbox` call, so bbox corners and depth cannot disagree on coordinate space for a rotated instance. Every resolved element gets an entry, `None`-valued rather than omitted, per Refactor Rule #1 |
| S2 | `color_assignment_map` (palette RGB → element id) and `build_palette`'s reserved-corner lattice | `color_id_buffer.py` `build_palette` / `"color_assignment_map"` | element **identity** is a persisted capture primitive that the stated two-primitive model omits; decode cannot attribute a pixel without it | CARRY (2026-09-20) | live; near-white corner reserved by construction so no assigned color collides with the default background, and decode absorbs off-palette pixels into `BACKGROUND_ELEMENT_ID` |
| S3 | The locked metrics manifest itself | `metrics/manifest/metrics_manifest.v1.json`, `metrics_manifest.py`, `metrics/manifest_evaluator.py` | R2 dispositions the *columns* as RE-DERIVE, but they are a **versioned, validated schema** with an evaluator, not a column list. Re-deriving them at the consumer means either a v2 manifest or a consumer outside the manifest's authority — a decision nobody has recorded | UNDECIDED (2026-09-20) — raise | live (arbiter path only). `_compute_manifest_metrics_payload` runs the scan and then `evaluate_metrics_manifest` over it; both sit past the Stage A `continue`, so no Stage A run is validated against the manifest at all |
| S4 | `core/source_identity.py` — `make_source_identity`, the HOST/LINK/DWG normalizer | `core/source_identity.py` | **it has no production consumer.** Refactor Rule #3 requires source identity be "normalized (HOST \| LINK \| DWG) before rasterization", this module is that normalization, and the only thing that imports it is `tests/test_source_normalization.py`. Meanwhile `source_type` is set as a bare string literal in `revit/collection.py`, `color_id_buffer.py`, `revit/linked_documents.py`, `core/element_cache.py` and `core/raster.py` | UNDECIDED (2026-09-20) — raise, and note it is not a carry-forward question | a single-source-of-truth module that nothing routes through, with 8 passing tests over it. Green, and binding nothing: CLAUDE.md's own "green means nothing until you know what ran". Whether the answer is to route the literals through it or to delete it, it is a live Rule #3 gap and not a substrate-change decision |
| S5 | `revit/annotation.py` — the extent-driver half | `revit/view_basis.py` (`from .annotation import compute_annotation_extents`, `from .annotation import collect_2d_annotations, is_extent_driver_annotation`) | closes the seed's Open item "no row for annotation collectors". The collectors do **not** have one role, they have two, and only one of them is on the capture path | CARRY (2026-09-20) | live on the capture path. Annotation extents expand B before any capture, so they are load-bearing for primitive 1 — reachable from `color_id_buffer.py` through `view_basis.py`, which the sweep's import closure confirms |
| S6 | `revit/annotation.py` — the rasterizing half (`rasterize_annotations`) | `pipeline.py` (`from .revit.annotation import rasterize_annotations`) | same file, opposite verdict: this is the cell-filling side that vector records replace | NO-ROLE as storage (2026-09-20) | live (arbiter path only). Unchanged since 2026-06-15, as the seed noted; the file's disposition had to be split rather than given, which is why the Open item could not be answered as written |
| S7 | `png_export.py` and `view_raster_export.py` | `png_export.py` `export_raster_to_png`; `view_raster_export.py` (`renders Revit views as-is to PNG for side-by-side comparison`) | a **named arbiter channel** with no ledger row: `colorid_to_occupancy.py` compares its `colorid_ink` against `geom_png` read from `vop_raster` PNGs (`0,255,0 = model edge; 0,128,0 = proxy; 255,0,0 = anno`) | ARBITER-ONLY (2026-09-20) | live (arbiter path only). `view_raster_export.py` also holds `_crop_uv_frame`, one of the two extractions CLAUDE.md credits with making the uv↔pixel defect visible, so it carries forward as *arithmetic* even where it does not as *output* |
| S8 | Geometry-path-only support modules: `core/footprint.py`, `core/hull.py`, `core/pca2d.py`, `revit/tierb_proxy.py`, `diagnostics/occlusion_tracker.py`, `diagnostics/strategy_tracker.py` | imported only from `pipeline.py`'s model pass, `core/geometry.py`, or `csv_export.py`'s tracker read | N1/N3/N5/A1 dispositioned the *decisions*; these are the modules that implement them, and no row named them | NO-ROLE (2026-09-20), following N1/N3/N5 | live (arbiter path only). `core/pca2d.py` is reached only by `classify_by_uv_pca`, so it inherits N1's divergence exactly |
| S9 | Double collection per Stage A view | `pipeline.py` `collect_view_elements`; `color_id_buffer.py` (`Re-collect under the neutral phase state`) | U5 asked the double-processing question against timing keys that cannot fire. The real double processing is collection, and it is a **cost to disposition**, not a telemetry bug | UNDECIDED (2026-09-20) — raise | both collections happen on every Stage A view. The second is required (the neutral phase filter reveals elements the view's own phase filter hides) and already narrowed — a local `Config` copy forces `include_linked_rvt = False` to skip per-placement LINK work the expansion would not consume. The open question is the **first**: the pipeline's pre-swap collection, whose result Stage A uses only as a fallback — when `raster is None`, or when the re-collection raises |
| S10 | `vop_interwoven/metrics/` has no `__init__.py` | `vop_interwoven/metrics/` (compare `diagnostics/__init__.py`, `export/__init__.py`) | not a carry-forward question, but it is in the code and in nobody's notes, and it sits under R2/S3's site | UNDECIDED (2026-09-20) — raise as a packaging defect, not a ledger row | imports resolve under CPython 3 as a namespace package. CLAUDE.md states the repo targets IronPython 2 **and** CPython 3, where the former has no namespace packages, and `pipeline.py` imports `from .metrics.final_state_scanner import scan_final_state_totals` on the arbiter path |

### X — defects the sweep found in the record itself

| id | what | where | state |
|----|------|-------|-------|
| X1 | A rotted line-number citation, the fourth recurring shape in CLAUDE.md, in the module that documents the discipline | `resolution_contract.py`, `cap_axes` docstring: `caller hands cap_axes an int anyway` — which then points at `color_id_buffer.py` `:1682` | the claim is **true** and the pointer is **wrong**: the single production call is `cap = cap_axes(pre_cap_px, pre_cap_px * aspect_derived_over_fit, cap_axis_px)`, and `pre_cap_px` is `max(64, int(round(export_dpi * paper_fit_in)))`, an int. But it is at line 1761, not 1682 — the citation is off by 79 lines. Fix by citing the call's words. Not fixed here: this ledger is a record, and a code edit does not belong in the same change (Refactor Rule #5) |
| X2 | `C4/R1 are one coupling, not two rows` (the seed's own Open item) | rows C4, R1 | **keep them split.** The sweep found the coupling has two independent ends: the capture end (`paper_width_in` sized from `raster.W * cell_size_ft`) and the consumer end (`colorid_to_occupancy.py` recovering W from `resolution.backoff_floor_px`, `grid_basis="assumed"`). Merging the rows would hide that re-sourcing C4 does not by itself free R1's consumer |

---

## Excluded by decision

- Verification tooling (`tools/verify_invariant_core.py`, invariant core, L0–L4)
  — out of scope for this ledger (2026-09-19). Read as evidence for U2 only;
  not dispositioned.

## What the sweep could and could not see

Recorded so the next sweep can attack the method rather than repeat it.

**Proven against** `d05038a0656d8389915e70a45444bb87ca024c0e`. Test baseline at
that commit, before and after this file: **1111 passed, 2 xfailed** (this is a
docs-only change; the count is recorded so a later claim about it has something
to fail against). CLAUDE.md's "Recurring Defect Classes" cites 981 — the suite
has grown since, so 981 is no longer the number to watch.

**Method.** (1) Every `site` cell in the seed was opened and the claim checked
against the code, not against the note. (2) An AST import closure over all 48
modules in `vop_interwoven/`, following function-level imports as well as
module-level ones, rooted at `color_id_buffer` (capture) and at
`core/silhouette` + `core/areal_extraction` (geometry), to find which modules
each substrate actually reaches. (3) Grep sweeps over `tools/` and `tests/` for
each artifact's names, since the closure does not cover them.

**Four holes, and they are where the next finding will be.**

1. **Module reachability is not branch reachability.** The closure says
   `color_id_buffer` does not import `core/silhouette`; it cannot say which
   *parts of `pipeline.py`* a Stage A run executes, because the model pass and
   the capture branch live in the same 4271-line module. Every "arbiter path
   only" State cell above rests on reading one `continue` and the order of
   statements after it, not on a test. **Nothing in the suite asserts that a
   Stage A view reaches no geometry pass** — an assertion worth more than any
   row here, and the obvious next piece of work.
2. **Nothing reached `tools/` systematically.** `tools/` was grepped per
   artifact, not closed over. A tool that reads a capture artifact nobody has
   named is exactly the shape this ledger is meant to catch, and would have been
   missed.
3. **`tests/` is undispositioned.** 116 test files bind these artifacts;
   S4 shows a test suite can be the *only* consumer of a module and keep it
   looking alive. No row here asks what a test binding a NO-ROLE artifact means.
4. **Dead code was not swept for.** CLAUDE.md names
   `vulture --min-confidence 80` as having found real defects; it is not
   installed here and was not run. S4 was found by grep, by accident, while
   checking something else — which is evidence that a systematic pass would
   find more.

**Still open after this sweep.**

- The two-primitive premise in "The test" is falsified by the sidecar and should
  be rewritten to four families (frame, pixels, identity, per-element
  bbox+depth), or S1/S2 will keep reading as exceptions to a rule instead of
  parts of it.
- S3, S4, S9, S10 are raised and undecided. None of them defaults safely.
- U4 has no code to decide about; it is a notes-only row and should either
  acquire a site or be struck.
