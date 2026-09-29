# Handoff — Stage A registered capture: from opt-in to default

Written at the close of the annotation-pass variant probe (PR #217, branch
`claude/confident-newton-gsgbtx`, head `e023eff` plus this file). Read this
first; the round-by-round detail is in the findings files it points to.

---

## 1. Where things stand

**In production, behind a flag that defaults OFF**
(`Config.color_id_buffer_registered_capture`):

| piece | file | what it does |
|---|---|---|
| orchestrator | `vop_interwoven/stage_a_registered_capture.py` — `export_registered_stage_a_view` | runs both passes inside one `TransactionGroup`: marks → model pass (OST_Lines visible) → white membership suppression → annotation pass (external suppression, crop mode `authored_else_crop_a`) → **rollback** → read-back (view properties, marks gone, element overrides as before) → the record into both sidecars under `registration_marks`, written LAST so the file carries the restore verdict |
| helpers | `vop_interwoven/stage_a_registration.py` | mark layout (`registration_mark_segments`, `MARK_MID_FRACTION = 0.4`), mark drawing, flat-colour override, white-override preflight, linked-RVT category discovery, element + link white suppression, sidecar annotation |
| crop mode | `color_id_buffer.py` — `export_annotation_color_id_buffer_view` | `authored_else_crop_a`: an ACTIVE authored crop is left untouched; a crop-INACTIVE view gets crop A (the model pass's own snapped crop) and it is restored. `registration.crop_applied` records which |
| model pass switch | `color_id_buffer.py` — `export_color_id_buffer_view` | `color_id_buffer_model_lines_visible`: OST_Lines left visible so the marks draw |
| pipeline | `pipeline.py` | with the flag on, the registered capture REPLACES both pass calls; result shape unchanged (annotation nested under `annotation_pass`) |
| geometry check | `tools/check_stage_a_no_geometry.py` | `export_registered_stage_a_view` is a root; PROVEN over 233 functions, and falsified by injecting `get_Geometry` into the mark code |

Shipped behaviour with the flag off is unchanged.

## 2. What the probes established (evidence, not opinion)

Findings: `tools/notes/ROUND2_ANNO_PASS_VARIANTS_FINDINGS.md`,
`tools/notes/ROUND3_ANNO_PASS_VARIANTS_FINDINGS.md`; data under
`tools/notes/data/round{2,3}_anno_pass_variants/`.

1. **The crop boundary Revit draws is not a ruler.** It lands on the image
   border and is clipped (the elevation lost its horizontal edges). Retired.
2. **The category/subcategory white layer does nothing to the image.** V7 and
   V10 annotation TIFFs are byte-identical on six views; it costs 23–86 s per
   view. Not in production.
3. **Membership suppression keeps dependent annotation** (plan dimensions
   2 → 135 visible once model categories stopped being hidden).
4. **The rollback is a sound restore:** 0.5–5.5 s against 33–51 s for the
   explicit reverse, every obligation read back `restored`.
5. **Registration marks register both passes.** Latest elevation
   (`probe_0928_0933`): 12/12 ticks in both captures, residuals 0.19–0.32 px,
   model marks 0.40 px from the recorded lattice. Annotation → model is
   x' = 1.063156 x − 152.17, y' = 1.063741 y − 274.97 (via marks), and within
   0.34 px of the same transform through the model lattice. The elevation
   scale has been 17.635 px/ft in every run since round 2, matching two
   independent rulers.
6. **Crop-inactive views need crop A.** Left untouched, Plan_CropInActive
   exported its whole extent at 2.9 px/ft and production refused it. With
   crop A the annotation capture lands exactly on the model lattice (the
   transform is the identity).
7. **Middle marks on an image centre line were dropped by the export**, not by
   Revit's visibility (the audit found all twelve visible, unhidden,
   unfiltered). Moved to 40 % of the crop, they drew. That's consistent with
   the explanation, not proof of it.

## 3. What is NOT established

- **The production entry point has never run in Revit.** Only its parts have,
  through the probe (which calls the same `stage_a_registration` helpers and
  the same production passes).
- **Unmeasured view types this round:** Plan_DWG 19294180, RCP_CropActive
  19293283, Section_CropActive 19293421, ModelCallout_CropActive 19293458.
- **Crop-inactive views with far-flung content** register perfectly but at low
  resolution: Plan_CropInActive's crop A grew to ≈ 1033 × 2411 ft (4.15 px/ft)
  when the document gained property lines ~1,850 ft away. This is a model-pass
  property; Greg's call was to leave crop A as is.
- **The registration is applied offline, not in the pipeline.** Step 3 below
  landed as `tools/registration_marks.py` (the fit, lifted out of the
  analyzer, which now imports it), `tools/register_stage_a_annotation.py`
  (resample onto the model lattice, marks removed) and the decoder's mark
  exclusion. Nothing in `vop_interwoven` calls them. Step 2's run
  (`pipeline_0928_0953`, all nine views) found three defects in them, since
  fixed; all nine now register at <= 0.39 px residual. See
  `RUN_pipeline_0928_0953_REGISTRATION.md`.

## 4. The order from here

1. **Review and merge PR #217.** Production defaults are unchanged.
2. **Greg: run the pipeline with the flag on** *(Done: `pipeline_0928_0953`,
   `RUN_pipeline_0928_0953_REGISTRATION.md`.)* over all eight test views
   (Elevation 19293413, Plan_CropActive 19290402, Plan_CropInActive 19291097,
   Plan_RVTLink 19293485, Plan_DWG 19294180, RCP 19293283, Section 19293421,
   ModelCallout 19293458). This is the first real test of the entry point and
   needs no code. Check each view's result for `registration_success`, the
   `registration.faults` list, and both sidecars' `registration_marks` block.
3. **Transform and mark removal — its own PR.** *(Done offline: see
   `tools/register_stage_a_annotation.py`. Run it over step 2's output.)* Fit the marks from
   `registration_marks` in both sidecars (lift `registration_mark_fit` /
   `compose_pixel_transform` out of the analyzer into a tool the decoder uses,
   don't copy them), resample the annotation capture onto the model lattice,
   and subtract the mark pixel rects from both. Refuse, don't guess, when an
   axis has fewer than two levels.
4. **Default flip — its own PR, fresh branch from main.** Turn the registered
   capture on by default, retire the frame-B annotation path (or keep it as the
   named fallback), and replace the probe-only `getattr` switches the
   orchestrator sets on a copied cfg (`color_id_buffer_anno_crop_mode`,
   `color_id_buffer_anno_model_suppression`, `color_id_buffer_model_lines_visible`)
   with real parameters. Update CLAUDE.md's Stage A description.
   *(Done: the registered capture is the default; frame B is kept as the named
   fallback (`color_id_buffer_registered_capture=False`); the three switches
   are `Config` parameters; the registered model pass hides the view's other
   detail lines, which OST_Lines being visible for the marks used to let
   through. CLAUDE.md has a Stage A section.)*

Do not do step 4 before step 3: the shipped annotation pass lands on the model
lattice by forcing the crop to frame B, and the registered one does not (6–15 %
off-grid wherever a view has an active crop). Anything overlaying the two by
integer offset would be wrong until the transform exists.

## 5. Things the next session should know

- **The repo's rules bit this work repeatedly.** Mutation-check every new test,
  keep `count_discarded_handlers` at 179, keep `check_stage_a_no_geometry`
  PROVEN (it refuses computed callees; write setters out, don't `getattr`
  them), and assert on sidecar FILES rather than return values.
- **Name collision:** the annotation sidecar already has production's own
  `registration` block. The capture's record is `registration_marks`.
  `annotate_sidecar` refuses to overwrite a key, and that refusal is what
  caught the collision.
- **Dynamo reload:** the thin runner must reload `vop_interwoven.color_id_buffer`
  and `vop_interwoven.stage_a_registration` before the probe/pipeline, or an
  already-imported copy runs.
- **Fake Revit harness:** `tests/test_probe_anno_pass_run_variant.py` has a
  `TransactionGroup` fake that really rolls view and document state back,
  a `doc.Create.NewDetailCurve` fake, and a view-scoped collector. Both the
  probe and `tests/test_stage_a_registered_capture.py` are driven through it;
  reuse it rather than writing another.
- **Probe:** `tests/dynamo/probe_stage_a_anno_pass_variants.py`, version
  `2026-09-29.3`. V9 carries the per-mark Revit audit
  (`mark_audit_before_*`); the analyzer's section 12 tabulates it.
