# Probe: four candidate fixes for the Stage A annotation pass (F1–F4, run 20260922T085737)

**This is a PROBE.** No production default changes, and nothing here decides a
fix. The probe reports values; your read of the output is the gate. The only
PASS/FAIL it makes is about **itself** — whether each variant ran and whether
the view was put back.

Branch: `claude/intelligent-faraday-1rbdox`, from `main` at `a3cd0b2`.

---

## What each variant changes

| variant | white filter | SmoothEdges off | expanded frame B′ | annotation crop offsets zeroed | production suppression mode |
|---|---|---|---|---|---|
| `v0_control` | — | — | — | — | `hide_categories` (shipped) |
| `v0_offsets0` | — | — | — | **yes** | `hide_categories` |
| `v1_white_filter` | **yes** | — | — | — | `external` |
| `v2_white_filter_smooth_edges_off` | **yes** | **yes** | — | — | `external` |
| `v3_white_filter_smooth_edges_off_expanded_frame` | **yes** | **yes** | **yes** | — | `external` |

- **V0** — the current annotation pass, unchanged. The control.
- **V0-offsets0** — reads the view's four annotation crop offsets, sets them to
  0, runs V0, restores them, reads them back. Isolates your competing hypothesis
  for F1: that the 1″-per-side offsets widen what `ExportImage` fits even though
  the annotation crop is inactive. `AnnotationCropActive` is recorded beside
  them, because that is the point of the hypothesis. **If the offsets cannot be
  READ, the variant is skipped for that view** — it never records a 0 it did not
  read.
- **V1** (F2) — one rule-less `ParameterFilterElement` over all filterable MODEL
  categories, projection and cut lines plus all four surface/cut patterns
  overridden to white solid fill, applied to the view and deleted on restore.
  Model categories are **not** hidden. Records the filter id, the category
  count, MODEL categories Revit **rejected as non-filterable** (the override
  cannot reach those), link instances **not set to "By Host View"** (the filter
  does not reach those either), and the count of elements carrying **authored
  per-element overrides**, which outrank a filter.
- **V2** (F3) — V1 plus `SmoothEdges = False`, captured and restored the way the
  model pass does. `applied_smooth_edges` is four-valued:
  `"not_attempted"` / `"read_failed"` / `"unchanged (failed)"` / `false`.
- **V3** (F1) — V2 plus `B′ = B ∪ get_BoundingBox(view)` of every
  annotation-pass member ∪ every rendered viewer element, + 0.5″ paper margin.
  Records B, B′, the per-side delta in feet **and paper inches**, which element
  set each side, and **every driver that contributed no rectangle with its
  reason**.

`revit/annotation.py`'s `compute_annotation_extents` (:123) and
`is_extent_driver_annotation` (:47) are **not modified and not called
differently** — that path is the retained arbiter. B′ comes from the probe's own
pure `expanded_frame_uv()`, and it is sized through production's
`frame_export_geometry`, so the existing pixel cap applies by being *called*
rather than re-derived.

---

## Production changes (PATCH-only, `color_id_buffer.py`)

Two opt-in switches on `export_annotation_color_id_buffer_view`, read with
`getattr` and **deliberately absent from `Config`** — no production default, no
`to_dict()`/`from_dict()` form, no way to reach a production run:

| attribute | default | probe value |
|---|---|---|
| `color_id_buffer_anno_model_suppression` | `"hide_categories"` | `"external"` for V1–V3 |
| `color_id_buffer_anno_smooth_edges_off` | `False` | `True` for V2–V3 |

`"external"` means the pass hides no model category and disables no view filter,
so the probe's white filter survives into the export (the shipped pass disables
every enabled+visible filter, which would undo exactly that). An **unknown** mode
raises rather than defaulting: the mode decides whether "annotation on white" was
arranged by production or by its caller, and a typo falling back would make a
variant that measured nothing look like one that did.

Both switches live in production rather than in the probe because the behaviour
they change happens inside that function's own transaction, where a caller has no
window. `SmoothEdges` is written **after** the `DisplayStyle` change — that
ordering is the mitigation for an UNCONFIRMED claim, and it is what the tests
assert.

Also: the sidecar gains `model_suppression_mode`, `applied_smooth_edges` and
`smooth_edges_read_error`; one **recording** `ViewDisplayModel` dispose helper
replaces what would have been three more `except Exception: pass` copies; and
`_export_tiff`'s `dim_check` gets a **TODO citing F4 and nothing else** — the fix
lands after you read this probe, because what the derived axis should be compared
against is one of the things measured here.

---

## UNCONFIRMED API claims

Nothing in this probe was observed on a Revit host. Each claim is resolved by
reflection at runtime and recorded three-valued — an absent property is
`unavailable` **with a reason**, never a 0 and never a `False` — and all of them
are collected into the combined report's `unconfirmed_api_claims`.

| claim | what depends on it |
|---|---|
| `ViewCropRegionShapeManager.Left/Right/Top/BottomAnnotationCropOffset` exist and are readable and writable, via `View.GetCropRegionShapeManager()` | **all** of V0-offsets0 |
| `ParameterFilterUtilities.GetAllFilterableCategories()` exists | **all** of V1–V3 |
| a rule-less `ParameterFilterElement` matches every element of its categories, so white line and pattern overrides suppress all model content the filter reaches | V1–V3's central claim |
| `View.GetLinkOverrides(ElementId).LinkVisibilityType` exists | the "not By Host View" record only |
| setting `View.DisplayStyle` does not replace the `ViewDisplayModel` and discard a `SmoothEdges` written before it | **mitigated** by the write ordering; recorded because the ordering *is* the mitigation |
| `ExportImage` fits the crop box unioned with the extents of annotations drawn beyond it | F1's primary hypothesis — **measured**, not assumed |

The viewer-element category list is resolved name by name and the **unresolved
names are recorded**, so a category that contributed nothing because it does not
exist on this host is distinguishable from one the view has none of.

---

## Dynamo run steps

```text
IN[0] = target view
IN[1] = output directory        (outside the checkout — a Stage A TIFF can be
                                 several hundred MB)
IN[2] = variant selection       ("all", or a comma-separated subset)
IN[3] = export dpi              (default 150)
```

1. Open a **copy** of the FVOT BLDG 1 test model.
2. **Restart Revit, or reset the Dynamo CPython3 engine**, so the probe is
   re-imported. Revit holds one interpreter open for the whole session; a probe
   imported before an edit stays imported after it, which is how the 2026-09-16
   run executed an old probe against a new campaign and flagged nothing.
3. Paste `tests/dynamo/probe_stage_a_anno_pass_variants.py` into a CPython3
   Python Script node with the four inputs above, or drive it through
   `revit_batch_dynamo.py` (registered as `stage_a_anno_pass_variants`, with
   settings validated in `IN[2] = True` validation-only mode).
4. Run **one view at a time**, and copy `OUT` out as text after each.

**Run the elevations first**, as the brief asks:

| order | view | id |
|---|---|---|
| 1 | `Elevation_CropActive` | 19293413 |
| 2 | `Plan_CropActive` | 19290402 |
| 3 | `Plan_CropInActive` | 19291097 |
| 4 | `Plan_DWG` | 19294180 |
| 5 | `Plan_RVTLink` | 19293485 |
| 6 | `RCP_CropActive` | 19293283 |
| 7 | `Section_CropActive` | 19293421 |
| 8 | `ModelCallout_CropActive` | 19293458 |

**If the restore read-back fails on the first view, stop the run**, send the
combined JSON, and do not open the next view.

### Output layout and expected file count

```text
<IN[1]>/anno_pass_variants_probe/
    <view>_<id>.anno_pass_variants.json            one combined report per view
    model/color_id_buffer/<view>_<id>.tiff|.json   the model pass, once per view
    v0_control/color_id_buffer/<view>_<id>_anno.tiff|.json
    v0_offsets0/color_id_buffer/...
    v1_white_filter/color_id_buffer/...
    v2_white_filter_smooth_edges_off/color_id_buffer/...
    v3_white_filter_smooth_edges_off_expanded_frame/color_id_buffer/...
```

Each variant gets its own output directory because production's TIFF and sidecar
names are fixed per view — five variants sharing one directory would overwrite
one file five times.

For the full 8-view set:

| artifact | count |
|---|---|
| annotation captures (8 views × 5 variants × 2 files) | **80** |
| model pairs (8 views × TIFF + sidecar) | **16** |
| combined probe reports (1 per view) | **8** |
| **total** | **104** |

Fewer means a variant was skipped or a view stopped early, and the combined
report says which and why in `skip_reason` / `stopped_early`. The two repeat
model exports per view leave **no** artifact — their scratch directories are
deleted.

### Reading the output

```bash
python tools/notes/anno_pass_variant_report.py <the anno_pass_variants_probe dir> \
    --out anno_variants.md
```

Leaf module (stdlib + numpy + PIL). Emits the brief's six measurements as tables
and nothing evaluative. Registration is **fitted from the pixels**, and the ink
test uses that fitted mapping, so it stays valid on exactly the captures where
registration failed. `capture_overlay.py` runs only where image == `frame_px`;
that is a **size gate only**, so the fitted px/ft is echoed beside every overlay
line.

---

## Restore

Per variant: a `TransactionGroup` rolled back in `finally`, explicit restore
steps inside it, and a read-back of **seven** properties taken **twice** — after
the explicit restore (the real measurement) and after the rollback (the safety
net). `crop_box` (corners and `CropBoxActive`), `smooth_edges`,
`annotation_crop_offsets`, `model_category_visibility` (every MODEL category, not
a sample), `view_filters`, `view_template_id`, `display_style`, plus
`probe_filter_deleted` checked **by id** rather than inferred from the filter-map
diff. Each is three-valued: unreadable is `unverified`, which is not `restored`.

Under V1–V3 model category visibility is **never written, in either direction**.

Only `document_safe` stops the run. A variant that errored with the view
verifiably restored does not: the other four candidates are still worth
measuring.

---

## Checks, before and after

| | before (`a3cd0b2`) | after |
|---|---|---|
| suite | 1442 passed, 2 xfailed | **1542 passed, 2 xfailed** |
| `count_discarded_handlers vop_interwoven tools` | 179 | **179** |
| `check_stage_a_no_geometry vop_interwoven` | PROVEN | **PROVEN** |
| `check_no_bare_except --paths vop_interwoven tools` | OK | **OK** |
| flake8 on new files (`--max-line-length=120`) | — | clean; no new findings in the two pre-existing files |

The 100 new tests are 11 production-switch tests, 27 analyzer tests and 62 probe
helper/adapter tests.

**The switch tests were falsified by mutating production, not by reading it.**
Six mutations, each turning a test red: `suppress_model_categories_here` forced
`True` and forced `False`, `anno_smooth_edges_off` forced `True` and forced
`False`, the unknown-mode guard neutered, and the `SmoothEdges` block relocated
above the `DisplayStyle` write — which turns **exactly** the ordering test red and
nothing else. That pass also found one test that **could not fail**: it read
`view.filter_enabled` after the restore, which is `True` either way. It now
asserts the state **at export time**, which is the only discriminating
observable.

The probe's helper tests live in `tests/`, not `tests/dynamo/` — `conftest.py`
refuses to collect the latter without `VOP_RUN_DYNAMO_TESTS=1`, so putting
Revit-free arithmetic behind that gate would mean it never runs.

---

## Notes worth your attention

**"All filterable MODEL categories" includes Detail Items and the shared
model/detail Lines category.** Those carry a Model label but are annotation
content, and production's `_model_category_hidden_state` deliberately excludes
them — so V1–V3 white them out and **real annotation ink is lost**. The probe
follows the brief literally and lists the affected categories in the sidecar
under `view_only_model_categories_included`. Setting
`white_filter_include_view_only_model_categories = false` runs it the other way
in one re-run, and both lists appear either way.

**V3 does not answer the model-lattice question, and says so.** V3 renders B′
while the model pass was sized from B, so production's
`annotation_lattice_mismatch` check would be comparing different frames. The
probe passes `model_accepted_px = None` so production **skips** it — "not
answered" rather than a fabricated pass — and records the real relationship
between the two lattices in full under `frame_prime.record.lattice_relationship`.

**Whether a variant changed how the model pass renders has a repeatability
control.** The model pass runs once per view, so re-hashing its TIFF after each
variant only catches a variant *clobbering* the file. A repeat export is
therefore taken **before** any variant, with the view untouched; only if that is
byte-identical does the post-variant comparison mean anything. If it is not — a
TIFF timestamp, a non-deterministic collector order — the verdict says
`not_applicable` with that reason rather than reporting the comparison anyway.

**A preflight was added after I reviewed my own code.** A missing
`OverrideGraphicSettings` pattern setter leaves that pattern *unchanged* rather
than raising at the point of use, so V1–V3 would have rendered model fills in
their authored colour while the sidecar said a white filter was applied — a
variant that measured nothing and reported success. The preflight opens no
transaction, names every missing setter, and skips V1–V3 with that list. Each
entry of `WHITE_OVERRIDE_SETTERS` is falsified individually, because a stub that
exposes everything cannot tell whether the set has the right names in it.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01LKUqQaYLBZGUMEwj5PLdoa
