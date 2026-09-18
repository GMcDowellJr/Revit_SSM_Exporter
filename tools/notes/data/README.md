# Stage A excursion snapshot — reference data, NOT a baseline

## `verification_bundle_20260917T112657.json`

| | |
|---|---|
| run | byGeom `20260917T112657_2025-02-03.rvt` |
| produced by | `tools/verify_invariant_core.py` at `2f741bd` |
| `tool_sha256` | `b10cd46e5d6b39efde1cbf9371bf6435db7d686f889da126889c9b6e6c13d861` |
| `ConfigHash` | `a493722f` |
| bundle schema | `1.1` |
| sha256 | `faaf9554366206f9617cae0b26c1d5da421c6cdb63d08eee5cf7e2296219de32` |
| bytes | 68,883 |

**This is the PRE-DELETION reference for the classification-surface deletion.**
It exists so the post-deletion run has something to diff against: new violations
in the second bundle are collateral damage from the deletion, and everything
already in this one is not.

Read it with `verify_invariant_core.load_verification_bundle()`, which refuses a
schema version it was not written for. `tool_sha256` is the producing build: a
comparison is only meaningful against a bundle from the SAME build, because a
differ is robust to its own systematic errors only when both sides share them.

### THIS IS NOT A GOLDEN BASELINE. DO NOT ASSERT AGAINST IT.

Same rule as the CSV below, for the same reason. Nothing under `tests/` reads
it and nothing should start. `§4` of the Stage A handoff defers the golden set
until after the deletion lands, after vector bbox is validated, and on a capture
where the invariant core reports no violations. This capture reports two
known-open violations, so it is not that capture.

### What it records, and what it does not

At the time of capture, `new_violation_count: 0` — every violation observed was
already in the known-open baseline:

- **I2** — 4 of 8 views emitted twice, each pair one fresh row plus one cached
  row (`from_cache: [false, true]`). `views_perf` duplicates too, but carries no
  `FromCache` column, so neither of its rows can be identified as cached.
- **I8** — two `ViewFrameHash` collisions. `1c1b4f43` covers 542078 and 554620
  at **identical** 350x288 dims; `6147c316` covers 630733 and 770742 at 97x25
  (2425 cells) and 52x152 (7904 cells). These are different kinds of thing and
  the bundle does not adjudicate which is a defect.
- **I3** — `NOT_EXERCISED`. No row carries `IsOnSheet` True, so the column
  cannot distinguish "nothing is sheeted" from "the viewport scan never ran".
- **I7** — report only, and only 4 of 8 views are joinable: the other 4 are the
  duplicated ones, excluded because nothing says which row is authoritative.

**`Ext_Cells_*` is zero on every view.** External content — linked RVT and DWG —
is entirely unexercised by this capture, so a before/after against it can say
nothing about whether external-content emission survived the deletion. That
needs a separate capture with links visible.

### byColor and byGeom produce identical CSVs

Measured, not assumed. The byColor run `20260917T112500` and the byGeom run
`20260917T112657` of the same document produce bundles that agree on every
observable: the same 8 views, the same `FilledCells`, the same cell-total sums,
the same annotation totals, the same frame hashes, the same invariant statuses
and the same collisions. They also share `ConfigHash` `a493722f`.

The color-ID capture is an ADDITIONAL output — TIFFs plus sidecars — layered on
the same pipeline run. It does not change these CSVs. So `--path` records which
export directory a bundle came from, not a difference in the numbers, and
`tools/colorid_to_occupancy.py` reads these CSVs as its `geom_csv` input
regardless of which folder they sit in.

### Real project data

View names come from a live hospital model, as with the CSV below. Relevant if
this repository's visibility ever changes.

---


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
