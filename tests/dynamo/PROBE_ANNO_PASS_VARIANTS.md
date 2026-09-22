# Stage A annotation-pass variant probe

`probe_stage_a_anno_pass_variants.py` runs four candidate fixes for the Stage A
annotation pass beside the current pass, on one view at a time, and writes what
each produced.

**It is a PROBE.** It changes no production default and it concludes nothing
about which fix is right. Greg's read of the output is the gate; the probe
reports values. The only PASS/FAIL it makes is about **itself** — whether each
variant ran, and whether the view was put back — because a variant that
silently failed to restore the document is not evidence, it is damage.

---

## What each variant changes

| variant | white filter | SmoothEdges off | expanded frame B′ | annotation crop offsets zeroed | production suppression mode |
|---|---|---|---|---|---|
| `v0_control` | no | no | no | no | `hide_categories` (shipped) |
| `v0_offsets0` | no | no | no | **yes** | `hide_categories` |
| `v1_white_filter` | **yes** | no | no | no | `external` |
| `v2_white_filter_smooth_edges_off` | **yes** | **yes** | no | no | `external` |
| `v3_white_filter_smooth_edges_off_expanded_frame` | **yes** | **yes** | **yes** | no | `external` |

That table is `variant_plan()`, not a second copy of it: the probe's records,
the production switches it sets and this document all read the same three
membership sets, and `tests/test_probe_stage_a_anno_pass_variants.py` asserts
the table above against them variant by variant.

### V0 — the control

The current annotation pass, unchanged. Everything else is read against this.

### V0-offsets0 — Greg's competing hypothesis for F1

Reads the view's four annotation crop offsets, sets them to 0, runs V0, restores
them and reads them back. On the 20260922T085737 views those offsets were 1″ per
side, and the hypothesis is that they widen what `ExportImage` fits **even
though the annotation crop is inactive**.

`AnnotationCropActive` is recorded beside the offsets, because that is the whole
point of the hypothesis.

**If the offsets cannot be READ on a view, this variant is skipped for that
view** with the reason, and the sidecar records `unavailable`. It never records
0: a 0 that was never read is indistinguishable from a view whose offsets really
are 0, and that difference is this variant's entire content.

### V1 — model suppression by a white filter (F2)

Hiding model categories takes dependent annotations with it — tags, and most
dimensions. Greg confirmed by hand that a view filter setting model lines and
fills to white leaves a clean annotation-only view.

The probe creates **one rule-less `ParameterFilterElement`** over all filterable
MODEL categories, overrides its projection and cut lines to white and all four
surface/cut patterns to white solid fill, applies it to the view, and deletes it
on restore. It does **not** hide model categories — production's
`color_id_buffer_anno_model_suppression = "external"` switch is what makes that
so, and the same switch stops production disabling the filter (the shipped pass
disables every enabled+visible filter, which would undo exactly this).

Recorded per run: the filter element id, the category count, every MODEL category
Revit **rejected as non-filterable** (the white override cannot reach those, so
anything they draw stays visible), every link instance **not set to "By Host
View"** (the filter does not reach those either), and the count of elements
carrying **authored per-element overrides**, which outrank a filter in Revit's
graphics precedence.

> **Known consequence, named rather than discovered.** "All filterable MODEL
> categories" includes **Detail Items** and the shared model/detail **Lines**
> category. Those carry a Model label but are annotation content, and
> production's `_model_category_hidden_state` deliberately excludes them — so
> V1–V3 white them out and **real annotation ink is lost**. The probe follows
> the brief literally and lists the affected categories in the sidecar under
> `view_only_model_categories_included`. Setting
> `white_filter_include_view_only_model_categories = false` runs it the other
> way in one re-run, and both lists appear either way.

### V2 — V1 plus SmoothEdges off (F3)

The annotation pass has never set `SmoothEdges`, and ~0.2% of pixels in the run
were palette-to-white blends. V2 captures, clears and restores it the way the
model pass does — three-valued read, `applied_smooth_edges` in the sidecar.

`applied_smooth_edges` is **four-valued** and the four are different facts:

| value | meaning |
|---|---|
| `"not_attempted"` | the pass was not asked to touch AA — the shipped state |
| `"read_failed"` | asked, but the pre-state could not be read |
| `"unchanged (failed)"` | read, but the write raised |
| `false` | AA is **confirmed off** for this export |

The write lands **after** the `DisplayStyle` change, deliberately. Whether
setting `DisplayStyle` replaces the `ViewDisplayModel` — and with it a
`SmoothEdges` written beforehand — is UNCONFIRMED on this Revit host. Ordering
the write after it makes the question moot rather than leaving a variant that may
have measured nothing, and that ordering is the whole reason the switch lives in
production instead of in the probe. `tests/test_stage_a_annotation_pass_probe_
switches.py` asserts the ordering against a fake view that models the worst case,
and relocating the block above the `DisplayStyle` write turns exactly that test
red.

### V3 — V2 plus an expanded annotation frame B′ (F1)

B is too small because `is_extent_driver_annotation` admits only text,
dimensions and a fixed tag list. It excludes grids, levels, viewers, ceiling
tags, multi-category tags, detail items and revision clouds — and the RCP and
section views got **no expansion at all** (B == A).

    B′ = B  ∪  get_BoundingBox(view) of every annotation-pass member
            ∪  get_BoundingBox(view) of every rendered viewer element
            +  0.5″ paper margin on all four sides

Viewers are included **for extent only**: the annotation pass does not paint
them and V3 does not make them painted.

**`revit/annotation.py` is not modified and not called differently.**
`compute_annotation_extents` (:123) and `is_extent_driver_annotation` (:47) are
the retained arbiter; B′ comes from this module's own pure `expanded_frame_uv()`.
A probe that edited the arbiter it measures against would have nothing left to
compare to.

The margin is applied **once, after the union**, on all four sides. Applying it
per driver would scale it by however many drivers happened to be on an extreme,
which is not a margin; there is a test for that specifically.

B′ is sized through production's `frame_export_geometry` with the same dpi, the
same fit direction and the same per-axis ceiling — so "the existing pixel cap on
B applies unchanged" holds by **calling the thing that applies it**, not by
re-deriving it.

Recorded: B, B′, the per-side delta in feet **and paper inches**, which element
set each side, how many driver elements contributed, and **every driver that
contributed no rectangle with its reason**. An unmeasurable driver is the single
most likely way B′ comes back too small while looking computed, so none is
dropped silently.

> **V3 and the model lattice.** V3 renders B′; the model pass was sized from B.
> Production's `annotation_lattice_mismatch` fault compares
> `geom["model_accepted_px"]` against `geom["requested_px"]` — under V3 those
> describe **different frames**, so the probe passes `model_accepted_px = None`
> and production skips that check. The question is **not answered** for V3, and
> "not answered" is not "passed". The real relationship between the two lattices
> — both frames' pixel sizes, both achieved fpp, both achieved dpi, and whether
> the fpp is shared — is recorded in full under `frame_prime.record.
> lattice_relationship`.

---

## Production code this uses, and the two switches

The annotation pass itself is production's
`export_annotation_color_id_buffer_view` — **called, never reimplemented**. Two
opt-in switches were added to it. Both are read with `getattr` off the cfg
object and both are **deliberately absent from `Config`**: they have no
production default, no `to_dict()`/`from_dict()` representation and no way to
reach a production run. A probe assigns the attribute on its own cfg object.

| attribute | default | probe value |
|---|---|---|
| `color_id_buffer_anno_model_suppression` | `"hide_categories"` | `"external"` for V1–V3 |
| `color_id_buffer_anno_smooth_edges_off` | `False` | `True` for V2–V3 |

An **unknown** suppression mode raises `ValueError` rather than defaulting. The
mode decides whether "the annotation TIFF is annotation on white" was arranged
by production or by its caller, and a typo falling back to `"hide_categories"`
would make a variant that measured nothing indistinguishable from one that did.

Also reused rather than re-answered: `_get_solid_pattern_id` (which fill pattern
is the solid one), `_override_is_cleared` (what a blank override is),
`split_stage_a_pass_membership`, `resolve_element_bbox`,
`project_bbox_corners_uv`, `frame_export_geometry`, `init_view_raster`,
`collect_view_elements`, `export_color_id_buffer_view`, and
`stage_a_probe_contract.element_id_value`.

---

## Transaction and restore discipline

Per variant, in order:

1. snapshot **before** — seven properties, read by one function so all three
   readings are the same reading;
2. `TransactionGroup.Start`;
3. a committed child `Transaction` applies this variant's pre-state (zeroed
   offsets, or the white filter);
4. production's annotation pass runs, with **no child transaction open**;
5. a committed child `Transaction` reverses step 3;
6. snapshot, and the read-back verdict — **the real measurement**, taken before
   the rollback so it judges the explicit restore;
7. `TransactionGroup.RollBack` in `finally`;
8. snapshot again, and a second verdict — the safety net.

Step 6 is what the restore contract is judged on. Step 8 exists so that a
failure at step 5 still leaves the document as found, and so the two can be told
apart: an explicit restore that failed while the rollback saved it is a probe
defect worth fixing, not a clean run.

### The seven obligations, named individually

`crop_box` (corners **and** `CropBoxActive`), `smooth_edges`,
`annotation_crop_offsets`, `model_category_visibility` (**every** MODEL
category, not a sample), `view_filters`, `view_template_id`, `display_style` —
plus `probe_filter_deleted`, checked **by id** rather than inferred from the
filter-map diff, because a filter deleted from the project but left associated
with the view (or the reverse) can leave an identical map.

Each is three-valued. A property that could not be **read** in either snapshot
is `unverified`, which is **not** `restored` — the same rule production's
override read-back follows, and the rule that stops a probe certifying a view it
never managed to inspect.

Under V1–V3 model category visibility is **never written, in either direction**,
which is what makes `model_category_visibility: restored` a real statement
rather than a no-op write's echo.

### Two different questions, and only one of them stops the run

`document_safe` is "is the view as this variant found it". It is the **only**
thing that stops the run, because continuing past a document in an unknown state
makes every later artifact evidence about nothing.

`conclusion` is "did this variant produce a capture". An annotation pass that
raised **with the view verifiably restored** is a failed variant and a safe
document: the run continues, because the other four candidates are still worth
measuring and stopping would throw away the whole view's evidence over one of
them.

### The model pass

Runs **once per view**, shipped behaviour, neither switch set. Its
`frame_export_geometry` result is caught through `geometry_out` and handed to
every variant — production's own wiring, so V0–V2 register against the model
capture exactly as a production run would.

Its TIFF is re-hashed after every variant. **That detects a variant CLOBBERING
the model artifact** — a path collision, a stray write — and nothing more,
because nothing rewrote the file.

Whether a variant changed how the model pass **renders** is a different
question, and it is answered with a **control**: a repeat export is taken
*before* any variant runs, with the view untouched. Only if that comes back
byte-identical to the original does the post-variant comparison mean anything.

| `model_reexport_after_variants.verdict.status` | meaning |
|---|---|
| `unchanged` | the control held **and** the post-variant hash matches |
| `changed` | the control held and it does not — a variant left the view in a state the model pass renders differently, which the per-variant re-hash cannot see |
| `not_applicable` | the control did **not** hold (two exports of the untouched view already differ on this host — a TIFF timestamp, a non-deterministic collector order), or a hash is missing. The question is **not answered**. |

Both repeat exports go to a throwaway directory that is deleted, so the run
still publishes exactly **one model pair per view**. `model_reexport_check =
false` turns both off; the cost is two extra model exports per view.

---

## Dynamo wiring

```text
IN[0] = target view
IN[1] = output directory
IN[2] = optional variant selection ("all", or a comma-separated subset)
IN[3] = optional export dpi (default 150)
```

### Via the campaign/batch pipeline

The registry adapter `stage_a_anno_pass_variants`
(`revit_probe_registry.py`) validates settings **before** a transaction starts
or a TIFF is exported, reusing the probe's own `select_variants` and
`variant_plan` rather than a second copy of the vocabulary — so a campaign typo
is refused in `IN[2] = True` validation-only mode, not discovered after five
exports.

```json
"settings": {
  "selection": "v0_control,v1_white_filter",
  "export_dpi": 150,
  "expanded_frame_margin_in": 0.5,
  "authored_override_scan_max": 5000,
  "white_filter_include_view_only_model_categories": true,
  "model_reexport_check": true
}
```

A campaign job's `variant` maps onto `selection`; a job that sets both to
different values is **refused**, not silently resolved one way.

---

## Running it — the 20260922T085737 view set

Run the **elevations first**, as the brief asks.

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

1. Open the FVOT BLDG 1 test model (a **copy**, as always).
2. Restart Revit, or reset the Dynamo CPython3 engine, so the edited probe is
   re-imported. Revit holds one interpreter open for the whole session; a probe
   imported before an edit stays imported after it, which is how a run on
   2026-09-16 executed an old probe against a new campaign and flagged nothing.
3. Paste `probe_stage_a_anno_pass_variants.py` into a CPython3 Python Script
   node with the four inputs above, or drive it through
   `revit_batch_dynamo.py`.
4. Point `IN[1]` at a writable directory **outside the checkout** — a Stage A
   TIFF can reach several hundred megabytes.
5. Run it for **one view at a time**, and copy `OUT` out as text after each.

### Output layout, and the expected file count

```text
<IN[1]>/anno_pass_variants_probe/
    <view>_<id>.anno_pass_variants.json          one combined report per view
    model/color_id_buffer/<view>_<id>.tiff        the model pass, once per view
    model/color_id_buffer/<view>_<id>.json
    v0_control/color_id_buffer/<view>_<id>_anno.tiff
    v0_control/color_id_buffer/<view>_<id>_anno.json
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

**If the restore read-back fails on the first view, stop the run**, send the
combined JSON, and do not open the next view.

---

## Reading the output

```bash
python tools/notes/anno_pass_variant_report.py <the anno_pass_variants_probe dir> \
    --out anno_variants.md
```

Leaf module: standard library + numpy + PIL, plus `tools/clamp_pad_geometry.py`.
It emits the brief's six measurements as tables and **nothing evaluative** — no
score, no tolerance, no pass/fail, no "this variant looks better".

1. **Image size vs `frame_px`, on both axes.** `dim_check` inspects the
   requested axis only (F4), so a nonzero `dh` beside `dim_check=pass` is F4
   exactly.
2. **Registration fit, measured from the pixels.** Exact-palette-colour
   centroids matched against the sidecar's `bbox_uv` centres, a linear map
   fitted to the pairs, both axes **independently** (F1 saw u and v off by
   different amounts). Reports px/ft on u and v against the frame's own, the
   offset at (u0, v1), the per-side margin in feet **and** paper inches, and the
   residual median and max. A colour whose mask touches an image edge is
   **excluded and counted**: a clipped element's centroid is not its centre.
3. **How far the recorded bboxes reach past the rendered frame, per side** —
   printed beside 2b. Where they agree, the render is behaving as if fitted to
   the crop unioned with the annotations drawn beyond it.
4. **Coverage per category**: painted, colour present, bbox region holds ink.
   The ink test uses the **fitted** mapping from (2), never the sidecar's — so
   it stays valid on exactly the captures where registration failed, which are
   the ones worth looking at. With no fitted mapping the ink column is
   **unmeasured with its reason**, not 0.
5. **Off-palette pixels** split into palette-to-white blends (within 3 RGB units
   of the segment between some palette colour and white), black/grey, and other,
   with a top-10 colour list. The blend and grey classes **overlap**; blend is
   tested first and `blend also grey` states what that ordering costs.
6. **Model TIFF hash vs V0**, from the probe's own per-variant readings.

`tools/capture_overlay.py` is invoked **only** where (1) shows image ==
`frame_px`. Otherwise the overlay is **withheld with the reason**: that tool maps
UV through the sidecar's recorded export size and refuses when the file
disagrees, so running it on a mis-registered capture yields either a refusal
panel or boxes placed against a rectangle that was never rendered.

> **That gate is a SIZE gate and nothing more, which matters for F1.** A capture
> whose CONTENT was drawn at a different scale or origin is still exactly
> `frame_px` pixels, so it passes the gate and gets an overlay whose boxes will
> not land on the ink — and that is precisely the F1 case. The report therefore
> echoes measurement 2's fitted px/ft beside every overlay line, so a fit of
> 17.8 against a frame's 18.7 is visible where the overlay is mentioned rather
> than three sections up. An overlay being written is not a statement that the
> capture registers.

---

## UNCONFIRMED API claims

Nothing in this probe was observed on a Revit host in the session that wrote it.
Each claim is resolved by reflection at runtime and recorded three-valued — an
absent property is `unavailable` **with a reason**, never a 0 and never a
`False`. They are collected into the combined report's `unconfirmed_api_claims`.

| claim | what depends on it |
|---|---|
| `ViewCropRegionShapeManager.Left/Right/Top/BottomAnnotationCropOffset` exist and are readable and writable, via `View.GetCropRegionShapeManager()` | **all** of V0-offsets0 |
| `ParameterFilterUtilities.GetAllFilterableCategories()` exists | **all** of V1–V3 |
| a rule-less `ParameterFilterElement` matches every element of its categories, so white line and pattern overrides suppress all model content the filter reaches | V1–V3's central claim |
| `View.GetLinkOverrides(ElementId).LinkVisibilityType` exists | the "not By Host View" record only; its absence degrades the record, not the variant |
| setting `View.DisplayStyle` does not replace the `ViewDisplayModel` and discard a `SmoothEdges` written before it | **mitigated** by writing SmoothEdges after DisplayStyle; recorded because the ordering *is* the mitigation |
| `ExportImage` fits the crop box unioned with the extents of annotations drawn beyond it | F1's primary hypothesis — which this probe **measures** rather than assumes |

The viewer-element category list (`OST_Viewers`, `OST_Elev`,
`OST_ElevationMarks`, `OST_Sections`, `OST_SectionHeads`, `OST_Callouts`,
`OST_CalloutHeads`, `OST_CalloutBoundary`, `OST_ReferenceViewer`) is resolved
name by name and the **unresolved names are recorded**, so a category that
contributed nothing because it does not exist on this host is distinguishable
from one the view simply has none of. That is the "an incomplete token set is the
same defect as an incomplete pattern" lesson, applied to a category list.

---

## Evidence to send back

* every `*.anno_pass_variants.json`;
* every `*_anno.tiff` and `*_anno.json`, per variant;
* the model pair per view;
* `OUT` copied as text, per view;
* the analyzer's report;
* notes on whether V1's image really is annotation-on-white in Revit, and
  whether V3's frame holds the grids, levels and viewer marks B was missing.

---

## What is deliberately NOT here

* **`dim_check` is not fixed.** F4 gets a TODO at its call site citing F4 and
  nothing else. The fix lands after Greg reads this probe, because what the
  derived axis should be compared against — `geom["predicted_derived_px"]`, or
  the frame's other axis — is one of the things measured here.
* **No production default changes**, and the two switches are not `Config`
  fields, so nothing about them is reachable from a production run.
* **No verdict on the candidates.** Not in the probe, not in the analyzer, not
  in this document.
