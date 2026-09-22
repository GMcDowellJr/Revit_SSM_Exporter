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
| suite | 1442 passed, 2 xfailed | **1610 passed, 2 xfailed** |
| `count_discarded_handlers vop_interwoven tools` | 179 | **179** |
| `check_stage_a_no_geometry vop_interwoven` | PROVEN | **PROVEN** |
| `check_no_bare_except --paths vop_interwoven tools` | OK | **OK** |
| flake8 on new files (`--max-line-length=120`) | — | clean; no new findings in the two pre-existing files |

The 168 new tests are 13 production-switch tests, 43 analyzer tests and 112 probe
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

---

## Review round 1 — three Codex findings, all confirmed and fixed

None were marked optional, so each was verified against the code rather than
argued with. All three were real.

**P1 — a variant could report `RAN` without having measured its candidate.**
A TIFF existing proves an export happened, not that the variant's mutation was
applied, and **both** switches can decline without raising:
`applied_smooth_edges` comes back `"read_failed"` / `"unchanged (failed)"` when
the `ViewDisplayModel` read or write fails, and `model_suppression_mode` comes
back `"hide_categories"` if production suppressed after all. Either way V2/V3
would have been analysed under a name claiming AA was off while measuring V1's
behaviour — and the analyzer was **reading `applied_smooth_edges` and never
rendering it**, so the failure was invisible in three places at once.

Fixed at the root rather than at the instance: `variant_measurement_check()`
verifies **every** mutation the variant's plan requested (the review named only
the SmoothEdges half; the suppression-mode half is the same defect one switch
over), a variant that did not get it concludes `DID_NOT_MEASURE`, the run and
the envelope's `execution_status` follow, and the analyzer gained **section 0**
which prints it first.

A first pass at this left the gate inline in `_run_variant`. Mutating its
measurement branch to `elif False:` **left the whole suite green** — the tests
bound the checker and nothing bound the call site, which is CLAUDE.md's own
"exercise the call site" corollary. `variant_conclusion()` is now extracted and
bound, and that mutation turns three tests red.

**P2 — the persisted combined JSON was serialised before it was finalized.**
Confirmed exactly as reported: `_write_combined` ran before `paths` and
`conclusion` were set, so the file the analyzer and Greg read permanently said
`INCONCLUSIVE` with no paths, while only the in-memory object returned to Dynamo
was correct. Two representations of one run, disagreeing, with the durable one
wrong.

Fixed with a **refusal**, not just a reorder: `finalize_native_report()` stamps
`report_finalized` and `_write_combined` raises without it, so a future call site
that writes early fails loudly. The test **reloads the file from disk** rather
than inspecting the return value — a test on the return value could not have
seen this.

**P1 — the registration fit rested on an unasserted premise.** Correct, and the
fixtures were the problem: every one drew solid rectangles exactly filling each
bbox, which makes ink centroid == bbox centre *by construction*. For real text, a
tag with a leader or a dimension it need not hold.

The method itself is kept — the brief specifies it and it is precedented at a
0.44 px median on the run's plan — but its premise is now measured rather than
assumed. A uniform displacement is **not recoverable** from a capture (nothing
distinguishes it from the whole render sitting that far over), so the report does
not pretend to measure it; it measures whether one is **possible and by how
much** (ink span as a fraction of its recorded bbox, solidity, and the resulting
bound in pixels), plus a **second fit** anchored on the ink's own bbox centre
whose deltas are ~0 exactly when the premise holds. New fixtures draw ink that
does *not* fill its box, and an L-shaped one where the two anchors genuinely
diverge.

My first attempt at this measured the displacement **through the fit that
absorbs it** and returned ~0 on the very fixture built to expose it. Its own test
caught that, and the replacement measures outside the fit. The offset-ink test
now pins the *signature* — left and right margins moving in opposite directions
and summing to ~0 — which is what keeps a uniform ink offset distinguishable from
the genuinely-oversized F1 render, where both come back positive.

Checks after the round: **1577 passed, 2 xfailed**; handler count **179**;
geometry-free **PROVEN**; lint clean. Each fix was falsified by mutating it back.

---

## Review round 2 — three more Codex findings, all confirmed and fixed

**P1 — a capture production declared INVALID came back `RAN`.** Confirmed: the
probe recorded production's `success` and `failure_reason` but
`variant_conclusion` never saw them, so `annotation_frame_not_applied`, a
lattice or dimension mismatch, or an unverified restore all concluded `RAN`.
Correctly noted as distinct from round 1: these faults arise *after* the switch
was applied, so `variant_measurement_check` cannot see them. Production's verdict
is now checked first, such a variant concludes `CAPTURE_FAILED`, and a
`capture_success` of `None` is not treated as a pass. A second test pins the
*wiring*, which is the lesson round 1 taught.

**P1 — the analyzer's section 0 printed `none` for exactly the failed captures
it exists to expose.** Root cause was a **production** defect, not an analyzer
one: `export_annotation_color_id_buffer_view` serialised `state_out` about a
hundred lines before it computed `capture_faults`, so the persisted sidecar never
carried them — the same serialize-before-finalize shape as round 1's P2, one file
over, and affecting every Stage A annotation sidecar rather than just this
probe's. Fixed on both sides: production writes the sidecar *after* the faults
are assigned (with `failure_reason` persisted beside them), and the analyzer
prefers the probe's combined record while **saying which source it used**,
because a sidecar from an earlier build has no such key. An absent key reads
`UNKNOWN`, not `none`. The new test drives the **real** production function,
forces a lattice mismatch and reads the file back — the review was right that
injecting faults into a synthetic sidecar binds nothing.

**P2 — a zero fitted slope raised `ZeroDivisionError`.** Confirmed reachable:
`fit_axis` returns `None` only when the *sample* values have no spread, and
validly returns `0.0` when distinct recorded UVs all render at the same pixel.
Every inversion then divided by it, and one collapsed capture aborted the whole
multi-view report before any other view was written. Now reported `unavailable`
with the degenerate mapping recorded, with a test that the rest of the report is
still produced.

Checks after the round: **1598 passed, 2 xfailed**; handler count **179**;
geometry-free **PROVEN**; lint clean. All five mutations turn tests red.

---

## Review round 3 — three more findings, all confirmed and fixed

**P1 — a failed annotation bbox collection read as "no annotations".** The reader
deliberately empties `bbox_entries` and keeps production's reason, and nothing
consumed that: the fit said "0 matched", the excursion came back `status: value`
over zero rectangles, and coverage printed an empty table. All four bbox-derived
measurements are now gated on the collection status with the same reason, and a
line near the top of the view's section states it. The review was also right that
the previous test asserted only the reader's intermediate field; the new one
drives `build_report` and asserts on the text a reader consumes.

**P2 — `probe_filter_deleted` checked only half of what its comment promised.**
`RemoveFilter` succeeding while `doc.Delete` fails leaves the id off the view and
a project-wide `ParameterFilterElement` alive; view membership alone declared
that restored. The verdict now also requires `doc.GetElement` to confirm the
element is gone, with "could not determine" as `unverified`. And
`explicit_step_errors` — recorded and consulted by nothing — is now its own
obligation, because a clean rollback could otherwise let the variant conclude
`RAN` and falsely validate an explicit restore the rollback had rescued.

**P1 — a model capture production rejected still ran all five variants.**
`export_color_id_buffer_view` can return a TIFF and usable geometry with
`success=False`. The probe now refuses before any variant, naming the
`failure_reason` and still writing the report — five captures against a rejected
foundation cost a Revit session and prove nothing, and which model faults are
tolerable is Greg's call.

Checks: **1610 passed, 2 xfailed**; handler count **179**; geometry-free
**PROVEN**; lint clean. All four mutations turn tests red.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01LKUqQaYLBZGUMEwj5PLdoa
