# Stage A capture-state probe

`probe_capture_state.py` answers questions about view state, in Revit and with
evidence, **before** any capture code changes. The offline half is
`tools/analyze_capture_state_probe.py`.

- **Round 1** (probe `2026-10-01.1`): Q1–Q5, run in Revit on 2026-10-01.
- **Round 2** (probe `2026-10-02.1`, analyzer `1.1.0`): Q1b, Q3b, Q5b and Q6,
  described under [Round 2](#round-2-probe-2026-10-021).
- **Round 3** (probe `2026-10-02.5`, analyzer `1.3.0`): Q7, described under
  [Round 3](#round-3-probe-2026-10-025). Round 3 is the default question set.

**Round 3 is UNVERIFIED in Revit.** Only its pure parts are tested offline
(the question set, the IN[9] keys, and the order in which split regions are
removed); every Revit call in it is the question.

**Round 2 is UNVERIFIED in Revit.** It has been smoke-run only against a
throwaway fake of the Revit API, including the production code Q3b and Q6
call. That checks that the Python runs end to end, that the gates refuse
after a field left changed, and that the verify reports it. It does not show
that any Revit call behaves as assumed. Treat its first run as the test of the
probe itself.

### Every write obeys one invariant

Every write records `{attempted, commit_status, read_back, took_effect}`:
- `read_back` is a fresh read after the transaction closed. It is a required
  argument, so a write without one cannot be written.
- `took_effect` compares the read-back with `attempted`, never with the commit
  status. A write that commits but does not take effect is a finding
  (`took_effect: false`), not an error. Round 1 found two: CropBox under a
  scope box, and a template detach on a dependent view.
- Each write also records `before` (the same read, taken before the write)
  and `attempted_differs_from_before`. When that is false, the write asked
  for the value already there, so its `took_effect` cannot show a silent
  no-op.
- Statuses and enums are recorded by name (`Committed`, `FlatColors`). Round
  1 recorded them with `str()`, which under Python.NET 3 gives the number
  (`3`, `7`). DisplayStyle comparisons go through the same conversion of the
  `DisplayStyle` member, so they hold either way.

**It changes nothing persistently.** Every write runs inside ONE outer
`TransactionGroup` that is rolled back. A step that has to start from the
untouched view runs inside a nested `TransactionGroup` that is rolled back
before the next step. After the outer rollback, every probed view is read
again and compared with the read taken before the group started. A changed
field is listed under `verify_changed`, and `VERIFY_SUMMARY` says so in
capitals. The probe does not try to fix it.

### Restore checks, and the refusals they cause

Every view is read once, before the outer group starts, into a **state
snapshot**. That snapshot is the **baseline** for every question on that view.
It holds:
- crop box, `CropBoxActive`
- scope box id, `ShapeSet`
- template id
- `DisplayStyle`, `ShadowIntensity`
- background type and colours
- the members' sha1

Each question runs its steps through one gated sequence (`run_gated`):
- **At the question's start,** the view is judged against the baseline. That
  catches an earlier question that left the view changed, such as Q3 leaving
  11999340 changed before Q5 runs on it.
- **After every nested group's rollback,** the view is judged again.
- **Not restored** means:
  - any field changed, or
  - a field is readable on one side only, or
  - a field the question itself writes cannot be read on either side, so its
    restore cannot be shown:

    | Question | Fields it writes (`Q*_REQUIRED`) |
    |---|---|
    | Q1 | crop box, `CropBoxActive`, members |
    | Q3 | crop box, scope box, `ShapeSet` |
    | Q4 | `ShadowIntensity`, background |
    | Q5 | template id, `DisplayStyle` |

  A field unreadable on both sides that the question does not write is
  recorded and does not refuse: a plan view, for example, has no readable
  background.
- **Once a check fails, every later step of that question on that view is
  REFUSED, not run.** Its record says `refused: "state_not_restored"`, why, and
  which check blocked it (`blocked_by`). A step run from state the previous one
  left behind would be evidence about the wrong cause. A check that fails at
  the start refuses the whole question for that view (`refused` on the view).
- **Verdicts** are under `restore_checks[<label>]`: `question_start`, then each
  step or group label. Each verdict lists `changed`, `unverifiable` and
  `required_unreadable`, plus every field's before and after.

The verify after the outer rollback compares the same snapshot. Fields
unreadable before and after are listed under `verify_unreadable_both`, so
their absence from `verify_changed` is not read as a verified match.

---

## The questions

| | Question | View | Why |
|---|---|---|---|
| Q1 | Does writing `view.CropBox` on a split elevation undo the split? | **6112047** (Plaza) | The capture writes the crop. If that unsplits the view, the export and the view's membership both change. |
| Q2 | Can the API see that split? | **6112047** | If `CropRegionShapeManager` reports it, the capture can detect a split view and avoid it. |
| Q3 | Does a scope box override the capture's `CropBox` write? Or is it a non-rectangular crop shape? | **11999340** (MOHAVE), a **SLAB PLAN** view, and a **control** view | 31 views in run `20261001T084840_1d0b0c1` found no registration ticks, and their exports do not match the crop the capture wrote (MOHAVE: predicted 846 × 729 px, actual 846 × 639). These views have scope boxes, but so do many views that registered fine. |
| Q4 | What do `ShadowIntensity` and a flat background change on an elevation? | **2888380** | Grey shading and a coloured background could both read as content. |
| Q5 | Can `DisplayStyle` be set while the template is attached? And after detaching it? | **13663964**, **11999340** | The capture sets FlatColors. Whether the template blocks it decides whether the detach step is needed. |

### What each step does

**Q1 / Q2 (6112047).** Each S1/S2 step starts from S0's state in its own
rolled-back nested group.
- **S0:** common reads, then an export.
- **S1:** writes `view.CropBox` = the box just read (an identity write). Common
  reads, then an export.
- **S2:** writes the crop the capture would write, then `CropBoxActive = True`
  (as `color_id_buffer` does). Common reads, then an export. **The crop used is
  S0's box inset by 1 ft on every edge.** The capture's own crop is
  `crop_box_from_uv_bounds` of the snapped crop-A rectangle, which needs the
  pipeline's raster and frame geometry and cannot be built standalone.
  `s2_crop_source` in the JSON says the same.
- **Report:** member count and sha1 at S0, S1 and S2. Added and removed ids for
  S1 and S2 against S0, first 50 of each. The split properties at every step.
  Q2's answer is `report.q2_api_sees_split_at_s0`.

**Q3 (test views and the control).** Each S1–S4 step starts from the
baseline state. The **restore mechanism is a nested TransactionGroup per step,
rolled back before the next**, with the restore checks above after each one.
If S2's rollback leaves the scope box changed, for example, S3 and S4 are
refused.
- **S0:** common reads, then an export.
- **S1:** writes a crop box 10 % smaller than S0's, centred. Reads it back,
  then exports.
- **S2:** sets `VIEWER_VOLUME_OF_INTEREST_CROP` to `InvalidElementId` (clears
  the scope box), then does the S1 write.
- **S3:** if `ShapeSet`, calls `RemoveCropRegionShape()`, then does the S1
  write.
- **S4:** S2 and S3 together.
- **A discriminator that did not apply refuses its step.** The scope-box
  clear counts as applied only if:
  - the write committed
  - `Set` returned True
  - the read-back shows no scope box

  The crop-shape removal counts as applied only if it committed and
  `ShapeSet` reads false afterwards. Otherwise the step writes no crop,
  exports nothing, and is refused as `discriminator_not_applied`, with the
  write and the read-back. If `ShapeSet` was already false, S3/S4 are refused
  as `no_crop_shape`, since they would repeat the step without the removal.
  These refusals are about one step, not the view's state, so later steps
  still run.

Each Q3 step records:
- `written_box`, in the crop transform the view carried at the write. If
  Revit rejected the write, `written_box` is null, `crop_write_state` says
  `raised`, the box that was tried is kept as `attempted_box`, and the
  analyzer's row reads `write_failed` with no implied size
- `read_back_box`
- `read_back_equal`: `local` compares Min/Max; `world` compares the corners
  through each box's own transform. Tolerance 1e-6 ft.
- `at_export`: `ShapeSet` and the scope box as they stood when the export ran

How to read Q3:
- The scope box is the discriminator if S1 fails on the test views, S2 fixes
  them, and the control passes S1.
- The crop shape is the discriminator if S3 fixes them and S2 does not.

**Q4 (2888380).**
- **S0:** common reads, the background record, then an export.
- **S1:** records `ShadowIntensity`, sets it to 0, then exports.
- **S2:** keeps S1. Records `GetBackground()` (type name, and the gradient
  colours if readable). Sets
  `SetBackground(ViewDisplayBackground.CreateGradient(white, white, white))`,
  then exports.
- **S3:** a separate nested group, so `ShadowIntensity` is back at its S0 value.
  The probe reads it to show that, rather than writing it. Sets only the
  background, then exports.

**Q5 (13663964, 11999340).** No exports.
- **S0:** sets `DisplayStyle = FlatColors` with the template attached. Records
  the set (value or raised), the read-back, and an `outcome`:
  - `took_effect`: the write committed and the style went from another style
    to FlatColors
  - `no_effect`: the write committed but the style is not FlatColors
  - `raised`: the write raised or did not commit
  - `already_target`: inconclusive, see below

  `took_effect` is the matching True or False, or null when inconclusive. The
  read-back alone is never the evidence: a view that was already FlatColors
  would read FlatColors after a blocked write too. So a view that starts as
  FlatColors is first switched to Hidden Line in the same step
  (`pre_switch_to_hidden_line`). If that switch doesn't take effect, the
  outcome is `already_target`, with the reason, and no FlatColors write is
  attempted.
- **S1:** a separate nested group. Detaches the template
  (`ViewTemplateId = InvalidElementId`), then makes the same FlatColors
  attempt, with the same `outcome`.
- **Also recorded:** `GetPrimaryViewId` and whether it is valid; the template's
  `GetNonControlledTemplateParameterIds()` count; and whether
  `MODEL_GRAPHICS_STYLE` (Visual Style) is among the controlled parameters.
- **A view with no template.** `template_attached` comes from the baseline
  template id, not from an assumption. With no template, S0 still runs: it
  shows whether DisplayStyle can be set at all, and records
  `template_attached: false`. S1 is refused as `no_template`, because there is
  nothing to detach and it would only repeat S0.

### Common reads (every step, every view)

- View type, template id, `IsTemplate`, `GetPrimaryViewId`
- `CropBox`: Min, Max, and the Transform's origin and bases
- `CropBoxActive`, `CropBoxVisible`, `DisplayStyle`, `ShadowIntensity`,
  `SunlightIntensity`
- The scope box parameter `VIEWER_VOLUME_OF_INTEREST_CROP`: its value, whether
  it is read-only, and the scope box's name
- `GetCropRegionShapeManager()`:
  - `Split`, `NumberOfSplitRegions`, `IsSplitHorizontally`,
    `IsSplitVertically`, `CanHaveShape`, `ShapeSet`, `CanHaveAnnotationCrop`,
    `CanBeSplit`
  - per split region, `GetSplitRegionMinimum/Maximum/Offset`
  - `GetCropShape()`: loop count, and per loop its curve count and UV extents
    in the view basis (`Origin`, `RightDirection`, `UpDirection`)
  - `GetAnnotationCropShape()` extents
- Members: `FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType()`,
  as the count and the sha1 of the sorted ids
- A parameter dump: every view parameter whose name contains shadow,
  background, light, sun, display, crop, scope, far clip, depth or split. Each
  entry records name, storage type, value, value string, read-only and the
  built-in id.

Every read and write is recorded as `{state: value | unavailable | raised,
value, error}`, never absent. A missing attribute or method is `unavailable`;
one that throws is `raised`.

---

## Round 2 (probe 2026-10-02.1)

### Round 1, as reported (run 20261001T144424)

Treated as evidence, not as settled:
- **Q1 (Plaza 6112047, Elevation, template 7249248).** Writing back the
  IDENTICAL CropBox changed membership (+80 / −3) and 8.8 % of pixels. The
  shape manager reports `Split = false`, `NumberOfSplitRegions = 1` and
  `ShapeSet = false`. The id lists were truncated at 50.
- **Q3.** `ShapeSet` is false on 11999340, 5823803 and 9948, so the crop shape
  is excluded as the cause.
  - With a scope box set, CropBox writes commit but read back unchanged: a
    silent no-op.
  - With the scope box cleared, writes stick, and MOHAVE's export matches the
    implied size to 1 px.
  - Slab 5823803 (0 of 12 ticks in production) and control 9948 (registered)
    share scope box 3626881 and their CropBox. They differ in template
    (5823949 vs 2812885).
- **Q3 probe defect, fixed.** MOHAVE's S0 crop transform is rotated 180° and
  S2/S4 wrote S0's local Min/Max in the identity transform. They cropped a
  mirrored, empty region (white TIFFs), so content with the scope box cleared
  was untested. Every crop write now carries its own transform.
- **Q4 (2888380).** ShadowIntensity 20 → 0 removes cast shadows; a white
  background removes the sky. The export ran in display style 7, not
  FlatColors.
- **Q5.** 13663964 and 11999340 are DEPENDENT views (primaries 12172722 and
  11998677). The template detach committed, but the template id after it is
  unchanged, and DisplayStyle then raises: a silent no-op.

### The questions

**Q1b: what the identity crop write changes.** Views 6112047 and 6207878
(WEST - EAST SECTION).
- **S0:** export.
- **S1:** inside one group:
  - read the members and **every** view parameter
  - write back the identical CropBox (in its own transform)
  - read members and parameters again, then export
- **Records:**
  - the FULL added and removed id lists, untruncated, each with its category,
    class and `OwnerViewId`
  - whether **15846537** and **15809670** are in the before/after sets or were
    added/removed
  - every parameter whose value or value string changed
- **Answers it:** the categories and owners of the +80 / −3. View-owned
  elements (OwnerViewId = the view) point at annotation the crop clips. Model
  categories point at the crop's depth or far clip. Any changed parameter
  names what the write touched beyond the box.

**Q3b: scope-box clear with the original frame.** View 11999340, with 5823803
as the control.
- **S1:** clear the scope box (a refused step if it does not take effect),
  then write S0's box in S0's own transform. Read back the transform and
  extents, then export.
- **S2:** scope box LEFT SET. Write production's crop:
  `crop_box_from_uv_bounds` on S0's extents, which come from
  `xy_bounds_from_crop_box_all_corners`. **This is the box already there**
  (`attempted_differs_from_before: false`), so it cannot show an override.
  It is kept because it was asked for.
- **S3:** as S2, but on S0's extents shrunk 10 %. That write can show whether
  the scope box overrides production's own write.
- **Answers it:**
  - S1's non-white pixel count against S0 says whether content survives the
    scope-box clear, once the frame is right.
  - `took_effect` on S3 says whether production's crop write is silently
    overridden by the scope box. `false` with `commit_status: Committed` is
    the scope-box override.
- Production snaps the rectangle to its pixel lattice first, which needs the
  pipeline raster and moves each edge by under a pixel. These are unsnapped
  rectangles (`snapped: false`).

**Q5b: DisplayStyle on dependent views.** Views 13663964 and 11999340. Each
view's primary is read with `GetPrimaryViewId` and snapshotted before the
group. Both views are gated together.
- **A:** detach the PRIMARY's template, read the dependent's template id,
  then try FlatColors on the dependent.
- **B:** detach the primary's template, try FlatColors on the primary, and
  read the dependent's DisplayStyle before and after
  (`dependent_followed_primary`).
- Each variant runs in its own group, with every write's `took_effect`
  recorded.
- **Answers it:**
  - A dependent template id that follows the primary's detach, plus
    `took_effect` on the dependent, says the capture must detach on the
    primary.
  - B says whether a dependent view's style simply follows its primary.

**Q6: why ticks do not render.** Views 5823803, 9948 and 11999340, plus
`q6_extra_views`.
- **Layout, built as the capture builds it** (production functions only):
  - `mark_reference_rectangle`
  - `mark_fpp_ft`
  - `registration_mark_segments`
  - `annotation_avoid_rects` on `split_stage_a_pass_membership`
  - `relocate_marks_clear_of`
  - then `create_registration_marks` in its own transaction
- **What could not be built outside the capture:**
  - the pipeline raster. Its only fields these functions read are stood in
    for by the view basis (`_LayoutRaster`).
  - `mark_fpp_ft` therefore takes its own documented fallback, the
    requested dpi, recorded as `fpp_basis`. With a raster it would use the
    achieved lattice, which matches on an uncapped view.
  - a view whose crop is inactive has no reference rectangle outside the
    pipeline (production would use the model crop A), so its layout is
    recorded unavailable with the reason.
- **Two variants, each in its own group:**
  - S0 unmarked / S1 marked, with the crop as authored
  - S2 unmarked / S3 marked, after production's crop write
- **Per mark:**
  - line style id/name and its `GraphicsStyleCategory` id/name
  - `GetCategoryHidden(OST_Lines)`
  - `GetCategoryHidden(<line-style subcategory>)`
  - `IsHidden(view)`
  - whether `FilteredElementCollector(doc, view.Id)` returns it
- **Per view:**
  - each filter (enabled, visible, and whether its categories include
    OST_Lines)
  - whether the template controls V/G model categories, annotation
    categories and filters
- **The pixel test, in the analyzer:**
  - Each mark's expected window comes from the crop at export (production's
    projection), through the grid's own nominal mapping, padded 3 px.
  - `rendered` compares pixels against the unmarked twin of the same state,
    then falls back to the mark colour.
  - It is `unmeasured`, with the reason, when the crop is inactive or
    unreadable, the window falls outside the image, or the export failed.
    It is never guessed.
- **Answers it:** a row with `rendered: false` next to the field that
  differs from 9948's. A hidden line-style subcategory, a filter on
  OST_Lines, or the template controlling V/G would each show there.
  (Round 2's answer: the template hides `<Thin Lines>`; production change A
  now picks a style whose subcategory the view does not hide. Since A, S1
  measures A itself: `create_registration_marks` is production's.)

**Q6b (probe `2026-10-02.2`, analyzer `1.2.0`): the tick-style fix and the
Lines-hidden case.** Same views, four more groups, every write recorded
with its read-back. **UNVERIFIED in Revit**, like round 2 was.
- `detached_twin` -- S6: the template detached with PRODUCTION's detach
  (`color_id_buffer._detach_view_template`, on the primary for a dependent
  view), unmarked.
- `production_order` -- S7: production's marks drawn with the template
  attached, THEN the template detached, as the capture does it; twin S6.
  Answers: does the style A chose with the template attached still draw
  once it is detached?
- `temporary_style` -- S4 unmarked; S5 the marks retargeted to production's
  temporary weight-1 Lines subcategory (`_temporary_tick_style`), template
  attached; then detached, S10. Twins S4 and S6. Answers: does a temporary
  subcategory draw under a template that controls V/G, and after detaching?
- `lines_unhidden` (B, not in production) -- S8: template detached,
  OST_Lines unhidden, then ONLY the OST_Lines elements the unhide made
  visible (collected after it, not before it) hidden one by one
  (`stage_a_registration.hide_in_view`), unmarked; S9 marked. Refused
  (`detach_failed`) when the detach does not take: with the template
  attached `SetCategoryHidden(OST_Lines)` raises "Category cannot be
  hidden". The analyzer's `q6_authored` row is S8 against S6, with each
  write's `state` and `took_effect`: **0 changed pixels** means the view
  still shows as authored. S9 against S8 is whether the ticks draw. Only
  meaningful on a view whose template hides OST_Lines -- none of the three
  defaults does; add one under `q6_extra_views`. On the others nothing is
  revealed, so nothing is hidden and S8 is a true control.

**Probe `2026-10-02.3` / analyzer `1.2.1`** (after run 20261001T175557, which
imported a checkout without A and C): Q6 is REFUSED (`production_mismatch`)
when the imported production lacks `tick_line_style`,
`_temporary_tick_style`, `TEMPORARY_TICK_SUBCATEGORY` or
`_detach_view_template`, naming the missing symbols and the checkout's root;
`production_imports` records `module_files` and `missing_for_q6`. IN[9] must
point at a checkout of PR #226's branch or later.

## Round 3 (probe 2026-10-02.5)

From run 1001-1950 (529 views). Q7 imports production, like Q3b and Q6, so
`IN[9]` must point at PR #226's branch.

**Q7 split: can a split crop be captured segment by segment?** HIGH ROOF PLAN
(3300684) is a split crop: its mid ticks did not draw and its two fits
disagreed on scale (18.75 and 23.00 px residuals). Steps, each in its own
rolled-back group: `S0` as authored; `R<i>` with every split region except
`i` removed (`RemoveSplitRegion`, highest index first); `U` with the split
removed (`RemoveSplit`). Each step reads the crop box, the crop shape and its
split regions (count, minimum, maximum, offset), and production's crop A,
then exports. The answer is whether `R<i>` is a plain one-region view of
segment `i` that the registered capture could take as it is. The restore
gate includes `split_regions`, so a region left removed refuses every later
step.

**Q7 markers: does painting the ElevationMarker colour its text?** CABINET
TYPES (17732958) painted its markers' viewers and left the marker body (the
`ElevationMarker` element, which carries the text) black. Recorded: every
`ElevationMarker` in the document that has a bbox in the view or is returned
by its collector, and the view's `OST_Viewers` elements, each with its class,
category, OwnerViewId, collector membership, hidden state and production's
pass placement. Steps: `S0` as authored; `S1` the markers painted
`(201, 3, 197)` with production's flat override; `S2` the markers and the
viewers, the viewers `(3, 157, 203)`. The analyzer counts each colour and
black per step; `black_removed_by_marker_paint` is S0 black minus S1 black.

## How to run

1. Open the model with the views below. Dynamo 3.x, CPython3 Python node.
2. Paste `tests/dynamo/probe_capture_state.py` into the node, or load it with
   `exec(open(path).read())`. Rounds 1 and Q1b/Q5b import nothing from the
   repository. **Q3b and Q6 import production code** from it, so the repository
   must be findable: set `repo_root` in `IN[9]`, set the
   `REVIT_SSM_EXPORTER_ROOT` environment variable, or put `IN[0]` inside the
   checkout. If it isn't found, `production_imports` says where it looked,
   and those steps are refused.
3. Set the node inputs:

| Input | Meaning | Default | Greg sets |
|---|---|---|---|
| `IN[0]` | output directory (required) | — | **yes** |
| `IN[1]` | Q1/Q2 split elevation id | `6112047` | only if different |
| `IN[2]` | Q3 test view id | `11999340` | only if different |
| `IN[3]` | Q3 SLAB PLAN view id | none (recorded `not_provided`) | **yes** |
| `IN[4]` | Q3 control view id: a view that HAS a scope box and registered fine in run `20261001T084840_1d0b0c1` | none (recorded `not_provided`) | **yes**, Greg picks it |
| `IN[5]` | Q4 elevation id | `2888380` | only if different |
| `IN[6]` | Q5 view ids, a list | `[13663964, 11999340]` | only if different |
| `IN[7]` | export pixel width | `2000` | optional |
| `IN[8]` | questions: `"round3"`, `"round2"`, `"round1"`, `"all"`, or a comma list of `q1_q2,q3,q4,q5,q1b,q3b,q5b,q6,q7` | `"round3"` (empty means round 3) | optional |
| `IN[9]` | round-2 options: a Dictionary or a JSON object (keys below). An unknown key is refused. | `{}` | `repo_root` |

   `IN[9]` keys:

   | Key | Meaning | Default |
   |---|---|---|
   | `repo_root` | the Revit_SSM_Exporter checkout, for Q3b/Q6's production imports | searched, see step 2 |
   | `q1b_views` | Q1b views | `[6112047, 6207878]` |
   | `q3b_views` | Q3b views: the first is the test, the rest are controls | `[11999340, 5823803]` |
   | `q5b_views` | Q5b dependent views | `[13663964, 11999340]` |
   | `q6_views` | Q6 views | `[5823803, 9948, 11999340]` |
   | `q6_extra_views` | more Q6 views, appended | `[]` |
   | `q7_split_views` | Q7 split-crop views | `[3300684]` (HIGH ROOF PLAN) |
   | `q7_marker_views` | Q7 elevation-marker views | `[17732958]` (CABINET TYPES) |

   The simplest `IN[9]` is just the repository folder as a string, which is
   taken as `repo_root` once it is confirmed to hold `vop_interwoven`. For
   other keys, use a Dictionary node, or a JSON string with forward slashes
   (JSON rejects a single backslash):
   `{"repo_root": "C:/Users/gmcdowell/Documents/Revit_SSM_Exporter", "q6_extra_views": [12345]}`.
   Anything else is refused with what was received and the accepted forms.

   An id may be an integer, a string, or a Dynamo-wrapped view.
4. Run. `OUT` is the JSON's path. If the probe could not start at all, `OUT`
   is a dict with `fatal`.
5. Offline, with NumPy and Pillow:

   ```bash
   python tools/analyze_capture_state_probe.py <IN[0]>/capture_state_<timestamp>
   ```

A Revit warning dialog may appear on a commit (a crop write on a split view,
for example). Dismiss it.

A write that raises, a `Commit` that raises, and a `Commit` that returns
anything but `Committed` (`Pending`, `RolledBack`, `Error`) are all recorded as
`raised`. In each case the transaction is closed before the probe continues:
if `HasEnded()` is false, it is rolled back. The outcome is recorded under
`transaction_close`.

**If the transaction still cannot be closed, the probe HALTS.** That covers a
transaction stuck in `Pending` whose rollback raises, and one whose state
cannot be read. Nothing more is written or exported: every later step and
question is refused as `probe_halted`. The outer group is still rolled back
and verified. `halted` in the JSON gives the reason, and `VERIFY_SUMMARY`
starts with `PROBE HALTED`.

## Outputs

Everything is written to `<IN[0]>/capture_state_<timestamp>/`.

- **`probe_capture_state_<timestamp>.json`**, written last:
  - `inputs`: the resolved inputs
  - `views`: each id's roles and whether it resolved
  - `questions.q1_q2 / q3 / q4 / q5 / q1b / q3b / q5b / q6`: the steps above,
    each with its writes, common reads and export record
  - `production_imports`: where the production code was found (Q3b/Q6), or
    why not
  - `q5b_primaries`: each Q5b view's primary
  - `exports`: every export with its step, file, requested pixel size, fit
    direction, and `dims_px` read back from the TIFF header (stdlib `struct`)
  - `transaction_group`: whether the outer group started and rolled back
  - `restore_checks` per question (per view for Q3 and Q5), and refused steps
    with `refused`, `refused_reason` and `blocked_by`
  - `verify`: per view and field, `equal`, `changed`, `unverifiable` or
    `unreadable_both`, with before and after
  - `verify_changed`, `verify_unreadable_both` and `VERIFY_SUMMARY`
  - `exceptions`: every exception, with its stage and traceback
- **`<step>_<viewid>.tiff`**, e.g. `Q1_S2_6112047.tiff`, `Q3B_S1_11999340.tiff`,
  `Q6_S3_9948.tiff`
- **`probe_capture_state_analysis.json`**, written by the analyzer. It carries
  the probe JSON's sha256, and its contents are:
  - per TIFF: size, distinct colours, the non-white fraction of a 2 % border
    band (a background indicator), the grey count (r = g = b, neither white
    nor black; a shadow indicator), and the top 10 colours
  - per step against the S0 of the same question and view: changed pixels and
    fraction, or `size_differs` (never resized)
  - the per-view Q3 table: step, written box, read-back box, exported px,
    the px the box implies at the same fit direction and pixel width, and the
    difference on the non-fit axis (a mismatch when it exceeds 1 px), and
    `restored_after`: whether that step's rollback restored the view. A step
    the probe refused is a `refused` row with its reason and no numbers, so it
    can never read as a match or a mismatch.

  - since analyzer 1.1.0:
    - `writes`: every write record
    - `commit_without_effect`: the writes that committed and did not take
      effect
    - `q1b`: every member change with category and owner, the watch ids,
      the changed parameters
    - `q3b`: per step, the writes, the scope box at export, whether S0's
      transform was kept, and non-white and changed pixels against S0
    - `q5b`: per variant, the detach, the template read and the FlatColors
      outcomes
    - `q6`: one row per (view, marked step, mark), with every visibility
      field and `rendered: true | false | unmeasured`
    - `non_white_pixels` per image

    A recorded value that is missing or unreadable is shown as unavailable,
    with the reason, never as a default. A round-1 probe JSON analyzes under
    1.1.0 with these sections empty.

  The analyzer refuses, with `status: "refused"`, its reasons, and exit 2,
  when:
  - the probe JSON is missing, ambiguous or unreadable
  - a TIFF the JSON names as exported is missing
  - a TIFF cannot be read

  An export that failed in Revit is listed under `failed_exports` and is not
  a refusal.

The implied pixel size is computed **only by the analyzer**. The probe records
the written box and the export settings, so the arithmetic exists in one place.

## What is reimplemented, not imported

Round 1, Q1b and Q5b import nothing from `vop_interwoven`, so they run in a
node with no repository path set up. Q3b and Q6 import production code
(`revit.view_basis`, `stage_a_registration`, `stage_a_registered_capture`,
`revit.annotation.split_stage_a_pass_membership`, `config.Config`), because
the question is what production does. Nothing from those modules is copied.
The minimum the rest needs is reimplemented:
- the TIFF header reader (`color_id_buffer.read_image_dimensions`)
- the ElementId reader (`stage_a_probe_contract.element_id_value`)
- `_export_tiff`'s ImageExportOptions set, minus its pixel-size backoff and
  retries. One export per step, and the accepted pixel size is recorded.

The capture's crop computation is NOT reimplemented: Q1 S2 uses the 1 ft inset
instead, as described above.

---

## Results

*(Greg: fill in after the run.)*

- Run timestamp / probe JSON sha256:
- Q1 — does the identity write (S1) or the capture's write (S2) undo the split?
- Q2 — does the API report the split (`Split`, `NumberOfSplitRegions`)?
- Q3 — test views vs control, S1–S4:
- Q4 — shadow (grey count) and background (border fraction) per step:
- Q5 — FlatColors with the template attached / after detach, per view:
- Verify — any field changed after the rollback?

### Round 2

- Run timestamp / probe JSON sha256 / `production_imports`:
- Q1b — the added/removed members by category and owner; 15846537 and
  15809670; changed parameters; 6207878 vs 6112047:
- Q3b — S1 non-white vs S0 (content once the frame is right); S3
  `took_effect` under the scope box; 5823803 vs 11999340:
- Q5b — A: dependent template after the primary's detach, dependent
  FlatColors; B: did the dependent follow the primary:
- Q6 — `rendered` per mark, and the field that differs between 5823803 and
  9948; with vs without the crop write:
- Q6b — S1 `rendered` on 5823803, 11999340, 9948 after A (expected true by
  `changed_anywhere_px`); S7 (production order); S5/S10 (temporary
  subcategory, attached / detached); `q6_authored` S8 vs S6 and S9 on a view
  whose template hides OST_Lines:
- `commit_without_effect`:
- Verify — any field changed after the rollback?
