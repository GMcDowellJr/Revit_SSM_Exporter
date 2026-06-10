## Stage 0a: `generate_reference_graph.py`

**Purpose:** Produces `reference_graph.json` by merging hand-maintained static edge seeds with auto-discovered dynamic edges, then auto-validating `available` flags against actual identity_items data.

**Inputs:**

| File | Path | Notes |
|---|---|---|
| `static_edges_seed.json` | `config/archetype/static_edges_seed.json` | Hand-maintained, no `available` flag |
| `vfd_dynamic_edges.csv` | `Fingerprint_Out/vfd_dynamic_edges.csv` | Produced by VFD parameter inventory |
| `vfd_param_inventory.csv` | `Fingerprint_Out/vfd_param_inventory.csv` | For file_count per param |
| `bip_lookup.json` | `Fingerprint_Out/bip_lookup.json` | Builtin param name resolution |
| `shared_param_names.json` | `Fingerprint_Out/shared_param_names.json` | Shared param name resolution |
| `identity_items_by_domain/*.csv` | `Fingerprint_Out/identity_items_by_domain/` | For availability validation |

**`static_edges_seed.json` schema** — the only truly hand-authored artifact:

```json
{
  "schema_version": "1.0",
  "edges": [
    {
      "edge_id": "materials→fill_patterns_drafting.cut_foreground",
      "source_domain": "materials",
      "source_field": "material.sig.cut_foreground_pattern.sig_hash",
      "target_domain": "fill_patterns_drafting",
      "edge_type": "structural",
      "direction": "source_references_target"
    },
    {
      "edge_id": "materials→fill_patterns_drafting.cut_background",
      "source_domain": "materials",
      "source_field": "material.sig.cut_background_pattern.sig_hash",
      "target_domain": "fill_patterns_drafting",
      "edge_type": "structural",
      "direction": "source_references_target"
    },
    {
      "edge_id": "wall_types→fill_patterns_drafting.cfpsh",
      "source_domain": "wall_types",
      "source_field": "wt.cfpsh",
      "target_domain": "fill_patterns_drafting",
      "edge_type": "structural",
      "direction": "source_references_target",
      "requires_extraction": ["wt.cfpsh field addition to wall_types extractor"]
    },
    {
      "edge_id": "line_styles→line_patterns",
      "source_domain": "line_styles",
      "source_field": "ls.pattern_ref.sig_hash",
      "target_domain": "line_patterns",
      "edge_type": "structural",
      "direction": "source_references_target"
    },
    {
      "edge_id": "vfa→vfd",
      "source_domain": "view_filter_applications_view_templates",
      "source_field": "vfa.stack[*].filter_sig_hash",
      "target_domain": "view_filter_definitions",
      "edge_type": "structural",
      "direction": "source_references_target"
    },
    {
      "edge_id": "object_styles→fill_patterns_drafting",
      "source_domain": "object_styles_model",
      "source_field": "os.pattern_ref.sig_hash",
      "target_domain": "fill_patterns_drafting",
      "edge_type": "structural",
      "direction": "source_references_target"
    }
  ]
}
```

**Processing:**

*Step 1 — Validate static edges:*
For each edge in seed, check whether `source_field` appears as an `item_key` value in `identity_items_by_domain/{source_domain}.csv`. Fields using indexed patterns (e.g. `vfa.stack[*].filter_sig_hash`) — strip the `[*]` and treat as prefix match against item_keys. `available = true` if at least one non-null, non-`<NONE>`, non-`unreadable` value found. Carry forward `requires_extraction` from seed unchanged.

*Step 2 — Build dynamic edges from VFD inventory:*
Load `vfd_dynamic_edges.csv`. Exclude `kind == 'project'` rows entirely. Join to `vfd_param_inventory.csv` on `(kind, item_value)` to get `file_count`. Apply support threshold: `file_count >= 10`. Group by `(kind, item_value, param_name, domain)` to collect `category_ids` as a set. Resolve display name: builtin params via `bip_lookup.json`, shared params via `shared_param_names.json`, fallback to raw GUID. For each unique `(param_id_cluster, target_domain)` produce one dynamic edge:

```json
{
  "edge_id": "vfd→wall_types.Fire_Rating_In_Hours",
  "source_domain": "view_filter_definitions",
  "source_field": "vf.rule[*].param_ref.id",
  "target_domain": "wall_types",
  "edge_type": "dynamic",
  "available": true,
  "direction": "source_governs_target",
  "scope_conditions": {
    "param_ids": ["84c97a7e-3d9f-4811-9981-03a01c590803"],
    "param_names": ["Fire Rating In Hours"],
    "category_ids": ["-2000011"]
  },
  "file_count": 108,
  "requires_extraction": []
}
```

`edge_id` slug: `vfd→{target_domain}.{param_name_normalized}` where normalized = replace spaces with `_`, strip special chars. Where multiple params map to the same target domain and are governance-related (e.g. `Fire Rating In Hours` + `Smoke Rating In Hours` both → wall_types), produce separate edges per param — don't collapse.

*Step 3 — Merge and output:*
Combine validated static edges and generated dynamic edges. Write `reference_graph.json`. Log: count of static edges (available/unavailable), count of dynamic edges generated, count filtered by support threshold.

**Output:** `Fingerprint_Out/archetype_analysis/reference_graph.json`

---

## Stage 0b: `generate_archetype_candidates.py`

**Purpose:** Runs after stage 3. Reads cross-domain co-occurrence patterns and produces `archetype_definitions_candidates.json` — candidate archetype definitions for human review and promotion to `archetype_definitions.json`.

**Inputs:**

| File | Path |
|---|---|
| `cross_domain_patterns.csv` | `Fingerprint_Out/archetype_analysis/cross_domain_patterns.csv` |
| `reference_graph.json` | `Fingerprint_Out/archetype_analysis/reference_graph.json` |

**`cross_domain_patterns.csv` schema** (defined here as it's the contract between stage 3 and this script):

| column | type | notes |
|---|---|---|
| `pattern_id` | str | md5 of sorted edge_ids + join_hashes |
| `edge_id_a` | str | first edge in co-occurrence pair |
| `edge_id_b` | str | second edge |
| `join_hash_a` | str | join_hash from domain A |
| `join_hash_b` | str | join_hash from domain B |
| `file_count` | int | files exhibiting this pair |
| `support_pct` | float | file_count / corpus_size |
| `target_domain_a` | str | |
| `target_domain_b` | str | |
| `param_name_a` | str | resolved name if dynamic edge, else null |
| `param_name_b` | str | |

**Processing:**

*Step 1 — Derive governance_question_hint:*
For each pattern row, collect the set of target domains touched by `edge_id_a` and `edge_id_b` (look up in `reference_graph.json`). Map domain sets to governance question hints using a small hardcoded lookup:
- Any edge targeting `wall_types` → `wall_graphics`
- Any edge targeting `fill_patterns_drafting` or `fill_patterns_model` → `fill_pattern_usage`
- Any edge targeting `line_patterns` → `line_pattern_usage`
- Any edge targeting `view_filter_definitions` → `view_filter_strategy`
- Unmatched → `unknown`

*Step 2 — Cluster patterns by governance_question_hint:*
Group patterns. Within each group, cluster by shared edge_ids (patterns sharing an edge_id are likely variants of the same archetype).

*Step 3 — Generate candidate per cluster:*
For each cluster produce:

```json
{
  "archetype_id": "wall_graphics.CANDIDATE.vfd_fire_rating_x_material_cut_fill",
  "governance_question": "wall_graphics",
  "approach_label": "CANDIDATE — human label required",
  "support_pct": 0.42,
  "file_count": 87,
  "signals": [
    {
      "signal_id": "edge_vfd→wall_types.Fire_Rating_In_Hours",
      "edge_id": "vfd→wall_types.Fire_Rating_In_Hours",
      "condition": "edge_active",
      "required": true,
      "auto_generated": true
    },
    {
      "signal_id": "edge_materials→fill_patterns_drafting.cut_foreground",
      "edge_id": "materials→fill_patterns_drafting.cut_foreground",
      "condition": "source_field_not_null_and_not_none",
      "required": true,
      "auto_generated": true
    }
  ],
  "auto_generated": true,
  "promoted": false
}
```

`CANDIDATE` in `archetype_id` is a deliberate marker — human replaces it when promoting. `promoted: false` means the entry is not yet picked up by `assign_archetype_classifications.py`, which skips any definition where `promoted == false`. Human edits: replace `archetype_id` slug, set `approach_label`, adjust `required` flags, set `promoted: true`. Promoted entries are manually moved (or merged) into `archetype_definitions.json`.

**Output:** `Fingerprint_Out/archetype_analysis/archetype_definitions_candidates.json`

---

## Stage 1: `build_cross_domain_items.py`

**Purpose:** For each file in the corpus, materialize the set of active cross-domain items — (edge_id, source_join_hash, target_ref_value) tuples where the file has records satisfying the edge's conditions. Output is the item universe for co-occurrence mining.

**Inputs:**

| File | Path |
|---|---|
| `reference_graph.json` | `Fingerprint_Out/archetype_analysis/reference_graph.json` |
| `records.csv` | `Fingerprint_Out/records.csv` |
| `identity_items_by_domain/*.csv` | `Fingerprint_Out/identity_items_by_domain/` |

**Output schema — `cross_domain_items.csv`:**

| column | type | notes |
|---|---|---|
| `export_run_id` | str | file identifier |
| `edge_id` | str | from reference_graph |
| `source_domain` | str | denormalized for readability |
| `target_domain` | str | denormalized |
| `source_record_pk` | str | source record identifier |
| `source_join_hash` | str | join_hash of source record from records.csv |
| `target_ref_value` | str | raw field value (sig_hash or param_ref.id) pointing to target |
| `target_join_hash` | str\|null | join_hash of target record if resolvable; null otherwise |

**Processing logic per edge type:**

*Structural edges (`available=true`):*
1. Read `identity_items_by_domain/{source_domain}.csv`
2. Filter: `item_key == source_field` (exact match; for indexed fields like `vfa.stack[*].filter_sig_hash` use prefix match stripping `[*]` → match `item_key.startswith('vfa.stack[')` and `item_key.endswith('].filter_sig_hash')`)
3. Filter: `item_value` not in `('<NONE>', '', None)` and `item_value_type` not in `('unreadable', 'unsupported.not_applicable')`
4. Result rows: `(export_run_id, record_pk, item_value)` = `(file, source_record, target_ref_value)`
5. Join to `records.csv` on `(export_run_id, domain=source_domain)` matching `record_pk` via the `record_pk` column → get `source_join_hash`
6. Join to `records.csv` on `(domain=target_domain, sig_hash=target_ref_value)` → get `target_join_hash`. Many-to-many possible (same fill pattern sig_hash in multiple files); take any match for the join_hash value — it's deterministic since sig_hash→join_hash is a function.
7. Emit one row per `(export_run_id, record_pk, target_ref_value)` with resolved hashes.

*Dynamic edges (`available=true`, has `scope_conditions`):*
1. Read `identity_items_by_domain/view_filter_definitions.csv`
2. Filter param_ref.id rows: `item_key` matches pattern `vf.rule[\d+].param_ref.id` AND `item_value` in `scope_conditions.param_ids`
3. Get the `record_pk` set from step 2
4. For those record_pks, additionally check: at least one `item_key == 'vf.categories'` row for that record_pk has `item_value` in `scope_conditions.category_ids` OR `item_value` contains one of the category_ids as a substring (comma-separated multi-category values)
5. Filter record_pks to only those passing both conditions
6. Get `source_join_hash` from `records.csv` on `(export_run_id, domain='view_filter_definitions', record_pk)`
7. `target_ref_value` = the `param_ref.id` value; `target_join_hash` = null (no specific target record)
8. Emit rows

*Structural edges (`available=false`):*
Skip entirely — do not emit rows. Downstream stages detect unavailability from `reference_graph.json` directly.

**Edge case — same record satisfies multiple edges:** Allowed. A VFD record can appear in both a fire-rating edge and a smoke-rating edge. Emit separate rows per edge.

**Output path:** `Fingerprint_Out/archetype_analysis/cross_domain_items.csv`

---

## Stage 2: `compute_cross_domain_cooccurrence.py`

**Purpose:** Mines co-occurrence patterns from the cross-domain item universe. Produces two outputs: edge-pair level aggregates (the archetype signal) and join_hash-pair level detail (for pass 2 validation).

**Inputs:**

| File | Path |
|---|---|
| `cross_domain_items.csv` | `Fingerprint_Out/archetype_analysis/cross_domain_items.csv` |
| `reference_graph.json` | `Fingerprint_Out/archetype_analysis/reference_graph.json` |

**Config:** `support_min_files` (default 5) — minimum file count for a pattern to be emitted.

**Processing:**

*Step 1 — Build per-file edge activation sets:*
Group `cross_domain_items.csv` by `export_run_id`. For each file produce:
- `active_edges`: set of `edge_id` values present for that file
- `edge_join_hashes`: dict of `{edge_id: set_of_source_join_hashes}` for that file

*Step 2 — Edge-pair co-occurrence (aggregate level):*
For each unordered pair `(edge_id_a, edge_id_b)` where a ≠ b:
- Count files where both edges are in `active_edges`
- Compute `support_pct = file_count / total_corpus_files`
- Apply `support_min_files` threshold
- Also record: files where only A active, only B active, neither active — for asymmetry analysis

Output schema — `cross_domain_edge_pairs.csv`:

| column | notes |
|---|---|
| `edge_id_a`, `edge_id_b` | always sorted alphabetically so pairs are unique |
| `target_domain_a`, `target_domain_b` | denormalized |
| `n_both` | files where both edges active |
| `n_a_only` | files where only A active |
| `n_b_only` | files where only B active |
| `n_neither` | files where neither active (excludes unavailable edges) |
| `n_a_unavailable` | files where edge A unavailable (from reference_graph) |
| `n_b_unavailable` | |
| `support_pct` | n_both / corpus_size |
| `jaccard` | n_both / (n_both + n_a_only + n_b_only) |
| `containment_a_in_b` | n_both / (n_both + n_a_only) |
| `containment_b_in_a` | n_both / (n_both + n_b_only) |

*Step 3 — Join_hash-pair co-occurrence (detail level):*
For each edge pair above the support threshold, and for each file where both edges active:
Cross-product `edge_join_hashes[edge_id_a]` × `edge_join_hashes[edge_id_b]` for that file. Aggregate across files: count how many files exhibit each `(source_join_hash_a, source_join_hash_b)` combination.

Output schema — `cross_domain_patterns.csv`:

| column | notes |
|---|---|
| `pattern_id` | md5 of `"{edge_id_a}\|{edge_id_b}\|{source_join_hash_a}\|{source_join_hash_b}"` (sorted) |
| `edge_id_a`, `edge_id_b` | |
| `target_domain_a`, `target_domain_b` | |
| `source_join_hash_a`, `source_join_hash_b` | |
| `file_count` | files with this specific join_hash pair |
| `support_pct` | file_count / corpus_size |
| `edge_pair_file_count` | parent edge pair file count (from edge_pairs output) |

Apply `support_min_files` to the join_hash pair level independently — a pair with only 1–2 files is noise.

**Outputs:**
- `Fingerprint_Out/archetype_analysis/cross_domain_edge_pairs.csv`
- `Fingerprint_Out/archetype_analysis/cross_domain_patterns.csv`

---

## Stage 3: `assign_archetype_classifications.py`

**Purpose:** For each file, evaluate which promoted archetypes are active using the per-file edge activation profile from `cross_domain_items.csv`. Tracks which signals fired, which were absent, and which were unavailable due to extraction gaps. Computes confidence tier and mixed-archetype flags.

**Inputs:**

| File | Path |
|---|---|
| `cross_domain_items.csv` | `Fingerprint_Out/archetype_analysis/cross_domain_items.csv` |
| `archetype_definitions.json` | `config/archetype/archetype_definitions.json` |
| `reference_graph.json` | `Fingerprint_Out/archetype_analysis/reference_graph.json` |
| `file_metadata.csv` | `Fingerprint_Out/file_metadata.csv` |

**Processing:**

*Step 1 — Build per-file activation profile:*
From `cross_domain_items.csv`, group by `export_run_id`. For each file:
- `active_edges`: set of `edge_id` values with at least one row
- `active_edge_join_hashes`: dict of `{edge_id: set_of_source_join_hashes}`

Build `unavailable_edges`: set of `edge_id` values where `available == false` in `reference_graph.json`. This is corpus-level, not per-file.

Get full file list from `file_metadata.csv` — files with no items in `cross_domain_items.csv` have empty activation profiles.

*Step 2 — Evaluate each archetype per file:*
Load `archetype_definitions.json`, filter to `promoted == true` only.

For each `(file, archetype)` combination, evaluate each signal in the archetype:

| Signal edge state | Result |
|---|---|
| `edge_id` in `active_edges` for this file | `fired` |
| `edge_id` in `unavailable_edges` | `unavailable` |
| `edge_id` available but not in file's `active_edges` | `absent` |

Determine row emission and confidence tier:
- If zero required signals `fired` → **do not emit a row**. File shows no positive evidence for this archetype regardless of unavailable signals.
- If all required signals `fired` and none `unavailable` → emit, `confidence_tier = "Full"`
- If at least one required signal `fired` and at least one required signal `unavailable` → emit, `confidence_tier = "Partial"` (classification is positive but incomplete — additional extraction may strengthen or revise)

Archetypes where all required signals are `unavailable` and none fire: do not emit per-file rows. Collect these in a separate summary file.

*Step 3 — Detect mixed archetypes:*
After evaluating all archetypes for all files, group by `(export_run_id, governance_question)`. If a file has more than one archetype row for the same `governance_question`, set `is_mixed = true` on all rows for that file+question combination.

*Step 4 — Join metadata:*
Left join to `file_metadata.csv` on `export_run_id` to attach `client_label`, `governance_role`, `discipline_label`, `unit_system`.

**Output schema — `archetype_classifications.csv`:**

| column | type | notes |
|---|---|---|
| `export_run_id` | str | |
| `archetype_id` | str | |
| `governance_question` | str | |
| `approach_label` | str | |
| `confidence_tier` | str | `Full` \| `Partial` |
| `is_mixed` | bool | multiple archetypes same governance_question |
| `signals_fired` | str | pipe-delimited `signal_id` values |
| `signals_absent` | str | pipe-delimited `signal_id` values |
| `signals_null` | str | pipe-delimited `signal_id` values (unavailable extraction) |
| `n_signals_fired` | int | |
| `n_signals_null` | int | |
| `client_label` | str | from metadata |
| `governance_role` | str | from metadata |
| `discipline_label` | str | from metadata |
| `unit_system` | str | from metadata |

**Additional output — `archetype_coverage_summary.json`:**
One entry per archetype_id: `{archetype_id, n_files_full, n_files_partial, n_files_no_evidence, unavailable_signal_ids, note}`. For archetypes with all signals unavailable, entry explains why no file rows were emitted.

**Outputs:**
- `Fingerprint_Out/archetype_analysis/archetype_classifications.csv`
- `Fingerprint_Out/archetype_analysis/archetype_coverage_summary.json`

---

## Stage 4: `validate_archetype_signals.py`

**Purpose:** Pass 2 at sig_hash grain. Scoped to files classified in stage 3. For each archetype bucket, measures whether the specific sig_hash values across classified files are coherent — confirming the join_hash-level classification holds — or fragmented, flagging that the archetype definition may be too coarse or picking up noise.

**Inputs:**

| File | Path |
|---|---|
| `archetype_classifications.csv` | `Fingerprint_Out/archetype_analysis/archetype_classifications.csv` |
| `cross_domain_items.csv` | `Fingerprint_Out/archetype_analysis/cross_domain_items.csv` |
| `records.csv` | `Fingerprint_Out/records.csv` |

**Processing:**

*Step 1 — Scope to classified files:*
Load `archetype_classifications.csv`. For each `(archetype_id, signal_id)` combination where the signal fired, get the set of `export_run_id` values.

*Step 2 — Resolve sig_hashes:*
For each scoped `(archetype_id, signal_id, export_run_id)`:
- Get `source_join_hash` values from `cross_domain_items.csv` where `edge_id` matches signal's `edge_id` and `export_run_id` matches
- Join to `records.csv` on `(export_run_id, domain=source_domain, join_hash=source_join_hash)` to get `sig_hash`
- Collect `(source_join_hash, sig_hash, target_ref_value)` tuples per file

*Step 3 — Compute coherence metrics per (archetype_id, signal_id):*

Across all files in the archetype bucket where this signal fired:

| metric | definition |
|---|---|
| `n_files_classified` | files in bucket with signal fired |
| `n_distinct_join_hashes` | distinct `source_join_hash` values across those files |
| `n_distinct_sig_hashes` | distinct `sig_hash` values across those files |
| `n_distinct_target_refs` | distinct `target_ref_value` values (what the field points to) |
| `n_multi_instance_files` | files where signal fired with >1 distinct join_hash |
| `coherence_score` | `n_distinct_sig_hashes / n_files_classified` |

Coherence tier assignment:
- `coherence_score < 0.3` → `"Convergent"` — few sig_hash variants, join_hash classification holds cleanly
- `0.3 ≤ coherence_score < 0.8` → `"Variable"` — moderate variation within bucket, may warrant archetype splitting
- `coherence_score ≥ 0.8` → `"Fragmented"` — near-unique sig_hashes per file, join_hash definition likely too coarse or signal is noise

*Step 4 — Detail rows:*
Emit one row per `(archetype_id, export_run_id, signal_id, source_join_hash, sig_hash, target_ref_value)` for forensic drill-down.

**Output schema — `archetype_validation.csv`** (aggregate):

| column | notes |
|---|---|
| `archetype_id` | |
| `signal_id` | |
| `edge_id` | |
| `source_domain` | |
| `n_files_classified` | |
| `n_distinct_join_hashes` | |
| `n_distinct_sig_hashes` | |
| `n_distinct_target_refs` | |
| `n_multi_instance_files` | |
| `coherence_score` | |
| `coherence_tier` | `Convergent` \| `Variable` \| `Fragmented` |

**Output schema — `archetype_validation_detail.csv`** (forensic):

| column | notes |
|---|---|
| `archetype_id` | |
| `export_run_id` | |
| `signal_id` | |
| `edge_id` | |
| `source_join_hash` | |
| `sig_hash` | |
| `target_ref_value` | |

**Outputs:**
- `Fingerprint_Out/archetype_analysis/archetype_validation.csv`
- `Fingerprint_Out/archetype_analysis/archetype_validation_detail.csv`

---