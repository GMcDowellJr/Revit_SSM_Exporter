# Frame-anchor determination (Phase 1, read-only)

Baseline: `main` @ a9051f2 (PR #199, P0 + I1 landed).
Method: code reading only. No code changed in this phase.

## Evidence availability (read this first)

The two runs named in the task are **not present in this container**:

- byColor RunId `20260917T104744` — absent
- byGeom  RunId `20260917T112657` — absent

There is no `~/Documents/_metrics`, no `color_id_buffer/` sidecar/TIFF tree,
and nothing matching `*KSRF*` anywhere on this filesystem. `tools/colorid_to_
occupancy.py` is likewise **not in the repo**; the copy analysed below is the
one supplied out-of-band during this session (referred to here as
`colorid_to_occupancy.py (supplied copy)`).

Consequence: Q1, Q2 and Q3 are answered fully from code. **Q4 is answered
structurally but its per-view numbers cannot be produced here** — see Q4.

## The three rectangles, named precisely

| | Name in code | Where it lives |
|---|---|---|
| A | render crop / `crop_bounds_xy` | `compute_model_crop()` result, `color_id_buffer.py:1539` |
| B | `raster.bounds_xy` | `bounds_result["bounds_uv"]`, `view_basis.py:1194` |
| C | exported pixel rect | Revit `ExportImage` output, measured into `resolution.actual_w/actual_h`, `color_id_buffer.py:1463-1476` |

A fourth quantity matters and is **not** a rectangle: the **grid extent**
`W * cell_size_ft_effective` by `H * cell_size_ft_effective`. Much of the
confusion in this area comes from treating it as if it were B.

---

## Q1 — Is B derived from A, or are both derived independently?

**Neither. A is derived from B.** The dependency runs the other way round
from how the question is posed.

Chain, in execution order:

1. `pipeline.py:1721-1733` calls `resolve_view_bounds(view, policy={... buffer_ft, cell_size_ft, max_W, max_H})`.
2. `view_basis.py:902-919` — when the crop box is active, **both** rectangles
   are projected from the *same* source, `view.CropBox`'s 8 corners through
   the view basis (`xy_bounds_from_crop_box_all_corners`, `view_basis.py:341`):
   - `base_bounds = xy_bounds_from_crop_box_all_corners(view, basis, buffer=buffer_ft)` (`view_basis.py:914`)
   - `model_bounds = xy_bounds_from_crop_box_all_corners(view, basis, buffer=0.0)` (`view_basis.py:917`)
   They differ **only** by `buffer_ft` at this point.
3. `view_basis.py:1008-1035` — `base_bounds` may then be replaced wholesale by
   `compute_annotation_extents(...)`, setting `anno_expanded=True`.
   `model_bounds` is **not** touched. This is where A and B genuinely part.
4. `view_basis.py:1040-1068` — cap envelope: only if `anno_expanded` **and**
   the grid exceeds `max_W`/`max_H`, `base_bounds` is re-cut to
   `min(cur, max)` ft and **re-centred on the pre-annotation centre**.
5. `view_basis.py:1194-1196` returns `bounds_uv` (→ B) and `model_bounds_uv`.
6. `pipeline.py:1735` `bounds_xy = bounds_result["bounds_uv"]` → `ViewRaster(..., bounds=bounds_xy)` (`pipeline.py:1777-1779`); `pipeline.py:1788` `raster.model_clip_bounds = bounds_result.get("model_bounds_uv")`.
7. `color_id_buffer.py:2051-2053` — **A is computed from B**:
   `render_bounds, model_crop_offset_uv = compute_model_crop(raster.model_clip_bounds, raster.bounds_xy)`.

The derivation (`color_id_buffer.py:1591-1616`) is a **four-sided
intersection**, not a preference:

```
A.xmin = max(model_clip_bounds.xmin, B.xmin)
A.ymin = max(model_clip_bounds.ymin, B.ymin)
A.xmax = min(model_clip_bounds.xmax, B.xmax)
A.ymax = min(model_clip_bounds.ymax, B.ymax)
```

with `A = B` (and offset `(0,0,0,0)`) when `model_clip_bounds is None`
(`:1595-1596`) or when the intersection is degenerate (`:1603-1606`).

**Invariant: A ⊆ B, always.** A is never wider than B on any side.

`A == B` exactly when the view took the crop path with `buffer_ft == 0`,
was not annotation-expanded, and did not hit the cap envelope. Then
`model_bounds == base_bounds` and the intersection is the identity.

A is set on the view as `view.CropBox` (`color_id_buffer.py:2063-2064`) and
recorded verbatim — *not recomputed* — into the sidecar's `bounds_xy` field
(`color_id_buffer.py:2694`). **The sidecar field named `bounds_xy` holds A,
not B.** B is recoverable only as `A - model_crop_offset_uv`
(`decode_stage_a_color_id.py:678-685`, output field `grid_bounds_uv`).

> Naming hazard worth writing down: `sidecar["bounds_xy"]` = A;
> `raster.bounds_xy` = B. Same name, different rectangles.

---

## Q2 — Where is B anchored relative to A, and where does cell slack land?

### The premise needs one correction first

**B is never rounded up to whole cells.** B is a raw feet rectangle and stays
one. What gets rounded is the **grid**:

```
W = max(1, ceil(width_ft  / cell_size_ft_effective))     view_basis.py:1183
H = max(1, ceil(height_ft / cell_size_ft_effective))     view_basis.py:1184
```

(same form at `view_basis.py:1094-1095` for the pre-cap request and
`:1132-1133` for the adaptive recompute). `bounds_uv` returned at
`view_basis.py:1194` is `base_bounds` unmodified — the cap path even says so
in a comment, `view_basis.py:1141-1143` ("CRITICAL: Bounds stay UNCHANGED").

So the slack is not slack *in B*. It is `W*cell − B.width` and
`H*cell − B.height`, each in `[0, cell)`, living between B and the grid.

### Where that slack goes: **CONFIRMED — far-edge only, not centred**

The grid's own anchor is stated twice in `raster.py`, identically:

```
raster.py:422-423   u = self.bounds_xy.xmin + (i + 0.5) * self.cell_size_ft
                    v = self.bounds_xy.ymin + (j + 0.5) * self.cell_size_ft
raster.py:916-917   (same two lines, model_clip_bounds guard)
```

Cell `(0,0)`'s centre is half a cell in from `(B.xmin, B.ymin)`. There is no
origin offset term and no half-slack term anywhere. Therefore:

- **Anchor: `(B.xmin, B.ymin)`.**
- **Overhang: entirely at `(B.xmax, B.ymax)`, up to one cell short of a full
  cell on each axis.**
- **Not centred.**

### Reconciling with the stated hypothesis "anchored at (xmin, ymax)"

Both describe the same geometry; they differ only in whether v runs up or
down. Model space has v increasing upward, so the overhang is at `ymax`.
Image space has y increasing downward and the decode pins the top image row
to `ymax`:

```
decode_stage_a_color_id.py:566-567
    u = xmin + (x / image_w) * (xmax - xmin)
    v = ymax - (y / image_h) * (ymax - ymin)
```

so image row `y = 0` **is** the `v = ymax` edge — the same edge the grid's
v-overhang sits on. Read that way, "anchored at (xmin, ymax) with far-edge
overhang" is correct and equivalent to the code's `(xmin, ymin)` anchor.

**Hypothesis confirmed. Not accepted by default — checked against
`raster.py:422-423` and `:916-917`, which are the only two places a cell
index is turned into a UV coordinate.**

### The one centring in the whole chain, and why it is not this

`view_basis.py:1051-1068` **does** centre — but it centres *B itself* on the
*pre-annotation* bounds' centre, and only when `anno_expanded` is true **and**
the requested grid exceeded `max_W`/`max_H`. It re-centres a rectangle; it
never splits cell slack. If it fires, A can end up wider than B on a side,
which is exactly what `compute_model_crop`'s defensive intersection exists to
absorb (`color_id_buffer.py:1560-1568`).

### Why the one-ROW offset, specifically

Independent corroboration of "far-edge, not centred": the supplied
`colorid_to_occupancy.py`'s assumed-mode grid derives the cell size **from the
crop**, not from the grid:

```
colorid_to_occupancy.py (supplied copy), grid_assumed():
    n = res["backoff_floor_px"]
    cell = crop_u / n           # axis == "width"
    W, H = n, max(1, int(round(crop_v / cell)))
```

`backoff_floor_px` is `grid_axis_px`, i.e. `raster.W` (or `raster.H` under
vertical fit) — `color_id_buffer.py:1735-1740`, written to the sidecar at
`color_id_buffer.py:2683`. So `n == W` is right, but `cell = crop_u / W`
**understates** the true `cell_size_ft_effective` by exactly the u-axis slack,
because the true relation is `crop_u ≤ B.width ≤ W*cell`. An understated cell
then makes `round(crop_v / cell)` come out **one too large** on the v axis.
A half-cell centring error would instead show as a half-cell shift on *both*
axes and no row-count change. The observed symptom — one row, no u-axis
half-shift — matches far-edge slack and rules out centring.

---

## Q3 — Does the A→B relationship differ by view type?

**The A→B relationship itself: no.** `compute_model_crop`
(`color_id_buffer.py:1539-1616`) has no view-type branch, no `ViewType`
import, and no plan/elevation special case. Nor does `resolve_view_bounds`.
What actually varies is a *property* that correlates with view type:

> **A ≠ B iff the view was annotation-expanded (or carries `buffer_ft > 0`,
> or hit the cap envelope).** `view_basis.py:1026-1028`.

Plans carry tags, dimensions and keynotes that sit outside the model crop, so
`compute_annotation_extents` expands B outward and A ⊊ B. The elevations and
section in this model apparently do not, so A == B there. That is a content
property, not a type dispatch — a plan with no annotation behaves like an
elevation, and vice versa.

### But the framing divergence you observed is real, and it is elsewhere

The "exactly 10 px/cell for elevations/section, different framing for plans"
observation is about the **`view_raster/` PNGs**, which are produced by a
completely separate path from the Stage A TIFF — `view_raster_export.py`, not
`color_id_buffer.py`. **That is where the divergence originates, and it is a
genuine bug, not a view-type policy.**

`_prepare_view_for_export()` (`view_raster_export.py:70`) has exactly two
branches (`:176` and `:211`), split on `_is_annotation_only_view()`:

**Annotation-only branch (drafting/legend), `view_raster_export.py:176-210`** —
recomputes the origin from annotation extents and **re-anchors both corners**:

```
view_raster_export.py:203-206
    new_cb.Min = XYZ(origin_x, origin_y, cb.Min.Z)
    new_cb.Max = XYZ(origin_x + W * cell_size_ft,
                     origin_y + H * cell_size_ft, cb.Max.Z)
```

**Model branch (everything else — plan, elevation, section), `:211-262`** —
measures the existing crop box in UV, then expands **only the MAX corner**:

```
view_raster_export.py:233-238
    crop_w_uv = max(u_vals) - min(u_vals)
    crop_h_uv = max(v_vals) - min(v_vals)
    delta_u = max(0.0, W * cell_size_ft - crop_w_uv)
    delta_v = max(0.0, H * cell_size_ft - crop_h_uv)

view_raster_export.py:246
    new_cb.Min = cb.Min          # <-- the origin is NEVER moved
```

So the exported frame spans `[cropbox.min, cropbox.min + W*cell]`, while the
VOP grid spans `[B.min, B.min + W*cell]`. Both are `W*cell × H*cell` in size,
and `_resize_to_exact` (`view_raster_export.py:376`, `:399`) forces the file
to exactly `W*ppc × H*ppc` pixels — which is why the **scale** is a clean
10 px/cell in every case. The two frames coincide **iff `B.min ==
cropbox.min`**, i.e. iff `buffer_ft == 0`, no annotation expansion, and no cap
re-centring.

- Elevations/section here: no annotation expansion ⇒ `B.min == cropbox.min`
  ⇒ frames register, 10 px/cell lands on the grid.
- Plans here: annotation-expanded ⇒ `B.xmin < cropbox.xmin` and
  `B.ymin < cropbox.ymin` ⇒ the PNG is translated by
  `(cropbox.xmin − B.xmin, cropbox.ymin − B.ymin)` relative to the grid, in
  **both** axes and under **any** orientation (the shift is a pure
  translation in UV, so rotating the view cannot fix or reveal it — which is
  exactly the "zero overlap under all orientations" signature). When the
  annotation margin exceeds the view's own model extent the translation
  exceeds the frame width and overlap goes to zero.

Two aggravating details in the same branch:

- `delta_u`/`delta_v` are clamped to `≥ 0` (`:236-238`, "never shrink"). If the
  cap envelope made `W*cell` *smaller* than the crop box, the branch is a
  complete no-op and the frames differ in size as well as origin.
- The expansion is applied in world space and inverse-transformed
  (`:240-251`), keeping `cb.Max.Z`. That part is orientation-correct; the
  origin is the only defect.

**This is Stage-A-adjacent but not Stage A.** It does not affect A, B, C, the
sidecar, or the decode. It is recorded here because it is the origin of the
observation in Q3, and because it is the natural place for the human
checkpoint to decide whether `view_raster/` PNGs can be used as visual ground
truth for plans at all. **Out of scope for the D1 patch; not touched.**

---

## Q4 — Elev 5: `paper_fit_in` 97.0 ft vs crop 96.38 ft

### What `paper_fit_in` actually measures

```
color_id_buffer.py:1651-1654
    paper_width_in  = (raster.W * raster.cell_size_ft * 12.0) / scale
    paper_height_in = (raster.H * raster.cell_size_ft * 12.0) / scale
color_id_buffer.py:1671
    paper_fit_in = paper_height_in if fit_direction == "vertical" else paper_width_in
```

Converted back to model feet, `paper_fit_in * scale / 12` is **exactly
`W * cell_size_ft_effective`** — the grid extent. Not B. Not A.

So the comparison "97.0 vs 96.38" is **grid extent vs A**, and decomposes as:

```
delta = (W*cell − B.width)      ceil() slack, in [0, cell)      <- the roundup
      + (B.width − A.width)     buffer_ft + annotation expansion <- NOT roundup
```

The second term is zero iff `model_crop_offset_uv == [0,0,0,0]` for that view.

### Confirming "the roundup and nothing else"

The test is two conditions, both readable straight from the sidecar:

1. `resolution.model_crop_offset_uv == [0.0, 0.0, 0.0, 0.0]` → second term is
   zero, so `delta` is pure ceil() slack.
2. `delta < cell_size_ft_effective` and `W == ceil(A.width / cell)`.

For Elev 5: `delta = 97.0 − 96.38 = 0.62 ft`. That is consistent with pure
roundup **iff** `cell_size_ft_effective > 0.62 ft`. The arithmetic closes
exactly at `cell = 1.0 ft` (the 1/8" default cell at scale 96, i.e.
1/8" = 1'-0"): `ceil(96.38 / 1.0) = 97`, `W*cell = 97.0`, residual 0.62 —
a clean single-cell roundup with `W = 97`. At `cell = 0.5 ft` (scale 48) it
would **not** close (`0.62 > 0.5`), which would mean a non-zero annotation/
buffer term.

**I cannot close this from the repo.** Confirming it needs
`cell_size_ft_effective`, `W`, and `model_crop_offset_uv` for that view, all
of which live in the byColor sidecar, which is not in this container.

### Per-view delta table for all 6 model views

**Cannot be produced here — the byColor run `20260917T104744` is not present.**

The table below is the shape it must take; every column is a direct sidecar
read plus one subtraction. A reproducer is checked in at
`tools/notes/frame_delta_report.py` so this can be filled in in one command on
a machine that has the run:

```
python tools/notes/frame_delta_report.py <run>/color_id_buffer/
```

| View | fit axis | `paper_fit_in`·scale/12 (= W·cell, ft) | A width/height (ft) | delta (ft) | cell (ft) | offset zero? | pure roundup? |
|---|---|---|---|---|---|---|---|
| Elev 5 | ? | 97.0 | 96.38 | 0.64% / 0.62 | ? | ? | consistent at cell=1.0 |
| (5 more) | | | | | | | **unavailable in this container** |

---

## Carry-forward into Phase 2

- The decode's crop numerator reads `sidecar["bounds_xy"]`, which is **A**
  (`color_id_buffer.py:2694`). Correct — A is what the TIFF images. Keep it.
- The decode comment at `decode_stage_a_color_id.py:634-637` calls
  `paper_fit_in` "the view's real paper extent". Per Q4 it is the **grid**
  extent, which overstates A. Comment is wrong; the numerator preference is
  right. Fixed in Phase 2, comment only.
- **G7 discrepancy — flagged from static reading, then confirmed
  numerically.** The supplied
  `colorid_to_occupancy.py`'s `frame_from_capture()` computes its crop as
  `sidecar["bounds_xy"] + model_crop_offset_uv`. Per `color_id_buffer.py:2694`
  and `:2697-2707`, `sidecar["bounds_xy"]` **is already A**, and the offset is
  the A-relative-to-B relationship — `raster.bounds_xy + offset = A`, so
  `A + offset` double-applies it. On any view with a non-zero offset the two
  tools disagree by construction. On views with a zero offset (A == B) they
  agree exactly.

  Driven over six synthetic sidecars spanning the cases the six model views
  cover (unpadded horizontal, unpadded vertical, clamp on each axis, square,
  narrowed crop):

  | case | decode fpp | colorid_to_occupancy fpp | \|delta\| |
  |---|---|---|---|
  | unpadded / horizontal fit | 0.012500648508 | 0.012500648508 | 0.0 |
  | unpadded / vertical fit | 0.039997348535 | 0.039997348535 | 0.0 |
  | clamp fired on height | 0.020000000000 | 0.020000000000 | 0.0 |
  | clamp fired on width | 0.020000000000 | 0.020000000000 | 0.0 |
  | square capture | 0.020000000000 | 0.020000000000 | 0.0 |
  | **narrowed crop (offset ≠ 0)** | **0.020000000000** | **0.012500000000** | **7.5e-03** |

  Exact agreement (delta 0.0, not merely under 1e-6) on every zero-offset
  case; a 60% disagreement the moment the offset is non-zero. **Reported, not
  reconciled** — `colorid_to_occupancy.py` is out of scope per the task.
  Re-run against the real six views with:

  ```
  python tools/notes/frame_delta_report.py <run>/color_id_buffer/ \
      --colorid-to-occupancy /path/to/colorid_to_occupancy.py
  ```
