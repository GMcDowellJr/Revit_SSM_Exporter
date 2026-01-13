# VOP Interwoven — Code Map (Authoritative)

## Scope
This code map reflects the current architecture after streaming-only operation with a single root cache (`vop_cache.json`) as the authoritative cache. Disk per-view caches are inert.

## Top-Level Modules

### `pipeline.py`
**Role:** Orchestrates per-view processing and cache short-circuiting.

**Key responsibilities:**
- Computes view signatures
- Reads and writes the **root cache** in both streaming and non-streaming modes
- Short-circuits view processing on root-cache HIT
- Ensures cache-hit results include identity fields and cached payloads

**Key decision boundaries:**
- Root cache HIT vs MISS (authoritative)
- Streaming path (metrics-only cache hits allowed)

---

### `root_cache.py`
**Role:** Single-file, metrics-oriented cache.

**Key responsibilities:**
- Persist per-view cache entries keyed by view id
- Validate cache via exporter version, config hash, project guid
- Store:
  - `metadata`
  - `metrics`
  - `timings`
  - `row_payload` (CSV-aligned, includes TitleCase identity fields)

---

### `csv_export.py`
**Role:** Materializes CSV outputs from pipeline results.

**Sheets:**
- CORE
- VOP
- PERF

**Cache semantics:**
- CORE: regenerated from document metadata; marks cache hits
- VOP: reuses cached `row_payload` on cache hits
- PERF: derived strictly from `timings` (no payload reuse)

---

### `streaming.py`
**Role:** Streaming driver for pipeline execution and CSV/PNG emission.

**Key responsibilities:**
- Iterates views
- Delegates per-view execution to `pipeline.process_document_views`
- Skips PNG regeneration on cache hits

---

## Deprecated / Inert Paths
- Disk per-view cache (`view_<id>.json`) — disabled by policy

## Notes
- Root cache is the sole cache authority
- All cache semantics are enforced in `pipeline.py` and `csv_export.py`
