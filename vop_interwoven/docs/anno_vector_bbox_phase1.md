# Annotation vector bbox — PHASE 1 design, repo-grounded

**Status:** proposal only. No production behaviour change is proposed here and none
was made. Phase 2 is a separate, PATCH-only task gated on Greg's approval of this
document.

**Grounding commit:** `d05038a0656d8389915e70a45444bb87ca024c0e`
(`git rev-parse HEAD`, this working tree, clean).

**Revision 3 — 2026-09-19**, second review pass. **Corrects revision 2 on a material
point:** rev 2 said the record is "the UV rectangle". It is not — the record is
shape-discriminated (§2.6, now governing), because storing a diagonal or L-shaped line
as its bounding box overstates its contribution, by `L²/2` at 45° against a figure of
zero area. §2 retitled and its helper scoped to AABB families only; D9 and D10 added;
Q1 answered (channel = element type + parameter); Q7 and Q8 opened. §0, §4 and §5
unchanged.

**Revision 2 — 2026-09-19**, after Greg's review. Changed: §1.4 and §1.5 are
withdrawn/retracted as design constraints (grid artifacts, not geometry); §1.6 added
on A↔B post alignment; §2 retargeted from `project_bbox_uv_and_near_face_w` to
`project_bbox_corners_uv` because annotations are 2D; §5 restates the comparison
residual as recovered signal; Q2 closed; Q4 largely resolved; Q1 and Q5 narrowed.
Nothing in §0, §3, §4, §6.Q3 or §6.Q6 changed. No production file has been touched in
either revision.

Every claim about current behaviour below carries a `file:line`. Every number names
the run or file it came from. Every "there is none" names the store that was
searched.

---

## 0. Baseline, before anything is proposed

### 0.1 Test count

    $ python -m pytest tests/ -q
    1111 passed, 2 xfailed in 15.97s      # exit 0

Run at `d05038a`, CPython 3.11.15, in this container. `VOP_RUN_DYNAMO_TESTS` unset,
so `tests/dynamo/` was **not** collected (`tests/conftest.py:21-33`). The two xfails
are the two registry-tracked quarantines, neither annotation-related:

| test | subject |
|---|---|
| `tests/test_occlusion_contract.py::test_rasterize_areal_loops_low_does_not_populate_rect_gate_cells` | AREAL occluder-rect seed under LOW confidence |
| `tests/test_single_areal_extraction_contract.py::test_areal_path_extracts_geometry_once_before_raster_decompose` | two `decompose_to_rects()` calls in one loop |

**The baseline is environment-dependent and that is itself a finding.** The
container started with **no** `numpy`, `pillow`, `hypothesis` or `pytest`. Runs in
order, all at `d05038a`:

| environment | result |
|---|---|
| pytest only | `1 skipped, 8 errors in 1.26s` — 8 collection errors, all `ModuleNotFoundError: numpy` |
| + numpy 2.4.6, pillow 12.3.0 | `3 errors in 0.37s` — `ModuleNotFoundError: hypothesis` |
| + hypothesis 6.168.0 | **`1111 passed, 2 xfailed`** |

Phase 2 must state which of these three it ran, or the number means nothing.

### 0.2 Refactor Rule #1 ledger

    $ python tools/count_discarded_handlers.py vop_interwoven tools
    scanned 65 .py file(s)
    except X as e:  90 | except X:  86 | except (A, B):  3 | TOTAL 179
    by body: {'continue': 32, 'pass': 147}

    $ python tools/check_no_bare_except.py --paths vop_interwoven tools
    OK: no bare `except:` found in scanned paths.

179 matches CLAUDE.md exactly — no drift since `2e4d679`. **8 of the 179 live in
`vop_interwoven/revit/annotation.py`**, at lines 34, 69, 762, 1289, 1566, 1640,
1705, 1721 (AST walk over that file, `ast.ExceptHandler` whose body is exactly one
`Pass`/`Continue`). Phase 2 must not move that total.

`semgrep` was **not** run: it is not installed here and `pip install semgrep` was not
attempted. The reconcile figure is therefore carried forward from CLAUDE.md, not
re-proven at `d05038a`.

### 0.3 Annotation test coverage: there is none

Store searched: the `tests/` tree at `d05038a`, by `grep -rn` for
`revit.annotation|rasterize_annotations|collect_2d_annotations|classify_annotation|
compute_annotation_extents|get_annotation_bbox`.

Hits, all of them:

- `tests/dynamo/test_phase8a_annotations.py:30,48` — `collect_2d_annotations`
- `tests/dynamo/probe_stage_a_image_alignment.py:473,498` — `compute_annotation_extents`

Both are under `tests/dynamo/`, which `tests/conftest.py:21-33` refuses to collect
without `VOP_RUN_DYNAMO_TESTS=1`, and both need a live Revit `doc`/`view`.

**`vop_interwoven/revit/annotation.py` has zero coverage in the 1111-test suite.**
`ViewRaster.rasterize_polygon_to_anno()` (`core/raster.py:1234`) likewise has zero —
`grep -rn rasterize_polygon_to_anno tests/` returns nothing. The only annotation
touch in the collected suite is four direct writes to `raster.anno_key` in
`tests/test_raster.py:250,254,259,275`.

There is also no shared Revit fake to build on. The fakes are per-file and minimal:
`tests/test_near_face_w_collection.py:112-126` installs an `Autodesk.Revit.DB` module
carrying **only** `Transform.Identity`, with an explicit comment (`:117-120`) that
`BuiltInCategory` and `FilteredElementCollector` are deliberately absent. Everything
`collect_2d_annotations` imports at `annotation.py:495-499` — `FilteredElementCollector`,
`BuiltInCategory`, `ElementId` — plus `XYZ`, `Dimension`, `FilledRegion`, `TextNote`,
`IndependentTag` would have to be built. This is the "know what the fake harness does
NOT provide" hazard from CLAUDE.md, and it is the single largest cost item in Phase 2.

### 0.4 Reference data: no annotation measurement is committed

Stores searched: `find . -name "views_vop*"` → nothing. `tools/notes/data/` holds
`views_core_2025-10-25.csv` (no `Anno*` column in its header), one
`stage_a_excursion_byColor_*.csv`, and `verification_bundle_20260917T112657.json`.

`grep -o "Anno[A-Za-z_]*"` over that bundle yields `AnnoCells_OTHER` ×6 and `AnnoOnly`
×13 — and inspection (`:404`, `:446`, `:490`) shows every `AnnoCells_OTHER` occurrence
is the literal `distinct_from` prose string from
`tools/verify_invariant_core.py:1601-1605`, not a measurement.

**There is no committed `AnnoCells_*` or `AnnoFinalCells_*` value anywhere in this
repository.** Phase 2's comparison needs a fresh production run to have anything to
compare against; it cannot be bootstrapped from the tree.

### 0.5 Dual-target status of the code in scope

`annotation.py` contains 17 f-strings (`grep -c 'f"' `; e.g. `:119`, `:167`, `:321`,
`:550`, `:814`, `:1808`). f-strings are CPython 3.6+. `metrics/final_state_scanner.py`
carries 4 plus `from __future__ import annotations` and `str | None` (`:3`, `:18`).

**Both modules already fail the IronPython 2 half of the dual target.** The constraint
is real for a *new leaf module*, and Phase 2 should honour it there; but it is not a
constraint the existing annotation path satisfies today, and a patch confined to
`annotation.py` cannot make that file worse than it is.

---

## 1. LOCKED item 2 — verified from code

> *"Frame is B. Origin is B.min; cells at B.min + (i+0.5)·cell; grid slack from the
> ceil sits entirely at the max corner. VERIFY from code that
> `compute_annotation_extents` expands `base_bounds_xy` (B is annotation-expanded)
> while the render crop prefers `model_clip_bounds` (A is not)."*

**Confirmed on all four clauses, with two exceptions that must be written into the
design.**

### 1.1 B is annotation-expanded — confirmed

`compute_annotation_extents` returns, at `annotation.py:435-446`:

    final_min_x = min(base_bounds_xy.xmin, anno_min_x)
    ...
    return Bounds2D(final_min_x - ann_margin_ft, ..., final_max_y + ann_margin_ft)

i.e. the union of `base_bounds_xy` with the driver-annotation extents, then a printed
margin outward. `resolve_view_bounds` calls it at `revit/view_basis.py:1018-1028` and
assigns the result straight onto `base_bounds` (`:1034-1036`), which becomes
`bounds_uv` in the returned dict (`:1191`) → `pipeline.py:1735` → `raster.bounds_xy`
(`pipeline.py:1778`).

### 1.2 A prefers `model_clip_bounds` — confirmed

`resolve_view_bounds` returns `model_bounds_uv` separately (`view_basis.py:1194-1196`),
the pre-annotation crop/extents bounds. `pipeline.py:1789` threads it onto
`raster.model_clip_bounds`. `color_id_buffer.compute_model_crop()` (`:1540`) then takes
the four-sided **intersection** of `model_clip_bounds` with `bounds_xy`
(`:1599-1611`), falling back to `bounds_xy` when `model_clip_bounds` is `None`
(`:1594-1595`). The result is applied as the actual view crop at `:2064-2065` and
recorded in the sidecar as `"bounds_xy"` (`:2726`).

So **A ⊆ B by construction**, and annotation lives in B\A. The premise holds.

### 1.3 Cell centres and origin — confirmed

`core/raster.py:36-37`:

    u = self.bounds.xmin + (i + 0.5) * self.cell_size
    v = self.bounds.ymin + (j + 0.5) * self.cell_size

Same convention at `:422-423`, `:916-917`, `:1498`. Origin is `bounds_xy.min` = B.min.

### 1.4 Grid slack at the max corner — confirmed, and **withdrawn as a design constraint**

`view_basis.py:1183-1190`:

    W = max(1, int(math.ceil(width_ft / cell_size_ft_effective)))
    ...
    if max_W is not None: W = min(int(W), int(max_W))

Ceil anchored at the min corner ⇒ slack at the max corner, unless `max_W`/`max_H`
bind, in which case the grid covers *less* than B.

**Greg's call (2026-09-19), accepted: this is a coarse-grid/set-size artifact and does
not apply to vector extents.** A vector record has no cell quantum, so neither the ceil
slack nor the clamp reaches it.

One residue survives, and only on the comparison side: `AnnoFinalCells_*` is a count
over `W·H` (`final_state_scanner.py:96`, `:136`), so the *old* measurement's domain is
the ceil'd grid, which spans up to one cell more than B per axis — or less, when the
clamp binds. When Phase 2 reports a coverage figure against a cell count, that is a
difference in domain, not in the thing measured. Name it; do not correct for it.

### 1.5 The cap envelope — **retracted as a data-loss risk; restated as a frame-stability one**

What the code does is unchanged: `view_basis.py:1038-1068` applies a cap envelope
*after* the annotation union, rebuilding `base_bounds` **re-centred on
`pre_annotation_bounds`** (`:1054-1065`):

    center_x = 0.5 * (pre_annotation_bounds.xmin + pre_annotation_bounds.xmax)
    new_xmin = center_x - 0.5 * clipped_w_ft

**Greg's call (2026-09-19), accepted:** the cap exists to bound the union of the model
and annotation *grids* — it is an allocation bound on `W·H`. A vector record is not
allocated per cell, so the cap clips nothing and loses nothing. My §1.5 as first
written treated a grid-sizing bound as a geometry bound. That was wrong.

**What does survive, and it is smaller and different.** The cap still moves B itself.
Under LOCKED item 2's framing — origin at B.min, coordinates relative to it — a cap
that fires re-centres the origin on the model crop, so the same annotation in the same
view yields *different frame-relative numbers* depending on whether the cap fired. That
is a reproducibility property of the chosen frame, not a loss of content, and it
disappears entirely if the record stores absolute view-local UV and lets the consumer
apply a frame (the `link_identity_resolver` precedent, §2.3). It is one input to Q3/Q4,
not an objection to the lock.

### 1.6 A↔B alignment in post — already emitted, nothing to build

Greg: *"Relationship of where model crop is relative to annotation extents is still
important to align them in post."* That relationship is already recorded and does not
need re-deriving. `compute_model_crop` returns `(render_bounds, offset)` where offset
is `(dxmin, dymin, dxmax, dymax)` to be **added** to `bounds_xy`'s corners to
reconstruct A (`color_id_buffer.py:1573-1589`, computed `:1611-1617`), and it ships in
the Stage A sidecar as `model_crop_offset_uv` (`:2737`) alongside `"bounds_xy"` = A
(`:2726`).

Two cautions carried from that docstring, both already written down there: the offset
is rectangle-to-rectangle reconstruction data, **not** a shift to apply a second time
to UV already decoded against A (`:1585-1589`); and it is `(0,0,0,0)` both when no
narrowing applied and when the crop could not be applied at all — in the latter case
`"bounds_xy"` is `None`, which is the only way to tell the two apart (`:2729-2736`).


---

## 2. The geometry contract: which projection per family, and is it valid outside the render crop?

> *Report whether `project_bbox_uv_and_near_face_w` is valid for content OUTSIDE the
> render crop.*

**Answer: valid. The assumption that actually needs checking is not in the projection
— it is in the bbox handed to it, and the two annotation paths in this repo already
disagree about that.**

**Correction of target (Greg, 2026-09-19): "Annotations are 2D so W is irrelevant."**
Accepted, and it improves the design. The reuse target is therefore
**`project_bbox_corners_uv` (`collection.py:837-893`)**, not
`project_bbox_uv_and_near_face_w` (`:896`). They are the same projection; the latter
additionally returns `min(w)` over the corners, which for a view-specific 2D
annotation is a depth the annotation channel never consults — nothing in
`rasterize_annotations` or `final_state_scanner` reads a w for an annotation, and
LOCKED item 1 of CLAUDE.md reserves depth for AREAL model elements.

Taking the narrower function is not merely "no worse". It is strictly better for
three reasons, all verified below: it shares `_bbox_world_corners`, so it **applies
`bbox.Transform`** and dissolves §2.4(a) for the new path; it **refuses** rather than
returning bbox-local corners when that transform cannot be applied; and unlike
anything in `annotation.py` it is **already covered** —
six cases in `tests/test_project_bbox_corners_uv.py`
(`:46`, `:52`, `:58`, `:63`, `:70`, `:81`), including the `bbox.Transform` path
(`:70`) and the refusal (`:81`).

### 2.1 The projection itself is crop-independent — verified, for both functions

`project_bbox_corners_uv` (`collection.py:837-893`): `_bbox_world_corners(...)`
(`:868`) → `world_to_view(corner, vb)` per corner (`:876`) → `min`/`max` over the
eight results (`:889-893`). `project_bbox_uv_and_near_face_w` (`:896-955`) is the
same, plus `min(p[2] ...)` (`:948`). `world_to_view` (`view_basis.py:115-130`)
delegates to `transform_to_view_uvw` (`:77-99`), a pure affine map: subtract origin,
dot with `right`/`up`/`forward`. **No reference to `bounds_xy`, `model_clip_bounds`, a
crop, `W`/`H`, or any clamp — in either.** It is exactly as valid at UV `(-10⁶, -10⁶)` as at the
grid centre. There is no floating-point cliff in B\A either: B\A is bounded by the
annotation expansion cap, a printed-inches quantity (`annotation.py:184-197`), not an
unbounded excursion.

### 2.2 Its production inputs are also crop-independent — verified

Both call sites pass `resolve_element_bbox(elem, view=None, ...)` —
`color_id_buffer.py:995-998` (HOST) and `:1060-1063` (LINK). `view=None` is the
model-space resolution, explicitly not view-scoped.

### 2.3 The existing consumer already treats out-of-frame as *no evidence*, deliberately

`tools/link_identity_resolver.py` reads the sidecar's `"bounds_xy"` — which is A
(`color_id_buffer.py:2726`) — at `:498-499`, and maps absolute UV into A's pixel space
at `:225`. Then `:290-299`:

    # Reject a candidate whose rectangle doesn't overlap the image at all
    # BEFORE clamping: clamping x0/x1 independently to [0, dim-1] would
    # collapse a wholly off-image rectangle into a fabricated one-pixel
    # sliver AT the image edge ... handing an off-view element fabricated
    # pixel evidence it never actually earned.
    if x1_raw < 0 or x0_raw > image_w - 1 or ...: return None

**This is the precedent the prompt points at, and its shape is: store UV absolute and
frame-free; apply the frame only at the consumer; reject rather than clamp.** That
argues against LOCKED item 2's "frame is B, quantized at analysis time" — see §6.3.

### 2.4 The actual unchecked assumption, and it is already broken in-tree

The annotation path does **not** resolve model-space bboxes. `rasterize_annotations`
calls `get_annotation_bbox(elem, view)` at `annotation.py:973`, which is
`elem.get_BoundingBox(view)` (`:893`) — **view-scoped**. Two things follow.

**(a) The two annotation paths disagree on `bbox.Transform`.**

`compute_annotation_extents` builds the eight local corners and applies the bbox's own
transform before projecting (`annotation.py:332-357`):

    TB = getattr(bbox, "Transform", None)
    if TB is not None:
        pts_to_check.extend([TB.OfPoint(p) for p in corners_local])

`_project_element_bbox_to_cell_rect_for_anno` builds the same eight corners from raw
`Min`/`Max` (`:1766-1775`) and projects them (`:1780`) with **no `Transform` lookup
anywhere in the function**. For an annotation whose `BoundingBoxXYZ` carries a
non-identity `Transform`, the extents pass and the rasterize pass compute different
rectangles for the same element. This is the identical failure mode
`_bbox_world_corners`'s own docstring was written to prevent
(`collection.py:752-759`).

**This defect does not have to be carried into Phase 2.** `_bbox_world_corners`
applies `bbox.Transform` at `:780-794` and, when both application attempts fail,
returns `None` rather than untransformed corners — with the reasoning written out at
`:805-810`: *"Returning them anyway would silently produce plausible-looking but wrong
data."* Adopting `project_bbox_corners_uv` therefore gets the correct `Transform`
handling and the refusal for free. The existing geometry path keeps the defect; that
is D-list, and LOCKED item 6 says it stays runnable as it is.

**(b) The docstring and the code contradict each other on coordinate space.**

`get_annotation_bbox`'s docstring claims (`annotation.py:885-887`):

    ✔ Uses element.get_BoundingBox(view) for view-space coordinates
    ✔ View coordinates are in feet (same as model coordinates)

But every consumer then applies `view_basis.transform_to_view_uv()` to that bbox
(`:1780`, and `:365` in the extents path). If the bbox really were view-space, that is
a *double* transform. The coherent reading is that the bbox is model-space and the
docstring is wrong — but "wrong docstring on the exact function the new design reads
its geometry from" is not a detail to inherit silently. Phase 2 must assert the space
it believes it is in, not restate this comment.

**(c) The view's crop is mutated mid-pipeline, and the annotation pass depends on it
being put back.**

`color_id_buffer.export_color_id_buffer_view` saves `view.CropBox`/`CropBoxActive`
(`:1980-1981`), overwrites them with A (`:2064-2065`), and restores in a
`_restore_step` (`:2534-2537`). `pipeline.py` runs that export at `:1109-1111` and
`rasterize_annotations` at `:1163` — **after**. So annotation bboxes are read under
the original crop *provided the restore ran*. `_restore_step` (`:2473`) is
best-effort: a failed restore leaves the view cropped to A, and every subsequent
`get_BoundingBox(view)` is then silently A-scoped. Nothing downstream can tell.

### 2.5 Verdict

| question | answer |
|---|---|
| Is the bbox→UV projection valid in B\A? | **Yes** — pure affine, verified `collection.py:837-893` and `:896-955` → `view_basis.py:77-99` |
| Are its production inputs crop-scoped? | **No** — `view=None`, `color_id_buffer.py:995,1060` |
| Is the `w` half of `project_bbox_uv_and_near_face_w` wanted here? | **No** — annotations are 2D; use `project_bbox_corners_uv` (`:837`) |
| Does that helper serve every annotation family? | **No** — AABB families only; polylines and loops need per-point projection (§2.6) |
| Is the *annotation* bbox source crop-scoped? | **Yes** — `get_BoundingBox(view)`, `annotation.py:893` |
| Is `Transform` handled consistently across the annotation paths? | **No** — applied at `:344-350`, absent at `:1766-1781` |
| Does any consumer already handle out-of-frame correctly? | **Yes** — reject-before-clamp, `link_identity_resolver.py:290-299` |

The risk is real but it is **not** where the prompt expected it. Reusing
`project_bbox_corners_uv` is safe and fixes `Transform` handling on the way. Reusing
`get_annotation_bbox` + `_project_element_bbox_to_cell_rect_for_anno` is not, and the
reason is `Transform`, not the crop.

What is **not** dissolved by the change of target: `get_annotation_bbox` is still the
only bbox source the annotation collector has, and it is still `get_BoundingBox(view)`
(`annotation.py:893`) under a crop this pipeline mutates and restores best-effort
(§2.4c).

### 2.6 Not one shape: what each family actually needs

**Greg, 2026-09-19:** *"The original version made distinctions between annotation
families and text (AABB), filled regions (nested boundaries), and lines (both linear
and curved). If an L or diagonal line comes back as a rectangle we'd overstate its
contribution."*

**Accepted, and it is the correction with the largest effect on the schema.** The
existing geometry path already makes these distinctions — they are not a new
requirement, they are an existing behaviour rev 2 would have thrown away. Reading the
dispatch in `rasterize_annotations` (`annotation.py:1058-1268`):

| family | geometry the current path uses | citation | true vector form |
|---|---|---|---|
| TEXT | bbox, filled | `:1136-1152` | **AABB** — bbox is correct |
| TAG / keynote | bbox, outline only | `:1154-1156` | **AABB** — bbox is correct |
| DIM | `Dimension.Curve` endpoints → line | `:1065-1066` | **polyline** |
| LINES | `Location.Curve` endpoints → line + band | `:1167-1168`, band `:1183` | **polyline** |
| REGION | `GetBoundaries()` → `Tessellate()` → outer minus holes | `:1247-1250`, and `_rasterize_filled_region_shape` `:1622-1640` | **nested loops** |
| DETAIL | bbox, filled (falls to the `else:`) | `:1269` → `:1313-1328` | **unresolved — see Q7** |
| OTHER | bbox, filled | same `else:`, `:1313-1328` | **unresolved — see Q7** |

**The overstatement is not marginal, and it is arithmetic rather than measurement.**
For a straight segment of length L at angle θ to the U axis, the AABB has area
`L²·|sin θ·cos θ|`, maximised at θ=45° where it is `L²/2`; the segment itself has zero
area. An L-shaped polyline's AABB additionally covers the empty quadrant — for two
equal legs of length L, `L²` against a figure with no area at all. Only θ=0 and θ=90°
give an AABB that does not overstate, and those are exactly the cases where a bbox and
a polyline agree. So "bbox for everything" is worst precisely for the diagonal and
dog-leg leaders that tags and dimensions actually produce.

**Schema consequence.** LOCKED item 3 carries channel, category and provenance as
plain strings; the record needs a fourth discriminant of the same kind — a `shape`
string over `aabb | polyline | loops` — plus a payload whose form that string selects.
Storing a bbox for all of them and a shape label beside it would be worse than either,
because the label would assert a precision the payload does not carry.

**Where each one's UV comes from, and the one place §2's helper does not reach.**
`project_bbox_corners_uv` serves `aabb` and fixes `bbox.Transform` there (§2). It does
not serve the other two, and it should not be made to: a polyline and a loop set are
sequences of curve points, not a box, and they have no `BoundingBoxXYZ` and therefore
no `Transform` ambiguity to resolve. Their UV is `vb.transform_to_view_uv(pt)` per
tessellated point — the same call `compute_annotation_extents` already makes at
`:365` and `_rasterize_filled_region_shape` makes at `:1632`.

**What the vector path must NOT reuse:** `_rasterize_filled_region_shape` tessellates
straight into *cell* coordinates (`_uv_to_cell` at `:1638`) inside the same loop that
projects to UV. Sub-cell precision is discarded before anything could record it. The
vector path has to stop at UV. That is a reason to write a new extraction, not a
defect in the raster path, which legitimately wants cells. Whichever projection Phase 2 calls, it is fed a view-scoped bbox.

---

## 3. Defects found while reading. Listed, not fixed.

All confirmed at `d05038a`. None are touched by this document.

### D1 — truncation toward zero fabricates a cell at the grid edge

`_project_element_bbox_to_cell_rect_for_anno` (`annotation.py:1790-1793`) and
`_uv_to_cell` (`:1499-1500`) both use `int(...)`, which truncates toward zero, where
floor is meant:

    $ python3 -c "print(int(-0.7), __import__('math').floor(-0.7))"
    0 -1

Any annotation whose UV lands in the half-open band `[B.min - cell, B.min)` maps to
cell **0** rather than to −1. `_stamp_rect_outline` then clamps with `max(0, ...)`
(`:1513-1516`) and stamps it. This is exactly the "fabricated one-pixel sliver AT the
image edge" that `link_identity_resolver.py:290-299` rejects by name, reproduced in
the annotation rasterizer. It is live for every view where the cap of §1.5 pushed
annotation outside B.

`rasterize_polygon_to_anno` (`raster.py:1279-1280`) has the same `int()` but clips the
polygon to bounds first (`:1273`), so it cannot reach a negative argument.

### D2 — `anno_meta` already carries a vector bbox, in an unverified space, that nothing reads

`rasterize_annotations` writes, per element (`annotation.py:1000-1006`):

    "bbox_min": (bbox.Min.X, bbox.Min.Y, bbox.Min.Z),
    "bbox_max": (bbox.Max.X, bbox.Max.Y, bbox.Max.Z),

`grep -rn "bbox_min\|bbox_max" vop_interwoven/ tools/ tests/` returns **only those two
lines**. Nothing reads them. They are raw `Min`/`Max` with `Transform` discarded (see
§2.4a), and they ride through `ViewRaster.to_dict()` (`raster.py:2044`) into the CSV
raster cache (`csv_export.py:1549`, `:2123`, `:2182`).

This matters twice over: a half-built vector record already exists, and its coordinate
space has never been established.

### D3 — unreachable `"KEYNOTE"` branch

`rasterize_annotations` dispatches `elif mode in ("TAG", "KEYNOTE")` at
`annotation.py:1154`. `classify_keynote` returns only `"TAG"` or `"TEXT"`
(`:849`, `:858`, `:864`, `:869`), and `classify_annotation` delegates to it for
`OST_KeynoteTags` (`:811`). No code path produces `"KEYNOTE"`. Were one to,
`final_state_scanner._normalize_anno_type` (`:58-66`) would bucket it as `OTHER`.

### D4 — ~35 lines of dead FilledRegion code

`elif mode == "REGION":` at `annotation.py:1247` already catches REGION. The `else:`
at `:1269` then opens with `if mode == "REGION":` (`:1271`) and reimplements boundary
tessellation — unreachable. It carries two of the file's eight discarding handlers
(`:1289` continue, and the `except Exception: stamped = False` below it).

### D5 — "Option A / Option B" prose describes an exclusive choice; the code runs both

`annotation.py:1177-1185`, LINES branch:

    # Option A: Render as Bresenham line (single-pixel width)
    _stamp_line_cells(...); stamped = True
    # Option B: Render as oriented band (2-cell width, archive parity)
    _stamp_detail_line_band(...)
    stamped = True

Both run; `stamped = True` is assigned twice. Detail lines are stamped as a Bresenham
line *and* a 2-cell band. `AnnoFinalCells_LINES` counts the union.

### D6 — the 4-way cell partition is computed twice and nothing composes the two copies

`csv_export.compute_cell_metrics` (`:284`) dispatches on `NUMPY_AVAILABLE` (`:292-296`)
to `_compute_cell_metrics_np` (`:299`) or `_compute_cell_metrics_py` (`:371`). The
`"ink"`/`"any"` predicates are written out independently in each. The store searched
for a test that runs both against the same raster: `grep -rn compute_cell_metrics
tests/` → `tests/test_csv_model_presence_mode.py:23,37,51,67` (calls the dispatcher,
so exercises whichever backend is installed) and two fault-injection monkeypatches
(`test_patch5_metrics_passthrough.py:10,65`, `test_fault_injection_csv_export.py:45`).

**Nothing composes them.** This is CLAUDE.md's recurring defect class 1 verbatim, and
§0.1 shows the suite genuinely runs under both numpy-present and numpy-absent
environments, so both copies are live.

### D7 — stale comment inside the function the design would replace

`_project_element_bbox_to_cell_rect_for_anno`'s body comment (`annotation.py:1750-1753`)
reads "For simplicity, we'll use the world coordinates directly since the view basis
should handle the transformation" — while the code 25 lines later does call
`view_basis.transform_to_view_uv` (`:1780`). CLAUDE.md's fourth recurring shape:
prose pointing at code that has moved out from under it.

### D8 — `print()` for error reporting, against the Diagnostics Contract

`annotation.py` contains 14 `print(` calls (`grep -c`), of which the `[WARN]` ones at
`:119`, `:167`, `:174`, `:321`, `:328`, `:550`, `:628`, `:722`, `:814`, `:860`,
`:868`, `:899`, `:1808` are error reporting. CLAUDE.md's Diagnostics Contract: *"Do
not use `print()` for error reporting."* Several sit in functions that have no `diag`
in scope and say so (`:33`, `:764`).


### D9 — curved detail lines and dimensions are silently reduced to their chord

The LINES branch takes `curve.GetEndPoint(0)` and `GetEndPoint(1)` and nothing else
(`annotation.py:1167-1168`); the DIM branch does the same (`:1065-1066`). An arc,
ellipse or spline detail line is therefore rasterized as the straight segment between
its endpoints. Meanwhile the REGION path on the same `Curve` objects calls
`c.Tessellate()` (`:1631`) — so the capability is present in this very file and simply
is not applied to the two line families.

Greg named this shape directly (*"lines (both linear and curved)"*). It is a defect in
the geometry path, it is on the D-list, and per LOCKED item 6 it is **not** Phase 2's
to fix — but Phase 2 must not inherit it: the vector polyline has to tessellate, or it
reproduces the same understatement in the new store while the AABB families overstate
in the old one, and the comparison would then be measuring two opposite errors at once.

### D10 — the detail-line band tests the cell corner, not the cell centre

`_stamp_detail_line_band` iterates candidate cells and tests `_point_in_quad(cx, cy,
corners)` (`:1462`) — the cell's **integer corner**. Every other containment test in
the pipeline uses the centre, `bounds.min + (i + 0.5)·cell` (`raster.py:36-37`,
`:422-423`, `:916-917`, `:1498`). The band is therefore displaced by half a cell on
both axes relative to every other channel.

This lands directly in `AnnoFinalCells_LINES`, which §6 names as the comparison target,
so it is worth knowing about before the residual is interpreted. The band's width is
itself `cfg.linear_band_thickness_cells`, default 1.0 (`config.py:79`, read at
`annotation.py:1431`) — a quantity in cells, with no model-space meaning. See Q8.
---

## 4. Invariants asserted in a comment but not in a test

Listed as required. **Not fixed, and Phase 2 should not fix them either** unless one
is load-bearing for the patch.

### 4.1 In `revit/annotation.py` — none of these is asserted anywhere

Every entry here is unasserted for the same reason: the file has zero coverage in the
collected suite (§0.3). Store searched: `tests/` at `d05038a`.

| claim | where |
|---|---|
| "annotation-like elements that must **never** drive extent expansion" | `:13` |
| "Extent drivers are annotations that **can exist beyond the view crop**" | `:50` |
| "✔ Only processes extent drivers (text, tags, dimensions)" | `:142` |
| "✔ Respects annotation crop when active" | `:143` |
| "✔ Enforces hard cap on expansion when annotation crop inactive" | `:144` |
| "✔ Matches SSM `_compute_2d_annotation_extents` logic" | `:145` |
| "**IMPORTANT:** This collects ONLY user annotations" | `:455` |
| "✔ ViewSpecific=True filter **automatically** separates detail lines from model lines" | `:486` |
| "Model lines (ViewSpecific=False) → MODEL" | `:473`, `:488` |
| "✔ Matches SSM exporter classification logic" | `:754` |
| "Material Element Keynotes → TAG; User Keynotes → TEXT" | `:824-825` |
| "✔ Uses `element.get_BoundingBox(view)` for **view-space** coordinates" | `:890` — and see §2.4b |
| "✔ View coordinates are in feet (same as model coordinates)" | `:891` |
| "✔ Bounding box **includes leader lines** for tags" | `:892` |
| "✔ Rotated text bounding boxes **include full extents**" | `:893` |

The last three are the ones this design would rest on, and they are claims about
Revit's API, so no unit test can settle them — only a Dynamo probe can. That is an
honest limit, and §7 treats it as one.

### 4.2 Elsewhere in scope

| claim | where | status |
|---|---|---|
| "**INVARIANT:** TotalCells == Empty + ModelOnly + AnnoOnly + Overlap" | `csv_export.py:290` | **unasserted.** `tests/test_csv_model_presence_mode.py` checks individual buckets; no test sums them. |
| "Return dict is **identical in both cases**" (np vs py) | `csv_export.py:288` | **unasserted** — see D6. |
| "annotations should use full raster bounds (not `model_clip_bounds`)" | `raster.py:1253` | **unasserted** — `rasterize_polygon_to_anno` has no test at all. |
| "guaranteeing both values come from the **identical** corner set" | `collection.py:898-900` | asserted indirectly by `tests/test_near_face_w_collection.py`; not by a property over generated transforms. |
| 8-way partition is exhaustive and disjoint | `final_state_scanner.py:155-170` | **asserted** — `tests/test_final_state_scanner.py:51-64` sums the eight against `TotalCells`. |
| "Intersecting keeps the render crop always inside `bounds_xy` regardless" | `color_id_buffer.py:1568-1571` | **asserted** — `tests/test_color_id_buffer.py:232-310`. |

---

## 5. What `AnnoFinalCells_*` actually measures — the comparison target, stated exactly

LOCKED item 5 fixes the comparison target. Its semantics, from code, because the
consumer-side collapse has to be written against the real thing:

1. `raster.anno_key` is **one `int32` per cell** (`raster.py:581`, `:593`), initialised
   to −1.
2. Every writer **overwrites**: `anno_key[...] = anno_idx` at `annotation.py:1509`
   (`_stamp_cell`), `:1151`, `:1267` and `:1328` (TEXT / legacy / REGION-fallback fills), `raster.py:1330`.
   **The channel is last-writer-wins, not a union.** Two annotations over one cell:
   the second erases the first.
3. `anno_idx = len(raster.anno_meta)` is taken *before* stamping
   (`annotation.py:982`) and appended unconditionally (`:1000`), so `anno_meta` has one
   entry per element *processed*, including elements that stamped zero cells.
4. `scan_final_state_totals` reads `anno_idx = anno_keys[idx]`, `has_anno = anno_idx != -1`
   (`final_state_scanner.py:141-142`), buckets by `anno_meta[anno_idx]["type"]` through
   `_normalize_anno_type` (`:174-178`), into the seven `ANNO_BUCKETS` (`:8`).
5. Therefore `AnnoPresentFinal == Σ AnnoFinalCells_*` exactly, and each is **a count of
   grid cells whose surviving last writer had that type** — never an area, never a
   count of elements, never a union across overlapping annotations.
6. Domain is `W·H` over B, subject to the `max_W`/`max_H` clamp of §1.4.

**This is what forces LOCKED item 5's written collapse, and it is worse than "binary
vs continuous".** A per-element vector record and a last-writer-wins per-cell count
differ in *two* independent ways:

- **binary vs continuous** — the declared `>0` threshold handles this;
- **per-element vs last-writer-wins** — the threshold does **not** handle this. Where
  two annotations of different types overlap, the vector store holds both records;
  `AnnoFinalCells_*` credits exactly one, chosen by iteration order.

Total `AnnoPresentFinal` is unaffected by the second — only the split across the seven
buckets. Phase 2's comparison must state both collapses in writing, or the
per-category residual will be read as a bug in the new path when it is the old path's
tie-break.

**And this is the point of the exercise, not an inconvenience.** Greg (2026-09-19):
*"Overlaps will now be managed in channels so the signal can be resurfaced."* The
signal `anno_key` destroys at `annotation.py:1509` is exactly the thing a per-element
store keeps and a channelled analysis can recover. So the residual is not noise to
tolerate — where it is non-zero it is the recovered signal, and Phase 2 should expect
it and report it per channel rather than treating agreement as the success
criterion.

---

## 6. RAISE — eight questions; three answered, five open. Defaults still withheld.

Greg's 2026-09-19 review answered Q1, closed Q2, largely resolved Q4 and narrowed Q5.
It also opened two new ones — Q7 and Q8 — that his shape correction exposes and that I
will not answer by default. Status is stated at each head.

### Q1 — the channel vocabulary — **ANSWERED by Greg, 2026-09-19**

> *"Channels element type, parameter, etc. (user keynotes vs element or material
> keynotes)."*

**So `channel` is finer than `category` and sourced from the element's type and
parameter values, not from its Revit category.** That resolves the gap rev 2 flagged —
`channel` and `category` are two fields because they answer different questions:
category is the Revit-category-derived bucket the old path already emits; channel is
the type/parameter discriminator that lets two annotations sharing a category be held
apart, which is what makes the collision recoverable.

**The worked example is a collapse this document already found independently.**
`classify_keynote` (`annotation.py:820-870`) reads exactly two things:
`elem.LookupParameter("Keynote").AsString()` (`:842-844`) and
`elem.TaggedLocalElementId` (`:855-856`). It returns `"TEXT"` when the key is empty
and `"TAG"` in every other case (`:849`, `:858`, `:864`, `:869`). **Material and
element keynotes are therefore indistinguishable in the current output** — both land
in `TAG` — even though the function's own docstring lists them as three kinds
(`:824-826`). That is §7.Q6's collapse #3, and it is exactly the distinction Greg
names as a channel.

**Two things this does not settle, and I am not deciding either.**

**(a) The read that separates them does not exist in this codebase.** Nothing in
`vop_interwoven/` reads a keynote's kind beyond those two calls; `GetTypeId()` appears
once in the whole package, in `linked_documents.py:849`, for import instances. So the
channel resolver needs a Revit-API read that is not written here yet, and which read
that is — tag family/type name, the tagged reference's own kind, a keynote-table
lookup — is a Revit-API question no unit test in this repo can settle. It belongs to
§8's limit #1: a Dynamo probe, not a test. **Which read should the probe establish?**

**(b) "Parameter" is open-ended, and LOCKED item 3 depends on it not being.** Item 3
says the vocabulary is plain strings so that *"a vocabulary change must be a data
migration, not a schema one."* That holds only if the set of channel values is
enumerable. A resolver that does ad-hoc `LookupParameter` calls per element produces an
unbounded vocabulary and makes item 3 unenforceable in practice. **Should the channel
resolver be driven by an explicit declared table — (element type or category, parameter
name, value → channel string) — with anything unmatched resolving to one named
fallback?** That is the shape that keeps the vocabulary auditable, and it is a design
commitment I want from you rather than one I install.

### Q2 — overlap semantics per channel — **CLOSED by Greg, 2026-09-19**

> *"Overlaps will now be managed in channels so the signal can be resurfaced."*
> *"Overlap semantics, if they survive, will be analysis side."*

**Accepted, and it dissolves the question at the emitter rather than answering it.**
The store is one record per annotation element. Two annotations covering the same
ground produce two records; nothing merges, so there is no union-vs-sum decision to
make at emit time and no `max`/clamp/accumulate to specify. My framing of Q2 assumed
the record was a per-cell coverage array, which would have reintroduced the very
collision the vector form exists to remove.

Two consequences for Phase 2, both narrowing:

1. **LOCKED item 4's "continuous fill fraction" is a derived quantity, not a stored
   one.** A per-element *shape* in UV (§2.6 — AABB, polyline or loop set, not a
   rectangle) yields per-cell coverage by intersection at analysis time, for whatever
   grid the analysis chooses. Storing a fill fraction would bake in a grid and a cell
   size, which is what LOCKED item 1 rejected the raster pass for. Phase 2 should
   store the shape; if it also stores coverage, it must say against which grid.
2. **The emitter gets no threshold, no tolerance and no accumulation rule** — which
   suits the DO-NOT on introducing a constant with no exercising instance.

### Q3 — persistence location and format

The one precedent in-tree is the Stage A sidecar:
`<cfg.output_dir>/color_id_buffer/<safe_view_name>_<view_id>.json` +
`.tiff` (`color_id_buffer.py:1632-1642`), written
`json.dump(state_out, f, indent=2, sort_keys=True)` (`:2787`), path returned as
`"sidecar_path"` (`:2819`).

The CSV precedent is the streaming writer: `views_core_{date}.csv`,
`views_vop_{date}.csv`, `views_occlusion_{date}.csv`, `views_perf_{date}.csv`
(`streaming.py:231`, `:242`, `:253`, `:266`), consumed by
`tools/verify_invariant_core.py`'s `ROLE_PATTERNS` (`:158-161`).

**Question.** Three live options, no default:
(a) a new per-view JSON sidecar beside the Stage A one — isolated, Phase-2-only, but
invisible to `verify_invariant_core`'s role model;
(b) a fifth streaming CSV role (`views_anno_*.csv`) — joins the existing tooling, but
a vector record per *element* does not fit a per-*view* row, so it would need its own
grain and `verify_invariant_core` would need a new role;
(c) ride inside `raster.anno_meta` and out through `to_dict()` (`raster.py:2044`) —
zero new I/O, and D2 shows the slot already exists; but it lands in the CSV raster
cache whose size is already a concern, and it is not independently readable.

The frame question (§6.4 / Q-frame below) partly decides this: a frame-free record can
be written once and re-framed by any consumer; a B-framed record cannot.

### Q4 — replace or supplement `_project_element_bbox_to_cell_rect_for_anno`? — **largely resolved by Greg's 2D correction**

LOCKED item 6 says nothing is deleted and the geometry path stays runnable and
emitting — it is the only comparison target that exists (§0.4 confirms nothing else is
committed). So `rasterize_annotations` **survives** as a matter of the lock. That part
was never open.

**The open part closed itself once "annotations are 2D so W is irrelevant" picked the
target — for the AABB families.** My three-way (supplement / extract-and-share /
extract-and-gate) assumed Phase 2 would have to *write* a corrected projection and then
decide whether the old call site shared it. For `aabb` it does not:
`project_bbox_corners_uv` (`collection.py:837-893`)
already exists, already applies `bbox.Transform` via `_bbox_world_corners`
(`:780-794`), already refuses rather than returning bbox-local corners (`:805-810`),
and already has six tests (§2). So:

- **supplement, by calling an existing helper.** `_project_element_bbox_to_cell_rect_for_anno`
  is untouched; the new path never calls it.
- **No fourth copy of a UV→cell mapping is created**, because the new path has no
  UV→cell step at all. Q2 being closed means the record is the UV *shape* (§2.6);
  cells are an analysis-side intersection. The three existing copies
  (`annotation.py:1790`, `:1499`, `raster.py:1279`) stay at three. This survives §2.6
  unchanged — it is the reason the vector path must not reuse
  `_rasterize_filled_region_shape`, which folds UV→cell into its tessellation loop
  (`:1638`).
- **D1 and §2.4(a) are therefore not inherited** by the new path, and not fixed on the
  old one. They stay on the D-list.

For `polyline` and `loops` there is no existing helper and Phase 2 does write the
extraction — but it writes a *tessellate-to-UV* walk, not a projection, and the per-
point call it uses (`vb.transform_to_view_uv`) is already the one both existing
annotation paths use (`:365`, `:1637`). So still no new mapping, and still nothing
shared with the old call site.

**What remains genuinely open is smaller and belongs to Q3:** whether the new record
is attached to the existing `raster.anno_meta` entry (where D2 shows an unread
`bbox_min`/`bbox_max` slot already sits) or written to a store of its own. That is a
persistence question, not a projection one.

### Q5 — what provenance the collector can honestly distinguish — **narrowed, still open**

Verified at `annotation.py:537-737`. The collector calls `collect_category(...)` once
per `BuiltInCategory` (`:635-666`), from `FilteredElementCollector(doc, view.Id)` on the host doc.
At that site it can honestly distinguish:

| distinguishable | evidence | notes |
|---|---|---|
| **which `BuiltInCategory` collector produced it** | the `label=` arg, `:635-666` | already accumulated into `cat_counts` (`:594-600`), already diagnostic-only |
| **`elem.Category.Id.IntegerValue`** | `:986-989` | already captured as `cat_id` into `anno_meta` (`:1003`) |
| **`ViewSpecific == True`** | `:546` | a hard filter; every collected element passes it, except keynotes |
| **keynote-vs-not** | `:668-701`, classified at `:679` | keynotes bypass the `ViewSpecific` filter entirely — a real asymmetry |
| **`isinstance(elem, FilledRegion)`** | `:561` | forces REGION ahead of classification |
| **a bbox exists in this view** | `:554-556` | `bbox is None` → skipped, so "collected" already implies a view bbox |

**It cannot distinguish `HOST | LINK | DWG` — they are not reachable from this site.**
There is no linked-document collector in `annotation.py` at all. Every record would
carry `source_type == "HOST"` (`core/source_identity.py:17`), a constant.

**Confirmed out of scope by Greg (2026-09-19): "No linked annotations scoped at this
time."** So this is not a gap to close in Phase 2 — it is a dimension that does not
exist yet. What it removes is the only reading of `provenance` that would have matched
`element_meta`'s. The question below is what is left.

**Question, narrowed.** With source type off the table, does `provenance` mean
(a) the collector label — which category collector found it, which distinguishes the
keynote path's different filtering (keynotes bypass the `ViewSpecific` gate entirely);
(b) the classification route — `forced_region` / `type_override` / `classify_keynote`
— which is the thing that would actually explain a surprising `category` value; or
(c) drop the field until a second source exists to distinguish?

(b) is the only one carrying information the record does not already hold via
`cat_id`, and Q6's four collapses are exactly what it would make visible. Still not
adopting it unasked.

### Q6 — do `classify_annotation`'s buckets map 1:1 onto `category`?

**Almost, and the exceptions are the interesting part.**

`classify_annotation` (`annotation.py:740-819`) returns
`TEXT | TAG | DIM | DETAIL | LINES | REGION | OTHER`. `final_state_scanner.ANNO_BUCKETS`
(`:8`) and `verify_invariant_core.ANNO_CATEGORIES` (`:172`) are the same seven, same
order. So the nominal mapping is 1:1.

The four exceptions:

1. **`classify_annotation` is not always the classifier.** `collect_category` takes
   `anno_type_override` and passes a hardcoded literal for every category it collects
   — `"TEXT"` (`:635`), `"DIM"` (`:639`), `"TAG"` (`:654`), `"REGION"` (`:658`),
   `"LINES"` (`:662`), `"DETAIL"` (`:666`). `classify_annotation` runs **only** when no
   override is given (`:576-580`), which in the current collector is never. It is
   effectively dead in the collection path and alive only in
   `_normalize_anno_type`'s expectations.

2. **FilledRegion is forced ahead of everything** (`:561-563`), so a FilledRegion
   living in Detail Items is REGION, not DETAIL. Deliberate and commented.

3. **Keynotes never get a literal** — they go through `classify_keynote` (`:679` in the
   collector, `:811` in `classify_annotation`), the one genuine runtime classification, returning TAG or TEXT. So two
   source categories collapse into buckets that also have their own source category.

4. **`OTHER` is unreachable from the collector.** Every collected element got a
   literal, and keynotes get TAG/TEXT. `OTHER` can only arise in
   `_normalize_anno_type`'s fallback (`final_state_scanner.py:66`) for a type not in
   `ANNO_BUCKETS` — e.g. D3's `"KEYNOTE"`, which nothing produces either. Meanwhile
   `verify_invariant_core.py:148-150`, `:1601-1605` goes to real trouble to insist
   `AnnoCells_OTHER` is "a real annotation bucket".

**Question.** Does `category` on the new record mean (a) the seven, unchanged, which
keeps the comparison to `AnnoFinalCells_*` one-to-one and inherits the collapses above;
or (b) the source `BuiltInCategory`, which is strictly finer, is already captured as
`cat_id` (`:1003`), and would make the keynote and FilledRegion collapses visible —
at the cost that the comparison to `AnnoFinalCells_*` now needs a stated fold?

(a) is the cheap answer; (b) is the one that would show whether the collapses matter.
Not mine to pick.

### Q7 — DETAIL components and OTHER: what shape? — **NEW, opened by the §2.6 correction**

Greg's shape list names text (AABB), filled regions (nested boundaries) and lines
(linear and curved). It does not name **detail components**, and they are neither.

Today they fall through every branch to the legacy bbox fill
(`annotation.py:1269` → `:1313-1328`). A detail component is a family instance whose
2D content is arbitrary linework — an L-shaped bracket, a hatched section of insulation,
a diagonal brace. The §2.6 arithmetic applies to it in full: its AABB overstates for
exactly the same reason a diagonal line's does, and `AnnoFinalCells_DETAIL` currently
carries that overstatement.

**Question.** Does `DETAIL` get (a) `aabb`, preserving current behaviour and the
current overstatement, with the record honestly labelled `aabb` so a consumer knows
not to read area from it; (b) its own geometry extraction — the family instance's
view-specific curves, which is the largest single piece of work in Phase 2 and has no
existing helper in this file; or (c) `aabb` now with the record's `shape` field making
(b) a later, non-breaking addition?

Same question for `OTHER`, which shares the branch — though §7.Q6 shows `OTHER` is
unreachable from the current collector, so it may be moot.

### Q8 — a polyline has no area: what is a line's contribution? — **NEW**

The raster path gives every detail line a band of `cfg.linear_band_thickness_cells`,
default 1.0 (`config.py:79`, read at `annotation.py:1431`), stamped by
`_stamp_detail_line_band` (`:1394-1464`). **That width is expressed in cells.** It has
no model-space meaning: halve the cell size and the line's footprint in feet halves
with it.

In vector form the problem inverts. A polyline is a 1-dimensional figure with **zero
area**, so under any area-based coverage measure a detail line contributes nothing at
all — which understates it as badly as the AABB overstated it. Greg's objection was to
*overstating* a diagonal; the mirror failure is available here for free and it would be
just as wrong.

**Question.** For `polyline` records, is the contribution
(a) **a width carried on the record**, and if so from where — Revit's line weight
(model-meaningful, but a pen weight is a printed-output property, not a geometric one),
a config constant (reintroduces the grid-dependence LOCKED item 1 rejected the raster
pass for), or the annotation's own type;
(b) **length, not area** — a different unit for this shape kind, which the channel
separation makes tractable but which means the per-channel totals cannot simply be
summed; or
(c) **zero area, reported as such**, treating a line's presence as a count rather than
a magnitude?

This is the one question where I think the answer changes what the measurement *means*,
not just how it is stored, so I would rather leave it entirely to you than pick the
tidiest option.

---

## 7. Two things Phase 1 could not settle, stated as limits rather than assumed away

1. **The three Revit-API claims in §4.1** — leader lines included, rotated text
   extents included, bbox units/space — cannot be settled by any unit test, because the
   fake harness cannot model Revit's bbox semantics. They can only be settled by a
   Dynamo probe against a real model, in the shape of
   `tests/dynamo/probe_stage_a_image_alignment.py`. If the vector record's meaning
   depends on any of them, Phase 2 owes a probe, not a test.

2. **LOCKED item 1's "~50 dpi on capped views"** is not re-proven here. The store that
   would prove it — a production run's `views_perf_*.csv` and Stage A sidecars — is not
   committed (§0.4). The decision stands as locked; the figure behind it is carried
   forward, not verified at `d05038a`.

---

## 8. If approved, what Phase 2 is and is not

**Is:** a PATCH to `vop_interwoven/revit/annotation.py` adding a per-element vector
record alongside the existing stamping — a **shape-discriminated** UV figure
(§2.6: `aabb` via `project_bbox_corners_uv`, `polyline` and `loops` via
tessellate-to-UV), not a coverage array and not a bbox for everything — plus the
persistence chosen in Q3, the channel resolver shaped by Q1(b), a comparison harness
that states both collapses of §5 in writing and reports the per-channel residual as
signal, and a first Revit fake adequate to reach the collection path (§0.3).

**Is not:** any fix to D1–D10. Any change to `classify_annotation`, the strategy
tracker, or geometry extraction. Any deletion. Any golden set. Any new tolerance or
constant without an exercising instance.

**Must hold at the end:** `1111 passed, 2 xfailed` plus whatever Phase 2 adds — and the
delta stated explicitly, because CLAUDE.md records that a truncated rewrite once
deleted 7 cases and only the total caught it. Discarded-handler total stays 179.
`check_no_bare_except.py` stays clean.
