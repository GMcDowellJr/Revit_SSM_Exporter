# Stage A excursion snapshot — reference data, NOT a baseline

## `stage_a_excursion_byColor_20260917T104744.csv`

| | |
|---|---|
| run | byColor `20260917T104744` |
| rows | 800, one per painted HOST element |
| views | 6 |
| columns | 25 |
| sha256 | `34e0cae378dee78a7d7966e4afe63e11f2c41b6fb8b96195c95701b8b51e4c50` |
| bytes | 254,385 |

Produced by `residual_floor_report.py`, a standalone tool that is **not in
this repository and should not be added to it**. Its reporting capability was
folded into `tools/notes/g2_bbox_excursion_report.py` (A6) and the clamp
arithmetic it carried became `tools/clamp_pad_geometry.py` (A1). Its own
docstring asked for both.

This file is committed because it is the only real measurement data the tree
has, and because `tools/notes/g2_source_findings.md` §Q5 reasons from it. The
reasoning should be checkable against its evidence.

---

## THIS IS NOT A GOLDEN BASELINE. DO NOT ASSERT AGAINST IT.

It is deliberately **not** under `tests/`. Nothing in `tests/` reads it, and
nothing should start.

The next capture is planned as the **whole model**, not six hand-picked
views. When that lands, essentially every aggregate here is superseded rather
than comparable:

- **The view set changes completely.** Every per-view figure in this file
  describes one of six specific views. There is no view-level continuity to
  diff against.
- **n changes by orders of magnitude**, and n is what makes these numbers
  mean anything — see `MIN_N_FOR_SUMMARY` in the folded tool. Cells that are
  n=3 here will not be n=3 there.
- **The category mix changes.** 11 categories appear here because six views
  happened to contain them. A whole-model run will contain more, in different
  proportions, and a per-category median computed over six views is not a
  prior for one computed over the model.
- **The clipped fraction changes.** Clipping is a function of where the crop
  falls relative to each element. A different view set has a different
  boundary population, and §Q5 shows that population is what two of the six
  recorded figures were made of.

A regression test built on any of this would fail on the next run for a
reason that is not a regression. Treat it as a historical record of what the
2026-09-17 figures were measuring, and nothing more.

---

## Schema, and how it differs from the folded tool's

The column names here are the standalone report's. The folded tool emits a
different and wider set, so a new `--per-element-csv` **will not line up with
this file column-for-column**:

| here | in `g2_bbox_excursion_report.py --per-element-csv` |
|---|---|
| `export_dpi` | `requested_export_dpi` (A10 rename; it records a REQUEST) |
| — | `effective_dpi` (what the capture ACHIEVED; absent here) |
| `exc_umin_ft` `exc_umax_ft` `exc_vmin_ft` `exc_vmax_ft` | `patched_u_lo` `patched_u_hi` `patched_v_lo` `patched_v_hi` |
| `worst_ft` `worst_px` | `patched_worst_ft` `patched_worst_px` |
| — | `patched_worst_edge` (which edge supplied it) |
| — | `baseline_*` (the pre-D1 mapping, for comparison) |
| `scope` | dropped — see below |

Sign convention is the same in both: **positive means the decoded extent
lies OUTSIDE the reference bbox** on that edge. `worst_ft` is the
per-element max over all four edges, **unclamped**, so it can be negative.

`clipped` means the element's **reference bbox crosses the crop**, so the
image cannot show its full extent and every edge figure for it is a lower
bound. `touches_image_edge` means its **painted pixels reach a border**.
These are different populations — Elev 5 is 30 vs 22, Section 1 is 25 vs 6 —
and neither substitutes for the other.

---

## Known limits of this data

- **HOST only.** `scope` is `host` in all 800 rows. The producing tool
  iterated `("host", "link")`, but its LINK branch cannot emit a row: it
  resolves pixel extents through `color_assignment_map`, which is per-element
  HOST identity, while LINK elements are painted per category through
  `link_category_color_map`. The column is constant and carries no
  information.
- **No absolute reference coordinates.** The file carries excursions and
  pixel extents but not the reference bbox's own UV corners. That is why the
  `eps = fpp * 0.5` tolerance in the clipped test **cannot be checked against
  this data** — there is no way to tell whether any element sits within half
  a pixel of the crop boundary. Third tolerance with no observed instance,
  after the 1-px round-trip tolerance and the negative-pad guard.
- **`near_face_w`, `export_dpi` and the pixel-size columns are carried, not
  analysed.** H1 / H3 / H4 are recorded do-not-investigate. The columns are
  here because dropping collected data is a silent loss, not as an invitation.
- **Real project data.** View names and element IDs come from a live hospital
  model. Relevant if this repository's visibility ever changes.

---

## What it is good for

- Checking `tools/notes/g2_source_findings.md` §Q5 — in particular that Elev 5
  and Elev 9 enter the recorded 0.097–0.151 ft band **only** through clipped
  elements, with unclipped maxima of 0.0438 and 0.0487 ft.
- Recovering which edge supplied each view's max (L1 `u_lo`, L5 `u_hi`, the
  other four `v_hi`) without re-running anything.
- A shape reference when reading a new `--per-element-csv`: same
  measurement, different schema and a different population.
