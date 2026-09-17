# G2 source findings (Phase 1, read-only)

Baseline: `main` @ a9051f2. D1 patch present on `claude/beautiful-allen-6rg3ks`
(cdb0b2a), PR #200. No code changed in this phase.

## Artifact availability — Phase 2 cannot execute in this container

byColor `20260917T104744` is **not present**. Searched the whole filesystem
for the RunId, for any `color_id_buffer/` directory, for `*.tiff`, and for
`*KSRF*`. The only hits are pytest temporaries under `/tmp/pytest-of-root/`,
which are synthetic fixtures from this repo's own test suite. There is no
`~/Documents/_metrics` and no `/home/user/Documents`.

Per the task's reporting discipline, no synthetic stand-in was constructed and
no number below is invented. The harness is written and self-tested against
the structures it consumes; **it has not been run against the real capture.**

---

## Q1 — Does a checked-in test or tool measure per-element decoded extent against a reference bbox?

**No.** Nothing in `tests/` or `tools/` performs a per-element bbox-containment
check on decode output. The two nearest hits are both aggregate, whole-image
pixel counts, not per-element extents:

- `tools/analyze_stage_a_probe.py:1479` — `acc['out_of_bbox_palette_px'] += int(outside.sum())`.
  Counts **palette pixels** falling outside the content rectangle
  (`:1471-1478`), summed across the entire image. No element identity enters
  it; it cannot tell you which element, or by how many feet.
- `tests/test_stage_a_export_metrics.py:189` — a test of that same aggregate
  ("every RED pixel outside that 5-px band is out-of-bbox"), on a synthetic
  probe image.

Neither is the G2 measurement, and neither can be reduced to it.

### Finding worth recording: the clamp was already modelled at baseline, just not in decode

`analyze_stage_a_probe.py:1345-1375` (`_frame_geometry`) already implements the
10:1 clamp and its symmetric padding — `pad_x_px`, `pad_y_px`, `content_rect_px`,
`clamp_applied`, and a `clamp_matches_actual_height` self-check against the
capture's real height. Its docstring states the defect D1 fixes, in the same
terms, at commit a9051f2:

> "`bounds_xy` does not reflect that padding, so a naive 'assigned pixel
> outside bounds_xy' count is wrong by the pad on a clamped view."

So at baseline the repo contained **two** implementations of the clamp model
(`analyze_stage_a_probe.py`, and `colorid_to_occupancy.py` out-of-tree) and one
component that ignored it (`decode_stage_a_color_id.py`). D1 did not introduce
the model; it brought decode into line with a model the probe already had.
That is context for the baseline number, not a defence of it.

---

## Q2 — Does decode compute this internally?

**No.** `tools/decode_stage_a_color_id.py` emits no bbox-containment counter,
warning, or field. Its full output contract (module docstring, `:39-78`;
constructed at `:741-775`) carries `feet_per_pixel`, pixel-count statistics
(`off_palette_foreground_pixel_count`, `background_pixel_count`,
`distinct_ids_decoded`), and per-element `loops`/`confidence`/`strategy`/
`pixel_area`. Nothing compares an element against any reference.

Decode also **never reads `near_face_w_map`** — stated explicitly at
`color_id_buffer.py:2712-2714`. So the measurement is not a matter of reading
an existing column: the reference and the measurement live in the same sidecar
but have never been joined by anything.

### The circularity trap, named

`decode_stage_a_color_id.py:881` calls `_element_bounding_boxes(id_array)`,
and `:886-889` passes the result into `_loops_for_element` as `bbox=`. This is
**not** a reference bbox — it is the element's own pixel extent inside the
decoded TIFF, computed to crop the loop tracer's search window. It is derived
from the same image by the same mapping.

Using it as G2's reference would be exactly the circular measurement Q3 warns
against: the patch moves the decoded extent and this "reference" identically,
so the excursion would read 0 at both commits and the gate would pass
trivially. It is the obvious wrong thing to reach for, and the harness does
not touch it.

---

## Q3 — Reference bbox source, and is it independent of the mapping under test? **AFFIRMATIVE — independent.**

**Source: `sidecar["near_face_w_map"]["host"]["<elem_id>"]["bbox_corners_uv"]`.**

Provenance chain, every step Revit-side and pre-export:

1. `color_id_buffer.py:2403-2406` — `_collect_near_face_w_data(...)` is called
   inside `export_color_id_buffer_view`, unconditionally (no cfg gate), at the
   point the identity set is final. `:2392-2395` records that it is "a pure
   read, so it runs here ... rather than depending on anything painted/exported
   below."
2. `color_id_buffer.py:1003` — per HOST element, calls
   `project_bbox_uv_and_near_face_w(...)`; result stored at `:1016` as
   `"bbox_corners_uv"`.
3. `revit/collection.py:837` `project_bbox_corners_uv` / `:897`
   `project_bbox_uv_and_near_face_w` — take the element's Revit
   `BoundingBoxXYZ`, expand to 8 corners and apply `bbox.Transform` (and any
   link transform) via `_bbox_world_corners` (`:746`), then project each corner
   with `world_to_view(corner, vb)` (`:876`), and return the axis-aligned UV
   rectangle `[[u_min,v_min],[u_max,v_min],[u_max,v_max],[u_min,v_max]]`
   (`:891-895`).
4. `color_id_buffer.py:2715` — persisted into the sidecar as `near_face_w_map`.

**Evidence of independence from the mapping under test:**

- The projection is `world_to_view(world_xyz, ViewBasis)` — model feet through
  basis vectors. **No image, no pixel count, no `feet_per_pixel`, no
  `image_w`/`image_h`, no `bounds_xy`, no pad.** Nothing in
  `revit/collection.py:746-895` can observe the TIFF.
- It is computed **before the TIFF exists.** `_collect_near_face_w_data` runs
  at `:2403`; the painting loop begins at `:2409` and the export after it. The
  D1 patch cannot move a number produced before the image was rendered.
- It is in a different module from the mapping under test, reached by a
  different call path, and `color_id_buffer.py:2712-2714` states decode never
  reads it — so there is no shared code between reference and measurement at
  all.
- The D1 diff (cdb0b2a) touches `tools/decode_stage_a_color_id.py` and two
  test files only. It does not touch `revit/collection.py`,
  `color_id_buffer.py`, or anything in the chain above.

**Conclusion: the reference is independent of the mapping under test. Phase 2
is unblocked on this question.**

### Two caveats that bear on how the number must be read

1. **A bbox is a bound, not an outline.** `bbox_corners_uv` is the AABB of the
   element's 8 projected corners — a *superset* of its true silhouette. So
   "decoded V outside its own bbox" is a **one-sided** test: an excursion is
   real evidence of a mapping error, but containment is not proof of
   correctness. A mapping could be wrong and still land inside a generous
   bbox. This limits what a post-fix 0/81 is allowed to claim.
2. **`bbox_corners_uv` may legitimately be `None`.** `color_id_buffer.py:995`,
   `:1009-1013` record `None` rather than omitting the element when no bbox or
   view basis resolves ("no silent failure"). Those elements have no reference
   and must be reported as *unmeasurable*, never counted as passing.

---

## Q4 — Are Section 1's reference bboxes in byColor alone, or only in byGeom?

**Code-derived: byColor alone. Not confirmed against the artifact (absent).**

`near_face_w_map` is written only by `export_color_id_buffer_view`
(`color_id_buffer.py:2403`, persisted `:2715`), which is the Stage A capture
path. The task states byGeom `20260917T112657` has no `color_id_buffer` output
at all — so it cannot contain this field, and there is nothing to pull in from
it. The byGeom run is **not needed**, and was not used.

One read of a byColor sidecar confirms it. The harness reports the per-view
count of HOST entries carrying a non-null `bbox_corners_uv`, so the first run
answers Q4 as a side effect. **If that count is 0 on every view, stop and say
so — do not reach for byGeom without saying it first.**

---

# PHASE 2 — status and the one result that could be measured

## Harness: `tools/notes/g2_bbox_excursion_report.py`

No existing code measures this (Q1/Q2), so a harness was written. Standalone,
Pillow + NumPy, imports nothing from `vop_interwoven` and nothing from
`tools/` unless `--use-installed-decode` is passed.

    python tools/notes/g2_bbox_excursion_report.py <run>/color_id_buffer/

**Deviation from the specified protocol, with rationale.** The task specifies
stash / measure / unstash / measure. The harness instead replicates **both**
mappings verbatim (`v_baseline`, citing a9051f2:564-568; `v_patched`, citing
cdb0b2a's `_clamp_pad_geometry`/`_pixel_corner_to_uv`) and reports both columns
from a single invocation. That is a stronger form of the same protocol: the
two numbers come from provably identical harness code, with no possibility
that stashing perturbed anything else.

The stash protocol remains available as an independent cross-check:
`--use-installed-decode` adds a third column measured through the live
`tools/decode_stage_a_color_id.py`, and **probes it** against both replicas to
report which mapping it actually resolved to — so the commit under measurement
is never assumed. On the current working tree it reports
`installed ... implements: patched (post-D1)` and agrees with the `patched`
replica to 0.0000 ft.

Two definitional choices, both made visible rather than baked in:

- Counts are reported at **two** tolerances — `strict` (any excursion > 0 ft)
  and `1px` (> one `feet_per_pixel`) — because the 33/81 figure came from code
  of unknown definition and a single arbitrary threshold would not be
  comparable to it.
- Elements whose `bbox_corners_uv` is `None` are counted as **`no_ref`**
  (unmeasurable) and never silently counted as passing.

One caveat recorded in the file: `--use-installed-decode` calls
`_pixel_corner_to_uv` without a precomputed geometry, so it derives from the
image's own dimensions, whereas the `patched` replica prefers the sidecar's
`actual_w`/`actual_h`. These are equal on any capture where `dim_check` is
`pass`; a divergence between the two columns is itself a signal.

## The measurement has NOT been run

byColor `20260917T104744` is absent from this container (see top of file). The
two-commit table is **not produced**, and no substitute for it appears
anywhere in this document.

The harness was exercised once against a constructed fixture solely to confirm
it runs, discriminates, and completes in 0.8 s at Section 1's real 9960x996
size. **Its counts are meaningless as a G2 result** — the element placements
were invented — and are recorded nowhere as one.

## What COULD be measured: the pre-D1 displacement ceiling on Section 1

This does not need the TIFF. It follows from the geometry the user's own
`frame_delta_report.py` run already reported for `Section 1_864834`:
`image 9960x996` (aspect exactly 10.0000:1), `fpp 0.053333054`, `pad_y 91.748`,
`crop_ft 531.197`. Every input below is measured.

The content band is rows 91.748 .. 904.252 (812.504 px), spanning
`crop_v = 43.33332 ft`. The pre-D1 mapping spread that 43.33 ft over all
**996** rows instead of the 812.5 rows it actually occupies:

| | ft/px |
|---|---|
| post-D1 `fpp` (content band) | 0.053333054 |
| pre-D1 rate (full canvas) | 0.043507349 |

Displacement of the pre-D1 mapping, `patched V - baseline V`, across the band:

| position | pixel row | displacement |
|---|---|---|
| content top | 91.748 | **+3.9917 ft** |
| mid-height | 498.000 | +0.0000 ft |
| content bottom | 904.252 | **-3.9917 ft** |

**So on Section 1 the pre-D1 error is zero at mid-height and grows linearly to
a hard ceiling of ±3.9917 ft at the content-band edges.**

**The recorded 3.96 ft worst-case is corroborated.** It sits just inside a
ceiling of 3.9917 ft derived independently from measured geometry — exactly
where an element whose extent nearly, but not quite, reaches the band edge
would land. That figure's provenance was unknown; its magnitude is now
accounted for.

**The 33/81 count is NOT corroborated and cannot be from this bound alone** —
it depends on where the elements sit and on how much slack each bbox carries.
Two things are worth predicting before the harness runs, so the result can
falsify them rather than be rationalised afterwards:

1. Because displacement is linear in distance from mid-height, a **strict**
   (>0 ft) count should approach the full element count — nearly every element
   is displaced by *something*. A strict baseline count near 81/81 would not
   contradict 33/81; it would mean 33 was measured with a threshold.
2. `bbox_corners_uv` is a superset of the silhouette (Q3, caveat 1). Elements
   near mid-height are displaced by well under a foot and would stay inside
   their own bbox with no tolerance applied at all. **33/81 ≈ 41% is consistent
   with "only elements far enough from mid-height to escape their bbox slack"**
   — i.e. no threshold needed, just a real bbox. This is the more likely
   explanation and requires no assumption of a hidden tolerance.

Neither prediction is a result. The harness settles it.
